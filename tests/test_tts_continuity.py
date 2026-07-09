import asyncio
import math
import shutil
import wave
from pathlib import Path

import pytest

from tella._voice_pace import resolve_pace
from tella.planner.models import Scene, TellaScenePlan
from tella.tts import synth_all
from tella.tts.providers import TTSResult


def _plan() -> TellaScenePlan:
    return TellaScenePlan(
        title="Continuous voice",
        language="vi",
        aspect_ratio="9:16",
        media_source="ai_image",
        duration_mode="short",
        theme="minimalist_emotional",
        voice_name="vi-VN-HoaiMyNeural",
        voice_edge_rate="-3%",
        scenes=[
            Scene(scene_index=1, kind="scene", title="Một", voice_script="Cô ấy đi ngang qua con phố."),
            Scene(scene_index=2, kind="scene", title="Hai", voice_script="Cô ấy nhìn thấy tiệm bánh nhỏ."),
            Scene(scene_index=3, kind="scene", title="Ba", voice_script="Cô ấy chọn một chiếc bánh cho mình."),
        ],
    )


async def _fake_postprocess(raw_path: Path, out_path: Path, *, max_pause_ms: int) -> dict:
    shutil.copyfile(raw_path, out_path)
    return {
        "silence_postprocess_applied": True,
        "max_pause_ms": max_pause_ms,
        "original_duration": 9.0,
        "processed_duration": 8.1,
        "longest_silence_before": 0.9,
        "longest_silence_after": 0.3,
    }


def test_tts_uses_global_narration_text_when_continuous(monkeypatch, tmp_path):
    captured: dict[str, str] = {}

    async def fake_synthesize(self, text, out_path, *, voice, language, speed, codec, sample_rate, metadata=None):
        captured["text"] = text
        out_path.write_bytes(b"fake mp3 bytes for tests")
        return TTSResult(
            audio_path=out_path,
            provider="edge",
            voice=voice,
            language=language,
            metadata={**(metadata or {}), "edge_rate": "-3%", "codec": "mp3"},
        )

    monkeypatch.setenv("TELLA_TTS_CONTINUOUS", "1")
    monkeypatch.setenv("TELLA_TTS_PROVIDER", "edge")
    monkeypatch.setattr(synth_all.EdgeTTSProvider, "synthesize", fake_synthesize)
    monkeypatch.setattr(synth_all, "_ffprobe_duration", lambda path: _async_value(8.1))
    monkeypatch.setattr(synth_all, "_postprocess_narration_audio", _fake_postprocess)
    plan = _plan()
    original_scene_text = [scene.voice_script for scene in plan.scenes]

    asyncio.run(synth_all.synthesize_all(plan, tmp_path))

    assert captured["text"] == plan.global_narration_text
    assert plan.tts_text_source == "global_narration_text"
    assert plan.tts_continuous is True
    assert "\n" not in captured["text"]
    assert "phố, Cô ấy nhìn" in captured["text"]
    assert [scene.voice_script for scene in plan.scenes] == original_scene_text
    assert plan.tts_metadata["silence_postprocess_applied"] is True
    assert plan.tts_metadata["original_duration"] == 9.0
    assert plan.tts_metadata["processed_duration"] == 8.1


def test_minimalist_edge_rate_default_is_not_minus_ten():
    pace = resolve_pace(theme="minimalist_emotional")

    assert pace.edge_rate in {"-3%", "+0%", "0%"}
    assert pace.edge_rate != "-10%"


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg required")
def test_silence_postprocess_reduces_long_pause_without_cutting_speech(tmp_path):
    raw_wav = tmp_path / "raw.wav"
    processed = tmp_path / "processed.mp3"
    _write_tone_silence_tone(raw_wav)

    result = asyncio.run(
        synth_all._postprocess_narration_audio(
            raw_wav,
            processed,
            max_pause_ms=350,
        )
    )

    assert result["silence_postprocess_applied"] is True
    assert result["processed_duration"] < result["original_duration"]
    assert result["processed_duration"] > 0.8
    assert result["longest_silence_after"] <= result["longest_silence_before"]
    assert processed.is_file()


async def _async_value(value):
    return value


def _write_tone_silence_tone(path: Path) -> None:
    sample_rate = 16000
    tone_seconds = 0.5
    silence_seconds = 1.2
    frames: list[int] = []
    for _segment in range(2):
        for i in range(int(sample_rate * tone_seconds)):
            frames.append(int(12000 * math.sin(2 * math.pi * 440 * i / sample_rate)))
        if _segment == 0:
            frames.extend([0] * int(sample_rate * silence_seconds))

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"".join(int(sample).to_bytes(2, "little", signed=True) for sample in frames))
