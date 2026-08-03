from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

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
    NarrationSourceSpan,
    PlannerMetadata,
    PlannerMode,
    SemanticBeat,
    StoryPlan,
)
from tella.topic_production.story_plan_coverage import assign_fixture_source_spans
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
    narration_text, segments, spans = assign_fixture_source_spans(
        tuple(f"segment {order}" for order in range(1, 8))
    )
    beats = [
        SemanticBeat(
            beat_id=f"beat_{order:02d}",
            order=order,
            source_span=spans[order - 1],
            narration_segment=segments[order - 1],
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
        narration_text=narration_text,
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


def _story_plan_with_exact_segments(exact_segments: tuple[str, ...]) -> StoryPlan:
    if len(exact_segments) != 7:
        raise ValueError("test StoryPlan requires exactly seven source segments")
    template = _story_plan()
    cursor = 0
    beats = []
    for template_beat, segment in zip(
        template.semantic_beats,
        exact_segments,
        strict=True,
    ):
        end = cursor + len(segment)
        beats.append(
            template_beat.model_copy(
                update={
                    "source_span": NarrationSourceSpan(start=cursor, end=end),
                    "narration_segment": segment,
                }
            )
        )
        cursor = end
    return StoryPlan(
        **{
            **template.model_dump(mode="python"),
            "narration_text": "".join(exact_segments),
            "semantic_beats": beats,
        }
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


def test_canonical_storyplan_hash_ignores_dictionary_key_insertion_order():
    story = _story_plan()
    payload = json.loads(story.model_dump_json())

    def reverse_mapping_keys(value):
        if isinstance(value, dict):
            return {key: reverse_mapping_keys(value[key]) for key in reversed(tuple(value))}
        if isinstance(value, list):
            return [reverse_mapping_keys(item) for item in value]
        return value

    reordered = reverse_mapping_keys(payload)
    reconstructed = StoryPlan.model_validate_json(json.dumps(reordered))

    assert tuple(reconstructed.semantic_beats) == tuple(story.semantic_beats)
    assert canonical_story_plan_sha256(reconstructed) == canonical_story_plan_sha256(story)


def test_canonical_storyplan_hash_changes_for_boundary_only_and_arc_changes():
    first = _story_plan_with_exact_segments(("A ", "B ", "C ", "D ", "E ", "F ", "G"))
    boundary_changed = _story_plan_with_exact_segments(("A", " B ", "C ", "D ", "E ", "F ", "G"))
    assert first.narration_text == boundary_changed.narration_text
    assert canonical_story_plan_sha256(first) != canonical_story_plan_sha256(boundary_changed)

    arc_changed = StoryPlan.model_validate(
        {
            **first.model_dump(mode="python"),
            "emotional_arc": ("changed", *first.emotional_arc[1:]),
        }
    )
    assert canonical_story_plan_sha256(first) != canonical_story_plan_sha256(arc_changed)


@pytest.mark.parametrize(
    "segments",
    [
        ("A\r\n", "B ", "C ", "D ", "E ", "F ", "G"),
        ("A\n", "B ", "C ", "D ", "E ", "F ", "G"),
        ("A\n\n", "B ", "C ", "D ", "E ", "F ", "G"),
        ("A  ", "B ", "C ", "D ", "E ", "F ", "G"),
        ("A\t", "B ", "C ", "D ", "E ", "F ", "G"),
        ("  A ", "B ", "C ", "D ", "E ", "F ", "G"),
        ("A ", "B ", "C ", "D ", "E ", "F ", "G  "),
        ("A", "B ", "C ", "D ", "E ", "F ", "G"),
        ("Tôi ", "ổn ", "C ", "D ", "E ", "F ", "G"),
        ("To\u0302i ", "o\u0309n ", "C ", "D ", "E ", "F ", "G"),
        ("🙂 ", "B ", "C ", "D ", "E ", "F ", "G"),
        ("👩‍👩‍👧‍👧 ", "B ", "C ", "D ", "E ", "F ", "G"),
        ("“A…”", "B ", "C ", "D ", "E ", "F ", "G"),
    ],
    ids=[
        "crlf",
        "lf",
        "blank-lines",
        "repeated-spaces",
        "tab",
        "leading-whitespace",
        "trailing-whitespace",
        "no-whitespace-boundary",
        "vietnamese-precomposed",
        "decomposed-combining",
        "simple-emoji",
        "zwj-emoji",
        "unicode-punctuation",
    ],
)
def test_exact_source_text_round_trips_without_normalization(
    segments: tuple[str, ...],
):
    story = _story_plan_with_exact_segments(segments)
    expected = "".join(segments)

    assert story.narration_text == expected
    assert "".join(beat.narration_segment for beat in story.semantic_beats) == expected
    reconstructed = StoryPlan.model_validate_json(story.model_dump_json())
    assert reconstructed.narration_text == expected
    assert reconstructed == story


def test_narration_source_span_is_strict_frozen_and_alias_safe():
    payload = {"start": 0, "end": 1}
    span = NarrationSourceSpan.model_validate(payload)
    payload["end"] = 2

    assert span == NarrationSourceSpan(start=0, end=1)
    with pytest.raises(ValidationError):
        span.end = 2  # type: ignore[misc]
    with pytest.raises(ValidationError):
        NarrationSourceSpan.model_validate({"start": True, "end": 1})
    with pytest.raises(ValidationError):
        NarrationSourceSpan.model_validate({"start": 0, "end": 1.0})
    with pytest.raises(ValidationError):
        NarrationSourceSpan.model_validate({"start": "0", "end": 1})
    with pytest.raises(ValidationError):
        NarrationSourceSpan.model_validate({"start": 0, "end": 1, "text": "A"})
    with pytest.raises(ValidationError):
        span.model_copy(update={"start": True})


def test_unsafe_source_span_cannot_enter_validated_story_authority():
    story = _story_plan()
    unsafe_span = NarrationSourceSpan.model_construct(start=2, end=1)

    with pytest.raises(ValidationError):
        NarrationSourceSpan.model_validate(unsafe_span)

    beat_payload = story.semantic_beats[0].model_dump(mode="python")
    beat_payload["source_span"] = unsafe_span
    with pytest.raises(ValidationError):
        SemanticBeat.model_validate(beat_payload)

    story_payload = story.model_dump(mode="python")
    story_payload["semantic_beats"][0]["source_span"] = unsafe_span
    with pytest.raises(ValidationError):
        StoryPlan.model_validate(story_payload)


@pytest.mark.parametrize(
    "mutation",
    [
        "first-not-zero",
        "final-not-complete",
        "gap",
        "overlap",
        "out-of-bounds",
        "out-of-order",
        "segment-mismatch",
        "missing-span",
    ],
)
def test_invalid_source_coverage_is_rejected(mutation: str):
    story = _story_plan()
    payload = story.model_dump(mode="python")
    beats = payload["semantic_beats"]
    if mutation == "first-not-zero":
        beats[0]["source_span"]["start"] = 1
    elif mutation == "final-not-complete":
        beats[-1]["source_span"]["end"] -= 1
        beats[-1]["narration_segment"] = beats[-1]["narration_segment"][:-1]
    elif mutation == "gap":
        beats[1]["source_span"]["start"] += 1
        beats[1]["narration_segment"] = beats[1]["narration_segment"][1:]
    elif mutation == "overlap":
        beats[1]["source_span"]["start"] -= 1
        beats[1]["narration_segment"] = story.narration_text[
            beats[1]["source_span"]["start"] : beats[1]["source_span"]["end"]
        ]
    elif mutation == "out-of-bounds":
        beats[-1]["source_span"]["end"] = len(story.narration_text) + 1
        beats[-1]["narration_segment"] += "X"
    elif mutation == "out-of-order":
        beats[0]["source_span"], beats[1]["source_span"] = (
            beats[1]["source_span"],
            beats[0]["source_span"],
        )
        beats[0]["narration_segment"], beats[1]["narration_segment"] = (
            beats[1]["narration_segment"],
            beats[0]["narration_segment"],
        )
    elif mutation == "segment-mismatch":
        beats[0]["narration_segment"] = "forged"
    else:
        beats[0].pop("source_span")

    with pytest.raises(ValidationError):
        StoryPlan.model_validate(payload)


def test_reversed_zero_length_and_whitespace_only_spans_are_rejected():
    with pytest.raises(ValidationError):
        NarrationSourceSpan(start=2, end=1)
    with pytest.raises(ValidationError):
        NarrationSourceSpan(start=1, end=1)
    with pytest.raises(ValidationError, match="non-whitespace"):
        _story_plan_with_exact_segments(("A", " ", "C ", "D ", "E ", "F ", "G"))


def test_stale_model_copy_and_unsafe_construct_fail_detached_revalidation():
    story = _story_plan()
    with pytest.raises(ValidationError):
        story.model_copy(update={"narration_text": f"{story.narration_text}!"})

    beats = list(story.semantic_beats)
    beats[0] = beats[0].model_copy(update={"source_span": NarrationSourceSpan(start=0, end=1)})
    with pytest.raises(ValidationError):
        story.model_copy(update={"semantic_beats": beats})

    beats = list(story.semantic_beats)
    beats[0] = beats[0].model_copy(update={"narration_segment": "forged"})
    with pytest.raises(ValidationError):
        story.model_copy(update={"semantic_beats": beats})

    payload = story.model_dump(mode="python")
    payload["semantic_beats"][0]["narration_segment"] = "unsafe forged segment"
    unsafe = StoryPlan.model_construct(**payload)
    with pytest.raises(ValidationError):
        canonical_story_plan_sha256(unsafe)


def test_tampered_json_and_detached_dump_cannot_change_authority():
    story = _story_plan()
    payload = json.loads(story.model_dump_json())
    payload["semantic_beats"][1]["source_span"]["start"] += 1
    with pytest.raises(ValidationError):
        StoryPlan.model_validate_json(json.dumps(payload))

    detached = story.model_dump(mode="python")
    detached["narration_text"] = "detached mutation"
    assert story.narration_text != detached["narration_text"]
    assert canonical_story_plan_sha256(story) == canonical_story_plan_sha256(
        StoryPlan.model_validate_json(story.model_dump_json())
    )


@pytest.mark.parametrize(
    "segments",
    [
        ("same", "same", "ending"),
        ("day", "daylight", "ending"),
    ],
    ids=["repeated-identical", "contained-text"],
)
def test_fixture_source_span_assignment_handles_ambiguous_text_without_search(
    segments: tuple[str, ...],
):
    narration_text, authority_segments, spans = assign_fixture_source_spans(segments)
    expected_segments = (f"{segments[0]} ", f"{segments[1]} ", segments[2])

    assert narration_text == " ".join(segments)
    assert authority_segments == expected_segments
    assert spans[0].start == 0
    assert spans[-1].end == len(narration_text)
    assert all(left.end == right.start for left, right in zip(spans, spans[1:]))
    assert tuple(narration_text[span.start : span.end] for span in spans) == authority_segments
    assert "".join(authority_segments) == narration_text


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


def test_source_span_is_public_but_fixture_migration_helpers_are_not():
    assert topic_production.NarrationSourceSpan is NarrationSourceSpan
    assert not hasattr(topic_production, "assign_fixture_source_spans")
    assert not hasattr(topic_production, "semantic_beat_display_text")
