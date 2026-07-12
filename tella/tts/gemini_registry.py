"""Versioned Gemini TTS voices and delivery-only style presets."""
from __future__ import annotations

from dataclasses import dataclass

REGISTRY_VERSION = 1
BENCHMARK_LANGUAGE = "vi-VN"
APPROVED_MODELS = ("gemini-3.1-flash-tts-preview",)


@dataclass(frozen=True)
class GeminiVoice:
    provider: str
    canonical_name: str
    enabled: bool
    benchmark_language: str
    tags: tuple[str, ...]
    compatible_models: tuple[str, ...]
    registry_version: int


VOICE_NAMES = ("Achernar", "Autonoe", "Callirrhoe", "Gacrux", "Leda", "Zephyr")
VOICES = {
    name: GeminiVoice(
        provider="gemini",
        canonical_name=name,
        enabled=True,
        benchmark_language=BENCHMARK_LANGUAGE,
        tags=("benchmark_candidate",),
        compatible_models=APPROVED_MODELS,
        registry_version=REGISTRY_VERSION,
    )
    for name in VOICE_NAMES
}

STYLE_PRESETS = {
    "natural": (
        "Speak in normal conversational Vietnamese with stable volume, medium natural "
        "speaking speed, clear pronunciation, and short natural pauses. Do not whisper "
        "or use breathy delivery, dramatic performance, prolonged final syllables, or a "
        "radio, advertisement, or virtual-assistant tone."
    ),
    "vocal_smile": (
        "Use a subtle audible smile and sound warm and approachable, but not so cheerful "
        "that the delivery sounds promotional. Do not exaggerate pitch changes, giggle, "
        "or add non-verbal sounds."
    ),
    "natural_vocal_smile": (
        "Use natural conversational Vietnamese with a subtle vocal smile. Keep the "
        "delivery calm, clear, warm, and direct, with stable volume, medium natural "
        "speaking speed, clear pronunciation, and short natural pauses. Narration must "
        "remain intelligible and grounded. Do not whisper, use breathy or dramatic "
        "delivery, prolong final syllables, exaggerate pitch changes, giggle, add "
        "non-verbal sounds, or use a radio, advertisement, or virtual-assistant tone."
    ),
}


def resolve_voice(name: str, model: str) -> GeminiVoice:
    voice = VOICES.get((name or "").strip())
    if voice is None:
        raise ValueError(f"unknown Gemini TTS voice: {name!r}")
    if not voice.enabled:
        raise ValueError(f"Gemini TTS voice is disabled: {name}")
    if model not in voice.compatible_models:
        raise ValueError(f"Gemini TTS voice {name} is incompatible with model {model!r}")
    return voice


def resolve_style(style: str) -> str:
    key = (style or "").strip()
    try:
        return STYLE_PRESETS[key]
    except KeyError as exc:
        raise ValueError(f"unknown Gemini TTS delivery style: {style!r}") from exc


__all__ = ["APPROVED_MODELS", "REGISTRY_VERSION", "STYLE_PRESETS", "VOICE_NAMES", "VOICES", "GeminiVoice", "resolve_style", "resolve_voice"]
