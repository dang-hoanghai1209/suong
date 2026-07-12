"""Displayed subtitle text preparation shared by timing and rendering."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


_REMOVED_FORMAT_CODEPOINTS = {
    0xFEFF,
    0x200B,
    0x200C,
    0x200D,
    0x2060,
    0xFFFD,
}
_LEADING_EMPTY_SQUARE = "\u25a1"


@dataclass(frozen=True)
class SubtitleSanitization:
    text: str
    removed_codepoints: tuple[str, ...] = ()


def sanitize_subtitle_text(text: str) -> SubtitleSanitization:
    """Remove display-breaking format/control characters and normalize to NFC."""
    source = text or ""
    cleaned: list[str] = []
    removed: list[str] = []
    for char in source:
        codepoint = ord(char)
        is_control = (
            (0x00 <= codepoint <= 0x1F and char not in {"\n", "\t"})
            or 0x7F <= codepoint <= 0x9F
        )
        if codepoint in _REMOVED_FORMAT_CODEPOINTS or is_control:
            removed.append(f"U+{codepoint:04X}")
            continue
        cleaned.append(char)

    value = "".join(cleaned)
    square_match = re.match(r"^\s*\u25a1\s*(?=\S)", value)
    if square_match:
        value = value[square_match.end():]
        removed.append("U+25A1")

    return SubtitleSanitization(
        text=unicodedata.normalize("NFC", value),
        removed_codepoints=tuple(dict.fromkeys(removed)),
    )


def subtitle_text_for_style(text: str, subtitle_style: str) -> SubtitleSanitization:
    if subtitle_style in {"reel_minimal", "insight_reel", "practical_steps_reel"}:
        return sanitize_subtitle_text(text)
    return SubtitleSanitization(text=text or "")


def sanitize_highlight_words(words: list[str], subtitle_style: str) -> list[str]:
    if subtitle_style not in {"reel_minimal", "insight_reel", "practical_steps_reel"}:
        return list(words)
    return [
        result.text
        for word in words
        if (result := sanitize_subtitle_text(word)).text
    ]


__all__ = [
    "SubtitleSanitization",
    "sanitize_highlight_words",
    "sanitize_subtitle_text",
    "subtitle_text_for_style",
]
