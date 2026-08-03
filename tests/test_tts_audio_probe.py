from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from tella.tts.audio_probe import (
    FFPROBE_TIMEOUT_SECONDS,
    SingleAudioStreamProbeError,
    SingleAudioStreamProbeFailure,
    probe_single_audio_stream_duration,
)


def _artifact(tmp_path: Path) -> Path:
    path = tmp_path / "narration.mp3"
    path.write_bytes(b"not-real-audio")
    return path


def _completed(payload: object, *, stderr: bytes = b"", returncode: int = 0):
    stdout = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        ("35", 35.0),
        ("35.1256789", 35.1256789),
        ("32.0", 32.0),
        ("38.0", 38.0),
    ],
)
def test_probe_uses_exact_command_and_accepts_one_positive_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    duration: str,
    expected: float,
) -> None:
    artifact = _artifact(tmp_path)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return _completed({"streams": [{"index": 0, "duration": duration}]})

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert probe_single_audio_stream_duration(artifact, ffprobe_binary="strict-ffprobe") == expected
    assert calls == [
        (
            [
                "strict-ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index,duration",
                "-of",
                "json",
                str(artifact),
            ],
            {
                "shell": False,
                "capture_output": True,
                "check": False,
                "timeout": FFPROBE_TIMEOUT_SECONDS,
            },
        )
    ]


def test_probe_rejects_missing_file_and_directory(tmp_path: Path) -> None:
    with pytest.raises(SingleAudioStreamProbeError) as missing:
        probe_single_audio_stream_duration(tmp_path / "missing.mp3")
    assert missing.value.category is SingleAudioStreamProbeFailure.ARTIFACT_MISSING

    with pytest.raises(SingleAudioStreamProbeError) as directory:
        probe_single_audio_stream_duration(tmp_path)
    assert directory.value.category is SingleAudioStreamProbeFailure.ARTIFACT_NOT_REGULAR_FILE


def test_probe_rejects_unavailable_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _artifact(tmp_path)

    def unavailable(*_args, **_kwargs):
        raise FileNotFoundError("not found")

    monkeypatch.setattr(subprocess, "run", unavailable)
    with pytest.raises(SingleAudioStreamProbeError) as failure:
        probe_single_audio_stream_duration(artifact)
    assert failure.value.category is SingleAudioStreamProbeFailure.EXECUTABLE_UNAVAILABLE


def test_probe_rejects_timeout_with_bounded_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _artifact(tmp_path)

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("ffprobe", 30, stderr=b"x" * 1000)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(SingleAudioStreamProbeError) as failure:
        probe_single_audio_stream_duration(artifact)
    assert failure.value.category is SingleAudioStreamProbeFailure.TIMEOUT
    assert len(failure.value.detail) == 500


def test_probe_rejects_nonzero_exit_with_bounded_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _artifact(tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: _completed({}, stderr=b"failure" * 100, returncode=2),
    )
    with pytest.raises(SingleAudioStreamProbeError) as failure:
        probe_single_audio_stream_duration(artifact)
    assert failure.value.category is SingleAudioStreamProbeFailure.NONZERO_EXIT
    assert len(failure.value.detail) == 500


@pytest.mark.parametrize(
    "stdout",
    [
        b"",
        b"{",
        b"\xff",
        b'{"streams":[{"duration":NaN}]}',
        b'{"streams":[{"duration":Infinity}]}',
    ],
)
def test_probe_rejects_empty_malformed_or_non_utf8_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: bytes,
) -> None:
    artifact = _artifact(tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(stdout),
    )
    with pytest.raises(SingleAudioStreamProbeError) as failure:
        probe_single_audio_stream_duration(artifact)
    assert failure.value.category is SingleAudioStreamProbeFailure.MALFORMED_RESPONSE


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"streams": None},
        {"streams": {}},
        {"streams": "audio"},
    ],
)
def test_probe_requires_streams_array(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
) -> None:
    artifact = _artifact(tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(payload),
    )
    with pytest.raises(SingleAudioStreamProbeError) as failure:
        probe_single_audio_stream_duration(artifact)
    assert failure.value.category is SingleAudioStreamProbeFailure.MALFORMED_RESPONSE


@pytest.mark.parametrize(
    "streams",
    [
        [],
        [{"index": 0, "duration": "35"}, {"index": 1, "duration": "35"}],
    ],
)
def test_probe_requires_exactly_one_audio_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    streams: list[dict[str, object]],
) -> None:
    artifact = _artifact(tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: _completed({"streams": streams}),
    )
    with pytest.raises(SingleAudioStreamProbeError) as failure:
        probe_single_audio_stream_duration(artifact)
    assert failure.value.category is SingleAudioStreamProbeFailure.INVALID_STREAM_COUNT


@pytest.mark.parametrize(
    "stream",
    [
        {},
        {"duration": None},
        {"duration": "N/A"},
        {"duration": ""},
        {"duration": True},
        {"duration": {}},
        {"duration": []},
        {"duration": "0"},
        {"duration": "-1"},
        {"duration": "NaN"},
        {"duration": "Infinity"},
    ],
)
def test_probe_rejects_missing_or_invalid_duration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stream: dict[str, object],
) -> None:
    artifact = _artifact(tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: _completed({"streams": [stream]}),
    )
    with pytest.raises(SingleAudioStreamProbeError) as failure:
        probe_single_audio_stream_duration(artifact)
    assert failure.value.category is SingleAudioStreamProbeFailure.INVALID_DURATION
