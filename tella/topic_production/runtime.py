"""Pure, fail-closed execution/QC transitions with no provider operations."""
from __future__ import annotations

from typing import Any

from .models import GenerationTier, ProductionSceneStatus, ReadinessResult
from .runtime_models import (
    AcceptedCandidateRecord,
    CallBudgetSummary,
    DraftAcceptanceAuthorization,
    EventType,
    ExecutionEvent,
    ExecutionRunState,
    FailureReason,
    GenerationAttempt,
    PromotionReason,
    PromotionRecord,
    QCChecks,
    QCDecision,
    QCRecord,
    ResumeAction,
    ResumePlan,
    ReviewSource,
    SceneCallBudget,
    SceneResumePlan,
    SceneRuntimeState,
    TechnicalStatus,
)


def initialize_execution_state(run_plan) -> ExecutionRunState:
    scenes = [SceneRuntimeState(execution_plan=item) for item in run_plan.scene_execution_plans]
    events = [
        ExecutionEvent(
            sequence=index,
            scene_id=scene.scene_id,
            event_type=EventType.PLANNED,
            detail={"status": ProductionSceneStatus.DRAFT_PENDING.value},
        )
        for index, scene in enumerate(scenes, start=1)
    ]
    return ExecutionRunState(run_plan=run_plan, scenes=scenes, event_history=events)


def _scene_index(state: ExecutionRunState, scene_id: str) -> int:
    matches = [index for index, scene in enumerate(state.scenes) if scene.scene_id == scene_id]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one runtime scene for {scene_id}")
    return matches[0]


def _replace_scene(
    state: ExecutionRunState,
    index: int,
    scene: SceneRuntimeState,
    event_type: EventType,
    detail: dict[str, Any],
) -> ExecutionRunState:
    scenes = list(state.scenes)
    scenes[index] = scene
    event = ExecutionEvent(
        sequence=len(state.event_history) + 1,
        scene_id=scene.scene_id,
        event_type=event_type,
        detail=detail,
    )
    return state.model_copy(
        update={"scenes": scenes, "event_history": [*state.event_history, event]}, deep=True
    )


def _failure_reason(status: TechnicalStatus) -> FailureReason:
    return {
        TechnicalStatus.PROVIDER_QUOTA_BLOCKED: FailureReason.PROVIDER_QUOTA_BLOCKED,
        TechnicalStatus.TECHNICAL_GENERATION_FAIL: FailureReason.TECHNICAL_GENERATION_FAIL,
        TechnicalStatus.REFERENCE_BLOCKED: FailureReason.REFERENCE_BLOCKED,
    }[status]


def record_generation_attempt(
    state: ExecutionRunState, attempt: GenerationAttempt
) -> ExecutionRunState:
    index = _scene_index(state, attempt.scene_id)
    scene = state.scenes[index]
    if any(
        prior.candidate_id == attempt.candidate_id
        for candidate_scene in state.scenes
        for prior in candidate_scene.generation_attempts
    ):
        raise ValueError("candidate ID is already recorded")
    expected_status = (
        ProductionSceneStatus.DRAFT_PENDING
        if attempt.tier is GenerationTier.DRAFT
        else ProductionSceneStatus.ACCEPTANCE_PENDING
    )
    if scene.status is not expected_status:
        raise ValueError(
            f"{attempt.tier.value} generation is not authorized from {scene.status.value}"
        )
    if any(prior.tier is attempt.tier for prior in scene.generation_attempts):
        raise ValueError("call budget exhausted; retries are not authorized")
    request = (
        scene.execution_plan.draft
        if attempt.tier is GenerationTier.DRAFT
        else scene.execution_plan.acceptance
    )
    if (attempt.provider, attempt.model, attempt.seed) != (
        request.provider,
        request.model,
        request.seed,
    ):
        raise ValueError("generation attempt does not match its authorized request template")
    if attempt.logical_request_hash != scene.execution_plan.draft.logical_visual_request_hash:
        raise ValueError("generation attempt logical request hash does not match scene plan")
    expected_reference_hashes = [item.sha256 for item in request.references]
    if attempt.reference_hashes != expected_reference_hashes:
        raise ValueError("generation attempt reference hashes do not match scene plan")
    if attempt.technical_status is TechnicalStatus.SUCCEEDED:
        status = (
            ProductionSceneStatus.DRAFT_GENERATED
            if attempt.tier is GenerationTier.DRAFT
            else ProductionSceneStatus.ACCEPTANCE_GENERATED
        )
        event_type = (
            EventType.DRAFT_GENERATED
            if attempt.tier is GenerationTier.DRAFT
            else EventType.ACCEPTANCE_GENERATED
        )
        reasons = scene.block_reasons
    else:
        status = ProductionSceneStatus.BLOCKED
        event_type = (
            EventType.DRAFT_GENERATION_FAILED
            if attempt.tier is GenerationTier.DRAFT
            else EventType.ACCEPTANCE_GENERATION_FAILED
        )
        reasons = list(dict.fromkeys([*scene.block_reasons, _failure_reason(attempt.technical_status)]))
    updated = scene.model_copy(
        update={
            "status": status,
            "generation_attempts": [*scene.generation_attempts, attempt],
            "block_reasons": reasons,
        },
        deep=True,
    )
    return _replace_scene(
        state,
        index,
        updated,
        event_type,
        {
            "candidate_id": attempt.candidate_id,
            "tier": attempt.tier.value,
            "technical_status": attempt.technical_status.value,
        },
    )


def record_qc(state: ExecutionRunState, record: QCRecord) -> ExecutionRunState:
    index = _scene_index(state, record.scene_id)
    scene = state.scenes[index]
    attempt = next(
        (
            item
            for item in scene.generation_attempts
            if item.candidate_id == record.candidate_id and item.tier is record.tier
        ),
        None,
    )
    if attempt is None:
        raise ValueError("QC candidate is not a recorded generation attempt for this scene/tier")
    if attempt.technical_status is not TechnicalStatus.SUCCEEDED:
        raise ValueError("QC cannot approve a technically failed generation attempt")
    expected_status = (
        ProductionSceneStatus.DRAFT_GENERATED
        if record.tier is GenerationTier.DRAFT
        else ProductionSceneStatus.ACCEPTANCE_GENERATED
    )
    if scene.status is not expected_status:
        raise ValueError(f"QC is not valid from scene status {scene.status.value}")
    if any(item.qc_record_id == record.qc_record_id for item in scene.qc_records):
        raise ValueError("QC record ID is already recorded")
    reasons = list(scene.block_reasons)
    if record.decision is QCDecision.PASS:
        status = (
            ProductionSceneStatus.DRAFT_QC_PASS
            if record.tier is GenerationTier.DRAFT
            else ProductionSceneStatus.ACCEPTANCE_QC_PASS
        )
        reasons = [item for item in reasons if item is not FailureReason.HUMAN_REVIEW_REQUIRED]
    elif record.decision is QCDecision.FAIL:
        status = (
            ProductionSceneStatus.DRAFT_QC_FAIL
            if record.tier is GenerationTier.DRAFT
            else ProductionSceneStatus.ACCEPTANCE_QC_FAIL
        )
        failure = (
            FailureReason.DRAFT_QC_FAIL
            if record.tier is GenerationTier.DRAFT
            else FailureReason.ACCEPTANCE_QC_FAIL
        )
        reasons = list(dict.fromkeys([*reasons, failure]))
    elif record.decision is QCDecision.BLOCKED:
        status = ProductionSceneStatus.BLOCKED
        reasons = list(dict.fromkeys([*reasons, FailureReason.HUMAN_REVIEW_REQUIRED]))
    else:
        status = expected_status
        reasons = list(dict.fromkeys([*reasons, FailureReason.HUMAN_REVIEW_REQUIRED]))
    updated = scene.model_copy(
        update={
            "status": status,
            "qc_records": [*scene.qc_records, record],
            "block_reasons": reasons,
        },
        deep=True,
    )
    event = EventType.DRAFT_QC if record.tier is GenerationTier.DRAFT else EventType.ACCEPTANCE_QC
    return _replace_scene(
        state,
        index,
        updated,
        event,
        {
            "qc_record_id": record.qc_record_id,
            "candidate_id": record.candidate_id,
            "decision": record.decision.value,
            "review_source": record.review_source.value,
        },
    )


def record_human_qc(
    state: ExecutionRunState,
    *,
    qc_record_id: str,
    scene_id: str,
    candidate_id: str,
    tier: GenerationTier,
    decision: QCDecision,
    reviewer: str,
    checks: QCChecks | None = None,
    scores: dict[str, float] | None = None,
    hard_fail_reasons: list[str] | None = None,
    soft_fail_reasons: list[str] | None = None,
    notes: str = "",
    review_metadata: dict[str, Any] | None = None,
) -> ExecutionRunState:
    return record_qc(
        state,
        QCRecord(
            qc_record_id=qc_record_id,
            scene_id=scene_id,
            candidate_id=candidate_id,
            tier=tier,
            decision=decision,
            checks=checks or QCChecks(),
            scores=scores or {},
            hard_fail_reasons=hard_fail_reasons or [],
            soft_fail_reasons=soft_fail_reasons or [],
            review_source=ReviewSource.HUMAN,
            reviewer=reviewer,
            notes=notes,
            review_metadata=review_metadata or {},
        ),
    )


def promote_scene_to_acceptance(
    state: ExecutionRunState,
    *,
    scene_id: str,
    reason: PromotionReason,
    authorized_by: str,
    metadata: dict[str, Any] | None = None,
) -> ExecutionRunState:
    index = _scene_index(state, scene_id)
    scene = state.scenes[index]
    if scene.status not in {
        ProductionSceneStatus.DRAFT_QC_PASS,
        ProductionSceneStatus.DRAFT_QC_FAIL,
        ProductionSceneStatus.DRAFT_GENERATED,
    }:
        raise ValueError(f"scene cannot be promoted from {scene.status.value}")
    draft_attempts = [item for item in scene.generation_attempts if item.tier is GenerationTier.DRAFT]
    draft_qc = [item for item in scene.qc_records if item.tier is GenerationTier.DRAFT]
    if not draft_attempts or not draft_qc:
        raise ValueError("promotion requires a recorded draft attempt and draft QC history")
    if scene.promotions:
        raise ValueError("scene is already promoted")
    promotion = PromotionRecord(
        scene_id=scene_id,
        reason=reason,
        authorized_by=authorized_by,
        metadata=metadata or {},
    )
    updated = scene.model_copy(
        update={
            "status": ProductionSceneStatus.ACCEPTANCE_PENDING,
            "promotions": [promotion],
            "block_reasons": [
                item for item in scene.block_reasons if item is not FailureReason.PROMOTION_REQUIRED
            ],
        },
        deep=True,
    )
    return _replace_scene(
        state,
        index,
        updated,
        EventType.PROMOTED,
        {"reason": reason.value, "authorized_by": authorized_by},
    )


def authorize_draft_acceptance(
    state: ExecutionRunState,
    *,
    scene_id: str,
    reason: str,
    authorized_by: str,
    metadata: dict[str, Any] | None = None,
) -> ExecutionRunState:
    index = _scene_index(state, scene_id)
    scene = state.scenes[index]
    if scene.status is not ProductionSceneStatus.DRAFT_QC_PASS:
        raise ValueError("draft acceptance eligibility requires DRAFT_QC_PASS")
    if scene.draft_acceptance_authorizations:
        raise ValueError("draft acceptance is already explicitly authorized")
    authorization = DraftAcceptanceAuthorization(
        scene_id=scene_id,
        reason=reason,
        authorized_by=authorized_by,
        metadata=metadata or {},
    )
    updated = scene.model_copy(
        update={"draft_acceptance_authorizations": [authorization]}, deep=True
    )
    return _replace_scene(
        state,
        index,
        updated,
        EventType.DRAFT_ACCEPTANCE_AUTHORIZED,
        {"reason": reason, "authorized_by": authorized_by},
    )


def register_accepted_candidate(
    state: ExecutionRunState,
    *,
    scene_id: str,
    candidate_id: str,
    qc_record_id: str,
    accepted_by: str,
    accepted_at: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ExecutionRunState:
    index = _scene_index(state, scene_id)
    scene = state.scenes[index]
    if scene.accepted_candidate is not None:
        raise ValueError("accepted candidate cannot be silently replaced")
    attempt = next(
        (item for item in scene.generation_attempts if item.candidate_id == candidate_id), None
    )
    if attempt is None:
        raise ValueError("candidate is not a recorded attempt for this scene")
    if attempt.technical_status is not TechnicalStatus.SUCCEEDED:
        raise ValueError("failed candidate cannot be accepted")
    qc = next((item for item in scene.qc_records if item.qc_record_id == qc_record_id), None)
    if qc is None or qc.candidate_id != candidate_id or qc.tier is not attempt.tier:
        raise ValueError("QC evidence does not correspond to candidate and source tier")
    if qc.decision is not QCDecision.PASS:
        raise ValueError("candidate requires corresponding QC PASS evidence")
    expected_status = (
        ProductionSceneStatus.DRAFT_QC_PASS
        if attempt.tier is GenerationTier.DRAFT
        else ProductionSceneStatus.ACCEPTANCE_QC_PASS
    )
    if scene.status is not expected_status:
        raise ValueError(f"candidate cannot be accepted from {scene.status.value}")
    if attempt.tier is GenerationTier.DRAFT and not scene.draft_acceptance_authorizations:
        raise ValueError("draft candidate requires explicit draft-acceptance authorization")
    if attempt.tier is GenerationTier.ACCEPTANCE and not scene.promotions:
        raise ValueError("acceptance candidate requires explicit promotion history")
    assert attempt.candidate_path is not None
    assert attempt.artifact_sha256 is not None
    accepted = AcceptedCandidateRecord(
        scene_id=scene_id,
        candidate_id=candidate_id,
        artifact_path=attempt.candidate_path,
        artifact_sha256=attempt.artifact_sha256,
        source_tier=attempt.tier,
        provider=attempt.provider,
        model=attempt.model,
        seed=attempt.seed,
        logical_request_hash=attempt.logical_request_hash,
        provider_request_hash=attempt.provider_request_hash,
        reference_hashes=attempt.reference_hashes,
        qc_record_id=qc_record_id,
        accepted_by=accepted_by,
        accepted_at=accepted_at,
        metadata=metadata or {},
    )
    updated = scene.model_copy(
        update={
            "status": ProductionSceneStatus.ACCEPTED,
            "accepted_candidate": accepted,
            "block_reasons": [],
        },
        deep=True,
    )
    return _replace_scene(
        state,
        index,
        updated,
        EventType.ACCEPTED,
        {
            "candidate_id": candidate_id,
            "source_tier": attempt.tier.value,
            "qc_record_id": qc_record_id,
            "accepted_by": accepted_by,
        },
    )


def block_scene(
    state: ExecutionRunState, *, scene_id: str, reason: FailureReason
) -> ExecutionRunState:
    index = _scene_index(state, scene_id)
    scene = state.scenes[index]
    updated = scene.model_copy(
        update={
            "status": ProductionSceneStatus.BLOCKED,
            "block_reasons": list(dict.fromkeys([*scene.block_reasons, reason])),
        },
        deep=True,
    )
    return _replace_scene(
        state, index, updated, EventType.BLOCKED, {"reason": reason.value}
    )


def evaluate_execution_readiness(state: ExecutionRunState) -> ReadinessResult:
    reasons: dict[str, list[str]] = {}
    for scene in state.scenes:
        scene_reasons: list[str] = []
        if scene.status is not ProductionSceneStatus.ACCEPTED:
            scene_reasons.append(f"scene status is {scene.status.value}, not ACCEPTED")
        accepted = scene.accepted_candidate
        if accepted is None:
            scene_reasons.append("accepted candidate registration is missing")
        else:
            attempt = next(
                (
                    item
                    for item in scene.generation_attempts
                    if item.candidate_id == accepted.candidate_id
                ),
                None,
            )
            if attempt is None:
                scene_reasons.append("accepted candidate has no recorded generation attempt")
            else:
                if not accepted.artifact_path or accepted.artifact_path != attempt.candidate_path:
                    scene_reasons.append("accepted artifact path is missing or mismatched")
                if accepted.artifact_sha256 != attempt.artifact_sha256:
                    scene_reasons.append("accepted artifact SHA-256 is missing or mismatched")
                if accepted.source_tier is not attempt.tier:
                    scene_reasons.append("accepted source tier is missing or mismatched")
                if (
                    accepted.provider != attempt.provider
                    or accepted.model != attempt.model
                    or accepted.seed != attempt.seed
                    or accepted.logical_request_hash != attempt.logical_request_hash
                    or accepted.provider_request_hash != attempt.provider_request_hash
                    or accepted.reference_hashes != attempt.reference_hashes
                ):
                    scene_reasons.append("accepted candidate provenance does not match attempt")
            qc = next(
                (item for item in scene.qc_records if item.qc_record_id == accepted.qc_record_id),
                None,
            )
            if (
                qc is None
                or qc.decision is not QCDecision.PASS
                or qc.candidate_id != accepted.candidate_id
                or qc.tier is not accepted.source_tier
            ):
                scene_reasons.append("matching QC PASS evidence is missing")
        if scene.block_reasons:
            scene_reasons.extend(f"unresolved block: {item.value}" for item in scene.block_reasons)
        if scene_reasons:
            reasons[scene.scene_id] = scene_reasons
    return ReadinessResult(
        ready=bool(state.scenes) and not reasons,
        unresolved_scene_ids=list(reasons),
        reasons=reasons,
    )


def summarize_call_budget(state: ExecutionRunState) -> CallBudgetSummary:
    budgets: list[SceneCallBudget] = []
    for scene in state.scenes:
        draft_completed = sum(
            item.tier is GenerationTier.DRAFT for item in scene.generation_attempts
        )
        acceptance_completed = sum(
            item.tier is GenerationTier.ACCEPTANCE for item in scene.generation_attempts
        )
        budgets.append(
            SceneCallBudget(
                scene_id=scene.scene_id,
                acceptance_max_calls=1 if scene.promotions else 0,
                draft_completed_calls=draft_completed,
                acceptance_completed_calls=acceptance_completed,
            )
        )
    authorized = sum(item.acceptance_max_calls for item in budgets)
    completed = sum(
        item.draft_completed_calls + item.acceptance_completed_calls for item in budgets
    )
    remaining = sum(
        item.draft_max_calls
        + item.acceptance_max_calls
        - item.draft_completed_calls
        - item.acceptance_completed_calls
        for item in budgets
    )
    return CallBudgetSummary(
        scenes=budgets,
        planned_draft_calls=len(budgets),
        currently_authorized_acceptance_calls=authorized,
        completed_calls=completed,
        remaining_authorized_calls=remaining,
    )


def plan_resume(state: ExecutionRunState) -> ResumePlan:
    action_by_status = {
        ProductionSceneStatus.DRAFT_PENDING: ResumeAction.EXECUTE_DRAFT,
        ProductionSceneStatus.DRAFT_GENERATED: ResumeAction.AWAIT_DRAFT_QC,
        ProductionSceneStatus.DRAFT_QC_PASS: ResumeAction.AWAIT_EXPLICIT_TIER_DECISION,
        ProductionSceneStatus.DRAFT_QC_FAIL: ResumeAction.AWAIT_EXPLICIT_INTERVENTION,
        ProductionSceneStatus.ACCEPTANCE_PENDING: ResumeAction.EXECUTE_ACCEPTANCE,
        ProductionSceneStatus.ACCEPTANCE_GENERATED: ResumeAction.AWAIT_ACCEPTANCE_QC,
        ProductionSceneStatus.ACCEPTANCE_QC_PASS: ResumeAction.READY_FOR_EXPLICIT_ACCEPTANCE,
        ProductionSceneStatus.ACCEPTANCE_QC_FAIL: ResumeAction.AWAIT_EXPLICIT_INTERVENTION,
        ProductionSceneStatus.ACCEPTED: ResumeAction.SKIP_ACCEPTED,
        ProductionSceneStatus.BLOCKED: ResumeAction.REMAIN_BLOCKED,
        ProductionSceneStatus.PLANNED: ResumeAction.EXECUTE_DRAFT,
    }
    plans: list[SceneResumePlan] = []
    for scene in state.scenes:
        action = action_by_status[scene.status]
        if (
            scene.status is ProductionSceneStatus.DRAFT_QC_PASS
            and scene.draft_acceptance_authorizations
        ):
            action = ResumeAction.READY_FOR_EXPLICIT_ACCEPTANCE
        plans.append(
            SceneResumePlan(
                scene_id=scene.scene_id,
                action=action,
                reason=f"persisted status {scene.status.value}; no automatic retry or fallback",
            )
        )
    return ResumePlan(scenes=plans)
