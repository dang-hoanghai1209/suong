"""Pre-transport narration-duration eligibility for production recipes."""
from __future__ import annotations

import re

from tella.planner.models import TellaScenePlan


class ProductionNarrationDurationError(RuntimeError):
    """Raised before TTS transport when narration requires replanning."""

    def __init__(self, assessment: dict[str, object]):
        self.assessment = assessment
        status = str(assessment["planning_duration_status"])
        estimate = float(assessment["estimated_duration_seconds"])
        target = assessment["production_target_range_seconds"]
        super().__init__(
            f"production narration is {status}: estimated {estimate:.3f}s "
            f"for target range {target}; replan narration before synthesis"
        )


def production_narration_target_range(
    plan: TellaScenePlan,
) -> tuple[float, float] | None:
    """Read the target from validated recipe metadata without inventing one."""
    if len(plan.recipe_duration_range) != 2:
        return None
    lower, upper = (float(value) for value in plan.recipe_duration_range)
    if lower <= 0 or lower > upper:
        return None
    return lower, upper


def requires_production_narration_duration_validation(
    plan: TellaScenePlan,
) -> bool:
    """Gate only full emotional plans carrying an explicit recipe contract."""
    if (
        plan.theme != "minimalist_emotional"
        or plan.recipe_validation_status != "passed"
    ):
        return False
    target = production_narration_target_range(plan)
    if target is None or len(plan.recipe_scene_range) != 2:
        raise ValueError("validated production recipe has no valid duration/scene range")
    scene_min, scene_max = (int(value) for value in plan.recipe_scene_range)
    if scene_min <= 0 or scene_min > scene_max:
        raise ValueError("validated production recipe scene range is invalid")
    body_scene_count = sum(scene.kind == "scene" for scene in plan.scenes)
    requested = float(plan.requested_production_duration_seconds)
    return (
        scene_min <= body_scene_count <= scene_max
        and target[0] <= requested <= target[1]
    )


def narration_planning_diagnostic(
    text: str,
    requested_duration: float,
    *,
    language: str = "",
    provider: str = "",
    voice: str = "",
    transport_speed_multiplier: float | None = None,
    pause_cap_ms: int | None = None,
    production_target_range: tuple[float, float] | None = None,
) -> dict[str, object]:
    """Estimate raw and processed narration without claiming audio precision."""
    normalized = re.sub(r"\s+", " ", text).strip()
    spoken_units = len(normalized.split()) if normalized else 0
    language_code = (language or "").strip().lower().replace("_", "-")
    language_family = language_code.split("-", 1)[0] or "unknown"

    # Vietnamese whitespace units are predominantly syllable-like. Ranges
    # remain language-level because no provider/voice calibration exists yet.
    unit_rates = {
        "vi": (2.9, 3.4, 3.9),
        "en": (2.2, 2.7, 3.2),
    }
    slow_rate, central_rate, fast_rate = unit_rates.get(
        language_family,
        (2.0, 2.9, 3.8),
    )
    speed_applied = transport_speed_multiplier is not None
    modeled_speed = (
        max(0.25, min(4.0, float(transport_speed_multiplier)))
        if speed_applied
        else 1.0
    )
    slow_rate *= modeled_speed
    central_rate *= modeled_speed
    fast_rate *= modeled_speed

    sentence_boundaries = len(re.findall(r"[.!?\u2026]+", normalized))
    clause_boundaries = len(re.findall(r"[,;:]", normalized))
    raw_pause_seconds = (
        max(0, sentence_boundaries - 1) * 0.45
        + clause_boundaries * 0.18
    )
    pause_cap_applied = pause_cap_ms is not None
    if pause_cap_applied:
        pause_cap_seconds = max(0.08, int(pause_cap_ms) / 1000.0)
        processed_pause_seconds = (
            max(0, sentence_boundaries - 1) * min(0.45, pause_cap_seconds)
            + clause_boundaries * min(0.18, pause_cap_seconds)
        )
    else:
        processed_pause_seconds = raw_pause_seconds

    if spoken_units:
        raw_central = spoken_units / central_rate + raw_pause_seconds
        raw_range = (
            spoken_units / fast_rate + raw_pause_seconds,
            spoken_units / slow_rate + raw_pause_seconds,
        )
        processed_central = spoken_units / central_rate + processed_pause_seconds
        processed_range = (
            spoken_units / fast_rate + processed_pause_seconds,
            spoken_units / slow_rate + processed_pause_seconds,
        )
    else:
        raw_central = processed_central = 0.0
        raw_range = processed_range = (0.0, 0.0)

    requested = max(0.0, float(requested_duration))
    if production_target_range is not None:
        target_min, target_max = (float(value) for value in production_target_range)
        if target_min <= 0 or target_min > target_max:
            raise ValueError("production target range must be positive and ordered")
        target_basis = "plan_recipe_duration_range"
    else:
        tolerance = max(1.0, requested * (3.0 / 35.0)) if requested else 0.0
        target_min = max(0.0, requested - tolerance)
        target_max = requested + tolerance
        target_basis = "scaled_requested_duration_tolerance"

    if requested <= 0 or spoken_units == 0:
        status = "not_evaluated"
    elif processed_central < target_min:
        status = "likely_too_short"
    elif processed_central > target_max:
        status = "likely_too_long"
    else:
        status = "within_production_target"
    eligible = status == "within_production_target"

    return {
        "narration_text_characters": len(normalized),
        "spoken_unit_count": spoken_units,
        "spoken_unit_kind": (
            "vietnamese_syllable_like_whitespace_units"
            if language_family == "vi"
            else "unicode_whitespace_words"
        ),
        "language": language or "unknown",
        "provider": provider or "unknown",
        "voice": voice or "unknown",
        "transport_speed_multiplier": modeled_speed,
        "speed_applied_to_transport": speed_applied,
        "provider_voice_calibrated": False,
        "spoken_units_per_second_range": [
            round(slow_rate, 3),
            round(fast_rate, 3),
        ],
        "estimated_raw_duration_seconds": round(raw_central, 3),
        "estimated_raw_duration_range_seconds": [
            round(raw_range[0], 3),
            round(raw_range[1], 3),
        ],
        "estimated_duration_seconds": round(processed_central, 3),
        "estimated_duration_range_seconds": [
            round(processed_range[0], 3),
            round(processed_range[1], 3),
        ],
        "punctuation_metrics": {
            "sentence_boundaries": sentence_boundaries,
            "clause_boundaries": clause_boundaries,
        },
        "postprocess_assumption": {
            "pause_cap_ms": int(pause_cap_ms) if pause_cap_applied else None,
            "pause_cap_applied": pause_cap_applied,
            "estimated_raw_pause_seconds": round(raw_pause_seconds, 3),
            "estimated_processed_pause_seconds": round(processed_pause_seconds, 3),
        },
        "estimate_basis": "language_units_speed_and_punctuation_pause_model",
        "estimate_is_authoritative": False,
        "requested_duration_seconds": requested,
        "production_target_range_seconds": [
            round(target_min, 3),
            round(target_max, 3),
        ],
        "production_target_basis": target_basis,
        "planning_duration_status": status,
        "production_tts_eligible": eligible,
        "repair_action": "none" if eligible else "requires_narration_replan",
    }


__all__ = [
    "ProductionNarrationDurationError",
    "narration_planning_diagnostic",
    "production_narration_target_range",
    "requires_production_narration_duration_validation",
]
