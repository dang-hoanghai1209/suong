"""Sealed planning authority for a separately reviewed future narration stage."""

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


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @field_validator("schema_version", mode="before", check_fields=False)
    @classmethod
    def exact_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("schema_version must be exactly integer 1")
        return value


class _OperationalLocks(_Contract):
    full_render_enabled: Literal[False] = False
    narration_generation_capability: Literal[False] = False
    tts_capability: Literal[False] = False
    audio_generation_capability: Literal[False] = False
    audio_measurement_capability: Literal[False] = False
    timeline_execution_authority: Literal[False] = False
    renderer_execution_authority: Literal[False] = False
    render_authority: Literal[False] = False
    video_render_authority: Literal[False] = False
    subtitle_generation_capability: Literal[False] = False
    media_muxing_capability: Literal[False] = False
    output_creation_capability: Literal[False] = False
    execution_job_creation_capability: Literal[False] = False
    final_media_capability: Literal[False] = False


class ExecutionPackageStatus(StrEnum):
    AWAITING_CREATION = "AWAITING_CREATION"
    SEALED_FOR_NARRATION_STAGE_REVIEW = "SEALED_FOR_NARRATION_STAGE_REVIEW"
    REVISION_REQUESTED = "REVISION_REQUESTED"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"


class ExecutionPackageReviewReason(StrEnum):
    PLANNING_PACKAGE_REVIEW_REQUIRED = "PLANNING_PACKAGE_REVIEW_REQUIRED"
    AUTHORITY_BINDING_CONCERN = "AUTHORITY_BINDING_CONCERN"
    NARRATION_SOURCE_CONCERN = "NARRATION_SOURCE_CONCERN"
    TIMELINE_CONCERN = "TIMELINE_CONCERN"
    ARTIFACT_INTEGRITY_CONCERN = "ARTIFACT_INTEGRITY_CONCERN"
    EXECUTION_SCOPE_CONCERN = "EXECUTION_SCOPE_CONCERN"
    LIMITATION_CONCERN = "LIMITATION_CONCERN"
    OTHER_BOUNDED_NOTE = "OTHER_BOUNDED_NOTE"


class ExecutionPackageAuthorityV1(_Contract):
    schema_version: Literal[1] = 1
    readiness_report_id: str = Field(pattern=_IDENTITY)
    readiness_report_revision_id: str = Field(pattern=_IDENTITY)
    readiness_source_authority_sha256: str = Field(pattern=_SHA256)
    readiness_approval_id: str = Field(pattern=_IDENTITY)
    readiness_approval_purpose: Literal["SEPARATE_EXECUTION_ENABLEMENT_TASK_REVIEW"]
    story_plan_revision_id: str = Field(pattern=_IDENTITY)
    accepted_story_plan_revision_id: str = Field(pattern=_IDENTITY)
    narration_source_sha256: str = Field(pattern=_SHA256)
    scene_collection_revision_id: str = Field(pattern=_IDENTITY)
    ordered_scene_ids: tuple[str, ...]
    ordered_scene_revision_ids: tuple[str, ...]
    visual_collection_revision_ids: tuple[str, ...]
    accepted_candidate_ids: tuple[str, ...]
    accepted_candidate_revision_ids: tuple[str, ...]
    accepted_candidate_artifact_sha256s: tuple[str, ...]
    accepted_candidate_mimes: tuple[str, ...]
    accepted_candidate_dimensions: tuple[tuple[StrictInt, StrictInt], ...]
    composition_collection_revision_id: str = Field(pattern=_IDENTITY)
    composition_ids: tuple[str, ...]
    composition_revision_ids: tuple[str, ...]
    timeline_collection_revision_id: str = Field(pattern=_IDENTITY)
    accepted_timeline_revision_id: str = Field(pattern=_IDENTITY)
    timeline_source_authority_sha256: str = Field(pattern=_SHA256)
    effective_timeline_duration_ms: StrictInt = Field(gt=0)

    @model_validator(mode="after")
    def aligned(self) -> ExecutionPackageAuthorityV1:
        counts = {
            len(self.ordered_scene_ids),
            len(self.ordered_scene_revision_ids),
            len(self.visual_collection_revision_ids),
            len(self.accepted_candidate_ids),
            len(self.accepted_candidate_revision_ids),
            len(self.accepted_candidate_artifact_sha256s),
            len(self.accepted_candidate_mimes),
            len(self.accepted_candidate_dimensions),
            len(self.composition_ids),
            len(self.composition_revision_ids),
        }
        if len(counts) != 1 or next(iter(counts), 0) == 0:
            raise ValueError("execution-package authority arrays must align")
        if len(set(self.ordered_scene_ids)) != len(self.ordered_scene_ids):
            raise ValueError("execution-package scenes must be unique")
        if any(width <= 0 or height <= 0 for width, height in self.accepted_candidate_dimensions):
            raise ValueError("execution-package artifact dimensions must be positive")
        return self


class ExecutionPackageReviewEntryV1(_Contract):
    schema_version: Literal[1] = 1
    review_id: str = Field(pattern=_IDENTITY)
    package_revision_id: str = Field(pattern=_IDENTITY)
    package_source_authority_sha256: str = Field(pattern=_SHA256)
    action: Literal[
        "PACKAGE_CREATED",
        "REVISION_REQUESTED",
        "PACKAGE_CANCELLED",
        "PACKAGE_SUPERSEDED",
    ]
    reason_code: ExecutionPackageReviewReason | None = None
    note: str | None = Field(default=None, max_length=500)
    created_at: str

    @field_validator("note")
    @classmethod
    def safe_note(cls, value: str | None) -> str | None:
        return _safe_note(value)


class ExecutionPackageV1(_OperationalLocks):
    schema_version: Literal[1] = 1
    package_id: str = Field(pattern=_IDENTITY)
    package_revision_id: str = Field(pattern=_IDENTITY)
    package_revision_number: StrictInt = Field(ge=1)
    run_id: str = Field(pattern=_IDENTITY)
    status: ExecutionPackageStatus
    current: StrictBool
    execution_package_created: Literal[True] = True
    eligible_for_narration_stage_review: StrictBool
    package_source_authority_sha256: str = Field(pattern=_SHA256)
    authority: ExecutionPackageAuthorityV1
    execution_enablement_contract_version: Literal["execution_enablement_v1"] = (
        "execution_enablement_v1"
    )
    review_history: tuple[ExecutionPackageReviewEntryV1, ...]
    superseded_reason: str | None = None
    created_at: str
    process_local: Literal[True] = True

    @model_validator(mode="after")
    def consistent(self) -> ExecutionPackageV1:
        eligible = (
            self.current and self.status is ExecutionPackageStatus.SEALED_FOR_NARRATION_STAGE_REVIEW
        )
        if self.eligible_for_narration_stage_review != eligible:
            raise ValueError("execution-package eligibility is inconsistent")
        if (
            self.status
            in {
                ExecutionPackageStatus.CANCELLED,
                ExecutionPackageStatus.SUPERSEDED,
            }
            and self.current
        ):
            raise ValueError("terminal execution package cannot be current")
        if (
            self.status
            not in {
                ExecutionPackageStatus.CANCELLED,
                ExecutionPackageStatus.SUPERSEDED,
            }
            and not self.current
        ):
            raise ValueError("non-terminal execution package must be current")
        if (self.status is ExecutionPackageStatus.SUPERSEDED) != (
            self.superseded_reason is not None
        ):
            raise ValueError("execution-package supersession summary is inconsistent")
        if not self.review_history or self.review_history[-1].package_revision_id != (
            self.package_revision_id
        ):
            raise ValueError("execution-package review history must bind current revision")
        if len({item.review_id for item in self.review_history}) != len(self.review_history):
            raise ValueError("execution-package reviews must be unique")
        return self


class ExecutionEnablementAccessV1(_OperationalLocks):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=_IDENTITY)
    execution_package_creation_authorized: StrictBool
    blocker_codes: tuple[str, ...]
    current_readiness_report_id: str | None = Field(default=None, pattern=_IDENTITY)
    current_readiness_report_revision_id: str | None = Field(default=None, pattern=_IDENTITY)
    current_readiness_source_authority_sha256: str | None = Field(default=None, pattern=_SHA256)
    current_readiness_approval_id: str | None = Field(default=None, pattern=_IDENTITY)
    package: ExecutionPackageV1 | None
    package_history_count: StrictInt = Field(ge=0)
    execution_package_created: StrictBool
    eligible_for_narration_stage_review: StrictBool
    process_local: Literal[True] = True

    @model_validator(mode="after")
    def consistent(self) -> ExecutionEnablementAccessV1:
        if self.execution_package_created != (self.package is not None):
            raise ValueError("execution-package access summary is inconsistent")
        if self.eligible_for_narration_stage_review != (
            self.package is not None and self.package.eligible_for_narration_stage_review
        ):
            raise ValueError("narration-stage eligibility summary is inconsistent")
        if self.execution_package_creation_authorized and self.blocker_codes:
            raise ValueError("authorized package creation cannot contain blockers")
        return self


class CreateExecutionPackageRequestV1(_Contract):
    schema_version: Literal[1]
    readiness_report_revision_id: str = Field(pattern=_IDENTITY)
    readiness_source_authority_sha256: str = Field(pattern=_SHA256)
    readiness_approval_id: str = Field(pattern=_IDENTITY)
    explicit_confirmation: Literal[True]
    note: str | None = Field(default=None, max_length=500)

    @field_validator("note")
    @classmethod
    def safe_note(cls, value: str | None) -> str | None:
        return _safe_note(value)


class MutateExecutionPackageRequestV1(_Contract):
    schema_version: Literal[1]
    package_revision_id: str = Field(pattern=_IDENTITY)
    package_source_authority_sha256: str = Field(pattern=_SHA256)
    explicit_confirmation: Literal[True]
    reason_code: ExecutionPackageReviewReason
    note: str | None = Field(default=None, max_length=500)

    @field_validator("reason_code", mode="before")
    @classmethod
    def json_reason(cls, value: object) -> object:
        if isinstance(value, str):
            return ExecutionPackageReviewReason(value)
        return value

    @field_validator("note")
    @classmethod
    def safe_note(cls, value: str | None) -> str | None:
        return _safe_note(value)


def _safe_note(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        not value.strip()
        or "://" in value
        or "/" in value
        or "\\" in value
        or "<" in value
        or "`" in value
        or "$(" in value
        or any(ord(character) < 32 and character not in "\n\t" for character in value)
    ):
        raise ValueError("execution-package note must be presentation-safe")
    return value


def _timestamp(number: int) -> str:
    return f"2026-01-02T00:{number // 60:02d}:{number % 60:02d}Z"


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
        "package": None,
        "error": {
            "schema_version": 1,
            "code": code,
            "message": message,
            "retryable": False,
        },
    }


class ExecutionEnablementStore:
    """Process-local immutable package authority; performs no execution."""

    def __init__(self) -> None:
        self._histories: dict[str, tuple[ExecutionPackageV1, ...]] = {}
        self._package_identity = 1
        self._revision_identity = 1
        self._review_identity = 1

    def _latest(self, run_id: str) -> ExecutionPackageV1 | None:
        history = self._histories.get(run_id, ())
        return history[-1] if history else None

    @staticmethod
    def _context(
        *,
        run: Mapping[str, Any] | None,
        readiness_access: Mapping[str, Any] | None,
    ) -> tuple[list[str], dict[str, Any] | None]:
        blockers: list[str] = []
        if run is None:
            return ["UNKNOWN_RUN"], None
        if run.get("status") != "PLANNED":
            blockers.append("RUN_NOT_PLANNED")
        report = None if readiness_access is None else readiness_access.get("report")
        if not isinstance(report, Mapping):
            blockers.append("READINESS_REPORT_UNAVAILABLE")
            return blockers, None
        approval = report.get("approval")
        if (
            report.get("current") is not True
            or report.get("status") != "REVIEW_APPROVED_FOR_SEPARATE_TASK"
            or report.get("planning_package_ready") is not True
            or report.get("future_execution_enablement_review_eligible") is not True
            or report.get("blocker_count") != 0
        ):
            blockers.append("READINESS_REPORT_NOT_CURRENTLY_APPROVED")
        limitations = report.get("limitations", ())
        acknowledgements = report.get("acknowledgements", ())
        if (
            not isinstance(limitations, list)
            or not isinstance(acknowledgements, list)
            or not limitations
            or any(item.get("acknowledged") is not True for item in limitations)
            or len(acknowledgements) != len(limitations)
        ):
            blockers.append("READINESS_ACKNOWLEDGEMENTS_INCOMPLETE")
        if (
            not isinstance(approval, Mapping)
            or approval.get("purpose") != "SEPARATE_EXECUTION_ENABLEMENT_TASK_REVIEW"
            or approval.get("approved_for_separate_execution_enablement_review") is not True
            or approval.get("report_revision_id") != report.get("report_revision_id")
            or approval.get("source_authority_sha256") != report.get("source_authority_sha256")
        ):
            blockers.append("READINESS_APPROVAL_UNAVAILABLE")
        lock_keys = (
            "full_render_enabled",
            "render_authority",
            "renderer_execution_authority",
            "video_render_authority",
            "timeline_execution_authority",
            "narration_generation_capability",
            "tts_capability",
            "audio_generation_capability",
            "subtitle_generation_capability",
            "media_muxing_capability",
            "output_creation_capability",
            "execution_job_creation_capability",
            "final_media_capability",
        )
        if any(readiness_access.get(key) is not False for key in lock_keys) or any(
            report.get(key) is not False for key in lock_keys
        ):
            blockers.append("OPERATIONAL_CAPABILITY_UNEXPECTEDLY_ENABLED")
        if blockers or not isinstance(approval, Mapping):
            return list(dict.fromkeys(blockers)), None
        authority = report["authority"]
        scenes = report["scene_checks"]
        package_authority = {
            "readiness_report_id": report["report_id"],
            "readiness_report_revision_id": report["report_revision_id"],
            "readiness_source_authority_sha256": report["source_authority_sha256"],
            "readiness_approval_id": approval["approval_id"],
            "readiness_approval_purpose": approval["purpose"],
            "story_plan_revision_id": authority["story_plan_revision_id"],
            "accepted_story_plan_revision_id": authority["accepted_story_plan_revision_id"],
            "narration_source_sha256": authority["narration_source_sha256"],
            "scene_collection_revision_id": authority["scene_collection_revision_id"],
            "ordered_scene_ids": tuple(item["scene_id"] for item in scenes),
            "ordered_scene_revision_ids": tuple(item["scene_revision_id"] for item in scenes),
            "visual_collection_revision_ids": tuple(authority["visual_collection_revision_ids"]),
            "accepted_candidate_ids": tuple(authority["accepted_candidate_ids"]),
            "accepted_candidate_revision_ids": tuple(authority["accepted_candidate_revision_ids"]),
            "accepted_candidate_artifact_sha256s": tuple(
                authority["accepted_candidate_artifact_sha256s"]
            ),
            "accepted_candidate_mimes": tuple(item["artifact_mime"] for item in scenes),
            "accepted_candidate_dimensions": tuple(
                (item["artifact_width"], item["artifact_height"]) for item in scenes
            ),
            "composition_collection_revision_id": authority["composition_collection_revision_id"],
            "composition_ids": tuple(authority["composition_ids"]),
            "composition_revision_ids": tuple(authority["composition_revision_ids"]),
            "timeline_collection_revision_id": authority["timeline_collection_revision_id"],
            "accepted_timeline_revision_id": authority["accepted_timeline_revision_id"],
            "timeline_source_authority_sha256": authority["timeline_source_authority_sha256"],
            "effective_timeline_duration_ms": report["total_effective_timeline_duration_ms"],
        }
        fingerprint_projection = {
            "authority": package_authority,
            "execution_enablement_contract_version": "execution_enablement_v1",
            "operational_capability_locks": {
                "full_render_enabled": False,
                "narration_generation_capability": False,
                "tts_capability": False,
                "audio_generation_capability": False,
                "audio_measurement_capability": False,
                "timeline_execution_authority": False,
                "renderer_execution_authority": False,
                "render_authority": False,
                "video_render_authority": False,
                "subtitle_generation_capability": False,
                "media_muxing_capability": False,
                "output_creation_capability": False,
                "execution_job_creation_capability": False,
                "final_media_capability": False,
            },
        }
        return [], {
            "report": report,
            "approval": approval,
            "authority": package_authority,
            "package_source_authority_sha256": _canonical_sha256(fingerprint_projection),
        }

    def _supersede(self, current: ExecutionPackageV1) -> ExecutionPackageV1:
        revision_id = f"execution-package-revision-{self._revision_identity:04d}"
        review = ExecutionPackageReviewEntryV1(
            review_id=f"execution-package-review-{self._review_identity:04d}",
            package_revision_id=revision_id,
            package_source_authority_sha256=current.package_source_authority_sha256,
            action="PACKAGE_SUPERSEDED",
            created_at=_timestamp(self._review_identity),
        )
        payload = current.model_dump(mode="python")
        payload.update(
            package_revision_id=revision_id,
            package_revision_number=current.package_revision_number + 1,
            status=ExecutionPackageStatus.SUPERSEDED,
            current=False,
            eligible_for_narration_stage_review=False,
            superseded_reason="BOUND_EXECUTION_PACKAGE_AUTHORITY_CHANGED",
            review_history=(*current.review_history, review),
            created_at=_timestamp(self._revision_identity),
        )
        self._revision_identity += 1
        self._review_identity += 1
        superseded = ExecutionPackageV1.model_validate(payload)
        self._histories[current.run_id] = (*self._histories[current.run_id], superseded)
        return superseded

    def access(
        self,
        *,
        run: Mapping[str, Any] | None,
        readiness_access: Mapping[str, Any] | None,
    ) -> dict[str, object] | None:
        if run is None:
            return None
        run_id = str(run["run_id"])
        blockers, context = self._context(run=run, readiness_access=readiness_access)
        latest = self._latest(run_id)
        if (
            latest is not None
            and latest.current
            and (
                context is None
                or latest.package_source_authority_sha256
                != context["package_source_authority_sha256"]
            )
        ):
            latest = self._supersede(latest)
        valid_context = not blockers and context is not None
        creation_authorized = valid_context and (
            latest is None
            or (
                not latest.current
                and latest.package_source_authority_sha256
                != context["package_source_authority_sha256"]
            )
        )
        report = None if context is None else context["report"]
        approval = None if context is None else context["approval"]
        return _detached(
            ExecutionEnablementAccessV1(
                run_id=run_id,
                execution_package_creation_authorized=creation_authorized,
                blocker_codes=tuple(blockers),
                current_readiness_report_id=(None if report is None else report["report_id"]),
                current_readiness_report_revision_id=(
                    None if report is None else report["report_revision_id"]
                ),
                current_readiness_source_authority_sha256=(
                    None if report is None else report["source_authority_sha256"]
                ),
                current_readiness_approval_id=(
                    None if approval is None else approval["approval_id"]
                ),
                package=latest,
                package_history_count=len(self._histories.get(run_id, ())),
                execution_package_created=latest is not None,
                eligible_for_narration_stage_review=(
                    latest is not None and latest.eligible_for_narration_stage_review
                ),
            )
        )

    def create(
        self,
        *,
        run: Mapping[str, Any] | None,
        readiness_access: Mapping[str, Any] | None,
        payload: object,
    ) -> dict[str, object]:
        try:
            request = CreateExecutionPackageRequestV1.model_validate(payload)
        except ValidationError:
            return _error(
                "INVALID_EXECUTION_PACKAGE_REQUEST",
                "Execution-package creation request is malformed.",
            )
        blockers, context = self._context(run=run, readiness_access=readiness_access)
        if run is None or blockers or context is None:
            return _error(
                "EXECUTION_PACKAGE_CREATION_NOT_AUTHORIZED",
                "Current readiness approval does not authorize package creation.",
            )
        report = context["report"]
        approval = context["approval"]
        if (
            request.readiness_report_revision_id != report["report_revision_id"]
            or request.readiness_source_authority_sha256 != report["source_authority_sha256"]
            or request.readiness_approval_id != approval["approval_id"]
        ):
            return _error(
                "STALE_EXECUTION_PACKAGE_AUTHORITY",
                "Readiness authority changed before package creation.",
            )
        run_id = str(run["run_id"])
        latest = self._latest(run_id)
        if (
            latest is not None
            and latest.package_source_authority_sha256 == context["package_source_authority_sha256"]
        ):
            return _error(
                "EXECUTION_PACKAGE_ALREADY_CREATED",
                "An execution package already exists for this readiness authority.",
            )
        if latest is not None and latest.current:
            self._supersede(latest)
        revision_id = f"execution-package-revision-{self._revision_identity:04d}"
        review = ExecutionPackageReviewEntryV1(
            review_id=f"execution-package-review-{self._review_identity:04d}",
            package_revision_id=revision_id,
            package_source_authority_sha256=context["package_source_authority_sha256"],
            action="PACKAGE_CREATED",
            note=request.note,
            created_at=_timestamp(self._review_identity),
        )
        package = ExecutionPackageV1(
            package_id=f"execution-package-{self._package_identity:04d}",
            package_revision_id=revision_id,
            package_revision_number=1,
            run_id=run_id,
            status=ExecutionPackageStatus.SEALED_FOR_NARRATION_STAGE_REVIEW,
            current=True,
            eligible_for_narration_stage_review=True,
            package_source_authority_sha256=context["package_source_authority_sha256"],
            authority=ExecutionPackageAuthorityV1.model_validate(context["authority"]),
            review_history=(review,),
            created_at=_timestamp(self._revision_identity),
        )
        self._package_identity += 1
        self._revision_identity += 1
        self._review_identity += 1
        self._histories[run_id] = (*self._histories.get(run_id, ()), package)
        return {
            "schema_version": 1,
            "access": None,
            "package": _detached(package),
            "error": None,
        }

    def mutate(
        self,
        run_id: str,
        operation: Literal["request-revision", "cancel-package"],
        payload: object,
    ) -> dict[str, object]:
        try:
            request = MutateExecutionPackageRequestV1.model_validate(payload)
        except ValidationError:
            return _error(
                "INVALID_EXECUTION_PACKAGE_MUTATION",
                "Execution-package mutation request is malformed.",
            )
        current = self._latest(run_id)
        if (
            current is None
            or not current.current
            or current.package_revision_id != request.package_revision_id
            or current.package_source_authority_sha256 != request.package_source_authority_sha256
        ):
            return _error(
                "STALE_EXECUTION_PACKAGE",
                "Execution package is stale or unavailable.",
            )
        revision_id = f"execution-package-revision-{self._revision_identity:04d}"
        cancelled = operation == "cancel-package"
        review = ExecutionPackageReviewEntryV1(
            review_id=f"execution-package-review-{self._review_identity:04d}",
            package_revision_id=revision_id,
            package_source_authority_sha256=current.package_source_authority_sha256,
            action="PACKAGE_CANCELLED" if cancelled else "REVISION_REQUESTED",
            reason_code=request.reason_code,
            note=request.note,
            created_at=_timestamp(self._review_identity),
        )
        payload_data = current.model_dump(mode="python")
        payload_data.update(
            package_revision_id=revision_id,
            package_revision_number=current.package_revision_number + 1,
            status=(
                ExecutionPackageStatus.CANCELLED
                if cancelled
                else ExecutionPackageStatus.REVISION_REQUESTED
            ),
            current=not cancelled,
            eligible_for_narration_stage_review=False,
            review_history=(*current.review_history, review),
            created_at=_timestamp(self._revision_identity),
        )
        self._revision_identity += 1
        self._review_identity += 1
        updated = ExecutionPackageV1.model_validate(payload_data)
        self._histories[run_id] = (*self._histories[run_id], updated)
        return {
            "schema_version": 1,
            "access": None,
            "package": _detached(updated),
            "error": None,
        }

    def history(self, run_id: str) -> dict[str, object] | None:
        history = self._histories.get(run_id)
        if not history:
            return None
        return {
            "schema_version": 1,
            "run_id": run_id,
            "packages": [_detached(item) for item in history],
        }

    def package(self, run_id: str, package_revision_id: str) -> dict[str, object] | None:
        selected = next(
            (
                item
                for item in self._histories.get(run_id, ())
                if item.package_revision_id == package_revision_id
            ),
            None,
        )
        return None if selected is None else _detached(selected)
