import asyncio
import json
import math
import socket
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from tella.music import audio
from tella.music.library import (
    MusicLibraryError,
    default_library_root,
    load_library,
    select_track,
)
from tella.music.profiles import profile_for_recipe
from tella.music.service import configure_music
from tella.planner.practical_life_steps import plan_practical_life_steps_from_script
from tella.recipes import apply_recipe_metadata, get_recipe


_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "script_practical_life_steps_test.txt"


def _write_tone(
    path: Path,
    duration: float,
    *,
    frequency: float = 220.0,
    amplitude: float = 0.3,
    channels: int = 1,
) -> None:
    sample_rate = 44100
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        frames = bytearray()
        for index in range(int(duration * sample_rate)):
            sample = int(32767 * amplitude * math.sin(2 * math.pi * frequency * index / sample_rate))
            packed = struct.pack("<h", sample)
            frames.extend(packed * channels)
        output.writeframes(bytes(frames))


def _track(track_id: str, *, recipes=None, energy="medium_low", loop_safe=True):
    return {
        "track_id": track_id,
        "file_path": f"tracks/{track_id}.wav",
        "moods": ["calm", "encouraging", "light_rhythm"],
        "supported_recipes": recipes or ["practical_life_steps_v1"],
        "energy": energy,
        "intro_safe_seconds": 0.5,
        "loop_safe": loop_safe,
        "loop_start": 0.1 if loop_safe else 0.0,
        "loop_end": 0.9 if loop_safe else 0.0,
        "default_start_offset": 0.0,
        "license_type": "user_provided_test_license",
        "license_reference": f"licenses/{track_id}.txt",
        "attribution_required": False,
        "attribution_text": "",
        "enabled": True,
    }


def _library(tmp_path: Path, entries: list[dict], duration: float = 1.0) -> Path:
    root = tmp_path / "music"
    (root / "tracks").mkdir(parents=True)
    (root / "licenses").mkdir()
    for entry in entries:
        _write_tone(root / entry["file_path"], duration, channels=2)
        (root / entry["license_reference"]).write_text(
            "Synthetic test fixture license metadata.",
            encoding="utf-8",
        )
    (root / "library.json").write_text(
        json.dumps({"version": 1, "tracks": entries}, indent=2),
        encoding="utf-8",
    )
    return root


def _plan():
    plan = plan_practical_life_steps_from_script(
        user_script=_SCRIPT.read_text(encoding="utf-8"),
        target_lang="vi",
    )
    apply_recipe_metadata(
        plan,
        get_recipe("practical_life_steps_v1"),
        validation_status="passed",
    )
    plan.narration_duration = 3.0
    return plan


def test_library_rejects_missing_audio_and_license(tmp_path):
    entry = _track("missing_audio")
    root = tmp_path / "music"
    root.mkdir()
    (root / "library.json").write_text(
        json.dumps({"tracks": [entry]}), encoding="utf-8"
    )
    with pytest.raises(MusicLibraryError, match="audio file is missing"):
        load_library(root)

    (root / "tracks").mkdir()
    _write_tone(root / entry["file_path"], 1.0)
    with pytest.raises(MusicLibraryError, match="license reference is missing"):
        load_library(root)


def test_library_rejects_unstable_track_id(tmp_path):
    entry = _track("valid_id")
    entry["track_id"] = "Not Stable!"
    root = tmp_path / "music"
    root.mkdir()
    (root / "library.json").write_text(
        json.dumps({"tracks": [entry]}), encoding="utf-8"
    )

    with pytest.raises(MusicLibraryError, match="invalid stable track_id"):
        load_library(root)


def test_deterministic_selection_recent_avoidance_and_explicit_override(tmp_path):
    root = _library(tmp_path, [_track("calm_one"), _track("calm_two")])
    tracks = load_library(root)
    profile = profile_for_recipe("practical_life_steps_v1")
    first = select_track(
        tracks,
        recipe_id="practical_life_steps_v1",
        content_moods={"encouraging"},
        narration_duration=35.0,
        profile=profile,
        seed="stable-seed",
    )
    second = select_track(
        tracks,
        recipe_id="practical_life_steps_v1",
        content_moods={"encouraging"},
        narration_duration=35.0,
        profile=profile,
        seed="stable-seed",
    )
    avoided = select_track(
        tracks,
        recipe_id="practical_life_steps_v1",
        content_moods={"encouraging"},
        narration_duration=35.0,
        profile=profile,
        seed="stable-seed",
        recent_track_ids=(first.track.track_id,),
    )
    explicit = select_track(
        tracks,
        recipe_id="practical_life_steps_v1",
        content_moods=set(),
        narration_duration=35.0,
        profile=profile,
        seed="different",
        explicit_track_id="calm_one",
    )

    assert first.track.track_id == second.track.track_id
    assert avoided.track.track_id != first.track.track_id
    assert explicit.track.track_id == "calm_one"
    assert explicit.reason == "explicit track override"


def test_recipe_compatibility_and_profile_routing_are_unchanged(tmp_path):
    entry = _track("practical_only")
    tracks = load_library(_library(tmp_path, [entry]))
    with pytest.raises(MusicLibraryError, match="does not support"):
        select_track(
            tracks,
            recipe_id="emotional_symbolic_v1",
            content_moods=set(),
            narration_duration=35.0,
            profile=profile_for_recipe("emotional_symbolic_v1"),
            seed="x",
            explicit_track_id="practical_only",
        )

    assert profile_for_recipe("emotional_symbolic_v1").profile_id == "emotional_soft"
    assert profile_for_recipe("life_insight_symbolic_v1").profile_id == "life_insight_steady"
    assert profile_for_recipe("practical_life_steps_v1").profile_id == "practical_calm_rhythm"


def test_callirrhoe_recipe_resolves_supported_production_track_without_provider(
    monkeypatch, synthetic_practical_music_library,
):
    def forbidden_socket(*args, **kwargs):
        pytest.fail("production music catalogue resolution attempted network access")

    monkeypatch.setattr(socket, "create_connection", forbidden_socket)
    monkeypatch.setattr(socket.socket, "connect", forbidden_socket)
    tracks = load_library(default_library_root())
    profile = profile_for_recipe(
        "practical_life_steps_callirrhoe_v1", "practical_calm_rhythm"
    )

    selection = select_track(
        tracks,
        recipe_id="practical_life_steps_callirrhoe_v1",
        content_moods={"calm", "encouraging", "light_rhythm"},
        narration_duration=34.84,
        profile=profile,
        seed="zero-network-callirrhoe-production",
        explicit_track_id="synthetic_practical_tone",
    )

    assert selection.track.track_id == "synthetic_practical_tone"
    assert "practical_life_steps_callirrhoe_v1" in selection.track.supported_recipes
    assert selection.profile.profile_id == "practical_calm_rhythm"
    assert {"calm", "encouraging", "light_rhythm"}.issubset(
        selection.track.moods
    )


def test_production_track_allowlist_still_rejects_unrelated_recipe(
    synthetic_practical_music_library,
):
    tracks = load_library(default_library_root())
    with pytest.raises(MusicLibraryError, match="does not support"):
        select_track(
            tracks,
            recipe_id="emotional_symbolic_v1",
            content_moods=set(),
            narration_duration=34.84,
            profile=profile_for_recipe("emotional_symbolic_v1"),
            seed="still-fail-closed",
            explicit_track_id="synthetic_practical_tone",
        )


def test_no_music_mode_never_selects_or_reads_track(monkeypatch, tmp_path):
    plan = _plan()
    monkeypatch.setenv("TELLA_MUSIC_LIBRARY", str(tmp_path / "missing"))

    configure_music(plan, tmp_path / "job", no_music=True)

    assert plan.music_enabled is False
    assert plan.music_no_music is True
    assert plan.selected_music_track_id == ""
    assert plan.music_metadata["status"] == "disabled"


def test_prepare_music_loops_trims_and_fades(monkeypatch, tmp_path):
    root = _library(tmp_path, [_track("short_loop")], duration=1.0)
    monkeypatch.setenv("TELLA_MUSIC_LIBRARY", str(root))
    plan = _plan()
    configure_music(plan, tmp_path / "job", requested_track_id="short_loop")

    prepared, metadata = asyncio.run(
        audio.prepare_music(plan, tmp_path / "job", duration=3.0)
    )

    assert audio.probe_duration
    assert asyncio.run(audio.probe_duration(prepared)) == pytest.approx(3.0, abs=0.03)
    assert metadata["loop_used"] is True
    assert "trim_loop_segment" in metadata["trim_or_loop_operations"]
    assert "loop_boundary_micro_fades" in metadata["trim_or_loop_operations"]
    assert "loop_declared_safe_segment" in metadata["trim_or_loop_operations"]
    assert "fade_in" in metadata["trim_or_loop_operations"]
    assert "fade_out" in metadata["trim_or_loop_operations"]
    assert metadata["loop_discontinuity_status"] == "passed"


def test_prepare_music_trims_long_track_without_loop(monkeypatch, tmp_path):
    root = _library(
        tmp_path,
        [_track("long_trim", loop_safe=False)],
        duration=4.0,
    )
    monkeypatch.setenv("TELLA_MUSIC_LIBRARY", str(root))
    plan = _plan()
    configure_music(plan, tmp_path / "job", requested_track_id="long_trim")

    prepared, metadata = asyncio.run(
        audio.prepare_music(plan, tmp_path / "job", duration=2.0)
    )

    assert asyncio.run(audio.probe_duration(prepared)) == pytest.approx(2.0, abs=0.03)
    assert metadata["loop_used"] is False
    assert metadata["trim_or_loop_operations"] == [
        "trim",
        "fade_in",
        "fade_out",
        "base_gain",
    ]


def test_prepare_music_rejects_short_non_loop_safe_track(monkeypatch, tmp_path):
    root = _library(
        tmp_path,
        [_track("short_unsafe", loop_safe=False)],
        duration=1.0,
    )
    monkeypatch.setenv("TELLA_MUSIC_LIBRARY", str(root))
    plan = _plan()
    configure_music(plan, tmp_path / "job", requested_track_id="short_unsafe")

    with pytest.raises(RuntimeError, match="not loop-safe"):
        asyncio.run(audio.prepare_music(plan, tmp_path / "job", duration=3.0))


def test_ducking_mastering_and_audio_qc_are_local(monkeypatch, tmp_path):
    root = _library(tmp_path, [_track("licensed_loop")], duration=1.0)
    monkeypatch.setenv("TELLA_MUSIC_LIBRARY", str(root))
    plan = _plan()
    job = tmp_path / "job"
    job.mkdir()
    configure_music(plan, job, requested_track_id="licensed_loop")
    narration = job / "narration.wav"
    silent_video = job / "silent.mp4"
    output = job / "video.mp4"
    _write_tone(narration, 3.0, frequency=440.0, amplitude=0.35)
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=320x568:r=30:d=3",
            "-an", str(silent_video),
        ],
        check=True,
    )
    socket_calls = []

    def fail_socket(*args, **kwargs):
        socket_calls.append(args)
        raise AssertionError("network socket attempted")

    monkeypatch.setattr(socket, "create_connection", fail_socket)
    prepared, metadata = asyncio.run(audio.prepare_music(plan, job, duration=3.0))
    asyncio.run(
        audio.mix_music_and_narration(
            plan,
            silent_video,
            narration,
            prepared,
            output,
        )
    )
    qc = asyncio.run(
        audio.run_audio_qc(
            plan,
            job,
            narration=narration,
            prepared_music=prepared,
            final_video=output,
            expected_duration=3.0,
            loop_discontinuity_status=metadata["loop_discontinuity_status"],
        )
    )

    assert socket_calls == []
    assert qc["status"] in {"passed", "warning"}
    assert qc["clipping_detected"] is False
    assert qc["duration_mismatch"] <= 0.15
    assert qc["narration_music_balance_status"] == "passed"
    assert qc["music_loudness_lufs"] < qc["narration_loudness_lufs"]
    assert (job / "audio_qc.json").is_file()


def test_mix_filter_uses_sidechain_ducking_and_mastering(monkeypatch, tmp_path):
    plan = _plan()
    plan.music_enabled = True
    plan.selected_music_profile_id = "practical_calm_rhythm"
    captured = []
    narration = tmp_path / "narration.wav"
    narration.write_bytes(b"audio")

    async def fake_run(cmd, label):
        captured.extend(cmd)

    monkeypatch.setattr(audio, "_run", fake_run)
    asyncio.run(
        audio.mix_music_and_narration(
            plan,
            tmp_path / "silent.mp4",
            narration,
            tmp_path / "music.wav",
            tmp_path / "video.mp4",
        )
    )
    graph = captured[captured.index("-filter_complex") + 1]

    assert "sidechaincompress" in graph
    assert "amix" in graph
    assert "loudnorm=I=-16.0:TP=-1.0" in graph
    assert "alimiter=limit=0.891251" in graph


def test_audio_qc_fails_clipping_duration_and_bad_balance(monkeypatch, tmp_path):
    plan = _plan()
    narration = tmp_path / "narration.wav"
    music = tmp_path / "music.wav"
    final = tmp_path / "final.wav"
    for path in (narration, music, final):
        _write_tone(path, 1.0)
    stats = iter(
        (
            {"integrated_lufs": -18.0, "true_peak_dbtp": -3.0},
            {"integrated_lufs": -15.5, "true_peak_dbtp": -0.2},
            {"integrated_lufs": -17.0, "true_peak_dbtp": -2.0},
        )
    )

    async def fake_stats(path):
        return next(stats)

    async def fake_duration(path):
        return 1.0

    monkeypatch.setattr(audio, "analyze_loudness", fake_stats)
    monkeypatch.setattr(audio, "probe_duration", fake_duration)

    with pytest.raises(RuntimeError, match="audio QC failed"):
        asyncio.run(
            audio.run_audio_qc(
                plan,
                tmp_path,
                narration=narration,
                prepared_music=music,
                final_video=final,
                expected_duration=2.0,
            )
        )

    assert plan.audio_qc["status"] == "failed"
    assert plan.audio_qc["clipping_detected"] is True
    assert plan.audio_qc["duration_mismatch"] == pytest.approx(1.0)
    assert plan.audio_qc["narration_music_balance_status"] == "failed"


def test_missing_narration_fails_closed(tmp_path):
    plan = _plan()
    with pytest.raises(RuntimeError, match="missing narration"):
        asyncio.run(
            audio.run_audio_qc(
                plan,
                tmp_path,
                narration=tmp_path / "missing.wav",
                final_video=tmp_path / "video.mp4",
                expected_duration=3.0,
            )
        )


def test_narration_only_qc_has_no_music_balance_requirement(monkeypatch, tmp_path):
    plan = _plan()
    narration = tmp_path / "narration.wav"
    final = tmp_path / "final.wav"
    _write_tone(narration, 2.0)
    _write_tone(final, 2.0)

    async def fake_stats(path):
        return {"integrated_lufs": -16.0, "true_peak_dbtp": -2.0}

    async def fake_duration(path):
        return 2.0

    monkeypatch.setattr(audio, "analyze_loudness", fake_stats)
    monkeypatch.setattr(audio, "probe_duration", fake_duration)
    qc = asyncio.run(
        audio.run_audio_qc(
            plan,
            tmp_path,
            narration=narration,
            final_video=final,
            expected_duration=2.0,
        )
    )

    assert qc["status"] == "passed"
    assert qc["music_loudness_lufs"] is None
    assert qc["narration_music_balance_status"] == "not_applicable"
