"""Truthful adapter around Tella's currently configured image provider."""
from __future__ import annotations

from pathlib import Path

from tella.media import ai_image
from tella.media.image_provider import ImageProvider, get_image_provider

from ..models import CandidateMetadata, GenerationRequest, ProviderCapabilities


class ExistingTellaProviderAdapter:
    def __init__(self, provider: ImageProvider | None = None, *, name: str = "cloudflare"):
        self._provider = provider or get_image_provider(name)

    def capabilities(self) -> ProviderCapabilities:
        current = self._provider.capabilities()
        return ProviderCapabilities(
            provider_id=current.provider_id,
            model=ai_image.DEFAULT_MODEL,
            supports_text_to_image=current.supports_text_to_image,
            supports_reference_images=current.supports_reference_conditioning,
            supports_multiple_references=(
                current.supports_reference_conditioning and current.max_reference_images > 1
            ),
            supports_image_edit=current.supports_image_to_image,
            supports_seed=current.supports_seed,
            # The existing adapter emits 768x1344 for the "9:16" request token,
            # which is portrait but not an exact 9:16 generation canvas.
            supports_9_16=False,
            max_reference_images=current.max_reference_images,
        )

    def credentials_present(self) -> bool:
        return self._provider.is_configured()

    async def generate_scene(
        self, request: GenerationRequest, output_path: Path
    ) -> CandidateMetadata:
        raise RuntimeError(
            f"provider {self._provider.provider_name} has no reference-conditioned scene adapter"
        )

    async def edit_scene(
        self, source_path: Path, request: GenerationRequest, output_path: Path
    ) -> CandidateMetadata:
        raise RuntimeError(f"provider {self._provider.provider_name} has no image-edit capability")
