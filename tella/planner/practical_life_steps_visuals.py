"""Deterministic visual identity for the practical life-steps recipe."""
from __future__ import annotations

from tella.planner.models import TellaScenePlan
from tella.planner.practical_prompt_policy import build_priority_prompt
from tella.planner.practical_visual_profiles import PracticalVisualProfile
from tella.render.subtitle_layout import PRACTICAL_DYNAMIC_SUBTITLE_POLICY


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


def _apply_explicit_visual_profile(
    plan: TellaScenePlan, profile: PracticalVisualProfile
) -> TellaScenePlan:
    scenes = [scene for scene in plan.scenes if scene.kind == "scene"]
    if len(scenes) != len(profile.scenes):
        raise ValueError(
            f"visual profile scene count mismatch: expected {len(scenes)}, "
            f"received {len(profile.scenes)}"
        )
    actual_roles = tuple(scene.scene_role for scene in scenes)
    profile_roles = tuple(item.scene_role for item in profile.scenes)
    if actual_roles != profile_roles:
        raise ValueError(
            f"visual profile scene-role order mismatch: expected {actual_roles}, "
            f"received {profile_roles}"
        )

    plan.character_fingerprint = profile.character_fingerprint
    plan.canonical_character_spec = dict(profile.canonical_character_spec)
    plan.identity_continuity_strategy = profile.identity_continuity_strategy
    plan.identity_acceptance_standard = profile.identity_acceptance_standard
    plan.identity_mode = profile.identity_mode
    plan.subtitle_layout_policy_id = profile.subtitle_layout_policy_id
    plan.cast_archetype_set = [profile.cast_archetype]

    global_negatives = " ".join(profile.global_hard_negatives)
    for scene, spec in zip(scenes, profile.scenes):
        scene.scene_setting = spec.setting
        scene.scene_action = spec.primary_action
        scene.body_pose = spec.body_pose
        scene.pose_family = spec.body_pose[:60]
        scene.camera_framing = spec.camera_framing
        scene.framing = spec.camera_framing
        scene.character_placement = spec.character_placement
        scene.primary_prop = spec.primary_prop
        scene.primary_object = spec.primary_prop
        scene.secondary_props = list(spec.secondary_props)
        scene.secondary_object = ", ".join(spec.secondary_props)
        scene.emotional_state = spec.emotional_state
        scene.emotion_tag = spec.emotional_state[:60]
        scene.composition_family = spec.composition_family
        scene.composition_pattern = spec.composition_family
        scene.composition_hint = f"{spec.camera_framing}; {spec.character_placement}"
        scene.character_count = 1
        scene.character_fingerprint = profile.character_fingerprint
        scene.character_required_view = spec.camera_framing
        scene.permitted_pose_variation = spec.body_pose
        scene.identity_invariants = list(profile.identity_invariants)
        scene.forbidden_identity_changes = list(profile.forbidden_identity_changes)
        scene.subtitle_safe_lower_fraction = spec.subtitle_safe_lower_fraction
        scene.subtitle_layout_policy_id = profile.subtitle_layout_policy_id
        scene.planning_overlay_strategy = spec.planning_overlay_strategy
        scene.cast_archetype = profile.cast_archetype
        scene.character_archetype = profile.cast_archetype
        scene.visual_variant_id = f"{profile.profile_id}_scene_{scene.scene_index}"
        scene.visual_action = spec.primary_action
        scene.visual_environment = spec.setting
        scene.symbolic_qc_expectations = list(spec.symbolic_qc_expectations)
        scene.frame_safety_hint = (
            "full head and required hands, props, and action inside frame; "
            "subtitle layout is renderer-owned"
        )
        hard_negatives = " ".join((global_negatives, *spec.semantic_hard_negatives))
        sections = {
            "action_setting": (
                f"Required action and setting: {spec.setting}; {spec.primary_action}. "
                f"Emotional state: {spec.emotional_state}."
            ),
            "required_props": (
                f"Required primary prop: {spec.primary_prop}. Required secondary props: "
                f"{', '.join(spec.secondary_props)}."
            ),
            "character_identity": profile.character_identity_prompt,
            "composition": (
                f"Composition: {spec.body_pose}; {spec.camera_framing}; "
                f"{spec.character_placement}. Keep the head, required hands, action, "
                "and props fully visible."
            ),
            "style": profile.style_instruction,
            "hard_negatives": hard_negatives,
        }
        prompt = build_priority_prompt(sections)
        scene.provider_prompt_variant = prompt
        scene.image_prompt = prompt
    return plan


def apply_practical_life_steps_visuals(
    plan: TellaScenePlan,
    *,
    visual_profile: PracticalVisualProfile | None = None,
) -> TellaScenePlan:
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
    return _apply_explicit_visual_profile(plan, visual_profile) if visual_profile else plan


__all__ = [
    "apply_practical_life_steps_visuals",
    "build_practical_provider_prompt",
]
