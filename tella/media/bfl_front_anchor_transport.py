"""Concrete, strictly bounded HTTP transport for the direct BFL front canary.

The transport is intentionally not imported by validate-only code.  A caller
must construct it only after authorization, clean-worktree, capability, and
credential gates have passed.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import re
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from tella.media.bfl_front_anchor_provider import ENDPOINT_PATH
from tella.media.temporary_reference_store import URLFetchResult


BASE_URL = "https://api.bfl.ai"
POLL_HOSTS = frozenset({"api.bfl.ai", "api.eu.bfl.ai", "api.us.bfl.ai"})
MAX_JSON_RESPONSE_BYTES = 1_000_000
MAX_RESULT_BYTES = 20_000_000


class BFLFrontAnchorTransportError(RuntimeError):
    def __init__(self, category: str, safe_message: str):
        self.category = category
        self.safe_message = safe_message
        super().__init__(f"{category}: {safe_message}")


def _https_locator(value: Any, *, policy: str) -> str:
    if not isinstance(value, str):
        raise BFLFrontAnchorTransportError("invalid_locator", "provider locator is invalid")
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        raise BFLFrontAnchorTransportError("invalid_locator", "provider locator is invalid") from None
    if (
        parsed.scheme != "https" or not host or parsed.username or parsed.password
        or parsed.fragment or port not in (None, 443)
    ):
        raise BFLFrontAnchorTransportError("invalid_locator", "provider locator is invalid")
    try:
        if ipaddress.ip_address(host):
            raise BFLFrontAnchorTransportError("invalid_locator", "provider locator is invalid")
    except ValueError:
        pass
    if policy == "poll" and host not in POLL_HOSTS:
        raise BFLFrontAnchorTransportError("invalid_locator", "provider locator is invalid")
    if policy == "delivery" and not re.fullmatch(r"delivery\.[a-z0-9-]+\.bfl\.ai", host):
        raise BFLFrontAnchorTransportError("invalid_locator", "provider locator is invalid")
    return value


class BFLFrontAnchorHTTPTransport:
    """The only concrete HTTP boundary permitted for the front-anchor provider."""

    def __init__(
        self, *, api_key: SecretStr,
        client_factory: Callable[..., httpx.AsyncClient] | None = None,
        max_json_response_bytes: int = MAX_JSON_RESPONSE_BYTES,
        max_result_bytes: int = MAX_RESULT_BYTES,
    ) -> None:
        if not api_key.get_secret_value():
            raise BFLFrontAnchorTransportError("configuration", "BFL credential is missing")
        self._api_key = api_key
        self._client_factory = client_factory
        self._api_client: httpx.AsyncClient | None = None
        self._delivery_client: httpx.AsyncClient | None = None
        self._max_json = max_json_response_bytes
        self._max_result = max_result_bytes

    async def create(
        self, endpoint_url: str, *, headers: dict[str, str], payload: dict[str, Any],
        connect_timeout_seconds: float, read_timeout_seconds: float,
        maximum_response_bytes: int,
    ) -> dict[str, Any]:
        if endpoint_url != BASE_URL + ENDPOINT_PATH:
            raise BFLFrontAnchorTransportError("configuration", "BFL endpoint is not allowlisted")
        if headers.get("x-key", "") == "" or "Authorization" in headers:
            raise BFLFrontAnchorTransportError("configuration", "BFL create authorization is invalid")
        allowed = {"prompt", "width", "height", "output_format", "prompt_upsampling", "seed"}
        if set(payload) != allowed or payload.get("width") != 768 or payload.get("height") != 1024:
            raise BFLFrontAnchorTransportError("configuration", "BFL request payload is outside the fixed contract")
        body = await self._json_request(
            "POST", endpoint_url, headers=headers, json_body=payload,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            maximum_bytes=min(maximum_response_bytes, self._max_json),
        )
        if not isinstance(body, dict):
            raise BFLFrontAnchorTransportError("malformed_response", "BFL response is malformed")
        return body

    async def poll(
        self, polling_url: str, *, headers: dict[str, str],
        connect_timeout_seconds: float, read_timeout_seconds: float,
        maximum_response_bytes: int,
    ) -> dict[str, Any]:
        locator = _https_locator(polling_url, policy="poll")
        if headers.get("x-key", "") == "" or "Authorization" in headers:
            raise BFLFrontAnchorTransportError("configuration", "BFL poll authorization is invalid")
        body = await self._json_request(
            "GET", locator, headers=headers, json_body=None,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            maximum_bytes=min(maximum_response_bytes, self._max_json),
        )
        if not isinstance(body, dict):
            raise BFLFrontAnchorTransportError("malformed_response", "BFL poll response is malformed")
        return body

    async def download(
        self, result_url: str, *, connect_timeout_seconds: float,
        read_timeout_seconds: float, maximum_bytes: int,
    ) -> URLFetchResult:
        locator = _https_locator(result_url, policy="delivery")
        response = await self._stream(
            "GET", locator, headers={"accept": "image/png"}, json_body=None,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            maximum_bytes=min(maximum_bytes, self._max_result),
        )
        return response

    async def close(self) -> None:
        clients = (self._api_client, self._delivery_client)
        self._api_client = None
        self._delivery_client = None
        for client in clients:
            if client is not None:
                await client.aclose()

    async def _json_request(
        self, method: str, url: str, *, headers: dict[str, str], json_body: dict[str, Any] | None,
        connect_timeout_seconds: float, read_timeout_seconds: float, maximum_bytes: int,
    ) -> Any:
        response = await self._stream(
            method, url, headers=headers, json_body=json_body,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            maximum_bytes=maximum_bytes,
        )
        try:
            return json.loads(response.content.decode("utf-8"))
        except Exception:
            raise BFLFrontAnchorTransportError("malformed_response", "BFL JSON response is malformed") from None

    async def _stream(
        self, method: str, url: str, *, headers: dict[str, str], json_body: dict[str, Any] | None,
        connect_timeout_seconds: float, read_timeout_seconds: float, maximum_bytes: int,
    ) -> URLFetchResult:
        scope = "delivery" if headers == {"accept": "image/png"} else "api"
        client = await self._get_client(scope, connect_timeout_seconds, read_timeout_seconds)
        try:
            async with client.stream(
                method, url, headers=headers, json=json_body, follow_redirects=False,
            ) as response:
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > maximum_bytes:
                        raise BFLFrontAnchorTransportError("response_too_large", "BFL response exceeds byte limit")
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                return URLFetchResult(
                    status_code=response.status_code,
                    content=bytes(data),
                    content_type=content_type,
                )
        except BFLFrontAnchorTransportError:
            raise
        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException:
            raise BFLFrontAnchorTransportError("timeout", "BFL HTTP operation timed out") from None
        except httpx.HTTPError:
            raise BFLFrontAnchorTransportError("http_failure", "BFL HTTP operation failed") from None
        except Exception:
            raise BFLFrontAnchorTransportError("transport_failure", "BFL HTTP operation failed") from None

    async def _get_client(self, scope: str, connect: float, read: float) -> httpx.AsyncClient:
        current = self._delivery_client if scope == "delivery" else self._api_client
        if current is None:
            timeout = httpx.Timeout(timeout=read, connect=connect)
            kwargs = {
                "timeout": timeout,
                "follow_redirects": False,
                "trust_env": False,
                "limits": httpx.Limits(max_connections=1, max_keepalive_connections=1),
            }
            if self._client_factory is None:
                created = httpx.AsyncClient(**kwargs)
            else:
                created = self._client_factory(scope=scope, **kwargs)
            if scope == "delivery":
                self._delivery_client = created
            else:
                self._api_client = created
            current = created
        return current


def build_bfl_front_anchor_http_transport(api_key: SecretStr) -> BFLFrontAnchorHTTPTransport:
    """Construct the concrete client only after the caller's live gates pass."""
    return BFLFrontAnchorHTTPTransport(api_key=api_key)


__all__ = [
    "BASE_URL", "BFLFrontAnchorHTTPTransport", "BFLFrontAnchorTransportError",
    "build_bfl_front_anchor_http_transport",
]
