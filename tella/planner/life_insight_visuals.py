"""Deterministic visual identity for the life-insight symbolic recipe."""
from __future__ import annotations

from dataclasses import dataclass

from tella.planner.models import TellaScenePlan


@dataclass(frozen=True)
class SymbolicCatalogItem:
    catalog_id: str
    visual: str
    primary_object: str
    secondary_object: str = ""


LIFE_INSIGHT_SYMBOLIC_CATALOG: dict[str, SymbolicCatalogItem] = {
    "locked_question": SymbolicCatalogItem(
        "locked_question",
        "one adult studying a small locked box beside an unanswered doorway",
        "locked box",
        "unanswered doorway",
    ),
    "interpreting_signals": SymbolicCatalogItem(
        "interpreting_signals",
        "one adult sorting small signal cards beside gently tangled message threads",
        "signal cards",
        "tangled message threads",
    ),
    "contrasting_behavior": SymbolicCatalogItem(
        "contrasting_behavior",
        "two simple adult figures showing clearly different patterns of presence",
        "two contrasting adult figures",
    ),
    "revealed_truth": SymbolicCatalogItem(
        "revealed_truth",
        "an open book revealing a clear path and a distant empty chair",
        "open book",
        "empty chair",
    ),
    "messages_and_thoughts": SymbolicCatalogItem(
        "messages_and_thoughts",
        "one adult comparing orderly message cards with a loose tangle of thought lines",
        "message cards",
        "tangled thought lines",
    ),
    "time_and_priority": SymbolicCatalogItem(
        "time_and_priority",
        "one adult placing a warm marker on a calendar beside a simple clock",
        "calendar",
        "clock",
    ),
    "uncertain_position": SymbolicCatalogItem(
        "uncertain_position",
        "one adult pausing at a readable maze with one clear exit path",
        "maze",
        "clear path",
    ),
    "disappearing_presence": SymbolicCatalogItem(
        "disappearing_presence",
        "one present adult facing an empty chair beyond a resolved task",
        "empty chair",
        "completed task",
    ),
    "boundary_path_scale": SymbolicCatalogItem(
        "boundary_path_scale",
        "one adult beside an open boundary door, a balanced scale, and a clear path",
        "open boundary door",
        "balanced scale",
    ),
    "mirror_standard": SymbolicCatalogItem(
        "mirror_standard",
        "one adult comparing a clear reflection with two uneven behavior markers",
        "mirror",
        "behavior markers",
    ),
}


_ROLE_CATALOGS: dict[str, tuple[str, ...]] = {
    "hook": ("locked_question", "uncertain_position"),
    "behavior": ("interpreting_signals", "messages_and_thoughts"),
    "false_belief": ("mirror_standard", "messages_and_thoughts"),
    "underlying_truth": ("revealed_truth", "contrasting_behavior"),
    "concrete_sign": ("disappearing_presence", "time_and_priority"),
    "underlying_truth_and_concrete_sign": (
        "contrasting_behavior",
        "disappearing_presence",
    ),
    "consequence": ("uncertain_position", "boundary_path_scale"),
    "mature_perspective": ("time_and_priority", "mirror_standard"),
    "conclusion": ("boundary_path_scale", "revealed_truth"),
}

LIFE_INSIGHT_COMPOSITIONS = (
    "centered symbolic composition",
    "character with object composition",
    "left-right relationship composition",
    "two-person contrast composition",
    "foreground subject with distant absence",
    "empty-space conclusion composition",
)

_VISUAL_IDENTITY_ID = "life_insight_symbolic_v1"
_PALETTE_ID = "blue_gray_charcoal_amber_v1"
_LINE_STYLE_ID = "clean_charcoal_editorial_v1"
_AGE_POLICY = "adult figures only"

_ROLE_COMPOSITIONS: dict[str, tuple[str, ...]] = {
    "hook": ("centered symbolic composition", "character with object composition"),
    "behavior": ("character with object composition", "left-right relationship composition"),
    "false_belief": ("left-right relationship composition", "character with object composition"),
    "underlying_truth": ("centered symbolic composition", "two-person contrast composition"),
    "concrete_sign": ("foreground subject with distant absence", "left-right relationship composition"),
    "underlying_truth_and_concrete_sign": (
        "two-person contrast composition",
        "foreground subject with distant absence",
    ),
    "consequence": ("character with object composition", "centered symbolic composition"),
    "mature_perspective": ("left-right relationship composition", "centered symbolic composition"),
    "conclusion": ("empty-space conclusion composition",),
}


def _select_non_repeating(
    candidates: tuple[str, ...],
    *,
    scene_index: int,
    previous: str,
) -> str:
    start = (max(1, scene_index) - 1) % len(candidates)
    for offset in range(len(candidates)):
        candidate = candidates[(start + offset) % len(candidates)]
        if candidate != previous:
            return candidate
    return candidates[start]


def build_life_insight_provider_prompt(scene) -> str:
    """Return one compact, positive-only Cloudflare prompt."""
    visual = (scene.symbolic_visual or scene.main_character_or_object).strip()
    composition = (scene.composition_pattern or "centered symbolic composition").strip()
    return " ".join(
        (
            "Hand-drawn symbolic editorial illustration.",
            f"{visual}.",
            f"{composition}.",
            "Muted blue-gray charcoal background with soft warm amber accents.",
            "Simple readable adult figures, clean charcoal lines, medium contrast.",
            "Clear symbolic action, controlled negative space, calm mature mood.",
            "Wordless unbranded artwork.",
        )
    )


def apply_life_insight_visuals(plan: TellaScenePlan) -> TellaScenePlan:
    if plan.theme != "life_insight_symbolic":
        return plan

    plan.subtitle_style = "insight_reel"
    plan.visual_identity_id = _VISUAL_IDENTITY_ID
    plan.palette_id = _PALETTE_ID
    plan.line_style_id = _LINE_STYLE_ID
    plan.age_policy = _AGE_POLICY
    plan.cast_archetype_set = ["adult_woman", "adult_man"]
    previous_catalog = ""
    previous_composition = ""
    for scene in (item for item in plan.scenes if item.kind == "scene"):
        catalogs = _ROLE_CATALOGS.get(
            scene.scene_role,
            ("boundary_path_scale", "interpreting_signals"),
        )
        catalog_id = _select_non_repeating(
            catalogs,
            scene_index=scene.scene_index,
            previous=previous_catalog,
        )
        compositions = _ROLE_COMPOSITIONS.get(
            scene.scene_role,
            LIFE_INSIGHT_COMPOSITIONS,
        )
        composition = _select_non_repeating(
            compositions,
            scene_index=scene.scene_index,
            previous=previous_composition,
        )
        item = LIFE_INSIGHT_SYMBOLIC_CATALOG[catalog_id]

        scene.symbolic_visual = item.visual
        scene.main_character_or_object = item.primary_object
        scene.primary_object = item.primary_object
        scene.secondary_object = item.secondary_object
        scene.composition_pattern = composition
        scene.composition_hint = composition
        scene.environment = "muted blue-gray charcoal symbolic space"
        scene.visual_mode = "life_insight_symbolic"
        scene.visual_identity_id = _VISUAL_IDENTITY_ID
        scene.palette_id = _PALETTE_ID
        scene.line_style_id = _LINE_STYLE_ID
        scene.age_policy = _AGE_POLICY
        scene.cast_archetype = "adult_woman_or_man"
        scene.character_archetype = "adult_woman_or_man"
        scene.visual_variant_id = catalog_id
        scene.scene_setting = "symbolic_editorial_space"
        scene.scene_action = scene.scene_role
        scene.frame_safety_hint = (
            "main symbol within the central safe area, controlled negative space, "
            "lower caption region visually quiet"
        )
        scene.image_prompt = build_life_insight_provider_prompt(scene)
        scene.provider_prompt_variant = scene.image_prompt
        scene.stock_query = f"symbolic {item.primary_object} illustration"
        scene.subtitle_highlight_words = list(scene.subtitle_highlight_words[:2])

        previous_catalog = catalog_id
        previous_composition = composition
    return plan


__all__ = [
    "LIFE_INSIGHT_COMPOSITIONS",
    "LIFE_INSIGHT_SYMBOLIC_CATALOG",
    "SymbolicCatalogItem",
    "apply_life_insight_visuals",
    "build_life_insight_provider_prompt",
]
