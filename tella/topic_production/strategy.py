"""Free-first production strategy, QC policy, and privacy-safe routing contracts."""
from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tella.visual_generation.providers.kinds import ProviderKind, describe_provider


class ProductionStrategy(StrEnum):
    QUALITY = "quality"
    VOLUME = "volume"


class SceneDataSensitivity(StrEnum):
    LOCAL_ONLY = "local_only"
    PRIVATE = "private"
    PUBLIC_SAFE = "public_safe"


class LocalCoverageStatus(StrEnum):
    SATISFIED = "satisfied"
    NOT_SATISFIED = "not_satisfied"
    AMBIGUOUS_UNSAFE = "ambiguous_unsafe"


class SceneGenerationCapability(StrEnum):
    LOCAL_COVERED = "local_covered"
    PRIVATE_AI = "private_ai"
    PUBLIC_SAFE_AI = "public_safe_ai"


class VolumeQCSeverity(StrEnum):
    HARD_FAIL = "hard_fail"
    SOFT_FAIL = "soft_fail"


class VolumeQCAction(StrEnum):
    ACCEPTABLE = "acceptable"
    RETRY_OR_REROUTE = "retry_or_reroute"
    BLOCK = "block"


class ProviderFailureCategory(StrEnum):
    RATE_LIMITED = "rate_limited"
    QUOTA_OR_CREDIT_EXHAUSTED = "quota_or_credit_exhausted"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TIMEOUT = "timeout"
    INVALID_REQUEST = "invalid_request"
    PROVIDER_ERROR = "provider_error"
    SOFT_QC_FAIL = "soft_qc_fail"
    QUALITY_INSUFFICIENT = "quality_insufficient"


class VolumeProductionPolicy(BaseModel):
    """Deterministic one-shot defaults for the free-first volume strategy."""

    model_config = ConfigDict(frozen=True)

    initial_candidates_per_scene: Literal[1] = 1
    soft_fail_retry_budget: Literal[0] = 0
    hard_fail_retry_per_scene: int = Field(default=1, ge=0, le=1)
    max_ai_retries_per_run: int = Field(default=2, ge=0, le=2)
    auto_premium_promotion: Literal[False] = False
    paid_fallback_allowed: Literal[False] = False
    generated_scene_chaining: Literal[False] = False


class ProductionStrategyConfig(BaseModel):
    """Explicit mode selection; Quality remains the backward-compatible default."""

    model_config = ConfigDict(frozen=True)

    strategy: ProductionStrategy = ProductionStrategy.QUALITY
    volume_policy: VolumeProductionPolicy | None = None

    @model_validator(mode="after")
    def validate_policy_matches_strategy(self) -> "ProductionStrategyConfig":
        if self.strategy is ProductionStrategy.QUALITY and self.volume_policy is not None:
            raise ValueError("Quality strategy cannot carry a Volume policy")
        if self.strategy is ProductionStrategy.VOLUME and self.volume_policy is None:
            raise ValueError("Volume strategy requires an explicit Volume policy")
        return self

    @classmethod
    def quality(cls) -> "ProductionStrategyConfig":
        return cls()

    @classmethod
    def volume(
        cls, policy: VolumeProductionPolicy | None = None
    ) -> "ProductionStrategyConfig":
        return cls(
            strategy=ProductionStrategy.VOLUME,
            volume_policy=policy or VolumeProductionPolicy(),
        )


class LocalCoverageAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: LocalCoverageStatus
    reason: str = Field(min_length=1)
    selected_semantic_id: str | None = None


class SceneRoutingRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    sensitivity: SceneDataSensitivity
    local_coverage: LocalCoverageAssessment


class ProviderAvailability(BaseModel):
    model_config = ConfigDict(frozen=True)

    local_compositor: bool = True
    cloudflare_klein_4b: bool = True
    pollinations: bool = True

    def is_available(self, provider: ProviderKind) -> bool:
        if provider is ProviderKind.LOCAL_COMPOSITOR:
            return self.local_compositor
        if provider is ProviderKind.CLOUDFLARE_KLEIN_4B:
            return self.cloudflare_klein_4b
        if provider is ProviderKind.POLLINATIONS:
            return self.pollinations
        return False


class ProviderRoute(BaseModel):
    model_config = ConfigDict(frozen=True)

    capability: SceneGenerationCapability | None
    eligible_providers: tuple[ProviderKind, ...]
    available_route: tuple[ProviderKind, ...]
    selected_provider: ProviderKind | None
    blocked: bool
    reason: str = Field(min_length=1)
    consumes_ai_call: bool = False

    @model_validator(mode="after")
    def validate_selection(self) -> "ProviderRoute":
        if self.blocked == (self.selected_provider is not None):
            raise ValueError("blocked routes must not select a provider")
        if self.selected_provider is not None:
            if self.selected_provider not in self.available_route:
                raise ValueError("selected provider must be in the available route")
            expected = describe_provider(self.selected_provider).consumes_ai_call
            if self.consumes_ai_call is not expected:
                raise ValueError("AI call accounting does not match selected provider")
        elif self.consumes_ai_call:
            raise ValueError("blocked routes cannot consume an AI call")
        return self


class VolumeQCDisposition(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: VolumeQCAction
    consumes_retry_budget: bool
    reason: str = Field(min_length=1)


class ProviderFailoverDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    eligible: bool
    next_provider: ProviderKind | None = None
    reason: str = Field(min_length=1)


def classify_scene_capability(
    sensitivity: SceneDataSensitivity,
    local_coverage: LocalCoverageStatus,
) -> SceneGenerationCapability | None:
    if local_coverage is LocalCoverageStatus.SATISFIED:
        return SceneGenerationCapability.LOCAL_COVERED
    if sensitivity is SceneDataSensitivity.LOCAL_ONLY:
        return None
    if sensitivity is SceneDataSensitivity.PRIVATE:
        return SceneGenerationCapability.PRIVATE_AI
    return SceneGenerationCapability.PUBLIC_SAFE_AI


def _eligible_providers(
    sensitivity: SceneDataSensitivity,
    capability: SceneGenerationCapability | None,
) -> tuple[ProviderKind, ...]:
    """Sensitivity-specific allowlists prevent generic fallback privacy leaks."""

    if capability is SceneGenerationCapability.LOCAL_COVERED:
        return (ProviderKind.LOCAL_COMPOSITOR,)
    if sensitivity is SceneDataSensitivity.LOCAL_ONLY:
        return (ProviderKind.LOCAL_COMPOSITOR,)
    if sensitivity is SceneDataSensitivity.PRIVATE:
        return (ProviderKind.CLOUDFLARE_KLEIN_4B,)
    return (ProviderKind.CLOUDFLARE_KLEIN_4B, ProviderKind.POLLINATIONS)


def route_scene(
    request: SceneRoutingRequest,
    availability: ProviderAvailability | None = None,
) -> ProviderRoute:
    """Choose a route deterministically from privacy-specific provider allowlists."""

    availability = availability or ProviderAvailability()
    capability = classify_scene_capability(
        request.sensitivity,
        request.local_coverage.status,
    )
    eligible = _eligible_providers(request.sensitivity, capability)
    available = tuple(provider for provider in eligible if availability.is_available(provider))

    if capability is None:
        return ProviderRoute(
            capability=None,
            eligible_providers=eligible,
            available_route=(),
            selected_provider=None,
            blocked=True,
            reason=(
                "LOCAL_ONLY scene is not safely covered by the local asset library; "
                "external providers are prohibited"
            ),
        )
    if not available:
        return ProviderRoute(
            capability=capability,
            eligible_providers=eligible,
            available_route=(),
            selected_provider=None,
            blocked=True,
            reason="no eligible provider is currently available; route fails closed",
        )
    selected = available[0]
    return ProviderRoute(
        capability=capability,
        eligible_providers=eligible,
        available_route=available,
        selected_provider=selected,
        blocked=False,
        reason=f"selected first available provider from {request.sensitivity.value} allowlist",
        consumes_ai_call=describe_provider(selected).consumes_ai_call,
    )


def pollinations_failover_decision(
    *,
    sensitivity: SceneDataSensitivity,
    failed_provider: ProviderKind,
    failure: ProviderFailureCategory,
) -> ProviderFailoverDecision:
    """Authorize only PUBLIC_SAFE Cloudflare infrastructure failover to Pollinations."""

    if sensitivity is not SceneDataSensitivity.PUBLIC_SAFE:
        return ProviderFailoverDecision(
            eligible=False,
            reason="Pollinations failover is prohibited unless sensitivity is PUBLIC_SAFE",
        )
    if failed_provider is not ProviderKind.CLOUDFLARE_KLEIN_4B:
        return ProviderFailoverDecision(
            eligible=False,
            reason="Pollinations overflow requires a failed Cloudflare Klein 4B attempt",
        )
    eligible_failures = {
        ProviderFailureCategory.RATE_LIMITED,
        ProviderFailureCategory.QUOTA_OR_CREDIT_EXHAUSTED,
        ProviderFailureCategory.PROVIDER_UNAVAILABLE,
        ProviderFailureCategory.TIMEOUT,
    }
    if failure not in eligible_failures:
        return ProviderFailoverDecision(
            eligible=False,
            reason=f"failure category {failure.value} is not infrastructure failover eligible",
        )
    return ProviderFailoverDecision(
        eligible=True,
        next_provider=ProviderKind.POLLINATIONS,
        reason=f"PUBLIC_SAFE Cloudflare failure {failure.value} authorizes Pollinations overflow",
    )
def volume_qc_disposition(
    severity: VolumeQCSeverity,
    *,
    scene_hard_retries_used: int,
    run_ai_retries_used: int,
    policy: VolumeProductionPolicy | None = None,
) -> VolumeQCDisposition:
    """Prepare Volume QC semantics without accepting or mutating runtime state."""

    policy = policy or VolumeProductionPolicy()
    if scene_hard_retries_used < 0 or run_ai_retries_used < 0:
        raise ValueError("retry counts cannot be negative")
    if severity is VolumeQCSeverity.SOFT_FAIL:
        return VolumeQCDisposition(
            action=VolumeQCAction.ACCEPTABLE,
            consumes_retry_budget=False,
            reason="Volume policy treats a usable soft failure as acceptable",
        )
    if (
        scene_hard_retries_used < policy.hard_fail_retry_per_scene
        and run_ai_retries_used < policy.max_ai_retries_per_run
    ):
        return VolumeQCDisposition(
            action=VolumeQCAction.RETRY_OR_REROUTE,
            consumes_retry_budget=True,
            reason="hard failure is within both scene and run retry ceilings",
        )
    return VolumeQCDisposition(
        action=VolumeQCAction.BLOCK,
        consumes_retry_budget=False,
        reason="hard failure retry budget is exhausted; fail closed",
    )


__all__ = [
    "LocalCoverageAssessment",
    "LocalCoverageStatus",
    "ProductionStrategy",
    "ProductionStrategyConfig",
    "ProviderFailureCategory",
    "ProviderFailoverDecision",
    "ProviderAvailability",
    "ProviderRoute",
    "SceneDataSensitivity",
    "SceneGenerationCapability",
    "SceneRoutingRequest",
    "VolumeProductionPolicy",
    "VolumeQCAction",
    "VolumeQCDisposition",
    "VolumeQCSeverity",
    "classify_scene_capability",
    "pollinations_failover_decision",
    "route_scene",
    "volume_qc_disposition",
]
