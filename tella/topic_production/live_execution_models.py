"""Contracts for bounded provider execution and durable canary state."""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from tella.visual_generation.models import CandidateMetadata, GenerationRequest

from .runtime_models import ExecutionRunState, GenerationAttempt


class DraftExecutionPreview(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: str
    topic: str
    planner_mode: str
    production_eligible: bool
    execution_purpose: str
    scene_id: str
    scene_type: str
    meaning: str
    duration_seconds: float
    prompt_profile: str
    request: GenerationRequest
    planning_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    logical_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_request_hash: None = None
    accepted_scene_chaining: bool = False
    candidate_count: int = Field(default=1, ge=1, le=1)
    maximum_provider_calls: int = Field(default=1, ge=1, le=1)
    retry_calls: int = Field(default=0, ge=0, le=0)
    fallback_calls: int = Field(default=0, ge=0, le=0)
    external_calls: int = Field(default=0, ge=0, le=0)


class ProductionJobPaths(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_dir: Path
    run_plan_path: Path
    runtime_state_path: Path
    manifest_path: Path
    candidate_base_path: Path
    candidate_metadata_path: Path


class DraftExecutionOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    preview: DraftExecutionPreview
    state: ExecutionRunState
    attempt: GenerationAttempt | None = None
    provider_metadata: CandidateMetadata | None = None
    paths: ProductionJobPaths
    dry_run: bool
    provider_invocations: int = Field(default=0, ge=0, le=1)
    external_calls: int = Field(default=0, ge=0, le=1)
    provider_latency_ms: int | None = Field(default=None, ge=0)


class CanarySelection(BaseModel):
    model_config = ConfigDict(frozen=True)

    scene_id: str
    reason: str
