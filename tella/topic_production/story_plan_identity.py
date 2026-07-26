"""Canonical content identity for validated StoryPlan authority."""

from __future__ import annotations

import hashlib
import json

from .models import StoryPlan

_STORY_PLAN_AUTHORITY_SCHEMA = "story_plan_exact_narration_coverage_v1"


def canonical_story_plan_sha256(story_plan: StoryPlan) -> str:
    """Return the canonical SHA-256 identity of one valid StoryPlan."""

    validated = StoryPlan.model_validate(story_plan.model_dump(mode="python"))
    story_payload = validated.model_dump(mode="json")
    for beat in story_payload["semantic_beats"]:
        beat.pop("narration_segment")
    payload = {
        "authority_schema": _STORY_PLAN_AUTHORITY_SCHEMA,
        "story_plan": story_payload,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["canonical_story_plan_sha256"]
