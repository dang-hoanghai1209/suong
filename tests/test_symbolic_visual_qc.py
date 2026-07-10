import asyncio
from pathlib import Path

import pytest
from PIL import Image

from tella.media import fetch
from tella.media.visual_qc import (
    _missing_symbolic_qc_fields,
    _result_from_vision,
    apply_qc_result_to_scene,
)
from tella.planner.models import (
    Scene,
    SceneQCResult,
    StyleBible,
    TellaScenePlan,
    VisualBible,
)
from tella.planner.symbolic_reel import enforce_symbolic_reel_plan


def _scene(
    *,
    meaning: str = "one tired adult carries an unseen burden",
    symbolic_visual: str = "adult carrying a small stone",
    metaphor: str = "effort continuing without recognition",
    expected_subjects: list[str] | None = None,
) -> Scene:
    return Scene(
        scene_index=1,
        title="Symbolic scene",
        voice_script=meaning,
        image_prompt="minimalist symbolic doodle",
        scene_meaning=meaning,
        symbolic_visual=symbolic_visual,
        emotional_metaphor=metaphor,
        main_character_or_object=symbolic_visual,
        visual_mode="symbolic_listicle",
        visual_identity_id="symbolic_dusk_taupe_v1",
        cast_archetype="adult_woman_or_man",
        age_policy="adult_only_unless_script_explicitly_requests_other_age",
        palette_id="dusk_taupe_earth_limited_v1",
        line_style_id="soft_rough_pencil_consistent_v1",
        subject_scale_profile="small_to_medium_subject_with_negative_space",
        symbolic_qc_expected_subjects=expected_subjects or [symbolic_visual],
    )


def _bible() -> VisualBible:
    return VisualBible(style_bible=StyleBible())


def _anatomy() -> dict[str, str]:
    return {
        "shot_type": "medium",
        "body_visibility": "upper_body",
        "pose_type": "unknown",
        "anatomy_expectation": "upper_body_only",
    }


def _base_data(**overrides):
    data = {
        "passed": True,
        "confidence": 0.9,
        "character_count": 1,
        "main_character_visible": True,
        "head_count": 1,
        "visible_arm_count": 2,
        "visible_hand_count": 2,
        "visible_leg_count": 0,
        "visible_foot_count": 0,
        "has_extra_limbs": False,
        "has_missing_limbs": False,
        "has_duplicate_face": False,
        "has_bad_crop": False,
        "has_text_or_watermark": False,
        "hairstyle_matches_spec": True,
        "outfit_matches_spec": True,
        "action": {
            "scene_matches_requested_action": True,
            "action_mismatch_severity": "none",
        },
        "symbolic_meaning_matches": True,
        "symbolic_visual_matches": True,
        "required_subjects_present": True,
        "metaphor_is_readable": True,
        "visual_identity_matches": True,
        "adult_age_policy_matches": True,
        "style_matches_symbolic_reel": True,
        "subject_scale_matches": True,
        "palette_matches": True,
        "line_style_matches": True,
        "forbidden_drift_detected": False,
        "forbidden_drift_types": [],
        "crowd_visible": True,
        "comparison_symbols_present": True,
        "effort_or_carrying_symbol_visible": True,
        "failure_reasons": [],
    }
    data.update(overrides)
    return data


def _qc(scene: Scene, data: dict, *, expected: dict | None = None):
    expected_data = {
        "theme": "minimalist_symbolic_reel",
        "attempt": 1,
        "max_attempts_allowed": 3,
        "expected_character_count": 1,
        "symbolic_qc_expected_subjects": list(scene.symbolic_qc_expected_subjects),
        "symbolic_soft_fail_streaks": {},
        **(expected or {}),
    }
    return _result_from_vision(
        scene,
        Path("scene.jpg"),
        _bible(),
        expected_data,
        _anatomy(),
        {},
        [],
        1.0,
        True,
        {
            "available": True,
            "data": data,
            "model": "fake-vision",
            "call_count": 1,
            "parse_attempt_count": 1,
        },
    )


def test_lonely_in_crowd_fails_when_crowd_is_missing():
    scene = _scene(
        meaning="lonely in a crowd",
        symbolic_visual="one isolated adult beside a small group",
        expected_subjects=[
            "one clearly isolated adult figure",
            "one clearly visible small group or crowd",
        ],
    )

    result = _qc(
        scene,
        _base_data(character_count=1, crowd_visible=False),
        expected={"expected_character_count": 2},
    )

    assert result.passed is False
    assert "required_subject_missing" in result.symbolic_qc_failure_reasons
    assert "required_subject_missing" in result.symbolic_qc_hard_fail_reasons


def test_comparison_fails_without_second_person_or_comparison_symbol():
    scene = _scene(
        meaning="being compared with another person",
        symbolic_visual="two adults on opposite sides of a balance line",
        expected_subjects=[
            "at least two adult human figures or one unmistakable comparison symbol"
        ],
    )

    result = _qc(
        scene,
        _base_data(character_count=1, comparison_symbols_present=False),
        expected={"expected_character_count": 2},
    )

    assert result.passed is False
    assert "required_subject_missing" in result.symbolic_qc_hard_fail_reasons


def test_unseen_effort_black_blob_fails_without_effort_symbol():
    scene = _scene(
        meaning="effort is unseen",
        symbolic_visual="adult carrying a visible bundle",
        expected_subjects=[
            "one readable effort or carrying symbol",
            "one ordinary adult or concrete effort object",
        ],
    )

    result = _qc(
        scene,
        _base_data(
            character_count=0,
            main_character_visible=False,
            head_count=0,
            effort_or_carrying_symbol_visible=False,
        ),
    )

    assert "required_subject_missing" in result.symbolic_qc_hard_fail_reasons
    assert result.symbolic_visual_matches is False


def test_medical_mask_drift_hard_fails():
    result = _qc(
        _scene(meaning="trying to look okay while hurt inside"),
        _base_data(
            passed=False,
            forbidden_drift_detected=True,
            forbidden_drift_types=["medical_mask"],
            medical_mask_detected=True,
        ),
    )

    assert "medical_mask_drift" in result.symbolic_qc_hard_fail_reasons
    assert result.symbolic_qc_final_status == "hard_failed"


def test_child_drift_hard_fails_under_adult_policy():
    result = _qc(
        _scene(),
        _base_data(
            passed=False,
            adult_age_policy_matches=False,
            forbidden_drift_detected=True,
            forbidden_drift_types=["child"],
            child_detected=True,
        ),
    )

    assert result.adult_age_policy_matches is False
    assert "age_drift" in result.symbolic_qc_hard_fail_reasons


@pytest.mark.parametrize("drift_type", ["ghost", "monster", "blob_creature"])
def test_ghost_or_monster_drift_hard_fails(drift_type):
    result = _qc(
        _scene(meaning="sadness feels heavier at night"),
        _base_data(
            passed=False,
            forbidden_drift_detected=True,
            forbidden_drift_types=[drift_type],
        ),
    )

    assert "creature_drift" in result.symbolic_qc_hard_fail_reasons
    assert result.passed is False


def test_unreadable_metaphor_soft_fails_then_escalates():
    scene = _scene()
    first = _qc(scene, _base_data(metaphor_is_readable=False))

    assert first.passed is False
    assert first.symbolic_qc_hard_fail_reasons == []
    assert first.symbolic_qc_soft_fail_reasons == ["metaphor_unreadable"]
    assert first.stopped_retry_loop_early_due_to_repeated_soft_fail is False

    second = _qc(
        scene,
        _base_data(metaphor_is_readable=False),
        expected={
            "attempt": 2,
            "symbolic_soft_fail_streaks": {"metaphor_unreadable": 1},
        },
    )

    assert "metaphor_unreadable" in second.symbolic_qc_hard_fail_reasons
    assert second.repeated_soft_fail_escalation_applied is True
    assert second.stopped_retry_loop_early_due_to_repeated_soft_fail is True


def test_repaired_prompt_names_missing_crowd_requirement():
    scene = _scene(
        meaning="lonely in a crowd",
        expected_subjects=[
            "one clearly isolated adult figure",
            "one clearly visible small group or crowd",
        ],
    )
    result = _qc(
        scene,
        _base_data(character_count=1, crowd_visible=False),
        expected={"expected_character_count": 2},
    )

    repaired = result.repair_prompt.lower()
    assert repaired != scene.image_prompt.lower()
    assert "isolated adult" in repaired
    assert "small group or crowd" in repaired


def test_symbolic_qc_metadata_is_preserved_on_scene():
    scene = _scene()
    result = _qc(
        scene,
        _base_data(
            passed=False,
            forbidden_drift_detected=True,
            forbidden_drift_types=["photorealistic"],
            photorealistic_figure_detected=True,
        ),
        expected={"attempt": 2, "repaired_prompt_used": True},
    )

    apply_qc_result_to_scene(scene, result, attempts_actually_ran=2)

    assert scene.symbolic_qc_attempts == 2
    assert scene.symbolic_qc_repaired_prompt_used is True
    assert scene.symbolic_qc_final_status == "hard_failed"
    assert "photorealistic_drift" in scene.symbolic_qc_failure_reasons
    assert scene.forbidden_drift_types == ["photorealistic"]


def test_symbolic_dimensions_remain_null_without_vision_result():
    scene = _scene()
    result = SceneQCResult(
        scene_index=scene.scene_index,
        passed=True,
        final_passed=True,
        basic_qc_passed=True,
        model_qc_passed=True,
        qc_mode="basic",
        vision_available=False,
        symbolic_qc_final_status="not_run",
    )

    apply_qc_result_to_scene(scene, result)

    assert scene.symbolic_meaning_matches is None
    assert scene.symbolic_visual_matches is None
    assert scene.adult_age_policy_matches is None
    assert scene.visual_identity_matches is None
    assert scene.style_matches_symbolic_reel is None


def test_missing_symbolic_qc_schema_fields_are_detected_for_retry():
    missing = _missing_symbolic_qc_fields({"symbolic_meaning_matches": True})

    assert "symbolic_visual_matches" in missing
    assert "forbidden_drift_types" in missing


def test_non_symbolic_vision_result_is_unchanged():
    scene = Scene(
        scene_index=1,
        title="Cinematic scene",
        voice_script="An adult walks through rain.",
        image_prompt="cinematic rainy street",
    )
    result = _result_from_vision(
        scene,
        Path("scene.jpg"),
        _bible(),
        {"attempt": 1, "expected_character_count": 1},
        _anatomy(),
        {},
        [],
        1.0,
        True,
        {
            "available": True,
            "data": _base_data(),
            "model": "fake-vision",
            "call_count": 1,
            "parse_attempt_count": 1,
        },
    )

    assert result.passed is True
    assert result.symbolic_qc_final_status == "not_applicable"
    assert result.symbolic_qc_failure_reasons == []


def _fetch_plan() -> TellaScenePlan:
    scenes = [
        Scene(
            scene_index=index,
            title=f"Scene {index}",
            voice_script=(
                "lonely in a crowd"
                if index == 1
                else f"quiet symbolic thought {index}"
            ),
            scene_meaning=(
                "lonely in a crowd"
                if index == 1
                else f"quiet symbolic thought {index}"
            ),
            symbolic_visual=(
                "one isolated adult beside a small group"
                if index == 1
                else "small paper heart"
            ),
            emotional_metaphor="distance becoming visible",
            main_character_or_object="adult figure",
        )
        for index in range(1, 4)
    ]
    plan = TellaScenePlan(
        title="Symbolic QC fetch",
        language="en",
        aspect_ratio="9:16",
        media_source="ai_image",
        duration_mode="short",
        theme="minimalist_symbolic_reel",
        scenes=scenes,
    )
    enforce_symbolic_reel_plan(plan)
    plan.scenes = plan.scenes[:1]
    return plan


def test_symbolic_fetch_retry_uses_repaired_prompt(monkeypatch, tmp_path):
    monkeypatch.setenv("TELLA_SCENE_QC", "vision")
    monkeypatch.setenv("TELLA_SCENE_MAX_ATTEMPTS", "2")
    monkeypatch.delenv("TELLA_ALLOW_LOCAL_IMAGE_FALLBACK", raising=False)
    monkeypatch.delenv("TELLA_MAX_AI_IMAGES", raising=False)
    monkeypatch.setattr(fetch, "MAX_CONCURRENT", 1)
    generated_prompts = []

    async def fake_generate_image(prompt, out_path, *, width, height, seed=None):
        generated_prompts.append(prompt)
        Image.new("RGB", (16, 28), "#5b514d").save(out_path)
        return out_path

    def fake_evaluate(scene, image_path, visual_bible, expected):
        if expected["attempt"] == 1:
            data = _base_data(
                passed=False,
                character_count=1,
                crowd_visible=False,
            )
        else:
            data = _base_data(
                character_count=2,
                head_count=2,
                crowd_visible=True,
            )
        return _result_from_vision(
            scene,
            image_path,
            visual_bible,
            expected,
            _anatomy(),
            {},
            [],
            1.0,
            True,
            {
                "available": True,
                "data": data,
                "model": "fake-vision",
                "call_count": 1,
                "parse_attempt_count": 1,
            },
        )

    monkeypatch.setattr(fetch.ai_image, "generate_image", fake_generate_image)
    monkeypatch.setattr(fetch, "evaluate_scene_image", fake_evaluate)
    plan = _fetch_plan()

    asyncio.run(fetch.fetch_assets(plan, tmp_path))

    scene = plan.scenes[0]
    assert len(generated_prompts) == 2
    assert generated_prompts[1] != generated_prompts[0]
    assert "isolated adult" in generated_prompts[1].lower()
    assert "small group or crowd" in generated_prompts[1].lower()
    assert scene.symbolic_qc_passed is True
    assert scene.symbolic_qc_attempts == 2
    assert scene.symbolic_qc_repaired_prompt_used is True
    assert scene.symbolic_qc_final_status == "passed"
    assert (tmp_path / scene.asset_path).is_file()


def test_symbolic_fetch_never_accepts_exhausted_hard_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("TELLA_SCENE_QC", "vision")
    monkeypatch.setenv("TELLA_SCENE_MAX_ATTEMPTS", "2")
    monkeypatch.delenv("TELLA_ALLOW_LOCAL_IMAGE_FALLBACK", raising=False)
    monkeypatch.delenv("TELLA_MAX_AI_IMAGES", raising=False)
    monkeypatch.setattr(fetch, "MAX_CONCURRENT", 1)
    generation_count = 0

    async def fake_generate_image(prompt, out_path, *, width, height, seed=None):
        nonlocal generation_count
        generation_count += 1
        Image.new("RGB", (16, 28), "#5b514d").save(out_path)
        return out_path

    def fake_evaluate(scene, image_path, visual_bible, expected):
        return _result_from_vision(
            scene,
            image_path,
            visual_bible,
            expected,
            _anatomy(),
            {},
            [],
            1.0,
            True,
            {
                "available": True,
                "data": _base_data(
                    passed=False,
                    forbidden_drift_detected=True,
                    forbidden_drift_types=["medical_mask"],
                    medical_mask_detected=True,
                ),
                "model": "fake-vision",
                "call_count": 1,
                "parse_attempt_count": 1,
            },
        )

    monkeypatch.setattr(fetch.ai_image, "generate_image", fake_generate_image)
    monkeypatch.setattr(fetch, "evaluate_scene_image", fake_evaluate)
    plan = _fetch_plan()

    with pytest.raises(RuntimeError, match="failed symbolic visual QC"):
        asyncio.run(fetch.fetch_assets(plan, tmp_path))

    scene = plan.scenes[0]
    assert generation_count == 2
    assert scene.symbolic_qc_passed is False
    assert scene.symbolic_qc_final_status == "hard_failed"
    assert "medical_mask_drift" in scene.symbolic_qc_failure_reasons
