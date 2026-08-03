"""Deterministic, metadata-only Phase 3A lifecycle simulation."""
from __future__ import annotations

import hashlib

from .execution import build_fixture_preview_run
from .models import GenerationTier
from .runtime import (
    authorize_draft_acceptance,
    evaluate_execution_readiness,
    initialize_execution_state,
    promote_scene_to_acceptance,
    record_generation_attempt,
    record_human_qc,
    register_accepted_candidate,
    summarize_call_budget,
)
from .runtime_models import (
    GenerationAttempt,
    OfflineSimulationResult,
    PromotionReason,
    QCCheckOutcome,
    QCChecks,
    QCDecision,
    TechnicalStatus,
)

_DRAFT_ACCEPTANCE_SCENES = {"scene_01", "scene_05", "scene_06"}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _attempt(state, scene_id: str, tier: GenerationTier) -> GenerationAttempt:
    scene = next(item for item in state.scenes if item.scene_id == scene_id)
    request = scene.execution_plan.draft if tier is GenerationTier.DRAFT else scene.execution_plan.acceptance
    candidate_id = f"{scene_id}-{tier.value}-fixture"
    return GenerationAttempt(
        scene_id=scene_id,
        tier=tier,
        provider=request.provider,
        model=request.model,
        seed=request.seed,
        candidate_id=candidate_id,
        candidate_path=f"fixture-artifacts/{candidate_id}.png",
        artifact_sha256=_digest(f"artifact:{candidate_id}"),
        logical_request_hash=scene.execution_plan.draft.logical_visual_request_hash,
        reference_hashes=[item.sha256 for item in request.references],
        technical_status=TechnicalStatus.SUCCEEDED,
        simulated=True,
        metadata={"fixture_only": True},
    )


def _passing_checks() -> QCChecks:
    return QCChecks(
        technical_generation=QCCheckOutcome.PASS,
        scene_meaning=QCCheckOutcome.PASS,
        identity=QCCheckOutcome.PASS,
        action_pose=QCCheckOutcome.PASS,
        anatomy=QCCheckOutcome.PASS,
        composition=QCCheckOutcome.PASS,
        reference_consistency=QCCheckOutcome.PASS,
    )


def simulate_eight_scene_execution(
    *, topic: str = "offline Phase 3A lifecycle fixture"
) -> OfflineSimulationResult:
    """Exercise explicit transitions; the split is fixture data, never policy."""
    state = initialize_execution_state(
        build_fixture_preview_run(topic=topic, scene_count=8, job_id="phase-3a-simulation")
    )
    for scene_id in [item.scene_id for item in state.scenes]:
        attempt = _attempt(state, scene_id, GenerationTier.DRAFT)
        state = record_generation_attempt(state, attempt)
        state = record_human_qc(
            state,
            qc_record_id=f"qc-{attempt.candidate_id}",
            scene_id=scene_id,
            candidate_id=attempt.candidate_id,
            tier=GenerationTier.DRAFT,
            decision=QCDecision.PASS,
            reviewer="fixture-human-reviewer",
            checks=_passing_checks(),
            notes="synthetic Phase 3A lifecycle evidence only",
        )
    for scene_id in [item.scene_id for item in state.scenes]:
        if scene_id in _DRAFT_ACCEPTANCE_SCENES:
            state = authorize_draft_acceptance(
                state,
                scene_id=scene_id,
                reason="fixture-only explicit draft eligibility",
                authorized_by="fixture-operator",
                metadata={"not_production_policy": True},
            )
            continue
        state = promote_scene_to_acceptance(
            state,
            scene_id=scene_id,
            reason=PromotionReason.HUMAN_REQUESTED_UPGRADE,
            authorized_by="fixture-operator",
            metadata={"not_production_policy": True},
        )
        attempt = _attempt(state, scene_id, GenerationTier.ACCEPTANCE)
        state = record_generation_attempt(state, attempt)
        state = record_human_qc(
            state,
            qc_record_id=f"qc-{attempt.candidate_id}",
            scene_id=scene_id,
            candidate_id=attempt.candidate_id,
            tier=GenerationTier.ACCEPTANCE,
            decision=QCDecision.PASS,
            reviewer="fixture-human-reviewer",
            checks=_passing_checks(),
            notes="synthetic Phase 3A lifecycle evidence only",
        )
    for scene in state.scenes:
        if scene.scene_id == "scene_08":
            continue
        tier = (
            GenerationTier.DRAFT
            if scene.scene_id in _DRAFT_ACCEPTANCE_SCENES
            else GenerationTier.ACCEPTANCE
        )
        candidate_id = f"{scene.scene_id}-{tier.value}-fixture"
        state = register_accepted_candidate(
            state,
            scene_id=scene.scene_id,
            candidate_id=candidate_id,
            qc_record_id=f"qc-{candidate_id}",
            accepted_by="fixture-operator",
            metadata={"not_production_policy": True},
        )
    before = state
    before_readiness = evaluate_execution_readiness(before)
    final_candidate = "scene_08-acceptance-fixture"
    state = register_accepted_candidate(
        state,
        scene_id="scene_08",
        candidate_id=final_candidate,
        qc_record_id=f"qc-{final_candidate}",
        accepted_by="fixture-operator",
        metadata={"not_production_policy": True},
    )
    return OfflineSimulationResult(
        state_before_last_acceptance=before,
        final_state=state,
        readiness_before_last_acceptance=before_readiness,
        final_readiness=evaluate_execution_readiness(state),
        final_call_budget=summarize_call_budget(state),
    )
