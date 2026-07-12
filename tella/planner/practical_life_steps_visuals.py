"""Deterministic visual identity for the practical life-steps recipe."""
from __future__ import annotations

from tella.planner.models import TellaScenePlan


_ROLE_VARIANTS = {
    "hook": "specific_problem_focus",
    "context": "cause_and_distraction_map",
    "context_part_one": "first_context_cause",
    "context_part_two": "second_context_cause",
    "practical_step": "validated_practical_action",
    "common_mistake": "repeated_mistake_pattern",
    "correction": "visible_course_correction",
    "today_action": "small_action_started_today",
    "closing": "completed_practical_action",
}
_ROLE_COMPOSITIONS = {
    "hook": ("centered problem-and-person composition",),
    "context": ("left-right cause-and-effect composition",),
    "context_part_one": ("left-weighted context composition",),
    "context_part_two": ("right-weighted context composition",),
    "common_mistake": ("split repeated-action composition",),
    "correction": ("clear before-and-after composition",),
    "today_action": ("open-space forward-action composition",),
    "closing": ("calm completed-action composition",),
}
_STEP_COMPOSITIONS = {
    1: "character-and-object composition with action moving left to right",
    2: "character-and-object composition with action moving right to left",
    3: "organized desk composition with a centered practical action",
}


def build_practical_provider_prompt(scene) -> str:
    """Build one compact positive prompt from validated planner metadata."""
    composition = scene.composition_pattern or "centered practical composition"
    return " ".join(
        (
            "Minimalist hand-drawn symbolic editorial illustration.",
            f"{scene.visual_action}.",
            f"{scene.visual_environment}.",
            f"{composition}.",
            "Bright warm off-white pale sage-gray background.",
            "Muted teal and slate-green forms with soft coral and warm yellow accents.",
            "Charcoal line art, simple readable adult figure, clean practical atmosphere.",
            "Controlled negative space, calm encouraging mood, wordless unbranded artwork.",
        )
    )


def apply_practical_life_steps_visuals(plan: TellaScenePlan) -> TellaScenePlan:
    if plan.theme != "practical_life_steps":
        return plan

    plan.subtitle_style = "practical_steps_reel"
    plan.visual_identity_id = "practical_life_steps_v1"
    plan.palette_id = "pale_sage_teal_coral_v1"
    plan.line_style_id = "clean_charcoal_practical_v1"
    plan.age_policy = "adult figures only"
    plan.cast_archetype_set = ["adult_woman", "adult_man"]

    previous_composition = ""
    for scene in (item for item in plan.scenes if item.kind == "scene"):
        if scene.scene_role == "practical_step":
            composition = _STEP_COMPOSITIONS.get(
                scene.step_number,
                "character-and-object practical action composition",
            )
        else:
            candidates = _ROLE_COMPOSITIONS.get(
                scene.scene_role,
                ("centered practical composition", "left-right practical composition"),
            )
            composition = next(
                (candidate for candidate in candidates if candidate != previous_composition),
                candidates[0],
            )

        scene.visual_identity_id = plan.visual_identity_id
        scene.palette_id = plan.palette_id
        scene.line_style_id = plan.line_style_id
        scene.age_policy = plan.age_policy
        scene.cast_archetype = "adult_woman_or_man"
        scene.character_archetype = "adult_woman_or_man"
        scene.visual_variant_id = _ROLE_VARIANTS.get(
            scene.scene_role,
            "practical_action",
        )
        scene.composition_pattern = composition
        scene.composition_hint = composition
        scene.frame_safety_hint = (
            "main action inside the central safe area, upper-left badge area clear, "
            "lower caption region visually quiet"
        )
        scene.visual_mode = "practical_life_steps"
        scene.scene_setting = "practical_editorial_space"
        scene.scene_action = scene.action_verb or scene.scene_role
        scene.provider_prompt_variant = build_practical_provider_prompt(scene)
        scene.image_prompt = scene.provider_prompt_variant
        scene.stock_query = "practical adult action illustration"
        if scene.scene_role == "practical_step" and scene.action_verb:
            scene.subtitle_highlight_words = [scene.action_verb]
        else:
            scene.subtitle_highlight_words = list(scene.subtitle_highlight_words[:2])
        previous_composition = composition
    return plan


__all__ = [
    "apply_practical_life_steps_visuals",
    "build_practical_provider_prompt",
]
