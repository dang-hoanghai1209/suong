"""Bounded loopback-only KiraAP-compatible WAV TTS provider."""

from __future__ import annotations

import math
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx

from tella.tts.providers import TTSProvider, TTSResult

KIRAAP_PROVIDER_ID = "kiraap-tts"
KIRAAP_IMPLEMENTATION_VERSION = "tella.tts.kiraap.KiraAPTTSProvider.v1"
KIRAAP_MODEL_ID = "gemini-3.1-flash-tts-preview"
KIRAAP_MODEL_DISPLAY_NAME = "Gemini 3.1 Flash TTS Preview"
KIRAAP_DEFAULT_BASE_URL = "http://127.0.0.1:3001"
KIRAAP_DEFAULT_VOICE = "Kore"
KIRAAP_DEFAULT_TIMEOUT_SECONDS = 90.0
KIRAAP_LANGUAGE = "vi-VN"
KIRAAP_MAX_AUDIO_BYTES = 20 * 1024 * 1024

_APPROVED_WAV_MIME_TYPES = frozenset(
    {
        "audio/wav",
        "audio/wave",
        "audio/x-wav",
        "audio/vnd.wave",
    }
)
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class KiraAPTTSProviderError(RuntimeError):
    """Sanitized typed provider failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def validate_kiraap_base_url(value: str) -> str:
    """Return a canonical loopback base URL or reject without echoing it."""

    try:
        parsed = urlparse(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("KIRAAP_BASE_URL_INVALID") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in _LOOPBACK_HOSTS
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or parsed.params
    ):
        raise ValueError("KIRAAP_BASE_URL_INVALID")
    host = f"[{parsed.hostname}]" if parsed.hostname == "::1" else parsed.hostname
    return f"http://{host}:{port}"


def _timeout_from_environment(value: str | None) -> float:
    if value is None or not value.strip():
        return KIRAAP_DEFAULT_TIMEOUT_SECONDS
    try:
        timeout = float(value)
    except ValueError as exc:
        raise ValueError("KIRAAP_TIMEOUT_INVALID") from exc
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 300:
        raise ValueError("KIRAAP_TIMEOUT_INVALID")
    return timeout


class KiraAPTTSProvider(TTSProvider):
    """POST canonical narration to a backend-owned loopback KiraAP endpoint."""

    provider_name = KIRAAP_PROVIDER_ID

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        voice: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._base_url = validate_kiraap_base_url(
            base_url
            if base_url is not None
            else (os.environ.get("KIRAAP_TTS_BASE_URL") or KIRAAP_DEFAULT_BASE_URL).strip()
        )
        self._api_key = (
            api_key if api_key is not None else os.environ.get("KIRAAP_TTS_API_KEY") or ""
        ).strip()
        self._voice = (
            voice if voice is not None else os.environ.get("KIRAAP_TTS_VOICE") or ""
        ).strip() or KIRAAP_DEFAULT_VOICE
        if self._voice != KIRAAP_DEFAULT_VOICE:
            raise ValueError("KIRAAP_VOICE_UNSUPPORTED")
        self._timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else _timeout_from_environment(os.environ.get("KIRAAP_TTS_TIMEOUT_SECONDS"))
        )
        if (
            not isinstance(self._timeout_seconds, (int, float))
            or isinstance(self._timeout_seconds, bool)
            or not math.isfinite(float(self._timeout_seconds))
            or float(self._timeout_seconds) <= 0
            or float(self._timeout_seconds) > 300
        ):
            raise ValueError("KIRAAP_TIMEOUT_INVALID")
        self._timeout_seconds = float(self._timeout_seconds)

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def synthesize(
        self,
        text: str,
        out_path: Path,
        *,
        voice: str,
        language: str,
        speed: float,
        codec: str,
        sample_rate: int,
        metadata: dict[str, object] | None = None,
    ) -> TTSResult:
        del speed, sample_rate
        if not self.configured:
            raise KiraAPTTSProviderError("TTS_PROVIDER_NOT_CONFIGURED")
        if voice != self._voice or language != KIRAAP_LANGUAGE or codec != "wav":
            raise KiraAPTTSProviderError("TTS_PROVIDER_FAILED")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "audio/wav",
        }
        payload = {"input": text, "voice": self._voice}
        content = bytearray()
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/v1/audio/speech",
                    headers=headers,
                    json=payload,
                ) as response:
                    self._validate_status(response.status_code)
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if content_type not in _APPROVED_WAV_MIME_TYPES:
                        raise KiraAPTTSProviderError("AUDIO_MIME_UNSUPPORTED")
                    declared_length = response.headers.get("content-length")
                    if declared_length:
                        try:
                            if int(declared_length) > KIRAAP_MAX_AUDIO_BYTES:
                                raise KiraAPTTSProviderError("AUDIO_ARTIFACT_TOO_LARGE")
                        except ValueError as exc:
                            raise KiraAPTTSProviderError("TTS_RESPONSE_INVALID") from exc
                    async for block in response.aiter_bytes():
                        content.extend(block)
                        if len(content) > KIRAAP_MAX_AUDIO_BYTES:
                            raise KiraAPTTSProviderError("AUDIO_ARTIFACT_TOO_LARGE")
        except KiraAPTTSProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise KiraAPTTSProviderError("TTS_PROVIDER_TIMEOUT") from exc
        except httpx.TransportError as exc:
            raise KiraAPTTSProviderError("TTS_PROVIDER_UNAVAILABLE") from exc
        if not content:
            raise KiraAPTTSProviderError("AUDIO_ARTIFACT_EMPTY")
        if len(content) < 12 or content[:4] != b"RIFF" or content[8:12] != b"WAVE":
            raise KiraAPTTSProviderError("AUDIO_SIGNATURE_INVALID")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(content)
        return TTSResult(
            audio_path=out_path,
            provider=self.provider_name,
            voice=self._voice,
            language=KIRAAP_LANGUAGE,
            metadata={
                **(metadata or {}),
                "model": KIRAAP_MODEL_ID,
                "codec": "wav",
                "sample_rate": 24000,
            },
        )

    @staticmethod
    def _validate_status(status_code: int) -> None:
        if 200 <= status_code < 300:
            return
        if status_code in {401, 403}:
            raise KiraAPTTSProviderError("TTS_PROVIDER_AUTHENTICATION_FAILED")
        if status_code == 429:
            raise KiraAPTTSProviderError("TTS_PROVIDER_RATE_LIMITED")
        if 300 <= status_code < 400 or status_code >= 500:
            raise KiraAPTTSProviderError("TTS_PROVIDER_UNAVAILABLE")
        raise KiraAPTTSProviderError("TTS_PROVIDER_FAILED")


__all__ = [
    "KIRAAP_DEFAULT_BASE_URL",
    "KIRAAP_DEFAULT_TIMEOUT_SECONDS",
    "KIRAAP_DEFAULT_VOICE",
    "KIRAAP_IMPLEMENTATION_VERSION",
    "KIRAAP_LANGUAGE",
    "KIRAAP_MAX_AUDIO_BYTES",
    "KIRAAP_MODEL_DISPLAY_NAME",
    "KIRAAP_MODEL_ID",
    "KIRAAP_PROVIDER_ID",
    "KiraAPTTSProvider",
    "KiraAPTTSProviderError",
    "validate_kiraap_base_url",
]
