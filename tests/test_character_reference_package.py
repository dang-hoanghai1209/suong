from __future__ import annotations

import hashlib
import json
import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from tella.media.character_reference_package import (
    ALL_ASSET_ROLES,
    ATOMIC_DIMENSIONS,
    ATOMIC_VIEW_ORDER,
    CHARACTER_ID,
    PACKAGE_ID,
    MASTER_SHEET_ASSEMBLY,
    MASTER_SHEET_DIMENSIONS,
    CharacterReferencePackageManifest,
    build_master_sheet,
    calculate_character_fingerprint,
    load_and_validate_reference_package,
    load_canonical_character_specification,
    provider_facing_atomic_assets,
)


SPEC_PATH = Path(
    "configs/character_references/practical_young_adult_male_teal_v1.json"
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
        raise AssertionError("character reference package tests must remain offline")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    yield
    assert calls == 0


def _write_png(path: Path, dimensions: tuple[int, int], color: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", dimensions, color).save(path, format="PNG")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _package(tmp_path: Path) -> tuple[Path, Path, dict]:
    specification = load_canonical_character_specification(SPEC_PATH)
    root = tmp_path / "repository"
    roles = {
        "front_portrait": (ATOMIC_DIMENSIONS, "#5b7f76"),
        "three_quarter_portrait": (ATOMIC_DIMENSIONS, "#5f8178"),
        "side_profile": (ATOMIC_DIMENSIONS, "#26332f"),
        "full_body_neutral": (ATOMIC_DIMENSIONS, "#607f76"),
    }
    records = {}
    for role, (dimensions, color) in roles.items():
        relative = Path("reference_package") / f"{role}.png"
        digest = _write_png(root / relative, dimensions, color)
        records[role] = {
            "asset_role": role,
            "path": relative.as_posix(),
            "mime_type": "image/png",
            "width": dimensions[0],
            "height": dimensions[1],
            "sha256": digest,
            "origin": "provider_generated",
            "source_sha256": [],
        }

    master_relative = Path("reference_package/master_sheet.png")
    master_result = build_master_sheet(
        tuple(
            (role, root / records[role]["path"])
            for role in ATOMIC_VIEW_ORDER
        ),
        root / master_relative,
    )
    records["master_sheet"] = {
        "asset_role": "master_sheet",
        "path": master_relative.as_posix(),
        "mime_type": "image/png",
        "width": master_result.width,
        "height": master_result.height,
        "sha256": master_result.sha256,
        "origin": "deterministic_local_derivative",
        "source_sha256": list(master_result.source_sha256),
    }

    timestamp = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
    approval = {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "character_id": CHARACTER_ID,
        "character_fingerprint": specification.character_fingerprint,
        "approval_timestamp": timestamp.isoformat(),
        "approver_role": "human_visual_reviewer",
        "asset_sha256": {role: records[role]["sha256"] for role in ALL_ASSET_ROLES},
        "checklist": {
            "same_face_across_views": True,
            "same_age_appearance": True,
            "same_hair_silhouette": True,
            "same_skin_tone": True,
            "same_body_proportions": True,
            "same_outfit_and_shoes": True,
            "anatomy_correct": True,
            "hands_correct": True,
            "no_cropping": True,
            "no_extra_person": True,
            "no_text_or_watermark": True,
            "master_and_atomic_views_match": True,
            "all_hashes_verified": True,
        },
    }
    approval_path = root / "reference_package" / "approval.json"
    approval_path.write_text(
        json.dumps(approval, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    approval_digest = hashlib.sha256(approval_path.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "package_id": PACKAGE_ID,
        "character_id": CHARACTER_ID,
        "character_fingerprint": specification.character_fingerprint,
        "canonical_spec_version": 1,
        "master_sheet": records["master_sheet"],
        "atomic_views": [records[role] for role in ATOMIC_VIEW_ORDER],
        "atomic_generation_provenance": "zero-network synthetic test fixture",
        "master_assembly_provenance": MASTER_SHEET_ASSEMBLY,
        "generation_provider": "fake_provider",
        "generation_model": "fake_model",
        "prompt_sha256": "a" * 64,
        "anatomy_qc_result": "passed",
        "style_qc_result": "passed",
        "cross_view_identity_qc_result": "passed",
        "human_approved": True,
        "approval_record_path": "reference_package/approval.json",
        "approval_timestamp": timestamp.isoformat(),
        "approver_role": "human_visual_reviewer",
        "immutable_approval_sha256": approval_digest,
    }
    manifest_path = root / "reference_package" / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return root, manifest_path, manifest


def test_canonical_character_specification_and_fingerprint_are_stable():
    specification = load_canonical_character_specification(SPEC_PATH)
    assert specification.character_id == CHARACTER_ID
    assert (specification.visual_age_min, specification.visual_age_max) == (24, 28)
    assert specification.footwear == "simple low-profile charcoal shoes with no logo"
    assert specification.character_fingerprint == EXPECTED_FINGERPRINT
    assert calculate_character_fingerprint(specification) == EXPECTED_FINGERPRINT
    assert specification.palette.scene_only_accent == "#df8668"
    assert "#df8668" not in specification.palette.locked_clothing_colors


def test_all_five_assets_validate_and_provider_order_excludes_master(tmp_path):
    root, manifest_path, _ = _package(tmp_path)
    manifest = load_and_validate_reference_package(
        manifest_path, SPEC_PATH, repository_root=root
    )
    assert {manifest.master_sheet.asset_role, *(v.asset_role for v in manifest.atomic_views)} == set(ALL_ASSET_ROLES)
    provider_assets = provider_facing_atomic_assets(
        manifest_path, SPEC_PATH, repository_root=root
    )
    assert tuple(asset.asset_role for asset in provider_assets) == ATOMIC_VIEW_ORDER
    assert "master_sheet" not in {asset.asset_role for asset in provider_assets}
    assert (manifest.master_sheet.width, manifest.master_sheet.height) == (
        MASTER_SHEET_DIMENSIONS
    )
    assert manifest.master_sheet.source_sha256 == tuple(
        asset.sha256 for asset in manifest.atomic_views
    )


def test_old_master_sheet_geometry_and_provider_origin_are_rejected(tmp_path):
    _, _, manifest = _package(tmp_path)
    manifest["master_sheet"]["height"] = 1024
    with pytest.raises(ValidationError, match="1536x2048"):
        CharacterReferencePackageManifest.model_validate(manifest)

    _, _, manifest = _package(tmp_path / "provider")
    manifest["master_sheet"]["origin"] = "provider_generated"
    with pytest.raises(ValidationError, match="deterministic local derivative"):
        CharacterReferencePackageManifest.model_validate(manifest)


@pytest.mark.parametrize("missing_role", ATOMIC_VIEW_ORDER)
def test_every_atomic_view_is_mandatory(tmp_path, missing_role):
    _, _, manifest = _package(tmp_path)
    manifest["atomic_views"] = [
        item for item in manifest["atomic_views"] if item["asset_role"] != missing_role
    ]
    with pytest.raises(ValidationError, match="atomic views"):
        CharacterReferencePackageManifest.model_validate(manifest)


def test_master_sheet_alone_cannot_satisfy_provider_references(tmp_path):
    _, _, manifest = _package(tmp_path)
    manifest["atomic_views"] = []
    with pytest.raises(ValidationError, match="atomic views"):
        CharacterReferencePackageManifest.model_validate(manifest)


def test_duplicate_atomic_view_type_fails(tmp_path):
    _, _, manifest = _package(tmp_path)
    manifest["atomic_views"][1] = dict(manifest["atomic_views"][0])
    with pytest.raises(ValidationError, match="duplicated"):
        CharacterReferencePackageManifest.model_validate(manifest)


def test_hash_mismatch_and_post_approval_asset_change_fail(tmp_path):
    root, manifest_path, manifest = _package(tmp_path)
    manifest["atomic_views"][0]["sha256"] = "b" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="source hashes do not match"):
        load_and_validate_reference_package(manifest_path, SPEC_PATH, repository_root=root)

    root, manifest_path, manifest = _package(tmp_path / "changed")
    changed = root / manifest["atomic_views"][0]["path"]
    changed.write_bytes(changed.read_bytes() + b"changed-after-approval")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        load_and_validate_reference_package(manifest_path, SPEC_PATH, repository_root=root)


@pytest.mark.parametrize("failure", ["mime", "dimensions"])
def test_decoded_mime_and_dimensions_must_match_manifest(tmp_path, failure):
    root, manifest_path, manifest = _package(tmp_path)
    asset = manifest["atomic_views"][0]
    path = root / asset["path"]
    if failure == "mime":
        Image.new("RGB", (768, 1024), "#5b7f76").save(path, format="JPEG")
        expected = "MIME mismatch"
    else:
        Image.new("RGB", (767, 1024), "#5b7f76").save(path, format="PNG")
        expected = "dimensions mismatch"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    asset["sha256"] = digest
    manifest["master_sheet"]["source_sha256"][0] = digest
    approval_path = root / "reference_package" / "approval.json"
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    approval["asset_sha256"][asset["asset_role"]] = digest
    approval_path.write_text(json.dumps(approval, sort_keys=True), encoding="utf-8")
    manifest["immutable_approval_sha256"] = hashlib.sha256(
        approval_path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match=expected):
        load_and_validate_reference_package(
            manifest_path, SPEC_PATH, repository_root=root
        )


def test_missing_approval_fails_closed(tmp_path):
    root, manifest_path, manifest = _package(tmp_path)
    manifest["human_approved"] = False
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValidationError, match="human_approved"):
        load_and_validate_reference_package(manifest_path, SPEC_PATH, repository_root=root)


@pytest.mark.parametrize(
    "field", ["anatomy_qc_result", "style_qc_result", "cross_view_identity_qc_result"]
)
def test_all_qc_approvals_are_required(tmp_path, field):
    _, _, manifest = _package(tmp_path)
    manifest[field] = "failed"
    with pytest.raises(ValidationError, match=field):
        CharacterReferencePackageManifest.model_validate(manifest)
