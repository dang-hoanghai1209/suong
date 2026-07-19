from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from tella.visual_generation.cli import main
from tella.visual_generation.continuity import select_references
from tella.visual_generation.orchestrator import (
    DRY_RUN_CAPABILITIES,
    load_proof_plan,
    render_proof,
)
from tella.visual_generation.prompt_builder import build_generation_request
from tella.visual_generation.providers.cloudflare_flux import (
    CloudflareFluxError,
    CloudflareFluxSceneImageProvider,
    prepare_reference,
)
from tella.visual_generation.references import REFERENCE_FILES, resolve_reference_catalog
from tella.visual_generation.style_bible import load_style_bible

ROOT = Path(__file__).parents[1]
PLAN = ROOT / "configs" / "visual_quality" / "four_scene_proof_v1.json"
STYLE = ROOT / "configs" / "visual_quality" / "soft_emotional_reference_v1.json"


@pytest.fixture(autouse=True)
def _live(monkeypatch):
    monkeypatch.setenv("TELLA_VISUAL_QUALITY_LIVE", "1")
    monkeypatch.setenv("CF_ACCOUNT_ID", "test-account")
    monkeypatch.setenv("CF_AI_TOKEN", "test-token")


def _refs(tmp_path: Path, *, size=(1080, 1920)) -> Path:
    root = tmp_path / "refs"
    root.mkdir()
    for index, (filename, _, _) in enumerate(sorted(set(REFERENCE_FILES.values()))):
        Image.new("RGB", size, (50 + index, 30, 30)).save(root / filename)
    return root


def _request(tmp_path: Path, scene=0):
    brief = load_proof_plan(PLAN).scenes[scene]
    pack = select_references(
        brief, resolve_reference_catalog(_refs(tmp_path)), DRY_RUN_CAPABILITIES
    )
    return build_generation_request(
        brief,
        load_style_bible(STYLE),
        pack,
        candidate_index=1,
        attempt=1,
        seed=101,
    )


def _png(width=576, height=1024) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (width, height), "#534238").save(stream, "PNG")
    return stream.getvalue()


class FakeResponse:
    def __init__(self, payload=None, *, status=200, text=""):
        self.payload = payload
        self.status_code = status
        self.text = text or json.dumps(payload)
        self.content = self.text.encode()

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class Sender:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _provider(sender, **kwargs):
    return CloudflareFluxSceneImageProvider(
        credential_resolver=lambda: [("account", "token")],
        request_sender=sender,
        **kwargs,
    )


def _success(image=None):
    encoded = base64.b64encode(image or _png()).decode("ascii")
    return FakeResponse({"success": True, "result": {"image": encoded}})


def test_capabilities_and_credentials_are_truthful(monkeypatch):
    provider = CloudflareFluxSceneImageProvider(
        credential_resolver=lambda: [("account", "secret")]
    )
    caps = provider.capabilities()
    assert caps.provider_id == "cloudflare-flux"
    assert caps.model == "@cf/black-forest-labs/flux-2-klein-9b"
    assert caps.supports_reference_images and caps.supports_multiple_references
    assert caps.supports_seed and caps.supports_9_16
    assert caps.max_reference_images == 4
    assert caps.supports_image_edit is False
    assert provider.credentials_present() is True
    assert "secret" not in repr(provider.__dict__)


def test_reference_preparation_is_deterministic_and_preserves_original(tmp_path):
    original = _refs(tmp_path).joinpath("scene_01_style_anchor.png")
    before = original.read_bytes()
    digest = hashlib.sha256(before).hexdigest()
    first = prepare_reference(original, digest, tmp_path / "cache")
    second = prepare_reference(original, digest, tmp_path / "cache")
    assert first == second
    assert original.read_bytes() == before
    assert first["prepared_sha256"] == hashlib.sha256(
        Path(first["prepared_path"]).read_bytes()
    ).hexdigest()
    assert (first["prepared_width"], first["prepared_height"]) == (287, 511)
    assert first["prepared_width"] / first["prepared_height"] == pytest.approx(
        1080 / 1920, abs=0.002
    )
    assert max(first["prepared_width"], first["prepared_height"]) < 512


@pytest.mark.asyncio
async def test_scene_one_uploads_one_multipart_reference_with_image_zero_prompt(tmp_path):
    sender = Sender(_success())
    metadata = await _provider(sender).generate_scene(
        _request(tmp_path), tmp_path / "candidate.png"
    )
    call = sender.calls[0]
    assert list(call["files"]) == ["input_image_0"]
    assert call["files"]["input_image_0"][2] == "image/png"
    assert "Use image 0 as guidance" in call["data"]["prompt"]
    assert call["data"]["width"] == "576"
    assert call["data"]["height"] == "1024"
    assert call["data"]["seed"] == "101"
    assert metadata.reference_roles == [["female_identity_anchor", "style_anchor"]]
    assert len(metadata.prepared_references) == 1


@pytest.mark.asyncio
async def test_base64_image_is_validated_written_and_recorded(tmp_path):
    metadata = await _provider(Sender(_success(_png(600, 1000)))).generate_scene(
        _request(tmp_path), tmp_path / "candidate.bin"
    )
    assert metadata.output_path.suffix == ".png"
    assert metadata.mime_type == "image/png"
    assert (metadata.actual_width, metadata.actual_height) == (600, 1000)
    assert (metadata.requested_width, metadata.requested_height) == (576, 1024)
    assert Image.open(metadata.output_path).size == (600, 1000)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "stage"),
    [
        (FakeResponse({"success": False, "errors": ["bad"]}), "cloudflare_envelope"),
        (FakeResponse({"success": True, "result": {}}), "empty_response"),
        (
            FakeResponse({"success": True, "result": {"image": "not-base64"}}),
            "base64_decode",
        ),
        (FakeResponse(None, status=429, text="daily neuron quota exhausted"), "quota_exceeded"),
    ],
)
async def test_provider_failures_are_staged(response, stage, tmp_path):
    with pytest.raises(CloudflareFluxError) as raised:
        await _provider(Sender(response)).generate_scene(
            _request(tmp_path), tmp_path / "candidate.png"
        )
    assert raised.value.stage == stage


@pytest.mark.asyncio
async def test_transport_error_is_sanitized_without_token_or_payload(tmp_path, monkeypatch):
    secret = "cloudflare-secret-token"
    monkeypatch.setenv("CF_AI_TOKEN", secret)

    async def fail(**_kwargs):
        raise RuntimeError(
            f"Authorization: Bearer {secret} payload=" + "QUJD" * 40
        )

    with pytest.raises(CloudflareFluxError) as raised:
        await _provider(fail).generate_scene(_request(tmp_path), tmp_path / "x.png")
    rendered = str(raised.value)
    assert raised.value.stage == "api_request"
    assert secret not in rendered
    assert "QUJD" * 20 not in rendered
    assert "[REDACTED]" in rendered


@pytest.mark.asyncio
async def test_live_off_blocks_before_network(tmp_path, monkeypatch):
    monkeypatch.delenv("TELLA_VISUAL_QUALITY_LIVE", raising=False)
    sender = Sender(_success())
    with pytest.raises(RuntimeError, match="OPT_IN_REQUIRED"):
        await _provider(sender).generate_scene(_request(tmp_path), tmp_path / "x.png")
    assert sender.calls == []


@pytest.mark.asyncio
async def test_single_scene_render_is_bounded_to_one_call(tmp_path):
    sender = Sender(_success(_png(1080, 1920)))
    summary = await render_proof(
        plan_path=PLAN,
        style_path=STYLE,
        reference_root=_refs(tmp_path),
        out_root=tmp_path / "out",
        job_id="single-cloudflare",
        dry_run=False,
        provider=_provider(sender),
        scene_id="scene_01",
    )
    assert len(sender.calls) == 1
    assert summary["selected_scenes"] == ["scene_01"]
    assert summary["maximum_generation_calls"] == 1


@pytest.mark.asyncio
async def test_default_scene_seed_remains_deterministic(tmp_path):
    sender = Sender(_success(_png(1080, 1920)))
    await render_proof(
        plan_path=PLAN,
        style_path=STYLE,
        reference_root=_refs(tmp_path),
        out_root=tmp_path / "out",
        job_id="default-seed",
        dry_run=False,
        provider=_provider(sender, width=1080, height=1920),
        scene_id="scene_01",
    )
    request = json.loads(
        (tmp_path / "out" / "visual_quality_v1" / "default-seed" / "scene_01" / "request.json").read_text()
    )
    assert request["seed"] == 10101
    assert sender.calls[0]["data"]["seed"] == "10101"


@pytest.mark.asyncio
async def test_explicit_seed_override_reaches_request_and_cloudflare(tmp_path):
    sender = Sender(_success(_png(1080, 1920)))
    await render_proof(
        plan_path=PLAN,
        style_path=STYLE,
        reference_root=_refs(tmp_path),
        out_root=tmp_path / "out",
        job_id="override-seed",
        dry_run=False,
        provider=_provider(sender, width=1080, height=1920),
        scene_id="scene_01",
        seed_override=27183,
    )
    request = json.loads(
        (tmp_path / "out" / "visual_quality_v1" / "override-seed" / "scene_01" / "request.json").read_text()
    )
    assert request["seed"] == 27183
    assert sender.calls[0]["data"]["seed"] == "27183"


@pytest.mark.asyncio
async def test_seed_override_dry_run_is_network_free(tmp_path):
    sender = Sender(_success())
    summary = await render_proof(
        plan_path=PLAN,
        style_path=STYLE,
        reference_root=_refs(tmp_path),
        out_root=tmp_path / "out",
        job_id="dry-override-seed",
        dry_run=True,
        provider=_provider(sender),
        scene_id="scene_01",
        seed_override=27183,
    )
    request = json.loads(
        (tmp_path / "out" / "visual_quality_v1" / "dry-override-seed" / "scene_01" / "request.json").read_text()
    )
    assert request["seed"] == 27183
    assert sender.calls == []
    assert summary["external_calls_made"] == 0


@pytest.mark.asyncio
async def test_provider_neutral_dry_run_makes_no_cloudflare_call(tmp_path):
    sender = Sender(_success())
    summary = await render_proof(
        plan_path=PLAN,
        style_path=STYLE,
        reference_root=_refs(tmp_path),
        out_root=tmp_path / "out",
        job_id="dry-cloudflare",
        dry_run=True,
        provider=_provider(sender),
        scene_id="scene_01",
    )
    assert sender.calls == []
    assert summary["external_calls_made"] == 0


def test_cli_selects_cloudflare_flux_for_scene_one(monkeypatch, tmp_path):
    captured = {}

    async def fake_render(**kwargs):
        captured.update(kwargs)
        return {"external_calls_made": 0}

    monkeypatch.setattr("tella.visual_generation.cli.render_proof", fake_render)
    result = main(
        [
            "render-proof",
            "--plan", str(PLAN),
            "--style", str(STYLE),
            "--reference-root", str(tmp_path),
            "--out", str(tmp_path / "out"),
            "--job-id", "cloudflare-cli",
            "--provider", "cloudflare-flux",
            "--model", "@cf/black-forest-labs/flux-2-klein-9b",
            "--scene", "scene_01",
            "--live",
        ]
    )
    assert result == 0
    assert captured["scene_id"] == "scene_01"
    assert isinstance(captured["provider"], CloudflareFluxSceneImageProvider)
