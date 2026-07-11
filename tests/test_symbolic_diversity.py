import re
from collections import Counter

from tella.media import fetch
from tella.planner.models import Scene, TellaScenePlan
from tella.planner.symbolic_diversity import (
    ACTIONS,
    INTENT_COMPATIBILITY,
    INTENT_VARIANTS,
    SYMBOLIC_OBJECTS,
    _candidate_metrics,
    apply_symbolic_visual_diversity,
)
from tella.planner.symbolic_reel import enforce_symbolic_reel_plan


_SCENE_SPECS = (
    ("The weight of unspoken emotions", "Stored sorrow", "A person carrying a heavy stone"),
    ("The facade of being okay", "Hiding exhaustion", "A simple mask"),
    ("Comparison with another person", "Unequal worth", "Two silhouettes"),
    ("Invisible effort", "Growth in darkness", "A small plant"),
    ("Isolation in a crowd", "Loneliness", "Many dots"),
    ("Nighttime intensifies feelings", "Nightfall", "Crescent moon"),
    ("Silence is the primary meaning", "Words kept inside", "A closed mouth"),
    ("Letting go", "Release", "A bird leaving closed hands"),
)


def _plan(theme: str = "minimalist_symbolic_reel") -> TellaScenePlan:
    scenes = [
        Scene(
            scene_index=index,
            title=f"Scene {index}",
            voice_script=(
                "Khi đêm xuống, mọi thứ lại trở nên nặng hơn."
                if index == 6
                else meaning
            ),
            scene_meaning=meaning,
            emotional_metaphor=metaphor,
            symbolic_visual=visual,
            main_character_or_object=visual,
        )
        for index, (meaning, metaphor, visual) in enumerate(_SCENE_SPECS, start=1)
    ]
    return TellaScenePlan(
        title="Symbolic diversity",
        language="en",
        aspect_ratio="9:16",
        media_source="ai_image",
        duration_mode="short",
        theme=theme,
        scenes=scenes,
    )


def _choices(plan: TellaScenePlan) -> list[tuple]:
    return [
        (
            scene.semantic_intent,
            scene.visual_variant_id,
            scene.character_archetype,
            scene.character_count,
            scene.primary_action,
            scene.primary_object,
            scene.secondary_object,
            scene.environment,
            scene.composition_pattern,
            scene.framing,
            scene.visual_seed,
        )
        for scene in plan.scenes
    ]


def _variant(intent: str, variant_id: str) -> dict[str, str]:
    return next(item for item in INTENT_VARIANTS[intent] if item["id"] == variant_id)


def _usage() -> dict[str, Counter[str]]:
    return {
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


def test_each_intent_uses_only_catalog_compatible_realizations(monkeypatch):
    monkeypatch.setenv("TELLA_SYMBOLIC_VISUAL_SEED", "compatibility-seed")
    plan = _plan()

    enforce_symbolic_reel_plan(plan)

    assert [scene.semantic_intent for scene in plan.scenes] == [
        "burden",
        "hidden_sadness",
        "comparison",
        "unseen_effort",
        "crowded_loneliness",
        "nighttime_heaviness",
        "silence",
        "letting_go",
    ]
    for scene in plan.scenes:
        compatible = INTENT_COMPATIBILITY[scene.semantic_intent]
        assert scene.primary_action in compatible["actions"]
        assert scene.primary_object in compatible["objects"]
        assert scene.environment in compatible["environments"]
        assert scene.composition_pattern in compatible["compositions"]
        assert scene.character_count in compatible["character_counts"]
        assert any(
            item["id"] == scene.visual_variant_id
            for item in INTENT_VARIANTS[scene.semantic_intent]
        )


def test_identical_seed_produces_identical_visual_variants(monkeypatch):
    monkeypatch.setenv("TELLA_SYMBOLIC_VISUAL_SEED", "repeatable")
    first = _plan()
    second = _plan()

    enforce_symbolic_reel_plan(first)
    enforce_symbolic_reel_plan(second)

    assert first.visual_diversity_seed == second.visual_diversity_seed
    assert _choices(first) == _choices(second)


def test_planner_paraphrases_do_not_change_effective_seed(monkeypatch):
    monkeypatch.delenv("TELLA_SYMBOLIC_VISUAL_SEED", raising=False)
    monkeypatch.setenv("TELLA_SYMBOLIC_JOB_ID", "stable-job")
    first = _plan()
    second = _plan()
    second.scenes[0].scene_meaning = "A paraphrased burden meaning"
    second.scenes[0].emotional_metaphor = "A paraphrased heavy feeling"

    enforce_symbolic_reel_plan(first)
    enforce_symbolic_reel_plan(second)

    assert first.visual_diversity_seed == second.visual_diversity_seed


def test_reapplying_diversity_is_idempotent(monkeypatch):
    monkeypatch.setenv("TELLA_SYMBOLIC_VISUAL_SEED", "idempotent")
    plan = _plan()
    enforce_symbolic_reel_plan(plan)
    first = _choices(plan)
    first_repairs = [
        (scene.diversity_repair_applied, list(scene.repeated_attribute_avoided))
        for scene in plan.scenes
    ]

    enforce_symbolic_reel_plan(plan)

    assert _choices(plan) == first
    assert [
        (scene.diversity_repair_applied, list(scene.repeated_attribute_avoided))
        for scene in plan.scenes
    ] == first_repairs


def test_different_seeds_can_select_different_valid_variants(monkeypatch):
    choices = set()
    for seed in range(1, 9):
        monkeypatch.setenv("TELLA_SYMBOLIC_VISUAL_SEED", str(seed))
        plan = _plan()
        enforce_symbolic_reel_plan(plan)
        choices.add(tuple(scene.visual_variant_id for scene in plan.scenes))

    assert len(choices) > 1


def test_adjacent_equivalent_intents_avoid_repeated_actions_and_objects(monkeypatch):
    monkeypatch.setenv("TELLA_SYMBOLIC_VISUAL_SEED", "adjacent-burden")
    plan = _plan()
    for scene in plan.scenes:
        scene.scene_meaning = "Emotional burden"
        scene.emotional_metaphor = "A heavy responsibility"
        scene.symbolic_visual = "A person carrying a heavy stone"
        scene.main_character_or_object = scene.symbolic_visual

    enforce_symbolic_reel_plan(plan)

    for previous, current in zip(plan.scenes, plan.scenes[1:]):
        assert current.primary_action != previous.primary_action
        assert current.primary_object != previous.primary_object


def test_strong_repeated_environment_beats_weak_unique_environment():
    usage = _usage()
    usage["environment"].update(
        {"simple_hallway": 1, "quiet_street": 1, "open_blank_space": 1}
    )
    usage["cohesion_family"]["interior_quiet_spaces"] = 1
    history = [_variant("comparison", "comparison_scale")]
    strong = _candidate_metrics(
        "burden",
        _variant("burden", "burden_cloud"),
        1,
        usage,
        history,
    )
    weak = _candidate_metrics(
        "burden",
        _variant("burden", "burden_bag"),
        0,
        usage,
        history,
    )

    assert strong["strength"] == "strong"
    assert weak["strength"] == "weak"
    assert strong["sort_key"] > weak["sort_key"]


def test_non_adjacent_environment_reuse_receives_cohesion_credit():
    usage = _usage()
    usage["environment"].update(
        {"simple_hallway": 1, "quiet_street": 1, "open_blank_space": 1}
    )
    usage["cohesion_family"]["interior_quiet_spaces"] = 1
    history = [
        _variant("burden", "burden_cloud"),
        _variant("comparison", "comparison_marks"),
    ]
    repeated = _candidate_metrics(
        "silence",
        _variant("silence", "silence_door"),
        0,
        usage,
        history,
    )

    assert repeated["adjacent_repeats"] == 0
    assert repeated["cohesion_score"] > 0


def test_semantic_requirements_take_priority_over_diversity(monkeypatch):
    monkeypatch.setenv("TELLA_SYMBOLIC_VISUAL_SEED", "semantic-priority")
    plan = _plan()

    enforce_symbolic_reel_plan(plan)

    comparison = plan.scenes[2]
    burden = plan.scenes[0]
    hidden = plan.scenes[1]
    effort = plan.scenes[3]
    crowd = plan.scenes[4]
    letting_go = plan.scenes[7]
    assert burden.primary_action in {
        "walking_while_carrying",
        "pulling_heavy_item",
        "standing_beneath_symbol",
    }
    assert burden.semantic_strength == "strong"
    assert hidden.semantic_anchor_fields == [
        "calm_outward_appearance",
        "dark_inner_weight_contrast",
    ]
    assert comparison.character_count >= 2
    assert comparison.primary_action == "comparing_positions"
    assert comparison.primary_object in {"measuring_marks", "balance_scale"}
    assert "clear_comparison_cue" in comparison.semantic_anchor_fields
    assert effort.character_archetype == "worker_plus_passers"
    assert effort.primary_action in {
        "carrying_while_ignored",
        "walking_while_carrying",
    }
    assert "unaware_passers_visible" in effort.semantic_anchor_fields
    assert crowd.character_count >= 4
    assert crowd.character_archetype == "isolated_person_plus_group"
    assert letting_go.primary_action in {
        "releasing_something",
        "opening_hands",
        "setting_something_down",
    }
    assert any(scene.semantic_priority_override for scene in plan.scenes)


def test_provider_prompts_keep_selected_action_and_object_compact_and_safe(monkeypatch):
    monkeypatch.setenv("TELLA_SYMBOLIC_VISUAL_SEED", "provider-safe")
    plan = _plan()
    enforce_symbolic_reel_plan(plan)

    risky = (
        "adult",
        "child",
        "medical",
        "mask",
        "ghost",
        "monster",
        "blob",
        "creature",
        "silhouette",
        "cracked",
        "shoulders",
        "mouth",
        "body-part",
        "close-up",
    )
    for scene in plan.scenes:
        prompt = fetch._cloudflare_safe_symbolic_prompt(scene).lower()
        assert len(prompt) <= 900
        assert ACTIONS[scene.primary_action] in prompt
        assert SYMBOLIC_OBJECTS[scene.primary_object] in prompt
        assert prompt == scene.provider_prompt_variant.lower()
        for term in risky:
            assert re.search(rf"\b{re.escape(term)}\b", prompt) is None
    assert "calm outward expression" in plan.scenes[1].provider_prompt_variant
    assert "passers do not acknowledge" in plan.scenes[3].provider_prompt_variant


def test_diversity_metadata_is_exposed_in_plan_dump(monkeypatch):
    monkeypatch.setenv("TELLA_SYMBOLIC_VISUAL_SEED", "dry-run-metadata")
    plan = _plan()
    enforce_symbolic_reel_plan(plan)

    payload = plan.model_dump()
    assert payload["visual_diversity_seed"] > 0
    assert payload["distinct_action_count"] >= 4
    assert payload["distinct_object_count"] >= 4
    assert payload["distinct_environment_count"] >= 2
    assert payload["distinct_composition_count"] >= 3
    assert payload["distinct_environment_count"] <= 5
    assert payload["distinct_composition_count"] <= 6
    assert payload["preferred_action_range"] == [5, 7]
    assert payload["preferred_object_range"] == [5, 7]
    assert payload["preferred_environment_range"] == [3, 5]
    assert payload["preferred_composition_range"] == [4, 6]
    for scene in payload["scenes"]:
        for field in (
            "semantic_intent",
            "visual_variant_id",
            "visual_seed",
            "character_archetype",
            "character_count",
            "primary_action",
            "primary_object",
            "secondary_object",
            "environment",
            "composition_pattern",
            "framing",
            "diversity_repair_applied",
            "repeated_attribute_avoided",
            "provider_prompt_variant",
            "semantic_strength",
            "semantic_strength_score",
            "semantic_anchor_fields",
            "cohesion_family",
            "diversity_score",
            "cohesion_score",
            "final_variant_score",
            "diversity_target_relaxed",
            "semantic_priority_override",
        ):
            assert field in scene


def test_non_symbolic_theme_is_unchanged():
    plan = _plan(theme="cinematic")
    before = plan.model_dump()

    apply_symbolic_visual_diversity(plan, lambda scene: "burden")

    assert plan.model_dump() == before
