from __future__ import annotations

import hashlib
import io
import socket
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlunsplit

import pytest
from PIL import Image
from pydantic import SecretStr

from tella.media.temporary_reference_store import (
    TemporaryReferenceObject,
    TemporaryReferenceStoreCapabilities,
    URLFetchResult,
    content_addressed_key,
    upload_and_verify_reference,
    validate_image_bytes,
)


@pytest.fixture(autouse=True)
def _block_real_sockets(monkeypatch):
    calls = 0
    def forbidden(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("real sockets are forbidden in reference-store tests")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    yield
    assert calls == 0


def _png(size=(64, 96), color="#248c86") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


class MemoryStore:
    def __init__(self, *, returned: bytes | None = None, mime="image/png", delete=True):
        self.objects = {}
        self.returned = returned
        self.mime = mime
        self.delete_result = delete
        self.uploads = []
        self.deletes = []

    def capabilities(self):
        return TemporaryReferenceStoreCapabilities(
            provider_id="memory", private_objects_only=True,
            supports_presigned_get=True, supports_retrieve=True,
            supports_delete=True, supports_exists=True, preserves_exact_bytes=True,
            minimum_ttl_seconds=60, maximum_ttl_seconds=3600,
        )

    async def upload_immutable(self, **kwargs):
        self.uploads.append(kwargs)
        key = kwargs["object_key"]
        if key in self.objects and self.objects[key] != kwargs["content"]:
            raise RuntimeError("conflict")
        self.objects[key] = kwargs["content"]
        now = datetime.now(UTC)
        return TemporaryReferenceObject(
            store_provider_id="memory", storage_namespace="private-tests",
            object_key=key, source_sha256=kwargs["source_sha256"],
            stored_byte_size=len(kwargs["content"]), content_type=kwargs["content_type"],
            created_at=now, expires_at=now + timedelta(seconds=kwargs["ttl_seconds"]),
            read_url=SecretStr(_signed_url("private.example", "object", "TOP-SECRET")),
        )

    async def retrieve_via_read_url(self, obj, *, timeout_seconds, maximum_bytes):
        content = self.returned if self.returned is not None else self.objects[obj.object_key]
        return URLFetchResult(status_code=200, content=content, content_type=self.mime)

    async def exists(self, object_key):
        return object_key in self.objects

    async def delete(self, obj):
        self.deletes.append(obj.object_key)
        if self.delete_result:
            self.objects.pop(obj.object_key, None)
        return self.delete_result

    async def cleanup_stale(self, *, prefix, older_than):
        return []


def _signed_url(host: str, path: str, token: str) -> str:
    return urlunsplit(("https", host, "/" + path, urlencode({"signature": token}), ""))


@pytest.mark.asyncio
async def test_exact_bytes_content_addressed_key_mime_hash_and_diagnostic_redaction():
    content = _png()
    digest = hashlib.sha256(content).hexdigest()
    store = MemoryStore()
    counts = {}
    obj = await upload_and_verify_reference(
        store, filename="My Character!!.png", content=content,
        content_type="image/png", approved_sha256=digest, ttl_seconds=600,
        download_timeout_seconds=3, expected_dimensions=(64, 96), accounting=counts,
    )
    assert store.uploads[0]["content"] is content
    assert obj.object_key == f"reference-sheets/{digest}/My_Character__.png"
    assert obj.source_sha256 == obj.roundtrip_sha256 == digest
    assert obj.stored_byte_size == len(content)
    assert obj.content_type == "image/png"
    assert obj.roundtrip_verified is True
    diagnostic = obj.diagnostic()
    assert diagnostic["read_url"] == {"scheme": "https", "host": "private.example"}
    assert "signature" not in repr(diagnostic)
    assert "TOP-SECRET" not in repr(obj)
    assert counts == {
        "temporary_store_upload_attempts": 1,
        "temporary_store_presign_operations": 1,
        "temporary_store_verification_downloads": 1,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("returned", "mime", "message"),
    [
        (b"different", "image/png", "roundtrip SHA256"),
        (_png(), "image/jpeg", "MIME mismatch"),
    ],
)
async def test_roundtrip_failures_delete_without_provider_submission(returned, mime, message):
    content = _png(color="#123456")
    store = MemoryStore(returned=returned, mime=mime)
    counts = {}
    with pytest.raises(ValueError, match=message):
        await upload_and_verify_reference(
            store, filename="ref.png", content=content, content_type="image/png",
            approved_sha256=hashlib.sha256(content).hexdigest(), ttl_seconds=600,
            download_timeout_seconds=3, accounting=counts,
        )
    assert len(store.deletes) == 1
    assert counts["reference_cleanup_attempts"] == 1
    assert counts["reference_cleanup_successes"] == 1
    assert "application_image_submissions" not in counts


def test_size_mime_decode_and_dimension_validation_fail_closed():
    content = _png()
    with pytest.raises(ValueError, match="byte-size"):
        validate_image_bytes(content, content_type="image/png", expected_size=len(content) + 1)
    with pytest.raises(ValueError, match="MIME is not allowed"):
        validate_image_bytes(content, content_type="image/gif", expected_size=len(content))
    with pytest.raises(ValueError, match="decoding"):
        validate_image_bytes(b"not-image", content_type="image/png", expected_size=9)
    with pytest.raises(ValueError, match="dimensions"):
        validate_image_bytes(
            content, content_type="image/png", expected_size=len(content),
            expected_dimensions=(1, 1),
        )


def test_key_rejects_invalid_digest_and_removes_path_components():
    with pytest.raises(ValueError, match="SHA256"):
        content_addressed_key("bad", "ref.png")
    digest = "a" * 64
    assert content_addressed_key(digest, "../unsafe/ref.png") == (
        f"reference-sheets/{digest}/ref.png"
    )


@pytest.mark.asyncio
async def test_oversized_local_reference_fails_before_upload():
    content = _png()
    store = MemoryStore()
    with pytest.raises(ValueError, match="local byte-size"):
        await upload_and_verify_reference(
            store, filename="ref.png", content=content, content_type="image/png",
            approved_sha256=hashlib.sha256(content).hexdigest(), ttl_seconds=600,
            download_timeout_seconds=3, maximum_reference_bytes=len(content) - 1,
        )
    assert store.uploads == []


@pytest.mark.asyncio
async def test_verification_transport_exception_is_redacted_and_cleans():
    class FailingStore(MemoryStore):
        async def retrieve_via_read_url(self, obj, *, timeout_seconds, maximum_bytes):
            raise RuntimeError(
                _signed_url("private.example", "object", "EXCEPTION-SECRET")
            )
    content = _png()
    store = FailingStore()
    with pytest.raises(RuntimeError, match="verification download failed") as caught:
        await upload_and_verify_reference(
            store, filename="ref.png", content=content, content_type="image/png",
            approved_sha256=hashlib.sha256(content).hexdigest(), ttl_seconds=600,
            download_timeout_seconds=3,
        )
    assert "EXCEPTION-SECRET" not in str(caught.value)
    assert len(store.deletes) == 1
