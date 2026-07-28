"""Detached planning-only application contract for UI presentation.

This boundary authorizes deterministic topic planning only. Its concrete
producer is explicitly not eligible for live production or rendering.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
    model_validator,
)

from tella.media.image_provider import get_image_provider

from .duration_policy import (
    DurationAssessmentStatus,
    DurationValueAuthority,
    assess_beat_duration_pacing,
    assess_mvp_duration_target,
)
from .execution_models import ExecutionMode
from .identity_eligibility import IdentityEligibilityError, require_supported_identity
from .models import PlannerMetadata, PlannerMode, StoryPlan
from .planner import DeterministicTopicPlanner
from .script_input import (
    CharacterRoleRequest,
    CharacterScopeRequest,
    NormalizedScriptInput,
    RecurringCharacterRequest,
    RelationshipScope,
    ScriptInputMode,
    TopicScriptInput,
    normalize_script_input,
)
from .scene_planning import ScenePlanningStore
from .composition_planning import CompositionPlanningStore
from .story_plan_producer import ApprovedStoryPlanProducer
from .timing import build_scene_timings
from .visual_candidates import (
    VisualArtifact,
    VisualCandidateProvider,
    VisualCandidateStore,
)


PLAN_ONLY_API_PREFIX = "/api/v1/plan-only"
_RUN_ID_PATTERN = r"^plan-[0-9a-f]{20}$"
_RENDER_LOCK_REASONS = (
    "BACKEND_RENDER_CAPABILITY_DISABLED",
    "SYNTHETIC_CLOSURE_PENDING",
    "LIVE_CANARY_PENDING",
    "PLAN_ONLY_NO_RENDER_AUTHORITY",
)


class PlanOnlyStatus(StrEnum):
    PLANNED = "PLANNED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class _ContractModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
    )


class PlanOnlyCreateRequestV1(_ContractModel):
    schema_version: Literal[1]
    input_mode: Literal["TOPIC", "NARRATION", "NARRATION_WITH_SCENE_HINTS"]
    source_content: str = Field(min_length=1, max_length=5000)
    language: Literal["en", "vi"]
    character_scope: Literal[
        "recurring_female",
        "female_with_anonymous_background",
        "recurring_male",
        "family",
        "unresolved",
    ]
    requested_scene_count: Literal[7, 8] = 8

    @model_validator(mode="before")
    @classmethod
    def require_exact_schema_version(cls, value: Any) -> Any:
        if isinstance(value, Mapping) and type(value.get("schema_version")) is not int:
            raise ValueError("schema_version must be the exact integer 1")
        return value


class PublicConditionV1(_ContractModel):
    code: str = Field(min_length=1)
    title: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    severity: Literal["INFO", "WARNING", "BLOCKED", "ERROR"]
    scene_ids: tuple[str, ...] = ()
    beat_ids: tuple[str, ...] = ()


class SemanticBeatViewV1(_ContractModel):
    beat_id: str = Field(pattern=r"^beat_[0-9]{2}$")
    order: int = Field(ge=1, le=8)
    narration_segment: str = Field(min_length=1)
    semantic_purpose: str = Field(min_length=1)
    emotional_state: str = Field(min_length=1)
    visual_intent: str = Field(min_length=1)
    duration_seconds: float = Field(gt=0, allow_inf_nan=False)


class SceneViewV1(_ContractModel):
    scene_id: str = Field(pattern=r"^scene_[0-9]{2}$")
    order: int = Field(ge=1, le=8)
    source_beat_id: str = Field(pattern=r"^beat_[0-9]{2}$")
    meaning: str = Field(min_length=1)
    emotional_tone: tuple[str, ...] = Field(min_length=1)
    visual_intent: str = Field(min_length=1)
    planned_duration_seconds: float = Field(gt=0, allow_inf_nan=False)


class StoryPlanViewV1(_ContractModel):
    schema_version: Literal[1] = 1
    topic: str = Field(min_length=1)
    language: str = Field(min_length=2, max_length=12)
    aspect_ratio: Literal["9:16"] = "9:16"
    target_duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    narration_text: str = Field(min_length=1)
    emotional_arc: tuple[str, ...] = Field(min_length=3)
    semantic_beats: tuple[SemanticBeatViewV1, ...]
    scenes: tuple[SceneViewV1, ...]

    @model_validator(mode="after")
    def validate_ordering(self) -> "StoryPlanViewV1":
        expected = list(range(1, len(self.semantic_beats) + 1))
        if [item.order for item in self.semantic_beats] != expected:
            raise ValueError("semantic beats must remain in canonical order")
        if [item.order for item in self.scenes] != expected:
            raise ValueError("scenes must remain in canonical order")
        if [item.source_beat_id for item in self.scenes] != [
            item.beat_id for item in self.semantic_beats
        ]:
            raise ValueError("scenes must map one-to-one to semantic beats")
        return self


class TimelineRowV1(_ContractModel):
    scene_id: str = Field(pattern=r"^scene_[0-9]{2}$")
    order: int = Field(ge=1, le=8)
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    narration_slot_duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    render_clip_duration_seconds: None = None
    end_seconds: float = Field(gt=0, allow_inf_nan=False)


class TimelineViewV1(_ContractModel):
    schema_version: Literal[1] = 1
    authority: Literal["PLANNED"] = "PLANNED"
    transition_profile_id: None = None
    configured_transition_seconds: None = None
    effective_transition_seconds: None = None
    total_duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    rows: tuple[TimelineRowV1, ...]

    @model_validator(mode="after")
    def validate_rows(self) -> "TimelineViewV1":
        expected = list(range(1, len(self.rows) + 1))
        if [row.order for row in self.rows] != expected:
            raise ValueError("timeline rows must remain in canonical order")
        if not self.rows or self.rows[0].start_seconds != 0:
            raise ValueError("timeline must start at zero")
        if any(
            row.start_seconds != self.rows[index - 1].end_seconds
            for index, row in enumerate(self.rows)
            if index > 0
        ):
            raise ValueError("timeline rows must remain contiguous")
        if self.total_duration_seconds != self.rows[-1].end_seconds:
            raise ValueError("timeline total must equal the final row end")
        return self


class WarningCollectionV1(_ContractModel):
    schema_version: Literal[1] = 1
    warning_count: int = Field(ge=0)
    conditions: tuple[PublicConditionV1, ...]

    @model_validator(mode="after")
    def validate_count(self) -> "WarningCollectionV1":
        if self.warning_count != len(self.conditions):
            raise ValueError("warning_count must equal conditions length")
        return self


class RenderReadinessV1(_ContractModel):
    ready: Literal[False] = False
    reason_codes: tuple[str, ...] = _RENDER_LOCK_REASONS


class NormalizedInputViewV1(_ContractModel):
    input_mode: Literal["TOPIC"]
    source_content: str = Field(min_length=1)
    language: str = Field(min_length=2, max_length=12)
    visual_mode: Literal["illustrated_scene"] = "illustrated_scene"
    character_scope: Literal[
        "recurring_female",
        "female_with_anonymous_background",
    ]


class ProductionRunViewV1(_ContractModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=_RUN_ID_PATTERN)
    execution_mode: Literal["PLAN_ONLY"] = "PLAN_ONLY"
    status: Literal[PlanOnlyStatus.PLANNED] = PlanOnlyStatus.PLANNED
    current_stage: Literal["planned"] = "planned"
    created_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    updated_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    normalized_input: NormalizedInputViewV1
    story_plan: StoryPlanViewV1
    timeline: TimelineViewV1
    warnings: WarningCollectionV1
    render_readiness: RenderReadinessV1


class ProductionRunSummaryV1(_ContractModel):
    run_id: str = Field(pattern=_RUN_ID_PATTERN)
    execution_mode: Literal["PLAN_ONLY"] = "PLAN_ONLY"
    status: Literal[PlanOnlyStatus.PLANNED] = PlanOnlyStatus.PLANNED
    current_stage: Literal["planned"] = "planned"
    warning_count: int = Field(ge=0)
    created_at: str
    updated_at: str


class ProductionDashboardViewV1(_ContractModel):
    schema_version: Literal[1] = 1
    runs: tuple[ProductionRunSummaryV1, ...]


class ProductionCapabilitiesV1(_ContractModel):
    schema_version: Literal[1] = 1
    supported_execution_modes: tuple[Literal["PLAN_ONLY"], ...] = ("PLAN_ONLY",)
    supported_input_modes: tuple[Literal["TOPIC"], ...] = ("TOPIC",)
    supported_languages: tuple[Literal["vi", "en"], ...] = ("vi", "en")
    supported_aspect_ratios: tuple[Literal["9:16"], ...] = ("9:16",)
    supported_visual_modes: tuple[Literal["illustrated_scene"], ...] = ("illustrated_scene",)
    supported_character_scopes: tuple[
        Literal["recurring_female", "female_with_anonymous_background"], ...
    ] = ("recurring_female", "female_with_anonymous_background")
    backend_render_capability: StrictBool = False
    synthetic_closure_passed: StrictBool = False
    live_canary_passed: StrictBool = False
    full_render_enabled: StrictBool = False
    render_lock_reason_codes: tuple[str, ...] = _RENDER_LOCK_REASONS

    @model_validator(mode="after")
    def require_render_lock(self) -> "ProductionCapabilitiesV1":
        if any(
            (
                self.backend_render_capability,
                self.synthetic_closure_passed,
                self.live_canary_passed,
                self.full_render_enabled,
            )
        ):
            raise ValueError("PLAN_ONLY capabilities cannot grant render readiness")
        return self


class PublicFieldErrorV1(_ContractModel):
    path: str
    code: str
    message: str


class PublicApiErrorV1(_ContractModel):
    schema_version: Literal[1] = 1
    request_id: str = Field(pattern=r"^request-[0-9a-f]{16}$")
    status: Literal["BLOCKED", "FAILED"]
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: StrictBool
    field_errors: tuple[PublicFieldErrorV1, ...] = ()
    details: dict[str, str] = Field(default_factory=dict)


class PlanOnlyCreateResultV1(_ContractModel):
    schema_version: Literal[1] = 1
    run: ProductionRunViewV1 | None = None
    error: PublicApiErrorV1 | None = None

    @model_validator(mode="after")
    def require_one_result(self) -> "PlanOnlyCreateResultV1":
        if (self.run is None) == (self.error is None):
            raise ValueError("exactly one of run or error is required")
        return self


class StoryPlanReviewStatus(StrEnum):
    UNREVIEWED = "UNREVIEWED"
    ACCEPTED_FOR_SCENE_PLANNING = "ACCEPTED_FOR_SCENE_PLANNING"
    REPLAN_REQUESTED = "REPLAN_REQUESTED"


class ReplanFeedbackDimension(StrEnum):
    NARRATION_TOO_SHORT = "narration_too_short"
    NARRATION_TOO_LONG = "narration_too_long"
    STORY_FOCUS_INCORRECT = "story_focus_incorrect"
    EMOTIONAL_PROGRESSION_WEAK = "emotional_progression_weak"
    CHARACTER_SCOPE_INCORRECT = "character_scope_incorrect"
    SCENE_COUNT_UNSUITABLE = "scene_count_unsuitable"
    CUSTOM_NOTE = "custom_note"


_REPLAN_FEEDBACK_ORDER = tuple(ReplanFeedbackDimension)


class NarrationSourceSpanViewV1(_ContractModel):
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(gt=0)

    @model_validator(mode="after")
    def validate_interval(self) -> "NarrationSourceSpanViewV1":
        if self.end <= self.start:
            raise ValueError("source span end must be greater than start")
        return self


class ReviewSemanticBeatV1(_ContractModel):
    beat_id: str = Field(pattern=r"^beat_[0-9]{2}$")
    order: StrictInt = Field(ge=1, le=8)
    source_span: NarrationSourceSpanViewV1
    narration_segment: str = Field(min_length=1)
    semantic_purpose: str = Field(min_length=1)
    emotional_state: str = Field(min_length=1)
    transition_intent: str = Field(min_length=1)
    visual_intent: str = Field(min_length=1)
    duration_seconds: float = Field(gt=0, allow_inf_nan=False)


class PlannerMetadataViewV1(_ContractModel):
    planner_id: str = Field(min_length=1)
    planner_version: str = Field(min_length=1)
    deterministic: Literal[True] = True
    external_calls: Literal[0] = 0
    production_eligible: Literal[False] = False
    story_planning_authorized: Literal[True] = True


class DurationAssessmentViewV1(_ContractModel):
    policy_id: Literal["mvp_emotional_duration_32_38_v1"]
    status: Literal["IN_TARGET", "OUTSIDE_TARGET_WARNING"]
    reason_code: str = Field(min_length=1)
    value_authority: Literal["PLANNED"]
    target_duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    target_min_seconds: Literal[32.0]
    target_max_seconds: Literal[38.0]
    semantic_beat_total_seconds: float = Field(gt=0, allow_inf_nan=False)
    scene_planning_total_seconds: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_totals(self) -> "DurationAssessmentViewV1":
        if not (
            self.target_duration_seconds
            == self.semantic_beat_total_seconds
            == self.scene_planning_total_seconds
        ):
            raise ValueError("planned duration summaries must remain consistent")
        return self


class IdentityScopeViewV1(_ContractModel):
    requested_scope: Literal[
        "recurring_female",
        "female_with_anonymous_background",
    ]
    supported: Literal[True] = True
    eligibility_status: Literal[
        "SUPPORTED_RECURRING_FEMALE",
        "SUPPORTED_FEMALE_WITH_ANONYMOUS_BACKGROUND",
    ]
    recurring_female_required: Literal[True] = True
    anonymous_background_people_allowed: StrictBool
    identity_continuity_required: Literal[True] = True
    visual_identity_verified: Literal[False] = False
    blocking_reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_scope_summary(self) -> "IdentityScopeViewV1":
        anonymous = self.requested_scope == "female_with_anonymous_background"
        expected_status = (
            "SUPPORTED_FEMALE_WITH_ANONYMOUS_BACKGROUND"
            if anonymous
            else "SUPPORTED_RECURRING_FEMALE"
        )
        if (
            self.anonymous_background_people_allowed is not anonymous
            or self.eligibility_status != expected_status
            or self.blocking_reason_codes
        ):
            raise ValueError("identity summary must match the supported requested scope")
        return self


class ReviewStoryPlanViewV1(_ContractModel):
    schema_version: Literal[1] = 1
    topic: str = Field(min_length=1)
    language: str = Field(min_length=2, max_length=12)
    aspect_ratio: Literal["9:16"] = "9:16"
    target_duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    requested_scene_count: StrictInt = Field(ge=7, le=8)
    narration_text: str = Field(min_length=1)
    emotional_arc: tuple[str, ...] = Field(min_length=3)
    topic_intent: str = Field(min_length=1)
    semantic_beats: tuple[ReviewSemanticBeatV1, ...]
    scenes: tuple[SceneViewV1, ...]
    planner_metadata: PlannerMetadataViewV1

    @model_validator(mode="after")
    def validate_story_projection(self) -> "ReviewStoryPlanViewV1":
        expected = list(range(1, self.requested_scene_count + 1))
        if len(self.semantic_beats) != self.requested_scene_count:
            raise ValueError("review beats must match requested scene count")
        if [beat.order for beat in self.semantic_beats] != expected:
            raise ValueError("review beats must remain in canonical order")
        if len({beat.beat_id for beat in self.semantic_beats}) != len(self.semantic_beats):
            raise ValueError("review beat IDs must be unique")
        if [scene.order for scene in self.scenes] != expected:
            raise ValueError("review scenes must remain in canonical order")
        if [scene.source_beat_id for scene in self.scenes] != [
            beat.beat_id for beat in self.semantic_beats
        ]:
            raise ValueError("review scenes must preserve beat correspondence")
        previous_end = 0
        for beat in self.semantic_beats:
            if (
                beat.source_span.start != previous_end
                or self.narration_text[beat.source_span.start : beat.source_span.end]
                != beat.narration_segment
            ):
                raise ValueError("review source spans must partition canonical narration")
            previous_end = beat.source_span.end
        if previous_end != len(self.narration_text):
            raise ValueError("review source spans must cover canonical narration")
        return self


class StoryPlanRevisionV1(_ContractModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=_RUN_ID_PATTERN)
    revision_id: str = Field(pattern=r"^revision-[0-9]{4}$")
    revision_number: StrictInt = Field(ge=1)
    created_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    reasons: tuple[str, ...] = Field(min_length=1)
    custom_note: str | None = Field(default=None, max_length=500)
    target_duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    beat_count: StrictInt = Field(ge=7, le=8)
    story_plan: ReviewStoryPlanViewV1
    timeline: TimelineViewV1
    warnings: WarningCollectionV1
    duration_assessment: DurationAssessmentViewV1
    identity_scope: IdentityScopeViewV1

    @model_validator(mode="after")
    def validate_revision_summary(self) -> "StoryPlanRevisionV1":
        if self.revision_id != f"revision-{self.revision_number:04d}":
            raise ValueError("revision identity must match revision number")
        if (
            self.target_duration_seconds != self.story_plan.target_duration_seconds
            or self.beat_count != len(self.story_plan.semantic_beats)
            or self.timeline.total_duration_seconds != self.target_duration_seconds
        ):
            raise ValueError("revision summaries must match the canonical StoryPlan")
        return self


class StoryPlanRevisionSummaryV1(_ContractModel):
    revision_id: str = Field(pattern=r"^revision-[0-9]{4}$")
    revision_number: StrictInt = Field(ge=1)
    created_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    reasons: tuple[str, ...] = Field(min_length=1)
    target_duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    beat_count: StrictInt = Field(ge=7, le=8)


class StoryPlanReviewViewV1(_ContractModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=_RUN_ID_PATTERN)
    review_status: StoryPlanReviewStatus
    current_revision_id: str = Field(pattern=r"^revision-[0-9]{4}$")
    accepted_revision_id: str | None = Field(default=None, pattern=r"^revision-[0-9]{4}$")
    revision_history: tuple[StoryPlanRevisionSummaryV1, ...] = Field(min_length=1)
    current_revision: StoryPlanRevisionV1
    scene_planning_accepted: StrictBool
    render_authority: Literal[False] = False
    media_capability: Literal[False] = False
    process_local: Literal[True] = True

    @model_validator(mode="after")
    def validate_review_state(self) -> "StoryPlanReviewViewV1":
        numbers = [revision.revision_number for revision in self.revision_history]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError("revision history must be strictly ordered")
        ids = [revision.revision_id for revision in self.revision_history]
        if len(ids) != len(set(ids)) or self.current_revision_id != ids[-1]:
            raise ValueError("current revision must be the final unique revision")
        if (
            self.current_revision.revision_id != self.current_revision_id
            or self.current_revision.run_id != self.run_id
        ):
            raise ValueError("current revision must match review identity")
        accepted = self.review_status is StoryPlanReviewStatus.ACCEPTED_FOR_SCENE_PLANNING
        if (
            self.scene_planning_accepted is not accepted
            or (self.accepted_revision_id is not None) is not accepted
            or (accepted and self.accepted_revision_id != self.current_revision_id)
        ):
            raise ValueError("acceptance summary must match review status")
        return self


class StoryPlanRevisionHistoryV1(_ContractModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=_RUN_ID_PATTERN)
    current_revision_id: str = Field(pattern=r"^revision-[0-9]{4}$")
    revisions: tuple[StoryPlanRevisionSummaryV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_history(self) -> "StoryPlanRevisionHistoryV1":
        numbers = [revision.revision_number for revision in self.revisions]
        ids = [revision.revision_id for revision in self.revisions]
        if (
            numbers != list(range(1, len(numbers) + 1))
            or len(ids) != len(set(ids))
            or self.current_revision_id != ids[-1]
        ):
            raise ValueError("revision history must be ordered, unique, and current")
        return self


class AcceptStoryPlanRequestV1(_ContractModel):
    schema_version: Literal[1]
    current_revision_id: str = Field(pattern=r"^revision-[0-9]{4}$")

    @model_validator(mode="before")
    @classmethod
    def require_exact_schema_version(cls, value: Any) -> Any:
        if isinstance(value, Mapping) and type(value.get("schema_version")) is not int:
            raise ValueError("schema_version must be the exact integer 1")
        return value


class ReplanRequestV1(_ContractModel):
    schema_version: Literal[1]
    base_revision_id: str = Field(pattern=r"^revision-[0-9]{4}$")
    feedback: tuple[ReplanFeedbackDimension, ...] = Field(min_length=1)
    custom_note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="before")
    @classmethod
    def require_exact_schema_version(cls, value: Any) -> Any:
        if isinstance(value, Mapping) and type(value.get("schema_version")) is not int:
            raise ValueError("schema_version must be the exact integer 1")
        return value

    @model_validator(mode="after")
    def validate_feedback(self) -> "ReplanRequestV1":
        expected = tuple(item for item in _REPLAN_FEEDBACK_ORDER if item in self.feedback)
        if self.feedback != expected or len(set(self.feedback)) != len(self.feedback):
            raise ValueError("feedback dimensions must be unique and canonically ordered")
        has_custom = ReplanFeedbackDimension.CUSTOM_NOTE in self.feedback
        if has_custom != (self.custom_note is not None):
            raise ValueError("custom_note must be supplied exactly when selected")
        if self.custom_note is not None:
            if not self.custom_note.strip() or any(
                ord(character) < 32 and character not in "\n\t" for character in self.custom_note
            ):
                raise ValueError("custom_note contains unsupported text")
        return self


class StoryPlanReviewOperationResultV1(_ContractModel):
    schema_version: Literal[1] = 1
    review: StoryPlanReviewViewV1 | None = None
    revision: StoryPlanRevisionV1 | None = None
    error: PublicApiErrorV1 | None = None

    @model_validator(mode="after")
    def require_success_or_error(self) -> "StoryPlanReviewOperationResultV1":
        success = self.review is not None and self.revision is not None
        if success == (self.error is not None):
            raise ValueError("review operation requires success or error")
        return self


class PlanOnlyDeterministicTopicProducer(ApprovedStoryPlanProducer):
    """Reviewed deterministic producer authorized only for non-render planning."""

    PRODUCER_ID = "plan_only.deterministic_topic"
    PRODUCER_VERSION = "v1"
    SUPPORTED_INPUT_MODES = frozenset({ScriptInputMode.TOPIC})

    def _produce(self, normalized_input: NormalizedScriptInput) -> StoryPlan:
        scene_count = normalized_input.requested_scene_count_range[0]
        plan = DeterministicTopicPlanner().plan(
            topic=normalized_input.source_content,
            language=normalized_input.language,
            scene_count=scene_count,
            target_duration_seconds=35.0,
        )
        metadata = PlannerMetadata.model_validate(
            {
                **plan.planner_metadata.model_dump(mode="python"),
                "planner_id": self.producer_id,
                "planner_version": self.producer_version,
                "planner_mode": PlannerMode.PRODUCTION,
                "production_eligible": False,
            }
        )
        return plan.model_copy(update={"planner_metadata": metadata})


def _json_detached(model: BaseModel) -> dict[str, object]:
    return json.loads(model.model_dump_json())


def _request_id(payload: object) -> str:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        encoded = b"invalid-plan-only-request"
    return f"request-{hashlib.sha256(encoded).hexdigest()[:16]}"


def _scope(value: str) -> CharacterScopeRequest:
    if value == "recurring_female":
        return CharacterScopeRequest(
            recurring_characters=(
                RecurringCharacterRequest(
                    character_id="recurring_woman",
                    role=CharacterRoleRequest.FEMALE,
                ),
            )
        )
    if value == "female_with_anonymous_background":
        return CharacterScopeRequest(
            recurring_characters=(
                RecurringCharacterRequest(
                    character_id="recurring_woman",
                    role=CharacterRoleRequest.FEMALE,
                ),
            ),
            anonymous_background_people=True,
        )
    if value == "recurring_male":
        return CharacterScopeRequest(
            recurring_characters=(
                RecurringCharacterRequest(
                    character_id="recurring_man",
                    role=CharacterRoleRequest.MALE,
                ),
            )
        )
    if value == "family":
        return CharacterScopeRequest(relationship=RelationshipScope.FAMILY)
    return CharacterScopeRequest(relationship=RelationshipScope.UNRESOLVED)


def _public_error(
    payload: object,
    *,
    status: Literal["BLOCKED", "FAILED"],
    code: str,
    message: str,
    retryable: bool = False,
    field_errors: tuple[PublicFieldErrorV1, ...] = (),
) -> PlanOnlyCreateResultV1:
    return PlanOnlyCreateResultV1(
        error=PublicApiErrorV1(
            request_id=_request_id(payload),
            status=status,
            code=code,
            message=message,
            retryable=retryable,
            field_errors=field_errors,
        )
    )


def _project_run(
    request: PlanOnlyCreateRequestV1,
    normalized_input: Any,
    story_plan: StoryPlan,
) -> ProductionRunViewV1:
    run_id = f"plan-{normalized_input.logical_input_hash[:20]}"
    seconds = int(normalized_input.logical_input_hash[20:28], 16) % (365 * 24 * 60 * 60)
    timestamp = (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    beats = tuple(
        SemanticBeatViewV1(
            beat_id=beat.beat_id,
            order=beat.order,
            narration_segment=beat.narration_segment,
            semantic_purpose=beat.semantic_purpose,
            emotional_state=beat.emotional_state,
            visual_intent=beat.visual_intent,
            duration_seconds=beat.duration_seconds,
        )
        for beat in story_plan.semantic_beats
    )
    scenes = tuple(
        SceneViewV1(
            scene_id=f"scene_{beat.order:02d}",
            order=beat.order,
            source_beat_id=beat.beat_id,
            meaning=beat.semantic_purpose,
            emotional_tone=(beat.emotional_state,),
            visual_intent=beat.visual_intent,
            planned_duration_seconds=beat.duration_seconds,
        )
        for beat in story_plan.semantic_beats
    )
    timings = build_scene_timings(list(story_plan.semantic_beats))
    timeline_rows = tuple(
        TimelineRowV1(
            scene_id=timing.scene_id,
            order=timing.order,
            start_seconds=timing.start_seconds,
            narration_slot_duration_seconds=timing.duration_seconds,
            end_seconds=timing.end_seconds,
        )
        for timing in timings
    )
    duration_assessment = assess_mvp_duration_target(
        float(story_plan.target_duration_seconds),
        value_authority=DurationValueAuthority.PLANNED,
    )
    conditions: list[PublicConditionV1] = []
    if duration_assessment.status is DurationAssessmentStatus.OUTSIDE_TARGET_WARNING:
        conditions.append(
            PublicConditionV1(
                code=duration_assessment.reason_code.value,
                title="Planned duration is outside the target",
                detail="The canonical planned duration is outside the MVP target.",
                severity="WARNING",
            )
        )
    for beat in story_plan.semantic_beats:
        warning = assess_beat_duration_pacing(
            beat_id=beat.beat_id,
            actual_duration_seconds=float(beat.duration_seconds),
        )
        if warning is not None:
            conditions.append(
                PublicConditionV1(
                    code=warning.warning_code.value,
                    title="Planned beat pacing warning",
                    detail="A canonical semantic beat is outside the pacing target.",
                    severity="WARNING",
                    beat_ids=(warning.beat_id,),
                )
            )
    return ProductionRunViewV1(
        run_id=run_id,
        created_at=timestamp,
        updated_at=timestamp,
        normalized_input=NormalizedInputViewV1(
            input_mode="TOPIC",
            source_content=normalized_input.source_content,
            language=normalized_input.language,
            character_scope=request.character_scope,
        ),
        story_plan=StoryPlanViewV1(
            topic=story_plan.topic,
            language=story_plan.language,
            target_duration_seconds=story_plan.target_duration_seconds,
            narration_text=story_plan.narration_text,
            emotional_arc=story_plan.emotional_arc,
            semantic_beats=beats,
            scenes=scenes,
        ),
        timeline=TimelineViewV1(
            total_duration_seconds=timeline_rows[-1].end_seconds,
            rows=timeline_rows,
        ),
        warnings=WarningCollectionV1(
            warning_count=len(conditions),
            conditions=tuple(conditions),
        ),
        render_readiness=RenderReadinessV1(),
    )


def _revision_summary(
    revision: StoryPlanRevisionV1,
) -> StoryPlanRevisionSummaryV1:
    return StoryPlanRevisionSummaryV1(
        revision_id=revision.revision_id,
        revision_number=revision.revision_number,
        created_at=revision.created_at,
        reasons=revision.reasons,
        target_duration_seconds=revision.target_duration_seconds,
        beat_count=revision.beat_count,
    )


def _revision_timestamp(created_at: str, revision_number: int) -> str:
    base = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return (base + timedelta(seconds=revision_number - 1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _project_revision(
    *,
    run: ProductionRunViewV1,
    request: PlanOnlyCreateRequestV1,
    story_plan: StoryPlan,
    revision_number: int,
    reasons: tuple[str, ...],
    custom_note: str | None,
) -> StoryPlanRevisionV1:
    duration = assess_mvp_duration_target(
        float(story_plan.target_duration_seconds),
        value_authority=DurationValueAuthority.PLANNED,
    )
    review_beats = tuple(
        ReviewSemanticBeatV1(
            beat_id=beat.beat_id,
            order=beat.order,
            source_span=NarrationSourceSpanViewV1(
                start=beat.source_span.start,
                end=beat.source_span.end,
            ),
            narration_segment=beat.narration_segment,
            semantic_purpose=beat.semantic_purpose,
            emotional_state=beat.emotional_state,
            transition_intent=beat.transition_intent,
            visual_intent=beat.visual_intent,
            duration_seconds=beat.duration_seconds,
        )
        for beat in story_plan.semantic_beats
    )
    review_story = ReviewStoryPlanViewV1(
        topic=story_plan.topic,
        language=story_plan.language,
        target_duration_seconds=story_plan.target_duration_seconds,
        requested_scene_count=story_plan.requested_scene_count,
        narration_text=story_plan.narration_text,
        emotional_arc=story_plan.emotional_arc,
        topic_intent=story_plan.topic_intent,
        semantic_beats=review_beats,
        scenes=run.story_plan.scenes,
        planner_metadata=PlannerMetadataViewV1(
            planner_id=story_plan.planner_metadata.planner_id,
            planner_version=story_plan.planner_metadata.planner_version,
        ),
    )
    return StoryPlanRevisionV1(
        run_id=run.run_id,
        revision_id=f"revision-{revision_number:04d}",
        revision_number=revision_number,
        created_at=_revision_timestamp(run.created_at, revision_number),
        reasons=reasons,
        custom_note=custom_note,
        target_duration_seconds=story_plan.target_duration_seconds,
        beat_count=len(story_plan.semantic_beats),
        story_plan=review_story,
        timeline=run.timeline,
        warnings=run.warnings,
        duration_assessment=DurationAssessmentViewV1(
            policy_id=duration.policy_id,
            status=duration.status.value,
            reason_code=duration.reason_code.value,
            value_authority=duration.value_authority.value,
            target_duration_seconds=story_plan.target_duration_seconds,
            target_min_seconds=duration.target_min_seconds,
            target_max_seconds=duration.target_max_seconds,
            semantic_beat_total_seconds=sum(
                beat.duration_seconds for beat in story_plan.semantic_beats
            ),
            scene_planning_total_seconds=sum(
                scene.planned_duration_seconds for scene in run.story_plan.scenes
            ),
        ),
        identity_scope=IdentityScopeViewV1(
            requested_scope=request.character_scope,
            eligibility_status=(
                "SUPPORTED_FEMALE_WITH_ANONYMOUS_BACKGROUND"
                if request.character_scope == "female_with_anonymous_background"
                else "SUPPORTED_RECURRING_FEMALE"
            ),
            anonymous_background_people_allowed=(
                request.character_scope == "female_with_anonymous_background"
            ),
        ),
    )


def _review_operation_error(
    payload: object,
    *,
    code: str,
    message: str,
    status: Literal["BLOCKED", "FAILED"] = "BLOCKED",
    field_errors: tuple[PublicFieldErrorV1, ...] = (),
) -> dict[str, object]:
    return _json_detached(
        StoryPlanReviewOperationResultV1(
            error=PublicApiErrorV1(
                request_id=_request_id(payload),
                status=status,
                code=code,
                message=message,
                retryable=False,
                field_errors=field_errors,
            )
        )
    )


class PlanOnlyApplication:
    """In-memory PLAN_ONLY application with bounded still-image candidate capability."""

    def __init__(
        self,
        producer: ApprovedStoryPlanProducer | None = None,
        *,
        visual_provider: VisualCandidateProvider | None = None,
        visual_artifact_root: Path | None = None,
    ) -> None:
        self._producer = producer or PlanOnlyDeterministicTopicProducer()
        self._runs: dict[str, ProductionRunViewV1] = {}
        self._requests: dict[str, PlanOnlyCreateRequestV1] = {}
        self._revisions: dict[str, tuple[StoryPlanRevisionV1, ...]] = {}
        self._review_statuses: dict[str, StoryPlanReviewStatus] = {}
        self._accepted_revision_ids: dict[str, str | None] = {}
        self._scene_planning = ScenePlanningStore()
        self._composition_planning = CompositionPlanningStore()
        if visual_provider is None:
            configured = get_image_provider()
            visual_provider = configured if configured.is_configured() else None
        self._visual_candidates = VisualCandidateStore(
            provider=visual_provider,
            artifact_root=visual_artifact_root,
        )

    def _produce(
        self,
        request: PlanOnlyCreateRequestV1,
    ) -> tuple[NormalizedScriptInput, StoryPlan]:
        normalized = normalize_script_input(
            TopicScriptInput(
                topic=request.source_content,
                language=request.language,
                requested_scene_count_range=(
                    request.requested_scene_count,
                    request.requested_scene_count,
                ),
                character_scope_request=_scope(request.character_scope),
                execution_mode=ExecutionMode.FIXTURE_PREVIEW,
            )
        )
        require_supported_identity(normalized)
        approved = self._producer.produce(normalized)
        return normalized, approved.story_plan

    def _review(self, run_id: str) -> StoryPlanReviewViewV1 | None:
        revisions = self._revisions.get(run_id)
        if not revisions:
            return None
        status = self._review_statuses[run_id]
        current = revisions[-1]
        return StoryPlanReviewViewV1(
            run_id=run_id,
            review_status=status,
            current_revision_id=current.revision_id,
            accepted_revision_id=self._accepted_revision_ids[run_id],
            revision_history=tuple(_revision_summary(item) for item in revisions),
            current_revision=current,
            scene_planning_accepted=(status is StoryPlanReviewStatus.ACCEPTED_FOR_SCENE_PLANNING),
        )

    def create_run(self, payload: object) -> dict[str, object]:
        try:
            request = PlanOnlyCreateRequestV1.model_validate(payload)
        except ValidationError as error:
            fields = tuple(
                PublicFieldErrorV1(
                    path=".".join(str(part) for part in item["loc"]),
                    code=str(item["type"]),
                    message="Invalid request field.",
                )
                for item in error.errors(include_url=False)
            )
            return _json_detached(
                _public_error(
                    payload,
                    status="FAILED",
                    code="INVALID_PLAN_ONLY_REQUEST",
                    message="The planning request is malformed.",
                    field_errors=fields,
                )
            )
        if request.input_mode != "TOPIC":
            return _json_detached(
                _public_error(
                    payload,
                    status="BLOCKED",
                    code="STORYPLAN_NARRATION_SEGMENTER_MISSING",
                    message="No approved narration-to-StoryPlan segmenter is available.",
                )
            )
        try:
            normalized, story_plan = self._produce(request)
            run = _project_run(request, normalized, story_plan)
        except IdentityEligibilityError as error:
            return _json_detached(
                _public_error(
                    payload,
                    status="BLOCKED",
                    code=error.code.value,
                    message="The declared identity scope is not supported.",
                )
            )
        except Exception:
            return _json_detached(
                _public_error(
                    payload,
                    status="FAILED",
                    code="PLAN_ONLY_PLANNING_FAILED",
                    message="The planning operation failed.",
                )
            )
        if run.run_id not in self._runs:
            stored_run = ProductionRunViewV1.model_validate(run.model_dump(mode="python"))
            initial_revision = _project_revision(
                run=stored_run,
                request=request,
                story_plan=story_plan,
                revision_number=1,
                reasons=("INITIAL_PLAN",),
                custom_note=None,
            )
            self._runs[run.run_id] = stored_run
            self._requests[run.run_id] = PlanOnlyCreateRequestV1.model_validate(
                request.model_dump(mode="python")
            )
            self._revisions[run.run_id] = (initial_revision,)
            self._review_statuses[run.run_id] = StoryPlanReviewStatus.UNREVIEWED
            self._accepted_revision_ids[run.run_id] = None
        return _json_detached(PlanOnlyCreateResultV1(run=self._runs[run.run_id]))

    def get_run(self, run_id: str) -> dict[str, object] | None:
        run = self._runs.get(run_id)
        return None if run is None else _json_detached(run)

    def list_runs(self) -> dict[str, object]:
        ordered = sorted(self._runs.values(), key=lambda run: (run.created_at, run.run_id))
        dashboard = ProductionDashboardViewV1(
            runs=tuple(
                ProductionRunSummaryV1(
                    run_id=run.run_id,
                    warning_count=run.warnings.warning_count,
                    created_at=run.created_at,
                    updated_at=run.updated_at,
                )
                for run in ordered
            )
        )
        return _json_detached(dashboard)

    def capabilities(self) -> dict[str, object]:
        return _json_detached(ProductionCapabilitiesV1())

    def get_review(self, run_id: str) -> dict[str, object] | None:
        review = self._review(run_id)
        return None if review is None else _json_detached(review)

    def accept_story_plan(self, run_id: str, payload: object) -> dict[str, object] | None:
        run = self._runs.get(run_id)
        review = self._review(run_id)
        if run is None or review is None:
            return None
        try:
            request = AcceptStoryPlanRequestV1.model_validate(payload)
        except ValidationError as error:
            fields = tuple(
                PublicFieldErrorV1(
                    path=".".join(str(part) for part in item["loc"]),
                    code=str(item["type"]),
                    message="Invalid review field.",
                )
                for item in error.errors(include_url=False)
            )
            return _review_operation_error(
                payload,
                code="INVALID_REVIEW_REQUEST",
                message="The StoryPlan review request is malformed.",
                status="FAILED",
                field_errors=fields,
            )
        if run.status is not PlanOnlyStatus.PLANNED:
            return _review_operation_error(
                payload,
                code="RUN_NOT_ACCEPTABLE",
                message="Only a valid planned run can be accepted for scene planning.",
            )
        if self._review_statuses[run_id] is StoryPlanReviewStatus.ACCEPTED_FOR_SCENE_PLANNING:
            return _review_operation_error(
                payload,
                code="REVIEW_ALREADY_ACCEPTED",
                message="This StoryPlan revision is already accepted for scene planning.",
            )
        if request.current_revision_id != review.current_revision_id:
            return _review_operation_error(
                payload,
                code="STALE_STORYPLAN_REVISION",
                message="The StoryPlan revision changed before acceptance.",
            )
        self._review_statuses[run_id] = StoryPlanReviewStatus.ACCEPTED_FOR_SCENE_PLANNING
        self._accepted_revision_ids[run_id] = review.current_revision_id
        updated = self._review(run_id)
        assert updated is not None
        return _json_detached(
            StoryPlanReviewOperationResultV1(
                review=updated,
                revision=updated.current_revision,
            )
        )

    def request_replan(self, run_id: str, payload: object) -> dict[str, object] | None:
        run = self._runs.get(run_id)
        review = self._review(run_id)
        request_source = self._requests.get(run_id)
        if run is None or review is None or request_source is None:
            return None
        try:
            request = ReplanRequestV1.model_validate(payload)
        except ValidationError as error:
            fields = tuple(
                PublicFieldErrorV1(
                    path=".".join(str(part) for part in item["loc"]),
                    code=str(item["type"]),
                    message="Invalid replan field.",
                )
                for item in error.errors(include_url=False)
            )
            return _review_operation_error(
                payload,
                code="INVALID_REPLAN_REQUEST",
                message="The replan request is malformed.",
                status="FAILED",
                field_errors=fields,
            )
        if run.status is not PlanOnlyStatus.PLANNED:
            return _review_operation_error(
                payload,
                code="RUN_NOT_REPLANNABLE",
                message="Only a valid planned run can request a revision.",
            )
        if request.base_revision_id != review.current_revision_id:
            return _review_operation_error(
                payload,
                code="STALE_STORYPLAN_REVISION",
                message="The StoryPlan revision changed before replanning.",
            )
        try:
            normalized, story_plan = self._produce(request_source)
            next_number = len(self._revisions[run_id]) + 1
            projected_run = _project_run(request_source, normalized, story_plan)
            run_payload = projected_run.model_dump(mode="python")
            run_payload["created_at"] = run.created_at
            run_payload["updated_at"] = _revision_timestamp(run.created_at, next_number)
            updated_run = ProductionRunViewV1.model_validate(run_payload)
            revision = _project_revision(
                run=updated_run,
                request=request_source,
                story_plan=story_plan,
                revision_number=next_number,
                reasons=tuple(item.value for item in request.feedback),
                custom_note=request.custom_note,
            )
        except Exception:
            return _review_operation_error(
                payload,
                code="PLAN_ONLY_REPLAN_FAILED",
                message="The StoryPlan revision could not be produced.",
                status="FAILED",
            )
        self._runs[run_id] = updated_run
        self._revisions[run_id] = (*self._revisions[run_id], revision)
        self._review_statuses[run_id] = StoryPlanReviewStatus.REPLAN_REQUESTED
        self._accepted_revision_ids[run_id] = None
        updated_review = self._review(run_id)
        assert updated_review is not None
        return _json_detached(
            StoryPlanReviewOperationResultV1(
                review=updated_review,
                revision=revision,
            )
        )

    def list_revisions(self, run_id: str) -> dict[str, object] | None:
        revisions = self._revisions.get(run_id)
        if not revisions:
            return None
        return _json_detached(
            StoryPlanRevisionHistoryV1(
                run_id=run_id,
                current_revision_id=revisions[-1].revision_id,
                revisions=tuple(_revision_summary(item) for item in revisions),
            )
        )

    def get_revision(
        self,
        run_id: str,
        revision_id: str,
    ) -> dict[str, object] | None:
        revisions = self._revisions.get(run_id)
        if not revisions:
            return None
        revision = next(
            (item for item in revisions if item.revision_id == revision_id),
            None,
        )
        return None if revision is None else _json_detached(revision)

    def _review_payload(self, run_id: str) -> dict[str, object] | None:
        review = self._review(run_id)
        return None if review is None else review.model_dump(mode="json")

    def get_scene_plan(self, run_id: str) -> dict[str, object] | None:
        if run_id not in self._runs:
            return None
        return self._scene_planning.get_access(run_id, self._review_payload(run_id))

    def initialize_scene_plan(self, run_id: str, payload: object) -> dict[str, object] | None:
        if run_id not in self._runs:
            return None
        return self._scene_planning.initialize(run_id, self._review_payload(run_id), payload)

    def mutate_scene_plan(
        self,
        run_id: str,
        operation: str,
        payload: object,
        *,
        scene_id: str | None = None,
    ) -> dict[str, object] | None:
        if run_id not in self._runs:
            return None
        review = self._review_payload(run_id)
        if operation == "save" and scene_id is not None:
            return self._scene_planning.save(run_id, review, scene_id, payload)
        if operation == "restore" and scene_id is not None:
            return self._scene_planning.restore(run_id, review, scene_id, payload)
        if operation == "accept":
            return self._scene_planning.transition(run_id, review, payload, accept=True)
        if operation == "request-revision":
            return self._scene_planning.transition(run_id, review, payload, accept=False)
        method = getattr(self._scene_planning, operation, None)
        if method is None:
            return None
        return method(run_id, review, payload)

    def get_scene_plan_history(self, run_id: str) -> dict[str, object] | None:
        if run_id not in self._runs:
            return None
        return self._scene_planning.collection_history(run_id)

    def get_scene_plan_revision(self, run_id: str, revision_id: str) -> dict[str, object] | None:
        if run_id not in self._runs:
            return None
        return self._scene_planning.collection_revision(run_id, revision_id)

    def get_planned_scene(self, run_id: str, scene_id: str) -> dict[str, object] | None:
        if run_id not in self._runs:
            return None
        return self._scene_planning.scene(run_id, scene_id)

    def get_planned_scene_history(self, run_id: str, scene_id: str) -> dict[str, object] | None:
        if run_id not in self._runs:
            return None
        return self._scene_planning.scene_history(run_id, scene_id)

    def _visual_context(
        self, run_id: str
    ) -> tuple[dict[str, object], dict[str, object], dict[str, object]] | None:
        run = self.get_run(run_id)
        review = self._review_payload(run_id)
        scene_access = self.get_scene_plan(run_id)
        if run is None or review is None or scene_access is None:
            return None
        return run, review, scene_access

    def get_visual_candidate_access(self, run_id: str, scene_id: str) -> dict[str, object] | None:
        context = self._visual_context(run_id)
        if context is None:
            return None
        run, review, scene_access = context
        return self._visual_candidates.access(
            run=run,
            review=review,
            scene_access=scene_access,
            scene_id=scene_id,
        )

    def generate_visual_candidates(
        self, run_id: str, scene_id: str, payload: object
    ) -> dict[str, object] | None:
        context = self._visual_context(run_id)
        if context is None:
            return None
        run, review, scene_access = context
        return self._visual_candidates.generate(
            run=run,
            review=review,
            scene_access=scene_access,
            scene_id=scene_id,
            payload=payload,
        )

    def mutate_visual_candidate(
        self,
        run_id: str,
        scene_id: str,
        candidate_id: str,
        operation: Literal["accept", "reject", "request-revision"],
        payload: object,
    ) -> dict[str, object] | None:
        context = self._visual_context(run_id)
        if context is None:
            return None
        run, review, scene_access = context
        return self._visual_candidates.mutate(
            run=run,
            review=review,
            scene_access=scene_access,
            scene_id=scene_id,
            candidate_id=candidate_id,
            operation=operation,
            payload=payload,
        )

    def get_visual_candidate(
        self, run_id: str, scene_id: str, candidate_id: str
    ) -> dict[str, object] | None:
        context = self._visual_context(run_id)
        if context is None:
            return None
        run, review, scene_access = context
        return self._visual_candidates.candidate(
            run=run,
            review=review,
            scene_access=scene_access,
            scene_id=scene_id,
            candidate_id=candidate_id,
        )

    def get_visual_candidate_artifact(
        self, run_id: str, scene_id: str, candidate_id: str
    ) -> VisualArtifact | None:
        context = self._visual_context(run_id)
        if context is None:
            return None
        run, review, scene_access = context
        return self._visual_candidates.artifact(
            run=run,
            review=review,
            scene_access=scene_access,
            scene_id=scene_id,
            candidate_id=candidate_id,
        )

    def _composition_context(
        self, run_id: str
    ) -> (
        tuple[
            dict[str, object],
            dict[str, object],
            dict[str, object],
            dict[str, dict[str, object]],
            dict[str, bool],
        ]
        | None
    ):
        run = self.get_run(run_id)
        review = self._review_payload(run_id)
        scene_access = self.get_scene_plan(run_id)
        if run is None or review is None or scene_access is None:
            return None
        collection = scene_access.get("collection")
        visual_accesses: dict[str, dict[str, object]] = {}
        artifact_validity: dict[str, bool] = {}
        if isinstance(collection, dict):
            for scene in collection.get("scenes", []):
                scene_id = str(scene["scene_id"])
                visual = self.get_visual_candidate_access(run_id, scene_id)
                if visual is None:
                    continue
                visual_accesses[scene_id] = visual
                visual_collection = visual.get("collection")
                accepted_id = (
                    visual_collection.get("current_accepted_candidate_id")
                    if isinstance(visual_collection, dict)
                    else None
                )
                artifact_validity[scene_id] = (
                    isinstance(accepted_id, str)
                    and self.get_visual_candidate_artifact(run_id, scene_id, accepted_id)
                    is not None
                )
        return run, review, scene_access, visual_accesses, artifact_validity

    def get_composition_access(self, run_id: str) -> dict[str, object] | None:
        context = self._composition_context(run_id)
        if context is None:
            return None
        run, review, scene_access, visual_accesses, artifact_validity = context
        return self._composition_planning.access(
            run=run,
            review=review,
            scene_access=scene_access,
            visual_accesses=visual_accesses,
            artifact_validity=artifact_validity,
        )

    def initialize_compositions(self, run_id: str, payload: object) -> dict[str, object] | None:
        context = self._composition_context(run_id)
        if context is None:
            return None
        run, review, scene_access, visual_accesses, artifact_validity = context
        return self._composition_planning.initialize(
            run=run,
            review=review,
            scene_access=scene_access,
            visual_accesses=visual_accesses,
            artifact_validity=artifact_validity,
            payload=payload,
        )

    def mutate_composition(
        self,
        run_id: str,
        scene_id: str,
        operation: Literal["save", "restore", "accept", "request-revision"],
        payload: object,
    ) -> dict[str, object] | None:
        access = self.get_composition_access(run_id)
        if access is None:
            return None
        if access.get("editable") is not True:
            return {
                "schema_version": 1,
                "access": None,
                "collection": None,
                "error": {
                    "schema_version": 1,
                    "code": "COMPOSITION_NOT_AUTHORIZED",
                    "message": "Current upstream authority does not permit composition mutation.",
                    "retryable": False,
                },
            }
        if operation == "save":
            return self._composition_planning.save(run_id, scene_id, payload)
        if operation == "restore":
            return self._composition_planning.restore(run_id, scene_id, payload)
        return self._composition_planning.review(run_id, scene_id, operation, payload)

    def get_composition(self, run_id: str, scene_id: str) -> dict[str, object] | None:
        if run_id not in self._runs:
            return None
        self.get_composition_access(run_id)
        return self._composition_planning.composition(run_id, scene_id)

    def get_composition_history(self, run_id: str, scene_id: str) -> dict[str, object] | None:
        if run_id not in self._runs:
            return None
        return self._composition_planning.composition_history(run_id, scene_id)

    def get_composition_collection_history(self, run_id: str) -> dict[str, object] | None:
        if run_id not in self._runs:
            return None
        return self._composition_planning.collection_history(run_id)

    def get_composition_collection_revision(
        self, run_id: str, revision_id: str
    ) -> dict[str, object] | None:
        if run_id not in self._runs:
            return None
        return self._composition_planning.collection_revision(run_id, revision_id)


class PlanOnlyFacadeResponse(_ContractModel):
    status_code: int = Field(ge=200, le=599)
    payload: dict[str, object] | None


def dispatch_plan_only_request(
    application: PlanOnlyApplication,
    *,
    method: str,
    path: str,
    body: object | None = None,
) -> PlanOnlyFacadeResponse:
    """Pure local/API facade; hosting may map it without changing authority."""

    if method == "GET" and path == f"{PLAN_ONLY_API_PREFIX}/capabilities":
        return PlanOnlyFacadeResponse(status_code=200, payload=application.capabilities())
    if method == "GET" and path == f"{PLAN_ONLY_API_PREFIX}/runs":
        return PlanOnlyFacadeResponse(status_code=200, payload=application.list_runs())
    if method == "POST" and path == f"{PLAN_ONLY_API_PREFIX}/runs":
        return PlanOnlyFacadeResponse(
            status_code=200,
            payload=application.create_run(body),
        )
    prefix = f"{PLAN_ONLY_API_PREFIX}/runs/"
    if not path.startswith(prefix):
        return PlanOnlyFacadeResponse(status_code=404, payload=None)
    parts = path[len(prefix) :].split("/")
    if len(parts) == 1 and method == "GET":
        run = application.get_run(parts[0])
        return PlanOnlyFacadeResponse(
            status_code=200 if run is not None else 404,
            payload=run,
        )
    if len(parts) == 2 and parts[1] == "review" and method == "GET":
        review = application.get_review(parts[0])
        return PlanOnlyFacadeResponse(
            status_code=200 if review is not None else 404,
            payload=review,
        )
    if len(parts) == 3 and parts[1:] == ["review", "accept"] and method == "POST":
        result = application.accept_story_plan(parts[0], body)
        return PlanOnlyFacadeResponse(
            status_code=200 if result is not None else 404,
            payload=result,
        )
    if len(parts) == 2 and parts[1] == "revisions":
        if method == "GET":
            history = application.list_revisions(parts[0])
            return PlanOnlyFacadeResponse(
                status_code=200 if history is not None else 404,
                payload=history,
            )
        if method == "POST":
            result = application.request_replan(parts[0], body)
            return PlanOnlyFacadeResponse(
                status_code=200 if result is not None else 404,
                payload=result,
            )
    if len(parts) == 3 and parts[1] == "revisions" and method == "GET":
        revision = application.get_revision(parts[0], parts[2])
        return PlanOnlyFacadeResponse(
            status_code=200 if revision is not None else 404,
            payload=revision,
        )
    if len(parts) >= 2 and parts[1] == "compositions":
        run_id = parts[0]
        tail = parts[2:]
        if tail in ([], ["access"]) and method == "GET":
            access = application.get_composition_access(run_id)
            return PlanOnlyFacadeResponse(
                status_code=200 if access is not None else 404,
                payload=access,
            )
        if tail == ["initialize"] and method == "POST":
            result = application.initialize_compositions(run_id, body)
            return PlanOnlyFacadeResponse(
                status_code=200 if result is not None else 404,
                payload=result,
            )
        if tail == ["revisions"] and method == "GET":
            history = application.get_composition_collection_history(run_id)
            return PlanOnlyFacadeResponse(
                status_code=200 if history is not None else 404,
                payload=history,
            )
        if len(tail) == 2 and tail[0] == "revisions" and method == "GET":
            revision = application.get_composition_collection_revision(run_id, tail[1])
            return PlanOnlyFacadeResponse(
                status_code=200 if revision is not None else 404,
                payload=revision,
            )
        if len(tail) >= 2 and tail[0] == "scenes":
            scene_id = tail[1]
            if len(tail) == 2 and method == "GET":
                composition = application.get_composition(run_id, scene_id)
                return PlanOnlyFacadeResponse(
                    status_code=200 if composition is not None else 404,
                    payload=composition,
                )
            if tail[2:] == ["history"] and method == "GET":
                history = application.get_composition_history(run_id, scene_id)
                return PlanOnlyFacadeResponse(
                    status_code=200 if history is not None else 404,
                    payload=history,
                )
            operation_by_tail = {
                ("revisions",): "save",
                ("restore",): "restore",
                ("accept",): "accept",
                ("request-revision",): "request-revision",
            }
            operation = operation_by_tail.get(tuple(tail[2:]))
            if operation is not None and method == "POST":
                result = application.mutate_composition(run_id, scene_id, operation, body)
                return PlanOnlyFacadeResponse(
                    status_code=200 if result is not None else 404,
                    payload=result,
                )
    if len(parts) >= 2 and parts[1] == "scene-plan":
        run_id = parts[0]
        tail = parts[2:]
        if not tail and method == "GET":
            access = application.get_scene_plan(run_id)
            return PlanOnlyFacadeResponse(
                status_code=200 if access is not None else 404,
                payload=access,
            )
        if tail == ["initialize"] and method == "POST":
            result = application.initialize_scene_plan(run_id, body)
            return PlanOnlyFacadeResponse(
                status_code=200 if result is not None else 404,
                payload=result,
            )
        if tail == ["revisions"] and method == "GET":
            history = application.get_scene_plan_history(run_id)
            return PlanOnlyFacadeResponse(
                status_code=200 if history is not None else 404,
                payload=history,
            )
        if len(tail) == 2 and tail[0] == "revisions" and method == "GET":
            revision = application.get_scene_plan_revision(run_id, tail[1])
            return PlanOnlyFacadeResponse(
                status_code=200 if revision is not None else 404,
                payload=revision,
            )
        if len(tail) >= 2 and tail[0] == "scenes":
            scene_id = tail[1]
            if len(tail) >= 3 and tail[2] == "visual-candidates":
                visual_tail = tail[3:]
                if not visual_tail and method == "GET":
                    access = application.get_visual_candidate_access(run_id, scene_id)
                    return PlanOnlyFacadeResponse(
                        status_code=200 if access is not None else 404,
                        payload=access,
                    )
                if visual_tail == ["generate"] and method == "POST":
                    result = application.generate_visual_candidates(run_id, scene_id, body)
                    return PlanOnlyFacadeResponse(
                        status_code=200 if result is not None else 404,
                        payload=result,
                    )
                if len(visual_tail) == 1 and method == "GET":
                    candidate = application.get_visual_candidate(run_id, scene_id, visual_tail[0])
                    return PlanOnlyFacadeResponse(
                        status_code=200 if candidate is not None else 404,
                        payload=candidate,
                    )
                if (
                    len(visual_tail) == 2
                    and visual_tail[1] in {"accept", "reject", "request-revision"}
                    and method == "POST"
                ):
                    result = application.mutate_visual_candidate(
                        run_id,
                        scene_id,
                        visual_tail[0],
                        visual_tail[1],
                        body,
                    )
                    return PlanOnlyFacadeResponse(
                        status_code=200 if result is not None else 404,
                        payload=result,
                    )
            if len(tail) == 2 and method == "GET":
                scene = application.get_planned_scene(run_id, scene_id)
                return PlanOnlyFacadeResponse(
                    status_code=200 if scene is not None else 404,
                    payload=scene,
                )
            if tail[2:] == ["history"] and method == "GET":
                history = application.get_planned_scene_history(run_id, scene_id)
                return PlanOnlyFacadeResponse(
                    status_code=200 if history is not None else 404,
                    payload=history,
                )
            operation_by_tail = {
                ("revisions",): "save",
                ("restore",): "restore",
                ("accept",): "accept",
                ("request-revision",): "request-revision",
            }
            operation = operation_by_tail.get(tuple(tail[2:]))
            if operation is not None and method == "POST":
                result = application.mutate_scene_plan(
                    run_id,
                    operation,
                    body,
                    scene_id=scene_id,
                )
                return PlanOnlyFacadeResponse(
                    status_code=200 if result is not None else 404,
                    payload=result,
                )
        if (
            len(tail) == 1
            and tail[0]
            in {
                "reorder",
                "split",
                "merge",
                "duplicate",
            }
            and method == "POST"
        ):
            result = application.mutate_scene_plan(run_id, tail[0], body)
            return PlanOnlyFacadeResponse(
                status_code=200 if result is not None else 404,
                payload=result,
            )
    return PlanOnlyFacadeResponse(status_code=404, payload=None)


__all__ = [
    "PLAN_ONLY_API_PREFIX",
    "PlanOnlyApplication",
    "PlanOnlyCreateRequestV1",
    "PlanOnlyCreateResultV1",
    "PlanOnlyDeterministicTopicProducer",
    "PlanOnlyFacadeResponse",
    "AcceptStoryPlanRequestV1",
    "ReplanFeedbackDimension",
    "ReplanRequestV1",
    "StoryPlanReviewOperationResultV1",
    "StoryPlanReviewStatus",
    "StoryPlanReviewViewV1",
    "StoryPlanRevisionHistoryV1",
    "StoryPlanRevisionV1",
    "ProductionCapabilitiesV1",
    "ProductionDashboardViewV1",
    "ProductionRunViewV1",
    "PublicApiErrorV1",
    "dispatch_plan_only_request",
]
