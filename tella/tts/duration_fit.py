"""Local post-TTS duration reconciliation for production recipes."""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from tella.planner.models import TellaScenePlan

logger = logging.getLogger("tella.tts.duration_fit")

PRACTICAL_MIN_DURATION = 32.0
PRACTICAL_TARGET_DURATION = 32.5
PRACTICAL_MAX_DURATION = 38.0
DURATION_FIT_SAFE_TEMPO_MIN = 0.85
DURATION_FIT_SAFE_TEMPO_MAX = 1.15


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
        raise RuntimeError(f"ffprobe narration duration failed: {message[-500:]}")
    try:
        return float(stdout.decode("utf-8").strip())
    except ValueError as exc:
        raise RuntimeError("ffprobe returned an invalid narration duration") from exc


def duration_fit_tempo(actual_duration: float, target_duration: float) -> float:
    if actual_duration <= 0 or target_duration <= 0:
        raise ValueError("audio durations must be positive")
    return actual_duration / target_duration


async def _run_atempo(source: Path, destination: Path, tempo: float) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-filter:a",
        f"atempo={tempo:.9f}",
        "-vn",
        "-codec:a",
        "libmp3lame",
        "-b:a",
        "96k",
        str(destination),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        message = stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"ffmpeg narration duration fit failed: {message[-1000:]}")


def _duration_metadata(plan: TellaScenePlan) -> dict:
    return {
        "planner_estimated_duration_seconds": plan.planner_estimated_duration_seconds,
        "original_narration_duration_seconds": plan.original_narration_duration_seconds,
        "fitted_narration_duration_seconds": plan.fitted_narration_duration_seconds,
        "duration_fit_required": plan.duration_fit_required,
        "duration_fit_applied": plan.duration_fit_applied,
        "duration_fit_reason": plan.duration_fit_reason,
        "duration_fit_target_seconds": plan.duration_fit_target_seconds,
        "duration_fit_tempo": plan.duration_fit_tempo,
        "duration_fit_scale": plan.duration_fit_scale,
        "duration_fit_safe_range_min": plan.duration_fit_safe_range_min,
        "duration_fit_safe_range_max": plan.duration_fit_safe_range_max,
        "duration_fit_within_safe_range": plan.duration_fit_within_safe_range,
        "source_narration_path": plan.source_narration_path,
        "fitted_narration_path": plan.fitted_narration_path,
        "actual_final_video_duration_seconds": plan.actual_final_video_duration_seconds,
        "actual_duration_validation_status": plan.actual_duration_validation_status,
        "actual_duration_failure_reason": plan.actual_duration_failure_reason,
        "local_post_tts_tempo_correction_applied": plan.duration_fit_applied,
    }


def _sync_duration_metadata(plan: TellaScenePlan) -> None:
    plan.tts_metadata = {
        **(plan.tts_metadata or {}),
        "narration_audio_path": plan.narration_audio_path,
        "narration_duration": plan.narration_duration,
        "processed_duration": plan.processed_narration_duration,
        **_duration_metadata(plan),
    }


def _scale_scene_audio_durations(
    plan: TellaScenePlan,
    original_duration: float,
    fitted_duration: float,
) -> None:
    scenes = [scene for scene in plan.scenes if scene.kind == "scene"]
    if not scenes:
        return
    weights = [max(0.0, float(scene.audio_duration)) for scene in scenes]
    weight_total = sum(weights)
    if weight_total <= 0:
        weights = [max(1, len((scene.voice_script or "").strip())) for scene in scenes]
        weight_total = float(sum(weights))
    assigned = 0.0
    for index, (scene, weight) in enumerate(zip(scenes, weights)):
        if index == len(scenes) - 1:
            duration = max(0.01, fitted_duration - assigned)
        else:
            duration = fitted_duration * (weight / weight_total)
            assigned += duration
        scene.audio_duration = round(duration, 4)


async def reconcile_practical_narration_duration(
    plan: TellaScenePlan,
    job_dir: Path,
) -> None:
    """Fit Practical Life Steps narration locally to its runtime contract."""
    if plan.recipe_id != "practical_life_steps_v1":
        return

    job_dir = Path(job_dir)
    current = (
        Path(plan.narration_audio_path)
        if plan.narration_audio_path
        else job_dir / plan.narration_audio_filename
    )
    if not current.is_file():
        raise RuntimeError(f"narration audio is missing before duration fit: {current}")

    actual_duration = await probe_duration(current)
    planner_estimate = float(
        plan.fitted_estimated_duration_seconds
        or plan.original_estimated_duration_seconds
        or 0.0
    )
    plan.planner_estimated_duration_seconds = round(planner_estimate, 3)
    plan.original_narration_duration_seconds = round(actual_duration, 3)
    plan.fitted_narration_duration_seconds = round(actual_duration, 3)
    plan.narration_duration = round(actual_duration, 2)
    plan.duration_fit_safe_range_min = DURATION_FIT_SAFE_TEMPO_MIN
    plan.duration_fit_safe_range_max = DURATION_FIT_SAFE_TEMPO_MAX
    plan.duration_fit_target_seconds = 0.0
    plan.duration_fit_tempo = 1.0
    plan.duration_fit_scale = 1.0
    plan.duration_fit_required = not (
        PRACTICAL_MIN_DURATION <= actual_duration <= PRACTICAL_MAX_DURATION
    )
    plan.duration_fit_applied = False
    plan.duration_fit_within_safe_range = True
    plan.duration_fit_reason = "actual narration already satisfies recipe duration"
    plan.source_narration_path = str(current)
    plan.fitted_narration_path = ""

    if not plan.duration_fit_required:
        plan.narration_duration = round(actual_duration, 2)
        plan.actual_duration_validation_status = "passed"
        plan.actual_duration_failure_reason = ""
        _sync_duration_metadata(plan)
        return

    target = (
        PRACTICAL_TARGET_DURATION
        if actual_duration < PRACTICAL_MIN_DURATION
        else PRACTICAL_MAX_DURATION - 0.5
    )
    tempo = duration_fit_tempo(actual_duration, target)
    scale = target / actual_duration
    within_safe_range = (
        DURATION_FIT_SAFE_TEMPO_MIN <= tempo <= DURATION_FIT_SAFE_TEMPO_MAX
    )
    plan.duration_fit_target_seconds = target
    plan.duration_fit_tempo = round(tempo, 9)
    plan.duration_fit_scale = round(scale, 9)
    plan.duration_fit_within_safe_range = within_safe_range
    plan.duration_fit_reason = (
        "actual narration below recipe minimum"
        if actual_duration < PRACTICAL_MIN_DURATION
        else "actual narration above recipe maximum"
    )

    if not within_safe_range:
        plan.actual_duration_validation_status = "failed"
        plan.actual_duration_failure_reason = (
            f"required tempo {tempo:.6f} is outside safe range "
            f"{DURATION_FIT_SAFE_TEMPO_MIN:.2f}-{DURATION_FIT_SAFE_TEMPO_MAX:.2f}"
        )
        _sync_duration_metadata(plan)
        raise RuntimeError(plan.actual_duration_failure_reason)

    assets_dir = job_dir / "assets"
    original = assets_dir / "narration_original.mp3"
    fitted = assets_dir / "narration_duration_fitted.mp3"
    final = assets_dir / "narration.mp3"
    if current.resolve() != original.resolve():
        shutil.copy2(current, original)
    await _run_atempo(original, fitted, tempo)
    fitted_duration = await probe_duration(fitted)
    shutil.copy2(fitted, final)
    final_duration = await probe_duration(final)

    if not (PRACTICAL_MIN_DURATION <= final_duration <= PRACTICAL_MAX_DURATION):
        plan.actual_duration_validation_status = "failed"
        plan.actual_duration_failure_reason = (
            f"locally fitted narration duration {final_duration:.3f}s is outside "
            f"recipe range {PRACTICAL_MIN_DURATION:.0f}-{PRACTICAL_MAX_DURATION:.0f}s"
        )
        _sync_duration_metadata(plan)
        raise RuntimeError(plan.actual_duration_failure_reason)

    _scale_scene_audio_durations(plan, actual_duration, final_duration)
    plan.duration_fit_applied = True
    plan.fitted_narration_duration_seconds = round(fitted_duration, 3)
    plan.source_narration_path = str(original)
    plan.fitted_narration_path = str(fitted)
    plan.narration_audio_filename = "assets/narration.mp3"
    plan.narration_audio_path = str(final)
    plan.narration_duration = round(final_duration, 2)
    plan.processed_narration_duration = round(final_duration, 3)
    plan.actual_duration_validation_status = "passed"
    plan.actual_duration_failure_reason = ""
    _sync_duration_metadata(plan)
    logger.info(
        "practical duration fit original=%.3fs target=%.3fs tempo=%.9f "
        "fitted=%.3fs source=%s fitted_path=%s",
        actual_duration,
        target,
        tempo,
        final_duration,
        original,
        fitted,
    )


async def validate_actual_video_duration(
    plan: TellaScenePlan,
    video_path: Path,
) -> None:
    if plan.recipe_id != "practical_life_steps_v1":
        return
    duration = await probe_duration(Path(video_path))
    plan.actual_final_video_duration_seconds = round(duration, 3)
    if PRACTICAL_MIN_DURATION <= duration <= PRACTICAL_MAX_DURATION:
        plan.actual_duration_validation_status = "passed"
        plan.actual_duration_failure_reason = ""
    else:
        plan.actual_duration_validation_status = "failed"
        plan.actual_duration_failure_reason = (
            f"actual final video duration {duration:.3f}s is outside recipe "
            f"range {PRACTICAL_MIN_DURATION:.0f}-{PRACTICAL_MAX_DURATION:.0f}s"
        )
    _sync_duration_metadata(plan)
    if plan.actual_duration_validation_status == "failed":
        raise RuntimeError(plan.actual_duration_failure_reason)


__all__ = [
    "DURATION_FIT_SAFE_TEMPO_MAX",
    "DURATION_FIT_SAFE_TEMPO_MIN",
    "PRACTICAL_MAX_DURATION",
    "PRACTICAL_MIN_DURATION",
    "PRACTICAL_TARGET_DURATION",
    "duration_fit_tempo",
    "probe_duration",
    "reconcile_practical_narration_duration",
    "validate_actual_video_duration",
]
