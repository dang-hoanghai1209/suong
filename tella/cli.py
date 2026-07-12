"""Tella CLI entry — full pipeline from topic to MP4 in one command.

Usage::

    python -m tella \
        --topic "the story of cinderella" \
        --lang en \
        --theme parable \
        --media ai_image \
        --duration short \
        --aspect 9:16 \
        --out ./out

Steps the CLI walks (each one logs progress):

  1. Translate the topic into ``target_lang`` (skip if source = target)
  2. Plan scene-by-scene with Gemini + apply character lock
  3. Fetch one media asset per scene (CF FLUX / Pexels Photo / Pexels Video)
  4. Synthesize Edge TTS narration for each scene
  5. Compose scene timing
  6. Render scene MP4s + concatenate → final video.mp4

The CLI is async-orchestrated so steps 3 + 4 run concurrently — typical
total wall time is dominated by the longest of {AI image gen, TTS} which
runs ~5-30 s for a short-mode 8-scene video.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv

    _REPO_ROOT = Path(__file__).resolve().parent.parent
    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

from tella._voice_pace import PRESETS, default_pace_for_theme, resolve_pace
from tella.composer.compose import compose_timing
from tella.ingest.topic_translator import SUPPORTED_LANGS, translate_topic
from tella.media.fetch import fetch_assets
from tella.planner.story_planner import plan_story, plan_story_from_script
from tella.planner.life_insight import (
    plan_life_insight_from_script,
    plan_life_insight_from_topic,
)
from tella.planner.practical_life_steps import (
    plan_practical_life_steps_from_script,
    plan_practical_life_steps_from_topic,
)
from tella.recipes import (
    RecipeDefinition,
    RecipeNotFoundError,
    apply_recipe_metadata,
    estimate_plan_duration,
    format_recipe_list,
    get_recipe,
    recipe_manifest,
    validate_recipe_run,
)
from tella.render.pipeline import render
from tella.tts.synth_all import synthesize_all
from tella.tts.duration_fit import (
    reconcile_practical_narration_duration,
    validate_actual_video_duration,
)
from tella.music.service import configure_music
from tella.voice_profiles import (
    VoiceProfileNotFoundError,
    VoiceResolution,
    apply_voice_resolution_metadata,
    format_voice_profile_list,
    resolve_voice,
    validate_voice_profiles,
)
from tella.production import (
    ProductionRun,
    ProductionStage,
    apply_sentence_alignment,
    dry_run_envelope,
    evaluate_resume,
    get_production_config,
)

logger = logging.getLogger("tella.cli")


def _write_recipe_manifest(
    job_dir: Path,
    recipe: RecipeDefinition,
    *,
    validation_status: str,
    validation_errors: list[str] | None = None,
    estimated_duration_seconds: float | None = None,
    voice_resolution: VoiceResolution | None = None,
) -> Path:
    out = job_dir / "recipe.json"
    out.write_text(
        json.dumps(
            recipe_manifest(
                recipe,
                validation_status=validation_status,
                validation_errors=validation_errors,
                estimated_duration_seconds=estimated_duration_seconds,
                voice_resolution=(
                    voice_resolution.model_dump() if voice_resolution else None
                ),
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return out


def _validate_recipe_plan(
    plan,
    recipe: RecipeDefinition,
    job_dir: Path,
    voice_resolution: VoiceResolution | None = None,
) -> list[str]:
    body_scenes = [scene for scene in plan.scenes if scene.kind == "scene"]
    estimated_duration = estimate_plan_duration(plan)
    narration_mode = "continuous" if plan.tts_continuous else "per_scene"
    errors = validate_recipe_run(
        recipe,
        scene_count=len(body_scenes),
        estimated_duration_seconds=estimated_duration,
        aspect_ratio=plan.aspect_ratio,
        narration_mode=narration_mode,
    )
    status = "passed" if not errors else "failed"
    apply_recipe_metadata(
        plan,
        recipe,
        validation_status=status,
        validation_errors=errors,
    )
    _write_recipe_manifest(
        job_dir,
        recipe,
        validation_status=status,
        validation_errors=errors,
        estimated_duration_seconds=estimated_duration,
        voice_resolution=voice_resolution,
    )
    return errors


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("invalid %s=%r; using %.2f", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("invalid %s=%r; using %d", name, raw, default)
        return default


def _parse_preview_scene_indices(raw: str) -> list[int]:
    values: list[int] = []
    for item in (raw or "").split(","):
        value = item.strip()
        if not value:
            continue
        try:
            index = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                "preview scene indices must be comma-separated integers"
            ) from exc
        if index <= 0:
            raise argparse.ArgumentTypeError("preview scene indices must be positive")
        if index not in values:
            values.append(index)
    if not values:
        raise argparse.ArgumentTypeError("at least one preview scene index is required")
    return values


def _edge_rate_to_speed(edge_rate: str) -> float:
    raw = (edge_rate or "0%").strip().rstrip("%")
    try:
        return round(1.0 + int(raw) / 100.0, 3)
    except ValueError:
        return 1.0


def _requested_tts_provider() -> str:
    return (os.environ.get("TELLA_TTS_PROVIDER") or "edge").strip().lower() or "edge"


def _tts_language_for_plan(plan) -> str:
    raw = (os.environ.get("TELLA_TTS_LANGUAGE") or "").strip().lower()
    return plan.language if raw in {"", "auto"} else raw


def _selected_reference_paths_from_metadata(job_dir: Path) -> list[str]:
    meta_path = job_dir / "references" / "references.json"
    if not meta_path.is_file():
        return []
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    refs = data if isinstance(data, list) else data.get("references", [])
    if not isinstance(refs, list):
        return []
    return [
        str(item.get("image_path", "")).replace("\\", "/")
        for item in refs
        if isinstance(item, dict) and item.get("selected") and item.get("image_path")
    ]


def _current_minimalist_visual_mode() -> str:
    raw = (os.environ.get("TELLA_MINIMALIST_VISUAL_MODE") or "").strip().lower()
    if raw in {"reference", "ai_scene", "curated_sprite", "rig"}:
        return raw
    return "ai_scene"


def _ensure_visual_metadata(plan, job_dir: Path) -> None:
    if plan.theme != "minimalist_emotional" or plan.media_source != "ai_image":
        return
    visual_mode = _current_minimalist_visual_mode()
    selected_refs = _selected_reference_paths_from_metadata(job_dir)
    provider_for_mode = {
        "reference": (os.environ.get("TELLA_IMAGE_PROVIDER") or "cloudflare").strip().lower() or "cloudflare",
        "ai_scene": "cloudflare",
        "curated_sprite": "local",
        "rig": "local",
    }
    source_for_mode = {
        "reference": "reference_guided_ai_image",
        "ai_scene": "ai_image_provider",
        "curated_sprite": "local_composer",
        "rig": "local_composer",
    }
    for scene in (s for s in plan.scenes if s.kind == "scene"):
        scene.visual_mode = scene.visual_mode or visual_mode
        scene.provider = scene.provider or provider_for_mode.get(scene.visual_mode, "")
        scene.image_provider = scene.image_provider or scene.provider
        scene.image_source = scene.image_source or source_for_mode.get(scene.visual_mode, "")
        scene.used_local_fallback = bool(scene.used_local_fallback)
        if not scene.asset_path and scene.image_filenames:
            scene.asset_path = scene.image_filenames[0]
        if scene.visual_mode == "reference":
            scene.used_reference_conditioning = bool(scene.used_reference_conditioning)
            if not scene.reference_paths:
                scene.reference_paths = selected_refs
        elif scene.visual_mode in {"curated_sprite", "rig"}:
            scene.used_reference_conditioning = False
            scene.reference_paths = []


def _ensure_tts_metadata(plan, job_dir: Path) -> None:
    audio_path = Path(plan.narration_audio_path) if plan.narration_audio_path else job_dir / "assets" / "narration.mp3"
    if not plan.narration_audio_path and audio_path.is_file():
        plan.narration_audio_path = str(audio_path)
        plan.narration_audio_filename = f"assets/{audio_path.name}"

    requested_provider = _requested_tts_provider()
    provider = plan.tts_provider or requested_provider
    language = plan.tts_language or _tts_language_for_plan(plan)
    codec = plan.tts_codec or (os.environ.get("TELLA_TTS_CODEC") or "mp3").strip().lower() or "mp3"
    sample_rate = plan.tts_sample_rate or _env_int("TELLA_TTS_SAMPLE_RATE", 24000)
    requested_speed = _env_float(
        "TELLA_TTS_SPEED",
        0.92 if requested_provider in {"cloudflare_grok", "xai"} and plan.theme == "minimalist_emotional" else _edge_rate_to_speed(plan.voice_edge_rate),
    )
    effective_speed = plan.tts_speed or (
        _edge_rate_to_speed(plan.voice_edge_rate) if provider == "edge" and not os.environ.get("TELLA_TTS_SPEED") else requested_speed
    )
    if not plan.tts_voice:
        env_voice = (os.environ.get("TELLA_TTS_VOICE") or "").strip()
        if provider == "edge":
            default_voice = plan.voice_name
        elif provider == "google":
            default_voice = (os.environ.get("GOOGLE_TTS_VOICE") or "").strip() or "vi-VN-Chirp3-HD-Achernar"
        else:
            default_voice = "ara"
        plan.tts_voice = env_voice or default_voice

    plan.tts_provider = provider
    plan.tts_language = language
    plan.tts_speed = effective_speed
    plan.tts_codec = codec
    plan.tts_sample_rate = sample_rate
    plan.tts_fallback_reason = plan.tts_fallback_reason or ""

    metadata = {
        **(plan.tts_metadata or {}),
        "requested_provider": (plan.tts_metadata or {}).get("requested_provider", requested_provider),
        "requested_tts_speed": (plan.tts_metadata or {}).get("requested_tts_speed", requested_speed),
        "tts_provider": plan.tts_provider,
        "tts_voice": plan.tts_voice,
        "tts_language": plan.tts_language,
        "tts_speed": plan.tts_speed,
        "tts_codec": plan.tts_codec,
        "tts_sample_rate": plan.tts_sample_rate,
        "narration_audio_path": plan.narration_audio_path,
        "narration_duration": plan.narration_duration,
        "fallback_used": plan.tts_fallback_used,
        "fallback_reason": plan.tts_fallback_reason,
        "tts_continuous": plan.tts_continuous,
        "tts_text_source": plan.tts_text_source,
        "tts_style": plan.tts_style,
        "raw_scene_text_chars": (plan.tts_metadata or {}).get("raw_scene_text_chars", 0),
        "global_narration_text_chars": len(plan.global_narration_text or ""),
        "silence_postprocess_applied": plan.silence_postprocess_applied,
        "max_pause_ms": plan.tts_max_pause_ms,
        "original_duration": plan.original_narration_duration,
        "processed_duration": plan.processed_narration_duration,
        "longest_silence_before": plan.longest_silence_before,
        "longest_silence_after": plan.longest_silence_after,
        "edge_rate": (plan.tts_metadata or {}).get("edge_rate", plan.voice_edge_rate),
    }
    plan.tts_metadata = metadata
    (job_dir / "tts_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _ensure_run_metadata(plan, job_dir: Path) -> None:
    _ensure_symbolic_runtime_defaults(plan)
    _ensure_tts_metadata(plan, job_dir)
    _ensure_visual_metadata(plan, job_dir)


def _build_symbolic_global_narration_text(plan) -> str:
    parts = []
    for scene in plan.scenes:
        if scene.kind != "scene":
            continue
        text = " ".join((scene.voice_script or "").split()).strip()
        if text:
            parts.append(text.rstrip(" .!?;:\u2026"))
    text = ", ".join(parts).strip(" ,")
    if text and text[-1] not in ".!?\u2026":
        text += "."
    return text


def _ensure_symbolic_runtime_defaults(plan) -> None:
    if plan.theme != "minimalist_symbolic_reel":
        return
    plan.subtitle_style = plan.subtitle_style or "reel_minimal"
    raw_continuous = (os.environ.get("TELLA_TTS_CONTINUOUS") or "").strip().lower()
    if raw_continuous in {"0", "false", "no", "off"}:
        plan.tts_continuous = False
        plan.tts_text_source = plan.tts_text_source or "scene_voice_script_join"
    else:
        plan.tts_continuous = True
        plan.tts_text_source = "global_narration_text"
        plan.global_narration_text = (
            plan.global_narration_text or _build_symbolic_global_narration_text(plan)
        )
    plan.tts_max_pause_ms = _env_int("TELLA_TTS_MAX_PAUSE_MS", 700)
    plan.tts_style = (
        plan.tts_style
        or (os.environ.get("TELLA_TTS_STYLE") or "emotional_storytelling").strip()
        or "emotional_storytelling"
    )
    plan.tts_metadata = {
        **(plan.tts_metadata or {}),
        "tts_continuous": plan.tts_continuous,
        "tts_text_source": plan.tts_text_source,
        "tts_style": plan.tts_style,
        "max_pause_ms": plan.tts_max_pause_ms,
        "global_narration_text_chars": len(plan.global_narration_text or ""),
    }


def _prompt_summary(text: str, max_len: int = 220) -> str:
    summary = " ".join((text or "").split())
    if len(summary) <= max_len:
        return summary
    return summary[: max(0, max_len - 3)].rstrip() + "..."


def _log_symbolic_plan_metadata(plan) -> None:
    if plan.theme != "minimalist_symbolic_reel":
        return
    logger.info(
        "symbolic_reel plan metadata: subtitle_style=%s tts_continuous=%s "
        "tts_text_source=%s max_pause_ms=%s scenes=%d diversity_seed=%s "
        "distinct_actions=%s distinct_objects=%s distinct_environments=%s "
        "distinct_compositions=%s preferred_actions=%s preferred_objects=%s "
        "preferred_environments=%s preferred_compositions=%s",
        plan.subtitle_style,
        plan.tts_continuous,
        plan.tts_text_source,
        plan.tts_max_pause_ms,
        len([s for s in plan.scenes if s.kind == "scene"]),
        plan.visual_diversity_seed,
        plan.distinct_action_count,
        plan.distinct_object_count,
        plan.distinct_environment_count,
        plan.distinct_composition_count,
        plan.preferred_action_range,
        plan.preferred_object_range,
        plan.preferred_environment_range,
        plan.preferred_composition_range,
    )
    for scene in (s for s in plan.scenes if s.kind == "scene"):
        logger.info(
            "symbolic_reel scene %02d meaning=%r visual=%r metaphor=%r "
            "object=%r highlights=%s prompt=%r",
            scene.scene_index,
            scene.scene_meaning,
            scene.symbolic_visual,
            scene.emotional_metaphor,
            scene.main_character_or_object,
            scene.subtitle_highlight_words,
            _prompt_summary(scene.image_prompt),
        )
        logger.info(
            "symbolic_reel diversity scene=%02d intent=%s character=%s count=%s "
            "action=%s object=%s secondary=%s environment=%s composition=%s "
            "framing=%s variant=%s seed=%s semantic_strength=%s "
            "semantic_score=%.1f diversity_score=%.1f cohesion_family=%s "
            "cohesion_score=%.1f final_score=%.1f semantic_priority_override=%s "
            "diversity_target_relaxed=%s diversity_repair=%s avoided=%s",
            scene.scene_index,
            scene.semantic_intent,
            scene.character_archetype,
            scene.character_count,
            scene.primary_action,
            scene.primary_object,
            scene.secondary_object,
            scene.environment,
            scene.composition_pattern,
            scene.framing,
            scene.visual_variant_id,
            scene.visual_seed,
            scene.semantic_strength,
            scene.semantic_strength_score,
            scene.diversity_score,
            scene.cohesion_family,
            scene.cohesion_score,
            scene.final_variant_score,
            scene.semantic_priority_override,
            scene.diversity_target_relaxed,
            scene.diversity_repair_applied,
            scene.repeated_attribute_avoided,
        )


def _log_life_insight_plan_metadata(plan) -> None:
    if plan.planner_id != "life_insight_symbolic":
        return
    logger.info(
        "life_insight duration fit: original=%.2fs target=%.2fs fitted=%.2fs "
        "passes=%d status=%s",
        plan.original_estimated_duration_seconds,
        plan.duration_target_seconds,
        plan.fitted_estimated_duration_seconds,
        plan.narration_fit_pass_count,
        plan.narration_fit_status,
    )
    logger.info(
        "life_insight table: scene | role | original_words | fitted_words | "
        "original_seconds | fitted_seconds | anchors_preserved"
    )
    for scene in (item for item in plan.scenes if item.kind == "scene"):
        logger.info(
            "life_insight row: %02d | %s | %d | %d | %.2f | %.2f | %s",
            scene.scene_index,
            scene.scene_role,
            scene.original_narration_word_count,
            scene.fitted_narration_word_count,
            scene.original_estimated_duration_seconds,
            scene.fitted_estimated_duration_seconds,
            bool(scene.semantic_anchors_preserved),
        )
    logger.info(
        "life_insight overlap: score=%.3f detected=%s repair=%s validation=%s",
        plan.recipe_overlap_score,
        plan.recipe_overlap_detected,
        plan.overlap_repair_applied,
        plan.life_insight_validation_status,
    )
    logger.info(
        "life_insight language: fallback_considered=%s fallback_applied=%s "
        "semantic_fidelity=%s naturalness=%s max_compression=%.3f "
        "surface=%s surface_repairs=%d surface_failures=%d "
        "incomplete_evidence=%d unsupported_inferences=%d",
        plan.seven_scene_fallback_considered,
        plan.seven_scene_fallback_applied,
        plan.semantic_fidelity_status,
        plan.vietnamese_naturalness_status,
        plan.maximum_scene_compression_ratio,
        plan.final_surface_validation_status,
        plan.final_surface_repairs_applied,
        plan.final_surface_failure_count,
        sum(not scene.evidence_condition_complete for scene in plan.scenes),
        sum(scene.unsupported_inference_detected for scene in plan.scenes),
    )
    for scene in (item for item in plan.scenes if item.kind == "scene"):
        logger.info(
            "life_insight visual scene=%02d role=%s variant=%s composition=%s "
            "object=%s palette=%s prompt=%s",
            scene.scene_index,
            scene.scene_role,
            scene.visual_variant_id,
            scene.composition_pattern,
            scene.main_character_or_object,
            scene.palette_id,
            json.dumps(scene.provider_prompt_variant, ensure_ascii=False),
        )


def _log_practical_life_steps_metadata(plan) -> None:
    if plan.planner_id != "practical_life_steps":
        return
    logger.info(
        "practical_steps summary scenes=%d original_words=%d fitted_words=%d "
        "original_seconds=%.2f fitted_seconds=%.2f fit=%s duration=%s "
        "safety=%s overlap=%s",
        len([scene for scene in plan.scenes if scene.kind == "scene"]),
        plan.original_total_word_count,
        plan.fitted_total_word_count,
        plan.original_estimated_duration_seconds,
        plan.fitted_estimated_duration_seconds,
        plan.narration_fit_status,
        plan.duration_validation_status,
        plan.safety_status,
        plan.overlap_validation_status,
    )
    logger.info(
        "practical_steps diagnostics pairwise=%s max_duplicate=%.3f distinct=%d "
        "action_density=%.3f emotional_overlap=%.3f insight_overlap=%.3f "
        "reflection=%.3f harsh_truth=%.3f abstract=%.3f",
        json.dumps(plan.pairwise_step_similarity, sort_keys=True),
        plan.maximum_duplicate_step_score,
        plan.distinct_step_count,
        plan.practical_action_density,
        plan.emotional_symbolic_overlap_score,
        plan.life_insight_symbolic_overlap_score,
        plan.reflective_statement_ratio,
        plan.harsh_truth_statement_ratio,
        plan.abstract_motivation_ratio,
    )
    for scene in (item for item in plan.scenes if item.kind == "scene"):
        logger.info(
            "practical_steps scene=%02d role=%s step=%d verb=%s subject=%s "
            "object=%s condition=%s specificity=%.2f duplicate=%.3f "
            "visual_action=%s rewritten=%s operations=%s",
            scene.scene_index,
            scene.scene_role,
            scene.step_number,
            scene.action_verb,
            scene.required_subject,
            scene.required_object,
            scene.action_condition,
            scene.practical_specificity_score,
            scene.duplicate_step_score,
            scene.visual_action,
            scene.narration_rewritten,
            ",".join(scene.rewrite_operations) or "none",
        )


def _slugify(text: str, max_len: int = 40) -> str:
    """Folder-safe slug, diacritic-stripped for readability.

    "Điều gì xảy ra nếu Mặt Trời tắt" → "dieu_gi_xay_ra_neu_mat_troi_tat"
    instead of the previous "i_u_g_x_y_ra_n_u_m_t_tr_i_t_t".

    Vietnamese 'đ'/'Đ' has no NFKD decomposition into base + combining mark
    so we handle it explicitly. Anything still non-ASCII after that
    (Chinese / Japanese / Korean glyphs) collapses to underscores — acceptable
    because those scripts have no obvious romanization to apply here.
    """
    import re
    import unicodedata

    raw = (text or "").strip().lower()
    # Special-case Vietnamese đ → d (NFKD doesn't split this one).
    raw = raw.replace("đ", "d").replace("Đ".lower(), "d")
    # Decompose accented chars; drop the combining-mark codepoints.
    decomposed = unicodedata.normalize("NFKD", raw)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_only).strip("_")
    return (slug or "tella")[:max_len]


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


async def run_pipeline(
    *,
    topic: str,
    target_lang: str,
    theme: str,
    media_source: str,
    duration_mode: str,
    aspect_ratio: str,
    voice_pace_name: str | None,
    voice_rate_custom: str | None,
    voice_gender: str | None,
    out_root: Path,
    job_id: str | None = None,
    google_tts_api_key: str = "",
    google_tts_voice: str = "",
    user_script: str | None = None,
    allow_local_image_fallback: bool = False,
    reuse_assets: bool = False,
    skip_image_generation: bool = False,
    images_from_job: str | None = None,
    reuse_assets_mode: str | None = None,
    allow_mismatched_reused_assets: bool = False,
    preview_scenes: int | None = None,
    preview_scene_indices: list[int] | None = None,
    max_ai_images: int | None = None,
    dry_run_plan: bool = False,
    tts_continuous: bool | None = None,
    tts_max_pause_ms: int | None = None,
    tts_style: str | None = None,
    music_track_id: str = "",
    music_profile_id: str = "",
    no_music: bool = False,
    recipe: RecipeDefinition | None = None,
    voice_resolution: VoiceResolution | None = None,
    resume: bool = False,
) -> Path:
    """Execute the full Tella pipeline. Returns the path to the final MP4.

    Two input modes (CEO 2026-06-17):
      * ``user_script=None`` — TOPIC MODE (default): translate ``topic`` →
        Gemini writes story → scenes.
      * ``user_script=<str>`` — PASTE-SCRIPT MODE: skip translation, ask
        Gemini to PARSE the user's narration into scenes preserving
        wording verbatim. ``topic`` is used only for the job slug + title
        fallback (pass a short label or empty).
    """

    use_script = bool((user_script or "").strip())
    life_insight_planner = bool(
        recipe is not None and recipe.planner_id == "life_insight_symbolic"
    )
    practical_steps_planner = bool(
        recipe is not None and recipe.planner_id == "practical_life_steps"
    )
    # ── 0. Setup output folder ─────────────────────────────────────────
    if not job_id:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        slug_seed = topic if topic else (user_script or "script")[:40]
        job_id = f"{ts}_{_slugify(slug_seed)}"
    job_dir = out_root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    production_config = get_production_config(recipe.recipe_id) if recipe else None
    resume_decision = (
        evaluate_resume(job_dir, production_config)
        if production_config and resume else None
    )
    if resume and production_config and not resume_decision["compatible"]:
        raise RuntimeError(
            "production resume is incompatible: "
            + "; ".join(resume_decision["reasons"])
        )
    if resume_decision and resume_decision["artifacts"].get("images", {}).get("valid"):
        reuse_assets = True
        images_from_job = str(job_dir)
    if resume_decision and resume_decision["artifacts"].get("raw_narration", {}).get("valid"):
        os.environ["TELLA_TTS_RESUME_RAW"] = str(job_dir / "assets" / "narration_raw.wav")
    production_run = (
        ProductionRun(job_dir, production_config, resume=resume)
        if production_config else None
    )
    if recipe is not None:
        _write_recipe_manifest(
            job_dir,
            recipe,
            validation_status="pending_plan_validation",
            voice_resolution=voice_resolution,
        )
        if production_run:
            production_run.advance(ProductionStage.recipe_resolved, {"recipe": job_dir / "recipe.json"})
    logger.info("job: %s (mode=%s)", job_dir, "script" if use_script else "topic")
    previous_env = {
        "TELLA_ALLOW_LOCAL_IMAGE_FALLBACK": os.environ.get("TELLA_ALLOW_LOCAL_IMAGE_FALLBACK"),
        "TELLA_REUSE_ASSETS": os.environ.get("TELLA_REUSE_ASSETS"),
        "TELLA_SKIP_IMAGE_GENERATION": os.environ.get("TELLA_SKIP_IMAGE_GENERATION"),
        "TELLA_IMAGES_FROM_JOB": os.environ.get("TELLA_IMAGES_FROM_JOB"),
        "TELLA_MAX_AI_IMAGES": os.environ.get("TELLA_MAX_AI_IMAGES"),
        "TELLA_REUSE_PLAN_PATH": os.environ.get("TELLA_REUSE_PLAN_PATH"),
        "TELLA_REUSE_ASSETS_MODE": os.environ.get("TELLA_REUSE_ASSETS_MODE"),
        "TELLA_ALLOW_MISMATCHED_REUSED_ASSETS": os.environ.get("TELLA_ALLOW_MISMATCHED_REUSED_ASSETS"),
        "TELLA_TTS_CONTINUOUS": os.environ.get("TELLA_TTS_CONTINUOUS"),
        "TELLA_TTS_MAX_PAUSE_MS": os.environ.get("TELLA_TTS_MAX_PAUSE_MS"),
        "TELLA_TTS_STYLE": os.environ.get("TELLA_TTS_STYLE"),
        "TELLA_TTS_RESUME_RAW": os.environ.get("TELLA_TTS_RESUME_RAW"),
        "TELLA_SYMBOLIC_JOB_ID": os.environ.get("TELLA_SYMBOLIC_JOB_ID"),
    }
    if theme == "minimalist_symbolic_reel":
        os.environ["TELLA_SYMBOLIC_JOB_ID"] = job_id
    if allow_local_image_fallback:
        os.environ["TELLA_ALLOW_LOCAL_IMAGE_FALLBACK"] = "1"
    if reuse_assets:
        os.environ["TELLA_REUSE_ASSETS"] = "1"
    if skip_image_generation:
        os.environ["TELLA_SKIP_IMAGE_GENERATION"] = "1"
    if images_from_job:
        os.environ["TELLA_IMAGES_FROM_JOB"] = images_from_job
    if reuse_assets_mode:
        os.environ["TELLA_REUSE_ASSETS_MODE"] = reuse_assets_mode
    if allow_mismatched_reused_assets:
        os.environ["TELLA_ALLOW_MISMATCHED_REUSED_ASSETS"] = "1"
        os.environ["TELLA_REUSE_ASSETS_MODE"] = "loose"
    if max_ai_images is not None:
        os.environ["TELLA_MAX_AI_IMAGES"] = str(max(0, int(max_ai_images)))
    if tts_continuous:
        os.environ["TELLA_TTS_CONTINUOUS"] = "1"
    if tts_max_pause_ms is not None:
        os.environ["TELLA_TTS_MAX_PAUSE_MS"] = str(max(80, int(tts_max_pause_ms)))
    if tts_style:
        os.environ["TELLA_TTS_STYLE"] = tts_style

    def _restore_fetch_env() -> None:
        for name, value in previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    # ── 1. Translate topic (skipped in script mode — user's text is canonical) ──
    if use_script:
        logger.info("step 1/6 — skip topic translation (paste-script mode)")
        topic_in_target = (topic or "").strip()
    elif life_insight_planner or practical_steps_planner:
        logger.info("step 1/6 - skip topic translation (local recipe planner)")
        topic_in_target = (topic or "").strip()
    else:
        logger.info("step 1/6 — translate topic")
        tr = await translate_topic(topic, target_lang)
        topic_in_target = tr.translated_topic
        logger.info(
            "  source=%s, target=%s, needs_translation=%s",
            tr.source_language_detected, tr.target_language, tr.needs_translation,
        )
        logger.info("  → %r", topic_in_target)

    # ── 2. Plan story (topic mode) OR parse script ────────────────────
    logger.info(
        "step 2/6 - %s (%s)",
        "parse user script" if use_script else "plan story",
        (
            "local recipe planner"
            if life_insight_planner or practical_steps_planner
            else "gemini"
        ),
    )
    pace = resolve_pace(
        theme=theme,
        override=voice_pace_name,
        custom_edge_rate=voice_rate_custom,
    )
    if life_insight_planner and use_script:
        plan = plan_life_insight_from_script(
            user_script=user_script.strip(),
            target_lang=target_lang,
            aspect_ratio=aspect_ratio,
            media_source=media_source,
            duration_mode=duration_mode,
            voice_pace=pace,
            voice_gender=voice_gender,
        )
    elif life_insight_planner:
        plan = plan_life_insight_from_topic(
            topic=topic_in_target,
            target_lang=target_lang,
            aspect_ratio=aspect_ratio,
            media_source=media_source,
            duration_mode=duration_mode,
            voice_pace=pace,
            voice_gender=voice_gender,
        )
    elif practical_steps_planner and use_script:
        plan = plan_practical_life_steps_from_script(
            user_script=user_script.strip(),
            target_lang=target_lang,
            aspect_ratio=aspect_ratio,
            media_source=media_source,
            duration_mode=duration_mode,
            voice_pace=pace,
            voice_gender=voice_gender,
        )
    elif practical_steps_planner:
        plan = plan_practical_life_steps_from_topic(
            topic=topic_in_target,
            target_lang=target_lang,
            aspect_ratio=aspect_ratio,
            media_source=media_source,
            duration_mode=duration_mode,
            voice_pace=pace,
            voice_gender=voice_gender,
        )
    elif use_script:
        plan = await plan_story_from_script(
            user_script=user_script.strip(),
            target_lang=target_lang,
            aspect_ratio=aspect_ratio,
            media_source=media_source,
            duration_mode=duration_mode,
            theme=theme,
            voice_pace=pace,
            voice_gender=voice_gender,
        )
    else:
        plan = await plan_story(
            topic=topic_in_target,
            target_lang=target_lang,
            aspect_ratio=aspect_ratio,
            media_source=media_source,
            duration_mode=duration_mode,
            theme=theme,
            voice_pace=pace,
            voice_gender=voice_gender,
        )
    # Channel branding — env contract shared with the Shortcraft worker:
    # CHANNEL_NAME / CHANNEL_HANDLE / DEMO_MODE. A blank name or DEMO_MODE=1
    # means no brand row (the standalone wizard sets these env vars too).
    _ch_name = (os.environ.get("CHANNEL_NAME") or "").strip()
    _ch_avatar = (os.environ.get("CHANNEL_AVATAR") or "").strip()
    _demo = os.environ.get("DEMO_MODE", "").strip() == "1" or not _ch_name
    plan.demo_mode = _demo
    plan.channel_name = "" if _demo else _ch_name
    plan.channel_avatar = "" if _demo else _ch_avatar

    plan_json = job_dir / "plan.json"
    if reuse_assets and not images_from_job and plan_json.is_file():
        reuse_plan = job_dir / ".reuse_plan.json"
        shutil.copyfile(plan_json, reuse_plan)
        os.environ["TELLA_REUSE_PLAN_PATH"] = str(reuse_plan)
    plan.local_fallback_allowed = bool(allow_local_image_fallback)
    if voice_resolution is not None:
        apply_voice_resolution_metadata(plan, voice_resolution)
    _ensure_symbolic_runtime_defaults(plan)
    recipe_errors: list[str] = []
    if recipe is not None:
        recipe_errors = _validate_recipe_plan(
            plan,
            recipe,
            job_dir,
            voice_resolution,
        )
    plan_json.write_text(
        json.dumps(plan.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if production_run:
        production_run.advance(ProductionStage.planned, {"plan": plan_json})
    logger.info("  %d scenes, voice=%s @ %s", len(plan.scenes), plan.voice_name, plan.voice_edge_rate)
    if recipe_errors:
        _restore_fetch_env()
        raise RuntimeError("recipe validation failed: " + "; ".join(recipe_errors))
    if dry_run_plan:
        _log_symbolic_plan_metadata(plan)
        _log_life_insight_plan_metadata(plan)
        _log_practical_life_steps_metadata(plan)
        logger.info("dry-run-plan active; wrote %s and skipped media/TTS/render", plan_json)
        _restore_fetch_env()
        return plan_json

    if preview_scene_indices:
        requested = list(preview_scene_indices)
        scenes_by_index = {
            scene.scene_index: scene
            for scene in plan.scenes
            if scene.kind == "scene"
        }
        missing = [index for index in requested if index not in scenes_by_index]
        if missing:
            _restore_fetch_env()
            raise RuntimeError(
                "preview scene indices are not present in the validated plan: "
                + ",".join(str(index) for index in missing)
            )
        original_count = len(plan.scenes)
        plan.scenes = [scenes_by_index[index] for index in requested]
        plan.global_narration_text = " ".join(
            scene.voice_script.strip()
            for scene in plan.scenes
            if scene.voice_script.strip()
        )
        plan_json.write_text(
            json.dumps(plan.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(
            "preview-scene-indices active after full plan validation: %d -> %d "
            "indices=%s roles=%s",
            original_count,
            len(plan.scenes),
            ",".join(str(index) for index in requested),
            ",".join(scene.scene_role for scene in plan.scenes),
        )
    elif preview_scenes is not None and preview_scenes > 0:
        original_count = len(plan.scenes)
        plan.scenes = plan.scenes[: max(1, int(preview_scenes))]
        for idx, scene in enumerate(plan.scenes, start=1):
            scene.scene_index = idx
        plan.global_narration_text = " ".join(
            scene.voice_script.strip()
            for scene in plan.scenes
            if scene.kind == "scene" and scene.voice_script.strip()
        )
        plan_json.write_text(
            json.dumps(plan.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(
            "preview-scenes active after full plan validation: %d -> %d scenes roles=%s",
            original_count,
            len(plan.scenes),
            ",".join(scene.scene_role for scene in plan.scenes),
        )

    # ── 3 + 4. Media + TTS in parallel ─────────────────────────────────
    logger.info("step 3/6 — fetch %d assets (%s)", len(plan.scenes), plan.media_source)
    logger.info("step 4/6 — synthesize TTS narration in parallel")
    try:
        if production_run:
            await fetch_assets(plan, job_dir)
            production_run.advance(ProductionStage.images_ready)
            production_run.counts["gemini"] = 1
            await synthesize_all(
                plan,
                job_dir,
                google_tts_api_key=google_tts_api_key,
                google_tts_voice=google_tts_voice,
            )
            production_run.counts["gemini"] = int(
                plan.tts_metadata.get("request_attempt_count", 1)
            )
            production_run.advance(ProductionStage.narration_ready, {
                "raw_narration": job_dir / "assets" / "narration_raw.wav",
                "normalized_narration": job_dir / "assets" / "narration.wav",
            })
        else:
            await asyncio.gather(
                fetch_assets(plan, job_dir),
                synthesize_all(
                    plan,
                    job_dir,
                    google_tts_api_key=google_tts_api_key,
                    google_tts_voice=google_tts_voice,
                ),
            )
        if voice_resolution is not None and not voice_resolution.post_tts_atempo_enabled:
            plan.duration_fit_required = False
            plan.duration_fit_applied = False
            plan.duration_fit_tempo = 1.0
            plan.duration_fit_scale = 1.0
            plan.duration_fit_reason = "disabled by selected natural-duration voice profile"
        else:
            await reconcile_practical_narration_duration(plan, job_dir)
        if production_run and production_config:
            apply_sentence_alignment(plan, job_dir, production_config)
            production_run.advance(ProductionStage.aligned, {
                "alignment": job_dir / "alignment_metadata.json",
            })
        configure_music(
            plan,
            job_dir,
            requested_track_id=music_track_id,
            requested_profile_id=music_profile_id,
            no_music=no_music,
        )
        if production_run and production_config:
            plan.music_metadata["mix_overrides"] = {
                "input_gain_db": production_config.music_gain_db,
                "ducking_threshold": production_config.ducking_threshold,
                "ducking_ratio": production_config.ducking_ratio,
                "ducking_attack_ms": production_config.ducking_attack_ms,
                "ducking_release_ms": production_config.ducking_release_ms,
                "fade_in_seconds": production_config.fade_in_seconds,
                "fade_out_seconds": production_config.fade_out_seconds,
                "start_offset_seconds": production_config.track_offset_seconds,
                "loop": production_config.music_loop,
            }
            (job_dir / "music_metadata.json").write_text(
                json.dumps(plan.music_metadata, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            production_run.advance(ProductionStage.music_ready, {
                "music_metadata": job_dir / "music_metadata.json",
            })
    except Exception as exc:
        _ensure_run_metadata(plan, job_dir)
        plan_json.write_text(
            json.dumps(plan.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _restore_fetch_env()
        if production_run:
            production_run.fail(production_run.stage.value, exc)
        raise

    # ── 5. Compose timing ──────────────────────────────────────────────
    logger.info("step 5/6 — compose timing")
    _ensure_run_metadata(plan, job_dir)
    compose_timing(plan)
    _ensure_run_metadata(plan, job_dir)

    # Re-write plan with timing populated for debugging.
    plan_json.write_text(
        json.dumps(plan.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # ── 6. Render MP4 ──────────────────────────────────────────────────
    logger.info("step 6/6 — render (ffmpeg)")
    try:
        final = await render(plan, job_dir)
        if production_run:
            production_run.advance(ProductionStage.rendered, {"final_video": final})
    except Exception as exc:
        if production_run:
            production_run.fail("rendered", exc)
        _restore_fetch_env()
        raise
    try:
        await validate_actual_video_duration(plan, final)
    except Exception:
        _ensure_run_metadata(plan, job_dir)
        plan_json.write_text(
            json.dumps(plan.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _restore_fetch_env()
        raise
    if production_run:
        video_qc_path = job_dir / "video_qc.json"
        video_qc_path.write_text(json.dumps({
            "status": "passed",
            "duration_seconds": plan.total_duration,
            "duration_policy": "natural_narration_duration",
            "atempo_applied": False,
            "duration_fit_applied": False,
            "video_path": str(final),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        production_run.advance(ProductionStage.qc_passed)
        production_run.record_artifact_hashes({
            "plan": plan_json,
            "raw_narration": job_dir / "assets" / "narration_raw.wav",
            "normalized_narration": job_dir / "assets" / "narration.wav",
            "alignment": job_dir / "alignment_metadata.json",
            "final_video": final,
            "video_qc": video_qc_path,
        }, image_artifacts=sorted((job_dir / "assets").glob("scene_*.jpg")),
           qc_results={"audio": "passed", "video": "passed"})
        production_run.advance(ProductionStage.completed)
    _ensure_run_metadata(plan, job_dir)
    plan_json.write_text(
        json.dumps(plan.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("DONE — %s (%.2fs total)", final, plan.total_duration)
    _restore_fetch_env()
    return final


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tella",
        description="Tella - creative storytelling video tool",
    )
    p.add_argument(
        "--recipe",
        default=None,
        dest="recipe_id",
        help="Select a registered versioned video recipe.",
    )
    p.add_argument(
        "--list-recipes",
        action="store_true",
        help="List registered recipes without making network calls.",
    )
    p.add_argument(
        "--list-voice-profiles",
        action="store_true",
        help="List configured voice profiles without making network calls.",
    )
    p.add_argument(
        "--validate-voice-profiles",
        action="store_true",
        help="Validate local voice profile and recipe mappings without synthesis.",
    )
    p.add_argument(
        "--dry-run-recipe",
        action="store_true",
        help="Resolve and validate --recipe locally, then write recipe.json only.",
    )
    p.add_argument(
        "--production-dry-run",
        action="store_true",
        help="Resolve a production recipe, cache/resume state, and request envelope locally; perform no providers or rendering.",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="Resume the same production job after validating recipe metadata and artifact hashes.",
    )
    p.add_argument(
        "--max-tts-requests",
        type=int,
        default=None,
        help="Maximum TTS provider submissions allowed for this run.",
    )
    p.add_argument(
        "--no-tts-retry",
        action="store_true",
        help="Disable application-level TTS retries (required by bounded production recipes).",
    )
    p.add_argument("--topic", default="", help="Story topic (any language)")
    p.add_argument(
        "--script-file",
        default=None,
        help="Path to a narration script file. The wording is preserved in voice_script.",
    )
    p.add_argument(
        "--exact-script",
        default=None,
        help="Inline narration script. The wording is preserved in voice_script.",
    )
    p.add_argument(
        "--lang", required=False, default=None, choices=list(SUPPORTED_LANGS),
        help="Target language (ISO-639-1)",
    )
    p.add_argument(
        "--theme", default="cinematic",
        choices=[
            "parable",
            "cinematic",
            "playful",
            "mindfulness",
            "minimalist_emotional",
            "minimalist_symbolic_reel",
            "life_insight_symbolic",
            "practical_life_steps",
        ],
    )
    p.add_argument(
        "--media", default="ai_image", dest="media_source",
        choices=["ai_image", "stock_photo", "stock_video"],
    )
    p.add_argument(
        "--duration", default="short", dest="duration_mode",
        choices=["short", "detailed"],
    )
    p.add_argument("--aspect", default="9:16", choices=["9:16", "16:9"])
    p.add_argument(
        "--pace", default=None, choices=list(PRESETS),
        dest="voice_pace_name",
        help="Voice pace preset (default = theme default)",
    )
    p.add_argument(
        "--voice-rate-custom", default=None, dest="voice_rate_custom",
        help='Custom Edge rate, e.g. "+3%%" or "-7%%" (overrides --pace)',
    )
    p.add_argument(
        "--gender", default=None, choices=["male", "female"],
        dest="voice_gender",
    )
    p.add_argument(
        "--tts-provider",
        default=None,
        choices=["edge", "google", "gemini", "cloudflare_grok", "xai"],
        dest="tts_provider",
        help="TTS provider override (also available as TELLA_TTS_PROVIDER)",
    )
    p.add_argument(
        "--voice",
        default=None,
        dest="tts_voice",
        help="TTS voice override, e.g. vi-VN-HoaiMyNeural (also TELLA_TTS_VOICE)",
    )
    p.add_argument("--tts-model", default=None, help="Explicit TTS model (also TELLA_TTS_MODEL)")
    p.add_argument(
        "--voice-profile",
        default=None,
        dest="voice_profile_id",
        help="Select a configured voice profile.",
    )
    p.add_argument(
        "--tts-continuous",
        action="store_true",
        help="Use a smoothed global narration paragraph for TTS synthesis.",
    )
    p.add_argument(
        "--tts-max-pause-ms",
        type=int,
        default=None,
        help="Maximum retained silence during TTS post-processing (theme default).",
    )
    p.add_argument(
        "--tts-style",
        default=None,
        choices=["natural", "vocal_smile", "natural_vocal_smile"],
        help="Narration flow style metadata for TTS processing (default emotional_storytelling).",
    )
    p.add_argument(
        "--music-track",
        default="",
        dest="music_track_id",
        help="Select a licensed local music track by stable track ID.",
    )
    p.add_argument(
        "--music-profile",
        default="",
        dest="music_profile_id",
        help="Override the recipe's local music profile.",
    )
    p.add_argument(
        "--no-music",
        action="store_true",
        help="Disable background music while retaining narration audio QC.",
    )
    p.add_argument(
        "--allow-local-image-fallback",
        action="store_true",
        help="Allow local placeholder image fallback when AI image generation fails.",
    )
    p.add_argument(
        "--reuse-assets",
        action="store_true",
        help="Reuse matching AI-generated assets from this job or --images-from-job.",
    )
    p.add_argument(
        "--skip-image-generation",
        action="store_true",
        help="Do not call the AI image provider; require reusable assets.",
    )
    p.add_argument(
        "--images-from-job",
        default=None,
        help="Reuse matching AI image assets from another job id or job path.",
    )
    p.add_argument(
        "--reuse-assets-mode",
        choices=["strict", "loose"],
        default=None,
        help="Asset reuse mode. strict requires prompt-hash match; loose reuses by scene index for debug only.",
    )
    p.add_argument(
        "--allow-mismatched-reused-assets",
        action="store_true",
        help="Debug only: reuse images by scene index even when prompt hashes differ.",
    )
    p.add_argument(
        "--preview-scenes",
        type=int,
        default=None,
        help="Render only the first N planned scenes for quota-safe previews.",
    )
    p.add_argument(
        "--preview-scene-indices",
        type=_parse_preview_scene_indices,
        default=None,
        help="Render selected validated scene indices, e.g. 3,4,5.",
    )
    p.add_argument(
        "--max-ai-images",
        type=int,
        default=None,
        help="Maximum number of AI image provider calls allowed for this run.",
    )
    p.add_argument(
        "--dry-run-plan",
        action="store_true",
        help="Write plan.json only; skip image generation, TTS, and render.",
    )
    p.add_argument(
        "--out", default=None, dest="out_root",
        help="Output root dir (default ./out or $TELLA_OUTPUT_DIR)",
    )
    p.add_argument("--job-id", default=None, help="Override job folder name")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    raw_argv = sys.argv[1:] if argv is None else argv

    # No flags at all → friendly interactive wizard (the RUN.bat experience).
    # Any flag present → classic argparse CLI (power users + automation).
    if not raw_argv:
        _setup_logging(verbose=False)
        if not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GEMINI_API_KEYS"):
            print(
                "ERROR: GEMINI_API_KEY missing. Copy .env.example to .env and fill it in.",
                file=sys.stderr,
            )
            return 1
        from tella.wizard import run_wizard

        try:
            choice = run_wizard()
        except KeyboardInterrupt:
            print("\nCancelled.", file=sys.stderr)
            return 130

        # Channel branding flows through the same env vars the Shortcraft
        # worker uses, so run_pipeline picks it up uniformly.
        if choice.channel_name:
            os.environ["CHANNEL_NAME"] = choice.channel_name
            os.environ["CHANNEL_AVATAR"] = choice.channel_avatar or ""
            os.environ["DEMO_MODE"] = "0"
        else:
            os.environ["DEMO_MODE"] = "1"

        out_root = Path(os.environ.get("TELLA_OUTPUT_DIR") or "./out")
        out_root.mkdir(parents=True, exist_ok=True)
        try:
            final = asyncio.run(
                run_pipeline(
                    topic=choice.topic,
                    target_lang=choice.target_lang,
                    theme=choice.theme,  # cinematic, or playful when cartoon style picked
                    media_source=choice.media_source,
                    duration_mode=choice.duration_mode,
                    aspect_ratio=choice.aspect_ratio,
                    voice_pace_name=choice.voice_pace_name,  # adapted to topic genre
                    voice_rate_custom=None,
                    voice_gender=choice.voice_gender,
                    out_root=out_root,
                    job_id=None,
                    user_script=choice.user_script,
                )
            )
        except KeyboardInterrupt:
            print("\nInterrupted.", file=sys.stderr)
            return 130
        except Exception as exc:
            logger.exception("pipeline failed: %s", exc)
            return 1

        # Auto-ideated topic + saved channel → record in history.jsonl AFTER
        # success so a failed render doesn't burn the topic.
        if choice.topic_embedding and choice.channel_slug:
            try:
                from tella.channels import list_channels
                from tella.ingest.seeder import append_history

                for c in list_channels():
                    if c.slug == choice.channel_slug and c.history_path:
                        append_history(
                            Path(c.history_path),
                            choice.topic,
                            choice.topic_embedding,
                        )
                        logger.info("history appended: %s", c.history_path)
                        break
            except Exception as exc:
                logger.warning("history append failed (non-fatal): %s", exc)

        print(f"\n[OK] Final video: {final}")
        return 0

    parser = build_arg_parser()
    args = parser.parse_args(raw_argv)
    _setup_logging(args.verbose)

    if args.list_recipes:
        print(format_recipe_list())
        return 0
    if args.list_voice_profiles:
        print(format_voice_profile_list())
        return 0
    if args.validate_voice_profiles:
        profile_errors = validate_voice_profiles()
        if profile_errors:
            print(
                "ERROR: voice profile validation failed: "
                + "; ".join(profile_errors),
                file=sys.stderr,
            )
            return 2
        print("Voice profiles valid: " + ", ".join(
            item.split(" |", 1)[0]
            for item in format_voice_profile_list().splitlines()
        ))
        return 0

    selected_recipe: RecipeDefinition | None = None
    if args.recipe_id:
        try:
            selected_recipe = get_recipe(args.recipe_id)
        except RecipeNotFoundError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
    production_config = (
        get_production_config(selected_recipe.recipe_id) if selected_recipe else None
    )
    if args.resume and (production_config is None or not args.job_id):
        parser.error("--resume requires a production recipe and explicit --job-id")
    if args.max_tts_requests is not None and args.max_tts_requests < 0:
        parser.error("--max-tts-requests must be non-negative")

    try:
        voice_resolution = resolve_voice(
            explicit_provider=args.tts_provider,
            explicit_voice=args.tts_voice,
            explicit_voice_rate=args.voice_rate_custom,
            explicit_profile_id=args.voice_profile_id,
            recipe_profile_id=(
                selected_recipe.voice_profile_id if selected_recipe else None
            ),
            narrative_mode=(
                selected_recipe.narrative_mode if selected_recipe else None
            ),
            legacy_provider=(
                os.environ.get("TELLA_TTS_PROVIDER") or "edge"
            ),
            legacy_voice=os.environ.get("TELLA_TTS_VOICE") or "",
        )
    except VoiceProfileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.production_dry_run:
        if selected_recipe is None or production_config is None:
            parser.error("--production-dry-run requires a registered production recipe")
        if args.no_music:
            parser.error("this production recipe requires its configured music")
        if args.max_tts_requests is not None and args.max_tts_requests != 1:
            parser.error("this production recipe requires --max-tts-requests 1")
        if args.tts_provider is not None and args.tts_provider != production_config.provider:
            parser.error("production recipe provider override is incompatible")
        if args.tts_voice is not None and args.tts_voice != production_config.voice:
            parser.error("production recipe voice override is incompatible")
        if args.voice_profile_id is not None and args.voice_profile_id != production_config.voice_profile:
            parser.error("production recipe voice-profile override is incompatible")
        if args.tts_model is not None and args.tts_model != production_config.model:
            parser.error("production recipe model override is incompatible")
        if args.tts_style is not None and args.tts_style != production_config.style:
            parser.error("production recipe style override is incompatible")
        out_root = Path(args.out_root or os.environ.get("TELLA_OUTPUT_DIR") or "./out")
        job_id = args.job_id or f"production_dry_{selected_recipe.recipe_id}"
        job_dir = out_root / job_id
        run = ProductionRun(job_dir, production_config, resume=args.resume)
        recipe_path = _write_recipe_manifest(
            job_dir, selected_recipe,
            validation_status="definition_validated",
            voice_resolution=voice_resolution,
        )
        envelope = dry_run_envelope(production_config, job_dir, resume=args.resume)
        envelope_path = job_dir / "request_envelope.json"
        envelope_path.write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        run.advance(ProductionStage.recipe_resolved, {
            "recipe": recipe_path, "request_envelope": envelope_path,
        })
        print(json.dumps(envelope, ensure_ascii=False, indent=2))
        print(f"\n[OK] Production dry-run: {job_dir}")
        return 0

    if args.dry_run_recipe:
        if selected_recipe is None:
            parser.error("--dry-run-recipe requires --recipe RECIPE_ID")
        out_root = Path(args.out_root or os.environ.get("TELLA_OUTPUT_DIR") or "./out")
        job_id = args.job_id or f"recipe_dry_{selected_recipe.recipe_id}"
        job_dir = out_root / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        recipe_path = _write_recipe_manifest(
            job_dir,
            selected_recipe,
            validation_status="definition_validated",
            voice_resolution=voice_resolution,
        )
        logger.info(
            "recipe resolved id=%s version=%s status=%s theme=%s planner=%s",
            selected_recipe.recipe_id,
            selected_recipe.recipe_version,
            selected_recipe.status,
            selected_recipe.visual_theme_id,
            selected_recipe.planner_id,
        )
        logger.info(
            "voice resolved profile=%s source=%s provider=%s voice=%s rate=%s "
            "overrides=%s compatibility=%s recipe_override=%s",
            voice_resolution.resolved_voice_profile_id or "(direct/legacy)",
            voice_resolution.voice_resolution_source,
            voice_resolution.resolved_tts_provider,
            voice_resolution.resolved_voice or "(planner/default)",
            voice_resolution.resolved_voice_rate or "(theme/default)",
            ",".join(voice_resolution.direct_override_fields) or "none",
            voice_resolution.voice_profile_compatibility_status,
            voice_resolution.recipe_voice_override_applied,
        )
        print(f"\n[OK] Recipe: {recipe_path}")
        return 0

    if args.lang is None and production_config is not None:
        args.lang = production_config.language
    if args.lang is None:
        parser.error("--lang is required unless --list-recipes or --dry-run-recipe is used")

    if selected_recipe is not None:
        theme_was_explicit = any(
            item == "--theme" or item.startswith("--theme=") for item in raw_argv
        )
        if theme_was_explicit and args.theme != selected_recipe.visual_theme_id:
            logger.warning(
                "recipe %s overrides requested theme %s with %s",
                selected_recipe.recipe_id,
                args.theme,
                selected_recipe.visual_theme_id,
            )
        args.theme = selected_recipe.visual_theme_id
        setting_errors = validate_recipe_run(
            selected_recipe,
            aspect_ratio=args.aspect,
            narration_mode=selected_recipe.narration_mode,
        )
        if setting_errors:
            print(
                "ERROR: recipe validation failed: " + "; ".join(setting_errors),
                file=sys.stderr,
            )
            return 2
        args.tts_continuous = selected_recipe.narration_mode == "continuous"
        if production_config is not None:
            if args.no_music:
                parser.error("this production recipe requires its configured music")
            if args.max_tts_requests is not None and args.max_tts_requests != 1:
                parser.error("this production recipe requires --max-tts-requests 1")
            if args.tts_provider is not None and args.tts_provider != production_config.provider:
                parser.error("production recipe provider override is incompatible")
            if args.tts_voice is not None and args.tts_voice != production_config.voice:
                parser.error("production recipe voice override is incompatible")
            if args.tts_model is not None and args.tts_model != production_config.model:
                parser.error("production recipe model override is incompatible")
            if args.tts_style is not None and args.tts_style != production_config.style:
                parser.error("production recipe style override is incompatible")
            if args.music_track_id and args.music_track_id != production_config.music_track:
                parser.error("production recipe music-track override is incompatible")
            if args.music_profile_id and args.music_profile_id != production_config.music_profile:
                parser.error("production recipe music-profile override is incompatible")
            args.max_ai_images = production_config.max_image_requests if args.max_ai_images is None else args.max_ai_images
            if args.max_ai_images > production_config.max_image_requests:
                parser.error("production recipe permits at most seven image requests")
            args.music_track_id = production_config.music_track
            args.music_profile_id = production_config.music_profile
            os.environ["TELLA_MAX_TTS_REQUESTS"] = str(production_config.max_tts_requests)
            os.environ["TELLA_NO_TTS_RETRY"] = "1"
            os.environ["TELLA_TTS_CACHE_ENABLED"] = "1"

    os.environ["TELLA_TTS_PROVIDER"] = voice_resolution.resolved_tts_provider
    if voice_resolution.resolved_tts_model:
        os.environ["TELLA_TTS_MODEL"] = voice_resolution.resolved_tts_model
    if voice_resolution.resolved_tts_style:
        os.environ["TELLA_TTS_STYLE"] = voice_resolution.resolved_tts_style
    if voice_resolution.resolved_tts_language:
        os.environ["TELLA_TTS_LANGUAGE"] = voice_resolution.resolved_tts_language
    if args.tts_model:
        os.environ["TELLA_TTS_MODEL"] = args.tts_model
    if voice_resolution.resolved_voice:
        os.environ["TELLA_TTS_VOICE"] = voice_resolution.resolved_voice
    logger.info(
        "voice resolved profile=%s source=%s provider=%s voice=%s rate=%s "
        "overrides=%s compatibility=%s recipe_override=%s",
        voice_resolution.resolved_voice_profile_id or "(none)",
        voice_resolution.voice_resolution_source,
        voice_resolution.resolved_tts_provider,
        voice_resolution.resolved_voice or "(planner/default)",
        voice_resolution.resolved_voice_rate or "(theme/default)",
        ",".join(voice_resolution.direct_override_fields) or "none",
        voice_resolution.voice_profile_compatibility_status,
        voice_resolution.recipe_voice_override_applied,
    )

    requires_gemini = not (
        selected_recipe is not None
        and selected_recipe.planner_id
        in {"life_insight_symbolic", "practical_life_steps"}
    )
    if (
        requires_gemini
        and not os.environ.get("GEMINI_API_KEY")
        and not os.environ.get("GEMINI_API_KEYS")
    ):
        print(
            "ERROR: GEMINI_API_KEY missing. Set it in .env (see .env.example).",
            file=sys.stderr,
        )
        return 1

    out_root = Path(args.out_root or os.environ.get("TELLA_OUTPUT_DIR") or "./out")
    out_root.mkdir(parents=True, exist_ok=True)
    user_script = ""
    if args.script_file and args.exact_script:
        parser.error("use either --script-file or --exact-script, not both")
    if args.script_file:
        script_path = Path(args.script_file)
        user_script = script_path.read_text(encoding="utf-8").strip()
    elif args.exact_script:
        user_script = args.exact_script.strip()
    if not args.topic.strip() and not user_script:
        parser.error("--topic is required unless --script-file or --exact-script is provided")
    if args.preview_scenes and args.preview_scene_indices:
        parser.error("use either --preview-scenes or --preview-scene-indices, not both")
    if args.no_music and (args.music_track_id or args.music_profile_id):
        parser.error("--no-music cannot be combined with --music-track or --music-profile")

    try:
        final = asyncio.run(
            run_pipeline(
                topic=args.topic or "exact script",
                target_lang=args.lang,
                theme=args.theme,
                media_source=args.media_source,
                duration_mode=args.duration_mode,
                aspect_ratio=args.aspect,
                voice_pace_name=args.voice_pace_name,
                voice_rate_custom=(
                    args.voice_rate_custom
                    or (
                        voice_resolution.resolved_voice_rate
                        if voice_resolution.resolved_voice_profile_id
                        else None
                    )
                ),
                voice_gender=args.voice_gender,
                out_root=out_root,
                job_id=args.job_id,
                user_script=user_script or None,
                allow_local_image_fallback=args.allow_local_image_fallback,
                reuse_assets=args.reuse_assets,
                skip_image_generation=args.skip_image_generation,
                images_from_job=args.images_from_job,
                reuse_assets_mode=args.reuse_assets_mode,
                allow_mismatched_reused_assets=args.allow_mismatched_reused_assets,
                preview_scenes=args.preview_scenes,
                preview_scene_indices=args.preview_scene_indices,
                max_ai_images=args.max_ai_images,
                dry_run_plan=args.dry_run_plan,
                tts_continuous=args.tts_continuous,
                tts_max_pause_ms=args.tts_max_pause_ms,
                tts_style=args.tts_style,
                music_track_id=args.music_track_id,
                music_profile_id=args.music_profile_id,
                no_music=args.no_music,
                recipe=selected_recipe,
                voice_resolution=voice_resolution,
                resume=args.resume,
            )
        )
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        logger.exception("pipeline failed: %s", exc)
        return 1

    # ASCII-only print so Windows cmd cp1252 doesn't choke.
    if args.dry_run_plan:
        print(f"\n[OK] Plan: {final}")
    else:
        print(f"\n[OK] Final video: {final}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
