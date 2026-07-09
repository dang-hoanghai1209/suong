"""Deterministic planner adherence repairs.

These helpers run after Gemini planning and before character locking. They
only repair contract-level misses that are easy to detect locally: preserving
an exact user script, and preserving an explicitly requested two-character
minimalist emotional premise.
"""
from __future__ import annotations

import logging
import re
import unicodedata

from tella.planner.models import CharacterBrief, Scene, SettingBrief, TellaScenePlan

logger = logging.getLogger("tella.planner.adherence")

PRIMARY_NAME = "female protagonist"
SECONDARY_NAME = "male memory"

PRIMARY_IDENTITY = (
    "young Vietnamese woman, short straight black bob hair, mustard yellow "
    "simple dress with rust sleeves, soft melancholic face"
)
SECONDARY_IDENTITY = (
    "young Vietnamese man, short dark hair, muted brown shirt, distant posture, "
    "turned partly away from her"
)
DEFAULT_SETTING_LOCATION = (
    "quiet everyday emotional setting matching the story, with soft "
    "environmental details and warm muted light"
)


def enforce_minimalist_cast_adherence(plan: TellaScenePlan, source_text: str) -> bool:
    """Preserve an explicit male/female premise for minimalist emotional plans.

    Returns True when a secondary male character was required by the source
    topic/script and therefore stamped into the plan.
    """
    if plan.theme != "minimalist_emotional" or plan.media_source != "ai_image":
        return False
    if not _needs_male_memory_character(source_text, plan):
        _stamp_single_character_metadata(plan)
        return False

    original_cast_count = len(plan.characters)
    original_has_setting = plan.setting_brief is not None
    primary = _find_character(plan, "female") or _default_primary()
    secondary = _find_character(plan, "male") or _default_secondary()

    primary.name = primary.name or PRIMARY_NAME
    secondary.name = secondary.name or SECONDARY_NAME
    primary.role = "protagonist"
    secondary.role = "supporting"

    plan.primary_character = primary
    plan.secondary_character = secondary
    plan.character_brief = plan.character_brief or primary
    plan.characters = _dedupe_cast([primary, secondary, *plan.characters])
    if plan.setting_brief is None:
        plan.setting_brief = SettingBrief(
            location=DEFAULT_SETTING_LOCATION,
            era="timeless",
            mood="quiet",
            time_of_day="soft evening",
        )

    fallback_applied = original_cast_count < 2 or not original_has_setting
    two_character_scene_count = min(2, len([s for s in plan.scenes if s.kind == "scene"]))
    body_seen = 0
    for scene in plan.scenes:
        if scene.kind != "scene":
            continue
        body_seen += 1
        if body_seen <= two_character_scene_count:
            _stamp_two_character_scene(scene, primary.name, secondary.name, fallback_applied)
        else:
            _stamp_single_character_scene(scene, primary.name, fallback_applied)

    logger.info(
        "minimalist_emotional cast adherence: two-character premise detected; "
        "two-character scenes=%d fallback_applied=%s",
        two_character_scene_count,
        fallback_applied,
    )
    return True


def refresh_cast_prompt_metadata(plan: TellaScenePlan) -> None:
    """Refresh prompt_contains_secondary_character after prompt construction."""
    for scene in plan.scenes:
        prompt_key = _ascii_key(scene.image_prompt)
        contains_secondary = any(
            marker in prompt_key
            for marker in (
                "young vietnamese man",
                "male memory",
                "secondary male",
                "young man",
                "man standing apart",
                "man turns away",
                "man turned away",
            )
        )
        scene.prompt_contains_secondary_character = contains_secondary


def apply_exact_script_to_plan(plan: TellaScenePlan, script: str) -> None:
    """Replace planned narration with contiguous slices of the exact script."""
    chunks = split_exact_script(script)
    if not chunks:
        raise ValueError("exact script is empty")
    if len(chunks) < 3:
        chunks = _split_by_words(" ".join(chunks), 3)
    if len(chunks) > 40:
        chunks = _merge_chunks_to_limit(chunks, 40)

    existing = [s for s in plan.scenes if s.kind == "scene"]
    if not existing:
        existing = [
            Scene(
                scene_index=1,
                kind="scene",
                title="Scene",
                voice_script=chunks[0],
                image_prompt="quiet emotional minimalist illustration matching the script setting",
                stock_query="emotional illustration",
            )
        ]

    scenes: list[Scene] = []
    for idx, chunk in enumerate(chunks, start=1):
        template = existing[min(idx - 1, len(existing) - 1)].model_copy(deep=True)
        template.scene_index = idx
        template.kind = "scene"
        template.voice_script = chunk
        if not template.title:
            template.title = _title_from_text(chunk)
        if not template.image_prompt:
            template.image_prompt = "quiet emotional minimalist illustration matching the script setting"
        if not template.stock_query:
            template.stock_query = "emotional illustration"
        template.audio_filename = ""
        template.audio_duration = 0.0
        template.duration = 0.0
        template.start = 0.0
        scenes.append(template)
    plan.scenes = scenes

    expected = _normalize_script_for_compare(script)
    actual = _normalize_script_for_compare(" ".join(s.voice_script for s in scenes))
    if expected != actual:
        raise ValueError("exact script preservation failed after scene splitting")


def split_exact_script(script: str) -> list[str]:
    text = (script or "").strip()
    if not text:
        return []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) >= 3:
        return lines

    parts = [
        part.strip()
        for part in re.split(r"(?<=[.!?。！？…])\s+", text)
        if part.strip()
    ]
    if len(parts) >= 3:
        return parts
    return lines or parts or [text]


def _stamp_single_character_metadata(plan: TellaScenePlan) -> None:
    primary_name = (
        plan.primary_character.name
        if plan.primary_character and plan.primary_character.name
        else (plan.character_brief.name if plan.character_brief and plan.character_brief.name else PRIMARY_NAME)
    )
    for scene in plan.scenes:
        if scene.kind != "scene":
            continue
        if not scene.required_characters:
            scene.required_characters = ["female"]
        if not scene.requested_characters:
            scene.requested_characters = list(scene.required_characters)
        if not scene.character_names:
            scene.character_names = [primary_name]
        scene.cast_source = scene.cast_source or "planner"


def _stamp_two_character_scene(
    scene: Scene,
    primary_name: str,
    secondary_name: str,
    fallback_applied: bool,
) -> None:
    scene.character_names = [primary_name, secondary_name]
    scene.requested_characters = ["female", "male"]
    scene.required_characters = ["female", "male"]
    scene.cast_source = "topic_two_character_detected"
    scene.cast_fallback_applied = fallback_applied
    action = (scene.image_prompt or "").strip().rstrip(".,;")
    required_action = (
        "two characters in a quiet emotional setting matching the story, young "
        "Vietnamese woman in the middle ground, young Vietnamese man standing apart near the doorway "
        "or window, emotional distance between them, the man turns away or "
        "leaves, no romantic hugging, no wedding, no extra people"
    )
    if "young Vietnamese man" not in action and "young man" not in action.lower():
        scene.image_prompt = f"{action}, {required_action}" if action else required_action


def _stamp_single_character_scene(
    scene: Scene,
    primary_name: str,
    fallback_applied: bool,
) -> None:
    scene.character_names = [primary_name]
    scene.requested_characters = ["female"]
    scene.required_characters = ["female"]
    scene.cast_source = "topic_two_character_healing_scene"
    scene.cast_fallback_applied = fallback_applied
    prompt_key = _ascii_key(scene.image_prompt)
    if any(marker in prompt_key for marker in ("young man", "male memory", "boy", "man standing")):
        scene.image_prompt = (
            "young Vietnamese woman alone in a quiet emotional setting matching "
            "the story, gentle healing moment, warm muted light, generous negative space"
        )


def _needs_male_memory_character(source_text: str, plan: TellaScenePlan) -> bool:
    key = _ascii_key(
        " ".join(
            [source_text, plan.title]
            + [s.title for s in plan.scenes]
            + [s.voice_script for s in plan.scenes]
            + [s.image_prompt for s in plan.scenes]
        )
    )
    explicit_phrases = (
        "co ban nam va nu",
        "ban nam va nu",
        "nam va nu",
        "chang trai khong chon minh",
        "nguoi khong chon minh",
        "boy and girl",
        "male and female",
        "man and woman",
    )
    if any(phrase in key for phrase in explicit_phrases):
        return True
    has_male = any(marker in key for marker in ("chang trai", "ban nam", "nguoi con trai", "young man", "boy", "male"))
    has_female = any(marker in key for marker in ("co gai", "ban nu", "nguoi con gai", "young woman", "girl", "female"))
    return has_male and has_female


def _find_character(plan: TellaScenePlan, gender: str) -> CharacterBrief | None:
    markers = (
        ("woman", "female", "girl", "co gai", "ban nu")
        if gender == "female"
        else ("man", "male", "boy", "chang trai", "ban nam")
    )
    for character in [*plan.characters, plan.character_brief, plan.primary_character, plan.secondary_character]:
        if character is None:
            continue
        key = _ascii_key(f"{character.name} {character.identity}")
        if any(marker in key for marker in markers):
            return character.model_copy(deep=True)
    return None


def _default_primary() -> CharacterBrief:
    return CharacterBrief(name=PRIMARY_NAME, identity=PRIMARY_IDENTITY, role="protagonist")


def _default_secondary() -> CharacterBrief:
    return CharacterBrief(name=SECONDARY_NAME, identity=SECONDARY_IDENTITY, role="supporting")


def _dedupe_cast(cast: list[CharacterBrief]) -> list[CharacterBrief]:
    seen: set[str] = set()
    out: list[CharacterBrief] = []
    for character in cast:
        key = _ascii_key(character.name or character.identity)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(character)
    return out[:4]


def _ascii_key(text: str) -> str:
    raw = (text or "").casefold().replace("đ", "d")
    decomposed = unicodedata.normalize("NFKD", raw)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", ascii_only).strip()


def _normalize_script_for_compare(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _split_by_words(text: str, target_count: int) -> list[str]:
    words = text.split()
    if len(words) < target_count:
        return [text]
    chunks: list[str] = []
    for idx in range(target_count):
        start = round(idx * len(words) / target_count)
        end = round((idx + 1) * len(words) / target_count)
        chunks.append(" ".join(words[start:end]))
    return [chunk for chunk in chunks if chunk]


def _merge_chunks_to_limit(chunks: list[str], limit: int) -> list[str]:
    if len(chunks) <= limit:
        return chunks
    merged = [""] * limit
    for idx, chunk in enumerate(chunks):
        slot = int(idx * limit / len(chunks))
        merged[slot] = f"{merged[slot]} {chunk}".strip()
    return [chunk for chunk in merged if chunk]


def _title_from_text(text: str) -> str:
    words = text.split()
    return " ".join(words[:6])[:120] or "Scene"


__all__ = [
    "SECONDARY_NAME",
    "apply_exact_script_to_plan",
    "enforce_minimalist_cast_adherence",
    "refresh_cast_prompt_metadata",
    "split_exact_script",
]
