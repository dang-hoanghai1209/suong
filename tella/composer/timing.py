"""Deterministic render-timeline planning for continuous narration."""
from __future__ import annotations

from dataclasses import dataclass


DEFAULT_TIMING_TOLERANCE_SECONDS = 0.15


def _allocate(total: float, weights: list[float]) -> list[float]:
    if total <= 0:
        raise ValueError("authoritative duration must be positive")
    if not weights:
        raise ValueError("at least one scene is required")
    normalized = [max(0.0, float(value)) for value in weights]
    if sum(normalized) <= 0:
        normalized = [1.0] * len(weights)
    weight_total = sum(normalized)
    allocated: list[float] = []
    running = 0.0
    for index, weight in enumerate(normalized):
        if index == len(normalized) - 1:
            value = total - running
        else:
            value = total * weight / weight_total
            running += value
        allocated.append(round(value, 6))
    return allocated


@dataclass(frozen=True)
class RenderTimingPlan:
    requested_duration: float
    narration_duration: float
    authoritative_duration: float
    authority: str
    configured_transition_duration: float
    effective_transition_duration: float
    scene_timeline_durations: tuple[float, ...]
    scene_clip_durations: tuple[float, ...]
    scene_starts: tuple[float, ...]
    transition_overlap_count: int
    total_transition_overlap: float
    expected_final_duration: float
    tolerance: float
    requested_duration_delta: float | None
    requested_duration_status: str

    def metadata(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "requested_duration_seconds": self.requested_duration,
            "narration_duration_seconds": self.narration_duration,
            "authoritative_duration_seconds": self.authoritative_duration,
            "duration_authority": self.authority,
            "configured_transition_duration_seconds": (
                self.configured_transition_duration
            ),
            "effective_transition_duration_seconds": (
                self.effective_transition_duration
            ),
            "transition_overlap_count": self.transition_overlap_count,
            "total_transition_overlap_seconds": self.total_transition_overlap,
            "scene_timeline_durations_seconds": list(
                self.scene_timeline_durations
            ),
            "scene_clip_durations_seconds": list(self.scene_clip_durations),
            "expected_final_timeline_duration_seconds": (
                self.expected_final_duration
            ),
            "timing_tolerance_seconds": self.tolerance,
            "requested_duration_delta_seconds": self.requested_duration_delta,
            "requested_duration_status": self.requested_duration_status,
            "actual_rendered_duration_seconds": None,
            "actual_duration_delta_seconds": None,
            "actual_duration_status": "not_evaluated",
            "actual_duration_failure_reason": "",
        }


def build_render_timing_plan(
    scene_weights: list[float],
    *,
    requested_duration: float = 0.0,
    narration_duration: float = 0.0,
    configured_transition_duration: float = 0.0,
    tolerance: float = DEFAULT_TIMING_TOLERANCE_SECONDS,
) -> RenderTimingPlan:
    """Build the single timing contract used by composition and rendering.

    Measured continuous narration is authoritative when present.  Otherwise
    an explicit requested production duration is authoritative.  The legacy
    fallback uses the sum of scene weights, preserving pre-contract plans.
    """
    if not scene_weights:
        raise ValueError("at least one scene is required")
    requested = max(0.0, float(requested_duration))
    narration = max(0.0, float(narration_duration))
    if narration > 0:
        authoritative = narration
        authority = "continuous_narration"
    elif requested > 0:
        authoritative = requested
        authority = "requested_production_duration"
    else:
        positive_total = sum(max(0.0, float(value)) for value in scene_weights)
        authoritative = positive_total or 6.0 * len(scene_weights)
        authority = "legacy_scene_duration_sum"

    timeline = _allocate(authoritative, scene_weights)
    configured = max(0.0, float(configured_transition_duration))
    if len(timeline) <= 1 or configured <= 0:
        effective = 0.0
    else:
        effective = min(configured, max(0.1, min(timeline) / 3.0))
    effective = round(effective, 6)
    overlap_count = max(0, len(timeline) - 1) if effective else 0
    clip_durations = [
        round(duration + (effective if index < overlap_count else 0.0), 6)
        for index, duration in enumerate(timeline)
    ]
    starts: list[float] = []
    cursor = 0.0
    for duration in timeline:
        starts.append(round(cursor, 6))
        cursor += duration
    expected = round(sum(clip_durations) - effective * overlap_count, 6)
    overlap_total = round(effective * overlap_count, 6)

    if requested <= 0:
        requested_delta = None
        requested_status = "not_specified"
    else:
        requested_delta = round(authoritative - requested, 6)
        requested_status = (
            "passed"
            if abs(requested_delta) <= tolerance
            else "differs_from_authoritative_narration"
        )

    return RenderTimingPlan(
        requested_duration=round(requested, 6),
        narration_duration=round(narration, 6),
        authoritative_duration=round(authoritative, 6),
        authority=authority,
        configured_transition_duration=round(configured, 6),
        effective_transition_duration=effective,
        scene_timeline_durations=tuple(timeline),
        scene_clip_durations=tuple(clip_durations),
        scene_starts=tuple(starts),
        transition_overlap_count=overlap_count,
        total_transition_overlap=overlap_total,
        expected_final_duration=expected,
        tolerance=float(tolerance),
        requested_duration_delta=requested_delta,
        requested_duration_status=requested_status,
    )


def validate_actual_render_duration(
    metadata: dict[str, object], actual_duration: float
) -> dict[str, object]:
    """Return auditable, fail-closed actual-duration metadata."""
    result = dict(metadata)
    expected = float(result["expected_final_timeline_duration_seconds"])
    tolerance = float(result["timing_tolerance_seconds"])
    actual = float(actual_duration)
    delta = round(actual - expected, 6)
    passed = abs(delta) <= tolerance
    result.update(
        {
            "actual_rendered_duration_seconds": round(actual, 6),
            "actual_duration_delta_seconds": delta,
            "actual_duration_status": "passed" if passed else "failed",
            "actual_duration_failure_reason": (
                ""
                if passed
                else (
                    f"actual render duration {actual:.3f}s differs from "
                    f"authoritative timeline {expected:.3f}s by "
                    f"{abs(delta):.3f}s (tolerance {tolerance:.3f}s)"
                )
            ),
        }
    )
    return result


__all__ = [
    "DEFAULT_TIMING_TOLERANCE_SECONDS",
    "RenderTimingPlan",
    "build_render_timing_plan",
    "validate_actual_render_duration",
]
