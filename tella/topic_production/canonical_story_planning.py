"""Minimal capability boundary for canonical StoryPlan production.

No approved topic producer or semantic narration segmenter currently exists.
This module therefore reports those missing capabilities and provides only
producer-independent scene-hint target binding.  It cannot construct, accept,
or authorize a StoryPlan.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Never

from .models import StoryPlan
from .script_input import NormalizedScriptInput, SceneHint, ScriptInputMode


class CanonicalStoryPlanningErrorCode(StrEnum):
    STORYPLAN_TOPIC_PRODUCER_MISSING = "STORYPLAN_TOPIC_PRODUCER_MISSING"
    STORYPLAN_NARRATION_SEGMENTER_MISSING = "STORYPLAN_NARRATION_SEGMENTER_MISSING"
    STORYPLAN_HINT_TARGET_INVALID = "STORYPLAN_HINT_TARGET_INVALID"


class CanonicalStoryPlanningError(RuntimeError):
    """Stable capability or target-binding failure."""

    def __init__(
        self,
        code: CanonicalStoryPlanningErrorCode,
        detail: str,
        *,
        logical_input_hash: str | None = None,
        available_beat_ids: tuple[str, ...] = (),
    ):
        self.code = code
        self.detail = detail
        self.logical_input_hash = logical_input_hash
        self.available_beat_ids = tuple(available_beat_ids)
        super().__init__(f"{code.value}: {detail}")

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "code": self.code.value,
            "detail": self.detail,
            "available_beat_ids": list(self.available_beat_ids),
        }
        if self.logical_input_hash is not None:
            payload["logical_input_hash"] = self.logical_input_hash
        return payload


@dataclass(frozen=True)
class StoryPlanHintTargetBinding:
    """One immutable producer-independent hint-to-beat target mapping."""

    hint: SceneHint
    beat_id: str
    scene_order: int


def plan_canonical_story(normalized_input: NormalizedScriptInput) -> Never:
    """Fail honestly until an approved StoryPlan producer is implemented."""

    if normalized_input.input_mode is ScriptInputMode.TOPIC:
        code = CanonicalStoryPlanningErrorCode.STORYPLAN_TOPIC_PRODUCER_MISSING
        detail = "no approved production StoryPlan topic producer is available"
    else:
        code = CanonicalStoryPlanningErrorCode.STORYPLAN_NARRATION_SEGMENTER_MISSING
        detail = "no approved semantic narration-to-StoryPlan segmenter is available"
    raise CanonicalStoryPlanningError(
        code,
        detail,
        logical_input_hash=normalized_input.logical_input_hash,
    )


def bind_story_plan_hint_targets(
    *,
    hints: tuple[SceneHint, ...],
    story_plan: StoryPlan,
) -> tuple[StoryPlanHintTargetBinding, ...]:
    """Bind every hint to an existing beat without judging its semantics."""

    by_id = {beat.beat_id: beat for beat in story_plan.semantic_beats}
    by_order = {beat.order: beat for beat in story_plan.semantic_beats}
    bindings: list[StoryPlanHintTargetBinding] = []
    for hint in hints:
        beat = (
            by_id.get(hint.target_beat_id)
            if hint.target_beat_id is not None
            else by_order.get(hint.target_scene_order)
        )
        if beat is None:
            target = hint.target_beat_id or f"scene_order={hint.target_scene_order}"
            raise CanonicalStoryPlanningError(
                CanonicalStoryPlanningErrorCode.STORYPLAN_HINT_TARGET_INVALID,
                f"scene hint target does not exist in StoryPlan: {target}",
                available_beat_ids=tuple(by_id),
            )
        bindings.append(
            StoryPlanHintTargetBinding(
                hint=hint,
                beat_id=beat.beat_id,
                scene_order=beat.order,
            )
        )
    return tuple(bindings)


__all__ = [
    "CanonicalStoryPlanningError",
    "CanonicalStoryPlanningErrorCode",
    "StoryPlanHintTargetBinding",
    "bind_story_plan_hint_targets",
    "plan_canonical_story",
]
