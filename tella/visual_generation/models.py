"""Typed, provider-neutral contracts for the four-scene visual proof."""
from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Canvas(StrictModel):
    width: int = Field(ge=64)
    height: int = Field(ge=64)
    aspect_ratio: Literal["9:16"]

    @model_validator(mode="after")
    def validate_portrait_ratio(self) -> "Canvas":
        if abs((self.width / self.height) - (9 / 16)) > 0.01:
            raise ValueError("canvas dimensions must be 9:16")
        return self


class CharacterArchetype(StrictModel):
    character_id: str = Field(min_length=1)
    identity_locks: list[str] = Field(min_length=1)
    unconstrained: list[str] = Field(default_factory=list)


class StyleBible(StrictModel):
    style_id: str = Field(min_length=1)
    canvas: Canvas
    background: list[str] = Field(min_length=1)
    drawing: list[str] = Field(min_length=1)
    palette: list[str] = Field(min_length=1)
    composition: list[str] = Field(min_length=1)
    lighting: list[str] = Field(min_length=1)
    negative_constraints: list[str] = Field(min_length=1)
    character_archetypes: dict[str, CharacterArchetype]


class SceneBrief(StrictModel):
    scene_id: str = Field(pattern=r"^scene_\d{2}$")
    scene_type: str = Field(min_length=1)
    narrative_text: str = Field(min_length=1)
    narrative_meaning: str = Field(min_length=1)
    characters: list[str] = Field(min_length=1)
    emotion: list[str] = Field(min_length=1)
    action: list[str] = Field(min_length=1)
    interaction: dict[str, str] = Field(default_factory=dict)
    environment_cues: list[str] = Field(default_factory=list)
    symbolic_elements: list[str] = Field(default_factory=list)
    composition: list[str] = Field(min_length=1)
    negative_constraints: list[str] = Field(default_factory=list)
    reference_roles: list[str] = Field(min_length=1)
    natural_interaction_required: bool = False

    @field_validator("characters")
    @classmethod
    def validate_characters(cls, value: list[str]) -> list[str]:
        allowed = {"female", "male"}
        if not set(value).issubset(allowed):
            raise ValueError("characters must use configured soft archetype IDs")
        return value


class ProofPlan(StrictModel):
    proof_id: str = Field(min_length=1)
    candidate_count: int = Field(default=2, ge=1, le=3)
    max_generation_attempts_per_scene: int = Field(default=3, ge=1, le=5)
    max_repairs_per_candidate: int = Field(default=1, ge=0, le=2)
    reference_strategy: Literal["priority_bounded"] = "priority_bounded"
    scenes: list[SceneBrief]

    @model_validator(mode="after")
    def validate_four_ordered_scenes(self) -> "ProofPlan":
        expected = [f"scene_{index:02d}" for index in range(1, 5)]
        if [scene.scene_id for scene in self.scenes] != expected:
            raise ValueError("proof plan must contain scene_01 through scene_04 in order")
        return self


class ReferenceAsset(StrictModel):
    role: str
    semantic_roles: list[str] = Field(default_factory=list)
    path: Path
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: Literal["master", "scene_type", "accepted_scene"]
    priority: int = Field(ge=1)


class ReferencePack(StrictModel):
    scene_id: str
    references: list[ReferenceAsset]


class ProviderCapabilities(StrictModel):
    provider_id: str
    model: str
    supports_text_to_image: bool
    supports_reference_images: bool
    supports_multiple_references: bool
    supports_image_edit: bool
    supports_seed: bool
    supports_9_16: bool
    max_reference_images: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_claims(self) -> "ProviderCapabilities":
        if not self.supports_reference_images and (
            self.supports_multiple_references or self.max_reference_images
        ):
            raise ValueError("text-only provider cannot claim reference capacity")
        if self.supports_multiple_references and self.max_reference_images < 2:
            raise ValueError("multi-reference support requires capacity of at least two")
        return self


class GenerationRequest(StrictModel):
    scene_id: str
    candidate_index: int = Field(ge=1)
    attempt: int = Field(ge=1)
    width: int
    height: int
    aspect_ratio: Literal["9:16"]
    instruction: str = Field(min_length=1)
    negative_instruction: str = Field(min_length=1)
    references: list[ReferenceAsset] = Field(min_length=1)
    seed: int | None = None
    preserve_existing: bool = False
    repair_instructions: list[str] = Field(default_factory=list)


class CandidateMetadata(StrictModel):
    provider: str
    model: str
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_hashes: list[str]
    instruction_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    seed: int | None
    generation_attempt: int = Field(ge=1)
    output_path: Path
    latency_ms: int | None = Field(default=None, ge=0)
    reference_roles: list[list[str]] = Field(default_factory=list)
    requested_aspect_ratio: str = ""
    requested_resolution: str = ""
    actual_width: int | None = Field(default=None, ge=1)
    actual_height: int | None = Field(default=None, ge=1)
    mime_type: str = ""
    requested_width: int | None = Field(default=None, ge=1)
    requested_height: int | None = Field(default=None, ge=1)
    prepared_references: list[dict[str, Any]] = Field(default_factory=list)
    steps: int | None = Field(default=None, ge=1)
    provider_request_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    request_timeout_seconds: float | None = Field(default=None, gt=0)


class QCDecision(StrEnum):
    PASS = "PASS"
    MINOR_REPAIR = "MINOR_REPAIR"
    REGENERATE = "REGENERATE"


ScoreSource = Literal["heuristic", "vision_model", "human_review"]


class VisualQCResult(StrictModel):
    style_coherence: float = Field(ge=0, le=10)
    character_identity: float = Field(ge=0, le=10)
    scene_meaning: float = Field(ge=0, le=10)
    composition: float = Field(ge=0, le=10)
    natural_interaction: float = Field(ge=0, le=10)
    anatomy: float = Field(ge=0, le=10)
    visual_appeal: float = Field(ge=0, le=10)
    score_source: ScoreSource
    decision: QCDecision
    notes: str = ""
    repair_instructions: list[str] = Field(default_factory=list)
    reviewer: str = ""

    @property
    def minimum_score(self) -> float:
        fields = (
            self.style_coherence,
            self.character_identity,
            self.scene_meaning,
            self.composition,
            self.natural_interaction,
            self.anatomy,
            self.visual_appeal,
        )
        return min(fields)


class SceneResult(StrictModel):
    scene_id: str
    status: Literal["dry_run", "accepted", "failed"]
    references_used: list[ReferenceAsset]
    generation_attempts: int = Field(ge=0)
    repair_attempts: int = Field(ge=0)
    accepted_candidate: int | None = None
    accepted_path: Path | None = None
    provider: str
    model: str
    human_review_required: bool = True
    failure_reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
