"""Top-level media dispatcher — fetch every scene's asset for a plan.

Given a :class:`TellaScenePlan`, ``fetch_assets`` walks every body scene
and writes one asset file per scene into ``<job_dir>/assets/`` based on
the plan's ``media_source``:

  - ``ai_image``    → CF Workers AI FLUX → JPG
  - ``stock_photo`` → Pexels Photo       → JPG
  - ``stock_video`` → Pexels Video       → MP4

For v1 MVP each scene gets exactly 1 asset. Multi-asset per scene
(``Scene.asset_count`` > 1) is deferred to a later iteration — the field
is preserved on the plan for downstream consumers but the media layer
ignores it for now (see DECISIONS.md D-007).

Scenes are fetched concurrently up to ``MAX_CONCURRENT`` to keep render
turnaround tight. Failures bubble per scene — the dispatcher does NOT
swap providers (e.g. stock photo when stock video fails) because cross-
provider fallback would silently change what the user asked for.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from pathlib import Path

from tella.media import ai_image, sprite_composer, stock_photo, stock_video
from tella.media.image_provider import get_image_provider
from tella.media.reference_pipeline import (
    generate_character_references,
    selected_reference_paths,
)
from tella.media.visual_qc import (
    apply_qc_result_to_scene,
    evaluate_scene_image,
    image_hash,
    infer_scene_anatomy_expectations,
    max_attempts,
    rank_qc_attempt,
    save_qc_result,
    strict_visual_qc,
    summarize_qc_attempts,
)
from tella.planner.models import SceneQCResult, TellaScenePlan
from tella.planner.visual_bible import build_visual_bible, save_visual_bible
from tella.planner.visual_prompts import build_scene_visual_plan, repair_prompt

logger = logging.getLogger("tella.media.fetch")

# Keep concurrency modest: bursting many simultaneous requests at one CF
# account triggers rate-limit 429s. 3 in flight + the global throttle in
# ai_image keeps us under the limit while still rendering quickly.
MAX_CONCURRENT = 3

# One stable seed per video keeps the AI-generated character looking the
# same across scenes (FLUX has no cross-call memory; a fixed seed + the
# locked identity text is the best text-only consistency lever we have).
_VIDEO_SEED = 73501

# Generation dims fed to the AI image provider. Smaller than the 1080×1920 /
# 1920×1080 final canvas on purpose — the renderer upscales/crops, and a
# smaller image costs fewer CF Neurons so a free account lasts far longer.
_GEN_DIMS: dict[str, tuple[int, int]] = {
    "9:16": (768, 1344),
    "16:9": (1344, 768),
}

_NSFW_MARKERS = (
    "3030",
    "input prompt contains nsfw content",
    "nsfw",
)

_MINIMALIST_SAFE_PROMPT = (
    "same small girl character, one head and one face, short straight black "
    "bob ending at chin, small round face, dot eyes, tiny nose, tiny neutral "
    "mouth, mustard yellow triangular dress, soft rust sleeves, simple "
    "doodle proportions, complete character visible"
)

_MINIMALIST_STYLE_LOCK = (
    "minimalist hand-drawn emotional doodle illustration, warm muted taupe "
    "bedroom, thin imperfect black linework, flat muted color, soft "
    "environmental room details, no realistic shading, no 3D, no anime, no "
    "photorealism, no text, no watermark"
)

_MINIMALIST_COMPOSITION_LOCK = (
    "vertical 9:16 medium-wide room composition, character placed within central "
    "safe area, character occupies about 35-45 percent of frame height, generous "
    "negative space, complete character visible, head and feet visible, bottom "
    "25 percent calm for captions, no cropped head or feet, no extreme close-up, "
    "not a character portrait, layered scene: foreground curtain edge or soft "
    "shadow, middle ground young woman, background bed, window with thin "
    "curtains, bedside table, warm lamp, books or folded blanket, soft wall "
    "shadows, subtle dust near window, muted floor and wall shapes"
)

_MINIMALIST_ONE_CHARACTER_LOCK = (
    "exactly one head, no second head, no duplicate face, no second person, "
    "no child doll, no baby, no face on objects, no face on heart, no tiny "
    "character inside the character, no nested person, symbolic objects must "
    "be plain and faceless, no deformed anatomy, no extra limbs"
)

_MINIMALIST_POSES: dict[str, str] = {
    "front_standing": (
        "small girl standing front-facing, arms relaxed by sides, simple "
        "mitten hands, beside a tiny flat paper heart symbol with no face"
    ),
    "side_sitting": (
        "small girl sitting in side view, hands resting on knees, calm posture"
    ),
    "side_walking": (
        "small girl walking slowly in side profile, arms relaxed, beside a "
        "thin line path"
    ),
    "looking_at_light": (
        "small girl looking at a small glowing light floating nearby"
    ),
    "holding_paper_heart": (
        "small girl holding a tiny flat paper heart symbol with no face in "
        "simple mitten-like hands in front of the dress, paper heart has no "
        "eyes, no mouth, no face, no contact with torso"
    ),
    "beside_lamp": "small girl sitting beside a small warm lamp",
    "beside_flower": "small girl standing beside a small flower",
    "under_scribble_cloud": (
        "small girl standing under a soft grey scribble cloud"
    ),
}

_MINIMALIST_MOTIFS = (
    "lamp",
    "paper_heart",
    "scribble_cloud",
    "small_flower",
    "glowing_light",
    "empty_chair",
    "thin_path",
    "sunrise_circle",
    "tiny_bird",
    "small_window",
    "little_star",
    "seedling",
)

_MOTIF_TO_POSE = {
    "lamp": "beside_lamp",
    "paper_heart": "holding_paper_heart",
    "scribble_cloud": "under_scribble_cloud",
    "small_flower": "beside_flower",
    "glowing_light": "looking_at_light",
    "empty_chair": "side_sitting",
    "thin_path": "side_walking",
    "sunrise_circle": "looking_at_light",
    "tiny_bird": "front_standing",
    "small_window": "side_sitting",
    "little_star": "looking_at_light",
    "seedling": "beside_flower",
}

_MOTIF_DESCRIPTIONS = {
    "lamp": "one warm table lamp on a small bedside table",
    "paper_heart": (
        "one tiny flat mustard paper heart symbol with no face, no eyes, no "
        "mouth"
    ),
    "scribble_cloud": "one soft grey scribble cloud above the character",
    "small_flower": "one small flower growing from the ground",
    "glowing_light": "one small glowing light floating nearby",
    "empty_chair": "one small folded blanket on the bed beside the character",
    "thin_path": "one thin line path under the character",
    "sunrise_circle": "one small muted sunrise circle near the horizon",
    "tiny_bird": "one tiny simple bird shape in the empty space",
    "small_window": "one simple window with thin curtains and soft dust particles",
    "little_star": "one little muted star above the character",
    "seedling": "one tiny seedling near the character's feet",
}

_COMPOSITIONS = (
    "medium-wide bedroom scene, character centered in middle safe area, bed on left, window and curtains behind, bottom 25 percent calm",
    "medium-wide bedroom scene, character slightly left of center, warm table lamp and books on right, soft wall shadows behind",
    "medium-wide bedroom scene, character centered above caption lane, window with thin curtains behind, dust particles in light",
    "medium-wide bedroom scene, character slightly right of center, bed and folded blanket on left, head and feet fully visible",
    "medium-wide bedroom scene, character centered, bedside table and lamp near side, generous negative space around her",
    "medium-wide bedroom scene, character in middle ground, foreground curtain edge or soft shadow, muted floor and wall shapes",
    "medium-wide bedroom scene, character side profile in central safe area, thin floor path shape, bed and window in background",
    "medium-wide bedroom scene, character small in middle third, background lamp glow and soft room shadows, quiet open space",
)

_MINIMALIST_FORBIDDEN_HINTS = (
    "hugging herself",
    "embracing herself",
    "holding herself",
    "touching herself",
    "touching her body",
    "touching her chest",
    "hands on body",
    "arms wrapped around herself",
    "wounded body",
    "broken body",
    "physical pain on body",
    "close-up face",
    "looking directly into camera",
    "back view with visible face",
    "twisted torso",
    "detailed hands",
    "detailed fingers",
    "realistic face",
    "anime face",
    "long flowing hair",
    "asymmetrical hair",
    "self-hug",
    "touching body",
)

_MOTIF_KEYWORDS = {
    "lamp": ("lamp", "room", "bed", "tired", "rest", "evening", "quiet"),
    "paper_heart": ("heart", "love", "accept", "heal", "gentle", "kind"),
    "scribble_cloud": ("sad", "heavy", "cloud", "stress", "pain", "hurt", "worry"),
    "small_flower": ("flower", "plant", "grow", "soft", "care"),
    "glowing_light": ("light", "glow", "hope", "warm", "peace"),
    "empty_chair": ("alone", "empty", "chair", "tired", "sit"),
    "thin_path": ("walk", "path", "step", "journey", "again", "start"),
    "sunrise_circle": ("morning", "sunrise", "new", "begin", "tomorrow"),
    "tiny_bird": ("free", "release", "breath", "quiet"),
    "small_window": ("window", "rain", "night", "look", "outside"),
    "little_star": ("wish", "remember", "small", "dream"),
    "seedling": ("seed", "grow", "begin", "return", "life"),
}


def _safe_stem(text: str, max_len: int = 30) -> str:
    """Filesystem-safe slug for asset filenames."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", (text or "scene")).strip("_").lower()
    return (slug or "scene")[:max_len]


def _is_nsfw_prompt_rejection(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in _NSFW_MARKERS)


def _stock_fallback_disabled() -> bool:
    return (os.environ.get("TELLA_DISABLE_STOCK_FALLBACK") or "").strip() == "1"


def _minimalist_use_ai_scenes() -> bool:
    return (os.environ.get("TELLA_MINIMALIST_USE_AI_SCENES") or "").strip() == "1"


def _minimalist_visual_mode() -> str:
    raw = (os.environ.get("TELLA_MINIMALIST_VISUAL_MODE") or "").strip().lower()
    if raw:
        if raw not in {"reference", "ai_scene", "curated_sprite", "rig"}:
            logger.warning("invalid TELLA_MINIMALIST_VISUAL_MODE=%r; using curated_sprite", raw)
            return "curated_sprite"
        return raw
    if _minimalist_use_ai_scenes():
        return "ai_scene"
    return "curated_sprite"


def _require_reference_conditioning() -> bool:
    return (os.environ.get("TELLA_REQUIRE_REFERENCE_CONDITIONING") or "").strip() == "1"


def _use_previous_scene_reference() -> bool:
    return (os.environ.get("TELLA_USE_PREVIOUS_SCENE_REFERENCE") or "").strip() == "1"


def _choose_motif(text: str, scene_index: int, used: set[str], previous: str) -> str:
    text_l = (text or "").lower()
    scored: list[tuple[int, int, str]] = []
    for i, motif in enumerate(_MINIMALIST_MOTIFS):
        score = sum(1 for word in _MOTIF_KEYWORDS.get(motif, ()) if word in text_l)
        if motif == previous:
            score -= 4
        if motif in used:
            score -= 1
        scored.append((score, -i, motif))
    scored.sort(reverse=True)
    chosen = scored[0][2]
    if scored[0][0] <= 0:
        start = (scene_index - 1) % len(_MINIMALIST_MOTIFS)
        for offset in range(len(_MINIMALIST_MOTIFS)):
            candidate = _MINIMALIST_MOTIFS[(start + offset) % len(_MINIMALIST_MOTIFS)]
            if candidate != previous and candidate not in used:
                return candidate
        chosen = _MINIMALIST_MOTIFS[start]
        if chosen == previous:
            chosen = _MINIMALIST_MOTIFS[(start + 1) % len(_MINIMALIST_MOTIFS)]
    return chosen


def _assign_minimalist_visual_plans(scenes) -> None:
    used: set[str] = set()
    previous_motif = ""
    for idx, scene in enumerate(scenes, start=1):
        text = " ".join(
            str(part or "")
            for part in (scene.title, scene.voice_script)
        )
        motif = _choose_motif(text, idx, used, previous_motif)
        pose = _MOTIF_TO_POSE[motif]
        composition = _COMPOSITIONS[(idx - 1) % len(_COMPOSITIONS)]

        scene.primary_motif = motif
        scene.pose_family = pose
        scene.optional_secondary_motif = ""
        scene.composition_hint = composition
        scene.frame_safety_hint = (
            "full body visible, head fully visible, feet fully visible, "
            "character within central safe area, character occupies about 35-45 "
            "percent of frame height, bottom 25 percent empty for captions"
        )

        used.add(motif)
        previous_motif = motif


def _normalize_prompt_for_compare(prompt: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (prompt or "").lower()).strip()


def scene_frame_hint(_: str = "") -> str:
    return (
        "full body visible, head fully visible, feet fully visible, character "
        "placed within central safe area, character occupies about 35-45 percent "
        "of frame height, keep bottom 25 percent mostly empty for captions"
    )


def _validate_minimalist_diversity(scenes) -> None:
    seen_prompts: dict[str, int] = {}
    pair_counts: dict[tuple[str, str], int] = {}
    previous_pair: tuple[str, str] | None = None
    for idx, scene in enumerate(scenes, start=1):
        pair = (scene.pose_family, scene.primary_motif)
        norm = _normalize_prompt_for_compare(scene.image_prompt)
        seen_prompts[norm] = seen_prompts.get(norm, 0) + 1
        pair_counts[pair] = pair_counts.get(pair, 0) + 1

        if previous_pair == pair or pair_counts[pair] > 2 or seen_prompts[norm] > 1:
            old_motif = scene.primary_motif
            used = {s.primary_motif for s in scenes[: idx - 1]}
            scene.primary_motif = _choose_motif(
                scene.voice_script + " " + scene.title,
                idx + len(used),
                used,
                previous_pair[1] if previous_pair else "",
            )
            scene.pose_family = _MOTIF_TO_POSE[scene.primary_motif]
            scene.composition_hint = _COMPOSITIONS[(idx + 2) % len(_COMPOSITIONS)]
            scene.frame_safety_hint = (
                "full body visible, head fully visible, feet fully visible, "
                "character within central safe area, character occupies about "
                "35-45 percent of frame height, bottom 25 percent empty for captions"
            )
            logger.warning(
                "scene %d: adjusted duplicate minimalist visual plan %s -> %s",
                scene.scene_index,
                old_motif,
                scene.primary_motif,
            )
        previous_pair = (scene.pose_family, scene.primary_motif)


def _prepare_minimalist_image_prompts(scenes) -> None:
    _assign_minimalist_visual_plans(scenes)
    _validate_minimalist_diversity(scenes)
    for scene in scenes:
        scene.image_prompt = _sanitize_minimalist_prompt(
            scene.image_prompt,
            pose_family=scene.pose_family,
            primary_motif=scene.primary_motif,
            composition_hint=scene.composition_hint,
        )
    _validate_minimalist_diversity(scenes)
    for scene in scenes:
        scene.image_prompt = _sanitize_minimalist_prompt(
            scene.image_prompt,
            pose_family=scene.pose_family,
            primary_motif=scene.primary_motif,
            composition_hint=scene.composition_hint,
        )


def _sanitize_minimalist_prompt(
    prompt: str,
    *,
    pose_family: str = "",
    primary_motif: str = "",
    composition_hint: str = "",
) -> str:
    """Return a stable safe-pose prompt for minimalist emotional scenes."""
    prompt_l = (prompt or "").lower()

    motif = primary_motif if primary_motif in _MINIMALIST_MOTIFS else ""
    if not motif:
        motif = _choose_motif(prompt_l, 1, set(), "")
    pose_key = pose_family if pose_family in _MINIMALIST_POSES else _MOTIF_TO_POSE[motif]
    composition = composition_hint or _COMPOSITIONS[0]

    prompt = ", ".join(
        (
            _MINIMALIST_SAFE_PROMPT,
            _MINIMALIST_POSES[pose_key],
            f"primary motif id: {motif}",
            f"primary motif: {_MOTIF_DESCRIPTIONS[motif]}",
            f"composition: {composition}",
            f"frame safety: {scene_frame_hint(composition_hint)}",
            _MINIMALIST_STYLE_LOCK,
            _MINIMALIST_COMPOSITION_LOCK,
            _MINIMALIST_ONE_CHARACTER_LOCK,
            "no exposed skin, no injury, no gore, no complex hand gesture",
        )
    )
    for phrase in _MINIMALIST_FORBIDDEN_HINTS:
        prompt = re.sub(re.escape(phrase), "", prompt, flags=re.IGNORECASE)
    prompt = re.sub(r"\s{2,}", " ", prompt)
    prompt = re.sub(r"\s+,", ",", prompt)
    return prompt.strip(" ,")


def _sha256_short(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _record_asset(scene, path: Path) -> None:
    scene.asset_hash = _sha256_short(path)
    logger.info(
        "scene %d asset=%s hash=%s status=%s pose=%s motif=%s compatible=%s "
        "character_mode=%s character_source=%s emotion=%s expression=%s head=%s face=%s "
        "socket_fallback=%s placeholder_sprite=%s placeholder_rig=%s "
        "placeholder_head=%s placeholder_face=%s composition=%s",
        scene.scene_index,
        path.name,
        scene.asset_hash,
        scene.asset_status,
        scene.pose_family,
        scene.primary_motif,
        scene.compatible_motif_used,
        scene.character_mode,
        scene.character_source,
        scene.emotion_tag,
        scene.selected_expression,
        scene.head_base_path,
        scene.face_path,
        scene.socket_alignment_fallback,
        scene.is_placeholder_sprite,
        scene.is_placeholder_rig,
        scene.is_placeholder_head,
        scene.is_placeholder_face,
        scene.composition_hint,
    )


def _finalize_minimalist_scene_metadata(
    scene,
    *,
    visual_mode: str,
    provider: str,
    used_reference_conditioning: bool = False,
    reference_paths: list[str] | None = None,
) -> None:
    scene.visual_mode = scene.visual_mode or visual_mode
    scene.provider = scene.provider or provider
    scene.used_reference_conditioning = bool(used_reference_conditioning)
    if reference_paths is not None:
        scene.reference_paths = list(reference_paths)


def _seed_for_scene(plan: TellaScenePlan, scene) -> int:
    if plan.theme == "minimalist_emotional":
        return _VIDEO_SEED + scene.scene_index * 101
    return _VIDEO_SEED


async def _fetch_minimalist_reference_assets(
    plan: TellaScenePlan,
    body_scenes,
    job_dir: Path,
    assets_dir: Path,
    width: int,
    height: int,
) -> None:
    provider = get_image_provider(os.environ.get("TELLA_IMAGE_PROVIDER") or "cloudflare")
    logger.info(
        "minimalist_emotional visual_mode=reference provider=%s supports_reference_conditioning=%s",
        provider.provider_name,
        provider.supports_reference_conditioning(),
    )
    if _require_reference_conditioning() and not provider.supports_reference_conditioning():
        raise RuntimeError(
            f"TELLA_REQUIRE_REFERENCE_CONDITIONING=1 but provider {provider.provider_name} "
            "does not support image-reference conditioning."
        )
    if not provider.supports_reference_conditioning():
        logger.warning(
            "Reference image conditioning is not available for this provider; using text lock only."
        )

    visual_bible = build_visual_bible(plan)
    save_visual_bible(visual_bible, job_dir)
    references = await generate_character_references(
        visual_bible,
        job_dir,
        provider,
        aspect=plan.aspect_ratio,
    )
    selected_refs = selected_reference_paths(references, job_dir)
    if not selected_refs:
        raise RuntimeError(
            "reference mode could not generate any usable character references; "
            "check image provider credentials and reference metadata."
        )

    previous_scene_path = ""
    previous_hashes: list[str] = []
    visual_plans: list[dict] = []
    total_vision_qc_calls = 0
    total_scene_regeneration_attempts = 0
    total_qc_json_parse_attempts = 0
    for scene in body_scenes:
        base = f"scene_{scene.scene_index:02d}_{_safe_stem(scene.title)}"
        use_previous = (
            _use_previous_scene_reference()
            and provider.supports_reference_conditioning()
            and previous_scene_path
        )
        infer_scene_anatomy_expectations(scene)
        scene_visual_plan = build_scene_visual_plan(
            scene,
            visual_bible,
            references,
            previous_scene_reference_path=previous_scene_path if use_previous else "",
        )
        visual_plans.append(scene_visual_plan.model_dump())

        prompt = scene_visual_plan.visual_prompt
        negative_prompt = scene_visual_plan.negative_prompt
        reference_inputs = list(selected_refs)
        if use_previous:
            reference_inputs.append(job_dir / previous_scene_path)
        original_reference_paths = [str(p.relative_to(job_dir)) for p in reference_inputs]
        scene.original_reference_paths = list(original_reference_paths)

        final_out = assets_dir / f"{base}.jpg"
        best_out: Path | None = None
        best_qc = None
        attempt_limit = max_attempts()
        scene.max_attempts_allowed = attempt_limit
        soft_fail_streaks: dict[str, int] = {}
        action_mismatch_severity_history: list[str] = []
        attempt_records: list[tuple[Path, SceneQCResult]] = []
        selected_best_failed_attempt = False
        selected_best_failed_attempt_reason = ""
        best_attempt_ranking_summary = ""
        for attempt in range(1, attempt_limit + 1):
            scene.attempt_count = attempt
            attempt_out = final_out if attempt == attempt_limit else assets_dir / f"{base}_attempt_{attempt}.jpg"
            if attempt > 1 and best_qc and best_qc.repair_prompt:
                prompt = best_qc.repair_prompt or repair_prompt(
                    scene_visual_plan.visual_prompt,
                    best_qc.failure_reasons,
                )
                scene.repair_reference_paths = [str(p.relative_to(job_dir)) for p in reference_inputs]
            try:
                result = await provider.generate_reference_image(
                    prompt=prompt,
                    references=reference_inputs,
                    negative_prompt=negative_prompt,
                    aspect=plan.aspect_ratio,
                    seed=_seed_for_scene(plan, scene) + attempt * 17 if provider.supports_seed() else None,
                    out_path=attempt_out,
                    metadata={
                        "scene_index": scene.scene_index,
                        "visual_mode": "reference",
                        "reference_ids": scene_visual_plan.character_reference_ids,
                    },
                )
            except Exception as exc:
                scene.asset_error = str(exc)[:300]
                if attempt >= attempt_limit:
                    raise
                logger.warning(
                    "scene %d reference generation attempt %d/%d failed: %s",
                    scene.scene_index,
                    attempt,
                    attempt_limit,
                    str(exc)[:160],
                )
                continue

            if attempt > 1:
                total_scene_regeneration_attempts += 1
            scene.prompt_used = result.prompt_used
            scene.negative_prompt_used = result.negative_prompt_used
            scene.provider = result.provider
            scene.used_reference_conditioning = result.used_reference_conditioning
            scene.reference_paths = [str(p.relative_to(job_dir)) for p in selected_refs]
            scene.previous_scene_reference_path = scene_visual_plan.previous_scene_reference_path
            if attempt > 1:
                scene.used_reference_conditioning_on_repair = bool(result.used_reference_conditioning)
            qc_result = evaluate_scene_image(
                scene,
                attempt_out,
                visual_bible,
                {
                    "aspect": plan.aspect_ratio,
                    "previous_hashes": previous_hashes,
                    "width": width,
                    "height": height,
                    "job_dir": job_dir,
                    "attempt": attempt,
                    "max_attempts_allowed": attempt_limit,
                    "is_final_attempt": attempt >= attempt_limit,
                    "expected_character_count": scene_visual_plan.expected_character_count,
                    "soft_fail_streaks": soft_fail_streaks,
                    "action_mismatch_severity_history": action_mismatch_severity_history,
                    "original_reference_paths": original_reference_paths,
                },
            )
            best_qc = qc_result
            total_vision_qc_calls += int(qc_result.vision_qc_call_count)
            total_qc_json_parse_attempts += int(qc_result.qc_json_parse_attempt_count)
            soft_fail_streaks = {
                "hairstyle": int(qc_result.hairstyle_mismatch_streak),
                "outfit": int(qc_result.outfit_mismatch_streak),
                "action": int(qc_result.action_mismatch_streak),
            }
            action_mismatch_severity_history = list(qc_result.action_mismatch_severity_history)
            attempt_records.append((attempt_out, qc_result))
            save_qc_result(qc_result, job_dir, attempt=attempt, final=False)
            if qc_result.passed:
                best_out = attempt_out
                break
            if qc_result.stopped_retry_loop_early_due_to_repeated_soft_fail:
                logger.warning(
                    "scene %d QC stopped retries early after repeated soft-fail escalation: %s",
                    scene.scene_index,
                    qc_result.repeated_soft_fail_escalation_reasons,
                )
                break
            logger.warning(
                "scene %d QC failed attempt %d/%d score=%.2f reasons=%s",
                scene.scene_index,
                attempt,
                attempt_limit,
                qc_result.score,
                qc_result.failure_reasons,
            )

        if best_out is None:
            if attempt_records:
                ranked_records = sorted(
                    enumerate(attempt_records),
                    key=lambda item: rank_qc_attempt(item[1][1], item[0]),
                )
                _, (best_out, best_qc) = ranked_records[0]
                selected_best_failed_attempt = True
                selected_best_failed_attempt_reason = (
                    "all attempts failed; selected least-bad attempt by QC ranking"
                )
                best_attempt_ranking_summary = summarize_qc_attempts(
                    [record[1] for record in attempt_records]
                )
            else:
                best_out = final_out if final_out.is_file() else attempt_out
            if strict_visual_qc() and best_qc and not best_qc.passed:
                raise RuntimeError(
                    f"scene {scene.scene_index} failed strict visual QC: {best_qc.failure_reasons}"
                )
        if best_qc:
            save_qc_result(best_qc, job_dir, final=True)
        if best_out != final_out:
            final_out.write_bytes(best_out.read_bytes())

        final_hash = image_hash(final_out)
        previous_hashes.append(final_hash)
        previous_scene_path = str(final_out.relative_to(job_dir))
        scene.image_filenames = [f"assets/{final_out.name}"]
        scene.asset_status = "reference_generated"
        scene.asset_error = ""
        scene.asset_hash = final_hash
        _finalize_minimalist_scene_metadata(
            scene,
            visual_mode="reference",
            provider=provider.provider_name,
            used_reference_conditioning=scene.used_reference_conditioning,
            reference_paths=[str(p.relative_to(job_dir)) for p in selected_refs],
        )
        scene.character_source = "reference_generated"
        scene.character_mode = "ai_reference"
        if best_qc:
            try:
                selected_attempt_path = str(best_out.relative_to(job_dir))
            except ValueError:
                selected_attempt_path = str(best_out)
            apply_qc_result_to_scene(
                scene,
                best_qc,
                selected_attempt_path=selected_attempt_path,
                attempts_actually_ran=len(attempt_records),
                max_attempts_allowed=attempt_limit,
                selected_best_failed_attempt=selected_best_failed_attempt,
                selected_best_failed_attempt_reason=selected_best_failed_attempt_reason,
                best_attempt_ranking_summary=best_attempt_ranking_summary,
            )
        _record_asset(scene, final_out)

    plan.total_vision_qc_calls = total_vision_qc_calls
    plan.total_scene_regeneration_attempts = total_scene_regeneration_attempts
    plan.total_qc_json_parse_attempts = total_qc_json_parse_attempts
    (job_dir / "visual_plans.json").write_text(
        json.dumps(visual_plans, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("fetch_assets: all %d minimalist reference scenes done", len(body_scenes))


def _force_character_mode_for_visual_mode(visual_mode: str) -> str | None:
    if visual_mode == "rig":
        return "rig"
    if visual_mode == "curated_sprite":
        return "auto"
    return None


async def fetch_assets(plan: TellaScenePlan, job_dir: Path) -> None:
    """Populate ``plan.scenes[i].image_filenames`` for every body scene.

    Mutates the plan in place. Writes to ``<job_dir>/assets/``.

    Raises:
        RuntimeError: when ANY scene's asset fetch fails. Callers wanting
            partial-success behaviour should wrap in their own try/except.
    """
    job_dir = Path(job_dir)
    assets_dir = job_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    body_scenes = [s for s in plan.scenes if s.kind == "scene"]
    if not body_scenes:
        return

    width, height = _GEN_DIMS.get(plan.aspect_ratio, _GEN_DIMS["9:16"])
    if plan.media_source == "ai_image" and plan.theme == "minimalist_emotional":
        visual_mode = _minimalist_visual_mode()
        logger.info("minimalist_emotional visual_mode=%s", visual_mode)
        _prepare_minimalist_image_prompts(body_scenes)
        if visual_mode == "reference":
            await _fetch_minimalist_reference_assets(
                plan,
                body_scenes,
                job_dir,
                assets_dir,
                width,
                height,
            )
            return
        if visual_mode in {"curated_sprite", "rig"}:
            logger.info(
                "minimalist_emotional local composition active; "
                "Cloudflare full-scene image generation is not called by default"
            )
            forced_character_mode = _force_character_mode_for_visual_mode(visual_mode)
            old_character_mode = os.environ.get("TELLA_MINIMALIST_CHARACTER_MODE")
            if forced_character_mode:
                os.environ["TELLA_MINIMALIST_CHARACTER_MODE"] = forced_character_mode
            job_state = sprite_composer.JobState(job_id=job_dir.name)
            try:
                for scene in body_scenes:
                    base = f"scene_{scene.scene_index:02d}_{_safe_stem(scene.title)}"
                    out = assets_dir / f"{base}.jpg"
                    result = sprite_composer.compose_scene(
                        scene,
                        out,
                        width,
                        height,
                        job_state,
                    )
                    scene.image_filenames = [f"assets/{out.name}"]
                    scene.asset_status = "local_composed"
                    scene.asset_error = ""
                    scene.asset_hash = result.asset_hash
                    _finalize_minimalist_scene_metadata(
                        scene,
                        visual_mode=visual_mode,
                        provider="local",
                    )
                    _record_asset(scene, out)
            finally:
                if forced_character_mode:
                    if old_character_mode is None:
                        os.environ.pop("TELLA_MINIMALIST_CHARACTER_MODE", None)
                    else:
                        os.environ["TELLA_MINIMALIST_CHARACTER_MODE"] = old_character_mode
            logger.info("fetch_assets: all %d minimalist local scenes done", len(body_scenes))
            return
        logger.info(
            "minimalist_emotional visual_mode=ai_scene; using full-scene AI generation without reference conditioning"
        )

    sem = asyncio.Semaphore(MAX_CONCURRENT)
    ai_fallback_state = sprite_composer.JobState(job_id=f"{job_dir.name}:fallback")

    async def _fallback_to_stock_photo(scene, base: str) -> None:
        """Last-resort fetch when the primary provider fails. Pexels Photo
        is the safest fallback — no NSFW safety filter false-positives,
        no per-account quota that resets only daily.
        """
        out = assets_dir / f"{base}_fallback.jpg"
        if _stock_fallback_disabled():
            raise RuntimeError(
                "stock fallback disabled by TELLA_DISABLE_STOCK_FALLBACK=1"
            )
        query = (
            scene.stock_query
            or scene.image_prompt[:60]
            or scene.title[:60]
            or "abstract"
        )
        await stock_photo.search_and_download(
            query, out, width=width, height=height,
        )
        scene.image_filenames = [f"assets/{out.name}"]
        scene.asset_status = scene.asset_status or "done"
        _record_asset(scene, out)
        logger.warning(
            "scene %d: AI image failed → fell through to Pexels Photo (query=%r)",
            scene.scene_index, query,
        )

    async def _one(scene_idx: int, scene) -> None:
        async with sem:
            base = f"scene_{scene.scene_index:02d}_{_safe_stem(scene.title)}"
            if plan.media_source == "ai_image":
                out = assets_dir / f"{base}.jpg"
                prompt_for_cf = scene.image_prompt
                try:
                    await ai_image.generate_image(
                        prompt_for_cf,
                        out,
                        width=width,
                        height=height,
                        # Other themes keep one video seed for continuity.
                        # Minimalist emotional gets a deterministic per-scene
                        # seed because prompt collapse showed up as duplicate
                        # images; the strict character/style lock carries
                        # identity consistency for that theme.
                        seed=_seed_for_scene(plan, scene),
                    )
                    scene.image_filenames = [f"assets/{out.name}"]
                    scene.asset_status = "done"
                    scene.asset_error = ""
                    if plan.theme == "minimalist_emotional":
                        _finalize_minimalist_scene_metadata(
                            scene,
                            visual_mode="ai_scene",
                            provider="cloudflare",
                        )
                        scene.prompt_used = prompt_for_cf
                    _record_asset(scene, out)
                except Exception as exc:
                    scene.asset_error = str(exc)[:300]
                    if plan.theme == "minimalist_emotional":
                        if _is_nsfw_prompt_rejection(exc):
                            sanitized_prompt = _sanitize_minimalist_prompt(
                                scene.image_prompt,
                                pose_family=scene.pose_family,
                                primary_motif=scene.primary_motif,
                                composition_hint=scene.composition_hint,
                            )
                            sanitized_out = assets_dir / f"{base}_safe.jpg"
                            logger.warning(
                                "scene %d: CF NSFW prompt rejection -> retry sanitized prompt",
                                scene.scene_index,
                            )
                            try:
                                await ai_image.generate_image(
                                    sanitized_prompt,
                                    sanitized_out,
                                    width=width,
                                    height=height,
                                    seed=_seed_for_scene(plan, scene),
                                )
                                scene.image_filenames = [
                                    f"assets/{sanitized_out.name}"
                                ]
                                scene.asset_status = "sanitized_retry"
                                scene.asset_error = ""
                                _finalize_minimalist_scene_metadata(
                                    scene,
                                    visual_mode="ai_scene",
                                    provider="cloudflare",
                                )
                                scene.prompt_used = sanitized_prompt
                                _record_asset(scene, sanitized_out)
                                return
                            except Exception as retry_exc:
                                scene.asset_error = str(retry_exc)[:300]
                                logger.warning(
                                    "scene %d: sanitized AI retry failed (%s)",
                                    scene.scene_index,
                                    str(retry_exc)[:120],
                                )

                        fallback_out = assets_dir / f"{base}_fallback.jpg"
                        result = sprite_composer.compose_scene(
                            scene,
                            fallback_out,
                            width,
                            height,
                            ai_fallback_state,
                        )
                        scene.image_filenames = [f"assets/{fallback_out.name}"]
                        scene.asset_status = "abstract_fallback"
                        scene.asset_hash = result.asset_hash
                        _finalize_minimalist_scene_metadata(
                            scene,
                            visual_mode="ai_scene",
                            provider="local_fallback",
                        )
                        _record_asset(scene, fallback_out)
                        return

                    # Either daily neuron quota burned across every CF
                    # account, or the safety filter false-positived a
                    # specific scene's prompt. Either way, Pexels Photo
                    # always works — fall through so the user still gets
                    # a complete video instead of "all 5 accounts failed".
                    logger.warning(
                        "scene %d: AI image failed (%s) → fallback to Pexels",
                        scene.scene_index, str(exc)[:120],
                    )
                    await _fallback_to_stock_photo(scene, base)
            elif plan.media_source == "stock_photo":
                out = assets_dir / f"{base}.jpg"
                await stock_photo.search_and_download(
                    scene.stock_query or scene.image_prompt[:60],
                    out,
                    width=width,
                    height=height,
                )
                scene.image_filenames = [f"assets/{out.name}"]
                scene.asset_status = "done"
                scene.asset_error = ""
                _record_asset(scene, out)
            elif plan.media_source == "stock_video":
                out = assets_dir / f"{base}.mp4"
                try:
                    final = await stock_video.search_and_download(
                        scene.stock_query or scene.image_prompt[:60],
                        out,
                        width=width,
                        height=height,
                    )
                    scene.image_filenames = [f"assets/{final.name}"]
                    scene.asset_status = "done"
                    scene.asset_error = ""
                    _record_asset(scene, final)
                except Exception as exc:
                    scene.asset_error = str(exc)[:300]
                    logger.warning(
                        "scene %d: stock video failed (%s) → fallback to Pexels Photo",
                        scene.scene_index, str(exc)[:120],
                    )
                    await _fallback_to_stock_photo(scene, base)
            else:
                raise RuntimeError(
                    f"unknown media_source {plan.media_source!r}"
                )

    logger.info(
        "fetch_assets: %d scenes, source=%s, %dx%d",
        len(body_scenes), plan.media_source, width, height,
    )
    await asyncio.gather(*[_one(i, s) for i, s in enumerate(body_scenes)])
    logger.info("fetch_assets: all %d scenes done", len(body_scenes))


__all__ = [
    "MAX_CONCURRENT",
    "fetch_assets",
]
