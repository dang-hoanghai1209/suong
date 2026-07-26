"""Fail-closed mapping from authorized accepted images to the existing renderer."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tella.planner.models import MediaSource, Scene, TellaScenePlan
from tella.visual_generation.providers.kinds import ProviderKind

from .execution_models import (
    _canonical_production_run_planning_hash,
    _revalidate_current_production_run_plan,
)
from .models import GenerationTier
from .runtime import evaluate_execution_readiness
from .runtime_models import ExecutionRunState
from .story_plan_identity import canonical_story_plan_sha256
from .strategy import VisualExecutionMode


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


class RendererSceneTimingInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    scene_id: str = Field(pattern=r"^scene_[0-9]{2}$")
    order: int = Field(ge=1)
    start_seconds: float = Field(ge=0)
    duration_seconds: float = Field(gt=0)
    render_clip_duration_seconds: float = Field(gt=0)
    end_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_interval(self) -> "RendererSceneTimingInput":
        if round(self.start_seconds + self.duration_seconds, 6) != round(self.end_seconds, 6):
            raise ValueError("renderer timeline end must equal start plus duration")
        if self.render_clip_duration_seconds < self.duration_seconds:
            raise ValueError("renderer clip duration cannot be shorter than its timeline slot")
        return self


class AuthoritativeNarrationTimeline(BaseModel):
    """Caller-owned processed narration identity and exact scene timeline."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    story_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    narration_text: str = Field(min_length=1)
    processed_duration_seconds: float = Field(gt=0)
    scene_timings: list[RendererSceneTimingInput] = Field(min_length=1)
    render_timing_contract: dict[str, object]
    renderer_stretch_authorized: Literal[False] = False

    @model_validator(mode="after")
    def validate_render_timing_contract(self) -> "AuthoritativeNarrationTimeline":
        required = {
            "expected_final_timeline_duration_seconds",
            "timing_tolerance_seconds",
        }
        missing = sorted(required - self.render_timing_contract.keys())
        if missing:
            raise ValueError(
                "authoritative narration timeline is missing required render "
                f"timing fields: {missing}"
            )
        tolerance = float(self.render_timing_contract["timing_tolerance_seconds"])
        if tolerance < 0:
            raise ValueError("render timing tolerance cannot be negative")
        return self


class RendererAcceptedCandidateInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

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
    reference_hashes: list[str] = Field(default_factory=list)
    qc_record_id: str = Field(min_length=1)


class AcceptedCandidateRendererBridge(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    renderer_plan: TellaScenePlan
    accepted_inputs: list[RendererAcceptedCandidateInput]
    source_state_ready: Literal[True] = True
    external_calls: Literal[0] = 0
    files_written: Literal[0] = 0


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
    transition = float(
        timeline.render_timing_contract.get(
            "effective_transition_duration_seconds",
            0.0,
        )
    )
    if transition < 0:
        raise ValueError("effective transition duration cannot be negative")
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
    artifact_root: Path | None = None,
) -> AcceptedCandidateRendererBridge:
    """Validate exact accepted provenance and construct a renderer input plan.

    The function performs no copying, generation, TTS, rendering, or provider
    activity. All production policy and timeline values come from caller-owned
    validated inputs.
    """

    _validate_authorized_run(state, authorization)
    readiness = evaluate_execution_readiness(state)
    if not readiness.ready:
        raise ValueError(
            "accepted candidates are not renderer-ready: " + readiness.model_dump_json()
        )
    timings = _validate_timeline(state, narration_timeline)

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
        render_timing_contract=narration_timeline.render_timing_contract,
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
    return AcceptedCandidateRendererBridge(
        renderer_plan=renderer_plan,
        accepted_inputs=inputs,
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
