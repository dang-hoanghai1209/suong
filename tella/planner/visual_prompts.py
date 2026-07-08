"""Prompt builders for reference-mode visual generation."""
from __future__ import annotations

from tella.planner.models import (
    CharacterReference,
    CharacterSpec,
    Scene,
    SceneVisualPlan,
    VisualBible,
)

_ROOM_SCENE_COMPOSITION_LOCK = (
    "complete emotional illustration scene, not a character portrait, medium-wide "
    "vertical bedroom shot, character about 35-45 percent of frame height, "
    "negative space around her, layered composition: foreground curtain edge or "
    "soft shadow, middle ground young woman, background bed, window with thin "
    "curtains, bedside table, warm lamp, books or folded blanket, soft wall "
    "shadows, subtle dust near window, muted floor and wall shapes"
)


def build_reference_prompt(character: CharacterSpec, visual_bible: VisualBible, variant: str) -> str:
    style = visual_bible.style_bible
    return _join_prompt(
        [
            "character reference sheet image for one recurring video character",
            variant,
            _character_identity_prompt(character),
            "plain warm beige background, complete character visible, head and feet fully visible",
            "neutral readable pose, no text, no watermark, exactly one character",
            style.art_style_prompt,
            style.palette_prompt,
            style.linework_prompt,
            style.rendering_prompt,
        ]
    )


def build_scene_visual_plan(
    scene: Scene,
    visual_bible: VisualBible,
    references: list[CharacterReference],
    *,
    previous_scene_reference_path: str = "",
) -> SceneVisualPlan:
    character = visual_bible.character_specs[0]
    emotion = scene.emotion_tag or _emotion_for_scene(scene)
    action = _scene_action(scene)
    refs = [r for r in references if r.selected and r.character_id == character.character_id]
    prompt = _join_prompt(
        [
            visual_bible.style_bible.art_style_prompt,
            visual_bible.style_bible.palette_prompt,
            visual_bible.style_bible.linework_prompt,
            visual_bible.style_bible.rendering_prompt,
            visual_bible.style_bible.composition_prompt,
            visual_bible.style_bible.background_prompt,
            _ROOM_SCENE_COMPOSITION_LOCK,
            _anatomy_prompt_hints(scene),
            _character_identity_prompt(character),
            "maintain the same character identity as the generated reference images",
            ", ".join(character.identity_lock_phrases),
            f"scene emotion: {emotion}",
            f"scene action: {action}",
            f"scene title: {scene.title}",
            f"scene narration: {scene.voice_script}",
            f"primary motif or prop: {scene.primary_motif or 'small symbolic emotional prop'}",
            "complete character visible, character in central safe area, bottom caption lane remains visually calm",
            "one main character only unless explicitly requested",
        ]
    )
    negative = _join_prompt(
        [
            visual_bible.global_negative_prompt,
            visual_bible.style_bible.negative_prompt,
            ", ".join(character.negative_identity_phrases),
        ]
    )
    return SceneVisualPlan(
        scene_index=scene.scene_index,
        visual_prompt=prompt,
        character_ids=[character.character_id],
        character_reference_ids=[r.reference_id for r in refs],
        previous_scene_reference_path=previous_scene_reference_path,
        action=action,
        emotion_tag=emotion,
        pose_action_description=action,
        location=", ".join(visual_bible.environment_locks),
        props=[scene.primary_motif] if scene.primary_motif else [],
        continuity_notes="; ".join(visual_bible.continuity_rules),
        negative_prompt=negative,
        expected_character_count=1,
        expected_object_count=1 if scene.primary_motif else 0,
    )


def repair_prompt(base_prompt: str, failure_reasons: list[str]) -> str:
    reason_text = "; ".join(failure_reasons) if failure_reasons else "basic QC failed"
    return _join_prompt(
        [
            base_prompt,
            f"Regenerate the same scene and repair these issues: {reason_text}.",
            "Keep the same character hairstyle, outfit, face shape, palette, and style.",
            "Complete character visible, medium-wide room composition, character about 35-45 percent of frame height.",
            "Preserve the structured shot/body/pose requirements from the original prompt.",
            "No extra limbs. No duplicate head. No text or watermark.",
        ]
    )


def _character_identity_prompt(character: CharacterSpec) -> str:
    pieces = [
        f"same {character.gender_or_presentation or 'young woman'} character",
        character.age_style,
        character.body_style,
        character.hair,
        character.face,
        character.outfit,
        character.palette,
        character.consistency_notes,
    ]
    if character.accessories:
        pieces.append("accessories: " + ", ".join(character.accessories))
    return _join_prompt(pieces)


def _scene_action(scene: Scene) -> str:
    text = " ".join([scene.title or "", scene.voice_script or ""]).strip()
    if scene.composition_hint:
        text = f"{text}. composition hint: {scene.composition_hint}"
    if scene.frame_safety_hint:
        text = f"{text}. frame safety: {scene.frame_safety_hint}"
    return text or "quiet emotional moment"


def _anatomy_prompt_hints(scene: Scene) -> str:
    parts = []
    if scene.shot_type:
        parts.append(f"structured shot type: {scene.shot_type}")
    if scene.body_visibility:
        parts.append(f"body visibility requirement: {scene.body_visibility}")
    if scene.pose_type:
        parts.append(f"pose type: {scene.pose_type}")
    if scene.anatomy_expectation:
        parts.append(scene.anatomy_expectation)
    parts.append("one head, one face, one torso, no extra limbs, no duplicated body parts")
    if scene.pose_type == "sitting":
        parts.append(
            "clear simple sitting pose, lower body naturally placed, no overlapping extra legs, no duplicated feet, no tangled limbs"
        )
    if scene.body_visibility == "full_body":
        parts.append("full body visible, exactly two legs and two feet, clear simple anatomy, no extra limbs")
    if scene.pose_type == "walking":
        parts.append("clear side or three-quarter walking pose, exactly two legs, no duplicate stride legs, no extra feet")
    if scene.shot_type in {"close_up", "medium"} or scene.body_visibility in {"upper_body", "waist_up"}:
        parts.append("waist-up framing if cropped, lower body not visible, avoid ambiguous partial legs")
    if scene.shot_type in {"medium", "medium_wide", "wide"}:
        parts.append("do not crop required body parts in the medium-wide room composition")
    return ", ".join(parts)


def _emotion_for_scene(scene: Scene) -> str:
    text = " ".join([scene.title or "", scene.voice_script or ""]).lower()
    checks = [
        ("self_kindness", ("kind", "accept", "love", "thương", "dịu")),
        ("tired", ("tired", "mệt", "exhaust", "weary", "kiệt")),
        ("sadness", ("sad", "buồn", "hurt", "pain", "đau")),
        ("loneliness", ("alone", "lonely", "một mình", "empty")),
        ("reflection", ("think", "reflect", "nhìn lại", "quiet", "im lặng")),
        ("trying_again", ("again", "start", "bắt đầu", "try")),
        ("hope", ("hope", "light", "sáng", "hy vọng")),
        ("healing", ("heal", "chữa", "peace", "bình yên")),
        ("relief", ("relief", "relax", "thở", "nhẹ")),
    ]
    for tag, words in checks:
        if any(word in text for word in words):
            return tag
    return scene.emotion_tag or "calm"


def _join_prompt(parts: list[str]) -> str:
    return ", ".join(p.strip(" ,") for p in parts if p and p.strip(" ,"))


__all__ = ["build_reference_prompt", "build_scene_visual_plan", "repair_prompt"]
