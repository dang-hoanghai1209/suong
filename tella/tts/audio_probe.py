"""Strict ffprobe boundary for one processed narration audio stream."""

from __future__ import annotations

import json
import math
from enum import StrEnum
from pathlib import Path
import subprocess
from typing import Any


FFPROBE_TIMEOUT_SECONDS = 30.0
_MAX_DIAGNOSTIC_CHARACTERS = 500


class SingleAudioStreamProbeFailure(StrEnum):
    ARTIFACT_MISSING = "artifact_missing"
    ARTIFACT_NOT_REGULAR_FILE = "artifact_not_regular_file"
    EXECUTABLE_UNAVAILABLE = "ffprobe_executable_unavailable"
    TIMEOUT = "ffprobe_timeout"
    NONZERO_EXIT = "ffprobe_nonzero_exit"
    MALFORMED_RESPONSE = "malformed_response"
    INVALID_STREAM_COUNT = "invalid_stream_count"
    INVALID_DURATION = "invalid_duration"


class SingleAudioStreamProbeError(RuntimeError):
    """Bounded diagnostic for strict processed-narration probing."""

    def __init__(
        self,
        artifact_path: Path,
        category: SingleAudioStreamProbeFailure,
        detail: str,
    ) -> None:
        self.artifact_path = artifact_path
        self.category = category
        self.detail = detail[:_MAX_DIAGNOSTIC_CHARACTERS]
        super().__init__(f"{category.value}: {artifact_path}: {self.detail}")


def _decode_utf8(
    value: bytes | str | None,
    *,
    artifact_path: Path,
    stream_name: str,
    failure_category: SingleAudioStreamProbeFailure = (
        SingleAudioStreamProbeFailure.MALFORMED_RESPONSE
    ),
) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SingleAudioStreamProbeError(
            artifact_path,
            failure_category,
            f"{stream_name} is not valid UTF-8",
        ) from exc


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _parse_positive_finite_duration(value: Any, *, artifact_path: Path) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise SingleAudioStreamProbeError(
            artifact_path,
            SingleAudioStreamProbeFailure.INVALID_DURATION,
            "audio stream duration must be numeric text or a JSON number",
        )
    if isinstance(value, str) and not value.strip():
        raise SingleAudioStreamProbeError(
            artifact_path,
            SingleAudioStreamProbeFailure.INVALID_DURATION,
            "audio stream duration is empty",
        )
    try:
        duration = float(value)
    except (TypeError, ValueError) as exc:
        raise SingleAudioStreamProbeError(
            artifact_path,
            SingleAudioStreamProbeFailure.INVALID_DURATION,
            "audio stream duration is not numeric",
        ) from exc
    if not math.isfinite(duration) or duration <= 0:
        raise SingleAudioStreamProbeError(
            artifact_path,
            SingleAudioStreamProbeFailure.INVALID_DURATION,
            "audio stream duration must be positive and finite",
        )
    return duration


def probe_single_audio_stream_duration(
    artifact_path: Path,
    *,
    ffprobe_binary: str = "ffprobe",
) -> float:
    """Return the duration of exactly one audio stream without container fallback."""

    path = Path(artifact_path)
    if not path.exists():
        raise SingleAudioStreamProbeError(
            path,
            SingleAudioStreamProbeFailure.ARTIFACT_MISSING,
            "artifact does not exist",
        )
    if not path.is_file():
        raise SingleAudioStreamProbeError(
            path,
            SingleAudioStreamProbeFailure.ARTIFACT_NOT_REGULAR_FILE,
            "artifact is not a regular file",
        )

    command = [
        ffprobe_binary,
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index,duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        completed = subprocess.run(
            command,
            shell=False,
            capture_output=True,
            check=False,
            timeout=FFPROBE_TIMEOUT_SECONDS,
        )
    except OSError as exc:
        raise SingleAudioStreamProbeError(
            path,
            SingleAudioStreamProbeFailure.EXECUTABLE_UNAVAILABLE,
            f"unable to execute {ffprobe_binary}: {exc}",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        stderr = _decode_utf8(
            exc.stderr,
            artifact_path=path,
            stream_name="ffprobe stderr",
            failure_category=SingleAudioStreamProbeFailure.TIMEOUT,
        )
        raise SingleAudioStreamProbeError(
            path,
            SingleAudioStreamProbeFailure.TIMEOUT,
            stderr or f"ffprobe exceeded {FFPROBE_TIMEOUT_SECONDS:g} seconds",
        ) from exc

    stderr = _decode_utf8(
        completed.stderr,
        artifact_path=path,
        stream_name="ffprobe stderr",
        failure_category=(
            SingleAudioStreamProbeFailure.NONZERO_EXIT
            if completed.returncode != 0
            else SingleAudioStreamProbeFailure.MALFORMED_RESPONSE
        ),
    )
    if completed.returncode != 0:
        raise SingleAudioStreamProbeError(
            path,
            SingleAudioStreamProbeFailure.NONZERO_EXIT,
            stderr or f"ffprobe exited with code {completed.returncode}",
        )
    stdout = _decode_utf8(
        completed.stdout,
        artifact_path=path,
        stream_name="ffprobe stdout",
    )
    try:
        payload = json.loads(stdout, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise SingleAudioStreamProbeError(
            path,
            SingleAudioStreamProbeFailure.MALFORMED_RESPONSE,
            "ffprobe stdout is not strict JSON",
        ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("streams"), list):
        raise SingleAudioStreamProbeError(
            path,
            SingleAudioStreamProbeFailure.MALFORMED_RESPONSE,
            "ffprobe response must contain a streams array",
        )
    streams = payload["streams"]
    if len(streams) != 1:
        raise SingleAudioStreamProbeError(
            path,
            SingleAudioStreamProbeFailure.INVALID_STREAM_COUNT,
            f"expected exactly one audio stream; received {len(streams)}",
        )
    stream = streams[0]
    if not isinstance(stream, dict) or "duration" not in stream:
        raise SingleAudioStreamProbeError(
            path,
            SingleAudioStreamProbeFailure.INVALID_DURATION,
            "audio stream duration is missing",
        )
    return _parse_positive_finite_duration(stream["duration"], artifact_path=path)


__all__ = [
    "SingleAudioStreamProbeError",
    "SingleAudioStreamProbeFailure",
    "probe_single_audio_stream_duration",
]
