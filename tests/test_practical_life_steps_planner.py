import asyncio
import json
import re
from pathlib import Path

import pytest

import tella.cli as cli
from tella.planner.life_insight import plan_life_insight_from_script
from tella.planner.practical_life_steps import (
    _action_metadata,
    _safe_visual_object,
    _stamp_actionability,
    _step_similarity,
    _validate_visual_metadata,
    plan_practical_life_steps_from_script,
)
from tella.planner.models import Scene
from tella.recipes import get_recipe
from tella.voice_profiles import resolve_voice


_ROOT = Path(__file__).resolve().parents[1]
_VALID_FIXTURE = _ROOT / "script_practical_life_steps_test.txt"
_GENERAL_FIXTURE = _ROOT / "script_practical_life_steps_generalization_test.txt"
_LIFE_FIXTURE = _ROOT / "script_life_insight_test.txt"


def _valid_script() -> str:
    return _VALID_FIXTURE.read_text(encoding="utf-8")


def _general_script() -> str:
    return _GENERAL_FIXTURE.read_text(encoding="utf-8")


def _plan(script: str | None = None):
    return plan_practical_life_steps_from_script(
        user_script=script or _valid_script(),
        target_lang="vi",
    )


def _replace_lines(*, step_lines: list[str] | None = None, other: dict[int, str] | None = None) -> str:
    lines = _valid_script().splitlines()
    if step_lines:
        lines[2:5] = step_lines
    for index, value in (other or {}).items():
        lines[index] = value
    return "\n".join(lines)


def _fail_call(*args, **kwargs):
    raise AssertionError("external planner/provider/TTS/QC/render call was reached")


def test_recipe_registration_and_voice_defaults_are_exact():
    recipe = get_recipe("practical_life_steps_v1")
    resolution = resolve_voice(
        recipe_profile_id=recipe.voice_profile_id,
        narrative_mode=recipe.narrative_mode,
    )

    assert recipe.recipe_version == 1
    assert recipe.status == "production"
    assert recipe.narrative_mode == "practical_steps"
    assert recipe.planner_id == "practical_life_steps"
    assert recipe.visual_theme_id == "practical_life_steps"
    assert recipe.voice_profile_id == "clear_female_vi"
    assert recipe.subtitle_style_id == "practical_steps_reel"
    assert recipe.transition_profile_id == "clean_progressive_cut"
    assert recipe.motion_profile_id == "gentle_progressive_motion"
    assert recipe.scene_range == [7, 8]
    assert recipe.duration_range == [32.0, 38.0]
    assert resolution.resolved_tts_provider == "edge"
    assert resolution.resolved_voice == "vi-VN-HoaiMyNeural"
    assert resolution.resolved_voice_rate == "-2%"
    assert resolution.voice_profile_compatibility_status == "compatible"


def test_direct_voice_override_precedence_remains_field_by_field():
    resolution = resolve_voice(
        explicit_provider="google",
        explicit_voice="custom-voice",
        recipe_profile_id="clear_female_vi",
        narrative_mode="practical_steps",
    )

    assert resolution.resolved_tts_provider == "google"
    assert resolution.resolved_voice == "custom-voice"
    assert resolution.resolved_voice_rate == "-2%"
    assert resolution.direct_override_fields == ["provider", "voice"]


def test_valid_plan_has_required_roles_duration_and_exactly_three_steps():
    plan = _plan()
    steps = [scene for scene in plan.scenes if scene.scene_role == "practical_step"]

    assert len(plan.scenes) == 7
    assert [scene.scene_role for scene in plan.scenes] == [
        "hook",
        "context",
        "practical_step",
        "practical_step",
        "practical_step",
        "common_mistake",
        "today_action",
    ]
    assert [scene.step_number for scene in steps] == [1, 2, 3]
    assert 32 <= plan.fitted_estimated_duration_seconds <= 38
    assert plan.duration_validation_status == "passed"
    assert plan.practical_validation_status == "passed"


def test_eight_scene_context_split_keeps_exactly_three_numbered_steps():
    lines = _valid_script().splitlines()
    lines[1:2] = [
        "Vấn đề thường xuất hiện khi mục tiêu chưa rõ.",
        "Môi trường có tín hiệu gây xao nhãng.",
    ]

    plan = _plan("\n".join(lines))
    steps = [scene for scene in plan.scenes if scene.scene_role == "practical_step"]

    assert len(plan.scenes) == 8
    assert [scene.scene_role for scene in plan.scenes[:3]] == [
        "hook",
        "context_part_one",
        "context_part_two",
    ]
    assert [scene.step_number for scene in steps] == [1, 2, 3]
    assert [scene.title for scene in steps] == [
        "Practical Step 1",
        "Practical Step 2",
        "Practical Step 3",
    ]


def test_compliant_fixture_remains_unchanged():
    plan = _plan()
    original_lines = _valid_script().splitlines()

    assert [scene.voice_script for scene in plan.scenes] == original_lines
    assert plan.original_total_word_count == plan.fitted_total_word_count == 107
    assert plan.original_estimated_duration_seconds == pytest.approx(37.77)
    assert plan.fitted_estimated_duration_seconds == pytest.approx(37.77)
    assert plan.narration_fit_required is False
    assert plan.narration_fit_applied is False
    assert plan.narration_fit_status == "not_required"
    assert all(not scene.narration_rewritten for scene in plan.scenes)


def test_steps_are_specific_immediate_free_and_complete():
    steps = [scene for scene in _plan().scenes if scene.scene_role == "practical_step"]

    assert [scene.action_verb for scene in steps] == ["viết", "đặt", "chuẩn bị"]
    assert all(scene.required_subject for scene in steps)
    assert all(scene.required_object for scene in steps)
    assert all(scene.action_condition for scene in steps)
    assert all(scene.immediate_action_possible for scene in steps)
    assert not any(scene.requires_purchase for scene in steps)
    assert not any(scene.requires_paid_service for scene in steps)
    assert all(scene.practical_specificity_score >= 0.75 for scene in steps)
    assert all(scene.actionability_status == "passed" for scene in steps)
    assert {scene.required_subject for scene in steps} == {"người xem"}


@pytest.mark.parametrize(
    "text",
    (
        "Hãy yêu bản thân hơn mỗi ngày.",
        "Hãy cố gắng tốt hơn trong mọi việc.",
        "Bạn chỉ cần suy nghĩ tích cực hơn.",
    ),
)
def test_vague_only_advice_fails_actionability(text):
    metadata = _action_metadata(text)

    assert metadata.is_actionable is False
    assert "missing observable action verb" in metadata.failure_reasons


def test_vague_idea_with_concrete_followup_can_pass():
    metadata = _action_metadata(
        "Hãy tập trung vào bản thân bằng cách viết một giới hạn rõ ràng trước khi trả lời."
    )

    assert metadata.is_actionable is True
    assert metadata.action_verb == "viết"
    assert metadata.required_object


def test_duplicate_write_steps_fail_pairwise_analysis():
    script = _replace_lines(
        step_lines=[
            "Viết một kế hoạch việc cần làm trước khi bắt đầu.",
            "Viết một danh sách việc cần làm trước khi bắt đầu.",
            "Ghi ra nhiệm vụ cần làm trước khi bắt đầu.",
        ]
    )

    with pytest.raises(ValueError, match="not materially distinct"):
        _plan(script)


def test_duplicate_notification_operations_are_detected():
    first = Scene(scene_index=1, voice_script="Tắt thông báo trong hai mươi phút.")
    second = Scene(scene_index=2, voice_script="Im lặng thông báo trong hai mươi phút.")
    _stamp_actionability(first)
    _stamp_actionability(second)

    assert _step_similarity(first, second) >= 0.68


def test_three_materially_distinct_actions_pass_duplicate_validation():
    plan = _plan()

    assert plan.pairwise_step_similarity == {
        "1-2": pytest.approx(0.012),
        "1-3": pytest.approx(0.1),
        "2-3": pytest.approx(0.011),
    }
    assert plan.maximum_duplicate_step_score == pytest.approx(0.1)
    assert plan.duplicate_step_pairs == []
    assert plan.distinct_step_count == 3
    assert plan.duplicate_step_validation_status == "passed"


def test_visual_planning_is_wordless_and_never_requests_generated_labels():
    plan = _plan()
    forbidden = re.compile(
        r"\b(readable text|step number|label|logo|watermark|speech bubble text)\b",
        flags=re.IGNORECASE,
    )

    assert all(scene.visual_text_required is False for scene in plan.scenes)
    assert all(scene.visual_action for scene in plan.scenes)
    assert all(scene.visual_object for scene in plan.scenes)
    assert all(forbidden.search(scene.image_prompt) is None for scene in plan.scenes)
    assert all("wordless" in scene.image_prompt for scene in plan.scenes)


def test_every_step_visual_action_has_valid_subject_action_object_and_condition():
    steps = [scene for scene in _plan().scenes if scene.step_number]

    assert all(scene.visual_action.startswith("visible adult ") for scene in steps)
    assert all(scene.visual_action_subject_present for scene in steps)
    assert all(scene.visual_action_verb_present for scene in steps)
    assert all(scene.visual_action_object_present for scene in steps)
    assert all(scene.visual_action_condition_preserved for scene in steps)
    assert all(scene.visual_action_language_consistent for scene in steps)
    assert all(scene.visual_action_provider_safe for scene in steps)
    assert all(scene.visual_metadata_status == "passed" for scene in steps)
    assert all(scene.visual_metadata_failure_reasons == [] for scene in steps)


def test_generic_verb_only_visual_action_fails_object_validation():
    step = next(scene for scene in _plan().scenes if scene.step_number == 1)
    step.visual_action = "visible adult writing"

    _validate_visual_metadata(step)

    assert step.visual_action_subject_present is True
    assert step.visual_action_verb_present is True
    assert step.visual_action_object_present is False
    assert step.visual_metadata_status == "failed"
    assert "normalized action object is missing" in step.visual_metadata_failure_reasons


def test_visual_metadata_uses_consistent_provider_safe_english_without_numbers():
    steps = [scene for scene in _plan().scenes if scene.step_number]
    forbidden = re.compile(
        r"\b(?:readable text|readable writing|readable numbers?|labels?|logos?|watermarks?)\b",
        flags=re.IGNORECASE,
    )

    for scene in steps:
        metadata = " ".join(
            (scene.visual_action, scene.visual_object, scene.visual_environment)
        )
        assert metadata.isascii()
        assert re.search(r"\d", metadata) is None
        assert forbidden.search(metadata) is None
        assert scene.action_verb not in scene.visual_action


@pytest.mark.parametrize(
    "value",
    (
        "ghi chú cho ngày mai",
        "tin nhắn trên điện thoại",
        "lịch công việc",
        "màn hình máy tính",
    ),
)
def test_note_calendar_message_and_device_objects_are_sanitized(value):
    result = _safe_visual_object(value)

    assert result.isascii()
    assert re.search(r"\d", result) is None
    assert "readable text" not in result


def test_visual_objects_match_each_practical_action():
    steps = [scene for scene in _plan().scenes if scene.step_number]

    assert "note card" in steps[0].visual_object
    assert "note card" in steps[0].visual_action
    assert "phone" in steps[1].visual_object
    assert "arm's reach" in steps[1].visual_action
    assert "study papers" in steps[2].visual_object
    assert "desk" in steps[2].visual_environment


@pytest.mark.parametrize(
    "unsafe_step",
    (
        "Ngừng thuốc điều trị trong hai ngày để tự kiểm tra phản ứng.",
        "Theo dõi bí mật điện thoại của người khác trong một tuần.",
        "Đầu tư toàn bộ tiền tiết kiệm vào một lựa chọn duy nhất.",
    ),
)
def test_dangerous_or_high_stakes_advice_is_rejected(unsafe_step):
    script = _replace_lines(step_lines=[unsafe_step, *(_valid_script().splitlines()[3:5])])

    with pytest.raises(ValueError, match="high-stakes|unsafe confrontation"):
        _plan(script)


def test_unsupported_guaranteed_claim_is_rejected():
    lines = _valid_script().splitlines()
    lines[2] = (
        "Viết một câu nêu rõ việc cần hoàn thành trước khi bắt đầu và đảm bảo thành công."
    )

    with pytest.raises(ValueError, match="unsupported guaranteed"):
        _plan("\n".join(lines))


def test_safety_and_overlap_diagnostics_pass_for_valid_plan():
    plan = _plan()

    assert plan.safety_status == "passed"
    assert plan.safety_failure_reasons == []
    assert plan.unsupported_claims == []
    assert plan.high_stakes_advice_detected is False
    assert plan.emotional_symbolic_overlap_score < 0.42
    assert plan.life_insight_symbolic_overlap_score < 0.42
    assert plan.practical_action_density == pytest.approx(0.571)
    assert plan.reflective_statement_ratio == 0.0
    assert plan.overlap_validation_status == "passed"


def test_plan_with_too_little_action_density_fails():
    script = _replace_lines(
        step_lines=[
            "Hãy yêu bản thân hơn trong những ngày khó khăn.",
            "Bạn nên suy nghĩ tích cực hơn trong mọi hoàn cảnh.",
            "Bạn cần mạnh mẽ hơn khi gặp một trở ngại.",
        ]
    )

    with pytest.raises(ValueError, match="action density"):
        _plan(script)


def test_generalization_fitting_is_deterministic_complete_and_semantic():
    first = _plan(_general_script())
    second = _plan(_general_script())

    assert first.model_dump() == second.model_dump()
    assert first.original_total_word_count == 188
    assert first.fitted_total_word_count == 100
    assert first.original_estimated_duration_seconds == pytest.approx(64.78)
    assert first.fitted_estimated_duration_seconds == pytest.approx(35.43)
    assert first.narration_fit_required is True
    assert first.narration_fit_applied is True
    assert first.narration_fit_pass_count == 1
    assert first.narration_fit_status == "passed"
    assert all(scene.narration_rewritten for scene in first.scenes)
    assert first.scenes[0].rewrite_operations == ["remove_redundant_secondary_sentence"]
    assert first.scenes[1].rewrite_operations == ["remove_redundant_secondary_sentence"]
    assert first.scenes[5].rewrite_operations == ["remove_redundant_secondary_sentence"]
    assert first.scenes[6].rewrite_operations == ["remove_redundant_secondary_sentence"]
    for scene in first.scenes[2:5]:
        assert "preserve_secondary_action_object" in scene.rewrite_operations
        assert "retain_distinguishing_detail" in scene.rewrite_operations
    assert all(scene.voice_script[-1] in ".!?…" for scene in first.scenes)
    assert all(scene.vietnamese_naturalness_status == "passed" for scene in first.scenes)


def test_stronger_generalization_retains_essential_second_sentence_details():
    valid = _plan()
    general = _plan(_general_script())
    steps = [scene for scene in general.scenes if scene.step_number]

    assert [scene.voice_script for scene in general.scenes] != [
        scene.voice_script for scene in valid.scenes
    ]
    assert "thẻ trống" in steps[0].required_object
    assert steps[0].action_condition == "trước khi bắt đầu"
    assert steps[1].required_object == "điện thoại ngoài tầm tay"
    assert "hai mươi lăm phút" in steps[1].action_condition
    assert "sách và giấy" in steps[2].required_object
    assert steps[2].action_condition == "trước phiên tập trung"
    assert [scene.step_number for scene in steps] == [1, 2, 3]
    assert 32 <= general.fitted_estimated_duration_seconds <= 38


def test_planner_source_contains_no_fixture_specific_sentences():
    source = (
        _ROOT / "tella" / "planner" / "practical_life_steps.py"
    ).read_text(encoding="utf-8")

    assert "Bạn thường mất tập trung ngay khi bắt đầu" not in source
    assert "Cảm giác bận rộn khiến bạn chuyển qua nhiều việc nhỏ" not in source
    assert "Khoảng cách nhỏ này giảm phản xạ kiểm tra" not in source
    assert "Khi nhiều việc cùng mở, bạn dễ chuyển qua lại" not in source
    assert "Viết một kết quả duy nhất lên thẻ trống" not in source


def test_plan_metadata_serializes_practical_contract():
    data = _plan().model_dump()
    step = next(scene for scene in data["scenes"] if scene["step_number"] == 1)

    assert data["pairwise_step_similarity"]
    assert data["duration_validation_status"] == "passed"
    assert data["overlap_validation_status"] == "passed"
    assert step["action_verb"] == "viết"
    assert step["visual_text_required"] is False


def test_existing_recipe_definitions_and_life_planner_are_unchanged():
    emotional = get_recipe("emotional_symbolic_v1")
    insight = get_recipe("life_insight_symbolic_v1")
    life_plan = plan_life_insight_from_script(
        user_script=_LIFE_FIXTURE.read_text(encoding="utf-8"),
        target_lang="vi",
    )

    assert emotional.visual_theme_id == "minimalist_symbolic_reel"
    assert emotional.voice_profile_id == "soft_female_vi"
    assert insight.visual_theme_id == "life_insight_symbolic"
    assert insight.voice_profile_id == "firm_male_vi"
    assert [scene.scene_role for scene in life_plan.scenes] == [
        "hook",
        "behavior",
        "false_belief",
        "underlying_truth",
        "concrete_sign",
        "consequence",
        "mature_perspective",
        "conclusion",
    ]


def test_normal_cli_render_is_blocked_before_external_calls(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "run_pipeline", _fail_call)
    monkeypatch.setattr(cli, "fetch_assets", _fail_call)
    monkeypatch.setattr(cli, "synthesize_all", _fail_call)
    monkeypatch.setattr(cli, "render", _fail_call)

    result = cli.main(
        [
            "--script-file",
            str(_VALID_FIXTURE),
            "--recipe",
            "practical_life_steps_v1",
            "--lang",
            "vi",
            "--out",
            str(tmp_path),
            "--job-id",
            "blocked",
        ]
    )

    assert result == 2
    assert not (tmp_path / "blocked").exists()


def test_direct_pipeline_guard_runs_before_external_calls(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "fetch_assets", _fail_call)
    monkeypatch.setattr(cli, "synthesize_all", _fail_call)
    monkeypatch.setattr(cli, "render", _fail_call)

    with pytest.raises(RuntimeError, match="planner-only"):
        asyncio.run(
            cli.run_pipeline(
                topic="exact script",
                target_lang="vi",
                theme="practical_life_steps",
                media_source="ai_image",
                duration_mode="short",
                aspect_ratio="9:16",
                voice_pace_name=None,
                voice_rate_custom="-2%",
                voice_gender="female",
                out_root=tmp_path,
                user_script=_valid_script(),
                recipe=get_recipe("practical_life_steps_v1"),
            )
        )

    assert list(tmp_path.iterdir()) == []


def test_dry_run_plan_makes_zero_external_calls(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "fetch_assets", _fail_call)
    monkeypatch.setattr(cli, "synthesize_all", _fail_call)
    monkeypatch.setattr(cli, "render", _fail_call)
    monkeypatch.setattr(cli, "translate_topic", _fail_call)
    monkeypatch.setattr(cli, "plan_story", _fail_call)
    monkeypatch.setattr(cli, "plan_story_from_script", _fail_call)

    result = cli.main(
        [
            "--script-file",
            str(_VALID_FIXTURE),
            "--recipe",
            "practical_life_steps_v1",
            "--lang",
            "vi",
            "--dry-run-plan",
            "--out",
            str(tmp_path),
            "--job-id",
            "dry",
        ]
    )

    assert result == 0
    assert {item.name for item in (tmp_path / "dry").iterdir()} == {
        "plan.json",
        "recipe.json",
    }
    data = json.loads((tmp_path / "dry" / "plan.json").read_text(encoding="utf-8"))
    assert data["ai_images_requested"] == 0
    assert data["total_vision_qc_calls"] == 0
    assert data["narration_audio_filename"] == ""
