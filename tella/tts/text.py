"""Text cleanup before narration synthesis."""
from __future__ import annotations

import re
import unicodedata


def normalize_narration_for_tts(text: str, provider: str, theme: str) -> str:
    """Return TTS-friendly narration without changing scene/caption text.

    The minimalist emotional theme should sound conversational. Ellipses,
    stacked punctuation, and line-broken fragments tend to make TTS engines
    overperform pauses, so we smooth those before sending the single combined
    narration request.
    """
    text = unicodedata.normalize("NFC", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)

    if theme in {"minimalist_emotional", "minimalist_symbolic_reel"}:
        text = re.sub(r"\s*(?:\.{3,}|\u2026+)\s*", ", ", text)
        text = re.sub(r"\s*\n+\s*", " ", text)
        text = re.sub(r"([!?]){2,}", r"\1", text)
        text = re.sub(r"([.]){2,}", r"\1", text)
        text = re.sub(r",\s*,+", ", ", text)
        text = re.sub(r"\s+([,.;:!?])", r"\1", text)
        text = re.sub(r"([,;:])\s*([.!?])", r"\2", text)
    else:
        text = re.sub(r"\s*\n+\s*", " ", text)

    text = re.sub(r"\s{2,}", " ", text).strip()
    return text


__all__ = ["normalize_narration_for_tts"]
