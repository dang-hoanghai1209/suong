"""Deterministic visual-quality tier configuration."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .providers.cloudflare_flux import (
    DEV_MODEL,
    HTTP_TIMEOUT,
    KLEIN_4B_FIXED_STEPS,
    KLEIN_4B_MODEL,
)


class VisualQualityTier(StrEnum):
    DRAFT = "draft"
    ACCEPTANCE = "acceptance"


@dataclass(frozen=True)
class VisualTierConfig:
    tier: VisualQualityTier
    provider: str
    model: str
    steps: int
    timeout_seconds: float
    cost_posture: str
    output_intent: str


TIER_CONFIGS = {
    VisualQualityTier.DRAFT: VisualTierConfig(
        tier=VisualQualityTier.DRAFT,
        provider="cloudflare-flux",
        model=KLEIN_4B_MODEL,
        steps=KLEIN_4B_FIXED_STEPS,
        timeout_seconds=HTTP_TIMEOUT,
        cost_posture="low-cost exploratory",
        output_intent="draft",
    ),
    VisualQualityTier.ACCEPTANCE: VisualTierConfig(
        tier=VisualQualityTier.ACCEPTANCE,
        provider="cloudflare-flux",
        model=DEV_MODEL,
        steps=25,
        timeout_seconds=300.0,
        cost_posture="higher-cost, use sparingly",
        output_intent="acceptance",
    ),
}


def resolve_visual_tier(
    tier: str | VisualQualityTier,
    *,
    provider: str | None = None,
    model: str | None = None,
    steps: int | None = None,
    timeout_seconds: float | None = None,
) -> VisualTierConfig:
    """Resolve and validate an explicit tier plus any operator overrides."""
    selected = VisualQualityTier(tier)
    default = TIER_CONFIGS[selected]
    if provider is not None and provider != default.provider:
        raise ValueError(
            f"tier {selected.value} requires provider {default.provider}, got {provider}"
        )
    if model is not None and model != default.model:
        raise ValueError(
            f"tier {selected.value} requires model {default.model}, got {model}"
        )
    if selected is VisualQualityTier.DRAFT and steps not in (None, default.steps):
        raise ValueError("tier draft requires fixed effective steps 4")
    resolved_steps = default.steps if steps is None else steps
    if resolved_steps < 1:
        raise ValueError("tier steps must be positive")
    resolved_timeout = default.timeout_seconds if timeout_seconds is None else timeout_seconds
    if resolved_timeout <= 0:
        raise ValueError("tier timeout must be positive")
    return VisualTierConfig(
        tier=selected,
        provider=default.provider,
        model=default.model,
        steps=resolved_steps,
        timeout_seconds=resolved_timeout,
        cost_posture=default.cost_posture,
        output_intent=default.output_intent,
    )


__all__ = [
    "TIER_CONFIGS",
    "VisualQualityTier",
    "VisualTierConfig",
    "resolve_visual_tier",
]
