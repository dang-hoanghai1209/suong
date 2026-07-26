from __future__ import annotations

import json
import math

import pytest
from pydantic import ValidationError

from tella.topic_production.duration_policy import (
    BeatPacingWarningCode,
    DurationAssessmentReasonCode,
    DurationAssessmentStatus,
    DurationValueAuthority,
    MvpDurationTargetAssessment,
    assess_beat_duration_pacing,
    assess_mvp_duration_target,
)
from tella.topic_production.manifest import build_initial_manifest
from tella.topic_production.models import (
    PlannerMetadata,
    PlannerMode,
    SceneTiming,
    SemanticBeat,
    StoryPlan,
    _canonical_milliseconds,
)
from tella.topic_production.planner import (
    DeterministicTopicPlanner,
    build_scene_briefs,
)
from tella.topic_production.story_plan_coverage import assign_fixture_source_spans
from tella.topic_production.story_plan_identity import canonical_story_plan_sha256
from tella.topic_production.timing import allocate_durations, build_scene_timings


_TOPIC = "structurally valid duration policy"


@pytest.mark.parametrize(
    ("value", "expected_milliseconds"),
    [
        (0.0001, 0),
        (0.0004, 0),
        (0.0005, 1),
        (0.0006, 1),
        (0.0010, 1),
        (0.0014, 1),
        (0.0015, 2),
        (0.0016, 2),
        (0.0070, 7),
        (0.0075, 8),
        (0.0080, 8),
        (1.2344, 1234),
        (1.2345, 1235),
        (1.2346, 1235),
        (29.7964, 29796),
        (29.7965, 29797),
        (29.7966, 29797),
        (38.0014, 38001),
        (38.0015, 38002),
        (38.0016, 38002),
        (10_000.0, 10_000_000),
    ],
)
def test_canonical_milliseconds_uses_literal_decimal_half_up_authority(
    value: float,
    expected_milliseconds: int,
) -> None:
    assert _canonical_milliseconds(value) == expected_milliseconds


def _plan(duration: float = 35.0, scene_count: int = 7):
    return DeterministicTopicPlanner().plan(
        topic=_TOPIC,
        language="en",
        scene_count=scene_count,
        target_duration_seconds=duration,
    )


def _manual_three_scene_plan(duration: float) -> StoryPlan:
    narration_text, segments, spans = assign_fixture_source_spans(("first", "second", "third"))
    beat_duration = duration / 3
    return StoryPlan(
        topic="manual three-scene duration",
        language="en",
        target_duration_seconds=duration,
        requested_scene_count=3,
        narration_text=narration_text,
        emotional_arc=("first", "second", "third"),
        topic_intent="preserve the explicit manual scene-count profile",
        semantic_beats=tuple(
            SemanticBeat(
                beat_id=f"beat_{order:02d}",
                order=order,
                source_span=spans[order - 1],
                narration_segment=segments[order - 1],
                semantic_purpose=f"manual purpose {order}",
                emotional_state="calm",
                transition_intent="continue",
                visual_intent=f"manual visual {order}",
                duration_seconds=beat_duration,
            )
            for order in range(1, 4)
        ),
        planner_metadata=PlannerMetadata(
            planner_id="manual_topic_input",
            planner_version="manual_v1",
            normalized_topic="manual three-scene duration",
            topic_concepts=("manual", "duration"),
            deterministic_key="1234567890abcdef",
            planner_mode=PlannerMode.PRODUCTION,
            production_eligible=True,
        ),
    )


@pytest.mark.parametrize("duration", [32.0, 38.0, 31.999, 38.001, 29.796])
def test_product_target_boundaries_do_not_control_storyplan_structure(
    duration: float,
) -> None:
    plan = _plan(duration)
    briefs = build_scene_briefs(plan)
    manifest = build_initial_manifest(
        job_id=f"duration-{duration}",
        plan=plan,
        briefs=briefs,
    )

    assert plan.target_duration_seconds == duration
    assert round(sum(beat.duration_seconds for beat in plan.semantic_beats), 3) == round(
        duration,
        3,
    )
    assert manifest.timings[-1].end_seconds == round(duration, 3)


@pytest.mark.parametrize("duration", [6.0, 18.0])
def test_manual_three_scene_profile_keeps_its_scene_authority_without_duration_range(
    duration: float,
) -> None:
    plan = _manual_three_scene_plan(duration)

    assert plan.requested_scene_count == 3
    assert plan.target_duration_seconds == duration
    assert round(sum(beat.duration_seconds for beat in plan.semantic_beats), 3) == duration


@pytest.mark.parametrize(
    ("duration", "expected_relation"),
    [(14.0, "below"), (42.0, "above")],
)
def test_per_beat_pacing_range_does_not_control_structural_validity(
    duration: float,
    expected_relation: str,
) -> None:
    plan = _plan(duration)
    briefs = build_scene_briefs(plan)
    manifest = build_initial_manifest(
        job_id=f"pacing-{expected_relation}",
        plan=plan,
        briefs=briefs,
    )
    beat_durations = tuple(beat.duration_seconds for beat in plan.semantic_beats)

    if expected_relation == "below":
        assert all(value < 3.0 for value in beat_durations)
    else:
        assert all(value > 5.0 for value in beat_durations)
    assert tuple(brief.duration_seconds for brief in briefs) == beat_durations
    assert tuple(timing.duration_seconds for timing in manifest.timings) == beat_durations


@pytest.mark.parametrize(
    "invalid",
    [-1.0, 0.0, float("nan"), float("inf"), float("-inf"), True],
    ids=["negative", "zero", "nan", "positive-infinity", "negative-infinity", "boolean"],
)
def test_every_topic_duration_model_rejects_nonpositive_nonfinite_or_boolean(
    invalid,
) -> None:
    plan = _plan()
    brief = build_scene_briefs(plan)[0]
    timing = build_initial_manifest(
        job_id="strict-duration-models",
        plan=plan,
        briefs=build_scene_briefs(plan),
    ).timings[0]

    plan_payload = plan.model_dump(mode="python")
    plan_payload["target_duration_seconds"] = invalid
    with pytest.raises(ValidationError):
        type(plan).model_validate(plan_payload)

    beat_payload = plan.semantic_beats[0].model_dump(mode="python")
    beat_payload["duration_seconds"] = invalid
    with pytest.raises(ValidationError):
        type(plan.semantic_beats[0]).model_validate(beat_payload)

    brief_payload = brief.model_dump(mode="python")
    brief_payload["duration_seconds"] = invalid
    with pytest.raises(ValidationError):
        type(brief).model_validate(brief_payload)

    timing_payload = timing.model_dump(mode="python")
    timing_payload["duration_seconds"] = invalid
    with pytest.raises(ValidationError):
        type(timing).model_validate(timing_payload)

    with pytest.raises(ValidationError):
        plan.model_copy(update={"target_duration_seconds": invalid})
    with pytest.raises(ValidationError):
        plan.semantic_beats[0].model_copy(update={"duration_seconds": invalid})
    with pytest.raises(ValidationError):
        brief.model_copy(update={"duration_seconds": invalid})
    with pytest.raises(ValidationError):
        timing.model_copy(update={"duration_seconds": invalid})


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("start_seconds", float("nan")),
        ("start_seconds", float("inf")),
        ("end_seconds", float("-inf")),
        ("end_seconds", 0.0),
        ("start_seconds", True),
    ],
)
def test_scene_timing_boundaries_remain_strict_and_finite(
    field: str,
    invalid,
) -> None:
    plan = _plan()
    timing = build_initial_manifest(
        job_id="strict-timeline-values",
        plan=plan,
        briefs=build_scene_briefs(plan),
    ).timings[0]
    payload = timing.model_dump(mode="python")
    payload[field] = invalid

    with pytest.raises(ValidationError):
        type(timing).model_validate(payload)


@pytest.mark.parametrize(
    ("start", "duration", "end"),
    [
        (1.0, 0.0001, 1.0),
        (1.0, 0.0001, 0.9999),
        (1.0, 0.001, 1.0),
        (1.0, 0.001, 0.999),
        (1.0, 0.0004, 1.0004),
        (1.0, 0.0006, 1.0006),
        (1.2344, 0.001, 1.2354),
        (1.2345, 0.001, 1.2355),
    ],
)
def test_scene_timing_rejects_collapsed_or_reversed_intervals(
    start: float,
    duration: float,
    end: float,
) -> None:
    with pytest.raises(ValidationError):
        SceneTiming(
            scene_id="scene_01",
            order=1,
            start_seconds=start,
            duration_seconds=duration,
            end_seconds=end,
        )


@pytest.mark.parametrize(
    "update",
    [
        {"end_seconds": 1.0},
        {"duration_seconds": 0.0001},
        {"duration_seconds": 0.0001, "end_seconds": 1.0},
        {"duration_seconds": 0.0001, "end_seconds": 0.9999},
    ],
)
def test_scene_timing_validated_copy_rejects_stale_or_collapsed_intervals(
    update: dict[str, float],
) -> None:
    timing = SceneTiming(
        scene_id="scene_01",
        order=1,
        start_seconds=1.0,
        duration_seconds=1.0,
        end_seconds=2.0,
    )

    with pytest.raises(ValidationError):
        timing.model_copy(update=update)


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("start_seconds", float("nan")),
        ("start_seconds", float("inf")),
        ("start_seconds", float("-inf")),
        ("start_seconds", True),
        ("duration_seconds", float("nan")),
        ("duration_seconds", float("inf")),
        ("duration_seconds", float("-inf")),
        ("duration_seconds", True),
        ("end_seconds", float("nan")),
        ("end_seconds", float("inf")),
        ("end_seconds", float("-inf")),
        ("end_seconds", True),
    ],
)
def test_scene_timing_rejects_nonfinite_or_boolean_interval_values(
    field: str,
    invalid,
) -> None:
    payload = {
        "scene_id": "scene_01",
        "order": 1,
        "start_seconds": 1.0,
        "duration_seconds": 1.0,
        "end_seconds": 2.0,
    }
    payload[field] = invalid

    with pytest.raises(ValidationError):
        SceneTiming.model_validate(payload)


@pytest.mark.parametrize(
    ("start", "duration", "end"),
    [
        (0.0, 0.001, 0.001),
        (1.0, 0.001, 1.001),
        (1.234, 0.001, 1.235),
        (0.0, 4.375, 4.375),
        (1.234, 2.345, 3.579),
    ],
)
def test_scene_timing_accepts_millisecond_intervals_and_round_trips_json(
    start: float,
    duration: float,
    end: float,
) -> None:
    timing = SceneTiming(
        scene_id="scene_01",
        order=1,
        start_seconds=start,
        duration_seconds=duration,
        end_seconds=end,
    )
    serialized = timing.model_dump_json()

    assert SceneTiming.model_validate_json(serialized) == timing
    assert SceneTiming.model_validate(timing.model_dump(mode="python")) == timing
    assert timing.model_dump_json() == serialized


def test_inconsistent_beat_total_remains_rejected_by_validated_copy() -> None:
    plan = _plan()

    with pytest.raises(ValidationError, match="must total target_duration_seconds"):
        plan.model_copy(update={"target_duration_seconds": 34.0})


def test_storyplan_preserves_raw_half_millisecond_intent_and_canonical_total() -> None:
    plan = _plan(29.7965)
    same_canonical_total = plan.model_copy(update={"target_duration_seconds": 29.7966})

    assert plan.target_duration_seconds == 29.7965
    assert plan.model_dump(mode="json")["target_duration_seconds"] == 29.7965
    assert _canonical_milliseconds(sum(beat.duration_seconds for beat in plan.semantic_beats)) == (
        29797
    )
    assert canonical_story_plan_sha256(same_canonical_total) != canonical_story_plan_sha256(plan)
    with pytest.raises(ValidationError, match="must total target_duration_seconds"):
        plan.model_copy(update={"target_duration_seconds": 29.798})

    upper = _plan(38.0015)
    assert upper.target_duration_seconds == 38.0015
    assert _canonical_milliseconds(sum(beat.duration_seconds for beat in upper.semantic_beats)) == (
        38002
    )


@pytest.mark.parametrize(
    ("scene_count", "duration", "canonical_target"),
    [
        (7, 0.0065, 0.007),
        (7, 0.007, 0.007),
        (7, 0.0075, 0.008),
        (7, 0.008, 0.008),
        (8, 0.008, 0.008),
        (7, 0.0085, 0.009),
        (8, 0.0085, 0.009),
        (7, 0.009, 0.009),
        (8, 0.009, 0.009),
        (7, 1.0, 1.0),
        (8, 1.0, 1.0),
        (7, 29.796, 29.796),
        (8, 29.796, 29.796),
        (7, 31.999, 31.999),
        (8, 31.999, 31.999),
        (7, 32.0, 32.0),
        (8, 32.0, 32.0),
        (7, 35.0, 35.0),
        (8, 35.0, 35.0),
        (7, 38.0, 38.0),
        (8, 38.0, 38.0),
        (7, 38.001, 38.001),
        (8, 38.001, 38.001),
        (7, 10_000.0, 10_000.0),
        (8, 10_000.0, 10_000.0),
    ],
)
def test_allocator_and_generated_timeline_are_deterministic_and_total_preserving(
    scene_count: int,
    duration: float,
    canonical_target: float,
) -> None:
    first = allocate_durations(scene_count, duration)
    second = allocate_durations(scene_count, duration)
    plan = _plan(duration, scene_count)
    timings = build_scene_timings(list(plan.semantic_beats))
    manifest = build_initial_manifest(
        job_id=f"timeline-{scene_count}-{duration}",
        plan=plan,
        briefs=build_scene_briefs(plan),
    )

    assert tuple(first) == tuple(second)
    assert len(first) == scene_count
    assert all(value > 0 and math.isfinite(value) for value in first)
    assert all(value == _canonical_milliseconds(value) / 1000 for value in first)
    assert round(sum(first), 3) == canonical_target
    assert manifest.timings == timings
    assert timings[0].start_seconds == 0
    assert all(timing.end_seconds > timing.start_seconds for timing in timings)
    assert all(timing.duration_seconds > 0 for timing in timings)
    assert all(
        left.end_seconds == right.start_seconds
        for left, right in zip(timings, timings[1:], strict=False)
    )
    assert timings[-1].end_seconds == canonical_target


@pytest.mark.parametrize(
    ("scene_count", "duration"),
    [
        (7, 0.0001),
        (8, 0.0001),
        (7, 0.001),
        (8, 0.001),
        (7, 0.004),
        (8, 0.004),
        (8, 0.0065),
        (8, 0.007),
    ],
)
def test_allocator_rejects_targets_without_one_millisecond_per_scene(
    scene_count: int,
    duration: float,
) -> None:
    with pytest.raises(
        ValueError,
        match="cannot allocate a positive millisecond duration to every scene",
    ):
        allocate_durations(scene_count, duration)
    with pytest.raises(
        ValueError,
        match="cannot allocate a positive millisecond duration to every scene",
    ):
        _plan(duration, scene_count)


@pytest.mark.parametrize(
    "invalid",
    [-1.0, 0.0, float("nan"), float("inf"), float("-inf"), True],
)
def test_allocator_rejects_structurally_invalid_targets(invalid) -> None:
    with pytest.raises(ValueError, match="finite positive"):
        allocate_durations(7, invalid)


@pytest.mark.parametrize(
    ("duration", "expected_status", "expected_reason"),
    [
        (
            32.0,
            DurationAssessmentStatus.IN_TARGET,
            DurationAssessmentReasonCode.DURATION_IN_TARGET,
        ),
        (
            38.0,
            DurationAssessmentStatus.IN_TARGET,
            DurationAssessmentReasonCode.DURATION_IN_TARGET,
        ),
        (
            31.999,
            DurationAssessmentStatus.OUTSIDE_TARGET_WARNING,
            DurationAssessmentReasonCode.OUTSIDE_DURATION_TARGET_WARNING,
        ),
        (
            38.001,
            DurationAssessmentStatus.OUTSIDE_TARGET_WARNING,
            DurationAssessmentReasonCode.OUTSIDE_DURATION_TARGET_WARNING,
        ),
        (
            29.796417,
            DurationAssessmentStatus.OUTSIDE_TARGET_WARNING,
            DurationAssessmentReasonCode.OUTSIDE_DURATION_TARGET_WARNING,
        ),
    ],
)
def test_mvp_duration_target_assessment_is_inclusive_and_deterministic(
    duration: float,
    expected_status: DurationAssessmentStatus,
    expected_reason: DurationAssessmentReasonCode,
) -> None:
    assessment = assess_mvp_duration_target(
        duration,
        value_authority=DurationValueAuthority.MEASURED,
    )

    assert assessment.status is expected_status
    assert assessment.reason_code is expected_reason
    assert assessment.target_min_seconds == 32.0
    assert assessment.target_max_seconds == 38.0
    assert assessment.policy_id == "mvp_emotional_duration_32_38_v1"


def test_duration_value_authorities_serialize_distinctly_and_round_trip() -> None:
    serialized = {}
    for authority in DurationValueAuthority:
        assessment = assess_mvp_duration_target(
            35.0,
            value_authority=authority,
        )
        serialized[authority] = json.loads(assessment.model_dump_json())
        assert (
            MvpDurationTargetAssessment.model_validate_json(assessment.model_dump_json())
            == assessment
        )

    assert {payload["value_authority"] for payload in serialized.values()} == {
        "PLANNED",
        "ESTIMATED",
        "MEASURED",
    }
    assert "estimate_is_authoritative" not in MvpDurationTargetAssessment.model_fields


def test_duration_assessment_is_immutable_and_cannot_become_contradictory() -> None:
    assessment = assess_mvp_duration_target(
        35.0,
        value_authority=DurationValueAuthority.PLANNED,
    )

    with pytest.raises(ValidationError):
        assessment.status = DurationAssessmentStatus.OUTSIDE_TARGET_WARNING
    with pytest.raises(ValidationError):
        assessment.model_copy(update={"status": DurationAssessmentStatus.OUTSIDE_TARGET_WARNING})


@pytest.mark.parametrize(
    ("duration", "expected_code"),
    [
        (2.999, BeatPacingWarningCode.BEAT_DURATION_BELOW_PACING_TARGET_WARNING),
        (5.001, BeatPacingWarningCode.BEAT_DURATION_ABOVE_PACING_TARGET_WARNING),
    ],
)
def test_per_beat_pacing_warnings_are_distinct(
    duration: float,
    expected_code: BeatPacingWarningCode,
) -> None:
    warning = assess_beat_duration_pacing(
        beat_id="beat_01",
        actual_duration_seconds=duration,
    )

    assert warning is not None
    assert warning.warning_code is expected_code
    assert warning.actual_duration_seconds == duration
    assert warning.target_min_seconds == 3.0
    assert warning.target_max_seconds == 5.0
    assert warning.warning_code.value != "OUTSIDE_DURATION_TARGET_WARNING"


@pytest.mark.parametrize("duration", [3.0, 4.0, 5.0])
def test_in_target_beats_produce_no_pacing_warning(duration: float) -> None:
    assert (
        assess_beat_duration_pacing(
            beat_id="beat_01",
            actual_duration_seconds=duration,
        )
        is None
    )


def test_duration_assessment_does_not_mutate_or_reconstruct_narration() -> None:
    plan = _plan(29.796)
    before_text = plan.narration_text
    before_segments = tuple(beat.narration_segment for beat in plan.semantic_beats)
    before_spans = tuple(beat.source_span for beat in plan.semantic_beats)

    assess_mvp_duration_target(
        plan.target_duration_seconds,
        value_authority=DurationValueAuthority.PLANNED,
    )
    for beat in plan.semantic_beats:
        assess_beat_duration_pacing(
            beat_id=beat.beat_id,
            actual_duration_seconds=beat.duration_seconds,
        )

    assert plan.narration_text == before_text
    assert tuple(beat.narration_segment for beat in plan.semantic_beats) == before_segments
    assert tuple(beat.source_span for beat in plan.semantic_beats) == before_spans
