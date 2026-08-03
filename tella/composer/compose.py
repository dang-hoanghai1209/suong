"""Compose one deterministic visual timeline for continuous narration."""
from __future__ import annotations

import logging

from tella.composer.timing import build_render_timing_plan
from tella.planner.models import TellaScenePlan
from tella.subtitles import sanitize_highlight_words, subtitle_text_for_style

logger = logging.getLogger("tella.composer.compose")


def compose_timing(
    plan: TellaScenePlan,
    *,
    transition_duration: float = 0.0,
) -> TellaScenePlan:
    """Apply the narration-authoritative timing contract to ``plan``.

    ``Scene.duration`` remains the non-overlapping output timeline slot.
    ``Scene.render_clip_duration`` includes the outgoing transition overlap,
    so subtracting all overlaps from the encoded clips yields exactly
    ``plan.total_duration``.
    """
    body_scenes = [scene for scene in plan.scenes if scene.kind == "scene"]
    if not body_scenes:
        plan.total_duration = 0.0
        plan.render_timing_contract = {}
        return plan

    for scene in body_scenes:
        if scene.audio_duration <= 0:
            logger.warning(
                "scene %d audio_duration=0; falling back to 6s weight",
                scene.scene_index,
            )
            scene.audio_duration = 6.0

    timing = build_render_timing_plan(
        [scene.audio_duration for scene in body_scenes],
        requested_duration=plan.requested_production_duration_seconds,
        narration_duration=plan.narration_duration,
        configured_transition_duration=transition_duration,
    )
    for index, scene in enumerate(body_scenes):
        scene.duration = timing.scene_timeline_durations[index]
        scene.render_clip_duration = timing.scene_clip_durations[index]
        scene.start = timing.scene_starts[index]

    plan.total_duration = timing.expected_final_duration
    plan.render_timing_contract = timing.metadata()
    plan.scene_timing_map = [
        {
            "scene_index": scene.scene_index,
            "start": scene.start,
            "duration": scene.duration,
            "timeline_duration": scene.duration,
            "render_clip_duration": scene.render_clip_duration,
            "outgoing_transition_overlap": round(
                scene.render_clip_duration - scene.duration, 6
            ),
        }
        for scene in body_scenes
    ]
    plan.subtitle_segments = [
        {
            "scene_index": scene.scene_index,
            "start": scene.start,
            "end": round(scene.start + scene.duration, 6),
            "text": subtitle_text_for_style(
                scene.voice_script,
                plan.subtitle_style,
            ).text,
            "highlight_words": sanitize_highlight_words(
                scene.subtitle_highlight_words,
                plan.subtitle_style,
            ),
        }
        for scene in body_scenes
    ]
    logger.info(
        "compose_timing: %d scenes, authority=%s total=%.2fs overlap=%.2fs",
        len(body_scenes),
        timing.authority,
        plan.total_duration,
        timing.total_transition_overlap,
    )
    return plan


__all__ = ["compose_timing"]
