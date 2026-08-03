"""Provider adapters for the visual-quality proof."""

from .base import PublicTextToImageProvider, SceneImageProvider, validate_provider_capabilities
from .existing import ExistingTellaProviderAdapter
from .kinds import ProviderDescriptor, ProviderKind, describe_provider
from .pollinations import (
    PollinationsConfig,
    PollinationsDataSensitivity,
    PollinationsError,
    PollinationsErrorCategory,
    PollinationsExecutionRequest,
    PollinationsPrivacyMetadata,
    PollinationsPromptSource,
    PollinationsPublicImageRequest,
    PollinationsSceneImageProvider,
    prepare_public_request,
    public_request_hash,
)

__all__ = [
    "ExistingTellaProviderAdapter",
    "ProviderDescriptor",
    "ProviderKind",
    "PublicTextToImageProvider",
    "PollinationsConfig",
    "PollinationsDataSensitivity",
    "PollinationsError",
    "PollinationsErrorCategory",
    "PollinationsExecutionRequest",
    "PollinationsPrivacyMetadata",
    "PollinationsPromptSource",
    "PollinationsPublicImageRequest",
    "PollinationsSceneImageProvider",
    "SceneImageProvider",
    "describe_provider",
    "prepare_public_request",
    "public_request_hash",
    "validate_provider_capabilities",
]
