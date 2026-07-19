"""Provider capability and invocation boundary."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..models import CandidateMetadata, GenerationRequest, ProviderCapabilities


class SceneImageProvider(Protocol):
    def capabilities(self) -> ProviderCapabilities: ...

    def credentials_present(self) -> bool: ...

    async def generate_scene(
        self, request: GenerationRequest, output_path: Path
    ) -> CandidateMetadata: ...

    async def edit_scene(
        self, source_path: Path, request: GenerationRequest, output_path: Path
    ) -> CandidateMetadata: ...


def validate_provider_capabilities(capabilities: ProviderCapabilities) -> None:
    missing: list[str] = []
    if not capabilities.supports_text_to_image:
        missing.append("text-to-image")
    if not capabilities.supports_reference_images:
        missing.append("reference images")
    if not capabilities.supports_9_16:
        missing.append("9:16")
    if missing:
        raise RuntimeError(
            f"provider {capabilities.provider_id} capability mismatch: missing "
            + ", ".join(missing)
        )
