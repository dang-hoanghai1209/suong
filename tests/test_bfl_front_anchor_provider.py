from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest
from PIL import Image
from pydantic import SecretStr, ValidationError

from tella.media.bfl_front_anchor_provider import (
    BFLFrontAnchorConfig,
    BFLFrontAnchorError,
    BFLFrontAnchorProvider,
    BFLFrontAnchorRequest,
)
from tella.media.temporary_reference_store import URLFetchResult


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (768, 1024), "white").save(output, format="PNG")
    return output.getvalue()


class FakeTransport:
    def __init__(self, *, create=None, poll=None, download=None):
        self.create_calls = []
        self.poll_calls = []
        self.download_calls = []
        self.create_result = create or {"id": "req-1", "polling_url": "https://api.bfl.ai/poll/req-1"}
        self.poll_result = poll or {"status": "Ready", "result": {"sample": "https://cdn.bfl.ai/result/req-1"}}
        self.download_result = download or URLFetchResult(status_code=200, content=_png(), content_type="image/png")

    async def create(self, endpoint_url, **kwargs):
        self.create_calls.append((endpoint_url, kwargs))
        return self.create_result

    async def poll(self, polling_url, **kwargs):
        self.poll_calls.append((polling_url, kwargs))
        return self.poll_result

    async def download(self, result_url, **kwargs):
        self.download_calls.append((result_url, kwargs))
        return self.download_result


def _provider(transport, accounting=None):
    return BFLFrontAnchorProvider(
        config=BFLFrontAnchorConfig(maximum_polls=2, polling_interval_seconds=0.001),
        transport=transport, api_key=SecretStr("test-key"), accounting=accounting,
    )


def test_fixed_request_contract_and_three_distinct_seeds():
    config = BFLFrontAnchorConfig()
    assert (config.width, config.height) == (768, 1024)
    assert config.output_format == "png"
    assert config.prompt_upsampling is False
    with pytest.raises(ValidationError):
        BFLFrontAnchorConfig(width=1024)


def test_one_create_poll_download_and_atomic_valid_png(tmp_path: Path):
    transport = FakeTransport()
    accounting = {}
    result = asyncio.run(_provider(transport, accounting).generate(
        BFLFrontAnchorRequest(prompt="front", seed=17001), tmp_path / "candidate.png"
    ))
    assert result.metadata["request_id"] == "req-1"
    assert result.metadata["dimensions"] == [768, 1024]
    assert len(transport.create_calls) == 1
    assert len(transport.poll_calls) == 1
    assert len(transport.download_calls) == 1
    assert accounting["application_image_submissions"] == 1
    assert accounting["bfl_create_attempts"] == 1
    assert (tmp_path / "candidate.png").read_bytes() == _png()


@pytest.mark.parametrize("content_type,content", [
    ("image/jpeg", _png()),
    ("image/webp", _png()),
    ("image/png", b"not-png"),
])
def test_invalid_mime_or_bytes_fail_without_resubmit(tmp_path, content_type, content):
    transport = FakeTransport(download=URLFetchResult(status_code=200, content=content, content_type=content_type))
    provider = _provider(transport)
    with pytest.raises(BFLFrontAnchorError, match="output_validation"):
        asyncio.run(provider.generate(BFLFrontAnchorRequest(prompt="front", seed=1), tmp_path / "x.png"))
    assert len(transport.create_calls) == 1
    assert len(transport.download_calls) == 1
    assert not (tmp_path / "x.png").exists()


def test_malformed_create_and_timeout_consume_budget_without_retry(tmp_path):
    malformed = FakeTransport(create={"status": "Pending"})
    provider = _provider(malformed)
    with pytest.raises(BFLFrontAnchorError, match="malformed_response"):
        asyncio.run(provider.generate(BFLFrontAnchorRequest(prompt="front", seed=1), tmp_path / "x.png"))
    assert len(malformed.create_calls) == 1

    class TimeoutTransport(FakeTransport):
        async def create(self, endpoint_url, **kwargs):
            self.create_calls.append((endpoint_url, kwargs))
            raise asyncio.TimeoutError

    timed = TimeoutTransport()
    provider = _provider(timed)
    with pytest.raises(BFLFrontAnchorError, match="timeout"):
        asyncio.run(provider.generate(BFLFrontAnchorRequest(prompt="front", seed=2), tmp_path / "y.png"))
    assert len(timed.create_calls) == 1


def test_failure_does_not_expose_url_or_secret(tmp_path):
    transport = FakeTransport(create={"id": "req", "polling_url": "http://bad"})
    provider = _provider(transport)
    with pytest.raises(BFLFrontAnchorError) as exc:
        asyncio.run(provider.generate(BFLFrontAnchorRequest(prompt="front", seed=1), tmp_path / "x.png"))
    text = str(exc.value)
    assert "http://" not in text
    assert "test-key" not in text

