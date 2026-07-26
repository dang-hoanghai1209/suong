"""Deterministic timing allocation for 7–8 scene emotional shorts."""

from __future__ import annotations

import math

from .models import SceneTiming, SemanticBeat, _canonical_milliseconds


def allocate_durations(scene_count: int, target_duration_seconds: float) -> list[float]:
    if scene_count not in {7, 8}:
        raise ValueError("scene_count must be 7 or 8")
    if (
        isinstance(target_duration_seconds, bool)
        or not isinstance(target_duration_seconds, (int, float))
        or not math.isfinite(target_duration_seconds)
        or target_duration_seconds <= 0
    ):
        raise ValueError("target duration must be a finite positive number")
    total_ms = _canonical_milliseconds(target_duration_seconds)
    if total_ms < scene_count:
        raise ValueError(
            "target duration cannot allocate a positive millisecond duration to every scene"
        )
    base_ms, remainder = divmod(total_ms, scene_count)
    durations = [base_ms + (1 if index < remainder else 0) for index in range(scene_count)]
    return [value / 1000 for value in durations]


def build_scene_timings(beats: list[SemanticBeat]) -> list[SceneTiming]:
    cursor = 0.0
    timings: list[SceneTiming] = []
    for beat in beats:
        end = _canonical_milliseconds(cursor + beat.duration_seconds) / 1000
        timings.append(
            SceneTiming(
                scene_id=f"scene_{beat.order:02d}",
                order=beat.order,
                start_seconds=cursor,
                duration_seconds=beat.duration_seconds,
                end_seconds=end,
            )
        )
        cursor = end
    return timings
