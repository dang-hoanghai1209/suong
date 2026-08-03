from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tella.topic_production import (
    ProcessedNarrationArtifactBindingError,
    ProcessedNarrationArtifactBindingFailure,
    build_fixture_preview_run,
    clear_processed_narration_measurement,
    initialize_execution_state,
    measure_and_bind_processed_narration_artifact,
)
from tella.topic_production.duration_policy import (
    DurationAssessmentStatus,
    DurationValueAuthority,
)
from tella.topic_production.story_plan_identity import canonical_story_plan_sha256
import tella.topic_production.narration_measurement as narration_measurement


def _state(topic: str = "artifact-bound narration"):
    return initialize_execution_state(
        build_fixture_preview_run(
            topic=topic,
            job_id=topic.replace(" ", "-"),
        )
    )


def _artifact(root: Path, relative: str, content: bytes = b"processed narration") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _bind(
    state,
    artifact: Path,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    duration: float = 35.0,
):
    monkeypatch.setattr(
        narration_measurement,
        "probe_single_audio_stream_duration",
        lambda *_args, **_kwargs: duration,
    )
    return measure_and_bind_processed_narration_artifact(
        state,
        artifact_path=artifact,
        artifact_root=root,
    )


@pytest.mark.parametrize(
    ("duration", "status"),
    [
        (35.0, DurationAssessmentStatus.IN_TARGET),
        (31.5, DurationAssessmentStatus.OUTSIDE_TARGET_WARNING),
        (38.5, DurationAssessmentStatus.OUTSIDE_TARGET_WARNING),
    ],
)
def test_measurement_binds_canonical_nested_identity_hash_and_soft_assessment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    duration: float,
    status: DurationAssessmentStatus,
) -> None:
    state = _state()
    artifact = _artifact(tmp_path, "assets/narration/final.mp3")

    bound = _bind(state, artifact, tmp_path, monkeypatch, duration=duration)
    measurement = bound.processed_narration_measurement

    assert measurement is not None
    assert measurement.artifact_relative_path == "assets/narration/final.mp3"
    assert measurement.artifact_sha256 == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert measurement.story_plan_sha256 == canonical_story_plan_sha256(state.run_plan.story_plan)
    assert measurement.measurement_method == "ffprobe_single_audio_stream_v1"
    assert measurement.measured_duration_assessment.status is status
    assert (
        measurement.measured_duration_assessment.value_authority is DurationValueAuthority.MEASURED
    )
    assert state.processed_narration_measurement is None


def test_absolute_artifact_inside_root_is_stored_relatively(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _artifact(tmp_path, "nested/final.mp3")

    bound = _bind(_state(), artifact.resolve(), tmp_path, monkeypatch)

    assert bound.processed_narration_measurement is not None
    assert bound.processed_narration_measurement.artifact_relative_path == "nested/final.mp3"


def test_binding_rejects_root_escape_outside_file_and_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "job"
    root.mkdir()
    outside = _artifact(tmp_path, "outside.mp3")
    state = _state()
    before = state.model_dump(mode="python")

    for artifact in (outside, Path("../outside.mp3"), root):
        with pytest.raises(ProcessedNarrationArtifactBindingError):
            _bind(state, artifact, root, monkeypatch)
        assert state.model_dump(mode="python") == before


def test_binding_rejects_windows_drive_relative_path_before_hash_or_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    before = state.model_dump(mode="python")
    _artifact(tmp_path, "relative.mp3")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("artifact processing started before drive-relative rejection")

    monkeypatch.setattr(narration_measurement, "_stream_sha256", forbidden)
    monkeypatch.setattr(
        narration_measurement,
        "probe_single_audio_stream_duration",
        forbidden,
    )

    with pytest.raises(ProcessedNarrationArtifactBindingError) as failure:
        measure_and_bind_processed_narration_artifact(
            state,
            artifact_path=Path("C:relative.mp3"),
            artifact_root=tmp_path,
        )

    assert failure.value.category is ProcessedNarrationArtifactBindingFailure.ARTIFACT_OUTSIDE_ROOT
    assert state.model_dump(mode="python") == before
    assert state.processed_narration_measurement is None


def test_binding_rejects_symlink_escape_when_supported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "job"
    root.mkdir()
    outside = _artifact(tmp_path, "outside.mp3")
    link = root / "linked.mp3"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(ProcessedNarrationArtifactBindingError) as failure:
        _bind(_state(), link, root, monkeypatch)
    assert failure.value.category is ProcessedNarrationArtifactBindingFailure.ARTIFACT_OUTSIDE_ROOT


def test_binding_requires_stable_pre_and_post_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _artifact(tmp_path, "final.mp3")
    state = _state()
    before = state.model_dump(mode="python")
    hashes = iter(("a" * 64, "b" * 64))
    monkeypatch.setattr(narration_measurement, "_stream_sha256", lambda _path: next(hashes))
    monkeypatch.setattr(
        narration_measurement,
        "probe_single_audio_stream_duration",
        lambda *_args, **_kwargs: 35.0,
    )

    with pytest.raises(ProcessedNarrationArtifactBindingError) as failure:
        measure_and_bind_processed_narration_artifact(
            state,
            artifact_path=artifact,
            artifact_root=tmp_path,
        )
    assert failure.value.category is ProcessedNarrationArtifactBindingFailure.ARTIFACT_CHANGED
    assert state.model_dump(mode="python") == before


def test_binding_wraps_hash_read_failure_without_state_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _artifact(tmp_path, "final.mp3")
    state = _state()
    before = state.model_dump(mode="python")
    original_open = Path.open

    def failed_open(path: Path, *args, **kwargs):
        if path == artifact:
            raise OSError("bounded test read failure")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failed_open)

    with pytest.raises(ProcessedNarrationArtifactBindingError) as failure:
        measure_and_bind_processed_narration_artifact(
            state,
            artifact_path=artifact,
            artifact_root=tmp_path,
        )
    assert failure.value.category is ProcessedNarrationArtifactBindingFailure.ARTIFACT_READ_FAILED
    assert state.model_dump(mode="python") == before


def test_binding_is_idempotent_conflict_requires_clear_and_replacement_then_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _artifact(tmp_path, "first.mp3", b"first")
    second = _artifact(tmp_path, "second.mp3", b"second")
    state = _state()
    bound = _bind(state, first, tmp_path, monkeypatch)

    assert _bind(bound, first, tmp_path, monkeypatch) == bound
    with pytest.raises(ValueError, match="clear it before replacement"):
        _bind(bound, second, tmp_path, monkeypatch)

    replaced = _bind(
        clear_processed_narration_measurement(bound),
        second,
        tmp_path,
        monkeypatch,
    )
    assert replaced.processed_narration_measurement is not None
    assert replaced.processed_narration_measurement.artifact_relative_path == "second.mp3"


def test_binding_has_no_persistence_or_renderer_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _artifact(tmp_path, "final.mp3")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("unrelated lifecycle function called")

    import tella.topic_production.persistence as persistence
    import tella.topic_production.renderer_bridge as renderer_bridge

    monkeypatch.setattr(persistence, "persist_execution_snapshot", forbidden)
    monkeypatch.setattr(renderer_bridge, "build_renderer_plan_from_accepted_candidates", forbidden)

    assert _bind(_state(), artifact, tmp_path, monkeypatch).processed_narration_measurement
