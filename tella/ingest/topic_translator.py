"""Topic translator — Gemini Flash Lite, runs BEFORE the planner.

Why this step exists: Lingora hit a bug where Gemini auto-detected the
user's input language (Vietnamese) and produced its output in Vietnamese
even when ``target_lang=en`` was set. Translating up-front eliminates
the ambiguity — the planner only ever sees the target-lang topic.

Public entry point: :func:`translate_topic`. Pass a free-text topic + an
ISO-639-1 ``target_lang`` (vi/en/ja/ko/zh/de/fr/es). Get back a
:class:`Translation` with the translated topic, the detected source
language, and a ``needs_translation`` short-circuit flag (True if Gemini
detected the source already matches the target).

Cost: ~30 input + ~30 output tokens at ``gemini-flash-lite-latest``
pricing — effectively free at moderate volume.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from google import genai
from google.genai import types

from tella._gemini import (
    DEFAULT_MODEL_TRANSLATE,
    get_client,
    parse_json_loose,
)

logger = logging.getLogger("tella.ingest.topic_translator")

# Supported target locales (ISO-639-1). Extend by adding entries here +
# the matching system-prompt block in :data:`_LANG_NAMES`.
SUPPORTED_LANGS: tuple[str, ...] = ("vi", "en", "ja", "ko", "zh", "de", "fr", "es")

_LANG_NAMES: dict[str, str] = {
    "vi": "Vietnamese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese (Simplified)",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
}

MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class Translation:
    """Result of one :func:`translate_topic` call."""

    translated_topic: str         # the topic in ``target_language``
    target_language: str          # ISO-639-1
    source_language_detected: str # ISO-639-1 of what Gemini saw in the input
    needs_translation: bool       # False when source_detected == target


_SYSTEM_PROMPT = """\
You are a careful translator for short story video titles / topic descriptions.

INPUT: one short topic line (5-20 words) in any language.
OUTPUT: a JSON object with these EXACT keys:
  {
    "translated_topic":         <the topic rephrased in TARGET_LANG, natural and
                                 idiomatic — NOT a literal word-for-word render>,
    "source_language_detected": <ISO-639-1 of the input language>,
    "needs_translation":        <true if source != target, false if same>
  }

RULES:
  * The translated topic must be a NATURAL story-title style phrase in
    TARGET_LANG, the way a native speaker would word the same idea —
    NOT word-for-word literal.
  * Preserve proper nouns (names, places, brands) verbatim where possible.
  * Keep the same emotional register (lyrical → lyrical, factual → factual).
  * If TARGET_LANG matches the input language exactly, set
    needs_translation=false and pass the input through unchanged
    (still inside translated_topic).
  * Output JSON ONLY. No markdown fences, no comments, no explanations.
"""


async def translate_topic(
    topic: str,
    target_lang: str,
    *,
    source_lang_hint: str | None = None,
    client: genai.Client | None = None,
    model: str = DEFAULT_MODEL_TRANSLATE,
) -> Translation:
    """Translate ``topic`` into ``target_lang``, returning a :class:`Translation`.

    Args:
        topic:            Free-text topic, any language (5-50 words ideally).
        target_lang:      ISO-639-1 of the target language, one of
                          :data:`SUPPORTED_LANGS`.
        source_lang_hint: Optional ISO-639-1 hint. Useful when the user
                          already knows their input language and wants to
                          skip Gemini's auto-detect (saves a few tokens
                          and avoids edge-case misdetections).
        client:           Inject a pre-built ``genai.Client`` for tests.
        model:            Override Gemini model. Defaults to
                          ``gemini-flash-lite-latest``.

    Returns:
        :class:`Translation`. Even when ``needs_translation=False``, the
        ``translated_topic`` is populated so callers can use it as the
        canonical topic going forward.

    Raises:
        ValueError: when ``target_lang`` is not supported, or all retries
            yield invalid JSON / missing fields.
    """
    target = (target_lang or "").strip().lower()
    if target not in SUPPORTED_LANGS:
        raise ValueError(
            f"target_lang={target_lang!r} not in supported set {SUPPORTED_LANGS!r}"
        )

    topic_norm = (topic or "").strip()
    if not topic_norm:
        raise ValueError("topic is empty")

    client = client or get_client()

    user_prompt_parts = [
        f"TARGET_LANG: {target} ({_LANG_NAMES[target]})",
    ]
    if source_lang_hint:
        hint = source_lang_hint.strip().lower()
        hint_name = _LANG_NAMES.get(hint, hint)
        user_prompt_parts.append(
            f"SOURCE_LANG_HINT: {hint} ({hint_name}) — skip detection if confident."
        )
    user_prompt_parts.append(f"\nTOPIC:\n{topic_norm}")
    user_prompt = "\n".join(user_prompt_parts)

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
                    temperature=0.2 if attempt == 1 else 0.4,
                    max_output_tokens=512,
                ),
            )
        except Exception as exc:
            last_err = exc
            logger.warning(
                "translate_topic attempt %d Gemini error %s: %s",
                attempt, type(exc).__name__, exc,
            )
            continue

        raw = (resp.text or "").strip()
        try:
            data = parse_json_loose(raw)
        except json.JSONDecodeError as exc:
            last_err = exc
            logger.warning(
                "translate_topic attempt %d JSON parse failed: %s — raw[:200]=%r",
                attempt, exc, raw[:200],
            )
            continue

        if not isinstance(data, dict):
            last_err = ValueError(f"expected JSON object, got {type(data).__name__}")
            continue

        translated = (data.get("translated_topic") or "").strip()
        detected = (data.get("source_language_detected") or "").strip().lower()
        needs = bool(data.get("needs_translation"))

        if not translated:
            last_err = ValueError("translated_topic missing or empty")
            logger.warning(
                "translate_topic attempt %d: empty translated_topic in %r",
                attempt, data,
            )
            continue

        # Normalize: if Gemini admits source==target but flag is True, trust target.
        if detected == target:
            needs = False

        logger.info(
            "translated %r (%s) → %r (%s, needs_translation=%s)",
            topic_norm[:60], detected or "auto", translated[:60], target, needs,
        )
        return Translation(
            translated_topic=translated,
            target_language=target,
            source_language_detected=detected or "auto",
            needs_translation=needs,
        )

    assert last_err is not None
    raise ValueError(
        f"translate_topic failed after {MAX_ATTEMPTS} attempts: {last_err}"
    )


def translate(topic: str, target_lang: str, **kwargs) -> Translation:
    """Sync wrapper for CLI / smoke tests."""
    return asyncio.run(translate_topic(topic, target_lang, **kwargs))


__all__ = [
    "MAX_ATTEMPTS",
    "SUPPORTED_LANGS",
    "Translation",
    "translate",
    "translate_topic",
]
