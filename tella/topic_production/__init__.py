"""Offline foundation for topic-aware 7–8 scene dual-tier production."""

from .execution import (
    build_fixture_preview_run,
    build_production_run_plan,
    deterministic_scene_seed,
)
from .execution_models import (
    ApprovedReference,
    ExecutionMode,
    ProductionRunPlan,
    ReferenceCatalog,
    ReferenceDecision,
    ReferenceDecisionStatus,
    SceneExecutionPlan,
)
from .manifest import build_initial_manifest, refresh_manifest_readiness
from .models import (
    AcceptancePriority,
    CandidateArtifact,
    DualTierPolicy,
    GenerationTier,
    PlannerMode,
    ProductionManifest,
    ProductionScene,
    ProductionSceneBrief,
    ProductionSceneStatus,
    ReadinessResult,
    SceneComplexity,
    SceneType,
    SemanticBeat,
    StoryPlan,
)
from .planner import (
    DeterministicTopicPlanner,
    TopicFidelityEvaluator,
    TopicStoryPlanner,
    build_scene_briefs,
    validate_topic_fidelity,
)
from .readiness import evaluate_render_readiness
from .reference_planning import (
    APPROVED_REFERENCE_DEFINITIONS,
    ApprovedReferenceValidationError,
    load_reference_catalog,
    resolve_references,
)
from .visual_adapter import adapt_scene_brief, required_reference_roles

__all__ = [
    "AcceptancePriority",
    "ApprovedReference",
    "ApprovedReferenceValidationError",
    "APPROVED_REFERENCE_DEFINITIONS",
    "CandidateArtifact",
    "DeterministicTopicPlanner",
    "DualTierPolicy",
    "ExecutionMode",
    "GenerationTier",
    "PlannerMode",
    "ProductionManifest",
    "ProductionRunPlan",
    "ProductionScene",
    "ProductionSceneBrief",
    "ProductionSceneStatus",
    "ReadinessResult",
    "ReferenceCatalog",
    "ReferenceDecision",
    "ReferenceDecisionStatus",
    "SceneComplexity",
    "SceneExecutionPlan",
    "SceneType",
    "SemanticBeat",
    "StoryPlan",
    "TopicFidelityEvaluator",
    "TopicStoryPlanner",
    "adapt_scene_brief",
    "build_fixture_preview_run",
    "build_initial_manifest",
    "build_production_run_plan",
    "build_scene_briefs",
    "deterministic_scene_seed",
    "evaluate_render_readiness",
    "load_reference_catalog",
    "refresh_manifest_readiness",
    "required_reference_roles",
    "resolve_references",
    "validate_topic_fidelity",
]
