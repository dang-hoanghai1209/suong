"""PUBLIC_SAFE text-only Pollinations image provider with an injected one-shot transport."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
from collections.abc import Awaitable, Callable
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import httpx
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tella.atomic_write import atomic_write_bytes
from ..models import CandidateMetadata, ProviderCapabilities
from .kinds import ProviderKind

DEFAULT_BASE_URL = "https://gen.pollinations.ai"
DEFAULT_MODEL = "flux"
DEFAULT_TIMEOUT_SECONDS = 120.0
API_KEY_ENV = "POLLINATIONS_API_KEY"


class PollinationsErrorCategory(StrEnum):
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    RATE_LIMITED = "rate_limited"
    QUOTA_OR_CREDIT_EXHAUSTED = "quota_or_credit_exhausted"
    TIMEOUT = "timeout"
    INVALID_REQUEST = "invalid_request"
    UNSAFE_REQUEST_BLOCKED_LOCALLY = "unsafe_request_blocked_locally"
    MALFORMED_RESPONSE = "malformed_response"
    PROVIDER_ERROR = "provider_error"


class PollinationsDataSensitivity(StrEnum):
    LOCAL_ONLY = "local_only"
    PRIVATE = "private"
    PUBLIC_SAFE = "public_safe"


class PollinationsError(RuntimeError):
    def __init__(
        self,
        category: PollinationsErrorCategory,
        message: str,
        *,
        request_reached_provider: bool,
        response_received: bool = False,
        status_code: int | None = None,
    ) -> None:
        self.category = category
        self.sanitized_message = message
        self.request_reached_provider = request_reached_provider
        self.response_received = response_received
        self.status_code = status_code
        super().__init__(
            f"Pollinations provider failure: category={category.value}; message={message}; "
            f"request_reached_provider={str(request_reached_provider).lower()}"
        )


class PollinationsConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    base_url: str = DEFAULT_BASE_URL
    api_key_env: str = API_KEY_ENV
    model: str = DEFAULT_MODEL
    timeout_seconds: float = Field(default=DEFAULT_TIMEOUT_SECONDS, gt=0)


class PollinationsPromptSource(BaseModel):
    """Allowlisted semantic text; it has no path, asset, reference, or secret fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scene_meaning: str = Field(min_length=1)
    action: list[str] = Field(default_factory=list)
    mood: list[str] = Field(default_factory=list)
    setting: list[str] = Field(default_factory=list)
    generic_character_description: str = Field(min_length=1)
    style_description: str = Field(min_length=1)
    composition: list[str] = Field(default_factory=list)
    negative_constraints: list[str] = Field(default_factory=list)


class PollinationsPrivacyMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    private_reference_present: bool = False
    source_sheet_requested: bool = False
    private_master_requested: bool = False
    contains_private_data: bool = False


class PollinationsExecutionRequest(BaseModel):
    """Security envelope inspected locally and never serialized to the transport."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scene_id: str = Field(pattern=r"^scene_[0-9]{2}$")
    sensitivity: PollinationsDataSensitivity
    prompt_source: PollinationsPromptSource
    privacy: PollinationsPrivacyMetadata = Field(default_factory=PollinationsPrivacyMetadata)
    reference_attachments: tuple[Path, ...] = ()
    seed: int = Field(ge=0, le=2_147_483_647)
    width: int = Field(ge=64, le=1920)
    height: int = Field(ge=64, le=1920)
    candidate_index: int = Field(default=1, ge=1)
    attempt: int = Field(default=1, ge=1)
    consumes_ai_retry: bool = False

    @model_validator(mode="after")
    def validate_portrait_dimensions(self) -> "PollinationsExecutionRequest":
        if abs((self.width / self.height) - (9 / 16)) > 0.01:
            raise ValueError("Pollinations production requests must use a 9:16 canvas")
        return self


class PollinationsPublicImageRequest(BaseModel):
    """Sanitized provider DTO. Only these fields may reach Pollinations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scene_id: str = Field(pattern=r"^scene_[0-9]{2}$", exclude=True)
    prompt: str = Field(min_length=1, max_length=32_000)
    model: str = Field(min_length=1)
    width: int = Field(ge=64, le=1920)
    height: int = Field(ge=64, le=1920)
    seed: int = Field(ge=0, le=2_147_483_647)
    safe: Literal["privacy,secrets"] = "privacy,secrets"

    def outbound_parameters(self) -> dict[str, str | int]:
        return {
            "model": self.model,
            "width": self.width,
            "height": self.height,
            "seed": self.seed,
            "safe": self.safe,
        }


def prepare_public_request(
    request: PollinationsExecutionRequest,
    *,
    model: str = DEFAULT_MODEL,
) -> PollinationsPublicImageRequest:
    unsafe = (
        request.sensitivity is not PollinationsDataSensitivity.PUBLIC_SAFE
        or request.privacy.private_reference_present
        or request.privacy.source_sheet_requested
        or request.privacy.private_master_requested
        or request.privacy.contains_private_data
        or bool(request.reference_attachments)
    )
    if unsafe:
        raise PollinationsError(
            PollinationsErrorCategory.UNSAFE_REQUEST_BLOCKED_LOCALLY,
            "request is not PUBLIC_SAFE text-only generation",
            request_reached_provider=False,
        )
    source = request.prompt_source
    sections = [
        f"Scene meaning: {source.scene_meaning}",
        f"Generic character: {source.generic_character_description}",
        f"Action: {', '.join(source.action)}" if source.action else "",
        f"Mood: {', '.join(source.mood)}" if source.mood else "",
        f"Setting: {', '.join(source.setting)}" if source.setting else "",
        f"Style: {source.style_description}",
        f"Composition: {', '.join(source.composition)}" if source.composition else "",
        (
            f"Avoid: {', '.join(source.negative_constraints)}"
            if source.negative_constraints
            else ""
        ),
    ]
    return PollinationsPublicImageRequest(
        scene_id=request.scene_id,
        prompt=". ".join(section for section in sections if section),
        model=model,
        width=request.width,
        height=request.height,
        seed=request.seed,
    )


def public_request_hash(request: PollinationsPublicImageRequest) -> str:
    material = {
        "provider": ProviderKind.POLLINATIONS.value,
        "scene_id": request.scene_id,
        "prompt": request.prompt,
        **request.outbound_parameters(),
    }
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class PollinationsSceneImageProvider:
    def __init__(
        self,
        *,
        config: PollinationsConfig | None = None,
        api_key_resolver: Callable[[], str] | None = None,
        request_sender: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self.config = config or PollinationsConfig()
        self._api_key_resolver = api_key_resolver or self._environment_api_key
        self._request_sender = request_sender or _get_once

    def _environment_api_key(self) -> str:
        return (os.environ.get(self.config.api_key_env) or "").strip()

    def credentials_present(self) -> bool:
        return bool(self._api_key_resolver())

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=ProviderKind.POLLINATIONS.value,
            model=self.config.model,
            supports_text_to_image=True,
            supports_reference_images=False,
            supports_multiple_references=False,
            supports_image_edit=False,
            supports_seed=True,
            supports_9_16=True,
            max_reference_images=0,
        )

    async def generate_public_scene(
        self,
        request: PollinationsExecutionRequest,
        output_path: Path,
    ) -> CandidateMetadata:
        public_request = prepare_public_request(request, model=self.config.model)
        if not self.config.enabled:
            raise PollinationsError(
                PollinationsErrorCategory.UNSAFE_REQUEST_BLOCKED_LOCALLY,
                "Pollinations provider is disabled",
                request_reached_provider=False,
            )
        api_key = self._api_key_resolver()
        if not api_key:
            raise PollinationsError(
                PollinationsErrorCategory.PROVIDER_UNAVAILABLE,
                "Pollinations credential is unavailable",
                request_reached_provider=False,
            )
        possible_targets = (output_path.with_suffix(".png"), output_path.with_suffix(".jpg"))
        if any(path.exists() for path in possible_targets):
            raise FileExistsError("Pollinations candidate target already exists")
        # The official POST /v1/images/generations schema does not document the
        # deterministic seed supported by GET /image/{prompt}. Until it does,
        # GET is permitted only here, after the dedicated PUBLIC_SAFE text-only
        # boundary above has rejected private/local data and all attachments.
        request_url = _public_safe_get_url(self.config.base_url, public_request.prompt)
        transport_failure: PollinationsError | None = None
        try:
            response = await self._request_sender(
                url=request_url,
                headers={"Authorization": f"Bearer {api_key}"},
                params=public_request.outbound_parameters(),
                timeout_seconds=self.config.timeout_seconds,
            )
        except (TimeoutError, httpx.TimeoutException) as exc:
            transport_failure = PollinationsError(
                PollinationsErrorCategory.TIMEOUT,
                _sanitize(str(exc), api_key),
                request_reached_provider=True,
            )
        except Exception as exc:
            transport_failure = PollinationsError(
                PollinationsErrorCategory.PROVIDER_ERROR,
                _sanitize(str(exc), api_key),
                request_reached_provider=True,
            )
        if transport_failure is not None:
            # Raise outside the handler so the unsanitized transport exception is
            # not retained as a displayed cause or context in logs/tracebacks.
            raise transport_failure
        status = int(getattr(response, "status_code", 0))
        content = bytes(getattr(response, "content", b"") or b"")
        if status != 200:
            raise PollinationsError(
                _http_category(status),
                f"HTTP {status}: {_sanitize(_response_text(response), api_key)}",
                request_reached_provider=True,
                response_received=True,
                status_code=status,
            )
        if not content:
            raise PollinationsError(
                PollinationsErrorCategory.MALFORMED_RESPONSE,
                "provider returned empty image bytes",
                request_reached_provider=True,
                response_received=True,
                status_code=status,
            )
        try:
            with Image.open(io.BytesIO(content)) as image:
                width, height = image.size
                image_format = (image.format or "").upper()
                image.verify()
        except Exception as exc:
            raise PollinationsError(
                PollinationsErrorCategory.MALFORMED_RESPONSE,
                "provider response is not a decodable image",
                request_reached_provider=True,
                response_received=True,
                status_code=status,
            ) from exc
        formats = {"PNG": (".png", "image/png"), "JPEG": (".jpg", "image/jpeg")}
        if image_format not in formats or width <= 0 or height <= 0:
            raise PollinationsError(
                PollinationsErrorCategory.MALFORMED_RESPONSE,
                "provider returned an unsupported or empty image",
                request_reached_provider=True,
                response_received=True,
                status_code=status,
            )
        if (width, height) != (public_request.width, public_request.height):
            raise PollinationsError(
                PollinationsErrorCategory.MALFORMED_RESPONSE,
                "provider image dimensions do not match the sanitized request",
                request_reached_provider=True,
                response_received=True,
                status_code=status,
            )
        suffix, mime = formats[image_format]
        artifact_path = output_path.with_suffix(suffix).resolve()
        atomic_write_bytes(artifact_path, content)
        digest = public_request_hash(public_request)
        return CandidateMetadata(
            provider=ProviderKind.POLLINATIONS.value,
            model=self.config.model,
            request_hash=digest,
            logical_request_hash=digest,
            reference_hashes=[],
            instruction_hash=hashlib.sha256(public_request.prompt.encode("utf-8")).hexdigest(),
            seed=public_request.seed,
            generation_attempt=request.attempt,
            output_path=artifact_path,
            requested_aspect_ratio="9:16",
            requested_resolution=f"{public_request.width}x{public_request.height}",
            actual_width=width,
            actual_height=height,
            mime_type=mime,
            requested_width=public_request.width,
            requested_height=public_request.height,
            provider_request_hash=digest,
            request_timeout_seconds=self.config.timeout_seconds,
        )


async def _get_once(**kwargs: Any) -> httpx.Response:
    timeout = float(kwargs["timeout_seconds"])
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.get(
            kwargs["url"],
            headers=kwargs["headers"],
            params=kwargs["params"],
        )


def _public_safe_get_url(base_url: str, prompt: str) -> str:
    """Build the prompt-bearing URL only for an already-sanitized public DTO."""
    return f"{base_url.rstrip('/')}/image/{quote(prompt, safe='')}"


def _http_category(status: int) -> PollinationsErrorCategory:
    if status == 429:
        return PollinationsErrorCategory.RATE_LIMITED
    if status == 402:
        return PollinationsErrorCategory.QUOTA_OR_CREDIT_EXHAUSTED
    if status in {400, 401, 403, 404, 405, 422}:
        return PollinationsErrorCategory.INVALID_REQUEST
    if status in {502, 503, 504}:
        return PollinationsErrorCategory.PROVIDER_UNAVAILABLE
    return PollinationsErrorCategory.PROVIDER_ERROR


def _response_text(response: Any) -> str:
    try:
        return str(response.text)
    except Exception:
        return "unreadable provider response"


def _sanitize(message: str, api_key: str) -> str:
    value = " ".join((message or "").split())
    if api_key:
        value = value.replace(api_key, "[REDACTED]")
    value = re.sub(r"(?i)Bearer\s+\S+", "Bearer [REDACTED]", value)
    value = re.sub(r"(?i)(token|authorization)\s*[:=]\s*\S+", r"\1=[REDACTED]", value)
    return value[:600] or "no provider message"


__all__ = [
    "API_KEY_ENV",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT_SECONDS",
    "PollinationsConfig",
    "PollinationsDataSensitivity",
    "PollinationsError",
    "PollinationsErrorCategory",
    "PollinationsExecutionRequest",
    "PollinationsPrivacyMetadata",
    "PollinationsPromptSource",
    "PollinationsPublicImageRequest",
    "PollinationsSceneImageProvider",
    "prepare_public_request",
    "public_request_hash",
]
