from __future__ import annotations

import hashlib
import io
import json
import socket
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlunsplit

import pytest
from PIL import Image
from pydantic import SecretStr, ValidationError

from tella.media.bfl_flux2_provider import (
    BFLFlux2Config,
    BFLFlux2ReferenceProvider,
    BFLProviderError,
    BFLReferenceInput,
    PINNED_ENDPOINT,
    PREVIEW_ENDPOINT,
    build_bfl_reference_provider,
    translate_positive_prompt,
    validate_accounting_invariants,
)
from tella.media.image_provider import CloudflareImageProvider, get_image_provider
from tella.media.image_provider_contract import (
    ReferenceConditionedImageRequest,
    ReferenceConditioningConfig,
    ReferenceSheetManifest,
)
from tella.media.temporary_reference_store import (
    TemporaryReferenceObject,
    TemporaryReferenceStoreCapabilities,
    URLFetchResult,
)


@pytest.fixture(autouse=True)
def _block_real_sockets(monkeypatch):
    calls = 0
    def forbidden(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("real sockets are forbidden in BFL provider tests")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    yield
    assert calls == 0


def _signed_url(host: str, path: str, token: str) -> str:
    return urlunsplit(("https", host, "/" + path, urlencode({"signature": token}), ""))


def _image(fmt="PNG", size=(64, 96), color="#218c89") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format=fmt)
    return out.getvalue()


def _reference(tmp_path: Path, *, approved=True, tampered=False):
    content = _image()
    digest = hashlib.sha256(content).hexdigest()
    path = tmp_path / "character.png"
    path.write_bytes(content)
    manifest = ReferenceSheetManifest(
        version=1, image_path=path, image_sha256=digest,
        character_fingerprint="a" * 64, provenance="human reviewed",
        views=("front_face", "three_quarter", "side_view", "full_body"),
        anatomy_qc_passed=True, style_qc_passed=True, human_approved=approved,
        approval_record="approval-1" if approved else "",
    )
    request = ReferenceConditionedImageRequest(
        prompt="No duplicate person and no generated text.",
        canonical_reference_image_path=path, reference_image_sha256=digest,
        reference_sheet_version=1, character_fingerprint="a" * 64,
        required_view_or_pose="three-quarter seated view",
        scene_action="writing in one open notebook",
        composition_family="asymmetric desk composition",
        width=64, height=96, seed=42,
        conditioning=ReferenceConditioningConfig(strength=0.7),
    )
    return BFLReferenceInput(
        request=request, manifest=manifest,
        approval_record_sha256=hashlib.sha256(
            manifest.approval_record.encode("utf-8")
        ).hexdigest(),
        content=(content + b"tampered" if tampered else content), content_type="image/png",
    )


class Store:
    def __init__(self, *, roundtrip=None, delete=True):
        self.objects = {}
        self.roundtrip = roundtrip
        self.delete_result = delete
        self.uploads = 0
        self.deletes = 0

    def capabilities(self):
        return TemporaryReferenceStoreCapabilities(
            provider_id="fake_private_store", private_objects_only=True,
            supports_presigned_get=True, supports_retrieve=True,
            supports_delete=True, supports_exists=True, preserves_exact_bytes=True,
            minimum_ttl_seconds=60, maximum_ttl_seconds=3600,
        )

    async def upload_immutable(self, **kwargs):
        self.uploads += 1
        self.objects[kwargs["object_key"]] = kwargs["content"]
        now = datetime.now(UTC)
        return TemporaryReferenceObject(
            store_provider_id="fake_private_store", storage_namespace="private",
            object_key=kwargs["object_key"], source_sha256=kwargs["source_sha256"],
            stored_byte_size=len(kwargs["content"]), content_type=kwargs["content_type"],
            created_at=now, expires_at=now + timedelta(seconds=kwargs["ttl_seconds"]),
            read_url=SecretStr(
                _signed_url("private.example", kwargs["object_key"], "SIGNED-SECRET")
            ),
        )

    async def retrieve_via_read_url(self, obj, *, timeout_seconds, maximum_bytes):
        content = self.roundtrip if self.roundtrip is not None else self.objects[obj.object_key]
        return URLFetchResult(status_code=200, content=content, content_type=obj.content_type)

    async def exists(self, object_key):
        return object_key in self.objects

    async def delete(self, obj):
        self.deletes += 1
        return self.delete_result

    async def cleanup_stale(self, *, prefix, older_than):
        return []


class Transport:
    def __init__(self, *, polls=None, output=None, output_mime="image/png", create_error=None,
                 poll_error=None, download_error=None):
        self.polls = list(polls or [{"status": "Ready", "result": {
            "sample": _signed_url("delivery.example", "result", "OUTPUT-SECRET")}}])
        self.output = output if output is not None else _image()
        self.output_mime = output_mime
        self.create_error = create_error
        self.poll_error = poll_error
        self.download_error = download_error
        self.create_calls = []
        self.poll_calls = 0
        self.download_calls = 0

    async def create(self, endpoint_url, **kwargs):
        self.create_calls.append((endpoint_url, kwargs))
        if self.create_error:
            raise self.create_error
        return {"id": "request-1", "polling_url": _signed_url(
            "poll.example", "1", "POLL-SECRET"
        )}

    async def poll(self, polling_url, **kwargs):
        self.poll_calls += 1
        if self.poll_error:
            raise self.poll_error
        return self.polls.pop(0)

    async def download(self, result_url, **kwargs):
        self.download_calls += 1
        if self.download_error:
            raise self.download_error
        return URLFetchResult(
            status_code=200, content=self.output, content_type=self.output_mime
        )


def _provider(*, store=None, transport=None, config=None, accounting=None, api_key=True,
              manifest=None):
    return BFLFlux2ReferenceProvider(
        config=config or BFLFlux2Config(polling_interval_seconds=0.001),
        reference_store=store, transport=transport or Transport(),
        api_key=SecretStr("BFL-SECRET") if api_key else None,
        reference_manifest=manifest, accounting=accounting,
    )


def test_truthful_capabilities_and_cloudflare_is_unchanged():
    disabled = _provider(store=None).capabilities()
    assert disabled.supports_reference_conditioning is False
    assert disabled.supports_character_identity_anchor is False
    enabled = _provider(store=Store()).capabilities()
    assert enabled.provider_id == "bfl_flux2_reference"
    assert enabled.supports_text_to_image is True
    assert enabled.supports_reference_conditioning is True
    assert enabled.supports_image_to_image is True
    assert enabled.supports_structural_conditioning is False
    assert enabled.supports_seed is True
    assert enabled.supports_negative_prompt is False
    assert enabled.supports_character_identity_anchor is False
    assert enabled.identity_anchor_verification == "per_request_verified"
    assert enabled.max_reference_images == 8
    assert enabled.accepted_reference_mime_types == ("image/png", "image/jpeg", "image/webp")
    assert enabled.provider_retry_control == "caller_bounded"
    assert CloudflareImageProvider().capabilities().supports_reference_conditioning is False


def test_fixed_default_preview_opt_in_and_factory_is_explicit():
    assert BFLFlux2Config().endpoint == PINNED_ENDPOINT
    with pytest.raises(ValidationError, match="explicit opt-in"):
        BFLFlux2Config(endpoint=PREVIEW_ENDPOINT)
    assert BFLFlux2Config(endpoint=PREVIEW_ENDPOINT, allow_preview=True).endpoint == PREVIEW_ENDPOINT
    provider = get_image_provider(
        "bfl_flux2_reference", config=BFLFlux2Config(), reference_store=Store(),
        transport=Transport(), api_key=SecretStr("secret"),
    )
    assert provider.provider_name == "bfl_flux2_reference"
    with pytest.raises(ValueError, match="accepts no"):
        get_image_provider("cloudflare", unexpected=True)
    with pytest.raises(RuntimeError, match="not exposed through the normal CLI"):
        get_image_provider("bfl_flux2_reference")


def test_ttl_timeout_invariant_boundaries_and_invalid_values():
    exact = BFLFlux2Config(reference_url_ttl_seconds=320)
    assert exact.minimum_reference_url_ttl_seconds == 320
    with pytest.raises(ValidationError, match="TTL is shorter"):
        BFLFlux2Config(reference_url_ttl_seconds=319)
    assert BFLFlux2Config(reference_url_ttl_seconds=3600).reference_url_ttl_seconds == 3600
    for values in (
        {"total_timeout_seconds": -1},
        {"read_timeout_seconds": 121},
        {"connect_timeout_seconds": 121},
        {"reference_url_safety_margin_seconds": 601},
    ):
        with pytest.raises(ValidationError):
            BFLFlux2Config(**values)


def test_controlled_builder_checks_bfl_before_constructing_any_client():
    constructions = []
    def store_factory():
        constructions.append("store")
        return Store()
    def transport_factory():
        constructions.append("transport")
        return Transport()
    with pytest.raises(BFLProviderError, match="credential"):
        build_bfl_reference_provider(
            config=BFLFlux2Config(), api_key=None,
            reference_store_factory=store_factory,
            transport_factory=transport_factory,
        )
    assert constructions == []


def test_provider_credential_namespaces_do_not_cross_satisfy(monkeypatch):
    for name in ("CF_ACCOUNTS", "CF_ACCOUNT_ID", "CF_AI_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    for name in ("BFL_API_KEY", "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID",
                 "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"):
        monkeypatch.setenv(name, "unrelated-present-value")
    assert get_image_provider("cloudflare").is_configured() is False
    monkeypatch.setenv("CF_ACCOUNT_ID", "cf-account")
    monkeypatch.setenv("CF_AI_TOKEN", "cf-token")
    constructions = []
    with pytest.raises(BFLProviderError, match="BFL credential"):
        build_bfl_reference_provider(
            config=BFLFlux2Config(), api_key=None,
            reference_store_factory=lambda: constructions.append("r2"),
            transport_factory=lambda: constructions.append("http"),
        )
    assert constructions == []


def test_positive_prompt_translation_has_no_native_negative_semantics(tmp_path):
    request = _reference(tmp_path).request
    prompt = translate_positive_prompt(request)
    assert "exactly one visible character" in prompt
    assert "blank symbol-only surfaces" in prompt
    assert "no duplicate person" not in prompt.lower()
    assert "no generated text" not in prompt.lower()
    assert "Reference fidelity priority: 0.70" in prompt


@pytest.mark.asyncio
async def test_ready_success_exact_url_private_metadata_atomic_write_and_accounting(tmp_path):
    ref = _reference(tmp_path)
    store = Store()
    transport = Transport()
    counts = {}
    out = tmp_path / "output.png"
    result = await _provider(store=store, transport=transport, accounting=counts).generate_with_references(
        references=[ref], out_path=out
    )
    payload = transport.create_calls[0][1]["payload"]
    assert payload["input_image"].endswith("?signature=SIGNED-SECRET")
    assert "negative_prompt" not in payload
    assert transport.create_calls[0][1]["maximum_response_bytes"] == 1_000_000
    assert transport.download_calls == 1
    assert out.read_bytes() == transport.output
    assert result.used_reference_conditioning is True
    serialized = repr(result.metadata)
    assert "SIGNED-SECRET" not in serialized
    assert "POLL-SECRET" not in serialized
    assert "OUTPUT-SECRET" not in serialized
    assert result.metadata["endpoint"] == PINNED_ENDPOINT
    assert result.metadata["roundtrip_verified"] is True
    assert result.metadata["character_identity_anchor_verified"] is True
    assert result.metadata["cleanup_required"] is False
    assert counts == {
        "local_request_validations": 1,
        "temporary_store_upload_attempts": 1,
        "temporary_store_presign_operations": 1,
        "temporary_store_verification_downloads": 1,
        "application_image_submissions": 1,
        "bfl_create_transport_attempts": 1,
        "bfl_poll_attempts": 1,
        "bfl_output_download_attempts": 1,
        "provider_generation_successes": 1,
        "reference_cleanup_attempts": 1,
        "reference_cleanup_successes": 1,
    }
    assert store.deletes == 1
    assert result.metadata["accounting"]["reference_cleanup_successes"] == 1


@pytest.mark.asyncio
async def test_pending_then_ready_polls_without_new_submission(tmp_path):
    transport = Transport(polls=[
        {"status": "Pending"},
        {"status": "Ready", "result": {"sample": "https://delivery.example/out"}},
    ])
    counts = {}
    await _provider(store=Store(), transport=transport, accounting=counts).generate_with_references(
        references=[_reference(tmp_path)], out_path=tmp_path / "out.png"
    )
    assert counts["application_image_submissions"] == 1
    assert counts["bfl_create_transport_attempts"] == 1
    assert counts["bfl_poll_attempts"] == 2


@pytest.mark.asyncio
async def test_eight_references_supported_and_ninth_rejected_before_upload(tmp_path):
    ref = _reference(tmp_path)
    store = Store()
    transport = Transport()
    result = await _provider(store=store, transport=transport).generate_with_references(
        references=[ref] * 8, out_path=tmp_path / "eight.png"
    )
    payload = transport.create_calls[0][1]["payload"]
    assert payload["input_image"]
    assert payload["input_image_8"]
    assert result.metadata["reference_count"] == 8
    store2 = Store()
    with pytest.raises(ValueError, match="between one and eight"):
        await _provider(store=store2).generate_with_references(
            references=[ref] * 9, out_path=tmp_path / "nine.png"
        )
    assert store2.uploads == 0


@pytest.mark.asyncio
async def test_total_reference_bytes_and_output_megapixels_fail_before_upload(tmp_path):
    ref = _reference(tmp_path)
    padded = ref.content.ljust(700, b"x")
    digest = hashlib.sha256(padded).hexdigest()
    manifest = ref.manifest.model_copy(update={"image_sha256": digest})
    request = ref.request.model_copy(update={"reference_image_sha256": digest})
    padded_ref = BFLReferenceInput(
        request=request, manifest=manifest,
        approval_record_sha256=ref.approval_record_sha256,
        content=padded, content_type="image/png",
    )
    store = Store()
    with pytest.raises(ValueError, match="total local byte-size"):
        await _provider(
            store=store,
            config=BFLFlux2Config(max_reference_bytes=1024, max_total_reference_bytes=1024),
        ).generate_with_references(
            references=[padded_ref, padded_ref], out_path=tmp_path / "never.png"
        )
    large_request = ref.request.model_copy(update={"width": 2048, "height": 2048})
    large_ref = BFLReferenceInput(
        request=large_request, manifest=ref.manifest,
        approval_record_sha256=ref.approval_record_sha256,
        content=ref.content, content_type=ref.content_type,
    )
    with pytest.raises(ValueError, match="megapixel"):
        await _provider(store=store).generate_with_references(
            references=[large_ref], out_path=tmp_path / "never-large.png"
        )
    assert store.uploads == 0


@pytest.mark.asyncio
async def test_missing_unapproved_tampered_and_missing_credential_fail_pre_submission(tmp_path):
    store = Store()
    for references, provider, message in [
        ([], _provider(store=store), "between one and eight"),
        ([_reference(tmp_path, approved=False)], _provider(store=store), "human-approved"),
        ([_reference(tmp_path, tampered=True)], _provider(store=store), "tampered"),
        ([_reference(tmp_path)], _provider(store=store, api_key=False), "credential"),
    ]:
        with pytest.raises((ValueError, BFLProviderError), match=message):
            await provider.generate_with_references(
                references=references, out_path=tmp_path / "never.png"
            )
    assert store.uploads == 0


@pytest.mark.asyncio
async def test_approval_hash_and_cross_reference_scene_binding_fail_locally(tmp_path):
    ref = _reference(tmp_path)
    bad_approval = BFLReferenceInput(
        request=ref.request, manifest=ref.manifest,
        approval_record_sha256="b" * 64, content=ref.content,
        content_type=ref.content_type,
    )
    store = Store()
    with pytest.raises(ValueError, match="approval-record SHA256"):
        await _provider(store=store).generate_with_references(
            references=[bad_approval], out_path=tmp_path / "never.png"
        )
    changed = ref.request.model_copy(update={"scene_action": "a different action"})
    other = BFLReferenceInput(
        request=changed, manifest=ref.manifest,
        approval_record_sha256=ref.approval_record_sha256,
        content=ref.content, content_type=ref.content_type,
    )
    with pytest.raises(ValueError, match="same scene request"):
        await _provider(store=store).generate_with_references(
            references=[ref, other], out_path=tmp_path / "never-2.png"
        )
    assert store.uploads == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "category"),
    [
        ("Request Moderated", "moderation"),
        ("Content Moderated", "moderation"),
        ("Error", "provider_failure"),
        ("Task not found", "provider_failure"),
        ("Unexpected", "malformed_response"),
    ],
)
async def test_terminal_and_malformed_states_cleanup_once(tmp_path, status, category):
    store = Store()
    transport = Transport(polls=[{"status": status}])
    with pytest.raises(BFLProviderError) as caught:
        await _provider(store=store, transport=transport).generate_with_references(
            references=[_reference(tmp_path)], out_path=tmp_path / "never.png"
        )
    assert caught.value.category == category
    assert store.deletes == 1


class HTTP429(Exception):
    status_code = 429


@pytest.mark.asyncio
async def test_http_429_no_retry_no_fallback_and_safe_exception(tmp_path, monkeypatch):
    calls = 0
    async def forbidden(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("Cloudflare fallback forbidden")
    monkeypatch.setattr("tella.media.ai_image.generate_image", forbidden)
    store = Store()
    transport = Transport(create_error=HTTP429(_signed_url("secret.invalid", "x", "LEAK")))
    with pytest.raises(BFLProviderError, match="HTTP 429") as caught:
        await _provider(store=store, transport=transport).generate_with_references(
            references=[_reference(tmp_path)], out_path=tmp_path / "never.png"
        )
    assert len(transport.create_calls) == 1
    assert store.deletes == 1
    assert calls == 0
    assert "LEAK" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transport", "category"),
    [
        (Transport(create_error=RuntimeError("unsafe create detail")), "create_failure"),
        (Transport(poll_error=RuntimeError("unsafe poll detail")), "poll_failure"),
    ],
)
async def test_transport_failures_are_single_submission_and_cleanup(tmp_path, transport, category):
    store = Store()
    counts = {}
    with pytest.raises(BFLProviderError) as caught:
        await _provider(
            store=store, transport=transport, accounting=counts
        ).generate_with_references(
            references=[_reference(tmp_path)], out_path=tmp_path / "never.png"
        )
    assert caught.value.category == category
    assert counts["application_image_submissions"] == 1
    assert counts["bfl_create_transport_attempts"] == 1
    assert store.deletes == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("create_response", "message"),
    [
        ({"polling_url": "https://poll.example/1"}, "request ID"),
        ({"id": "request-1"}, "polling URL"),
        ({"id": "request-1", "polling_url": "not-a-url"}, "polling URL"),
    ],
)
async def test_malformed_create_response_fails_and_cleans(tmp_path, create_response, message):
    class MalformedTransport(Transport):
        async def create(self, endpoint_url, **kwargs):
            self.create_calls.append((endpoint_url, kwargs))
            return create_response
    store = Store()
    with pytest.raises(BFLProviderError, match=message):
        await _provider(store=store, transport=MalformedTransport()).generate_with_references(
            references=[_reference(tmp_path)], out_path=tmp_path / "never.png"
        )
    assert store.deletes == 1


@pytest.mark.asyncio
async def test_poll_timeout_missing_result_and_download_failure(tmp_path):
    cases = [
        (Transport(polls=[{"status": "Pending"}]),
         BFLFlux2Config(maximum_polls=1, polling_interval_seconds=0.001), "timeout"),
        (Transport(polls=[{"status": "Ready", "result": {}}]),
         BFLFlux2Config(polling_interval_seconds=0.001), "missing_result"),
        (Transport(download_error=RuntimeError("expired signed URL token SECRET")),
         BFLFlux2Config(polling_interval_seconds=0.001), "download_failure"),
    ]
    for index, (transport, config, category) in enumerate(cases):
        store = Store()
        with pytest.raises(BFLProviderError) as caught:
            await _provider(store=store, transport=transport, config=config).generate_with_references(
                references=[_reference(tmp_path)], out_path=tmp_path / f"never-{index}.png"
            )
        assert caught.value.category == category
        assert "SECRET" not in str(caught.value)
        assert store.deletes == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("output", "mime", "message"),
    [
        (_image(), "image/jpeg", "MIME"),
        (b"not-an-image", "image/png", "decoding"),
        (_image(size=(65, 96)), "image/png", "dimensions"),
    ],
)
async def test_output_validation_failures_do_not_write(tmp_path, output, mime, message):
    out = tmp_path / "never.png"
    store = Store()
    counts = {}
    with pytest.raises(BFLProviderError, match=message):
        await _provider(
            store=store, transport=Transport(output=output, output_mime=mime), accounting=counts
        ).generate_with_references(references=[_reference(tmp_path)], out_path=out)
    assert not out.exists()
    assert counts["local_output_validation_failures"] == 1
    assert store.deletes == 1


@pytest.mark.asyncio
async def test_output_byte_limit_fails_locally(tmp_path):
    output = _image() + (b"x" * 2000)
    config = BFLFlux2Config(
        polling_interval_seconds=0.001, max_output_bytes=1024
    )
    with pytest.raises(BFLProviderError, match="byte-size"):
        await _provider(
            store=Store(), transport=Transport(output=output), config=config
        ).generate_with_references(
            references=[_reference(tmp_path)], out_path=tmp_path / "never.png"
        )


@pytest.mark.asyncio
async def test_roundtrip_mismatch_has_zero_bfl_submission_and_cleanup(tmp_path):
    store = Store(roundtrip=b"wrong")
    counts = {}
    transport = Transport()
    with pytest.raises(ValueError, match="roundtrip SHA256"):
        await _provider(store=store, transport=transport, accounting=counts).generate_with_references(
            references=[_reference(tmp_path)], out_path=tmp_path / "never.png"
        )
    assert transport.create_calls == []
    assert "application_image_submissions" not in counts
    assert store.deletes == 1


@pytest.mark.asyncio
async def test_cleanup_failure_is_reported_without_resubmission(tmp_path):
    store = Store(delete=False)
    transport = Transport()
    result = await _provider(store=store, transport=transport).generate_with_references(
        references=[_reference(tmp_path)], out_path=tmp_path / "out.png"
    )
    assert result.metadata["cleanup_required"] is True
    assert len(transport.create_calls) == 1
    assert store.deletes == 1


@pytest.mark.asyncio
async def test_multiple_reference_partial_verification_failure_cleans_each_once(tmp_path):
    class SecondVerificationFails(Store):
        def __init__(self):
            super().__init__()
            self.retrievals = 0
        async def retrieve_via_read_url(self, obj, *, timeout_seconds, maximum_bytes):
            self.retrievals += 1
            if self.retrievals == 2:
                return URLFetchResult(
                    status_code=200, content=b"mismatch", content_type=obj.content_type
                )
            return await super().retrieve_via_read_url(
                obj, timeout_seconds=timeout_seconds, maximum_bytes=maximum_bytes
            )
    store = SecondVerificationFails()
    transport = Transport()
    with pytest.raises(ValueError, match="roundtrip SHA256"):
        await _provider(store=store, transport=transport).generate_with_references(
            references=[_reference(tmp_path)] * 2, out_path=tmp_path / "never.png"
        )
    assert store.uploads == 2
    assert store.deletes == 2
    assert transport.create_calls == []


@pytest.mark.asyncio
async def test_caller_cancellation_cleans_without_resubmission(tmp_path):
    class CancelTransport(Transport):
        async def poll(self, polling_url, **kwargs):
            self.poll_calls += 1
            raise asyncio.CancelledError()
    import asyncio
    store = Store()
    transport = CancelTransport()
    with pytest.raises(asyncio.CancelledError):
        await _provider(store=store, transport=transport).generate_with_references(
            references=[_reference(tmp_path)], out_path=tmp_path / "never.png"
        )
    assert store.deletes == 1
    assert len(transport.create_calls) == 1


@pytest.mark.asyncio
async def test_atomic_write_failure_is_safe_and_cleans(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("path with signed query signature=DO-NOT-LEAK")
    monkeypatch.setattr("tella.media.bfl_flux2_provider.atomic_write_bytes", fail)
    store = Store()
    counts = {}
    with pytest.raises(BFLProviderError, match="atomic output write failed") as caught:
        await _provider(store=store, accounting=counts).generate_with_references(
            references=[_reference(tmp_path)], out_path=tmp_path / "never.png"
        )
    assert "DO-NOT-LEAK" not in str(caught.value)
    assert counts["local_output_write_failures"] == 1
    assert store.deletes == 1


def test_secret_url_redacted_from_dataclass_json_and_accounting_diagnostics(tmp_path):
    ref = _reference(tmp_path)
    now = datetime.now(UTC)
    obj = TemporaryReferenceObject(
        store_provider_id="fake", storage_namespace="private", object_key="key",
        source_sha256=ref.manifest.image_sha256, stored_byte_size=len(ref.content),
        content_type="image/png", created_at=now, expires_at=now + timedelta(minutes=5),
        read_url=SecretStr(_signed_url("private.example", "key", "SERIALIZE-SECRET")),
    )
    snapshots = [repr(obj), str(obj), json.dumps(asdict(obj), default=str),
                 json.dumps(obj.diagnostic(), default=str)]
    assert all("SERIALIZE-SECRET" not in snapshot for snapshot in snapshots)
    assert all("signature=" not in snapshot for snapshot in snapshots)


def test_accounting_invariants_fail_closed():
    with pytest.raises(RuntimeError, match="invalid counter"):
        validate_accounting_invariants({"application_image_submissions": -1})
    with pytest.raises(RuntimeError, match="presign"):
        validate_accounting_invariants({
            "temporary_store_upload_attempts": 0,
            "temporary_store_presign_operations": 1,
        })
    with pytest.raises(RuntimeError, match="create attempts"):
        validate_accounting_invariants({
            "application_image_submissions": 1, "bfl_create_transport_attempts": 0
        })


def test_no_secret_or_signed_url_in_config_serialization():
    provider = _provider(store=Store())
    assert "BFL-SECRET" not in repr(provider.__dict__)
    assert "signature=" not in repr(BFLFlux2Config().model_dump())
