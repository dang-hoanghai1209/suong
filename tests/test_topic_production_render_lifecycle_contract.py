from __future__ import annotations

from copy import deepcopy
import inspect
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import tella.topic_production as topic_production
from tella.topic_production import (
    AcceptedCandidateRendererBridge,
    AuthorizedCandidateRequest,
    AuthorizedRenderLifecycleCoordinator,
    AuthorizedRenderLifecycleError,
    AuthorizedRenderLifecycleOutcome,
    AuthorizedRenderLifecycleRequest,
    AuthorizedRenderLifecycleStage,
    AuthorizedRenderer,
    ExecutionRunState,
    GenerationTier,
    ProcessedNarrationDurationMeasurement,
    ProcessedNarrationArtifactBindingError,
    ProcessedNarrationArtifactBindingFailure,
    ProductionJobPaths,
    RendererBridgeAuthorization,
    RendererSceneTimingInput,
    build_fixture_preview_run,
    initialize_execution_state,
)
from tella.topic_production.duration_policy import (
    DurationValueAuthority,
    assess_mvp_duration_target,
)
from tella.topic_production.renderer_bridge import AuthoritativeNarrationTimeline
from tella.topic_production.story_plan_identity import canonical_story_plan_sha256
from tella.visual_generation.providers.kinds import ProviderKind
from tests.test_topic_production_renderer_bridge import _profile


_PUBLIC_CONTRACT = frozenset(
    {
        "AuthorizedRenderer",
        "AuthorizedRenderLifecycleCoordinator",
        "AuthorizedRenderLifecycleError",
        "AuthorizedRenderLifecycleOutcome",
        "AuthorizedRenderLifecycleRequest",
        "AuthorizedRenderLifecycleStage",
    }
)


def _request(tmp_path: Path) -> AuthorizedRenderLifecycleRequest:
    run = build_fixture_preview_run(
        topic="authorized render lifecycle contract",
        job_id="render-lifecycle-contract",
    )
    initial_state = initialize_execution_state(run)
    measurement = ProcessedNarrationDurationMeasurement(
        schema_version=1,
        artifact_relative_path="narration/final.mp3",
        artifact_sha256="0" * 64,
        story_plan_sha256=canonical_story_plan_sha256(run.story_plan),
        measurement_method="ffprobe_single_audio_stream_v1",
        measured_duration_assessment=assess_mvp_duration_target(
            35.0,
            value_authority=DurationValueAuthority.MEASURED,
        ),
    )
    state = ExecutionRunState.model_validate(
        {
            **initial_state.model_dump(mode="python"),
            "processed_narration_measurement": measurement.model_dump(mode="python"),
        }
    )
    authorization = RendererBridgeAuthorization(
        job_id=run.job_id,
        planning_hash=run.planning_hash,
        story_plan_sha256=measurement.story_plan_sha256,
        scene_requests=[
            AuthorizedCandidateRequest(
                scene_id=item.scene_id,
                order=item.order,
                source_tier=GenerationTier.DRAFT,
                provider_kind=ProviderKind.CLOUDFLARE_KLEIN_4B,
                provider=item.draft.provider,
                model=item.draft.model,
                seed=item.draft.seed,
                width=item.draft.width,
                height=item.draft.height,
                planning_request_hash=item.draft.logical_visual_request_hash,
                logical_request_hash="1" * 64,
                provider_request_hash="2" * 64,
            )
            for item in run.scene_execution_plans
        ],
        forbid_local_compositor=True,
        supported_image_formats=("PNG", "JPEG"),
        require_portrait_geometry=True,
    )
    job_dir = tmp_path / "topic_production" / state.run_plan.job_id
    draft_dir = job_dir / "scenes" / "scene_01" / "draft"
    paths = ProductionJobPaths(
        job_dir=job_dir,
        run_plan_path=job_dir / "run_plan.json",
        runtime_state_path=job_dir / "runtime_state.json",
        manifest_path=job_dir / "manifest.json",
        candidate_base_path=draft_dir / "candidate_01.bin",
        candidate_metadata_path=draft_dir / "metadata.json",
    )
    return AuthorizedRenderLifecycleRequest(
        state=state,
        paths=paths,
        processed_narration_path=paths.job_dir / "narration" / "final.mp3",
        authorization=authorization,
        profile=_profile(),
        execution_purpose="authorized_render_lifecycle",
        selected_scene_id="scene_01",
    )


def _timeline(request: AuthorizedRenderLifecycleRequest) -> AuthoritativeNarrationTimeline:
    measurement = request.state.processed_narration_measurement
    assert measurement is not None
    duration = measurement.measured_duration_assessment.actual_duration_seconds
    plans = request.state.run_plan.scene_execution_plans
    return AuthoritativeNarrationTimeline(
        story_plan_sha256=measurement.story_plan_sha256,
        narration_text=request.state.run_plan.story_plan.narration_text,
        processed_duration_seconds=duration,
        scene_timings=[
            RendererSceneTimingInput(
                scene_id=item.scene_id,
                order=item.order,
                start_seconds=item.timing.start_seconds,
                duration_seconds=item.timing.duration_seconds,
                render_clip_duration_seconds=item.timing.duration_seconds,
                end_seconds=item.timing.end_seconds,
            )
            for item in plans
        ],
        render_timing_contract={
            "expected_final_timeline_duration_seconds": duration,
            "timing_tolerance_seconds": 0.15,
        },
    )


def _outcome(tmp_path: Path) -> AuthorizedRenderLifecycleOutcome:
    request = _request(tmp_path)
    return AuthorizedRenderLifecycleOutcome(
        state=request.state,
        narration_timeline=_timeline(request),
        render_output_path=request.paths.job_dir / "video.mp4",
    )


def test_lifecycle_stage_membership_and_declaration_order_are_exact() -> None:
    stages = tuple(AuthorizedRenderLifecycleStage)

    assert stages == (
        AuthorizedRenderLifecycleStage.BIND,
        AuthorizedRenderLifecycleStage.PERSIST,
        AuthorizedRenderLifecycleStage.TIMELINE,
        AuthorizedRenderLifecycleStage.RENDER_PLAN,
        AuthorizedRenderLifecycleStage.RENDER,
    )
    assert len(stages) == len(set(stages))
    assert stages[0] is AuthorizedRenderLifecycleStage.BIND
    assert stages.index(AuthorizedRenderLifecycleStage.PERSIST) < stages.index(
        AuthorizedRenderLifecycleStage.TIMELINE
    )
    assert stages.index(AuthorizedRenderLifecycleStage.RENDER_PLAN) < stages.index(
        AuthorizedRenderLifecycleStage.RENDER
    )
    with pytest.raises(ValueError):
        AuthorizedRenderLifecycleStage("tts")


def test_request_has_exact_required_fields_and_strict_boundary(tmp_path: Path) -> None:
    request = _request(tmp_path)
    fields = set(AuthorizedRenderLifecycleRequest.model_fields)

    assert fields == {
        "state",
        "paths",
        "processed_narration_path",
        "authorization",
        "profile",
        "execution_purpose",
        "selected_scene_id",
    }
    assert not fields.intersection(
        {
            "manifest",
            "measured_duration",
            "narration_timeline",
            "renderer_plan",
            "renderer",
            "artifact_root",
        }
    )
    with pytest.raises(ValidationError, match="processed_narration_path"):
        AuthorizedRenderLifecycleRequest.model_validate(
            {
                key: value
                for key, value in request.model_dump(mode="python").items()
                if key != "processed_narration_path"
            }
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        AuthorizedRenderLifecycleRequest.model_validate(
            {**request.model_dump(mode="python"), "manifest": {}}
        )
    with pytest.raises(ValidationError, match="instance of Path"):
        AuthorizedRenderLifecycleRequest.model_validate(
            {
                **request.model_dump(mode="python"),
                "processed_narration_path": "narration/final.mp3",
            }
        )


def test_request_revalidates_and_detaches_nested_authority(tmp_path: Path) -> None:
    request = _request(tmp_path)
    payload = request.model_dump(mode="python")
    constructed = AuthorizedRenderLifecycleRequest.model_validate(payload)
    original_scene_count = len(constructed.state.scenes)
    original_request_count = len(constructed.authorization.scene_requests)

    payload["state"]["scenes"].clear()
    payload["authorization"]["scene_requests"].clear()
    payload["profile"]["title"] = "Changed"
    payload["paths"]["job_dir"] = tmp_path / "changed"

    assert len(constructed.state.scenes) == original_scene_count
    assert len(constructed.authorization.scene_requests) == original_request_count
    assert constructed.profile.title == request.profile.title
    assert constructed.paths.job_dir == request.paths.job_dir
    with pytest.raises(ValidationError, match="schema_version"):
        AuthorizedRenderLifecycleRequest.model_validate(
            {
                **request.model_dump(mode="python"),
                "state": {
                    **request.state.model_dump(mode="python"),
                    "schema_version": 1,
                },
            }
        )
    with pytest.raises(ValidationError, match="frozen"):
        constructed.execution_purpose = "changed"


def test_request_exposes_only_disposable_nested_authority_copies(tmp_path: Path) -> None:
    source = _request(tmp_path)
    state = source.state
    paths = source.paths
    authorization = source.authorization
    profile = source.profile
    request = AuthorizedRenderLifecycleRequest(
        state=state,
        paths=paths,
        processed_narration_path=source.processed_narration_path,
        authorization=authorization,
        profile=profile,
        execution_purpose=source.execution_purpose,
        selected_scene_id=source.selected_scene_id,
    )
    scene_count = len(request.state.scenes)
    event_count = len(request.state.event_history)
    execution_plan_count = len(request.state.run_plan.scene_execution_plans)
    authorization_count = len(request.authorization.scene_requests)

    state.scenes.clear()
    state.event_history.clear()
    state.run_plan.scene_execution_plans.clear()
    authorization.scene_requests.clear()
    request.state.scenes.clear()
    request.state.event_history.clear()
    request.state.run_plan.scene_execution_plans.clear()
    request.authorization.scene_requests.clear()
    dict(request)["state"].scenes.clear()
    dict(request)["authorization"].scene_requests.clear()

    assert len(request.state.scenes) == scene_count
    assert len(request.state.event_history) == event_count
    assert len(request.state.run_plan.scene_execution_plans) == execution_plan_count
    assert len(request.authorization.scene_requests) == authorization_count
    assert request.paths == paths
    assert request.profile == profile
    with pytest.raises(ValidationError, match="schema_version"):
        request.model_copy(
            update={
                "state": {
                    **request.state.model_dump(mode="python"),
                    "schema_version": 1,
                }
            }
        )


def test_final_narration_path_and_authority_correspondence_are_explicit(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)

    assert request.processed_narration_path == (request.paths.job_dir / "narration" / "final.mp3")
    with pytest.raises(ValidationError, match="explicit artifact"):
        AuthorizedRenderLifecycleRequest.model_validate(
            {**request.model_dump(mode="python"), "processed_narration_path": Path(".")}
        )
    with pytest.raises(ValidationError, match="job ID"):
        AuthorizedRenderLifecycleRequest.model_validate(
            {
                **request.model_dump(mode="python"),
                "authorization": {
                    **request.authorization.model_dump(mode="python"),
                    "job_id": "different-job",
                },
            }
        )
    with pytest.raises(ValidationError, match="selected_scene_id"):
        AuthorizedRenderLifecycleRequest.model_validate(
            {**request.model_dump(mode="python"), "selected_scene_id": "scene_99"}
        )


def test_renderer_and_coordinator_protocols_are_future_facing() -> None:
    renderer_parameters = inspect.signature(AuthorizedRenderer.__call__).parameters
    coordinator_parameters = inspect.signature(
        AuthorizedRenderLifecycleCoordinator.__call__
    ).parameters

    assert tuple(renderer_parameters) == (
        "self",
        "authorized_plan",
        "job_dir",
        "narration_artifact_path",
    )
    assert tuple(coordinator_parameters) == ("self", "request", "renderer")

    class RendererStub:
        async def __call__(
            self,
            authorized_plan: AcceptedCandidateRendererBridge,
            *,
            job_dir: Path,
            narration_artifact_path: Path,
        ) -> Path:
            del authorized_plan, narration_artifact_path
            return job_dir / "video.mp4"

    assert isinstance(RendererStub(), AuthorizedRenderer)


def test_outcome_is_immutable_and_reuses_existing_authority_types(tmp_path: Path) -> None:
    request = _request(tmp_path)
    state = request.state
    timeline = _timeline(request)
    outcome = AuthorizedRenderLifecycleOutcome(
        state=state,
        narration_timeline=timeline,
        render_output_path=request.paths.job_dir / "video.mp4",
    )

    assert set(AuthorizedRenderLifecycleOutcome.model_fields) == {
        "state",
        "narration_timeline",
        "render_output_path",
    }
    assert outcome.state is not state
    assert isinstance(outcome.narration_timeline, AuthoritativeNarrationTimeline)
    assert outcome.state.processed_narration_measurement is not None
    original_scene_count = len(outcome.state.scenes)
    original_timing_count = len(outcome.narration_timeline.scene_timings)
    original_contract = outcome.narration_timeline.render_timing_contract

    state.scenes.clear()
    timeline.scene_timings.clear()
    timeline.render_timing_contract.clear()
    outcome.state.scenes.clear()
    outcome.narration_timeline.scene_timings.clear()
    outcome.narration_timeline.render_timing_contract.clear()
    dict(outcome)["state"].scenes.clear()
    dict(outcome)["narration_timeline"].scene_timings.clear()

    assert len(outcome.state.scenes) == original_scene_count
    assert len(outcome.narration_timeline.scene_timings) == original_timing_count
    assert outcome.narration_timeline.render_timing_contract == original_contract
    with pytest.raises(ValidationError, match="frozen"):
        outcome.render_output_path = tmp_path / "changed.mp4"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        AuthorizedRenderLifecycleOutcome.model_validate(
            {
                **outcome.model_dump(mode="python"),
                "renderer_bridge": {},
            }
        )
    with pytest.raises(ValidationError, match="narration measurement"):
        AuthorizedRenderLifecycleOutcome.model_validate(
            {
                **outcome.model_dump(mode="python"),
                "state": {
                    **outcome.state.model_dump(mode="python"),
                    "processed_narration_measurement": None,
                },
            }
        )


@pytest.mark.parametrize(
    ("timeline_update", "message"),
    [
        ({"story_plan_sha256": "3" * 64}, "StoryPlan SHA-256"),
        ({"processed_duration_seconds": 34.0}, "duration"),
        ({"narration_text": "foreign narration"}, "narration"),
    ],
)
def test_outcome_rejects_foreign_timeline_authority(
    tmp_path: Path,
    timeline_update: dict[str, object],
    message: str,
) -> None:
    request = _request(tmp_path)
    timeline_payload = _timeline(request).model_dump(mode="python")
    timeline_payload.update(timeline_update)

    with pytest.raises(ValidationError, match=message):
        AuthorizedRenderLifecycleOutcome(
            state=request.state,
            narration_timeline=timeline_payload,
            render_output_path=request.paths.job_dir / "video.mp4",
        )


def test_outcome_rejects_reordered_or_missing_timeline_scenes(tmp_path: Path) -> None:
    request = _request(tmp_path)
    timeline = _timeline(request)
    reversed_payload = timeline.model_dump(mode="python")
    reversed_payload["scene_timings"] = list(reversed(reversed_payload["scene_timings"]))
    missing_payload = timeline.model_dump(mode="python")
    missing_payload["scene_timings"] = missing_payload["scene_timings"][:-1]

    with pytest.raises(ValidationError, match="timeline scenes"):
        AuthorizedRenderLifecycleOutcome(
            state=request.state,
            narration_timeline=reversed_payload,
            render_output_path=request.paths.job_dir / "video.mp4",
        )
    with pytest.raises(ValidationError, match="timeline scenes"):
        AuthorizedRenderLifecycleOutcome(
            state=request.state,
            narration_timeline=missing_payload,
            render_output_path=request.paths.job_dir / "video.mp4",
        )


def test_request_and_outcome_serialization_are_public_detached_and_round_trip(
    tmp_path: Path,
) -> None:
    models = (_request(tmp_path), _outcome(tmp_path))

    for model in models:
        declared_fields = tuple(type(model).model_fields)
        original = model.model_dump(mode="python")
        python_dump = model.model_dump()
        json_dump = model.model_dump(mode="json")
        json_text = model.model_dump_json()
        parsed_json = json.loads(json_text)

        assert tuple(python_dump) == declared_fields
        assert tuple(json_dump) == declared_fields
        assert tuple(parsed_json) == declared_fields
        assert not any(name.startswith("_") for name in declared_fields)
        assert json_dump == parsed_json
        assert type(model).model_validate_json(json_text) == model

        python_dump["state"]["scenes"].clear()
        json_dump["state"]["event_history"].clear()
        parsed_json["state"]["run_plan"]["scene_execution_plans"].clear()
        if isinstance(model, AuthorizedRenderLifecycleRequest):
            python_dump["authorization"]["scene_requests"].clear()
        else:
            python_dump["narration_timeline"]["scene_timings"].clear()
            json_dump["narration_timeline"]["render_timing_contract"].clear()

        assert model.model_dump(mode="python") == original


def test_request_and_outcome_copy_operations_revalidate_and_remain_detached(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    outcome = _outcome(tmp_path)

    for model in (request, outcome):
        original = model.model_dump(mode="python")
        for copied in (
            model.model_copy(),
            model.model_copy(deep=True),
            type(model).model_validate(model),
        ):
            assert copied == model
            assert copied is not model
            assert copied.state is not model.state
            copied.state.scenes.clear()
            assert copied.model_dump(mode="python") == original
            assert model.model_dump(mode="python") == original

    updated_request = request.model_copy(update={"execution_purpose": "updated-purpose"})
    updated_outcome = outcome.model_copy(
        update={"render_output_path": tmp_path / "updated-video.mp4"}
    )
    assert updated_request.execution_purpose == "updated-purpose"
    assert updated_request.state.run_plan.job_id == request.state.run_plan.job_id
    assert request.execution_purpose == "authorized_render_lifecycle"
    assert updated_outcome.render_output_path == tmp_path / "updated-video.mp4"
    assert outcome.render_output_path != updated_outcome.render_output_path

    with pytest.raises(ValidationError, match="instance of Path"):
        request.model_copy(update={"processed_narration_path": "unsafe.mp3"})
    with pytest.raises(ValidationError, match="schema_version"):
        request.model_copy(
            update={
                "state": {
                    **request.state.model_dump(mode="python"),
                    "schema_version": 1,
                }
            }
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        request.model_copy(update={"manifest": {}})
    with pytest.raises(ValidationError, match="schema_version"):
        outcome.model_copy(update={"state": {"schema_version": 1}})
    with pytest.raises(ValidationError, match="extra_forbidden"):
        outcome.model_copy(update={"renderer_bridge": {}})


def test_request_and_outcome_repr_and_equality_ignore_disposable_mutations(
    tmp_path: Path,
) -> None:
    left_request = _request(tmp_path)
    right_request = _request(tmp_path)
    left_outcome = _outcome(tmp_path)
    right_outcome = _outcome(tmp_path)
    request_repr = repr(left_request)
    outcome_repr = repr(left_outcome)

    assert left_request == right_request
    assert left_outcome == right_outcome
    assert all(name in request_repr for name in AuthorizedRenderLifecycleRequest.model_fields)
    assert all(name in outcome_repr for name in AuthorizedRenderLifecycleOutcome.model_fields)
    assert "_detached_authority_fields" not in request_repr
    assert "_detached_authority_fields" not in outcome_repr

    left_request.state.scenes.clear()
    left_request.authorization.scene_requests.clear()
    left_outcome.state.scenes.clear()
    left_outcome.narration_timeline.scene_timings.clear()

    assert repr(left_request) == request_repr
    assert repr(left_outcome) == outcome_repr
    assert left_request == right_request
    assert left_outcome == right_outcome


def test_outcome_rejects_extra_or_duplicate_timeline_scenes_without_aliasing(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    timeline = _timeline(request)
    original_timeline = timeline.model_dump(mode="python")
    original_state = request.state.model_dump(mode="python")
    extra_payload = deepcopy(original_timeline)
    extra_scene = deepcopy(extra_payload["scene_timings"][-1])
    extra_scene.update({"scene_id": "scene_09", "order": 9})
    extra_payload["scene_timings"].append(extra_scene)
    duplicate_payload = deepcopy(original_timeline)
    duplicate_payload["scene_timings"][-1] = deepcopy(duplicate_payload["scene_timings"][-2])

    for payload in (extra_payload, duplicate_payload):
        with pytest.raises(ValidationError, match="timeline scenes"):
            AuthorizedRenderLifecycleOutcome(
                state=request.state,
                narration_timeline=payload,
                render_output_path=request.paths.job_dir / "video.mp4",
            )

    assert timeline.model_dump(mode="python") == original_timeline
    assert request.state.model_dump(mode="python") == original_state


def test_lifecycle_error_adds_only_untyped_stage_context(tmp_path: Path) -> None:
    cause = OSError("snapshot write failed")
    error = AuthorizedRenderLifecycleError(
        AuthorizedRenderLifecycleStage.PERSIST,
        cause,
    )

    assert error.stage is AuthorizedRenderLifecycleStage.PERSIST
    assert error.original_exception is cause
    assert error.__cause__ is cause
    with pytest.raises(TypeError, match="AuthorizedRenderLifecycleStage"):
        AuthorizedRenderLifecycleError("persist", cause)  # type: ignore[arg-type]

    typed = ProcessedNarrationArtifactBindingError(
        tmp_path / "narration.mp3",
        ProcessedNarrationArtifactBindingFailure.ARTIFACT_MISSING,
        "missing",
    )
    with pytest.raises(TypeError, match="pass through unchanged"):
        AuthorizedRenderLifecycleError(AuthorizedRenderLifecycleStage.BIND, typed)


def test_package_exports_are_exact_and_contract_has_no_execution_surface() -> None:
    import tella.topic_production.render_lifecycle as contract

    assert set(contract.__all__) == _PUBLIC_CONTRACT
    assert _PUBLIC_CONTRACT <= set(topic_production.__all__)
    assert all(
        getattr(topic_production, name) is getattr(contract, name) for name in contract.__all__
    )
    assert not hasattr(contract, "execute_authorized_render_lifecycle")
    assert not hasattr(contract, "measure_and_bind_processed_narration_artifact")
    assert not hasattr(contract, "persist_execution_snapshot")
    assert not hasattr(contract, "build_renderer_plan_from_accepted_candidates")
    assert not hasattr(contract, "render")


def test_contract_construction_performs_no_lifecycle_operations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tella.composer.timing as composer_timing
    import tella.render.pipeline as render_pipeline
    import tella.topic_production.narration_measurement as narration_measurement
    import tella.topic_production.persistence as persistence
    import tella.topic_production.renderer_bridge as renderer_bridge
    import tella.topic_production.runtime as runtime
    import tella.tts.audio_probe as audio_probe

    def fail(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("contract construction executed a lifecycle operation")

    monkeypatch.setattr(
        narration_measurement,
        "measure_and_bind_processed_narration_artifact",
        fail,
    )
    monkeypatch.setattr(runtime, "bind_processed_narration_measurement", fail)
    monkeypatch.setattr(persistence, "persist_execution_snapshot", fail)
    monkeypatch.setattr(composer_timing, "build_render_timing_plan", fail)
    monkeypatch.setattr(
        renderer_bridge,
        "build_renderer_plan_from_accepted_candidates",
        fail,
    )
    monkeypatch.setattr(renderer_bridge, "_sha256_file", fail)
    monkeypatch.setattr(narration_measurement, "_stream_sha256", fail)
    monkeypatch.setattr(render_pipeline, "render", fail)
    monkeypatch.setattr(audio_probe, "probe_single_audio_stream_duration", fail)
    monkeypatch.setattr(Path, "exists", fail)
    monkeypatch.setattr(Path, "is_file", fail)
    monkeypatch.setattr(Path, "resolve", fail)
    monkeypatch.setattr(Path, "open", fail)

    request = _request(tmp_path)
    outcome = AuthorizedRenderLifecycleOutcome(
        state=request.state,
        narration_timeline=_timeline(request),
        render_output_path=request.paths.job_dir / "video.mp4",
    )

    assert request.state.processed_narration_measurement is not None
    assert outcome.state.processed_narration_measurement is not None
