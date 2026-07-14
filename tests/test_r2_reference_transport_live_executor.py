from __future__ import annotations

import asyncio
import hashlib
import json
import socket
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit

import pytest

from scripts.benchmarks.r2_reference_transport_canary import (
    AUTHORIZATION_TOKEN,
    deterministic_test_png,
    load_canary_config,
)
from scripts.benchmarks.r2_reference_transport_live_executor import (
    R2CanaryExecutionError,
    execute_r2_transport_canary,
)
from tella.media.temporary_reference_store import URLFetchResult, content_addressed_key


CONFIG_PATH = Path("configs/benchmarks/r2_reference_transport_canary_v1.json")


class NotFound(Exception):
    response = {"ResponseMetadata": {"HTTPStatusCode": 404}}


class ConditionalFailure(Exception):
    def __init__(self, status_code: int):
        self.response = {"ResponseMetadata": {"HTTPStatusCode": status_code}}
        super().__init__("safe conditional failure")


class FakeClient:
    def __init__(
        self,
        *,
        conditional_results: list[int | str] | None = None,
        presign_failure: bool = False,
        delete_failure: bool = False,
        fail_after_initial_put: bool = False,
    ) -> None:
        self.objects: dict[str, tuple[bytes, str, dict[str, str]]] = {}
        self.put_calls: list[dict] = []
        self.delete_calls: list[str] = []
        self.conditional_results = list(conditional_results or [409, 412])
        self.presign_failure = presign_failure
        self.delete_failure = delete_failure
        self.fail_after_initial_put = fail_after_initial_put
        self.initial_put_seen = False
        self.successful_overwrites = 0

    def head_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise NotFound()
        body, mime, metadata = self.objects[Key]
        return {
            "ContentLength": len(body),
            "ContentType": mime,
            "Metadata": dict(metadata),
        }

    def get_object(self, *, Bucket, Key):
        body, mime, metadata = self.objects[Key]
        return {
            "Body": body,
            "ContentLength": len(body),
            "ContentType": mime,
            "Metadata": dict(metadata),
        }

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        key = kwargs["Key"]
        if key in self.objects and kwargs.get("IfNoneMatch") == "*":
            result = self.conditional_results.pop(0)
            if isinstance(result, int):
                raise ConditionalFailure(result)
            if result == "cancel":
                raise asyncio.CancelledError()
            if result == "success":
                self.successful_overwrites += 1
            else:
                raise ConditionalFailure(500)
        self.objects[key] = (
            kwargs["Body"], kwargs["ContentType"], dict(kwargs["Metadata"])
        )
        if not self.initial_put_seen:
            self.initial_put_seen = True
            if self.fail_after_initial_put:
                raise RuntimeError("ambiguous transport response")
        return {}

    def delete_object(self, *, Bucket, Key):
        self.delete_calls.append(Key)
        if self.delete_failure:
            raise RuntimeError("safe delete failure")
        self.objects.pop(Key, None)
        return {}

    def list_objects_v2(self, **kwargs):
        return {"Contents": []}

    def generate_presigned_url(self, operation, *, Params, ExpiresIn):
        if self.presign_failure:
            raise RuntimeError("presign failed with query material that must stay private")
        return urlunsplit((
            "https", "private.r2.example", "/" + Params["Key"],
            urlencode({"signature": "RUNTIME-ONLY-SECRET"}), "",
        ))


class FakeFetcher:
    def __init__(
        self,
        client: FakeClient,
        *,
        failure: bool = False,
        mismatch: bool = False,
        mime_override: str | None = None,
    ) -> None:
        self.client = client
        self.failure = failure
        self.mismatch = mismatch
        self.mime_override = mime_override
        self.calls: list[tuple[str, float, int]] = []

    async def get(self, url, *, timeout_seconds, maximum_bytes):
        self.calls.append((url, timeout_seconds, maximum_bytes))
        if self.failure:
            raise RuntimeError(url)
        key = urlsplit(url).path.lstrip("/")
        body, mime, _ = self.client.objects[key]
        if self.mismatch:
            body = b"different exact bytes"
        return URLFetchResult(
            status_code=200,
            content=body,
            content_type=self.mime_override or mime,
        )


@pytest.fixture(autouse=True)
def _blocked_network_and_credentials(monkeypatch):
    calls = 0

    def forbidden(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("network is forbidden in R2 executor tests")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    for name in (
        "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"
    ):
        monkeypatch.setenv(name, "present-test-value")
    monkeypatch.setenv("BFL_API_KEY", "ignored-test-value")
    yield
    assert calls == 0


def _config(tmp_path: Path, *, private=True, conditional=True):
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    payload["transport_policy"]["private_bucket_status_confirmed"] = private
    payload["transport_policy"]["conditional_write_test_confirmed"] = conditional
    path = tmp_path / "executor-config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return load_canary_config(path)


async def _execute(tmp_path: Path, client: FakeClient, fetcher: FakeFetcher | None = None):
    constructed = []

    def client_factory(config):
        constructed.append(config)
        return client

    result = await execute_r2_transport_canary(
        _config(tmp_path),
        mode="live-r2",
        authorization_token=AUTHORIZATION_TOKEN,
        client_factory=client_factory,
        url_fetcher_factory=lambda: fetcher or FakeFetcher(client),
    )
    assert len(constructed) == 1
    return result


@pytest.mark.asyncio
async def test_exact_bytes_conditional_contract_roundtrip_and_cleanup(tmp_path):
    client = FakeClient(conditional_results=[409, 412])
    fetcher = FakeFetcher(client)
    result = await _execute(tmp_path, client, fetcher)
    expected = deterministic_test_png(_config(tmp_path).test_image)
    assert result["status"] == "passed"
    assert result["conditional_write_observed_result"] == {
        "identical": "409", "conflicting": "412"
    }
    assert result["borrowed_object_policy_verified"] is True
    assert result["cleanup_outcome"] == "deleted"
    assert result["post_cleanup_absence_confirmed"] is True
    assert result["cleanup_required"] is False
    assert len(client.put_calls) == 3
    first = client.put_calls[0]
    assert first["Body"] == expected
    assert first["ContentType"] == "image/png"
    assert first["IfNoneMatch"] == "*"
    assert first["Metadata"]["source-sha256"] == hashlib.sha256(expected).hexdigest()
    assert len(first["Metadata"]["tella-upload-owner"]) == 32
    assert len(fetcher.calls) == 1
    assert fetcher.calls[0][2] == 65536
    assert client.successful_overwrites == 0
    assert len(client.delete_calls) == 1
    assert client.objects == {}
    accounting = result["accounting"]
    assert accounting["r2_client_constructions"] == 1
    assert accounting["temporary_store_upload_attempts"] == 1
    assert accounting["conditional_write_attempts"] == 2
    assert accounting["temporary_store_verification_downloads"] == 1
    serialized = json.dumps(result)
    assert "RUNTIME-ONLY-SECRET" not in serialized
    assert "signature" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [409, 412])
async def test_second_conditional_write_records_supported_conflict(tmp_path, status):
    result = await _execute(
        tmp_path, FakeClient(conditional_results=[status, status])
    )
    assert result["conditional_write_observed_result"]["identical"] == str(status)


@pytest.mark.asyncio
@pytest.mark.parametrize("result", ["unexpected", "success"])
async def test_unexpected_or_successful_second_write_fails_closed(tmp_path, result):
    client = FakeClient(conditional_results=[result, 412])
    with pytest.raises(R2CanaryExecutionError) as caught:
        await _execute(tmp_path, client)
    assert caught.value.category == "identical_conditional_write_did_not_conflict"
    assert caught.value.diagnostic["cleanup_outcome"] == "deleted"
    assert client.objects == {}


@pytest.mark.asyncio
async def test_preexisting_conflicting_object_is_not_overwritten_or_deleted(tmp_path):
    config = _config(tmp_path)
    content = deterministic_test_png(config.test_image)
    digest = hashlib.sha256(content).hexdigest()
    key = content_addressed_key(digest, "tella-r2-reference-canary.png")
    client = FakeClient()
    client.objects[key] = (b"preexisting conflict", "image/png", {"owner": "other"})
    with pytest.raises(R2CanaryExecutionError) as caught:
        await _execute(tmp_path, client)
    assert caught.value.category == "initial_upload_or_roundtrip_failed"
    assert client.objects[key][0] == b"preexisting conflict"
    assert client.put_calls == []
    assert client.delete_calls == []


@pytest.mark.asyncio
async def test_presign_failure_cleans_owned_object(tmp_path):
    client = FakeClient(presign_failure=True)
    with pytest.raises(R2CanaryExecutionError) as caught:
        await _execute(tmp_path, client)
    assert caught.value.diagnostic["cleanup_outcome"] == "helper_cleanup_succeeded"
    assert client.objects == {}
    assert len(client.delete_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,mismatch", [(True, False), (False, True)])
async def test_verification_failure_or_hash_mismatch_cleans(tmp_path, failure, mismatch):
    client = FakeClient()
    fetcher = FakeFetcher(client, failure=failure, mismatch=mismatch)
    with pytest.raises(R2CanaryExecutionError) as caught:
        await _execute(tmp_path, client, fetcher)
    assert caught.value.diagnostic["cleanup_outcome"] == "helper_cleanup_succeeded"
    assert client.objects == {}


@pytest.mark.asyncio
async def test_verification_mime_failure_cleans(tmp_path):
    client = FakeClient()
    with pytest.raises(R2CanaryExecutionError) as caught:
        await _execute(
            tmp_path, client, FakeFetcher(client, mime_override="image/jpeg")
        )
    assert caught.value.diagnostic["cleanup_outcome"] == "helper_cleanup_succeeded"
    assert client.objects == {}


@pytest.mark.asyncio
async def test_roundtrip_decode_failure_cleans(tmp_path, monkeypatch):
    client = FakeClient()

    def fail_decode(*args, **kwargs):
        raise ValueError("safe synthetic decode failure")

    monkeypatch.setattr(
        "tella.media.temporary_reference_store.validate_image_bytes", fail_decode
    )
    with pytest.raises(R2CanaryExecutionError) as caught:
        await _execute(tmp_path, client)
    assert caught.value.diagnostic["cleanup_outcome"] == "helper_cleanup_succeeded"
    assert client.objects == {}


@pytest.mark.asyncio
async def test_cancellation_cleans_owned_object(tmp_path):
    client = FakeClient(conditional_results=["cancel", 412])
    with pytest.raises(asyncio.CancelledError):
        await _execute(tmp_path, client)
    assert client.objects == {}
    assert len(client.delete_calls) == 1


@pytest.mark.asyncio
async def test_ambiguous_initial_write_owner_token_is_reconciled_and_deleted(tmp_path):
    client = FakeClient(fail_after_initial_put=True)
    with pytest.raises(R2CanaryExecutionError) as caught:
        await _execute(tmp_path, client)
    diagnostic = caught.value.diagnostic
    assert diagnostic["cleanup_outcome"] == "helper_cleanup_succeeded"
    assert diagnostic["cleanup_required"] is False
    assert client.objects == {}
    assert len(client.delete_calls) == 1


@pytest.mark.asyncio
async def test_delete_failure_sets_cleanup_required_and_preserves_safe_diagnostic(tmp_path):
    client = FakeClient(delete_failure=True)
    with pytest.raises(R2CanaryExecutionError) as caught:
        await _execute(tmp_path, client)
    assert caught.value.category == "owned_object_cleanup_failed"
    assert caught.value.diagnostic["cleanup_required"] is True
    assert caught.value.diagnostic["cleanup_outcome"] == "delete_failed"
    serialized = json.dumps(caught.value.diagnostic)
    assert "RUNTIME-ONLY-SECRET" not in serialized
    assert "signature" not in serialized


@pytest.mark.asyncio
async def test_all_gates_precede_client_construction(tmp_path, monkeypatch):
    constructed = 0

    def client_factory(config):
        nonlocal constructed
        constructed += 1
        return FakeClient()

    cases = [
        (_config(tmp_path), "not-live", AUTHORIZATION_TOKEN, "mode"),
        (_config(tmp_path), "live-r2", "wrong", "authorization"),
        (_config(tmp_path, private=False), "live-r2", AUTHORIZATION_TOKEN, "private"),
        (_config(tmp_path, conditional=False), "live-r2", AUTHORIZATION_TOKEN, "IfNoneMatch"),
    ]
    for config, mode, token, message in cases:
        with pytest.raises((R2CanaryExecutionError, RuntimeError), match=message):
            await execute_r2_transport_canary(
                config,
                mode=mode,
                authorization_token=token,
                client_factory=client_factory,
                url_fetcher_factory=lambda: FakeFetcher(FakeClient()),
            )
    monkeypatch.delenv("R2_SECRET_ACCESS_KEY", raising=False)
    with pytest.raises(RuntimeError, match="credentials"):
        await execute_r2_transport_canary(
            _config(tmp_path),
            mode="live-r2",
            authorization_token=AUTHORIZATION_TOKEN,
            client_factory=client_factory,
            url_fetcher_factory=lambda: FakeFetcher(FakeClient()),
        )
    assert constructed == 0


def test_executor_has_no_image_provider_import_or_signed_url_literal():
    source = Path(
        "scripts/benchmarks/r2_reference_transport_live_executor.py"
    ).read_text(encoding="utf-8")
    assert "bfl_flux2_provider" not in source
    assert "BFL_API_KEY" not in source
    assert "https://" not in source


@pytest.mark.asyncio
@pytest.mark.parametrize("factory_name", ["client", "fetcher"])
async def test_factory_exceptions_are_redacted(tmp_path, factory_name):
    def failing_factory(*args):
        raise RuntimeError(
            urlunsplit((
                "https", "secret.invalid", "/factory",
                urlencode({"signature": "FACTORY-SECRET"}), "",
            ))
        )

    with pytest.raises(R2CanaryExecutionError) as caught:
        await execute_r2_transport_canary(
            _config(tmp_path),
            mode="live-r2",
            authorization_token=AUTHORIZATION_TOKEN,
            client_factory=(failing_factory if factory_name == "client" else lambda c: FakeClient()),
            url_fetcher_factory=(
                failing_factory if factory_name == "fetcher"
                else lambda: FakeFetcher(FakeClient())
            ),
        )
    assert "FACTORY-SECRET" not in str(caught.value)
    assert "FACTORY-SECRET" not in json.dumps(caught.value.diagnostic)
