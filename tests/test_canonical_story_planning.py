from __future__ import annotations

import json

import pytest

import tella.topic_production as topic_production
from tella.topic_production import canonical_story_planning
from tella.topic_production import full_canary, renderer_bridge
from tella.topic_production.canonical_story_planning import (
    CanonicalStoryPlanningError,
    CanonicalStoryPlanningErrorCode,
    bind_story_plan_hint_targets,
    plan_canonical_story,
)
from tella.topic_production.models import (
    PlannerMetadata,
    PlannerMode,
    SemanticBeat,
    StoryPlan,
)
from tella.topic_production.script_input import (
    HintedNarrationScriptInput,
    NarrationScriptInput,
    SceneHint,
    ScriptInputMode,
    TopicScriptInput,
    normalize_script_input,
)
from tella.topic_production.story_plan_identity import canonical_story_plan_sha256


def _story_plan() -> StoryPlan:
    beats = [
        SemanticBeat(
            beat_id=f"beat_{order:02d}",
            order=order,
            narration_segment=f"segment {order}",
            semantic_purpose=f"meaning {order}",
            emotional_state=f"state {order}",
            transition_intent=f"transition {order}",
            visual_intent=f"visual intent {order}",
            duration_seconds=5.0,
        )
        for order in range(1, 8)
    ]
    return StoryPlan(
        topic="quiet courage",
        language="en",
        target_duration_seconds=35.0,
        requested_scene_count=7,
        narration_text=" ".join(beat.narration_segment for beat in beats),
        emotional_arc=[f"state {order}" for order in range(1, 8)],
        topic_intent="one coherent emotional story",
        semantic_beats=beats,
        planner_metadata=PlannerMetadata(
            normalized_topic="quiet courage",
            topic_concepts=["quiet", "courage"],
            deterministic_key="0123456789abcdef",
            planner_mode=PlannerMode.FIXTURE,
            production_eligible=False,
        ),
    )


def _normalized(mode: ScriptInputMode):
    common = {"language": "en"}
    if mode is ScriptInputMode.TOPIC:
        value = TopicScriptInput(topic="quiet courage", **common)
    elif mode is ScriptInputMode.NARRATION:
        value = NarrationScriptInput(narration="Exact narration.", **common)
    else:
        value = HintedNarrationScriptInput(
            narration="Exact narration.",
            scene_hints=[SceneHint(target_scene_order=1, focal_scale="medium")],
            **common,
        )
    return normalize_script_input(value)


def test_storyplan_semantic_collections_are_tuple_backed_and_alias_safe():
    concepts = ["quiet", "courage"]
    arc = [f"state {order}" for order in range(1, 8)]
    beats = list(_story_plan().semantic_beats)
    payload = _story_plan().model_dump(mode="python")
    payload["emotional_arc"] = arc
    payload["semantic_beats"] = beats
    payload["planner_metadata"]["topic_concepts"] = concepts

    story = StoryPlan.model_validate(payload)
    concepts.append("caller mutation")
    arc.append("caller mutation")
    beats.clear()

    assert story.planner_metadata.topic_concepts == ("quiet", "courage")
    assert len(story.emotional_arc) == 7
    assert len(story.semantic_beats) == 7
    with pytest.raises(TypeError):
        story.semantic_beats[0] = story.semantic_beats[0]  # type: ignore[index]


def test_storyplan_accepts_and_emits_json_arrays():
    story = _story_plan()
    payload = json.loads(story.model_dump_json())

    assert isinstance(payload["planner_metadata"]["topic_concepts"], list)
    assert isinstance(payload["emotional_arc"], list)
    assert isinstance(payload["semantic_beats"], list)

    reconstructed = StoryPlan.model_validate_json(json.dumps(payload))
    assert isinstance(reconstructed.planner_metadata.topic_concepts, tuple)
    assert isinstance(reconstructed.emotional_arc, tuple)
    assert isinstance(reconstructed.semantic_beats, tuple)
    assert reconstructed == story


def test_canonical_storyplan_hash_is_deterministic_and_semantic():
    story = _story_plan()
    reconstructed = StoryPlan.model_validate_json(story.model_dump_json())
    assert canonical_story_plan_sha256(story) == canonical_story_plan_sha256(reconstructed)

    payload = story.model_dump(mode="python")
    payload["semantic_beats"][0]["semantic_purpose"] = "changed meaning"
    changed = StoryPlan.model_validate(payload)
    assert canonical_story_plan_sha256(changed) != canonical_story_plan_sha256(story)


def test_canary_and_renderer_use_the_single_canonical_hash_function():
    assert full_canary.canonical_story_plan_sha256 is canonical_story_plan_sha256
    assert renderer_bridge.canonical_story_plan_sha256 is canonical_story_plan_sha256


@pytest.mark.parametrize(
    ("mode", "expected_code"),
    [
        (
            ScriptInputMode.TOPIC,
            CanonicalStoryPlanningErrorCode.STORYPLAN_TOPIC_PRODUCER_MISSING,
        ),
        (
            ScriptInputMode.NARRATION,
            CanonicalStoryPlanningErrorCode.STORYPLAN_NARRATION_SEGMENTER_MISSING,
        ),
        (
            ScriptInputMode.NARRATION_WITH_SCENE_HINTS,
            CanonicalStoryPlanningErrorCode.STORYPLAN_NARRATION_SEGMENTER_MISSING,
        ),
    ],
)
def test_every_input_mode_reports_its_missing_production_capability(
    mode,
    expected_code,
):
    normalized = _normalized(mode)

    with pytest.raises(CanonicalStoryPlanningError) as raised:
        plan_canonical_story(normalized)

    assert raised.value.code is expected_code
    assert raised.value.as_dict()["logical_input_hash"] == normalized.logical_input_hash


def test_hint_targets_bind_deterministically_without_semantic_judgment():
    hints = (
        SceneHint(target_scene_order=2, action="open the window"),
        SceneHint(target_beat_id="beat_05", environment="quiet room"),
    )

    bindings = bind_story_plan_hint_targets(hints=hints, story_plan=_story_plan())

    assert tuple((binding.beat_id, binding.scene_order) for binding in bindings) == (
        ("beat_02", 2),
        ("beat_05", 5),
    )
    assert tuple(binding.hint for binding in bindings) == hints


def test_invalid_hint_target_fails_without_silent_drop():
    story = _story_plan()
    hint = SceneHint(target_scene_order=8, focal_scale="wide")

    with pytest.raises(CanonicalStoryPlanningError) as raised:
        bind_story_plan_hint_targets(hints=(hint,), story_plan=story)

    assert raised.value.code is CanonicalStoryPlanningErrorCode.STORYPLAN_HINT_TARGET_INVALID
    assert raised.value.available_beat_ids == tuple(beat.beat_id for beat in story.semantic_beats)


def test_no_generic_finalizer_or_result_framework_remains():
    removed = (
        "CanonicalStoryPlanningRequest",
        "CanonicalStoryPlanningResult",
        "finalize_canonical_story_planning",
        "StoryPlanningEligibility",
        "StoryPlanIdentityEvidenceReadiness",
        "NarrationProvenance",
    )
    assert all(not hasattr(canonical_story_planning, name) for name in removed)
    assert all(not hasattr(topic_production, name) for name in removed)


def test_canonical_planning_symbols_are_not_exported_at_package_root():
    module_local = (
        "CanonicalStoryPlanningError",
        "CanonicalStoryPlanningErrorCode",
        "StoryPlanHintTargetBinding",
        "bind_story_plan_hint_targets",
        "plan_canonical_story",
    )
    assert all(not hasattr(topic_production, name) for name in module_local)
