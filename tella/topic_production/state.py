"""Pure production-scene lifecycle helpers and dual-tier decisions."""
from __future__ import annotations

from .models import (
    AcceptancePriority,
    CandidateArtifact,
    DualTierPolicy,
    GenerationTier,
    ProductionScene,
    ProductionSceneBrief,
    ProductionSceneStatus,
    SceneComplexity,
    SceneQCRecord,
    SceneTierDecision,
)


def tier_decision_for_brief(
    brief: ProductionSceneBrief, policy: DualTierPolicy
) -> SceneTierDecision:
    priority = brief.acceptance_priority
    dev_recommended = (
        brief.complexity is SceneComplexity.COMPLEX
        or priority in {AcceptancePriority.HIGH, AcceptancePriority.CONTINUITY_CRITICAL}
    )
    return SceneTierDecision(
        acceptance_priority=priority,
        dev_acceptance_recommended=dev_recommended,
        draft_only_acceptance_allowed_after_explicit_qc=(
            policy.simple_scene_may_accept_from_draft
            and brief.complexity is SceneComplexity.SIMPLE
            and not dev_recommended
        ),
        reason=(
            "complex, continuity-critical, or metaphorical scene"
            if dev_recommended
            else "standard scene; explicit QC still required"
        ),
    )


def initialize_scenes(
    briefs: list[ProductionSceneBrief], policy: DualTierPolicy
) -> list[ProductionScene]:
    return [
        ProductionScene(
            brief=brief,
            status=ProductionSceneStatus.DRAFT_PENDING,
            tier_decision=tier_decision_for_brief(brief, policy),
        )
        for brief in briefs
    ]


def record_draft_candidate(
    scene: ProductionScene, candidate: CandidateArtifact
) -> ProductionScene:
    if candidate.tier is not GenerationTier.DRAFT:
        raise ValueError("draft candidate must have draft tier")
    return scene.model_copy(
        update={
            "status": ProductionSceneStatus.DRAFT_GENERATED,
            "draft_candidate": candidate,
        },
        deep=True,
    )


def accept_candidate(
    scene: ProductionScene,
    candidate: CandidateArtifact,
    qc_record: SceneQCRecord,
) -> ProductionScene:
    if not qc_record.passed:
        raise ValueError("candidate cannot be accepted without passing explicit QC")
    if qc_record.tier is not candidate.tier:
        raise ValueError("QC tier must match candidate tier")
    if (
        candidate.tier is GenerationTier.DRAFT
        and not scene.tier_decision.draft_only_acceptance_allowed_after_explicit_qc
    ):
        raise ValueError("this scene requires acceptance-tier generation")
    updates: dict[str, object] = {
        "status": ProductionSceneStatus.ACCEPTED,
        "accepted_candidate": candidate,
        "accepted_source_tier": candidate.tier,
        "qc_records": [*scene.qc_records, qc_record],
    }
    if candidate.tier is GenerationTier.DRAFT:
        updates["draft_candidate"] = candidate
    else:
        updates["acceptance_candidate"] = candidate
    return scene.model_copy(update=updates, deep=True)
