"""Character + setting brief injection into scene image prompts.

When media_source == ai_image, the planner emits a ``characters`` cast +
``setting_brief``. Each scene names which cast members appear in it; this
module prepends those characters' identities (plus the locked setting) onto
the scene's ``image_prompt`` so the image model renders the SAME subjects +
place across all scenes — a two-character fable keeps both characters
consistent instead of collapsing into one.

When media_source == stock_photo or stock_video, the cast is empty and we
leave the per-scene prompts alone (stock content is random, no character
locking possible).
"""
from __future__ import annotations

import logging

from tella.planner.models import TellaScenePlan

logger = logging.getLogger("tella.planner.character_lock")

_MINIMALIST_CHARACTER_TEMPLATE = (
    "one small simple girl character, short straight black bob haircut ending "
    "at the chin, simple symmetrical bob shape, no long loose hair strands, "
    "small round face, dot eyes only, tiny nose, tiny neutral mouth, mustard "
    "yellow simple triangular dress, soft rust sleeves, stick-like simple "
    "legs, simple mitten-like hands, simple doodle proportions, exactly one "
    "head, full body visible"
)

_MINIMALIST_NEGATIVE_LOCK = (
    "single simple safe pose, no twisted body, head and torso face the same "
    "direction, full body visible, head fully visible, feet fully visible, "
    "character within central safe area, keep bottom 25 percent empty for "
    "captions, do not make the character too large, character occupies about "
    "35-45 percent of frame height, generous negative space, complete emotional "
    "room illustration scene, not only a character portrait, layered composition "
    "with foreground curtain edge or soft shadow, middle ground young woman, "
    "background bed, window with thin curtains, bedside table, warm table lamp, "
    "books or folded blanket, soft wall shadows, subtle dust or memory particles, "
    "muted floor and wall shapes, exactly one head, no second head, no duplicate face, no second "
    "character, no face on heart, no face on object, no deformed anatomy, no "
    "extra limbs, no cropped body, arms stay simple and relaxed, no arms "
    "crossing the torso, hands stay away from chest and torso, mitten hands "
    "only, no finger detail, no realistic body, "
    "no anime face, no detailed eyes, no eyelashes, no complex hair shine, "
    "no close-up face, no cropped head, no cropped feet, no large character "
    "filling the frame"
)


def _minimalist_emotional_prefix(plan: TellaScenePlan) -> str:
    """Repeat a deliberately simple character template for this style.

    Text-only image generation has no identity memory. For this theme the
    product goal is not photoreal identity, but reduced hair/face/outfit
    drift, so every scene repeats the same simplified character constraints.
    """
    location = (
        "quiet warm taupe bedroom, bed on one side, window with thin curtains, "
        "small bedside table, warm table lamp, a few books or folded blanket, "
        "soft shadows on the wall, subtle dust or memory particles near the window"
    )
    if plan.setting_brief and plan.setting_brief.location:
        location = plan.setting_brief.location.strip().rstrip(".,;")
    return f"{_MINIMALIST_CHARACTER_TEMPLATE}, {location}"


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
    is_minimalist = plan.theme == "minimalist_emotional"

    if plan.media_source != "ai_image":
        # Stock modes: nothing to lock — but we still tack on the style
        # suffix so any future swap to ai_image still has a hook.
        if style_suffix:
            for scene in plan.scenes:
                if scene.image_prompt and style_suffix not in scene.image_prompt:
                    scene.image_prompt = f"{scene.image_prompt.rstrip(', ')}{style_suffix}"
        return plan

    # Build the cast: prefer the multi-character list; fall back to the
    # single legacy character_brief so older plans still lock.
    cast = list(plan.characters)
    if not cast and plan.character_brief is not None:
        cast = [plan.character_brief]

    sb = plan.setting_brief
    if not cast or sb is None:
        if not is_minimalist:
            logger.warning(
                "ai_image mode but cast/setting_brief missing — leaving "
                "image_prompts as-is. Planner likely failed to emit briefs."
            )
            return plan
        logger.warning(
            "minimalist_emotional: cast/setting missing — applying fallback "
            "character template to image_prompts."
        )
        for scene in plan.scenes:
            action = (scene.image_prompt or "").strip().rstrip(".,;")
            prompt = ", ".join(
                p for p in (
                    _minimalist_emotional_prefix(plan),
                    action,
                    _MINIMALIST_NEGATIVE_LOCK,
                )
                if p
            )
            if style_suffix:
                prompt = f"{prompt}{style_suffix}"
            scene.image_prompt = prompt
        return plan

    # name (lowercased) → identity string. Unnamed characters fall back to
    # positional keys so a scene can still reference them.
    by_name: dict[str, str] = {}
    for i, c in enumerate(cast):
        identity = c.identity.strip().rstrip(".,;")
        key = (c.name or "").strip().lower()
        if key:
            by_name[key] = identity
        by_name.setdefault(f"__pos_{i}", identity)
    all_identities = [c.identity.strip().rstrip(".,;") for c in cast]

    # FLUX only understands English. If the planner leaked non-Latin text
    # (e.g. Vietnamese) into an identity or the location, the image model
    # renders random, inconsistent output — warn loudly so it's visible.
    def _looks_non_english(s: str) -> bool:
        return any(ord(ch) > 0x024F for ch in s)  # beyond Latin Extended-A/B

    suspect = [i for i in all_identities if _looks_non_english(i)]
    if _looks_non_english(sb.location):
        suspect.append(sb.location)
    if suspect:
        logger.warning(
            "character_lock: non-English visual prompt detected (FLUX needs "
            "English) — images may be inconsistent. Offending text: %r",
            suspect[0][:80],
        )

    location = sb.location.strip().rstrip(".,;")
    setting_extras = []
    if sb.era and sb.era.lower() != "timeless":
        setting_extras.append(sb.era.strip())
    if sb.time_of_day:
        setting_extras.append(sb.time_of_day.strip())
    setting_tail = ", ".join(setting_extras)

    for scene in plan.scenes:
        action = (scene.image_prompt or "").strip().rstrip(".,;")

        # Resolve which characters appear in this scene. If the planner named
        # them, use those; if it named none but the story has a single
        # character, default to that one; otherwise leave the scene
        # character-free (pure scenery).
        wanted = [n.strip().lower() for n in scene.character_names if n.strip()]
        identities: list[str] = []
        for n in wanted:
            if n in by_name:
                identities.append(by_name[n])
        if not identities:
            if not scene.character_names and len(all_identities) == 1:
                identities = list(all_identities)
            # else: scenery shot — no character prepended

        parts = [*identities, location]
        if setting_tail:
            parts.append(setting_tail)
        if action:
            parts.append(action)
        if is_minimalist:
            # The minimalist emotional theme intentionally repeats the same
            # simple template and negative drift terms in every prompt.
            parts = [
                _minimalist_emotional_prefix(plan),
                action,
                _MINIMALIST_NEGATIVE_LOCK,
            ]
        prompt = ", ".join(p for p in parts if p)
        if style_suffix:
            prompt = f"{prompt}{style_suffix}"
        scene.image_prompt = prompt

    logger.info(
        "character_lock applied to %d scenes (cast=%d: %s; location=%r)",
        len(plan.scenes), len(cast),
        ", ".join((c.name or "?") for c in cast)[:80], location[:60],
    )
    return plan


__all__ = [
    "apply_lock",
]
