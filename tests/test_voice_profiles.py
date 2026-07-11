import json

import pytest

import tella.cli as cli
from tella.planner.models import Scene, TellaScenePlan
from tella.recipes import get_recipe
from tella.voice_profiles import (
    VoiceProfileNotFoundError,
    apply_voice_resolution_metadata,
    get_voice_profile,
    resolve_voice,
)


def _plan() -> TellaScenePlan:
    return TellaScenePlan(
        title="Voice metadata",
        language="vi",
        aspect_ratio="9:16",
        media_source="ai_image",
        duration_mode="short",
        theme="cinematic",
        voice_name="vi-VN-NamMinhNeural",
        voice_edge_rate="-5%",
        scenes=[
            Scene(scene_index=index, title=f"Scene {index}", voice_script="Một câu")
            for index in range(1, 4)
        ],
    )


def _fail_call(*args, **kwargs):
    raise AssertionError("TTS/network/provider/render function must not be called")


def test_voice_profile_definitions_match_required_vietnamese_mappings():
    assert get_voice_profile("soft_female_vi").model_dump() == {
        "profile_id": "soft_female_vi",
        "provider": "edge",
        "voice": "vi-VN-HoaiMyNeural",
        "rate": "-10%",
        "role": "gentle emotional narrator",
        "suitable_narrative_modes": ["emotional_reflection"],
    }
    assert get_voice_profile("firm_male_vi").voice == "vi-VN-NamMinhNeural"
    assert get_voice_profile("firm_male_vi").rate == "-5%"
    assert get_voice_profile("clear_female_vi").rate == "-2%"


def test_recipe_default_voice_resolution():
    recipe = get_recipe("emotional_symbolic_v1")

    result = resolve_voice(
        recipe_profile_id=recipe.voice_profile_id,
        narrative_mode=recipe.narrative_mode,
    )

    assert result.resolved_voice_profile_id == "soft_female_vi"
    assert result.voice_resolution_source == "recipe_profile"
    assert result.resolved_tts_provider == "edge"
    assert result.resolved_voice == "vi-VN-HoaiMyNeural"
    assert result.resolved_voice_rate == "-10%"
    assert result.voice_profile_compatibility_status == "compatible"
    assert result.recipe_voice_override_applied is False


def test_cli_profile_override_takes_precedence_over_recipe(caplog):
    result = resolve_voice(
        explicit_profile_id="firm_male_vi",
        recipe_profile_id="soft_female_vi",
        narrative_mode="emotional_reflection",
    )

    assert result.resolved_voice_profile_id == "firm_male_vi"
    assert result.voice_resolution_source == "cli_profile"
    assert result.resolved_voice == "vi-VN-NamMinhNeural"
    assert result.recipe_voice_override_applied is True
    assert result.voice_profile_compatibility_status == "warning"
    assert "not suggested for narrative mode emotional_reflection" in caplog.text


def test_direct_voice_overlays_cli_profile_without_discarding_it():
    result = resolve_voice(
        explicit_provider="edge",
        explicit_voice="vi-VN-NamMinhNeural",
        explicit_profile_id="clear_female_vi",
        recipe_profile_id="soft_female_vi",
        narrative_mode="emotional_reflection",
    )

    assert result.voice_resolution_source == "cli_profile_with_cli_override"
    assert result.resolved_voice_profile_id == "clear_female_vi"
    assert result.resolved_tts_provider == "edge"
    assert result.resolved_voice == "vi-VN-NamMinhNeural"
    assert result.resolved_voice_rate == "-2%"
    assert result.direct_override_fields == ["provider", "voice"]
    assert result.recipe_voice_override_applied is True


def test_direct_provider_overrides_only_provider_on_recipe_profile():
    result = resolve_voice(
        explicit_provider="edge",
        recipe_profile_id="soft_female_vi",
        narrative_mode="emotional_reflection",
    )

    assert result.resolved_voice_profile_id == "soft_female_vi"
    assert result.voice_resolution_source == "recipe_profile_with_cli_override"
    assert result.resolved_tts_provider == "edge"
    assert result.resolved_voice == "vi-VN-HoaiMyNeural"
    assert result.resolved_voice_rate == "-10%"
    assert result.direct_override_fields == ["provider"]
    assert result.recipe_voice_override_applied is False
    assert result.voice_profile_compatibility_status == "compatible"


def test_direct_voice_overrides_only_voice_on_recipe_profile():
    result = resolve_voice(
        explicit_voice="vi-VN-NamMinhNeural",
        recipe_profile_id="soft_female_vi",
        narrative_mode="emotional_reflection",
    )

    assert result.resolved_voice_profile_id == "soft_female_vi"
    assert result.voice_resolution_source == "recipe_profile_with_cli_override"
    assert result.resolved_tts_provider == "edge"
    assert result.resolved_voice == "vi-VN-NamMinhNeural"
    assert result.resolved_voice_rate == "-10%"
    assert result.direct_override_fields == ["voice"]
    assert result.recipe_voice_override_applied is True
    assert result.voice_profile_compatibility_status == "compatible"


def test_cli_profile_plus_direct_voice_preserves_cli_profile_rate(caplog):
    result = resolve_voice(
        explicit_profile_id="firm_male_vi",
        explicit_voice="vi-VN-HoaiMyNeural",
        recipe_profile_id="soft_female_vi",
        narrative_mode="emotional_reflection",
    )

    assert result.resolved_voice_profile_id == "firm_male_vi"
    assert result.voice_resolution_source == "cli_profile_with_cli_override"
    assert result.resolved_tts_provider == "edge"
    assert result.resolved_voice == "vi-VN-HoaiMyNeural"
    assert result.resolved_voice_rate == "-5%"
    assert result.direct_override_fields == ["voice"]
    assert result.recipe_voice_override_applied is True
    assert result.voice_profile_compatibility_status == "warning"
    assert "not suggested for narrative mode emotional_reflection" in caplog.text


def test_direct_provider_and_voice_together_preserve_recipe_profile_rate():
    result = resolve_voice(
        explicit_provider="edge",
        explicit_voice="vi-VN-NamMinhNeural",
        recipe_profile_id="soft_female_vi",
        narrative_mode="emotional_reflection",
    )

    assert result.resolved_voice_profile_id == "soft_female_vi"
    assert result.voice_resolution_source == "recipe_profile_with_cli_override"
    assert result.resolved_tts_provider == "edge"
    assert result.resolved_voice == "vi-VN-NamMinhNeural"
    assert result.resolved_voice_rate == "-10%"
    assert result.direct_override_fields == ["provider", "voice"]
    assert result.recipe_voice_override_applied is True


def test_explicit_rate_overrides_only_profile_rate():
    result = resolve_voice(
        explicit_voice_rate="+3%",
        recipe_profile_id="soft_female_vi",
        narrative_mode="emotional_reflection",
    )

    assert result.resolved_voice_profile_id == "soft_female_vi"
    assert result.resolved_tts_provider == "edge"
    assert result.resolved_voice == "vi-VN-HoaiMyNeural"
    assert result.resolved_voice_rate == "+3%"
    assert result.direct_override_fields == ["rate"]
    assert result.recipe_voice_override_applied is True


def test_unknown_voice_profile_is_rejected_clearly():
    with pytest.raises(VoiceProfileNotFoundError, match="unknown voice profile"):
        resolve_voice(explicit_profile_id="missing_profile")


def test_voice_resolution_metadata_serializes_on_plan():
    plan = _plan()
    resolution = resolve_voice(
        explicit_profile_id="clear_female_vi",
        narrative_mode="practical_steps",
    )

    apply_voice_resolution_metadata(plan, resolution)
    data = plan.model_dump()

    assert data["requested_voice_profile_id"] == "clear_female_vi"
    assert data["resolved_voice_profile_id"] == "clear_female_vi"
    assert data["voice_resolution_source"] == "cli_profile"
    assert data["resolved_tts_provider"] == "edge"
    assert data["resolved_voice"] == "vi-VN-HoaiMyNeural"
    assert data["resolved_voice_rate"] == "-2%"
    assert data["voice_profile_compatibility_status"] == "compatible"
    assert data["voice_name"] == "vi-VN-HoaiMyNeural"
    assert data["voice_edge_rate"] == "-2%"


def test_legacy_direct_cli_voice_behavior_is_preserved(monkeypatch, tmp_path):
    captured = {}

    async def fake_run_pipeline(**kwargs):
        captured.update(kwargs)
        return tmp_path / "plan.json"

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")

    result = cli.main(
        [
            "--topic",
            "test",
            "--lang",
            "vi",
            "--tts-provider",
            "edge",
            "--voice",
            "vi-VN-HoaiMyNeural",
            "--dry-run-plan",
            "--out",
            str(tmp_path),
        ]
    )

    assert result == 0
    resolution = captured["voice_resolution"]
    assert resolution.voice_resolution_source == "explicit_cli"
    assert resolution.resolved_voice == "vi-VN-HoaiMyNeural"
    assert captured["voice_rate_custom"] is None


def test_validate_voice_profiles_command_makes_no_external_calls(
    monkeypatch,
    capsys,
):
    for name in (
        "translate_topic",
        "plan_story",
        "plan_story_from_script",
        "fetch_assets",
        "synthesize_all",
        "render",
    ):
        monkeypatch.setattr(cli, name, _fail_call)

    result = cli.main(["--validate-voice-profiles"])

    assert result == 0
    output = capsys.readouterr().out
    assert "Voice profiles valid" in output
    assert "soft_female_vi" in output


def test_recipe_dry_run_records_resolved_voice_without_external_calls(
    monkeypatch,
    tmp_path,
):
    for name in (
        "translate_topic",
        "plan_story",
        "plan_story_from_script",
        "fetch_assets",
        "synthesize_all",
        "render",
    ):
        monkeypatch.setattr(cli, name, _fail_call)

    result = cli.main(
        [
            "--recipe",
            "emotional_symbolic_v1",
            "--voice-profile",
            "soft_female_vi",
            "--dry-run-recipe",
            "--out",
            str(tmp_path),
            "--job-id",
            "voice_dry",
        ]
    )

    assert result == 0
    files = list((tmp_path / "voice_dry").iterdir())
    assert [path.name for path in files] == ["recipe.json"]
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["requested_voice_profile_id"] == "soft_female_vi"
    assert data["resolved_voice_profile_id"] == "soft_female_vi"
    assert data["voice_resolution_source"] == "cli_profile"
    assert data["resolved_tts_provider"] == "edge"
    assert data["resolved_voice"] == "vi-VN-HoaiMyNeural"
    assert data["resolved_voice_rate"] == "-10%"
