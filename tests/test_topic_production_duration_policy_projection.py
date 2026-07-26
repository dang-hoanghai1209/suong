"""Immutable planned-duration policy projection tests."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from tella.topic_production import (
    DurationPolicyReport,
    build_duration_policy_report,
    build_production_run_plan,
    build_scene_briefs,
    load_reference_catalog,
)
from tella.topic_production.duration_policy import (
    DurationAssessmentStatus,
    DurationValueAuthority,
    assess_beat_duration_pacing,
    assess_mvp_duration_target,
)
from tella.topic_production.execution_models import ProductionRunPlan
from tella.topic_production.planner import DeterministicTopicPlanner


def _run_with_durations(durations: tuple[float, ...]) -> ProductionRunPlan:
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
    return build_production_run_plan(
        job_id="planned-duration-authority",
        story_plan=story,
        scene_briefs=build_scene_briefs(story),
        reference_catalog=load_reference_catalog(None),
    )


def _unsafe_run_plan(
    run_plan: ProductionRunPlan,
    **updates: object,
) -> ProductionRunPlan:
    fields = {
        field_name: getattr(run_plan, field_name) for field_name in ProductionRunPlan.model_fields
    }
    fields.update(updates)
    return ProductionRunPlan.model_construct(**fields)


@pytest.mark.parametrize(
    ("durations", "expected_count"),
    [
        ((5.0,) * 7, 0),
        ((4.0,) * 7, 1),
        ((4.751,) * 8, 1),
        ((2.5, 6.25, 4.375, 4.375, 4.375, 4.375, 4.375, 4.375), 2),
        ((2.5, 5.25, 3.875, 3.875, 3.875, 3.875, 3.875, 3.875), 3),
    ],
    ids=[
        "in-target-no-beat-warning",
        "below-target-no-beat-warning",
        "above-target-no-beat-warning",
        "in-target-two-beat-warnings",
        "outside-target-two-beat-warnings",
    ],
)
def test_report_derives_exact_warning_count(
    durations: tuple[float, ...],
    expected_count: int,
) -> None:
    run_plan = _run_with_durations(durations)
    report = build_duration_policy_report(run_plan)

    assert report.schema_version == 1
    assert report.warning_count == expected_count
    assert report.planned_duration_assessment == run_plan.planned_duration_assessment
    assert report.planned_beat_pacing_warnings == run_plan.planned_beat_pacing_warnings
    assert run_plan.external_calls == 0


def test_report_retains_complete_warnings_in_canonical_run_plan_order() -> None:
    run_plan = _run_with_durations((2.5, 6.25, 4.375, 4.375, 4.375, 4.375, 4.375, 4.375))
    report = build_duration_policy_report(run_plan)

    assert [warning.beat_id for warning in report.planned_beat_pacing_warnings] == [
        "beat_01",
        "beat_02",
    ]
    assert [warning.model_dump(mode="json") for warning in report.planned_beat_pacing_warnings] == [
        warning.model_dump(mode="json") for warning in run_plan.planned_beat_pacing_warnings
    ]


@pytest.mark.parametrize("schema_version", [None, 2, "1", 1.0, True])
def test_report_requires_exact_integer_schema_one(schema_version: object) -> None:
    payload = build_duration_policy_report(_run_with_durations((5.0,) * 7)).model_dump(
        mode="python"
    )
    payload["schema_version"] = schema_version

    with pytest.raises(ValidationError, match="schema_version must be exact integer 1"):
        DurationPolicyReport.model_validate(payload)


@pytest.mark.parametrize(
    "missing_field",
    [
        "schema_version",
        "planned_duration_assessment",
        "planned_beat_pacing_warnings",
        "warning_count",
    ],
)
def test_report_rejects_missing_fields_and_extras(missing_field: str) -> None:
    payload = build_duration_policy_report(_run_with_durations((5.0,) * 7)).model_dump(
        mode="python"
    )
    payload.pop(missing_field)

    with pytest.raises(ValidationError, match="Field required"):
        DurationPolicyReport.model_validate(payload)

    complete = build_duration_policy_report(_run_with_durations((5.0,) * 7)).model_dump(
        mode="python"
    )
    complete["unexpected"] = "forbidden"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DurationPolicyReport.model_validate(complete)


@pytest.mark.parametrize("forged_count", [-1, True, 0.0, 1])
def test_report_rejects_forged_warning_count(forged_count: object) -> None:
    payload = build_duration_policy_report(_run_with_durations((5.0,) * 7)).model_dump(
        mode="python"
    )
    payload["warning_count"] = forged_count

    with pytest.raises(ValidationError):
        DurationPolicyReport.model_validate(payload)


def test_report_revalidates_detaches_and_immutably_stores_nested_records() -> None:
    source = build_duration_policy_report(
        _run_with_durations((2.5, 6.25, 4.375, 4.375, 4.375, 4.375, 4.375, 4.375))
    )
    assessment = source.planned_duration_assessment
    warnings = list(source.planned_beat_pacing_warnings)
    warning_payloads = [
        warning.model_dump(mode="python") for warning in source.planned_beat_pacing_warnings
    ]
    payload = {
        "schema_version": 1,
        "planned_duration_assessment": assessment,
        "planned_beat_pacing_warnings": warning_payloads,
        "warning_count": 2,
    }

    report = DurationPolicyReport.model_validate(payload)
    warnings.reverse()
    warning_payloads.reverse()
    warning_payloads[0]["beat_id"] = "beat_08"

    assert report.planned_duration_assessment is not assessment
    assert isinstance(report.planned_beat_pacing_warnings, tuple)
    assert all(
        stored is not original
        for stored, original in zip(
            report.planned_beat_pacing_warnings,
            source.planned_beat_pacing_warnings,
            strict=True,
        )
    )
    assert [warning.beat_id for warning in report.planned_beat_pacing_warnings] == [
        "beat_01",
        "beat_02",
    ]
    with pytest.raises(TypeError):
        report.planned_beat_pacing_warnings[0] = report.planned_beat_pacing_warnings[1]
    with pytest.raises(ValidationError):
        report.warning_count = 3


def test_report_json_round_trip_is_exact_and_uses_arrays() -> None:
    report = build_duration_policy_report(
        _run_with_durations((2.5, 6.25, 4.375, 4.375, 4.375, 4.375, 4.375, 4.375))
    )
    serialized = report.model_dump_json()
    payload = json.loads(serialized)

    assert list(payload) == [
        "schema_version",
        "planned_duration_assessment",
        "planned_beat_pacing_warnings",
        "warning_count",
    ]
    assert isinstance(payload["planned_beat_pacing_warnings"], list)
    assert DurationPolicyReport.model_validate_json(serialized) == report
    assert DurationPolicyReport.model_validate_json(serialized).model_dump_json() == serialized


def test_report_rejects_non_planned_authority_and_duplicate_beat_ids() -> None:
    report = build_duration_policy_report(
        _run_with_durations((2.5, 6.25, 4.375, 4.375, 4.375, 4.375, 4.375, 4.375))
    )
    measured = assess_mvp_duration_target(
        report.planned_duration_assessment.actual_duration_seconds,
        value_authority=DurationValueAuthority.MEASURED,
    )
    payload = report.model_dump(mode="python")
    payload["planned_duration_assessment"] = measured
    with pytest.raises(ValidationError, match="requires PLANNED authority"):
        DurationPolicyReport.model_validate(payload)

    payload = report.model_dump(mode="python")
    payload["planned_beat_pacing_warnings"] = [
        report.planned_beat_pacing_warnings[0],
        report.planned_beat_pacing_warnings[0],
    ]
    with pytest.raises(ValidationError, match="duplicate beat IDs"):
        DurationPolicyReport.model_validate(payload)


def test_report_model_copy_revalidates_every_update() -> None:
    report = build_duration_policy_report(_run_with_durations((5.0,) * 7))
    outside = assess_mvp_duration_target(
        31.0,
        value_authority=DurationValueAuthority.PLANNED,
    )
    warning = assess_beat_duration_pacing(
        beat_id="beat_01",
        actual_duration_seconds=2.5,
    )
    assert warning is not None

    assert report.model_copy() == report
    assert report.model_copy(deep=True) == report
    with pytest.raises(ValidationError):
        report.model_copy(update={"warning_count": 1})
    with pytest.raises(ValidationError):
        report.model_copy(update={"planned_duration_assessment": outside})
    with pytest.raises(ValidationError):
        report.model_copy(update={"planned_beat_pacing_warnings": (warning,)})
    with pytest.raises(ValidationError, match="schema_version must be exact integer 1"):
        report.model_copy(update={"schema_version": 2})
    with pytest.raises(ValidationError, match="extra_forbidden"):
        report.model_copy(update={"unexpected": "forbidden"})


def test_report_model_copy_accepts_coordinated_structurally_valid_updates() -> None:
    report = build_duration_policy_report(_run_with_durations((5.0,) * 7))
    outside = assess_mvp_duration_target(
        31.0,
        value_authority=DurationValueAuthority.PLANNED,
    )
    warning = assess_beat_duration_pacing(
        beat_id="beat_01",
        actual_duration_seconds=2.5,
    )
    assert warning is not None

    assessment_update = report.model_copy(
        update={
            "planned_duration_assessment": outside,
            "warning_count": 1,
        }
    )
    warning_update = report.model_copy(
        update={
            "planned_beat_pacing_warnings": (warning,),
            "warning_count": 1,
        }
    )
    combined_update = report.model_copy(
        update={
            "planned_duration_assessment": outside,
            "planned_beat_pacing_warnings": (warning,),
            "warning_count": 2,
        }
    )

    assert assessment_update.planned_duration_assessment == outside
    assert assessment_update.warning_count == 1
    assert warning_update.planned_beat_pacing_warnings == (warning,)
    assert warning_update.warning_count == 1
    assert combined_update.planned_duration_assessment == outside
    assert combined_update.planned_beat_pacing_warnings == (warning,)
    assert combined_update.warning_count == 2


def test_direct_report_is_structurally_valid_but_source_unbound() -> None:
    run_plan = _run_with_durations((5.0,) * 7)
    source_unbound_assessment = assess_mvp_duration_target(
        31.0,
        value_authority=DurationValueAuthority.PLANNED,
    )

    structurally_valid_but_source_unbound = DurationPolicyReport(
        schema_version=1,
        planned_duration_assessment=source_unbound_assessment,
        planned_beat_pacing_warnings=(),
        warning_count=1,
    )
    source_consistent = build_duration_policy_report(run_plan)

    assert structurally_valid_but_source_unbound.warning_count == 1
    assert (
        structurally_valid_but_source_unbound.planned_duration_assessment
        != run_plan.planned_duration_assessment
    )
    assert source_consistent.planned_duration_assessment == run_plan.planned_duration_assessment
    assert source_consistent.planned_beat_pacing_warnings == (run_plan.planned_beat_pacing_warnings)
    assert source_consistent != structurally_valid_but_source_unbound


def test_direct_report_retains_noncanonical_warning_input_order() -> None:
    assessment = assess_mvp_duration_target(
        35.0,
        value_authority=DurationValueAuthority.PLANNED,
    )
    beat_02_warning = assess_beat_duration_pacing(
        beat_id="beat_02",
        actual_duration_seconds=6.0,
    )
    beat_01_warning = assess_beat_duration_pacing(
        beat_id="beat_01",
        actual_duration_seconds=2.5,
    )
    assert beat_02_warning is not None
    assert beat_01_warning is not None

    report = DurationPolicyReport(
        schema_version=1,
        planned_duration_assessment=assessment,
        planned_beat_pacing_warnings=(beat_02_warning, beat_01_warning),
        warning_count=2,
    )
    serialized = report.model_dump_json()
    reconstructed = DurationPolicyReport.model_validate_json(serialized)

    expected_ids = ["beat_02", "beat_01"]
    expected_durations = [6.0, 2.5]
    assert [warning.beat_id for warning in report.planned_beat_pacing_warnings] == expected_ids
    assert [
        warning.actual_duration_seconds for warning in report.planned_beat_pacing_warnings
    ] == expected_durations
    assert [
        warning["beat_id"] for warning in json.loads(serialized)["planned_beat_pacing_warnings"]
    ] == expected_ids
    assert [
        warning.beat_id for warning in reconstructed.planned_beat_pacing_warnings
    ] == expected_ids


def test_builder_rejects_unsafe_schema_two_and_stale_hash() -> None:
    run_plan = _run_with_durations((5.0,) * 7)

    with pytest.raises(
        ValueError,
        match="in-memory ProductionRunPlan authority requires schema_version 3",
    ):
        build_duration_policy_report(_unsafe_run_plan(run_plan, schema_version=2))
    with pytest.raises(ValidationError, match="planning_hash does not match"):
        build_duration_policy_report(_unsafe_run_plan(run_plan, planning_hash="0" * 64))


def test_builder_rejects_unsafe_schema_three_policy_records() -> None:
    run_plan = _run_with_durations((5.0,) * 7)
    assessment_payload = run_plan.planned_duration_assessment.model_dump(mode="python")
    assessment_payload["status"] = DurationAssessmentStatus.OUTSIDE_TARGET_WARNING
    unsafe_assessment = type(run_plan.planned_duration_assessment).model_construct(
        **assessment_payload
    )

    with pytest.raises(ValidationError):
        build_duration_policy_report(
            _unsafe_run_plan(
                run_plan,
                planned_duration_assessment=unsafe_assessment,
            )
        )

    warning_run = _run_with_durations((2.5, 6.25, 4.375, 4.375, 4.375, 4.375, 4.375, 4.375))
    with pytest.raises(ValidationError, match="warnings do not match StoryPlan"):
        build_duration_policy_report(
            _unsafe_run_plan(
                warning_run,
                planned_beat_pacing_warnings=(),
            )
        )


def test_builder_is_plan_only_and_preserves_locked_planning_hash() -> None:
    run_plan = _run_with_durations((5.0,) * 7)

    report = build_duration_policy_report(run_plan)

    assert isinstance(report, DurationPolicyReport)
    assert (
        run_plan.planning_hash == "31ef6b7caac5a8a513109274e1f2d159678630d4e495bf2634a5e8c5e97ee87d"
    )
    assert set(DurationPolicyReport.model_fields) == {
        "schema_version",
        "planned_duration_assessment",
        "planned_beat_pacing_warnings",
        "warning_count",
    }
