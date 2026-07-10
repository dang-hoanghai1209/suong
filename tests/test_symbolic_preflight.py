from tella.planner.models import Scene, TellaScenePlan
from tella.planner.symbolic_reel import enforce_symbolic_reel_plan


def _preflight_scene(
    meaning: str,
    visual: str,
    *,
    voice_script: str | None = None,
    metaphor: str | None = None,
    cast_archetype: str = "",
    main_character_or_object: str | None = None,
) -> tuple[TellaScenePlan, Scene]:
    scenes = [
        Scene(
            scene_index=1,
            title="Preflight target",
            voice_script=voice_script or meaning,
            scene_meaning=meaning,
            symbolic_visual=visual,
            emotional_metaphor=metaphor or meaning,
            main_character_or_object=main_character_or_object or visual,
            cast_archetype=cast_archetype,
        ),
        Scene(
            scene_index=2,
            title="Neutral two",
            voice_script="A small paper heart rests quietly.",
            scene_meaning="quiet care",
            symbolic_visual="small paper heart with a soft crack",
            emotional_metaphor="care becoming visible",
            main_character_or_object="small paper heart",
        ),
        Scene(
            scene_index=3,
            title="Neutral three",
            voice_script="A small warm light remains.",
            scene_meaning="hope remains",
            symbolic_visual="tiny warm light beside a folded note",
            emotional_metaphor="hope staying present",
            main_character_or_object="tiny warm light",
        ),
    ]
    plan = TellaScenePlan(
        title="Symbolic preflight",
        language="en",
        aspect_ratio="9:16",
        media_source="ai_image",
        duration_mode="short",
        theme="minimalist_symbolic_reel",
        scenes=scenes,
    )
    enforce_symbolic_reel_plan(plan)
    return plan, plan.scenes[0]


def test_simple_mask_is_repaired_for_hidden_hurt_scene():
    plan, scene = _preflight_scene(
        "Trying to appear okay while hurt inside",
        "A simple mask",
    )

    visual = scene.symbolic_visual.lower()
    assert scene.symbolic_preflight_original_visual == "A simple mask"
    assert scene.symbolic_preflight_repaired is True
    assert scene.symbolic_preflight_status == "repaired"
    assert "small calm smile" in visual
    assert "dark cracked shape or heavy cloud" in visual
    assert "no mask" in visual
    assert plan.symbolic_preflight_repaired is True
    assert plan.symbolic_preflight_original_visual["1"] == "A simple mask"


def test_two_silhouettes_are_repaired_for_comparison_scene():
    _, scene = _preflight_scene(
        "Being compared with another person",
        "Two silhouettes",
    )

    visual = scene.symbolic_visual.lower()
    assert "two clearly drawn adult figures" in visual
    assert "unequal measuring marks" in visual
    assert "balance scale" in visual
    assert "no black silhouettes" in visual
    assert "silhouette_visual" in scene.symbolic_preflight_failure_reasons


def test_plant_in_shadow_is_repaired_for_unseen_effort_scene():
    _, scene = _preflight_scene(
        "Effort is unseen",
        "A plant in shadow",
    )

    visual = scene.symbolic_visual.lower()
    assert "adult carrying a visible stack of heavy boxes or stones" in visual
    assert "at least two nearby adults walk past" in visual


def test_heavy_moon_is_repaired_for_nighttime_sadness_scene():
    _, scene = _preflight_scene(
        "Sadness feels heavier at night",
        "A heavy moon",
    )

    visual = scene.symbolic_visual.lower()
    assert "adult sitting alone beneath a large dim moon" in visual
    assert "heavy stone resting beside them" in visual
    assert (
        "no ocean, ship, anchor poster, ghost, creature, or object-only composition"
        in visual
    )


def test_closed_mouth_is_repaired_for_silence_scene():
    _, scene = _preflight_scene(
        "Silence can hold what is not said",
        "A closed mouth",
    )

    visual = scene.symbolic_visual.lower()
    assert "adult inside a quiet circle" in visual
    assert "crossed-out or empty speech bubbles" in visual
    assert "no mouth or body-part close-up" in visual


def test_human_action_cannot_keep_symbolic_object_cast_archetype():
    _, scene = _preflight_scene(
        "Letting go can happen slowly",
        "A small stone",
    )

    assert scene.cast_archetype == "adult_woman_or_man"
    assert "adult placing a stone down" in scene.symbolic_visual.lower()
    assert "opening both hands" in scene.symbolic_visual.lower()
    assert scene.symbolic_preflight_repaired is True


def test_heavy_stone_burden_is_not_reclassified_as_silence():
    _, scene = _preflight_scene(
        "Carrying hidden emotional burdens",
        "A person carrying a heavy stone",
        voice_script="Có những nỗi buồn, mình không nói ra, nhưng vẫn mang theo rất lâu.",
        metaphor="Burden of silence",
    )

    assert scene.symbolic_visual == (
        "one clearly drawn adult carrying a large cracked stone on their "
        "shoulders, visible facial features, no black silhouette"
    )
    assert scene.main_character_or_object == "adult carrying a heavy cracked stone"
    assert scene.symbolic_preflight_status == "repaired"
    assert scene.symbolic_preflight_repaired is True
    assert scene.cast_archetype == "adult_woman_or_man"
    assert not any("silence" in reason for reason in scene.symbolic_preflight_failure_reasons)
    assert "quiet circle" not in scene.image_prompt.lower()


def test_many_dots_are_repaired_to_visible_adult_group():
    _, scene = _preflight_scene(
        "Loneliness in a crowd",
        "One figure among many dots",
        metaphor="Isolation despite being surrounded",
    )

    visual = scene.symbolic_visual.lower()
    assert "one isolated adult spatially separated" in visual
    assert "clearly visible group of at least three adults" in visual
    assert "unreadable_object_only_metaphor" in scene.symbolic_preflight_failure_reasons


def test_moon_and_anchor_are_repaired_to_human_nighttime_composition():
    _, scene = _preflight_scene(
        "Sadness feels heavier at night",
        "A moon and anchor",
        metaphor="Night makes sadness feel heavier",
    )

    visual = scene.symbolic_visual.lower()
    assert "adult sitting alone beneath a large dim moon" in visual
    assert "heavy stone resting beside them" in visual
    assert (
        "no ocean, ship, anchor poster, ghost, creature, or object-only composition"
        in visual
    )
    assert scene.cast_archetype == "adult_woman_or_man"


def test_releasing_balloon_or_bird_becomes_human_letting_go_action():
    for original in ("A balloon drifting away", "A small bird flying away"):
        _, scene = _preflight_scene("Letting go slowly", original)
        visual = scene.symbolic_visual.lower()
        assert "one adult placing a stone down" in visual
        assert "opening both hands" in visual
        assert "small bird flies away" in visual
        assert scene.cast_archetype == "adult_woman_or_man"


def test_known_scene_types_never_use_generic_fallback():
    cases = (
        ("Trying to appear okay while hurt inside", "A simple mask"),
        ("Being compared with another person", "Two silhouettes"),
        ("Effort is unseen", "A plant in shadow"),
        ("Loneliness in a crowd", "Many dots"),
        ("Sadness feels heavier at night", "A moon and anchor"),
        ("Silence is the primary meaning", "A closed mouth"),
        ("Carrying hidden emotional burdens", "An abstract shape"),
        ("Letting go slowly", "A balloon drifting away"),
    )
    generic = "one ordinary adult interacting with one concrete paper heart or stone"

    for meaning, visual in cases:
        _, scene = _preflight_scene(meaning, visual)
        assert generic not in scene.symbolic_visual.lower()


def test_scene_type_precedence_is_deterministic():
    plan, scene = _preflight_scene(
        "Being compared with another person",
        "A person carrying a heavy stone",
        voice_script="They remain silent and do not say what they feel.",
        metaphor="Unequal comparison",
    )
    first_visual = scene.symbolic_visual
    first_reasons = list(scene.symbolic_preflight_failure_reasons)

    enforce_symbolic_reel_plan(plan)

    assert scene.symbolic_visual == first_visual
    assert scene.symbolic_preflight_failure_reasons == first_reasons
    primary_reasons = [
        reason
        for reason in first_reasons
        if reason.startswith("scene_type_requires_concrete_composition:")
    ]
    assert primary_reasons == ["scene_type_requires_concrete_composition:comparison"]


def test_exact_facade_aliases_use_hidden_hurt_template():
    _, scene = _preflight_scene(
        "The facade of being okay",
        "A simple mask",
        metaphor="Hiding exhaustion",
    )

    expected = (
        "one adult showing a small calm smile while a dark cracked shape or heavy "
        "cloud is clearly visible behind their shoulders, no mask and no medical "
        "imagery"
    )
    assert scene.symbolic_visual == expected
    assert "paper heart or stone" not in scene.symbolic_visual.lower()
    assert scene.cast_archetype == "adult_woman_or_man"


def test_exact_unrecognized_effort_aliases_use_unseen_effort_template():
    _, scene = _preflight_scene(
        "Unrecognized effort",
        "A small plant in shadows",
        metaphor="Invisible growth",
    )

    expected = (
        "one adult carrying a visible stack of heavy boxes or stones while at "
        "least two nearby adults walk past without noticing"
    )
    assert scene.symbolic_visual == expected
    assert "paper heart or stone" not in scene.symbolic_visual.lower()
    assert scene.cast_archetype == "adult_woman_or_man"


def test_efforts_going_unnoticed_repairs_exact_dry_run_record_idempotently():
    plan, scene = _preflight_scene(
        "Efforts going unnoticed.",
        "A small plant.",
        metaphor="Unseen growth.",
        cast_archetype="symbolic_object",
        main_character_or_object="Plant",
    )

    expected_visual = (
        "one adult carrying a visible stack of heavy boxes or stones while at "
        "least two nearby adults walk past without noticing"
    )
    expected_object = "adult carrying visible weight while others pass"
    first_state = (
        scene.symbolic_visual,
        scene.main_character_or_object,
        scene.cast_archetype,
        scene.symbolic_preflight_status,
        list(scene.symbolic_preflight_failure_reasons),
        scene.symbolic_preflight_repaired,
    )
    assert scene.symbolic_visual == expected_visual
    assert scene.main_character_or_object == expected_object
    assert scene.cast_archetype == "adult_woman_or_man"

    enforce_symbolic_reel_plan(plan)

    assert (
        scene.symbolic_visual,
        scene.main_character_or_object,
        scene.cast_archetype,
        scene.symbolic_preflight_status,
        scene.symbolic_preflight_failure_reasons,
        scene.symbolic_preflight_repaired,
    ) == first_state


def test_isolation_in_crowd_repairs_exact_dry_run_record_idempotently():
    plan, scene = _preflight_scene(
        "Isolation in a crowd.",
        "One figure apart from many.",
        metaphor="Loneliness.",
        cast_archetype="adult_woman_or_man",
        main_character_or_object="Isolated figure",
    )

    expected_visual = (
        "one isolated adult spatially separated from one clearly visible group of "
        "at least three adults"
    )
    expected_object = "isolated adult and a group of at least three adults"
    first_state = (
        scene.symbolic_visual,
        scene.main_character_or_object,
        scene.cast_archetype,
        scene.symbolic_preflight_status,
        list(scene.symbolic_preflight_failure_reasons),
        scene.symbolic_preflight_repaired,
    )
    assert scene.symbolic_visual == expected_visual
    assert scene.main_character_or_object == expected_object
    assert scene.cast_archetype == "adult_woman_or_man"

    enforce_symbolic_reel_plan(plan)

    assert (
        scene.symbolic_visual,
        scene.main_character_or_object,
        scene.cast_archetype,
        scene.symbolic_preflight_status,
        scene.symbolic_preflight_failure_reasons,
        scene.symbolic_preflight_repaired,
    ) == first_state


def test_exact_nighttime_aliases_repair_object_only_anchor():
    _, scene = _preflight_scene(
        "Nighttime heaviness",
        "An anchor",
        metaphor="Weight of night",
        cast_archetype="symbolic_object",
    )

    expected = (
        "one adult sitting alone beneath a large dim moon with a heavy stone "
        "resting beside them, no ocean, ship, anchor poster, ghost, creature, or "
        "object-only composition"
    )
    assert scene.symbolic_visual == expected
    assert scene.cast_archetype == "adult_woman_or_man"
    assert "unreadable_object_only_metaphor" in scene.symbolic_preflight_failure_reasons


def test_latest_eight_scene_plan_uses_specific_repairs():
    scene_values = (
        ("Carrying hidden emotional burdens", "Burden of silence", "A person carrying a stone", "A person carrying a stone"),
        ("The facade of being okay", "Hiding exhaustion", "A simple mask", "A simple mask"),
        ("Feeling measured against others", "Comparison", "Two silhouettes", "Two silhouettes"),
        ("Efforts going unnoticed.", "Unseen growth.", "A small plant.", "Plant"),
        ("Isolation in a crowd.", "Loneliness.", "One figure apart from many.", "Isolated figure"),
        ("Nighttime heaviness", "Weight of night", "An anchor", "An anchor"),
        ("Loss of words through silence", "Silence", "A closed mouth", "A closed mouth"),
        ("The relief of letting go", "Release", "A balloon drifting away", "A balloon drifting away"),
    )
    scenes = [
        Scene(
            scene_index=index,
            title=f"Scene {index}",
            voice_script=meaning,
            scene_meaning=meaning,
            emotional_metaphor=metaphor,
            symbolic_visual=visual,
            main_character_or_object=main_character_or_object,
            cast_archetype="symbolic_object" if index == 6 else "",
        )
        for index, (meaning, metaphor, visual, main_character_or_object) in enumerate(
            scene_values,
            start=1,
        )
    ]
    plan = TellaScenePlan(
        title="Latest symbolic dry-run regression",
        language="en",
        aspect_ratio="9:16",
        media_source="ai_image",
        duration_mode="short",
        theme="minimalist_symbolic_reel",
        scenes=scenes,
    )

    enforce_symbolic_reel_plan(plan)

    expected_visual_starts = (
        "one clearly drawn adult carrying a large cracked stone",
        "one adult showing a small calm smile",
        "two clearly drawn adult figures",
        "one adult carrying a visible stack of heavy boxes or stones",
        "one isolated adult spatially separated",
        "one adult sitting alone beneath a large dim moon",
        "one adult inside a quiet circle",
        "one adult placing a stone down",
    )
    expected_objects = (
        "adult carrying a heavy cracked stone",
        "adult figure with calm smile and hidden burden",
        "two adult figures and a visible comparison cue",
        "adult carrying visible weight while others pass",
        "isolated adult and a group of at least three adults",
        "adult beneath a dim moon with a nearby heavy stone",
        "adult in a quiet circle with empty speech bubbles",
        "adult putting down a stone or releasing a bird",
    )
    for scene, expected_start, expected_object in zip(
        plan.scenes,
        expected_visual_starts,
        expected_objects,
        strict=True,
    ):
        assert scene.symbolic_visual.startswith(expected_start)
        assert scene.main_character_or_object == expected_object
        assert "paper heart or stone" not in scene.symbolic_visual.lower()
        assert scene.cast_archetype == "adult_woman_or_man"


def test_nighttime_weight_and_nocturnal_melancholy_repair_heavy_crescent():
    plan, scene = _preflight_scene(
        "Nighttime weight.",
        "A heavy crescent moon.",
        metaphor="Nocturnal melancholy.",
        cast_archetype="symbolic_object",
        main_character_or_object="Crescent moon",
    )

    expected_visual = (
        "one adult sitting alone beneath a large dim moon with a heavy stone "
        "resting beside them, no ocean, ship, anchor poster, ghost, creature, or "
        "object-only composition"
    )
    expected_object = "adult beneath a dim moon with a nearby heavy stone"
    first_metadata = (
        scene.symbolic_preflight_status,
        list(scene.symbolic_preflight_failure_reasons),
        scene.symbolic_preflight_repaired,
        scene.symbolic_preflight_original_visual,
    )
    assert scene.symbolic_visual == expected_visual
    assert scene.main_character_or_object == expected_object
    assert scene.cast_archetype == "adult_woman_or_man"

    enforce_symbolic_reel_plan(plan)

    assert scene.symbolic_visual == expected_visual
    assert scene.main_character_or_object == expected_object
    assert scene.cast_archetype == "adult_woman_or_man"
    assert (
        scene.symbolic_preflight_status,
        scene.symbolic_preflight_failure_reasons,
        scene.symbolic_preflight_repaired,
        scene.symbolic_preflight_original_visual,
    ) == first_metadata


def test_stale_silhouette_object_normalizes_burden_metadata_and_prompt():
    plan, scene = _preflight_scene(
        "The weight of unspoken emotions.",
        "A person carrying a heavy stone.",
        main_character_or_object="Silhouette with stone",
    )

    expected_visual = (
        "one clearly drawn adult carrying a large cracked stone on their "
        "shoulders, visible facial features, no black silhouette"
    )
    expected_object = "adult carrying a heavy cracked stone"
    first_state = (
        scene.symbolic_visual,
        scene.cast_archetype,
        scene.main_character_or_object,
        scene.symbolic_preflight_status,
        list(scene.symbolic_preflight_failure_reasons),
        scene.symbolic_preflight_repaired,
    )
    assert scene.symbolic_visual == expected_visual
    assert scene.main_character_or_object == expected_object
    assert scene.cast_archetype == "adult_woman_or_man"
    assert "silhouette with stone" not in scene.image_prompt.lower()
    assert f"main character or object: {expected_object}" in scene.image_prompt

    enforce_symbolic_reel_plan(plan)

    assert (
        scene.symbolic_visual,
        scene.cast_archetype,
        scene.main_character_or_object,
        scene.symbolic_preflight_status,
        scene.symbolic_preflight_failure_reasons,
        scene.symbolic_preflight_repaired,
    ) == first_state


def test_negative_black_silhouette_constraint_does_not_trigger_repair():
    visual = (
        "one clearly drawn adult carrying a large cracked stone on their "
        "shoulders, visible facial features, no black silhouette"
    )
    _, scene = _preflight_scene(
        "The weight of unspoken emotions.",
        visual,
        main_character_or_object="adult carrying a heavy cracked stone",
    )

    assert scene.symbolic_visual == visual
    assert scene.symbolic_preflight_status == "passed"
    assert scene.symbolic_preflight_repaired is False


def test_plain_crescent_moon_without_emotional_weight_is_not_nighttime_heaviness():
    _, scene = _preflight_scene(
        "A quiet evening sky.",
        "A crescent moon.",
        metaphor="Simple evening calm.",
        cast_archetype="symbolic_object",
        main_character_or_object="Crescent moon",
    )

    assert scene.symbolic_visual == "A crescent moon."
    assert scene.main_character_or_object == "Crescent moon"
    assert scene.cast_archetype == "symbolic_object"
    assert scene.symbolic_preflight_status == "passed"
