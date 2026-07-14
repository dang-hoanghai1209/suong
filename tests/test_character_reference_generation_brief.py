from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path

import pytest

from tella.media.bfl_flux2_provider import BFLFlux2Config, BFLFlux2ReferenceProvider
from tella.media.character_reference_package import ATOMIC_VIEW_ORDER, ALL_ASSET_ROLES
from tella.media.image_provider import CloudflareImageProvider


GENERATION_PATH = Path(
    "configs/character_references/"
    "practical_young_adult_male_teal_v1_generation_v1.json"
)
APPROVAL_TEMPLATE_PATH = Path(
    "configs/character_references/"
    "practical_young_adult_male_teal_v1_approval_template_v1.json"
)
DOCUMENTATION_PATH = Path(
    "docs/character_reference/practical_young_adult_male_teal_v1.md"
)
EXPECTED_FINGERPRINT = (
    "4bb86c902dfedba848ad8ae43ef6dbd0bb41059be7fa1af816ecd85cc28fba5f"
)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    calls = 0

    def forbidden(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("generation-brief tests must remain offline")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    yield
    assert calls == 0


def _generation() -> dict:
    return json.loads(GENERATION_PATH.read_text(encoding="utf-8"))


def test_generation_prompt_and_fingerprint_are_immutable():
    generation = _generation()
    assert generation["character_fingerprint"] == EXPECTED_FINGERPRINT
    assert hashlib.sha256(generation["generation_prompt"].encode("utf-8")).hexdigest() == generation["prompt_sha256"]
    assert generation["provider_selection"] == "not_selected"


def test_generation_outputs_cover_master_and_deterministic_atomic_views():
    generation = _generation()
    roles = [generation["master_sheet"]["asset_role"], *(
        view["asset_role"] for view in generation["atomic_views"]
    )]
    assert tuple(roles) == ALL_ASSET_ROLES
    assert tuple(view["asset_role"] for view in generation["atomic_views"]) == ATOMIC_VIEW_ORDER
    assert generation["master_sheet"]["provider_facing"] is False
    assert [view["provider_order"] for view in generation["atomic_views"]] == [1, 2, 3, 4]


def test_generation_constraints_lock_anatomy_style_and_scene_only_coral():
    generation = _generation()
    negative = " ".join(generation["negative_constraints"]).lower()
    assert "coral clothing" in negative
    assert "no text" in negative
    assert "no duplicated, missing, merged, disconnected, or malformed body part" in negative
    anatomy = generation["anatomy_contract"]
    assert [anatomy[key] for key in (
        "head_count", "arm_count", "hand_count", "leg_count", "foot_count"
    )] == [1, 2, 2, 2, 2]
    assert anatomy["five_digits_correctly_represented_or_implied"] is True
    assert anatomy["individual_finger_separation_required"] is False


def test_approval_template_is_explicitly_incomplete_and_covers_every_asset():
    approval = json.loads(APPROVAL_TEMPLATE_PATH.read_text(encoding="utf-8"))
    assert approval["human_approved"] is False
    assert approval["approval_timestamp"] is None
    assert approval["approver_role"] is None
    assert tuple(approval["asset_sha256"]) == ALL_ASSET_ROLES
    assert all(value is None for value in approval["asset_sha256"].values())
    assert approval["checklist"]
    assert not any(approval["checklist"].values())


def test_existing_provider_capabilities_are_assessed_without_construction():
    bfl = BFLFlux2ReferenceProvider(
        config=BFLFlux2Config(),
        reference_store=object(),
        transport=object(),
        api_key=None,
    ).capabilities()
    assert bfl.supports_reference_conditioning is True
    assert bfl.max_reference_images >= 4
    assert "image/png" in bfl.accepted_reference_mime_types
    cloudflare = CloudflareImageProvider().capabilities()
    assert cloudflare.supports_reference_conditioning is False
    assert cloudflare.max_reference_images == 0


def test_documentation_preserves_package_and_authorization_boundaries():
    documentation = DOCUMENTATION_PATH.read_text(encoding="utf-8")
    normalized = " ".join(documentation.split())
    assert "all four atomic views" in normalized
    assert "must not silently fall back to the master sheet" in normalized
    assert "does not authorize image generation" in normalized
    assert "No placeholder image is permitted" in normalized
