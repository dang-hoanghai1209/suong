"""Strictly reuse a Practical Life Steps job and rerender it locally."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
from pathlib import Path

from tella.composer.compose import compose_timing
from tella.media.fetch import fetch_assets
from tella.music.service import configure_music
from tella.planner.models import TellaScenePlan
from tella.render.pipeline import render
from tella.tts.duration_fit import (
    reconcile_practical_narration_duration,
    validate_actual_video_duration,
)


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


async def rerender_local(
    source_job: Path,
    target_job: Path,
    *,
    music_track_id: str = "",
    music_profile_id: str = "",
    no_music: bool = False,
) -> Path:
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-job", type=Path, required=True)
    parser.add_argument("--target-job", type=Path, required=True)
    parser.add_argument("--music-track", default="")
    parser.add_argument("--music-profile", default="")
    parser.add_argument("--no-music", action="store_true")
    args = parser.parse_args()
    if args.no_music and (args.music_track or args.music_profile):
        parser.error("--no-music cannot be combined with music overrides")
    video = asyncio.run(
        rerender_local(
            args.source_job,
            args.target_job,
            music_track_id=args.music_track,
            music_profile_id=args.music_profile,
            no_music=args.no_music,
        )
    )
    print(video)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
