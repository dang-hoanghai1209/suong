import asyncio
import json
import shutil
import socket
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.benchmark_gemini_tts import BENCHMARK_TEXT, parse_voices, run_benchmark
from tella.recipes import list_recipes
from tella.tts import gemini
from tella.tts.gemini_registry import (
    APPROVED_MODELS, REGISTRY_VERSION, STYLE_PRESETS, VOICE_NAMES,
    GeminiVoice, VOICES, resolve_style, resolve_voice,
)
from tella.tts.providers import EdgeTTSProvider, GeminiTTSProvider, get_tts_provider
from tella.voice_profiles import list_voice_profiles

MODEL = "gemini-3.1-flash-tts-preview"


def test_registry_accepts_exactly_six_canonical_voices():
    assert VOICE_NAMES == ("Achernar", "Autonoe", "Callirrhoe", "Gacrux", "Leda", "Zephyr")
    assert tuple(VOICES) == VOICE_NAMES
    for name in VOICE_NAMES:
        voice = resolve_voice(name, MODEL)
        assert voice.provider == "gemini"
        assert voice.benchmark_language == "vi-VN"
        assert voice.registry_version == REGISTRY_VERSION


def test_achernar_is_accepted_but_achenar_and_unknown_are_rejected():
    assert resolve_voice("Achernar", MODEL).canonical_name == "Achernar"
    with pytest.raises(ValueError, match="unknown"):
        resolve_voice("Achenar", MODEL)
    with pytest.raises(ValueError, match="unknown"):
        resolve_voice("Missing", MODEL)


def test_disabled_and_incompatible_voices_are_rejected(monkeypatch):
    monkeypatch.setitem(VOICES, "Achernar", GeminiVoice(
        "gemini", "Achernar", False, "vi-VN", (), APPROVED_MODELS, REGISTRY_VERSION
    ))
    with pytest.raises(ValueError, match="disabled"):
        resolve_voice("Achernar", MODEL)
    with pytest.raises(ValueError, match="incompatible"):
        resolve_voice("Autonoe", "unapproved-model")


def test_three_styles_resolve_deterministically():
    assert tuple(STYLE_PRESETS) == ("natural", "vocal_smile", "natural_vocal_smile")
    for name, instruction in STYLE_PRESETS.items():
        assert resolve_style(name) == instruction == resolve_style(name)


def test_edge_and_recipe_defaults_are_unchanged():
    assert isinstance(get_tts_provider("edge"), EdgeTTSProvider)
    assert isinstance(get_tts_provider("gemini"), GeminiTTSProvider)
    assert all(profile.provider == "edge" for profile in list_voice_profiles())
    recipe_profile_ids = {recipe.voice_profile_id for recipe in list_recipes()}
    assert recipe_profile_ids
    assert all(profile.provider == "edge" for profile in list_voice_profiles() if profile.profile_id in recipe_profile_ids)


def test_dry_run_has_zero_calls_and_distinct_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    calls = []
    async def fail_synth(*args, **kwargs):
        calls.append(args)
        raise AssertionError("provider called in dry-run")
    manifest = asyncio.run(run_benchmark(
        voices=VOICE_NAMES, model=MODEL, style="natural_vocal_smile",
        output_dir=tmp_path, dry_run=True, max_requests=6, no_retry=True,
        synthesize_fn=fail_synth,
    ))
    assert calls == []
    assert manifest["request_count"] == 0
    assert manifest["post_tts_atempo_applied"] is False
    assert all(e["raw_output_path"] != e["normalized_output_path"] for e in manifest["entries"])


def test_maximum_request_count_and_voice_limit_are_enforced(tmp_path):
    with pytest.raises(ValueError, match="maximum"):
        asyncio.run(run_benchmark(
            voices=VOICE_NAMES, model=MODEL, style="natural", output_dir=tmp_path,
            dry_run=True, max_requests=5, no_retry=True,
        ))
    with pytest.raises(ValueError, match="six"):
        parse_voices("Achernar,Autonoe,Callirrhoe,Gacrux,Leda,Zephyr,Extra")


def _write_wav(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(24000)
        output.writeframes(b"\0\0" * 240)


def test_live_runner_is_sequential_one_attempt_per_voice_and_has_metadata(tmp_path):
    active = 0
    order = []
    async def fake_synth(text, path, *, model, voice, style):
        nonlocal active
        assert active == 0
        active += 1; order.append(voice); _write_wav(path); await asyncio.sleep(0); active -= 1
        return {"provider": "gemini", "model": model, "voice": voice,
                "voice_registry_version": 1, "language": "vi-VN",
                "requested_style": style, "resolved_style_instruction": resolve_style(style),
                "source_narration_text_hash": "hash", "raw_output_path": str(path),
                "request_attempt_count": 1, "fallback_used": False}
    async def fake_normalize(raw, normalized): shutil.copyfile(raw, normalized)
    async def fake_duration(path): return 0.01
    selected = ("Achernar", "Autonoe", "Leda")
    result = asyncio.run(run_benchmark(
        voices=selected, model=MODEL, style="natural_vocal_smile", output_dir=tmp_path,
        dry_run=False, max_requests=3, no_retry=True, synthesize_fn=fake_synth,
        normalize_fn=fake_normalize, duration_fn=fake_duration,
    ))
    assert order == list(selected)
    assert result["request_count"] == 3
    assert all(e["request_attempt_count"] == 1 and not e["fallback_used"] for e in result["entries"])
    assert all(e["post_tts_duration_fit_status"].startswith("skipped") for e in result["entries"])


def test_live_mode_requires_no_retry(tmp_path):
    with pytest.raises(ValueError, match="no-retry"):
        asyncio.run(run_benchmark(
            voices=("Achernar",), model=MODEL, style="natural",
            output_dir=tmp_path, dry_run=False, max_requests=1, no_retry=False,
        ))


def test_partial_failure_stops_without_fallback(tmp_path):
    calls = []
    async def fake_synth(text, path, *, model, voice, style):
        calls.append(voice)
        if voice == "Autonoe": raise RuntimeError("provider failure")
        _write_wav(path)
        return {"provider": "gemini", "model": model, "voice": voice, "fallback_used": False}
    async def fake_normalize(raw, normalized): shutil.copyfile(raw, normalized)
    async def fake_duration(path): return 0.01
    with pytest.raises(RuntimeError, match="provider failure"):
        asyncio.run(run_benchmark(
            voices=("Achernar", "Autonoe", "Callirrhoe"), model=MODEL, style="natural",
            output_dir=tmp_path, dry_run=False, max_requests=3, no_retry=True,
            synthesize_fn=fake_synth, normalize_fn=fake_normalize, duration_fn=fake_duration,
        ))
    assert calls == ["Achernar", "Autonoe"]
    manifest = json.loads((tmp_path / "benchmark_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed_stopped_on_first_provider_failure"


def test_credentials_are_identified_but_never_serialized(tmp_path, monkeypatch):
    secret = "super-secret-key-value"
    monkeypatch.setenv("GEMINI_API_KEY", secret)
    manifest = asyncio.run(run_benchmark(
        voices=("Achernar",), model=MODEL, style="natural", output_dir=tmp_path,
        dry_run=True, max_requests=1, no_retry=True,
    ))
    serialized = json.dumps(manifest)
    assert manifest["credential_environment_variable"] == "GEMINI_API_KEY"
    assert secret not in serialized
    assert BENCHMARK_TEXT in manifest["narration_text"]


def test_official_sdk_shape_is_extracted_with_fake_client(tmp_path):
    pcm = b"\0\0" * 240
    inline = SimpleNamespace(data=pcm, mime_type="audio/L16;rate=24000")
    response = SimpleNamespace(candidates=[SimpleNamespace(
        content=SimpleNamespace(parts=[SimpleNamespace(inline_data=inline)])
    )])
    calls = []
    class FakeModels:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            return response
    client = SimpleNamespace(models=FakeModels())
    output = tmp_path / "achernar_raw.wav"
    metadata = asyncio.run(gemini.synthesize(
        BENCHMARK_TEXT, output, model=MODEL, voice="Achernar",
        style="natural_vocal_smile", client_factory=lambda: client,
    ))
    assert calls[0]["model"] == MODEL
    assert calls[0]["contents"] == BENCHMARK_TEXT
    assert output.read_bytes().startswith(b"RIFF")
    assert metadata["provider"] == "gemini"
    assert metadata["voice"] == "Achernar"
    assert metadata["requested_style"] == "natural_vocal_smile"
    assert metadata["request_attempt_count"] == 1
    assert metadata["fallback_used"] is False


def test_gemini_provider_failure_propagates_without_fallback(monkeypatch, tmp_path):
    async def fail(*args, **kwargs):
        raise RuntimeError("fake Gemini failure")
    monkeypatch.setattr(gemini, "synthesize", fail)
    provider = GeminiTTSProvider()
    with pytest.raises(RuntimeError, match="fake Gemini failure"):
        asyncio.run(provider.synthesize(
            "text", tmp_path / "raw.wav", voice="Achernar", language="vi-VN",
            speed=1.0, codec="wav", sample_rate=24000,
            metadata={"model": MODEL, "style": "natural"},
        ))
