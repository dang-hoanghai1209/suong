"""Controlled one-request Practical Life Steps Callirrhoe production A/B."""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from pathlib import Path
from typing import Any, Awaitable, Callable

from scripts.benchmark_gemini_tts import normalize_audio, probe_duration
from tella.composer.compose import compose_timing
from tella.music.service import configure_music
from tella.planner.models import TellaScenePlan
from tella.tts import gemini
from tella.tts.gemini_registry import REGISTRY_VERSION, resolve_style
from tella.voice_profiles import apply_voice_resolution_metadata, get_voice_profile, resolve_voice

PROFILE_ID = "gemini_callirrhoe_vi_natural_smile"
REUSED_IMAGE_INDICES = (1, 2, 3, 4, 5, 6, 7)


def build_production_plan(source_job: Path) -> tuple[TellaScenePlan, dict[str, Any]]:
    source_job = Path(source_job).resolve()
    plan = TellaScenePlan.model_validate_json(
        (source_job / "plan.json").read_text(encoding="utf-8")
    )
    if plan.recipe_id != "practical_life_steps_v1":
        raise RuntimeError("Callirrhoe production A/B requires practical_life_steps_v1")
    scenes = [scene for scene in plan.scenes if scene.kind == "scene"]
    if tuple(scene.scene_index for scene in scenes) != REUSED_IMAGE_INDICES:
        raise RuntimeError("source must contain exactly reusable scene indices 1 through 7")
    for scene in scenes:
        for relative in scene.image_filenames:
            if not (source_job / relative).is_file():
                raise RuntimeError(f"missing reusable image: {relative}")

    profile = get_voice_profile(PROFILE_ID)
    resolution = resolve_voice(
        explicit_profile_id=PROFILE_ID,
        recipe_profile_id=plan.voice_profile_id,
        narrative_mode=plan.narrative_mode,
    )
    apply_voice_resolution_metadata(plan, resolution)
    canonical_text = plan.global_narration_text.strip()
    if not canonical_text:
        canonical_text = " ".join(scene.voice_script.strip() for scene in scenes)
    style_instruction = resolve_style(profile.style)
    provider_input = gemini.serialize_provider_input(canonical_text, style_instruction)
    metadata = {
        "selected_voice_profile": PROFILE_ID,
        "provider": profile.provider,
        "model": profile.model,
        "voice": profile.voice,
        "style": profile.style,
        "language": profile.language,
        "voice_registry_version": REGISTRY_VERSION,
        "canonical_narration_text": canonical_text,
        "canonical_narration_hash": gemini.sha256_text(canonical_text),
        "serialized_provider_input_hash": gemini.sha256_text(provider_input),
        "post_tts_atempo_enabled": profile.post_tts_atempo_enabled,
        "automatic_edge_fallback_enabled": profile.automatic_edge_fallback_enabled,
        "automatic_model_fallback_enabled": profile.automatic_model_fallback_enabled,
        "timeline_reconciliation_method": "proportional_visual_scene_and_subtitle_allocation_to_natural_narration",
        "reused_image_indices": list(REUSED_IMAGE_INDICES),
        "image_provider_request_count": 0,
        "gemini_request_limit": 1,
        "gemini_request_count": 0,
        "fallback_used": False,
        "music_track_id": "practical_calm_01",
        "music_mix_profile": "practical_calm_rhythm",
    }
    return plan, metadata


def adapt_visual_timeline(plan: TellaScenePlan, natural_duration: float) -> None:
    if natural_duration <= 0:
        raise ValueError("natural narration duration must be positive")
    scenes = [scene for scene in plan.scenes if scene.kind == "scene"]
    weights = [max(1, len(scene.voice_script.strip())) for scene in scenes]
    total = sum(weights)
    allocated = 0.0
    for index, (scene, weight) in enumerate(zip(scenes, weights)):
        duration = (
            natural_duration - allocated
            if index == len(scenes) - 1
            else round(natural_duration * weight / total, 3)
        )
        scene.audio_duration = max(0.01, duration)
        allocated += scene.audio_duration
    plan.narration_duration = natural_duration
    plan.duration_fit_required = False
    plan.duration_fit_applied = False
    plan.duration_fit_tempo = 1.0
    plan.duration_fit_scale = 1.0
    plan.duration_fit_reason = "disabled for natural Gemini Callirrhoe narration"
    compose_timing(plan)


async def _default_render(plan: TellaScenePlan, output_dir: Path) -> Path:
    from tella.render.pipeline import render
    return await render(plan, output_dir)


async def run_production_ab(
    source_job: Path,
    output_dir: Path,
    *,
    max_gemini_requests: int,
    no_retry: bool,
    synthesize_fn: Callable[..., Awaitable[dict[str, Any]]] = gemini.synthesize,
    normalize_fn: Callable[[Path, Path], Awaitable[None]] = normalize_audio,
    duration_fn: Callable[[Path], Awaitable[float]] = probe_duration,
    render_fn: Callable[[TellaScenePlan, Path], Awaitable[Path]] = _default_render,
) -> Path:
    if max_gemini_requests != 1 or not no_retry:
        raise ValueError("Callirrhoe production A/B requires one request and --no-retry")
    output_dir = Path(output_dir).resolve()
    if output_dir.exists():
        raise RuntimeError(f"output directory already exists: {output_dir}")
    plan, metadata = build_production_plan(source_job)
    source_job = Path(source_job).resolve()
    output_dir.mkdir(parents=True)
    shutil.copytree(source_job / "assets", output_dir / "assets")
    if (source_job / "recipe.json").is_file():
        shutil.copy2(source_job / "recipe.json", output_dir / "recipe.json")
    raw = output_dir / "assets" / "narration_callirrhoe_raw.wav"
    normalized = output_dir / "assets" / "narration_callirrhoe_normalized.wav"
    metadata["gemini_request_count"] = 1
    (output_dir / "production_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    result = await synthesize_fn(
        metadata["canonical_narration_text"], raw,
        model=metadata["model"], voice=metadata["voice"], style=metadata["style"],
    )
    await normalize_fn(raw, normalized)
    raw_duration = await duration_fn(raw)
    normalized_duration = await duration_fn(normalized)
    if abs(raw_duration - normalized_duration) > 0.01:
        raise RuntimeError("local normalization changed natural narration duration")
    adapt_visual_timeline(plan, normalized_duration)
    plan.narration_audio_filename = "assets/narration_callirrhoe_normalized.wav"
    plan.narration_audio_path = str(normalized)
    plan.tts_provider = "gemini"
    plan.tts_voice = "Callirrhoe"
    plan.tts_language = "vi-VN"
    plan.tts_style = "natural_vocal_smile"
    plan.tts_fallback_used = False
    plan.ai_images_requested = 0
    plan.ai_images_generated = 0
    plan.ai_images_reused = 7
    configure_music(plan, output_dir, requested_track_id="practical_calm_01")
    metadata.update(result)
    metadata.update({
        "raw_tts_duration": raw_duration,
        "final_narration_duration": normalized_duration,
        "atempo_status": "disabled",
        "fallback_used": False,
    })
    plan.tts_metadata = metadata
    (output_dir / "plan.json").write_text(
        json.dumps(plan.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "tts_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "production_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return await render_fn(plan, output_dir)


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-job", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--voice-profile", choices=(PROFILE_ID,), required=True)
    parser.add_argument("--max-gemini-requests", type=int, required=True)
    parser.add_argument("--no-retry", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        _, metadata = build_production_plan(args.source_job)
        print(json.dumps(metadata, ensure_ascii=True, indent=2))
        return 0
    video = asyncio.run(run_production_ab(
        args.source_job, args.output_dir,
        max_gemini_requests=args.max_gemini_requests, no_retry=args.no_retry,
    ))
    print(video)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
