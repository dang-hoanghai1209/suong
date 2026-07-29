"""Process-local planning-package readiness review with no execution authority."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal, Mapping

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

_SHA256 = r"^[0-9a-f]{64}$"
_IDENTITY = r"^[a-z0-9][a-z0-9._-]{0,127}$"
_SAFE_SUMMARY = r"^[^<>\\]{1,240}$"


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @field_validator("schema_version", mode="before", check_fields=False)
    @classmethod
    def exact_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("schema_version must be exactly integer 1")
        return value


class _CapabilityLocks(_Contract):
    full_render_enabled: Literal[False] = False
    render_authority: Literal[False] = False
    renderer_execution_authority: Literal[False] = False
    video_render_authority: Literal[False] = False
    timeline_execution_authority: Literal[False] = False
    narration_generation_capability: Literal[False] = False
    tts_capability: Literal[False] = False
    audio_generation_capability: Literal[False] = False
    subtitle_generation_capability: Literal[False] = False
    media_muxing_capability: Literal[False] = False
    final_media_capability: Literal[False] = False
    output_creation_capability: Literal[False] = False
    execution_job_creation_capability: Literal[False] = False


class ReadinessStatus(StrEnum):
    LOCKED = "LOCKED"
    NOT_READY = "NOT_READY"
    READY_FOR_EXECUTION_ENABLEMENT_REVIEW = "READY_FOR_EXECUTION_ENABLEMENT_REVIEW"
    REVIEW_APPROVED_FOR_SEPARATE_TASK = "REVIEW_APPROVED_FOR_SEPARATE_TASK"
    REVISION_REQUESTED = "REVISION_REQUESTED"
    REVIEW_REJECTED = "REVIEW_REJECTED"
    SUPERSEDED = "SUPERSEDED"


class StageCheckStatus(StrEnum):
    PASS = "PASS"
    WARNING = "WARNING"
    BLOCKED = "BLOCKED"
    NOT_EVALUATED = "NOT_EVALUATED"
    SUPERSEDED = "SUPERSEDED"


class ReadinessStage(StrEnum):
    RUN = "RUN"
    STORY_PLAN = "STORY_PLAN"
    SCENE_PLANNING = "SCENE_PLANNING"
    VISUAL_CANDIDATES = "VISUAL_CANDIDATES"
    COMPOSITION_PLANNING = "COMPOSITION_PLANNING"
    TIMELINE_PLANNING = "TIMELINE_PLANNING"
    NARRATION_SOURCE = "NARRATION_SOURCE"
    ARTIFACT_INTEGRITY = "ARTIFACT_INTEGRITY"
    AUTHORITY_FRESHNESS = "AUTHORITY_FRESHNESS"
    EXECUTION_CAPABILITY_LOCKS = "EXECUTION_CAPABILITY_LOCKS"
    PROCESS_LOCAL_LIMITATIONS = "PROCESS_LOCAL_LIMITATIONS"


class LimitationCode(StrEnum):
    PROCESS_LOCAL_AUTHORITY = "PROCESS_LOCAL_AUTHORITY"
    HOST_RESTART_RESETS_REVIEW_STATE = "HOST_RESTART_RESETS_REVIEW_STATE"
    REAL_PROVIDER_RUNTIME_NOT_EVALUATED = "REAL_PROVIDER_RUNTIME_NOT_EVALUATED"
    TTS_NOT_GENERATED = "TTS_NOT_GENERATED"
    AUDIO_DURATION_NOT_MEASURED = "AUDIO_DURATION_NOT_MEASURED"
    AUDIO_ALIGNMENT_NOT_VERIFIED = "AUDIO_ALIGNMENT_NOT_VERIFIED"
    RENDERER_NOT_EXECUTED = "RENDERER_NOT_EXECUTED"
    FFMPEG_NOT_PROBED = "FFMPEG_NOT_PROBED"
    FRAME_OUTPUT_NOT_VERIFIED = "FRAME_OUTPUT_NOT_VERIFIED"
    VIDEO_OUTPUT_NOT_VERIFIED = "VIDEO_OUTPUT_NOT_VERIFIED"
    COLOR_ACCURACY_NOT_VERIFIED = "COLOR_ACCURACY_NOT_VERIFIED"
    MOTION_SMOOTHNESS_NOT_VERIFIED = "MOTION_SMOOTHNESS_NOT_VERIFIED"
    TRANSITION_RENDER_NOT_VERIFIED = "TRANSITION_RENDER_NOT_VERIFIED"
    FINAL_MEDIA_NOT_CREATED = "FINAL_MEDIA_NOT_CREATED"
    OUTPUT_STORAGE_NOT_EVALUATED = "OUTPUT_STORAGE_NOT_EVALUATED"


class ReadinessReviewReason(StrEnum):
    STORY_PLAN_REVIEW_REQUIRED = "STORY_PLAN_REVIEW_REQUIRED"
    SCENE_PLANNING_REVIEW_REQUIRED = "SCENE_PLANNING_REVIEW_REQUIRED"
    VISUAL_REVIEW_REQUIRED = "VISUAL_REVIEW_REQUIRED"
    COMPOSITION_REVIEW_REQUIRED = "COMPOSITION_REVIEW_REQUIRED"
    TIMELINE_REVIEW_REQUIRED = "TIMELINE_REVIEW_REQUIRED"
    NARRATION_ALIGNMENT_REVIEW_REQUIRED = "NARRATION_ALIGNMENT_REVIEW_REQUIRED"
    OTHER_BOUNDED_NOTE = "OTHER_BOUNDED_NOTE"


class SourceCoverageV1(_Contract):
    schema_version: Literal[1] = 1
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self) -> SourceCoverageV1:
        if self.end <= self.start:
            raise ValueError("source coverage must be ordered")
        return self


class ReadinessStageCheckV1(_Contract):
    schema_version: Literal[1] = 1
    check_id: str = Field(pattern=_IDENTITY)
    stage: ReadinessStage
    status: StageCheckStatus
    blocker_codes: tuple[str, ...] = ()
    warning_codes: tuple[str, ...] = ()
    informational_codes: tuple[str, ...] = ()
    bound_revision_ids: tuple[str, ...] = ()
    summary: str = Field(pattern=_SAFE_SUMMARY)
    required_for_review_eligibility: StrictBool
    checked_at: str

    @model_validator(mode="after")
    def consistent(self) -> ReadinessStageCheckV1:
        diagnostics = (*self.blocker_codes, *self.warning_codes, *self.informational_codes)
        if len(set(diagnostics)) != len(diagnostics):
            raise ValueError("stage diagnostics must be unique")
        if self.status is StageCheckStatus.BLOCKED and not self.blocker_codes:
            raise ValueError("blocked stage requires a blocker")
        if self.blocker_codes and self.status is not StageCheckStatus.BLOCKED:
            raise ValueError("stage blockers require blocked status")
        if self.status is StageCheckStatus.WARNING and not self.warning_codes:
            raise ValueError("warning stage requires a warning")
        return self


class ReadinessSceneCheckV1(_Contract):
    schema_version: Literal[1] = 1
    scene_check_id: str = Field(pattern=_IDENTITY)
    position: StrictInt = Field(ge=1, le=64)
    scene_id: str = Field(pattern=_IDENTITY)
    scene_revision_id: str = Field(pattern=_IDENTITY)
    semantic_beat_id: str = Field(pattern=_IDENTITY)
    source_coverage: SourceCoverageV1
    visual_collection_revision_id: str = Field(pattern=_IDENTITY)
    accepted_candidate_id: str = Field(pattern=_IDENTITY)
    candidate_revision_id: str = Field(pattern=_IDENTITY)
    artifact_sha256: str = Field(pattern=_SHA256)
    artifact_mime: Literal["image/png", "image/jpeg", "image/webp"]
    artifact_width: StrictInt = Field(gt=0, le=8192)
    artifact_height: StrictInt = Field(gt=0, le=8192)
    artifact_registered: Literal[True] = True
    artifact_technically_valid: Literal[True] = True
    composition_id: str = Field(pattern=_IDENTITY)
    composition_revision_id: str = Field(pattern=_IDENTITY)
    composition_accepted: Literal[True] = True
    timeline_segment_id: str = Field(pattern=_IDENTITY)
    timeline_segment_revision_id: str = Field(pattern=_IDENTITY)
    start_ms: StrictInt = Field(ge=0)
    end_ms: StrictInt = Field(gt=0)
    duration_ms: StrictInt = Field(gt=0)
    transition_duration_ms: StrictInt = Field(ge=0)
    narration_alignment_status: Literal["ALIGNED"]
    blocker_codes: tuple[str, ...] = ()
    warning_codes: tuple[str, ...] = ()
    readiness_status: Literal["PASS"]

    @model_validator(mode="after")
    def consistent(self) -> ReadinessSceneCheckV1:
        if self.end_ms != self.start_ms + self.duration_ms:
            raise ValueError("scene timing arithmetic is inconsistent")
        if self.transition_duration_ms >= self.duration_ms:
            raise ValueError("scene transition exceeds duration")
        if self.blocker_codes:
            raise ValueError("passing scene check cannot contain blockers")
        return self


class ReadinessLimitationV1(_Contract):
    schema_version: Literal[1] = 1
    limitation_code: LimitationCode
    description: str = Field(pattern=_SAFE_SUMMARY)
    acknowledgement_required: Literal[True] = True
    acknowledged: StrictBool = False
    acknowledgement_id: str | None = Field(default=None, pattern=_IDENTITY)

    @model_validator(mode="after")
    def consistent(self) -> ReadinessLimitationV1:
        if self.acknowledged != (self.acknowledgement_id is not None):
            raise ValueError("limitation acknowledgement summary is inconsistent")
        return self


class ReadinessAcknowledgementV1(_Contract):
    schema_version: Literal[1] = 1
    acknowledgement_id: str = Field(pattern=_IDENTITY)
    report_revision_id: str = Field(pattern=_IDENTITY)
    source_authority_sha256: str = Field(pattern=_SHA256)
    limitation_code: LimitationCode
    acknowledged: Literal[True] = True
    reviewer_note: str | None = Field(default=None, max_length=500)
    created_at: str

    @field_validator("reviewer_note")
    @classmethod
    def safe_note(cls, value: str | None) -> str | None:
        return _safe_note(value)


class ReadinessApprovalV1(_CapabilityLocks):
    schema_version: Literal[1] = 1
    approval_id: str = Field(pattern=_IDENTITY)
    report_revision_id: str = Field(pattern=_IDENTITY)
    source_authority_sha256: str = Field(pattern=_SHA256)
    purpose: Literal["SEPARATE_EXECUTION_ENABLEMENT_TASK_REVIEW"]
    approved_for_separate_execution_enablement_review: Literal[True] = True
    reviewer_note: str | None = Field(default=None, max_length=500)
    created_at: str

    @field_validator("reviewer_note")
    @classmethod
    def safe_note(cls, value: str | None) -> str | None:
        return _safe_note(value)


class ReadinessReviewEntryV1(_Contract):
    schema_version: Literal[1] = 1
    review_id: str = Field(pattern=_IDENTITY)
    report_revision_id: str = Field(pattern=_IDENTITY)
    source_authority_sha256: str = Field(pattern=_SHA256)
    action: Literal[
        "ACKNOWLEDGED",
        "APPROVED_SEPARATE_TASK_REVIEW",
        "REQUEST_UPSTREAM_REVISION",
        "REJECT_PLANNING_PACKAGE",
        "CLEAR_CURRENT_REVIEW_APPROVAL",
        "SUPERSEDED",
    ]
    reason_code: ReadinessReviewReason | None = None
    note: str | None = Field(default=None, max_length=500)
    created_at: str

    @field_validator("note")
    @classmethod
    def safe_note(cls, value: str | None) -> str | None:
        return _safe_note(value)


class ReadinessAuthorityBindingsV1(_Contract):
    schema_version: Literal[1] = 1
    story_plan_revision_id: str = Field(pattern=_IDENTITY)
    accepted_story_plan_revision_id: str = Field(pattern=_IDENTITY)
    narration_source_sha256: str = Field(pattern=_SHA256)
    scene_collection_revision_id: str = Field(pattern=_IDENTITY)
    visual_collection_revision_ids: tuple[str, ...]
    accepted_candidate_ids: tuple[str, ...]
    accepted_candidate_revision_ids: tuple[str, ...]
    accepted_candidate_artifact_sha256s: tuple[str, ...]
    composition_collection_revision_id: str = Field(pattern=_IDENTITY)
    composition_ids: tuple[str, ...]
    composition_revision_ids: tuple[str, ...]
    timeline_collection_revision_id: str = Field(pattern=_IDENTITY)
    accepted_timeline_revision_id: str = Field(pattern=_IDENTITY)
    timeline_source_authority_sha256: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def consistent(self) -> ReadinessAuthorityBindingsV1:
        counts = {
            len(self.visual_collection_revision_ids),
            len(self.accepted_candidate_ids),
            len(self.accepted_candidate_revision_ids),
            len(self.accepted_candidate_artifact_sha256s),
            len(self.composition_ids),
            len(self.composition_revision_ids),
        }
        if len(counts) != 1 or next(iter(counts), 0) == 0:
            raise ValueError("authority bindings must be aligned and non-empty")
        return self


class ExecutionReadinessReportV1(_CapabilityLocks):
    schema_version: Literal[1] = 1
    report_id: str = Field(pattern=_IDENTITY)
    report_revision_id: str = Field(pattern=_IDENTITY)
    report_revision_number: StrictInt = Field(ge=1)
    run_id: str = Field(pattern=_IDENTITY)
    source_authority_sha256: str = Field(pattern=_SHA256)
    status: ReadinessStatus
    current: StrictBool
    planning_package_ready: StrictBool
    future_execution_enablement_review_eligible: StrictBool
    approved_for_separate_execution_enablement_review: StrictBool
    blocker_count: StrictInt = Field(ge=0)
    warning_count: StrictInt = Field(ge=0)
    informational_count: StrictInt = Field(ge=0)
    stage_checks: tuple[ReadinessStageCheckV1, ...]
    scene_checks: tuple[ReadinessSceneCheckV1, ...]
    limitations: tuple[ReadinessLimitationV1, ...]
    acknowledgements: tuple[ReadinessAcknowledgementV1, ...] = ()
    approval: ReadinessApprovalV1 | None = None
    review_history: tuple[ReadinessReviewEntryV1, ...] = ()
    authority: ReadinessAuthorityBindingsV1
    total_effective_timeline_duration_ms: StrictInt = Field(gt=0)
    narration_coverage_valid: Literal[True]
    transition_valid: Literal[True]
    target_duration_valid: Literal[True]
    process_local: Literal[True] = True
    created_at: str

    @model_validator(mode="after")
    def consistent(self) -> ExecutionReadinessReportV1:
        expected_stages = tuple(ReadinessStage)
        if tuple(item.stage for item in self.stage_checks) != expected_stages:
            raise ValueError("readiness stages must use canonical order")
        if len({item.check_id for item in self.stage_checks}) != len(self.stage_checks):
            raise ValueError("readiness stage checks must be unique")
        if [item.position for item in self.scene_checks] != list(
            range(1, len(self.scene_checks) + 1)
        ):
            raise ValueError("readiness scenes must use contiguous canonical order")
        if len({item.scene_check_id for item in self.scene_checks}) != len(self.scene_checks):
            raise ValueError("readiness scene checks must be unique")
        if len({item.scene_id for item in self.scene_checks}) != len(self.scene_checks):
            raise ValueError("readiness scene identities must be unique")
        if tuple(item.limitation_code for item in self.limitations) != tuple(LimitationCode):
            raise ValueError("readiness limitations must use canonical order")
        if len({item.acknowledgement_id for item in self.acknowledgements}) != len(
            self.acknowledgements
        ):
            raise ValueError("readiness acknowledgements must be unique")
        acknowledged = {
            item.limitation_code: item.acknowledgement_id for item in self.acknowledgements
        }
        if any(
            item.acknowledgement_id != acknowledged.get(item.limitation_code)
            for item in self.limitations
        ):
            raise ValueError("limitation summaries must match acknowledgements")
        blockers = sum(len(item.blocker_codes) for item in self.stage_checks) + sum(
            len(item.blocker_codes) for item in self.scene_checks
        )
        warnings = sum(len(item.warning_codes) for item in self.stage_checks) + sum(
            len(item.warning_codes) for item in self.scene_checks
        )
        information = sum(len(item.informational_codes) for item in self.stage_checks)
        if (self.blocker_count, self.warning_count, self.informational_count) != (
            blockers,
            warnings,
            information,
        ):
            raise ValueError("readiness diagnostic counts are inconsistent")
        structurally_ready = blockers == 0 and all(
            item.status is StageCheckStatus.PASS
            for item in self.stage_checks
            if item.required_for_review_eligibility
        )
        planning_ready = (
            structurally_ready and self.current and self.status is not ReadinessStatus.SUPERSEDED
        )
        review_eligible = planning_ready and self.status in {
            ReadinessStatus.READY_FOR_EXECUTION_ENABLEMENT_REVIEW,
            ReadinessStatus.REVIEW_APPROVED_FOR_SEPARATE_TASK,
        }
        approved = self.approval is not None
        if (
            self.planning_package_ready != planning_ready
            or self.future_execution_enablement_review_eligible != review_eligible
            or self.approved_for_separate_execution_enablement_review != approved
        ):
            raise ValueError("readiness summaries are inconsistent")
        expected_statuses = {
            ReadinessStatus.READY_FOR_EXECUTION_ENABLEMENT_REVIEW,
            ReadinessStatus.REVIEW_APPROVED_FOR_SEPARATE_TASK,
            ReadinessStatus.REVISION_REQUESTED,
            ReadinessStatus.REVIEW_REJECTED,
            ReadinessStatus.SUPERSEDED,
        }
        if self.status not in expected_statuses:
            raise ValueError("initialized report status is invalid")
        if approved and self.status is not ReadinessStatus.REVIEW_APPROVED_FOR_SEPARATE_TASK:
            raise ValueError("approval requires approved report status")
        if self.status is ReadinessStatus.REVIEW_APPROVED_FOR_SEPARATE_TASK and not approved:
            raise ValueError("approved report status requires approval")
        if self.status is ReadinessStatus.SUPERSEDED and self.current:
            raise ValueError("superseded report cannot be current")
        if self.status is not ReadinessStatus.SUPERSEDED and not self.current:
            raise ValueError("non-superseded report must be current")
        return self


class ExecutionReadinessAccessV1(_CapabilityLocks):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=_IDENTITY)
    review_available: StrictBool
    initialization_authorized: StrictBool
    mutation_authorized: StrictBool
    blocker_codes: tuple[str, ...]
    report: ExecutionReadinessReportV1 | None
    process_local: Literal[True] = True


class InitializeReadinessRequestV1(_Contract):
    schema_version: Literal[1]
    timeline_collection_revision_id: str = Field(pattern=_IDENTITY)
    accepted_timeline_revision_id: str = Field(pattern=_IDENTITY)


class AcknowledgeLimitationsRequestV1(_Contract):
    schema_version: Literal[1]
    report_revision_id: str = Field(pattern=_IDENTITY)
    source_authority_sha256: str = Field(pattern=_SHA256)
    limitation_codes: tuple[LimitationCode, ...]
    acknowledged: Literal[True]
    reviewer_note: str | None = Field(default=None, max_length=500)

    @field_validator("limitation_codes", mode="before")
    @classmethod
    def json_array_codes(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(LimitationCode(item) for item in value)
        return value

    @field_validator("reviewer_note")
    @classmethod
    def safe_note(cls, value: str | None) -> str | None:
        return _safe_note(value)

    @model_validator(mode="after")
    def canonical(self) -> AcknowledgeLimitationsRequestV1:
        if not self.limitation_codes or len(set(self.limitation_codes)) != len(
            self.limitation_codes
        ):
            raise ValueError("acknowledgement codes must be non-empty and unique")
        if self.limitation_codes != tuple(
            item for item in LimitationCode if item in self.limitation_codes
        ):
            raise ValueError("acknowledgement codes must use canonical order")
        return self


class ApproveReadinessRequestV1(_Contract):
    schema_version: Literal[1]
    report_revision_id: str = Field(pattern=_IDENTITY)
    source_authority_sha256: str = Field(pattern=_SHA256)
    purpose: Literal["SEPARATE_EXECUTION_ENABLEMENT_TASK_REVIEW"]
    explicit_confirmation: Literal[True]
    reviewer_note: str | None = Field(default=None, max_length=500)

    @field_validator("reviewer_note")
    @classmethod
    def safe_note(cls, value: str | None) -> str | None:
        return _safe_note(value)


class ReviewReadinessRequestV1(_Contract):
    schema_version: Literal[1]
    report_revision_id: str = Field(pattern=_IDENTITY)
    source_authority_sha256: str = Field(pattern=_SHA256)
    reason_code: ReadinessReviewReason
    note: str | None = Field(default=None, max_length=500)

    @field_validator("reason_code", mode="before")
    @classmethod
    def json_reason(cls, value: object) -> object:
        if isinstance(value, str):
            return ReadinessReviewReason(value)
        return value

    @field_validator("note")
    @classmethod
    def safe_note(cls, value: str | None) -> str | None:
        return _safe_note(value)


class ClearApprovalRequestV1(_Contract):
    schema_version: Literal[1]
    report_revision_id: str = Field(pattern=_IDENTITY)
    source_authority_sha256: str = Field(pattern=_SHA256)
    explicit_confirmation: Literal[True]


def _safe_note(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        not value.strip()
        or "://" in value
        or "/" in value
        or "<" in value
        or "\\" in value
        or "`" in value
        or "$(" in value
        or any(ord(character) < 32 and character not in "\n\t" for character in value)
    ):
        raise ValueError("review note must be presentation-safe")
    return value


def _timestamp(number: int) -> str:
    return f"2026-01-01T00:{number // 60:02d}:{number % 60:02d}Z"


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _detached(model: BaseModel) -> dict[str, object]:
    return json.loads(model.model_dump_json())


def _error(code: str, message: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "access": None,
        "report": None,
        "error": {
            "schema_version": 1,
            "code": code,
            "message": message,
            "retryable": False,
        },
    }


_LIMITATION_DESCRIPTIONS = {
    LimitationCode.PROCESS_LOCAL_AUTHORITY: "Readiness review authority exists only in this host process.",
    LimitationCode.HOST_RESTART_RESETS_REVIEW_STATE: "Host restart resets readiness reports and reviews.",
    LimitationCode.REAL_PROVIDER_RUNTIME_NOT_EVALUATED: "Real provider runtime has not been evaluated.",
    LimitationCode.TTS_NOT_GENERATED: "Text-to-speech narration has not been generated.",
    LimitationCode.AUDIO_DURATION_NOT_MEASURED: "Narration audio duration has not been measured.",
    LimitationCode.AUDIO_ALIGNMENT_NOT_VERIFIED: "Audio alignment has not been verified.",
    LimitationCode.RENDERER_NOT_EXECUTED: "No renderer has been executed.",
    LimitationCode.FFMPEG_NOT_PROBED: "FFmpeg and FFprobe have not been probed.",
    LimitationCode.FRAME_OUTPUT_NOT_VERIFIED: "Frame output has not been verified.",
    LimitationCode.VIDEO_OUTPUT_NOT_VERIFIED: "Video output has not been verified.",
    LimitationCode.COLOR_ACCURACY_NOT_VERIFIED: "Rendered color accuracy has not been verified.",
    LimitationCode.MOTION_SMOOTHNESS_NOT_VERIFIED: "Rendered motion smoothness has not been verified.",
    LimitationCode.TRANSITION_RENDER_NOT_VERIFIED: "Rendered transitions have not been verified.",
    LimitationCode.FINAL_MEDIA_NOT_CREATED: "Final media has not been created.",
    LimitationCode.OUTPUT_STORAGE_NOT_EVALUATED: "Output storage has not been evaluated.",
}


class ExecutionReadinessStore:
    """Immutable planning-readiness history with no execution capability."""

    def __init__(self) -> None:
        self._histories: dict[str, tuple[ExecutionReadinessReportV1, ...]] = {}
        self._report_identity = 1
        self._revision_identity = 1
        self._acknowledgement_identity = 1
        self._approval_identity = 1
        self._review_identity = 1
        self._stage_check_identity = 1
        self._scene_check_identity = 1

    def _current(self, run_id: str) -> ExecutionReadinessReportV1 | None:
        history = self._histories.get(run_id, ())
        return history[-1] if history else None

    @staticmethod
    def _context(
        *,
        run: Mapping[str, Any] | None,
        review: Mapping[str, Any] | None,
        scene_access: Mapping[str, Any] | None,
        visual_accesses: Mapping[str, Mapping[str, Any]],
        composition_access: Mapping[str, Any] | None,
        timeline_access: Mapping[str, Any] | None,
    ) -> tuple[list[str], dict[str, Any] | None]:
        blockers: list[str] = []
        if run is None:
            return ["UNKNOWN_RUN"], None
        if run.get("status") != "PLANNED":
            blockers.append("RUN_NOT_PLANNED")
        if (
            review is None
            or review.get("review_status") != "ACCEPTED_FOR_SCENE_PLANNING"
            or review.get("current_revision_id") != review.get("accepted_revision_id")
        ):
            blockers.append("STORY_PLAN_NOT_CURRENT")
        story = None if review is None else review.get("current_revision", {}).get("story_plan")
        scenes = None if scene_access is None else scene_access.get("collection")
        compositions = None if composition_access is None else composition_access.get("collection")
        timeline = None if timeline_access is None else timeline_access.get("collection")
        if not isinstance(story, Mapping):
            blockers.append("STORY_PLAN_UNAVAILABLE")
        if not isinstance(scenes, Mapping) or scenes.get("ready_for_visual_planning") is not True:
            blockers.append("SCENE_COLLECTION_NOT_CURRENT")
        if (
            not isinstance(compositions, Mapping)
            or compositions.get("overall_ready_for_timeline_planning") is not True
        ):
            blockers.append("COMPOSITION_COLLECTION_NOT_CURRENT")
        if (
            not isinstance(timeline, Mapping)
            or timeline.get("accepted_for_execution_review") is not True
            or timeline.get("accepted_timeline_revision_id")
            != timeline.get("collection_revision_id")
            or timeline.get("overall_ready_for_execution_review") is not True
        ):
            blockers.append("TIMELINE_NOT_CURRENTLY_ACCEPTED")
        if blockers or not all(
            isinstance(item, Mapping) for item in (story, scenes, compositions, timeline)
        ):
            return list(dict.fromkeys(blockers)), None
        scene_items = list(scenes["scenes"])
        composition_items = list(compositions["compositions"])
        segment_items = list(timeline["segments"])
        if not (
            len(scene_items) == len(composition_items) == len(segment_items) == len(visual_accesses)
        ):
            return ["UPSTREAM_SCENE_COUNT_MISMATCH"], None
        candidates: list[Mapping[str, Any]] = []
        visual_collections: list[Mapping[str, Any]] = []
        for position, (scene, composition, segment) in enumerate(
            zip(scene_items, composition_items, segment_items, strict=True),
            start=1,
        ):
            visual_access = visual_accesses.get(scene["scene_id"])
            visual = None if visual_access is None else visual_access.get("collection")
            if not isinstance(visual, Mapping):
                return ["VISUAL_COLLECTION_NOT_CURRENT"], None
            accepted_id = visual.get("current_accepted_candidate_id")
            accepted = [
                item
                for item in visual.get("candidates", ())
                if item.get("candidate_id") == accepted_id
                and item.get("accepted_for_composition_planning") is True
            ]
            if len(accepted) != 1:
                return ["ACCEPTED_VISUAL_MISSING"], None
            candidate = accepted[0]
            technical = candidate.get("technical_validation", {})
            if technical.get("passed") is not True:
                return ["VISUAL_ARTIFACT_INVALID"], None
            if (
                scene.get("order") != position
                or composition.get("position") != position
                or segment.get("position") != position
                or scene.get("scene_id") != composition.get("scene_id") != segment.get("scene_id")
                or scene.get("scene_revision_id")
                != composition.get("scene_revision_id")
                != segment.get("scene_revision_id")
                or candidate.get("candidate_id")
                != composition.get("accepted_candidate_id")
                != segment.get("accepted_candidate_id")
                or candidate.get("candidate_revision_id")
                != composition.get("accepted_candidate_revision_id")
                != segment.get("accepted_candidate_revision_id")
                or candidate.get("sha256")
                != composition.get("accepted_candidate_sha256")
                != segment.get("candidate_artifact_sha256")
                or composition.get("composition_id") != segment.get("composition_id")
                or composition.get("composition_revision_id")
                != segment.get("composition_revision_id")
            ):
                return ["UPSTREAM_AUTHORITY_BINDING_MISMATCH"], None
            candidates.append(candidate)
            visual_collections.append(visual)
        locks = (
            "full_render_enabled",
            "render_authority",
            "renderer_execution_authority",
            "video_render_authority",
            "timeline_execution_authority",
            "narration_generation_capability",
            "tts_capability",
            "audio_generation_capability",
            "final_media_capability",
        )
        if any(timeline_access.get(key) is not False for key in locks):
            return ["EXECUTION_CAPABILITY_UNEXPECTEDLY_ENABLED"], None
        authority = {
            "story_plan_revision_id": review["current_revision_id"],
            "accepted_story_plan_revision_id": review["accepted_revision_id"],
            "narration_source_sha256": timeline["narration_source_sha256"],
            "scene_collection_revision_id": scenes["collection_revision_id"],
            "visual_collection_revision_ids": tuple(
                item["visual_collection_revision_id"] for item in visual_collections
            ),
            "accepted_candidate_ids": tuple(item["candidate_id"] for item in candidates),
            "accepted_candidate_revision_ids": tuple(
                item["candidate_revision_id"] for item in candidates
            ),
            "accepted_candidate_artifact_sha256s": tuple(item["sha256"] for item in candidates),
            "composition_collection_revision_id": compositions["collection_revision_id"],
            "composition_ids": tuple(item["composition_id"] for item in composition_items),
            "composition_revision_ids": tuple(
                item["composition_revision_id"] for item in composition_items
            ),
            "timeline_collection_revision_id": timeline["collection_revision_id"],
            "accepted_timeline_revision_id": timeline["accepted_timeline_revision_id"],
            "timeline_source_authority_sha256": timeline["source_authority_sha256"],
        }
        fingerprint_projection = {
            **authority,
            "scenes": [
                {
                    "position": item["order"],
                    "scene_id": item["scene_id"],
                    "scene_revision_id": item["scene_revision_id"],
                    "semantic_beat_id": item["source_beat_id"],
                    "source_coverage": item["source_coverage"],
                    "status": item["status"],
                }
                for item in scene_items
            ],
            "visuals": [
                {
                    "visual_collection_revision_id": visual["visual_collection_revision_id"],
                    "candidate_id": candidate["candidate_id"],
                    "candidate_revision_id": candidate["candidate_revision_id"],
                    "sha256": candidate["sha256"],
                    "mime_type": candidate["mime_type"],
                    "width": candidate["width"],
                    "height": candidate["height"],
                    "status": candidate["status"],
                }
                for visual, candidate in zip(visual_collections, candidates, strict=True)
            ],
            "compositions": [
                {
                    "composition_id": item["composition_id"],
                    "composition_revision_id": item["composition_revision_id"],
                    "accepted": item["accepted_for_timeline_planning"],
                }
                for item in composition_items
            ],
            "timeline": {
                "collection_revision_id": timeline["collection_revision_id"],
                "accepted_timeline_revision_id": timeline["accepted_timeline_revision_id"],
                "source_authority_sha256": timeline["source_authority_sha256"],
                "effective_duration_ms": timeline["effective_timeline_duration_ms"],
                "narration_coverage_valid": timeline["narration_coverage_valid"],
                "ordering_valid": timeline["ordering_valid"],
                "duration_valid": timeline["duration_valid"],
                "transition_valid": timeline["transition_valid"],
                "status": timeline["status"],
            },
            "capability_locks": {key: False for key in locks},
        }
        return [], {
            "story": story,
            "scenes": scenes,
            "scene_items": scene_items,
            "visual_collections": visual_collections,
            "candidates": candidates,
            "compositions": compositions,
            "composition_items": composition_items,
            "timeline": timeline,
            "segment_items": segment_items,
            "authority": authority,
            "source_authority_sha256": _canonical_sha256(fingerprint_projection),
        }

    def _supersede(self, current: ExecutionReadinessReportV1) -> ExecutionReadinessReportV1:
        payload = current.model_dump(mode="python")
        payload.update(
            report_revision_id=f"readiness-report-revision-{self._revision_identity:04d}",
            report_revision_number=current.report_revision_number + 1,
            status=ReadinessStatus.SUPERSEDED,
            current=False,
            planning_package_ready=False,
            future_execution_enablement_review_eligible=False,
            approval=None,
            approved_for_separate_execution_enablement_review=False,
            review_history=(
                *current.review_history,
                ReadinessReviewEntryV1(
                    review_id=f"readiness-review-{self._review_identity:04d}",
                    report_revision_id=f"readiness-report-revision-{self._revision_identity:04d}",
                    source_authority_sha256=current.source_authority_sha256,
                    action="SUPERSEDED",
                    created_at=_timestamp(self._review_identity),
                ),
            ),
            created_at=_timestamp(self._revision_identity),
        )
        self._revision_identity += 1
        self._review_identity += 1
        superseded = ExecutionReadinessReportV1.model_validate(payload)
        self._histories[current.run_id] = (*self._histories[current.run_id], superseded)
        return superseded

    def access(
        self,
        *,
        run: Mapping[str, Any] | None,
        review: Mapping[str, Any] | None,
        scene_access: Mapping[str, Any] | None,
        visual_accesses: Mapping[str, Mapping[str, Any]],
        composition_access: Mapping[str, Any] | None,
        timeline_access: Mapping[str, Any] | None,
    ) -> dict[str, object] | None:
        if run is None:
            return None
        blockers, context = self._context(
            run=run,
            review=review,
            scene_access=scene_access,
            visual_accesses=visual_accesses,
            composition_access=composition_access,
            timeline_access=timeline_access,
        )
        run_id = str(run["run_id"])
        current = self._current(run_id)
        if (
            current is not None
            and current.current
            and (
                context is None
                or current.source_authority_sha256 != context["source_authority_sha256"]
            )
        ):
            current = self._supersede(current)
        available = not blockers and context is not None
        return _detached(
            ExecutionReadinessAccessV1(
                run_id=run_id,
                review_available=available,
                initialization_authorized=available
                and (current is None or current.status is ReadinessStatus.SUPERSEDED),
                mutation_authorized=available
                and current is not None
                and current.current
                and current.status is not ReadinessStatus.SUPERSEDED,
                blocker_codes=tuple(blockers),
                report=current,
            )
        )

    def _stage_checks(self, context: Mapping[str, Any]) -> tuple[ReadinessStageCheckV1, ...]:
        authority = context["authority"]
        bindings = {
            ReadinessStage.RUN: (context["timeline"]["run_id"],),
            ReadinessStage.STORY_PLAN: (authority["story_plan_revision_id"],),
            ReadinessStage.SCENE_PLANNING: (authority["scene_collection_revision_id"],),
            ReadinessStage.VISUAL_CANDIDATES: tuple(authority["visual_collection_revision_ids"]),
            ReadinessStage.COMPOSITION_PLANNING: (authority["composition_collection_revision_id"],),
            ReadinessStage.TIMELINE_PLANNING: (authority["timeline_collection_revision_id"],),
            ReadinessStage.NARRATION_SOURCE: (authority["narration_source_sha256"],),
            ReadinessStage.ARTIFACT_INTEGRITY: tuple(
                authority["accepted_candidate_artifact_sha256s"]
            ),
            ReadinessStage.AUTHORITY_FRESHNESS: (context["source_authority_sha256"],),
            ReadinessStage.EXECUTION_CAPABILITY_LOCKS: (),
            ReadinessStage.PROCESS_LOCAL_LIMITATIONS: (),
        }
        summaries = {
            stage: stage.value.replace("_", " ").title() + " planning check passed."
            for stage in ReadinessStage
        }
        summaries[ReadinessStage.PROCESS_LOCAL_LIMITATIONS] = (
            "Readiness review is process-local and runtime execution is not evaluated."
        )
        output = []
        for index, stage in enumerate(ReadinessStage, start=1):
            process_local = stage is ReadinessStage.PROCESS_LOCAL_LIMITATIONS
            output.append(
                ReadinessStageCheckV1(
                    check_id=(f"readiness-stage-check-{self._stage_check_identity:04d}"),
                    stage=stage,
                    status=StageCheckStatus.WARNING if process_local else StageCheckStatus.PASS,
                    warning_codes=("PROCESS_LOCAL_AUTHORITY",) if process_local else (),
                    informational_codes=(
                        ("RUNTIME_EXECUTION_ENVIRONMENT_NOT_EVALUATED",) if process_local else ()
                    ),
                    bound_revision_ids=bindings[stage],
                    summary=summaries[stage],
                    required_for_review_eligibility=not process_local,
                    checked_at=_timestamp(index),
                )
            )
            self._stage_check_identity += 1
        return tuple(output)

    def _scene_checks(self, context: Mapping[str, Any]) -> tuple[ReadinessSceneCheckV1, ...]:
        output = []
        for position, (scene, visual, candidate, composition, segment) in enumerate(
            zip(
                context["scene_items"],
                context["visual_collections"],
                context["candidates"],
                context["composition_items"],
                context["segment_items"],
                strict=True,
            ),
            start=1,
        ):
            output.append(
                ReadinessSceneCheckV1(
                    scene_check_id=(f"readiness-scene-check-{self._scene_check_identity:04d}"),
                    position=position,
                    scene_id=scene["scene_id"],
                    scene_revision_id=scene["scene_revision_id"],
                    semantic_beat_id=scene["source_beat_id"],
                    source_coverage={
                        "start": scene["source_coverage"]["start"],
                        "end": scene["source_coverage"]["end"],
                    },
                    visual_collection_revision_id=visual["visual_collection_revision_id"],
                    accepted_candidate_id=candidate["candidate_id"],
                    candidate_revision_id=candidate["candidate_revision_id"],
                    artifact_sha256=candidate["sha256"],
                    artifact_mime=candidate["mime_type"],
                    artifact_width=candidate["width"],
                    artifact_height=candidate["height"],
                    composition_id=composition["composition_id"],
                    composition_revision_id=composition["composition_revision_id"],
                    timeline_segment_id=segment["segment_id"],
                    timeline_segment_revision_id=segment["segment_revision_id"],
                    start_ms=segment["start_ms"],
                    end_ms=segment["end_ms"],
                    duration_ms=segment["duration_ms"],
                    transition_duration_ms=segment["transition_out_ms"],
                    narration_alignment_status=segment["narration_alignment"]["status"],
                    warning_codes=tuple(segment["warning_codes"]),
                    readiness_status="PASS",
                )
            )
            self._scene_check_identity += 1
        return tuple(output)

    def initialize(
        self,
        *,
        context_inputs: Mapping[str, Any],
        payload: object,
    ) -> dict[str, object]:
        try:
            request = InitializeReadinessRequestV1.model_validate(payload)
        except ValidationError:
            return _error(
                "INVALID_READINESS_INITIALIZATION",
                "Readiness initialization request is malformed.",
            )
        blockers, context = self._context(**context_inputs)
        if blockers or context is None:
            return _error(
                "READINESS_NOT_AUTHORIZED",
                "Current planning authority does not permit readiness initialization.",
            )
        timeline = context["timeline"]
        if (
            request.timeline_collection_revision_id != timeline["collection_revision_id"]
            or request.accepted_timeline_revision_id != timeline["accepted_timeline_revision_id"]
        ):
            return _error(
                "STALE_READINESS_AUTHORITY",
                "Timeline authority changed before readiness initialization.",
            )
        run_id = str(timeline["run_id"])
        current = self._current(run_id)
        if current is not None and current.current:
            if current.source_authority_sha256 == context["source_authority_sha256"]:
                return {
                    "schema_version": 1,
                    "access": None,
                    "report": _detached(current),
                    "error": None,
                }
            self._supersede(current)
        stage_checks = self._stage_checks(context)
        scene_checks = self._scene_checks(context)
        revision_id = f"readiness-report-revision-{self._revision_identity:04d}"
        report = ExecutionReadinessReportV1(
            report_id=f"readiness-report-{self._report_identity:04d}",
            report_revision_id=revision_id,
            report_revision_number=1,
            run_id=run_id,
            source_authority_sha256=context["source_authority_sha256"],
            status=ReadinessStatus.READY_FOR_EXECUTION_ENABLEMENT_REVIEW,
            current=True,
            planning_package_ready=True,
            future_execution_enablement_review_eligible=True,
            approved_for_separate_execution_enablement_review=False,
            blocker_count=0,
            warning_count=sum(len(item.warning_codes) for item in stage_checks),
            informational_count=sum(len(item.informational_codes) for item in stage_checks),
            stage_checks=stage_checks,
            scene_checks=scene_checks,
            limitations=tuple(
                ReadinessLimitationV1(
                    limitation_code=code,
                    description=_LIMITATION_DESCRIPTIONS[code],
                )
                for code in LimitationCode
            ),
            authority=ReadinessAuthorityBindingsV1.model_validate(context["authority"]),
            total_effective_timeline_duration_ms=timeline["effective_timeline_duration_ms"],
            narration_coverage_valid=True,
            transition_valid=True,
            target_duration_valid=True,
            created_at=_timestamp(self._revision_identity),
        )
        self._report_identity += 1
        self._revision_identity += 1
        self._histories[run_id] = (*self._histories.get(run_id, ()), report)
        return {
            "schema_version": 1,
            "access": None,
            "report": _detached(report),
            "error": None,
        }

    def _authority(
        self,
        run_id: str,
        report_revision_id: str,
        source_authority_sha256: str,
    ) -> ExecutionReadinessReportV1 | dict[str, object]:
        current = self._current(run_id)
        if (
            current is None
            or not current.current
            or current.report_revision_id != report_revision_id
            or current.source_authority_sha256 != source_authority_sha256
        ):
            return _error(
                "STALE_READINESS_REPORT",
                "Readiness report or source authority changed.",
            )
        return current

    def _commit(
        self,
        current: ExecutionReadinessReportV1,
        *,
        status: ReadinessStatus | None = None,
        acknowledgements: tuple[ReadinessAcknowledgementV1, ...] | None = None,
        approval: ReadinessApprovalV1 | None | object = ...,
        review: ReadinessReviewEntryV1,
    ) -> ExecutionReadinessReportV1:
        revision_id = f"readiness-report-revision-{self._revision_identity:04d}"
        effective_acknowledgements = (
            current.acknowledgements if acknowledgements is None else acknowledgements
        )
        effective_approval = current.approval if approval is ... else approval
        acknowledgement_ids = {
            item.limitation_code: item.acknowledgement_id for item in effective_acknowledgements
        }
        payload = current.model_dump(mode="python")
        effective_status = status or current.status
        payload.update(
            report_revision_id=revision_id,
            report_revision_number=current.report_revision_number + 1,
            status=effective_status,
            planning_package_ready=True,
            future_execution_enablement_review_eligible=effective_status
            in {
                ReadinessStatus.READY_FOR_EXECUTION_ENABLEMENT_REVIEW,
                ReadinessStatus.REVIEW_APPROVED_FOR_SEPARATE_TASK,
            },
            acknowledgements=effective_acknowledgements,
            limitations=tuple(
                ReadinessLimitationV1(
                    limitation_code=item.limitation_code,
                    description=item.description,
                    acknowledged=item.limitation_code in acknowledgement_ids,
                    acknowledgement_id=acknowledgement_ids.get(item.limitation_code),
                )
                for item in current.limitations
            ),
            approval=effective_approval,
            approved_for_separate_execution_enablement_review=effective_approval is not None,
            review_history=(
                *current.review_history,
                ReadinessReviewEntryV1.model_validate(
                    {
                        **review.model_dump(mode="python"),
                        "report_revision_id": revision_id,
                    }
                ),
            ),
            created_at=_timestamp(self._revision_identity),
        )
        if effective_approval is not None:
            payload["approval"] = ReadinessApprovalV1.model_validate(
                {
                    **effective_approval.model_dump(mode="python"),
                    "report_revision_id": revision_id,
                }
            )
        self._revision_identity += 1
        committed = ExecutionReadinessReportV1.model_validate(payload)
        self._histories[current.run_id] = (*self._histories[current.run_id], committed)
        return committed

    def acknowledge(self, run_id: str, payload: object) -> dict[str, object]:
        try:
            request = AcknowledgeLimitationsRequestV1.model_validate(payload)
        except ValidationError:
            return _error(
                "INVALID_READINESS_ACKNOWLEDGEMENT",
                "Readiness acknowledgement request is malformed.",
            )
        authority = self._authority(
            run_id, request.report_revision_id, request.source_authority_sha256
        )
        if isinstance(authority, dict):
            return authority
        existing = {item.limitation_code for item in authority.acknowledgements}
        if any(item in existing for item in request.limitation_codes):
            return _error(
                "DUPLICATE_READINESS_ACKNOWLEDGEMENT",
                "A limitation is already acknowledged for this report.",
            )
        acknowledgements = list(authority.acknowledgements)
        for code in request.limitation_codes:
            acknowledgements.append(
                ReadinessAcknowledgementV1(
                    acknowledgement_id=(
                        f"readiness-acknowledgement-{self._acknowledgement_identity:04d}"
                    ),
                    report_revision_id=authority.report_revision_id,
                    source_authority_sha256=authority.source_authority_sha256,
                    limitation_code=code,
                    reviewer_note=request.reviewer_note,
                    created_at=_timestamp(self._acknowledgement_identity),
                )
            )
            self._acknowledgement_identity += 1
        review = ReadinessReviewEntryV1(
            review_id=f"readiness-review-{self._review_identity:04d}",
            report_revision_id=authority.report_revision_id,
            source_authority_sha256=authority.source_authority_sha256,
            action="ACKNOWLEDGED",
            note=request.reviewer_note,
            created_at=_timestamp(self._review_identity),
        )
        self._review_identity += 1
        committed = self._commit(
            authority,
            acknowledgements=tuple(acknowledgements),
            review=review,
        )
        return {
            "schema_version": 1,
            "access": None,
            "report": _detached(committed),
            "error": None,
        }

    def approve(self, run_id: str, payload: object) -> dict[str, object]:
        try:
            request = ApproveReadinessRequestV1.model_validate(payload)
        except ValidationError:
            return _error(
                "INVALID_READINESS_APPROVAL",
                "Readiness approval request is malformed.",
            )
        authority = self._authority(
            run_id, request.report_revision_id, request.source_authority_sha256
        )
        if isinstance(authority, dict):
            return authority
        if authority.approval is not None:
            return _error("READINESS_ALREADY_APPROVED", "Readiness report is already approved.")
        if not authority.future_execution_enablement_review_eligible or authority.blocker_count:
            return _error(
                "READINESS_APPROVAL_BLOCKED",
                "Planning package is not eligible for separate-task review.",
            )
        if {item.limitation_code for item in authority.acknowledgements} != set(LimitationCode):
            return _error(
                "READINESS_ACKNOWLEDGEMENTS_REQUIRED",
                "All required limitations must be acknowledged before approval.",
            )
        revision_id = f"readiness-report-revision-{self._revision_identity:04d}"
        approval = ReadinessApprovalV1(
            approval_id=f"readiness-approval-{self._approval_identity:04d}",
            report_revision_id=revision_id,
            source_authority_sha256=authority.source_authority_sha256,
            purpose=request.purpose,
            reviewer_note=request.reviewer_note,
            created_at=_timestamp(self._approval_identity),
        )
        self._approval_identity += 1
        review = ReadinessReviewEntryV1(
            review_id=f"readiness-review-{self._review_identity:04d}",
            report_revision_id=revision_id,
            source_authority_sha256=authority.source_authority_sha256,
            action="APPROVED_SEPARATE_TASK_REVIEW",
            note=request.reviewer_note,
            created_at=_timestamp(self._review_identity),
        )
        self._review_identity += 1
        committed = self._commit(
            authority,
            status=ReadinessStatus.REVIEW_APPROVED_FOR_SEPARATE_TASK,
            approval=approval,
            review=review,
        )
        return {
            "schema_version": 1,
            "access": None,
            "report": _detached(committed),
            "error": None,
        }

    def review(
        self,
        run_id: str,
        operation: Literal["request-revision", "reject"],
        payload: object,
    ) -> dict[str, object]:
        try:
            request = ReviewReadinessRequestV1.model_validate(payload)
        except ValidationError:
            return _error(
                "INVALID_READINESS_REVIEW",
                "Readiness review request is malformed.",
            )
        authority = self._authority(
            run_id, request.report_revision_id, request.source_authority_sha256
        )
        if isinstance(authority, dict):
            return authority
        action = (
            "REQUEST_UPSTREAM_REVISION"
            if operation == "request-revision"
            else "REJECT_PLANNING_PACKAGE"
        )
        review = ReadinessReviewEntryV1(
            review_id=f"readiness-review-{self._review_identity:04d}",
            report_revision_id=authority.report_revision_id,
            source_authority_sha256=authority.source_authority_sha256,
            action=action,
            reason_code=request.reason_code,
            note=request.note,
            created_at=_timestamp(self._review_identity),
        )
        self._review_identity += 1
        committed = self._commit(
            authority,
            status=(
                ReadinessStatus.REVISION_REQUESTED
                if operation == "request-revision"
                else ReadinessStatus.REVIEW_REJECTED
            ),
            approval=None,
            review=review,
        )
        return {
            "schema_version": 1,
            "access": None,
            "report": _detached(committed),
            "error": None,
        }

    def clear_approval(self, run_id: str, payload: object) -> dict[str, object]:
        try:
            request = ClearApprovalRequestV1.model_validate(payload)
        except ValidationError:
            return _error(
                "INVALID_READINESS_CLEAR_APPROVAL",
                "Readiness approval-clear request is malformed.",
            )
        authority = self._authority(
            run_id, request.report_revision_id, request.source_authority_sha256
        )
        if isinstance(authority, dict):
            return authority
        if authority.approval is None:
            return _error("READINESS_APPROVAL_ABSENT", "No current approval exists.")
        review = ReadinessReviewEntryV1(
            review_id=f"readiness-review-{self._review_identity:04d}",
            report_revision_id=authority.report_revision_id,
            source_authority_sha256=authority.source_authority_sha256,
            action="CLEAR_CURRENT_REVIEW_APPROVAL",
            created_at=_timestamp(self._review_identity),
        )
        self._review_identity += 1
        committed = self._commit(
            authority,
            status=ReadinessStatus.READY_FOR_EXECUTION_ENABLEMENT_REVIEW,
            approval=None,
            review=review,
        )
        return {
            "schema_version": 1,
            "access": None,
            "report": _detached(committed),
            "error": None,
        }

    def history(self, run_id: str) -> dict[str, object] | None:
        history = self._histories.get(run_id)
        if not history:
            return None
        return {
            "schema_version": 1,
            "run_id": run_id,
            "reports": [_detached(item) for item in history],
        }

    def report(self, run_id: str, report_revision_id: str) -> dict[str, object] | None:
        selected = next(
            (
                item
                for item in self._histories.get(run_id, ())
                if item.report_revision_id == report_revision_id
            ),
            None,
        )
        return None if selected is None else _detached(selected)
