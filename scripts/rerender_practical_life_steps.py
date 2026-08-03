"""Strictly reuse a Practical Life Steps job and rerender it locally."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
from pathlib import Path

from tella.music.service import configure_music
from tella.music.audio import (
    mix_music_and_narration,
    prepare_music,
    probe_duration,
    run_audio_qc,
)
from tella.music.service import write_music_metadata
from tella.planner.models import TellaScenePlan


def _write_metadata(plan: TellaScenePlan, job_dir: Path) -> None:
    (job_dir / "plan.json").write_text(
        json.dumps(plan.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (job_dir / "tts_metadata.json").write_text(
        json.dumps(plan.tts_metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def _preview_frame(video: Path, destination: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-ss",
        "00:00:17",
        "-i",
        str(video),
        "-frames:v",
        "1",
        str(destination),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        message = stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"preview frame extraction failed: {message[-500:]}")


async def _preserve_mixed_audio(video: Path, destination: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
        "-vn", "-c:a", "copy", str(destination),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        message = stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"mixed audio preservation failed: {message[-500:]}")


async def rerender_local(
    source_job: Path,
    target_job: Path,
    *,
    music_track_id: str = "",
    music_profile_id: str = "",
    no_music: bool = False,
) -> Path:
    from tella.composer.compose import compose_timing
    from tella.media.fetch import fetch_assets
    from tella.render.pipeline import render
    from tella.tts.duration_fit import (
        reconcile_practical_narration_duration,
        validate_actual_video_duration,
    )

    source_job = source_job.resolve()
    target_job = target_job.resolve()
    if target_job.exists():
        raise RuntimeError(f"target job already exists: {target_job}")
    source_plan_path = source_job / "plan.json"
    if not source_plan_path.is_file():
        raise RuntimeError(f"source plan is missing: {source_plan_path}")

    plan = TellaScenePlan.model_validate_json(
        source_plan_path.read_text(encoding="utf-8")
    )
    if plan.recipe_id != "practical_life_steps_v1" or plan.recipe_version != 1:
        raise RuntimeError("source job is not practical_life_steps_v1 version 1")
    body_scenes = [scene for scene in plan.scenes if scene.kind == "scene"]
    if [scene.scene_index for scene in body_scenes] != list(range(1, 8)):
        raise RuntimeError("source job must contain exactly scene indices 1 through 7")

    source_audio = source_job / "assets" / "narration.mp3"
    if not source_audio.is_file():
        raise RuntimeError(f"source narration is missing: {source_audio}")

    target_job.mkdir(parents=True)
    (target_job / "assets").mkdir()
    source_recipe = source_job / "recipe.json"
    if source_recipe.is_file():
        shutil.copy2(source_recipe, target_job / "recipe.json")

    env_names = {
        "TELLA_REUSE_ASSETS": "1",
        "TELLA_SKIP_IMAGE_GENERATION": "1",
        "TELLA_IMAGES_FROM_JOB": str(source_job),
        "TELLA_REUSE_ASSETS_MODE": "strict",
        "TELLA_ALLOW_MISMATCHED_REUSED_ASSETS": "0",
        "TELLA_REQUIRE_REUSED_SCENE_INDICES": "1,2,3,4,5,6,7",
        "TELLA_ALLOW_LOCAL_IMAGE_FALLBACK": "0",
        "TELLA_DISABLE_STOCK_FALLBACK": "1",
        "TELLA_SCENE_QC": "off",
    }
    previous = {name: os.environ.get(name) for name in env_names}
    try:
        os.environ.update(env_names)
        await fetch_assets(plan, target_job)
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    if plan.ai_images_requested != 0 or plan.ai_images_reused != 7:
        raise RuntimeError(
            "local rerender asset invariant failed: expected 0 provider requests "
            f"and 7 reused assets, got {plan.ai_images_requested} and "
            f"{plan.ai_images_reused}"
        )

    original_audio = target_job / "assets" / "narration_original.mp3"
    shutil.copy2(source_audio, original_audio)
    source_tts = source_job / "tts_metadata.json"
    if source_tts.is_file():
        plan.tts_metadata = json.loads(source_tts.read_text(encoding="utf-8"))
    plan.narration_audio_filename = "assets/narration_original.mp3"
    plan.narration_audio_path = str(original_audio)
    plan.narration_duration = 0.0
    plan.actual_final_video_duration_seconds = 0.0
    plan.actual_duration_validation_status = "not_evaluated"
    plan.actual_duration_failure_reason = ""

    await reconcile_practical_narration_duration(plan, target_job)
    configure_music(
        plan,
        target_job,
        requested_track_id=music_track_id,
        requested_profile_id=music_profile_id,
        no_music=no_music,
    )
    compose_timing(plan)
    _write_metadata(plan, target_job)
    video = await render(plan, target_job)
    await validate_actual_video_duration(plan, video)
    _write_metadata(plan, target_job)
    await _preview_frame(video, target_job / "preview_frame_17s.jpg")
    return video


async def remix_music_ab(
    source_job: Path,
    target_job: Path,
    *,
    music_track_id: str,
    music_profile_id: str = "",
    music_gain_db: float | None = None,
    ducking_ratio: float | None = None,
    ducking_attack_ms: int | None = None,
    ducking_release_ms: int | None = None,
) -> Path:
    """Create an audio-only A/B variant without recomposing any visual or TTS."""
    source_job = source_job.resolve()
    target_job = target_job.resolve()
    if target_job.exists():
        raise RuntimeError(f"target job already exists: {target_job}")
    plan = TellaScenePlan.model_validate_json(
        (source_job / "plan.json").read_text(encoding="utf-8")
    )
    narration = source_job / "assets" / "narration.mp3"
    silent_video = source_job / "_render" / "silent_video.mp4"
    required = [narration, silent_video]
    required.extend(source_job / "assets" / f"scene_{index:02d}_{suffix}.jpg" for index, suffix in (
        (1, "hook"), (2, "context"), (3, "practical_step_1"),
        (4, "practical_step_2"), (5, "practical_step_3"),
        (6, "common_mistake"), (7, "today_action"),
    ))
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError("source A/B inputs are missing: " + ", ".join(missing))

    target_job.mkdir(parents=True)
    shutil.copytree(source_job / "assets", target_job / "assets")
    shutil.copytree(source_job / "_render", target_job / "_render")
    for name in ("recipe.json", "tts_metadata.json"):
        source = source_job / name
        if source.is_file():
            shutil.copy2(source, target_job / name)

    target_narration = target_job / "assets" / "narration.mp3"
    target_silent_video = target_job / "_render" / "silent_video.mp4"
    exact_duration = await probe_duration(target_narration)
    plan.narration_audio_filename = "assets/narration.mp3"
    plan.narration_audio_path = str(target_narration)
    plan.narration_duration = exact_duration
    plan.total_duration = exact_duration
    configure_music(
        plan,
        target_job,
        requested_track_id=music_track_id,
        requested_profile_id=music_profile_id,
    )
    overrides = {
        key: value for key, value in {
            "base_gain_db": music_gain_db,
            "ducking_ratio": ducking_ratio,
            "ducking_attack_ms": ducking_attack_ms,
            "ducking_release_ms": ducking_release_ms,
        }.items() if value is not None
    }
    if overrides:
        plan.music_metadata = {**plan.music_metadata, "mix_overrides": overrides}
    prepared, processing = await prepare_music(
        plan, target_job, duration=exact_duration
    )
    plan.music_metadata = {**plan.music_metadata, "processing": processing}
    write_music_metadata(plan, target_job)
    video = target_job / "video.mp4"
    await mix_music_and_narration(
        plan, target_silent_video, target_narration, prepared, video
    )
    await _preserve_mixed_audio(video, target_job / "_render" / "final_mixed_audio.m4a")
    await run_audio_qc(
        plan,
        target_job,
        narration=target_narration,
        prepared_music=prepared,
        final_video=video,
        expected_duration=exact_duration,
        loop_discontinuity_status=processing["loop_discontinuity_status"],
    )
    plan.music_metadata = {
        **plan.music_metadata,
        "final_duration": plan.audio_qc["output_duration"],
        "loudness_statistics": {
            "narration_loudness_lufs": plan.audio_qc["narration_loudness_lufs"],
            "processed_music_loudness_lufs": plan.audio_qc["music_loudness_lufs"],
            "final_integrated_loudness_lufs": plan.audio_qc["final_integrated_loudness_lufs"],
            "final_true_peak_dbtp": plan.audio_qc["true_peak_dbtp"],
        },
        "qc_result": plan.audio_qc["status"],
    }
    write_music_metadata(plan, target_job)
    _write_metadata(plan, target_job)
    await _preview_frame(video, target_job / "preview_frame_17s.jpg")
    return video


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-job", type=Path, required=True)
    parser.add_argument("--target-job", type=Path, required=True)
    parser.add_argument("--music-track", default="")
    parser.add_argument("--music-profile", default="")
    parser.add_argument("--no-music", action="store_true")
    parser.add_argument("--audio-only-ab", action="store_true")
    parser.add_argument("--music-gain-db", type=float)
    parser.add_argument("--ducking-ratio", type=float)
    parser.add_argument("--ducking-attack-ms", type=int)
    parser.add_argument("--ducking-release-ms", type=int)
    args = parser.parse_args()
    if args.no_music and (args.music_track or args.music_profile):
        parser.error("--no-music cannot be combined with music overrides")
    operation = remix_music_ab if args.audio_only_ab else rerender_local
    if args.audio_only_ab and args.no_music:
        parser.error("--audio-only-ab cannot use --no-music")
    balance_args = ({
        "music_gain_db": args.music_gain_db,
        "ducking_ratio": args.ducking_ratio,
        "ducking_attack_ms": args.ducking_attack_ms,
        "ducking_release_ms": args.ducking_release_ms,
    } if args.audio_only_ab else {})
    video = asyncio.run(
        operation(
            args.source_job,
            args.target_job,
            music_track_id=args.music_track,
            music_profile_id=args.music_profile,
            **balance_args,
            **({} if args.audio_only_ab else {"no_music": args.no_music}),
        )
    )
    print(video)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
