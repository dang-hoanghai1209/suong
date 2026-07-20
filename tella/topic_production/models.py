"""Typed contracts for topic-aware, dual-tier emotional-video production."""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SceneType(StrEnum):
    SOLO_EMOTIONAL_VIGNETTE = "solo_emotional_vignette"
    RELATIONSHIP_VIGNETTE = "relationship_vignette"
    ORGANIC_DAILY_VIGNETTE = "organic_daily_vignette"
    EMOTIONAL_METAPHOR = "emotional_metaphor"
    SYMBOLIC_CHOICE = "symbolic_choice"
    SELF_COMPASSION = "self_compassion"
    JOURNEY_TRANSITION = "journey_transition"
    CLOSURE_VIGNETTE = "closure_vignette"


class SceneComplexity(StrEnum):
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


class AcceptancePriority(StrEnum):
    STANDARD = "standard"
    HIGH = "high"
    CONTINUITY_CRITICAL = "continuity_critical"


class GenerationTier(StrEnum):
    DRAFT = "draft"
    ACCEPTANCE = "acceptance"


class ProductionSceneStatus(StrEnum):
    PLANNED = "PLANNED"
    DRAFT_PENDING = "DRAFT_PENDING"
    DRAFT_GENERATED = "DRAFT_GENERATED"
    DRAFT_QC_PASS = "DRAFT_QC_PASS"
    DRAFT_QC_FAIL = "DRAFT_QC_FAIL"
    ACCEPTANCE_PENDING = "ACCEPTANCE_PENDING"
    ACCEPTANCE_GENERATED = "ACCEPTANCE_GENERATED"
    ACCEPTANCE_QC_PASS = "ACCEPTANCE_QC_PASS"
    ACCEPTANCE_QC_FAIL = "ACCEPTANCE_QC_FAIL"
    ACCEPTED = "ACCEPTED"
    BLOCKED = "BLOCKED"


class PlannerMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    planner_id: str = "deterministic_topic_fixture"
    planner_version: str = "topic_fixture_v1"
    normalized_topic: str = Field(min_length=1)
    topic_concepts: list[str] = Field(min_length=1)
    deterministic_key: str = Field(pattern=r"^[0-9a-f]{16}$")
    semantic_evaluator: str = "deterministic_structural_v1"
    external_calls: int = Field(default=0, ge=0, le=0)


class SemanticBeat(BaseModel):
    model_config = ConfigDict(frozen=True)

    beat_id: str = Field(pattern=r"^beat_[0-9]{2}$")
    order: int = Field(ge=1, le=8)
    narration_segment: str = Field(min_length=1)
    semantic_purpose: str = Field(min_length=1)
    emotional_state: str = Field(min_length=1)
    transition_intent: str = Field(min_length=1)
    visual_intent: str = Field(min_length=1)
    duration_seconds: float = Field(gt=0, ge=3.0, le=5.0)


class StoryPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    topic: str = Field(min_length=1)
    language: str = Field(min_length=2, max_length=12)
    aspect_ratio: Literal["9:16"] = "9:16"
    target_duration_seconds: float = Field(ge=32.0, le=38.0)
    requested_scene_count: Literal[7, 8]
    narration_text: str = Field(min_length=1)
    emotional_arc: list[str] = Field(min_length=3)
    topic_intent: str = Field(min_length=1)
    semantic_beats: list[SemanticBeat]
    planner_metadata: PlannerMetadata

    @model_validator(mode="after")
    def validate_beats(self) -> "StoryPlan":
        if len(self.semantic_beats) != self.requested_scene_count:
            raise ValueError("semantic beat count must match requested_scene_count")
        expected_orders = list(range(1, self.requested_scene_count + 1))
        if [beat.order for beat in self.semantic_beats] != expected_orders:
            raise ValueError("semantic beats must be ordered contiguously")
        ids = [beat.beat_id for beat in self.semantic_beats]
        if len(ids) != len(set(ids)):
            raise ValueError("semantic beat IDs must be unique")
        expected_narration = " ".join(beat.narration_segment for beat in self.semantic_beats)
        if self.narration_text != expected_narration:
            raise ValueError("narration_text must be the continuous concatenation of beat narration")
        duration = round(sum(beat.duration_seconds for beat in self.semantic_beats), 3)
        if duration != round(self.target_duration_seconds, 3):
            raise ValueError("semantic beat durations must total target_duration_seconds")
        return self


class ReferenceStrategy(BaseModel):
    model_config = ConfigDict(frozen=True)

    strategy: str = Field(min_length=1)
    accepted_scene_chaining: bool = False
    notes: str = ""


class ProductionSceneBrief(BaseModel):
    model_config = ConfigDict(frozen=True)

    scene_id: str = Field(pattern=r"^scene_[0-9]{2}$")
    order: int = Field(ge=1, le=8)
    scene_type: SceneType
    narrative_text: str = Field(min_length=1)
    meaning: str = Field(min_length=1)
    emotional_tone: list[str] = Field(min_length=1)
    topic_intent: str = Field(min_length=1)
    characters: list[str] = Field(default_factory=list)
    identity_requirements: list[str] = Field(default_factory=list)
    continuity_requirements: list[str] = Field(default_factory=list)
    action: list[str] = Field(default_factory=list)
    interaction: dict[str, str] = Field(default_factory=dict)
    environment: list[str] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    composition: list[str] = Field(default_factory=list)
    negative_space_requirements: list[str] = Field(default_factory=list)
    visual_hierarchy: list[str] = Field(default_factory=list)
    reference_roles: list[str] = Field(default_factory=list)
    reference_strategy: ReferenceStrategy
    hard_negatives: list[str] = Field(default_factory=list)
    complexity: SceneComplexity = SceneComplexity.MODERATE
    acceptance_priority: AcceptancePriority = AcceptancePriority.STANDARD
    source_beat_id: str = Field(pattern=r"^beat_[0-9]{2}$")
    duration_seconds: float = Field(gt=0, ge=3.0, le=5.0)


class CandidateArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_id: str = Field(min_length=1)
    tier: GenerationTier
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    seed: int | None = None
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reference_hashes: list[str] = Field(default_factory=list)

    @field_validator("reference_hashes")
    @classmethod
    def validate_reference_hashes(cls, value: list[str]) -> list[str]:
        if any(len(item) != 64 or any(char not in "0123456789abcdef" for char in item) for item in value):
            raise ValueError("reference hashes must be lowercase SHA-256 digests")
        return value


class SceneQCRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    tier: GenerationTier
    passed: bool
    reviewer: str = Field(min_length=1)
    reasons: list[str] = Field(default_factory=list)
    scores: dict[str, float] = Field(default_factory=dict)


class DualTierPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    draft_provider: str = "cloudflare-flux"
    draft_model: str = "@cf/black-forest-labs/flux-2-klein-4b"
    acceptance_provider: str = "cloudflare-flux"
    acceptance_model: str = "@cf/black-forest-labs/flux-2-dev"
    every_scene_starts_draft: bool = True
    simple_scene_may_accept_from_draft: bool = True
    explicit_qc_required_for_acceptance: bool = True
    draft_is_never_automatically_final: bool = True


class SceneTierDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    draft_required: bool = True
    acceptance_priority: AcceptancePriority
    dev_acceptance_recommended: bool
    draft_only_acceptance_allowed_after_explicit_qc: bool
    reason: str = Field(min_length=1)


class ProductionScene(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    brief: ProductionSceneBrief
    status: ProductionSceneStatus = ProductionSceneStatus.DRAFT_PENDING
    tier_decision: SceneTierDecision
    draft_candidate: CandidateArtifact | None = None
    acceptance_candidate: CandidateArtifact | None = None
    accepted_candidate: CandidateArtifact | None = None
    accepted_source_tier: GenerationTier | None = None
    qc_records: list[SceneQCRecord] = Field(default_factory=list)
    failure_reasons: list[str] = Field(default_factory=list)
    block_reasons: list[str] = Field(default_factory=list)


class SceneTiming(BaseModel):
    model_config = ConfigDict(frozen=True)

    scene_id: str = Field(pattern=r"^scene_[0-9]{2}$")
    order: int = Field(ge=1, le=8)
    start_seconds: float = Field(ge=0)
    duration_seconds: float = Field(ge=3.0, le=5.0)
    end_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_interval(self) -> "SceneTiming":
        if round(self.start_seconds + self.duration_seconds, 3) != round(self.end_seconds, 3):
            raise ValueError("scene timing end must equal start plus duration")
        return self


class ReadinessResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    ready: bool
    unresolved_scene_ids: list[str] = Field(default_factory=list)
    reasons: dict[str, list[str]] = Field(default_factory=dict)


class TopicFidelityReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    passed: bool
    signals: dict[str, bool]
    issues: list[str] = Field(default_factory=list)
    evaluator: str = "deterministic_structural_v1"


class ProductionManifest(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    schema_version: int = 1
    job_id: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    story_plan: StoryPlan
    scene_briefs: list[ProductionSceneBrief]
    timings: list[SceneTiming]
    scenes: list[ProductionScene]
    dual_tier_policy: DualTierPolicy
    narration_path: str | None = None
    subtitle_path: str | None = None
    video_path: str | None = None
    render_ready: bool = False
    blocked_reasons: dict[str, list[str]] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
