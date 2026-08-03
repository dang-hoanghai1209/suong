import asyncio
import json
import socket
from pathlib import Path

import pytest

from tella import cli
from tella._voice_pace import normalize_voice_rate, resolve_pace
from tella.production import (
    CALLIRRHOE_PRODUCTION_CONFIG,
    ProductionRun,
    production_fingerprint,
)
from tella.planner.practical_life_steps import (
    plan_practical_life_steps_from_script,
    plan_practical_life_steps_from_topic,
)
from tella.recipes import get_recipe
from tella.tts.google import rate_to_speaking_rate
from tella.voice_profiles import VoiceResolution, get_voice_profile, resolve_voice


PHONE_FOCUS_TOPIC = (
    "Đặt điện thoại ngoài tầm tay trong hai mươi phút để tập trung làm một "
    "việc quan trọng."
)


@pytest.mark.parametrize("value", ["0%", "+0%", "-0%"])
def test_neutral_voice_rates_have_one_canonical_representation(value):
    assert normalize_voice_rate(value) == "+0%"
    assert resolve_pace(
        theme="practical_life_steps", custom_edge_rate=value
    ).edge_rate == "+0%"


@pytest.mark.parametrize("value", ["+3%", "-7%", "+100%", "-100%"])
def test_signed_nonzero_voice_rates_remain_unchanged(value):
    assert normalize_voice_rate(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "", "0", "3%", "++3%", "--3%", "+3", "3", "+1000%",
        "+ 3%", " +3%", "+3% ", " 0%", 0,
    ],
)
def test_malformed_voice_rates_remain_rejected(value):
    with pytest.raises(ValueError, match="voice rate"):
        normalize_voice_rate(value)


def test_voice_resolution_does_not_trim_malformed_rate_whitespace():
    with pytest.raises(ValueError, match="voice rate"):
        resolve_voice(explicit_voice_rate=" +3%")
    with pytest.raises(ValueError, match="voice rate"):
        resolve_voice(legacy_rate="-7% ")


def test_callirrhoe_resolution_and_planner_setup_accept_declared_neutral_rate():
    profile = get_voice_profile("gemini_callirrhoe_vi_natural_smile")
    resolution = resolve_voice(
        recipe_profile_id=profile.profile_id,
        narrative_mode="practical_steps",
    )
    pace = resolve_pace(
        theme="practical_life_steps",
        custom_edge_rate=resolution.resolved_voice_rate,
    )
    assert profile.rate == resolution.resolved_voice_rate == pace.edge_rate == "+0%"
    assert pace.google_rate == 1.0


def test_neutral_rate_fingerprint_and_envelope_identity_is_deterministic(tmp_path):
    fingerprints = {
        production_fingerprint(
            CALLIRRHOE_PRODUCTION_CONFIG.model_copy(update={"voice_rate": value})
        )
        for value in ("0%", "+0%", "-0%")
    }
    assert len(fingerprints) == 1
    from tella.production import dry_run_envelope

    envelopes = [
        dry_run_envelope(
            CALLIRRHOE_PRODUCTION_CONFIG.model_copy(update={"voice_rate": value}),
            tmp_path / value.replace("%", "pct"),
            resume=False,
        )
        for value in ("0%", "+0%", "-0%")
    ]
    assert {item["effective_voice_rate"] for item in envelopes} == {"+0%"}


def test_existing_edge_profiles_keep_their_signed_rates():
    assert get_voice_profile("soft_female_vi").rate == "-10%"
    assert get_voice_profile("firm_male_vi").rate == "-5%"
    assert get_voice_profile("clear_female_vi").rate == "-2%"


@pytest.mark.parametrize(
    ("rate", "expected"),
    [("0%", 1.0), ("+0%", 1.0), ("-0%", 1.0), ("+25%", 1.25), ("-10%", 0.9)],
)
def test_google_rate_conversion_remains_numerically_equivalent(rate, expected):
    assert rate_to_speaking_rate(rate) == expected


def test_failure_message_redacts_secret_environment_values(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_TOKEN", "environment-secret-value")
    run = ProductionRun(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)
    run.fail("planned", ValueError("invalid setting environment-secret-value"))
    summary_text = run.summary_path.read_text(encoding="utf-8")
    summary = json.loads(summary_text)
    assert summary["status"] == "validation_failure"
    assert summary["safe_error_message"]
    assert "environment-secret-value" not in summary_text


def _invalid_resolution() -> VoiceResolution:
    valid = resolve_voice(
        recipe_profile_id="gemini_callirrhoe_vi_natural_smile",
        narrative_mode="practical_steps",
    )
    return valid.model_copy(update={"resolved_voice_rate": "3%"})


def _forbid_external(*args, **kwargs):
    pytest.fail("provider, socket, media, or render path was called")


def test_invalid_rate_fails_dry_run_with_truthful_safe_summary(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cli, "resolve_voice", lambda **kwargs: _invalid_resolution())
    monkeypatch.setattr(cli, "run_pipeline", _forbid_external)
    monkeypatch.setattr(socket, "create_connection", _forbid_external)
    monkeypatch.setattr(socket.socket, "connect", _forbid_external)
    monkeypatch.setenv("GEMINI_API_KEY", "dry-run-test-secret")

    result = cli.main([
        "--recipe", "practical_life_steps_callirrhoe_v1",
        "--topic", PHONE_FOCUS_TOPIC,
        "--out", str(tmp_path),
        "--job-id", "invalid_dry",
        "--max-ai-images", "7",
        "--max-tts-requests", "1",
        "--no-tts-retry",
        "--production-dry-run",
    ])

    assert result == 1
    job = tmp_path / "invalid_dry"
    summary_text = (job / "production_summary.json").read_text(encoding="utf-8")
    summary = json.loads(summary_text)
    assert summary["status"] == "validation_failure"
    assert summary["failed_stage"] == "planned"
    assert summary["error_category"] == "validation_failure"
    assert summary["safe_error_message"]
    assert summary["resumable"] is False
    assert summary["external_submission_counts"] == {
        "gemini": 0, "edge": 0, "image_provider": 0,
        "retries": 0, "fallbacks": 0,
    }
    assert "dry-run-test-secret" not in summary_text
    assert not (job / ".tella-job.lock").exists()


def test_invalid_planner_configuration_fails_same_dry_run_preflight(
    tmp_path, monkeypatch
):
    invalid = CALLIRRHOE_PRODUCTION_CONFIG.model_copy(
        update={"planner_id": "wrong_planner"}
    )
    monkeypatch.setattr(cli, "get_production_config", lambda recipe_id: invalid)
    monkeypatch.setattr(cli, "run_pipeline", _forbid_external)
    monkeypatch.setattr(socket, "create_connection", _forbid_external)
    monkeypatch.setattr(socket.socket, "connect", _forbid_external)

    result = cli.main([
        "--recipe", "practical_life_steps_callirrhoe_v1",
        "--topic", PHONE_FOCUS_TOPIC,
        "--out", str(tmp_path),
        "--job-id", "invalid_planner_dry",
        "--max-ai-images", "7",
        "--max-tts-requests", "1",
        "--no-tts-retry",
        "--production-dry-run",
    ])

    assert result == 1
    summary = json.loads(
        (tmp_path / "invalid_planner_dry" / "production_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["status"] == "validation_failure"
    assert summary["failed_stage"] == "planned"
    assert "planner_id" in summary["safe_error_message"]
    assert summary["external_submission_counts"]["gemini"] == 0


def test_invalid_rate_fails_production_before_any_provider_and_preserves_recipe(
    tmp_path, monkeypatch
):
    for name in ("fetch_assets", "synthesize_all", "render"):
        monkeypatch.setattr(cli, name, _forbid_external)
    monkeypatch.setattr(socket, "create_connection", _forbid_external)
    recipe = get_recipe("practical_life_steps_callirrhoe_v1")
    job = tmp_path / "invalid_real"

    with pytest.raises(ValueError, match="voice rate"):
        asyncio.run(cli.run_pipeline(
            topic=PHONE_FOCUS_TOPIC,
            target_lang="vi",
            theme=recipe.visual_theme_id,
            media_source="ai_image",
            duration_mode="short",
            aspect_ratio="9:16",
            voice_pace_name=None,
            voice_rate_custom="3%",
            voice_gender=None,
            out_root=tmp_path,
            job_id=job.name,
            dry_run_plan=False,
            recipe=recipe,
            voice_resolution=_invalid_resolution(),
        ))

    summary_text = (job / "production_summary.json").read_text(encoding="utf-8")
    summary = json.loads(summary_text)
    assert summary["status"] == "validation_failure"
    assert summary["current_stage"] == "failed"
    assert summary["last_successful_stage"] == "recipe_resolved"
    assert summary["failed_stage"] == "planned"
    assert summary["error_category"] == "validation_failure"
    assert summary["safe_error_message"]
    assert summary["resumable"] is False
    assert summary["external_submission_counts"]["gemini"] == 0
    assert summary["external_submission_counts"]["image_provider"] == 0
    assert (job / "recipe.json").is_file()
    assert summary["preserved_artifact_paths"]["recipe"].endswith("recipe.json")
    assert not (job / ".tella-job.lock").exists()


def test_phone_focus_acceptance_configuration_passes_previous_planner_failure(
    tmp_path, monkeypatch
):
    def fake_topic_planner(*, target_lang, voice_pace, **kwargs):
        assert voice_pace.edge_rate == "+0%"
        return plan_practical_life_steps_from_script(
            user_script=(
                Path(__file__).resolve().parents[1]
                / "script_practical_life_steps_test.txt"
            ).read_text(encoding="utf-8"),
            target_lang=target_lang,
            aspect_ratio=kwargs["aspect_ratio"],
            media_source=kwargs["media_source"],
            duration_mode=kwargs["duration_mode"],
            voice_pace=voice_pace,
            voice_gender=kwargs["voice_gender"],
        )

    for name in ("fetch_assets", "synthesize_all", "render"):
        monkeypatch.setattr(cli, name, _forbid_external)
    monkeypatch.setattr(
        cli, "plan_practical_life_steps_from_topic", fake_topic_planner
    )
    monkeypatch.setattr(socket, "create_connection", _forbid_external)
    monkeypatch.setenv("GEMINI_API_KEY", "local-test-only")

    result = cli.main([
        "--recipe", "practical_life_steps_callirrhoe_v1",
        "--topic", PHONE_FOCUS_TOPIC,
        "--lang", "vi",
        "--out", str(tmp_path),
        "--job-id", "phone_focus_source_regression",
        "--max-ai-images", "7",
        "--max-tts-requests", "1",
        "--no-tts-retry",
        "--dry-run-plan",
    ])

    assert result == 0
    job = tmp_path / "phone_focus_source_regression"
    plan = json.loads((job / "plan.json").read_text(encoding="utf-8"))
    assert plan["voice_edge_rate"] == "+0%"
    assert plan["resolved_voice_rate"] == "+0%"
    assert len([scene for scene in plan["scenes"] if scene["kind"] == "scene"]) == 7
    assert not (job / ".tella-job.lock").exists()


def test_real_phone_focus_topic_planner_remains_intentionally_unavailable():
    pace = resolve_pace(
        theme="practical_life_steps", custom_edge_rate="0%"
    )
    assert pace.edge_rate == "+0%"
    with pytest.raises(ValueError, match="topic-only advice generation.*unavailable"):
        plan_practical_life_steps_from_topic(
            topic=PHONE_FOCUS_TOPIC,
            target_lang="vi",
            aspect_ratio="9:16",
            media_source="ai_image",
            duration_mode="short",
            voice_pace=pace,
            voice_gender=None,
        )
