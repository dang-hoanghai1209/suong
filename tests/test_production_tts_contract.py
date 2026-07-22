"""Network-free production continuous narration contract tests."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from tella.composer.compose import compose_timing
from tella.planner.models import Scene, TellaScenePlan
from tella.tts import synth_all
from tella.tts.duration import narration_planning_diagnostic
from tella.tts.policy import GEMINI_EMOTIONAL_MODEL, resolve_production_tts_policy
from tella.tts.providers import GeminiTTSProvider, TTSResult


def _plan(count: int = 3, *, requested: float = 12.0) -> TellaScenePlan:
    if count >= 7:
        spoken_units_per_scene = 15 if count == 7 else 13
    else:
        spoken_units_per_scene = 0

    def voice_script(index: int) -> str:
        if not spoken_units_per_scene:
            return f"Khoảnh khắc dịu dàng thứ {index}."
        units = [
            "Mình",
            "chậm",
            "lại",
            "và",
            "dịu",
            "dàng",
            "lắng",
            "nghe",
            "cơ",
            "thể",
            "trong",
            "nhịp",
            str(index),
            "hôm",
            "nay",
        ]
        return " ".join(units[:spoken_units_per_scene]) + "."

    return TellaScenePlan(
        title="Production narration",
        language="vi",
        theme="minimalist_emotional",
        planner_id="manual_topic_input",
        recipe_scene_range=[7, 8] if count >= 7 else [],
        recipe_duration_range=[32.0, 38.0] if count >= 7 else [],
        recipe_validation_status="passed" if count >= 7 else "",
        requested_production_duration_seconds=requested,
        voice_name="vi-VN-HoaiMyNeural",
        scenes=[
            Scene(
                scene_index=index,
                voice_script=voice_script(index),
            )
            for index in range(1, count + 1)
        ],
    )


def _configure_gemini(monkeypatch) -> None:
    monkeypatch.delenv("TELLA_TTS_PROVIDER", raising=False)
    monkeypatch.delenv("TELLA_TTS_VOICE", raising=False)
    monkeypatch.delenv("TELLA_TTS_MODEL", raising=False)
    monkeypatch.delenv("TELLA_TTS_STYLE", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret-never-persist")


def _mock_durations(monkeypatch, *, raw: float = 12.4, processed: float = 12.0):
    async def fake_probe(path: Path) -> float:
        return processed if path.name == "narration.wav" else raw

    async def fake_normalize(raw_path: Path, out_path: Path) -> dict:
        shutil.copyfile(raw_path, out_path)
        return {
            "silence_postprocess_applied": False,
            "max_pause_ms": 0,
            "original_duration": raw,
            "processed_duration": processed,
            "duration_delta": round(processed - raw, 6),
            "processing_steps": [
                "loudness_normalize_-16lufs",
                "true_peak_limit_-1dbtp",
                "mono_24000hz",
            ],
            "duration_change_expected": False,
            "longest_silence_before": 0.0,
            "longest_silence_after": 0.0,
            "atempo_applied": False,
            "duration_fit_applied": False,
        }

    monkeypatch.setattr(synth_all, "_ffprobe_duration", fake_probe)
    monkeypatch.setattr(synth_all, "_normalize_gemini_narration", fake_normalize)


def _mock_gemini(monkeypatch, calls: list[dict]) -> None:
    async def fake_synthesize(
        self,
        text,
        out_path,
        *,
        voice,
        language,
        speed,
        codec,
        sample_rate,
        metadata=None,
    ):
        calls.append({"text": text, "voice": voice, "language": language})
        out_path.write_bytes(b"deterministic mocked continuous gemini wav")
        return TTSResult(
            audio_path=out_path,
            provider="gemini",
            voice=voice,
            language=language,
            metadata={
                **(metadata or {}),
                "model": GEMINI_EMOTIONAL_MODEL,
                "voice_registry_version": 1,
                "request_attempt_count": 1,
                "codec": "wav",
                "sample_rate": 24000,
            },
        )

    monkeypatch.setattr(
        GeminiTTSProvider,
        "synthesize",
        fake_synthesize,
    )


def test_production_emotional_policy_prefers_configured_callirrhoe(monkeypatch):
    _configure_gemini(monkeypatch)

    policy = resolve_production_tts_policy(_plan())

    assert policy.preferred_provider == policy.actual_provider == "gemini"
    assert policy.preferred_voice == policy.actual_voice == "Callirrhoe"
    assert policy.voice_gender == "female"
    assert policy.language == "vi-VN"
    assert policy.style == "gentle_emotional"
    assert policy.fallback_used is False


def test_explicit_edge_configuration_remains_edge(monkeypatch):
    _configure_gemini(monkeypatch)

    policy = resolve_production_tts_policy(_plan(), explicit_provider="edge")

    assert policy.mode == "explicit"
    assert policy.preferred_provider == policy.actual_provider == "edge"
    assert policy.fallback_used is False


@pytest.mark.parametrize(
    "count,requested,processed",
    [(3, 12.0, 11.75), (7, 32.0, 32.4), (8, 35.0, 34.6)],
)
def test_one_continuous_request_drives_authoritative_timing(
    monkeypatch,
    tmp_path,
    count: int,
    requested: float,
    processed: float,
):
    _configure_gemini(monkeypatch)
    calls: list[dict] = []
    _mock_gemini(monkeypatch, calls)
    _mock_durations(monkeypatch, raw=processed + 0.2, processed=processed)
    plan = _plan(count, requested=requested)

    asyncio.run(synth_all.synthesize_all(plan, tmp_path))
    compose_timing(plan, transition_duration=0.8)

    assert len(calls) == 1
    assert all(
        scene.voice_script.rstrip(".\u2026") in calls[0]["text"]
        for scene in plan.scenes
    )
    assert all(scene.audio_filename == "" for scene in plan.scenes)
    assert plan.tts_continuous is True
    assert plan.tts_voice == "Callirrhoe"
    assert plan.tts_language == "vi-VN"
    assert plan.narration_duration == processed
    assert plan.total_duration == pytest.approx(processed)
    assert plan.render_timing_contract["duration_authority"] == "continuous_narration"
    assert (
        sum(scene.render_clip_duration for scene in plan.scenes)
        - plan.render_timing_contract["total_transition_overlap_seconds"]
        == pytest.approx(processed)
    )
    assert plan.tts_metadata["narration_synthesis_count"] == 1
    assert plan.tts_metadata["continuous_artifact_count"] == 1
    if count >= 7:
        diagnostic = plan.tts_metadata["narration_planning_diagnostic"]
        assert diagnostic["provider"] == "gemini"
        assert diagnostic["speed_applied_to_transport"] is False
        assert diagnostic["postprocess_assumption"]["pause_cap_applied"] is False


def test_raw_processed_duration_and_steps_are_auditable(monkeypatch, tmp_path):
    _configure_gemini(monkeypatch)
    calls: list[dict] = []
    _mock_gemini(monkeypatch, calls)
    _mock_durations(monkeypatch, raw=8.88, processed=8.426688)
    plan = _plan()

    asyncio.run(synth_all.synthesize_all(plan, tmp_path))

    metadata = plan.tts_metadata
    assert metadata["raw_duration"] == 8.88
    assert metadata["processed_duration"] == 8.426688
    assert metadata["duration_delta"] == pytest.approx(-0.453312)
    assert metadata["processing_steps"] == [
        "loudness_normalize_-16lufs",
        "true_peak_limit_-1dbtp",
        "mono_24000hz",
    ]
    assert metadata["requested_vs_narration_delta_seconds"] == pytest.approx(
        -3.573312
    )
    assert metadata["authoritative_timeline_duration_seconds"] == 8.426688


def test_valid_job_cache_resume_makes_zero_new_synthesis_calls(monkeypatch, tmp_path):
    _configure_gemini(monkeypatch)
    calls: list[dict] = []
    _mock_gemini(monkeypatch, calls)
    _mock_durations(monkeypatch)
    plan = _plan()

    asyncio.run(synth_all.synthesize_all(plan, tmp_path))
    asyncio.run(synth_all.synthesize_all(plan, tmp_path))

    assert len(calls) == 1
    assert plan.tts_metadata["cache_hit"] is True
    assert plan.tts_metadata["request_attempt_count"] == 0
    assert plan.tts_metadata["narration_synthesis_count"] == 0


@pytest.mark.parametrize("change", ["text", "voice"])
def test_text_or_voice_change_invalidates_job_cache(
    monkeypatch,
    tmp_path,
    change: str,
):
    _configure_gemini(monkeypatch)
    calls: list[dict] = []
    _mock_gemini(monkeypatch, calls)
    _mock_durations(monkeypatch)
    plan = _plan()
    asyncio.run(synth_all.synthesize_all(plan, tmp_path))

    if change == "text":
        plan.scenes[0].voice_script += " Một thay đổi nhỏ."
    else:
        monkeypatch.setenv("TELLA_TTS_VOICE", "Achernar")
    asyncio.run(synth_all.synthesize_all(plan, tmp_path))

    assert len(calls) == 2
    assert plan.tts_metadata["cache_hit"] is False


def test_missing_gemini_configuration_uses_truthful_edge_fallback(
    monkeypatch,
    tmp_path,
):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("TELLA_TTS_PROVIDER", raising=False)
    calls: list[str] = []

    async def fake_edge(
        self,
        text,
        out_path,
        *,
        voice,
        language,
        speed,
        codec,
        sample_rate,
        metadata=None,
    ):
        calls.append(text)
        out_path.write_bytes(b"deterministic mocked edge audio")
        return TTSResult(
            audio_path=out_path,
            provider="edge",
            voice=voice,
            language=language,
            metadata={**(metadata or {}), "codec": "mp3", "edge_rate": "-3%"},
        )

    async def fake_postprocess(raw_path, out_path, *, max_pause_ms):
        shutil.copyfile(raw_path, out_path)
        return {
            "silence_postprocess_applied": True,
            "max_pause_ms": max_pause_ms,
            "original_duration": 12.0,
            "processed_duration": 11.8,
            "duration_delta": -0.2,
            "processing_steps": ["cap_detected_silence_to_350ms"],
            "duration_change_expected": True,
            "longest_silence_before": 0.6,
            "longest_silence_after": 0.35,
        }

    monkeypatch.setattr(synth_all.EdgeTTSProvider, "synthesize", fake_edge)
    monkeypatch.setattr(synth_all, "_postprocess_narration_audio", fake_postprocess)
    monkeypatch.setattr(synth_all, "_ffprobe_duration", lambda path: _async_value(12.0))
    plan = _plan()

    asyncio.run(synth_all.synthesize_all(plan, tmp_path))

    assert len(calls) == 1
    assert plan.tts_provider == "edge"
    assert plan.tts_fallback_used is True
    assert plan.tts_fallback_reason == "preferred_provider_unavailable"
    policy = plan.tts_metadata["production_tts_policy"]
    assert policy["preferred_provider"] == "gemini"
    assert policy["actual_provider"] == "edge"


def test_provider_error_redacts_secret_and_does_not_fallback_after_request(
    monkeypatch,
    tmp_path,
):
    secret = "test-secret-never-persist"
    _configure_gemini(monkeypatch)
    edge_calls: list[str] = []

    async def fail_gemini(*args, **kwargs):
        raise RuntimeError(f"provider rejected credential {secret}")

    async def forbidden_edge(*args, **kwargs):
        edge_calls.append("called")
        raise AssertionError("Edge must not run after a Gemini request failure")

    monkeypatch.setattr(GeminiTTSProvider, "synthesize", fail_gemini)
    monkeypatch.setattr(synth_all.EdgeTTSProvider, "synthesize", forbidden_edge)

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(synth_all.synthesize_all(_plan(), tmp_path))

    assert secret not in str(caught.value)
    assert "[REDACTED]" in str(caught.value)
    assert edge_calls == []


def test_persisted_metadata_contains_no_secret(monkeypatch, tmp_path):
    _configure_gemini(monkeypatch)
    calls: list[dict] = []
    _mock_gemini(monkeypatch, calls)
    _mock_durations(monkeypatch)

    asyncio.run(synth_all.synthesize_all(_plan(), tmp_path))

    persisted = (tmp_path / "tts_metadata.json").read_text(encoding="utf-8")
    assert "test-secret-never-persist" not in persisted
    assert json.loads(persisted)["tts_voice"] == "Callirrhoe"


def test_planning_diagnostic_is_explicitly_non_authoritative():
    diagnostic = narration_planning_diagnostic("một câu rất ngắn", 35.0)

    assert diagnostic["planning_duration_status"] == "likely_too_short"
    assert diagnostic["estimate_is_authoritative"] is False


async def _async_value(value):
    return value
