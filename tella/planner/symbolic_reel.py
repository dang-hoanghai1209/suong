"""Deterministic repairs for the minimalist_symbolic_reel theme."""
from __future__ import annotations

import re
import unicodedata

from tella.planner.models import TellaScenePlan

_SYMBOLIC_STYLE = (
    "minimalist hand-drawn emotional doodle illustration, dark warm taupe "
    "background, warm dusk-like muted brown-gray backdrop, soft low-key ambient "
    "light, simple expressive character or symbolic object, soft rough pencil "
    "lines, flat muted earthy colors, consistent earthy palette, centered "
    "composition, lots of negative space, low visual clutter, stronger emotional "
    "depth, clear tonal contrast, gentle melancholic mood, not black, not cold "
    "gray, no text, no watermark, no realistic rendering, no 3D, no anime, no "
    "complex background"
)

_SYMBOLIC_VISUALS = (
    "small paper heart with one soft crack",
    "tiny glowing dot beside a gray cloud",
    "single empty chair drawn as a soft outline",
    "mustard thread untangling into a loose circle",
    "small red seed under a transparent glass dome",
    "simple figure standing beside a low gray shadow",
    "folded note with a muted red corner",
    "small sprout growing from a thin pencil line",
)

_METAPHORS = (
    "a feeling that is still tender but no longer hidden",
    "a small hope staying alive inside tired days",
    "the quiet space left by someone absent",
    "a knot of worry becoming easier to hold",
    "care returning slowly in a protected place",
    "sadness becoming a soft shape outside the self",
    "words kept gently instead of carried heavily",
    "new calm growing from a very small beginning",
)

_STOPWORDS = {
    "a", "an", "and", "the", "to", "of", "in", "on", "with", "for", "but",
    "is", "are", "was", "were", "she", "he", "her", "him", "you", "your",
    "co", "ay", "mot", "minh", "va", "la", "da", "duoc", "trong", "nhung",
    "ngay", "hon", "khong", "cho", "roi", "that", "this", "still",
}

_PHRASE_HIGHLIGHTS = (
    "m\u1ed9t m\u00ecnh",
    "im l\u1eb7ng",
    "bu\u00f4ng xu\u1ed1ng",
    "kh\u00f4ng n\u00f3i ra",
)

_SETTING_TERMS = {
    "bedroom": "quiet bedroom suggested by the script",
    "bed": "quiet bedroom suggested by the script",
    "window": "simple window shape requested by the script",
    "curtain": "simple curtain shape requested by the script",
    "room": "quiet room requested by the script",
    "phong": "quiet room requested by the script",
    "giuong": "quiet bedroom suggested by the script",
    "cua so": "simple window shape requested by the script",
}


def enforce_symbolic_reel_plan(plan: TellaScenePlan) -> None:
    """Stamp symbolic scene metadata and safe plain-background prompts."""
    if plan.theme != "minimalist_symbolic_reel":
        return

    plan.subtitle_style = "reel_minimal"
    for idx, scene in enumerate((s for s in plan.scenes if s.kind == "scene"), start=1):
        seed_text = " ".join(
            p for p in (scene.title, scene.voice_script, scene.scene_meaning) if p
        ).strip()
        scene.scene_meaning = scene.scene_meaning or _meaning_from_text(seed_text)
        scene.symbolic_visual = scene.symbolic_visual or _SYMBOLIC_VISUALS[(idx - 1) % len(_SYMBOLIC_VISUALS)]
        scene.emotional_metaphor = scene.emotional_metaphor or _METAPHORS[(idx - 1) % len(_METAPHORS)]
        scene.main_character_or_object = scene.main_character_or_object or scene.symbolic_visual
        scene.subtitle_highlight_words = scene.subtitle_highlight_words or _highlight_words(scene.voice_script)
        scene.visual_mode = "symbolic_listicle"
        scene.image_prompt = _symbolic_prompt(scene)
        scene.stock_query = scene.stock_query or "symbolic emotional doodle"
        scene.character_names = []
        scene.requested_characters = []
        scene.required_characters = []


def _symbolic_prompt(scene) -> str:
    explicit_setting = _explicit_setting_phrase(
        " ".join([scene.title or "", scene.voice_script or "", scene.scene_meaning or ""])
    )
    parts = [
        _SYMBOLIC_STYLE,
        f"scene meaning: {scene.scene_meaning}",
        f"symbolic visual: {scene.symbolic_visual}",
        f"emotional metaphor: {scene.emotional_metaphor}",
        f"main character or object: {scene.main_character_or_object}",
    ]
    if explicit_setting:
        parts.append(explicit_setting)
    parts.append(
        "very limited background detail, plain symbolic composition, no multiple unnecessary characters"
    )
    return ", ".join(p.strip(" ,") for p in parts if p and p.strip(" ,"))


def _meaning_from_text(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return "one quiet emotional idea"
    return text[:260].rstrip(" ,.;:")


def _highlight_words(text: str, limit: int = 3) -> list[str]:
    highlights: list[str] = []
    text_key = f" {_ascii_key(text)} "
    for phrase in _PHRASE_HIGHLIGHTS:
        phrase_key = _ascii_key(phrase)
        if phrase_key and f" {phrase_key} " in text_key:
            highlights.append(phrase)
            if len(highlights) >= limit:
                return highlights

    words = re.findall(r"[\w\u00c0-\u1ef9]+", text or "", flags=re.UNICODE)
    scored: list[str] = []
    seen: set[str] = set()
    seen.update(_ascii_key(item) for item in highlights)
    for word in words:
        key = _ascii_key(word)
        if len(key) < 3 or key in _STOPWORDS or key in seen:
            continue
        seen.add(key)
        scored.append(word)
    return [*highlights, *scored[: max(0, limit - len(highlights))]]


def _explicit_setting_phrase(text: str) -> str:
    key = _ascii_key(text)
    for term, phrase in _SETTING_TERMS.items():
        if f" {_ascii_key(term)} " in f" {key} ":
            return phrase
    return ""


def _ascii_key(text: str) -> str:
    raw = (text or "").casefold().replace("\u0111", "d")
    decomposed = unicodedata.normalize("NFKD", raw)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", ascii_only).strip()


__all__ = ["enforce_symbolic_reel_plan"]
