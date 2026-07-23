"""Typed contracts for provider-free visual execution planning."""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tella.visual_generation.models import SceneBrief as VisualSceneBrief
from tella.visual_generation.providers.kinds import ProviderKind

from .models import (
    AcceptancePriority,
    GenerationTier,
    ProductionManifest,
    ProductionSceneBrief,
    ProductionSceneStatus,
    SceneTiming,
    StoryPlan,
)
from .strategy import (
    LocalCoverageAssessment,
    ProductionStrategyConfig,
    ProviderRoute,
    SceneDataSensitivity,
)


class ExecutionMode(StrEnum):
    FIXTURE_PREVIEW = "fixture_preview"
    LIVE_PRODUCTION = "live_production"


class ReferenceDecisionStatus(StrEnum):
    SELECTED = "SELECTED"
    REFERENCE_BLOCKED_REQUIRED_IDENTITY = "REFERENCE_BLOCKED_REQUIRED_IDENTITY"
    REFERENCE_BLOCKED_REQUIRED_STYLE = "REFERENCE_BLOCKED_REQUIRED_STYLE"
    NO_APPROVED_REFERENCE_AVAILABLE = "NO_APPROVED_REFERENCE_AVAILABLE"
    NO_COMPOSITION_REFERENCE_AVAILABLE = "NO_COMPOSITION_REFERENCE_AVAILABLE"


class ApprovedReference(BaseModel):
    model_config = ConfigDict(frozen=True)

    reference_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    roles: list[str] = Field(min_length=1)
    sensitivity: SceneDataSensitivity = SceneDataSensitivity.PRIVATE
    supported_scene_types: list[str] = Field(default_factory=list)
    identity_scope: str | None = None
    style_scope: str | None = None
    priority: int = Field(ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReferenceCatalog(BaseModel):
    model_config = ConfigDict(frozen=True)

    catalog_id: str = "validated_four_scene_static_references_v1"
    references: list[ApprovedReference] = Field(default_factory=list)
    unavailable_roles: dict[str, str] = Field(default_factory=dict)
    generated_assets_authoritative: bool = False


class ReferenceDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: str = Field(min_length=1)
    status: ReferenceDecisionStatus
    reason: str = Field(min_length=1)
    reference_id: str | None = None
    path: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    priority: int = Field(ge=1)


class VisualSceneAdapterResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    visual_scene: VisualSceneBrief
    preserved_semantics: dict[str, Any]
    field_mapping: dict[str, str]
    prompt_profile: Literal["topic_production_v1"] = "topic_production_v1"


class AcceptanceRequestTemplate(BaseModel):
    model_config = ConfigDict(frozen=True)

    tier: Literal[GenerationTier.ACCEPTANCE] = GenerationTier.ACCEPTANCE
    provider: str
    model: str
    steps: int = Field(ge=1)
    timeout_seconds: float = Field(gt=0)
    width: int = Field(ge=64)
    height: int = Field(ge=64)
    seed: int = Field(ge=0)
    seed_policy: Literal["reuse_stable_scene_seed"] = "reuse_stable_scene_seed"
    references: list[ApprovedReference] = Field(default_factory=list)
    reference_decisions: list[ReferenceDecision] = Field(default_factory=list)
    reference_strategy: Literal["reuse_approved_static_draft_resolution"] = (
        "reuse_approved_static_draft_resolution"
    )
    accepted_scene_chaining: bool = False
    promotion_requires_explicit_qc: bool = True
    provider_request_hash: None = None


class DraftRequestPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    tier: Literal[GenerationTier.DRAFT] = GenerationTier.DRAFT
    provider: str
    model: str
    steps: int = Field(ge=1)
    timeout_seconds: float = Field(gt=0)
    width: int = Field(ge=64)
    height: int = Field(ge=64)
    seed: int = Field(ge=0)
    references: list[ApprovedReference] = Field(default_factory=list)
    reference_decisions: list[ReferenceDecision] = Field(default_factory=list)
    accepted_scene_chaining: bool = False
    logical_visual_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_request_hash: None = None


class AcceptancePolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    priority: AcceptancePriority
    dev_acceptance_recommended: bool
    draft_acceptance_eligible_after_explicit_qc: bool
    automatic_acceptance: bool = False
    reasons: list[str] = Field(min_length=1)


class LocalCompositionRequest(BaseModel):
    """Portable inputs understood by the existing semantic resolver/compositor."""

    model_config = ConfigDict(frozen=True)

    character_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    emotion: str = Field(min_length=1)
    direction: str = "front"
    location: str = Field(min_length=1)
    time_of_day: str = Field(min_length=1)
    objects: list[str] = Field(default_factory=list)
    optional_objects: list[str] = Field(default_factory=list)
    composition_preset: str = Field(min_length=1)
    seed: int = Field(ge=0)
    base_seed: int = Field(default=0, ge=0)
    scene_duration: float = Field(default=0.0, ge=0)
    narration_segment: str = ""
    background_mood: str = ""
    layout_template: str = ""


class VolumeLocalSceneInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    sensitivity: SceneDataSensitivity
    request: LocalCompositionRequest


class LocalExecutionPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: Literal[ProviderKind.LOCAL_COMPOSITOR] = ProviderKind.LOCAL_COMPOSITOR
    request: LocalCompositionRequest
    coverage: LocalCoverageAssessment
    logical_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_width: Literal[1080] = 1080
    expected_height: Literal[1920] = 1920
    consumes_ai_call: Literal[False] = False
    consumes_ai_retry: Literal[False] = False
    external_calls: Literal[0] = 0


class SceneRoutingPlan(BaseModel):
    """Provider-neutral privacy and provider intent for one scene."""

    model_config = ConfigDict(frozen=True)

    sensitivity: SceneDataSensitivity
    route: ProviderRoute


class SceneExecutionPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    scene_id: str = Field(pattern=r"^scene_[0-9]{2}$")
    order: int = Field(ge=1, le=8)
    scene_brief: ProductionSceneBrief
    visual_adapter: VisualSceneAdapterResult
    timing: SceneTiming
    initial_status: Literal[ProductionSceneStatus.DRAFT_PENDING] = (
        ProductionSceneStatus.DRAFT_PENDING
    )
    draft: DraftRequestPlan
    acceptance: AcceptanceRequestTemplate
    acceptance_policy: AcceptancePolicyDecision
    routing: SceneRoutingPlan | None = None
    local_execution: LocalExecutionPlan | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_local_route(cls, value: Any) -> Any:
        """Load v2 plans that stored generic routing inside local_execution."""

        if not isinstance(value, dict) or value.get("routing") is not None:
            return value
        legacy_local = value.get("local_execution")
        if not isinstance(legacy_local, dict):
            return value
        sensitivity = legacy_local.get("sensitivity")
        route = legacy_local.get("route")
        if sensitivity is None or route is None:
            return value
        migrated = dict(value)
        migrated["routing"] = {"sensitivity": sensitivity, "route": route}
        return migrated


class ProductionRunPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 2
    plan_label: Literal["OFFLINE_FIXTURE_PREVIEW", "PRODUCTION_RUN_PLAN"]
    execution_mode: ExecutionMode
    job_id: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    story_plan: StoryPlan
    scene_execution_plans: list[SceneExecutionPlan]
    manifest: ProductionManifest
    planning_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    production_strategy: ProductionStrategyConfig = Field(
        default_factory=ProductionStrategyConfig.quality
    )
    external_calls: int = Field(default=0, ge=0, le=0)

    @model_validator(mode="after")
    def validate_scene_mapping(self) -> "ProductionRunPlan":
        expected = [brief.scene_id for brief in self.manifest.scene_briefs]
        actual = [scene.scene_id for scene in self.scene_execution_plans]
        if actual != expected:
            raise ValueError("execution plans must map one-to-one to manifest scene briefs")
        if self.manifest.render_ready:
            raise ValueError("pre-generation production run plan cannot be render ready")
        return self
