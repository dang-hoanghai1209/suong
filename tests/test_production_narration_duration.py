"""Network-free tests for the production narration-duration eligibility gate."""
from __future__ import annotations

import asyncio

import pytest

from tella.planner.models import Scene, TellaScenePlan
from tella.tts import synth_all
from tella.tts.duration import (
    ProductionNarrationDurationError,
    narration_planning_diagnostic,
    requires_production_narration_duration_validation,
)
from tella.tts.providers import EdgeTTSProvider


CHECKPOINT_7_NARRATION = (
    "Có những ngày, mình cố gắng quá nhiều đến mức quên mất rằng bản thân "
    "cũng cần được nghỉ ngơi. Không phải lúc nào mạnh mẽ cũng là tiếp tục "
    "bước đi. Đôi khi, chỉ cần ngồi yên một chút, lau đi những giọt nước mắt, "
    "và cho mình thời gian để thở. Rồi khi lòng đã nhẹ hơn, mình lại bước "
    "tiếp. Không cần nhanh. Chỉ cần dịu dàng hơn với chính mình."
)
IN_RANGE_NARRATION = " ".join(["dịu"] * 100)
TOO_LONG_NARRATION = " ".join(["dịu"] * 150)


def _diagnose(
    text: str,
    *,
    provider: str = "edge",
    voice: str = "vi-VN-HoaiMyNeural",
    transport_speed_multiplier: float | None = 0.85,
):
    return narration_planning_diagnostic(
        text,
        35.0,
        language="vi-VN",
        provider=provider,
        voice=voice,
        transport_speed_multiplier=transport_speed_multiplier,
        pause_cap_ms=350,
        production_target_range=(32.0, 38.0),
    )


def _full_production_plan(text: str) -> TellaScenePlan:
    return TellaScenePlan(
        title="Dịu dàng với chính mình",
        language="vi",
        theme="minimalist_emotional",
        recipe_scene_range=[8, 8],
        recipe_duration_range=[32.0, 38.0],
        recipe_validation_status="passed",
        requested_production_duration_seconds=35.0,
        voice_edge_rate="-15%",
        voice_name="vi-VN-HoaiMyNeural",
        global_narration_text=text,
        tts_text_source="manual_global_narration_text",
        scenes=[
            Scene(scene_index=index, voice_script=f"Phân đoạn {index}.")
            for index in range(1, 9)
        ],
    )


def test_checkpoint_7_vietnamese_narration_is_now_rejected_as_too_short():
    assessment = _diagnose(CHECKPOINT_7_NARRATION)

    assert assessment["spoken_unit_kind"] == "vietnamese_syllable_like_whitespace_units"
    assert assessment["spoken_unit_count"] == 74
    assert assessment["estimated_duration_seconds"] == pytest.approx(28.256)
    assert assessment["estimated_duration_range_seconds"] == [24.973, 32.67]
    assert assessment["production_target_range_seconds"] == [32.0, 38.0]
    assert assessment["planning_duration_status"] == "likely_too_short"
    assert assessment["production_tts_eligible"] is False


def test_in_range_narration_remains_unchanged_and_is_eligible():
    original = IN_RANGE_NARRATION

    assessment = _diagnose(original)

    assert IN_RANGE_NARRATION == original
    assert assessment["planning_duration_status"] == "within_production_target"
    assert assessment["production_tts_eligible"] is True
    assert assessment["repair_action"] == "none"


def test_clearly_too_long_narration_requires_replanning():
    assessment = _diagnose(TOO_LONG_NARRATION)

    assert assessment["planning_duration_status"] == "likely_too_long"
    assert assessment["production_tts_eligible"] is False
    assert assessment["repair_action"] == "requires_narration_replan"


def test_unknown_provider_and_voice_do_not_validate_materially_short_text():
    assessment = _diagnose(
        CHECKPOINT_7_NARRATION,
        provider="",
        voice="",
        transport_speed_multiplier=None,
    )

    assert assessment["provider"] == "unknown"
    assert assessment["voice"] == "unknown"
    assert assessment["provider_voice_calibrated"] is False
    assert assessment["transport_speed_multiplier"] == 1.0
    assert assessment["speed_applied_to_transport"] is False
    assert assessment["production_tts_eligible"] is False


@pytest.mark.parametrize(
    "field",
    [
        "recipe_scene_range",
        "recipe_duration_range",
    ],
)
def test_malformed_validated_recipe_contract_fails_closed(field):
    plan = _full_production_plan(CHECKPOINT_7_NARRATION)
    setattr(plan, field, [])

    with pytest.raises(ValueError, match="validated production recipe"):
        requires_production_narration_duration_validation(plan)


def test_unvalidated_plan_does_not_inherit_a_global_duration_contract():
    plan = _full_production_plan(CHECKPOINT_7_NARRATION)
    plan.recipe_scene_range = []
    plan.recipe_duration_range = []
    plan.recipe_validation_status = ""

    assert requires_production_narration_duration_validation(plan) is False


def test_pause_cap_effect_is_modeled_before_renderer():
    uncapped = narration_planning_diagnostic(
        CHECKPOINT_7_NARRATION,
        35.0,
        language="vi-VN",
        provider="edge",
        voice="vi-VN-HoaiMyNeural",
        transport_speed_multiplier=0.85,
        pause_cap_ms=None,
        production_target_range=(32.0, 38.0),
    )
    capped = _diagnose(CHECKPOINT_7_NARRATION)

    assert capped["estimated_raw_duration_range_seconds"] == uncapped[
        "estimated_raw_duration_range_seconds"
    ]
    assert capped["estimated_duration_seconds"] < uncapped[
        "estimated_duration_seconds"
    ]
    assert uncapped["postprocess_assumption"]["pause_cap_applied"] is False
    assert capped["postprocess_assumption"]["pause_cap_applied"] is True


def test_modeled_edge_speed_matches_transport_rate(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    async def fake_edge(text, voice_name, out_path, *, rate, pitch="+0Hz", volume="+0%"):
        captured.update(text=text, voice=voice_name, rate=rate)
        out_path.write_bytes(b"mock edge audio")
        return out_path

    monkeypatch.setattr("tella.tts.providers.edge.synthesize", fake_edge)
    plan = _full_production_plan(IN_RANGE_NARRATION)
    settings = synth_all._resolve_tts_settings(plan, "edge")

    asyncio.run(
        EdgeTTSProvider().synthesize(
            IN_RANGE_NARRATION,
            tmp_path / "narration.mp3",
            voice=settings["voice"],
            language=settings["language"],
            speed=settings["speed"],
            codec="mp3",
            sample_rate=24000,
            metadata={"edge_rate": plan.voice_edge_rate},
        )
    )
    assessment = _diagnose(IN_RANGE_NARRATION)

    assert settings["speed"] == 0.85
    assert captured["rate"] == "-15%"
    assert assessment["transport_speed_multiplier"] == 0.85
    assert assessment["speed_applied_to_transport"] is True


@pytest.mark.parametrize(
    "text,expected_status",
    [
        (CHECKPOINT_7_NARRATION, "likely_too_short"),
        (TOO_LONG_NARRATION, "likely_too_long"),
    ],
)
def test_out_of_range_full_production_is_blocked_before_tts_or_renderer(
    monkeypatch,
    tmp_path,
    text: str,
    expected_status: str,
):
    provider_calls: list[str] = []

    async def forbidden_synthesis(*args, **kwargs):
        provider_calls.append("called")
        raise AssertionError("TTS transport must not run for ineligible narration")

    def forbidden_renderer(*args, **kwargs):
        raise AssertionError("renderer timing must not repair narration duration")

    monkeypatch.setenv("TELLA_TTS_PROVIDER", "edge")
    monkeypatch.setattr(synth_all.EdgeTTSProvider, "synthesize", forbidden_synthesis)
    monkeypatch.setattr(synth_all, "build_render_timing_plan", forbidden_renderer)

    with pytest.raises(ProductionNarrationDurationError) as caught:
        asyncio.run(synth_all.synthesize_all(_full_production_plan(text), tmp_path))

    assert caught.value.assessment["planning_duration_status"] == expected_status
    assert caught.value.assessment["repair_action"] == "requires_narration_replan"
    assert provider_calls == []
    assert not (tmp_path / "tts_metadata.json").exists()
