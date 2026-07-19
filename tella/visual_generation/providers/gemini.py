"""Gemini reference-conditioned complete-scene image provider."""
from __future__ import annotations

import asyncio
import base64
import io
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image

from tella._gemini import get_client
from tella.atomic_write import atomic_write_bytes

from ..models import CandidateMetadata, GenerationRequest, ProviderCapabilities
from ..prompt_builder import instruction_hash, request_hash

DEFAULT_MODEL = "gemini-3.1-flash-image"
DEFAULT_RESOLUTION = "1K"
SUPPORTED_REFERENCE_MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(api[_-]?key|x-goog-api-key|authorization)\s*[:=]\s*([^\s,;]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")
_LONG_BASE64 = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{24,}={0,2}")


class GeminiProviderError(RuntimeError):
    """Safe, stage-aware Gemini failure suitable for CLI output."""

    def __init__(
        self,
        *,
        stage: str,
        exception_class: str,
        message: str,
        request_reached_gemini: bool,
        response_received: bool,
        response_shape_matched: bool,
        image_bytes_present: bool,
    ) -> None:
        self.stage = stage
        self.exception_class = exception_class
        self.sanitized_message = message
        self.request_reached_gemini = request_reached_gemini
        self.response_received = response_received
        self.response_shape_matched = response_shape_matched
        self.image_bytes_present = image_bytes_present
        super().__init__(
            "Gemini provider failure: "
            f"stage={stage}; exception_class={exception_class}; message={message}; "
            f"request_reached_gemini={str(request_reached_gemini).lower()}; "
            f"response_received={str(response_received).lower()}; "
            f"response_shape_matched={str(response_shape_matched).lower()}; "
            f"image_bytes_present={str(image_bytes_present).lower()}"
        )


class GeminiSceneImageProvider:
    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        resolution: str = DEFAULT_RESOLUTION,
        client_factory: Callable[[], Any] = get_client,
    ) -> None:
        if resolution not in {"0.5K", "1K", "2K", "4K"}:
            raise ValueError("Gemini image resolution must be 0.5K, 1K, 2K, or 4K")
        self.model = model
        self.resolution = resolution
        self._client_factory = client_factory

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id="gemini",
            model=self.model,
            supports_text_to_image=True,
            supports_reference_images=True,
            supports_multiple_references=True,
            supports_image_edit=False,
            supports_seed=False,
            supports_9_16=True,
            max_reference_images=10,
        )

    def credentials_present(self) -> bool:
        return any(
            (os.environ.get(name) or "").strip()
            for name in ("GEMINI_API_KEYS", "GEMINI_API_KEY", "GOOGLE_API_KEY")
        )

    async def generate_scene(
        self, request: GenerationRequest, output_path: Path
    ) -> CandidateMetadata:
        if not self.credentials_present():
            raise RuntimeError("LIVE_VISUAL_ACCEPTANCE_BLOCKED_CREDENTIAL_MISSING")
        if os.environ.get("TELLA_VISUAL_QUALITY_LIVE") != "1":
            raise RuntimeError("LIVE_VISUAL_ACCEPTANCE_NOT_RUN_OPT_IN_REQUIRED")
        if not request.references:
            raise RuntimeError("LIVE_VISUAL_ACCEPTANCE_BLOCKED_REFERENCE_MISSING")
        if len(request.references) > self.capabilities().max_reference_images:
            raise RuntimeError("LIVE_VISUAL_ACCEPTANCE_BLOCKED_PROVIDER_CAPABILITY")

        try:
            instruction = _provider_instruction(request)
            request_digest = request_hash(request)
            instruction_digest = instruction_hash(request)
            response_format = {
                "type": "image",
                "mime_type": "image/jpeg",
                "aspect_ratio": request.aspect_ratio,
                "image_size": self.resolution,
            }
        except Exception as exc:
            raise _provider_error("request_build", exc) from exc

        parts: list[dict[str, str]] = [{"type": "text", "text": instruction}]
        roles: list[list[str]] = []
        for reference in request.references:
            if not reference.path.is_file():
                raise RuntimeError("LIVE_VISUAL_ACCEPTANCE_BLOCKED_REFERENCE_MISSING")
            mime = SUPPORTED_REFERENCE_MIMES.get(reference.path.suffix.lower())
            if mime is None:
                raise GeminiProviderError(
                    stage="reference_image_load_encode",
                    exception_class="UnsupportedReferenceMimeError",
                    message="reference image has unsupported MIME type",
                    request_reached_gemini=False,
                    response_received=False,
                    response_shape_matched=False,
                    image_bytes_present=False,
                )
            try:
                encoded = base64.b64encode(reference.path.read_bytes()).decode("ascii")
            except Exception as exc:
                raise _provider_error("reference_image_load_encode", exc) from exc
            parts.append({"type": "image", "data": encoded, "mime_type": mime})
            roles.append(reference.semantic_roles or [reference.role])

        try:
            client = self._client_factory()
        except Exception as exc:
            stage = (
                "client_initialization_authentication"
                if _looks_like_auth_error(exc)
                else "client_initialization"
            )
            raise _provider_error(stage, exc) from exc

        try:
            response = await asyncio.to_thread(
                client.interactions.create,
                model=self.model,
                input=parts,
                response_format=response_format,
            )
        except Exception as exc:
            stage = "credential_authentication" if _looks_like_auth_error(exc) else "api_request"
            raise _provider_error(stage, exc, request_reached_gemini=True) from exc

        if response is None:
            raise GeminiProviderError(
                stage="empty_response",
                exception_class="EmptyResponseError",
                message="Gemini returned no response object",
                request_reached_gemini=True,
                response_received=False,
                response_shape_matched=False,
                image_bytes_present=False,
            )
        try:
            output_image = response.output_image
        except AttributeError as exc:
            raise _provider_error(
                "unsupported_response_shape",
                exc,
                request_reached_gemini=True,
                response_received=True,
            ) from exc
        if output_image is None:
            raise GeminiProviderError(
                stage="no_image_found_in_response",
                exception_class="NoImageFoundError",
                message="Gemini response contained no output image",
                request_reached_gemini=True,
                response_received=True,
                response_shape_matched=True,
                image_bytes_present=False,
            )
        try:
            data = output_image.data
        except AttributeError as exc:
            raise _provider_error(
                "unsupported_response_shape",
                exc,
                request_reached_gemini=True,
                response_received=True,
            ) from exc
        if not data:
            raise GeminiProviderError(
                stage="no_image_found_in_response",
                exception_class="EmptyImageDataError",
                message="Gemini output image contained no image bytes",
                request_reached_gemini=True,
                response_received=True,
                response_shape_matched=True,
                image_bytes_present=False,
            )
        try:
            image_bytes = (
                base64.b64decode(data, validate=True) if isinstance(data, str) else bytes(data)
            )
            with Image.open(io.BytesIO(image_bytes)) as image:
                width, height = image.size
                detected_format = (image.format or "").upper()
                image.verify()
        except Exception as exc:
            raise _provider_error(
                "image_decode",
                exc,
                request_reached_gemini=True,
                response_received=True,
                response_shape_matched=True,
                image_bytes_present=True,
            ) from exc
        if detected_format != "JPEG":
            raise GeminiProviderError(
                stage="image_decode",
                exception_class="UnsupportedImageFormatError",
                message=f"unsupported decoded image format: {_sanitize(detected_format)}",
                request_reached_gemini=True,
                response_received=True,
                response_shape_matched=True,
                image_bytes_present=True,
            )
        mime_type = "image/jpeg"
        output_path = output_path.with_suffix(".jpg")
        try:
            atomic_write_bytes(output_path, image_bytes)
        except Exception as exc:
            raise _provider_error(
                "image_write",
                exc,
                request_reached_gemini=True,
                response_received=True,
                response_shape_matched=True,
                image_bytes_present=True,
            ) from exc
        return CandidateMetadata(
            provider="gemini",
            model=self.model,
            request_hash=request_digest,
            reference_hashes=[item.sha256 for item in request.references],
            reference_roles=roles,
            instruction_hash=instruction_digest,
            seed=None,
            generation_attempt=request.attempt,
            output_path=output_path.resolve(),
            requested_aspect_ratio=request.aspect_ratio,
            requested_resolution=self.resolution,
            actual_width=width,
            actual_height=height,
            mime_type=mime_type,
        )

    async def edit_scene(
        self, source_path: Path, request: GenerationRequest, output_path: Path
    ) -> CandidateMetadata:
        raise RuntimeError("Gemini image-edit capability is not enabled in this adapter")


def _provider_instruction(request: GenerationRequest) -> str:
    return (
        f"{request.instruction}\n\nREFERENCE GUIDANCE: Use the supplied images as actual "
        "visual guidance for character archetype, illustration family, line quality, "
        "palette, emotional language, and composition grammar. Create a new complete "
        "coherent scene; do not copy source pixels or reproduce a reference composition. "
        "Generate character, pose, interactions, props, environment, texture, lighting, "
        "and symbolism together.\n\n"
        f"NEGATIVE CONSTRAINTS: {request.negative_instruction}"
    )


def _provider_error(
    stage: str,
    exc: BaseException,
    *,
    request_reached_gemini: bool = False,
    response_received: bool = False,
    response_shape_matched: bool = False,
    image_bytes_present: bool = False,
) -> GeminiProviderError:
    return GeminiProviderError(
        stage=stage,
        exception_class=type(exc).__name__,
        message=_sanitize(str(exc)) or "no provider message",
        request_reached_gemini=request_reached_gemini,
        response_received=response_received,
        response_shape_matched=response_shape_matched,
        image_bytes_present=image_bytes_present,
    )


def _looks_like_auth_error(exc: BaseException) -> bool:
    value = f"{type(exc).__name__} {exc}".upper()
    return any(
        token in value
        for token in (
            "401",
            "403",
            "UNAUTHENTICATED",
            "PERMISSION_DENIED",
            "API_KEY_INVALID",
            "INVALID API KEY",
        )
    )


def _sanitize(message: str) -> str:
    value = " ".join((message or "").split())
    secrets: list[str] = []
    for name in ("GEMINI_API_KEYS", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        raw = (os.environ.get(name) or "").strip()
        if raw:
            secrets.append(raw)
            secrets.extend(piece.strip() for piece in raw.split(",") if piece.strip())
    for secret in sorted(set(secrets), key=len, reverse=True):
        value = value.replace(secret, "[REDACTED]")
    value = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
    value = _BEARER_TOKEN.sub("Bearer [REDACTED]", value)
    value = _LONG_BASE64.sub("[REDACTED_BASE64]", value)
    return value[:600]


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_RESOLUTION",
    "GeminiProviderError",
    "GeminiSceneImageProvider",
]
