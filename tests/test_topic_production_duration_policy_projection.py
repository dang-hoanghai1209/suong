"""Immutable planned/measured duration-policy projection tests."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from tella.topic_production import (
    DurationPolicyReport,
    ExecutionRunState,
    ProcessedNarrationDurationMeasurement,
    bind_processed_narration_measurement,
    build_duration_policy_report,
    build_production_run_plan,
    build_scene_briefs,
    initialize_execution_state,
    load_reference_catalog,
)
from tella.topic_production.duration_policy import (
    DurationAssessmentStatus,
    DurationValueAuthority,
    assess_mvp_duration_target,
)
from tella.topic_production.execution_models import ProductionRunPlan
from tella.topic_production.planner import DeterministicTopicPlanner
from tella.topic_production.story_plan_identity import canonical_story_plan_sha256


def _state_with_durations(durations: tuple[float, ...]) -> ExecutionRunState:
    story = DeterministicTopicPlanner().plan(
        topic="planned duration authority",
        language="en",
        scene_count=len(durations),
        target_duration_seconds=sum(durations),
    )
    story = story.model_copy(
        update={
            "semantic_beats": tuple(
                beat.model_copy(update={"duration_seconds": duration})
                for beat, duration in zip(story.semantic_beats, durations, strict=True)
            )
        }
    )
    run_plan = build_production_run_plan(
        job_id="planned-duration-authority",
        story_plan=story,
        scene_briefs=build_scene_briefs(story),
        reference_catalog=load_reference_catalog(None),
    )
    return initialize_execution_state(run_plan)


def _measurement(
    state: ExecutionRunState,
    duration: float,
) -> ProcessedNarrationDurationMeasurement:
    return ProcessedNarrationDurationMeasurement(
        schema_version=1,
        artifact_relative_path="assets/narration.mp3",
        artifact_sha256="a" * 64,
        story_plan_sha256=canonical_story_plan_sha256(state.run_plan.story_plan),
        measurement_method="ffprobe_single_audio_stream_v1",
        measured_duration_assessment=assess_mvp_duration_target(
            duration,
            value_authority=DurationValueAuthority.MEASURED,
        ),
    )


def _with_measurement(state: ExecutionRunState, duration: float) -> ExecutionRunState:
    return bind_processed_narration_measurement(state, _measurement(state, duration))


def _unsafe_run_plan(
    run_plan: ProductionRunPlan,
    **updates: object,
) -> ProductionRunPlan:
    fields = {
        field_name: getattr(run_plan, field_name) for field_name in ProductionRunPlan.model_fields
    }
    fields.update(updates)
    return ProductionRunPlan.model_construct(**fields)


def _unsafe_state(
    state: ExecutionRunState,
    **updates: object,
) -> ExecutionRunState:
    fields = {
        field_name: getattr(state, field_name) for field_name in ExecutionRunState.model_fields
    }
    fields.update(updates)
    return ExecutionRunState.model_construct(**fields)


@pytest.mark.parametrize("schema_version", ["missing", None, True, 2.0, "2", 1, 3])
def test_report_requires_exact_integer_schema_two(schema_version: object) -> None:
    payload = build_duration_policy_report(_state_with_durations((5.0,) * 7)).model_dump(
        mode="python"
    )
    if schema_version == "missing":
        payload.pop("schema_version")
    else:
        payload["schema_version"] = schema_version

    with pytest.raises(ValidationError):
        DurationPolicyReport.model_validate(payload)


def test_measured_assessment_is_required_nullable() -> None:
    report = build_duration_policy_report(_state_with_durations((5.0,) * 7))
    assert report.schema_version == 2
    assert report.measured_duration_assessment is None

    payload = report.model_dump(mode="python")
    payload.pop("measured_duration_assessment")
    with pytest.raises(ValidationError, match="Field required"):
        DurationPolicyReport.model_validate(payload)


@pytest.mark.parametrize(
    "missing_field",
    [
        "planned_duration_assessment",
        "planned_beat_pacing_warnings",
        "warning_count",
    ],
)
def test_report_rejects_other_missing_fields_and_extras(missing_field: str) -> None:
    payload = build_duration_policy_report(_state_with_durations((5.0,) * 7)).model_dump(
        mode="python"
    )
    payload.pop(missing_field)
    with pytest.raises(ValidationError, match="Field required"):
        DurationPolicyReport.model_validate(payload)

    complete = build_duration_policy_report(_state_with_durations((5.0,) * 7)).model_dump(
        mode="python"
    )
    complete["unexpected"] = "forbidden"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DurationPolicyReport.model_validate(complete)


def test_report_requires_planned_and_measured_authorities() -> None:
    state = _with_measurement(_state_with_durations((5.0,) * 7), 35.0)
    report = build_duration_policy_report(state)
    payload = report.model_dump(mode="python")
    payload["planned_duration_assessment"] = assess_mvp_duration_target(
        35.0,
        value_authority=DurationValueAuthority.MEASURED,
    )
    with pytest.raises(ValidationError, match="requires PLANNED authority"):
        DurationPolicyReport.model_validate(payload)

    payload = report.model_dump(mode="python")
    payload["measured_duration_assessment"] = assess_mvp_duration_target(
        35.0,
        value_authority=DurationValueAuthority.PLANNED,
    )
    with pytest.raises(ValidationError, match="requires MEASURED authority"):
        DurationPolicyReport.model_validate(payload)


@pytest.mark.parametrize("forged_count", [-1, True, 0.0, "0", 1])
def test_report_rejects_invalid_or_mismatched_warning_count(forged_count: object) -> None:
    payload = build_duration_policy_report(_state_with_durations((5.0,) * 7)).model_dump(
        mode="python"
    )
    payload["warning_count"] = forged_count
    with pytest.raises(ValidationError):
        DurationPolicyReport.model_validate(payload)


@pytest.mark.parametrize(
    ("durations", "measured_duration", "expected_count"),
    [
        ((5.0,) * 7, None, 0),
        ((4.0,) * 7, None, 1),
        ((5.0,) * 7, 31.0, 1),
        ((5.0,) * 7, 35.0, 0),
        ((5.0,) * 7, 39.0, 1),
        ((2.5, 6.25, 4.375, 4.375, 4.375, 4.375, 4.375, 4.375), None, 2),
        ((2.5, 5.25, 3.875, 3.875, 3.875, 3.875, 3.875, 3.875), 39.0, 4),
    ],
)
def test_builder_uses_exact_combined_warning_formula(
    durations: tuple[float, ...],
    measured_duration: float | None,
    expected_count: int,
) -> None:
    state = _state_with_durations(durations)
    if measured_duration is not None:
        state = _with_measurement(state, measured_duration)

    report = build_duration_policy_report(state)

    assert report.warning_count == expected_count
    assert report.planned_duration_assessment == state.run_plan.planned_duration_assessment
    assert report.planned_beat_pacing_warnings == state.run_plan.planned_beat_pacing_warnings
    assert report.measured_duration_assessment == (
        state.processed_narration_measurement.measured_duration_assessment
        if state.processed_narration_measurement is not None
        else None
    )


def test_report_preserves_canonical_warning_order_and_rejects_duplicates() -> None:
    state = _state_with_durations((2.5, 6.25, 4.375, 4.375, 4.375, 4.375, 4.375, 4.375))
    report = build_duration_policy_report(state)
    caller_warnings = report.planned_beat_pacing_warnings
    caller_snapshot = tuple(warning.model_dump(mode="python") for warning in caller_warnings)
    assert [warning.beat_id for warning in caller_warnings] == [
        "beat_01",
        "beat_02",
    ]

    canonical = DurationPolicyReport(
        schema_version=2,
        planned_duration_assessment=report.planned_duration_assessment,
        planned_beat_pacing_warnings=caller_warnings,
        measured_duration_assessment=None,
        warning_count=2,
    )
    assert canonical.planned_beat_pacing_warnings == caller_warnings

    reversed_warnings = tuple(reversed(caller_warnings))
    with pytest.raises(ValidationError, match="canonical beat ID order"):
        DurationPolicyReport(
            schema_version=2,
            planned_duration_assessment=report.planned_duration_assessment,
            planned_beat_pacing_warnings=reversed_warnings,
            measured_duration_assessment=None,
            warning_count=2,
        )

    payload = report.model_dump(mode="python")
    payload["planned_beat_pacing_warnings"] = (
        caller_warnings[0],
        caller_warnings[0],
    )
    with pytest.raises(ValidationError, match="duplicate beat IDs"):
        DurationPolicyReport.model_validate(payload)
    assert (
        tuple(warning.model_dump(mode="python") for warning in caller_warnings) == caller_snapshot
    )
    assert reversed_warnings == (caller_warnings[1], caller_warnings[0])


def test_direct_report_is_structurally_valid_but_source_unbound() -> None:
    state = _state_with_durations((5.0,) * 7)
    planned = assess_mvp_duration_target(
        31.0,
        value_authority=DurationValueAuthority.PLANNED,
    )
    measured = assess_mvp_duration_target(
        39.0,
        value_authority=DurationValueAuthority.MEASURED,
    )

    report = DurationPolicyReport(
        schema_version=2,
        planned_duration_assessment=planned,
        planned_beat_pacing_warnings=(),
        measured_duration_assessment=measured,
        warning_count=2,
    )

    assert report != build_duration_policy_report(state)
    assert report.measured_duration_assessment == measured


def test_report_revalidates_detaches_and_is_frozen() -> None:
    state = _with_measurement(
        _state_with_durations((2.5, 6.25, 4.375, 4.375, 4.375, 4.375, 4.375, 4.375)),
        35.0,
    )
    source = build_duration_policy_report(state)
    payload = source.model_dump(mode="python")
    report = DurationPolicyReport.model_validate(
        {
            **payload,
            "planned_duration_assessment": source.planned_duration_assessment,
            "planned_beat_pacing_warnings": list(source.planned_beat_pacing_warnings),
            "measured_duration_assessment": source.measured_duration_assessment,
        }
    )

    assert report.planned_duration_assessment is not source.planned_duration_assessment
    assert report.measured_duration_assessment is not source.measured_duration_assessment
    assert isinstance(report.planned_beat_pacing_warnings, tuple)
    assert report.planned_beat_pacing_warnings[0] is not source.planned_beat_pacing_warnings[0]
    with pytest.raises(ValidationError):
        report.warning_count = 99
    with pytest.raises(TypeError):
        report.planned_beat_pacing_warnings[0] = report.planned_beat_pacing_warnings[1]


def test_report_isolated_from_caller_owned_nested_payload_aliases() -> None:
    source = build_duration_policy_report(
        _with_measurement(
            _state_with_durations((2.5, 6.25, 4.375, 4.375, 4.375, 4.375, 4.375, 4.375)),
            35.0,
        )
    )
    planned_payload = source.planned_duration_assessment.model_dump(mode="python")
    assert source.measured_duration_assessment is not None
    measured_payload = source.measured_duration_assessment.model_dump(mode="python")
    warning_payloads = [
        warning.model_dump(mode="python") for warning in source.planned_beat_pacing_warnings
    ]
    report = DurationPolicyReport(
        schema_version=2,
        planned_duration_assessment=planned_payload,
        planned_beat_pacing_warnings=warning_payloads,
        measured_duration_assessment=measured_payload,
        warning_count=2,
    )
    report_snapshot = report.model_dump(mode="python")

    planned_payload["actual_duration_seconds"] = 99.0
    measured_payload["actual_duration_seconds"] = 1.0
    warning_payloads.reverse()
    warning_payloads[0]["beat_id"] = "beat_08"
    warning_payloads.append({"forged": True})

    assert report.model_dump(mode="python") == report_snapshot


def test_report_rejects_unsafe_nested_records() -> None:
    report = build_duration_policy_report(
        _with_measurement(
            _state_with_durations((2.5, 6.25, 4.375, 4.375, 4.375, 4.375, 4.375, 4.375)),
            35.0,
        )
    )
    unsafe_payload = report.planned_duration_assessment.model_dump(mode="python")
    unsafe_payload["status"] = DurationAssessmentStatus.OUTSIDE_TARGET_WARNING
    unsafe_assessment = type(report.planned_duration_assessment).model_construct(**unsafe_payload)

    with pytest.raises(ValidationError):
        DurationPolicyReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "planned_duration_assessment": unsafe_assessment,
            }
        )

    assert report.measured_duration_assessment is not None
    unsafe_payload = report.measured_duration_assessment.model_dump(mode="python")
    unsafe_payload["status"] = DurationAssessmentStatus.OUTSIDE_TARGET_WARNING
    unsafe_assessment = type(report.measured_duration_assessment).model_construct(**unsafe_payload)
    with pytest.raises(ValidationError):
        DurationPolicyReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "measured_duration_assessment": unsafe_assessment,
            }
        )

    warning = report.planned_beat_pacing_warnings[0]
    unsafe_payload = warning.model_dump(mode="python")
    unsafe_payload["actual_duration_seconds"] = 4.0
    unsafe_warning = type(warning).model_construct(**unsafe_payload)
    with pytest.raises(ValidationError):
        DurationPolicyReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "planned_beat_pacing_warnings": (unsafe_warning,),
                "warning_count": 2,
            }
        )


def test_report_json_round_trip_is_exact_and_uses_arrays_and_null() -> None:
    report = build_duration_policy_report(_state_with_durations((5.0,) * 7))
    serialized = report.model_dump_json()
    payload = json.loads(serialized)

    assert list(payload) == [
        "schema_version",
        "planned_duration_assessment",
        "planned_beat_pacing_warnings",
        "measured_duration_assessment",
        "warning_count",
    ]
    assert isinstance(payload["planned_beat_pacing_warnings"], list)
    assert payload["measured_duration_assessment"] is None
    assert DurationPolicyReport.model_validate_json(serialized) == report
    assert DurationPolicyReport.model_validate_json(serialized).model_dump_json() == serialized


def test_report_model_copy_revalidates_coordinated_updates() -> None:
    report = build_duration_policy_report(_state_with_durations((5.0,) * 7))
    measured = assess_mvp_duration_target(
        39.0,
        value_authority=DurationValueAuthority.MEASURED,
    )
    updated = report.model_copy(
        update={
            "measured_duration_assessment": measured,
            "warning_count": 1,
        }
    )
    assert updated.measured_duration_assessment == measured
    assert updated.warning_count == 1

    with pytest.raises(ValidationError):
        report.model_copy(update={"warning_count": 1})
    with pytest.raises(ValidationError):
        report.model_copy(update={"schema_version": 1})
    with pytest.raises(ValidationError):
        report.model_copy(update={"unexpected": "forbidden"})


def test_builder_rejects_stale_planning_hash_and_unsafe_planned_records() -> None:
    state = _state_with_durations((5.0,) * 7)
    stale_run = _unsafe_run_plan(state.run_plan, planning_hash="0" * 64)
    with pytest.raises(ValidationError, match="planning_hash does not match"):
        build_duration_policy_report(_unsafe_state(state, run_plan=stale_run))

    assessment_payload = state.run_plan.planned_duration_assessment.model_dump(mode="python")
    assessment_payload["status"] = DurationAssessmentStatus.OUTSIDE_TARGET_WARNING
    unsafe_assessment = type(state.run_plan.planned_duration_assessment).model_construct(
        **assessment_payload
    )
    unsafe_run = _unsafe_run_plan(
        state.run_plan,
        planned_duration_assessment=unsafe_assessment,
    )
    with pytest.raises(ValidationError):
        build_duration_policy_report(_unsafe_state(state, run_plan=unsafe_run))


def test_builder_rejects_unsafe_schema_two_run_plan_without_repair() -> None:
    state = _state_with_durations((5.0,) * 7)
    unsafe_run = _unsafe_run_plan(state.run_plan, schema_version=2)
    unsafe_state = _unsafe_state(state, run_plan=unsafe_run)
    source_snapshot = unsafe_state.model_dump(mode="python")

    with pytest.raises(
        ValueError,
        match="in-memory ProductionRunPlan authority requires schema_version 3",
    ):
        build_duration_policy_report(unsafe_state)

    assert unsafe_state.model_dump(mode="python") == source_snapshot


@pytest.mark.parametrize("forged_warnings", ["reversed", "missing"])
def test_builder_rejects_forged_planned_warning_source_without_repair(
    forged_warnings: str,
) -> None:
    state = _state_with_durations((2.5, 6.25, 4.375, 4.375, 4.375, 4.375, 4.375, 4.375))
    canonical_warnings = state.run_plan.planned_beat_pacing_warnings
    replacement = tuple(reversed(canonical_warnings)) if forged_warnings == "reversed" else ()
    unsafe_run = _unsafe_run_plan(
        state.run_plan,
        planned_beat_pacing_warnings=replacement,
    )
    unsafe_state = _unsafe_state(state, run_plan=unsafe_run)
    source_snapshot = unsafe_state.model_dump(mode="python")

    with pytest.raises(ValidationError, match="warnings do not match StoryPlan"):
        build_duration_policy_report(unsafe_state)

    assert unsafe_state.model_dump(mode="python") == source_snapshot
    assert unsafe_run.planned_beat_pacing_warnings == replacement


def test_builder_rejects_stale_measurement_story_identity() -> None:
    state = _with_measurement(_state_with_durations((5.0,) * 7), 35.0)
    measurement = state.processed_narration_measurement
    assert measurement is not None
    stale = measurement.model_copy(update={"story_plan_sha256": "0" * 64})

    with pytest.raises(ValidationError, match="StoryPlan SHA-256 does not match"):
        build_duration_policy_report(_unsafe_state(state, processed_narration_measurement=stale))


def test_builder_leaves_source_state_unchanged_and_preserves_locked_hash() -> None:
    state = _with_measurement(_state_with_durations((5.0,) * 7), 35.0)
    before = state.model_dump(mode="python")

    report = build_duration_policy_report(state)

    assert state.model_dump(mode="python") == before
    assert isinstance(report, DurationPolicyReport)
    assert (
        state.run_plan.planning_hash
        == "31ef6b7caac5a8a513109274e1f2d159678630d4e495bf2634a5e8c5e97ee87d"
    )
    assert set(DurationPolicyReport.model_fields) == {
        "schema_version",
        "planned_duration_assessment",
        "planned_beat_pacing_warnings",
        "measured_duration_assessment",
        "warning_count",
    }


def test_report_contains_no_artifact_or_measured_beat_authority() -> None:
    report = build_duration_policy_report(
        _with_measurement(_state_with_durations((5.0,) * 7), 35.0)
    )
    payload = report.model_dump(mode="json")

    assert (
        not {
            "artifact_relative_path",
            "artifact_sha256",
            "measurement_method",
            "measured_beat_pacing_warnings",
            "estimated_duration_assessment",
        }
        & payload.keys()
    )
