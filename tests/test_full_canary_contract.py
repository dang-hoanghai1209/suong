from __future__ import annotations

import pytest

from tella.topic_production.models import (
    PlannerMetadata,
    PlannerMode,
    SemanticBeat,
    StoryPlan,
)
from tella.topic_production.full_canary import (
    FULL_CANARY_STORY_PLAN_SHA256,
    build_full_canary_scene_briefs,
)
from tella.topic_production.story_plan_identity import canonical_story_plan_sha256


_TOPIC = "Học cách dịu dàng với chính mình sau những ngày mệt mỏi."
_SEGMENTS = (
    "Có những ngày, mình cố gắng quá nhiều",
    "đến mức quên rằng bản thân cũng cần một khoảng nghỉ ngơi.",
    "Mạnh mẽ đôi khi là cho phép mình dừng lại ngay trong lúc này.",
    "Mình có thể ngồi yên, lắng nghe nhịp thở,",
    "lau những giọt nước mắt bằng một bàn tay ấm áp,",
    "rồi cho cơ thể và trái tim đủ thời gian để hồi phục.",
    "Khi lòng nhẹ hơn, mình bước tiếp từng chút một mà không cần vội vàng.",
    "Hôm nay, mình chọn đối xử dịu dàng và kiên nhẫn hơn với chính mình.",
)
_PURPOSES = (
    "an exhausted person recognizes the weight of trying too hard",
    "the person has forgotten their own need for rest",
    "strength can include pausing instead of continuing",
    "quiet sitting creates a safe pause",
    "a tear is gently wiped away",
    "stillness makes room for an unhurried breath",
    "self-compassion makes the heart feel lighter",
    "gentleness, not speed, closes the healing moment",
)
_EMOTIONAL_STATES = (
    "sad",
    "sad",
    "worried",
    "worried",
    "sad",
    "reflective",
    "accepting",
    "neutral",
)
_DURATIONS = (4.0, 5.0, 4.5, 4.0, 4.0, 4.0, 5.0, 4.5)


def _story_plan() -> StoryPlan:
    beats = [
        SemanticBeat(
            beat_id=f"beat_{order:02d}",
            order=order,
            narration_segment=segment,
            semantic_purpose=purpose,
            emotional_state=emotion,
            transition_intent="gentle emotional continuation",
            visual_intent=purpose,
            duration_seconds=duration,
        )
        for order, (segment, purpose, emotion, duration) in enumerate(
            zip(
                _SEGMENTS,
                _PURPOSES,
                _EMOTIONAL_STATES,
                _DURATIONS,
                strict=True,
            ),
            start=1,
        )
    ]
    return StoryPlan(
        topic=_TOPIC,
        language="vi",
        target_duration_seconds=35.0,
        requested_scene_count=8,
        narration_text=" ".join(_SEGMENTS),
        emotional_arc=["exhausted", "paused", "recovering", "gentle"],
        topic_intent="show self-compassion after emotionally exhausting days",
        semantic_beats=beats,
        planner_metadata=PlannerMetadata(
            planner_id="supplied_eligible_acceptance_story",
            planner_version="duration_reviewed_v1",
            normalized_topic=_TOPIC.lower(),
            topic_concepts=["self-compassion", "rest", "emotional healing"],
            deterministic_key="82d876cb5ef79698",
            semantic_evaluator="reviewed_duration_fixture",
            planner_mode=PlannerMode.PRODUCTION,
            production_eligible=True,
        ),
    )


def test_canary_staging_requires_exact_authoritative_story_identity_and_hash() -> None:
    story = _story_plan()
    assert canonical_story_plan_sha256(story) == FULL_CANARY_STORY_PLAN_SHA256
    assert len(build_full_canary_scene_briefs(story)) == 8

    wrong_identity = story.model_copy(
        update={
            "planner_metadata": story.planner_metadata.model_copy(
                update={"deterministic_key": "0000000000000000"}
            )
        }
    )
    with pytest.raises(ValueError, match="locked to one StoryPlan"):
        build_full_canary_scene_briefs(wrong_identity)

    wrong_hash = story.model_copy(update={"topic_intent": "mutated intent"})
    with pytest.raises(ValueError, match="SHA-256 does not match"):
        build_full_canary_scene_briefs(wrong_hash)


def test_visual_overlay_preserves_every_authoritative_story_field() -> None:
    story = _story_plan()
    briefs = build_full_canary_scene_briefs(story)

    assert [brief.scene_id for brief in briefs] == [f"scene_{order:02d}" for order in range(1, 9)]
    assert [brief.order for brief in briefs] == list(range(1, 9))
    assert [brief.source_beat_id for brief in briefs] == [
        beat.beat_id for beat in story.semantic_beats
    ]
    assert [brief.narrative_text for brief in briefs] == [
        beat.narration_segment for beat in story.semantic_beats
    ]
    assert [brief.meaning for brief in briefs] == [
        beat.semantic_purpose for beat in story.semantic_beats
    ]
    assert [brief.topic_intent for brief in briefs] == [story.topic_intent] * 8
    assert [brief.duration_seconds for brief in briefs] == [
        beat.duration_seconds for beat in story.semantic_beats
    ]


@pytest.mark.parametrize("mutation", ["narration", "meaning", "order", "topic_intent"])
def test_story_semantic_mutation_is_rejected(mutation: str) -> None:
    story = _story_plan()
    if mutation == "narration":
        changed = story.model_copy(update={"narration_text": f"{story.narration_text}!"})
    elif mutation == "meaning":
        beats = list(story.semantic_beats)
        beats[0] = beats[0].model_copy(update={"semantic_purpose": "changed meaning"})
        changed = story.model_copy(update={"semantic_beats": beats})
    elif mutation == "order":
        changed = story.model_copy(update={"semantic_beats": list(reversed(story.semantic_beats))})
    else:
        changed = story.model_copy(update={"topic_intent": "changed topic intent"})

    with pytest.raises(ValueError):
        build_full_canary_scene_briefs(changed)


def test_visual_overlay_is_female_only_and_has_no_generated_scene_chaining() -> None:
    for brief in build_full_canary_scene_briefs(_story_plan()):
        assert brief.characters == ["recurring_woman"]
        assert brief.reference_roles == ["female_identity_anchor", "style_anchor"]
        assert not brief.reference_strategy.accepted_scene_chaining
        assert "no recurring male or couple" in brief.hard_negatives
        assert "no extra detailed person" in brief.hard_negatives


def test_canary_fixture_has_quantitative_visual_diversity() -> None:
    briefs = build_full_canary_scene_briefs(_story_plan())
    actions = {brief.scene_id: " ".join(brief.action).lower() for brief in briefs}
    seated_or_reclined = {
        scene_id
        for scene_id, action in actions.items()
        if "sitting" in action or "reclining" in action
    }
    clear_standing = {
        scene_id
        for scene_id, action in actions.items()
        if "standing" in action or "pausing halfway up" in action
    }
    moving = {scene_id for scene_id, action in actions.items() if "walking" in action}

    assert len(seated_or_reclined) <= 3
    assert len(clear_standing) >= 3
    assert len(moving) >= 1
    assert len({brief.environment[0] for brief in briefs}) == 8
    assert len({brief.composition[0] for brief in briefs}) == 8
