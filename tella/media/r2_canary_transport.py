"""Concrete, live-only transport boundaries for the R2 reference canary.

The optional S3 SDK is imported only when the explicitly gated live canary
constructs a client.  Importing this module does not read credentials, build a
client, or perform network activity.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

from tella.media.r2_reference_store import R2ReferenceStoreConfig, S3CompatibleClient
from tella.media.temporary_reference_store import URLFetchResult


R2_SDK_CONNECT_TIMEOUT_SECONDS = 5.0
R2_SDK_READ_TIMEOUT_SECONDS = 15.0
R2_HTTPS_CONNECT_TIMEOUT_SECONDS = 5.0
R2_HTTPS_READ_TIMEOUT_SECONDS = 15.0
R2_HTTPS_MAXIMUM_RESPONSE_BYTES = 1_000_000
R2_HTTPS_MAXIMUM_REDIRECTS = 0

_SAFE_MIME = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")


class R2ClientFactoryError(RuntimeError):
    """Redacted client-construction failure safe for persistent diagnostics."""

    def __init__(self, category: str) -> None:
        self.safe_category = category
        super().__init__(f"R2 client construction failed: {category}")


class BoundedHTTPSFetchError(RuntimeError):
    """Redacted fetch failure that never retains the requested URL."""

    def __init__(
        self, category: str, *, host_sha256: str = "", status_code: int | None = None
    ) -> None:
        self.safe_category = category
        self.host_sha256 = host_sha256
        self.status_code = status_code
        suffix = f" ({status_code})" if status_code is not None else ""
        super().__init__(f"R2 verification fetch failed: {category}{suffix}")

    def diagnostic(self) -> dict[str, Any]:
        return {
            "operation_category": self.safe_category,
            "status_code": self.status_code,
            "url_scheme": "https",
            "url_host_sha256": self.host_sha256,
        }


def r2_credential_presence() -> dict[str, bool]:
    """Return presence booleans for only the four R2 runtime variables."""
    import os

    return {
        name: bool(os.environ.get(name))
        for name in (
            "R2_ACCOUNT_ID",
            "R2_ACCESS_KEY_ID",
            "R2_SECRET_ACCESS_KEY",
            "R2_BUCKET_NAME",
        )
    }


def _load_boto3() -> tuple[Any, Any]:
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise R2ClientFactoryError("optional_s3_sdk_unavailable") from None
    return boto3, Config


def create_r2_s3_client(
    config: R2ReferenceStoreConfig,
    *,
    connect_timeout_seconds: float = R2_SDK_CONNECT_TIMEOUT_SECONDS,
    read_timeout_seconds: float = R2_SDK_READ_TIMEOUT_SECONDS,
) -> S3CompatibleClient:
    """Construct one retry-disabled boto3 S3 client for the configured R2 account."""
    if connect_timeout_seconds <= 0 or read_timeout_seconds <= 0:
        raise R2ClientFactoryError("invalid_timeout_configuration")
    boto3, sdk_config_type = _load_boto3()
    client: S3CompatibleClient | None = None
    try:
        sdk_config = sdk_config_type(
            connect_timeout=connect_timeout_seconds,
            read_timeout=read_timeout_seconds,
            retries={"mode": "standard", "total_max_attempts": 1},
            signature_version="s3v4",
        )
        client = boto3.client(
            "s3",
            endpoint_url=config.endpoint_url,
            aws_access_key_id=config.access_key_id.get_secret_value(),
            aws_secret_access_key=config.secret_access_key.get_secret_value(),
            region_name="auto",
            config=sdk_config,
        )
    except Exception:
        pass
    if client is None:
        raise R2ClientFactoryError("s3_client_initialization_failed")
    return client


class BoundedR2HTTPSFetcher:
    """No-redirect, HTTPS-only streaming fetcher with hard timeout and byte limits."""

    def __init__(
        self,
        *,
        connect_timeout_seconds: float = R2_HTTPS_CONNECT_TIMEOUT_SECONDS,
        read_timeout_seconds: float = R2_HTTPS_READ_TIMEOUT_SECONDS,
        maximum_response_bytes: int = R2_HTTPS_MAXIMUM_RESPONSE_BYTES,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if connect_timeout_seconds <= 0 or read_timeout_seconds <= 0:
            raise ValueError("R2 HTTPS timeouts must be positive")
        if maximum_response_bytes <= 0:
            raise ValueError("R2 HTTPS response limit must be positive")
        self._connect_timeout_seconds = float(connect_timeout_seconds)
        self._read_timeout_seconds = float(read_timeout_seconds)
        self._maximum_response_bytes = int(maximum_response_bytes)
        self._transport = transport

    def __repr__(self) -> str:
        return (
            "BoundedR2HTTPSFetcher(redirects=0, "
            f"maximum_response_bytes={self._maximum_response_bytes})"
        )

    async def get(
        self, url: str, *, timeout_seconds: float, maximum_bytes: int
    ) -> URLFetchResult:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        host_sha256 = hashlib.sha256(host.encode("utf-8")).hexdigest() if host else ""
        if parsed.scheme.lower() != "https" or not host:
            raise BoundedHTTPSFetchError("https_required", host_sha256=host_sha256)
        if parsed.username is not None or parsed.password is not None:
            raise BoundedHTTPSFetchError("url_userinfo_forbidden", host_sha256=host_sha256)
        if parsed.fragment:
            raise BoundedHTTPSFetchError("url_fragment_forbidden", host_sha256=host_sha256)
        if timeout_seconds <= 0 or maximum_bytes <= 0:
            raise BoundedHTTPSFetchError("invalid_fetch_limits", host_sha256=host_sha256)

        body_limit = min(maximum_bytes, self._maximum_response_bytes)
        request_timeout = httpx.Timeout(
            connect=min(self._connect_timeout_seconds, timeout_seconds),
            read=min(self._read_timeout_seconds, timeout_seconds),
            write=min(self._read_timeout_seconds, timeout_seconds),
            pool=min(self._connect_timeout_seconds, timeout_seconds),
        )
        failure_category: str | None = None
        try:
            async with httpx.AsyncClient(
                timeout=request_timeout,
                follow_redirects=False,
                max_redirects=R2_HTTPS_MAXIMUM_REDIRECTS,
                transport=self._transport,
                trust_env=False,
            ) as client:
                async with client.stream("GET", url) as response:
                    if 300 <= response.status_code < 400:
                        raise BoundedHTTPSFetchError(
                            "redirect_forbidden",
                            host_sha256=host_sha256,
                            status_code=response.status_code,
                        )
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > body_limit:
                            raise BoundedHTTPSFetchError(
                                "response_body_too_large",
                                host_sha256=host_sha256,
                                status_code=response.status_code,
                            )
                    return URLFetchResult(
                        status_code=response.status_code,
                        content=bytes(content),
                        content_type=_safe_content_type(response.headers.get("content-type")),
                    )
        except asyncio.CancelledError:
            raise
        except BoundedHTTPSFetchError:
            raise
        except httpx.ConnectTimeout:
            failure_category = "connect_timeout"
        except httpx.ReadTimeout:
            failure_category = "read_timeout"
        except httpx.TimeoutException:
            failure_category = "transport_timeout"
        except Exception:
            failure_category = "transport_failure"
        raise BoundedHTTPSFetchError(
            failure_category or "transport_failure", host_sha256=host_sha256
        )


def _safe_content_type(value: str | None) -> str:
    media_type = str(value or "").split(";", 1)[0].strip().lower()
    return media_type if len(media_type) <= 127 and _SAFE_MIME.fullmatch(media_type) else ""


__all__ = [
    "BoundedHTTPSFetchError",
    "BoundedR2HTTPSFetcher",
    "R2ClientFactoryError",
    "R2_HTTPS_CONNECT_TIMEOUT_SECONDS",
    "R2_HTTPS_MAXIMUM_REDIRECTS",
    "R2_HTTPS_MAXIMUM_RESPONSE_BYTES",
    "R2_HTTPS_READ_TIMEOUT_SECONDS",
    "R2_SDK_CONNECT_TIMEOUT_SECONDS",
    "R2_SDK_READ_TIMEOUT_SECONDS",
    "create_r2_s3_client",
    "r2_credential_presence",
]
