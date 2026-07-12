"""Local FFmpeg music preparation, mastering, and audio QC."""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path

from tella.music.library import default_library_root, load_library
from tella.music.profiles import get_music_profile
from tella.planner.models import TellaScenePlan


@dataclass(frozen=True)
class AudioMasteringConfig:
    final_loudness_lufs: float = -16.0
    final_true_peak_dbtp: float = -1.0
    loudness_warning_tolerance: float = 2.0
    narration_music_margin_db: float = 6.0
    duration_tolerance_seconds: float = 0.15
    silence_lufs: float = -60.0


DEFAULT_MASTERING = AudioMasteringConfig()


async def _run(cmd: list[str], label: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        message = stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"{label} failed: {message[-1200:]}")


async def probe_duration(path: Path) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        message = stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"audio duration probe failed: {message[-500:]}")
    return float(stdout.decode("utf-8").strip())


async def analyze_loudness(path: Path) -> dict[str, float]:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        str(path),
        "-filter_complex",
        "ebur128=peak=true",
        "-f",
        "null",
        "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    text = stderr.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"audio loudness analysis failed: {text[-800:]}")
    integrated = re.findall(r"\bI:\s*(-?\d+(?:\.\d+)?)\s+LUFS", text)
    peaks = re.findall(r"\bPeak:\s*(-?\d+(?:\.\d+)?)\s+dBFS", text)
    if not integrated or not peaks:
        raise RuntimeError("audio loudness analysis returned no EBU R128 summary")
    return {
        "integrated_lufs": float(integrated[-1]),
        "true_peak_dbtp": float(peaks[-1]),
    }


async def prepare_music(
    plan: TellaScenePlan,
    job_dir: Path,
    *,
    duration: float,
) -> tuple[Path, dict]:
    tracks = load_library(default_library_root())
    track = tracks.get(plan.selected_music_track_id)
    if track is None:
        raise RuntimeError(f"selected music track is unavailable: {plan.selected_music_track_id}")
    profile = get_music_profile(plan.selected_music_profile_id)
    source_duration = await probe_duration(track.file_path)
    offset = min(track.default_start_offset, max(0.0, source_duration - 0.1))
    remaining = max(0.0, source_duration - offset)
    needs_loop = remaining + 0.01 < duration
    if needs_loop and not track.loop_safe:
        raise RuntimeError(
            f"music track {track.track_id} is too short and is not loop-safe"
        )

    work = Path(job_dir) / "_render"
    work.mkdir(parents=True, exist_ok=True)
    prepared = work / "music_prepared.wav"
    fade_in = min(profile.fade_in_seconds, duration / 4)
    fade_out = min(profile.fade_out_seconds, duration / 4)
    fade_out_start = max(0.0, duration - fade_out)
    filters = (
        f"atrim=duration={duration:.6f},asetpts=N/SR/TB,"
        f"afade=t=in:st=0:d={fade_in:.3f},"
        f"afade=t=out:st={fade_out_start:.3f}:d={fade_out:.3f},"
        f"volume={profile.base_gain_db:.2f}dB"
    )
    operations: list[str] = []

    if needs_loop:
        segment = work / "music_loop_segment.wav"
        loop_duration = track.loop_end - track.loop_start
        if loop_duration <= 0:
            raise RuntimeError(f"music track {track.track_id} has invalid loop boundaries")
        boundary_fade = min(0.02, loop_duration / 8)
        boundary_fade_out = max(0.0, loop_duration - boundary_fade)
        await _run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-ss", f"{track.loop_start:.6f}",
                "-i", str(track.file_path),
                "-t", f"{loop_duration:.6f}",
                "-af",
                f"afade=t=in:st=0:d={boundary_fade:.6f},"
                f"afade=t=out:st={boundary_fade_out:.6f}:d={boundary_fade:.6f}",
                "-vn", "-ac", "2", "-ar", "44100", str(segment),
            ],
            "music loop segment preparation",
        )
        await _run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-stream_loop", "-1", "-i", str(segment),
                "-t", f"{duration:.6f}",
                "-af", filters,
                "-ac", "2", "-ar", "44100", str(prepared),
            ],
            "music loop and fade preparation",
        )
        operations.extend(
            [
                "trim_loop_segment",
                "loop_boundary_micro_fades",
                "loop_declared_safe_segment",
            ]
        )
    else:
        await _run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-ss", f"{offset:.6f}", "-i", str(track.file_path),
                "-t", f"{duration:.6f}",
                "-af", filters,
                "-ac", "2", "-ar", "44100", str(prepared),
            ],
            "music trim and fade preparation",
        )
        operations.append("trim")
    operations.extend(["fade_in", "fade_out", "base_gain"])
    output_duration = await probe_duration(prepared)
    metadata = {
        "source_duration": round(source_duration, 3),
        "output_duration": round(output_duration, 3),
        "start_offset": offset,
        "trim_or_loop_operations": operations,
        "loop_used": needs_loop,
        "loop_start": track.loop_start,
        "loop_end": track.loop_end,
        "loop_discontinuity_status": "passed" if not needs_loop or track.loop_safe else "failed",
        "fade_in_seconds": fade_in,
        "fade_out_seconds": fade_out,
        "base_gain_db": profile.base_gain_db,
        "prepared_music_path": str(prepared),
        "ducking": {
            "threshold": profile.ducking_threshold,
            "ratio": profile.ducking_ratio,
            "attack_ms": profile.ducking_attack_ms,
            "release_ms": profile.ducking_release_ms,
        },
    }
    return prepared, metadata


async def mix_music_and_narration(
    plan: TellaScenePlan,
    silent_video: Path,
    narration: Path,
    prepared_music: Path,
    output: Path,
    *,
    config: AudioMasteringConfig = DEFAULT_MASTERING,
) -> Path:
    if not narration.is_file():
        raise RuntimeError(f"missing narration audio: {narration}")
    profile = get_music_profile(plan.selected_music_profile_id)
    filter_graph = (
        "[1:a]aresample=44100,asplit=2[narr_mix][narr_sc];"
        "[2:a]aresample=44100[music];"
        f"[music][narr_sc]sidechaincompress=threshold={profile.ducking_threshold}:"
        f"ratio={profile.ducking_ratio}:attack={profile.ducking_attack_ms}:"
        f"release={profile.ducking_release_ms}[ducked];"
        "[narr_mix][ducked]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
        f"loudnorm=I={config.final_loudness_lufs}:TP={config.final_true_peak_dbtp}:LRA=7,"
        "alimiter=limit=0.891251[outa]"
    )
    await _run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(silent_video),
            "-i", str(narration),
            "-i", str(prepared_music),
            "-filter_complex", filter_graph,
            "-map", "0:v", "-map", "[outa]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
            "-ar", "44100", "-ac", "2", "-shortest",
            "-movflags", "+faststart", str(output),
        ],
        "music ducking and mastering",
    )
    return output


def _write_audio_qc(plan: TellaScenePlan, job_dir: Path) -> Path:
    path = Path(job_dir) / "audio_qc.json"
    path.write_text(
        json.dumps(plan.audio_qc, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


async def run_audio_qc(
    plan: TellaScenePlan,
    job_dir: Path,
    *,
    narration: Path,
    final_video: Path,
    expected_duration: float,
    prepared_music: Path | None = None,
    loop_discontinuity_status: str = "not_applicable",
    config: AudioMasteringConfig = DEFAULT_MASTERING,
) -> dict:
    if not narration.is_file():
        raise RuntimeError(f"missing narration audio: {narration}")
    narration_stats = await analyze_loudness(narration)
    final_stats = await analyze_loudness(final_video)
    music_stats = await analyze_loudness(prepared_music) if prepared_music else None
    final_duration = await probe_duration(final_video)
    duration_delta = abs(final_duration - expected_duration)
    clipping = final_stats["true_peak_dbtp"] > config.final_true_peak_dbtp
    silent = final_stats["integrated_lufs"] <= config.silence_lufs
    balance_status = "not_applicable"
    failures: list[str] = []
    warnings: list[str] = []
    if music_stats:
        margin = narration_stats["integrated_lufs"] - music_stats["integrated_lufs"]
        balance_status = "passed" if margin >= config.narration_music_margin_db else "failed"
        if balance_status == "failed":
            failures.append(
                f"music is only {margin:.2f} dB below narration; narration may be unreadable"
            )
    if clipping:
        failures.append(
            f"true peak {final_stats['true_peak_dbtp']:.2f} dBTP exceeds "
            f"{config.final_true_peak_dbtp:.2f} dBTP"
        )
    if silent:
        failures.append("final audio is silent")
    if duration_delta > config.duration_tolerance_seconds:
        failures.append(
            f"final audio duration mismatch is {duration_delta:.3f}s"
        )
    if loop_discontinuity_status == "failed":
        failures.append("music loop discontinuity validation failed")
    loudness_delta = abs(
        final_stats["integrated_lufs"] - config.final_loudness_lufs
    )
    if loudness_delta > config.loudness_warning_tolerance:
        warnings.append(
            f"final loudness differs from target by {loudness_delta:.2f} LU"
        )
    status = "failed" if failures else ("warning" if warnings else "passed")
    qc = {
        "status": status,
        "narration_loudness_lufs": narration_stats["integrated_lufs"],
        "music_loudness_lufs": (
            music_stats["integrated_lufs"] if music_stats else None
        ),
        "final_integrated_loudness_lufs": final_stats["integrated_lufs"],
        "true_peak_dbtp": final_stats["true_peak_dbtp"],
        "clipping_detected": clipping,
        "silent_audio_detected": silent,
        "narration_music_balance_status": balance_status,
        "loop_discontinuity_status": loop_discontinuity_status,
        "expected_duration": round(expected_duration, 3),
        "output_duration": round(final_duration, 3),
        "duration_mismatch": round(duration_delta, 3),
        "final_loudness_target_lufs": config.final_loudness_lufs,
        "final_true_peak_limit_dbtp": config.final_true_peak_dbtp,
        "failure_reasons": failures,
        "warnings": warnings,
    }
    plan.audio_qc = qc
    _write_audio_qc(plan, job_dir)
    if failures:
        raise RuntimeError("audio QC failed: " + "; ".join(failures))
    return qc


__all__ = [
    "AudioMasteringConfig",
    "DEFAULT_MASTERING",
    "analyze_loudness",
    "mix_music_and_narration",
    "prepare_music",
    "probe_duration",
    "run_audio_qc",
]
