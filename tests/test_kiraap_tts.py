from __future__ import annotations

import asyncio
from contextlib import contextmanager
from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import struct
import threading
import time
from typing import Iterator

import pytest

from tella.topic_production.narration_stage import NarrationStageStore
from tella.tts.kiraap import (
    KIRAAP_IMPLEMENTATION_VERSION,
    KiraAPTTSProvider,
    KiraAPTTSProviderError,
    validate_kiraap_base_url,
)
from tella.tts.providers import GeminiTTSProvider, get_tts_provider
from tests.test_plan_only_narration_stage import (
    _approve,
    _generate,
    _sealed_application,
)


def _wav_bytes(*, payload_size: int = 128) -> bytes:
    payload = b"\0" * payload_size
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(payload))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 24000, 48000, 2, 16)
        + b"data"
        + struct.pack("<I", len(payload))
        + payload
    )


class _FakeKiraAP(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _FakeKiraAPHandler)
        self.status = 200
        self.content_type = "audio/wav"
        self.body = _wav_bytes()
        self.extra_headers: dict[str, str] = {}
        self.delay_seconds = 0.0
        self.calls: list[dict[str, object]] = []
        self.request_received = threading.Event()

    def handle_error(self, request, client_address) -> None:
        del request, client_address


class _FakeKiraAPHandler(BaseHTTPRequestHandler):
    server: _FakeKiraAP

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.calls.append(
            {
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers),
                "body": body,
            }
        )
        self.server.request_received.set()
        if self.server.delay_seconds:
            time.sleep(self.server.delay_seconds)
        try:
            self.send_response(self.server.status)
            self.send_header("Content-Type", self.server.content_type)
            self.send_header("Content-Length", str(len(self.server.body)))
            for name, value in self.server.extra_headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(self.server.body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@contextmanager
def _running_kiraap() -> Iterator[_FakeKiraAP]:
    server = _FakeKiraAP()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def _provider(server: _FakeKiraAP, *, timeout_seconds: float = 2) -> KiraAPTTSProvider:
    host, port = server.server_address
    return KiraAPTTSProvider(
        base_url=f"http://{host}:{port}",
        api_key="kira_sk_test_only",
        timeout_seconds=timeout_seconds,
    )


def _synthesize(provider: KiraAPTTSProvider, out_path: Path, text: str = "Lời kể chuẩn.") -> None:
    asyncio.run(
        provider.synthesize(
            text,
            out_path,
            voice="Kore",
            language="vi-VN",
            speed=0.85,
            codec="wav",
            sample_rate=24000,
        )
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://127.0.0.1:3001", "http://127.0.0.1:3001"),
        ("http://localhost:8123/", "http://localhost:8123"),
        ("http://[::1]:9000", "http://[::1]:9000"),
    ],
)
def test_loopback_base_url_is_canonical(value: str, expected: str) -> None:
    assert validate_kiraap_base_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "https://127.0.0.1:3001",
        "http://example.com:3001",
        "http://192.168.1.2:3001",
        "http://user:secret@127.0.0.1:3001",
        "http://127.0.0.1:3001?api_key=secret",
        "http://127.0.0.1:3001/#secret",
        "http://127.0.0.1",
        "file:///tmp/kiraap",
    ],
)
def test_non_loopback_or_credential_shaped_base_url_is_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="^KIRAAP_BASE_URL_INVALID$"):
        validate_kiraap_base_url(value)


def test_exact_request_and_wav_bytes_are_preserved(tmp_path: Path) -> None:
    with _running_kiraap() as server:
        output = tmp_path / "narration.wav"
        _synthesize(_provider(server), output, "Đây là lời kể chuẩn.")

    assert output.read_bytes() == server.body
    assert len(server.calls) == 1
    call = server.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == "/v1/audio/speech"
    assert call["headers"]["Authorization"] == "Bearer kira_sk_test_only"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["headers"]["Accept"] == "audio/wav"
    assert json.loads(call["body"]) == {
        "input": "Đây là lời kể chuẩn.",
        "voice": "Kore",
    }


@pytest.mark.parametrize(
    ("content_type", "body", "code"),
    [
        ("audio/wav", b"", "AUDIO_ARTIFACT_EMPTY"),
        (
            "application/json",
            b'{"url":"https://example.invalid/audio.wav"}',
            "AUDIO_MIME_UNSUPPORTED",
        ),
        ("text/html", b"<html>error</html>", "AUDIO_MIME_UNSUPPORTED"),
        ("audio/mpeg", b"RIFF1234WAVE", "AUDIO_MIME_UNSUPPORTED"),
        ("audio/wav", b"not-wave", "AUDIO_SIGNATURE_INVALID"),
        ("audio/wav", b"RIFF1234NOPE", "AUDIO_SIGNATURE_INVALID"),
    ],
)
def test_invalid_responses_fail_closed_without_output(
    tmp_path: Path,
    content_type: str,
    body: bytes,
    code: str,
) -> None:
    with _running_kiraap() as server:
        server.content_type = content_type
        server.body = body
        output = tmp_path / "narration.wav"
        with pytest.raises(KiraAPTTSProviderError, match=f"^{code}$"):
            _synthesize(_provider(server), output)
    assert not output.exists()


def test_oversized_response_is_rejected_while_streaming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tella.tts.kiraap.KIRAAP_MAX_AUDIO_BYTES", 32)
    with _running_kiraap() as server:
        server.body = _wav_bytes()
        output = tmp_path / "narration.wav"
        with pytest.raises(KiraAPTTSProviderError, match="^AUDIO_ARTIFACT_TOO_LARGE$"):
            _synthesize(_provider(server), output)
    assert not output.exists()


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "TTS_PROVIDER_AUTHENTICATION_FAILED"),
        (403, "TTS_PROVIDER_AUTHENTICATION_FAILED"),
        (429, "TTS_PROVIDER_RATE_LIMITED"),
        (500, "TTS_PROVIDER_UNAVAILABLE"),
        (503, "TTS_PROVIDER_UNAVAILABLE"),
        (307, "TTS_PROVIDER_UNAVAILABLE"),
    ],
)
def test_http_failures_are_typed_and_sanitized(tmp_path: Path, status: int, code: str) -> None:
    with _running_kiraap() as server:
        server.status = status
        server.body = b"kira_sk_test_only http://127.0.0.1/private C:\\secret"
        server.extra_headers["Location"] = "https://public.example/audio"
        output = tmp_path / "narration.wav"
        with pytest.raises(KiraAPTTSProviderError, match=f"^{code}$") as failure:
            _synthesize(_provider(server), output)
    detail = str(failure.value)
    assert detail == code
    assert "kira_sk" not in detail
    assert "127.0.0.1" not in detail
    assert "secret" not in detail
    assert not output.exists()


def test_timeout_is_typed_and_does_not_write_output(tmp_path: Path) -> None:
    with _running_kiraap() as server:
        server.delay_seconds = 0.2
        output = tmp_path / "narration.wav"
        with pytest.raises(KiraAPTTSProviderError, match="^TTS_PROVIDER_TIMEOUT$"):
            _synthesize(_provider(server, timeout_seconds=0.05), output)
    assert not output.exists()


def test_configuration_and_provider_selection_are_backend_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELLA_TTS_PROVIDER", "kiraap")
    monkeypatch.setenv("KIRAAP_TTS_API_KEY", "kira_sk_backend_only")
    monkeypatch.setenv("KIRAAP_TTS_BASE_URL", "http://127.0.0.1:3001")
    store = NarrationStageStore()
    projection = store.provider_configuration(True)
    assert isinstance(get_tts_provider("kiraap"), KiraAPTTSProvider)
    assert projection.provider_configured is True
    assert projection.provider_id == "kiraap-tts"
    assert projection.provider_display_name == "KiraAP TTS"
    assert projection.provider_implementation_version == KIRAAP_IMPLEMENTATION_VERSION
    assert projection.model_display_name == "Gemini 3.1 Flash TTS Preview"
    assert projection.voice_display_name == "Kore"
    assert projection.language == "vi-VN"
    assert projection.audio_format == "audio/wav"
    serialized = projection.model_dump_json()
    assert "kira_sk_backend_only" not in serialized
    assert "127.0.0.1" not in serialized

    monkeypatch.delenv("KIRAAP_TTS_API_KEY")
    unconfigured = NarrationStageStore()
    assert unconfigured.provider_configuration(False).provider_configured is False


def test_kiraap_runtime_acceptance_preserves_ex2_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gemini_calls = 0

    async def forbidden_gemini(*args, **kwargs):
        nonlocal gemini_calls
        gemini_calls += 1
        raise AssertionError("Gemini fallback is forbidden")

    monkeypatch.setattr(GeminiTTSProvider, "synthesize", forbidden_gemini)
    with _running_kiraap() as server:
        application, _, visual, run_id, package, _ = _sealed_application(
            tmp_path,
            provider=_provider(server),
            duration=35.0,
            configured=True,
        )
        visual_calls_before = len(visual.calls)
        access = application.get_narration_stage_access(run_id)
        source = deepcopy(access["narration_source"])
        approval = _approve(application, run_id, package)["approval"]
        generated = _generate(application, run_id, package, approval)
        artifact = generated["artifact"]
        assert generated["error"] is None
        assert len(server.calls) == 1
        assert json.loads(server.calls[0]["body"]) == {
            "input": source["narration_text"],
            "voice": "Kore",
        }
        assert artifact["provider_id"] == "kiraap-tts"
        assert artifact["voice_id"] == "Kore"
        assert artifact["audio_sha256"] == hashlib.sha256(server.body).hexdigest()
        assert artifact["measured_duration_ms"] == 35000
        duplicate = _generate(application, run_id, package, approval)
        assert duplicate["error"]["code"] == "TTS_REQUEST_ALREADY_COMPLETED"
        assert len(server.calls) == 1
        accepted = application.review_narration_audio(
            run_id,
            artifact["artifact_id"],
            "accept",
            {
                "schema_version": 1,
                "artifact_revision_id": artifact["artifact_revision_id"],
                "audio_sha256": artifact["audio_sha256"],
                "explicit_confirmation": True,
                "reason": None,
            },
        )["artifact"]

    assert accepted["eligible_for_renderer_stage_review"] is True
    assert accepted["renderer_execution_authority"] is False
    assert accepted["video_render_authority"] is False
    assert accepted["final_media_capability"] is False
    assert gemini_calls == 0
    assert len(visual.calls) == visual_calls_before


def test_concurrent_kiraap_generation_makes_one_request(tmp_path: Path) -> None:
    with _running_kiraap() as server:
        server.delay_seconds = 0.15
        application, _, _, run_id, package, _ = _sealed_application(
            tmp_path,
            provider=_provider(server),
            duration=35.0,
            configured=True,
        )
        approval = _approve(application, run_id, package)["approval"]
        results: list[dict[str, object]] = []
        worker = threading.Thread(
            target=lambda: results.append(_generate(application, run_id, package, approval))
        )
        worker.start()
        assert server.request_received.wait(timeout=5)
        concurrent = _generate(application, run_id, package, approval)
        worker.join(timeout=5)
        assert not worker.is_alive()

    assert concurrent["error"]["code"] == "TTS_GENERATION_ALREADY_IN_PROGRESS"
    assert results[0]["error"] is None
    assert len(server.calls) == 1


def test_provider_selection_is_fixed_for_store_lifetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELLA_TTS_PROVIDER", "kiraap")
    monkeypatch.setenv("KIRAAP_TTS_API_KEY", "kira_sk_backend_only")
    store = NarrationStageStore()
    initial = store.provider_configuration(True)
    monkeypatch.setenv("TELLA_TTS_PROVIDER", "gemini")
    current = store.provider_configuration(True)
    assert current == initial
    assert current.provider_id == "kiraap-tts"
