from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from urllib.parse import urlencode, urlunsplit

import httpx
import pytest
from pydantic import SecretStr

from scripts.benchmarks import r2_reference_transport_canary as canary
from scripts.benchmarks import r2_reference_transport_live_executor as executor
from tella.media import r2_canary_transport as transport
from tella.media.r2_reference_store import R2ReferenceStoreConfig


CONFIG_PATH = Path("configs/benchmarks/r2_reference_transport_canary_v1.json")
R2_NAMES = (
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET_NAME",
)


@pytest.fixture(autouse=True)
def _network_guard(monkeypatch):
    calls = 0

    def forbidden(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("network is forbidden in concrete R2 transport tests")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    yield
    assert calls == 0


def _store_config() -> R2ReferenceStoreConfig:
    return R2ReferenceStoreConfig(
        account_id=SecretStr("account-for-test"),
        access_key_id=SecretStr("access-for-test"),
        secret_access_key=SecretStr("secret-for-test"),
        bucket_name="private-test-bucket",
    )


def _confirmed_config(tmp_path: Path) -> Path:
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    payload["transport_policy"]["private_bucket_status_confirmed"] = True
    payload["transport_policy"]["conditional_write_test_confirmed"] = True
    path = tmp_path / "confirmed.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_optional_sdk_is_lazy_and_missing_sdk_error_is_safe(monkeypatch):
    def missing():
        raise transport.R2ClientFactoryError("optional_s3_sdk_unavailable")

    monkeypatch.setattr(transport, "_load_boto3", missing)
    with pytest.raises(transport.R2ClientFactoryError) as caught:
        transport.create_r2_s3_client(_store_config())
    assert caught.value.safe_category == "optional_s3_sdk_unavailable"
    serialized = repr(caught.value) + str(caught.value)
    assert "access-for-test" not in serialized
    assert "secret-for-test" not in serialized


def test_s3_factory_derives_endpoint_disables_retries_and_passes_timeouts(monkeypatch):
    seen: dict = {}

    class FakeSDKConfig:
        def __init__(self, **kwargs):
            seen["sdk_config"] = kwargs

    class FakeBoto3:
        @staticmethod
        def client(name, **kwargs):
            seen["service"] = name
            seen["client"] = kwargs
            return object()

    monkeypatch.setattr(transport, "_load_boto3", lambda: (FakeBoto3, FakeSDKConfig))
    result = transport.create_r2_s3_client(_store_config())
    assert result is not None
    assert seen["service"] == "s3"
    assert seen["client"]["endpoint_url"] == (
        "https://account-for-test.r2.cloudflarestorage.com"
    )
    assert seen["client"]["region_name"] == "auto"
    assert seen["sdk_config"]["connect_timeout"] == 5.0
    assert seen["sdk_config"]["read_timeout"] == 15.0
    assert seen["sdk_config"]["retries"] == {
        "mode": "standard", "total_max_attempts": 1
    }
    assert seen["sdk_config"]["signature_version"] == "s3v4"


def test_s3_factory_redacts_nested_sdk_failure(monkeypatch):
    class FakeSDKConfig:
        def __init__(self, **kwargs):
            pass

    class FakeBoto3:
        @staticmethod
        def client(*args, **kwargs):
            raise RuntimeError(
                "secret-for-test https://account-for-test.r2.cloudflarestorage.com"
            )

    monkeypatch.setattr(transport, "_load_boto3", lambda: (FakeBoto3, FakeSDKConfig))
    with pytest.raises(transport.R2ClientFactoryError) as caught:
        transport.create_r2_s3_client(_store_config())
    serialized = repr(caught.value) + str(caught.value)
    assert caught.value.safe_category == "s3_client_initialization_failed"
    assert "secret-for-test" not in serialized
    assert "account-for-test" not in serialized
    assert caught.value.__context__ is None


@pytest.mark.parametrize("missing", R2_NAMES)
def test_each_missing_r2_credential_blocks_before_client(
    tmp_path, monkeypatch, capsys, missing
):
    for name in R2_NAMES:
        monkeypatch.setenv(name, "present-test-value")
    monkeypatch.delenv(missing)
    constructed = 0

    def forbidden_factory(config):
        nonlocal constructed
        constructed += 1
        raise AssertionError("client construction must remain gated")

    monkeypatch.setattr(transport, "create_r2_s3_client", forbidden_factory)
    with pytest.raises(RuntimeError, match="credentials are incomplete"):
        canary.main([
            "--config", str(_confirmed_config(tmp_path)),
            "--mode", "live-r2",
            "--authorization-token", canary.AUTHORIZATION_TOKEN,
        ])
    assert constructed == 0
    assert capsys.readouterr().out == ""


def test_bfl_key_cannot_satisfy_r2_credentials(monkeypatch):
    for name in R2_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BFL_API_KEY", "unrelated-test-value")
    assert transport.r2_credential_presence() == {name: False for name in R2_NAMES}


class TrackingStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], *, cancel: bool = False):
        self.chunks = chunks
        self.cancel = cancel
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.cancel:
            raise asyncio.CancelledError()

    async def aclose(self):
        self.closed = True


def _signed_url() -> str:
    return urlunsplit((
        "https", "private.r2.example", "/reference.png",
        urlencode({"X-Amz-Signature": "RUNTIME-QUERY-ONLY"}), "",
    ))


@pytest.mark.asyncio
async def test_https_fetcher_preserves_exact_url_bytes_and_safe_mime():
    seen: list[str] = []
    stream = TrackingStream([b"exact", b"-bytes"])

    async def handler(request):
        seen.append(str(request.url))
        return httpx.Response(
            200, headers={"content-type": "image/png; charset=binary"}, stream=stream
        )

    fetcher = transport.BoundedR2HTTPSFetcher(transport=httpx.MockTransport(handler))
    result = await fetcher.get(_signed_url(), timeout_seconds=20, maximum_bytes=64)
    assert seen == [_signed_url()]
    assert result.content == b"exact-bytes"
    assert result.content_type == "image/png"
    assert stream.closed is True
    assert "RUNTIME-QUERY-ONLY" not in repr(fetcher)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url_parts", "category"),
    [
        (("http", "private.r2.example", "", ""), "https_required"),
        (("https", "user:pass@private.r2.example", "", ""), "url_userinfo_forbidden"),
        (("https", "private.r2.example", "", "fragment"), "url_fragment_forbidden"),
    ],
)
async def test_https_fetcher_rejects_unsafe_urls_without_transport(url_parts, category):
    called = False
    scheme, authority, path_suffix, fragment = url_parts
    url = urlunsplit((
        scheme,
        authority,
        "/object" + path_suffix,
        urlencode({"signature": "x"}),
        fragment,
    ))

    async def handler(request):
        nonlocal called
        called = True
        return httpx.Response(200, content=b"unused")

    fetcher = transport.BoundedR2HTTPSFetcher(transport=httpx.MockTransport(handler))
    with pytest.raises(transport.BoundedHTTPSFetchError) as caught:
        await fetcher.get(url, timeout_seconds=20, maximum_bytes=64)
    assert caught.value.safe_category == category
    assert called is False
    assert "signature=x" not in str(caught.value)
    assert "signature=x" not in json.dumps(caught.value.diagnostic())


@pytest.mark.asyncio
async def test_https_fetcher_aborts_oversized_body_and_closes_response():
    stream = TrackingStream([b"1234", b"5678"])
    fetcher = transport.BoundedR2HTTPSFetcher(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream)
        )
    )
    with pytest.raises(transport.BoundedHTTPSFetchError) as caught:
        await fetcher.get(_signed_url(), timeout_seconds=20, maximum_bytes=7)
    assert caught.value.safe_category == "response_body_too_large"
    assert stream.closed is True


@pytest.mark.asyncio
async def test_https_fetcher_rejects_redirect_without_query_leakage():
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            302, headers={"location": "https://untrusted.example/collect"}
        )

    fetcher = transport.BoundedR2HTTPSFetcher(transport=httpx.MockTransport(handler))
    with pytest.raises(transport.BoundedHTTPSFetchError) as caught:
        await fetcher.get(_signed_url(), timeout_seconds=20, maximum_bytes=64)
    assert caught.value.safe_category == "redirect_forbidden"
    assert calls == 1
    assert "RUNTIME-QUERY-ONLY" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raised", "category"),
    [
        (httpx.ConnectTimeout("private query material"), "connect_timeout"),
        (httpx.ReadTimeout("private query material"), "read_timeout"),
    ],
)
async def test_https_fetcher_timeout_errors_are_redacted(raised, category):
    async def handler(request):
        raise raised

    fetcher = transport.BoundedR2HTTPSFetcher(transport=httpx.MockTransport(handler))
    with pytest.raises(transport.BoundedHTTPSFetchError) as caught:
        await fetcher.get(_signed_url(), timeout_seconds=20, maximum_bytes=64)
    assert caught.value.safe_category == category
    assert "private query material" not in str(caught.value)
    assert "RUNTIME-QUERY-ONLY" not in str(caught.value)
    assert caught.value.__context__ is None


@pytest.mark.asyncio
async def test_https_fetcher_closes_response_on_cancellation():
    stream = TrackingStream([b"partial"], cancel=True)
    fetcher = transport.BoundedR2HTTPSFetcher(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream)
        )
    )
    with pytest.raises(asyncio.CancelledError):
        await fetcher.get(_signed_url(), timeout_seconds=20, maximum_bytes=64)
    assert stream.closed is True


def test_validate_only_does_not_import_optional_sdk_or_construct_clients(
    monkeypatch, capsys
):
    loaded = False

    def forbidden_load():
        nonlocal loaded
        loaded = True
        raise AssertionError("validate-only must not load boto3")

    monkeypatch.setattr(transport, "_load_boto3", forbidden_load)
    assert canary.main([
        "--config", str(CONFIG_PATH), "--mode", "validate-only"
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["clients_constructed"] == 0
    assert result["external_calls"] == 0
    assert loaded is False


def test_live_cli_wires_concrete_factories_only_after_all_gates(
    tmp_path, monkeypatch, capsys
):
    for name in R2_NAMES:
        monkeypatch.setenv(name, "present-test-value")
    constructions = 0

    def fake_factory(config):
        nonlocal constructions
        constructions += 1
        return object()

    async def fake_execute(config, **kwargs):
        assert kwargs["client_factory"] is fake_factory
        assert kwargs["url_fetcher_factory"] is transport.BoundedR2HTTPSFetcher
        kwargs["client_factory"](_store_config())
        return {"status": "passed", "accounting": {"r2_client_constructions": 1}}

    monkeypatch.setattr(transport, "create_r2_s3_client", fake_factory)
    monkeypatch.setattr(executor, "execute_r2_transport_canary", fake_execute)
    assert canary.main([
        "--config", str(_confirmed_config(tmp_path)),
        "--mode", "live-r2",
        "--authorization-token", canary.AUTHORIZATION_TOKEN,
    ]) == 0
    assert constructions == 1
    assert json.loads(capsys.readouterr().out)["status"] == "passed"


def test_wrong_authorization_and_missing_confirmation_construct_zero_clients(
    tmp_path, monkeypatch
):
    for name in R2_NAMES:
        monkeypatch.setenv(name, "present-test-value")
    constructed = 0

    def forbidden_factory(config):
        nonlocal constructed
        constructed += 1
        raise AssertionError("client construction must remain gated")

    monkeypatch.setattr(transport, "create_r2_s3_client", forbidden_factory)
    with pytest.raises(RuntimeError, match="authorization"):
        canary.main([
            "--config", str(_confirmed_config(tmp_path)),
            "--mode", "live-r2", "--authorization-token", "wrong",
        ])
    with pytest.raises(RuntimeError, match="private-bucket"):
        canary.main([
            "--config", str(CONFIG_PATH),
            "--mode", "live-r2",
            "--authorization-token", canary.AUTHORIZATION_TOKEN,
        ])
    assert constructed == 0


def test_concrete_transport_source_has_no_bfl_provider_dependency():
    source = Path("tella/media/r2_canary_transport.py").read_text(encoding="utf-8")
    harness = Path(
        "scripts/benchmarks/r2_reference_transport_canary.py"
    ).read_text(encoding="utf-8")
    assert "BFL_API_KEY" not in source + harness
    assert "bfl_flux2_provider" not in source + harness
