from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image

from tella.media.image_provider import CloudflareImageProvider
from tella.media.image_provider_contract import (
    CharacterIdentityMode,
    ImageProviderCapabilities,
    ReferenceConditionedImageRequest,
    ReferenceConditioningConfig,
    ReferenceIdentityObservation,
    ReferenceSheetManifest,
    ReferenceVisualGates,
    aggregate_reference_visual_gates,
    evaluate_reference_visual_gates,
    submit_reference_conditioned,
    validate_identity_mode,
    validate_reference_request,
)


def _capabilities() -> ImageProviderCapabilities:
    return ImageProviderCapabilities(
        provider_id="mock_reference",
        supports_text_to_image=True,
        supports_reference_conditioning=True,
        supports_image_to_image=True,
        supports_structural_conditioning=False,
        supports_seed=True,
        supports_negative_prompt=True,
        max_prompt_utf8_bytes=1850,
        max_reference_images=1,
        accepted_reference_mime_types=("image/png",),
        supports_character_identity_anchor=True,
        identity_anchor_verification="provider_static",
        provider_retry_control="caller_bounded",
    )


def _reference(tmp_path: Path) -> tuple[Path, bytes, str]:
    path = tmp_path / "character_v1.png"
    Image.new("RGB", (64, 96), "#218c89").save(path)
    content = path.read_bytes()
    return path, content, hashlib.sha256(content).hexdigest()


def _request(path: Path, digest: str) -> ReferenceConditionedImageRequest:
    return ReferenceConditionedImageRequest(
        prompt="One character prepares a bag.",
        canonical_reference_image_path=path,
        reference_image_sha256=digest,
        reference_sheet_version=1,
        character_fingerprint="a" * 64,
        required_view_or_pose="three-quarter standing view",
        scene_action="placing one bottle into one open bag",
        composition_family="angled preparation view",
        width=768,
        height=1344,
        seed=73,
        conditioning=ReferenceConditioningConfig(strength=0.72),
    )


def _manifest(path: Path, digest: str) -> ReferenceSheetManifest:
    return ReferenceSheetManifest(
        version=1,
        image_path=path,
        image_sha256=digest,
        character_fingerprint="a" * 64,
        provenance="human-reviewed project reference",
        views=("front_face", "three_quarter", "side_view", "full_body"),
        anatomy_qc_passed=True,
        style_qc_passed=True,
        human_approved=True,
        approval_record="review-001",
    )


def test_cloudflare_capabilities_are_truthful_and_text_only():
    provider = CloudflareImageProvider()
    caps = provider.capabilities()
    assert caps.provider_id == "cloudflare"
    assert caps.supports_text_to_image is True
    assert caps.supports_reference_conditioning is False
    assert caps.supports_character_identity_anchor is False
    assert caps.supports_image_to_image is False
    assert caps.supports_structural_conditioning is False
    assert caps.supports_seed is True
    assert caps.max_reference_images == 0
    assert caps.accepted_reference_mime_types == ()
    assert caps.provider_retry_control == "provider_managed"


@pytest.mark.asyncio
async def test_cloudflare_prompt_byte_limit_fails_before_adapter_call(tmp_path, monkeypatch):
    calls = 0

    async def forbidden(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("provider must not be called")

    monkeypatch.setattr("tella.media.image_provider.ai_image.generate_image", forbidden)
    with pytest.raises(ValueError, match="UTF-8 byte limit"):
        await CloudflareImageProvider().generate_text_image(
            prompt="đ" * 1100, negative_prompt="", aspect="9:16", seed=1,
            out_path=tmp_path / "out.png",
        )
    assert calls == 0


@pytest.mark.asyncio
async def test_cloudflare_reference_call_fails_without_text_downgrade(tmp_path):
    provider = CloudflareImageProvider()
    reference, _, _ = _reference(tmp_path)
    with pytest.raises(RuntimeError, match="text-only downgrade is forbidden"):
        await provider.generate_reference_image(
            prompt="scene", references=[reference], negative_prompt="",
            aspect="9:16", seed=1, out_path=tmp_path / "out.png",
        )


def test_recipe_modes_reject_reference_on_cloudflare_but_allow_other_modes():
    caps = CloudflareImageProvider().capabilities()
    assert validate_identity_mode(
        CharacterIdentityMode.approximate_character_continuity, caps
    ) is CharacterIdentityMode.approximate_character_continuity
    assert validate_identity_mode(
        CharacterIdentityMode.no_recurring_character, caps
    ) is CharacterIdentityMode.no_recurring_character
    with pytest.raises(RuntimeError, match="unsupported"):
        validate_identity_mode(CharacterIdentityMode.reference_conditioned_character, caps)


def test_missing_tampered_and_unsupported_reference_fail_locally(tmp_path):
    caps = _capabilities()
    missing = tmp_path / "missing.png"
    with pytest.raises(FileNotFoundError):
        validate_reference_request(_request(missing, "a" * 64), caps)
    path, _, digest = _reference(tmp_path)
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        validate_reference_request(_request(path, "b" * 64), caps)
    unsupported = path.with_suffix(".jpg")
    unsupported.write_bytes(path.read_bytes())
    with pytest.raises(ValueError, match="MIME"):
        validate_reference_request(_request(unsupported, digest), caps)


@pytest.mark.asyncio
async def test_validation_precedes_provider_construction_and_accounting(tmp_path):
    calls = {"factory": 0}
    accounting: dict[str, int] = {}

    def factory():
        calls["factory"] += 1
        raise AssertionError("provider must not be constructed")

    with pytest.raises(FileNotFoundError):
        await submit_reference_conditioned(
            _request(tmp_path / "missing.png", "a" * 64), _capabilities(), factory,
            out_path=tmp_path / "out.png", accounting=accounting,
        )
    assert calls == {"factory": 0}
    assert accounting == {}


@pytest.mark.asyncio
async def test_mock_reference_adapter_receives_exact_bytes_and_metadata(tmp_path):
    path, content, digest = _reference(tmp_path)
    request = _request(path, digest)
    caps = _capabilities()
    received = {}

    class MockProvider:
        def capabilities(self):
            return caps

        async def generate_reference_conditioned(
            self, *, request, reference_bytes, reference_mime_type, out_path
        ):
            received.update({
                "request": request,
                "bytes": reference_bytes,
                "mime": reference_mime_type,
                "out_path": out_path,
            })
            return {"status": "mock_success"}

    accounting: dict[str, int] = {}
    result = await submit_reference_conditioned(
        request, caps, MockProvider, out_path=tmp_path / "result.png",
        accounting=accounting,
    )
    assert result == {"status": "mock_success"}
    assert received["bytes"] == content
    assert received["request"].character_fingerprint == "a" * 64
    assert received["request"].reference_sheet_version == 1
    assert received["mime"] == "image/png"
    assert accounting == {"image_provider_submissions": 1, "transport_attempts": 1}


def test_approved_reference_sheet_is_required_for_reference_mode(tmp_path):
    path, _, digest = _reference(tmp_path)
    caps = _capabilities()
    with pytest.raises(RuntimeError, match="approved reference sheet"):
        validate_identity_mode(CharacterIdentityMode.reference_conditioned_character, caps)
    assert validate_identity_mode(
        CharacterIdentityMode.reference_conditioned_character,
        caps,
        reference_sheet=_manifest(path, digest),
    ) is CharacterIdentityMode.reference_conditioned_character


def _identity(**overrides) -> ReferenceIdentityObservation:
    payload = {
        "hair_color": "match", "hair_silhouette": "match",
        "face_shape": "match", "skin_tone": "match",
        "outfit_details": "match", "body_build": "match",
        "line_style_and_palette": "match",
    }
    payload.update(overrides)
    return ReferenceIdentityObservation(**payload)


def test_reference_identity_and_semantic_anatomy_gates_are_independent():
    identity_fail = evaluate_reference_visual_gates(ReferenceVisualGates(
        scene_index=1, identity=_identity(hair_silhouette="hard_mismatch"),
        semantic_action_passed=True, anatomy_passed=True, props_passed=True,
        style_passed=True, composition_passed=True, subtitle_layout_passed=True,
    ))
    semantic_fail = evaluate_reference_visual_gates(ReferenceVisualGates(
        scene_index=2, identity=_identity(face_shape="soft_variation"),
        semantic_action_passed=False, anatomy_passed=True, props_passed=True,
        style_passed=True, composition_passed=True, subtitle_layout_passed=True,
    ))
    anatomy_fail = evaluate_reference_visual_gates(ReferenceVisualGates(
        scene_index=3, identity=_identity(), semantic_action_passed=True,
        anatomy_passed=False, props_passed=True, style_passed=True,
        composition_passed=True, subtitle_layout_passed=True,
    ))
    assert identity_fail["passed"] is False
    assert semantic_fail["identity"]["passed"] is True
    assert semantic_fail["passed"] is False
    assert anatomy_fail["passed"] is False


def test_seven_scene_aggregate_requires_every_independent_gate():
    rows = []
    for index in range(1, 8):
        rows.append(evaluate_reference_visual_gates(ReferenceVisualGates(
            scene_index=index, identity=_identity(),
            semantic_action_passed=index != 5, anatomy_passed=True,
            props_passed=True, style_passed=True, composition_passed=True,
            subtitle_layout_passed=True,
        )))
    result = aggregate_reference_visual_gates(rows)
    assert result["passed"] is False
    assert result["failed_scene_indices"] == [5]
    assert result["human_review_required"] is True
