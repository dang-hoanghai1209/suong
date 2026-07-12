"""Official-SDK Gemini TTS adapter with explicit model and voice controls."""
from __future__ import annotations

import asyncio
import hashlib
import wave
from pathlib import Path
from typing import Any, Callable

from tella.tts.gemini_registry import REGISTRY_VERSION, resolve_style, resolve_voice


def credential_environment_name() -> str:
    import os
    if os.environ.get("GOOGLE_API_KEY"):
        return "GOOGLE_API_KEY"
    if os.environ.get("GEMINI_API_KEY"):
        return "GEMINI_API_KEY"
    return "GOOGLE_API_KEY or GEMINI_API_KEY"


def _official_client() -> Any:
    from google import genai
    return genai.Client()


def _request(client: Any, *, model: str, voice: str, text: str, instruction: str) -> Any:
    from google.genai import types
    return client.models.generate_content(
        model=model,
        contents=text,
        config=types.GenerateContentConfig(
            system_instruction=instruction,
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                )
            ),
        ),
    )


def _audio_bytes(response: Any) -> tuple[bytes, str]:
    try:
        inline = response.candidates[0].content.parts[0].inline_data
        data = inline.data
        mime = inline.mime_type or "audio/L16;rate=24000"
    except (AttributeError, IndexError, TypeError) as exc:
        raise RuntimeError("Gemini TTS response contained no inline audio") from exc
    if not data:
        raise RuntimeError("Gemini TTS response contained empty audio")
    return bytes(data), str(mime)


def _write_wav(path: Path, data: bytes, mime_type: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if data[:4] == b"RIFF":
        path.write_bytes(data)
        return
    rate = 24000
    if "rate=" in mime_type:
        try:
            rate = int(mime_type.split("rate=", 1)[1].split(";", 1)[0])
        except ValueError:
            pass
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(data)


async def synthesize(
    text: str,
    out_path: Path,
    *,
    model: str,
    voice: str,
    style: str,
    client_factory: Callable[[], Any] = _official_client,
) -> dict[str, Any]:
    registered = resolve_voice(voice, model)
    instruction = resolve_style(style)
    client = client_factory()
    response = await asyncio.to_thread(
        _request, client, model=model, voice=registered.canonical_name,
        text=text, instruction=instruction,
    )
    data, mime = _audio_bytes(response)
    _write_wav(Path(out_path), data, mime)
    return {
        "provider": "gemini",
        "model": model,
        "voice": registered.canonical_name,
        "voice_registry_version": REGISTRY_VERSION,
        "language": registered.benchmark_language,
        "requested_style": style,
        "resolved_style_instruction": instruction,
        "source_narration_text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "raw_output_path": str(out_path),
        "request_attempt_count": 1,
        "fallback_used": False,
        "credential_environment_variable": credential_environment_name(),
    }


__all__ = ["credential_environment_name", "synthesize"]
