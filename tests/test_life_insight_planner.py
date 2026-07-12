import json
import logging
from pathlib import Path

import pytest

import tella.cli as cli
from tella._voice_pace import VoicePace
from tella.planner.life_insight import (
    _evaluate_evidence_fidelity,
    _evaluate_semantic_fidelity,
    _evaluate_surface_quality,
    _vietnamese_naturalness_errors,
    plan_life_insight_from_script,
)
from tella.recipes import get_recipe, validate_recipe_run
from tella.voice_profiles import resolve_voice

_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = _ROOT / "script_life_insight_test.txt"
_GENERALIZATION_FIXTURE = _ROOT / "script_life_insight_generalization_test.txt"
_FIRM_MALE_PACE = VoicePace(name="custom", edge_rate="-5%", google_rate=0.95)


def _script() -> str:
    return _FIXTURE.read_text(encoding="utf-8")


def _generalization_script() -> str:
    return _GENERALIZATION_FIXTURE.read_text(encoding="utf-8")


def _fail_call(*args, **kwargs):
    raise AssertionError("provider, TTS, QC, render, or Gemini planner was called")


def test_life_insight_recipe_definition_is_registered():
    recipe = get_recipe("life_insight_symbolic_v1")

    assert recipe.planner_id == "life_insight_symbolic"
    assert recipe.visual_theme_id == "life_insight_symbolic"
    assert recipe.voice_profile_id == "firm_male_vi"
    assert recipe.subtitle_style_id == "insight_reel"
    assert recipe.transition_profile_id == "clean_soft_cut"
    assert recipe.motion_profile_id == "controlled_slow_pan"
    assert recipe.scene_range == [7, 8]
    assert recipe.duration_range == [32.0, 38.0]


def test_eight_scene_structure_and_required_roles():
    plan = plan_life_insight_from_script(user_script=_script(), target_lang="vi")

    assert len(plan.scenes) == 8
    assert [scene.scene_role for scene in plan.scenes] == [
        "hook",
        "behavior",
        "false_belief",
        "underlying_truth",
        "concrete_sign",
        "consequence",
        "mature_perspective",
        "conclusion",
    ]
    assert plan.life_insight_validation_status == "passed"
    assert plan.life_insight_validation_errors == []


def test_seven_scene_structure_allows_one_meaningful_middle_merge():
    lines = [line for line in _script().splitlines() if line.strip()]
    seven_scene_script = "\n".join([*lines[:4], *lines[5:]])

    plan = plan_life_insight_from_script(
        user_script=seven_scene_script,
        target_lang="vi",
    )

    assert len(plan.scenes) == 7
    assert plan.scenes[3].scene_role == "underlying_truth_and_concrete_sign"
    assert plan.scenes[-1].scene_role == "conclusion"


@pytest.mark.parametrize("count", [6, 9])
def test_scene_count_outside_seven_or_eight_is_rejected(count):
    lines = [line for line in _script().splitlines() if line.strip()]
    while len(lines) < count:
        lines.append(lines[-1])

    with pytest.raises(ValueError, match="exactly 7 or 8"):
        plan_life_insight_from_script(
            user_script="\n".join(lines[:count]),
            target_lang="vi",
        )


def test_duration_contract_and_per_scene_estimates():
    plan = plan_life_insight_from_script(user_script=_script(), target_lang="vi")
    recipe = get_recipe("life_insight_symbolic_v1")

    assert 32 <= plan.life_insight_estimated_duration_seconds <= 38
    assert all(
        3.8 <= scene.estimated_duration_seconds <= 5.2
        for scene in plan.scenes[:-1]
    )
    assert 3.8 <= plan.scenes[-1].estimated_duration_seconds <= 5.5
    assert validate_recipe_run(recipe, estimated_duration_seconds=31.9)
    assert validate_recipe_run(recipe, estimated_duration_seconds=38.1)
    assert validate_recipe_run(
        recipe,
        estimated_duration_seconds=plan.life_insight_estimated_duration_seconds,
    ) == []


def test_existing_fixture_is_not_unnecessarily_rewritten():
    plan = plan_life_insight_from_script(
        user_script=_script(),
        target_lang="vi",
        voice_pace=_FIRM_MALE_PACE,
    )

    assert plan.original_estimated_duration_seconds == 37.25
    assert plan.fitted_estimated_duration_seconds == 37.25
    assert plan.original_total_word_count == 104
    assert plan.fitted_total_word_count == 104
    assert plan.narration_fit_required is False
    assert plan.narration_fit_applied is False
    assert plan.narration_fit_status == "not_required"
    assert plan.seven_scene_fallback_considered is False
    assert plan.seven_scene_fallback_applied is False
    assert plan.semantic_fidelity_status == "passed"
    assert plan.vietnamese_naturalness_status == "passed"
    assert plan.final_surface_validation_status == "passed"
    assert plan.final_surface_repairs_applied == 0
    assert all(
        scene.voice_script == scene.original_voice_script for scene in plan.scenes
    )


def test_generalization_fixture_is_semantically_fitted_to_preferred_duration():
    plan = plan_life_insight_from_script(
        user_script=_generalization_script(),
        target_lang="vi",
        voice_pace=_FIRM_MALE_PACE,
    )

    assert plan.original_estimated_duration_seconds == 53.94
    assert 34 <= plan.fitted_estimated_duration_seconds <= 36
    assert plan.original_total_word_count == 154
    assert plan.fitted_total_word_count == 101
    assert plan.narration_fit_required is True
    assert plan.narration_fit_applied is True
    assert plan.narration_fit_pass_count == 2
    assert plan.narration_fit_status == "passed"
    assert plan.seven_scene_fallback_considered is True
    assert plan.seven_scene_fallback_applied is True
    assert plan.semantic_fidelity_status == "passed"
    assert plan.vietnamese_naturalness_status == "passed"
    assert plan.final_surface_validation_status == "passed"
    assert plan.final_surface_repairs_applied == 2
    assert plan.final_surface_failure_count == 0
    assert all(
        scene.fitted_estimated_duration_seconds <= (
            7.8
            if scene.scene_role in {
                "underlying_truth_and_concrete_sign",
                "conclusion",
            }
            else 5.8
            if scene.scene_role in {"consequence", "mature_perspective"}
            else 5.2
        )
        for scene in plan.scenes
    )


def test_generalization_fit_preserves_roles_evidence_and_distinct_takeaway():
    plan = plan_life_insight_from_script(
        user_script=_generalization_script(),
        target_lang="vi",
        voice_pace=_FIRM_MALE_PACE,
    )

    assert [scene.scene_role for scene in plan.scenes] == list(
        (
            "hook",
            "behavior",
            "false_belief",
            "underlying_truth_and_concrete_sign",
            "consequence",
            "mature_perspective",
            "conclusion",
        )
    )
    assert "biến mất" in plan.scenes[3].observable_evidence
    assert plan.scenes[5].voice_script != plan.scenes[6].voice_script
    assert "hành động nhất quán" in plan.scenes[5].voice_script
    assert "khi cần bạn" in plan.scenes[6].voice_script
    assert "khi không cần gì" in plan.scenes[6].voice_script


def test_generalization_fit_keeps_anchors_and_complete_vietnamese_clauses():
    plan = plan_life_insight_from_script(
        user_script=_generalization_script(),
        target_lang="vi",
        voice_pace=_FIRM_MALE_PACE,
    )
    dangling = {"và", "nhưng", "vì", "khi", "nếu"}

    assert all(scene.semantic_anchors_preserved for scene in plan.scenes)
    assert not any(scene.semantic_anchor_loss_detected for scene in plan.scenes)
    assert all(scene.voice_script[-1] in ".!?" for scene in plan.scenes)
    assert all(
        scene.voice_script.rstrip(".!?").split()[-1].lower() not in dangling
        for scene in plan.scenes
    )
    assert plan.scenes[1].voice_script == (
        "Khi khó khăn, họ tìm đến, khiến bạn thấy mình quan trọng."
    )
    assert plan.scenes[3].voice_script == (
        "Quan tâm thật lòng không chỉ xuất hiện khi cần nhận lại; "
        "họ lại biến mất khi vấn đề được giải quyết."
    )
    assert all(not scene.missing_semantic_relations for scene in plan.scenes)


def test_generalization_fit_is_deterministic_and_serializes_metadata():
    kwargs = {
        "user_script": _generalization_script(),
        "target_lang": "vi",
        "voice_pace": _FIRM_MALE_PACE,
        "seed": 19,
    }
    first = plan_life_insight_from_script(**kwargs)
    second = plan_life_insight_from_script(**kwargs)
    data = first.model_dump()

    assert data == second.model_dump()
    assert data["narration_fit_status"] == "passed"
    assert data["duration_reduction_seconds"] == 18.03
    assert data["duration_reduction_ratio"] > 0
    assert data["scenes"][1]["original_voice_script"].startswith("Mỗi lần")
    assert data["scenes"][1]["narration_fit_operations"]
    assert data["scenes"][1]["fitting_candidate_count"] >= 2
    assert data["scenes"][1]["selected_fitting_candidate_id"]
    assert data["scenes"][1]["preserved_semantic_anchors"] == [
        "observable_actor",
        "observable_action",
        "resulting_effect",
    ]
    assert data["scenes"][-1]["preserved_semantic_relations"] == [
        "needed_vs_not_needed_contrast",
        "comparison_guides_final_evaluation",
    ]
    assert data["final_surface_validation_status"] == "passed"
    assert data["final_surface_repairs_applied"] == 2
    assert data["scenes"][-1]["predicate_complete"] is True
    assert data["scenes"][-1]["complement_complete"] is True


@pytest.mark.parametrize(
    "candidate,missing_anchor",
    [
        (
            "Cách họ đối xử khi không cần gì cho thấy vị trí.",
            "first_comparison_condition",
        ),
        (
            "Cách họ đối xử khi cần bạn cho thấy vị trí.",
            "second_comparison_condition",
        ),
    ],
)
def test_conclusion_requires_both_sides_of_needed_contrast(
    candidate,
    missing_anchor,
):
    source = _generalization_script().splitlines()[-1]
    fidelity = _evaluate_semantic_fidelity(source, candidate, "conclusion")

    assert missing_anchor in fidelity["missing_anchors"]
    assert "needed_vs_not_needed_contrast" in fidelity["missing_relations"]


def test_generalization_uses_natural_role_specific_vietnamese():
    plan = plan_life_insight_from_script(
        user_script=_generalization_script(),
        target_lang="vi",
        voice_pace=_FIRM_MALE_PACE,
    )
    false_belief = plan.scenes[2].voice_script
    consequence = plan.scenes[4].voice_script
    perspective = plan.scenes[5].voice_script

    assert false_belief.startswith("Bạn nghĩ họ")
    assert "họ có lẽ" not in false_belief
    assert "xem bị lợi dụng" not in consequence
    assert "coi bị lợi dụng" not in consequence
    assert "coi sự lợi dụng" in consequence
    assert "hành động nhất quán" in perspective
    assert all(
        scene.vietnamese_naturalness_status == "passed" for scene in plan.scenes
    )


def test_compression_limit_requires_the_seven_scene_semantic_merge():
    plan = plan_life_insight_from_script(
        user_script=_generalization_script(),
        target_lang="vi",
        voice_pace=_FIRM_MALE_PACE,
    )
    over_limit = [scene for scene in plan.scenes if scene.compression_limit_exceeded]

    assert plan.seven_scene_fallback_applied is True
    assert [scene.scene_role for scene in over_limit] == [
        "underlying_truth_and_concrete_sign"
    ]
    assert all(
        scene.scene_compression_ratio <= 0.40
        for scene in plan.scenes
        if scene.scene_role != "underlying_truth_and_concrete_sign"
    )
    assert plan.maximum_scene_compression_ratio == over_limit[0].scene_compression_ratio


def test_naturalness_validation_rejects_telegraphic_fragments():
    errors = _vietnamese_naturalness_errors("Quan tâm. Nhất quán. Hành động.")

    assert "repeated telegraphic fragments" in errors


@pytest.mark.parametrize(
    "text,expected_reason",
    [
        (
            "Nếu tiếp tục, bạn sẽ xem bị lợi dụng là bình thường.",
            "unnatural exploitation construction",
        ),
        (
            "Bạn tự nhủ họ có lẽ chưa giỏi thể hiện tình cảm.",
            "unnatural adverb order; use 'có lẽ họ'",
        ),
        (
            "Trưởng thành là nhìn sự nhất quán trong hành động.",
            "missing preposition before 'sự nhất quán'",
        ),
    ],
)
def test_naturalness_validation_rejects_known_awkward_forms(
    text,
    expected_reason,
):
    assert expected_reason in _vietnamese_naturalness_errors(text)


@pytest.mark.parametrize(
    "text",
    [
        "Cách họ đối xử khi cần bạn và khi không cần gì cho thấy vị trí.",
        "Những dấu hiệu đó thể hiện vai trò.",
        "Các hành động nhất quán nói lên giá trị.",
    ],
)
def test_surface_guard_rejects_incomplete_abstract_complement(text):
    result = _evaluate_surface_quality(text, "conclusion")

    assert result["status"] == "failed"
    assert result["predicate_complete"] is False
    assert result["complement_complete"] is False
    assert "predicate ends with an unresolved abstract complement" in result[
        "failure_reasons"
    ]


def test_surface_guard_requires_vietnamese_preposition():
    result = _evaluate_surface_quality(
        "Trưởng thành là nhìn sự nhất quán trong hành động.",
        "mature_perspective",
    )

    assert result["status"] == "failed"
    assert result["complement_complete"] is False
    assert "required Vietnamese preposition is missing" in result[
        "failure_reasons"
    ]


def test_complete_two_condition_conclusion_passes_surface_guard():
    result = _evaluate_surface_quality(
        "Chính cách họ đối xử khi cần bạn và khi không cần gì là câu trả lời.",
        "conclusion",
    )

    assert result == {
        "status": "passed",
        "failure_reasons": [],
        "predicate_complete": True,
        "complement_complete": True,
        "clause_connection_natural": True,
        "pronoun_reference_clear": True,
    }


def test_generalization_surface_repairs_are_complete_and_connected():
    plan = plan_life_insight_from_script(
        user_script=_generalization_script(),
        target_lang="vi",
        voice_pace=_FIRM_MALE_PACE,
    )
    merged = plan.scenes[3]
    conclusion = plan.scenes[-1]

    assert merged.final_surface_repair_applied is True
    assert merged.final_surface_candidate_id == "surface_connected_truth_evidence"
    assert merged.predicate_complete is True
    assert merged.complement_complete is True
    assert merged.clause_connection_natural is True
    assert "biến mất khi vấn đề được giải quyết" in merged.voice_script
    assert "sau đó" not in merged.voice_script
    assert "vụ lợi" not in merged.voice_script
    assert conclusion.final_surface_repair_applied is True
    assert conclusion.final_surface_candidate_id == "surface_complete_conclusion"
    assert conclusion.voice_script.endswith("là câu trả lời.")
    assert conclusion.predicate_complete is True
    assert conclusion.complement_complete is True


def test_longer_complete_surface_candidate_beats_shorter_fragment():
    plan = plan_life_insight_from_script(
        user_script=_generalization_script(),
        target_lang="vi",
        voice_pace=_FIRM_MALE_PACE,
    )
    merged = plan.scenes[3]

    assert merged.final_surface_repair_applied is True
    assert merged.fitted_narration_word_count == 22
    assert merged.surface_quality_status == "passed"
    assert merged.evidence_condition_complete is True


def test_generic_after_that_fails_post_resolution_evidence_relation():
    lines = _generalization_script().splitlines()
    source = f"{lines[3]} {lines[4]}"
    candidate = (
        "Quan tâm thật lòng không chỉ xuất hiện khi cần nhận lại; "
        "họ biến mất sau đó."
    )
    evidence = _evaluate_evidence_fidelity(
        source,
        candidate,
        "underlying_truth_and_concrete_sign",
    )
    fidelity = _evaluate_semantic_fidelity(
        source,
        candidate,
        "underlying_truth_and_concrete_sign",
    )

    assert evidence["evidence_condition_complete"] is False
    assert "disappearance_after_resolution" in evidence["missing_relations"]
    assert "problem_resolution_event" in evidence["missing_relations"]
    assert "disappearance_after_resolution" in fidelity["missing_relations"]


def test_explicit_post_resolution_disappearance_preserves_evidence_relations():
    plan = plan_life_insight_from_script(
        user_script=_generalization_script(),
        target_lang="vi",
        voice_pace=_FIRM_MALE_PACE,
    )
    merged = plan.scenes[3]

    assert merged.evidence_condition_complete is True
    assert merged.observable_claims == [
        "help_or_return_needed_condition",
        "problem_resolution_event",
        "disappearance",
        "disappearance_after_resolution",
    ]
    assert merged.preserved_semantic_relations[-4:] == [
        "help_needed_condition",
        "problem_resolution_event",
        "disappearance_after_resolution",
        "contrast_between_genuine_care_and_observed_pattern",
    ]
    assert merged.missing_semantic_relations == []


def test_private_motive_label_is_rejected_but_observable_pattern_is_allowed():
    lines = _generalization_script().splitlines()
    source = f"{lines[3]} {lines[4]}"
    motive_candidate = (
        "Sự quan tâm của họ mang tính vụ lợi; "
        "họ biến mất khi vấn đề được giải quyết."
    )
    motive = _evaluate_evidence_fidelity(
        source,
        motive_candidate,
        "underlying_truth_and_concrete_sign",
    )
    observable = _evaluate_evidence_fidelity(
        source,
        "Quan tâm thật lòng không chỉ xuất hiện khi cần nhận lại; "
        "họ lại biến mất khi vấn đề được giải quyết.",
        "underlying_truth_and_concrete_sign",
    )

    assert motive["unsupported_inference_detected"] is True
    assert motive["inferred_private_motives"] == ["transactional_motive"]
    assert motive["unsupported_inference_reasons"]
    assert observable["unsupported_inference_detected"] is False
    assert observable["inferred_private_motives"] == []
    assert observable["evidence_condition_complete"] is True


def test_planner_does_not_embed_fixture_sentences():
    planner_source = (
        _ROOT / "tella" / "planner" / "life_insight.py"
    ).read_text(encoding="utf-8")

    for fixture_line in _generalization_script().splitlines():
        assert fixture_line not in planner_source


def test_scene_metadata_distinguishes_observation_from_interpretation():
    plan = plan_life_insight_from_script(user_script=_script(), target_lang="vi")

    behavior = plan.scenes[1]
    truth = plan.scenes[3]
    sign = plan.scenes[4]
    conclusion = plan.scenes[-1]
    assert behavior.claim_type == "observation"
    assert truth.claim_type == "interpretation"
    assert sign.claim_type == "observable_evidence"
    assert sign.observable_evidence
    assert conclusion.claim_type == "takeaway"
    assert conclusion.insight_strength == "strong"
    assert conclusion.conclusion_dependency == "mature_perspective"
    for scene in plan.scenes:
        assert scene.emotional_function
        assert scene.narration_word_count > 0
        assert scene.transition_purpose


def test_emotional_only_plan_is_rejected_as_recipe_overlap():
    emotional_line = "Tôi chỉ thấy buồn và cô đơn trong các ngày dài không lối thoát."
    emotional_script = "\n".join([emotional_line] * 8)

    with pytest.raises(ValueError, match="overlaps emotional reflection"):
        plan_life_insight_from_script(
            user_script=emotional_script,
            target_lang="vi",
        )


def test_planning_is_deterministic_with_fixed_seed():
    first = plan_life_insight_from_script(
        user_script=_script(),
        target_lang="vi",
        seed=42,
    )
    second = plan_life_insight_from_script(
        user_script=_script(),
        target_lang="vi",
        seed=42,
    )

    assert first.model_dump() == second.model_dump()


def test_recipe_resolves_firm_male_voice_by_default():
    recipe = get_recipe("life_insight_symbolic_v1")
    resolution = resolve_voice(
        recipe_profile_id=recipe.voice_profile_id,
        narrative_mode=recipe.narrative_mode,
    )

    assert resolution.resolved_voice_profile_id == "firm_male_vi"
    assert resolution.resolved_voice == "vi-VN-NamMinhNeural"
    assert resolution.resolved_voice_rate == "-5%"
    assert resolution.voice_profile_compatibility_status == "compatible"


def test_dry_run_is_local_and_emits_diagnostics(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="tella.cli")
    for name in (
        "translate_topic",
        "plan_story",
        "plan_story_from_script",
        "fetch_assets",
        "synthesize_all",
        "render",
    ):
        monkeypatch.setattr(cli, name, _fail_call)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)

    result = cli.main(
        [
            "--script-file",
            str(_FIXTURE),
            "--recipe",
            "life_insight_symbolic_v1",
            "--lang",
            "vi",
            "--dry-run-plan",
            "--out",
            str(tmp_path),
            "--job-id",
            "life_dry",
            "-v",
        ]
    )

    assert result == 0
    files = sorted(path.name for path in (tmp_path / "life_dry").iterdir())
    assert files == ["plan.json", "recipe.json"]
    data = json.loads(
        (tmp_path / "life_dry" / "plan.json").read_text(encoding="utf-8")
    )
    assert data["planner_id"] == "life_insight_symbolic"
    assert data["resolved_voice_profile_id"] == "firm_male_vi"
    assert data["recipe_overlap_detected"] is False
    assert data["life_insight_validation_status"] == "passed"
    assert "life_insight table" in caplog.text
    assert "life_insight row" in caplog.text


def test_duration_fitting_dry_run_makes_no_external_calls(
    monkeypatch,
    tmp_path,
    caplog,
):
    caplog.set_level(logging.INFO, logger="tella.cli")
    for name in (
        "translate_topic",
        "plan_story",
        "plan_story_from_script",
        "fetch_assets",
        "synthesize_all",
        "render",
    ):
        monkeypatch.setattr(cli, name, _fail_call)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)

    result = cli.main(
        [
            "--script-file",
            str(_GENERALIZATION_FIXTURE),
            "--recipe",
            "life_insight_symbolic_v1",
            "--lang",
            "vi",
            "--dry-run-plan",
            "--out",
            str(tmp_path),
            "--job-id",
            "life_fit_dry",
            "-v",
        ]
    )

    assert result == 0
    files = sorted(path.name for path in (tmp_path / "life_fit_dry").iterdir())
    assert files == ["plan.json", "recipe.json"]
    data = json.loads(
        (tmp_path / "life_fit_dry" / "plan.json").read_text(encoding="utf-8")
    )
    assert data["narration_fit_status"] == "passed"
    assert data["fitted_estimated_duration_seconds"] == 35.91
    assert data["seven_scene_fallback_applied"] is True
    assert data["final_surface_repairs_applied"] == 2
    assert "original=53.94s target=35.00s fitted=35.91s" in caplog.text
