"""Visual QC for generated scene images."""
from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat

from tella._gemini import get_client, parse_json_loose
from tella.planner.models import Scene, SceneQCResult, VisualBible
from tella.planner.visual_prompts import repair_prompt

logger = logging.getLogger("tella.media.visual_qc")

_DEFAULT_VISION_MODEL = "gemini-flash-latest"
_SOFT_STREAK_LIMIT = 2
_SYMBOLIC_SOFT_FAILURES = (
    "metaphor_unreadable",
    "palette_drift",
    "line_style_drift",
    "composition_scale_drift",
    "minor_object_ambiguity",
    "interaction_plausibility_drift",
    "composition_clarity_drift",
    "style_consistency_drift",
)
_SYMBOLIC_REQUIRED_QC_FIELDS = (
    "symbolic_meaning_matches",
    "symbolic_visual_matches",
    "required_subjects_present",
    "metaphor_is_readable",
    "visual_identity_matches",
    "adult_age_policy_matches",
    "style_matches_symbolic_reel",
    "subject_scale_matches",
    "palette_matches",
    "line_style_matches",
    "forbidden_drift_detected",
    "forbidden_drift_types",
    "requested_action_visible",
    "character_object_interaction_plausible",
    "emotional_meaning_readable",
    "composition_clear",
    "style_consistent",
    "object_ambiguity_severity",
)
_HARD_FAILURE_RE = re.compile(
    r"(extra\s+(?:limb|arm|leg|hand|foot)|duplicate\s+(?:face|head|body)|"
    r"duplicated\s+(?:foot|feet|leg|head)|third\s+leg|two\s+heads|"
    r"missing\s+head|no\s+head|broken\s+body|disconnected\s+limb|"
    r"severe\s+limb\s+deformation|watermark|visible\s+text|bad\s+crop|"
    r"cropped\s+(?:head|body|feet|legs)|tangled\s+(?:limbs|legs))",
    re.IGNORECASE,
)


def qc_mode() -> str:
    mode = (os.environ.get("TELLA_SCENE_QC") or "basic").strip().lower()
    if mode not in {"off", "basic", "vision"}:
        logger.warning("invalid TELLA_SCENE_QC=%r; using basic", mode)
        return "basic"
    return mode


def max_attempts() -> int:
    raw = (os.environ.get("TELLA_SCENE_MAX_ATTEMPTS") or "2").strip()
    try:
        return max(1, min(5, int(raw)))
    except ValueError:
        return 2


def qc_json_parse_attempts() -> int:
    raw = (os.environ.get("TELLA_QC_JSON_PARSE_ATTEMPTS") or "2").strip()
    try:
        return max(1, min(3, int(raw)))
    except ValueError:
        return 2


def strict_visual_qc() -> bool:
    return (os.environ.get("TELLA_STRICT_VISUAL_QC") or "").strip() == "1"


def infer_scene_anatomy_expectations(scene: Scene) -> dict[str, str]:
    """Populate conservative structured anatomy expectations on a scene."""

    text = " ".join(
        [
            scene.pose_family or "",
            scene.composition_hint or "",
            scene.frame_safety_hint or "",
            scene.image_prompt or "",
            scene.title or "",
        ]
    ).lower()

    if any(token in text for token in ("close-up", "close up", "portrait", "headshot")):
        shot_type = "close_up"
    elif "medium" in text and "wide" in text:
        shot_type = "medium_wide"
    elif "wide" in text:
        shot_type = "wide"
    elif "medium" in text:
        shot_type = "medium"
    else:
        shot_type = "medium_wide"

    if any(token in text for token in ("sitting", "seated", "kneeling", "curled")):
        pose_type = "sitting"
    elif any(token in text for token in ("lying", "laying", "in bed")):
        pose_type = "lying"
    elif any(token in text for token in ("walking", "step", "moving", "path")):
        pose_type = "walking"
    elif any(token in text for token in ("standing", "stand")):
        pose_type = "standing"
    elif any(token in text for token in ("reaching", "holding", "touching")):
        pose_type = "reaching"
    else:
        pose_type = "unknown"

    if shot_type == "close_up":
        body_visibility = "upper_body"
    elif "waist" in text:
        body_visibility = "waist_up"
    elif "knee" in text:
        body_visibility = "knees_up"
    else:
        body_visibility = "full_body"

    if body_visibility == "full_body":
        anatomy_expectation = (
            "full_body_two_legs_visible: one main character, one head, one face, "
            "one torso, exactly two arms, exactly two legs and two feet when the "
            "lower body is visible, no duplicated or disconnected body parts"
        )
    elif body_visibility in {"upper_body", "waist_up"}:
        anatomy_expectation = (
            "upper_body_only: head, face, torso, and visible arms should be clear; "
            "legs are not required in this crop, and partial ambiguous legs should be avoided"
        )
    else:
        anatomy_expectation = (
            "lower_body_visible: visible anatomy should be simple and readable, "
            "with no extra limbs, duplicate feet, duplicate head, or bad crop"
        )

    scene.shot_type = scene.shot_type or shot_type
    scene.body_visibility = scene.body_visibility or body_visibility
    scene.pose_type = scene.pose_type or pose_type
    scene.anatomy_expectation = scene.anatomy_expectation or anatomy_expectation
    return {
        "shot_type": scene.shot_type,
        "body_visibility": scene.body_visibility,
        "pose_type": scene.pose_type,
        "anatomy_expectation": scene.anatomy_expectation,
    }


def anatomy_prompt_hints(scene: Scene) -> list[str]:
    expectations = infer_scene_anatomy_expectations(scene)
    hints = [
        f"structured shot type: {expectations['shot_type']}",
        f"body visibility requirement: {expectations['body_visibility']}",
        f"pose type: {expectations['pose_type']}",
        expectations["anatomy_expectation"],
        "one head, one face, one torso, no extra limbs, no duplicate head, no text, no watermark",
    ]
    if expectations["pose_type"] == "sitting":
        hints.append(
            "clear simple sitting pose, lower body naturally placed, no overlapping extra legs, no duplicated feet, no tangled limbs"
        )
    if expectations["body_visibility"] == "full_body":
        hints.append("full body visible, exactly two legs and two feet, clear simple anatomy, no extra limbs")
    if expectations["pose_type"] == "walking":
        hints.append("clear side or three-quarter walking pose, exactly two legs, no duplicate stride legs, no extra feet")
    if expectations["shot_type"] in {"close_up", "medium"} or expectations["body_visibility"] in {"upper_body", "waist_up"}:
        hints.append("waist-up framing if cropped, lower body not visible, avoid ambiguous partial legs")
    if expectations["shot_type"] in {"medium", "medium_wide", "wide"}:
        hints.append("do not crop the head, feet, hands, or lower body in this medium-wide composition")
    return hints


def evaluate_scene_image(
    scene: Scene,
    image_path: Path,
    visual_bible: VisualBible,
    expected: dict[str, Any] | None = None,
) -> SceneQCResult:
    mode = qc_mode()
    expected = expected or {}
    anatomy = infer_scene_anatomy_expectations(scene)
    image_path = Path(image_path)

    if mode == "off":
        return SceneQCResult(
            scene_index=scene.scene_index,
            passed=True,
            final_passed=True,
            model_passed=True,
            model_qc_passed=True,
            basic_qc_passed=True,
            confidence=1.0,
            score=1.0,
            checks={"qc_off": True},
            attempt_count=scene.attempt_count,
            qc_mode=mode,
            image_path=str(image_path),
            scene_image_attempt_count=int(expected.get("attempt", scene.attempt_count or 0)),
            remaining_scene_attempts=_remaining_attempts(expected, scene),
            **anatomy,
        )

    basic = _basic_qc(image_path, expected)
    if not basic["file_ready"]:
        return _result(
            scene,
            image_path,
            visual_bible,
            checks=basic["checks"],
            failures=basic["failures"],
            score=basic["score"],
            basic_qc_passed=False,
            model_qc_passed=False,
            mode=mode,
            anatomy=anatomy,
            expected=expected,
        )

    checks = dict(basic["checks"])
    failures = list(basic["failures"])
    score = float(basic["score"])
    basic_passed = not failures and score >= 0.7

    if mode != "vision":
        return _result(
            scene,
            image_path,
            visual_bible,
            checks=checks,
            failures=failures,
            score=score,
            basic_qc_passed=basic_passed,
            model_qc_passed=basic_passed,
            mode=mode,
            anatomy=anatomy,
            expected=expected,
        )

    vision = _run_vision_qc(scene, image_path, visual_bible, expected, anatomy)
    checks["vision_qc_available"] = bool(vision.get("available"))
    checks["vision_json_valid"] = bool(vision.get("data"))

    if not vision.get("available"):
        logger.warning("scene %d vision QC unavailable: %s", scene.scene_index, vision.get("error", "unknown"))
        return _result(
            scene,
            image_path,
            visual_bible,
            checks=checks,
            failures=failures,
            score=score,
            basic_qc_passed=basic_passed,
            model_qc_passed=basic_passed,
            mode=mode,
            anatomy=anatomy,
            expected=expected,
            vision_available=False,
            vision_model=vision.get("model", ""),
            vision_qc_call_count=int(vision.get("call_count", 0)),
            qc_json_parse_attempt_count=int(vision.get("parse_attempt_count", 0)),
            raw_response_path=vision.get("raw_response_path", ""),
        )

    if not vision.get("data"):
        failures.append("vision QC JSON parse failed")
        return _result(
            scene,
            image_path,
            visual_bible,
            checks=checks,
            failures=failures,
            score=max(0.0, score - 0.45),
            basic_qc_passed=basic_passed,
            model_qc_passed=False,
            mode=mode,
            anatomy=anatomy,
            expected=expected,
            vision_available=True,
            vision_model=vision.get("model", ""),
            vision_qc_call_count=int(vision.get("call_count", 0)),
            qc_json_parse_attempt_count=int(vision.get("parse_attempt_count", 0)),
            raw_response_path=vision.get("raw_response_path", ""),
            final_attempt_hard_fail_reasons=["vision QC JSON parse failed"] if expected.get("is_final_attempt") else [],
            loop_stop_reason="vision QC JSON parse failed" if expected.get("is_final_attempt") else "",
            loop_stop_reasons_all=["vision QC JSON parse failed"] if expected.get("is_final_attempt") else [],
        )

    return _result_from_vision(
        scene,
        image_path,
        visual_bible,
        expected,
        anatomy,
        checks,
        failures,
        score,
        basic_passed,
        vision,
    )


def save_qc_result(
    result: SceneQCResult,
    job_dir: Path,
    *,
    attempt: int | None = None,
    final: bool = True,
) -> Path:
    out_dir = Path(job_dir) / "qc"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    if attempt is not None:
        paths.append(out_dir / f"scene_{result.scene_index:02d}_attempt_{attempt}_qc.json")
    if final or attempt is None:
        paths.append(out_dir / f"scene_{result.scene_index:02d}_qc.json")
    payload = json.dumps(result.model_dump(), ensure_ascii=False, indent=2)
    for out in paths:
        out.write_text(payload, encoding="utf-8")
    return paths[-1]


def image_hash(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def rank_qc_attempt(result: SceneQCResult, order_index: int = 0) -> tuple:
    too_many_people = result.character_count > max(2, result.expected_character_count + 1)
    too_many_heads = result.head_count > max(1, result.expected_character_count)
    hard_anatomy = result.has_extra_limbs or result.has_duplicate_face or too_many_heads
    identity_score = int(result.hairstyle_matches_spec) + int(result.outfit_matches_spec)
    return (
        int(bool(result.symbolic_qc_hard_fail_reasons)),
        int(hard_anatomy),
        int(result.has_text_or_watermark),
        int(too_many_people),
        -identity_score,
        int(not result.scene_matches_requested_action),
        int(result.action_mismatch_severity == "major"),
        int(result.bad_crop),
        len(result.loop_stop_reasons_all or result.failure_reasons),
        len(result.failure_reasons),
        -float(result.confidence or result.score),
        -int(result.attempt_count or order_index),
    )


def summarize_qc_attempts(results: list[SceneQCResult]) -> str:
    pieces = []
    for result in results:
        hard = result.loop_stop_reason or result.hard_fail_priority_reason or "none"
        pieces.append(
            f"attempt {result.attempt_count}: score={result.score:.2f}, "
            f"passed={result.passed}, hard={hard}, failures={len(result.failure_reasons)}"
        )
    return "; ".join(pieces)[:800]


def apply_qc_result_to_scene(
    scene: Scene,
    result: SceneQCResult,
    *,
    selected_attempt_path: str = "",
    attempts_actually_ran: int = 0,
    max_attempts_allowed: int = 0,
    selected_best_failed_attempt: bool = False,
    selected_best_failed_attempt_reason: str = "",
    best_attempt_ranking_summary: str = "",
) -> None:
    scene.qc_mode = result.qc_mode
    scene.qc_passed = bool(result.passed)
    scene.final_passed = bool(result.final_passed)
    scene.model_qc_passed = bool(result.model_qc_passed)
    scene.qc_confidence = float(result.confidence)
    scene.qc_score = float(result.score)
    scene.qc_failure_reasons = list(result.failure_reasons)
    scene.repair_prompt = result.repair_prompt
    scene.selected_attempt_path = selected_attempt_path
    scene.attempt_count = int(result.attempt_count)
    scene.attempts_actually_ran = int(attempts_actually_ran)
    scene.max_attempts_allowed = int(max_attempts_allowed)
    scene.selected_best_failed_attempt = bool(selected_best_failed_attempt)
    scene.selected_best_failed_attempt_reason = selected_best_failed_attempt_reason
    scene.best_attempt_ranking_summary = best_attempt_ranking_summary

    scene.shot_type = result.shot_type
    scene.body_visibility = result.body_visibility
    scene.pose_type = result.pose_type
    scene.anatomy_expectation = result.anatomy_expectation
    scene.deterministic_override_applied = bool(result.deterministic_override_applied)
    scene.deterministic_override_reasons = list(result.deterministic_override_reasons)
    scene.identity_soft_fail = bool(result.identity_soft_fail)
    scene.identity_hard_fail = bool(result.identity_hard_fail)
    scene.action_soft_fail = bool(result.action_soft_fail)
    scene.action_hard_fail = bool(result.action_hard_fail)
    scene.action_mismatch_severity = result.action_mismatch_severity
    scene.action_mismatch_severity_history = list(result.action_mismatch_severity_history)
    scene.hairstyle_matches_spec = bool(result.hairstyle_matches_spec)
    scene.outfit_matches_spec = bool(result.outfit_matches_spec)
    scene.scene_matches_requested_action = bool(result.scene_matches_requested_action)
    scene.hairstyle_mismatch_streak = int(result.hairstyle_mismatch_streak)
    scene.outfit_mismatch_streak = int(result.outfit_mismatch_streak)
    scene.action_mismatch_streak = int(result.action_mismatch_streak)
    scene.repeated_soft_fail_escalation_applied = bool(result.repeated_soft_fail_escalation_applied)
    scene.repeated_soft_fail_escalation_reasons = list(result.repeated_soft_fail_escalation_reasons)
    scene.stopped_retry_loop_early_due_to_repeated_soft_fail = bool(
        result.stopped_retry_loop_early_due_to_repeated_soft_fail
    )
    scene.soft_fail_on_final_attempt = bool(result.soft_fail_on_final_attempt)
    scene.previous_attempt_identity_failures = list(result.previous_attempt_identity_failures)
    scene.repeated_identity_failures = list(result.repeated_identity_failures)
    scene.escalation_applied = bool(result.escalation_applied)
    scene.escalation_reasons = list(result.escalation_reasons)
    scene.final_attempt_hard_fail_reasons = list(result.final_attempt_hard_fail_reasons)
    scene.final_attempt_soft_fail_reasons = list(result.final_attempt_soft_fail_reasons)
    scene.loop_stop_reason = result.loop_stop_reason
    scene.loop_stop_reasons_all = list(result.loop_stop_reasons_all)
    scene.anatomy_hard_fail = bool(result.anatomy_hard_fail)
    scene.hard_fail_priority_reason = result.hard_fail_priority_reason
    scene.scene_image_attempt_count = int(result.scene_image_attempt_count)
    scene.remaining_scene_attempts = int(result.remaining_scene_attempts)
    scene.regeneration_reasons = list(result.regeneration_reasons)
    scene.original_reference_paths = list(result.original_reference_paths)
    scene.symbolic_qc_passed = bool(result.symbolic_qc_passed)
    scene.symbolic_qc_attempts = max(
        int(result.symbolic_qc_attempts),
        int(attempts_actually_ran),
    )
    scene.symbolic_qc_failure_reasons = list(result.symbolic_qc_failure_reasons)
    scene.symbolic_qc_last_failure_reason = result.symbolic_qc_last_failure_reason
    scene.symbolic_qc_repaired_prompt_used = bool(result.symbolic_qc_repaired_prompt_used)
    scene.symbolic_qc_final_status = result.symbolic_qc_final_status
    symbolic_image_evaluated = bool(
        result.qc_mode == "vision"
        and result.vision_available
        and result.symbolic_qc_final_status
        not in {"not_run", "vision_unavailable", "disabled", "not_applicable"}
    )
    scene.symbolic_meaning_matches = (
        bool(result.symbolic_meaning_matches) if symbolic_image_evaluated else None
    )
    scene.symbolic_visual_matches = (
        bool(result.symbolic_visual_matches) if symbolic_image_evaluated else None
    )
    scene.metaphor_is_readable = (
        bool(result.metaphor_is_readable) if symbolic_image_evaluated else None
    )
    scene.visual_identity_matches = (
        bool(result.visual_identity_matches) if symbolic_image_evaluated else None
    )
    scene.adult_age_policy_matches = (
        bool(result.adult_age_policy_matches) if symbolic_image_evaluated else None
    )
    scene.style_matches_symbolic_reel = (
        bool(result.style_matches_symbolic_reel) if symbolic_image_evaluated else None
    )
    scene.subject_scale_matches = (
        bool(result.subject_scale_matches) if symbolic_image_evaluated else None
    )
    scene.forbidden_drift_detected = (
        bool(result.forbidden_drift_detected) if symbolic_image_evaluated else None
    )
    scene.forbidden_drift_types = (
        list(result.forbidden_drift_types) if symbolic_image_evaluated else []
    )
    scene.symbolic_qc_hard_fail_reasons = list(result.symbolic_qc_hard_fail_reasons)
    scene.symbolic_qc_soft_fail_reasons = list(result.symbolic_qc_soft_fail_reasons)
    scene.symbolic_soft_fail_streaks = dict(result.symbolic_soft_fail_streaks)
    scene.required_subjects_present = (
        bool(result.required_subjects_present) if symbolic_image_evaluated else None
    )
    scene.requested_action_visible = (
        bool(result.requested_action_visible) if symbolic_image_evaluated else None
    )
    scene.character_object_interaction_plausible = (
        bool(result.character_object_interaction_plausible)
        if symbolic_image_evaluated
        else None
    )
    scene.emotional_meaning_readable = (
        bool(result.emotional_meaning_readable) if symbolic_image_evaluated else None
    )
    scene.composition_clear = (
        bool(result.composition_clear) if symbolic_image_evaluated else None
    )
    scene.style_consistent = (
        bool(result.style_consistent) if symbolic_image_evaluated else None
    )
    scene.object_ambiguity_severity = (
        result.object_ambiguity_severity if symbolic_image_evaluated else None
    )


def _basic_qc(image_path: Path, expected: dict[str, Any]) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    failures: list[str] = []
    score = 1.0

    checks["file_exists"] = image_path.is_file()
    if not checks["file_exists"]:
        failures.append("image file missing")
        return {"checks": checks, "failures": failures, "score": 0.0, "file_ready": False}

    try:
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            width, height = img.size
            checks["valid_image"] = True
            checks["expected_aspect"] = _aspect_ok(width, height, expected.get("aspect", "9:16"))
            if not checks["expected_aspect"]:
                failures.append(f"unexpected image aspect/size: {img.size}")
                score -= 0.2

            small = img.resize((64, 64))
            stat = ImageStat.Stat(small)
            mean = sum(stat.mean) / 3.0
            contrast = sum(high - low for low, high in small.getextrema()) / 3.0
            checks["not_too_dark"] = mean >= 18
            checks["not_too_flat"] = contrast >= 8
            if not checks["not_too_dark"]:
                failures.append("image is too dark or blank")
                score -= 0.35
            if not checks["not_too_flat"]:
                failures.append("image has very low contrast or appears blank")
                score -= 0.25

            digest = image_hash(image_path)
            previous_hashes = set(expected.get("previous_hashes", []))
            checks["not_duplicate_hash"] = digest not in previous_hashes
            if not checks["not_duplicate_hash"]:
                failures.append("image hash duplicates a previous scene")
                score -= 0.35

            caption_crop = img.crop((0, int(height * 0.75), width, height))
            caption_contrast = sum(
                high - low for low, high in caption_crop.resize((64, 16)).getextrema()
            ) / 3.0
            checks["caption_region_not_busy"] = caption_contrast < 95
            if not checks["caption_region_not_busy"]:
                score -= 0.1
                if (os.environ.get("TELLA_STRICT_CAPTION_SAFE_AREA") or "").strip() == "1":
                    failures.append("caption-safe lower region appears visually busy")
    except OSError as exc:
        checks["valid_image"] = False
        failures.append(f"cannot open image: {exc}")
        return {"checks": checks, "failures": failures, "score": 0.0, "file_ready": False}

    return {"checks": checks, "failures": failures, "score": max(0.0, score), "file_ready": True}


def _result(
    scene: Scene,
    image_path: Path,
    visual_bible: VisualBible,
    *,
    checks: dict[str, bool],
    failures: list[str],
    score: float,
    basic_qc_passed: bool,
    model_qc_passed: bool,
    mode: str,
    anatomy: dict[str, str],
    expected: dict[str, Any] | None = None,
    vision_available: bool = False,
    vision_model: str = "",
    vision_qc_call_count: int = 0,
    qc_json_parse_attempt_count: int = 0,
    raw_response_path: str = "",
    **extra: Any,
) -> SceneQCResult:
    del visual_bible
    expected = expected or {}
    failures = _unique_failures(failures)
    passed = not failures and score >= 0.7 and basic_qc_passed and model_qc_passed
    confidence = float(extra.pop("confidence", score))
    raw_model_passed = bool(extra.pop("model_passed", model_qc_passed))
    regeneration_reasons = list(extra.pop("regeneration_reasons", failures))
    symbolic_qc = _is_symbolic_qc(scene, expected)
    if symbolic_qc and "symbolic_qc_final_status" not in extra:
        if mode == "off":
            symbolic_status = "disabled"
        elif mode == "vision" and not vision_available:
            symbolic_status = "vision_unavailable"
        else:
            symbolic_status = "not_run"
        extra.update(
            symbolic_qc_passed=False,
            symbolic_qc_attempts=int(expected.get("attempt", scene.attempt_count or 0)),
            symbolic_qc_failure_reasons=[],
            symbolic_qc_last_failure_reason="",
            symbolic_qc_repaired_prompt_used=bool(expected.get("repaired_prompt_used")),
            symbolic_qc_final_status=symbolic_status,
        )
    if failures:
        if symbolic_qc:
            symbolic_failures = list(extra.get("symbolic_qc_failure_reasons", [])) or failures
            repaired = _symbolic_repair_prompt(
                scene,
                scene.prompt_used or scene.image_prompt,
                symbolic_failures,
            )
        else:
            repaired = repair_prompt(scene.prompt_used or scene.image_prompt, failures)
    else:
        repaired = ""
    return SceneQCResult(
        scene_index=scene.scene_index,
        passed=passed,
        final_passed=passed,
        model_passed=raw_model_passed,
        model_qc_passed=model_qc_passed,
        basic_qc_passed=basic_qc_passed,
        confidence=round(max(0.0, min(1.0, confidence)), 3),
        score=round(max(0.0, min(1.0, score)), 3),
        checks=checks,
        failure_reasons=failures,
        repair_prompt=repaired[:1500],
        attempt_count=scene.attempt_count,
        qc_mode=mode,
        vision_available=vision_available,
        vision_model=vision_model,
        vision_qc_call_count=vision_qc_call_count,
        qc_json_parse_attempt_count=qc_json_parse_attempt_count,
        raw_response_path=raw_response_path,
        image_path=str(image_path),
        scene_image_attempt_count=int(expected.get("attempt", scene.attempt_count or 0)),
        remaining_scene_attempts=_remaining_attempts(expected, scene),
        regeneration_reasons=regeneration_reasons,
        original_reference_paths=list(expected.get("original_reference_paths", [])),
        **anatomy,
        **extra,
    )


def _run_vision_qc(
    scene: Scene,
    image_path: Path,
    visual_bible: VisualBible,
    expected: dict[str, Any],
    anatomy: dict[str, str],
) -> dict[str, Any]:
    if not _has_gemini_key():
        return {
            "available": False,
            "error": "no Gemini API key configured",
            "model": _vision_model(),
            "call_count": 0,
            "parse_attempt_count": 0,
        }

    model = _vision_model()
    raw_response_path = _raw_response_path(scene, expected)
    try:
        from google.genai import types

        image_bytes = image_path.read_bytes()
        mime_type = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
        client = get_client()
        prompt = _vision_prompt(scene, visual_bible, expected, anatomy)
        last_raw = ""
        call_count = 0
        for parse_attempt in range(1, qc_json_parse_attempts() + 1):
            call_count += 1
            response = client.models.generate_content(
                model=model,
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(text=prompt),
                            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                        ],
                    )
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0,
                    max_output_tokens=2048,
                ),
            )
            last_raw = (response.text or "").strip()
            if raw_response_path:
                raw_response_path.parent.mkdir(parents=True, exist_ok=True)
                raw_response_path.write_text(last_raw, encoding="utf-8")
            try:
                data = parse_json_loose(last_raw)
            except json.JSONDecodeError:
                prompt = (
                    _vision_prompt(scene, visual_bible, expected, anatomy)
                    + "\n\nYour previous answer was invalid. Return ONLY one JSON object, no markdown."
                )
                continue
            if isinstance(data, dict):
                missing_symbolic = _missing_symbolic_qc_fields(data) if _is_symbolic_qc(scene, expected) else []
                if missing_symbolic:
                    prompt = (
                        _vision_prompt(scene, visual_bible, expected, anatomy)
                        + "\n\nYour previous JSON omitted required symbolic QC fields: "
                        + ", ".join(missing_symbolic)
                        + ". Return the complete schema with every field populated."
                    )
                    continue
                return {
                    "available": True,
                    "data": data,
                    "model": model,
                    "call_count": call_count,
                    "parse_attempt_count": parse_attempt,
                    "raw_response_path": _relative_raw_path(raw_response_path, expected),
                }
            prompt = (
                _vision_prompt(scene, visual_bible, expected, anatomy)
                + "\n\nYour previous answer was not a JSON object. Return ONLY one JSON object."
            )
        logger.warning("scene %d vision QC JSON parse failed after retries: %s", scene.scene_index, last_raw[:200])
        return {
            "available": True,
            "data": None,
            "model": model,
            "call_count": call_count,
            "parse_attempt_count": qc_json_parse_attempts(),
            "raw_response_path": _relative_raw_path(raw_response_path, expected),
            "error": "invalid JSON",
        }
    except Exception as exc:
        logger.warning("scene %d vision QC provider error %s: %s", scene.scene_index, type(exc).__name__, exc)
        return {
            "available": False,
            "error": str(exc)[:300],
            "model": model,
            "call_count": 0,
            "parse_attempt_count": 0,
            "raw_response_path": _relative_raw_path(raw_response_path, expected),
        }


def _result_from_vision(
    scene: Scene,
    image_path: Path,
    visual_bible: VisualBible,
    expected: dict[str, Any],
    anatomy: dict[str, str],
    checks: dict[str, bool],
    failures: list[str],
    score: float,
    basic_passed: bool,
    vision: dict[str, Any],
) -> SceneQCResult:
    data = vision.get("data") or {}
    model_passed = _as_bool(_pick(data, "model_qc_passed", "model_passed", "passed"), True)
    confidence = _as_float(_pick(data, "confidence", "qc_confidence", "score"), 1.0 if model_passed else 0.0)

    character_count = _as_int(_deep_pick(data, ("counts", "character_count"), ("character_count",)), 0)
    head_count = _as_int(_deep_pick(data, ("counts", "head_count"), ("head_count",)), 0)
    arm_count = _as_int(_deep_pick(data, ("counts", "arm_count"), ("visible_arm_count",), ("arm_count",)), 0)
    leg_count = _as_int(_deep_pick(data, ("counts", "leg_count"), ("visible_leg_count",), ("leg_count",)), 0)
    visible_foot_count = _as_int(
        _deep_pick(data, ("counts", "visible_foot_count"), ("visible_foot_count",), ("foot_count",)),
        0,
    )
    visible_hand_count = _as_int(
        _deep_pick(data, ("counts", "visible_hand_count"), ("visible_hand_count",)),
        0,
    )
    expected_count = _as_int(expected.get("expected_character_count"), 1)

    main_character_visible = _as_bool(
        _deep_pick(data, ("main_character_visible",), ("character", "main_character_visible")),
        character_count > 0 or head_count > 0,
    )
    has_extra_limbs = _as_bool(_deep_pick(data, ("anatomy", "has_extra_limbs"), ("has_extra_limbs",)), False)
    has_missing_limbs = _as_bool(_deep_pick(data, ("anatomy", "has_missing_limbs"), ("has_missing_limbs",)), False)
    has_duplicate_face = _as_bool(
        _deep_pick(data, ("anatomy", "has_duplicate_face"), ("has_duplicate_face",)),
        False,
    )
    has_text_or_watermark = _as_bool(
        _deep_pick(data, ("has_text_or_watermark",), ("visible_text_or_watermark",), ("text", "present")),
        False,
    )
    bad_crop = _as_bool(_deep_pick(data, ("crop", "bad_crop"), ("bad_crop",), ("has_bad_crop",)), False)
    lower_body_visible = _as_bool(
        _deep_pick(data, ("anatomy", "lower_body_visible"), ("lower_body_visible",)),
        True,
    )
    legs_visible = _as_bool(_deep_pick(data, ("anatomy", "legs_visible"), ("legs_visible",)), True)

    hairstyle_matches = _as_bool(
        _deep_pick(data, ("identity", "hairstyle_matches_spec"), ("hairstyle_matches_spec",)),
        True,
    )
    outfit_matches = _as_bool(
        _deep_pick(data, ("identity", "outfit_matches_spec"), ("outfit_matches_spec",)),
        True,
    )
    action_matches = _as_bool(
        _deep_pick(data, ("action", "scene_matches_requested_action"), ("scene_matches_requested_action",)),
        True,
    )
    action_severity = _normalize_action_mismatch_severity(
        _deep_pick(data, ("action", "action_mismatch_severity"), ("action_mismatch_severity",)),
        action_matches=action_matches,
    )
    severity_history = list(expected.get("action_mismatch_severity_history", [])) + [action_severity]

    vision_failures = _string_list(_pick(data, "failure_reasons", "failures", "issues"))
    failures.extend(vision_failures)
    symbolic = _symbolic_qc_analysis(
        scene,
        data,
        expected,
        character_count=character_count,
        model_passed=model_passed,
    )
    failures.extend(symbolic["failure_reasons"])
    override_reasons = _deterministic_overrides(
        failures,
        expected_count=expected_count,
        shot_type=anatomy["shot_type"],
        body_visibility=anatomy["body_visibility"],
        pose_type=anatomy["pose_type"],
        main_character_visible=main_character_visible,
        character_count=character_count,
        head_count=head_count,
        arm_count=arm_count,
        leg_count=leg_count,
        visible_foot_count=visible_foot_count,
        has_extra_limbs=has_extra_limbs,
        has_missing_limbs=has_missing_limbs,
        has_duplicate_face=has_duplicate_face,
        has_text_or_watermark=has_text_or_watermark,
        bad_crop=bad_crop,
        lower_body_visible=lower_body_visible,
        legs_visible=legs_visible,
    )

    identity_soft_fail = not hairstyle_matches or not outfit_matches
    action_major = (not action_matches) and action_severity == "major"
    action_soft_fail = (not action_matches) and action_severity == "minor"
    action_hard_fail = action_major

    previous_soft_failures = _previous_soft_failures(expected.get("soft_fail_streaks", {}))
    current_soft_failures = _current_soft_failures(
        hairstyle_matches=hairstyle_matches,
        outfit_matches=outfit_matches,
        action_soft_fail=action_soft_fail,
    )
    streaks = _updated_soft_fail_streaks(
        expected.get("soft_fail_streaks", {}),
        hairstyle_matches=hairstyle_matches,
        outfit_matches=outfit_matches,
        action_soft_fail=action_soft_fail,
    )
    repeated_reasons = _repeated_soft_fail_reasons(streaks)
    repeated_identity_failures = [field for field in current_soft_failures if field in previous_soft_failures]

    is_final_attempt = bool(expected.get("is_final_attempt"))
    final_soft_reasons = _soft_failure_reasons(
        hairstyle_matches=hairstyle_matches,
        outfit_matches=outfit_matches,
        action_soft_fail=action_soft_fail,
    )
    exhaustion_reasons: list[str] = []
    if is_final_attempt and final_soft_reasons and not repeated_reasons:
        exhaustion_reasons = [
            reason.replace("soft fail", "final-attempt hard fail") for reason in final_soft_reasons
        ]

    major_action_reasons = []
    if action_major:
        major_action_reasons.append("major action mismatch: requested scene action/location/object is completely wrong or missing")

    model_fail_reasons = []
    symbolic_soft_only = bool(
        symbolic["soft_reasons"] and not symbolic["hard_reasons"]
    )
    if not model_passed and not action_soft_fail and not symbolic_soft_only:
        model_fail_reasons.append("vision model marked scene as failed")

    hard_reasons = _unique_failures(
        override_reasons
        + symbolic["hard_reasons"]
        + major_action_reasons
        + repeated_reasons
        + exhaustion_reasons
        + model_fail_reasons
    )
    all_soft_reasons = _unique_failures(final_soft_reasons + symbolic["soft_reasons"])
    soft_reasons_for_failure = [] if hard_reasons else all_soft_reasons
    failures.extend(hard_reasons)
    failures.extend(soft_reasons_for_failure)

    if override_reasons:
        score -= 0.55
    if major_action_reasons:
        score -= 0.35
    if repeated_reasons:
        score -= 0.35
    if exhaustion_reasons:
        score -= 0.25
    if soft_reasons_for_failure:
        score -= 0.15
    if symbolic["hard_reasons"]:
        score -= 0.45
    if model_fail_reasons and not hard_reasons:
        score -= 0.25

    identity_hard_fail = bool(identity_soft_fail and (repeated_reasons or exhaustion_reasons))
    action_hard_fail = bool(action_hard_fail or (action_soft_fail and (repeated_reasons or exhaustion_reasons)))
    soft_fail_on_final = bool(
        is_final_attempt
        and all_soft_reasons
        and not repeated_reasons
        and not symbolic["repeated_reasons"]
    )
    loop_stop_reason = hard_reasons[0] if hard_reasons else ""
    loop_stop_reasons_all = list(hard_reasons)
    hard_priority = _hard_priority(
        override_reasons,
        symbolic["hard_reasons"],
        major_action_reasons,
        repeated_reasons,
        exhaustion_reasons,
    )

    checks.update(
        {
            "model_qc_passed": bool(model_passed),
            "main_character_visible": main_character_visible,
            "expected_character_count_ok": character_count in {0, expected_count}
            or character_count <= max(2, expected_count + 1),
            "no_extra_limbs": not has_extra_limbs and arm_count <= 2 and leg_count <= 2 and visible_foot_count <= 2,
            "no_missing_limbs": not has_missing_limbs,
            "no_duplicate_face": not has_duplicate_face and head_count <= max(1, expected_count),
            "no_text_or_watermark": not has_text_or_watermark,
            "crop_ok": not bad_crop,
            "hairstyle_matches_spec": hairstyle_matches,
            "outfit_matches_spec": outfit_matches,
            "scene_matches_requested_action": action_matches,
            "action_mismatch_severity_valid": action_severity in {"none", "minor", "major"},
            "symbolic_meaning_matches": symbolic["symbolic_meaning_matches"],
            "symbolic_visual_matches": symbolic["symbolic_visual_matches"],
            "metaphor_is_readable": symbolic["metaphor_is_readable"],
            "visual_identity_matches": symbolic["visual_identity_matches"],
            "adult_age_policy_matches": symbolic["adult_age_policy_matches"],
            "style_matches_symbolic_reel": symbolic["style_matches_symbolic_reel"],
            "subject_scale_matches": symbolic["subject_scale_matches"],
            "no_forbidden_symbolic_drift": not symbolic["forbidden_drift_detected"],
            "required_subjects_present": symbolic["required_subjects_present"],
            "requested_action_visible": symbolic["requested_action_visible"],
            "character_object_interaction_plausible": symbolic[
                "character_object_interaction_plausible"
            ],
            "emotional_meaning_readable": symbolic["emotional_meaning_readable"],
            "composition_clear": symbolic["composition_clear"],
            "style_consistent": symbolic["style_consistent"],
        }
    )

    return _result(
        scene,
        image_path,
        visual_bible,
        checks=checks,
        failures=failures,
        score=max(0.0, score),
        basic_qc_passed=basic_passed,
        model_qc_passed=bool(
            model_passed
            or ((action_soft_fail or symbolic_soft_only) and not hard_reasons)
        ),
        mode="vision",
        anatomy=anatomy,
        expected=expected,
        vision_available=True,
        vision_model=vision.get("model", ""),
        vision_qc_call_count=int(vision.get("call_count", 0)),
        qc_json_parse_attempt_count=int(vision.get("parse_attempt_count", 0)),
        raw_response_path=vision.get("raw_response_path", ""),
        confidence=confidence,
        model_passed=bool(model_passed),
        main_character_visible=main_character_visible,
        expected_character_count=expected_count,
        character_count=character_count,
        head_count=head_count,
        arm_count=arm_count,
        leg_count=leg_count,
        visible_foot_count=visible_foot_count,
        visible_hand_count=visible_hand_count,
        has_extra_limbs=has_extra_limbs or arm_count > 2 or leg_count > 2 or visible_foot_count > 2,
        has_missing_limbs=has_missing_limbs,
        has_duplicate_face=has_duplicate_face or head_count > max(1, expected_count),
        has_text_or_watermark=has_text_or_watermark,
        bad_crop=bad_crop,
        lower_body_visible=lower_body_visible,
        legs_visible=legs_visible,
        hairstyle_matches_spec=hairstyle_matches,
        outfit_matches_spec=outfit_matches,
        scene_matches_requested_action=action_matches,
        action_mismatch_severity=action_severity,
        action_mismatch_severity_history=severity_history,
        identity_soft_fail=identity_soft_fail,
        identity_hard_fail=identity_hard_fail,
        action_soft_fail=action_soft_fail,
        action_hard_fail=action_hard_fail,
        hairstyle_mismatch_streak=int(streaks["hairstyle"]),
        outfit_mismatch_streak=int(streaks["outfit"]),
        action_mismatch_streak=int(streaks["action"]),
        repeated_soft_fail_escalation_applied=bool(
            repeated_reasons or symbolic["repeated_reasons"]
        ),
        repeated_soft_fail_escalation_reasons=_unique_failures(
            repeated_reasons + symbolic["repeated_reasons"]
        ),
        stopped_retry_loop_early_due_to_repeated_soft_fail=bool(
            repeated_reasons or symbolic["repeated_reasons"]
        ),
        soft_fail_on_final_attempt=soft_fail_on_final,
        previous_attempt_identity_failures=previous_soft_failures,
        repeated_identity_failures=repeated_identity_failures,
        escalation_applied=bool(
            repeated_reasons
            or exhaustion_reasons
            or symbolic["repeated_reasons"]
            or symbolic["exhaustion_reasons"]
        ),
        escalation_reasons=_unique_failures(
            repeated_reasons
            + exhaustion_reasons
            + symbolic["repeated_reasons"]
            + symbolic["exhaustion_reasons"]
        ),
        anatomy_hard_fail=bool(override_reasons),
        deterministic_override_applied=bool(override_reasons),
        deterministic_override_reasons=override_reasons,
        final_attempt_hard_fail_reasons=hard_reasons,
        final_attempt_soft_fail_reasons=all_soft_reasons,
        loop_stop_reason=loop_stop_reason,
        loop_stop_reasons_all=loop_stop_reasons_all,
        hard_fail_priority_reason=hard_priority,
        symbolic_qc_passed=symbolic["passed"],
        symbolic_qc_attempts=int(expected.get("attempt", scene.attempt_count or 0)),
        symbolic_qc_failure_reasons=symbolic["failure_reasons"],
        symbolic_qc_last_failure_reason=(symbolic["failure_reasons"][-1] if symbolic["failure_reasons"] else ""),
        symbolic_qc_repaired_prompt_used=bool(expected.get("repaired_prompt_used")),
        symbolic_qc_final_status=symbolic["final_status"],
        symbolic_meaning_matches=symbolic["symbolic_meaning_matches"],
        symbolic_visual_matches=symbolic["symbolic_visual_matches"],
        metaphor_is_readable=symbolic["metaphor_is_readable"],
        visual_identity_matches=symbolic["visual_identity_matches"],
        adult_age_policy_matches=symbolic["adult_age_policy_matches"],
        style_matches_symbolic_reel=symbolic["style_matches_symbolic_reel"],
        subject_scale_matches=symbolic["subject_scale_matches"],
        forbidden_drift_detected=symbolic["forbidden_drift_detected"],
        forbidden_drift_types=symbolic["forbidden_drift_types"],
        symbolic_qc_hard_fail_reasons=symbolic["hard_reasons"],
        symbolic_qc_soft_fail_reasons=symbolic["soft_reasons"],
        symbolic_soft_fail_streaks=symbolic["soft_fail_streaks"],
        required_subjects_present=symbolic["required_subjects_present"],
        requested_action_visible=symbolic["requested_action_visible"],
        character_object_interaction_plausible=symbolic[
            "character_object_interaction_plausible"
        ],
        emotional_meaning_readable=symbolic["emotional_meaning_readable"],
        composition_clear=symbolic["composition_clear"],
        style_consistent=symbolic["style_consistent"],
        object_ambiguity_severity=symbolic["object_ambiguity_severity"],
    )


def _symbolic_qc_analysis(
    scene: Scene,
    data: dict[str, Any],
    expected: dict[str, Any],
    *,
    character_count: int,
    model_passed: bool,
) -> dict[str, Any]:
    if not _is_symbolic_qc(scene, expected):
        return {
            "passed": False,
            "final_status": "not_applicable",
            "failure_reasons": [],
            "hard_reasons": [],
            "soft_reasons": [],
            "repeated_reasons": [],
            "exhaustion_reasons": [],
            "soft_fail_streaks": {},
            "symbolic_meaning_matches": True,
            "symbolic_visual_matches": True,
            "metaphor_is_readable": True,
            "visual_identity_matches": True,
            "adult_age_policy_matches": True,
            "style_matches_symbolic_reel": True,
            "subject_scale_matches": True,
            "forbidden_drift_detected": False,
            "forbidden_drift_types": [],
            "required_subjects_present": True,
            "requested_action_visible": True,
            "character_object_interaction_plausible": True,
            "emotional_meaning_readable": True,
            "composition_clear": True,
            "style_consistent": True,
            "object_ambiguity_severity": "none",
        }

    def value(name: str) -> Any:
        return _deep_pick(data, ("symbolic", name), (name,))

    meaning_matches = _as_bool(value("symbolic_meaning_matches"), True)
    visual_matches = _as_bool(value("symbolic_visual_matches"), True)
    required_subjects_present = _as_bool(value("required_subjects_present"), visual_matches)
    metaphor_readable = _as_bool(value("metaphor_is_readable"), True)
    identity_matches = _as_bool(value("visual_identity_matches"), True)
    age_matches = _as_bool(value("adult_age_policy_matches"), True)
    style_matches = _as_bool(value("style_matches_symbolic_reel"), True)
    scale_matches = _as_bool(value("subject_scale_matches"), True)
    palette_matches = _as_bool(value("palette_matches"), True)
    line_style_matches = _as_bool(value("line_style_matches"), True)
    requested_action_visible = _as_bool(value("requested_action_visible"), visual_matches)
    interaction_plausible = _as_bool(
        value("character_object_interaction_plausible"),
        True,
    )
    emotional_meaning_readable = _as_bool(
        value("emotional_meaning_readable"),
        meaning_matches,
    )
    composition_clear = _as_bool(value("composition_clear"), True)
    style_consistent = _as_bool(value("style_consistent"), style_matches)
    object_ambiguity = str(value("object_ambiguity_severity") or "none").strip().lower()
    if object_ambiguity not in {"none", "minor", "major"}:
        object_ambiguity = "minor"

    drift_types = [_normalize_symbolic_drift_type(item) for item in _string_list(value("forbidden_drift_types"))]
    drift_flags = {
        "child": _as_bool(value("child_detected"), False),
        "medical_mask": _as_bool(value("medical_mask_detected"), False),
        "ghost": _as_bool(value("ghost_detected"), False),
        "monster": _as_bool(value("monster_detected"), False),
        "blob_creature": _as_bool(value("blob_creature_detected"), False),
        "horror": _as_bool(value("horror_imagery_detected"), False),
        "photorealistic": _as_bool(value("photorealistic_figure_detected"), False),
    }
    drift_types.extend(name for name, detected in drift_flags.items() if detected)
    drift_types = _unique_failures(drift_types)
    forbidden_drift = _as_bool(value("forbidden_drift_detected"), bool(drift_types)) or bool(drift_types)

    expected_subjects = list(
        expected.get("symbolic_qc_expected_subjects")
        or scene.symbolic_qc_expected_subjects
    )
    expected_subject_text = " ".join(expected_subjects).lower()
    crowd_visible = _as_bool(value("crowd_visible"), character_count >= 2)
    comparison_symbol_visible = _as_bool(value("comparison_symbols_present"), False)
    effort_symbol_visible = _as_bool(
        value("effort_or_carrying_symbol_visible"),
        visual_matches,
    )
    if any(term in expected_subject_text for term in ("crowd", "group")) and not crowd_visible:
        required_subjects_present = False
    if "comparison symbol" in expected_subject_text and character_count < 2 and not comparison_symbol_visible:
        required_subjects_present = False
    if "effort or carrying symbol" in expected_subject_text and not effort_symbol_visible:
        required_subjects_present = False

    hard_reasons: list[str] = []
    soft_reasons: list[str] = []
    if not meaning_matches:
        hard_reasons.append("semantic_symbol_mismatch")
    if not emotional_meaning_readable:
        hard_reasons.append("semantic_symbol_mismatch")
    if not visual_matches or not required_subjects_present:
        hard_reasons.append("required_subject_missing")
    if not requested_action_visible:
        hard_reasons.append("requested_action_missing")
    if not identity_matches:
        hard_reasons.append("visual_identity_drift")
    if not age_matches:
        hard_reasons.append("age_drift")
    if not metaphor_readable:
        soft_reasons.append("metaphor_unreadable")
    if not palette_matches:
        soft_reasons.append("palette_drift")
    if not line_style_matches:
        soft_reasons.append("line_style_drift")
    if not scale_matches:
        soft_reasons.append("composition_scale_drift")
    if not interaction_plausible:
        soft_reasons.append("interaction_plausibility_drift")
    if not composition_clear:
        soft_reasons.append("composition_clarity_drift")
    if not style_consistent:
        soft_reasons.append("style_consistency_drift")
    if object_ambiguity == "minor":
        soft_reasons.append("minor_object_ambiguity")
    elif object_ambiguity == "major":
        hard_reasons.append("symbolic_object_unreadable")

    for drift_type in drift_types:
        if drift_type == "child":
            hard_reasons.append("age_drift")
        elif drift_type == "medical_mask":
            hard_reasons.append("medical_mask_drift")
        elif drift_type in {"ghost", "monster", "blob_creature", "creature"}:
            hard_reasons.append("creature_drift")
        elif drift_type == "horror":
            hard_reasons.append("horror_drift")
        elif drift_type == "photorealistic":
            hard_reasons.append("photorealistic_drift")
        else:
            hard_reasons.append("visual_identity_drift")
    if not style_matches and palette_matches and line_style_matches and not drift_types:
        hard_reasons.append("visual_identity_drift")
    if forbidden_drift and not drift_types:
        hard_reasons.append("visual_identity_drift")
    if not model_passed and not hard_reasons and not soft_reasons:
        hard_reasons.append("semantic_symbol_mismatch")

    hard_reasons = _unique_failures(hard_reasons)
    soft_reasons = _unique_failures(soft_reasons)
    previous_streaks = dict(expected.get("symbolic_soft_fail_streaks", {}))
    soft_fail_streaks = {
        reason: int(previous_streaks.get(reason, 0)) + 1 if reason in soft_reasons else 0
        for reason in _SYMBOLIC_SOFT_FAILURES
    }
    repeated_reasons = [
        f"repeated_soft_fail_escalated:{reason}"
        for reason, streak in soft_fail_streaks.items()
        if streak >= _SOFT_STREAK_LIMIT
    ]
    repeated_codes = [
        reason
        for reason, streak in soft_fail_streaks.items()
        if streak >= _SOFT_STREAK_LIMIT
    ]
    exhaustion_reasons: list[str] = []
    if bool(expected.get("is_final_attempt")):
        exhaustion_reasons = [
            f"final_attempt_soft_fail_escalated:{reason}"
            for reason in soft_reasons
            if reason not in repeated_codes
        ]
    hard_reasons = _unique_failures(
        hard_reasons + repeated_codes + repeated_reasons + exhaustion_reasons
    )
    failure_reasons = _unique_failures(
        [reason for reason in hard_reasons if ":" not in reason] + soft_reasons
    )
    passed = not hard_reasons and not soft_reasons
    if passed:
        final_status = "passed"
    elif repeated_reasons or exhaustion_reasons:
        final_status = "soft_failed_escalated"
    elif hard_reasons:
        final_status = "hard_failed"
    else:
        final_status = "soft_failed"

    return {
        "passed": passed,
        "final_status": final_status,
        "failure_reasons": failure_reasons,
        "hard_reasons": hard_reasons,
        "soft_reasons": soft_reasons,
        "repeated_reasons": repeated_reasons,
        "exhaustion_reasons": exhaustion_reasons,
        "soft_fail_streaks": soft_fail_streaks,
        "symbolic_meaning_matches": meaning_matches,
        "symbolic_visual_matches": visual_matches and required_subjects_present,
        "metaphor_is_readable": metaphor_readable,
        "visual_identity_matches": identity_matches,
        "adult_age_policy_matches": age_matches,
        "style_matches_symbolic_reel": style_matches,
        "subject_scale_matches": scale_matches,
        "forbidden_drift_detected": forbidden_drift,
        "forbidden_drift_types": drift_types,
        "required_subjects_present": required_subjects_present,
        "requested_action_visible": requested_action_visible,
        "character_object_interaction_plausible": interaction_plausible,
        "emotional_meaning_readable": emotional_meaning_readable,
        "composition_clear": composition_clear,
        "style_consistent": style_consistent,
        "object_ambiguity_severity": object_ambiguity,
    }


def _normalize_symbolic_drift_type(value: Any) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    if "mask" in key:
        return "medical_mask"
    if "photo" in key or "realistic" in key or "cinematic_figure" in key:
        return "photorealistic"
    if "blob" in key:
        return "blob_creature"
    if "ghost" in key:
        return "ghost"
    if "monster" in key:
        return "monster"
    if "horror" in key:
        return "horror"
    if "child" in key or "kid" in key or "baby" in key:
        return "child"
    return key or "unknown"


def _deterministic_overrides(
    failures: list[str],
    *,
    expected_count: int,
    shot_type: str,
    body_visibility: str,
    pose_type: str,
    main_character_visible: bool,
    character_count: int,
    head_count: int,
    arm_count: int,
    leg_count: int,
    visible_foot_count: int,
    has_extra_limbs: bool,
    has_missing_limbs: bool,
    has_duplicate_face: bool,
    has_text_or_watermark: bool,
    bad_crop: bool,
    lower_body_visible: bool,
    legs_visible: bool,
) -> list[str]:
    reasons: list[str] = []
    if has_extra_limbs or arm_count > 2 or leg_count > 2 or visible_foot_count > 2:
        reasons.append("deterministic hard fail: extra or duplicated limbs detected")
    if has_duplicate_face or head_count > max(1, expected_count):
        reasons.append("deterministic hard fail: duplicate face or head detected")
    if has_text_or_watermark:
        reasons.append("deterministic hard fail: visible text or watermark detected")
    if main_character_visible and head_count == 0:
        reasons.append("deterministic hard fail: main character is visible but no head is readable")
    if character_count > max(2, expected_count + 1):
        reasons.append("deterministic hard fail: too many visible characters for one-character scene")
    if (
        body_visibility == "full_body"
        and pose_type not in {"sitting", "lying"}
        and (not lower_body_visible or not legs_visible)
    ):
        reasons.append("deterministic hard fail: full character expected but lower body or legs are missing")
    if body_visibility == "full_body" and main_character_visible and 0 < leg_count < 2:
        reasons.append("deterministic hard fail: full-body scene has fewer than two visible legs")
    if body_visibility == "full_body" and has_missing_limbs:
        reasons.append("deterministic hard fail: missing visible limbs reported in full-body scene")
    if pose_type == "sitting" and _failure_text_matches(failures, r"(tangled|duplicated|extra).*(limb|leg|foot|feet)"):
        reasons.append("deterministic hard fail: seated pose has tangled or duplicated limbs")
    if bad_crop and shot_type in {"medium", "medium_wide", "wide"}:
        reasons.append("deterministic hard fail: bad crop in medium or wide shot")
    if _failure_text_matches(failures, _HARD_FAILURE_RE):
        reasons.append("deterministic hard fail: model failure text reports anatomy/crop/text artifact")
    return _unique_failures(reasons)


def _updated_soft_fail_streaks(
    previous: dict[str, Any],
    *,
    hairstyle_matches: bool,
    outfit_matches: bool,
    action_soft_fail: bool,
) -> dict[str, int]:
    return {
        "hairstyle": 0 if hairstyle_matches else int(previous.get("hairstyle", 0)) + 1,
        "outfit": 0 if outfit_matches else int(previous.get("outfit", 0)) + 1,
        "action": int(previous.get("action", 0)) + 1 if action_soft_fail else 0,
    }


def _previous_soft_failures(previous: dict[str, Any]) -> list[str]:
    out = []
    if int(previous.get("hairstyle", 0)) > 0:
        out.append("hairstyle_matches_spec")
    if int(previous.get("outfit", 0)) > 0:
        out.append("outfit_matches_spec")
    if int(previous.get("action", 0)) > 0:
        out.append("scene_matches_requested_action")
    return out


def _current_soft_failures(
    *,
    hairstyle_matches: bool,
    outfit_matches: bool,
    action_soft_fail: bool,
) -> list[str]:
    out = []
    if not hairstyle_matches:
        out.append("hairstyle_matches_spec")
    if not outfit_matches:
        out.append("outfit_matches_spec")
    if action_soft_fail:
        out.append("scene_matches_requested_action")
    return out


def _soft_failure_reasons(
    *,
    hairstyle_matches: bool,
    outfit_matches: bool,
    action_soft_fail: bool,
) -> list[str]:
    reasons = []
    if not hairstyle_matches:
        reasons.append("identity soft fail: hairstyle does not match locked character spec")
    if not outfit_matches:
        reasons.append("identity soft fail: outfit does not match locked character spec")
    if action_soft_fail:
        reasons.append("action soft fail: minor action mismatch")
    return reasons


def _repeated_soft_fail_reasons(streaks: dict[str, int]) -> list[str]:
    reasons = []
    if streaks.get("hairstyle", 0) >= _SOFT_STREAK_LIMIT:
        reasons.append("repeated soft fail escalated: hairstyle mismatch persisted across attempts")
    if streaks.get("outfit", 0) >= _SOFT_STREAK_LIMIT:
        reasons.append("repeated soft fail escalated: outfit mismatch persisted across attempts")
    if streaks.get("action", 0) >= _SOFT_STREAK_LIMIT:
        reasons.append("repeated soft fail escalated: minor requested-action mismatch persisted across attempts")
    return reasons


def _vision_prompt(
    scene: Scene,
    visual_bible: VisualBible,
    expected: dict[str, Any],
    anatomy: dict[str, str],
) -> str:
    symbolic_qc = _is_symbolic_qc(scene, expected)
    character = visual_bible.character_specs[0] if visual_bible.character_specs else None
    identity = ""
    if character:
        identity = "; ".join(
            [
                character.gender_or_presentation,
                character.age_style,
                character.body_style,
                character.hair,
                character.face,
                character.outfit,
                character.palette,
            ]
        )
    schema = {
        "passed": True,
        "confidence": 0.0,
        "character_count": 1,
        "main_character_visible": True,
        "head_count": 1,
        "visible_arm_count": 2,
        "visible_hand_count": 2,
        "visible_leg_count": 2,
        "visible_foot_count": 2,
        "has_extra_limbs": False,
        "has_missing_limbs": False,
        "has_duplicate_face": False,
        "has_bad_crop": False,
        "has_text_or_watermark": False,
        "hairstyle_matches_spec": True,
        "outfit_matches_spec": True,
        "scene_matches_requested_action": True,
        "action_mismatch_severity": "none",
        "anatomy_notes": "",
        "identity_notes": "",
        "action_notes": "",
        "failure_reasons": [],
        "repair_prompt": "",
    }
    symbolic_context = ""
    if symbolic_qc:
        schema.update(
            {
                "symbolic_meaning_matches": True,
                "symbolic_visual_matches": True,
                "required_subjects_present": True,
                "requested_action_visible": True,
                "character_object_interaction_plausible": True,
                "emotional_meaning_readable": True,
                "composition_clear": True,
                "style_consistent": True,
                "object_ambiguity_severity": "none",
                "metaphor_is_readable": True,
                "visual_identity_matches": True,
                "adult_age_policy_matches": True,
                "style_matches_symbolic_reel": True,
                "subject_scale_matches": True,
                "palette_matches": True,
                "line_style_matches": True,
                "forbidden_drift_detected": False,
                "forbidden_drift_types": [],
                "child_detected": False,
                "medical_mask_detected": False,
                "ghost_detected": False,
                "monster_detected": False,
                "blob_creature_detected": False,
                "horror_imagery_detected": False,
                "photorealistic_figure_detected": False,
                "crowd_visible": False,
                "comparison_symbols_present": False,
                "effort_or_carrying_symbol_visible": False,
                "symbolic_notes": "",
            }
        )
        expected_subjects = list(
            expected.get("symbolic_qc_expected_subjects")
            or scene.symbolic_qc_expected_subjects
        )
        symbolic_context = (
            "\nSYMBOLIC REEL SEMANTIC QC: inspect the actual attached image, not "
            "the prompt text alone. Evaluate every symbolic field in the schema.\n"
            f"Scene meaning: {scene.scene_meaning}\n"
            f"Required symbolic visual: {scene.symbolic_visual}\n"
            f"Emotional metaphor: {scene.emotional_metaphor}\n"
            f"Required subjects: {json.dumps(expected_subjects, ensure_ascii=False)}\n"
            f"Visual identity id: {scene.visual_identity_id}\n"
            f"Cast archetype: {scene.cast_archetype}\n"
            f"Age policy: {scene.age_policy}\n"
            f"Palette id: {scene.palette_id}\n"
            f"Line style id: {scene.line_style_id}\n"
            f"Subject scale profile: {scene.subject_scale_profile}\n"
            f"Selected semantic intent: {scene.semantic_intent}\n"
            f"Selected character setup: {scene.character_archetype}; expected count: {scene.character_count}\n"
            f"Selected action: {scene.primary_action}\n"
            f"Selected primary object: {scene.primary_object}; secondary object: {scene.secondary_object}\n"
            f"Selected environment: {scene.environment}\n"
            f"Selected composition: {scene.composition_pattern}; framing: {scene.framing}\n"
            "Hard symbolic failures: missing required subject, child under the "
            "adult policy, medical mask, ghost, monster, blob creature, horror "
            "imagery, photorealistic/cinematic figure, unreadable scene meaning, "
            "or global visual identity drift. A requested action that is absent "
            "or opposite is hard. Soft-only failures include minor object "
            "ambiguity, imperfect but plausible interaction, slight composition "
            "drift, unreadable metaphor, palette drift, line-style drift, or "
            "subject-scale drift. Judge whether the symbolic relationship reads; "
            "do not demand photorealistic object accuracy. "
            "For soft-only failures keep passed=true; use passed=false for hard "
            "failures. Record forbidden drift types with structured labels.\n"
            "Concrete rules: lonely-in-a-crowd requires one isolated adult plus "
            "a visible group; comparison requires two people or an unmistakable "
            "comparison symbol; looking okay while hurt must not become illness "
            "or a medical mask; sadness at night must not become a ghost, monster, "
            "or horror scene; unseen effort needs a concrete effort/carrying symbol.\n"
        )
    return (
        "Review the attached generated illustration for Tella scene QC. "
        "Return only valid JSON matching this schema. Do not include markdown.\n"
        f"Schema:\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        f"Expected aspect: {expected.get('aspect', '9:16')}\n"
        f"Expected character count: {expected.get('expected_character_count', 1)}\n"
        f"Shot type: {anatomy['shot_type']}\n"
        f"Body visibility: {anatomy['body_visibility']}\n"
        f"Pose type: {anatomy['pose_type']}\n"
        f"Anatomy expectation: {anatomy['anatomy_expectation']}\n"
        f"Locked character identity: {identity}\n"
        f"Scene title: {scene.title}\n"
        f"Scene narration: {scene.voice_script}\n"
        f"Scene action/prompt: {scene.prompt_used or scene.image_prompt}\n"
        f"{symbolic_context}"
        "Use action_mismatch_severity='none' when the action is correct, 'minor' for a usable but slightly off action, "
        "and 'major' when the main action, location, or central requested object is completely wrong or missing. "
        "Use passed=false for hard failures only; for a minor action mismatch only, keep passed=true and set "
        "action_mismatch_severity='minor'. "
        "Do not infer severity from prose alone; set the structured severity field. "
        "Hard anatomy issues include extra limbs, duplicate face/head, visible text/watermark, missing readable head, "
        "bad medium/wide crop, broken body, or duplicated feet/legs."
    )


def _is_symbolic_qc(scene: Scene, expected: dict[str, Any]) -> bool:
    return bool(
        expected.get("theme") == "minimalist_symbolic_reel"
        or scene.visual_mode == "symbolic_listicle"
        or scene.visual_identity_id.startswith("symbolic_")
    )


def _missing_symbolic_qc_fields(data: dict[str, Any]) -> list[str]:
    missing = []
    for field in _SYMBOLIC_REQUIRED_QC_FIELDS:
        if _deep_pick(data, ("symbolic", field), (field,)) is None:
            missing.append(field)
    return missing


def _symbolic_repair_prompt(
    scene: Scene,
    base_prompt: str,
    failure_reasons: list[str],
) -> str:
    reasons = _unique_failures(failure_reasons)
    corrections: list[str] = []
    if any("semantic_symbol_mismatch" in reason for reason in reasons):
        corrections.append(
            f"make this scene meaning immediately recognizable: {scene.scene_meaning}"
        )
    if any("required_subject_missing" in reason for reason in reasons):
        required = "; ".join(scene.symbolic_qc_expected_subjects)
        corrections.append(
            "show every required subject clearly and separately: "
            + (required or scene.symbolic_visual)
        )
    if any("age_drift" in reason for reason in reasons):
        corrections.append(
            "use an ordinary adult aged 22-35 with adult proportions; no child or childlike body"
        )
    if any("medical_mask_drift" in reason for reason in reasons):
        corrections.append(
            "show an ordinary unobstructed adult face; no medical mask, illness, hospital, or medical symbolism"
        )
    if any(
        marker in reason
        for reason in reasons
        for marker in ("creature_drift", "horror_drift")
    ):
        corrections.append(
            "use an ordinary adult or gentle concrete emotional symbol; no supernatural creature, ghost, monster, blob, or horror imagery"
        )
    if any("photorealistic_drift" in reason for reason in reasons):
        corrections.append(
            "return to a flat minimalist hand-drawn doodle; no photorealism or cinematic figure"
        )
    if any("visual_identity_drift" in reason for reason in reasons):
        corrections.append(
            f"match visual identity {scene.visual_identity_id}, palette {scene.palette_id}, and line style {scene.line_style_id}"
        )
    if any("metaphor_unreadable" in reason for reason in reasons):
        corrections.append(
            f"use one concrete readable symbol for the metaphor: {scene.symbolic_visual}; remove unrelated abstractions"
        )
    if any("requested_action_missing" in reason for reason in reasons):
        corrections.append(
            f"make the selected action clearly visible: {scene.primary_action}"
        )
    if any("interaction_plausibility_drift" in reason for reason in reasons):
        corrections.append(
            "show a clear physical relationship between the person, action, and symbolic object"
        )
    if any("minor_object_ambiguity" in reason for reason in reasons):
        corrections.append(
            f"clarify the simplified symbolic object while keeping it stylized: {scene.primary_object}"
        )
    if any("composition_clarity_drift" in reason for reason in reasons):
        corrections.append(
            f"simplify the layout and preserve this composition: {scene.composition_pattern}"
        )
    if any("style_consistency_drift" in reason for reason in reasons):
        corrections.append(
            "restore the same soft brown pencil lines and muted taupe illustration style"
        )
    if any("palette_drift" in reason for reason in reasons):
        corrections.append(
            f"use only the limited muted earthy palette {scene.palette_id}"
        )
    if any("line_style_drift" in reason for reason in reasons):
        corrections.append(
            f"restore the consistent soft rough pencil line feel {scene.line_style_id}"
        )
    if any("composition_scale_drift" in reason for reason in reasons):
        corrections.append(
            f"follow subject scale {scene.subject_scale_profile}; keep the subject clearly readable with generous negative space"
        )
    if not corrections:
        corrections.append(
            "make the requested symbolic subject concrete, readable, and faithful to the shared visual identity"
        )
    return (
        "SYMBOLIC QC REPAIR. Mandatory corrections: "
        + "; ".join(corrections)
        + ". Preserve the original scene meaning and low-detail symbolic style. "
        + "Revised source prompt: "
        + base_prompt
    )


def _vision_model() -> str:
    return (
        os.environ.get("TELLA_VISION_QC_MODEL")
        or os.environ.get("TELLA_SCENE_QC_MODEL")
        or os.environ.get("GEMINI_MODEL")
        or _DEFAULT_VISION_MODEL
    ).strip()


def _has_gemini_key() -> bool:
    return bool(
        (os.environ.get("GEMINI_API_KEYS") or "").strip()
        or (os.environ.get("GEMINI_API_KEY") or "").strip()
        or (os.environ.get("GOOGLE_API_KEY") or "").strip()
    )


def _raw_response_path(scene: Scene, expected: dict[str, Any]) -> Path | None:
    job_dir = expected.get("job_dir")
    if not job_dir:
        return None
    attempt = int(expected.get("attempt", scene.attempt_count or 0))
    return Path(job_dir) / "qc" / f"scene_{scene.scene_index:02d}_attempt_{attempt}_qc_raw.txt"


def _relative_raw_path(raw_path: Path | None, expected: dict[str, Any]) -> str:
    if not raw_path:
        return ""
    job_dir = expected.get("job_dir")
    if not job_dir:
        return str(raw_path)
    try:
        return str(raw_path.relative_to(Path(job_dir)))
    except ValueError:
        return str(raw_path)


def _remaining_attempts(expected: dict[str, Any], scene: Scene) -> int:
    attempt = int(expected.get("attempt", scene.attempt_count or 0))
    limit = int(expected.get("max_attempts_allowed", expected.get("attempt_limit", 0)) or 0)
    return max(0, limit - attempt) if limit else 0


def _aspect_ok(width: int, height: int, aspect: str) -> bool:
    if width <= 0 or height <= 0:
        return False
    ratio = width / height
    expected = (16 / 9) if aspect == "16:9" else (9 / 16)
    return abs(ratio - expected) < 0.08


def _deep_pick(data: dict[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        current: Any = data
        for key in path:
            if not isinstance(current, dict) or key not in current:
                current = None
                break
            current = current[key]
        if current is not None:
            return current
    return None


def _pick(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lower = value.strip().lower()
        if lower in {"true", "yes", "1", "pass", "passed"}:
            return True
        if lower in {"false", "no", "0", "fail", "failed"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _as_int(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        out = []
        for item in value:
            if item is not None and str(item).strip():
                out.append(str(item).strip())
        return out
    return [str(value)]


def _normalize_action_mismatch_severity(value: Any, *, action_matches: bool) -> str:
    raw = str(value or "").strip().lower()
    if action_matches:
        return "none"
    if raw == "major":
        return "major"
    return "minor"


def _failure_text_matches(failures: list[str], pattern: str | re.Pattern[str]) -> bool:
    text = " ; ".join(failures)
    return bool(re.search(pattern, text) if isinstance(pattern, str) else pattern.search(text))


def _unique_failures(failures: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for failure in failures:
        text = str(failure).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _hard_priority(*groups: list[str]) -> str:
    for group in groups:
        if group:
            return group[0][:300]
    return ""


__all__ = [
    "anatomy_prompt_hints",
    "apply_qc_result_to_scene",
    "evaluate_scene_image",
    "image_hash",
    "infer_scene_anatomy_expectations",
    "max_attempts",
    "qc_json_parse_attempts",
    "qc_mode",
    "rank_qc_attempt",
    "save_qc_result",
    "strict_visual_qc",
    "summarize_qc_attempts",
]
