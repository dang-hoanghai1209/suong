"""Zero-network sentence alignment and optional local Callirrhoe rerender."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import socket
from pathlib import Path

from tella.planner.models import TellaScenePlan
from tella.tts.sentence_alignment import AlignmentConfig, align_sentences, sha256_file

EXPECTED_NARRATION_SHA256 = "B7DE095B464A4B1E843893E833C1FFC017362AE7E2F18E36B7EF7A33C806FAA9"
EXPECTED_TEXT_SHA256 = "8ecc739570f4c7c993264fa928c0f5955a151422fbbfa68dc1bb0e7993287ae3"


def _install_network_guard() -> None:
    def blocked(*args, **kwargs):
        raise RuntimeError("network access is disabled for local sentence alignment")
    socket.create_connection = blocked
    socket.socket.connect = blocked


def _parse_manual(raw: str) -> list[float] | None:
    if not raw.strip():
        return None
    try:
        return [float(value.strip()) for value in raw.split(",")]
    except ValueError as exc:
        raise ValueError("manual boundaries must be comma-separated seconds") from exc


def _srt_time(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def analyze_job(
    source_job: Path,
    music_source_job: Path,
    *,
    config: AlignmentConfig,
    manual_boundaries: list[float] | None = None,
) -> tuple[TellaScenePlan, dict]:
    source_job = Path(source_job).resolve()
    music_source_job = Path(music_source_job).resolve()
    plan = TellaScenePlan.model_validate_json((source_job / "plan.json").read_text(encoding="utf-8"))
    scenes = [scene for scene in plan.scenes if scene.kind == "scene"]
    if len(scenes) != 7:
        raise RuntimeError("source plan must contain exactly seven narration scenes")
    sentences = [scene.voice_script for scene in scenes]
    canonical = plan.global_narration_text
    canonical_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if canonical_hash.lower() != EXPECTED_TEXT_SHA256.lower():
        raise RuntimeError("canonical narration text SHA256 mismatch")
    narration = source_job / "assets" / "narration_callirrhoe_normalized.wav"
    if not narration.is_file():
        raise RuntimeError("normalized Callirrhoe narration is missing")
    narration_hash = sha256_file(narration)
    if narration_hash.lower() != EXPECTED_NARRATION_SHA256.lower():
        raise RuntimeError("normalized narration SHA256 mismatch")
    image_paths = sorted((source_job / "assets").glob("scene_*.jpg"))
    if len(image_paths) != 7:
        raise RuntimeError("source job must contain exactly seven reusable images")
    mixed_audio = music_source_job / "_render" / "final_mixed_audio_ab.m4a"
    if not mixed_audio.is_file():
        raise RuntimeError("accepted final mixed audio is missing")
    original_intervals = [
        {"sentence_index": scene.scene_index, "start": scene.start,
         "end": round(scene.start + scene.duration, 6), "duration": scene.duration}
        for scene in scenes
    ]
    result = align_sentences(
        narration, sentences, total_duration=plan.narration_duration,
        current_expected_boundaries=[item["end"] for item in original_intervals[:-1]],
        config=config, manual_boundaries=manual_boundaries,
    )
    result.update({
        "alignment_mode": "sentence_silence",
        "canonical_narration_sha256": canonical_hash,
        "normalized_narration_sha256": narration_hash,
        "reused_final_mixed_audio_path": str(mixed_audio),
        "reused_final_mixed_audio_sha256": sha256_file(mixed_audio),
        "source_job": str(source_job),
        "music_source_job": str(music_source_job),
        "original_scene_intervals": original_intervals,
        "original_internal_boundaries": [item["end"] for item in original_intervals[:-1]],
        "aligned_internal_boundaries": result["boundaries"],
        "total_duration_before": plan.total_duration,
        "total_duration_after": result["audio_duration"],
        "narration_duration_before": plan.narration_duration,
        "narration_duration_after": result["audio_duration"],
        "atempo_status": "disabled",
        "external_request_count": 0,
        "reused_image_indices": [1, 2, 3, 4, 5, 6, 7],
        "reused_image_sha256": {path.name: sha256_file(path) for path in image_paths},
    })
    return plan, result


def _apply_alignment(plan: TellaScenePlan, result: dict) -> None:
    scenes = [scene for scene in plan.scenes if scene.kind == "scene"]
    original_subtitles = {item["scene_index"]: item for item in plan.subtitle_segments}
    for scene, interval in zip(scenes, result["scene_intervals"]):
        scene.start = interval["start"]
        scene.duration = interval["duration"]
        scene.audio_duration = interval["duration"]
    plan.total_duration = result["audio_duration"]
    plan.scene_timing_map = [
        {"scene_index": scene.scene_index, "start": scene.start, "duration": scene.duration}
        for scene in scenes
    ]
    plan.subtitle_segments = [
        {**original_subtitles[scene.scene_index], "start": interval["start"], "end": interval["end"]}
        for scene, interval in zip(scenes, result["subtitle_intervals"])
    ]


async def _render_local(plan: TellaScenePlan, output_dir: Path, mixed_audio: Path) -> Path:
    from tella.render.pipeline import render
    copied_audio = output_dir / "assets" / "final_mixed_audio_reused.m4a"
    shutil.copy2(mixed_audio, copied_audio)
    plan.narration_audio_filename = "assets/final_mixed_audio_reused.m4a"
    plan.narration_audio_path = str(copied_audio)
    plan.music_enabled = False
    plan.selected_music_track_id = "practical_calm_01"
    plan.selected_music_profile_id = "practical_calm_rhythm_ab"
    rendered = await render(plan, output_dir)
    silent = output_dir / "_render" / "silent_video.mp4"
    remuxed = output_dir / "video_aligned.mp4"
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(silent), "-i", str(copied_audio),
        "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "copy",
        "-t", f"{plan.total_duration:.6f}", "-movflags", "+faststart", str(remuxed),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode:
        raise RuntimeError(f"aligned audio stream-copy mux failed: {stderr.decode(errors='replace')[-500:]}")
    remuxed.replace(rendered)
    return rendered


def _write_artifacts(output_dir: Path, plan: TellaScenePlan, result: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "alignment_metadata.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "alignment_boundaries.json").write_text(
        json.dumps({"boundaries": result["boundaries"], "diagnostics": result["boundary_diagnostics"]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    subtitles = []
    for index, item in enumerate(plan.subtitle_segments, start=1):
        subtitles.append(f"{index}\n{_srt_time(item['start'])} --> {_srt_time(item['end'])}\n{item['text']}\n")
    (output_dir / "subtitles.srt").write_text("\n".join(subtitles), encoding="utf-8")


def main() -> int:
    _install_network_guard()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-job", type=Path, required=True)
    parser.add_argument("--music-source-job", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--alignment-mode", choices=("sentence_silence",), default="sentence_silence")
    parser.add_argument("--search-window-seconds", type=float, default=1.25)
    parser.add_argument("--minimum-scene-duration", type=float, default=2.0)
    parser.add_argument("--manual-boundaries", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--analyze-only", action="store_true")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--write-diagnostics", action="store_true")
    args = parser.parse_args()
    if sum((args.dry_run, args.analyze_only, args.render)) != 1:
        parser.error("choose exactly one of --dry-run, --analyze-only, or --render")
    config = AlignmentConfig(
        search_window_seconds=args.search_window_seconds,
        minimum_scene_duration=args.minimum_scene_duration,
    )
    plan, result = analyze_job(
        args.source_job, args.music_source_job, config=config,
        manual_boundaries=_parse_manual(args.manual_boundaries),
    )
    _apply_alignment(plan, result)
    if args.render or args.write_diagnostics:
        if Path(args.output_dir).exists():
            raise RuntimeError("output directory already exists")
        _write_artifacts(Path(args.output_dir), plan, result)
    if args.render:
        source = Path(args.source_job).resolve()
        output = Path(args.output_dir).resolve()
        shutil.copytree(source / "assets", output / "assets")
        if (source / "recipe.json").is_file():
            shutil.copy2(source / "recipe.json", output / "recipe.json")
        (output / "plan.json").write_text(json.dumps(plan.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
        video = asyncio.run(_render_local(plan, output, Path(result["reused_final_mixed_audio_path"])))
        print(video)
    else:
        print(json.dumps({
            "boundaries": result["boundaries"],
            "diagnostics": result["boundary_diagnostics"],
            "fallback_count": result["fallback_count"],
        }, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
