"""Fail-closed mapping from authorized accepted images to the existing renderer."""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
import hashlib
import json
import math
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any, Literal, Self
import weakref

from PIL import Image, UnidentifiedImageError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    field_serializer,
    field_validator,
    model_validator,
)

from tella.composer.timing import DEFAULT_TIMING_TOLERANCE_SECONDS
from tella.planner.models import MediaSource, Scene, TellaScenePlan
from tella.visual_generation.providers.kinds import ProviderKind

from .duration_policy import DurationValueAuthority
from .execution_models import (
    _canonical_production_run_planning_hash,
    _revalidate_current_production_run_plan,
)
from .models import GenerationTier
from .narration_measurement import (
    _inspect_processed_narration_artifact,
    _stream_sha256,
)
from .runtime import _revalidate_execution_state, evaluate_execution_readiness
from .runtime_models import ExecutionRunState, ProcessedNarrationDurationMeasurement
from .story_plan_identity import canonical_story_plan_sha256
from .strategy import VisualExecutionMode


_TIMELINE_QUANTUM = Decimal("0.000001")
_TRANSITION_DURATIONS_SECONDS = MappingProxyType(
    {
        "subtle_crossfade": Decimal("0.800000"),
        "clean_soft_cut": Decimal("0.000000"),
        "clean_progressive_cut": Decimal("0.000000"),
    }
)
_BUILDER_AUTHORIZATION_SEAL = object()


@dataclass(frozen=True, slots=True)
class _AuthorizedBridgeRegistration:
    reference: weakref.ReferenceType[Any]
    payload_sha256: str


_AUTHORIZED_BRIDGE_REFS: dict[int, _AuthorizedBridgeRegistration] = {}
_WINDOWS_RESERVED_JOB_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
)


def _timeline_decimal(value: object, *, field_name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{field_name} must be a finite number")
    decimal_value = Decimal(str(value))
    if not decimal_value.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return decimal_value


def _quantize_timeline(value: Decimal) -> Decimal:
    return value.quantize(_TIMELINE_QUANTUM, rounding=ROUND_HALF_EVEN)


def _resolve_configured_transition_duration(transition_profile_id: str) -> Decimal:
    transition = _TRANSITION_DURATIONS_SECONDS.get(transition_profile_id)
    if transition is None:
        raise ValueError(f"unsupported renderer transition profile: {transition_profile_id}")
    return transition


class _StrictFrozenRendererAuthorityModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        strict=True,
    )

    def model_copy(
        self,
        *,
        update: dict[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(mode="python")
        if deep:
            payload = deepcopy(payload)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)


class AuthorizedCandidateRequest(BaseModel):
    """Caller-owned exact provider serialization identity for one scene."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scene_id: str = Field(pattern=r"^scene_[0-9]{2}$")
    order: int = Field(ge=1)
    source_tier: GenerationTier
    provider_kind: ProviderKind
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    seed: int = Field(ge=0)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    planning_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    logical_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class RendererBridgeAuthorization(BaseModel):
    """Caller-owned identity and realization constraints for one authorized run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str = Field(min_length=1)
    planning_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    story_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scene_requests: list[AuthorizedCandidateRequest] = Field(min_length=1)
    required_visual_mode: Literal[VisualExecutionMode.ILLUSTRATED_SCENE] = (
        VisualExecutionMode.ILLUSTRATED_SCENE
    )
    forbid_local_compositor: bool
    supported_image_formats: tuple[Literal["PNG", "JPEG"], ...]
    require_portrait_geometry: bool


class RendererPlanProfile(BaseModel):
    """Renderer/TTS/music/accounting policy supplied by the caller, not the bridge."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str = Field(min_length=2, max_length=160)
    media_source: MediaSource
    theme: str = Field(min_length=1)
    recipe_id: str = Field(min_length=1)
    recipe_version: int = Field(ge=1)
    recipe_status: str = Field(min_length=1)
    narrative_mode: str = Field(min_length=1)
    visual_theme_id: str = Field(min_length=1)
    voice_profile_id: str = Field(min_length=1)
    subtitle_style_id: str = Field(min_length=1)
    transition_profile_id: str = Field(min_length=1)
    motion_profile_id: str = Field(min_length=1)
    recipe_scene_range: tuple[int, int]
    recipe_duration_range: tuple[float, float]
    recipe_validation_status: str = Field(min_length=1)
    voice_pace_name: str = Field(min_length=1)
    voice_edge_rate: str = Field(pattern=r"^[+-]\d{1,3}%$")
    voice_gender: str = Field(min_length=1)
    voice_name: str = Field(min_length=1)
    resolved_tts_provider: str = Field(min_length=1)
    resolved_tts_language: str = Field(min_length=1)
    resolved_voice: str = Field(min_length=1)
    resolved_voice_rate: str = Field(pattern=r"^[+-]\d{1,3}%$")
    requested_production_duration_seconds: float = Field(gt=0)
    duration_target_seconds: float = Field(gt=0)
    tts_continuous: bool
    tts_text_source: str = Field(min_length=1)
    subtitle_style: str = Field(min_length=1)
    demo_mode: bool
    music_enabled: bool
    image_request_budget_max: int = Field(ge=0)
    image_request_budget_used_at_finish: int = Field(ge=0)
    ai_images_requested: int = Field(ge=0)
    ai_images_generated: int = Field(ge=0)
    ai_images_reused: int = Field(ge=0)
    local_fallback_allowed: Literal[False]
    used_local_fallback: Literal[False]

    @model_validator(mode="after")
    def validate_ranges(self) -> "RendererPlanProfile":
        scene_min, scene_max = self.recipe_scene_range
        duration_min, duration_max = self.recipe_duration_range
        if scene_min <= 0 or scene_min > scene_max:
            raise ValueError("renderer profile scene range must be positive and ordered")
        if duration_min <= 0 or duration_min > duration_max:
            raise ValueError("renderer profile duration range must be positive and ordered")
        return self


class RendererSceneTimingInput(_StrictFrozenRendererAuthorityModel):
    scene_id: str = Field(pattern=r"^scene_[0-9]{2}$")
    order: int = Field(ge=1)
    start_seconds: float = Field(ge=0)
    duration_seconds: float = Field(gt=0)
    render_clip_duration_seconds: float = Field(gt=0)
    end_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_interval(self) -> "RendererSceneTimingInput":
        values = (
            self.start_seconds,
            self.duration_seconds,
            self.render_clip_duration_seconds,
            self.end_seconds,
        )
        if any(not math.isfinite(value) for value in values):
            raise ValueError("renderer timeline values must be finite")
        if round(self.start_seconds + self.duration_seconds, 6) != round(self.end_seconds, 6):
            raise ValueError("renderer timeline end must equal start plus duration")
        if self.render_clip_duration_seconds < self.duration_seconds:
            raise ValueError("renderer clip duration cannot be shorter than its timeline slot")
        return self


class AuthoritativeRenderTimingContract(_StrictFrozenRendererAuthorityModel):
    """Typed topic authority projected onto the legacy render timing keys."""

    schema_version: Literal[1]
    requested_duration_seconds: StrictFloat = Field(ge=0, allow_inf_nan=False)
    narration_duration_seconds: StrictFloat = Field(gt=0, allow_inf_nan=False)
    authoritative_duration_seconds: StrictFloat = Field(gt=0, allow_inf_nan=False)
    duration_authority: Literal[DurationValueAuthority.MEASURED]
    configured_transition_duration_seconds: StrictFloat = Field(ge=0, allow_inf_nan=False)
    effective_transition_duration_seconds: StrictFloat = Field(ge=0, allow_inf_nan=False)
    transition_overlap_count: int = Field(ge=0)
    total_transition_overlap_seconds: StrictFloat = Field(ge=0, allow_inf_nan=False)
    scene_timeline_durations_seconds: tuple[StrictFloat, ...]
    scene_clip_durations_seconds: tuple[StrictFloat, ...]
    scene_start_times_seconds: tuple[StrictFloat, ...]
    scene_end_times_seconds: tuple[StrictFloat, ...]
    expected_final_timeline_duration_seconds: StrictFloat = Field(gt=0, allow_inf_nan=False)
    timing_tolerance_seconds: Literal[0.15]
    requested_duration_delta_seconds: StrictFloat
    requested_duration_status: Literal["passed", "differs_from_authoritative_narration"]
    renderer_stretch_authorized: Literal[False]
    actual_rendered_duration_seconds: None
    actual_duration_delta_seconds: None
    actual_duration_status: Literal["not_evaluated"]
    actual_duration_failure_reason: Literal[""]

    @field_validator(
        "scene_timeline_durations_seconds",
        "scene_clip_durations_seconds",
        "scene_start_times_seconds",
        "scene_end_times_seconds",
        mode="before",
    )
    @classmethod
    def freeze_scene_arrays(cls, value: object) -> object:
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def validate_derived_contract(self) -> "AuthoritativeRenderTimingContract":
        arrays = (
            self.scene_timeline_durations_seconds,
            self.scene_clip_durations_seconds,
            self.scene_start_times_seconds,
            self.scene_end_times_seconds,
        )
        if not arrays[0] or any(len(values) != len(arrays[0]) for values in arrays[1:]):
            raise ValueError("render timing contract scene arrays must be non-empty and aligned")
        numeric_values = (
            self.requested_duration_seconds,
            self.narration_duration_seconds,
            self.authoritative_duration_seconds,
            self.configured_transition_duration_seconds,
            self.effective_transition_duration_seconds,
            self.total_transition_overlap_seconds,
            self.expected_final_timeline_duration_seconds,
            self.requested_duration_delta_seconds,
            *arrays[0],
            *arrays[1],
            *arrays[2],
            *arrays[3],
        )
        if any(not math.isfinite(value) for value in numeric_values):
            raise ValueError("render timing contract values must be finite")
        if any(value <= 0 for value in arrays[0]) or any(value <= 0 for value in arrays[1]):
            raise ValueError("render timing contract scene durations must be positive")
        narration_total = _timeline_decimal(
            self.narration_duration_seconds,
            field_name="narration duration",
        )
        authoritative_total = _timeline_decimal(
            self.authoritative_duration_seconds,
            field_name="authoritative duration",
        )
        expected_total = _timeline_decimal(
            self.expected_final_timeline_duration_seconds,
            field_name="expected final duration",
        )
        if narration_total != authoritative_total or expected_total != authoritative_total:
            raise ValueError("render timing contract authoritative totals are inconsistent")
        supported_transitions = {float(value) for value in _TRANSITION_DURATIONS_SECONDS.values()}
        if self.configured_transition_duration_seconds not in supported_transitions:
            raise ValueError("render timing contract transition duration is unsupported")
        expected_effective = _effective_transition_duration(
            _timeline_decimal(
                self.configured_transition_duration_seconds,
                field_name="configured transition duration",
            ),
            tuple(
                _timeline_decimal(value, field_name="scene duration")
                for value in self.scene_timeline_durations_seconds
            ),
        )
        if (
            _quantize_timeline(
                _timeline_decimal(
                    self.effective_transition_duration_seconds,
                    field_name="effective transition duration",
                )
            )
            != expected_effective
        ):
            raise ValueError("render timing contract effective transition is inconsistent")
        expected_overlap_count = (
            len(arrays[0]) - 1 if self.effective_transition_duration_seconds > 0 else 0
        )
        if self.transition_overlap_count != expected_overlap_count:
            raise ValueError("render timing contract transition overlap count is inconsistent")
        expected_overlap = _quantize_timeline(expected_effective * Decimal(expected_overlap_count))
        if (
            _quantize_timeline(
                _timeline_decimal(
                    self.total_transition_overlap_seconds,
                    field_name="total transition overlap",
                )
            )
            != expected_overlap
        ):
            raise ValueError("render timing contract transition overlap total is inconsistent")
        durations = tuple(
            _timeline_decimal(value, field_name="scene duration") for value in arrays[0]
        )
        clips = tuple(
            _timeline_decimal(value, field_name="scene clip duration") for value in arrays[1]
        )
        starts = tuple(_timeline_decimal(value, field_name="scene start") for value in arrays[2])
        ends = tuple(_timeline_decimal(value, field_name="scene end") for value in arrays[3])
        cursor = Decimal("0")
        for index, (duration, clip, start, end) in enumerate(
            zip(durations, clips, starts, ends, strict=True)
        ):
            expected_clip = duration + (
                expected_effective if index < len(durations) - 1 else Decimal("0")
            )
            if start != cursor or end != start + duration:
                raise ValueError("render timing contract scene arrays must be contiguous")
            if clip != expected_clip:
                raise ValueError("render timing contract clip arrays are inconsistent")
            cursor = end
        if cursor != expected_total:
            raise ValueError("render timing contract scene arrays do not preserve exact total")
        requested_delta = _quantize_timeline(
            _timeline_decimal(
                self.authoritative_duration_seconds,
                field_name="authoritative duration",
            )
            - _timeline_decimal(
                self.requested_duration_seconds,
                field_name="requested duration",
            )
        )
        if (
            _quantize_timeline(
                _timeline_decimal(
                    self.requested_duration_delta_seconds,
                    field_name="requested duration delta",
                )
            )
            != requested_delta
        ):
            raise ValueError("render timing contract requested duration delta is inconsistent")
        expected_status = (
            "passed"
            if abs(requested_delta) <= Decimal(str(DEFAULT_TIMING_TOLERANCE_SECONDS))
            else "differs_from_authoritative_narration"
        )
        if self.requested_duration_status != expected_status:
            raise ValueError("render timing contract requested duration status is inconsistent")
        return self


class AuthoritativeNarrationTimeline(_StrictFrozenRendererAuthorityModel):
    """Caller-owned processed narration identity and exact scene timeline."""

    story_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    narration_text: str = Field(min_length=1)
    processed_duration_seconds: StrictFloat = Field(gt=0, allow_inf_nan=False)
    scene_timings: tuple[RendererSceneTimingInput, ...] = Field(min_length=1)
    render_timing_contract: AuthoritativeRenderTimingContract
    renderer_stretch_authorized: Literal[False] = False

    @field_validator("scene_timings", "render_timing_contract", mode="before")
    @classmethod
    def detach_nested_authority(cls, value: object) -> object:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="python")
        if isinstance(value, (list, tuple)):
            return tuple(
                item.model_dump(mode="python") if isinstance(item, BaseModel) else deepcopy(item)
                for item in value
            )
        return deepcopy(value)

    @model_validator(mode="after")
    def validate_timeline_consistency(self) -> "AuthoritativeNarrationTimeline":
        if not math.isfinite(self.processed_duration_seconds):
            raise ValueError("processed narration duration must be finite")
        expected_orders = tuple(range(1, len(self.scene_timings) + 1))
        orders = tuple(item.order for item in self.scene_timings)
        if orders != expected_orders:
            raise ValueError("authoritative narration timeline scenes must use canonical order")
        expected_ids = tuple(f"scene_{order:02d}" for order in expected_orders)
        ids = tuple(item.scene_id for item in self.scene_timings)
        if ids != expected_ids:
            raise ValueError("authoritative narration timeline scene IDs must be canonical")
        contract_scene_count = len(self.render_timing_contract.scene_timeline_durations_seconds)
        if contract_scene_count != len(self.scene_timings):
            raise ValueError(
                "authoritative narration timeline scenes do not match render timing contract"
            )
        cursor = Decimal("0")
        for timing in self.scene_timings:
            start = _timeline_decimal(timing.start_seconds, field_name="scene start")
            duration = _timeline_decimal(timing.duration_seconds, field_name="scene duration")
            end = _timeline_decimal(timing.end_seconds, field_name="scene end")
            if start != cursor or end != start + duration:
                raise ValueError("authoritative narration timeline scenes must be contiguous")
            cursor = end
        total = _timeline_decimal(
            self.processed_duration_seconds,
            field_name="processed narration duration",
        )
        if cursor != total:
            raise ValueError(
                "authoritative narration timeline final duration/end must equal measured total"
            )
        contract = self.render_timing_contract
        contract_total_values = (
            contract.narration_duration_seconds,
            contract.authoritative_duration_seconds,
            contract.expected_final_timeline_duration_seconds,
        )
        if any(
            _timeline_decimal(value, field_name="contract total") != total
            for value in contract_total_values
        ):
            raise ValueError("render timing contract total does not match measured narration")
        expected_arrays = (
            tuple(item.duration_seconds for item in self.scene_timings),
            tuple(item.render_clip_duration_seconds for item in self.scene_timings),
            tuple(item.start_seconds for item in self.scene_timings),
            tuple(item.end_seconds for item in self.scene_timings),
        )
        contract_arrays = (
            contract.scene_timeline_durations_seconds,
            contract.scene_clip_durations_seconds,
            contract.scene_start_times_seconds,
            contract.scene_end_times_seconds,
        )
        if contract_arrays != expected_arrays:
            raise ValueError("render timing contract arrays do not match scene timings")
        transition = contract.effective_transition_duration_seconds
        for index, timing in enumerate(self.scene_timings):
            expected_clip = timing.duration_seconds + (
                transition if index < len(self.scene_timings) - 1 else 0.0
            )
            if round(timing.render_clip_duration_seconds, 6) != round(expected_clip, 6):
                raise ValueError("authoritative narration timeline transition is inconsistent")
        return self


def _effective_transition_duration(
    configured: Decimal,
    durations: tuple[Decimal, ...],
) -> Decimal:
    if len(durations) <= 1 or configured <= 0:
        return Decimal("0.000000")
    cap = max(Decimal("0.100000"), min(durations) / Decimal(3))
    return _quantize_timeline(min(configured, cap))


def _allocate_measured_scene_durations(
    measured_total: float,
    weights: tuple[float, ...],
) -> tuple[Decimal, tuple[Decimal, ...]]:
    total = _timeline_decimal(measured_total, field_name="measured narration duration")
    if total <= 0:
        raise ValueError("measured narration duration must be positive")
    if not weights:
        raise ValueError("measured narration timeline requires at least one scene")
    decimal_weights = tuple(
        _timeline_decimal(value, field_name="planned scene duration") for value in weights
    )
    if any(value <= 0 for value in decimal_weights):
        raise ValueError("planned scene duration weights must be strictly positive")
    weight_total = sum(decimal_weights, Decimal("0"))
    allocated: list[Decimal] = []
    assigned = Decimal("0")
    for weight in decimal_weights[:-1]:
        duration = _quantize_timeline(total * weight / weight_total)
        if duration <= 0:
            raise ValueError("measured duration cannot allocate a positive slot to every scene")
        allocated.append(duration)
        assigned += duration
    final_duration = total - assigned
    if final_duration <= 0:
        raise ValueError("measured duration cannot allocate a positive slot to every scene")
    allocated.append(final_duration)
    if sum(allocated, Decimal("0")) != total:
        raise ValueError("measured narration timeline allocation does not preserve exact total")
    return total, tuple(allocated)


def build_authoritative_narration_timeline(
    state: ExecutionRunState,
    *,
    profile: RendererPlanProfile,
) -> AuthoritativeNarrationTimeline:
    """Build one pure measured timeline from validated runtime authority."""

    validated_state = _revalidate_execution_state(state)
    validated_profile = RendererPlanProfile.model_validate(
        profile.model_dump(mode="python", warnings=False),
        strict=True,
    )
    measurement = validated_state.processed_narration_measurement
    if measurement is None:
        raise ValueError("processed narration measurement is required for timeline construction")
    assessment = measurement.measured_duration_assessment
    if assessment.value_authority is not DurationValueAuthority.MEASURED:
        raise ValueError("authoritative narration timeline requires MEASURED duration authority")
    expected_story_sha256 = canonical_story_plan_sha256(validated_state.run_plan.story_plan)
    if measurement.story_plan_sha256 != expected_story_sha256:
        raise ValueError("processed narration measurement StoryPlan SHA-256 does not match")
    execution_plans = tuple(validated_state.run_plan.scene_execution_plans)
    expected_orders = tuple(range(1, len(execution_plans) + 1))
    if not execution_plans:
        raise ValueError("authoritative narration timeline requires scene execution plans")
    if tuple(item.order for item in execution_plans) != expected_orders:
        raise ValueError("scene execution plans must be in canonical order")
    if tuple(item.scene_id for item in execution_plans) != tuple(
        f"scene_{order:02d}" for order in expected_orders
    ):
        raise ValueError("scene execution plan IDs must match canonical order")
    transition = _resolve_configured_transition_duration(validated_profile.transition_profile_id)
    total, durations = _allocate_measured_scene_durations(
        assessment.actual_duration_seconds,
        tuple(item.timing.duration_seconds for item in execution_plans),
    )
    effective_transition = _effective_transition_duration(transition, durations)
    timings: list[RendererSceneTimingInput] = []
    starts: list[Decimal] = []
    ends: list[Decimal] = []
    clips: list[Decimal] = []
    cursor = Decimal("0")
    for index, (execution, duration) in enumerate(zip(execution_plans, durations, strict=True)):
        start = cursor
        end = start + duration
        clip = duration + (
            effective_transition if index < len(execution_plans) - 1 else Decimal("0")
        )
        timings.append(
            RendererSceneTimingInput(
                scene_id=execution.scene_id,
                order=execution.order,
                start_seconds=float(start),
                duration_seconds=float(duration),
                render_clip_duration_seconds=float(clip),
                end_seconds=float(end),
            )
        )
        starts.append(start)
        ends.append(end)
        clips.append(clip)
        cursor = end
    if cursor != total:
        raise ValueError("authoritative narration timeline does not end at measured total")
    requested = _quantize_timeline(
        _timeline_decimal(
            validated_profile.requested_production_duration_seconds,
            field_name="requested production duration",
        )
    )
    requested_delta = total - requested
    tolerance = Decimal(str(DEFAULT_TIMING_TOLERANCE_SECONDS))
    contract = AuthoritativeRenderTimingContract(
        schema_version=1,
        requested_duration_seconds=float(requested),
        narration_duration_seconds=float(total),
        authoritative_duration_seconds=float(total),
        duration_authority=DurationValueAuthority.MEASURED,
        configured_transition_duration_seconds=float(transition),
        effective_transition_duration_seconds=float(effective_transition),
        transition_overlap_count=(len(execution_plans) - 1 if effective_transition > 0 else 0),
        total_transition_overlap_seconds=float(
            _quantize_timeline(
                effective_transition
                * Decimal(len(execution_plans) - 1 if effective_transition > 0 else 0)
            )
        ),
        scene_timeline_durations_seconds=tuple(float(value) for value in durations),
        scene_clip_durations_seconds=tuple(float(value) for value in clips),
        scene_start_times_seconds=tuple(float(value) for value in starts),
        scene_end_times_seconds=tuple(float(value) for value in ends),
        expected_final_timeline_duration_seconds=float(total),
        timing_tolerance_seconds=DEFAULT_TIMING_TOLERANCE_SECONDS,
        requested_duration_delta_seconds=float(requested_delta),
        requested_duration_status=(
            "passed"
            if abs(requested_delta) <= tolerance
            else "differs_from_authoritative_narration"
        ),
        renderer_stretch_authorized=False,
        actual_rendered_duration_seconds=None,
        actual_duration_delta_seconds=None,
        actual_duration_status="not_evaluated",
        actual_duration_failure_reason="",
    )
    return AuthoritativeNarrationTimeline(
        story_plan_sha256=expected_story_sha256,
        narration_text=validated_state.run_plan.story_plan.narration_text,
        processed_duration_seconds=float(total),
        scene_timings=tuple(timings),
        render_timing_contract=contract,
    )


class RendererAcceptedCandidateInput(_StrictFrozenRendererAuthorityModel):
    scene_id: str = Field(pattern=r"^scene_[0-9]{2}$")
    order: int = Field(ge=1)
    candidate_id: str = Field(min_length=1)
    artifact_path: Path
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    image_format: Literal["PNG", "JPEG"]
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    provider_kind: ProviderKind
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    seed: int = Field(ge=0)
    planning_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    logical_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_hashes: tuple[str, ...] = Field(default_factory=tuple)
    qc_record_id: str = Field(min_length=1)

    @field_validator("reference_hashes", mode="before")
    @classmethod
    def freeze_reference_hashes(cls, value: object) -> object:
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return value

    @field_validator("reference_hashes")
    @classmethod
    def validate_reference_hashes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
            for value in values
        ):
            raise ValueError("reference hashes must be lowercase SHA-256 digests")
        return values


class AcceptedCandidateRendererBridge(_StrictFrozenRendererAuthorityModel):
    """Portable narration and accepted-image authority for one renderer plan."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        strict=True,
        serialize_by_alias=True,
        validate_by_alias=True,
        validate_by_name=True,
    )

    job_id: str
    processed_narration_measurement: ProcessedNarrationDurationMeasurement
    renderer_plan_snapshot: str = Field(
        validation_alias="renderer_plan",
        serialization_alias="renderer_plan",
    )
    accepted_inputs: tuple[RendererAcceptedCandidateInput, ...] = Field(min_length=1)
    source_state_ready: Literal[True] = True
    external_calls: Literal[0] = 0
    files_written: Literal[0] = 0

    @property
    def renderer_plan(self) -> TellaScenePlan:
        return TellaScenePlan.model_validate_json(self.renderer_plan_snapshot)

    def __iter__(self) -> Iterator[tuple[str, object]]:
        yield from self.model_dump(mode="python").items()

    def model_copy(
        self,
        *,
        update: dict[str, Any] | None = None,
        deep: bool = False,
    ) -> "AcceptedCandidateRendererBridge":
        if update:
            raise ValueError(
                "AcceptedCandidateRendererBridge authority cannot be changed through model_copy"
            )
        registration = _AUTHORIZED_BRIDGE_REFS.get(id(self))
        if registration is not None and registration.reference() is self:
            raise ValueError("sealed AcceptedCandidateRendererBridge authority cannot be copied")
        return super().model_copy(deep=deep)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AcceptedCandidateRendererBridge):
            return NotImplemented
        return self.model_dump(mode="json") == other.model_dump(mode="json")

    def require_builder_authorization(self) -> "AcceptedCandidateRendererBridge":
        bridge_id = id(self)
        registration = _AUTHORIZED_BRIDGE_REFS.get(bridge_id)
        if registration is None or registration.reference() is not self:
            raise ValueError("renderer bridge was not authorized by the accepted-candidate builder")
        try:
            snapshot, payload_sha256 = _validated_bridge_payload_snapshot(self)
        except Exception as error:
            if _AUTHORIZED_BRIDGE_REFS.get(bridge_id) is registration:
                _AUTHORIZED_BRIDGE_REFS.pop(bridge_id, None)
            raise ValueError("renderer bridge authorized payload is malformed") from error
        if payload_sha256 != registration.payload_sha256:
            if _AUTHORIZED_BRIDGE_REFS.get(bridge_id) is registration:
                _AUTHORIZED_BRIDGE_REFS.pop(bridge_id, None)
            raise ValueError("renderer bridge authorized payload does not match minted authority")
        return snapshot

    @classmethod
    def _mint_builder_authorized(
        cls,
        *,
        _mint_authority: object,
        **data: object,
    ) -> "AcceptedCandidateRendererBridge":
        if _mint_authority is not _BUILDER_AUTHORIZATION_SEAL:
            raise ValueError("invalid renderer bridge mint authority")
        bridge = cls.model_validate(data)
        _, payload_sha256 = _validated_bridge_payload_snapshot(bridge)
        bridge_id = id(bridge)

        def release_authorization(
            reference: weakref.ReferenceType[AcceptedCandidateRendererBridge],
        ) -> None:
            registration = _AUTHORIZED_BRIDGE_REFS.get(bridge_id)
            if registration is not None and registration.reference is reference:
                _AUTHORIZED_BRIDGE_REFS.pop(bridge_id, None)

        reference = weakref.ref(bridge, release_authorization)
        _AUTHORIZED_BRIDGE_REFS[bridge_id] = _AuthorizedBridgeRegistration(
            reference=reference,
            payload_sha256=payload_sha256,
        )
        return bridge

    @field_validator("processed_narration_measurement", mode="before")
    @classmethod
    def detach_authority_model(cls, value: object) -> object:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="python")
        return deepcopy(value)

    @field_validator("renderer_plan_snapshot", mode="before")
    @classmethod
    def snapshot_renderer_plan(cls, value: object) -> str:
        if isinstance(value, TellaScenePlan):
            plan = TellaScenePlan.model_validate(value.model_dump(mode="python"))
        elif isinstance(value, str):
            plan = TellaScenePlan.model_validate_json(value)
        else:
            plan = TellaScenePlan.model_validate(deepcopy(value))
        return plan.model_dump_json()

    @field_serializer("renderer_plan_snapshot")
    def serialize_renderer_plan_snapshot(self, value: str) -> dict[str, object]:
        decoded = json.loads(value)
        if not isinstance(decoded, dict):
            raise ValueError("renderer plan snapshot must serialize as an object")
        return decoded

    @field_validator("accepted_inputs", mode="before")
    @classmethod
    def detach_accepted_inputs(cls, value: object) -> object:
        if isinstance(value, (list, tuple)):
            return tuple(
                item.model_dump(mode="python") if isinstance(item, BaseModel) else deepcopy(item)
                for item in value
            )
        return deepcopy(value)

    @field_validator("job_id")
    @classmethod
    def require_canonical_job_id(cls, value: str) -> str:
        if not value or value != value.strip() or value in {".", ".."}:
            raise ValueError("renderer bridge job ID must be one portable directory basename")
        if any(character in '<>:"/\\|?*' or ord(character) < 32 for character in value):
            raise ValueError("renderer bridge job ID must be one portable directory basename")
        posix_path = PurePosixPath(value)
        windows_path = PureWindowsPath(value)
        if (
            posix_path.is_absolute()
            or len(posix_path.parts) != 1
            or windows_path.drive
            or windows_path.root
            or len(windows_path.parts) != 1
            or value.rstrip(" .") != value
            or value.split(".", 1)[0].upper() in _WINDOWS_RESERVED_JOB_NAMES
        ):
            raise ValueError("renderer bridge job ID must be one portable directory basename")
        return value

    @model_validator(mode="after")
    def validate_authority_correspondence(self) -> "AcceptedCandidateRendererBridge":
        measurement = self.processed_narration_measurement
        measured_duration = measurement.measured_duration_assessment.actual_duration_seconds
        plan = self.renderer_plan
        if (
            plan.narration_duration != measured_duration
            or plan.processed_narration_duration != measured_duration
            or plan.total_duration != measured_duration
        ):
            raise ValueError("renderer plan duration does not match narration measurement")
        if not plan.global_narration_text:
            raise ValueError("renderer plan requires authoritative global narration text")

        contract = AuthoritativeRenderTimingContract.model_validate(
            plan.render_timing_contract,
            strict=True,
        )
        if (
            contract.narration_duration_seconds != measured_duration
            or contract.authoritative_duration_seconds != measured_duration
            or contract.expected_final_timeline_duration_seconds != measured_duration
        ):
            raise ValueError("renderer timing contract does not match narration measurement")

        inputs = self.accepted_inputs
        scenes = tuple(plan.scenes)
        if len(inputs) != len(scenes):
            raise ValueError("accepted input count does not match renderer plan scene count")
        input_identity = tuple((item.scene_id, item.order) for item in inputs)
        if len({item.scene_id for item in inputs}) != len(inputs):
            raise ValueError("accepted inputs contain duplicate scene IDs")
        if len({item.candidate_id for item in inputs}) != len(inputs):
            raise ValueError("accepted inputs contain duplicate candidate IDs")
        expected_identity = tuple(
            (f"scene_{order:02d}", order) for order in range(1, len(scenes) + 1)
        )
        renderer_identity = tuple(
            (f"scene_{scene.scene_index:02d}", scene.scene_index) for scene in scenes
        )
        if renderer_identity != expected_identity:
            raise ValueError("renderer plan scenes are not in canonical order")
        if input_identity != expected_identity:
            raise ValueError("accepted input scene IDs/order do not match renderer plan")

        timing_map = tuple(plan.scene_timing_map)
        if len(timing_map) != len(scenes):
            raise ValueError("renderer timing map does not match renderer plan scene count")
        for index, (item, scene, timing) in enumerate(zip(inputs, scenes, timing_map, strict=True)):
            artifact_text = str(item.artifact_path)
            if (
                scene.asset_path != artifact_text
                or scene.selected_attempt_path != artifact_text
                or scene.image_filenames != [artifact_text]
            ):
                raise ValueError("accepted input artifact path does not match renderer plan")
            if scene.image_provider != item.provider or scene.provider != item.provider:
                raise ValueError("accepted input provider does not match renderer plan")
            expected_timing = {
                "scene_index": item.order,
                "start": contract.scene_start_times_seconds[index],
                "duration": contract.scene_timeline_durations_seconds[index],
                "timeline_duration": contract.scene_timeline_durations_seconds[index],
                "render_clip_duration": contract.scene_clip_durations_seconds[index],
                "outgoing_transition_overlap": round(
                    contract.scene_clip_durations_seconds[index]
                    - contract.scene_timeline_durations_seconds[index],
                    6,
                ),
                "end": contract.scene_end_times_seconds[index],
            }
            if timing != expected_timing:
                raise ValueError("accepted input timing does not match renderer plan")
            if (
                scene.start != expected_timing["start"]
                or scene.duration != expected_timing["duration"]
                or scene.audio_duration != expected_timing["duration"]
                or scene.render_clip_duration != expected_timing["render_clip_duration"]
            ):
                raise ValueError("renderer scene timing does not match timing contract")
        return self


def _validated_bridge_payload_snapshot(
    bridge: AcceptedCandidateRendererBridge,
) -> tuple[AcceptedCandidateRendererBridge, str]:
    snapshot = AcceptedCandidateRendererBridge.model_validate_json(bridge.model_dump_json())
    canonical_payload = json.dumps(
        snapshot.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return snapshot, hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _renderer_primary_text(values: list[str], *, maximum: int) -> str:
    value = next((item.strip() for item in values if item.strip()), "")
    if len(value) > maximum:
        raise ValueError(f"renderer compatibility field exceeds {maximum} characters: {value}")
    return value


def _artifact_path(value: str, artifact_root: Path | None) -> Path:
    path = Path(value)
    if not path.is_absolute():
        if artifact_root is None:
            raise ValueError("relative accepted artifact paths require an explicit artifact_root")
        path = artifact_root / path
    return path.resolve()


def _validate_image(
    path: Path,
    *,
    expected_width: int,
    expected_height: int,
    supported_formats: tuple[str, ...],
    require_portrait: bool,
) -> tuple[str, int, int]:
    try:
        with Image.open(path) as image:
            image_format = (image.format or "").upper()
            image.verify()
        with Image.open(path) as image:
            image.load()
            width, height = image.size
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError(f"accepted artifact is not a decodable image: {path}") from exc
    if image_format not in supported_formats:
        raise ValueError(f"accepted artifact format is unsupported: {image_format}")
    if (width, height) != (expected_width, expected_height):
        raise ValueError("accepted artifact dimensions do not match the authorized request")
    if require_portrait and height <= width:
        raise ValueError("accepted artifact does not have required portrait geometry")
    return image_format, width, height


def _validate_authorized_run(
    state: ExecutionRunState,
    authorization: RendererBridgeAuthorization,
) -> None:
    run = _revalidate_current_production_run_plan(state.run_plan)
    if run.job_id != authorization.job_id:
        raise ValueError("renderer authorization job ID does not match")
    if run.planning_hash != authorization.planning_hash:
        raise ValueError("renderer authorization planning hash does not match")
    expected_hash = _canonical_production_run_planning_hash(
        job_id=run.job_id,
        story_plan=run.story_plan,
        scene_execution_plans=run.scene_execution_plans,
        manifest=run.manifest,
        production_strategy=run.production_strategy,
    )
    if expected_hash != run.planning_hash:
        raise ValueError("run planning identity does not match its authorized contents")
    story_hash = canonical_story_plan_sha256(state.run_plan.story_plan)
    if story_hash != authorization.story_plan_sha256:
        raise ValueError("renderer authorization StoryPlan SHA-256 does not match")
    if run.production_strategy.visual_mode is not authorization.required_visual_mode:
        raise ValueError("authorized run is not illustrated-scene execution")

    expected = run.scene_execution_plans
    actual_identity = [(item.scene_id, item.execution_plan.order) for item in state.scenes]
    expected_identity = [(item.scene_id, item.order) for item in expected]
    if actual_identity != expected_identity:
        raise ValueError(
            "runtime scene set/order does not exactly match the authorized execution plan"
        )
    if any(
        runtime.execution_plan != planned
        for runtime, planned in zip(state.scenes, expected, strict=True)
    ):
        raise ValueError("runtime scene execution differs from the authorized plan")
    request_identity = [(item.scene_id, item.order) for item in authorization.scene_requests]
    if request_identity != expected_identity:
        raise ValueError("authorized request scene set/order does not match the execution plan")


def _validate_timeline(
    state: ExecutionRunState,
    timeline: AuthoritativeNarrationTimeline,
) -> dict[str, RendererSceneTimingInput]:
    story = state.run_plan.story_plan
    if timeline.story_plan_sha256 != canonical_story_plan_sha256(story):
        raise ValueError("narration timeline StoryPlan SHA-256 does not match")
    if timeline.narration_text != story.narration_text:
        raise ValueError("narration timeline text does not match authoritative StoryPlan")
    expected_identity = [
        (item.scene_id, item.order) for item in state.run_plan.scene_execution_plans
    ]
    actual_identity = [(item.scene_id, item.order) for item in timeline.scene_timings]
    if actual_identity != expected_identity:
        raise ValueError("narration timeline scene set/order does not match")
    prior_end = 0.0
    transition = timeline.render_timing_contract.effective_transition_duration_seconds
    last_index = len(timeline.scene_timings) - 1
    for index, timing in enumerate(timeline.scene_timings):
        if round(timing.start_seconds, 6) != round(prior_end, 6):
            raise ValueError("narration timeline must be contiguous")
        expected_clip_duration = timing.duration_seconds + (
            transition if index < last_index else 0.0
        )
        if round(timing.render_clip_duration_seconds, 6) != round(expected_clip_duration, 6):
            raise ValueError("renderer clip duration does not preserve effective transition timing")
        prior_end = timing.end_seconds
    if round(prior_end, 6) != round(timeline.processed_duration_seconds, 6):
        raise ValueError("narration timeline does not end at processed narration duration")
    return {item.scene_id: item for item in timeline.scene_timings}


def _validate_processed_narration_artifact(
    state: ExecutionRunState,
    timeline: AuthoritativeNarrationTimeline,
    *,
    artifact_path: Path,
    artifact_root: Path,
) -> ExecutionRunState:
    validated_state = _revalidate_execution_state(state)
    measurement = validated_state.processed_narration_measurement
    if measurement is None:
        raise ValueError("processed narration measurement is required for renderer preparation")
    validated_state, resolved_path, relative_path = _inspect_processed_narration_artifact(
        validated_state,
        artifact_path=artifact_path,
        artifact_root=artifact_root,
    )
    if relative_path != measurement.artifact_relative_path:
        raise ValueError("renderer narration artifact path does not match bound measurement")
    if _stream_sha256(resolved_path) != measurement.artifact_sha256:
        raise ValueError("renderer narration artifact SHA-256 does not match bound measurement")
    if measurement.story_plan_sha256 != canonical_story_plan_sha256(
        validated_state.run_plan.story_plan
    ):
        raise ValueError("renderer narration measurement StoryPlan SHA-256 does not match")
    measured_duration = measurement.measured_duration_assessment.actual_duration_seconds
    if timeline.processed_duration_seconds != measured_duration:
        raise ValueError("renderer narration duration does not match bound measurement")
    return validated_state


def _validate_attempt(
    runtime_scene,
    authorized_identity: AuthorizedCandidateRequest,
    *,
    forbid_local_compositor: bool,
) -> tuple[object, object, str]:
    accepted = runtime_scene.accepted_candidate
    assert accepted is not None
    attempt = next(
        (
            item
            for item in runtime_scene.generation_attempts
            if item.candidate_id == accepted.candidate_id
        ),
        None,
    )
    assert attempt is not None
    execution = runtime_scene.execution_plan
    authorized_request = (
        execution.draft if accepted.source_tier is GenerationTier.DRAFT else execution.acceptance
    )
    if accepted.source_tier is not authorized_identity.source_tier:
        raise ValueError("accepted candidate source tier is unauthorized")
    if forbid_local_compositor and attempt.provider_kind is ProviderKind.LOCAL_COMPOSITOR:
        raise ValueError("local-compositor candidates are forbidden by authorization")
    if attempt.provider_kind is not authorized_identity.provider_kind:
        raise ValueError("accepted candidate provider kind is unauthorized")
    if (
        attempt.provider != authorized_identity.provider
        or attempt.model != authorized_identity.model
    ):
        raise ValueError("accepted candidate provider/model is unauthorized")
    if (
        attempt.provider != authorized_request.provider
        or attempt.model != authorized_request.model
        or attempt.seed != authorized_request.seed
    ):
        raise ValueError(
            "accepted candidate provider/model/seed does not match authorized execution"
        )
    if execution.local_execution is not None:
        raise ValueError("illustrated accepted candidate cannot use local execution")
    if execution.routing is not None:
        selected = execution.routing.route.selected_provider
        if selected is not authorized_identity.provider_kind:
            raise ValueError("scene route does not authorize the accepted provider")
    if authorized_request.accepted_scene_chaining:
        raise ValueError("generated-scene chaining is unauthorized")
    if any(
        bool(attempt.metadata.get(flag))
        for flag in (
            "fallback_used",
            "used_local_fallback",
            "local_fallback_used",
            "provider_fallback_used",
        )
    ):
        raise ValueError("accepted candidate contains unauthorized fallback provenance")

    if attempt.planning_request_hash != execution.draft.logical_visual_request_hash:
        raise ValueError("candidate planning request hash is unauthorized")
    if attempt.planning_request_hash != authorized_identity.planning_request_hash:
        raise ValueError("candidate planning request authorization does not match")
    if attempt.logical_request_hash != authorized_identity.logical_request_hash:
        raise ValueError("candidate logical request hash is unauthorized")
    if attempt.provider_request_hash != authorized_identity.provider_request_hash:
        raise ValueError("candidate provider request hash is unauthorized")
    if (
        authorized_identity.provider != authorized_request.provider
        or authorized_identity.model != authorized_request.model
        or authorized_identity.seed != authorized_request.seed
        or authorized_identity.width != authorized_request.width
        or authorized_identity.height != authorized_request.height
    ):
        raise ValueError("caller authorization does not match scene execution request")
    return accepted, authorized_request, authorized_identity.provider_request_hash


def build_renderer_plan_from_accepted_candidates(
    state: ExecutionRunState,
    *,
    authorization: RendererBridgeAuthorization,
    profile: RendererPlanProfile,
    narration_timeline: AuthoritativeNarrationTimeline,
    narration_artifact_path: Path,
    artifact_root: Path,
) -> AcceptedCandidateRendererBridge:
    """Validate exact accepted provenance and construct a renderer input plan.

    The function performs no copying, generation, TTS, rendering, or provider
    activity. All production policy and timeline values come from caller-owned
    validated inputs.
    """

    state = _validate_processed_narration_artifact(
        state,
        narration_timeline,
        artifact_path=narration_artifact_path,
        artifact_root=artifact_root,
    )
    profile = RendererPlanProfile.model_validate(
        profile.model_dump(mode="python", warnings=False),
        strict=True,
    )
    _validate_authorized_run(state, authorization)
    timings = _validate_timeline(state, narration_timeline)
    configured_transition = _resolve_configured_transition_duration(profile.transition_profile_id)
    timeline_transition = _timeline_decimal(
        narration_timeline.render_timing_contract.configured_transition_duration_seconds,
        field_name="timeline configured transition duration",
    )
    if timeline_transition != configured_transition:
        raise ValueError(
            "renderer profile transition does not match narration timeline "
            "configured transition authority"
        )
    readiness = evaluate_execution_readiness(state)
    if not readiness.ready:
        raise ValueError(
            "accepted candidates are not renderer-ready: " + readiness.model_dump_json()
        )
    inputs: list[RendererAcceptedCandidateInput] = []
    renderer_scenes: list[Scene] = []
    for runtime_scene, authorized_identity in zip(
        state.scenes,
        authorization.scene_requests,
        strict=True,
    ):
        accepted, authorized_request, expected_provider_hash = _validate_attempt(
            runtime_scene,
            authorized_identity,
            forbid_local_compositor=authorization.forbid_local_compositor,
        )
        brief = runtime_scene.execution_plan.scene_brief
        path = _artifact_path(accepted.artifact_path, artifact_root)
        if not path.is_file():
            raise ValueError(f"accepted artifact is missing for {brief.scene_id}: {path}")
        actual_sha256 = _sha256_file(path)
        if actual_sha256 != accepted.artifact_sha256:
            raise ValueError(f"accepted artifact SHA-256 mismatch for {brief.scene_id}")
        image_format, width, height = _validate_image(
            path,
            expected_width=authorized_request.width,
            expected_height=authorized_request.height,
            supported_formats=authorization.supported_image_formats,
            require_portrait=authorization.require_portrait_geometry,
        )
        expected_mime = {"PNG": "image/png", "JPEG": "image/jpeg"}[image_format]
        attempt = next(
            item
            for item in runtime_scene.generation_attempts
            if item.candidate_id == accepted.candidate_id
        )
        if (
            attempt.metadata.get("mime_type") != expected_mime
            or attempt.metadata.get("actual_width") != width
            or attempt.metadata.get("actual_height") != height
        ):
            raise ValueError("candidate image metadata does not match decoded artifact")

        timing = timings[brief.scene_id]
        inputs.append(
            RendererAcceptedCandidateInput(
                scene_id=brief.scene_id,
                order=brief.order,
                candidate_id=accepted.candidate_id,
                artifact_path=path,
                artifact_sha256=actual_sha256,
                image_format=image_format,
                width=width,
                height=height,
                provider_kind=attempt.provider_kind,
                provider=accepted.provider,
                model=accepted.model,
                seed=accepted.seed,
                planning_request_hash=accepted.planning_request_hash,
                logical_request_hash=accepted.logical_request_hash,
                provider_request_hash=expected_provider_hash,
                reference_hashes=accepted.reference_hashes,
                qc_record_id=accepted.qc_record_id,
            )
        )
        renderer_scenes.append(
            Scene(
                kind="scene",
                scene_index=brief.order,
                title="",
                voice_script=brief.narrative_text,
                character_names=brief.characters,
                requested_characters=brief.characters,
                required_characters=brief.characters,
                cast_source="topic_production_accepted_candidate",
                scene_setting=_renderer_primary_text(
                    brief.environment,
                    maximum=80,
                ),
                scene_action=_renderer_primary_text(
                    brief.action,
                    maximum=80,
                ),
                setting_source="production_scene_brief",
                action_source="production_scene_brief",
                scene_meaning=brief.meaning,
                emotional_state=_renderer_primary_text(
                    brief.emotional_tone,
                    maximum=120,
                ),
                image_source="accepted_candidate_registry",
                image_provider=accepted.provider,
                provider=accepted.provider,
                asset_status="done",
                asset_count=1,
                asset_path=str(path),
                selected_attempt_path=str(path),
                image_filenames=[str(path)],
                qc_passed=True,
                final_passed=True,
                scene_image_attempt_count=1,
                visual_mode=authorization.required_visual_mode.value,
                used_local_fallback=False,
                local_fallback_allowed=False,
                audio_duration=timing.duration_seconds,
                duration=timing.duration_seconds,
                render_clip_duration=timing.render_clip_duration_seconds,
                start=timing.start_seconds,
            )
        )

    story = state.run_plan.story_plan
    renderer_plan = TellaScenePlan(
        title=profile.title,
        language=story.language,
        aspect_ratio=story.aspect_ratio,
        media_source=profile.media_source,
        theme=profile.theme,
        recipe_id=profile.recipe_id,
        recipe_version=profile.recipe_version,
        recipe_status=profile.recipe_status,
        narrative_mode=profile.narrative_mode,
        planner_id=story.planner_metadata.planner_id,
        visual_theme_id=profile.visual_theme_id,
        voice_profile_id=profile.voice_profile_id,
        subtitle_style_id=profile.subtitle_style_id,
        transition_profile_id=profile.transition_profile_id,
        motion_profile_id=profile.motion_profile_id,
        recipe_scene_range=list(profile.recipe_scene_range),
        recipe_duration_range=list(profile.recipe_duration_range),
        recipe_validation_status=profile.recipe_validation_status,
        voice_pace_name=profile.voice_pace_name,
        voice_edge_rate=profile.voice_edge_rate,
        voice_gender=profile.voice_gender,
        voice_name=profile.voice_name,
        resolved_tts_provider=profile.resolved_tts_provider,
        resolved_tts_language=profile.resolved_tts_language,
        resolved_voice=profile.resolved_voice,
        resolved_voice_rate=profile.resolved_voice_rate,
        requested_production_duration_seconds=(profile.requested_production_duration_seconds),
        duration_target_seconds=profile.duration_target_seconds,
        scenes=renderer_scenes,
        demo_mode=profile.demo_mode,
        global_narration_text=narration_timeline.narration_text,
        tts_continuous=profile.tts_continuous,
        tts_text_source=profile.tts_text_source,
        narration_duration=narration_timeline.processed_duration_seconds,
        processed_narration_duration=narration_timeline.processed_duration_seconds,
        scene_timing_map=[
            {
                "scene_index": timing.order,
                "start": timing.start_seconds,
                "duration": timing.duration_seconds,
                "timeline_duration": timing.duration_seconds,
                "render_clip_duration": timing.render_clip_duration_seconds,
                "outgoing_transition_overlap": round(
                    timing.render_clip_duration_seconds - timing.duration_seconds,
                    6,
                ),
                "end": timing.end_seconds,
            }
            for timing in narration_timeline.scene_timings
        ],
        render_timing_contract=narration_timeline.render_timing_contract.model_dump(mode="python"),
        total_duration=narration_timeline.processed_duration_seconds,
        subtitle_style=profile.subtitle_style,
        music_enabled=profile.music_enabled,
        image_request_budget_max=profile.image_request_budget_max,
        image_request_budget_used_at_finish=(profile.image_request_budget_used_at_finish),
        ai_images_requested=profile.ai_images_requested,
        ai_images_generated=profile.ai_images_generated,
        ai_images_reused=profile.ai_images_reused,
        local_fallback_allowed=profile.local_fallback_allowed,
        used_local_fallback=profile.used_local_fallback,
    )
    return AcceptedCandidateRendererBridge._mint_builder_authorized(
        _mint_authority=_BUILDER_AUTHORIZATION_SEAL,
        job_id=state.run_plan.job_id,
        processed_narration_measurement=state.processed_narration_measurement,
        renderer_plan=renderer_plan,
        accepted_inputs=tuple(inputs),
    )


__all__ = [
    "AcceptedCandidateRendererBridge",
    "AuthorizedCandidateRequest",
    "AuthoritativeNarrationTimeline",
    "RendererAcceptedCandidateInput",
    "RendererBridgeAuthorization",
    "RendererPlanProfile",
    "RendererSceneTimingInput",
    "build_renderer_plan_from_accepted_candidates",
]
