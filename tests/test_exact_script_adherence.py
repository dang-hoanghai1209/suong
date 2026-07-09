from tella.planner.adherence import (
    apply_exact_script_to_plan,
    enforce_minimalist_cast_adherence,
    refresh_cast_prompt_metadata,
)
from tella.planner.character_lock import apply_lock
from tella.planner.models import Scene, TellaScenePlan


def test_exact_script_mode_preserves_original_vietnamese_lines():
    script = "\n".join(
        [
            "Em đã từng nghĩ mình không đủ tốt.",
            "Rồi một ngày, em học cách ngồi yên với nỗi buồn.",
            "Và em hiểu rằng bình yên bắt đầu từ chính mình.",
        ]
    )
    plan = TellaScenePlan(
        title="Script",
        language="vi",
        aspect_ratio="9:16",
        media_source="ai_image",
        duration_mode="short",
        theme="minimalist_emotional",
        scenes=[
            Scene(scene_index=1, kind="scene", title="Một", voice_script="Planner rewrote this."),
            Scene(scene_index=2, kind="scene", title="Hai", voice_script="Planner also rewrote this."),
            Scene(scene_index=3, kind="scene", title="Ba", voice_script="Planner added a new ending."),
        ],
    )

    apply_exact_script_to_plan(plan, script)

    assert [scene.voice_script for scene in plan.scenes] == script.splitlines()
    assert " ".join(scene.voice_script for scene in plan.scenes) == " ".join(script.splitlines())


def test_exact_script_preserves_script_and_two_character_scene_metadata():
    script = "\n".join(
        [
            "Có bạn nam và nữ đứng trong cùng một căn phòng.",
            "Chàng trai không chọn mình và lặng lẽ quay đi.",
            "Sau đó, cô gái học cách bình yên một mình.",
        ]
    )
    plan = TellaScenePlan(
        title="Script",
        language="vi",
        aspect_ratio="9:16",
        media_source="ai_image",
        duration_mode="short",
        theme="minimalist_emotional",
        scenes=[
            Scene(scene_index=1, kind="scene", title="Một", voice_script="rewritten one"),
            Scene(scene_index=2, kind="scene", title="Hai", voice_script="rewritten two"),
            Scene(scene_index=3, kind="scene", title="Ba", voice_script="rewritten three"),
        ],
    )

    apply_exact_script_to_plan(plan, script)
    enforce_minimalist_cast_adherence(plan, script)
    apply_lock(plan)
    refresh_cast_prompt_metadata(plan)

    assert [scene.voice_script for scene in plan.scenes] == script.splitlines()
    assert plan.scenes[0].required_characters == ["female", "male"]
    assert plan.scenes[1].required_characters == ["female", "male"]
    assert plan.scenes[2].required_characters == ["female"]
    assert plan.scenes[0].prompt_contains_secondary_character is True
    assert plan.scenes[2].prompt_contains_secondary_character is False
