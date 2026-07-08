"""Image provider abstraction for generated visuals."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tella.media import ai_image

logger = logging.getLogger("tella.media.image_provider")


@dataclass
class ImageResult:
    output_path: Path
    provider: str
    prompt_used: str
    negative_prompt_used: str = ""
    seed: int | None = None
    used_reference_conditioning: bool = False
    reference_paths: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class ImageProvider:
    provider_name = "base"

    def supports_reference_conditioning(self) -> bool:
        return False

    def supports_seed(self) -> bool:
        return False

    def is_configured(self) -> bool:
        return True

    async def generate_text_image(
        self,
        prompt: str,
        negative_prompt: str,
        aspect: str,
        seed: int | None,
        out_path: Path,
        metadata: dict[str, Any] | None = None,
    ) -> ImageResult:
        raise NotImplementedError

    async def generate_reference_image(
        self,
        prompt: str,
        references: list[Path],
        negative_prompt: str,
        aspect: str,
        seed: int | None,
        out_path: Path,
        metadata: dict[str, Any] | None = None,
    ) -> ImageResult:
        if references and not self.supports_reference_conditioning():
            logger.warning(
                "Reference image conditioning is not available for provider=%s; using text lock only.",
                self.provider_name,
            )
        return await self.generate_text_image(
            prompt=prompt,
            negative_prompt=negative_prompt,
            aspect=aspect,
            seed=seed,
            out_path=out_path,
            metadata={
                **(metadata or {}),
                "reference_paths": [str(p) for p in references],
                "used_reference_conditioning": False,
            },
        )


class CloudflareImageProvider(ImageProvider):
    provider_name = "cloudflare"

    def supports_reference_conditioning(self) -> bool:
        return False

    def supports_seed(self) -> bool:
        return True

    def is_configured(self) -> bool:
        return bool(ai_image.resolve_all_credentials())

    async def generate_text_image(
        self,
        prompt: str,
        negative_prompt: str,
        aspect: str,
        seed: int | None,
        out_path: Path,
        metadata: dict[str, Any] | None = None,
    ) -> ImageResult:
        width, height = _dims_for_aspect(aspect)
        prompt_used = _merge_negative_prompt(prompt, negative_prompt)
        try:
            await ai_image.generate_image(
                prompt_used,
                out_path,
                width=width,
                height=height,
                seed=seed,
            )
        except RuntimeError as exc:
            if not _looks_like_safety_rejection(str(exc)):
                raise
            prompt_used = _safety_sanitize_prompt(prompt_used)
            logger.warning(
                "provider=%s safety rejection; retrying with sanitized visual prompt",
                self.provider_name,
            )
            await ai_image.generate_image(
                prompt_used,
                out_path,
                width=width,
                height=height,
                seed=seed,
            )
        return ImageResult(
            output_path=out_path,
            provider=self.provider_name,
            prompt_used=prompt_used,
            negative_prompt_used=negative_prompt,
            seed=seed,
            used_reference_conditioning=False,
            reference_paths=list((metadata or {}).get("reference_paths", [])),
            metadata=dict(metadata or {}),
        )


def get_image_provider(name: str = "cloudflare") -> ImageProvider:
    normalized = (name or "cloudflare").strip().lower()
    if normalized != "cloudflare":
        raise RuntimeError(
            f"Unsupported TELLA_IMAGE_PROVIDER={name!r}; only 'cloudflare' is available."
        )
    return CloudflareImageProvider()


def _dims_for_aspect(aspect: str) -> tuple[int, int]:
    if aspect == "16:9":
        return 1344, 768
    return 768, 1344


def _merge_negative_prompt(prompt: str, negative_prompt: str) -> str:
    prompt = _compact(prompt)
    negative_prompt = _compact(negative_prompt)
    if not negative_prompt:
        return _limit(prompt)
    merged = f"{_limit(prompt, 1700)}\n\nAvoid: {_limit(negative_prompt, 260)}"
    return _limit(merged)


def _compact(text: str) -> str:
    return " ".join((text or "").split())


def _limit(text: str, max_chars: int = 2000) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars].rsplit(",", 1)[0].strip()
    return cut or text[:max_chars].strip()


def _looks_like_safety_rejection(message: str) -> bool:
    lower = message.lower()
    return "3030" in lower or "nsfw" in lower or "safety" in lower


def _safety_sanitize_prompt(prompt: str) -> str:
    replacements = {
        "young adult": "adult",
        "young woman": "adult woman",
        "girl": "woman",
        "body": "figure",
        "full figure": "complete figure",
        "full-body": "complete character",
        "full body": "complete character",
        "emotional": "gentle",
        "sad": "quiet",
        "pain": "worry",
        "hurt": "worry",
        "wounded": "tired",
        "injury": "imperfection",
        "skin": "surface",
    }
    cleaned = prompt
    for old, new in replacements.items():
        cleaned = re_replace_case_insensitive(cleaned, old, new)
    cleaned = _compact(cleaned)
    return _limit(cleaned)


def re_replace_case_insensitive(text: str, old: str, new: str) -> str:
    import re

    return re.sub(re.escape(old), new, text, flags=re.IGNORECASE)


__all__ = ["ImageProvider", "ImageResult", "CloudflareImageProvider", "get_image_provider"]
