"""Auditable offline runtime contracts for topic-production execution and QC."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tella.visual_generation.providers.kinds import ProviderKind

from .duration_policy import DurationValueAuthority, MvpDurationTargetAssessment
from .execution_models import ProductionRunPlan, SceneExecutionPlan
from .models import GenerationTier, ProductionSceneStatus, ReadinessResult
from .pollinations_readiness import PollinationsReadinessSnapshot
from .story_plan_identity import canonical_story_plan_sha256


class TechnicalStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    TECHNICAL_GENERATION_FAIL = "TECHNICAL_GENERATION_FAIL"
    PROVIDER_QUOTA_BLOCKED = "PROVIDER_QUOTA_BLOCKED"
    REFERENCE_BLOCKED = "REFERENCE_BLOCKED"


class QCDecision(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class ReviewSource(StrEnum):
    DETERMINISTIC_STRUCTURAL = "deterministic_structural"
    VISION_MODEL = "vision_model"
    HUMAN = "human"


class QCCheckOutcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NOT_EVALUATED = "NOT_EVALUATED"


class FailureReason(StrEnum):
    PROVIDER_QUOTA_BLOCKED = "PROVIDER_QUOTA_BLOCKED"
    TECHNICAL_GENERATION_FAIL = "TECHNICAL_GENERATION_FAIL"
    DRAFT_QC_FAIL = "DRAFT_QC_FAIL"
    ACCEPTANCE_QC_FAIL = "ACCEPTANCE_QC_FAIL"
    REFERENCE_BLOCKED = "REFERENCE_BLOCKED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    PROMOTION_REQUIRED = "PROMOTION_REQUIRED"


class PromotionReason(StrEnum):
    HIGH_ACCEPTANCE_PRIORITY = "high_acceptance_priority"
    IDENTITY_CONTINUITY = "identity_continuity"
    COMPLEX_ANATOMY = "complex_anatomy"
    RELATIONSHIP_SCENE = "relationship_scene"
    EMOTIONAL_METAPHOR = "emotional_metaphor"
    HUMAN_REQUESTED_UPGRADE = "human_requested_upgrade"
    DRAFT_QUALITY_INSUFFICIENT = "draft_quality_insufficient"


class EventType(StrEnum):
    PLANNED = "planned"
    DRAFT_GENERATED = "draft_generated"
    DRAFT_GENERATION_FAILED = "draft_generation_failed"
    DRAFT_QC = "draft_qc"
    DRAFT_ACCEPTANCE_AUTHORIZED = "draft_acceptance_authorized"
    PROMOTED = "promotion"
    ACCEPTANCE_GENERATED = "acceptance_generated"
    ACCEPTANCE_GENERATION_FAILED = "acceptance_generation_failed"
    ACCEPTANCE_QC = "acceptance_qc"
    ACCEPTED = "accepted"
    BLOCKED = "blocked"


class ResumeAction(StrEnum):
    EXECUTE_DRAFT = "EXECUTE_DRAFT"
    AWAIT_DRAFT_QC = "AWAIT_DRAFT_QC"
    AWAIT_EXPLICIT_TIER_DECISION = "AWAIT_EXPLICIT_TIER_DECISION"
    EXECUTE_ACCEPTANCE = "EXECUTE_ACCEPTANCE"
    AWAIT_ACCEPTANCE_QC = "AWAIT_ACCEPTANCE_QC"
    READY_FOR_EXPLICIT_ACCEPTANCE = "READY_FOR_EXPLICIT_ACCEPTANCE"
    AWAIT_EXPLICIT_INTERVENTION = "AWAIT_EXPLICIT_INTERVENTION"
    SKIP_ACCEPTED = "SKIP_ACCEPTED"
    REMAIN_BLOCKED = "REMAIN_BLOCKED"
    COMPLETE_VOLUME_ACCEPTANCE = "COMPLETE_VOLUME_ACCEPTANCE"
    RETRY_VOLUME = "RETRY_VOLUME"


class GenerationAttempt(BaseModel):
    model_config = ConfigDict(frozen=True)

    scene_id: str = Field(pattern=r"^scene_[0-9]{2}$")
    tier: GenerationTier
    provider: str = Field(min_length=1)
    provider_kind: ProviderKind | None = None
    model: str = Field(min_length=1)
    seed: int = Field(ge=0)
    candidate_id: str = Field(min_length=1)
    candidate_path: str | None = None
    artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    planning_request_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    logical_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_request_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reference_hashes: list[str] = Field(default_factory=list)
    started_at: str | None = None
    completed_at: str | None = None
    technical_status: TechnicalStatus
    technical_failure_reason: str | None = None
    simulated: bool = False
    consumes_ai_call: bool = True
    consumes_ai_retry: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("reference_hashes")
    @classmethod
    def validate_reference_hashes(cls, values: list[str]) -> list[str]:
        if any(
            len(value) != 64 or any(c not in "0123456789abcdef" for c in value) for value in values
        ):
            raise ValueError("reference hashes must be lowercase SHA-256 digests")
        return values

    @model_validator(mode="after")
    def validate_technical_result(self) -> "GenerationAttempt":
        if self.provider_kind is ProviderKind.LOCAL_COMPOSITOR:
            if self.provider != ProviderKind.LOCAL_COMPOSITOR.value:
                raise ValueError("local provider identity mismatch")
            if self.consumes_ai_call or self.consumes_ai_retry:
                raise ValueError("local compositor cannot consume AI call or retry budget")
            if self.provider_request_hash is not None:
                raise ValueError("local compositor cannot have external provider request metadata")
        if self.provider_kind is ProviderKind.POLLINATIONS:
            if self.provider != ProviderKind.POLLINATIONS.value:
                raise ValueError("Pollinations provider identity mismatch")
            if not self.consumes_ai_call:
                raise ValueError("Pollinations attempts must consume an AI call")
            if self.reference_hashes:
                raise ValueError("Pollinations attempts cannot contain reference hashes")
            if self.provider_request_hash is None:
                raise ValueError("Pollinations attempt requires a sanitized request hash")
        if self.technical_status is TechnicalStatus.SUCCEEDED:
            if not self.candidate_path or not self.artifact_sha256:
                raise ValueError("successful generation requires artifact path and SHA-256")
            if self.technical_failure_reason:
                raise ValueError("successful generation cannot have a technical failure reason")
        elif not self.technical_failure_reason:
            raise ValueError("unsuccessful generation requires a technical failure reason")
        return self


class QCChecks(BaseModel):
    model_config = ConfigDict(frozen=True)

    technical_generation: QCCheckOutcome = QCCheckOutcome.NOT_EVALUATED
    scene_meaning: QCCheckOutcome = QCCheckOutcome.NOT_EVALUATED
    identity: QCCheckOutcome = QCCheckOutcome.NOT_EVALUATED
    action_pose: QCCheckOutcome = QCCheckOutcome.NOT_EVALUATED
    anatomy: QCCheckOutcome = QCCheckOutcome.NOT_EVALUATED
    composition: QCCheckOutcome = QCCheckOutcome.NOT_EVALUATED
    reference_consistency: QCCheckOutcome = QCCheckOutcome.NOT_EVALUATED


class QCRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    qc_record_id: str = Field(min_length=1)
    scene_id: str = Field(pattern=r"^scene_[0-9]{2}$")
    candidate_id: str = Field(min_length=1)
    tier: GenerationTier
    decision: QCDecision
    checks: QCChecks = Field(default_factory=QCChecks)
    scores: dict[str, float] = Field(default_factory=dict)
    hard_fail_reasons: list[str] = Field(default_factory=list)
    soft_fail_reasons: list[str] = Field(default_factory=list)
    review_source: ReviewSource
    reviewer: str = Field(min_length=1)
    reviewed_at: str | None = None
    notes: str = ""
    review_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_review_boundary(self) -> "QCRecord":
        if (
            self.review_source is ReviewSource.DETERMINISTIC_STRUCTURAL
            and self.decision is QCDecision.PASS
        ):
            raise ValueError("deterministic structural QC cannot claim subjective visual PASS")
        if self.decision is QCDecision.PASS and self.hard_fail_reasons:
            raise ValueError("QC PASS cannot retain hard-fail reasons")
        if (
            self.decision is QCDecision.PASS
            and self.checks.technical_generation is not QCCheckOutcome.PASS
        ):
            raise ValueError("QC PASS requires an explicit technical-generation PASS check")
        return self


class PromotionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    scene_id: str
    reason: PromotionReason
    authorized_by: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DraftAcceptanceAuthorization(BaseModel):
    model_config = ConfigDict(frozen=True)

    scene_id: str
    reason: str = Field(min_length=1)
    authorized_by: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AcceptedCandidateRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    scene_id: str
    candidate_id: str
    artifact_path: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_tier: GenerationTier
    provider: str
    model: str
    seed: int
    logical_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    planning_request_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    provider_request_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reference_hashes: list[str] = Field(default_factory=list)
    qc_record_id: str = Field(min_length=1)
    qc_decision: Literal[QCDecision.PASS] = QCDecision.PASS
    accepted_at: str | None = None
    accepted_by: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int = Field(ge=1)
    scene_id: str
    event_type: EventType
    detail: dict[str, Any] = Field(default_factory=dict)
    occurred_at: str | None = None


class SceneRuntimeState(BaseModel):
    model_config = ConfigDict(frozen=True)

    execution_plan: SceneExecutionPlan
    status: ProductionSceneStatus = ProductionSceneStatus.DRAFT_PENDING
    generation_attempts: list[GenerationAttempt] = Field(default_factory=list)
    qc_records: list[QCRecord] = Field(default_factory=list)
    promotions: list[PromotionRecord] = Field(default_factory=list)
    draft_acceptance_authorizations: list[DraftAcceptanceAuthorization] = Field(
        default_factory=list
    )
    accepted_candidate: AcceptedCandidateRecord | None = None
    block_reasons: list[FailureReason] = Field(default_factory=list)

    @property
    def scene_id(self) -> str:
        return self.execution_plan.scene_id


class ProcessedNarrationDurationMeasurement(BaseModel):
    """Artifact-bound measured duration supplied by a later filesystem boundary."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
    )

    schema_version: Literal[1]
    artifact_relative_path: str
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    story_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    measurement_method: Literal["ffprobe_single_audio_stream_v1"]
    measured_duration_assessment: MvpDurationTargetAssessment

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError(
                "ProcessedNarrationDurationMeasurement schema_version must be exact integer 1"
            )
        return value

    @field_validator("artifact_relative_path", mode="before")
    @classmethod
    def validate_artifact_relative_path(cls, value: object) -> object:
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError("artifact_relative_path must be a non-empty string")
        if "\\" in value:
            raise ValueError("artifact_relative_path must use forward slashes")
        posix_path = PurePosixPath(value)
        windows_path = PureWindowsPath(value)
        if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
            raise ValueError("artifact_relative_path must be job-relative")
        if value == "." or any(part in {".", ".."} for part in posix_path.parts):
            raise ValueError("artifact_relative_path cannot contain traversal segments")
        if posix_path.as_posix() != value:
            raise ValueError("artifact_relative_path must use canonical POSIX form")
        return value

    @field_validator("measured_duration_assessment", mode="before")
    @classmethod
    def detach_assessment(cls, value: object) -> object:
        if isinstance(value, MvpDurationTargetAssessment):
            return value.model_dump(mode="python")
        return deepcopy(value)

    @model_validator(mode="after")
    def validate_measured_authority(self) -> "ProcessedNarrationDurationMeasurement":
        if self.measured_duration_assessment.value_authority is not DurationValueAuthority.MEASURED:
            raise ValueError("processed narration duration requires MEASURED authority")
        return self

    def model_copy(
        self,
        *,
        update: dict[str, Any] | None = None,
        deep: bool = False,
    ) -> "ProcessedNarrationDurationMeasurement":
        if update is not None and "schema_version" in update:
            raise ValueError(
                "ProcessedNarrationDurationMeasurement schema_version "
                "cannot be changed through model_copy"
            )
        payload = self.model_dump(mode="python")
        if deep:
            payload = deepcopy(payload)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)


class ExecutionRunState(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
    )

    schema_version: Literal[2]
    run_plan: ProductionRunPlan
    scenes: list[SceneRuntimeState]
    event_history: list[ExecutionEvent]
    processed_narration_measurement: ProcessedNarrationDurationMeasurement | None
    external_calls: int = Field(default=0, ge=0)
    readiness_external_calls: int = Field(default=0, ge=0)
    pollinations_readiness: PollinationsReadinessSnapshot | None = None

    @model_validator(mode="before")
    @classmethod
    def require_current_schema(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        if "schema_version" not in value:
            raise ValueError("ExecutionRunState schema_version is required")
        version = value["schema_version"]
        if type(version) is not int:
            raise ValueError("ExecutionRunState schema_version must be an integer")
        if version != 2:
            raise ValueError(f"unsupported ExecutionRunState schema_version: {version}")
        if "processed_narration_measurement" not in value:
            raise ValueError("ExecutionRunState processed_narration_measurement is required")
        return value

    @field_validator("processed_narration_measurement", mode="before")
    @classmethod
    def detach_measurement(cls, value: object) -> object:
        if isinstance(value, ProcessedNarrationDurationMeasurement):
            return value.model_dump(mode="python")
        return deepcopy(value)

    @model_validator(mode="after")
    def validate_measurement_story_identity(self) -> "ExecutionRunState":
        measurement = self.processed_narration_measurement
        if measurement is not None and measurement.story_plan_sha256 != (
            canonical_story_plan_sha256(self.run_plan.story_plan)
        ):
            raise ValueError("processed narration measurement StoryPlan SHA-256 does not match")
        return self

    @classmethod
    def migrate_schema_v1(cls, value: Mapping[str, object]) -> "ExecutionRunState":
        """Explicitly migrate one raw persisted schema-1 mapping."""

        if not isinstance(value, Mapping) or isinstance(value, BaseModel):
            raise TypeError("ExecutionRunState schema-1 migration requires a raw mapping")
        if "schema_version" not in value:
            raise ValueError("ExecutionRunState schema_version is required")
        version = value["schema_version"]
        if type(version) is not int:
            raise ValueError("ExecutionRunState schema_version must be an integer")
        if version != 1:
            raise ValueError("ExecutionRunState schema-1 migration requires schema_version 1")
        if "processed_narration_measurement" in value:
            raise ValueError("ExecutionRunState schema 1 cannot contain measured authority fields")
        migrated = dict(value)
        migrated.update(
            {
                "schema_version": 2,
                "processed_narration_measurement": None,
            }
        )
        return cls.model_validate(migrated)

    def model_copy(
        self,
        *,
        update: dict[str, Any] | None = None,
        deep: bool = False,
    ) -> "ExecutionRunState":
        if update is not None and "schema_version" in update:
            raise ValueError(
                "ExecutionRunState schema_version cannot be changed through model_copy"
            )
        payload = self.model_dump(mode="python")
        if deep:
            payload = deepcopy(payload)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)


class SceneCallBudget(BaseModel):
    model_config = ConfigDict(frozen=True)

    scene_id: str
    draft_max_calls: int = Field(default=1, ge=0, le=2)
    acceptance_max_calls: int = Field(ge=0, le=1)
    draft_completed_calls: int = Field(ge=0, le=2)
    acceptance_completed_calls: int = Field(ge=0, le=1)
    retry_calls: int = Field(default=0, ge=0, le=1)
    fallback_calls: Literal[0] = 0


class CallBudgetSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenes: list[SceneCallBudget]
    planned_draft_calls: int = Field(ge=0)
    currently_authorized_acceptance_calls: int = Field(ge=0)
    completed_calls: int = Field(ge=0)
    remaining_authorized_calls: int = Field(ge=0)
    retry_calls: int = Field(default=0, ge=0)
    fallback_calls: Literal[0] = 0


class SceneResumePlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    scene_id: str
    action: ResumeAction
    reason: str


class ResumePlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenes: list[SceneResumePlan]
    external_calls: int = Field(default=0, ge=0, le=0)


class QCEvaluator(Protocol):
    """Extension point; implementations must declare honest review provenance."""

    def evaluate(self, *, attempt: GenerationAttempt, scene: SceneExecutionPlan) -> QCRecord: ...


class ExecutionReadiness(BaseModel):
    model_config = ConfigDict(frozen=True)

    result: ReadinessResult
    accepted_candidate_paths: dict[str, str] = Field(default_factory=dict)


class OfflineSimulationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    state_before_last_acceptance: ExecutionRunState
    final_state: ExecutionRunState
    readiness_before_last_acceptance: ReadinessResult
    final_readiness: ReadinessResult
    final_call_budget: CallBudgetSummary
    external_calls: int = Field(default=0, ge=0, le=0)
