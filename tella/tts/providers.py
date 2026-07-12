"""TTS provider abstraction for continuous narration."""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from tella.tts import edge

logger = logging.getLogger("tella.tts.providers")

_CLOUDFLARE_GROK_MODEL = "xai/grok-tts"
_HTTP_TIMEOUT = 90.0


@dataclass
class TTSResult:
    audio_path: Path
    duration: float = 0.0
    provider: str = ""
    voice: str = ""
    language: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class TTSProvider:
    provider_name = "base"

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
        metadata: dict[str, Any] | None = None,
    ) -> TTSResult:
        raise NotImplementedError


class EdgeTTSProvider(TTSProvider):
    provider_name = "edge"

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
        metadata: dict[str, Any] | None = None,
    ) -> TTSResult:
        meta = dict(metadata or {})
        rate = meta.get("edge_rate") or _speed_to_edge_rate(speed)
        await edge.synthesize(text, voice, out_path, rate=rate)
        return TTSResult(
            audio_path=out_path,
            provider=self.provider_name,
            voice=voice,
            language=language,
            metadata={**meta, "edge_rate": rate, "codec": "mp3"},
        )


class GeminiTTSProvider(TTSProvider):
    provider_name = "gemini"

    async def synthesize(self, text, out_path, *, voice, language, speed, codec, sample_rate, metadata=None):
        from tella.tts import gemini
        meta = dict(metadata or {})
        model = str(meta.get("model") or "").strip()
        style = str(meta.get("style") or "").strip()
        if not model:
            raise RuntimeError("Gemini TTS requires an explicit model")
        generated = await gemini.synthesize(
            text, out_path, model=model, voice=voice, style=style
        )
        return TTSResult(
            audio_path=Path(out_path), provider="gemini", voice=voice,
            language=language, metadata={**meta, **generated, "codec": "wav", "sample_rate": 24000},
        )


class CloudflareGrokTTSProvider(TTSProvider):
    provider_name = "cloudflare_grok"

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
        metadata: dict[str, Any] | None = None,
    ) -> TTSResult:
        creds = _resolve_cloudflare_credentials()
        if not creds:
            raise RuntimeError(
                "Cloudflare Grok TTS: no credentials. Set CF_ACCOUNT_ID + CF_AI_TOKEN "
                "or CLOUDFLARE_ACCOUNT_ID + CLOUDFLARE_API_TOKEN."
            )

        model = (
            os.environ.get("TELLA_CLOUDFLARE_GROK_TTS_MODEL")
            or _CLOUDFLARE_GROK_MODEL
        ).strip()
        full_payload = {
            "text": text,
            "voice_id": voice,
            "language": language,
            "output_format": {
                "codec": codec,
                "sample_rate": sample_rate,
            },
        }
        minimal_payload = {
            "text": text,
            "voice_id": voice,
            "language": language,
        }

        last_err: Exception | None = None
        for cred_idx, (account_id, token) in enumerate(creds, start=1):
            unified_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run"
            path_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            for payload_idx, payload in enumerate((full_payload, minimal_payload), start=1):
                request_variants = (
                    ("unified", unified_url, {"model": model, "input": payload}),
                    ("path", path_url, payload),
                )
                for endpoint_style, url, body in request_variants:
                    try:
                        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                            resp = await client.post(url, headers=headers, json=body)
                            if resp.status_code == 200:
                                await _write_audio_response(resp, out_path, client=client)
                                logger.info(
                                    "tts provider=cloudflare_grok model=%s voice_id=%s language=%s output=%s",
                                    model,
                                    voice,
                                    language,
                                    out_path,
                                )
                                return TTSResult(
                                    audio_path=out_path,
                                    provider=self.provider_name,
                                    voice=voice,
                                    language=language,
                                    metadata={
                                        **(metadata or {}),
                                        "model": model,
                                        "endpoint_style": endpoint_style,
                                        "payload_style": "full" if payload_idx == 1 else "minimal",
                                        "account_index": cred_idx,
                                        "codec": codec,
                                        "sample_rate": sample_rate,
                                        "speed": speed,
                                    },
                                )
                            last_err = RuntimeError(
                                f"Cloudflare Grok TTS HTTP {resp.status_code}: {resp.text[:300]}"
                            )
                            logger.warning(
                                "cloudflare_grok tts failed account=%d endpoint=%s payload=%d status=%d: %s",
                                cred_idx,
                                endpoint_style,
                                payload_idx,
                                resp.status_code,
                                resp.text[:180],
                            )
                            if resp.status_code not in (400, 422):
                                break
                    except Exception as exc:
                        last_err = exc
                        logger.warning(
                            "cloudflare_grok tts error account=%d endpoint=%s: %s",
                            cred_idx,
                            endpoint_style,
                            exc,
                        )
                        break
                else:
                    continue
                if last_err and "No route for that URI" not in str(last_err):
                    continue
        raise RuntimeError(f"Cloudflare Grok TTS failed: {last_err}")


class XAITTSProvider(TTSProvider):
    provider_name = "xai"

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
        metadata: dict[str, Any] | None = None,
    ) -> TTSResult:
        api_key = (os.environ.get("XAI_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("xAI TTS: XAI_API_KEY is not set")
        payload = {
            "text": text,
            "voice_id": voice,
            "language": language,
            "speed": speed,
            "output_format": {
                "codec": codec,
                "sample_rate": sample_rate,
            },
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post("https://api.x.ai/v1/tts", headers=headers, json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"xAI TTS HTTP {resp.status_code}: {resp.text[:300]}")
            await _write_audio_response(resp, out_path, client=client)
        logger.info("tts provider=xai voice_id=%s language=%s output=%s", voice, language, out_path)
        return TTSResult(
            audio_path=out_path,
            provider=self.provider_name,
            voice=voice,
            language=language,
            metadata={
                **(metadata or {}),
                "codec": codec,
                "sample_rate": sample_rate,
                "speed": speed,
            },
        )


def get_tts_provider(name: str) -> TTSProvider:
    normalized = (name or "edge").strip().lower()
    if normalized == "edge":
        return EdgeTTSProvider()
    if normalized == "gemini":
        return GeminiTTSProvider()
    if normalized == "cloudflare_grok":
        return CloudflareGrokTTSProvider()
    if normalized == "xai":
        return XAITTSProvider()
    raise RuntimeError(
        f"Unsupported TELLA_TTS_PROVIDER={name!r}; use edge, gemini, cloudflare_grok, or xai."
    )


async def list_xai_voices(api_key: str | None = None) -> Any:
    """Return the direct xAI TTS voice list, when the direct API is configured."""
    token = (api_key or os.environ.get("XAI_API_KEY") or "").strip()
    if not token:
        raise RuntimeError("XAI_API_KEY is not set")
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.get("https://api.x.ai/v1/tts/voices", headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"xAI voice list HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()
    except json.JSONDecodeError:
        return resp.text


def _resolve_cloudflare_credentials() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    accounts_csv = (os.environ.get("CF_ACCOUNTS") or "").strip()
    if accounts_csv:
        for piece in accounts_csv.split(";"):
            piece = piece.strip()
            if ":" not in piece:
                continue
            account_id, token = piece.split(":", 1)
            account_id, token = account_id.strip(), token.strip()
            if account_id and token:
                pairs.append((account_id, token))

    candidates = [
        ("CF_ACCOUNT_ID", "CF_AI_TOKEN"),
        ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"),
    ]
    for account_var, token_var in candidates:
        account_id = (os.environ.get(account_var) or "").strip()
        token = (os.environ.get(token_var) or "").strip()
        if account_id and token:
            pair = (account_id, token)
            if pair not in pairs:
                pairs.append(pair)
    return pairs


async def _write_audio_response(
    resp: httpx.Response,
    out_path: Path,
    *,
    client: httpx.AsyncClient,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    content_type = resp.headers.get("content-type", "").lower()
    if "json" not in content_type and resp.content:
        out_path.write_bytes(resp.content)
        _assert_audio_file(out_path)
        return

    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"TTS response was not JSON or audio bytes: {exc}") from exc

    if isinstance(data, dict) and data.get("success") is False:
        raise RuntimeError(f"TTS provider errors: {data.get('errors') or data}")

    obj = data.get("result", data) if isinstance(data, dict) else data
    audio_url = _find_audio_url(obj)
    if audio_url:
        download = await client.get(audio_url)
        if download.status_code != 200 or not download.content:
            raise RuntimeError(f"TTS audio URL download failed HTTP {download.status_code}")
        out_path.write_bytes(download.content)
        _assert_audio_file(out_path)
        return

    audio_bytes = _find_audio_bytes(obj)
    if audio_bytes:
        out_path.write_bytes(audio_bytes)
        _assert_audio_file(out_path)
        return

    raise RuntimeError(f"TTS response did not contain audio bytes or URL: {str(data)[:300]}")


def _find_audio_url(obj: Any) -> str:
    for value in _walk_values(obj, keys=("audio", "audio_url", "audioUrl", "url", "download_url")):
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return ""


def _find_audio_bytes(obj: Any) -> bytes:
    for value in _walk_values(
        obj,
        keys=("audio", "audioContent", "audio_content", "audio_b64", "audio_base64", "data"),
    ):
        if isinstance(value, str):
            decoded = _decode_audio_string(value)
            if decoded:
                return decoded
        if isinstance(value, list) and all(isinstance(x, int) for x in value):
            return bytes(value)
    return b""


def _walk_values(obj: Any, *, keys: tuple[str, ...]):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in keys:
                yield value
            yield from _walk_values(value, keys=keys)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_values(item, keys=keys)


def _decode_audio_string(value: str) -> bytes:
    text = (value or "").strip()
    if not text or text.startswith(("http://", "https://")):
        return b""
    if "," in text and text.lower().startswith("data:"):
        text = text.split(",", 1)[1]
    text = re.sub(r"\s+", "", text)
    try:
        return base64.b64decode(text, validate=True)
    except (ValueError, TypeError):
        return b""


def _assert_audio_file(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < 256:
        raise RuntimeError(
            f"TTS provider wrote an invalid audio file ({path.stat().st_size if path.exists() else 0} bytes)"
        )


def _speed_to_edge_rate(speed: float) -> str:
    try:
        rate = float(speed)
    except (TypeError, ValueError):
        rate = 1.0
    pct = int(round((rate - 1.0) * 100))
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct}%"


__all__ = [
    "TTSProvider",
    "TTSResult",
    "EdgeTTSProvider",
    "GeminiTTSProvider",
    "CloudflareGrokTTSProvider",
    "XAITTSProvider",
    "get_tts_provider",
    "list_xai_voices",
]
