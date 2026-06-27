"""Character + setting brief injection into scene image prompts.

When media_source == ai_image, the planner emits a top-level
``character_brief`` + ``setting_brief``. This module prepends them onto
every scene's ``image_prompt`` so FLUX renders the SAME character + place
across all scenes.

When media_source == stock_photo or stock_video, the briefs are ``None``
and we leave the per-scene prompts alone (Pexels content is random, no
character locking possible).
"""
from __future__ import annotations

import logging

from tella.planner.models import TellaScenePlan

logger = logging.getLogger("tella.planner.character_lock")


def apply_lock(plan: TellaScenePlan, *, style_suffix: str = "") -> TellaScenePlan:
    """Mutate ``plan`` in place: prepend identity + setting to each
    scene's ``image_prompt`` (ai_image mode only). Returns the same plan.

    Args:
        plan:          The plan to lock. Only ``ai_image`` mode is rewritten.
        style_suffix:  Theme-specific style tail (e.g. ", watercolor, golden
                       hour"). Appended after the action description so the
                       whole prompt reads: ``<identity> + <setting> + <scene
                       action> + <style suffix>``.
    """
    if plan.media_source != "ai_image":
        # Stock modes: nothing to lock — but we still tack on the style
        # suffix so any future swap to ai_image still has a hook.
        if style_suffix:
            for scene in plan.scenes:
                if scene.image_prompt and style_suffix not in scene.image_prompt:
                    scene.image_prompt = f"{scene.image_prompt.rstrip(', ')}{style_suffix}"
        return plan

    cb = plan.character_brief
    sb = plan.setting_brief
    if cb is None or sb is None:
        logger.warning(
            "ai_image mode but character_brief/setting_brief missing — "
            "leaving image_prompts as-is. Planner likely failed to emit briefs."
        )
        return plan

    identity = cb.identity.strip().rstrip(".,;")
    location = sb.location.strip().rstrip(".,;")
    setting_extras = []
    if sb.era and sb.era.lower() != "timeless":
        setting_extras.append(sb.era.strip())
    if sb.time_of_day:
        setting_extras.append(sb.time_of_day.strip())
    setting_tail = ", ".join(setting_extras)

    for scene in plan.scenes:
        action = (scene.image_prompt or "").strip().rstrip(".,;")
        parts = [identity, location]
        if setting_tail:
            parts.append(setting_tail)
        if action:
            parts.append(action)
        prompt = ", ".join(parts)
        if style_suffix:
            prompt = f"{prompt}{style_suffix}"
        scene.image_prompt = prompt

    logger.info(
        "character_lock applied to %d scenes (identity=%r, location=%r)",
        len(plan.scenes), identity[:60], location[:60],
    )
    return plan


__all__ = [
    "apply_lock",
]
