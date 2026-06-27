"""Clean a user-supplied story .txt before it goes to the planner / TTS.

Raw text files (copied from the web, docs, chat) carry punctuation and
glyphs that read badly or break narration:

  - ellipses ("…" or "...") — TTS often voices them as "dot dot dot" or
    inserts a jarring long pause
  - repeated punctuation ("!!!", "???", "—— ")
  - smart quotes / fancy dashes / non-breaking spaces
  - markdown decoration (#, *, >, backticks) from pasted content
  - bullet glyphs and box-drawing characters

We normalise all of that to plain, speakable prose WITHOUT changing the
words themselves — only punctuation, whitespace, and decoration.
"""
from __future__ import annotations

import re
import unicodedata

# Characters we map to a plain ASCII equivalent before anything else.
_TRANSLATE = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",   # single quotes
    "“": '"', "”": '"', "„": '"', "‟": '"',   # double quotes
    "–": ",", "—": ",", "―": ",",                   # en/em/horiz dash -> comma
    "…": ".",                                                   # ellipsis -> period
    " ": " ", " ": " ", " ": " ", " ": " ",    # thin/nbsp spaces
    "•": " ", "·": " ", "●": " ", "▪": " ",    # bullets
    "﻿": "",                                                   # BOM
    "`": "",
}
_TRANS_TABLE = {ord(k): v for k, v in _TRANSLATE.items()}

# Lines that are pure decoration / separators (===, ---, ***, ___, |, etc.).
_SEPARATOR_LINE = re.compile(r"^[\s=\-*_~|#>•·]+$")

# Leading markdown / list markers at the start of a line.
_LINE_LEAD_MARKUP = re.compile(r"^\s*(?:#{1,6}\s+|>+\s*|[-*+]\s+|\d+[.)]\s+)")


def clean_script_text(raw: str) -> str:
    """Return a TTS-safe, plainly-punctuated version of ``raw``.

    Preserves the words; rewrites punctuation, whitespace, and decoration.
    """
    if not raw:
        return ""

    # Normalise unicode form first so composed/decomposed diacritics match
    # (important for Vietnamese), then map fancy glyphs to plain ones.
    text = unicodedata.normalize("NFC", raw)
    text = text.translate(_TRANS_TABLE)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    cleaned_lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            cleaned_lines.append("")
            continue
        if _SEPARATOR_LINE.match(stripped):
            continue  # drop ruler / divider lines
        stripped = _LINE_LEAD_MARKUP.sub("", stripped)
        # strip inline markdown emphasis markers but keep the words
        stripped = re.sub(r"(\*\*|\*|__|_)", "", stripped)
        cleaned_lines.append(stripped.strip())

    text = "\n".join(cleaned_lines)

    # Collapse runs of dots (".." / "..." / ". . .") -> single period.
    text = re.sub(r"\s*\.(?:\s*\.)+", ".", text)
    # Collapse repeated ! or ? -> a single mark.
    text = re.sub(r"([!?])\1+", r"\1", text)
    # Comma runs (from dash mapping) -> single comma; tidy ", ," sequences.
    text = re.sub(r"\s*,(?:\s*,)+", ",", text)
    # Space before punctuation -> none; ensure one space after sentence marks.
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    text = re.sub(r"([,.!?;:])(?=[^\s\d])", r"\1 ", text)
    # Drop a stray leading punctuation mark on a line ("., word" -> "word").
    text = re.sub(r"^\s*[,.;:]\s*", "", text, flags=re.MULTILINE)
    # Collapse horizontal whitespace.
    text = re.sub(r"[ \t]+", " ", text)
    # Collapse 3+ blank lines down to a single paragraph break.
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


__all__ = ["clean_script_text"]
