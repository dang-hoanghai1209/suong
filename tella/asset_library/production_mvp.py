"""Deterministic seven-scene Asset-library V2 production acceptance job."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from tella.planner.models import Scene, TellaScenePlan

BASE_SEED = 12345
OUTPUT_DIR = Path(r"D:\tella-production-resolver\out\asset_library_v2_7_scene_acceptance")
NARRATION_TEXT = (
    "Đã có những đêm, mình chỉ ngồi yên và chờ một tin nhắn. "
    "Cứ nghĩ rằng, chỉ cần đợi thêm một chút thôi... thì người ấy sẽ nhớ đến mình "
    "như cách mình vẫn nhớ. Nhưng rồi, sự im lặng cứ dài ra, lâu hơn cả hy vọng. "
    "Mình đã buồn, đã tự hỏi liệu mình còn thiếu điều gì. Cho đến khi hiểu rằng, "
    "có những người không ở lại... không phải vì mình chưa đủ tốt, mà vì họ vốn "
    "không thuộc về hành trình của mình."
)

SCENE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "action": "sit_hug_knees", "emotion": "sad", "direction": "front",
        "location": "bedroom", "time_of_day": "night", "objects": ["pillow", "phone_dark"],
        "composition_preset": "bedroom_floor_sitting", "duration": 4.5,
        "narration_segment": "Đã có những đêm, mình chỉ ngồi yên và chờ một tin nhắn.",
    },
    {
        "action": "sit_floor_sad", "emotion": "worried", "direction": "front",
        "location": "room_by_window", "time_of_day": "day", "objects": ["phone_dark"],
        "composition_preset": "window_waiting", "duration": 4.5,
        "narration_segment": "Cứ nghĩ rằng, chỉ cần đợi thêm một chút thôi...",
    },
    {
        "action": "sit_hug_knees", "emotion": "sad", "direction": "front",
        "location": "cafe_corner", "time_of_day": "day", "objects": ["empty_cup", "phone_dark"],
        "composition_preset": "cafe_sitting", "duration": 4.5,
        "narration_segment": "thì người ấy sẽ nhớ đến mình như cách mình vẫn nhớ.",
    },
    {
        "action": "head_down_stand", "emotion": "sad", "direction": "front",
        "location": "bus_stop_rain", "time_of_day": "day", "objects": [],
        "composition_preset": "bus_stop_waiting", "duration": 4.5,
        "narration_segment": "Nhưng rồi, sự im lặng cứ dài ra, lâu hơn cả hy vọng.",
    },
    {
        "action": "sit_floor_relaxed", "emotion": "reflective", "direction": "front",
        "location": "park_bench", "time_of_day": "day", "objects": ["paper_letter", "flower_single"],
        "composition_preset": "floor_reflection", "duration": 5.0,
        "narration_segment": "Mình đã buồn, đã tự hỏi liệu mình còn thiếu điều gì.",
    },
    {
        "action": "wipe_tear", "emotion": "sad", "direction": "front",
        "location": "bedroom", "time_of_day": "day", "objects": ["tissue_box", "phone_dark"],
        "composition_preset": "tear_wiping", "duration": 5.0,
        "narration_segment": "Cho đến khi hiểu rằng, có những người không ở lại...",
    },
    {
        "action": "hand_on_chest", "emotion": "accepting", "direction": "front",
        "location": "room_by_window", "time_of_day": "day", "objects": ["flower_single"],
        "composition_preset": "park_acceptance", "duration": 5.0,
        "narration_segment": "không phải vì mình chưa đủ tốt, mà vì họ vốn không thuộc về hành trình của mình.",
    },
)


def scene_seed(base_seed: int, scene_number: int) -> int:
    return base_seed if scene_number == 1 else base_seed + (scene_number - 1) * 1009


def build_seven_scene_plan(*, base_seed: int = BASE_SEED, enabled: bool | None = None) -> TellaScenePlan:
    if enabled is None:
        enabled = (os.environ.get("TELLA_ASSET_LIBRARY_V2") or "").strip().lower() in {"1", "true", "yes", "on"}
    scenes: list[Scene] = []
    for scene_number, definition in enumerate(SCENE_DEFINITIONS, start=1):
        seed = scene_seed(base_seed, scene_number)
        scene = Scene(
            kind="scene",
            scene_index=scene_number,
            title="",
            voice_script=definition["narration_segment"],
            scene_setting=definition["location"],
            scene_action=definition["action"],
            emotion_tag=definition["emotion"],
            character_id="female_01",
            visual_seed=seed,
        )
        if enabled:
            scene.asset_library_request = {
                "character_id": "female_01",
                **definition,
                "seed": seed,
                "base_seed": base_seed,
            }
        scenes.append(scene)
    return TellaScenePlan(
        title="Asset-library V2 seven-scene acceptance",
        language="vi",
        aspect_ratio="9:16",
        media_source="ai_image",
        theme="minimalist_emotional",
        narrative_mode="emotional_reflection",
        voice_gender="female",
        voice_name="vi-VN-HoaiMyNeural",
        voice_edge_rate="-30%",
        subtitle_style="reel_minimal",
        demo_mode=True,
        music_enabled=False,
        global_narration_text=NARRATION_TEXT,
        scenes=scenes,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _probe_video(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "stream=width,height,codec_name,codec_type,r_frame_rate",
            "-show_entries", "format=duration", "-of", "json", str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    video = next(item for item in streams if item.get("codec_type") == "video")
    audio = next(item for item in streams if item.get("codec_type") == "audio")
    return {
        "width": int(video["width"]),
        "height": int(video["height"]),
        "duration": float(payload["format"]["duration"]),
        "video_codec": video.get("codec_name", ""),
        "audio_codec": audio.get("codec_name", ""),
        "frame_rate": video.get("r_frame_rate", ""),
    }


def build_job_metadata(plan: TellaScenePlan, job_dir: Path, final_video: Path, *, base_seed: int) -> dict[str, Any]:
    scene_metadata = json.loads((job_dir / "asset_library_scene_metadata.json").read_text(encoding="utf-8"))
    body_scenes = [scene for scene in plan.scenes if scene.kind == "scene"]
    if len(body_scenes) != 7 or len(scene_metadata) != 7:
        raise RuntimeError("asset-library acceptance requires exactly seven scene records")
    records: list[dict[str, Any]] = []
    for scene, rendered in zip(body_scenes, scene_metadata):
        image_path = (job_dir / scene.image_filenames[0]).resolve()
        records.append({
            "scene_number": scene.scene_index,
            "scene_seed": rendered["seed"],
            "duration": scene.duration,
            "narration_timing": {"start": scene.start, "duration": scene.duration, "end": scene.start + scene.duration},
            "narration_segment": scene.voice_script,
            "requested_semantics": rendered["character_request"],
            "selected_semantic_id": rendered["character"]["selected_semantic_id"],
            "selected_source_asset_id": rendered["character"]["selected_asset_id"],
            "selected_tier": rendered["character"]["selected_tier"],
            "selection_score": rendered["character"]["selection_score"],
            "selection_reasons": rendered["character"]["selection_reasons"],
            "score_breakdown": rendered["character"]["score_breakdown"],
            "canonical": rendered["character"]["canonical"],
            "enabled": rendered["character"]["enabled"],
            "production_eligible": rendered["character"]["production_eligible"],
            "quality_status": rendered["character"]["quality_status"],
            "fallback_reason": rendered["character"]["fallback_reason"],
            "background": rendered["background"],
            "objects": rendered["objects"],
            "object_warnings": rendered.get("object_warnings", []),
            "character_placement": rendered["character"]["placement"],
            "layer_order": rendered["layer_order"],
            "layers": rendered.get("layers", []),
            "output_scene_image": str(image_path),
            "scene_image_sha256": _sha256(image_path),
        })
    metadata = {
        "schema_version": 1,
        "feature_mode": "asset_library_v2",
        "base_seed": base_seed,
        "narration_text": NARRATION_TEXT,
        "narration_duration": plan.narration_duration,
        "tts_provider": plan.tts_provider,
        "external_ai_image_provider_calls": 0,
        "final_video_path": str(final_video.resolve()),
        "final_video_properties": _probe_video(final_video),
        "scene_count": 7,
        "scenes": records,
    }
    (job_dir / "asset_library_video_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return metadata


def validate_acceptance_job(plan: TellaScenePlan, job_dir: Path) -> None:
    body_scenes = [scene for scene in plan.scenes if scene.kind == "scene"]
    if len(body_scenes) != 7:
        raise RuntimeError(f"expected seven body scenes, found {len(body_scenes)}")
    hashes: list[str] = []
    for scene in body_scenes:
        path = job_dir / scene.image_filenames[0]
        if not path.is_file():
            raise RuntimeError(f"missing scene image: {path}")
        from PIL import Image
        with Image.open(path) as image:
            if image.size != (1080, 1920):
                raise RuntimeError(f"scene {scene.scene_index} has size {image.size}")
        hashes.append(_sha256(path))
        for obj in (scene.asset_library_result.get("object_dimensions") or []):
            if obj["width"] <= 0 or obj["height"] <= 0:
                raise RuntimeError(f"scene {scene.scene_index} has zero-sized object {obj}")
    if any(left == right for left, right in zip(hashes, hashes[1:])):
        raise RuntimeError("consecutive scene images unexpectedly share a hash")


async def render_acceptance_job(output_dir: Path = OUTPUT_DIR, *, base_seed: int = BASE_SEED) -> tuple[Path, dict[str, Any]]:
    from tella.media.fetch import fetch_assets
    from tella.render.pipeline import render
    from tella.tts.synth_all import synthesize_all

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    previous = os.environ.get("TELLA_ASSET_LIBRARY_V2")
    os.environ["TELLA_ASSET_LIBRARY_V2"] = "1"
    try:
        plan = build_seven_scene_plan(base_seed=base_seed, enabled=True)
        await fetch_assets(plan, output_dir)
        await synthesize_all(plan, output_dir)
        final_video = await render(plan, output_dir)
    finally:
        if previous is None:
            os.environ.pop("TELLA_ASSET_LIBRARY_V2", None)
        else:
            os.environ["TELLA_ASSET_LIBRARY_V2"] = previous
    validate_acceptance_job(plan, output_dir)
    metadata = build_job_metadata(plan, output_dir, final_video, base_seed=base_seed)
    return final_video, metadata


__all__ = [
    "BASE_SEED", "NARRATION_TEXT", "OUTPUT_DIR", "SCENE_DEFINITIONS",
    "build_job_metadata", "build_seven_scene_plan", "render_acceptance_job",
    "scene_seed", "validate_acceptance_job",
]
