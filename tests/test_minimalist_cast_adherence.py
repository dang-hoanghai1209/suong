from tella.planner.adherence import (
    enforce_minimalist_cast_adherence,
    refresh_cast_prompt_metadata,
)
from tella.planner.character_lock import apply_lock
from tella.planner.models import Scene, TellaScenePlan


def _plan(topic: str = "") -> TellaScenePlan:
    return TellaScenePlan(
        title=topic or "Two character memory",
        language="vi",
        aspect_ratio="9:16",
        media_source="ai_image",
        duration_mode="short",
        theme="minimalist_emotional",
        scenes=[
            Scene(
                scene_index=1,
                kind="scene",
                title="Mở đầu",
                voice_script="Có bạn nam và nữ đứng rất xa nhau.",
                image_prompt="quiet bedroom memory",
                stock_query="quiet bedroom",
            ),
            Scene(
                scene_index=2,
                kind="scene",
                title="Không chọn",
                voice_script="Chàng trai không chọn mình.",
                image_prompt="he turns away near the doorway",
                stock_query="doorway",
            ),
            Scene(
                scene_index=3,
                kind="scene",
                title="Bình yên",
                voice_script="Cô ấy học cách bình yên một mình.",
                image_prompt="she sits beside a warm lamp",
                stock_query="warm lamp",
            ),
        ],
    )


def test_two_character_topic_preserves_male_and_female_in_opening_scenes():
    plan = _plan("Một câu chuyện có bạn nam và nữ, chàng trai không chọn mình")

    enforce_minimalist_cast_adherence(plan, plan.title)
    apply_lock(plan)
    refresh_cast_prompt_metadata(plan)

    assert plan.primary_character is not None
    assert plan.secondary_character is not None
    assert plan.scenes[0].required_characters == ["female", "male"]
    assert plan.scenes[1].required_characters == ["female", "male"]
    assert "young Vietnamese woman" in plan.scenes[0].image_prompt
    assert "young Vietnamese man" in plan.scenes[0].image_prompt
    assert "young Vietnamese woman" in plan.scenes[1].image_prompt
    assert "young Vietnamese man" in plan.scenes[1].image_prompt
    assert plan.scenes[0].prompt_contains_secondary_character is True
    assert plan.scenes[1].prompt_contains_secondary_character is True


def test_later_healing_scenes_allow_only_female_protagonist():
    plan = _plan("Một câu chuyện có bạn nam và nữ, chàng trai không chọn mình")

    enforce_minimalist_cast_adherence(plan, plan.title)
    apply_lock(plan)
    refresh_cast_prompt_metadata(plan)

    healing_scene = plan.scenes[2]
    assert healing_scene.required_characters == ["female"]
    assert healing_scene.character_names == ["female protagonist"]
    assert "young Vietnamese man" not in healing_scene.image_prompt
    assert healing_scene.prompt_contains_secondary_character is False


def test_minimalist_fallback_template_preserves_required_secondary_character():
    plan = _plan()
    plan.scenes[0].required_characters = ["female", "male"]

    apply_lock(plan)
    refresh_cast_prompt_metadata(plan)

    prompt = plan.scenes[0].image_prompt
    assert "young Vietnamese man" in prompt
    assert "exactly two characters only" in prompt
    assert "no second character" not in prompt
    assert plan.scenes[0].prompt_contains_secondary_character is True
