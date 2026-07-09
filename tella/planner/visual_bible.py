"""Build a job-scoped visual bible for reference-mode generation."""
from __future__ import annotations

import json
import re
from pathlib import Path

from tella.planner.models import (
    CharacterSpec,
    Scene,
    StyleBible,
    TellaScenePlan,
    VisualBible,
)


def build_visual_bible(plan: TellaScenePlan) -> VisualBible:
    """Create a deterministic visual bible from the planned story.

    This intentionally does not create permanent assets. It only records the
    per-job identity/style constraints used by reference-mode media generation.
    """
    source_text = _source_text(plan)
    character = _build_character_spec(source_text)
    character_specs = [character]
    secondary = plan.secondary_character or _find_secondary_character(plan)
    if secondary is not None:
        character_specs.append(
            _character_spec_from_brief(
                secondary,
                character_id="male_01",
                role="secondary_character",
                gender="young man",
            )
        )
    style = StyleBible(
        style_name="minimalist_emotional_reference",
        art_style_prompt=(
            "soft hand-drawn gentle illustration, calm adult "
            "character, simple expressive face, natural proportions, quiet "
            "melancholic mood, minimal symbolic props"
        ),
        palette_prompt=(
            "warm muted earthy colors, taupe and beige background, mustard "
            "brown clothing accents, soft rust details, low saturation"
        ),
        linework_prompt=(
            "thin imperfect dark brown linework, clean readable silhouette, "
            "minimal facial marks"
        ),
        rendering_prompt=(
            "flat color with subtle soft shading, no photorealism, no 3D, no "
            "heavy texture, no complex rendering"
        ),
        composition_prompt=(
            "vertical 9:16 medium-wide story-setting composition, complete character visible, "
            "do not make the character too large, character occupies about 35-45 "
            "percent of frame height, enough negative space around her for emotional "
            "atmosphere, bottom 25 percent calm enough for subtitles"
        ),
        background_prompt=(
            "complete quiet scene with soft environmental details matching the "
            "current narration setting: street, bakery, shop interior, room, or "
            "another explicitly requested place, soft shadows, subtle dust or "
            "memory particles, muted floor, wall, sidewalk, or shop shapes"
        ),
        motion_prompt="slow gentle visual rhythm, subtle zoom and soft dissolve transitions",
        negative_prompt=_global_negative_prompt(),
        aspect_ratio=plan.aspect_ratio,
        safety_margin_notes=(
            "keep head, feet, and main symbolic object away from video edges and "
            "away from the bottom caption lane"
        ),
    )
    return VisualBible(
        style_bible=style,
        character_specs=character_specs,
        environment_locks=[
            "setting must match each scene narration",
            "quiet street details when the scene is outside",
            "small warm bakery storefront when the scene notices the bakery",
            "bakery doorway when the scene enters or exits",
            "glass display counter with cakes when the scene chooses cake",
            "soft shadows in the current location",
            "subtle dust or memory particles in warm light",
            "muted floor, wall, sidewalk, or shop shapes",
            "consistent calm lighting",
        ],
        palette_locks=[
            "warm muted earthy palette",
            "mustard brown outfit",
            "dark bob hair",
            "soft rust and beige accents",
        ],
        composition_locks=[
            "one main character unless the scene explicitly asks otherwise",
            "complete character visible",
            "character occupies about 35-45 percent of frame height",
            "leave negative space around the character",
            "layered composition: foreground curtain edge or soft shadow",
            "middle ground: the young woman",
            "background: details from the current scene setting",
            "complete emotional illustration scene, not only a character portrait",
            "caption-safe lower area",
            "consistent scale across scenes",
        ],
        global_negative_prompt=_global_negative_prompt(),
        continuity_rules=[
            "same hairstyle in every scene",
            "same face shape in every scene",
            "same outfit and color palette in every scene",
            "same hand-drawn visual style in every scene",
            "do not add extra characters unless explicitly requested",
        ],
    )


def save_visual_bible(visual_bible: VisualBible, job_dir: Path) -> Path:
    out = Path(job_dir) / "visual_bible.json"
    out.write_text(
        json.dumps(visual_bible.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out


def _source_text(plan: TellaScenePlan) -> str:
    pieces = [plan.title]
    if plan.character_brief:
        pieces.append(plan.character_brief.identity)
    pieces.extend(scene.title for scene in plan.scenes)
    pieces.extend(scene.voice_script for scene in plan.scenes)
    pieces.extend(scene.image_prompt for scene in plan.scenes)
    return " ".join(str(p or "") for p in pieces).lower()


def _build_character_spec(text: str) -> CharacterSpec:
    hair = "short-to-medium dark bob hair"
    if _has_any(text, ("tóc bob", "bob hair", "short bob", "dark bob")):
        hair = "short-to-medium dark bob hair, consistent rounded bob silhouette"
    elif _has_any(text, ("tóc dài", "long hair")):
        hair = "dark shoulder-length hair, simple and consistent"

    outfit = "mustard brown simple coat or dress with soft rust sleeves"
    if _has_any(text, ("áo khoác nâu vàng", "brown yellow coat", "mustard coat")):
        outfit = "mustard yellow-brown simple coat, same coat in every scene"
    elif _has_any(text, ("váy", "dress")):
        outfit = "mustard brown simple dress, same dress in every scene"

    gender = "young woman"
    if _has_any(text, ("cô gái", "girl", "woman", "young woman")):
        gender = "young woman"

    return CharacterSpec(
        character_id="girl_01",
        role="main_character",
        gender_or_presentation=gender,
        age_style="young adult",
        body_style="natural slim simple proportions, complete figure readable",
        hair=hair,
        face="soft round face, small expressive eyes, subtle eyebrows, gentle emotional expression",
        outfit=outfit,
        palette="warm muted earthy colors, dark hair, mustard brown outfit, muted story-setting background",
        accessories=[],
        emotional_range=["tired", "sad", "calm", "gentle smile", "hopeful", "relieved"],
        identity_lock_phrases=[
            "same hairstyle in every scene",
            "same outfit in every scene",
            "same face shape in every scene",
            "same color palette in every scene",
            "same young woman character in every scene",
        ],
        negative_identity_phrases=[
            "no hairstyle changes",
            "no outfit changes",
            "no extra characters unless specified",
            "no missing hands or feet",
            "no malformed anatomy",
            "no duplicate head or duplicate face",
        ],
        consistency_notes=(
            "Use this as a job-scoped character bible. The character should feel "
            "like the same person across every scene even when pose and emotion change."
        ),
    )


def _find_secondary_character(plan: TellaScenePlan):
    for character in plan.characters:
        key = f"{character.name} {character.identity}".lower()
        if "man" in key or "male" in key or "boy" in key:
            return character
    return None


def _character_spec_from_brief(
    brief,
    *,
    character_id: str,
    role: str,
    gender: str,
) -> CharacterSpec:
    return CharacterSpec(
        character_id=character_id,
        role=role,
        gender_or_presentation=gender,
        age_style="young adult",
        body_style="natural simple proportions, complete figure readable",
        hair="short dark hair",
        face="soft understated face, calm distant expression",
        outfit="muted brown simple shirt and dark trousers",
        palette="warm muted earthy colors, dark hair, muted brown clothing",
        accessories=[],
        emotional_range=["distant", "quiet", "turned away", "leaving"],
        identity_lock_phrases=[
            brief.identity,
            "same young man character when requested",
            "same muted clothing",
        ],
        negative_identity_phrases=[
            "no extra characters beyond the requested cast",
            "no romantic hugging",
            "no wedding scene",
            "no malformed anatomy",
        ],
        consistency_notes="Secondary character appears only in scenes that explicitly request him.",
    )


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(re.search(re.escape(n), text, flags=re.IGNORECASE) for n in needles)


def _global_negative_prompt() -> str:
    return (
        "no text, no watermark, no logo, no extra characters, no duplicate "
        "face, no duplicate head, no missing head, no missing hands, no missing "
        "feet, no malformed anatomy, no distorted limbs, no cropped head, no "
        "cropped feet, no photorealism, no 3D render, no complex cluttered "
        "background, no harsh neon colors"
    )


__all__ = ["build_visual_bible", "save_visual_bible"]
