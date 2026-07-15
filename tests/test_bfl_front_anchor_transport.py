from __future__ import annotations

import asyncio
import httpx
import pytest
from pydantic import SecretStr

from tella.media.bfl_front_anchor_transport import (
    BASE_URL,
    BFLFrontAnchorHTTPTransport,
    BFLFrontAnchorTransportError,
)


def _transport(handler, **kwargs):
    def factory(**client_kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **client_kwargs)

    return BFLFrontAnchorHTTPTransport(api_key=SecretStr("secret-key"), client_factory=factory, **kwargs)


def test_create_uses_fixed_endpoint_body_and_header():
    seen = {}

    async def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = request.read()
        return httpx.Response(200, json={"id": "req-1", "polling_url": "https://api.bfl.ai/poll/req-1"})

    transport = _transport(handler)
    result = asyncio.run(transport.create(
        BASE_URL + "/v1/flux-pro-1.1",
        headers={"x-key": "secret-key", "Content-Type": "application/json"},
        payload={"prompt": "front", "width": 768, "height": 1024, "output_format": "png", "prompt_upsampling": False, "seed": 17001},
        connect_timeout_seconds=1, read_timeout_seconds=1, maximum_response_bytes=10000,
    ))
    asyncio.run(transport.close())
    assert result["id"] == "req-1"
    assert seen["url"] == BASE_URL + "/v1/flux-pro-1.1"
    assert seen["headers"]["x-key"] == "secret-key"
    assert set(__import__("json").loads(seen["body"])) == {"prompt", "width", "height", "output_format", "prompt_upsampling", "seed"}


def test_create_rejects_extra_optional_fields_before_http():
    transport = _transport(lambda request: httpx.Response(200, json={}))
    with pytest.raises(BFLFrontAnchorTransportError, match="payload"):
        asyncio.run(transport.create(
            BASE_URL + "/v1/flux-pro-1.1", headers={},
            payload={"prompt": "x", "width": 768, "height": 1024, "output_format": "png", "prompt_upsampling": False, "seed": 1, "webhook_url": "x"},
            connect_timeout_seconds=1, read_timeout_seconds=1, maximum_response_bytes=1000,
        ))


@pytest.mark.parametrize("locator", [
    "http://api.bfl.ai/poll", "https://user:pass@api.bfl.ai/poll",
    "https://api.bfl.ai/poll#fragment", "not-a-url",
])
def test_poll_rejects_unsafe_locator_without_http(locator):
    transport = _transport(lambda request: httpx.Response(200, json={"status": "Ready"}))
    with pytest.raises(BFLFrontAnchorTransportError, match="locator"):
        asyncio.run(transport.poll(locator, headers={}, connect_timeout_seconds=1, read_timeout_seconds=1, maximum_response_bytes=1000))


def test_poll_and_download_are_bounded_and_redirects_disabled():
    seen = []

    async def handler(request: httpx.Request):
        seen.append(request)
        if request.url.path == "/poll":
            return httpx.Response(200, json={"status": "Pending"})
        return httpx.Response(302, headers={"location": "https://other.example/result"}, content=b"redirect")

    transport = _transport(handler, max_result_bytes=20)
    poll = asyncio.run(transport.poll("https://api.bfl.ai/poll", headers={}, connect_timeout_seconds=1, read_timeout_seconds=1, maximum_response_bytes=1000))
    assert poll["status"] == "Pending"
    result = asyncio.run(transport.download("https://api.bfl.ai/result", connect_timeout_seconds=1, read_timeout_seconds=1, maximum_bytes=20))
    asyncio.run(transport.close())
    assert result.status_code == 302
    assert len(seen) == 2


def test_result_download_aborts_oversize_and_does_not_expose_url():
    async def handler(request: httpx.Request):
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"12345")

    transport = _transport(handler, max_result_bytes=4)
    with pytest.raises(BFLFrontAnchorTransportError, match="byte limit") as exc:
        asyncio.run(transport.download("https://cdn.bfl.ai/signed?token=secret", connect_timeout_seconds=1, read_timeout_seconds=1, maximum_bytes=20))
    assert "signed" not in str(exc.value)
    assert "secret" not in str(exc.value)
    asyncio.run(transport.close())


def test_key_is_not_in_transport_repr_or_configuration_error():
    transport = _transport(lambda request: httpx.Response(200, json={}))
    assert "secret-key" not in repr(transport)
    with pytest.raises(BFLFrontAnchorTransportError):
        BFLFrontAnchorHTTPTransport(api_key=SecretStr(""))
