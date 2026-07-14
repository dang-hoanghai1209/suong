from __future__ import annotations

import hashlib
import io
import socket
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlunsplit

import pytest
from PIL import Image
from pydantic import SecretStr, ValidationError

from tella.media.r2_reference_store import R2ReferenceStoreConfig, R2TemporaryReferenceStore
from tella.media.temporary_reference_store import URLFetchResult


@pytest.fixture(autouse=True)
def _block_real_sockets(monkeypatch):
    calls = 0
    def forbidden(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("real sockets are forbidden in R2 tests")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    yield
    assert calls == 0


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (64, 64), "navy").save(out, format="PNG")
    return out.getvalue()


class NotFound(Exception):
    response = {"ResponseMetadata": {"HTTPStatusCode": 404}}


class Client:
    def __init__(self):
        self.objects = {}
        self.puts = []
        self.deletes = []
        self.listed = []
        self.presigned_url = None

    def head_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise NotFound()
        body, mime = self.objects[Key]
        return {"ContentLength": len(body), "ContentType": mime}

    def get_object(self, *, Bucket, Key):
        body, mime = self.objects[Key]
        return {"Body": body, "ContentType": mime}

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        self.objects[kwargs["Key"]] = (kwargs["Body"], kwargs["ContentType"])
        return {}

    def delete_object(self, *, Bucket, Key):
        self.deletes.append(Key)
        self.objects.pop(Key, None)
        return {}

    def list_objects_v2(self, *, Bucket, Prefix, MaxKeys):
        assert MaxKeys == 1000
        return {"Contents": list(self.listed)}

    def generate_presigned_url(self, operation, *, Params, ExpiresIn):
        return self.presigned_url or _signed_url(
            "private.r2.example", Params["Key"], "DO-NOT-SERIALIZE"
        )


class Fetcher:
    def __init__(self, client):
        self.client = client
        self.calls = []

    async def get(self, url, *, timeout_seconds, maximum_bytes):
        self.calls.append((url, timeout_seconds, maximum_bytes))
        key = url.split("private.r2.example/", 1)[1].split("?", 1)[0]
        body, mime = self.client.objects[key]
        return URLFetchResult(status_code=200, content=body, content_type=mime)


def _signed_url(host: str, path: str, token: str) -> str:
    return urlunsplit(("https", host, "/" + path, urlencode({"sig": token}), ""))


def _config(**overrides):
    values = dict(
        account_id=SecretStr("account"), access_key_id=SecretStr("access"),
        secret_access_key=SecretStr("secret"), bucket_name="private-bucket",
        private_bucket_status_confirmed=True,
        conditional_write_support_confirmed=True,
    )
    values.update(overrides)
    return R2ReferenceStoreConfig(**values)


@pytest.mark.asyncio
async def test_r2_upload_is_immutable_private_exact_and_presigned():
    client = Client()
    store = R2TemporaryReferenceStore(_config(), client=client, url_fetcher=Fetcher(client))
    content = _png()
    digest = hashlib.sha256(content).hexdigest()
    obj = await store.upload_immutable(
        object_key=f"reference-sheets/{digest}/ref.png", content=content,
        content_type="image/png", source_sha256=digest, ttl_seconds=600,
    )
    assert client.puts[0]["Body"] is content
    assert client.puts[0]["IfNoneMatch"] == "*"
    assert client.puts[0]["Metadata"]["source-sha256"] == digest
    assert len(client.puts[0]["Metadata"]["tella-upload-owner"]) == 32
    assert obj.read_url.get_secret_value().startswith("https://")
    assert "DO-NOT-SERIALIZE" not in repr(obj)
    response = await store.retrieve_via_read_url(
        obj, timeout_seconds=5, maximum_bytes=len(content)
    )
    assert response.content == content
    assert response.content_type == "image/png"
    assert obj.diagnostic()["read_url"] == {
        "scheme": "https", "host": "private.r2.example"
    }
    assert "DO-NOT-SERIALIZE" not in repr(obj.diagnostic())


@pytest.mark.asyncio
async def test_existing_identical_object_reused_but_conflict_fails():
    client = Client()
    store = R2TemporaryReferenceStore(_config(), client=client, url_fetcher=Fetcher(client))
    content = _png()
    key = "reference-sheets/" + hashlib.sha256(content).hexdigest() + "/ref.png"
    client.objects[key] = (content, "image/png")
    reused = await store.upload_immutable(
        object_key=key, content=content, content_type="image/png",
        source_sha256=hashlib.sha256(content).hexdigest(), ttl_seconds=600,
    )
    assert client.puts == []
    assert reused.cleanup_owned is False
    assert await store.delete(reused) is True
    assert client.deletes == []
    client.objects[key] = (b"conflict", "image/png")
    with pytest.raises(RuntimeError, match="conflicting"):
        await store.upload_immutable(
            object_key=key, content=content, content_type="image/png",
            source_sha256=hashlib.sha256(content).hexdigest(), ttl_seconds=600,
        )


def test_missing_credentials_fail_before_client_construction(monkeypatch):
    for name in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"):
        monkeypatch.delenv(name, raising=False)
    constructed = 0
    def factory(config):
        nonlocal constructed
        constructed += 1
        raise AssertionError
    with pytest.raises(RuntimeError, match="credentials are incomplete"):
        R2TemporaryReferenceStore.from_environment(
            client_factory=factory, url_fetcher=Fetcher(Client())
        )
    assert constructed == 0


def test_private_bucket_and_ttl_are_bounded_and_secrets_excluded():
    with pytest.raises(ValidationError, match="private"):
        _config(private_bucket=False)
    dumped = _config().model_dump()
    assert "account_id" not in dumped
    assert "access_key_id" not in dumped
    assert "secret_access_key" not in dumped
    assert "account" not in repr(_config())


@pytest.mark.asyncio
async def test_deployment_claims_must_be_separately_confirmed():
    content = _png()
    digest = hashlib.sha256(content).hexdigest()
    key = f"reference-sheets/{digest}/ref.png"
    for config, message in [
        (_config(private_bucket_status_confirmed=False), "private-bucket status"),
        (_config(conditional_write_support_confirmed=False), "conditional-write support"),
    ]:
        client = Client()
        store = R2TemporaryReferenceStore(config, client=client, url_fetcher=Fetcher(client))
        with pytest.raises(RuntimeError, match=message):
            await store.upload_immutable(
                object_key=key, content=content, content_type="image/png",
                source_sha256=digest, ttl_seconds=600,
            )
        assert client.puts == []


@pytest.mark.asyncio
async def test_presign_failure_deletes_only_newly_created_object():
    content = _png()
    digest = hashlib.sha256(content).hexdigest()
    key = f"reference-sheets/{digest}/ref.png"
    client = Client()
    client.presigned_url = "http://public.invalid/object?sig=secret"
    store = R2TemporaryReferenceStore(_config(), client=client, url_fetcher=Fetcher(client))
    with pytest.raises(RuntimeError, match="presigned read URL creation failed") as caught:
        await store.upload_immutable(
            object_key=key, content=content, content_type="image/png",
            source_sha256=digest, ttl_seconds=600,
        )
    assert "sig=secret" not in str(caught.value)
    assert client.deletes == [key]
    assert key not in client.objects


@pytest.mark.asyncio
async def test_sdk_upload_exception_is_redacted():
    class FailingClient(Client):
        def put_object(self, **kwargs):
            raise RuntimeError(_signed_url("r2.example", "object", "SDK-SECRET"))
    content = _png()
    digest = hashlib.sha256(content).hexdigest()
    client = FailingClient()
    store = R2TemporaryReferenceStore(_config(), client=client, url_fetcher=Fetcher(client))
    with pytest.raises(RuntimeError, match="immutable upload failed") as caught:
        await store.upload_immutable(
            object_key=f"reference-sheets/{digest}/ref.png", content=content,
            content_type="image/png", source_sha256=digest, ttl_seconds=600,
        )
    assert "SDK-SECRET" not in str(caught.value)


@pytest.mark.asyncio
async def test_uncertain_completed_upload_is_owner_verified_then_deleted():
    class StoredThenFailedClient(Client):
        def __init__(self):
            super().__init__()
            self.metadata = {}
        def put_object(self, **kwargs):
            self.objects[kwargs["Key"]] = (kwargs["Body"], kwargs["ContentType"])
            self.metadata[kwargs["Key"]] = kwargs["Metadata"]
            raise RuntimeError("transport response lost")
        def head_object(self, *, Bucket, Key):
            response = super().head_object(Bucket=Bucket, Key=Key)
            response["Metadata"] = self.metadata.get(Key, {})
            return response
    content = _png()
    digest = hashlib.sha256(content).hexdigest()
    key = f"reference-sheets/{digest}/ref.png"
    client = StoredThenFailedClient()
    store = R2TemporaryReferenceStore(_config(), client=client, url_fetcher=Fetcher(client))
    with pytest.raises(RuntimeError, match="immutable upload failed"):
        await store.upload_immutable(
            object_key=key, content=content, content_type="image/png",
            source_sha256=digest, ttl_seconds=600,
        )
    assert client.deletes == [key]
    assert key not in client.objects


@pytest.mark.asyncio
async def test_stale_cleanup_requires_safe_prefix_and_deletes_old_only():
    client = Client()
    now = datetime.now(UTC)
    client.listed = [
        {"Key": "reference-sheets/a/old.png", "LastModified": now - timedelta(days=2)},
        {"Key": "reference-sheets/a/new.png", "LastModified": now},
    ]
    store = R2TemporaryReferenceStore(_config(), client=client, url_fetcher=Fetcher(client))
    with pytest.raises(ValueError, match="safe"):
        await store.cleanup_stale(prefix="other/", older_than=now - timedelta(days=1))
    deleted = await store.cleanup_stale(
        prefix="reference-sheets/", older_than=now - timedelta(days=1)
    )
    assert deleted == ["reference-sheets/a/old.png"]
