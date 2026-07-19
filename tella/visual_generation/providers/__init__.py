"""Provider adapters for the visual-quality proof."""

from .base import SceneImageProvider, validate_provider_capabilities
from .existing import ExistingTellaProviderAdapter

__all__ = [
    "ExistingTellaProviderAdapter",
    "SceneImageProvider",
    "validate_provider_capabilities",
]
