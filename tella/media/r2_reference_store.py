"""Cloudflare R2 implementation of the private temporary-reference store."""
from __future__ import annotations

import asyncio
import os
import secrets
from datetime import UTC, datetime
from typing import Any, Callable, Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from tella.media.temporary_reference_store import (
    DeletionStatus,
    ReferenceURLFetcher,
    TemporaryReferenceObject,
    TemporaryReferenceUploadError,
    TemporaryReferenceStoreCapabilities,
    URLFetchResult,
    expires_at_from_ttl,
)


class S3CompatibleClient(Protocol):
    def head_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def put_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def delete_object(self, **kwargs: Any) -> dict[str, Any]: ...
    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]: ...
    def generate_presigned_url(self, *args: Any, **kwargs: Any) -> str: ...


class R2ReferenceStoreConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: SecretStr = Field(exclude=True, repr=False)
    access_key_id: SecretStr = Field(exclude=True, repr=False)
    secret_access_key: SecretStr = Field(exclude=True, repr=False)
    bucket_name: str = Field(min_length=1, max_length=255)
    upload_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    delete_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    private_bucket: bool = True
    private_bucket_status_confirmed: bool = False
    conditional_write_support_confirmed: bool = False
    stale_cleanup_max_objects: int = Field(default=1000, ge=1, le=10_000)

    @model_validator(mode="after")
    def private_only(self) -> "R2ReferenceStoreConfig":
        if not self.private_bucket:
            raise ValueError("R2 temporary reference bucket must be private")
        return self

    @property
    def endpoint_url(self) -> str:
        return (
            "https://" + self.account_id.get_secret_value()
            + ".r2.cloudflarestorage.com"
        )

    @classmethod
    def from_environment(cls) -> "R2ReferenceStoreConfig":
        names = (
            "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"
        )
        missing = [name for name in names if not os.environ.get(name)]
        if missing:
            raise RuntimeError("R2 temporary reference credentials are incomplete")
        return cls(
            account_id=SecretStr(os.environ["R2_ACCOUNT_ID"]),
            access_key_id=SecretStr(os.environ["R2_ACCESS_KEY_ID"]),
            secret_access_key=SecretStr(os.environ["R2_SECRET_ACCESS_KEY"]),
            bucket_name=os.environ["R2_BUCKET_NAME"],
        )


class R2TemporaryReferenceStore:
    provider_id = "cloudflare_r2_private"

    def __init__(
        self, config: R2ReferenceStoreConfig, *, client: S3CompatibleClient,
        url_fetcher: ReferenceURLFetcher,
    ) -> None:
        self.config = config
        self._client = client
        self._url_fetcher = url_fetcher

    @classmethod
    def from_environment(
        cls, *, client_factory: Callable[[R2ReferenceStoreConfig], S3CompatibleClient],
        url_fetcher: ReferenceURLFetcher,
    ) -> "R2TemporaryReferenceStore":
        config = R2ReferenceStoreConfig.from_environment()
        return cls(config, client=client_factory(config), url_fetcher=url_fetcher)

    def capabilities(self) -> TemporaryReferenceStoreCapabilities:
        return TemporaryReferenceStoreCapabilities(
            provider_id=self.provider_id, private_objects_only=True,
            supports_presigned_get=True, supports_retrieve=True,
            supports_delete=True, supports_exists=True, preserves_exact_bytes=True,
            minimum_ttl_seconds=60, maximum_ttl_seconds=3600,
        )

    async def exists(self, object_key: str) -> bool:
        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    self._client.head_object,
                    Bucket=self.config.bucket_name, Key=object_key,
                ),
                timeout=self.config.upload_timeout_seconds,
            )
            return True
        except Exception as exc:
            status = _status_code(exc)
            if status in {404, 403}:  # 403 is intentionally not treated as absent.
                if status == 404:
                    return False
            raise RuntimeError("R2 object existence check failed") from None

    async def upload_immutable(
        self, *, object_key: str, content: bytes, content_type: str,
        source_sha256: str, ttl_seconds: int,
    ) -> TemporaryReferenceObject:
        if not self.config.private_bucket:
            raise RuntimeError("public R2 buckets are forbidden")
        if not self.config.private_bucket_status_confirmed:
            raise RuntimeError("R2 private-bucket status has not been confirmed")
        if not self.config.conditional_write_support_confirmed:
            raise RuntimeError("R2 conditional-write support requires a separate canary")
        caps = self.capabilities()
        if not caps.minimum_ttl_seconds <= ttl_seconds <= caps.maximum_ttl_seconds:
            raise ValueError("R2 presigned GET expiry is outside the allowed range")
        created_new = not await self.exists(object_key)
        if not created_new:
            try:
                existing = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._client.get_object,
                        Bucket=self.config.bucket_name, Key=object_key,
                    ),
                    timeout=self.config.upload_timeout_seconds,
                )
            except Exception:
                raise RuntimeError("R2 immutable object verification failed") from None
            reported_size = existing.get("ContentLength")
            if isinstance(reported_size, int) and reported_size != len(content):
                raise RuntimeError("conflicting immutable R2 reference object")
            body = await asyncio.wait_for(
                asyncio.to_thread(
                    _read_body, existing.get("Body"), maximum_bytes=len(content)
                ),
                timeout=self.config.upload_timeout_seconds,
            )
            existing_type = str(existing.get("ContentType") or "").lower()
            if body != content or existing_type != content_type:
                raise RuntimeError("conflicting immutable R2 reference object")
        else:
            upload_owner_token = secrets.token_hex(16)
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(
                        self._client.put_object,
                        Bucket=self.config.bucket_name,
                        Key=object_key,
                        Body=content,
                        ContentType=content_type,
                        Metadata={
                            "source-sha256": source_sha256,
                            "tella-upload-owner": upload_owner_token,
                        },
                        IfNoneMatch="*",
                    ),
                    timeout=self.config.upload_timeout_seconds,
                )
            except Exception as exc:
                if _status_code(exc) in {409, 412}:
                    raise RuntimeError(
                        "R2 immutable conditional write was rejected"
                    ) from None
                cleanup_succeeded = await self._reconcile_uncertain_upload(
                    object_key, upload_owner_token
                )
                raise TemporaryReferenceUploadError(
                    "R2 immutable upload failed",
                    cleanup_attempted=True,
                    cleanup_succeeded=cleanup_succeeded,
                ) from None
        try:
            read_url = self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.config.bucket_name, "Key": object_key},
                ExpiresIn=ttl_seconds,
            )
            if url_scheme(read_url) != "https":
                raise RuntimeError("R2 presigned read URL must use HTTPS")
        except Exception:
            cleanup_succeeded = False
            if created_new:
                cleanup_succeeded = await self._delete_object_key(object_key)
            raise TemporaryReferenceUploadError(
                "R2 presigned read URL creation failed",
                cleanup_attempted=created_new,
                cleanup_succeeded=cleanup_succeeded,
            ) from None
        created, expires = expires_at_from_ttl(ttl_seconds)
        return TemporaryReferenceObject(
            store_provider_id=self.provider_id,
            storage_namespace=self.config.bucket_name,
            object_key=object_key,
            source_sha256=source_sha256,
            stored_byte_size=len(content),
            content_type=content_type,
            created_at=created,
            expires_at=expires,
            read_url=SecretStr(read_url),
            cleanup_owned=created_new,
        )

    async def retrieve_via_read_url(
        self, obj: TemporaryReferenceObject, *, timeout_seconds: float,
        maximum_bytes: int,
    ) -> URLFetchResult:
        return await self._url_fetcher.get(
            obj.read_url.get_secret_value(), timeout_seconds=timeout_seconds,
            maximum_bytes=maximum_bytes,
        )

    async def delete(self, obj: TemporaryReferenceObject) -> bool:
        if not obj.cleanup_owned:
            return True
        return await self._delete_object_key(obj.object_key)

    async def _delete_object_key(self, object_key: str) -> bool:
        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    self._client.delete_object,
                    Bucket=self.config.bucket_name, Key=object_key,
                ),
                timeout=self.config.delete_timeout_seconds,
            )
            return True
        except Exception:
            return False

    async def _reconcile_uncertain_upload(
        self, object_key: str, upload_owner_token: str
    ) -> bool:
        """Delete only an uncertain write proven to belong to this invocation."""
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    self._client.head_object,
                    Bucket=self.config.bucket_name,
                    Key=object_key,
                ),
                timeout=self.config.delete_timeout_seconds,
            )
        except Exception as exc:
            return _status_code(exc) == 404
        metadata = response.get("Metadata")
        if not isinstance(metadata, dict):
            return False
        if metadata.get("tella-upload-owner") != upload_owner_token:
            return False
        return await self._delete_object_key(object_key)

    async def cleanup_stale(self, *, prefix: str, older_than: datetime) -> list[str]:
        if not prefix.startswith("reference-sheets/"):
            raise ValueError("stale cleanup requires the safe reference-sheets/ prefix")
        if older_than.tzinfo is None:
            raise ValueError("stale cleanup cutoff must be timezone-aware")
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    self._client.list_objects_v2,
                    Bucket=self.config.bucket_name, Prefix=prefix,
                    MaxKeys=self.config.stale_cleanup_max_objects,
                ),
                timeout=self.config.delete_timeout_seconds,
            )
        except Exception:
            raise RuntimeError("R2 stale-object listing failed") from None
        deleted: list[str] = []
        for item in response.get("Contents", []):
            key = str(item.get("Key") or "")
            modified = item.get("LastModified")
            if not key.startswith(prefix) or not isinstance(modified, datetime):
                continue
            if modified.astimezone(UTC) < older_than.astimezone(UTC):
                probe = TemporaryReferenceObject(
                    store_provider_id=self.provider_id,
                    storage_namespace=self.config.bucket_name,
                    object_key=key, source_sha256="", stored_byte_size=0,
                    content_type="", created_at=modified, expires_at=modified,
                    read_url=SecretStr("https://redacted.invalid/"),
                    deletion_status=DeletionStatus.pending,
                )
                if await self.delete(probe):
                    deleted.append(key)
        return deleted


def _read_body(body: Any, *, maximum_bytes: int) -> bytes:
    if isinstance(body, bytes):
        content = body[: maximum_bytes + 1]
        if len(content) > maximum_bytes:
            raise RuntimeError("R2 object body exceeds expected size")
        return content
    if hasattr(body, "read"):
        content = bytes(body.read(maximum_bytes + 1))
        if len(content) > maximum_bytes:
            raise RuntimeError("R2 object body exceeds expected size")
        return content
    raise RuntimeError("R2 returned an unreadable object body")


def _status_code(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        metadata = response.get("ResponseMetadata", {})
        return metadata.get("HTTPStatusCode")
    return getattr(exc, "status_code", None)


def url_scheme(url: str) -> str:
    from urllib.parse import urlsplit
    return urlsplit(url).scheme.lower()


__all__ = ["R2ReferenceStoreConfig", "R2TemporaryReferenceStore", "S3CompatibleClient"]
