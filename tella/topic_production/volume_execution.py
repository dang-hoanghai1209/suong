"""Deterministic one-shot QC, acceptance, and hard-fail retry policy for Volume."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from tella.visual_generation.providers.kinds import ProviderKind

from .models import GenerationTier, ProductionSceneStatus
from .runtime import (
    authorize_draft_acceptance,
    block_scene,
    record_qc,
    register_accepted_candidate,
)
from .runtime_models import (
    ExecutionRunState,
    FailureReason,
    QCCheckOutcome,
    QCDecision,
    QCRecord,
)
from .strategy import (
    ProductionStrategy,
    SceneDataSensitivity,
    VolumeQCAction,
    VolumeQCSeverity,
    volume_qc_disposition,
)


class VolumeCandidateDisposition(StrEnum):
    ACCEPTABLE_FOR_VOLUME = "acceptable_for_volume"
    HARD_FAIL = "hard_fail"
    REVIEW_REQUIRED = "review_required"


class VolumeAcceptanceReason(StrEnum):
    VOLUME_USABLE = "VOLUME_USABLE"
    SOFT_FAIL_ACCEPTED = "SOFT_FAIL_ACCEPTED"


class VolumeNextAction(StrEnum):
    ACCEPTED = "accepted"
    RETRY_CLOUDFLARE = "retry_cloudflare"
    BLOCK = "block"
    AWAIT_REVIEW = "await_review"


class VolumeRetryDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed: bool
    provider: ProviderKind | None = None
    reason: str = Field(min_length=1)
    scene_retries_used: int = Field(ge=0)
    run_retries_used: int = Field(ge=0)


class VolumeQCTransition(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: ExecutionRunState
    disposition: VolumeCandidateDisposition
    next_action: VolumeNextAction
    acceptance_reason: VolumeAcceptanceReason | None = None
    retry_provider: ProviderKind | None = None
    retry_budget_required: bool = False
    reason: str = Field(min_length=1)


def classify_volume_qc(record: QCRecord) -> VolumeCandidateDisposition:
    """Use existing QC decision/reason fields without inventing quality thresholds."""

    if record.hard_fail_reasons:
        return VolumeCandidateDisposition.HARD_FAIL
    if record.checks.technical_generation is not QCCheckOutcome.PASS:
        return VolumeCandidateDisposition.REVIEW_REQUIRED
    if record.decision in {QCDecision.BLOCKED, QCDecision.NEEDS_REVIEW}:
        return VolumeCandidateDisposition.REVIEW_REQUIRED
    if record.decision is QCDecision.FAIL and not record.soft_fail_reasons:
        return VolumeCandidateDisposition.HARD_FAIL
    return VolumeCandidateDisposition.ACCEPTABLE_FOR_VOLUME


def _volume_scene(state: ExecutionRunState, scene_id: str):
    if state.run_plan.production_strategy.strategy is not ProductionStrategy.VOLUME:
        raise ValueError("Volume execution transition requires Volume strategy")
    scene = next((item for item in state.scenes if item.scene_id == scene_id), None)
    if scene is None:
        raise ValueError(f"unknown scene ID: {scene_id}")
    if scene.execution_plan.local_execution is None:
        raise ValueError("Volume execution requires a sensitivity-aware scene plan")
    return scene


def volume_retry_decision(
    state: ExecutionRunState,
    *,
    scene_id: str,
    candidate_id: str,
) -> VolumeRetryDecision:
    scene = _volume_scene(state, scene_id)
    policy = state.run_plan.production_strategy.volume_policy
    assert policy is not None
    attempt = next(
        (item for item in scene.generation_attempts if item.candidate_id == candidate_id),
        None,
    )
    if attempt is None:
        raise ValueError("Volume retry candidate is not a recorded attempt")
    qc = next(
        (item for item in reversed(scene.qc_records) if item.candidate_id == candidate_id),
        None,
    )
    if qc is None or classify_volume_qc(qc) is not VolumeCandidateDisposition.HARD_FAIL:
        raise ValueError("Volume retry requires a recorded QC hard failure")
    scene_retries = sum(item.consumes_ai_retry for item in scene.generation_attempts)
    run_retries = sum(
        item.consumes_ai_retry
        for runtime_scene in state.scenes
        for item in runtime_scene.generation_attempts
    )
    disposition = volume_qc_disposition(
        VolumeQCSeverity.HARD_FAIL,
        scene_hard_retries_used=scene_retries,
        run_ai_retries_used=run_retries,
        policy=policy,
    )
    if disposition.action is not VolumeQCAction.RETRY_OR_REROUTE:
        return VolumeRetryDecision(
            allowed=False,
            reason=disposition.reason,
            scene_retries_used=scene_retries,
            run_retries_used=run_retries,
        )
    sensitivity = scene.execution_plan.local_execution.sensitivity
    if sensitivity is SceneDataSensitivity.LOCAL_ONLY:
        return VolumeRetryDecision(
            allowed=False,
            reason="LOCAL_ONLY hard failure cannot use an external retry",
            scene_retries_used=scene_retries,
            run_retries_used=run_retries,
        )
    if attempt.provider == ProviderKind.POLLINATIONS.value:
        return VolumeRetryDecision(
            allowed=False,
            reason="Pollinations hard failure exhausts the one-shot overflow route",
            scene_retries_used=scene_retries,
            run_retries_used=run_retries,
        )
    return VolumeRetryDecision(
        allowed=True,
        provider=ProviderKind.CLOUDFLARE_KLEIN_4B,
        reason=(
            "Volume QC hard failure authorizes exactly one Cloudflare retry; "
            "visual quality does not authorize Pollinations overflow"
        ),
        scene_retries_used=scene_retries,
        run_retries_used=run_retries,
    )


def complete_volume_acceptance(
    state: ExecutionRunState,
    *,
    scene_id: str,
) -> ExecutionRunState:
    """Resume or complete acceptance through the existing authoritative registry."""

    scene = _volume_scene(state, scene_id)
    if scene.accepted_candidate is not None:
        return state
    if scene.status is not ProductionSceneStatus.DRAFT_QC_PASS:
        raise ValueError("Volume acceptance requires a recorded acceptable draft QC")
    qc = next(
        (
            item
            for item in reversed(scene.qc_records)
            if item.tier is GenerationTier.DRAFT and item.decision is QCDecision.PASS
        ),
        None,
    )
    if qc is None or qc.hard_fail_reasons:
        raise ValueError("Volume acceptance requires QC PASS without hard-failure reasons")
    attempt = next(
        (
            item
            for item in reversed(scene.generation_attempts)
            if item.candidate_id == qc.candidate_id
        ),
        None,
    )
    if attempt is None:
        raise ValueError("Volume acceptance QC has no matching candidate")
    reason = (
        VolumeAcceptanceReason.SOFT_FAIL_ACCEPTED
        if qc.soft_fail_reasons
        else VolumeAcceptanceReason.VOLUME_USABLE
    )
    updated = state
    if not scene.draft_acceptance_authorizations:
        updated = authorize_draft_acceptance(
            updated,
            scene_id=scene_id,
            reason=reason.value,
            authorized_by="volume_policy",
            metadata={
                "volume_disposition": VolumeCandidateDisposition.ACCEPTABLE_FOR_VOLUME.value,
                "soft_fail_reasons": list(qc.soft_fail_reasons),
            },
        )
    return register_accepted_candidate(
        updated,
        scene_id=scene_id,
        candidate_id=attempt.candidate_id,
        qc_record_id=qc.qc_record_id,
        accepted_by="volume_policy",
        metadata={
            "volume_acceptance_reason": reason.value,
            "volume_disposition": VolumeCandidateDisposition.ACCEPTABLE_FOR_VOLUME.value,
        },
    )


def apply_volume_qc(
    state: ExecutionRunState,
    record: QCRecord,
) -> VolumeQCTransition:
    """Record QC, accept usable work, or expose one explicit hard-fail retry action."""

    scene = _volume_scene(state, record.scene_id)
    if record.tier is not GenerationTier.DRAFT:
        raise ValueError("Volume mode never enters the premium acceptance tier")
    if scene.accepted_candidate is not None:
        raise ValueError("accepted Volume scene cannot be evaluated again")
    disposition = classify_volume_qc(record)
    if disposition is VolumeCandidateDisposition.ACCEPTABLE_FOR_VOLUME:
        reason = (
            VolumeAcceptanceReason.SOFT_FAIL_ACCEPTED
            if record.soft_fail_reasons
            else VolumeAcceptanceReason.VOLUME_USABLE
        )
        normalized = record.model_copy(
            update={
                "decision": QCDecision.PASS,
                "review_metadata": {
                    **record.review_metadata,
                    "original_qc_decision": record.decision.value,
                    "volume_disposition": disposition.value,
                    "volume_acceptance_reason": reason.value,
                },
            },
            deep=True,
        )
        updated = record_qc(state, normalized)
        accepted = complete_volume_acceptance(updated, scene_id=record.scene_id)
        return VolumeQCTransition(
            state=accepted,
            disposition=disposition,
            next_action=VolumeNextAction.ACCEPTED,
            acceptance_reason=reason,
            reason="usable Volume candidate accepted without regeneration",
        )
    annotated = record.model_copy(
        update={
            "review_metadata": {
                **record.review_metadata,
                "volume_disposition": disposition.value,
            }
        },
        deep=True,
    )
    updated = record_qc(state, annotated)
    if disposition is VolumeCandidateDisposition.REVIEW_REQUIRED:
        return VolumeQCTransition(
            state=updated,
            disposition=disposition,
            next_action=VolumeNextAction.AWAIT_REVIEW,
            reason="QC is incomplete or blocked; Volume fails closed without regeneration",
        )
    retry = volume_retry_decision(
        updated,
        scene_id=record.scene_id,
        candidate_id=record.candidate_id,
    )
    if retry.allowed:
        return VolumeQCTransition(
            state=updated,
            disposition=disposition,
            next_action=VolumeNextAction.RETRY_CLOUDFLARE,
            retry_provider=retry.provider,
            retry_budget_required=True,
            reason=retry.reason,
        )
    blocked = block_scene(updated, scene_id=record.scene_id, reason=FailureReason.DRAFT_QC_FAIL)
    return VolumeQCTransition(
        state=blocked,
        disposition=disposition,
        next_action=VolumeNextAction.BLOCK,
        reason=retry.reason,
    )


__all__ = [
    "VolumeAcceptanceReason",
    "VolumeCandidateDisposition",
    "VolumeNextAction",
    "VolumeQCTransition",
    "VolumeRetryDecision",
    "apply_volume_qc",
    "classify_volume_qc",
    "complete_volume_acceptance",
    "volume_retry_decision",
]
