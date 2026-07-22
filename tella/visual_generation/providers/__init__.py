"""Provider adapters for the visual-quality proof."""

from .base import SceneImageProvider, validate_provider_capabilities
from .existing import ExistingTellaProviderAdapter
from .kinds import ProviderDescriptor, ProviderKind, describe_provider

__all__ = [
    "ExistingTellaProviderAdapter",
    "ProviderDescriptor",
    "ProviderKind",
    "SceneImageProvider",
    "describe_provider",
    "validate_provider_capabilities",
]
