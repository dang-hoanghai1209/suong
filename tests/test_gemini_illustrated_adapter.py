"""Network-free IllustratedSceneRequest to Gemini 3 Pro mapping."""
from __future__ import annotations

import base64
import hashlib
import io
import re
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from tella.topic_production.execution_models import ApprovedReference
from tella.topic_production.gemini_illustrated_adapter import (
    GEMINI_PRO_ASPECT_RATIO,
    GEMINI_PRO_EXPECTED_HEIGHT,
    GEMINI_PRO_EXPECTED_WIDTH,
    GEMINI_PRO_IMAGE_SIZE,
    GeminiBillingReadiness,
    GeminiPrivacyState,
    build_gemini_illustrated_preview,
    gemini_provider_request_hash,
)
from tella.topic_production.illustrated_request import (
    IllustratedPromptProfile,
    IllustratedReferenceAuthority,
    IllustratedReferenceBinding,
    IllustratedSceneRequest,
)
from tella.topic_production.planner import DeterministicTopicPlanner, build_scene_briefs
from tella.topic_production.strategy import SceneDataSensitivity
from tella.visual_generation.providers.gemini import (
    PRO_IMAGE_MODEL,
    GeminiSceneImageProvider,
    gemini_image_model_contract,
    gemini_prompt,
)

APPROVED_REFERENCE_SHA = (
    "ac7775d1e07aa81aea835a61efc6df19de112fbd694ec815c341ca029f7dec72"
)


def _brief():
    return build_scene_briefs(
        DeterministicTopicPlanner().plan(topic="Healing through a quiet evening.")
    )[0]


def _reference(
    *,
    path_root: str = "C:/approved-private",
    suffix: str = ".png",
    sha256: str = APPROVED_REFERENCE_SHA,
) -> ApprovedReference:
    return ApprovedReference(
        reference_id="female_style_master",
        path=f"{path_root}/female_style_master{suffix}",
        sha256=sha256,
        roles=["style_anchor", "female_identity_anchor"],
        sensitivity=SceneDataSensitivity.PRIVATE,
        identity_scope="recurring_female",
        style_scope="soft_editorial_v1",
        priority=1,
    )


def _binding(
    reference: ApprovedReference,
    *,
    authority: IllustratedReferenceAuthority,
    role: str,
) -> IllustratedReferenceBinding:
    return IllustratedReferenceBinding(
        authority=authority,
        declared_role=role,
        reference=reference,
    )


def _request(
    *,
    path_root: str = "C:/approved-private",
    suffix: str = ".png",
    sha256: str = APPROVED_REFERENCE_SHA,
    sensitivity: SceneDataSensitivity = SceneDataSensitivity.PRIVATE,
) -> IllustratedSceneRequest:
    reference = _reference(path_root=path_root, suffix=suffix, sha256=sha256)
    return IllustratedSceneRequest(
        prompt_profile=IllustratedPromptProfile.LEGACY_ACCEPTED,
        scene_id=_brief().scene_id,
        semantic_scene=_brief(),
        sensitivity=sensitivity,
        required_reference_roles=["female_identity_anchor", "style_anchor"],
        style_anchors=[
            _binding(
                reference,
                authority=IllustratedReferenceAuthority.STYLE,
                role="style_anchor",
            )
        ],
        character_identity_anchors=[
            _binding(
                reference,
                authority=IllustratedReferenceAuthority.CHARACTER_IDENTITY,
                role="female_identity_anchor",
            )
        ],
    )


def _many_reference_request(
    *,
    characters: int,
    styles: int,
    contexts: int,
    dual_role: bool = False,
) -> IllustratedSceneRequest:
    serial = 1
    required_roles = []
    character_bindings = []
    style_bindings = []
    composition_bindings = []

    def reference(role: str, authority: IllustratedReferenceAuthority):
        nonlocal serial
        digest = f"{serial:064x}"
        serial += 1
        approved = ApprovedReference(
            reference_id=f"reference_{serial:02d}",
            path=f"C:/approved-private/reference_{serial:02d}.png",
            sha256=digest,
            roles=[role],
            sensitivity=SceneDataSensitivity.PRIVATE,
            identity_scope=(
                f"identity_{serial}"
                if authority is IllustratedReferenceAuthority.CHARACTER_IDENTITY
                else None
            ),
            style_scope=(
                "soft_editorial_v1"
                if authority is IllustratedReferenceAuthority.STYLE
                else None
            ),
            priority=serial,
        )
        return _binding(approved, authority=authority, role=role)

    if dual_role:
        approved = ApprovedReference(
            reference_id="dual_master",
            path="C:/approved-private/dual_master.png",
            sha256=f"{serial:064x}",
            roles=["dual_identity_anchor", "style_anchor"],
            sensitivity=SceneDataSensitivity.PRIVATE,
            identity_scope="dual_identity",
            style_scope="soft_editorial_v1",
            priority=1,
        )
        serial += 1
        character_bindings.append(
            _binding(
                approved,
                authority=IllustratedReferenceAuthority.CHARACTER_IDENTITY,
                role="dual_identity_anchor",
            )
        )
        style_bindings.append(
            _binding(
                approved,
                authority=IllustratedReferenceAuthority.STYLE,
                role="style_anchor",
            )
        )
        required_roles.extend(["dual_identity_anchor", "style_anchor"])
        characters -= 1
        styles -= 1

    for index in range(characters):
        role = f"character_{index}_identity_anchor"
        required_roles.append(role)
        character_bindings.append(
            reference(role, IllustratedReferenceAuthority.CHARACTER_IDENTITY)
        )
    for _ in range(styles):
        if "style_anchor" not in required_roles:
            required_roles.append("style_anchor")
        style_bindings.append(
            reference("style_anchor", IllustratedReferenceAuthority.STYLE)
        )
    for index in range(contexts):
        role = f"context_{index}"
        required_roles.append(role)
        composition_bindings.append(
            reference(role, IllustratedReferenceAuthority.COMPOSITION)
        )
    return IllustratedSceneRequest(
        prompt_profile=IllustratedPromptProfile.LEGACY_ACCEPTED,
        scene_id=_brief().scene_id,
        semantic_scene=_brief(),
        sensitivity=SceneDataSensitivity.PRIVATE,
        required_reference_roles=list(dict.fromkeys(required_roles)),
        style_anchors=style_bindings,
        character_identity_anchors=character_bindings,
        composition_references=composition_bindings,
    )


def _jpeg_response():
    stream = io.BytesIO()
    Image.new("RGB", (768, 1376), "#554433").save(stream, "JPEG")
    return SimpleNamespace(
        output_image=SimpleNamespace(
            data=base64.b64encode(stream.getvalue()).decode("ascii"),
            mime_type="image/jpeg",
        )
    )


class RecordingInteractions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _jpeg_response()


def test_exact_gemini_pro_contract_is_model_specific() -> None:
    contract = gemini_image_model_contract(PRO_IMAGE_MODEL)
    provider = GeminiSceneImageProvider(
        model=PRO_IMAGE_MODEL,
        resolution=GEMINI_PRO_IMAGE_SIZE,
    )

    assert contract.model == "gemini-3-pro-image"
    assert contract.supported_resolutions == ("1K", "2K", "4K")
    assert (
        contract.max_reference_images,
        contract.max_character_references,
        contract.max_style_references,
        contract.max_context_references,
    ) == (14, 5, 3, 6)
    assert contract.supports_seed is provider.capabilities().supports_seed is False
    assert provider.capabilities().max_reference_images == 14


def test_gemini_pro_rejects_flash_only_half_k() -> None:
    with pytest.raises(ValueError, match="does not support resolution 0.5K"):
        GeminiSceneImageProvider(model=PRO_IMAGE_MODEL, resolution="0.5K")


def test_private_preview_is_deterministic_and_fail_closed() -> None:
    first = build_gemini_illustrated_preview(_request())
    second = build_gemini_illustrated_preview(_request())

    assert first == second
    assert first.model == PRO_IMAGE_MODEL
    assert first.aspect_ratio == GEMINI_PRO_ASPECT_RATIO == "9:16"
    assert first.image_size == GEMINI_PRO_IMAGE_SIZE == "1K"
    assert (first.expected_width, first.expected_height) == (
        GEMINI_PRO_EXPECTED_WIDTH,
        GEMINI_PRO_EXPECTED_HEIGHT,
    ) == (768, 1376)
    assert first.seed is None
    assert "no documented provider seed" in first.determinism_limitation
    assert first.billing_readiness is GeminiBillingReadiness.REQUIRES_CONFIRMATION
    assert first.privacy_state is GeminiPrivacyState.PRIVATE_REQUIRES_CONFIRMATION
    assert first.privacy_blocked is True
    assert first.transport_authorized is False


def test_private_preview_has_no_self_authorizing_billing_argument() -> None:
    with pytest.raises(TypeError, match="billing_readiness"):
        build_gemini_illustrated_preview(  # type: ignore[call-arg]
            _request(),
            billing_readiness="PAID_BILLING_CONFIRMED",
        )


def test_dual_authority_survives_one_physical_reference() -> None:
    preview = build_gemini_illustrated_preview(_request())

    assert [binding.authority for binding in preview.semantic_bindings] == [
        IllustratedReferenceAuthority.CHARACTER_IDENTITY,
        IllustratedReferenceAuthority.STYLE,
    ]
    assert len(preview.reference_uploads) == 1
    physical = preview.reference_uploads[0]
    assert physical.sha256 == APPROVED_REFERENCE_SHA
    assert physical.declared_roles == [
        "female_identity_anchor",
        "style_anchor",
    ]
    assert physical.authorities == [
        IllustratedReferenceAuthority.CHARACTER_IDENTITY,
        IllustratedReferenceAuthority.STYLE,
    ]
    assert preview.provider_request is not None
    assert len(preview.provider_request.references) == 1
    assert preview.provider_request.references[0].semantic_roles == physical.declared_roles


def test_prompt_indexes_only_existing_physical_references() -> None:
    preview = build_gemini_illustrated_preview(
        _many_reference_request(characters=2, styles=2, contexts=2)
    )
    indexes = {int(value) for value in re.findall(r"Reference image (\d+)", preview.prompt)}

    assert indexes == set(range(len(preview.reference_uploads)))


def test_prompt_semantics_come_from_explicit_provider_neutral_profile() -> None:
    preview = build_gemini_illustrated_preview(_request())
    assert preview.provider_request is not None
    assert gemini_prompt(preview.provider_request) == preview.prompt
    prompt = preview.prompt.casefold()

    assert "legacy accepted visual target" in prompt
    assert "authoritative visual target" in prompt
    assert "character identity only" in prompt
    assert "visual style only" in prompt
    assert "does not control scene composition" not in prompt
    assert "[story beat]" in prompt
    assert "[negative constraints]" in prompt


def test_paths_do_not_change_identity_but_normalized_mime_does() -> None:
    baseline = build_gemini_illustrated_preview(
        _request(path_root="C:/approved-private")
    )
    moved = build_gemini_illustrated_preview(
        _request(path_root="D:/moved-approved-private")
    )
    jpeg = build_gemini_illustrated_preview(
        _request(path_root="D:/moved-approved-private", suffix=".jpg")
    )

    assert moved.illustrated_request_hash == baseline.illustrated_request_hash
    assert moved.provider_request_hash == baseline.provider_request_hash
    assert jpeg.illustrated_request_hash == baseline.illustrated_request_hash
    assert jpeg.provider_request_hash != baseline.provider_request_hash


def test_total_reference_limits_and_overlapping_roles() -> None:
    accepted = build_gemini_illustrated_preview(
        _many_reference_request(characters=5, styles=3, contexts=6)
    )
    overlapping = build_gemini_illustrated_preview(
        _many_reference_request(
            characters=5,
            styles=3,
            contexts=6,
            dual_role=True,
        )
    )

    assert len(accepted.reference_uploads) == 14
    assert len(overlapping.reference_uploads) == 13
    assert len(overlapping.semantic_bindings) == 14
    with pytest.raises(ValueError, match="total reference limit exceeded"):
        build_gemini_illustrated_preview(
            _many_reference_request(characters=5, styles=3, contexts=7)
        )


@pytest.mark.parametrize(
    ("counts", "message"),
    [
        ((6, 0, 0), "character reference limit exceeded"),
        ((0, 4, 0), "style reference limit exceeded"),
        ((0, 0, 7), "contextual reference limit exceeded"),
    ],
)
def test_category_reference_limits(
    counts: tuple[int, int, int],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        build_gemini_illustrated_preview(
            _many_reference_request(
                characters=counts[0],
                styles=counts[1],
                contexts=counts[2],
            )
        )


def test_material_provider_fields_change_provider_identity() -> None:
    preview = build_gemini_illustrated_preview(_request())
    assert preview.provider_request_hash is not None
    kwargs = {
        "model": preview.model,
        "aspect_ratio": preview.aspect_ratio,
        "image_size": preview.image_size,
        "expected_width": preview.expected_width,
        "expected_height": preview.expected_height,
        "prompt": preview.prompt,
        "logical_request_hash": preview.illustrated_request_hash,
        "reference_uploads": preview.reference_uploads,
    }

    assert gemini_provider_request_hash(
        **{**kwargs, "model": "gemini-3.1-flash-image"}
    ) != preview.provider_request_hash
    assert gemini_provider_request_hash(
        **{**kwargs, "prompt": preview.prompt + "\ncontrolled change"}
    ) != preview.provider_request_hash
    changed_reference = _request(sha256="d" * 64)
    assert (
        build_gemini_illustrated_preview(changed_reference).provider_request_hash
        != preview.provider_request_hash
    )


@pytest.mark.asyncio
async def test_fake_client_receives_exact_deduplicated_pro_payload(
    tmp_path: Path,
    monkeypatch,
) -> None:
    reference_path = tmp_path / "female_style_master.png"
    Image.new("RGB", (90, 160), "#554433").save(reference_path)
    digest = hashlib.sha256(reference_path.read_bytes()).hexdigest()
    preview = build_gemini_illustrated_preview(
        _request(path_root=str(tmp_path), suffix=".png", sha256=digest)
    )
    assert preview.provider_request is not None
    interactions = RecordingInteractions()
    client = SimpleNamespace(interactions=interactions)
    provider = GeminiSceneImageProvider(
        model=PRO_IMAGE_MODEL,
        resolution=GEMINI_PRO_IMAGE_SIZE,
        client_factory=lambda: client,
    )
    monkeypatch.setenv("GEMINI_API_KEY", "unit-test-only")
    monkeypatch.setenv("TELLA_VISUAL_QUALITY_LIVE", "1")

    await provider.generate_scene(preview.provider_request, tmp_path / "candidate.png")

    assert len(interactions.calls) == 1
    call = interactions.calls[0]
    assert call["model"] == PRO_IMAGE_MODEL
    assert call["response_format"] == {
        "type": "image",
        "mime_type": "image/jpeg",
        "aspect_ratio": "9:16",
        "image_size": "1K",
    }
    assert call["input"][0] == {"type": "text", "text": preview.prompt}
    image_parts = [part for part in call["input"] if part["type"] == "image"]
    assert len(image_parts) == 1
    assert image_parts[0]["mime_type"] == "image/png"
    assert "seed" not in call


def test_generated_scene_chaining_and_provider_seed_are_absent() -> None:
    request = _request()
    preview = build_gemini_illustrated_preview(request)

    assert request.accepted_scene_chaining is False
    assert preview.accepted_scene_chaining is False
    assert preview.seed is None
    assert preview.provider_request is not None
    assert preview.provider_request.seed is None
    assert all(
        binding.reference.metadata.get("source") != "accepted_scene"
        for binding in preview.semantic_bindings
    )
