"""Explicit PUBLIC_SAFE Cloudflare-to-Pollinations overflow execution."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tella.visual_generation.models import CandidateMetadata
from tella.visual_generation.providers.base import PublicTextToImageProvider
from tella.visual_generation.providers.kinds import ProviderKind
from tella.visual_generation.providers.pollinations import (
    PollinationsError,
    PollinationsErrorCategory,
    PollinationsExecutionRequest,
    PollinationsPrivacyMetadata,
    PollinationsPromptSource,
    prepare_public_request,
    public_request_hash,
)
from tella.visual_generation.references import sha256_file

from .live_execution_models import ProductionJobPaths
from .models import GenerationTier, ProductionSceneStatus
from .persistence import persist_execution_snapshot, production_job_paths
from .runtime import record_pollinations_generation_attempt
from .runtime_models import ExecutionRunState, GenerationAttempt, TechnicalStatus
from .strategy import (
    ProductionStrategy,
    ProviderFailureCategory,
    pollinations_failover_decision,
)


class CloudflareOverflowAuthorization(BaseModel):
    model_config = ConfigDict(frozen=True)

    prior_candidate_id: str = Field(min_length=1)
    failure_category: ProviderFailureCategory


class PollinationsExecutionOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: ExecutionRunState
    paths: ProductionJobPaths
    attempt: GenerationAttempt
    provider_metadata: CandidateMetadata | None = None
    provider_reaching_calls: int = Field(ge=0, le=1)
    external_calls: int = Field(ge=0, le=1)
    ai_calls: Literal[1] = 1
    ai_retry_calls: Literal[1] = 1
    latency_ms: int = Field(ge=0)


def pollinations_production_job_paths(
    out_root: Path | str, *, job_id: str, scene_id: str
) -> ProductionJobPaths:
    base = production_job_paths(out_root, job_id=job_id, scene_id=scene_id)
    directory = base.job_dir / "scenes" / scene_id / "draft" / "pollinations"
    return base.model_copy(
        update={
            "candidate_base_path": directory / "candidate_02.bin",
            "candidate_metadata_path": directory / "metadata.json",
        }
    )


def _pollinations_request(
    state: ExecutionRunState,
    *,
    scene_id: str,
    prompt_source: PollinationsPromptSource,
    privacy: PollinationsPrivacyMetadata,
) -> PollinationsExecutionRequest:
    scene = next(item for item in state.scenes if item.scene_id == scene_id)
    local_plan = scene.execution_plan.local_execution
    if local_plan is None:
        raise ValueError("Pollinations overflow requires a sensitivity-aware scene plan")
    return PollinationsExecutionRequest(
        scene_id=scene_id,
        sensitivity=local_plan.sensitivity.value,
        prompt_source=prompt_source,
        privacy=privacy,
        seed=scene.execution_plan.draft.seed,
        width=scene.execution_plan.draft.width,
        height=scene.execution_plan.draft.height,
        candidate_index=2,
        attempt=2,
        consumes_ai_retry=True,
    )


async def execute_pollinations_overflow(
    state: ExecutionRunState,
    *,
    scene_id: str,
    out_root: Path | str,
    authorization: CloudflareOverflowAuthorization,
    prompt_source: PollinationsPromptSource,
    provider: PublicTextToImageProvider,
    privacy: PollinationsPrivacyMetadata | None = None,
) -> PollinationsExecutionOutcome:
    """Execute one authorized overflow request; never chooses a provider or retries itself."""

    if state.run_plan.production_strategy.strategy is not ProductionStrategy.VOLUME:
        raise ValueError("Pollinations overflow requires Volume strategy")
    scene = next((item for item in state.scenes if item.scene_id == scene_id), None)
    if scene is None:
        raise ValueError(f"unknown scene ID: {scene_id}")
    local_plan = scene.execution_plan.local_execution
    if local_plan is None:
        raise ValueError("Pollinations overflow requires a sensitivity-aware scene plan")
    decision = pollinations_failover_decision(
        sensitivity=local_plan.sensitivity,
        failed_provider=ProviderKind.CLOUDFLARE_KLEIN_4B,
        failure=authorization.failure_category,
    )
    if not decision.eligible or decision.next_provider is not ProviderKind.POLLINATIONS:
        raise PermissionError(decision.reason)
    if scene.status is not ProductionSceneStatus.BLOCKED:
        raise ValueError("Pollinations overflow requires a blocked Cloudflare attempt")
    prior = next(
        (
            item
            for item in scene.generation_attempts
            if item.candidate_id == authorization.prior_candidate_id
        ),
        None,
    )
    if (
        prior is None
        or prior.provider != scene.execution_plan.draft.provider
        or prior.technical_status is TechnicalStatus.SUCCEEDED
    ):
        raise ValueError("overflow authorization does not match a failed Cloudflare attempt")
    policy = state.run_plan.production_strategy.volume_policy
    if policy is None:
        raise ValueError("Volume retry policy is missing")
    used_scene_retries = sum(item.consumes_ai_retry for item in scene.generation_attempts)
    used_run_retries = sum(
        item.consumes_ai_retry
        for runtime_scene in state.scenes
        for item in runtime_scene.generation_attempts
    )
    if (
        used_scene_retries >= policy.hard_fail_retry_per_scene
        or used_run_retries >= policy.max_ai_retries_per_run
    ):
        raise PermissionError("Volume AI retry budget is exhausted")

    request = _pollinations_request(
        state,
        scene_id=scene_id,
        prompt_source=prompt_source,
        privacy=privacy or PollinationsPrivacyMetadata(),
    )
    public_request = prepare_public_request(request, model=provider.capabilities().model)
    sanitized_hash = public_request_hash(public_request)
    paths = pollinations_production_job_paths(
        out_root, job_id=state.run_plan.job_id, scene_id=scene_id
    )
    persist_execution_snapshot(
        state,
        paths,
        execution_purpose="volume_public_safe_pollinations_overflow",
        selected_scene_id=scene_id,
    )
    started = time.monotonic()
    provider_metadata: CandidateMetadata | None = None
    provider_reaching_calls = 0
    error_category: PollinationsErrorCategory | None = None
    error_message = ""
    try:
        provider_metadata = await provider.generate_public_scene(request, paths.candidate_base_path)
        provider_reaching_calls = 1
        if (
            provider_metadata.provider != ProviderKind.POLLINATIONS.value
            or provider_metadata.logical_request_hash != sanitized_hash
            or provider_metadata.provider_request_hash != sanitized_hash
            or provider_metadata.reference_hashes
            or not provider_metadata.output_path.is_file()
        ):
            raise ValueError("Pollinations provider metadata violates the authorized request")
        artifact_sha = sha256_file(provider_metadata.output_path)
        attempt = GenerationAttempt(
            scene_id=scene_id,
            tier=GenerationTier.DRAFT,
            provider=ProviderKind.POLLINATIONS.value,
            provider_kind=ProviderKind.POLLINATIONS,
            model=provider_metadata.model,
            seed=request.seed,
            candidate_id=f"{scene_id}-pollinations-candidate-02",
            candidate_path=str(provider_metadata.output_path.resolve()),
            artifact_sha256=artifact_sha,
            planning_request_hash=sanitized_hash,
            logical_request_hash=sanitized_hash,
            provider_request_hash=sanitized_hash,
            reference_hashes=[],
            technical_status=TechnicalStatus.SUCCEEDED,
            consumes_ai_call=True,
            consumes_ai_retry=True,
            metadata={
                "mime_type": provider_metadata.mime_type,
                "actual_width": provider_metadata.actual_width,
                "actual_height": provider_metadata.actual_height,
                "failover_from": ProviderKind.CLOUDFLARE_KLEIN_4B.value,
                "failover_category": authorization.failure_category.value,
            },
        )
    except PollinationsError as exc:
        provider_reaching_calls = int(exc.request_reached_provider)
        error_category = exc.category
        error_message = exc.sanitized_message
        attempt = GenerationAttempt(
            scene_id=scene_id,
            tier=GenerationTier.DRAFT,
            provider=ProviderKind.POLLINATIONS.value,
            provider_kind=ProviderKind.POLLINATIONS,
            model=provider.capabilities().model,
            seed=request.seed,
            candidate_id=f"{scene_id}-pollinations-candidate-02",
            planning_request_hash=sanitized_hash,
            logical_request_hash=sanitized_hash,
            provider_request_hash=sanitized_hash,
            reference_hashes=[],
            technical_status=(
                TechnicalStatus.PROVIDER_QUOTA_BLOCKED
                if exc.category
                in {
                    PollinationsErrorCategory.RATE_LIMITED,
                    PollinationsErrorCategory.QUOTA_OR_CREDIT_EXHAUSTED,
                }
                else TechnicalStatus.TECHNICAL_GENERATION_FAIL
            ),
            technical_failure_reason=f"{exc.category.value}: {exc.sanitized_message}",
            consumes_ai_call=True,
            consumes_ai_retry=True,
            metadata={
                "error_category": exc.category.value,
                "failover_from": ProviderKind.CLOUDFLARE_KLEIN_4B.value,
                "failover_category": authorization.failure_category.value,
            },
        )
    except Exception as exc:
        provider_reaching_calls = 1
        error_category = PollinationsErrorCategory.MALFORMED_RESPONSE
        error_message = f"provider result validation failed: {type(exc).__name__}"
        attempt = GenerationAttempt(
            scene_id=scene_id,
            tier=GenerationTier.DRAFT,
            provider=ProviderKind.POLLINATIONS.value,
            provider_kind=ProviderKind.POLLINATIONS,
            model=provider.capabilities().model,
            seed=request.seed,
            candidate_id=f"{scene_id}-pollinations-candidate-02",
            planning_request_hash=sanitized_hash,
            logical_request_hash=sanitized_hash,
            provider_request_hash=sanitized_hash,
            reference_hashes=[],
            technical_status=TechnicalStatus.TECHNICAL_GENERATION_FAIL,
            technical_failure_reason=(
                f"{PollinationsErrorCategory.MALFORMED_RESPONSE.value}: {error_message}"
            ),
            consumes_ai_call=True,
            consumes_ai_retry=True,
            metadata={
                "error_category": PollinationsErrorCategory.MALFORMED_RESPONSE.value,
                "failover_from": ProviderKind.CLOUDFLARE_KLEIN_4B.value,
                "failover_category": authorization.failure_category.value,
            },
        )
    latency_ms = round((time.monotonic() - started) * 1000)
    attempt = attempt.model_copy(
        update={"metadata": {**attempt.metadata, "provider_latency_ms": latency_ms}},
        deep=True,
    )
    updated = record_pollinations_generation_attempt(
        state,
        attempt,
        prior_candidate_id=authorization.prior_candidate_id,
    )
    updated = updated.model_copy(
        update={"external_calls": state.external_calls + provider_reaching_calls},
        deep=True,
    )
    persisted_metadata: dict[str, object]
    if provider_metadata is not None:
        persisted_metadata = provider_metadata.model_dump(mode="json")
    else:
        persisted_metadata = {
            "provider": ProviderKind.POLLINATIONS.value,
            "model": provider.capabilities().model,
            "request_hash": sanitized_hash,
            "error_category": error_category.value if error_category else "provider_error",
            "error_message": error_message,
            "provider_reaching_calls": provider_reaching_calls,
            "reference_hashes": [],
        }
    persist_execution_snapshot(
        updated,
        paths,
        execution_purpose="volume_public_safe_pollinations_overflow",
        selected_scene_id=scene_id,
        candidate_metadata=persisted_metadata,
    )
    return PollinationsExecutionOutcome(
        state=updated,
        paths=paths,
        attempt=attempt,
        provider_metadata=provider_metadata,
        provider_reaching_calls=provider_reaching_calls,
        external_calls=provider_reaching_calls,
        latency_ms=latency_ms,
    )


__all__ = [
    "CloudflareOverflowAuthorization",
    "PollinationsExecutionOutcome",
    "execute_pollinations_overflow",
    "pollinations_production_job_paths",
]
