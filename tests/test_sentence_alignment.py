import hashlib
import json
import math
import socket
import wave
from pathlib import Path

import pytest

from scripts import align_practical_callirrhoe as script
from tella.tts.sentence_alignment import AlignmentConfig, align_sentences, sha256_file
from tella.voice_profiles import get_voice_profile

SENTENCES = [f"Câu thử nghiệm số {index}." for index in range(1, 8)]


def _write_pattern(path: Path, pauses: set[int], *, block=2.7, pause=0.2, extra=()):
    rate = 16000
    samples = []
    total = block * 7
    pause_centers = [block * index for index in range(1, 7)]
    extra = tuple(extra)
    for sample_index in range(round(total * rate)):
        time = sample_index / rate
        silent = any(
            boundary in pauses and abs(time - center) <= pause / 2
            for boundary, center in enumerate(pause_centers, start=1)
        ) or any(abs(time - center) <= pause / 2 for center in extra)
        value = 0 if silent else round(9000 * math.sin(2 * math.pi * 220 * time))
        samples.append(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(rate)
        from array import array
        output.writeframes(array("h", samples).tobytes())
    return pause_centers


def test_six_clear_pauses_align_near_centers(tmp_path):
    wav = tmp_path / "six.wav"
    centers = _write_pattern(wav, {1, 2, 3, 4, 5, 6})
    result = align_sentences(wav, SENTENCES)
    assert result["fallback_count"] == 0
    assert all(abs(actual - expected) < 0.12 for actual, expected in zip(result["boundaries"], centers))
    assert all(item["candidate_type"] == "silence" for item in result["boundary_diagnostics"])


def test_extra_silence_selects_candidate_nearest_expected(tmp_path):
    wav = tmp_path / "extra.wav"
    centers = _write_pattern(wav, {1, 2, 3, 4, 5, 6}, extra=(3.35,))
    result = align_sentences(wav, SENTENCES)
    assert abs(result["boundaries"][0] - centers[0]) < 0.12
    assert abs(result["boundaries"][0] - 3.35) > 0.4


def test_no_silence_uses_deterministic_weighted_fallback(tmp_path):
    wav = tmp_path / "tone.wav"
    _write_pattern(wav, set())
    first = align_sentences(wav, SENTENCES)
    second = align_sentences(wav, SENTENCES)
    assert first["boundaries"] == second["boundaries"]
    assert first["fallback_count"] == 6
    assert all(item["candidate_type"] == "weighted_fallback" for item in first["boundary_diagnostics"])


def test_partial_silence_mixes_detection_and_fallback(tmp_path):
    wav = tmp_path / "partial.wav"
    _write_pattern(wav, {1, 3, 5})
    result = align_sentences(wav, SENTENCES)
    kinds = [item["candidate_type"] for item in result["boundary_diagnostics"]]
    assert "silence" in kinds and "weighted_fallback" in kinds
    assert 0 < result["fallback_count"] < 6


def test_intervals_are_monotonic_cover_duration_and_match_subtitles(tmp_path):
    wav = tmp_path / "coverage.wav"
    _write_pattern(wav, {1, 2, 3, 4, 5, 6})
    result = align_sentences(wav, SENTENCES)
    assert all(b > a for a, b in zip(result["boundaries"], result["boundaries"][1:]))
    assert result["scene_intervals"][0]["start"] == 0
    assert result["scene_intervals"][-1]["end"] == result["audio_duration"]
    assert result["subtitle_intervals"] == result["scene_intervals"]


def test_impossible_minimum_duration_fails(tmp_path):
    wav = tmp_path / "short.wav"
    _write_pattern(wav, set(), block=1.0)
    with pytest.raises(ValueError, match="minimum scene duration"):
        align_sentences(wav, SENTENCES, config=AlignmentConfig(minimum_scene_duration=2.0))


def test_manual_overrides_validate_and_apply(tmp_path):
    wav = tmp_path / "manual.wav"
    _write_pattern(wav, set())
    values = [2.5, 5.2, 7.9, 10.6, 13.3, 16.0]
    result = align_sentences(wav, SENTENCES, manual_boundaries=values)
    assert result["boundaries"] == values
    assert result["manual_override_count"] == 6
    assert all(item["candidate_type"] == "manual_override" for item in result["boundary_diagnostics"])
    with pytest.raises(ValueError, match="exactly six"):
        align_sentences(wav, SENTENCES, manual_boundaries=[3, 6])
    with pytest.raises(ValueError, match="minimum scene"):
        align_sentences(wav, SENTENCES, manual_boundaries=[1, 4, 7, 10, 13, 16])


def test_sentence_count_and_declared_duration_are_validated(tmp_path):
    wav = tmp_path / "validation.wav"
    _write_pattern(wav, set())
    with pytest.raises(ValueError, match="exactly seven"):
        align_sentences(wav, SENTENCES[:6])
    with pytest.raises(ValueError, match="does not match"):
        align_sentences(wav, SENTENCES, total_duration=25)


def test_analysis_never_rewrites_wav_and_metadata_is_truthful(tmp_path):
    wav = tmp_path / "immutable.wav"
    _write_pattern(wav, {1, 3, 5})
    before = sha256_file(wav)
    result = align_sentences(wav, SENTENCES)
    assert sha256_file(wav) == before == result["wav_sha256"]
    assert result["atempo_applied"] is False
    assert result["external_request_count"] == 0
    assert sum(item["fallback_used"] for item in result["boundary_diagnostics"]) == result["fallback_count"]


def test_apply_alignment_preserves_subtitle_text_and_profile_defaults(tmp_path):
    from tella.planner.models import Scene, TellaScenePlan
    scenes = [Scene(scene_index=i, voice_script=SENTENCES[i-1], audio_duration=2.7, start=(i-1)*2.7, duration=2.7) for i in range(1, 8)]
    plan = TellaScenePlan(title="Alignment", language="vi", aspect_ratio="9:16", media_source="ai_image", duration_mode="short", theme="practical_life_steps", scenes=scenes)
    plan.subtitle_segments = [{"scene_index": i, "start": (i-1)*2.7, "end": i*2.7, "text": SENTENCES[i-1], "highlight_words": []} for i in range(1, 8)]
    wav = tmp_path / "apply.wav"; _write_pattern(wav, {1,2,3,4,5,6})
    result = align_sentences(wav, SENTENCES)
    original_text = [item["text"] for item in plan.subtitle_segments]
    script._apply_alignment(plan, result)
    assert [item["text"] for item in plan.subtitle_segments] == original_text
    callirrhoe = get_voice_profile("gemini_callirrhoe_vi_natural_smile")
    assert (callirrhoe.voice, callirrhoe.model) == ("Callirrhoe", "gemini-3.1-flash-tts-preview")
    assert get_voice_profile("clear_female_vi").provider == "edge"


def test_network_guard_blocks_provider_style_socket_calls():
    original = socket.socket.connect
    try:
        script._install_network_guard()
        with pytest.raises(RuntimeError, match="network access is disabled"):
            socket.socket().connect(("127.0.0.1", 9))
    finally:
        socket.socket.connect = original
