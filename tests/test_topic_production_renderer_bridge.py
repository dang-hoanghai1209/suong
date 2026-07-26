from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image
import pytest
from pydantic import ValidationError

from tella.composer.timing import build_render_timing_plan
from tella.topic_production import (
    AuthoritativeNarrationTimeline,
    AuthorizedCandidateRequest,
    GenerationAttempt,
    GenerationTier,
    QCCheckOutcome,
    QCChecks,
    QCDecision,
    RendererBridgeAuthorization,
    RendererPlanProfile,
    RendererSceneTimingInput,
    TechnicalStatus,
    ExecutionRunState,
    ProductionRunPlan,
    authorize_draft_acceptance,
    build_fixture_preview_run,
    build_renderer_plan_from_accepted_candidates,
    initialize_execution_state,
    record_generation_attempt,
    record_human_qc,
    register_accepted_candidate,
)
from tella.topic_production.duration_policy import DurationAssessmentStatus
from tella.topic_production.story_plan_identity import canonical_story_plan_sha256
from tella.visual_generation.providers.kinds import ProviderKind


def _checks() -> QCChecks:
    return QCChecks(
        technical_generation=QCCheckOutcome.PASS,
        scene_meaning=QCCheckOutcome.PASS,
        identity=QCCheckOutcome.PASS,
        action_pose=QCCheckOutcome.PASS,
        anatomy=QCCheckOutcome.PASS,
        composition=QCCheckOutcome.PASS,
        reference_consistency=QCCheckOutcome.PASS,
    )


def _accepted_state_with_valid_images(tmp_path: Path):
    run = build_fixture_preview_run(
        topic="offline accepted-candidate renderer bridge",
        job_id="renderer-bridge-test",
    )
    state = initialize_execution_state(run)
    authorized_requests = []
    for runtime_scene in state.scenes:
        execution = runtime_scene.execution_plan
        draft = execution.draft
        logical_hash = hashlib.sha256(f"logical:{runtime_scene.scene_id}".encode()).hexdigest()
        provider_hash = hashlib.sha256(f"provider:{runtime_scene.scene_id}".encode()).hexdigest()
        artifact = tmp_path / f"{runtime_scene.scene_id}.png"
        Image.new("RGB", (draft.width, draft.height), "#a58f82").save(
            artifact,
            "PNG",
        )
        artifact_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
        candidate_id = f"{runtime_scene.scene_id}-draft-authorized"
        attempt = GenerationAttempt(
            scene_id=runtime_scene.scene_id,
            tier=GenerationTier.DRAFT,
            provider=draft.provider,
            provider_kind=ProviderKind.CLOUDFLARE_KLEIN_4B,
            model=draft.model,
            seed=draft.seed,
            candidate_id=candidate_id,
            candidate_path=str(artifact),
            artifact_sha256=artifact_hash,
            planning_request_hash=draft.logical_visual_request_hash,
            logical_request_hash=logical_hash,
            provider_request_hash=provider_hash,
            reference_hashes=[item.sha256 for item in draft.references],
            technical_status=TechnicalStatus.SUCCEEDED,
            metadata={
                "mime_type": "image/png",
                "actual_width": draft.width,
                "actual_height": draft.height,
                "used_local_fallback": False,
            },
        )
        state = record_generation_attempt(state, attempt)
        qc_record_id = f"qc-{candidate_id}"
        state = record_human_qc(
            state,
            qc_record_id=qc_record_id,
            scene_id=runtime_scene.scene_id,
            candidate_id=candidate_id,
            tier=GenerationTier.DRAFT,
            decision=QCDecision.PASS,
            reviewer="human-test-reviewer",
            checks=_checks(),
        )
        state = authorize_draft_acceptance(
            state,
            scene_id=runtime_scene.scene_id,
            reason="explicit test authorization",
            authorized_by="test-operator",
        )
        state = register_accepted_candidate(
            state,
            scene_id=runtime_scene.scene_id,
            candidate_id=candidate_id,
            qc_record_id=qc_record_id,
            accepted_by="test-operator",
        )
        authorized_requests.append(
            AuthorizedCandidateRequest(
                scene_id=runtime_scene.scene_id,
                order=execution.order,
                source_tier=GenerationTier.DRAFT,
                provider_kind=ProviderKind.CLOUDFLARE_KLEIN_4B,
                provider=draft.provider,
                model=draft.model,
                seed=draft.seed,
                width=draft.width,
                height=draft.height,
                planning_request_hash=draft.logical_visual_request_hash,
                logical_request_hash=logical_hash,
                provider_request_hash=provider_hash,
            )
        )
    authorization = RendererBridgeAuthorization(
        job_id=run.job_id,
        planning_hash=run.planning_hash,
        story_plan_sha256=canonical_story_plan_sha256(run.story_plan),
        scene_requests=authorized_requests,
        forbid_local_compositor=True,
        supported_image_formats=("PNG", "JPEG"),
        require_portrait_geometry=True,
    )
    return state, authorization


def _profile(scene_count: int = 8) -> RendererPlanProfile:
    return RendererPlanProfile(
        title="Authorized renderer bridge",
        media_source="ai_image",
        theme="minimalist_emotional",
        recipe_id="emotional_symbolic_v1",
        recipe_version=1,
        recipe_status="production",
        narrative_mode="emotional_reflection",
        visual_theme_id="minimalist_symbolic_reel",
        voice_profile_id="soft_female_vi",
        subtitle_style_id="reel_minimal",
        transition_profile_id="subtle_crossfade",
        motion_profile_id="slow_ken_burns",
        recipe_scene_range=(7, 8),
        recipe_duration_range=(32.0, 38.0),
        recipe_validation_status="passed",
        voice_pace_name="slow",
        voice_edge_rate="-15%",
        voice_gender="female",
        voice_name="vi-VN-HoaiMyNeural",
        resolved_tts_provider="edge",
        resolved_tts_language="vi-VN",
        resolved_voice="vi-VN-HoaiMyNeural",
        resolved_voice_rate="-15%",
        requested_production_duration_seconds=35.0,
        duration_target_seconds=35.0,
        tts_continuous=True,
        tts_text_source="authoritative_narration_timeline",
        subtitle_style="reel_minimal",
        demo_mode=True,
        music_enabled=False,
        image_request_budget_max=0,
        image_request_budget_used_at_finish=0,
        ai_images_requested=0,
        ai_images_generated=0,
        ai_images_reused=scene_count,
        local_fallback_allowed=False,
        used_local_fallback=False,
    )


def test_renderer_profile_rejects_noncanonical_media_source() -> None:
    with pytest.raises(ValidationError, match="media_source"):
        RendererPlanProfile.model_validate(
            {
                **_profile().model_dump(),
                "media_source": "accepted_ai_images",
            }
        )


def test_legacy_story_without_source_spans_cannot_build_runtime_authority() -> None:
    run = build_fixture_preview_run(
        topic="legacy payload authority rejection",
        job_id="legacy-source-span-test",
    )
    run_payload = run.model_dump(mode="python")
    for beat in run_payload["story_plan"]["semantic_beats"]:
        beat.pop("source_span")

    with pytest.raises(ValidationError):
        ProductionRunPlan.model_validate(run_payload)

    state = initialize_execution_state(run)
    state_payload = state.model_dump(mode="python")
    for beat in state_payload["run_plan"]["story_plan"]["semantic_beats"]:
        beat.pop("source_span")

    with pytest.raises(ValidationError):
        ExecutionRunState.model_validate(state_payload)


def _timeline(state) -> AuthoritativeNarrationTimeline:
    effective_transition = 0.8
    execution_plans = state.run_plan.scene_execution_plans
    timing_plan = build_render_timing_plan(
        [item.timing.duration_seconds for item in execution_plans],
        narration_duration=execution_plans[-1].timing.end_seconds,
        configured_transition_duration=effective_transition,
    )
    timings = [
        RendererSceneTimingInput(
            scene_id=item.scene_id,
            order=item.order,
            start_seconds=timing_plan.scene_starts[index],
            duration_seconds=timing_plan.scene_timeline_durations[index],
            render_clip_duration_seconds=timing_plan.scene_clip_durations[index],
            end_seconds=round(
                timing_plan.scene_starts[index] + timing_plan.scene_timeline_durations[index],
                6,
            ),
        )
        for index, item in enumerate(execution_plans)
    ]
    return AuthoritativeNarrationTimeline(
        story_plan_sha256=canonical_story_plan_sha256(state.run_plan.story_plan),
        narration_text=state.run_plan.story_plan.narration_text,
        processed_duration_seconds=timing_plan.authoritative_duration,
        scene_timings=timings,
        render_timing_contract={
            **timing_plan.metadata(),
            "renderer_stretch_authorized": False,
        },
    )


def test_timeline_rejects_missing_postmux_tolerance(tmp_path: Path) -> None:
    state, _ = _accepted_state_with_valid_images(tmp_path)
    payload = _timeline(state).model_dump()
    contract = dict(payload["render_timing_contract"])
    contract.pop("timing_tolerance_seconds")
    payload["render_timing_contract"] = contract

    with pytest.raises(ValidationError, match="timing_tolerance_seconds"):
        AuthoritativeNarrationTimeline.model_validate(payload)


def _bridge(state, authorization):
    return build_renderer_plan_from_accepted_candidates(
        state,
        authorization=authorization,
        profile=_profile(),
        narration_timeline=_timeline(state),
    )


def _mutate_candidate(
    state,
    *,
    scene_index: int = 0,
    attempt_updates: dict | None = None,
    accepted_updates: dict | None = None,
):
    scenes = list(state.scenes)
    scene = scenes[scene_index]
    accepted = scene.accepted_candidate
    assert accepted is not None
    attempts = [
        item.model_copy(update=attempt_updates or {})
        if item.candidate_id == accepted.candidate_id
        else item
        for item in scene.generation_attempts
    ]
    accepted = accepted.model_copy(update=accepted_updates or {})
    scenes[scene_index] = scene.model_copy(
        update={
            "generation_attempts": attempts,
            "accepted_candidate": accepted,
        },
        deep=True,
    )
    return state.model_copy(update={"scenes": scenes}, deep=True)


def test_bridge_maps_validated_images_without_side_effects(tmp_path: Path) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    before = sorted(tmp_path.iterdir())

    bridge = _bridge(state, authorization)

    assert bridge.source_state_ready
    assert bridge.external_calls == 0
    assert bridge.files_written == 0
    assert sorted(tmp_path.iterdir()) == before
    assert len(bridge.accepted_inputs) == 8
    assert all(item.image_format == "PNG" for item in bridge.accepted_inputs)
    assert all((item.width, item.height) == (576, 1024) for item in bridge.accepted_inputs)
    assert bridge.renderer_plan.global_narration_text == state.run_plan.story_plan.narration_text
    assert bridge.renderer_plan.processed_narration_duration == 35.0
    assert bridge.renderer_plan.total_duration == 35.0
    assert bridge.renderer_plan.render_timing_contract["renderer_stretch_authorized"] is False
    assert bridge.renderer_plan.ai_images_requested == 0
    assert bridge.renderer_plan.ai_images_generated == 0
    assert bridge.renderer_plan.ai_images_reused == 8
    assert not bridge.renderer_plan.used_local_fallback


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "wrong_order", "extra"])
def test_bridge_requires_exact_authorized_scene_set_and_order(
    tmp_path: Path,
    mutation: str,
) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    scenes = list(state.scenes)
    if mutation == "missing":
        scenes = scenes[:-1]
    elif mutation == "wrong_order":
        scenes[0], scenes[1] = scenes[1], scenes[0]
    else:
        scenes.append(scenes[0])
    changed = state.model_copy(update={"scenes": scenes}, deep=True)

    with pytest.raises(ValueError, match="scene set/order"):
        _bridge(changed, authorization)


def test_bridge_rejects_wrong_run_or_planning_identity(tmp_path: Path) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    wrong_job = authorization.model_copy(update={"job_id": "wrong-run"})
    with pytest.raises(ValueError, match="job ID"):
        _bridge(state, wrong_job)

    wrong_hash = authorization.model_copy(update={"planning_hash": "0" * 64})
    with pytest.raises(ValueError, match="planning hash"):
        _bridge(state, wrong_hash)


def test_bridge_revalidates_unsafe_constructed_planned_policy(
    tmp_path: Path,
) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    run_payload = {
        field: getattr(state.run_plan, field) for field in ProductionRunPlan.model_fields
    }
    assessment_payload = state.run_plan.planned_duration_assessment.model_dump(mode="python")
    assessment_payload["status"] = DurationAssessmentStatus.OUTSIDE_TARGET_WARNING
    unsafe_assessment = type(state.run_plan.planned_duration_assessment).model_construct(
        **assessment_payload
    )
    run_payload["planned_duration_assessment"] = unsafe_assessment
    unsafe_run = ProductionRunPlan.model_construct(**run_payload)
    unsafe_state = state.model_copy(update={"run_plan": unsafe_run}, deep=True)

    with pytest.raises(ValidationError):
        _bridge(unsafe_state, authorization)


def test_bridge_rejects_in_memory_schema_two_run_plan(tmp_path: Path) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    run_payload = {
        field: getattr(state.run_plan, field)
        for field in ProductionRunPlan.model_fields
        if field
        not in {
            "planned_duration_assessment",
            "planned_beat_pacing_warnings",
        }
    }
    run_payload["schema_version"] = 2
    unsafe_run = ProductionRunPlan.model_construct(**run_payload)
    unsafe_state = state.model_copy(update={"run_plan": unsafe_run}, deep=True)

    with pytest.raises(
        ValueError,
        match="in-memory ProductionRunPlan authority requires schema_version 3",
    ):
        _bridge(unsafe_state, authorization)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("planning_request_hash", "0" * 64, "planning request"),
        ("logical_request_hash", "1" * 64, "logical request hash"),
        ("provider_request_hash", "2" * 64, "provider request hash"),
    ],
)
def test_bridge_rejects_wrong_request_hashes(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    changed = _mutate_candidate(
        state,
        attempt_updates={field: value},
        accepted_updates={field: value},
    )

    with pytest.raises(ValueError, match=message):
        _bridge(changed, authorization)


def test_bridge_rejects_narration_or_timeline_identity_mismatch(
    tmp_path: Path,
) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    timeline = _timeline(state).model_copy(update={"narration_text": "changed"})
    with pytest.raises(ValueError, match="narration timeline text"):
        build_renderer_plan_from_accepted_candidates(
            state,
            authorization=authorization,
            profile=_profile(),
            narration_timeline=timeline,
        )

    timings = list(_timeline(state).scene_timings)
    timings[0] = timings[0].model_copy(update={"scene_id": "scene_08"})
    timeline = _timeline(state).model_copy(update={"scene_timings": timings})
    with pytest.raises(ValueError, match="timeline scene set/order"):
        build_renderer_plan_from_accepted_candidates(
            state,
            authorization=authorization,
            profile=_profile(),
            narration_timeline=timeline,
        )


@pytest.mark.parametrize(
    ("attempt_updates", "accepted_updates"),
    [
        ({"provider": "wrong-provider"}, {"provider": "wrong-provider"}),
        ({"model": "wrong-model"}, {"model": "wrong-model"}),
        ({"seed": 999}, {"seed": 999}),
    ],
)
def test_bridge_rejects_provider_model_or_seed_mismatch(
    tmp_path: Path,
    attempt_updates: dict,
    accepted_updates: dict,
) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    changed = _mutate_candidate(
        state,
        attempt_updates=attempt_updates,
        accepted_updates=accepted_updates,
    )

    with pytest.raises(ValueError, match="unauthorized|authorized execution"):
        _bridge(changed, authorization)


def test_bridge_rejects_unaccepted_or_nonpassing_qc_state(tmp_path: Path) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    unaccepted = initialize_execution_state(state.run_plan)
    with pytest.raises(ValueError, match="not renderer-ready"):
        _bridge(unaccepted, authorization)

    scenes = list(state.scenes)
    scenes[0] = scenes[0].model_copy(update={"qc_records": []}, deep=True)
    missing_qc = state.model_copy(update={"scenes": scenes}, deep=True)
    with pytest.raises(ValueError, match="not renderer-ready"):
        _bridge(missing_qc, authorization)


def test_bridge_rejects_local_compositor_or_fallback_provenance(
    tmp_path: Path,
) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    local = _mutate_candidate(
        state,
        attempt_updates={
            "provider_kind": ProviderKind.LOCAL_COMPOSITOR,
            "provider": ProviderKind.LOCAL_COMPOSITOR.value,
            "model": "semantic_asset_compositor_v2",
        },
        accepted_updates={
            "provider": ProviderKind.LOCAL_COMPOSITOR.value,
            "model": "semantic_asset_compositor_v2",
        },
    )
    with pytest.raises(ValueError, match="local-compositor"):
        _bridge(local, authorization)

    attempt = state.scenes[0].generation_attempts[0]
    fallback = _mutate_candidate(
        state,
        attempt_updates={"metadata": {**attempt.metadata, "used_local_fallback": True}},
    )
    with pytest.raises(ValueError, match="fallback provenance"):
        _bridge(fallback, authorization)


def test_bridge_rejects_missing_artifact_or_sha_mismatch(tmp_path: Path) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    path = Path(state.scenes[0].accepted_candidate.artifact_path)
    path.unlink()
    with pytest.raises(ValueError, match="artifact is missing"):
        _bridge(state, authorization)

    state, authorization = _accepted_state_with_valid_images(tmp_path)
    path = Path(state.scenes[0].accepted_candidate.artifact_path)
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        _bridge(state, authorization)


def test_bridge_rejects_invalid_image_bytes(tmp_path: Path) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    path = Path(state.scenes[0].accepted_candidate.artifact_path)
    path.write_bytes(b"not an image")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    changed = _mutate_candidate(
        state,
        attempt_updates={"artifact_sha256": digest},
        accepted_updates={"artifact_sha256": digest},
    )

    with pytest.raises(ValueError, match="not a decodable image"):
        _bridge(changed, authorization)


def test_bridge_rejects_wrong_image_dimensions(tmp_path: Path) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    path = Path(state.scenes[0].accepted_candidate.artifact_path)
    Image.new("RGB", (100, 100), "white").save(path, "PNG")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    attempt = state.scenes[0].generation_attempts[0]
    changed = _mutate_candidate(
        state,
        attempt_updates={
            "artifact_sha256": digest,
            "metadata": {
                **attempt.metadata,
                "actual_width": 100,
                "actual_height": 100,
            },
        },
        accepted_updates={"artifact_sha256": digest},
    )

    with pytest.raises(ValueError, match="dimensions"):
        _bridge(changed, authorization)
