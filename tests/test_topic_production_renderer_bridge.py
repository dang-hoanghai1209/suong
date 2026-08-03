from __future__ import annotations

import copy
from copy import deepcopy
from dataclasses import FrozenInstanceError
import gc
import hashlib
import json
from pathlib import Path
import weakref

from PIL import Image
import pytest
from pydantic import ValidationError

from tella.composer.timing import DEFAULT_TIMING_TOLERANCE_SECONDS
import tella.topic_production.renderer_bridge as renderer_bridge
from tella.topic_production import (
    AcceptedCandidateRendererBridge,
    AuthoritativeNarrationTimeline,
    AuthorizedCandidateRequest,
    GenerationAttempt,
    GenerationTier,
    QCCheckOutcome,
    QCChecks,
    QCDecision,
    RendererBridgeAuthorization,
    RendererAcceptedCandidateInput,
    RendererPlanProfile,
    TechnicalStatus,
    ExecutionRunState,
    ProcessedNarrationDurationMeasurement,
    ProductionRunPlan,
    authorize_draft_acceptance,
    build_fixture_preview_run,
    build_renderer_plan_from_accepted_candidates,
    clear_processed_narration_measurement,
    initialize_execution_state,
    bind_processed_narration_measurement,
    record_generation_attempt,
    record_human_qc,
    register_accepted_candidate,
)
from tella.topic_production.renderer_bridge import (
    build_authoritative_narration_timeline,
)
from tella.topic_production.duration_policy import DurationAssessmentStatus
from tella.topic_production.duration_policy import (
    DurationValueAuthority,
    assess_mvp_duration_target,
)
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


def _accepted_state_with_valid_images(tmp_path: Path, *, run=None):
    run = run or build_fixture_preview_run(
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
    narration = tmp_path / "narration" / "final.mp3"
    narration.parent.mkdir(parents=True, exist_ok=True)
    narration.write_bytes(b"final processed narration")
    state = bind_processed_narration_measurement(
        state,
        ProcessedNarrationDurationMeasurement(
            schema_version=1,
            artifact_relative_path="narration/final.mp3",
            artifact_sha256=hashlib.sha256(narration.read_bytes()).hexdigest(),
            story_plan_sha256=canonical_story_plan_sha256(run.story_plan),
            measurement_method="ffprobe_single_audio_stream_v1",
            measured_duration_assessment=assess_mvp_duration_target(
                35.0,
                value_authority=DurationValueAuthority.MEASURED,
            ),
        ),
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
    return build_authoritative_narration_timeline(
        state,
        profile=_profile(scene_count=len(state.run_plan.scene_execution_plans)),
    )


def test_timeline_rejects_missing_postmux_tolerance(tmp_path: Path) -> None:
    state, _ = _accepted_state_with_valid_images(tmp_path)
    payload = _timeline(state).model_dump()
    contract = dict(payload["render_timing_contract"])
    contract.pop("timing_tolerance_seconds")
    payload["render_timing_contract"] = contract

    with pytest.raises(ValidationError, match="timing_tolerance_seconds"):
        AuthoritativeNarrationTimeline.model_validate(payload)


def _bridge(state, authorization, *, artifact_root: Path | None = None):
    if artifact_root is None:
        accepted = state.scenes[0].accepted_candidate
        assert accepted is not None
        artifact_root = Path(accepted.artifact_path).parent
    return build_renderer_plan_from_accepted_candidates(
        state,
        authorization=authorization,
        profile=_profile(),
        narration_timeline=_timeline(state),
        narration_artifact_path=artifact_root / "narration" / "final.mp3",
        artifact_root=artifact_root,
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


def _unsafe_state_with_run_plan(state, run_plan):
    fields = {
        field_name: getattr(state, field_name) for field_name in ExecutionRunState.model_fields
    }
    fields["run_plan"] = run_plan
    return ExecutionRunState.model_construct(**fields)


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


def test_bridge_embeds_portable_narration_identity(tmp_path: Path) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    measurement = state.processed_narration_measurement
    assert measurement is not None

    bridge = _bridge(state, authorization)

    assert bridge.job_id == state.run_plan.job_id
    assert bridge.processed_narration_measurement == measurement
    assert bridge.processed_narration_measurement is not measurement
    assert bridge.processed_narration_measurement.artifact_relative_path == "narration/final.mp3"
    assert bridge.processed_narration_measurement.artifact_sha256 == measurement.artifact_sha256
    assert bridge.processed_narration_measurement.story_plan_sha256 == canonical_story_plan_sha256(
        state.run_plan.story_plan
    )
    assert (
        bridge.processed_narration_measurement.measurement_method
        == "ffprobe_single_audio_stream_v1"
    )
    assert (
        bridge.processed_narration_measurement.measured_duration_assessment.value_authority
        is DurationValueAuthority.MEASURED
    )
    assert (
        bridge.processed_narration_measurement.measured_duration_assessment.actual_duration_seconds
        == bridge.renderer_plan.processed_narration_duration
        == bridge.renderer_plan.total_duration
    )
    assert "artifact_root" not in bridge.model_dump(mode="json")
    assert "job_dir" not in bridge.model_dump(mode="json")


def test_bridge_detaches_all_nested_caller_owned_authority(tmp_path: Path) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    source = _bridge(state, authorization)
    payload = source.model_dump(mode="python")
    caller_plan = source.renderer_plan
    caller_inputs = list(payload["accepted_inputs"])
    caller_inputs[0]["reference_hashes"] = list(caller_inputs[0]["reference_hashes"])
    caller_measurement = deepcopy(payload["processed_narration_measurement"])
    bridge = AcceptedCandidateRendererBridge(
        job_id=source.job_id,
        processed_narration_measurement=caller_measurement,
        renderer_plan=caller_plan,
        accepted_inputs=caller_inputs,
    )
    original = bridge.model_dump(mode="python")

    caller_plan.scenes.clear()
    caller_inputs.clear()
    caller_measurement["artifact_relative_path"] = "foreign.mp3"
    payload["accepted_inputs"][0]["reference_hashes"] = ("f" * 64,)
    bridge.renderer_plan.scenes.clear()
    bridge.renderer_plan.render_timing_contract.clear()
    bridge.renderer_plan.scene_timing_map.clear()
    dict(bridge)["renderer_plan"]["scenes"].clear()

    assert bridge.model_dump(mode="python") == original
    assert isinstance(bridge.accepted_inputs, tuple)
    assert isinstance(bridge.accepted_inputs[0].reference_hashes, tuple)
    with pytest.raises(ValidationError, match="frozen"):
        bridge.accepted_inputs[0].reference_hashes = ()
    with pytest.raises(ValidationError, match="frozen"):
        bridge.processed_narration_measurement.artifact_relative_path = "foreign.mp3"


def test_bridge_serialization_copy_and_existing_instance_validation_are_safe(
    tmp_path: Path,
) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    bridge = _bridge(state, authorization)
    original = bridge.model_dump(mode="python")
    json_payload = bridge.model_dump(mode="json")
    json_text = bridge.model_dump_json()

    public_fields = {
        "job_id",
        "processed_narration_measurement",
        "renderer_plan",
        "accepted_inputs",
        "source_state_ready",
        "external_calls",
        "files_written",
    }
    assert set(original) == public_fields
    assert set(json_payload) == public_fields
    assert json.loads(json_text) == json_payload
    assert not any(name.startswith("_") for name in original)
    restored = AcceptedCandidateRendererBridge.model_validate_json(json_text)
    validated = AcceptedCandidateRendererBridge.model_validate(bridge)
    assert restored == bridge
    assert validated == bridge
    assert validated is not bridge
    for unsealed in (restored, validated):
        with pytest.raises(ValueError, match="not authorized"):
            unsealed.require_builder_authorization()

    with pytest.raises(ValueError, match="sealed.*cannot be copied"):
        bridge.model_copy()
    with pytest.raises(ValueError, match="sealed.*cannot be copied"):
        bridge.model_copy(deep=True)
    for copied in (restored.model_copy(), restored.model_copy(deep=True)):
        assert copied == bridge
        assert copied is not restored
        copied.renderer_plan.scenes.clear()
        assert copied.model_dump(mode="python") == original
        with pytest.raises(ValueError, match="not authorized"):
            copied.require_builder_authorization()

    original["renderer_plan"]["scenes"].clear()
    json_payload["accepted_inputs"].clear()
    parsed = json.loads(json_text)
    parsed["processed_narration_measurement"]["artifact_relative_path"] = "foreign.mp3"
    assert bridge == _bridge(state, authorization)
    assert bridge.model_dump(mode="python") != original
    assert "_builder_authorization" not in repr(bridge)
    assert "_BUILDER_AUTHORIZATION_SEAL" not in repr(bridge)

    with pytest.raises(ValueError, match="cannot be changed through model_copy"):
        bridge.model_copy(
            update={
                "processed_narration_measurement": {
                    **bridge.processed_narration_measurement.model_dump(mode="python"),
                    "measured_duration_assessment": assess_mvp_duration_target(
                        34.0,
                        value_authority=DurationValueAuthority.MEASURED,
                    ),
                }
            }
        )
    with pytest.raises(ValueError, match="cannot be changed through model_copy"):
        bridge.model_copy(update={"artifact_root": tmp_path})
    with pytest.raises(ValueError, match="cannot be changed through model_copy"):
        bridge.model_copy(update={"job_id": "foreign-job"})


def test_builder_mints_only_ephemeral_authorized_bridge_capability(tmp_path: Path) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    bridge = _bridge(state, authorization)
    payload = bridge.model_dump(mode="python")

    bridge.require_builder_authorization()
    snapshots = (
        AcceptedCandidateRendererBridge(**payload),
        AcceptedCandidateRendererBridge.model_validate(payload),
        AcceptedCandidateRendererBridge.model_validate_json(bridge.model_dump_json()),
        AcceptedCandidateRendererBridge.model_validate(dict(bridge)),
    )
    for snapshot in snapshots:
        assert snapshot == bridge
        with pytest.raises(ValueError, match="not authorized"):
            snapshot.require_builder_authorization()

    assert "_builder_authorization" not in payload
    assert "_builder_authorization" not in bridge.model_dump_json()
    assert "_BUILDER_AUTHORIZATION_SEAL" not in repr(bridge)
    assert "_AUTHORIZED_BRIDGE_REFS" not in repr(bridge)
    assert not hasattr(bridge, "_builder_authorization")
    assert not any("authorization" in name or "seal" in name for name in vars(bridge))
    for seal_field in (
        "_builder_authorization",
        "_mint_authority",
        "builder_authorization",
        "seal",
    ):
        with pytest.raises(ValidationError, match="extra_forbidden"):
            AcceptedCandidateRendererBridge.model_validate(
                {
                    **payload,
                    seal_field: True,
                }
            )


def _assert_authorized_payload_mutation_is_permanently_revoked(
    bridge: AcceptedCandidateRendererBridge,
    *,
    owner: object,
    field: str,
    replacement: object,
) -> None:
    original_payload = bridge.model_dump(mode="python")
    storage = vars(owner)
    original_value = storage[field]
    registration = renderer_bridge._AUTHORIZED_BRIDGE_REFS[id(bridge)]
    result: object = None

    storage[field] = replacement
    with pytest.raises(
        ValueError,
        match="authorized payload (is malformed|does not match minted authority)",
    ):
        result = bridge.require_builder_authorization()
    assert result is None
    assert id(bridge) not in renderer_bridge._AUTHORIZED_BRIDGE_REFS

    storage[field] = original_value
    assert bridge.model_dump(mode="python") == original_payload
    with pytest.raises(ValueError, match="not authorized"):
        bridge.require_builder_authorization()
    assert registration.reference() is bridge


@pytest.mark.parametrize(
    "mutation",
    [
        "job_id",
        "renderer_plan_snapshot",
        "narration_path",
        "narration_sha",
        "story_plan_sha",
        "measured_duration",
        "scene_id",
        "candidate_id",
        "artifact_path",
        "artifact_sha",
        "provider",
        "reference_hashes",
        "source_state_ready",
        "external_calls",
        "files_written",
    ],
)
def test_builder_authorization_is_bound_to_complete_public_payload(
    tmp_path: Path,
    mutation: str,
) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    bridge = _bridge(state, authorization)
    measurement = bridge.processed_narration_measurement
    candidate = bridge.accepted_inputs[0]
    mutated_plan = bridge.renderer_plan
    vars(mutated_plan)["title"] = "Foreign renderer plan"
    targets = {
        "job_id": (bridge, "job_id", "foreign-job"),
        "renderer_plan_snapshot": (
            bridge,
            "renderer_plan_snapshot",
            mutated_plan.model_dump_json(),
        ),
        "narration_path": (
            measurement,
            "artifact_relative_path",
            "narration/foreign.mp3",
        ),
        "narration_sha": (measurement, "artifact_sha256", "0" * 64),
        "story_plan_sha": (measurement, "story_plan_sha256", "1" * 64),
        "measured_duration": (
            measurement.measured_duration_assessment,
            "actual_duration_seconds",
            33.125,
        ),
        "scene_id": (candidate, "scene_id", "scene_99"),
        "candidate_id": (candidate, "candidate_id", "foreign-candidate"),
        "artifact_path": (candidate, "artifact_path", tmp_path / "foreign.png"),
        "artifact_sha": (candidate, "artifact_sha256", "2" * 64),
        "provider": (candidate, "provider", "foreign-provider"),
        "reference_hashes": (candidate, "reference_hashes", ("3" * 64,)),
        "source_state_ready": (bridge, "source_state_ready", False),
        "external_calls": (bridge, "external_calls", 1),
        "files_written": (bridge, "files_written", 1),
    }
    owner, field, replacement = targets[mutation]

    _assert_authorized_payload_mutation_is_permanently_revoked(
        bridge,
        owner=owner,
        field=field,
        replacement=replacement,
    )


def test_authorization_atomically_returns_detached_unsealed_snapshot(tmp_path: Path) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    bridge = _bridge(state, authorization)
    original = bridge.model_dump(mode="python")

    snapshot = bridge.require_builder_authorization()

    assert snapshot == bridge
    assert snapshot is not bridge
    assert snapshot.processed_narration_measurement is not bridge.processed_narration_measurement
    assert (
        snapshot.processed_narration_measurement.measured_duration_assessment
        is not bridge.processed_narration_measurement.measured_duration_assessment
    )
    assert snapshot.accepted_inputs is not bridge.accepted_inputs
    assert snapshot.accepted_inputs[0] is not bridge.accepted_inputs[0]
    with pytest.raises(ValueError, match="not authorized"):
        snapshot.require_builder_authorization()

    vars(snapshot)["job_id"] = "snapshot-only"
    vars(snapshot.processed_narration_measurement)["artifact_sha256"] = "0" * 64
    vars(snapshot.accepted_inputs[0])["provider"] = "snapshot-only"
    assert bridge.model_dump(mode="python") == original
    assert bridge.require_builder_authorization() == bridge


def test_builder_authorization_registration_is_immutable_and_weak(tmp_path: Path) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    bridge = _bridge(state, authorization)
    bridge_id = id(bridge)
    registration = renderer_bridge._AUTHORIZED_BRIDGE_REFS[bridge_id]
    bridge_reference = weakref.ref(bridge)
    canonical_payload = json.dumps(
        bridge.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )

    assert registration.reference() is bridge
    assert (
        registration.payload_sha256 == hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
    )
    with pytest.raises(FrozenInstanceError):
        registration.payload_sha256 = "0" * 64

    del bridge
    gc.collect()

    assert bridge_reference() is None
    assert bridge_id not in renderer_bridge._AUTHORIZED_BRIDGE_REFS


def test_python_copy_protocol_cannot_inherit_builder_authorization(tmp_path: Path) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    bridge = _bridge(state, authorization)

    shallow = copy.copy(bridge)
    deep = copy.deepcopy(bridge)

    for copied in (shallow, deep):
        assert copied == bridge
        assert copied is not bridge
        with pytest.raises(ValueError, match="not authorized"):
            copied.require_builder_authorization()
    assert bridge.require_builder_authorization() == bridge


def test_malformed_renderer_plan_snapshot_revocation_is_permanent(tmp_path: Path) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    bridge = _bridge(state, authorization)
    original_snapshot = vars(bridge)["renderer_plan_snapshot"]

    vars(bridge)["renderer_plan_snapshot"] = '{"scenes":[]}'
    with pytest.raises(ValueError, match="authorized payload is malformed"):
        bridge.require_builder_authorization()
    assert id(bridge) not in renderer_bridge._AUTHORIZED_BRIDGE_REFS

    vars(bridge)["renderer_plan_snapshot"] = original_snapshot
    with pytest.raises(ValueError, match="not authorized"):
        bridge.require_builder_authorization()


def test_equal_python_and_json_reconstructions_remain_unsealed(tmp_path: Path) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    bridge = _bridge(state, authorization)
    reconstructions = (
        AcceptedCandidateRendererBridge.model_validate(bridge.model_dump(mode="python")),
        AcceptedCandidateRendererBridge.model_validate_json(bridge.model_dump_json()),
    )

    for reconstructed in reconstructions:
        assert reconstructed == bridge
        assert reconstructed is not bridge
        with pytest.raises(ValueError, match="not authorized"):
            reconstructed.require_builder_authorization()
    assert bridge.require_builder_authorization() == bridge


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("job_id", "foreign-job"),
        ("artifact_relative_path", "narration/foreign.mp3"),
        ("artifact_sha256", "0" * 64),
        ("story_plan_sha256", "1" * 64),
        ("candidate_id", "foreign-candidate"),
        ("candidate_artifact_sha256", "2" * 64),
        ("reference_hashes", ("3" * 64,)),
    ],
)
def test_foreign_payload_reconstruction_never_mints_authorization(
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    bridge = _bridge(state, authorization)
    payload = bridge.model_dump(mode="python")
    if field == "job_id":
        payload["job_id"] = replacement
    elif field in {"artifact_relative_path", "artifact_sha256", "story_plan_sha256"}:
        payload["processed_narration_measurement"][field] = replacement
    elif field == "candidate_artifact_sha256":
        payload["accepted_inputs"][0]["artifact_sha256"] = replacement
    else:
        payload["accepted_inputs"][0][field] = replacement

    reconstructed = AcceptedCandidateRendererBridge.model_validate(payload)

    with pytest.raises(ValueError, match="not authorized"):
        reconstructed.require_builder_authorization()


@pytest.mark.parametrize(
    "job_id",
    [
        "",
        " ",
        ".",
        "..",
        "job/child",
        "job\\child",
        "C:job",
        "C:\\job",
        "/job",
        "\\\\server\\share",
        " job",
        "job ",
    ],
)
def test_bridge_rejects_nonportable_job_ids(tmp_path: Path, job_id: str) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    payload = _bridge(state, authorization).model_dump(mode="python")
    payload["job_id"] = job_id

    with pytest.raises(ValidationError, match="portable directory basename"):
        AcceptedCandidateRendererBridge.model_validate(payload)


def test_bridge_accepts_portable_job_id(tmp_path: Path) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    payload = _bridge(state, authorization).model_dump(mode="python")
    payload["job_id"] = "valid-job_01"

    snapshot = AcceptedCandidateRendererBridge.model_validate(payload)

    assert snapshot.job_id == "valid-job_01"
    with pytest.raises(ValueError, match="not authorized"):
        snapshot.require_builder_authorization()


def test_renderer_plan_canonical_snapshot_has_no_mutable_storage_escape(
    tmp_path: Path,
) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    bridge = _bridge(state, authorization)
    original = bridge.model_dump(mode="python")
    first = bridge.renderer_plan
    second = bridge.renderer_plan

    assert first == second
    assert first is not second
    assert not any(isinstance(value, type(first)) for value in vars(bridge).values())
    assert isinstance(vars(bridge)["renderer_plan_snapshot"], str)

    first.scenes.clear()
    second.render_timing_contract.clear()
    second.scene_timing_map.clear()
    dict_payload = dict(bridge)
    dict_payload["renderer_plan"]["scenes"].clear()
    dumped = bridge.model_dump(mode="python")
    dumped["renderer_plan"]["render_timing_contract"].clear()
    dumped["renderer_plan"]["scene_timing_map"].clear()

    assert bridge.model_dump(mode="python") == original
    assert len(bridge.renderer_plan.scenes) == 8
    bridge.require_builder_authorization()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing", "count"),
        ("extra", "count"),
        ("duplicate", "scene IDs|scene IDs/order"),
        ("reordered", "scene IDs/order"),
        ("foreign_path", "artifact path"),
        ("foreign_provider", "provider"),
        ("foreign_duration", "duration"),
        ("foreign_timing", "timing"),
    ],
)
def test_bridge_direct_construction_rejects_representable_foreign_correspondence(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    bridge = _bridge(state, authorization)
    payload = bridge.model_dump(mode="python")
    inputs = list(payload["accepted_inputs"])
    plan = payload["renderer_plan"]
    if mutation == "missing":
        inputs.pop()
    elif mutation == "extra":
        inputs.append(deepcopy(inputs[-1]))
    elif mutation == "duplicate":
        inputs[-1] = deepcopy(inputs[-2])
    elif mutation == "reordered":
        inputs[0], inputs[1] = inputs[1], inputs[0]
    elif mutation == "foreign_path":
        inputs[0]["artifact_path"] = tmp_path / "foreign.png"
    elif mutation == "foreign_provider":
        inputs[0]["provider"] = "foreign-provider"
    elif mutation == "foreign_duration":
        plan["total_duration"] = 34.0
    elif mutation == "foreign_timing":
        plan["scene_timing_map"][0]["duration"] = 1.0

    with pytest.raises(ValidationError, match=message):
        AcceptedCandidateRendererBridge.model_validate(
            {
                **payload,
                "accepted_inputs": inputs,
                "renderer_plan": plan,
            }
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing_measurement", "processed_narration_measurement"),
        ("malformed_measurement", "artifact_relative_path"),
        ("non_measured", "MEASURED"),
    ],
)
def test_bridge_rejects_invalid_identity_payloads(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    bridge = _bridge(state, authorization)
    payload = bridge.model_dump(mode="python")
    if mutation == "missing_measurement":
        payload.pop("processed_narration_measurement")
    elif mutation == "malformed_measurement":
        payload["processed_narration_measurement"]["artifact_relative_path"] = "../foreign.mp3"
    elif mutation == "non_measured":
        payload["processed_narration_measurement"]["measured_duration_assessment"][
            "value_authority"
        ] = DurationValueAuthority.PLANNED

    with pytest.raises(ValidationError, match=message):
        AcceptedCandidateRendererBridge.model_validate(payload)


def test_renderer_accepted_input_revalidates_and_freezes_reference_hashes(
    tmp_path: Path,
) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    source = _bridge(state, authorization).accepted_inputs[0]
    payload = source.model_dump(mode="python")
    references = list(payload["reference_hashes"])
    constructed = RendererAcceptedCandidateInput.model_validate(
        {**payload, "reference_hashes": references}
    )

    references.append("f" * 64)

    assert constructed.reference_hashes == source.reference_hashes
    assert isinstance(constructed.reference_hashes, tuple)
    with pytest.raises(ValidationError, match="frozen"):
        constructed.reference_hashes = ()
    with pytest.raises(ValidationError, match="extra_forbidden"):
        constructed.model_copy(update={"foreign": True})


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
    unsafe_state = _unsafe_state_with_run_plan(state, unsafe_run)

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
    unsafe_state = _unsafe_state_with_run_plan(state, unsafe_run)

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
            narration_artifact_path=tmp_path / "narration" / "final.mp3",
            artifact_root=tmp_path,
        )

    timings = list(_timeline(state).scene_timings)
    timings[0] = timings[0].model_copy(update={"scene_id": "scene_08"})
    valid_timeline = _timeline(state)
    timeline = AuthoritativeNarrationTimeline.model_construct(
        **{
            **valid_timeline.model_dump(mode="python"),
            "scene_timings": tuple(timings),
        }
    )
    with pytest.raises(ValueError, match="timeline scene set/order"):
        build_renderer_plan_from_accepted_candidates(
            state,
            authorization=authorization,
            profile=_profile(),
            narration_timeline=timeline,
            narration_artifact_path=tmp_path / "narration" / "final.mp3",
            artifact_root=tmp_path,
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
    unaccepted = bind_processed_narration_measurement(
        unaccepted,
        state.processed_narration_measurement,
    )
    with pytest.raises(ValueError, match="not renderer-ready"):
        _bridge(unaccepted, authorization, artifact_root=tmp_path)

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
            "provider_request_hash": None,
            "consumes_ai_call": False,
        },
        accepted_updates={
            "provider": ProviderKind.LOCAL_COMPOSITOR.value,
            "model": "semantic_asset_compositor_v2",
            "provider_request_hash": None,
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


def test_bridge_requires_bound_processed_narration_measurement(tmp_path: Path) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    unbound = clear_processed_narration_measurement(state)

    with pytest.raises(ValueError, match="processed narration measurement is required"):
        _bridge(unbound, authorization, artifact_root=tmp_path)


def test_bridge_rejects_narration_artifact_path_or_current_hash_mismatch(
    tmp_path: Path,
) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    expected = tmp_path / "narration" / "final.mp3"
    other = tmp_path / "narration" / "other.mp3"
    other.write_bytes(expected.read_bytes())

    with pytest.raises(ValueError, match="artifact path"):
        build_renderer_plan_from_accepted_candidates(
            state,
            authorization=authorization,
            profile=_profile(),
            narration_timeline=_timeline(state),
            narration_artifact_path=other,
            artifact_root=tmp_path,
        )

    expected.write_bytes(b"changed processed narration")
    with pytest.raises(ValueError, match="SHA-256"):
        _bridge(state, authorization, artifact_root=tmp_path)


def test_bridge_rejects_stale_measurement_story_identity(tmp_path: Path) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    measurement = state.processed_narration_measurement
    assert measurement is not None
    wrong_measurement = measurement.model_copy(update={"story_plan_sha256": "0" * 64})
    fields = {
        field_name: getattr(state, field_name) for field_name in ExecutionRunState.model_fields
    }
    fields["processed_narration_measurement"] = wrong_measurement
    unsafe_state = ExecutionRunState.model_construct(**fields)

    with pytest.raises(ValidationError, match="StoryPlan SHA-256 does not match"):
        _bridge(unsafe_state, authorization, artifact_root=tmp_path)


def test_bridge_rejects_free_duration_before_image_or_media_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    valid_timeline = _timeline(state)
    timeline = AuthoritativeNarrationTimeline.model_construct(
        **{
            **valid_timeline.model_dump(mode="python"),
            "scene_timings": valid_timeline.scene_timings,
            "render_timing_contract": valid_timeline.render_timing_contract,
            "processed_duration_seconds": 35.5,
        }
    )
    monkeypatch.setattr(
        Image,
        "open",
        lambda *_args, **_kwargs: pytest.fail("image work started before narration validation"),
    )

    with pytest.raises(ValueError, match="duration does not match bound measurement"):
        build_renderer_plan_from_accepted_candidates(
            state,
            authorization=authorization,
            profile=_profile(),
            narration_timeline=timeline,
            narration_artifact_path=tmp_path / "narration" / "final.mp3",
            artifact_root=tmp_path,
        )


def test_renderer_bridge_preserves_postmux_sync_tolerance(tmp_path: Path) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)

    bridge = _bridge(state, authorization)

    assert (
        bridge.renderer_plan.render_timing_contract["timing_tolerance_seconds"]
        == DEFAULT_TIMING_TOLERANCE_SECONDS
    )


@pytest.mark.parametrize(
    ("transition_profile_id", "configured_duration"),
    [
        ("subtle_crossfade", 0.8),
        ("clean_soft_cut", 0.0),
        ("clean_progressive_cut", 0.0),
    ],
)
def test_bridge_accepts_matching_transition_profile_authority(
    tmp_path: Path,
    transition_profile_id: str,
    configured_duration: float,
) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    profile = _profile().model_copy(update={"transition_profile_id": transition_profile_id})
    timeline = build_authoritative_narration_timeline(state, profile=profile)

    bridge = build_renderer_plan_from_accepted_candidates(
        state,
        authorization=authorization,
        profile=profile,
        narration_timeline=timeline,
        narration_artifact_path=tmp_path / "narration" / "final.mp3",
        artifact_root=tmp_path,
    )

    assert bridge.renderer_plan.transition_profile_id == transition_profile_id
    assert (
        bridge.renderer_plan.render_timing_contract["configured_transition_duration_seconds"]
        == configured_duration
    )


@pytest.mark.parametrize(
    ("profile_transition", "timeline_transition"),
    [
        ("subtle_crossfade", "clean_soft_cut"),
        ("clean_soft_cut", "subtle_crossfade"),
    ],
)
def test_bridge_rejects_mismatched_transition_authority_before_candidate_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    profile_transition: str,
    timeline_transition: str,
) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    profile = _profile().model_copy(update={"transition_profile_id": profile_transition})
    timeline_profile = _profile().model_copy(update={"transition_profile_id": timeline_transition})
    timeline = build_authoritative_narration_timeline(
        state,
        profile=timeline_profile,
    )
    before = tuple(
        value.model_dump(mode="python") for value in (state, authorization, profile, timeline)
    )

    def forbidden(*_args, **_kwargs):
        pytest.fail("candidate effect started before transition authority validation")

    monkeypatch.setattr(renderer_bridge, "_artifact_path", forbidden)
    monkeypatch.setattr(renderer_bridge, "_sha256_file", forbidden)
    monkeypatch.setattr(renderer_bridge, "_validate_image", forbidden)

    with pytest.raises(ValueError, match="profile transition does not match"):
        build_renderer_plan_from_accepted_candidates(
            state,
            authorization=authorization,
            profile=profile,
            narration_timeline=timeline,
            narration_artifact_path=tmp_path / "narration" / "final.mp3",
            artifact_root=tmp_path,
        )

    assert (
        tuple(
            value.model_dump(mode="python") for value in (state, authorization, profile, timeline)
        )
        == before
    )


def test_bridge_rejects_unknown_transition_profile_before_candidate_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, authorization = _accepted_state_with_valid_images(tmp_path)
    profile = _profile().model_copy(update={"transition_profile_id": "unknown_transition"})
    timeline = build_authoritative_narration_timeline(
        state,
        profile=_profile(),
    )
    before = tuple(
        value.model_dump(mode="python") for value in (state, authorization, profile, timeline)
    )

    def forbidden(*_args, **_kwargs):
        pytest.fail("candidate effect started before transition profile validation")

    monkeypatch.setattr(renderer_bridge, "_artifact_path", forbidden)
    monkeypatch.setattr(renderer_bridge, "_sha256_file", forbidden)
    monkeypatch.setattr(renderer_bridge, "_validate_image", forbidden)

    with pytest.raises(ValueError, match="unsupported renderer transition profile"):
        build_renderer_plan_from_accepted_candidates(
            state,
            authorization=authorization,
            profile=profile,
            narration_timeline=timeline,
            narration_artifact_path=tmp_path / "narration" / "final.mp3",
            artifact_root=tmp_path,
        )

    assert (
        tuple(
            value.model_dump(mode="python") for value in (state, authorization, profile, timeline)
        )
        == before
    )
