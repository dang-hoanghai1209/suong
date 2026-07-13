import json
import socket
from pathlib import Path

import pytest

from tella import cli
from tella.music.profiles import profile_for_recipe
from tella.production import CALLIRRHOE_PRODUCTION_CONFIG, dry_run_envelope
from tella.recipes import get_recipe
from tella.voice_profiles import get_voice_profile, resolve_voice


def test_production_recipe_resolves_explicit_contract():
    recipe = get_recipe("practical_life_steps_callirrhoe_v1")
    config = CALLIRRHOE_PRODUCTION_CONFIG
    assert recipe.recipe_version == config.recipe_version == 1
    assert recipe.planner_id == "practical_life_steps"
    assert recipe.minimum_scene_count == recipe.maximum_scene_count == 7
    assert recipe.natural_duration is True
    assert recipe.voice_profile_id == config.voice_profile
    assert (config.provider, config.model, config.voice, config.style) == (
        "gemini", "gemini-3.1-flash-tts-preview", "Callirrhoe",
        "natural_vocal_smile",
    )
    assert config.voice_rate == "+0%"
    assert not config.post_tts_atempo and not config.duration_fitting
    assert not config.edge_fallback and not config.model_fallback
    assert config.alignment_enabled and not config.alignment_asr_enabled
    assert config.alignment_manual_overrides == {}


def test_music_contract_is_exact_and_scoped():
    config = CALLIRRHOE_PRODUCTION_CONFIG
    profile = profile_for_recipe(config.recipe_id, config.music_profile)
    assert profile.profile_id == "practical_calm_rhythm"
    assert (config.music_track, config.music_gain_db) == ("practical_calm_01", -11.0)
    assert (config.ducking_threshold, config.ducking_ratio) == (0.025, 2.5)
    assert (config.ducking_attack_ms, config.ducking_release_ms) == (25, 300)
    assert (config.fade_in_seconds, config.fade_out_seconds) == (0.6, 0.9)
    assert config.track_offset_seconds == 8.0 and config.music_loop is False
    assert profile_for_recipe("practical_life_steps_v1").base_gain_db == -21.0


def test_legacy_defaults_and_unrelated_recipes_remain_edge():
    legacy = get_recipe("practical_life_steps_v1")
    assert legacy.voice_profile_id == "clear_female_vi"
    edge = get_voice_profile(legacy.voice_profile_id)
    assert (edge.provider, edge.voice) == ("edge", "vi-VN-HoaiMyNeural")
    for recipe_id in ("emotional_symbolic_v1", "life_insight_symbolic_v1"):
        resolution = resolve_voice(recipe_profile_id=get_recipe(recipe_id).voice_profile_id)
        assert resolution.resolved_tts_provider == "edge"
        assert resolution.resolved_voice != "Callirrhoe"


def test_request_envelope_is_bounded_and_zero_call(tmp_path):
    envelope = dry_run_envelope(
        CALLIRRHOE_PRODUCTION_CONFIG, tmp_path / "job", resume=False
    )
    assert envelope["maximum_gemini_requests"] == 1
    assert envelope["maximum_image_requests"] == 7
    assert envelope["effective_voice_rate"] == "+0%"
    assert envelope["external_calls_performed"] == 0
    assert envelope["render_operations_performed"] == 0
    assert envelope["retry_policy"] == "no retries"


def test_cli_production_dry_run_writes_metadata_only(tmp_path, monkeypatch):
    forbidden = lambda *args, **kwargs: pytest.fail("provider or render path called")
    monkeypatch.setattr(cli, "run_pipeline", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    result = cli.main([
        "--recipe", "practical_life_steps_callirrhoe_v1",
        "--production-dry-run", "--out", str(tmp_path), "--job-id", "dry",
        "--max-tts-requests", "1", "--no-tts-retry",
    ])
    assert result == 0
    files = {item.name for item in (tmp_path / "dry").iterdir()}
    assert files == {
        "production_manifest.json", "production_summary.json", "recipe.json",
        "request_envelope.json",
    }
    data = json.loads((tmp_path / "dry" / "request_envelope.json").read_text())
    assert data["external_calls_performed"] == 0
    assert data["effective_voice_rate"] == "+0%"


def test_cli_rejects_ambiguous_production_overrides(tmp_path):
    with pytest.raises(SystemExit):
        cli.main([
            "--recipe", "practical_life_steps_callirrhoe_v1",
            "--production-dry-run", "--out", str(tmp_path),
            "--tts-provider", "edge",
        ])
