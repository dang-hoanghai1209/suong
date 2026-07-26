"""Schema-only processed-narration measured-authority contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tella.topic_production import (
    DurationPolicyReport,
    ExecutionRunState,
    ProcessedNarrationDurationMeasurement,
    bind_processed_narration_measurement,
    build_duration_policy_report,
    build_fixture_preview_run,
    clear_processed_narration_measurement,
    initialize_execution_state,
    load_runtime_state,
    persist_execution_snapshot,
    production_job_paths,
)
from tella.topic_production.duration_policy import (
    DurationValueAuthority,
    assess_mvp_duration_target,
)
from tella.topic_production.execution_models import ProductionRunPlan
from tella.topic_production.runtime import _revalidate_execution_state
from tella.topic_production.story_plan_identity import canonical_story_plan_sha256


def _state(topic: str = "measured authority foundation") -> ExecutionRunState:
    return initialize_execution_state(
        build_fixture_preview_run(
            topic=topic,
            job_id=topic.replace(" ", "-"),
        )
    )


def _measurement(
    state: ExecutionRunState,
    *,
    duration: float = 35.0,
    path: str = "assets/narration.mp3",
    artifact_sha256: str = "a" * 64,
) -> ProcessedNarrationDurationMeasurement:
    return ProcessedNarrationDurationMeasurement(
        schema_version=1,
        artifact_relative_path=path,
        artifact_sha256=artifact_sha256,
        story_plan_sha256=canonical_story_plan_sha256(state.run_plan.story_plan),
        measurement_method="ffprobe_single_audio_stream_v1",
        measured_duration_assessment=assess_mvp_duration_target(
            duration,
            value_authority=DurationValueAuthority.MEASURED,
        ),
    )


@pytest.mark.parametrize("duration", [35.0, 29.75, 41.25])
def test_measurement_accepts_in_target_and_soft_outside_target_values(
    duration: float,
) -> None:
    state = _state()
    measurement = _measurement(state, duration=duration)

    assert measurement.measured_duration_assessment.actual_duration_seconds == duration
    assert (
        measurement.measured_duration_assessment.value_authority is DurationValueAuthority.MEASURED
    )


@pytest.mark.parametrize("schema_version", ["missing", None, True, 1.0, "1", 2])
def test_measurement_requires_exact_integer_schema_one(schema_version: object) -> None:
    payload = _measurement(_state()).model_dump(mode="python")
    if schema_version == "missing":
        payload.pop("schema_version")
    else:
        payload["schema_version"] = schema_version

    with pytest.raises(ValidationError):
        ProcessedNarrationDurationMeasurement.model_validate(payload)


@pytest.mark.parametrize(
    "missing_field",
    [
        "artifact_relative_path",
        "artifact_sha256",
        "story_plan_sha256",
        "measurement_method",
        "measured_duration_assessment",
    ],
)
def test_measurement_requires_every_authority_field(missing_field: str) -> None:
    payload = _measurement(_state()).model_dump(mode="python")
    payload.pop(missing_field)

    with pytest.raises(ValidationError):
        ProcessedNarrationDurationMeasurement.model_validate(payload)


def test_measurement_rejects_extra_fields_and_is_frozen() -> None:
    measurement = _measurement(_state())
    payload = measurement.model_dump(mode="python")
    payload["metadata"] = {}

    with pytest.raises(ValidationError):
        ProcessedNarrationDurationMeasurement.model_validate(payload)
    with pytest.raises(ValidationError):
        measurement.artifact_relative_path = "assets/replaced.mp3"


def test_measurement_model_copy_revalidates_every_update() -> None:
    measurement = _measurement(_state())

    assert measurement.model_copy() == measurement
    assert measurement.model_copy(deep=True) == measurement
    with pytest.raises(ValueError, match="schema_version cannot be changed"):
        measurement.model_copy(update={"schema_version": 1})
    with pytest.raises(ValidationError):
        measurement.model_copy(update={"artifact_sha256": "A" * 64})
    with pytest.raises(ValidationError):
        measurement.model_copy(update={"unexpected": "field"})


@pytest.mark.parametrize(
    "path",
    [
        "",
        " ",
        " assets/narration.mp3",
        "assets/narration.mp3 ",
        ".",
        "./assets/narration.mp3",
        "assets/../narration.mp3",
        "../assets/narration.mp3",
        "/assets/narration.mp3",
        r"C:\job\assets\narration.mp3",
        "C:/job/assets/narration.mp3",
        r"assets\narration.mp3",
        "assets//narration.mp3",
        "assets/narration.mp3/",
    ],
)
def test_measurement_rejects_noncanonical_or_unsafe_artifact_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        _measurement(_state(), path=path)


def test_measurement_uses_stable_forward_slash_relative_path_and_json() -> None:
    measurement = _measurement(_state(), path="assets/narration/final.mp3")
    restored = ProcessedNarrationDurationMeasurement.model_validate_json(
        measurement.model_dump_json()
    )

    assert restored == measurement
    assert restored.artifact_relative_path == "assets/narration/final.mp3"
    assert isinstance(json.loads(measurement.model_dump_json())["artifact_relative_path"], str)


@pytest.mark.parametrize(
    "digest",
    [
        "A" * 64,
        "a" * 63,
        "a" * 65,
        "g" * 64,
        ("a" * 63) + " ",
    ],
)
def test_measurement_requires_canonical_lowercase_sha256(digest: str) -> None:
    with pytest.raises(ValidationError):
        _measurement(_state(), artifact_sha256=digest)


def test_measurement_requires_measured_assessment_authority() -> None:
    state = _state()
    payload = _measurement(state).model_dump(mode="python")
    payload["measured_duration_assessment"] = assess_mvp_duration_target(
        35.0,
        value_authority=DurationValueAuthority.PLANNED,
    )

    with pytest.raises(ValidationError, match="requires MEASURED authority"):
        ProcessedNarrationDurationMeasurement.model_validate(payload)


@pytest.mark.parametrize("invalid_duration", [0.0, -1.0, float("nan"), float("inf")])
def test_measurement_revalidates_strict_finite_positive_duration(
    invalid_duration: float,
) -> None:
    payload = _measurement(_state()).model_dump(mode="python")
    payload["measured_duration_assessment"]["actual_duration_seconds"] = invalid_duration

    with pytest.raises(ValidationError):
        ProcessedNarrationDurationMeasurement.model_validate(payload)


def test_schema_two_requires_explicit_measurement_or_none() -> None:
    state = _state()
    assert state.schema_version == 2
    assert state.processed_narration_measurement is None

    payload = state.model_dump(mode="python")
    payload.pop("processed_narration_measurement")
    with pytest.raises(
        ValidationError,
        match="processed_narration_measurement is required",
    ):
        ExecutionRunState.model_validate(payload)
    with pytest.raises(ValidationError):
        state.processed_narration_measurement = _measurement(state)


@pytest.mark.parametrize("schema_version", [1, True, 2.0, "2", 3])
def test_current_state_requires_exact_integer_schema_two(schema_version: object) -> None:
    payload = _state().model_dump(mode="python")
    payload["schema_version"] = schema_version

    with pytest.raises(ValidationError):
        ExecutionRunState.model_validate(payload)


def test_state_rejects_measurement_from_another_story_plan() -> None:
    state = _state("first measured story")
    other = _state("second measured story")

    with pytest.raises(ValidationError, match="StoryPlan SHA-256 does not match"):
        state.model_copy(update={"processed_narration_measurement": _measurement(other)})


def test_state_revalidates_unsafe_nested_measurement() -> None:
    state = _state()
    valid = _measurement(state)
    assessment_payload = valid.measured_duration_assessment.model_dump(mode="python")
    assessment_payload["value_authority"] = DurationValueAuthority.PLANNED
    unsafe_assessment = type(valid.measured_duration_assessment).model_construct(
        **assessment_payload
    )
    measurement_payload = valid.model_dump(mode="python")
    measurement_payload["measured_duration_assessment"] = unsafe_assessment
    unsafe_measurement = ProcessedNarrationDurationMeasurement.model_construct(
        **measurement_payload
    )

    with pytest.raises(ValidationError, match="requires MEASURED authority"):
        state.model_copy(update={"processed_narration_measurement": unsafe_measurement})


def test_state_model_copy_fully_revalidates_updates() -> None:
    state = _state()
    measurement = _measurement(state)

    assert state.model_copy() == state
    assert state.model_copy(deep=True) == state
    assert (
        state.model_copy(
            update={"processed_narration_measurement": measurement.model_dump(mode="python")}
        ).processed_narration_measurement
        == measurement
    )

    with pytest.raises(ValueError, match="schema_version cannot be changed"):
        state.model_copy(update={"schema_version": 2})
    with pytest.raises(ValidationError):
        state.model_copy(update={"unexpected": "field"})
    with pytest.raises(ValidationError):
        state.model_copy(
            update={
                "processed_narration_measurement": {
                    **measurement.model_dump(mode="python"),
                    "artifact_sha256": "A" * 64,
                }
            }
        )
    run_plan_fields = {
        field_name: getattr(state.run_plan, field_name)
        for field_name in ProductionRunPlan.model_fields
    }
    run_plan_fields["planning_hash"] = "0" * 64
    unsafe_run_plan = ProductionRunPlan.model_construct(**run_plan_fields)
    with pytest.raises(ValidationError, match="planning_hash does not match"):
        state.model_copy(update={"run_plan": unsafe_run_plan})


def test_bind_is_pure_idempotent_and_conflict_requires_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    state_before = state.model_dump(mode="python")
    measurement = _measurement(state)
    measurement_before = measurement.model_dump(mode="python")
    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: pytest.fail("filesystem open"))
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda *_args, **_kwargs: pytest.fail("filesystem probe"),
    )

    bound = bind_processed_narration_measurement(state, measurement)
    repeated = bind_processed_narration_measurement(bound, measurement)

    assert bound.processed_narration_measurement == measurement
    assert repeated == bound
    assert repeated is not bound
    assert state.model_dump(mode="python") == state_before
    assert measurement.model_dump(mode="python") == measurement_before
    with pytest.raises(ValueError, match="clear it before replacement"):
        bind_processed_narration_measurement(
            bound,
            _measurement(state, duration=36.0, artifact_sha256="b" * 64),
        )


def test_clear_is_pure_and_idempotent() -> None:
    state = _state()
    bound = bind_processed_narration_measurement(state, _measurement(state))

    cleared = clear_processed_narration_measurement(bound)
    repeated = clear_processed_narration_measurement(cleared)

    assert cleared.processed_narration_measurement is None
    assert repeated == cleared
    assert repeated is not cleared
    assert bound.processed_narration_measurement is not None


def test_schema_one_migration_is_explicit_and_fabricates_no_measurement() -> None:
    current = _state()
    legacy = current.model_dump(mode="python")
    legacy["schema_version"] = 1
    legacy.pop("processed_narration_measurement")

    migrated = ExecutionRunState.migrate_schema_v1(legacy)

    assert migrated.schema_version == 2
    assert migrated.processed_narration_measurement is None
    assert migrated.run_plan == current.run_plan
    assert migrated.scenes == current.scenes
    assert migrated.event_history == current.event_history
    with pytest.raises(ValidationError):
        ExecutionRunState.model_validate(legacy)


@pytest.mark.parametrize("schema_version", ["missing", None, True, 1.0, "1", 2, 3])
def test_schema_one_migration_rejects_wrong_versions(schema_version: object) -> None:
    legacy = _state().model_dump(mode="python")
    legacy.pop("processed_narration_measurement")
    if schema_version == "missing":
        legacy.pop("schema_version")
    else:
        legacy["schema_version"] = schema_version

    with pytest.raises((TypeError, ValueError, ValidationError)):
        ExecutionRunState.migrate_schema_v1(legacy)


def test_schema_one_migration_rejects_future_fields_and_model_instances() -> None:
    current = _state()
    legacy = current.model_dump(mode="python")
    legacy["schema_version"] = 1
    legacy["processed_narration_measurement"] = None

    with pytest.raises(ValueError, match="cannot contain measured authority"):
        ExecutionRunState.migrate_schema_v1(legacy)
    with pytest.raises(TypeError, match="requires a raw mapping"):
        ExecutionRunState.migrate_schema_v1(current)


def test_load_is_the_automatic_schema_one_migration_boundary(tmp_path: Path) -> None:
    current = _state()
    legacy = current.model_dump(mode="json")
    legacy["schema_version"] = 1
    legacy.pop("processed_narration_measurement")
    runtime_path = tmp_path / "runtime_state.json"
    runtime_path.write_text(json.dumps(legacy), encoding="utf-8")

    restored = load_runtime_state(runtime_path)

    assert restored.schema_version == 2
    assert restored.processed_narration_measurement is None
    assert restored.run_plan == current.run_plan


def test_schema_one_in_memory_state_revalidation_and_persistence_reject(
    tmp_path: Path,
) -> None:
    current = _state()
    fields = {
        field_name: getattr(current, field_name)
        for field_name in ExecutionRunState.model_fields
        if field_name != "processed_narration_measurement"
    }
    fields["schema_version"] = 1
    unsafe = ExecutionRunState.model_construct(**fields)
    paths = production_job_paths(
        tmp_path,
        job_id=current.run_plan.job_id,
        scene_id="scene_01",
    )

    with pytest.raises(
        ValueError,
        match="in-memory ExecutionRunState authority requires schema_version 2",
    ):
        _revalidate_execution_state(unsafe)
    with pytest.raises(
        ValueError,
        match="in-memory ExecutionRunState authority requires schema_version 2",
    ):
        persist_execution_snapshot(
            unsafe,
            paths,
            execution_purpose="schema-one-rejection",
            selected_scene_id="scene_01",
        )
    assert not paths.job_dir.exists()


@pytest.mark.parametrize("schema_version", ["missing", None, True, 1.0, "1", 3])
def test_load_rejects_malformed_or_unknown_outer_schema(
    tmp_path: Path,
    schema_version: object,
) -> None:
    payload = _state().model_dump(mode="json")
    if schema_version == "missing":
        payload.pop("schema_version")
    else:
        payload["schema_version"] = schema_version
    runtime_path = tmp_path / "runtime_state.json"
    runtime_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        load_runtime_state(runtime_path)


def test_persistence_round_trips_null_and_bound_measurement(tmp_path: Path) -> None:
    state = _state()
    paths = production_job_paths(
        tmp_path,
        job_id=state.run_plan.job_id,
        scene_id="scene_01",
    )

    persist_execution_snapshot(
        state,
        paths,
        execution_purpose="measured-authority-foundation",
        selected_scene_id="scene_01",
    )
    absent_payload = json.loads(paths.runtime_state_path.read_text(encoding="utf-8"))
    absent_manifest = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
    assert absent_payload["schema_version"] == 2
    assert absent_payload["processed_narration_measurement"] is None
    assert load_runtime_state(paths.runtime_state_path) == state
    assert absent_manifest["duration_policy"]["schema_version"] == 2
    assert absent_manifest["duration_policy"]["measured_duration_assessment"] is None

    bound = bind_processed_narration_measurement(state, _measurement(state))
    persist_execution_snapshot(
        bound,
        paths,
        execution_purpose="measured-authority-foundation",
        selected_scene_id="scene_01",
    )
    assert load_runtime_state(paths.runtime_state_path) == bound
    bound_manifest = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
    assert bound_manifest["duration_policy"] == build_duration_policy_report(bound).model_dump(
        mode="json"
    )
    assert (
        DurationPolicyReport.model_validate(
            bound_manifest["duration_policy"]
        ).measured_duration_assessment
        == bound.processed_narration_measurement.measured_duration_assessment
    )
