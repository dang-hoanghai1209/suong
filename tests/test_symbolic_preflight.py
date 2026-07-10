from tella.planner.models import Scene, TellaScenePlan
from tella.planner.symbolic_reel import enforce_symbolic_reel_plan


def _preflight_scene(meaning: str, visual: str) -> tuple[TellaScenePlan, Scene]:
    scenes = [
        Scene(
            scene_index=1,
            title="Preflight target",
            voice_script=meaning,
            scene_meaning=meaning,
            symbolic_visual=visual,
            emotional_metaphor=meaning,
            main_character_or_object=visual,
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
    assert "heavy dark cloud or cracked shape" in visual
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
    assert "unequal measuring marks or a balance scale" in visual
    assert "no black silhouettes" in visual
    assert "silhouette_visual" in scene.symbolic_preflight_failure_reasons


def test_plant_in_shadow_is_repaired_for_unseen_effort_scene():
    _, scene = _preflight_scene(
        "Effort is unseen",
        "A plant in shadow",
    )

    visual = scene.symbolic_visual.lower()
    assert "adult carrying visible boxes, stones" in visual
    assert "nearby adult figures pass without noticing" in visual
    assert "not only a plant in shadow" in visual


def test_heavy_moon_is_repaired_for_nighttime_sadness_scene():
    _, scene = _preflight_scene(
        "Sadness feels heavier at night",
        "A heavy moon",
    )

    visual = scene.symbolic_visual.lower()
    assert "adult sitting alone under a dim moon" in visual
    assert "concrete stone weight nearby" in visual
    assert "no ghost, creature" in visual


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
