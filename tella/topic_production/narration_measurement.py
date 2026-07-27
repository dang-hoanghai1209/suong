"""Artifact-derived processed narration measurement and state binding."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path, PureWindowsPath

from tella.tts.audio_probe import (
    SingleAudioStreamProbeError,
    probe_single_audio_stream_duration,
)

from .duration_policy import DurationValueAuthority, assess_mvp_duration_target
from .runtime import (
    _revalidate_execution_state,
    bind_processed_narration_measurement,
)
from .runtime_models import ExecutionRunState, ProcessedNarrationDurationMeasurement
from .story_plan_identity import canonical_story_plan_sha256


_HASH_CHUNK_SIZE = 1024 * 1024


class ProcessedNarrationArtifactBindingFailure(StrEnum):
    ARTIFACT_ROOT_MISSING = "artifact_root_missing"
    ARTIFACT_ROOT_NOT_DIRECTORY = "artifact_root_not_directory"
    ARTIFACT_MISSING = "artifact_missing"
    ARTIFACT_NOT_REGULAR_FILE = "artifact_not_regular_file"
    ARTIFACT_OUTSIDE_ROOT = "artifact_outside_root"
    ARTIFACT_READ_FAILED = "artifact_read_failed"
    ARTIFACT_CHANGED = "artifact_changed_during_measurement"
    BOUND_IDENTITY_MISMATCH = "bound_identity_mismatch"
    STRICT_PROBE_FAILED = "strict_probe_failed"


class ProcessedNarrationArtifactBindingError(RuntimeError):
    """Typed failure before processed narration authority can be bound."""

    def __init__(
        self,
        artifact_path: Path,
        category: ProcessedNarrationArtifactBindingFailure,
        detail: str,
    ) -> None:
        self.artifact_path = artifact_path
        self.category = category
        self.detail = detail
        super().__init__(f"{category.value}: {artifact_path}: {detail}")


def _resolve_artifact_within_root(
    artifact_path: Path,
    artifact_root: Path,
) -> tuple[Path, Path, str]:
    supplied_path = Path(artifact_path)
    windows_path = PureWindowsPath(str(supplied_path))
    if windows_path.drive and not windows_path.root:
        raise ProcessedNarrationArtifactBindingError(
            supplied_path,
            ProcessedNarrationArtifactBindingFailure.ARTIFACT_OUTSIDE_ROOT,
            "Windows drive-relative artifact paths are not canonical job-relative identities",
        )
    root = Path(artifact_root)
    if not root.exists():
        raise ProcessedNarrationArtifactBindingError(
            supplied_path,
            ProcessedNarrationArtifactBindingFailure.ARTIFACT_ROOT_MISSING,
            "artifact root does not exist",
        )
    if not root.is_dir():
        raise ProcessedNarrationArtifactBindingError(
            supplied_path,
            ProcessedNarrationArtifactBindingFailure.ARTIFACT_ROOT_NOT_DIRECTORY,
            "artifact root is not a directory",
        )
    resolved_root = root.resolve(strict=True)
    candidate = supplied_path if supplied_path.is_absolute() else resolved_root / supplied_path
    if not candidate.exists():
        raise ProcessedNarrationArtifactBindingError(
            supplied_path,
            ProcessedNarrationArtifactBindingFailure.ARTIFACT_MISSING,
            "processed narration artifact does not exist",
        )
    if not candidate.is_file():
        raise ProcessedNarrationArtifactBindingError(
            supplied_path,
            ProcessedNarrationArtifactBindingFailure.ARTIFACT_NOT_REGULAR_FILE,
            "processed narration artifact is not a regular file",
        )
    resolved_path = candidate.resolve(strict=True)
    try:
        relative = resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ProcessedNarrationArtifactBindingError(
            supplied_path,
            ProcessedNarrationArtifactBindingFailure.ARTIFACT_OUTSIDE_ROOT,
            "processed narration artifact resolves outside artifact root",
        ) from exc
    relative_text = relative.as_posix()
    if not relative_text or relative_text == "." or "\\" in relative_text:
        raise ProcessedNarrationArtifactBindingError(
            supplied_path,
            ProcessedNarrationArtifactBindingFailure.ARTIFACT_OUTSIDE_ROOT,
            "processed narration artifact has no canonical job-relative identity",
        )
    return resolved_root, resolved_path, relative_text


def _stream_sha256(artifact_path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with artifact_path.open("rb") as stream:
            for block in iter(lambda: stream.read(_HASH_CHUNK_SIZE), b""):
                digest.update(block)
    except OSError as exc:
        raise ProcessedNarrationArtifactBindingError(
            artifact_path,
            ProcessedNarrationArtifactBindingFailure.ARTIFACT_READ_FAILED,
            str(exc),
        ) from exc
    return digest.hexdigest()


def _inspect_processed_narration_artifact(
    state: ExecutionRunState,
    *,
    artifact_path: Path,
    artifact_root: Path,
) -> tuple[ExecutionRunState, Path, str]:
    """Validate current path containment without probing or reading artifact bytes."""

    validated_state = _revalidate_execution_state(state)
    _, resolved_path, relative_path = _resolve_artifact_within_root(
        artifact_path,
        artifact_root,
    )
    return validated_state, resolved_path, relative_path


def measure_and_bind_processed_narration_artifact(
    state: ExecutionRunState,
    *,
    artifact_path: Path,
    artifact_root: Path,
    ffprobe_binary: str = "ffprobe",
) -> ExecutionRunState:
    """Measure one stable final narration artifact and bind runtime authority."""

    validated_state, resolved_path, relative_path = _inspect_processed_narration_artifact(
        state,
        artifact_path=artifact_path,
        artifact_root=artifact_root,
    )
    before_sha256 = _stream_sha256(resolved_path)
    try:
        measured_seconds = probe_single_audio_stream_duration(
            resolved_path,
            ffprobe_binary=ffprobe_binary,
        )
    except SingleAudioStreamProbeError as exc:
        raise ProcessedNarrationArtifactBindingError(
            resolved_path,
            ProcessedNarrationArtifactBindingFailure.STRICT_PROBE_FAILED,
            str(exc),
        ) from exc
    after_sha256 = _stream_sha256(resolved_path)
    if before_sha256 != after_sha256:
        raise ProcessedNarrationArtifactBindingError(
            resolved_path,
            ProcessedNarrationArtifactBindingFailure.ARTIFACT_CHANGED,
            "processed narration artifact changed between hash checks",
        )
    assessment = assess_mvp_duration_target(
        measured_seconds,
        value_authority=DurationValueAuthority.MEASURED,
    )
    measurement = ProcessedNarrationDurationMeasurement(
        schema_version=1,
        artifact_relative_path=relative_path,
        artifact_sha256=after_sha256,
        story_plan_sha256=canonical_story_plan_sha256(validated_state.run_plan.story_plan),
        measurement_method="ffprobe_single_audio_stream_v1",
        measured_duration_assessment=assessment,
    )
    return bind_processed_narration_measurement(validated_state, measurement)


__all__ = [
    "ProcessedNarrationArtifactBindingError",
    "ProcessedNarrationArtifactBindingFailure",
    "measure_and_bind_processed_narration_artifact",
]
