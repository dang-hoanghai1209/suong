import re

from tella.media import fetch
from tella.planner.models import Scene


def _bakery_walk_scenes() -> list[Scene]:
    stale_bedroom_prompt = (
        "medium-wide bedroom scene, bed and window with thin curtains in "
        "background, bedside table, warm lamp, books or folded blanket"
    )
    scripts = [
        "Co gai di bo mot minh tren via he sau mot ngay met.",
        "Co ay nhin thay mot tiem banh nho ben duong.",
        "Co ay buoc vao tiem banh, noi co anh den am.",
        "Co ay dung truoc quay banh va chon mot chiec banh nho.",
        "Co ay cam hop banh nho trong tay.",
        "Co ay buoc ra khoi tiem, mang theo tui giay nho.",
    ]
    return [
        Scene(
            scene_index=idx,
            kind="scene",
            title=f"Scene {idx}",
            voice_script=script,
            image_prompt=stale_bedroom_prompt,
            stock_query="quiet bedroom",
        )
        for idx, script in enumerate(scripts, start=1)
    ]


def _combined_prompt(scene: Scene) -> str:
    return f"{scene.image_prompt} {fetch._minimalist_provider_prompt(scene)}".lower()


def test_bakery_walk_prompts_do_not_keep_global_bedroom_details():
    scenes = _bakery_walk_scenes()

    fetch._prepare_minimalist_image_prompts(scenes)

    for scene in scenes:
        prompt = _combined_prompt(scene)
        assert "bedroom" not in prompt
        assert "bedside table" not in prompt
        assert "folded blanket" not in prompt
        assert "window with curtains" not in prompt
        assert "window with thin curtains" not in prompt
        assert re.search(r"\bbed\b", prompt) is None


def test_bakery_walk_prompts_keep_scene_setting_and_action_by_index():
    scenes = _bakery_walk_scenes()

    fetch._prepare_minimalist_image_prompts(scenes)

    expected = [
        ("street_sidewalk", "walking_outside", ("street", "sidewalk"), ("walking",)),
        ("bakery_exterior", "noticing_bakery", ("bakery", "storefront"), ("looks",)),
        ("bakery_entrance", "entering_shop", ("bakery", "door"), ("stepping",)),
        ("bakery_counter", "choosing_cake", ("display counter", "cake"), ("choosing",)),
        ("bakery_interior", "holding_cake", ("bakery", "counter"), ("holding", "cake box")),
        ("exit_street", "leaving_shop", ("outside bakery", "sidewalk"), ("walking out", "paper bag")),
    ]
    for scene, (setting, action, setting_terms, action_terms) in zip(scenes, expected):
        prompt = _combined_prompt(scene)
        assert scene.scene_setting == setting
        assert scene.scene_action == action
        assert scene.setting_source == "bakery_sequence"
        assert scene.action_source == "bakery_sequence"
        assert scene.prompt_setting_matches_story is True
        assert scene.prompt_action_matches_story is True
        for term in setting_terms:
            assert term in prompt
        for term in action_terms:
            assert term in prompt


def test_voice_script_and_image_prompt_agree_on_bakery_actions():
    scenes = _bakery_walk_scenes()

    fetch._prepare_minimalist_image_prompts(scenes)

    voice_markers = [
        "di bo",
        "nhin thay",
        "buoc vao",
        "chon",
        "cam hop banh",
        "buoc ra",
    ]
    for scene, marker in zip(scenes, voice_markers):
        assert marker in fetch._ascii_key(scene.voice_script)
        assert scene.prompt_action_matches_story is True
        assert scene.scene_action in {
            "walking_outside",
            "noticing_bakery",
            "entering_shop",
            "choosing_cake",
            "holding_cake",
            "leaving_shop",
        }
