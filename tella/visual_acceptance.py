"""Human visual acceptance records and deterministic suite aggregation."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tella.atomic_write import atomic_write_text
from tella.planner.models import TellaScenePlan
from tella.production import file_sha256, stable_hash
from tella.scene_regeneration import SceneCorrection


VISUAL_ACCEPTANCE_SCHEMA_VERSION = 1
_UNSAFE_NOTES = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|credential|access[_-]?token|"
    r"provider\s*[:=]|model\s*[:=]|system_instruction)"
)


class RequestedActionMatch(StrEnum):
    passed = "pass"
    soft_fail = "soft_fail"
    hard_fail = "hard_fail"
    not_reviewed = "not_reviewed"


class CharacterConsistency(StrEnum):
    passed = "pass"
    warning = "warning"
    failed = "fail"
    not_reviewed = "not_reviewed"


class UnwantedReadableText(StrEnum):
    absent = "absent"
    present = "present"
    uncertain = "uncertain"
    not_reviewed = "not_reviewed"


class StyleComposition(StrEnum):
    passed = "pass"
    warning = "warning"
    failed = "fail"
    not_reviewed = "not_reviewed"


class SceneDecision(StrEnum):
    accept = "accept"
    regenerate = "regenerate"
    reject = "reject"
    not_reviewed = "not_reviewed"


class JobDecision(StrEnum):
    accepted = "accepted"
    conditionally_accepted = "conditionally_accepted"
    rejected = "rejected"
    incomplete_review = "incomplete_review"


class SceneReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = VISUAL_ACCEPTANCE_SCHEMA_VERSION
    job_id: str
    scene_index: int = Field(gt=0)
    scene_role: str
    image_path: str
    image_sha256: str
    plan_or_narration_sentence_hash: str
    requested_action: str
    automated_qc_result: dict[str, Any] = Field(default_factory=dict)
    requested_action_match: RequestedActionMatch = RequestedActionMatch.not_reviewed
    character_consistency: CharacterConsistency = CharacterConsistency.not_reviewed
    unwanted_readable_text: UnwantedReadableText = UnwantedReadableText.not_reviewed
    style_and_composition: StyleComposition = StyleComposition.not_reviewed
    overall_scene_decision: SceneDecision = SceneDecision.not_reviewed
    reviewer_notes: str = ""
    reviewer_label: str = ""
    reviewed_at_utc: str = ""
    regeneration_reason: str = ""
    must_show_suggestions: list[str] = Field(default_factory=list, max_length=12)
    must_not_show_suggestions: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("reviewer_notes", "reviewer_label", "regeneration_reason", mode="before")
    @classmethod
    def safe_notes(cls, value: Any) -> str:
        text = str(value or "").strip()
        if len(text) > 1000:
            raise ValueError("review text exceeds 1000 characters")
        if _UNSAFE_NOTES.search(text):
            raise ValueError("review notes must not contain credentials or provider configuration")
        return text

    @field_validator("must_show_suggestions", "must_not_show_suggestions", mode="before")
    @classmethod
    def safe_suggestions(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("review suggestions must be arrays")
        output = []
        for item in value:
            text = str(item).strip()
            if len(text) > 300 or _UNSAFE_NOTES.search(text):
                raise ValueError("unsafe or oversized correction suggestion")
            output.append(text)
        return output

    @property
    def human_review_complete(self) -> bool:
        return all((
            self.requested_action_match != RequestedActionMatch.not_reviewed,
            self.character_consistency != CharacterConsistency.not_reviewed,
            self.unwanted_readable_text != UnwantedReadableText.not_reviewed,
            self.style_and_composition != StyleComposition.not_reviewed,
            self.overall_scene_decision != SceneDecision.not_reviewed,
            bool(self.reviewed_at_utc),
        ))


class JobReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = VISUAL_ACCEPTANCE_SCHEMA_VERSION
    job_id: str
    technical_validation: dict[str, Any] = Field(default_factory=dict)
    scenes: list[SceneReview]

    @model_validator(mode="after")
    def unique_scenes(self) -> "JobReview":
        indices = [scene.scene_index for scene in self.scenes]
        if len(indices) != len(set(indices)):
            raise ValueError("review contains duplicate scene indices")
        if any(scene.job_id != self.job_id for scene in self.scenes):
            raise ValueError("scene review job ID does not match job review")
        return self


class AcceptanceThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    minimum_technical_job_completion_rate: float = Field(1.0, ge=0, le=1)
    maximum_action_hard_fail_rate: float = Field(0.0, ge=0, le=1)
    maximum_unwanted_text_rate: float = Field(0.0, ge=0, le=1)
    maximum_character_consistency_fail_rate: float = Field(0.0, ge=0, le=1)
    maximum_average_regeneration_count: float = Field(1.0, ge=0)
    all_scenes_reviewed_required: bool = True
    hard_action_mismatch_rejects: bool = True
    unwanted_text_rejects: bool = True
    soft_fail_is_conditional: bool = True


class AcceptanceCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    case_id: str
    topic: str
    expected_recipe: str
    expected_scene_count: int = 7
    intended_visual_challenge: str
    important_object_states: list[str]
    high_risk_scenes: list[int]
    expected_request_budget: int = 7
    manual_review_required: bool = True


class AcceptanceSuite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: int = VISUAL_ACCEPTANCE_SCHEMA_VERSION
    suite_id: str
    acceptance_policy_version: int = 1
    thresholds: AcceptanceThresholds
    cases: list[AcceptanceCase]

    @model_validator(mode="after")
    def validate_cases(self) -> "AcceptanceSuite":
        ids = [case.case_id for case in self.cases]
        if not ids:
            raise ValueError("acceptance suite must contain at least one case")
        if len(ids) != len(set(ids)):
            raise ValueError("acceptance suite case IDs must be unique")
        for case in self.cases:
            if case.expected_recipe != "practical_life_steps_callirrhoe_v1":
                raise ValueError("every acceptance case must target the production recipe")
            if case.expected_scene_count != 7:
                raise ValueError("every acceptance case must require exactly seven scenes")
        return self


def _write_json(path: Path, payload: Any) -> Path:
    return atomic_write_text(
        path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )


def load_suite(path: Path) -> AcceptanceSuite:
    return AcceptanceSuite.model_validate_json(Path(path).read_text(encoding="utf-8"))


def load_review(path: Path) -> JobReview:
    return JobReview.model_validate_json(Path(path).read_text(encoding="utf-8"))


def initialize_review(job_dir: Path, output: Path) -> JobReview:
    job_dir = Path(job_dir).resolve()
    plan = TellaScenePlan.model_validate_json((job_dir / "plan.json").read_text(encoding="utf-8"))
    scenes = [scene for scene in plan.scenes if scene.kind == "scene"]
    if [scene.scene_index for scene in scenes] != list(range(1, 8)):
        raise ValueError("acceptance review requires scene indices 1 through 7")
    summary = {}
    summary_path = job_dir / "production_summary.json"
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    reviews = []
    for scene in scenes:
        relative = scene.asset_path or (scene.image_filenames[0] if scene.image_filenames else "")
        image = job_dir / relative
        if not image.is_file():
            raise ValueError(f"missing scene image: {relative}")
        sentence_hash = hashlib_sha256(scene.voice_script)
        reviews.append(SceneReview(
            job_id=job_dir.name, scene_index=scene.scene_index,
            scene_role=scene.scene_role, image_path=relative,
            image_sha256=file_sha256(image),
            plan_or_narration_sentence_hash=sentence_hash,
            requested_action=scene.visual_action or scene.scene_action,
            automated_qc_result={
                "technical_asset_status": scene.asset_status,
                "automated_visual_qc": scene.symbolic_qc_status,
            },
        ))
    result = JobReview(
        job_id=job_dir.name,
        technical_validation={
            "production_status": summary.get("status", "unknown"),
            "audio_qc": summary.get("qc_results", {}).get("audio", "unknown"),
            "video_qc": summary.get("qc_results", {}).get("video", "unknown"),
        }, scenes=reviews,
    )
    _write_json(Path(output), result.model_dump(mode="json"))
    return result


def hashlib_sha256(value: str) -> str:
    import hashlib
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def scene_decision(review: SceneReview, policy: AcceptanceThresholds) -> JobDecision:
    if not review.human_review_complete:
        return JobDecision.incomplete_review
    if policy.hard_action_mismatch_rejects and review.requested_action_match == RequestedActionMatch.hard_fail:
        return JobDecision.rejected
    if policy.unwanted_text_rejects and review.unwanted_readable_text == UnwantedReadableText.present:
        return JobDecision.rejected
    if review.overall_scene_decision in {SceneDecision.reject, SceneDecision.regenerate}:
        return JobDecision.rejected
    if review.character_consistency == CharacterConsistency.failed or review.style_and_composition == StyleComposition.failed:
        return JobDecision.rejected
    if (
        (policy.soft_fail_is_conditional and review.requested_action_match == RequestedActionMatch.soft_fail)
        or review.character_consistency == CharacterConsistency.warning
        or review.unwanted_readable_text == UnwantedReadableText.uncertain
        or review.style_and_composition == StyleComposition.warning
    ):
        return JobDecision.conditionally_accepted
    return JobDecision.accepted


def validate_review(job_dir: Path, review: JobReview) -> dict[str, Any]:
    job_dir = Path(job_dir).resolve()
    stale, missing = [], []
    for scene in review.scenes:
        image = (job_dir / scene.image_path).resolve()
        if job_dir not in image.parents or not image.is_file():
            missing.append(scene.scene_index)
        elif file_sha256(image) != scene.image_sha256:
            stale.append(scene.scene_index)
    return {"valid": not stale and not missing, "stale_scene_indices": stale,
            "missing_scene_indices": missing}


def aggregate_job(job_dir: Path, review: JobReview, policy: AcceptanceThresholds) -> dict[str, Any]:
    validity = validate_review(job_dir, review)
    by_index = {scene.scene_index: scene for scene in review.scenes}
    missing_reviews = [index for index in range(1, 8) if index not in by_index]
    decisions = {
        str(index): scene_decision(scene, policy).value
        for index, scene in sorted(by_index.items())
    }
    if not validity["valid"] or missing_reviews or any(
        value == JobDecision.incomplete_review.value for value in decisions.values()
    ):
        status = JobDecision.incomplete_review
    elif any(value == JobDecision.rejected.value for value in decisions.values()):
        status = JobDecision.rejected
    elif any(value == JobDecision.conditionally_accepted.value for value in decisions.values()):
        status = JobDecision.conditionally_accepted
    else:
        status = JobDecision.accepted
    return {"job_id": review.job_id, "status": status.value,
            "scene_decisions": decisions, "missing_review_scene_indices": missing_reviews,
            **validity}


def report_acceptance(suite: AcceptanceSuite, jobs_root: Path, output: Path | None = None) -> dict[str, Any]:
    jobs_root = Path(jobs_root)
    jobs, all_reviews = [], []
    technical_complete = 0
    regeneration_count = 0
    for case in suite.cases:
        job_dir = jobs_root / case.case_id
        review_path = job_dir / "visual_acceptance_review.json"
        if not review_path.is_file():
            jobs.append({"case_id": case.case_id, "status": JobDecision.incomplete_review.value,
                         "reason": "review file missing"})
            continue
        review = load_review(review_path)
        aggregated = aggregate_job(job_dir, review, suite.thresholds)
        jobs.append({"case_id": case.case_id, **aggregated})
        all_reviews.extend(review.scenes)
        if review.technical_validation.get("production_status") == "completed":
            technical_complete += 1
        regeneration_count += sum(
            scene.overall_scene_decision == SceneDecision.regenerate for scene in review.scenes
        )

    total_scenes = len(all_reviews)
    reviewed_scenes = sum(scene.human_review_complete for scene in all_reviews)
    divisor = max(1, total_scenes)
    metrics = {
        "technical_job_completion_rate": technical_complete / max(1, len(suite.cases)),
        "action_hard_fail_rate": sum(scene.requested_action_match == RequestedActionMatch.hard_fail for scene in all_reviews) / divisor,
        "unwanted_text_rate": sum(scene.unwanted_readable_text == UnwantedReadableText.present for scene in all_reviews) / divisor,
        "character_consistency_fail_rate": sum(scene.character_consistency == CharacterConsistency.failed for scene in all_reviews) / divisor,
        "average_regeneration_count": regeneration_count / max(1, len(suite.cases)),
        "reviewed_scene_count": reviewed_scenes,
        "total_required_scene_count": len(suite.cases) * 7,
    }
    t = suite.thresholds
    threshold_failures = []
    comparisons = (
        (metrics["technical_job_completion_rate"] >= t.minimum_technical_job_completion_rate, "technical completion rate"),
        (metrics["action_hard_fail_rate"] <= t.maximum_action_hard_fail_rate, "action hard-fail rate"),
        (metrics["unwanted_text_rate"] <= t.maximum_unwanted_text_rate, "unwanted-text rate"),
        (metrics["character_consistency_fail_rate"] <= t.maximum_character_consistency_fail_rate, "character-consistency fail rate"),
        (metrics["average_regeneration_count"] <= t.maximum_average_regeneration_count, "average regeneration count"),
    )
    threshold_failures.extend(label for passed, label in comparisons if not passed)
    incomplete = (
        not all_reviews
        or any(job["status"] == JobDecision.incomplete_review.value for job in jobs)
        or (t.all_scenes_reviewed_required and reviewed_scenes != len(suite.cases) * 7)
    )
    rejected = any(job["status"] == JobDecision.rejected.value for job in jobs)
    if incomplete:
        status, exit_code = JobDecision.incomplete_review.value, 2
    elif threshold_failures or rejected:
        status, exit_code = JobDecision.rejected.value, 1
    elif any(job["status"] == JobDecision.conditionally_accepted.value for job in jobs):
        status, exit_code = JobDecision.conditionally_accepted.value, 0
    else:
        status, exit_code = JobDecision.accepted.value, 0
    report = {
        "visual_acceptance_schema_version": VISUAL_ACCEPTANCE_SCHEMA_VERSION,
        "suite_id": suite.suite_id, "acceptance_policy_version": suite.acceptance_policy_version,
        "status": status, "exit_code": exit_code, "jobs": jobs, "metrics": metrics,
        "threshold_failures": threshold_failures, "human_review_required": True,
        "automated_qc_is_human_approval": False,
        "command_status": "completed",
        "threshold_result": (
            "incomplete" if incomplete else ("failed" if threshold_failures else "passed")
        ),
        "human_acceptance_result": status,
        "release_approved": status == JobDecision.accepted.value and not threshold_failures,
        "report_fingerprint": stable_hash({"jobs": jobs, "metrics": metrics, "status": status}),
    }
    if output is not None:
        _write_json(Path(output), report)
    return report


def corrections_from_review(review: JobReview, output: Path) -> dict[str, Any]:
    corrections = []
    for scene in sorted(review.scenes, key=lambda item: item.scene_index):
        if scene.overall_scene_decision != SceneDecision.regenerate:
            continue
        correction = SceneCorrection(
            scene_index=scene.scene_index,
            reason=scene.regeneration_reason or "human_visual_review",
            must_show=scene.must_show_suggestions,
            must_not_show=scene.must_not_show_suggestions,
            forbidden_text=scene.unwanted_readable_text == UnwantedReadableText.present,
            requested_action=scene.requested_action,
            reviewer_notes=scene.reviewer_notes,
            source_image_sha256=scene.image_sha256,
        )
        corrections.append(correction.model_dump(mode="json"))
    payload = {
        "scene_regeneration_schema_version": 1,
        "source_job_id": review.job_id,
        "human_edits_required_before_regeneration": True,
        "corrections": corrections,
    }
    _write_json(Path(output), payload)
    return payload


__all__ = [
    "AcceptanceSuite", "AcceptanceThresholds", "CharacterConsistency",
    "JobDecision", "JobReview", "RequestedActionMatch", "SceneDecision",
    "SceneReview", "StyleComposition", "UnwantedReadableText",
    "aggregate_job", "corrections_from_review", "initialize_review",
    "load_review", "load_suite", "report_acceptance", "validate_review",
]
