"""Canonical content identity for validated StoryPlan authority."""

from __future__ import annotations

import hashlib
import json

from .models import StoryPlan


def canonical_story_plan_sha256(story_plan: StoryPlan) -> str:
    """Return the canonical SHA-256 identity of one valid StoryPlan."""

    validated = StoryPlan.model_validate(story_plan.model_dump(mode="python"))
    encoded = json.dumps(
        validated.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["canonical_story_plan_sha256"]
