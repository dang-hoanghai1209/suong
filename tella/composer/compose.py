"""Compose timing for every scene in a plan.

After Phase 3 (media) + Phase 4 (TTS) populate ``image_filenames`` +
``audio_duration``, ``compose_timing`` walks the scenes and assigns
``start`` + ``duration`` per scene + sets the plan's ``total_duration``.

A scene's duration = audio_duration + tail buffer (0.4 s) so the visual
finishes resolving cleanly before the next scene cuts in.

The render layer reads these timing fields to drive the ffmpeg pipeline.
"""
from __future__ import annotations

import logging

from tella.planner.models import TellaScenePlan

logger = logging.getLogger("tella.composer.compose")

SCENE_TAIL_BUFFER = 0.4   # seconds of visual breathing room after audio ends


def compose_timing(plan: TellaScenePlan) -> TellaScenePlan:
    """Cursor-walk the scenes; set ``start`` + ``duration`` + plan total.

    Mutates the plan in place. Returns the same plan for chaining.

    Pre-requirement: every body scene already has ``audio_duration``
    populated (by :func:`tella.tts.synth_all.synthesize_all`).
    """
    body_scenes = [s for s in plan.scenes if s.kind == "scene"]
    if not body_scenes:
        plan.total_duration = 0.0
        return plan

    cursor = 0.0
    for scene in body_scenes:
        if scene.audio_duration <= 0:
            logger.warning(
                "scene %d audio_duration=0 — did TTS run? falling back to 6s",
                scene.scene_index,
            )
            scene.audio_duration = 6.0
        scene.duration = round(scene.audio_duration + SCENE_TAIL_BUFFER, 2)
        scene.start = round(cursor, 2)
        cursor = round(cursor + scene.duration, 2)

    plan.total_duration = round(cursor, 2)
    logger.info(
        "compose_timing: %d scenes, total=%.2fs",
        len(body_scenes), plan.total_duration,
    )
    return plan


__all__ = [
    "SCENE_TAIL_BUFFER",
    "compose_timing",
]
