from pathlib import Path

from tella.media.visual_qc import _normalize_action_mismatch_severity, _result_from_vision
from tella.planner.models import CharacterSpec, Scene, StyleBible, VisualBible


def _scene() -> Scene:
    scene = Scene(
        scene_index=1,
        title="Quiet room",
        voice_script="She walks slowly toward the window.",
        image_prompt="medium-wide bedroom scene, full body visible, walking",
    )
    scene.attempt_count = 1
    return scene


def _bible() -> VisualBible:
    return VisualBible(
        style_bible=StyleBible(),
        character_specs=[
            CharacterSpec(
                character_id="girl",
                gender_or_presentation="young woman",
                hair="short black bob",
                outfit="mustard yellow dress",
            )
        ],
    )


def _anatomy() -> dict[str, str]:
    return {
        "shot_type": "medium_wide",
        "body_visibility": "full_body",
        "pose_type": "walking",
        "anatomy_expectation": "full_body_two_legs_visible",
    }


def _base_data(**overrides):
    data = {
        "passed": True,
        "confidence": 0.9,
        "character_count": 1,
        "main_character_visible": True,
        "head_count": 1,
        "visible_arm_count": 2,
        "visible_leg_count": 2,
        "visible_foot_count": 2,
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
        "failure_reasons": [],
    }
    data.update(overrides)
    return data


def _qc(data, *, expected=None):
    return _result_from_vision(
        _scene(),
        Path("scene.jpg"),
        _bible(),
        {
            "attempt": 1,
            "max_attempts_allowed": 3,
            "expected_character_count": 1,
            "soft_fail_streaks": {},
            **(expected or {}),
        },
        _anatomy(),
        {},
        [],
        1.0,
        True,
        {"available": True, "data": data, "model": "fake", "call_count": 1, "parse_attempt_count": 1},
    )


def test_action_false_severity_none_normalizes_to_minor():
    assert _normalize_action_mismatch_severity("none", action_matches=False) == "minor"
    assert _normalize_action_mismatch_severity("", action_matches=False) == "minor"
    assert _normalize_action_mismatch_severity("invalid", action_matches=False) == "minor"


def test_major_action_mismatch_hard_fails_immediately():
    result = _qc(
        _base_data(
            action={
                "scene_matches_requested_action": False,
                "action_mismatch_severity": "major",
            }
        )
    )

    assert result.passed is False
    assert result.action_hard_fail is True
    assert result.action_soft_fail is False
    assert result.repeated_soft_fail_escalation_applied is False
    assert any("major action mismatch" in reason for reason in result.final_attempt_hard_fail_reasons)


def test_minor_action_mismatch_does_not_hard_fail_immediately():
    result = _qc(
        _base_data(
            action={
                "scene_matches_requested_action": False,
                "action_mismatch_severity": "minor",
            }
        )
    )

    assert result.passed is False
    assert result.action_soft_fail is True
    assert result.action_hard_fail is False
    assert result.final_attempt_hard_fail_reasons == []
    assert result.loop_stop_reason == ""


def test_repeated_minor_action_mismatch_escalates_by_streak():
    result = _qc(
        _base_data(
            action={
                "scene_matches_requested_action": False,
                "action_mismatch_severity": "minor",
            }
        ),
        expected={"soft_fail_streaks": {"action": 1}},
    )

    assert result.action_mismatch_streak == 2
    assert result.action_soft_fail is True
    assert result.action_hard_fail is True
    assert result.repeated_soft_fail_escalation_applied is True
    assert result.stopped_retry_loop_early_due_to_repeated_soft_fail is True


def test_minor_action_with_model_passed_false_is_not_immediate_hard_fail():
    result = _qc(
        _base_data(
            passed=False,
            action={
                "scene_matches_requested_action": False,
                "action_mismatch_severity": "minor",
            },
        )
    )

    assert result.model_passed is False
    assert result.action_soft_fail is True
    assert result.action_hard_fail is False
    assert result.final_attempt_hard_fail_reasons == []
    assert result.loop_stop_reason == ""


def test_stop_metadata_aggregates_all_hard_and_escalated_reasons():
    result = _qc(
        _base_data(
            has_bad_crop=True,
            hairstyle_matches_spec=False,
            action={
                "scene_matches_requested_action": False,
                "action_mismatch_severity": "major",
            },
        ),
        expected={"soft_fail_streaks": {"hairstyle": 1}},
    )

    joined = " | ".join(result.final_attempt_hard_fail_reasons)
    assert "bad crop" in joined
    assert "major action mismatch" in joined
    assert "hairstyle mismatch" in joined
    assert result.loop_stop_reason == result.final_attempt_hard_fail_reasons[0]
    assert result.loop_stop_reasons_all == result.final_attempt_hard_fail_reasons
