"""Keyword expander — turn 1 fuzzy keyword into 5 story directions.

When the user supplies a *keyword* instead of a fully-formed topic
(e.g. ``"lonely childhood"`` or ``"first day at school"``), Tella
proposes a small menu of distinct story angles. The user picks one
(index 1-5) and that becomes the planner's topic.

This is the only place in the pipeline where Gemini has explicit
creative latitude — picking 5 *different* tones / angles / character
archetypes for the same keyword is the whole job here.

Public entry point: :func:`expand_keyword`. Returns an
:class:`ExpandedKeyword` with exactly ``n`` (default 5) directions.
Output is already in ``target_lang`` so the user can read the menu
in their own language.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from google import genai
from google.genai import types

from tella._gemini import (
    DEFAULT_MODEL_EXPAND,
    get_client,
    parse_json_loose,
)
from tella.ingest.topic_translator import SUPPORTED_LANGS, _LANG_NAMES

logger = logging.getLogger("tella.ingest.keyword_expander")

MAX_ATTEMPTS = 3
DEFAULT_N = 5
MIN_N = 3
MAX_N = 8


@dataclass(frozen=True)
class StoryDirection:
    """One option in the expanded menu."""

    index: int          # 1-based position in the menu
    title: str          # 4-8 word title in target_lang
    synopsis: str       # 2-3 sentence pitch in target_lang
    tone: str           # 1-2 word tone tag (lowercased English):
                        # meditative / dramatic / whimsical / suspenseful /
                        # bittersweet / playful / hopeful / cautionary / …


@dataclass(frozen=True)
class ExpandedKeyword:
    """Result of one :func:`expand_keyword` call."""

    keyword: str
    target_language: str
    directions: list[StoryDirection]   # always len == requested n


_SYSTEM_PROMPT = """\
You are a creative story editor for a short-form video studio.

INPUT: one fuzzy KEYWORD plus a TARGET_LANG.
OUTPUT: a JSON OBJECT with exactly one top-level key "directions" whose
value is an ARRAY of N distinct story-direction proposals.

Output shape EXACTLY:
  {
    "directions": [
      {
        "index":    1,
        "title":    "<4-8 word title in TARGET_LANG, no ending period>",
        "synopsis": "<2-3 sentence pitch in TARGET_LANG, ~40-70 words,
                     hints at protagonist + setting + central conflict>",
        "tone":     "<1-2 word tone tag in lowercase ENGLISH from this allowed
                     set: meditative | dramatic | whimsical | suspenseful |
                     bittersweet | playful | hopeful | cautionary | nostalgic |
                     gritty | uplifting>"
      },
      { ... up to N entries ... }
    ]
  }

CRITICAL RULES:
  * "directions" array length MUST equal N exactly.
  * The N proposals must be GENUINELY DIFFERENT — not N ways to say the
    same story. Vary at least 2 of: setting, era, protagonist age/gender,
    tone, central conflict.
  * Each proposal is a STAND-ALONE story idea, not a sequence of beats.
  * Titles + synopses are written in TARGET_LANG — natural, idiomatic.
  * Tone tags stay in lowercase English (downstream consumer is non-localized).
  * Output JSON OBJECT ONLY. No markdown fence, no commentary, no extra
    top-level keys beyond "directions".
"""


async def expand_keyword(
    keyword: str,
    target_lang: str,
    *,
    n: int = DEFAULT_N,
    client: genai.Client | None = None,
    model: str = DEFAULT_MODEL_EXPAND,
) -> ExpandedKeyword:
    """Expand ``keyword`` into ``n`` story directions in ``target_lang``.

    Args:
        keyword:     A short fuzzy concept (2-6 words usually) — e.g.
                     ``"lonely childhood"``, ``"first job interview"``.
        target_lang: ISO-639-1, one of :data:`tella.ingest.topic_translator.SUPPORTED_LANGS`.
        n:           Number of proposals (default 5, clamped to [3, 8]).
        client:      Inject a pre-built ``genai.Client`` for tests.
        model:       Gemini model. Defaults to ``gemini-flash-latest`` —
                     creativity benefits from the bigger model over Lite.

    Returns:
        :class:`ExpandedKeyword` with exactly the requested number of directions.

    Raises:
        ValueError: invalid args, OR Gemini fails to produce a valid array
            of exactly ``n`` well-formed entries after :data:`MAX_ATTEMPTS`.
    """
    target = (target_lang or "").strip().lower()
    if target not in SUPPORTED_LANGS:
        raise ValueError(
            f"target_lang={target_lang!r} not in supported set {SUPPORTED_LANGS!r}"
        )

    kw = (keyword or "").strip()
    if not kw:
        raise ValueError("keyword is empty")

    n = max(MIN_N, min(MAX_N, int(n)))
    client = client or get_client()

    user_prompt = (
        f"TARGET_LANG: {target} ({_LANG_NAMES[target]})\n"
        f"N: {n}\n\n"
        f"KEYWORD:\n{kw}"
    )

    last_err: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = await asyncio.to_thread(
                client.models.generate_content,
                model=model,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    temperature=0.85 if attempt == 1 else 0.65,
                    max_output_tokens=2048,
                ),
            )
        except Exception as exc:
            last_err = exc
            logger.warning(
                "expand_keyword attempt %d Gemini error %s: %s",
                attempt, type(exc).__name__, exc,
            )
            continue

        raw = (resp.text or "").strip()
        try:
            data = parse_json_loose(raw)
        except json.JSONDecodeError as exc:
            last_err = exc
            logger.warning(
                "expand_keyword attempt %d JSON parse failed: %s — raw[:200]=%r",
                attempt, exc, raw[:200],
            )
            continue

        # Canonical shape is {"directions": [...]}, but be defensive against
        # Gemini renaming the wrapper key or returning a bare array.
        if isinstance(data, dict):
            for k in ("directions", "items", "proposals", "options", "stories", "results"):
                if isinstance(data.get(k), list):
                    data = data[k]
                    break
            else:
                # Last resort: take the first list-valued key.
                list_keys = [k for k, v in data.items() if isinstance(v, list)]
                if list_keys:
                    data = data[list_keys[0]]

        if not isinstance(data, list):
            last_err = ValueError(
                f"expected directions array, got {type(data).__name__} "
                f"(keys={list(data.keys()) if isinstance(data, dict) else 'n/a'})"
            )
            logger.warning("expand_keyword attempt %d: %s", attempt, last_err)
            continue

        if len(data) != n:
            last_err = ValueError(
                f"expected {n} directions, got {len(data)}"
            )
            logger.warning("expand_keyword attempt %d: %s", attempt, last_err)
            # Don't continue — try anyway if all entries are well-formed and
            # we got at least MIN_N; otherwise retry.
            if len(data) < MIN_N:
                continue

        directions: list[StoryDirection] = []
        all_ok = True
        for i, entry in enumerate(data, start=1):
            if not isinstance(entry, dict):
                all_ok = False
                break
            title = (entry.get("title") or "").strip()
            synopsis = (entry.get("synopsis") or "").strip()
            tone = (entry.get("tone") or "").strip().lower() or "neutral"
            if not title or not synopsis:
                all_ok = False
                break
            directions.append(
                StoryDirection(index=i, title=title, synopsis=synopsis, tone=tone)
            )

        if not all_ok or not directions:
            last_err = ValueError("one or more directions had empty title/synopsis")
            logger.warning("expand_keyword attempt %d: %s", attempt, last_err)
            continue

        logger.info(
            "expanded %r → %d directions in %s (attempt %d)",
            kw[:60], len(directions), target, attempt,
        )
        return ExpandedKeyword(
            keyword=kw, target_language=target, directions=directions,
        )

    assert last_err is not None
    raise ValueError(
        f"expand_keyword failed after {MAX_ATTEMPTS} attempts: {last_err}"
    )


def expand(keyword: str, target_lang: str, **kwargs) -> ExpandedKeyword:
    """Sync wrapper for CLI / smoke tests."""
    return asyncio.run(expand_keyword(keyword, target_lang, **kwargs))


__all__ = [
    "DEFAULT_N",
    "MAX_ATTEMPTS",
    "MAX_N",
    "MIN_N",
    "ExpandedKeyword",
    "StoryDirection",
    "expand",
    "expand_keyword",
]
