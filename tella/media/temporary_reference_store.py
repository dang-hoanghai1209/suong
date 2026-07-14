"""Private, temporary transport for approved reference-image bytes."""
from __future__ import annotations

import asyncio
import hashlib
import io
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Protocol
from urllib.parse import urlsplit

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, SecretStr


class DeletionStatus(StrEnum):
    pending = "pending"
    deleted = "deleted"
    failed = "failed"


class TemporaryReferenceStoreCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_id: str
    private_objects_only: bool
    supports_presigned_get: bool
    supports_retrieve: bool
    supports_delete: bool
    supports_exists: bool
    preserves_exact_bytes: bool
    minimum_ttl_seconds: int = Field(ge=1)
    maximum_ttl_seconds: int = Field(ge=1)


class URLFetchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status_code: int
    content: bytes
    content_type: str = ""


class TemporaryReferenceUploadError(RuntimeError):
    """Safe upload failure carrying only cleanup outcome, never a signed URL."""

    def __init__(
        self, safe_message: str, *, cleanup_attempted: bool, cleanup_succeeded: bool
    ) -> None:
        self.cleanup_attempted = cleanup_attempted
        self.cleanup_succeeded = cleanup_succeeded
        super().__init__(safe_message)


class ReferenceURLFetcher(Protocol):
    async def get(
        self, url: str, *, timeout_seconds: float, maximum_bytes: int
    ) -> URLFetchResult: ...


@dataclass(frozen=True)
class TemporaryReferenceObject:
    store_provider_id: str
    storage_namespace: str
    object_key: str
    source_sha256: str
    stored_byte_size: int
    content_type: str
    created_at: datetime
    expires_at: datetime
    read_url: SecretStr
    roundtrip_sha256: str = ""
    roundtrip_verified: bool = False
    deletion_status: DeletionStatus = DeletionStatus.pending
    cleanup_owned: bool = True

    def diagnostic(self) -> dict[str, Any]:
        parsed = urlsplit(self.read_url.get_secret_value())
        return {
            "store_provider_id": self.store_provider_id,
            "storage_namespace": self.storage_namespace,
            "object_key_sha256": hashlib.sha256(self.object_key.encode()).hexdigest(),
            "source_sha256": self.source_sha256,
            "stored_byte_size": self.stored_byte_size,
            "content_type": self.content_type,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "read_url": {"scheme": parsed.scheme, "host": parsed.hostname or ""},
            "roundtrip_sha256": self.roundtrip_sha256,
            "roundtrip_verified": self.roundtrip_verified,
            "deletion_status": self.deletion_status.value,
            "cleanup_owned": self.cleanup_owned,
        }


class TemporaryReferenceStore(Protocol):
    def capabilities(self) -> TemporaryReferenceStoreCapabilities: ...

    async def upload_immutable(
        self, *, object_key: str, content: bytes, content_type: str,
        source_sha256: str, ttl_seconds: int,
    ) -> TemporaryReferenceObject: ...

    async def retrieve_via_read_url(
        self, obj: TemporaryReferenceObject, *, timeout_seconds: float,
        maximum_bytes: int,
    ) -> URLFetchResult: ...

    async def exists(self, object_key: str) -> bool: ...

    async def delete(self, obj: TemporaryReferenceObject) -> bool: ...

    async def cleanup_stale(self, *, prefix: str, older_than: datetime) -> list[str]: ...


def sanitize_filename(filename: str) -> str:
    name = PurePosixPath(filename.replace("\\", "/")).name
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)
    safe = safe.strip("._") or "reference.bin"
    return safe[:160]


def content_addressed_key(source_sha256: str, filename: str) -> str:
    digest = source_sha256.strip().lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ValueError("source SHA256 must be a full lowercase hex digest")
    return f"reference-sheets/{digest}/{sanitize_filename(filename)}"


def validate_image_bytes(
    content: bytes, *, content_type: str, expected_size: int,
    expected_dimensions: tuple[int, int] | None = None,
    maximum_megapixels: float | None = None,
) -> tuple[int, int]:
    if len(content) != expected_size:
        raise ValueError("temporary reference byte-size mismatch")
    expected_format = {
        "image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"
    }.get(content_type)
    if expected_format is None:
        raise ValueError("temporary reference MIME is not allowed")
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.load()
            actual_format = image.format
            dimensions = image.size
    except Exception as exc:
        raise ValueError("temporary reference image decoding failed") from exc
    if actual_format != expected_format:
        raise ValueError("temporary reference MIME does not match decoded image")
    if expected_dimensions is not None and dimensions != expected_dimensions:
        raise ValueError("temporary reference dimensions mismatch")
    if maximum_megapixels is not None:
        if dimensions[0] * dimensions[1] > int(maximum_megapixels * 1_000_000):
            raise ValueError("temporary reference exceeds megapixel limit")
    return dimensions


async def upload_and_verify_reference(
    store: TemporaryReferenceStore,
    *,
    filename: str,
    content: bytes,
    content_type: str,
    approved_sha256: str,
    ttl_seconds: int,
    download_timeout_seconds: float,
    expected_dimensions: tuple[int, int] | None = None,
    maximum_reference_bytes: int = 20_000_000,
    maximum_reference_megapixels: float = 20.0,
    accounting: dict[str, int] | None = None,
) -> TemporaryReferenceObject:
    """Upload and roundtrip exact bytes; delete on any verification failure."""
    counts = accounting if accounting is not None else {}
    source_sha256 = hashlib.sha256(content).hexdigest()
    if source_sha256 != approved_sha256:
        raise ValueError("approved reference SHA256 mismatch")
    if len(content) > maximum_reference_bytes:
        raise ValueError("approved reference exceeds local byte-size limit")
    key = content_addressed_key(source_sha256, filename)
    counts["temporary_store_upload_attempts"] = counts.get(
        "temporary_store_upload_attempts", 0
    ) + 1
    try:
        obj = await store.upload_immutable(
            object_key=key, content=content, content_type=content_type,
            source_sha256=source_sha256, ttl_seconds=ttl_seconds,
        )
    except TemporaryReferenceUploadError as exc:
        if exc.cleanup_attempted:
            counts["reference_cleanup_attempts"] = counts.get(
                "reference_cleanup_attempts", 0
            ) + 1
            outcome = (
                "reference_cleanup_successes"
                if exc.cleanup_succeeded else "reference_cleanup_failures"
            )
            counts[outcome] = counts.get(outcome, 0) + 1
        raise
    counts["temporary_store_presign_operations"] = counts.get(
        "temporary_store_presign_operations", 0
    ) + 1
    try:
        counts["temporary_store_verification_downloads"] = counts.get(
            "temporary_store_verification_downloads", 0
        ) + 1
        try:
            response = await asyncio.wait_for(
                store.retrieve_via_read_url(
                    obj, timeout_seconds=download_timeout_seconds,
                    maximum_bytes=maximum_reference_bytes,
                ),
                timeout=download_timeout_seconds,
            )
        except Exception:
            raise RuntimeError(
                "temporary reference verification download failed"
            ) from None
        if response.status_code != 200:
            raise RuntimeError("temporary reference verification download failed")
        response_type = response.content_type.split(";", 1)[0].strip().lower()
        if response_type != content_type:
            raise ValueError("temporary reference verification MIME mismatch")
        if len(response.content) > maximum_reference_bytes:
            raise ValueError("temporary reference verification exceeds byte-size limit")
        roundtrip_sha256 = hashlib.sha256(response.content).hexdigest()
        if roundtrip_sha256 != source_sha256:
            raise ValueError("temporary reference roundtrip SHA256 mismatch")
        await asyncio.to_thread(
            validate_image_bytes,
            response.content,
            content_type=content_type,
            expected_size=len(content),
            expected_dimensions=expected_dimensions,
            maximum_megapixels=maximum_reference_megapixels,
        )
        return replace(
            obj, roundtrip_sha256=roundtrip_sha256, roundtrip_verified=True
        )
    except BaseException:
        if obj.cleanup_owned:
            counts["reference_cleanup_attempts"] = counts.get(
                "reference_cleanup_attempts", 0
            ) + 1
            try:
                deleted = await store.delete(obj)
            except Exception:
                deleted = False
            key_name = (
                "reference_cleanup_successes" if deleted
                else "reference_cleanup_failures"
            )
            counts[key_name] = counts.get(key_name, 0) + 1
        raise


def expires_at_from_ttl(ttl_seconds: int) -> tuple[datetime, datetime]:
    created = datetime.now(UTC)
    return created, created + timedelta(seconds=ttl_seconds)


__all__ = [
    "DeletionStatus", "ReferenceURLFetcher", "TemporaryReferenceObject",
    "TemporaryReferenceUploadError",
    "TemporaryReferenceStore", "TemporaryReferenceStoreCapabilities", "URLFetchResult",
    "content_addressed_key", "expires_at_from_ttl", "sanitize_filename",
    "upload_and_verify_reference", "validate_image_bytes",
]
