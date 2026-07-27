from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tella.tts.audio_probe import (
    SingleAudioStreamProbeError,
    SingleAudioStreamProbeFailure,
)
from tella.topic_production import (
    AuthorizedRenderLifecycleError,
    AuthorizedRenderLifecycleStage,
    ProcessedNarrationArtifactBindingError,
    ProcessedNarrationArtifactBindingFailure,
    clear_processed_narration_measurement,
    load_runtime_state,
)
from tella.topic_production.duration_policy import DurationAssessmentStatus
import tella.topic_production.narration_measurement as narration_measurement
import tella.topic_production.render_lifecycle as render_lifecycle
from tests.test_topic_production_render_lifecycle_contract import _request


def _unbound_request(tmp_path: Path):
    request = _request(tmp_path)
    return request.model_copy(
        update={"state": clear_processed_narration_measurement(request.state)}
    )


def _write_narration(request, content: bytes = b"final processed narration") -> Path:
    path = request.processed_narration_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_absent_measurement_binds_then_persists_authoritative_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _unbound_request(tmp_path)
    narration = _write_narration(request)
    source_state = request.state.model_dump(mode="python")
    probe_calls: list[Path] = []
    order: list[str] = []
    real_persist = render_lifecycle._persist_execution_snapshot

    def probe(path: Path, *, ffprobe_binary: str = "ffprobe") -> float:
        assert ffprobe_binary == "ffprobe"
        probe_calls.append(path)
        order.append("bind")
        return 35.0

    def persist(state, paths, **kwargs) -> None:
        assert state.processed_narration_measurement is not None
        assert kwargs == {
            "execution_purpose": request.execution_purpose,
            "selected_scene_id": request.selected_scene_id,
        }
        assert paths == request.paths
        order.append("persist")
        real_persist(state, paths, **kwargs)

    monkeypatch.setattr(
        narration_measurement,
        "probe_single_audio_stream_duration",
        probe,
    )
    monkeypatch.setattr(render_lifecycle, "_persist_execution_snapshot", persist)

    bound = render_lifecycle.bind_and_persist_processed_narration_authority(request)
    measurement = bound.processed_narration_measurement
    assert measurement is not None
    assert order == ["bind", "persist"]
    assert probe_calls == [narration.resolve()]
    assert measurement.artifact_relative_path == "narration/final.mp3"
    assert measurement.artifact_sha256 == hashlib.sha256(narration.read_bytes()).hexdigest()
    assert load_runtime_state(request.paths.runtime_state_path) == bound
    manifest = json.loads(request.paths.manifest_path.read_text(encoding="utf-8"))
    measured = manifest["duration_policy"]["measured_duration_assessment"]
    assert manifest["duration_policy"]["schema_version"] == 2
    assert measured["status"] == DurationAssessmentStatus.IN_TARGET
    assert measured["actual_duration_seconds"] == 35.0
    assert request.state.model_dump(mode="python") == source_state
    assert request.state.processed_narration_measurement is None


def test_outside_target_measurement_persists_as_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _unbound_request(tmp_path)
    _write_narration(request)
    monkeypatch.setattr(
        narration_measurement,
        "probe_single_audio_stream_duration",
        lambda *_args, **_kwargs: 45.0,
    )

    bound = render_lifecycle.bind_and_persist_processed_narration_authority(request)
    measurement = bound.processed_narration_measurement
    assert measurement is not None
    assert (
        measurement.measured_duration_assessment.status
        is DurationAssessmentStatus.OUTSIDE_TARGET_WARNING
    )
    manifest = json.loads(request.paths.manifest_path.read_text(encoding="utf-8"))
    report = manifest["duration_policy"]
    assert (
        report["measured_duration_assessment"]["status"]
        == DurationAssessmentStatus.OUTSIDE_TARGET_WARNING
    )
    assert report["warning_count"] >= 1


def test_unchanged_bound_artifact_revalidates_without_second_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _unbound_request(tmp_path)
    narration = _write_narration(request)
    probe_calls: list[Path] = []
    persistence_calls: list[object] = []
    real_persist = render_lifecycle._persist_execution_snapshot

    def probe(path: Path, **_kwargs) -> float:
        probe_calls.append(path)
        return 35.0

    def persist(state, paths, **kwargs) -> None:
        persistence_calls.append(state)
        real_persist(state, paths, **kwargs)

    monkeypatch.setattr(
        narration_measurement,
        "probe_single_audio_stream_duration",
        probe,
    )
    monkeypatch.setattr(render_lifecycle, "_persist_execution_snapshot", persist)

    first = render_lifecycle.bind_and_persist_processed_narration_authority(request)
    second_request = request.model_copy(update={"state": first})
    second = render_lifecycle.bind_and_persist_processed_narration_authority(second_request)

    assert second == first
    assert probe_calls == [narration.resolve()]
    assert len(persistence_calls) == 2


@pytest.mark.parametrize("failure", ["missing", "changed", "different", "directory"])
def test_bound_artifact_identity_failures_prevent_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    request = _unbound_request(tmp_path)
    narration = _write_narration(request, b"version A")
    monkeypatch.setattr(
        narration_measurement,
        "probe_single_audio_stream_duration",
        lambda *_args, **_kwargs: 35.0,
    )
    bound = render_lifecycle.bind_and_persist_processed_narration_authority(request)
    bound_request = request.model_copy(update={"state": bound})

    if failure == "missing":
        narration.unlink()
    elif failure == "changed":
        narration.write_bytes(b"version B")
    elif failure == "different":
        alternate = request.paths.job_dir / "narration" / "alternate.mp3"
        alternate.write_bytes(b"version A")
        bound_request = bound_request.model_copy(update={"processed_narration_path": alternate})
    else:
        narration.unlink()
        narration.mkdir()

    persistence_calls = 0

    def persist(*_args, **_kwargs) -> None:
        nonlocal persistence_calls
        persistence_calls += 1

    monkeypatch.setattr(render_lifecycle, "_persist_execution_snapshot", persist)

    expected_category = {
        "missing": ProcessedNarrationArtifactBindingFailure.ARTIFACT_MISSING,
        "changed": ProcessedNarrationArtifactBindingFailure.BOUND_IDENTITY_MISMATCH,
        "different": ProcessedNarrationArtifactBindingFailure.BOUND_IDENTITY_MISMATCH,
        "directory": (ProcessedNarrationArtifactBindingFailure.ARTIFACT_NOT_REGULAR_FILE),
    }[failure]
    with pytest.raises(ProcessedNarrationArtifactBindingError) as captured:
        render_lifecycle.bind_and_persist_processed_narration_authority(bound_request)
    assert captured.value.category is expected_category
    assert persistence_calls == 0


def test_bound_outside_root_and_symlink_escape_fail_before_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _unbound_request(tmp_path)
    _write_narration(request)
    monkeypatch.setattr(
        narration_measurement,
        "probe_single_audio_stream_duration",
        lambda *_args, **_kwargs: 35.0,
    )
    bound = render_lifecycle.bind_and_persist_processed_narration_authority(request)
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"outside")
    persistence_calls = 0

    def persist(*_args, **_kwargs) -> None:
        nonlocal persistence_calls
        persistence_calls += 1

    monkeypatch.setattr(render_lifecycle, "_persist_execution_snapshot", persist)
    outside_request = request.model_copy(
        update={"state": bound, "processed_narration_path": outside}
    )
    with pytest.raises(ProcessedNarrationArtifactBindingError):
        render_lifecycle.bind_and_persist_processed_narration_authority(outside_request)

    link = request.paths.job_dir / "narration" / "escape.mp3"
    try:
        link.symlink_to(outside)
    except OSError:
        assert persistence_calls == 0
        return
    link_request = outside_request.model_copy(update={"processed_narration_path": link})
    with pytest.raises(ProcessedNarrationArtifactBindingError):
        render_lifecycle.bind_and_persist_processed_narration_authority(link_request)
    assert persistence_calls == 0


def test_explicit_clear_allows_one_new_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _unbound_request(tmp_path)
    _write_narration(request)
    probe_calls = 0

    def probe(*_args, **_kwargs) -> float:
        nonlocal probe_calls
        probe_calls += 1
        return 35.0

    monkeypatch.setattr(
        narration_measurement,
        "probe_single_audio_stream_duration",
        probe,
    )
    bound = render_lifecycle.bind_and_persist_processed_narration_authority(request)
    cleared = clear_processed_narration_measurement(bound)
    rebound = render_lifecycle.bind_and_persist_processed_narration_authority(
        request.model_copy(update={"state": cleared})
    )

    assert probe_calls == 2
    assert rebound.processed_narration_measurement == bound.processed_narration_measurement
    assert cleared.processed_narration_measurement is None


def test_typed_binding_error_passes_through_without_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _unbound_request(tmp_path)
    typed_error = ProcessedNarrationArtifactBindingError(
        request.processed_narration_path,
        ProcessedNarrationArtifactBindingFailure.ARTIFACT_MISSING,
        "missing",
    )
    persistence_calls = 0

    def fail_bind(*_args, **_kwargs):
        raise typed_error

    def persist(*_args, **_kwargs) -> None:
        nonlocal persistence_calls
        persistence_calls += 1

    monkeypatch.setattr(
        render_lifecycle,
        "_measure_and_bind_processed_narration_artifact",
        fail_bind,
    )
    monkeypatch.setattr(render_lifecycle, "_persist_execution_snapshot", persist)

    with pytest.raises(ProcessedNarrationArtifactBindingError) as captured:
        render_lifecycle.bind_and_persist_processed_narration_authority(request)
    assert captured.value is typed_error
    assert persistence_calls == 0


def test_strict_probe_failure_becomes_typed_binding_error_without_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _unbound_request(tmp_path)
    narration = _write_narration(request)
    probe_error = SingleAudioStreamProbeError(
        narration,
        SingleAudioStreamProbeFailure.INVALID_STREAM_COUNT,
        "expected exactly one audio stream",
    )
    persistence_calls = 0

    def fail_probe(*_args, **_kwargs):
        raise probe_error

    def persist(*_args, **_kwargs) -> None:
        nonlocal persistence_calls
        persistence_calls += 1

    monkeypatch.setattr(
        narration_measurement,
        "probe_single_audio_stream_duration",
        fail_probe,
    )
    monkeypatch.setattr(render_lifecycle, "_persist_execution_snapshot", persist)

    with pytest.raises(ProcessedNarrationArtifactBindingError) as captured:
        render_lifecycle.bind_and_persist_processed_narration_authority(request)
    assert captured.value.category is ProcessedNarrationArtifactBindingFailure.STRICT_PROBE_FAILED
    assert captured.value.__cause__ is probe_error
    assert persistence_calls == 0


def test_untyped_binding_error_receives_bind_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _unbound_request(tmp_path)
    cause = RuntimeError("unexpected bind failure")
    persistence_calls = 0

    def fail_bind(*_args, **_kwargs):
        raise cause

    def persist(*_args, **_kwargs) -> None:
        nonlocal persistence_calls
        persistence_calls += 1

    monkeypatch.setattr(
        render_lifecycle,
        "_measure_and_bind_processed_narration_artifact",
        fail_bind,
    )
    monkeypatch.setattr(render_lifecycle, "_persist_execution_snapshot", persist)

    with pytest.raises(AuthorizedRenderLifecycleError) as captured:
        render_lifecycle.bind_and_persist_processed_narration_authority(request)
    assert captured.value.stage is AuthorizedRenderLifecycleStage.BIND
    assert captured.value.original_exception is cause
    assert captured.value.__cause__ is cause
    assert persistence_calls == 0


def test_persistence_failure_preserves_bound_argument_and_unbound_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _unbound_request(tmp_path)
    _write_narration(request)
    source_state = request.state.model_dump(mode="python")
    cause = OSError("snapshot write failed")
    persisted_states = []
    downstream_calls = 0

    monkeypatch.setattr(
        narration_measurement,
        "probe_single_audio_stream_duration",
        lambda *_args, **_kwargs: 35.0,
    )

    def fail_persist(state, *_args, **_kwargs) -> None:
        persisted_states.append(state)
        assert state.processed_narration_measurement is not None
        raise cause

    def downstream(*_args, **_kwargs) -> None:
        nonlocal downstream_calls
        downstream_calls += 1

    monkeypatch.setattr(render_lifecycle, "_persist_execution_snapshot", fail_persist)
    monkeypatch.setattr(
        render_lifecycle,
        "AuthorizedRenderLifecycleOutcome",
        downstream,
    )

    with pytest.raises(AuthorizedRenderLifecycleError) as captured:
        render_lifecycle.bind_and_persist_processed_narration_authority(request)
    assert captured.value.stage is AuthorizedRenderLifecycleStage.PERSIST
    assert captured.value.original_exception is cause
    assert captured.value.__cause__ is cause
    assert len(persisted_states) == 1
    assert request.state.model_dump(mode="python") == source_state
    assert request.state.processed_narration_measurement is None
    assert downstream_calls == 0


def test_bind_persist_stage_cannot_reach_timeline_renderer_or_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tella.composer.timing as composer_timing
    import tella.media.ai_image as ai_image
    import tella.render.pipeline as render_pipeline
    import tella.topic_production.renderer_bridge as renderer_bridge
    import tella.tts.duration_fit as duration_fit
    import tella.tts.synth_all as synth_all

    request = _unbound_request(tmp_path)
    _write_narration(request)
    persistence_calls = 0

    def fail(*_args, **_kwargs):
        raise AssertionError("BIND/PERSIST reached a forbidden downstream stage")

    def persist(*_args, **_kwargs) -> None:
        nonlocal persistence_calls
        persistence_calls += 1

    monkeypatch.setattr(
        narration_measurement,
        "probe_single_audio_stream_duration",
        lambda *_args, **_kwargs: 35.0,
    )
    monkeypatch.setattr(render_lifecycle, "_persist_execution_snapshot", persist)
    monkeypatch.setattr(composer_timing, "build_render_timing_plan", fail)
    monkeypatch.setattr(
        renderer_bridge,
        "build_renderer_plan_from_accepted_candidates",
        fail,
    )
    monkeypatch.setattr(render_pipeline, "render", fail)
    monkeypatch.setattr(ai_image, "generate_image", fail)
    monkeypatch.setattr(synth_all, "synthesize_all", fail)
    monkeypatch.setattr(
        duration_fit,
        "reconcile_practical_narration_duration",
        fail,
    )

    bound = render_lifecycle.bind_and_persist_processed_narration_authority(request)

    assert bound.processed_narration_measurement is not None
    assert persistence_calls == 1
