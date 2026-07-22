from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from tella.topic_production import SceneDataSensitivity
from tella.visual_generation.providers import (
    PollinationsConfig,
    PollinationsError,
    PollinationsErrorCategory,
    PollinationsExecutionRequest,
    PollinationsPrivacyMetadata,
    PollinationsPromptSource,
    PollinationsSceneImageProvider,
    prepare_public_request,
)


def _png(width=576, height=1024):
    stream = io.BytesIO()
    Image.new("RGB", (width, height), "#4a382f").save(stream, format="PNG")
    return stream.getvalue()


class Response:
    def __init__(self, status_code=200, content=None, text=""):
        self.status_code = status_code
        self.content = _png() if content is None else content
        self.text = text


class Sender:
    def __init__(self, response=None, error=None):
        self.response = response or Response()
        self.error = error
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def _source():
    return PollinationsPromptSource(
        scene_meaning="A person chooses a calm evening routine",
        action=["placing a phone out of reach"],
        mood=["quiet", "grounded"],
        setting=["generic warm bedroom at night"],
        generic_character_description="an anonymous adult with a simple silhouette",
        style_description="soft hand-drawn editorial illustration",
        composition=["one clear action", "open upper negative space"],
        negative_constraints=["text", "watermark", "broken anatomy"],
    )


def _request(**updates):
    values = {
        "scene_id": "scene_01",
        "sensitivity": SceneDataSensitivity.PUBLIC_SAFE,
        "prompt_source": _source(),
        "seed": 10101,
        "width": 576,
        "height": 1024,
    }
    values.update(updates)
    return PollinationsExecutionRequest(**values)


def _provider(sender, *, key="sk_unit_test_secret", enabled=True):
    return PollinationsSceneImageProvider(
        config=PollinationsConfig(enabled=enabled, timeout_seconds=17.0),
        api_key_resolver=lambda: key,
        request_sender=sender,
    )


def test_public_safe_request_builds_allowlisted_text_only_payload():
    public = prepare_public_request(_request())
    payload = public.model_dump(mode="json")

    assert "placing a phone out of reach" in public.prompt
    assert "soft hand-drawn editorial illustration" in public.prompt
    assert payload == {
        "prompt": public.prompt,
        "model": "flux",
        "width": 576,
        "height": 1024,
        "seed": 10101,
        "safe": "privacy,secrets",
    }
    assert not ({"image", "references", "path", "token", "authorization"} & payload.keys())


def test_prompt_source_forbids_private_path_or_reference_fields():
    with pytest.raises(ValidationError):
        PollinationsPromptSource(
            **_source().model_dump(),
            private_reference_path=r"D:\private\master.png",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_case",
    [
        _request(sensitivity=SceneDataSensitivity.PRIVATE),
        _request(sensitivity=SceneDataSensitivity.LOCAL_ONLY),
        _request(
            privacy=PollinationsPrivacyMetadata(private_reference_present=True),
        ),
        _request(reference_attachments=(Path(r"D:\private\master.png"),)),
        _request(privacy=PollinationsPrivacyMetadata(source_sheet_requested=True)),
        _request(privacy=PollinationsPrivacyMetadata(private_master_requested=True)),
        _request(privacy=PollinationsPrivacyMetadata(contains_private_data=True)),
    ],
)
async def test_unsafe_requests_are_rejected_before_transport(unsafe_case, tmp_path):
    sender = Sender()
    with pytest.raises(PollinationsError) as raised:
        await _provider(sender).generate_public_scene(unsafe_case, tmp_path / "candidate.bin")
    assert raised.value.category is PollinationsErrorCategory.UNSAFE_REQUEST_BLOCKED_LOCALLY
    assert raised.value.request_reached_provider is False
    assert sender.calls == []


@pytest.mark.asyncio
async def test_mock_success_is_one_sanitized_transport_call_and_atomic_artifact(tmp_path):
    secret = "sk_unit_test_secret"
    sender = Sender()
    metadata = await _provider(sender, key=secret).generate_public_scene(
        _request(), tmp_path / "candidate.bin"
    )

    assert len(sender.calls) == 1
    call = sender.calls[0]
    assert call["url"].startswith("https://gen.pollinations.ai/image/Scene%20meaning%3A")
    assert secret not in call["url"]
    assert call["headers"] == {"Authorization": f"Bearer {secret}"}
    assert call["params"] == {
        "model": "flux",
        "width": 576,
        "height": 1024,
        "seed": 10101,
        "safe": "privacy,secrets",
    }
    assert "key" not in call["params"]
    assert call["timeout_seconds"] == 17.0
    assert "D:\\private" not in json.dumps(call)
    assert metadata.provider == "pollinations"
    assert metadata.reference_hashes == metadata.prepared_references == []
    assert metadata.output_path == (tmp_path / "candidate.png").resolve()
    assert metadata.output_path.read_bytes() == _png()
    assert metadata.actual_width == 576 and metadata.actual_height == 1024
    persisted_metadata = metadata.model_dump_json()
    assert secret not in persisted_metadata
    assert secret not in metadata.request_hash
    assert secret not in metadata.logical_request_hash
    assert secret not in metadata.provider_request_hash


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "category"),
    [
        (TimeoutError("controlled timeout"), PollinationsErrorCategory.TIMEOUT),
        (RuntimeError("controlled provider error"), PollinationsErrorCategory.PROVIDER_ERROR),
    ],
)
async def test_transport_failure_has_no_internal_retry(tmp_path, error, category):
    sender = Sender(error=error)
    with pytest.raises(PollinationsError) as raised:
        await _provider(sender).generate_public_scene(_request(), tmp_path / "candidate.bin")
    assert raised.value.category is category
    assert len(sender.calls) == 1
    assert not list(tmp_path.glob("candidate.*"))


@pytest.mark.asyncio
async def test_transport_error_does_not_retain_api_key_in_error_chain(tmp_path):
    secret = "sk_error_chain_secret"
    sender = Sender(error=RuntimeError(f"Authorization: Bearer {secret}"))

    with pytest.raises(PollinationsError) as raised:
        await _provider(sender, key=secret).generate_public_scene(
            _request(), tmp_path / "candidate.bin"
        )

    assert secret not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert len(sender.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"", b"not an image"])
async def test_empty_or_malformed_image_fails_closed(tmp_path, content):
    sender = Sender(Response(content=content))
    with pytest.raises(PollinationsError) as raised:
        await _provider(sender).generate_public_scene(_request(), tmp_path / "candidate.bin")
    assert raised.value.category is PollinationsErrorCategory.MALFORMED_RESPONSE
    assert len(sender.calls) == 1
    assert not list(tmp_path.glob("candidate.*"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "category"),
    [
        (400, PollinationsErrorCategory.INVALID_REQUEST),
        (402, PollinationsErrorCategory.QUOTA_OR_CREDIT_EXHAUSTED),
        (429, PollinationsErrorCategory.RATE_LIMITED),
        (502, PollinationsErrorCategory.PROVIDER_UNAVAILABLE),
        (503, PollinationsErrorCategory.PROVIDER_UNAVAILABLE),
        (500, PollinationsErrorCategory.PROVIDER_ERROR),
    ],
)
async def test_http_error_classification_is_narrow_and_single_call(tmp_path, status, category):
    secret = "sk_never_persist_me"
    sender = Sender(Response(status_code=status, content=b"error", text=f"Bearer {secret}"))
    with pytest.raises(PollinationsError) as raised:
        await _provider(sender, key=secret).generate_public_scene(
            _request(), tmp_path / "candidate.bin"
        )
    assert raised.value.category is category
    assert secret not in str(raised.value)
    assert len(sender.calls) == 1


@pytest.mark.asyncio
async def test_disabled_configuration_blocks_before_transport(tmp_path):
    sender = Sender()
    with pytest.raises(PollinationsError) as raised:
        await _provider(sender, enabled=False).generate_public_scene(
            _request(), tmp_path / "candidate.bin"
        )
    assert raised.value.request_reached_provider is False
    assert sender.calls == []


@pytest.mark.asyncio
async def test_existing_candidate_is_not_overwritten_or_submitted(tmp_path):
    target = tmp_path / "candidate.png"
    target.write_bytes(b"prior evidence")
    sender = Sender()
    with pytest.raises(FileExistsError):
        await _provider(sender).generate_public_scene(_request(), tmp_path / "candidate.bin")
    assert sender.calls == []
    assert target.read_bytes() == b"prior evidence"
