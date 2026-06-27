"""Word-wrap helpers for on-screen captions / titles.

ffmpeg's ``drawtext`` filter does NOT auto-wrap. We pre-wrap text into
lines at a target character width so each line fits inside the safe zone
horizontally. The width budget is computed from font_size + safe_zone
width; characters per line ≈ safe_width / (font_size * AVG_GLYPH_RATIO).

We pick AVG_GLYPH_RATIO conservatively (0.55) because we mix Latin
(narrow) + CJK/IPA/diacritics (wide). Better to wrap slightly early
than to overflow the safe zone.
"""
from __future__ import annotations

import re

AVG_GLYPH_RATIO = 0.55  # average glyph width / font_size for mixed-script text


def chars_per_line(safe_width_px: int, font_size_px: int) -> int:
    """Estimate how many characters fit on one line at this font size."""
    if font_size_px <= 0:
        return 999
    return max(8, int(safe_width_px / (font_size_px * AVG_GLYPH_RATIO)))


def wrap(text: str, max_chars: int, *, max_lines: int = 5) -> list[str]:
    """Word-wrap ``text`` to lines ≤ ``max_chars`` long.

    Splits on whitespace + common Asian-script boundary punctuation so
    Vietnamese / Japanese / Korean text wraps sensibly without breaking
    inside a word.

    Returns up to ``max_lines`` lines. Excess is truncated with "…".
    """
    text = (text or "").strip()
    if not text:
        return []

    # CJK languages often lack spaces — split on character boundaries when
    # whitespace splitting produces single "word" longer than max_chars.
    words = re.split(r"\s+", text)
    if any(len(w) > max_chars for w in words):
        words = list(text)  # char-by-char fallback

    lines: list[str] = []
    cur = ""
    for w in words:
        candidate = (cur + " " + w).strip() if cur else w
        if len(candidate) <= max_chars:
            cur = candidate
        else:
            if cur:
                lines.append(cur)
                cur = w
            else:
                lines.append(w[:max_chars])
                cur = w[max_chars:]
            if len(lines) >= max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)

    if len(lines) >= max_lines and (cur or len(words) > sum(len(l.split()) for l in lines)):
        # Indicate truncation on the last line.
        last = lines[-1]
        if len(last) > 3 and not last.endswith("…"):
            lines[-1] = last[: max(1, max_chars - 1)].rstrip() + "…"

    return lines


__all__ = [
    "chars_per_line",
    "wrap",
]
