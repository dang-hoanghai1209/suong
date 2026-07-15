"""Strict, injected-transport BFL FLUX 1.1 front-anchor adapter.

This adapter is deliberately separate from the reference-conditioned BFL
provider.  It has one create attempt per candidate, bounded polling, and one
result download.  Network transports are injected so validation and tests do
not construct clients or make calls.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from tella.atomic_write import atomic_write_bytes
from tella.media.image_provider import ImageProvider, ImageResult
from tella.media.image_provider_contract import ImageProviderCapabilities
from tella.media.temporary_reference_store import URLFetchResult


PROVIDER_ID = "bfl_flux_1_1_pro_front_anchor"
ENDPOINT_PATH = "/v1/flux-pro-1.1"
AUTHORIZATION_TOKEN = "AUTHORIZE_BFL_FRONT_ANCHOR_CANARY_01"
WIDTH = 768
HEIGHT = 1024
OUTPUT_FORMAT = "png"
MAX_OUTPUT_BYTES = 20_000_000


class BFLFrontAnchorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    endpoint_path: str = ENDPOINT_PATH
    width: int = Field(default=WIDTH, ge=1)
    height: int = Field(default=HEIGHT, ge=1)
    output_format: str = OUTPUT_FORMAT
    prompt_upsampling: bool = False
    maximum_polls: int = Field(default=60, ge=1, le=300)
    polling_interval_seconds: float = Field(default=1.0, gt=0, le=10)
    total_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    connect_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    read_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    max_json_response_bytes: int = Field(default=1_000_000, ge=1024, le=5_000_000)
    max_result_bytes: int = Field(default=MAX_OUTPUT_BYTES, ge=1024, le=MAX_OUTPUT_BYTES)

    @model_validator(mode="after")
    def fixed_contract(self) -> "BFLFrontAnchorConfig":
        if self.endpoint_path != ENDPOINT_PATH:
            raise ValueError("front-anchor endpoint is fixed")
        if (self.width, self.height) != (WIDTH, HEIGHT):
            raise ValueError("front-anchor dimensions are fixed at 768x1024")
        if self.output_format != OUTPUT_FORMAT:
            raise ValueError("front-anchor output format is fixed to PNG")
        if self.prompt_upsampling is not False:
            raise ValueError("front-anchor prompt upsampling must be disabled")
        return self


class BFLFrontAnchorTransport(Protocol):
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


class BFLFrontAnchorError(RuntimeError):
    def __init__(self, category: str, safe_message: str):
        self.category = category
        self.safe_message = safe_message
        super().__init__(f"{category}: {safe_message}")


@dataclass(frozen=True)
class BFLFrontAnchorRequest:
    prompt: str
    seed: int


class BFLFrontAnchorProvider(ImageProvider):
    """One-candidate BFL adapter with no retry or fallback behavior."""

    provider_name = PROVIDER_ID

    def __init__(
        self, *, config: BFLFrontAnchorConfig,
        transport: BFLFrontAnchorTransport, api_key: SecretStr,
        accounting: dict[str, int] | None = None,
    ) -> None:
        if not api_key.get_secret_value():
            raise BFLFrontAnchorError("configuration", "BFL credential is missing")
        self.config = config
        self.transport = transport
        self.api_key = api_key
        self.accounting = accounting if accounting is not None else {}

    def supports_reference_conditioning(self) -> bool:
        return False

    def supports_seed(self) -> bool:
        return True

    def capabilities(self) -> ImageProviderCapabilities:
        return ImageProviderCapabilities(
            provider_id=PROVIDER_ID,
            supports_text_to_image=True,
            supports_reference_conditioning=False,
            supports_image_to_image=False,
            supports_structural_conditioning=False,
            supports_seed=True,
            supports_negative_prompt=False,
            max_prompt_utf8_bytes=2000,
            max_reference_images=0,
            accepted_reference_mime_types=(),
            supports_character_identity_anchor=False,
            identity_anchor_verification="unsupported",
            provider_retry_control="caller_bounded",
        )

    def is_configured(self) -> bool:
        return bool(self.api_key.get_secret_value())

    async def generate_text_image(
        self, prompt: str, negative_prompt: str, aspect: str, seed: int | None,
        out_path: Path, metadata: dict[str, Any] | None = None,
    ) -> ImageResult:
        if negative_prompt:
            raise BFLFrontAnchorError("configuration", "native negative prompts are unsupported")
        if seed is None:
            raise BFLFrontAnchorError("configuration", "an explicit seed is required")
        return await self.generate(BFLFrontAnchorRequest(prompt=prompt, seed=seed), out_path)

    async def generate(
        self, request: BFLFrontAnchorRequest, out_path: Path,
    ) -> ImageResult:
        self._inc("application_image_submissions")
        self._inc("bfl_create_attempts")
        payload = {
            "prompt": request.prompt,
            "width": WIDTH,
            "height": HEIGHT,
            "output_format": OUTPUT_FORMAT,
            "prompt_upsampling": False,
            "seed": request.seed,
        }
        try:
            created = await asyncio.wait_for(
                self.transport.create(
                    "https://api.bfl.ai" + ENDPOINT_PATH,
                    headers=self._headers(), payload=payload,
                    connect_timeout_seconds=self.config.connect_timeout_seconds,
                    read_timeout_seconds=self.config.read_timeout_seconds,
                    maximum_response_bytes=self.config.max_json_response_bytes,
                ),
                timeout=self.config.connect_timeout_seconds + self.config.read_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self._fail("timeout", None)
        except Exception as exc:
            self._fail("create_failure", exc)
        request_id = created.get("id")
        polling_url = created.get("polling_url") or created.get("pollingUrl")
        if not isinstance(request_id, str) or not request_id or not _safe_https_url(polling_url):
            self._fail("malformed_response", None)
        ready = await self._poll(polling_url)
        result_url = (ready.get("result") or {}).get("sample")
        if not _safe_https_url(result_url):
            self._fail("missing_result", None)
        self._inc("bfl_result_download_attempts")
        try:
            downloaded = await asyncio.wait_for(
                self.transport.download(
                    result_url,
                    connect_timeout_seconds=self.config.connect_timeout_seconds,
                    read_timeout_seconds=self.config.read_timeout_seconds,
                    maximum_bytes=self.config.max_result_bytes,
                ),
                timeout=self.config.connect_timeout_seconds + self.config.read_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self._fail("timeout", None)
        except Exception:
            self._fail("download_failure", None)
        try:
            self._validate_png(downloaded)
            atomic_write_bytes(out_path, downloaded.content)
        except BFLFrontAnchorError:
            raise
        except Exception:
            self._fail("output_validation", None)
        self._inc("successful_candidates")
        return ImageResult(
            output_path=out_path, provider=PROVIDER_ID,
            prompt_used=request.prompt, seed=request.seed,
            used_reference_conditioning=False, reference_paths=[],
            metadata={
                "provider_id": PROVIDER_ID,
                "endpoint_path": ENDPOINT_PATH,
                "request_id": request_id,
                "seed": request.seed,
                "prompt_sha256": hashlib.sha256(request.prompt.encode()).hexdigest(),
                "dimensions": [WIDTH, HEIGHT],
                "mime": "image/png",
                "byte_size": len(downloaded.content),
                "accounting": dict(self.accounting),
            },
        )

    async def _poll(self, polling_url: str) -> dict[str, Any]:
        started = time.monotonic()
        for _ in range(self.config.maximum_polls):
            if time.monotonic() - started >= self.config.total_timeout_seconds:
                self._fail("timeout", None)
            self._inc("bfl_poll_attempts")
            try:
                response = await asyncio.wait_for(
                    self.transport.poll(
                        polling_url, headers=self._headers(),
                        connect_timeout_seconds=self.config.connect_timeout_seconds,
                        read_timeout_seconds=self.config.read_timeout_seconds,
                        maximum_response_bytes=self.config.max_json_response_bytes,
                    ),
                    timeout=self.config.connect_timeout_seconds + self.config.read_timeout_seconds,
                )
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                self._fail("timeout", None)
            except Exception:
                self._fail("poll_failure", None)
            status = response.get("status")
            if status == "Ready":
                return response
            if status == "Pending":
                await asyncio.sleep(self.config.polling_interval_seconds)
                continue
            if status in {"Request Moderated", "Content Moderated"}:
                self._fail("moderation", None)
            if status in {"Error", "Task not found"}:
                self._fail("provider_failure", None)
            self._fail("malformed_response", None)
        self._fail("timeout", None)

    def _validate_png(self, response: URLFetchResult) -> None:
        if response.status_code != 200 or response.content_type.split(";", 1)[0].strip().lower() != "image/png":
            self._fail("output_validation", None)
        if len(response.content) > self.config.max_result_bytes:
            self._fail("output_validation", None)
        if not response.content.startswith(b"\x89PNG\r\n\x1a\n"):
            self._fail("output_validation", None)
        try:
            with Image.open(io.BytesIO(response.content)) as image:
                image.load()
                if image.format != "PNG" or image.size != (WIDTH, HEIGHT) or getattr(image, "n_frames", 1) != 1:
                    self._fail("output_validation", None)
        except BFLFrontAnchorError:
            raise
        except Exception:
            self._fail("output_validation", None)

    def _headers(self) -> dict[str, str]:
        return {"accept": "application/json", "Content-Type": "application/json", "x-key": self.api_key.get_secret_value()}

    def _inc(self, key: str) -> None:
        self.accounting[key] = int(self.accounting.get(key, 0)) + 1

    def _fail(self, category: str, _exc: Exception | None) -> None:
        self._inc("failed_candidates")
        raise BFLFrontAnchorError(category, "BFL front-anchor operation failed")


def _safe_https_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return parsed.scheme == "https" and bool(parsed.hostname)


__all__ = [
    "AUTHORIZATION_TOKEN", "BFLFrontAnchorConfig", "BFLFrontAnchorError",
    "BFLFrontAnchorProvider", "BFLFrontAnchorRequest", "BFLFrontAnchorTransport",
    "ENDPOINT_PATH", "PROVIDER_ID", "WIDTH", "HEIGHT", "OUTPUT_FORMAT",
]
