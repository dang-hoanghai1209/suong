"""Opt-in, reference-conditioned proof-of-visual-quality pipeline."""

from .models import (
    GenerationRequest,
    ProviderCapabilities,
    QCDecision,
    ReferencePack,
    SceneBrief,
    SceneResult,
    StyleBible,
    VisualQCResult,
)
from .tiers import VisualQualityTier, VisualTierConfig, resolve_visual_tier

__all__ = [
    "GenerationRequest",
    "ProviderCapabilities",
    "QCDecision",
    "ReferencePack",
    "SceneBrief",
    "SceneResult",
    "StyleBible",
    "VisualQCResult",
    "VisualQualityTier",
    "VisualTierConfig",
    "resolve_visual_tier",
]
