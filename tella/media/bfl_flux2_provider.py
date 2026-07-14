"""Fail-closed FLUX.2 reference-conditioned image adapter.

All network boundaries are injectable. This module does not construct an HTTP client,
an R2 client, or credentials by itself.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from tella.atomic_write import atomic_write_bytes
from tella.media.image_provider import ImageProvider, ImageResult
from tella.media.image_provider_contract import (
    ImageProviderCapabilities,
    ReferenceConditionedImageRequest,
    ReferenceSheetManifest,
)
from tella.media.temporary_reference_store import (
    TemporaryReferenceObject,
    TemporaryReferenceStore,
    URLFetchResult,
    upload_and_verify_reference,
)


PINNED_ENDPOINT = "flux-2-pro"
PREVIEW_ENDPOINT = "flux-2-pro-preview"
_ENDPOINTS = {PINNED_ENDPOINT, PREVIEW_ENDPOINT}
_LOCAL_REFERENCE_MIMES = ("image/png", "image/jpeg", "image/webp")
_OUTPUT_MIMES = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}


class BFLFlux2Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    endpoint: str = PINNED_ENDPOINT
    allow_preview: bool = False
    prompt_upsampling_enabled: bool = False
    output_format: str = "png"
    max_prompt_utf8_bytes: int = Field(default=2000, ge=256, le=10000)
    polling_interval_seconds: float = Field(default=0.5, gt=0, le=10)
    maximum_polls: int = Field(default=120, ge=1, le=1000)
    total_timeout_seconds: float = Field(default=180, gt=0, le=900)
    connect_timeout_seconds: float = Field(default=10, gt=0, le=120)
    read_timeout_seconds: float = Field(default=30, gt=0, le=120)
    reference_url_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    reference_url_safety_margin_seconds: float = Field(default=60, ge=30, le=600)
    max_reference_bytes: int = Field(default=20_000_000, ge=1024, le=50_000_000)
    max_total_reference_bytes: int = Field(default=40_000_000, ge=1024, le=200_000_000)
    max_reference_megapixels: float = Field(default=20.0, gt=0, le=100)
    max_output_bytes: int = Field(default=25_000_000, ge=1024, le=100_000_000)
    max_output_megapixels: float = Field(default=4.0, gt=0, le=4.0)
    max_json_response_bytes: int = Field(default=1_000_000, ge=1024, le=5_000_000)
    local_reference_mime_types: tuple[str, ...] = _LOCAL_REFERENCE_MIMES

    @field_validator("output_format")
    @classmethod
    def output_format_supported(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in _OUTPUT_MIMES:
            raise ValueError("BFL output format must be png, jpeg, or webp")
        return normalized

    @model_validator(mode="after")
    def endpoint_policy(self) -> "BFLFlux2Config":
        if self.endpoint not in _ENDPOINTS:
            raise ValueError("BFL endpoint is not allowlisted")
        if self.endpoint == PREVIEW_ENDPOINT and not self.allow_preview:
            raise ValueError("BFL preview endpoint requires explicit opt-in")
        if not self.local_reference_mime_types:
            raise ValueError("local reference MIME allowlist must not be empty")
        if self.reference_url_ttl_seconds < self.minimum_reference_url_ttl_seconds:
            raise ValueError(
                "reference URL TTL is shorter than create, poll, download, and safety bounds"
            )
        if self.max_total_reference_bytes < self.max_reference_bytes:
            raise ValueError("total reference-byte limit cannot be below per-reference limit")
        return self

    @property
    def minimum_reference_url_ttl_seconds(self) -> int:
        create = self.connect_timeout_seconds + self.read_timeout_seconds
        result_download = self.connect_timeout_seconds + self.read_timeout_seconds
        return math.ceil(
            create + self.total_timeout_seconds + result_download
            + self.reference_url_safety_margin_seconds
        )


class BFLTransport(Protocol):
    async def create(
        self, endpoint_url: str, *, headers: dict[str, str], payload: dict[str, Any],
        connect_timeout_seconds: float, read_timeout_seconds: float,
        maximum_response_bytes: int,
    ) -> dict[str, Any]: ...

    async def poll(
        self, polling_url: str, *, headers: dict[str, str],
        connect_timeout_seconds: float, read_timeout_seconds: float,
        maximum_response_bytes: int,
    ) -> dict[str, Any]: ...

    async def download(
        self, result_url: str, *, connect_timeout_seconds: float,
        read_timeout_seconds: float, maximum_bytes: int,
    ) -> URLFetchResult: ...


@dataclass(frozen=True)
class BFLReferenceInput:
    request: ReferenceConditionedImageRequest
    manifest: ReferenceSheetManifest
    approval_record_sha256: str
    content: bytes = field(repr=False)
    content_type: str


class BFLProviderError(RuntimeError):
    def __init__(self, category: str, safe_message: str):
        self.category = category
        self.safe_message = safe_message
        super().__init__(f"{category}: {safe_message}")


class BFLFlux2ReferenceProvider(ImageProvider):
    provider_name = "bfl_flux2_reference"

    def __init__(
        self,
        *,
        config: BFLFlux2Config,
        reference_store: TemporaryReferenceStore | None,
        transport: BFLTransport,
        api_key: SecretStr | None,
        reference_manifest: ReferenceSheetManifest | None = None,
        accounting: dict[str, int] | None = None,
    ) -> None:
        self.config = config
        self.reference_store = reference_store
        self.transport = transport
        self.api_key = api_key
        self.reference_manifest = reference_manifest
        self.accounting = accounting if accounting is not None else {}
        self.cleanup_diagnostic: dict[str, Any] = {"cleanup_required": False}

    def supports_reference_conditioning(self) -> bool:
        return self.reference_store is not None

    def supports_seed(self) -> bool:
        return True

    def capabilities(self) -> ImageProviderCapabilities:
        enabled = self.reference_store is not None
        return ImageProviderCapabilities(
            provider_id=self.provider_name,
            supports_text_to_image=True,
            supports_reference_conditioning=enabled,
            supports_image_to_image=enabled,
            supports_structural_conditioning=False,
            supports_seed=True,
            supports_negative_prompt=False,
            max_prompt_utf8_bytes=self.config.max_prompt_utf8_bytes,
            max_reference_images=8 if enabled else 0,
            accepted_reference_mime_types=(
                self.config.local_reference_mime_types if enabled else ()
            ),
            supports_character_identity_anchor=False,
            identity_anchor_verification=(
                "per_request_verified" if enabled else "unsupported"
            ),
            provider_retry_control="caller_bounded",
        )

    def is_configured(self) -> bool:
        return self.reference_store is not None and self.api_key is not None

    async def generate_text_image(
        self, prompt: str, negative_prompt: str, aspect: str, seed: int | None,
        out_path: Path, metadata: dict[str, Any] | None = None,
    ) -> ImageResult:
        raise RuntimeError("BFL reference adapter requires verified reference conditioning")

    async def generate_reference_image(
        self, prompt: str, references: list[Path], negative_prompt: str,
        aspect: str, seed: int | None, out_path: Path,
        metadata: dict[str, Any] | None = None,
    ) -> ImageResult:
        raise RuntimeError(
            "path-only reference generation is forbidden; use approved typed manifests"
        )

    async def generate_reference_conditioned(
        self, *, request: ReferenceConditionedImageRequest, reference_bytes: bytes,
        reference_mime_type: str, out_path: Path,
    ) -> ImageResult:
        if self.reference_manifest is None:
            raise RuntimeError("approved reference manifest is required")
        return await self.generate_with_references(
            references=[BFLReferenceInput(
                request=request, manifest=self.reference_manifest,
                approval_record_sha256=hashlib.sha256(
                    self.reference_manifest.approval_record.encode("utf-8")
                ).hexdigest(),
                content=reference_bytes, content_type=reference_mime_type,
            )],
            out_path=out_path,
        )

    async def generate_with_references(
        self, *, references: list[BFLReferenceInput], out_path: Path
    ) -> ImageResult:
        if self.reference_store is None:
            raise BFLProviderError("configuration", "temporary reference store is required")
        if self.api_key is None or not self.api_key.get_secret_value():
            raise BFLProviderError("configuration", "BFL credential is missing")
        if not 1 <= len(references) <= 8:
            raise ValueError("BFL reference count must be between one and eight")
        self._inc("local_request_validations")
        for item in references:
            self._validate_reference(item)
        total_reference_bytes = sum(len(item.content) for item in references)
        if total_reference_bytes > self.config.max_total_reference_bytes:
            raise ValueError("references exceed total local byte-size limit")
        first = references[0].request
        scene_binding = (
            first.prompt, first.required_view_or_pose, first.scene_action,
            first.composition_family, first.width, first.height, first.seed,
        )
        if any((
            item.request.prompt, item.request.required_view_or_pose,
            item.request.scene_action, item.request.composition_family,
            item.request.width, item.request.height, item.request.seed,
        ) != scene_binding for item in references[1:]):
            raise ValueError("all references must bind to the same scene request")
        prompt = translate_positive_prompt(first)
        if len(prompt.encode("utf-8")) > self.config.max_prompt_utf8_bytes:
            raise ValueError("BFL prompt exceeds local UTF-8 byte policy")
        if first.width < 64 or first.height < 64:
            raise ValueError("BFL dimensions must be at least 64 pixels")
        if first.width * first.height > int(self.config.max_output_megapixels * 1_000_000):
            raise ValueError("BFL output dimensions exceed local megapixel limit")

        temporary: list[TemporaryReferenceObject] = []
        cleanup_required = False
        result: ImageResult | None = None
        try:
            for item in references:
                obj = await upload_and_verify_reference(
                    self.reference_store,
                    filename=item.manifest.image_path.name,
                    content=item.content,
                    content_type=item.content_type,
                    approved_sha256=item.manifest.image_sha256,
                    ttl_seconds=self.config.reference_url_ttl_seconds,
                    download_timeout_seconds=self.config.read_timeout_seconds,
                    maximum_reference_bytes=self.config.max_reference_bytes,
                    maximum_reference_megapixels=self.config.max_reference_megapixels,
                    accounting=self.accounting,
                )
                if not obj.roundtrip_verified:
                    raise BFLProviderError("reference_validation", "roundtrip was not verified")
                temporary.append(obj)

            payload: dict[str, Any] = {
                "prompt": prompt,
                "disable_pup": not self.config.prompt_upsampling_enabled,
                "seed": first.seed,
                "width": first.width,
                "height": first.height,
                "output_format": self.config.output_format,
            }
            for index, obj in enumerate(temporary, start=1):
                field_name = "input_image" if index == 1 else f"input_image_{index}"
                payload[field_name] = obj.read_url.get_secret_value()
            if "negative_prompt" in payload:
                raise AssertionError("native negative prompt is forbidden")

            self._inc("application_image_submissions")
            self._inc("bfl_create_transport_attempts")
            try:
                created = await asyncio.wait_for(
                    self.transport.create(
                        f"https://api.bfl.ai/v1/{self.config.endpoint}",
                        headers=self._headers(), payload=payload,
                        connect_timeout_seconds=self.config.connect_timeout_seconds,
                        read_timeout_seconds=self.config.read_timeout_seconds,
                        maximum_response_bytes=self.config.max_json_response_bytes,
                    ),
                    timeout=(
                        self.config.connect_timeout_seconds
                        + self.config.read_timeout_seconds
                    ),
                )
            except Exception as exc:
                if _http_status(exc) == 429:
                    self._inc("provider_generation_failures")
                    raise BFLProviderError("rate_limit", "BFL returned HTTP 429") from None
                self._inc("provider_generation_failures")
                raise BFLProviderError("create_failure", "BFL create request failed") from None
            request_id = created.get("id")
            polling_url = created.get("polling_url")
            if not isinstance(request_id, str) or not request_id:
                self._inc("provider_generation_failures")
                raise BFLProviderError("malformed_response", "create response lacks request ID")
            if not _safe_https_url(polling_url):
                self._inc("provider_generation_failures")
                raise BFLProviderError("malformed_response", "create response lacks polling URL")

            ready = await self._poll_until_ready(polling_url)
            result_url = (ready.get("result") or {}).get("sample")
            if not _safe_https_url(result_url):
                self._inc("provider_generation_failures")
                raise BFLProviderError("missing_result", "Ready response lacks result URL")
            self._inc("bfl_output_download_attempts")
            try:
                downloaded = await asyncio.wait_for(
                    self.transport.download(
                        result_url,
                        connect_timeout_seconds=self.config.connect_timeout_seconds,
                        read_timeout_seconds=self.config.read_timeout_seconds,
                        maximum_bytes=self.config.max_output_bytes,
                    ),
                    timeout=(
                        self.config.connect_timeout_seconds
                        + self.config.read_timeout_seconds
                    ),
                )
            except Exception:
                self._inc("provider_generation_failures")
                raise BFLProviderError("download_failure", "BFL result download failed") from None
            try:
                dimensions = await asyncio.to_thread(
                    self._validate_output, downloaded, first
                )
            except Exception as exc:
                self._inc("local_output_validation_failures")
                raise BFLProviderError("output_validation", str(exc)) from None
            try:
                await asyncio.to_thread(atomic_write_bytes, out_path, downloaded.content)
            except Exception:
                self._inc("local_output_write_failures")
                raise BFLProviderError(
                    "output_write", "atomic output write failed"
                ) from None
            self._inc("provider_generation_successes")
            metadata = {
                "provider_id": self.provider_name,
                "endpoint": self.config.endpoint,
                "reference_source_sha256": [item.manifest.image_sha256 for item in references],
                "reference_object_key_sha256": [
                    hashlib.sha256(obj.object_key.encode()).hexdigest() for obj in temporary
                ],
                "reference_count": len(references),
                "transport_provider": self.reference_store.capabilities().provider_id,
                "roundtrip_verified": all(obj.roundtrip_verified for obj in temporary),
                "character_identity_anchor_verified": bool(temporary),
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "dimensions": list(dimensions),
                "seed": first.seed,
                "output_format": self.config.output_format,
                "request_id": request_id,
                "accounting": dict(self.accounting),
            }
            result = ImageResult(
                output_path=out_path, provider=self.provider_name,
                prompt_used=prompt, seed=first.seed,
                used_reference_conditioning=True,
                reference_paths=[str(item.manifest.image_path) for item in references],
                metadata=metadata,
            )
            return result
        except asyncio.CancelledError:
            raise
        finally:
            for obj in temporary:
                if not obj.cleanup_owned:
                    continue
                self._inc("reference_cleanup_attempts")
                try:
                    deleted = await self.reference_store.delete(obj)
                except Exception:
                    deleted = False
                self._inc(
                    "reference_cleanup_successes" if deleted else "reference_cleanup_failures"
                )
                cleanup_required = cleanup_required or not deleted
            cleanup_required = cleanup_required or bool(
                self.accounting.get("reference_cleanup_failures", 0)
            )
            self.cleanup_diagnostic = {"cleanup_required": cleanup_required}
            validate_accounting_invariants(self.accounting)
            if result is not None:
                result.metadata["cleanup_required"] = cleanup_required
                result.metadata["accounting"] = dict(self.accounting)

    def _validate_reference(self, item: BFLReferenceInput) -> None:
        manifest = item.manifest
        request = item.request
        if not manifest.human_approved or not manifest.approval_record.strip():
            raise ValueError("reference sheet is not human-approved")
        approval_digest = hashlib.sha256(
            manifest.approval_record.encode("utf-8")
        ).hexdigest()
        if item.approval_record_sha256 != approval_digest:
            raise ValueError("reference approval-record SHA256 mismatch")
        if not manifest.anatomy_qc_passed or not manifest.style_qc_passed:
            raise ValueError("reference sheet QC is incomplete")
        if request.reference_sheet_version != manifest.version:
            raise ValueError("reference sheet version mismatch")
        if request.canonical_reference_image_path != manifest.image_path:
            raise ValueError("reference manifest path mismatch")
        if request.reference_image_sha256 != manifest.image_sha256:
            raise ValueError("reference manifest SHA256 mismatch")
        if request.character_fingerprint != manifest.character_fingerprint:
            raise ValueError("reference character fingerprint mismatch")
        if hashlib.sha256(item.content).hexdigest() != manifest.image_sha256:
            raise ValueError("reference bytes are tampered")
        if len(item.content) > self.config.max_reference_bytes:
            raise ValueError("reference exceeds local byte-size limit")
        if item.content_type not in self.config.local_reference_mime_types:
            raise ValueError("reference MIME is outside Tella local policy")

    async def _poll_until_ready(self, polling_url: str) -> dict[str, Any]:
        started = time.monotonic()
        for _ in range(self.config.maximum_polls):
            remaining = self.config.total_timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                self._inc("provider_generation_failures")
                raise BFLProviderError("timeout", "BFL total polling timeout exceeded")
            self._inc("bfl_poll_attempts")
            try:
                response = await asyncio.wait_for(
                    self.transport.poll(
                        polling_url, headers=self._headers(),
                        connect_timeout_seconds=self.config.connect_timeout_seconds,
                        read_timeout_seconds=self.config.read_timeout_seconds,
                        maximum_response_bytes=self.config.max_json_response_bytes,
                    ),
                    timeout=min(
                        remaining,
                        self.config.connect_timeout_seconds
                        + self.config.read_timeout_seconds,
                    ),
                )
            except asyncio.TimeoutError:
                self._inc("provider_generation_failures")
                raise BFLProviderError(
                    "timeout", "BFL total polling timeout exceeded"
                ) from None
            except Exception:
                self._inc("provider_generation_failures")
                raise BFLProviderError("poll_failure", "BFL poll request failed") from None
            status = response.get("status")
            if status == "Ready":
                return response
            if status == "Pending":
                remaining = self.config.total_timeout_seconds - (
                    time.monotonic() - started
                )
                if remaining <= 0:
                    self._inc("provider_generation_failures")
                    raise BFLProviderError(
                        "timeout", "BFL total polling timeout exceeded"
                    )
                await asyncio.sleep(min(self.config.polling_interval_seconds, remaining))
                continue
            if status in {"Request Moderated", "Content Moderated"}:
                self._inc("provider_generation_failures")
                raise BFLProviderError("moderation", f"BFL terminal status: {status}")
            if status in {"Task not found", "Error"}:
                self._inc("provider_generation_failures")
                raise BFLProviderError("provider_failure", f"BFL terminal status: {status}")
            self._inc("provider_generation_failures")
            raise BFLProviderError("malformed_response", "unknown BFL poll status")
        self._inc("provider_generation_failures")
        raise BFLProviderError("timeout", "BFL maximum poll count exceeded")

    def _validate_output(
        self, response: URLFetchResult, request: ReferenceConditionedImageRequest
    ) -> tuple[int, int]:
        if response.status_code != 200:
            raise ValueError("BFL output download returned a non-200 response")
        if len(response.content) > self.config.max_output_bytes:
            raise ValueError("BFL output exceeds byte-size limit")
        expected_mime = _OUTPUT_MIMES[self.config.output_format]
        actual_mime = response.content_type.split(";", 1)[0].strip().lower()
        if actual_mime != expected_mime:
            raise ValueError("BFL output MIME mismatch")
        try:
            with Image.open(io.BytesIO(response.content)) as image:
                image.load()
                actual_format = image.format
                dimensions = image.size
        except Exception as exc:
            raise ValueError("BFL output image decoding failed") from exc
        if actual_format != {"png": "PNG", "jpeg": "JPEG", "webp": "WEBP"}[
            self.config.output_format
        ]:
            raise ValueError("BFL output format mismatch")
        if dimensions != (request.width, request.height):
            raise ValueError("BFL output dimensions mismatch")
        return dimensions

    def _headers(self) -> dict[str, str]:
        if self.api_key is None:
            raise BFLProviderError("configuration", "BFL credential is missing")
        return {"accept": "application/json", "Content-Type": "application/json",
                "x-key": self.api_key.get_secret_value()}

    def _inc(self, key: str) -> None:
        self.accounting[key] = int(self.accounting.get(key, 0)) + 1


def translate_positive_prompt(request: ReferenceConditionedImageRequest) -> str:
    intent = " ".join(request.prompt.split())
    replacements = {
        r"\bno duplicate person\b": "exactly one visible character",
        r"\bno second character\b": "exactly one visible character",
        r"\bno generated text\b": "blank symbol-only surfaces",
        r"\bno second bag\b": "exactly one open bag",
    }
    for pattern, value in replacements.items():
        intent = re.sub(pattern, value, intent, flags=re.IGNORECASE)
    return " ".join([
        "Use the canonical character from input image 1 and preserve recognizable identity.",
        f"Action and pose: {request.scene_action}; {request.required_view_or_pose}.",
        f"Composition: {request.composition_family}.",
        f"Scene intent: {intent}.",
        f"Reference fidelity priority: {request.conditioning.strength:.2f} on a 0-to-1 scale.",
        "Keep anatomy coherent, props unambiguous, and exactly one visible character.",
    ])


def _safe_https_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return parsed.scheme == "https" and bool(parsed.hostname)


def _http_status(exc: Exception) -> int | None:
    return getattr(exc, "status_code", None)


def validate_accounting_invariants(counts: dict[str, int]) -> None:
    if any(not isinstance(value, int) or value < 0 for value in counts.values()):
        raise RuntimeError("BFL accounting contains an invalid counter")
    uploads = counts.get("temporary_store_upload_attempts", 0)
    if counts.get("temporary_store_presign_operations", 0) > uploads:
        raise RuntimeError("BFL accounting presign count exceeds uploads")
    if counts.get("temporary_store_verification_downloads", 0) > uploads:
        raise RuntimeError("BFL accounting verification count exceeds uploads")
    submissions = counts.get("application_image_submissions", 0)
    creates = counts.get("bfl_create_transport_attempts", 0)
    if creates != submissions:
        raise RuntimeError("BFL accounting create attempts do not match submissions")
    results = (
        counts.get("provider_generation_successes", 0)
        + counts.get("provider_generation_failures", 0)
    )
    if results > submissions:
        raise RuntimeError("BFL accounting provider results exceed submissions")
    cleanup_attempts = counts.get("reference_cleanup_attempts", 0)
    cleanup_results = (
        counts.get("reference_cleanup_successes", 0)
        + counts.get("reference_cleanup_failures", 0)
    )
    if cleanup_attempts != cleanup_results:
        raise RuntimeError("BFL accounting cleanup attempts and outcomes differ")


def build_bfl_reference_provider(
    *,
    config: BFLFlux2Config,
    api_key: SecretStr | None,
    reference_store_factory: Callable[[], TemporaryReferenceStore],
    transport_factory: Callable[[], BFLTransport],
    reference_manifest: ReferenceSheetManifest | None = None,
    accounting: dict[str, int] | None = None,
) -> BFLFlux2ReferenceProvider:
    """Construct injected clients only after the provider credential is present.

    This is the typed controlled-canary boundary.  Normal CLI configuration does
    not construct BFL or R2 clients yet.
    """
    if api_key is None or not api_key.get_secret_value():
        raise BFLProviderError("configuration", "BFL credential is missing")
    reference_store = reference_store_factory()
    transport = transport_factory()
    return BFLFlux2ReferenceProvider(
        config=config,
        reference_store=reference_store,
        transport=transport,
        api_key=api_key,
        reference_manifest=reference_manifest,
        accounting=accounting,
    )


__all__ = [
    "BFLFlux2Config", "BFLFlux2ReferenceProvider", "BFLProviderError",
    "BFLReferenceInput", "BFLTransport", "PINNED_ENDPOINT", "PREVIEW_ENDPOINT",
    "translate_positive_prompt",
    "validate_accounting_invariants",
    "build_bfl_reference_provider",
]
