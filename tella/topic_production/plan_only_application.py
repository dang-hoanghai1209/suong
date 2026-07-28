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
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, model_validator

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
from .story_plan_producer import ApprovedStoryPlanProducer
from .timing import build_scene_timings


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


class PlanOnlyApplication:
    """In-memory, detached, provider-free planning application boundary."""

    def __init__(self, producer: ApprovedStoryPlanProducer | None = None) -> None:
        self._producer = producer or PlanOnlyDeterministicTopicProducer()
        self._runs: dict[str, ProductionRunViewV1] = {}

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
            run = _project_run(request, normalized, approved.story_plan)
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
        self._runs[run.run_id] = ProductionRunViewV1.model_validate(run.model_dump(mode="python"))
        return _json_detached(PlanOnlyCreateResultV1(run=run))

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
    if method == "GET" and path.startswith(prefix) and "/" not in path[len(prefix) :]:
        run = application.get_run(path[len(prefix) :])
        return PlanOnlyFacadeResponse(
            status_code=200 if run is not None else 404,
            payload=run,
        )
    return PlanOnlyFacadeResponse(status_code=404, payload=None)


__all__ = [
    "PLAN_ONLY_API_PREFIX",
    "PlanOnlyApplication",
    "PlanOnlyCreateRequestV1",
    "PlanOnlyCreateResultV1",
    "PlanOnlyDeterministicTopicProducer",
    "PlanOnlyFacadeResponse",
    "ProductionCapabilitiesV1",
    "ProductionDashboardViewV1",
    "ProductionRunViewV1",
    "PublicApiErrorV1",
    "dispatch_plan_only_request",
]
