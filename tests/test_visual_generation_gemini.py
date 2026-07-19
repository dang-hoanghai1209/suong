from __future__ import annotations

import base64
import io
from pathlib import Path
from types import SimpleNamespace

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
from tella.visual_generation.providers.gemini import (
    GeminiProviderError,
    GeminiSceneImageProvider,
)
from tella.visual_generation.models import QCDecision, VisualQCResult
from tella.visual_generation.references import REFERENCE_FILES, resolve_reference_catalog
from tella.visual_generation.style_bible import load_style_bible

ROOT = Path(__file__).parents[1]
PLAN = ROOT / "configs" / "visual_quality" / "four_scene_proof_v1.json"
STYLE = ROOT / "configs" / "visual_quality" / "soft_emotional_reference_v1.json"


@pytest.fixture(autouse=True)
def _mock_live_environment(monkeypatch):
    monkeypatch.setenv("TELLA_VISUAL_QUALITY_LIVE", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "unit-test-only")


def _reference_root(tmp_path: Path) -> Path:
    root = tmp_path / "refs"
    root.mkdir()
    for index, (filename, _, _) in enumerate(sorted(set(REFERENCE_FILES.values()))):
        Image.new("RGB", (90, 160), (40 + index, 30, 30)).save(root / filename)
    return root


def _request(tmp_path: Path, scene_index: int = 0):
    scene = load_proof_plan(PLAN).scenes[scene_index]
    style = load_style_bible(STYLE)
    pack = select_references(
        scene, resolve_reference_catalog(_reference_root(tmp_path)), DRY_RUN_CAPABILITIES
    )
    return build_generation_request(
        scene, style, pack, candidate_index=1, attempt=1, seed=None
    )


def _jpeg_response(width: int = 768, height: int = 1376):
    stream = io.BytesIO()
    Image.new("RGB", (width, height), "#554433").save(stream, "JPEG", quality=90)
    image = SimpleNamespace(
        data=base64.b64encode(stream.getvalue()).decode("ascii"), mime_type="image/jpeg"
    )
    return SimpleNamespace(output_image=image)


class RecordingInteractions:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def _provider(interactions: RecordingInteractions, **kwargs):
    client = SimpleNamespace(interactions=interactions)
    return GeminiSceneImageProvider(client_factory=lambda: client, **kwargs)


def _passing_qc():
    return VisualQCResult(
        style_coherence=9,
        character_identity=9,
        scene_meaning=9,
        composition=9,
        natural_interaction=9,
        anatomy=9,
        visual_appeal=9,
        score_source="human_review",
        decision=QCDecision.PASS,
    )


def test_capabilities_are_truthful_and_edit_is_disabled():
    caps = GeminiSceneImageProvider().capabilities()
    assert caps.provider_id == "gemini"
    assert caps.model == "gemini-3.1-flash-image"
    assert caps.supports_reference_images and caps.supports_multiple_references
    assert caps.supports_9_16 and caps.max_reference_images == 10
    assert caps.supports_image_edit is False
    assert caps.supports_seed is False


def test_credentials_detected_without_exposing_value(monkeypatch):
    secret = "unit-test-secret-never-send"
    for name in ("GEMINI_API_KEYS", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    provider = GeminiSceneImageProvider()
    assert provider.credentials_present() is False
    monkeypatch.setenv("GEMINI_API_KEY", secret)
    assert provider.credentials_present() is True
    assert secret not in repr(provider.__dict__)


def test_dedup_preserves_roles_and_scene_reference_strategy(tmp_path):
    plan = load_proof_plan(PLAN)
    catalog = resolve_reference_catalog(_reference_root(tmp_path))
    duplicate = tmp_path / "same-bytes-different-path.png"
    duplicate.write_bytes(catalog["style_anchor"].path.read_bytes())
    catalog["style_anchor"] = catalog["style_anchor"].model_copy(
        update={"path": duplicate.resolve()}
    )
    scene_1 = select_references(plan.scenes[0], catalog, DRY_RUN_CAPABILITIES)
    scene_2 = select_references(plan.scenes[1], catalog, DRY_RUN_CAPABILITIES)
    assert len(scene_1.references) == 1
    assert scene_1.references[0].semantic_roles == [
        "female_identity_anchor",
        "style_anchor",
    ]
    assert [item.role for item in scene_2.references] == [
        "couple_identity_anchor",
        "female_identity_anchor",
    ]
    assert scene_2.references[1].semantic_roles == [
        "female_identity_anchor",
        "style_anchor",
    ]


@pytest.mark.asyncio
async def test_multiple_references_serialize_with_9_16_and_1k(tmp_path):
    interactions = RecordingInteractions(_jpeg_response())
    provider = _provider(interactions)
    request = _request(tmp_path, scene_index=1)
    metadata = await provider.generate_scene(request, tmp_path / "candidate.png")
    call = interactions.calls[0]
    image_parts = [part for part in call["input"] if part["type"] == "image"]
    assert len(image_parts) == 2
    assert all(part["mime_type"] == "image/png" for part in image_parts)
    assert call["response_format"] == {
        "type": "image",
        "mime_type": "image/jpeg",
        "aspect_ratio": "9:16",
        "image_size": "1K",
    }
    assert metadata.requested_aspect_ratio == "9:16"
    assert metadata.requested_resolution == "1K"
    assert (metadata.actual_width, metadata.actual_height) == (768, 1376)
    assert metadata.mime_type == "image/jpeg"
    assert metadata.output_path.suffix == ".jpg"
    assert metadata.reference_roles[1] == ["female_identity_anchor", "style_anchor"]


@pytest.mark.asyncio
async def test_response_bytes_saved_and_metadata_has_no_secret(tmp_path, monkeypatch):
    secret = "must-not-appear-in-metadata"
    monkeypatch.setenv("GEMINI_API_KEY", secret)
    interactions = RecordingInteractions(_jpeg_response(384, 688))
    output = tmp_path / "saved.png"
    metadata = await _provider(interactions).generate_scene(_request(tmp_path), output)
    assert not output.is_file()
    assert metadata.output_path.is_file()
    assert metadata.output_path.suffix == ".jpg"
    assert Image.open(metadata.output_path).size == (384, 688)
    assert metadata.mime_type == "image/jpeg"
    assert secret not in metadata.model_dump_json()
    assert len(metadata.request_hash) == len(metadata.instruction_hash) == 64
    assert metadata.reference_hashes


@pytest.mark.asyncio
async def test_orchestrator_uses_jpg_for_gemini_candidate(tmp_path):
    interactions = RecordingInteractions(_jpeg_response(1080, 1920))
    summary = await render_proof(
        plan_path=PLAN,
        style_path=STYLE,
        reference_root=_reference_root(tmp_path),
        out_root=tmp_path / "out",
        job_id="jpeg-scene",
        dry_run=False,
        provider=_provider(interactions),
        qc_evaluator=lambda _scene, _path: _passing_qc(),
        scene_id="scene_01",
    )
    candidate = (
        tmp_path / "out" / "visual_quality_v1" / "jpeg-scene" / "scene_01" / "candidate_01.jpg"
    )
    assert summary["external_calls_made"] == 1
    assert candidate.is_file()
    assert not candidate.with_suffix(".png").exists()
    assert candidate.with_suffix(".metadata.json").is_file()


@pytest.mark.asyncio
async def test_empty_and_unsupported_image_responses_fail(tmp_path):
    request = _request(tmp_path)
    empty = _provider(RecordingInteractions(SimpleNamespace(output_image=None)))
    with pytest.raises(GeminiProviderError, match="stage=no_image_found_in_response"):
        await empty.generate_scene(request, tmp_path / "empty.png")
    bad = SimpleNamespace(
        output_image=SimpleNamespace(
            data=base64.b64encode(b"not-image").decode("ascii"), mime_type="image/png"
        )
    )
    with pytest.raises(GeminiProviderError, match="stage=image_decode"):
        await _provider(RecordingInteractions(bad)).generate_scene(
            request, tmp_path / "bad.png"
        )


@pytest.mark.asyncio
async def test_authentication_error_is_clear_and_redacted(tmp_path, monkeypatch):
    secret = "secret-in-provider-error"
    monkeypatch.setenv("GEMINI_API_KEY", secret)
    provider = _provider(
        RecordingInteractions(error=RuntimeError(f"401 API_KEY_INVALID {secret}"))
    )
    with pytest.raises(GeminiProviderError, match="stage=credential_authentication") as error:
        await provider.generate_scene(_request(tmp_path), tmp_path / "auth.png")
    assert secret not in str(error.value)
    assert error.value.exception_class == "RuntimeError"


@pytest.mark.asyncio
async def test_provider_error_preserves_class_stage_and_safe_message(tmp_path, monkeypatch):
    class FakeProviderAPIError(Exception):
        pass

    secret = "provider-secret-value"
    monkeypatch.setenv("GEMINI_API_KEY", secret)
    provider = _provider(
        RecordingInteractions(
            error=FakeProviderAPIError(
                f"503 upstream failed api_key={secret} body=QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo="
            )
        )
    )
    with pytest.raises(GeminiProviderError) as raised:
        await provider.generate_scene(_request(tmp_path), tmp_path / "failure.png")
    error = raised.value
    assert error.exception_class == "FakeProviderAPIError"
    assert error.stage == "api_request"
    assert error.request_reached_gemini is True
    assert error.response_received is False
    assert error.image_bytes_present is False
    assert secret not in str(error)
    assert "[REDACTED]" in error.sanitized_message
    assert "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=" not in str(error)


@pytest.mark.asyncio
async def test_missing_credential_blocks_before_client_creation(tmp_path, monkeypatch):
    monkeypatch.setenv("TELLA_VISUAL_QUALITY_LIVE", "1")
    for name in ("GEMINI_API_KEYS", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    called = False

    def client_factory():
        nonlocal called
        called = True
        raise AssertionError("network boundary reached")

    with pytest.raises(RuntimeError, match="CREDENTIAL_MISSING"):
        await render_proof(
            plan_path=PLAN,
            style_path=STYLE,
            reference_root=_reference_root(tmp_path),
            out_root=tmp_path / "out",
            job_id="missing-credential",
            dry_run=False,
            provider=GeminiSceneImageProvider(client_factory=client_factory),
            scene_id="scene_01",
        )
    assert called is False


@pytest.mark.asyncio
async def test_missing_opt_in_blocks_before_client_creation(tmp_path, monkeypatch):
    monkeypatch.delenv("TELLA_VISUAL_QUALITY_LIVE", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")
    called = False

    def client_factory():
        nonlocal called
        called = True
        raise AssertionError("network boundary reached")

    with pytest.raises(RuntimeError, match="OPT_IN_REQUIRED"):
        await render_proof(
            plan_path=PLAN,
            style_path=STYLE,
            reference_root=_reference_root(tmp_path),
            out_root=tmp_path / "out",
            job_id="missing-opt-in",
            dry_run=False,
            provider=GeminiSceneImageProvider(client_factory=client_factory),
            scene_id="scene_01",
        )
    assert called is False


def test_cli_single_scene_and_explicit_provider(monkeypatch, tmp_path):
    captured = {}

    async def fake_render(**kwargs):
        captured.update(kwargs)
        return {"external_calls_made": 0}

    monkeypatch.setattr("tella.visual_generation.cli.render_proof", fake_render)
    monkeypatch.setenv("TELLA_VISUAL_QUALITY_LIVE", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")
    result = main(
        [
            "render-proof",
            "--plan", str(PLAN),
            "--style", str(STYLE),
            "--reference-root", str(tmp_path),
            "--out", str(tmp_path / "out"),
            "--job-id", "scene-one",
            "--provider", "gemini",
            "--scene", "scene_01",
            "--live",
        ]
    )
    assert result == 0
    assert captured["scene_id"] == "scene_01"
    assert isinstance(captured["provider"], GeminiSceneImageProvider)
    assert captured["dry_run"] is False


def test_cli_live_is_disabled_by_default(tmp_path, capsys):
    result = main(
        [
            "render-proof", "--plan", str(PLAN), "--style", str(STYLE),
            "--reference-root", str(tmp_path), "--job-id", "blocked",
        ]
    )
    assert result == 2
    assert "OPT_IN_REQUIRED" in capsys.readouterr().out
