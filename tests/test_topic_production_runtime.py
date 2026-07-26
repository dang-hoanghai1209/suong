"""Phase 3A fail-closed execution, QC, acceptance, and resume tests."""

from __future__ import annotations

import hashlib
import inspect
import json

import pytest
from pydantic import ValidationError

from tella.topic_production import (
    DurationPolicyReport,
    FailureReason,
    ExecutionRunState,
    GenerationAttempt,
    GenerationTier,
    ProductionRunPlan,
    ProductionSceneStatus,
    PromotionReason,
    QCCheckOutcome,
    QCChecks,
    QCDecision,
    QCRecord,
    ResumeAction,
    ReviewSource,
    TechnicalStatus,
    authorize_draft_acceptance,
    block_scene,
    build_duration_policy_report,
    build_fixture_preview_run,
    build_production_run_plan,
    build_scene_briefs,
    evaluate_execution_readiness,
    initialize_execution_state,
    load_runtime_state,
    load_reference_catalog,
    persist_execution_snapshot,
    plan_resume,
    production_job_paths,
    promote_scene_to_acceptance,
    record_generation_attempt,
    record_human_qc,
    register_accepted_candidate,
    simulate_eight_scene_execution,
    summarize_call_budget,
)
from tella.topic_production.duration_policy import DurationAssessmentStatus
from tella.topic_production.planner import DeterministicTopicPlanner
import tella.topic_production.persistence as persistence_module


def _state():
    return initialize_execution_state(
        build_fixture_preview_run(topic="offline runtime contract", job_id="runtime-test")
    )


def _state_with_durations(durations: tuple[float, ...]) -> ExecutionRunState:
    story = DeterministicTopicPlanner().plan(
        topic="runtime duration projection",
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
        job_id="runtime-duration-projection",
        story_plan=story,
        scene_briefs=build_scene_briefs(story),
        reference_catalog=load_reference_catalog(None),
    )
    return initialize_execution_state(run_plan)


def _unsafe_run_plan() -> ProductionRunPlan:
    run_plan = build_fixture_preview_run(
        topic="unsafe nested runtime contract",
        job_id="unsafe-runtime-test",
    )
    assessment_payload = run_plan.planned_duration_assessment.model_dump(mode="python")
    assessment_payload["status"] = DurationAssessmentStatus.OUTSIDE_TARGET_WARNING
    unsafe_assessment = type(run_plan.planned_duration_assessment).model_construct(
        **assessment_payload
    )
    fields = {
        field_name: getattr(run_plan, field_name) for field_name in ProductionRunPlan.model_fields
    }
    fields["planned_duration_assessment"] = unsafe_assessment
    return ProductionRunPlan.model_construct(**fields)


def _unsafe_schema_v2_run_plan() -> ProductionRunPlan:
    run_plan = build_fixture_preview_run(
        topic="unsafe in-memory schema two contract",
        job_id="unsafe-schema-two-runtime-test",
    )
    fields = {
        field_name: getattr(run_plan, field_name)
        for field_name in ProductionRunPlan.model_fields
        if field_name
        not in {
            "planned_duration_assessment",
            "planned_beat_pacing_warnings",
        }
    }
    fields["schema_version"] = 2
    return ProductionRunPlan.model_construct(**fields)


def _attempt(state, scene_id: str, tier: GenerationTier, *, success: bool = True):
    scene = next(item for item in state.scenes if item.scene_id == scene_id)
    request = (
        scene.execution_plan.draft
        if tier is GenerationTier.DRAFT
        else scene.execution_plan.acceptance
    )
    candidate_id = f"{scene_id}-{tier.value}-candidate"
    return GenerationAttempt(
        scene_id=scene_id,
        tier=tier,
        provider=request.provider,
        model=request.model,
        seed=request.seed,
        candidate_id=candidate_id,
        candidate_path=f"fixtures/{candidate_id}.png" if success else None,
        artifact_sha256=hashlib.sha256(candidate_id.encode()).hexdigest() if success else None,
        logical_request_hash=scene.execution_plan.draft.logical_visual_request_hash,
        reference_hashes=[item.sha256 for item in request.references],
        technical_status=(
            TechnicalStatus.SUCCEEDED if success else TechnicalStatus.TECHNICAL_GENERATION_FAIL
        ),
        technical_failure_reason=None if success else "synthetic transport failure",
        simulated=True,
    )


def _checks() -> QCChecks:
    return QCChecks(
        technical_generation=QCCheckOutcome.PASS,
        scene_meaning=QCCheckOutcome.PASS,
        identity=QCCheckOutcome.PASS,
        action_pose=QCCheckOutcome.PASS,
        anatomy=QCCheckOutcome.PASS,
        composition=QCCheckOutcome.PASS,
        reference_consistency=QCCheckOutcome.PASS,
    )


def test_runtime_initialization_revalidates_nested_run_plan_authority() -> None:
    with pytest.raises(ValidationError):
        initialize_execution_state(_unsafe_run_plan())


def test_runtime_initialization_rejects_in_memory_schema_two() -> None:
    with pytest.raises(
        ValueError,
        match="in-memory ProductionRunPlan authority requires schema_version 3",
    ):
        initialize_execution_state(_unsafe_schema_v2_run_plan())


def test_resume_revalidates_nested_run_plan_authority() -> None:
    state = _state()
    unsafe_state = state.model_copy(
        update={"run_plan": _unsafe_run_plan()},
        deep=True,
    )

    with pytest.raises(ValidationError):
        plan_resume(unsafe_state)


def test_resume_rejects_in_memory_schema_two() -> None:
    state = _state()
    unsafe_state = state.model_copy(
        update={"run_plan": _unsafe_schema_v2_run_plan()},
        deep=True,
    )

    with pytest.raises(
        ValueError,
        match="in-memory ProductionRunPlan authority requires schema_version 3",
    ):
        plan_resume(unsafe_state)


def test_persistence_revalidates_before_creating_files(tmp_path) -> None:
    state = _state()
    unsafe_state = state.model_copy(
        update={"run_plan": _unsafe_run_plan()},
        deep=True,
    )
    paths = production_job_paths(
        tmp_path,
        job_id="unsafe-runtime-test",
        scene_id="scene_01",
    )

    with pytest.raises(ValidationError):
        persist_execution_snapshot(
            unsafe_state,
            paths,
            execution_purpose="authority-boundary-test",
            selected_scene_id="scene_01",
        )

    assert not paths.job_dir.exists()


def test_persistence_rejects_in_memory_schema_two_without_filesystem_effects(
    tmp_path,
) -> None:
    state = _state()
    paths = production_job_paths(
        tmp_path / "new",
        job_id="unsafe-schema-two-runtime-test",
        scene_id="scene_01",
    )
    unsafe_state = state.model_copy(
        update={"run_plan": _unsafe_schema_v2_run_plan()},
        deep=True,
    )

    with pytest.raises(
        ValueError,
        match="in-memory ProductionRunPlan authority requires schema_version 3",
    ):
        persist_execution_snapshot(
            unsafe_state,
            paths,
            execution_purpose="authority-boundary-test",
            selected_scene_id="scene_01",
        )

    assert not paths.job_dir.exists()

    existing_paths = production_job_paths(
        tmp_path / "existing",
        job_id=state.run_plan.job_id,
        scene_id="scene_01",
    )
    persist_execution_snapshot(
        state,
        existing_paths,
        execution_purpose="authority-boundary-test",
        selected_scene_id="scene_01",
    )
    expected = {
        path: path.read_bytes()
        for path in (
            existing_paths.run_plan_path,
            existing_paths.runtime_state_path,
            existing_paths.manifest_path,
        )
    }
    with pytest.raises(
        ValueError,
        match="in-memory ProductionRunPlan authority requires schema_version 3",
    ):
        persist_execution_snapshot(
            unsafe_state,
            existing_paths,
            execution_purpose="authority-boundary-test",
            selected_scene_id="scene_01",
        )

    assert {path: path.read_bytes() for path in expected} == expected
    assert not list(existing_paths.job_dir.rglob("*.tmp"))


@pytest.mark.parametrize(
    "durations",
    [
        (5.0,) * 7,
        (4.0,) * 7,
        (2.5, 6.25, 4.375, 4.375, 4.375, 4.375, 4.375, 4.375),
    ],
    ids=["in-target", "outside-target", "ordered-beat-warnings"],
)
def test_runtime_manifest_contains_exact_canonical_duration_policy_projection(
    tmp_path,
    durations: tuple[float, ...],
) -> None:
    state = _state_with_durations(durations)
    paths = production_job_paths(
        tmp_path,
        job_id=state.run_plan.job_id,
        scene_id="scene_01",
    )

    persist_execution_snapshot(
        state,
        paths,
        execution_purpose="duration-policy-projection-test",
        selected_scene_id="scene_01",
    )

    manifest = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
    expected = build_duration_policy_report(state.run_plan).model_dump(mode="json")
    assert manifest["schema_version"] == 1
    assert manifest["duration_policy"] == expected
    assert manifest["duration_policy"]["schema_version"] == 1
    assert manifest["duration_policy"]["planned_duration_assessment"] == (
        state.run_plan.planned_duration_assessment.model_dump(mode="json")
    )
    assert manifest["duration_policy"]["planned_beat_pacing_warnings"] == [
        warning.model_dump(mode="json") for warning in state.run_plan.planned_beat_pacing_warnings
    ]
    assert manifest["duration_policy"]["warning_count"] == expected["warning_count"]
    assert "measured_duration_assessment" not in manifest["duration_policy"]
    assert "estimated_duration_assessment" not in manifest["duration_policy"]
    assert "has_warnings" not in manifest["duration_policy"]
    assert "duration_policy" not in type(state).model_fields
    assert "duration_policy" not in type(state.run_plan.manifest).model_fields
    assert "duration_policy" not in inspect.signature(persist_execution_snapshot).parameters

    persist_execution_snapshot(
        state,
        paths,
        execution_purpose="duration-policy-projection-test",
        selected_scene_id="scene_01",
    )
    repeated = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
    assert repeated["duration_policy"] == expected


@pytest.mark.parametrize(
    "failure_stage",
    ["builder", "report-serialization", "json-serialization"],
)
@pytest.mark.parametrize("existing_snapshot", [False, True], ids=["new", "existing"])
def test_duration_policy_failure_occurs_before_every_filesystem_side_effect(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
    existing_snapshot: bool,
) -> None:
    state = _state()
    state_before = state.model_copy(deep=True)
    paths = production_job_paths(
        tmp_path,
        job_id=state.run_plan.job_id,
        scene_id="scene_01",
    )
    expected: dict[object, bytes] = {}
    if existing_snapshot:
        persist_execution_snapshot(
            state,
            paths,
            execution_purpose="duration-policy-preflight-test",
            selected_scene_id="scene_01",
        )
        expected = {
            path: path.read_bytes()
            for path in (
                paths.run_plan_path,
                paths.runtime_state_path,
                paths.manifest_path,
            )
        }

    if failure_stage == "builder":

        def fail_builder(_run_plan):
            raise RuntimeError("injected duration-policy builder failure")

        monkeypatch.setattr(
            persistence_module,
            "build_duration_policy_report",
            fail_builder,
        )
        expected_message = "injected duration-policy builder failure"
    elif failure_stage == "report-serialization":

        def fail_report_serialization(_self, *args, **kwargs):
            raise RuntimeError("injected duration-policy serialization failure")

        monkeypatch.setattr(
            DurationPolicyReport,
            "model_dump",
            fail_report_serialization,
        )
        expected_message = "injected duration-policy serialization failure"
    else:

        def fail_json_serialization(*args, **kwargs):
            raise RuntimeError("injected JSON serialization failure")

        monkeypatch.setattr(persistence_module.json, "dumps", fail_json_serialization)
        expected_message = "injected JSON serialization failure"

    with pytest.raises(RuntimeError, match=expected_message):
        persist_execution_snapshot(
            state,
            paths,
            execution_purpose="duration-policy-preflight-test",
            selected_scene_id="scene_01",
        )

    assert state == state_before
    if existing_snapshot:
        assert {path: path.read_bytes() for path in expected} == expected
        assert not list(paths.job_dir.rglob("*.tmp"))
    else:
        assert not paths.job_dir.exists()


@pytest.mark.parametrize("existing_snapshot", [False, True], ids=["new", "existing"])
def test_utf8_encoding_failure_precedes_every_filesystem_side_effect(
    tmp_path,
    existing_snapshot: bool,
) -> None:
    state = _state()
    state_before = state.model_copy(deep=True)
    paths = production_job_paths(
        tmp_path,
        job_id=state.run_plan.job_id,
        scene_id="scene_01",
    )
    valid_metadata = {
        "status": "original",
        "detail": "valid UTF-8 metadata",
    }
    expected: dict[str, bytes] = {}
    if existing_snapshot:
        persist_execution_snapshot(
            state,
            paths,
            execution_purpose="duration-policy-utf8-preflight-test",
            selected_scene_id="scene_01",
            candidate_metadata=valid_metadata,
        )
        expected = {
            str(path.relative_to(paths.job_dir)): path.read_bytes()
            for path in paths.job_dir.rglob("*")
            if path.is_file()
        }
        candidate_relative_path = str(paths.candidate_metadata_path.relative_to(paths.job_dir))
        assert candidate_relative_path in expected
        assert expected[candidate_relative_path] == json.dumps(
            valid_metadata,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        manifest = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
        assert manifest["duration_policy"] == build_duration_policy_report(
            state.run_plan
        ).model_dump(mode="json")

    with pytest.raises(UnicodeEncodeError):
        persist_execution_snapshot(
            state,
            paths,
            execution_purpose="changed-purpose",
            selected_scene_id="scene_01",
            candidate_metadata={"value": "\ud800"},
        )

    assert state == state_before
    if existing_snapshot:
        actual = {
            str(path.relative_to(paths.job_dir)): path.read_bytes()
            for path in paths.job_dir.rglob("*")
            if path.is_file()
        }
        assert actual == expected
        assert set(actual) == set(expected)
        assert not any(path.name.endswith(".tmp") for path in paths.job_dir.rglob("*"))
    else:
        assert not paths.job_dir.exists()


def test_persistence_rejects_stale_planning_hash_before_filesystem_effects(
    tmp_path,
) -> None:
    state = _state()
    run_plan_fields = {
        field_name: getattr(state.run_plan, field_name)
        for field_name in ProductionRunPlan.model_fields
    }
    run_plan_fields["planning_hash"] = "0" * 64
    unsafe_run_plan = ProductionRunPlan.model_construct(**run_plan_fields)
    unsafe_state = state.model_copy(update={"run_plan": unsafe_run_plan}, deep=True)
    paths = production_job_paths(
        tmp_path,
        job_id=state.run_plan.job_id,
        scene_id="scene_01",
    )

    with pytest.raises(ValidationError, match="planning_hash does not match"):
        persist_execution_snapshot(
            unsafe_state,
            paths,
            execution_purpose="duration-policy-preflight-test",
            selected_scene_id="scene_01",
        )

    assert not paths.job_dir.exists()


@pytest.mark.parametrize(
    "manifest_variant",
    ["missing", "malformed", "forged", "invalid-json"],
)
def test_runtime_load_and_resume_ignore_manifest_duration_policy(
    tmp_path,
    manifest_variant: str,
) -> None:
    state = _state_with_durations((4.0,) * 7)
    paths = production_job_paths(
        tmp_path,
        job_id=state.run_plan.job_id,
        scene_id="scene_01",
    )
    persist_execution_snapshot(
        state,
        paths,
        execution_purpose="duration-policy-compatibility-test",
        selected_scene_id="scene_01",
    )
    expected_resume = plan_resume(state)

    if manifest_variant == "invalid-json":
        paths.manifest_path.write_text("{not-json", encoding="utf-8")
    else:
        manifest = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
        if manifest_variant == "missing":
            manifest.pop("duration_policy")
        elif manifest_variant == "malformed":
            manifest["duration_policy"] = "not-a-report"
        else:
            manifest["duration_policy"] = {
                "schema_version": 99,
                "planned_duration_assessment": None,
                "planned_beat_pacing_warnings": [],
                "warning_count": -1,
            }
        paths.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    restored = load_runtime_state(paths.runtime_state_path)

    assert restored == state
    assert plan_resume(restored) == expected_resume
    assert (
        restored.run_plan.planned_duration_assessment.status
        is DurationAssessmentStatus.OUTSIDE_TARGET_WARNING
    )


def test_persistence_rebuilds_and_overwrites_stale_duration_policy(
    tmp_path,
) -> None:
    state = _state_with_durations((2.5, 6.25, 4.375, 4.375, 4.375, 4.375, 4.375, 4.375))
    paths = production_job_paths(
        tmp_path,
        job_id=state.run_plan.job_id,
        scene_id="scene_01",
    )
    persist_execution_snapshot(
        state,
        paths,
        execution_purpose="duration-policy-rebuild-test",
        selected_scene_id="scene_01",
    )
    manifest = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
    copied = build_duration_policy_report(_state_with_durations((4.0,) * 7).run_plan).model_dump(
        mode="json"
    )
    copied["schema_version"] = 99
    copied["warning_count"] = 999
    copied["planned_duration_assessment"]["policy_id"] = "forged-policy"
    copied["planned_beat_pacing_warnings"] = list(
        reversed(manifest["duration_policy"]["planned_beat_pacing_warnings"])
    )
    manifest["duration_policy"] = copied
    paths.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    persist_execution_snapshot(
        state,
        paths,
        execution_purpose="duration-policy-rebuild-test",
        selected_scene_id="scene_01",
    )

    rebuilt = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
    expected = build_duration_policy_report(state.run_plan).model_dump(mode="json")
    assert rebuilt["duration_policy"] == expected


def test_persisted_nested_schema_v2_runtime_json_migrates_to_schema_three(
    tmp_path,
) -> None:
    state = _state()
    payload = state.model_dump(mode="json")
    payload["run_plan"]["schema_version"] = 2
    payload["run_plan"].pop("planned_duration_assessment")
    payload["run_plan"].pop("planned_beat_pacing_warnings")
    runtime_path = tmp_path / "runtime_state.json"
    runtime_path.write_text(json.dumps(payload), encoding="utf-8")

    restored = load_runtime_state(runtime_path)

    assert restored.run_plan.schema_version == 3
    assert (
        restored.run_plan.planned_duration_assessment == state.run_plan.planned_duration_assessment
    )
    assert (
        restored.run_plan.planned_beat_pacing_warnings
        == state.run_plan.planned_beat_pacing_warnings
    )


def test_persisted_nested_schema_three_runtime_json_loads_strictly(tmp_path) -> None:
    state = _state()
    runtime_path = tmp_path / "runtime_state.json"
    runtime_path.write_text(state.model_dump_json(), encoding="utf-8")

    restored = load_runtime_state(runtime_path)

    assert restored == state
    assert restored.run_plan.schema_version == 3


@pytest.mark.parametrize("schema_version", ["missing", 1, 4, "2"])
def test_persisted_runtime_rejects_missing_or_unsupported_nested_schema(
    tmp_path,
    schema_version: object,
) -> None:
    payload = _state().model_dump(mode="json")
    if schema_version == "missing":
        payload["run_plan"].pop("schema_version")
    else:
        payload["run_plan"]["schema_version"] = schema_version
    runtime_path = tmp_path / "runtime_state.json"
    runtime_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        load_runtime_state(runtime_path)


def test_persisted_schema_two_rejects_malformed_legacy_source(tmp_path) -> None:
    payload = _state().model_dump(mode="json")
    payload["run_plan"]["schema_version"] = 2
    payload["run_plan"].pop("planned_duration_assessment")
    payload["run_plan"].pop("planned_beat_pacing_warnings")
    payload["run_plan"]["story_plan"]["target_duration_seconds"] = 1.0
    runtime_path = tmp_path / "runtime_state.json"
    runtime_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_runtime_state(runtime_path)


def test_corrupted_persisted_runtime_json_is_rejected(tmp_path) -> None:
    payload = _state().model_dump(mode="json")
    payload["run_plan"]["planning_hash"] = "0" * 64
    runtime_path = tmp_path / "runtime_state.json"
    runtime_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="planning_hash does not match"):
        load_runtime_state(runtime_path)


def _record_draft_pass(state, scene_id: str = "scene_01"):
    attempt = _attempt(state, scene_id, GenerationTier.DRAFT)
    state = record_generation_attempt(state, attempt)
    state = record_human_qc(
        state,
        qc_record_id=f"qc-{attempt.candidate_id}",
        scene_id=scene_id,
        candidate_id=attempt.candidate_id,
        tier=GenerationTier.DRAFT,
        decision=QCDecision.PASS,
        reviewer="human-test-reviewer",
        checks=_checks(),
    )
    return state, attempt


def _accept_draft(state, scene_id: str = "scene_01"):
    state, attempt = _record_draft_pass(state, scene_id)
    state = authorize_draft_acceptance(
        state,
        scene_id=scene_id,
        reason="explicit fixture authorization",
        authorized_by="test-operator",
    )
    state = register_accepted_candidate(
        state,
        scene_id=scene_id,
        candidate_id=attempt.candidate_id,
        qc_record_id=f"qc-{attempt.candidate_id}",
        accepted_by="test-operator",
    )
    return state


def test_generation_success_does_not_auto_accept() -> None:
    state = _state()
    state = record_generation_attempt(state, _attempt(state, "scene_01", GenerationTier.DRAFT))
    scene = state.scenes[0]

    assert scene.status is ProductionSceneStatus.DRAFT_GENERATED
    assert scene.accepted_candidate is None


def test_human_draft_qc_pass_does_not_auto_accept_or_promote() -> None:
    state, _ = _record_draft_pass(_state())
    scene = state.scenes[0]

    assert scene.status is ProductionSceneStatus.DRAFT_QC_PASS
    assert scene.accepted_candidate is None
    assert scene.promotions == []
    assert scene.execution_plan.acceptance_policy.dev_acceptance_recommended


def test_deterministic_structural_qc_cannot_claim_subjective_pass() -> None:
    with pytest.raises(ValidationError, match="cannot claim subjective visual PASS"):
        QCRecord(
            qc_record_id="structural-pass",
            scene_id="scene_01",
            candidate_id="candidate",
            tier=GenerationTier.DRAFT,
            decision=QCDecision.PASS,
            review_source=ReviewSource.DETERMINISTIC_STRUCTURAL,
            reviewer="structural-fixture",
        )


def test_promotion_is_explicit_and_requires_draft_qc_history() -> None:
    with pytest.raises(ValueError, match="cannot be promoted"):
        promote_scene_to_acceptance(
            _state(),
            scene_id="scene_01",
            reason=PromotionReason.HIGH_ACCEPTANCE_PRIORITY,
            authorized_by="operator",
        )
    state, _ = _record_draft_pass(_state())
    promoted = promote_scene_to_acceptance(
        state,
        scene_id="scene_01",
        reason=PromotionReason.HIGH_ACCEPTANCE_PRIORITY,
        authorized_by="operator",
    )

    assert promoted.scenes[0].status is ProductionSceneStatus.ACCEPTANCE_PENDING
    assert promoted.scenes[0].promotions[0].reason is PromotionReason.HIGH_ACCEPTANCE_PRIORITY


def test_acceptance_generation_and_human_qc_pass_still_do_not_auto_accept() -> None:
    state, _ = _record_draft_pass(_state())
    state = promote_scene_to_acceptance(
        state,
        scene_id="scene_01",
        reason=PromotionReason.HUMAN_REQUESTED_UPGRADE,
        authorized_by="operator",
    )
    acceptance = _attempt(state, "scene_01", GenerationTier.ACCEPTANCE)
    state = record_generation_attempt(state, acceptance)
    assert state.scenes[0].status is ProductionSceneStatus.ACCEPTANCE_GENERATED
    assert state.scenes[0].accepted_candidate is None
    state = record_human_qc(
        state,
        qc_record_id="acceptance-qc",
        scene_id="scene_01",
        candidate_id=acceptance.candidate_id,
        tier=GenerationTier.ACCEPTANCE,
        decision=QCDecision.PASS,
        reviewer="human-reviewer",
        checks=_checks(),
    )
    assert state.scenes[0].status is ProductionSceneStatus.ACCEPTANCE_QC_PASS
    assert state.scenes[0].accepted_candidate is None


def test_draft_acceptance_requires_separate_authorization_and_registration() -> None:
    state, attempt = _record_draft_pass(_state())
    with pytest.raises(ValueError, match="explicit draft-acceptance authorization"):
        register_accepted_candidate(
            state,
            scene_id="scene_01",
            candidate_id=attempt.candidate_id,
            qc_record_id=f"qc-{attempt.candidate_id}",
            accepted_by="operator",
        )
    state = authorize_draft_acceptance(
        state,
        scene_id="scene_01",
        reason="explicitly eligible after human review",
        authorized_by="operator",
    )
    accepted = register_accepted_candidate(
        state,
        scene_id="scene_01",
        candidate_id=attempt.candidate_id,
        qc_record_id=f"qc-{attempt.candidate_id}",
        accepted_by="operator",
    )
    assert accepted.scenes[0].status is ProductionSceneStatus.ACCEPTED
    assert accepted.scenes[0].accepted_candidate.source_tier is GenerationTier.DRAFT


def test_candidate_from_wrong_scene_or_unknown_candidate_cannot_be_accepted() -> None:
    state, _ = _record_draft_pass(_state())
    state = authorize_draft_acceptance(
        state,
        scene_id="scene_01",
        reason="test",
        authorized_by="operator",
    )
    with pytest.raises(ValueError, match="not a recorded attempt"):
        register_accepted_candidate(
            state,
            scene_id="scene_02",
            candidate_id="scene_01-draft-candidate",
            qc_record_id="qc-scene_01-draft-candidate",
            accepted_by="operator",
        )


def test_candidate_without_matching_qc_pass_cannot_be_accepted() -> None:
    state = _state()
    attempt = _attempt(state, "scene_01", GenerationTier.DRAFT)
    state = record_generation_attempt(state, attempt)
    with pytest.raises(ValueError, match="QC evidence"):
        register_accepted_candidate(
            state,
            scene_id="scene_01",
            candidate_id=attempt.candidate_id,
            qc_record_id="missing-qc",
            accepted_by="operator",
        )


def test_failed_candidate_cannot_be_accepted_and_scene_is_honestly_blocked() -> None:
    state = _state()
    failed = _attempt(state, "scene_01", GenerationTier.DRAFT, success=False)
    state = record_generation_attempt(state, failed)

    assert state.scenes[0].status is ProductionSceneStatus.BLOCKED
    assert FailureReason.TECHNICAL_GENERATION_FAIL in state.scenes[0].block_reasons
    with pytest.raises(ValueError, match="failed candidate"):
        register_accepted_candidate(
            state,
            scene_id="scene_01",
            candidate_id=failed.candidate_id,
            qc_record_id="no-qc",
            accepted_by="operator",
        )


def test_accepted_candidate_cannot_be_silently_replaced() -> None:
    state = _accept_draft(_state())
    accepted = state.scenes[0].accepted_candidate
    assert accepted is not None
    with pytest.raises(ValueError, match="silently replaced"):
        register_accepted_candidate(
            state,
            scene_id="scene_01",
            candidate_id=accepted.candidate_id,
            qc_record_id=accepted.qc_record_id,
            accepted_by="another-operator",
        )


@pytest.mark.parametrize(
    ("tamper", "expected"),
    [
        ("path", "artifact path"),
        ("tier", "source tier"),
        ("qc", "QC PASS evidence"),
    ],
)
def test_readiness_rejects_tampered_accepted_registration(tamper: str, expected: str) -> None:
    result = simulate_eight_scene_execution()
    state = result.final_state
    scene = state.scenes[0]
    accepted = scene.accepted_candidate
    assert accepted is not None
    if tamper == "path":
        scene = scene.model_copy(
            update={"accepted_candidate": accepted.model_copy(update={"artifact_path": ""})}
        )
    elif tamper == "tier":
        scene = scene.model_copy(
            update={"accepted_candidate": accepted.model_copy(update={"source_tier": None})}
        )
    else:
        scene = scene.model_copy(update={"qc_records": []})
    scenes = list(state.scenes)
    scenes[0] = scene
    tampered = state.model_copy(update={"scenes": scenes})

    readiness = evaluate_execution_readiness(tampered)
    assert not readiness.ready
    assert expected in " ".join(readiness.reasons["scene_01"])


def test_seven_of_eight_blocks_and_eight_valid_acceptances_are_ready() -> None:
    result = simulate_eight_scene_execution()

    assert not result.readiness_before_last_acceptance.ready
    assert result.readiness_before_last_acceptance.unresolved_scene_ids == ["scene_08"]
    assert result.final_readiness.ready
    assert all(
        scene.status is ProductionSceneStatus.ACCEPTED for scene in result.final_state.scenes
    )


def test_call_budget_authorizes_one_draft_no_retry_and_acceptance_only_after_promotion() -> None:
    state = _state()
    initial = summarize_call_budget(state)
    assert initial.planned_draft_calls == 8
    assert initial.currently_authorized_acceptance_calls == 0
    assert initial.retry_calls == initial.fallback_calls == 0
    state, _ = _record_draft_pass(state)
    state = promote_scene_to_acceptance(
        state,
        scene_id="scene_01",
        reason=PromotionReason.HIGH_ACCEPTANCE_PRIORITY,
        authorized_by="operator",
    )
    promoted = summarize_call_budget(state)
    assert promoted.currently_authorized_acceptance_calls == 1
    assert promoted.completed_calls == 1
    assert promoted.remaining_authorized_calls == 8


def test_second_draft_attempt_is_not_an_automatic_retry() -> None:
    state = _state()
    state = record_generation_attempt(state, _attempt(state, "scene_01", GenerationTier.DRAFT))
    second = _attempt(state, "scene_01", GenerationTier.DRAFT).model_copy(
        update={"candidate_id": "second-draft-candidate"}
    )
    with pytest.raises(ValueError, match="not authorized"):
        record_generation_attempt(state, second)


def test_resume_planner_skips_accepted_waits_for_qc_and_preserves_blocked() -> None:
    state = _accept_draft(_state(), "scene_01")
    state = record_generation_attempt(state, _attempt(state, "scene_02", GenerationTier.DRAFT))
    state = block_scene(state, scene_id="scene_03", reason=FailureReason.REFERENCE_BLOCKED)
    resume = {item.scene_id: item.action for item in plan_resume(state).scenes}

    assert resume["scene_01"] is ResumeAction.SKIP_ACCEPTED
    assert resume["scene_02"] is ResumeAction.AWAIT_DRAFT_QC
    assert resume["scene_03"] is ResumeAction.REMAIN_BLOCKED
    assert resume["scene_04"] is ResumeAction.EXECUTE_DRAFT


def test_serialized_runtime_state_resumes_without_regeneration_or_reset() -> None:
    state = _accept_draft(_state(), "scene_01")
    state = record_generation_attempt(state, _attempt(state, "scene_02", GenerationTier.DRAFT))
    restored = ExecutionRunState.model_validate_json(state.model_dump_json())
    resume = {item.scene_id: item.action for item in plan_resume(restored).scenes}

    assert resume["scene_01"] is ResumeAction.SKIP_ACCEPTED
    assert resume["scene_02"] is ResumeAction.AWAIT_DRAFT_QC
    assert restored.event_history == state.event_history


def test_event_history_is_ordered_auditable_and_credential_free() -> None:
    result = simulate_eight_scene_execution()
    events = result.final_state.event_history
    serialized = result.final_state.model_dump_json()

    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert events[0].event_type.value == "planned"
    assert events[-1].event_type.value == "accepted"
    assert "credential" not in serialized.casefold()
    assert "api_key" not in serialized.casefold()


def test_offline_simulation_has_expected_split_budget_and_zero_external_calls() -> None:
    result = simulate_eight_scene_execution()
    draft_accepts = {
        scene.scene_id
        for scene in result.final_state.scenes
        if scene.accepted_candidate is not None
        and scene.accepted_candidate.source_tier is GenerationTier.DRAFT
    }
    promoted = {scene.scene_id for scene in result.final_state.scenes if scene.promotions}

    assert draft_accepts == {"scene_01", "scene_05", "scene_06"}
    assert promoted == {"scene_02", "scene_03", "scene_04", "scene_07", "scene_08"}
    assert result.final_call_budget.completed_calls == 13
    assert result.final_call_budget.retry_calls == 0
    assert result.final_call_budget.fallback_calls == 0
    assert result.external_calls == result.final_state.external_calls == 0
