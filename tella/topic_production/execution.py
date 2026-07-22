"""Pure orchestration for deterministic pre-generation production run plans."""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path

from tella.visual_generation.providers.cloudflare_flux import DEFAULT_HEIGHT, DEFAULT_WIDTH
from tella.visual_generation.providers.kinds import ProviderKind
from tella.visual_generation.tiers import VisualQualityTier, resolve_visual_tier

from .execution_models import (
    AcceptancePolicyDecision,
    AcceptanceRequestTemplate,
    DraftRequestPlan,
    ExecutionMode,
    ProductionRunPlan,
    ReferenceCatalog,
    ReferenceDecisionStatus,
    SceneExecutionPlan,
    SceneRoutingPlan,
    VolumeLocalSceneInput,
)
from .local_coverage import LocalSceneCoverageResolver, plan_local_scene_execution
from .manifest import build_initial_manifest
from .models import (
    AcceptancePriority,
    PlannerMode,
    ProductionSceneBrief,
    SceneComplexity,
    SceneType,
    StoryPlan,
)
from .planner import DeterministicTopicPlanner, build_scene_briefs
from .reference_planning import load_reference_catalog, resolve_references
from .strategy import (
    ProductionStrategyConfig,
    SceneDataSensitivity,
    SceneRoutingRequest,
    VisualExecutionMode,
    route_illustrated_scene,
    route_scene,
)
from .visual_adapter import adapt_scene_brief


def deterministic_scene_seed(order: int) -> int:
    """Extend the proof sequence 10101..10404 deterministically through scene 8."""
    if not 1 <= order <= 8:
        raise ValueError("scene order must be between 1 and 8")
    return 10_000 + (order * 101)


def _canonical_hash(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _acceptance_policy(brief: ProductionSceneBrief) -> AcceptancePolicyDecision:
    reasons: list[str] = []
    high_type = brief.scene_type in {
        SceneType.RELATIONSHIP_VIGNETTE,
        SceneType.EMOTIONAL_METAPHOR,
        SceneType.SYMBOLIC_CHOICE,
        SceneType.CLOSURE_VIGNETTE,
    }
    if brief.scene_type is SceneType.RELATIONSHIP_VIGNETTE:
        reasons.append("relationship interaction and couple identity continuity")
    if brief.scene_type in {SceneType.EMOTIONAL_METAPHOR, SceneType.SYMBOLIC_CHOICE}:
        reasons.append("complex symbolic or emotional metaphor composition")
    if brief.scene_type is SceneType.CLOSURE_VIGNETTE:
        reasons.append("narratively important closure continuity")
    if brief.complexity is SceneComplexity.COMPLEX:
        reasons.append("scene is marked visually complex")
    high = high_type or brief.complexity is SceneComplexity.COMPLEX or brief.acceptance_priority in {
        AcceptancePriority.HIGH,
        AcceptancePriority.CONTINUITY_CRITICAL,
    }
    if not reasons:
        reasons.append("standard visual complexity; explicit QC still required")
    return AcceptancePolicyDecision(
        priority=AcceptancePriority.HIGH if high else brief.acceptance_priority,
        dev_acceptance_recommended=high,
        draft_acceptance_eligible_after_explicit_qc=(
            brief.complexity is SceneComplexity.SIMPLE and not high
        ),
        reasons=reasons,
    )


def build_production_run_plan(
    *,
    job_id: str,
    story_plan: StoryPlan,
    scene_briefs: list[ProductionSceneBrief],
    reference_catalog: ReferenceCatalog,
    execution_mode: ExecutionMode = ExecutionMode.FIXTURE_PREVIEW,
    production_strategy: ProductionStrategyConfig | None = None,
    volume_local_inputs: Mapping[str, VolumeLocalSceneInput] | None = None,
    local_coverage_resolver: LocalSceneCoverageResolver | None = None,
) -> ProductionRunPlan:
    production_strategy = production_strategy or ProductionStrategyConfig.quality()
    local_inputs = dict(volume_local_inputs or {})
    brief_ids = {brief.scene_id for brief in scene_briefs}
    unknown_local_ids = set(local_inputs) - brief_ids
    if unknown_local_ids:
        raise ValueError(f"local inputs reference unknown scenes: {sorted(unknown_local_ids)}")
    if local_inputs and production_strategy.strategy.value != "volume":
        raise ValueError("local-first execution inputs require Volume strategy")
    if (
        local_inputs
        and production_strategy.visual_mode is VisualExecutionMode.LOCAL_COMPOSITOR
        and local_coverage_resolver is None
    ):
        raise ValueError("local-first execution inputs require an injected coverage resolver")
    metadata = story_plan.planner_metadata
    if execution_mode is ExecutionMode.LIVE_PRODUCTION and (
        metadata.planner_mode is not PlannerMode.PRODUCTION or not metadata.production_eligible
    ):
        raise ValueError(
            "live production requires a production-eligible planner; "
            "deterministic fixture plans are preview/test only"
        )
    manifest = build_initial_manifest(job_id=job_id, plan=story_plan, briefs=scene_briefs)
    draft_tier = resolve_visual_tier(VisualQualityTier.DRAFT)
    acceptance_tier = resolve_visual_tier(VisualQualityTier.ACCEPTANCE)
    execution_plans: list[SceneExecutionPlan] = []
    for brief, timing in zip(scene_briefs, manifest.timings, strict=True):
        adapter = adapt_scene_brief(brief)
        references, decisions = resolve_references(brief, reference_catalog)
        local_input = local_inputs.get(brief.scene_id)
        local_execution = None
        routing = None
        if local_input is not None:
            if production_strategy.visual_mode is VisualExecutionMode.LOCAL_COMPOSITOR:
                assert local_coverage_resolver is not None
                local_execution = plan_local_scene_execution(
                    brief.scene_id,
                    local_input,
                    local_coverage_resolver,
                )
                route = route_scene(
                    SceneRoutingRequest(
                        sensitivity=local_input.sensitivity,
                        local_coverage=local_execution.coverage,
                    )
                )
            else:
                route = route_illustrated_scene(local_input.sensitivity)
            routing = SceneRoutingPlan(
                sensitivity=local_input.sensitivity,
                route=route,
            )
        blocking_reference_decisions = [
            decision
            for decision in decisions
            if decision.status
            in {
                ReferenceDecisionStatus.REFERENCE_BLOCKED_REQUIRED_IDENTITY,
                ReferenceDecisionStatus.REFERENCE_BLOCKED_REQUIRED_STYLE,
            }
        ]
        local_covered = bool(
            routing is not None
            and routing.route.selected_provider is ProviderKind.LOCAL_COMPOSITOR
        )
        public_safe_text_only = bool(
            local_input is not None
            and local_input.sensitivity is SceneDataSensitivity.PUBLIC_SAFE
            and not references
            and not brief.identity_requirements
            and not brief.continuity_requirements
            and not brief.reference_roles
        )
        if (
            execution_mode is ExecutionMode.LIVE_PRODUCTION
            and blocking_reference_decisions
            and not local_covered
            and not public_safe_text_only
        ):
            blocked = ", ".join(
                f"{decision.role}:{decision.status.value}"
                for decision in blocking_reference_decisions
            )
            raise ValueError(f"required approved references unavailable for {brief.scene_id}: {blocked}")
        seed = deterministic_scene_seed(brief.order)
        logical_payload = {
            "scene": adapter.model_dump(mode="json"),
            "tier": draft_tier.tier.value,
            "provider": draft_tier.provider,
            "model": draft_tier.model,
            "steps": draft_tier.steps,
            "timeout_seconds": draft_tier.timeout_seconds,
            "dimensions": [DEFAULT_WIDTH, DEFAULT_HEIGHT],
            "seed": seed,
            "references": [item.model_dump(mode="json") for item in references],
            "accepted_scene_chaining": False,
        }
        scene_execution = SceneExecutionPlan(
            scene_id=brief.scene_id,
            order=brief.order,
            scene_brief=brief,
            visual_adapter=adapter,
            timing=timing,
            draft=DraftRequestPlan(
                provider=draft_tier.provider,
                model=draft_tier.model,
                steps=draft_tier.steps,
                timeout_seconds=draft_tier.timeout_seconds,
                width=DEFAULT_WIDTH,
                height=DEFAULT_HEIGHT,
                seed=seed,
                references=references,
                reference_decisions=decisions,
                logical_visual_request_hash=_canonical_hash(logical_payload),
            ),
            acceptance=AcceptanceRequestTemplate(
                provider=acceptance_tier.provider,
                model=acceptance_tier.model,
                steps=acceptance_tier.steps,
                timeout_seconds=acceptance_tier.timeout_seconds,
                width=DEFAULT_WIDTH,
                height=DEFAULT_HEIGHT,
                seed=seed,
                references=references,
                reference_decisions=decisions,
            ),
            acceptance_policy=_acceptance_policy(brief),
            routing=routing,
            local_execution=local_execution,
        )
        execution_plans.append(scene_execution)
    execution_summary = [
        {
            "scene_id": item.scene_id,
            "scene_type": item.scene_brief.scene_type.value,
            "meaning": item.scene_brief.meaning,
            "status": item.initial_status.value,
            "seed": item.draft.seed,
            "draft_model": item.draft.model,
            "reference_ids": [ref.reference_id for ref in item.draft.references],
            "reference_hashes": [ref.sha256 for ref in item.draft.references],
            "reference_decisions": [
                decision.model_dump(mode="json") for decision in item.draft.reference_decisions
            ],
            "acceptance_priority": item.acceptance_policy.priority.value,
            "logical_visual_request_hash": item.draft.logical_visual_request_hash,
            "provider_request_hash": None,
        }
        for item in execution_plans
    ]
    label = (
        "OFFLINE_FIXTURE_PREVIEW"
        if metadata.planner_mode is PlannerMode.FIXTURE
        else "PRODUCTION_RUN_PLAN"
    )
    manifest = manifest.model_copy(
        update={
            "metadata": {
                **manifest.metadata,
                "planner_mode": metadata.planner_mode.value,
                "production_eligible": metadata.production_eligible,
                "plan_label": label,
                "execution_plans": execution_summary,
            }
        },
        deep=True,
    )
    planning_payload = {
        "job_id": job_id,
        "topic": story_plan.topic,
        "story_plan": story_plan.model_dump(mode="json"),
        "executions": [item.model_dump(mode="json") for item in execution_plans],
        "manifest": manifest.model_dump(mode="json"),
        "production_strategy": production_strategy.model_dump(mode="json"),
    }
    return ProductionRunPlan(
        plan_label=label,
        execution_mode=execution_mode,
        job_id=job_id,
        topic=story_plan.topic,
        story_plan=story_plan,
        scene_execution_plans=execution_plans,
        manifest=manifest,
        planning_hash=_canonical_hash(planning_payload),
        production_strategy=production_strategy,
    )


def build_fixture_preview_run(
    *,
    topic: str,
    scene_count: int = 8,
    target_duration_seconds: float = 35.0,
    language: str = "vi",
    job_id: str = "topic-production-preview",
    reference_root: Path | str | None = None,
) -> ProductionRunPlan:
    story_plan = DeterministicTopicPlanner().plan(
        topic=topic,
        language=language,
        scene_count=scene_count,
        target_duration_seconds=target_duration_seconds,
    )
    return build_production_run_plan(
        job_id=job_id,
        story_plan=story_plan,
        scene_briefs=build_scene_briefs(story_plan),
        reference_catalog=load_reference_catalog(reference_root),
        execution_mode=ExecutionMode.FIXTURE_PREVIEW,
    )
