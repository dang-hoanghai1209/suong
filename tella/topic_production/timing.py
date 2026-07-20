"""Deterministic timing allocation for 7–8 scene emotional shorts."""
from __future__ import annotations

from .models import SceneTiming, SemanticBeat


def allocate_durations(scene_count: int, target_duration_seconds: float) -> list[float]:
    if scene_count not in {7, 8}:
        raise ValueError("scene_count must be 7 or 8")
    if not 32.0 <= target_duration_seconds <= 38.0:
        raise ValueError("target duration must be between 32 and 38 seconds")
    total_ms = round(target_duration_seconds * 1000)
    base_ms, remainder = divmod(total_ms, scene_count)
    durations = [base_ms + (1 if index < remainder else 0) for index in range(scene_count)]
    if any(value < 3000 or value > 5000 for value in durations):
        raise ValueError("target duration cannot allocate every scene within 3–5 seconds")
    return [value / 1000 for value in durations]


def build_scene_timings(beats: list[SemanticBeat]) -> list[SceneTiming]:
    cursor = 0.0
    timings: list[SceneTiming] = []
    for beat in beats:
        end = round(cursor + beat.duration_seconds, 3)
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
