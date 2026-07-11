"""Deterministic visual realization selection for symbolic reels."""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from collections import Counter
from typing import Any, Callable

from tella.planner.models import TellaScenePlan


CHARACTER_ARCHETYPES: dict[str, dict[str, Any]] = {
    "one_quiet_person": {"prompt": "one quiet person", "count": 1},
    "two_contrasting_people": {"prompt": "two contrasting people", "count": 2},
    "isolated_person_plus_group": {
        "prompt": "one separated person and a visible group of three people",
        "count": 4,
    },
    "seated_person": {"prompt": "one seated person", "count": 1},
    "walking_person": {"prompt": "one walking person", "count": 1},
    "standing_person": {"prompt": "one standing person", "count": 1},
    "rear_view_person": {"prompt": "one person viewed from behind", "count": 1},
    "side_profile_person": {"prompt": "one person in side profile", "count": 1},
    "small_group": {"prompt": "a small group of ordinary people", "count": 3},
    "worker_plus_passers": {
        "prompt": "one working person with two people passing nearby",
        "count": 3,
    },
}

ACTIONS = {
    "walking_while_carrying": "walking while carrying",
    "pulling_heavy_item": "pulling a heavy item",
    "sitting_and_waiting": "sitting and waiting",
    "setting_something_down": "setting something down",
    "holding_object_close": "holding an object close",
    "reaching_toward": "reaching toward",
    "turning_away": "turning away",
    "watching_others_pass": "watching others pass",
    "standing_apart": "standing apart from a group",
    "releasing_something": "releasing something",
    "opening_hands": "opening both hands",
    "following_path": "following a simple path",
    "looking_through_window": "looking through a window",
    "standing_beneath_symbol": "standing beneath a large symbolic shape",
    "carrying_while_ignored": "carrying a visible load while others pass",
    "comparing_positions": "standing in a clear visual comparison",
}

SYMBOLIC_OBJECTS = {
    "irregular_stone": "an irregular heavy stone",
    "stacked_boxes": "a stack of boxes",
    "folded_paper": "a folded paper",
    "empty_chair": "an empty chair",
    "paper_heart": "a paper heart",
    "thread": "a loose thread",
    "small_bird": "a small bird",
    "closed_door": "a closed door",
    "open_window": "an open window",
    "dim_lamp": "a dim lamp",
    "umbrella": "an umbrella",
    "suitcase": "a heavy suitcase",
    "speech_bubbles": "empty speech bubbles",
    "measuring_marks": "unequal measuring marks",
    "balance_scale": "a simple balance scale",
    "shadow_cloud": "a large dark cloud",
    "scattered_papers": "a pile of papers",
    "small_flower": "a small flower",
    "path": "a simple path",
    "moon": "a dim moon",
    "star": "one muted star",
    "paper_boat": "a paper boat",
    "oversized_bag": "an oversized bag",
}

ENVIRONMENTS = {
    "empty_room": "an empty room",
    "quiet_street": "a quiet street",
    "open_blank_space": "open blank space",
    "train_platform": "a small train platform",
    "window_corner": "a quiet window corner",
    "night_field": "a simple night field",
    "simple_hallway": "a simple hallway",
    "minimal_crowd_scene": "a minimal crowd scene",
    "desk_with_lamp": "a desk with a dim lamp",
    "moonlit_path": "a path beneath the moon",
}

COMPOSITIONS = {
    "centered_small_character": "a centered small character",
    "lower_third_character": "a lower-third character",
    "separated_subject_and_group": "a separated subject and group",
    "two_side_comparison": "a clear two-side comparison",
    "subject_beneath_oversized_symbol": "the subject beneath an oversized symbol",
    "subject_beside_empty_object": "the subject beside an empty object",
    "left_to_right_movement": "the subject moving from left to right",
    "subject_facing_away": "the subject facing away",
    "large_negative_space": "large negative space",
    "foreground_with_distant_figures": "a foreground subject with distant figures",
    "subject_beside_window": "the subject beside a window",
}

FRAMINGS = {
    "wide_scene": "wide scene framing",
    "medium_wide": "medium-wide framing",
    "full_scene": "full-scene framing",
}


def _variant(
    variant_id: str,
    character: str,
    action: str,
    primary_object: str,
    environment: str,
    composition: str,
    framing: str,
    secondary_object: str = "",
) -> dict[str, str]:
    return {
        "id": variant_id,
        "character": character,
        "action": action,
        "primary_object": primary_object,
        "secondary_object": secondary_object,
        "environment": environment,
        "composition": composition,
        "framing": framing,
    }


INTENT_VARIANTS: dict[str, tuple[dict[str, str], ...]] = {
    "burden": (
        _variant("burden_boxes", "walking_person", "walking_while_carrying", "stacked_boxes", "open_blank_space", "centered_small_character", "medium_wide"),
        _variant("burden_suitcase", "side_profile_person", "pulling_heavy_item", "suitcase", "quiet_street", "left_to_right_movement", "wide_scene"),
        _variant("burden_bag", "standing_person", "holding_object_close", "oversized_bag", "empty_room", "large_negative_space", "medium_wide"),
        _variant("burden_cloud", "standing_person", "standing_beneath_symbol", "shadow_cloud", "simple_hallway", "subject_beneath_oversized_symbol", "wide_scene"),
        _variant("burden_papers", "walking_person", "walking_while_carrying", "scattered_papers", "train_platform", "foreground_with_distant_figures", "full_scene"),
    ),
    "hidden_sadness": (
        _variant("hidden_folded_paper", "seated_person", "holding_object_close", "folded_paper", "window_corner", "subject_beside_window", "medium_wide", "shadow_cloud"),
        _variant("hidden_paper_heart", "standing_person", "holding_object_close", "paper_heart", "empty_room", "large_negative_space", "medium_wide", "shadow_cloud"),
        _variant("hidden_umbrella", "one_quiet_person", "standing_beneath_symbol", "umbrella", "open_blank_space", "centered_small_character", "wide_scene", "shadow_cloud"),
        _variant("hidden_window", "side_profile_person", "looking_through_window", "open_window", "window_corner", "subject_beside_window", "medium_wide", "dim_lamp"),
    ),
    "comparison": (
        _variant("comparison_marks", "two_contrasting_people", "comparing_positions", "measuring_marks", "open_blank_space", "two_side_comparison", "wide_scene"),
        _variant("comparison_scale", "two_contrasting_people", "comparing_positions", "balance_scale", "empty_room", "two_side_comparison", "full_scene"),
        _variant("comparison_paths", "two_contrasting_people", "following_path", "path", "open_blank_space", "two_side_comparison", "wide_scene", "measuring_marks"),
        _variant("comparison_turn", "two_contrasting_people", "turning_away", "balance_scale", "simple_hallway", "two_side_comparison", "medium_wide"),
    ),
    "unseen_effort": (
        _variant("effort_boxes", "worker_plus_passers", "carrying_while_ignored", "stacked_boxes", "quiet_street", "foreground_with_distant_figures", "wide_scene"),
        _variant("effort_papers", "worker_plus_passers", "walking_while_carrying", "scattered_papers", "simple_hallway", "left_to_right_movement", "wide_scene"),
        _variant("effort_suitcase", "worker_plus_passers", "pulling_heavy_item", "suitcase", "train_platform", "foreground_with_distant_figures", "full_scene"),
        _variant("effort_bag", "worker_plus_passers", "carrying_while_ignored", "oversized_bag", "open_blank_space", "centered_small_character", "medium_wide"),
    ),
    "crowded_loneliness": (
        _variant("lonely_standing", "isolated_person_plus_group", "standing_apart", "empty_chair", "minimal_crowd_scene", "separated_subject_and_group", "wide_scene"),
        _variant("lonely_platform", "isolated_person_plus_group", "watching_others_pass", "suitcase", "train_platform", "foreground_with_distant_figures", "wide_scene"),
        _variant("lonely_waiting", "isolated_person_plus_group", "sitting_and_waiting", "empty_chair", "open_blank_space", "separated_subject_and_group", "full_scene"),
        _variant("lonely_umbrella", "isolated_person_plus_group", "standing_apart", "umbrella", "quiet_street", "large_negative_space", "medium_wide"),
    ),
    "nighttime_heaviness": (
        _variant("night_stone", "seated_person", "sitting_and_waiting", "irregular_stone", "night_field", "subject_beneath_oversized_symbol", "wide_scene", "moon"),
        _variant("night_suitcase", "walking_person", "pulling_heavy_item", "suitcase", "moonlit_path", "left_to_right_movement", "wide_scene", "moon"),
        _variant("night_cloud", "one_quiet_person", "standing_beneath_symbol", "shadow_cloud", "night_field", "centered_small_character", "medium_wide", "moon"),
        _variant("night_lamp", "seated_person", "holding_object_close", "dim_lamp", "empty_room", "large_negative_space", "medium_wide", "moon"),
    ),
    "silence": (
        _variant("silence_bubbles", "seated_person", "sitting_and_waiting", "speech_bubbles", "open_blank_space", "centered_small_character", "medium_wide"),
        _variant("silence_paper", "one_quiet_person", "holding_object_close", "folded_paper", "empty_room", "large_negative_space", "medium_wide", "speech_bubbles"),
        _variant("silence_window", "side_profile_person", "looking_through_window", "open_window", "window_corner", "subject_beside_window", "wide_scene", "speech_bubbles"),
        _variant("silence_door", "rear_view_person", "turning_away", "closed_door", "simple_hallway", "subject_facing_away", "full_scene", "speech_bubbles"),
    ),
    "letting_go": (
        _variant("release_bird", "standing_person", "releasing_something", "small_bird", "open_blank_space", "centered_small_character", "wide_scene"),
        _variant("release_thread", "one_quiet_person", "opening_hands", "thread", "empty_room", "large_negative_space", "medium_wide"),
        _variant("release_paper", "side_profile_person", "releasing_something", "folded_paper", "quiet_street", "left_to_right_movement", "wide_scene"),
        _variant("set_down_stone", "walking_person", "setting_something_down", "irregular_stone", "moonlit_path", "lower_third_character", "full_scene", "small_bird"),
        _variant("release_boat", "seated_person", "releasing_something", "paper_boat", "open_blank_space", "large_negative_space", "medium_wide"),
    ),
    "general_symbolic": (
        _variant("general_paper", "one_quiet_person", "holding_object_close", "folded_paper", "open_blank_space", "centered_small_character", "medium_wide"),
        _variant("general_flower", "seated_person", "reaching_toward", "small_flower", "empty_room", "large_negative_space", "medium_wide"),
        _variant("general_path", "walking_person", "following_path", "path", "open_blank_space", "left_to_right_movement", "wide_scene"),
        _variant("general_boat", "side_profile_person", "reaching_toward", "paper_boat", "open_blank_space", "large_negative_space", "wide_scene"),
    ),
}

INTENT_COMPATIBILITY = {
    intent: {
        "actions": frozenset(item["action"] for item in variants),
        "objects": frozenset(item["primary_object"] for item in variants),
        "environments": frozenset(item["environment"] for item in variants),
        "compositions": frozenset(item["composition"] for item in variants),
        "character_counts": frozenset(
            int(CHARACTER_ARCHETYPES[item["character"]]["count"])
            for item in variants
        ),
    }
    for intent, variants in INTENT_VARIANTS.items()
}

_INTENT_NAMES = {
    "hidden_hurt": "hidden_sadness",
    "lonely_crowd": "crowded_loneliness",
    "nighttime_sadness": "nighttime_heaviness",
}

PREFERRED_RANGES = {
    "action": (5, 7),
    "object": (5, 7),
    "environment": (3, 5),
    "composition": (4, 6),
}

ENVIRONMENT_FAMILIES = {
    "empty_room": "interior_quiet_spaces",
    "window_corner": "interior_quiet_spaces",
    "desk_with_lamp": "interior_quiet_spaces",
    "simple_hallway": "interior_quiet_spaces",
    "quiet_street": "open_melancholic_spaces",
    "train_platform": "open_melancholic_spaces",
    "night_field": "open_melancholic_spaces",
    "moonlit_path": "open_melancholic_spaces",
    "open_blank_space": "minimal_symbolic_spaces",
    "minimal_crowd_scene": "minimal_symbolic_spaces",
}

_COMPOSITION_FAMILIES = {
    "centered_small_character": "quiet_single_subject",
    "lower_third_character": "quiet_single_subject",
    "subject_beneath_oversized_symbol": "quiet_single_subject",
    "subject_beside_empty_object": "quiet_single_subject",
    "large_negative_space": "quiet_single_subject",
    "separated_subject_and_group": "relational_scene",
    "two_side_comparison": "relational_scene",
    "foreground_with_distant_figures": "relational_scene",
    "left_to_right_movement": "directional_scene",
    "subject_facing_away": "directional_scene",
    "subject_beside_window": "directional_scene",
}

_OBJECT_FAMILIES = {
    "irregular_stone": "weight_symbols",
    "stacked_boxes": "weight_symbols",
    "suitcase": "weight_symbols",
    "oversized_bag": "weight_symbols",
    "folded_paper": "paper_symbols",
    "paper_heart": "paper_symbols",
    "scattered_papers": "paper_symbols",
    "paper_boat": "paper_symbols",
    "shadow_cloud": "atmospheric_symbols",
    "moon": "atmospheric_symbols",
    "star": "atmospheric_symbols",
    "dim_lamp": "atmospheric_symbols",
    "speech_bubbles": "communication_symbols",
    "closed_door": "communication_symbols",
    "open_window": "communication_symbols",
    "measuring_marks": "comparison_symbols",
    "balance_scale": "comparison_symbols",
    "thread": "release_symbols",
    "small_bird": "release_symbols",
    "small_flower": "release_symbols",
    "path": "direction_symbols",
    "umbrella": "shelter_symbols",
    "empty_chair": "absence_symbols",
}


def _stable_number(*parts: object) -> int:
    raw = "|".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def _intent_fallback(scene: Any) -> str:
    text = " ".join(
        str(value or "")
        for value in (
            scene.scene_meaning,
            scene.emotional_metaphor,
            scene.symbolic_visual,
            scene.voice_script,
        )
    )
    key = unicodedata.normalize("NFKD", text.lower())
    key = "".join(char for char in key if not unicodedata.combining(char))
    key = re.sub(r"[^a-z0-9]+", " ", key).strip()
    if "effort" in key and any(
        signal in key
        for signal in ("invisible", "unseen", "unnoticed", "unrecognized")
    ):
        return "unseen_effort"
    has_night = any(
        signal in key
        for signal in ("night", "nighttime", "nocturnal", "moon", "crescent", "dem")
    )
    has_weight = any(
        signal in key
        for signal in (
            "weight",
            "heavy",
            "heaviness",
            "sadness",
            "melancholy",
            "burden",
            "nang",
        )
    )
    if has_night and has_weight:
        return "nighttime_heaviness"
    return "general_symbolic"


def _effective_seed(plan: TellaScenePlan) -> int:
    explicit_seed = (os.environ.get("TELLA_SYMBOLIC_VISUAL_SEED") or "").strip()
    job_id = (os.environ.get("TELLA_SYMBOLIC_JOB_ID") or "").strip()
    scenes = [
        {
            "index": scene.scene_index,
            "voice": scene.voice_script,
        }
        for scene in plan.scenes
        if scene.kind == "scene"
    ]
    stable_plan = json.dumps(
        {"theme": plan.theme, "language": plan.language, "scenes": scenes},
        ensure_ascii=False,
        sort_keys=True,
    )
    seed_source = explicit_seed if explicit_seed else f"{job_id}|{stable_plan}"
    return _stable_number(seed_source) % 2_147_483_647


def _semantic_prompt_anchor(intent: str, variant: dict[str, str]) -> str:
    if intent == "burden":
        return "the load visibly creates weight, resistance, or downward pressure"
    if intent == "hidden_sadness":
        return "a calm outward expression clearly contrasted with a dark heavy emotional symbol"
    if intent == "comparison":
        return "both people and the unequal comparison cue are clearly visible"
    if intent == "unseen_effort":
        return "the work action is clear while two nearby passers do not acknowledge it"
    if intent == "crowded_loneliness":
        return "the separated person and the nearby group are both clearly visible"
    if intent == "nighttime_heaviness":
        return "the night cue and the visible emotional weight are readable together"
    if intent == "silence":
        return "the empty or unused communication cue is clearly visible"
    if intent == "letting_go":
        return "the object is visibly being released, opened, or placed down"
    return "the symbolic action and object are immediately readable together"


def _provider_prompt(variant: dict[str, str], intent: str) -> str:
    character = CHARACTER_ARCHETYPES[variant["character"]]["prompt"]
    action = ACTIONS[variant["action"]]
    primary = SYMBOLIC_OBJECTS[variant["primary_object"]]
    secondary = SYMBOLIC_OBJECTS.get(variant["secondary_object"], "")
    environment = ENVIRONMENTS[variant["environment"]]
    composition = COMPOSITIONS[variant["composition"]]
    framing = FRAMINGS[variant["framing"]]
    subject = f"{character}, {action} with {primary}"
    if secondary:
        subject += f", with {secondary} clearly visible"
    return (
        "minimalist hand-drawn emotional illustration, "
        f"{subject}, {_semantic_prompt_anchor(intent, variant)}, {environment}, "
        f"{composition}, {framing}, warm muted taupe "
        "and brown-gray palette, soft brown pencil lines, simple readable scene, "
        "gentle melancholic mood, generous negative space, low visual clutter"
    )


def _semantic_profile(intent: str, variant: dict[str, str]) -> dict[str, Any]:
    action = variant["action"]
    primary = variant["primary_object"]
    secondary = variant["secondary_object"]
    composition = variant["composition"]
    character_count = int(CHARACTER_ARCHETYPES[variant["character"]]["count"])
    anchors: list[str] = []
    score = 20

    if intent == "burden":
        if primary in {
            "irregular_stone",
            "stacked_boxes",
            "suitcase",
            "oversized_bag",
            "scattered_papers",
            "shadow_cloud",
        }:
            anchors.append("visible_load_or_weight")
            score += 30
        if action in {
            "walking_while_carrying",
            "pulling_heavy_item",
            "standing_beneath_symbol",
        }:
            anchors.append("resistance_or_downward_pressure")
            score += 45
        if action == "holding_object_close":
            score -= 15
    elif intent == "hidden_sadness":
        if secondary == "shadow_cloud":
            anchors.extend(
                ["calm_outward_appearance", "dark_inner_weight_contrast"]
            )
            score += 70
        if primary == "umbrella":
            score -= 15
    elif intent == "comparison":
        if character_count >= 2:
            anchors.append("two_visible_people")
            score += 30
        if primary in {"measuring_marks", "balance_scale"} or secondary == "measuring_marks":
            anchors.append("clear_comparison_cue")
            score += 35
        if action == "comparing_positions":
            anchors.append("comparison_is_primary_action")
            score += 15
        elif action in {"turning_away", "following_path"}:
            score -= 10
    elif intent == "unseen_effort":
        if variant["character"] == "worker_plus_passers":
            anchors.append("unaware_passers_visible")
            score += 30
        if action in {
            "carrying_while_ignored",
            "walking_while_carrying",
            "pulling_heavy_item",
        }:
            anchors.append("clear_effort_action")
            score += 30
        if primary in {"stacked_boxes", "scattered_papers", "oversized_bag", "suitcase"}:
            anchors.append("visible_work_or_load")
            score += 20
        if primary == "suitcase" and variant["environment"] == "train_platform":
            score -= 25
    elif intent == "crowded_loneliness":
        if variant["character"] == "isolated_person_plus_group":
            anchors.append("separated_person_and_group")
            score += 45
        if action in {"standing_apart", "watching_others_pass", "sitting_and_waiting"}:
            anchors.append("spatial_or_emotional_separation")
            score += 30
    elif intent == "nighttime_heaviness":
        if secondary == "moon" or variant["environment"] in {"night_field", "moonlit_path"}:
            anchors.append("nighttime_cue")
            score += 35
        if action in {
            "sitting_and_waiting",
            "pulling_heavy_item",
            "standing_beneath_symbol",
            "holding_object_close",
        }:
            anchors.append("visible_weight_or_stillness")
            score += 35
    elif intent == "silence":
        if primary == "speech_bubbles" or secondary == "speech_bubbles":
            anchors.append("blocked_or_unused_communication")
            score += 65
    elif intent == "letting_go":
        if action in {"opening_hands", "releasing_something", "setting_something_down"}:
            anchors.append("release_or_placing_down_action")
            score += 65
    else:
        anchors.append("readable_symbolic_relationship")
        score = 70

    score = max(0, min(100, score))
    strength = "strong" if score >= 85 else "acceptable" if score >= 65 else "weak"
    return {
        "score": score,
        "strength": strength,
        "anchors": anchors,
        "required_clarity": len(anchors),
        "composition": composition,
    }


def _cohesion_score(
    variant: dict[str, str],
    usage: dict[str, Counter[str]],
    history: list[dict[str, str]],
) -> tuple[int, str]:
    environment = variant["environment"]
    composition = variant["composition"]
    family = ENVIRONMENT_FAMILIES[environment]
    composition_family = _COMPOSITION_FAMILIES[composition]
    object_family = _OBJECT_FAMILIES[variant["primary_object"]]
    score = min(24, usage["cohesion_family"][family] * 8)
    score += min(12, usage["composition_family"][composition_family] * 4)
    score += min(9, usage["object_family"][object_family] * 3)
    if usage["environment"][environment] and len(usage["environment"]) >= PREFERRED_RANGES["environment"][0]:
        score += 8
    if usage["composition"][composition] and len(usage["composition"]) >= PREFERRED_RANGES["composition"][0]:
        score += 6
    if usage["object"][variant["primary_object"]] and len(usage["object"]) >= PREFERRED_RANGES["object"][0]:
        score += 18
    if (
        usage["character_archetype"][variant["character"]]
        and len(usage["character_archetype"]) >= 3
    ):
        score += 10
    if history and history[-1]["environment"] == environment:
        score -= 8
    return score, family


def _diversity_score(
    variant: dict[str, str],
    usage: dict[str, Counter[str]],
    history: list[dict[str, str]],
) -> tuple[int, int]:
    score = 0
    for field, usage_name in (
        ("action", "action"),
        ("primary_object", "object"),
        ("environment", "environment"),
        ("composition", "composition"),
    ):
        value = variant[field]
        low, high = PREFERRED_RANGES[usage_name]
        distinct = len(usage[usage_name])
        if not usage[usage_name][value]:
            score += 8 if distinct < low else 2 if distinct < high else -6
        elif distinct >= low:
            score += 3

    adjacent_repeats = 0
    if history:
        previous = history[-1]
        for field in ("action", "primary_object"):
            if variant[field] == previous[field]:
                adjacent_repeats += 1
                score -= 45
        if variant["environment"] == previous["environment"]:
            score -= 6
        if variant["composition"] == previous["composition"]:
            score -= 6
    return score, adjacent_repeats


def _candidate_metrics(
    intent: str,
    variant: dict[str, str],
    rank: int,
    usage: dict[str, Counter[str]],
    history: list[dict[str, str]],
) -> dict[str, Any]:
    semantic = _semantic_profile(intent, variant)
    cohesion_score, cohesion_family = _cohesion_score(variant, usage, history)
    diversity_score, adjacent_repeats = _diversity_score(variant, usage, history)
    sort_key = (
        semantic["score"],
        semantic["required_clarity"],
        -adjacent_repeats,
        cohesion_score,
        diversity_score,
        -rank,
    )
    final_score = (
        semantic["score"] * 100
        + semantic["required_clarity"] * 10
        - adjacent_repeats * 50
        + cohesion_score
        + diversity_score / 10
    )
    return {
        **semantic,
        "cohesion_score": cohesion_score,
        "cohesion_family": cohesion_family,
        "diversity_score": diversity_score,
        "adjacent_repeats": adjacent_repeats,
        "sort_key": sort_key,
        "final_score": round(final_score, 1),
    }


def apply_symbolic_visual_diversity(
    plan: TellaScenePlan,
    intent_resolver: Callable[[Any], str],
) -> None:
    """Attach one compatible deterministic visual realization to each scene."""
    if plan.theme != "minimalist_symbolic_reel":
        return

    seed = _effective_seed(plan)
    plan.visual_diversity_seed = seed
    plan.preferred_action_range = list(PREFERRED_RANGES["action"])
    plan.preferred_object_range = list(PREFERRED_RANGES["object"])
    plan.preferred_environment_range = list(PREFERRED_RANGES["environment"])
    plan.preferred_composition_range = list(PREFERRED_RANGES["composition"])
    usage = {
        name: Counter()
        for name in (
            "action",
            "object",
            "environment",
            "composition",
            "framing",
            "character_count",
            "cohesion_family",
            "composition_family",
            "object_family",
            "character_archetype",
        )
    }
    history: list[dict[str, str]] = []

    for scene in (item for item in plan.scenes if item.kind == "scene"):
        raw_intent = intent_resolver(scene) or _intent_fallback(scene)
        intent = _INTENT_NAMES.get(raw_intent, raw_intent)
        if intent not in INTENT_VARIANTS:
            intent = "general_symbolic"
        variants = INTENT_VARIANTS[intent]
        scene_seed = _stable_number(seed, scene.scene_index, intent) % 2_147_483_647
        start = scene_seed % len(variants)
        ordered = [*variants[start:], *variants[:start]]
        candidates = [
            (
                variant,
                _candidate_metrics(intent, variant, rank, usage, history),
            )
            for rank, variant in enumerate(ordered)
        ]
        selected, selected_metrics = max(
            candidates,
            key=lambda item: item[1]["sort_key"],
        )
        initial = ordered[0]
        avoided: list[str] = []
        previous = history[-1] if history else None
        if previous and selected["id"] != initial["id"]:
            if initial["action"] == previous["action"]:
                avoided.append("primary_action")
            if initial["primary_object"] == previous["primary_object"]:
                avoided.append("primary_object")
            if initial["environment"] == previous["environment"]:
                avoided.append("environment")
            if initial["composition"] == previous["composition"]:
                avoided.append("composition_pattern")
        for field, usage_name, label in (
            ("action", "action", "primary_action"),
            ("primary_object", "object", "primary_object"),
            ("environment", "environment", "environment"),
            ("composition", "composition", "composition_pattern"),
            ("framing", "framing", "framing"),
        ):
            if (
                selected["id"] != initial["id"]
                and usage[usage_name][initial[field]] > usage[usage_name][selected[field]]
                and label not in avoided
            ):
                avoided.append(label)

        semantic_priority_override = any(
            metrics["diversity_score"] > selected_metrics["diversity_score"]
            and metrics["score"] < selected_metrics["score"]
            for _, metrics in candidates
        )
        diversity_target_relaxed = any(
            metrics["diversity_score"] > selected_metrics["diversity_score"]
            and (
                metrics["score"] < selected_metrics["score"]
                or metrics["required_clarity"] < selected_metrics["required_clarity"]
                or metrics["cohesion_score"] < selected_metrics["cohesion_score"]
            )
            for _, metrics in candidates
        )

        character = selected["character"]
        scene.semantic_intent = intent
        scene.visual_variant_id = selected["id"]
        scene.visual_seed = scene_seed
        scene.character_archetype = character
        scene.character_count = int(CHARACTER_ARCHETYPES[character]["count"])
        scene.primary_action = selected["action"]
        scene.primary_object = selected["primary_object"]
        scene.secondary_object = selected["secondary_object"]
        scene.environment = selected["environment"]
        scene.composition_pattern = selected["composition"]
        scene.framing = selected["framing"]
        scene.diversity_repair_applied = selected["id"] != initial["id"]
        scene.repeated_attribute_avoided = avoided
        scene.provider_prompt_variant = _provider_prompt(selected, intent)
        scene.semantic_strength = selected_metrics["strength"]
        scene.semantic_strength_score = float(selected_metrics["score"])
        scene.semantic_anchor_fields = list(selected_metrics["anchors"])
        scene.cohesion_family = selected_metrics["cohesion_family"]
        scene.diversity_score = float(selected_metrics["diversity_score"])
        scene.cohesion_score = float(selected_metrics["cohesion_score"])
        scene.final_variant_score = float(selected_metrics["final_score"])
        scene.diversity_target_relaxed = diversity_target_relaxed
        scene.semantic_priority_override = semantic_priority_override

        for name, value in (
            ("action", selected["action"]),
            ("object", selected["primary_object"]),
            ("environment", selected["environment"]),
            ("composition", selected["composition"]),
            ("framing", selected["framing"]),
            ("character_count", str(scene.character_count)),
        ):
            usage[name][value] += 1
        usage["cohesion_family"][selected_metrics["cohesion_family"]] += 1
        usage["composition_family"][_COMPOSITION_FAMILIES[selected["composition"]]] += 1
        usage["object_family"][_OBJECT_FAMILIES[selected["primary_object"]]] += 1
        usage["character_archetype"][selected["character"]] += 1
        history.append(selected)

    plan.distinct_action_count = len(usage["action"])
    plan.distinct_object_count = len(usage["object"])
    plan.distinct_environment_count = len(usage["environment"])
    plan.distinct_composition_count = len(usage["composition"])


__all__ = [
    "ACTIONS",
    "CHARACTER_ARCHETYPES",
    "COMPOSITIONS",
    "ENVIRONMENTS",
    "FRAMINGS",
    "INTENT_VARIANTS",
    "INTENT_COMPATIBILITY",
    "SYMBOLIC_OBJECTS",
    "apply_symbolic_visual_diversity",
]
