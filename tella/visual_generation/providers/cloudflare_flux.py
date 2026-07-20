"""Cloudflare FLUX.2 multipart reference-conditioned scene provider."""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageOps

from tella.atomic_write import atomic_write_bytes
from tella.media.ai_image import resolve_all_credentials

from ..models import CandidateMetadata, GenerationRequest, ProviderCapabilities
from ..prompt_builder import instruction_hash, request_hash
from ..references import sha256_file

DEFAULT_MODEL = "@cf/black-forest-labs/flux-2-klein-9b"
DEV_MODEL = "@cf/black-forest-labs/flux-2-dev"
KLEIN_4B_MODEL = "@cf/black-forest-labs/flux-2-klein-4b"
KLEIN_4B_FIXED_STEPS = 4
DEFAULT_WIDTH = 576
DEFAULT_HEIGHT = 1024
REFERENCE_LIMIT = 511
HTTP_TIMEOUT = 120.0


class CloudflareFluxError(RuntimeError):
    def __init__(
        self,
        *,
        stage: str,
        exception_class: str,
        message: str,
        request_reached_provider: bool,
        response_received: bool,
        image_bytes_present: bool,
    ) -> None:
        self.stage = stage
        self.exception_class = exception_class
        self.sanitized_message = message
        self.request_reached_provider = request_reached_provider
        self.response_received = response_received
        self.image_bytes_present = image_bytes_present
        super().__init__(
            "Cloudflare FLUX provider failure: "
            f"stage={stage}; exception_class={exception_class}; message={message}; "
            f"request_reached_provider={str(request_reached_provider).lower()}; "
            f"response_received={str(response_received).lower()}; "
            f"image_bytes_present={str(image_bytes_present).lower()}"
        )


class CloudflareFluxSceneImageProvider:
    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        steps: int | None = None,
        timeout_seconds: float = HTTP_TIMEOUT,
        tier: str | None = None,
        intended_usage_class: str | None = None,
        credential_resolver: Callable[[], list[tuple[str, str]]] = resolve_all_credentials,
        request_sender: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        if not 256 <= width <= 1920 or not 256 <= height <= 1920:
            raise ValueError("Cloudflare FLUX width and height must be between 256 and 1920")
        if steps is not None and steps < 1:
            raise ValueError("Cloudflare FLUX steps must be positive")
        if model == KLEIN_4B_MODEL:
            if steps not in (None, KLEIN_4B_FIXED_STEPS):
                raise ValueError("Cloudflare FLUX Klein 4B uses fixed 4-step inference")
            effective_steps = KLEIN_4B_FIXED_STEPS
        else:
            if steps is not None and model != DEV_MODEL:
                raise ValueError("Cloudflare FLUX steps are supported only for flux-2-dev")
            effective_steps = steps
        if timeout_seconds <= 0:
            raise ValueError("Cloudflare FLUX timeout must be positive")
        self.model = model
        self.width = width
        self.height = height
        self.steps = effective_steps
        self._serialize_steps = model == DEV_MODEL and steps is not None
        self.timeout_seconds = timeout_seconds
        self.tier = tier
        self.intended_usage_class = intended_usage_class
        self._credential_resolver = credential_resolver
        self._request_sender = request_sender or _post_once

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id="cloudflare-flux",
            model=self.model,
            supports_text_to_image=True,
            supports_reference_images=True,
            supports_multiple_references=True,
            supports_image_edit=False,
            supports_seed=True,
            supports_9_16=True,
            max_reference_images=4,
        )

    def credentials_present(self) -> bool:
        return bool(self._credential_resolver())

    async def generate_scene(
        self, request: GenerationRequest, output_path: Path
    ) -> CandidateMetadata:
        credentials = self._credential_resolver()
        if not credentials:
            raise RuntimeError("LIVE_VISUAL_ACCEPTANCE_BLOCKED_CREDENTIAL_MISSING")
        if os.environ.get("TELLA_VISUAL_QUALITY_LIVE") != "1":
            raise RuntimeError("LIVE_VISUAL_ACCEPTANCE_NOT_RUN_OPT_IN_REQUIRED")
        if not request.references:
            raise RuntimeError("LIVE_VISUAL_ACCEPTANCE_BLOCKED_REFERENCE_MISSING")
        if len(request.references) > 4:
            raise RuntimeError("LIVE_VISUAL_ACCEPTANCE_BLOCKED_PROVIDER_CAPABILITY")

        try:
            prompt = _cloudflare_prompt(request)
            fields = {
                "prompt": prompt,
                "width": str(self.width),
                "height": str(self.height),
            }
            if request.seed is not None:
                fields["seed"] = str(request.seed)
            if self._serialize_steps:
                fields["steps"] = str(self.steps)
            invocation_hash = provider_request_hash(
                request=request,
                prompt=prompt,
                model=self.model,
                width=self.width,
                height=self.height,
                steps=self.steps,
            )
        except Exception as exc:
            raise _error("request_build", exc) from exc

        prepared: list[dict[str, Any]] = []
        files: dict[str, tuple[str, bytes, str]] = {}
        cache = output_path.parent / ".reference_cache"
        for index, reference in enumerate(request.references):
            try:
                item = prepare_reference(reference.path, reference.sha256, cache)
                item["semantic_roles"] = reference.semantic_roles or [reference.role]
                prepared.append(item)
                prepared_path = Path(item["prepared_path"])
                files[f"input_image_{index}"] = (
                    prepared_path.name,
                    prepared_path.read_bytes(),
                    "image/png",
                )
            except Exception as exc:
                raise _error("reference_prepare", exc) from exc

        account_id, token = credentials[0]
        url = (
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/"
            f"{self.model}"
        )
        try:
            response = await self._request_sender(
                url=url,
                headers={"Authorization": f"Bearer {token}"},
                data=fields,
                files=files,
                timeout_seconds=self.timeout_seconds,
            )
        except Exception as exc:
            raise _error("api_request", exc, reached=True) from exc

        if response is None or not getattr(response, "content", b""):
            raise CloudflareFluxError(
                stage="empty_response",
                exception_class="EmptyResponseError",
                message="Cloudflare returned an empty response",
                request_reached_provider=True,
                response_received=False,
                image_bytes_present=False,
            )
        status = int(getattr(response, "status_code", 0))
        if status != 200:
            message = _response_text(response)
            stage = "quota_exceeded" if status == 429 or _is_quota(message) else "api_request"
            raise CloudflareFluxError(
                stage=stage,
                exception_class="CloudflareHTTPError",
                message=f"HTTP {status}: {_sanitize(message)}",
                request_reached_provider=True,
                response_received=True,
                image_bytes_present=False,
            )
        try:
            envelope = response.json()
        except Exception as exc:
            raise _error("cloudflare_envelope", exc, reached=True, received=True) from exc
        if not isinstance(envelope, dict) or envelope.get("success") is False:
            raise CloudflareFluxError(
                stage="cloudflare_envelope",
                exception_class="InvalidCloudflareEnvelopeError",
                message=_sanitize(str(envelope.get("errors", "invalid envelope")))
                if isinstance(envelope, dict)
                else "response envelope is not an object",
                request_reached_provider=True,
                response_received=True,
                image_bytes_present=False,
            )
        result = envelope.get("result")
        if not isinstance(result, dict):
            raise CloudflareFluxError(
                stage="cloudflare_envelope",
                exception_class="InvalidCloudflareEnvelopeError",
                message="response result is not an object",
                request_reached_provider=True,
                response_received=True,
                image_bytes_present=False,
            )
        encoded = result.get("image") or result.get("image_b64")
        if not encoded:
            raise CloudflareFluxError(
                stage="empty_response",
                exception_class="EmptyImageDataError",
                message="Cloudflare result contained no image data",
                request_reached_provider=True,
                response_received=True,
                image_bytes_present=False,
            )
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError, TypeError) as exc:
            raise _error("base64_decode", exc, reached=True, received=True) from exc
        try:
            with Image.open(io.BytesIO(image_bytes)) as image:
                actual_width, actual_height = image.size
                image_format = (image.format or "").upper()
                image.verify()
        except Exception as exc:
            raise _error(
                "image_decode", exc, reached=True, received=True, image_bytes=True
            ) from exc
        suffixes = {"PNG": (".png", "image/png"), "JPEG": (".jpg", "image/jpeg")}
        if image_format not in suffixes:
            raise CloudflareFluxError(
                stage="image_decode",
                exception_class="UnsupportedImageFormatError",
                message=f"unsupported image format: {_sanitize(image_format)}",
                request_reached_provider=True,
                response_received=True,
                image_bytes_present=True,
            )
        suffix, mime = suffixes[image_format]
        output_path = output_path.with_suffix(suffix)
        try:
            atomic_write_bytes(output_path, image_bytes)
        except Exception as exc:
            raise _error(
                "image_write", exc, reached=True, received=True, image_bytes=True
            ) from exc
        return CandidateMetadata(
            tier=self.tier,
            intended_usage_class=self.intended_usage_class,
            provider="cloudflare-flux",
            model=self.model,
            request_hash=request_hash(request),
            logical_request_hash=request_hash(request),
            reference_hashes=[item.sha256 for item in request.references],
            reference_roles=[item.semantic_roles or [item.role] for item in request.references],
            instruction_hash=instruction_hash(request),
            seed=request.seed,
            generation_attempt=request.attempt,
            output_path=output_path.resolve(),
            requested_aspect_ratio=request.aspect_ratio,
            requested_resolution=f"{self.width}x{self.height}",
            requested_width=self.width,
            requested_height=self.height,
            actual_width=actual_width,
            actual_height=actual_height,
            mime_type=mime,
            prepared_references=prepared,
            steps=self.steps,
            provider_request_hash=invocation_hash,
            request_timeout_seconds=self.timeout_seconds,
        )

    async def edit_scene(
        self, source_path: Path, request: GenerationRequest, output_path: Path
    ) -> CandidateMetadata:
        raise RuntimeError("Cloudflare FLUX image-edit capability is not enabled")


def prepare_reference(path: Path, original_hash: str, cache: Path) -> dict[str, Any]:
    original = path.resolve()
    before = sha256_file(original)
    if before != original_hash:
        raise ValueError("reference SHA-256 does not match approved catalog")
    try:
        with Image.open(original) as source:
            image = ImageOps.exif_transpose(source).convert("RGBA")
            image.thumbnail((REFERENCE_LIMIT, REFERENCE_LIMIT), Image.Resampling.LANCZOS)
            width, height = image.size
            stream = io.BytesIO()
            image.save(stream, "PNG", optimize=False, compress_level=9)
    except Exception as exc:
        raise ValueError(f"unable to prepare reference: {type(exc).__name__}") from exc
    cache.mkdir(parents=True, exist_ok=True)
    prepared_path = cache / f"{original_hash}_{width}x{height}.png"
    content = stream.getvalue()
    if not prepared_path.is_file() or prepared_path.read_bytes() != content:
        atomic_write_bytes(prepared_path, content)
    if sha256_file(original) != before:
        raise RuntimeError("approved reference changed during preparation")
    return {
        "original_path": str(original),
        "original_sha256": before,
        "prepared_path": str(prepared_path.resolve()),
        "prepared_sha256": sha256_file(prepared_path),
        "prepared_width": width,
        "prepared_height": height,
    }


async def _post_once(**kwargs: Any) -> httpx.Response:
    timeout = kwargs.pop("timeout_seconds", HTTP_TIMEOUT)
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.post(**kwargs)


def provider_request_hash(
    *,
    request: GenerationRequest,
    prompt: str,
    model: str,
    width: int,
    height: int,
    steps: int | None,
) -> str:
    """Hash the exact non-secret Cloudflare invocation identity."""
    material = {
        "provider": "cloudflare-flux",
        "model": model,
        "prompt": prompt,
        "logical_request_hash": request_hash(request),
        "seed": request.seed,
        "width": width,
        "height": height,
        "steps": steps,
        "references": [
            {
                "sha256": item.sha256,
                "semantic_roles": item.semantic_roles or [item.role],
            }
            for item in request.references
        ],
    }
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _cloudflare_prompt(request: GenerationRequest) -> str:
    return (
        "Use image 0 as guidance for the female character archetype, short dark bob, "
        "dusty-pink long dress, soft hand-drawn editorial illustration style, warm dark "
        "brown visual world, cream halo or vignette, thin imperfect outlines, and muted "
        "palette. Generate a NEW complete illustration; do not copy the source composition "
        "pixel-for-pixel. All symbolic objects must be naturally drawn into the illustration, "
        "never an icon collage or UI.\n\n"
        f"{request.instruction}\n\nNEGATIVE CONSTRAINTS: {request.negative_instruction}"
    )


def _error(
    stage: str,
    exc: BaseException,
    *,
    reached: bool = False,
    received: bool = False,
    image_bytes: bool = False,
) -> CloudflareFluxError:
    return CloudflareFluxError(
        stage=stage,
        exception_class=type(exc).__name__,
        message=_sanitize(str(exc)) or "no provider message",
        request_reached_provider=reached,
        response_received=received,
        image_bytes_present=image_bytes,
    )


def _response_text(response: Any) -> str:
    try:
        return str(response.text)
    except Exception:
        return "unreadable provider response"


def _is_quota(message: str) -> bool:
    value = message.lower()
    return any(token in value for token in ("quota", "daily allocation", "neurons"))


def _sanitize(message: str) -> str:
    value = " ".join((message or "").split())
    secrets: list[str] = []
    for name in ("CF_ACCOUNTS", "CF_AI_TOKEN"):
        raw = (os.environ.get(name) or "").strip()
        if raw:
            secrets.append(raw)
            if name == "CF_ACCOUNTS":
                secrets.extend(
                    part.split(":", 1)[1]
                    for part in raw.split(";")
                    if ":" in part
                )
    for secret in sorted(set(secrets), key=len, reverse=True):
        value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"(?i)Bearer\s+\S+", "Bearer [REDACTED]", value)
    value = re.sub(r"(?i)(token|authorization)\s*[:=]\s*\S+", r"\1=[REDACTED]", value)
    value = re.sub(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{80,}={0,2}", "[REDACTED_BASE64]", value)
    return value[:600]


__all__ = [
    "CloudflareFluxError",
    "CloudflareFluxSceneImageProvider",
    "DEFAULT_HEIGHT",
    "DEFAULT_MODEL",
    "DEFAULT_WIDTH",
    "DEV_MODEL",
    "KLEIN_4B_FIXED_STEPS",
    "KLEIN_4B_MODEL",
    "prepare_reference",
    "provider_request_hash",
]
