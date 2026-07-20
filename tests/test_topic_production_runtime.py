"""Phase 3A fail-closed execution, QC, acceptance, and resume tests."""
from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from tella.topic_production import (
    FailureReason,
    ExecutionRunState,
    GenerationAttempt,
    GenerationTier,
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
    build_fixture_preview_run,
    evaluate_execution_readiness,
    initialize_execution_state,
    plan_resume,
    promote_scene_to_acceptance,
    record_generation_attempt,
    record_human_qc,
    register_accepted_candidate,
    simulate_eight_scene_execution,
    summarize_call_budget,
)


def _state():
    return initialize_execution_state(
        build_fixture_preview_run(topic="offline runtime contract", job_id="runtime-test")
    )


def _attempt(state, scene_id: str, tier: GenerationTier, *, success: bool = True):
    scene = next(item for item in state.scenes if item.scene_id == scene_id)
    request = scene.execution_plan.draft if tier is GenerationTier.DRAFT else scene.execution_plan.acceptance
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
            TechnicalStatus.SUCCEEDED
            if success
            else TechnicalStatus.TECHNICAL_GENERATION_FAIL
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
    assert all(scene.status is ProductionSceneStatus.ACCEPTED for scene in result.final_state.scenes)


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
    promoted = {
        scene.scene_id for scene in result.final_state.scenes if scene.promotions
    }

    assert draft_accepts == {"scene_01", "scene_05", "scene_06"}
    assert promoted == {"scene_02", "scene_03", "scene_04", "scene_07", "scene_08"}
    assert result.final_call_budget.completed_calls == 13
    assert result.final_call_budget.retry_calls == 0
    assert result.final_call_budget.fallback_calls == 0
    assert result.external_calls == result.final_state.external_calls == 0
