"""Immutable, provider-independent character reference package contracts."""
from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Sequence

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tella.atomic_write import atomic_write_bytes


CHARACTER_ID = "practical_young_adult_male_teal_v1"
PACKAGE_ID = "practical_young_adult_male_teal_v1_package_v1"
MASTER_SHEET_ROLE = "master_sheet"
ATOMIC_VIEW_ORDER = (
    "front_portrait",
    "three_quarter_portrait",
    "side_profile",
    "full_body_neutral",
)
ALL_ASSET_ROLES = (MASTER_SHEET_ROLE, *ATOMIC_VIEW_ORDER)
ATOMIC_DIMENSIONS = (768, 1024)
MASTER_SHEET_DIMENSIONS = (1536, 2048)
MASTER_SHEET_ASSEMBLY = "deterministic_local_exact_2x2_v1"

AssetRole = Literal[
    "master_sheet",
    "front_portrait",
    "three_quarter_portrait",
    "side_profile",
    "full_body_neutral",
]


class CharacterPalette(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    muted_teal: Literal["#5b7f76"]
    charcoal: Literal["#26332f"]
    skin_tone: Literal["fixed warm medium-light skin tone"]
    reference_background: Literal["#eef0e7"]
    scene_only_accent: Literal["#df8668"]
    locked_clothing_colors: tuple[str, ...]

    @model_validator(mode="after")
    def scene_accent_is_not_clothing(self) -> "CharacterPalette":
        normalized = {color.lower() for color in self.locked_clothing_colors}
        if self.scene_only_accent.lower() in normalized:
            raise ValueError("scene-only coral must not be a locked clothing color")
        if normalized != {self.muted_teal, self.charcoal}:
            raise ValueError("locked clothing colors must be muted teal and charcoal")
        return self


class CanonicalCharacterSpecification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    character_id: Literal[CHARACTER_ID]
    canonical_spec_version: Literal[1]
    character_fingerprint: str
    visual_age_min: Literal[24]
    visual_age_max: Literal[28]
    presentation: Literal["young adult male"]
    face_shape: Literal["softly angular"]
    eyes: Literal["small dark eyes"]
    nose: Literal["short straight nose"]
    stable_facial_traits: tuple[str, ...]
    hair: Literal["short dark rounded side-swept hair"]
    skin_tone: Literal["fixed warm medium-light skin tone"]
    body_build: Literal["slim average adult build"]
    proportion_locks: tuple[str, ...]
    top: Literal["muted teal long-sleeve round-collar top with narrow cuffs"]
    trousers: Literal["charcoal straight trousers"]
    footwear: Literal["simple low-profile charcoal shoes with no logo"]
    accessories: tuple[str, ...]
    palette: CharacterPalette
    style: tuple[str, ...]
    forbidden_styles: tuple[str, ...]

    @field_validator("character_fingerprint")
    @classmethod
    def full_sha256(cls, value: str) -> str:
        return _normalize_sha256(value)

    @model_validator(mode="after")
    def validate_identity_contract(self) -> "CanonicalCharacterSpecification":
        if self.accessories:
            raise ValueError("canonical practical character must have no accessories")
        if self.character_fingerprint != calculate_character_fingerprint(self):
            raise ValueError("canonical character fingerprint mismatch")
        return self


class ReferenceAssetRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_role: AssetRole
    path: Path
    mime_type: Literal["image/png"]
    width: int = Field(ge=64, le=8192)
    height: int = Field(ge=64, le=8192)
    sha256: str
    origin: Literal["provider_generated", "deterministic_local_derivative"]
    source_sha256: tuple[str, ...] = ()

    @field_validator("sha256")
    @classmethod
    def full_sha256(cls, value: str) -> str:
        return _normalize_sha256(value)

    @field_validator("source_sha256")
    @classmethod
    def source_hashes_are_sha256(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_normalize_sha256(item) for item in value)

    @model_validator(mode="after")
    def validate_origin(self) -> "ReferenceAssetRecord":
        if self.asset_role == MASTER_SHEET_ROLE:
            if self.origin != "deterministic_local_derivative":
                raise ValueError("master sheet must be a deterministic local derivative")
            if len(self.source_sha256) != len(ATOMIC_VIEW_ORDER):
                raise ValueError("master sheet must record all four atomic source hashes")
        elif self.origin != "provider_generated" or self.source_sha256:
            raise ValueError("atomic views must be provider-generated source assets")
        return self


class MasterSheetBuildResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    width: Literal[1536]
    height: Literal[2048]
    mime_type: Literal["image/png"]
    sha256: str
    source_sha256: tuple[str, ...]
    assembly: Literal[MASTER_SHEET_ASSEMBLY]

    @field_validator("sha256")
    @classmethod
    def full_sha256(cls, value: str) -> str:
        return _normalize_sha256(value)


class ApprovalChecklist(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    same_face_across_views: Literal[True]
    same_age_appearance: Literal[True]
    same_hair_silhouette: Literal[True]
    same_skin_tone: Literal[True]
    same_body_proportions: Literal[True]
    same_outfit_and_shoes: Literal[True]
    anatomy_correct: Literal[True]
    hands_correct: Literal[True]
    no_cropping: Literal[True]
    no_extra_person: Literal[True]
    no_text_or_watermark: Literal[True]
    master_and_atomic_views_match: Literal[True]
    all_hashes_verified: Literal[True]


class CharacterReferenceApprovalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    package_id: Literal[PACKAGE_ID]
    character_id: Literal[CHARACTER_ID]
    character_fingerprint: str
    approval_timestamp: datetime
    approver_role: str = Field(min_length=1, max_length=120)
    asset_sha256: dict[str, str]
    checklist: ApprovalChecklist

    @field_validator("character_fingerprint")
    @classmethod
    def full_sha256(cls, value: str) -> str:
        return _normalize_sha256(value)

    @model_validator(mode="after")
    def validate_asset_hash_set(self) -> "CharacterReferenceApprovalRecord":
        if set(self.asset_sha256) != set(ALL_ASSET_ROLES):
            raise ValueError("approval record must cover all five reference assets")
        for digest in self.asset_sha256.values():
            _normalize_sha256(digest)
        return self


class CharacterReferencePackageManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    package_id: Literal[PACKAGE_ID]
    character_id: Literal[CHARACTER_ID]
    character_fingerprint: str
    canonical_spec_version: Literal[1]
    master_sheet: ReferenceAssetRecord
    atomic_views: tuple[ReferenceAssetRecord, ...]
    atomic_generation_provenance: str = Field(min_length=1, max_length=500)
    master_assembly_provenance: Literal[MASTER_SHEET_ASSEMBLY]
    generation_provider: str = Field(min_length=1, max_length=120)
    generation_model: str = Field(min_length=1, max_length=160)
    prompt_sha256: str
    anatomy_qc_result: Literal["passed"]
    style_qc_result: Literal["passed"]
    cross_view_identity_qc_result: Literal["passed"]
    human_approved: Literal[True]
    approval_record_path: Path
    approval_timestamp: datetime
    approver_role: str = Field(min_length=1, max_length=120)
    immutable_approval_sha256: str

    @field_validator(
        "character_fingerprint", "prompt_sha256", "immutable_approval_sha256"
    )
    @classmethod
    def full_sha256(cls, value: str) -> str:
        return _normalize_sha256(value)

    @model_validator(mode="after")
    def validate_package_shape(self) -> "CharacterReferencePackageManifest":
        if self.master_sheet.asset_role != MASTER_SHEET_ROLE:
            raise ValueError("master sheet record has the wrong asset role")
        if (self.master_sheet.width, self.master_sheet.height) != MASTER_SHEET_DIMENSIONS:
            raise ValueError("master sheet must be 1536x2048")
        roles = tuple(asset.asset_role for asset in self.atomic_views)
        if roles != ATOMIC_VIEW_ORDER:
            raise ValueError("atomic views are missing, duplicated, or out of order")
        for asset in self.atomic_views:
            if (asset.width, asset.height) != ATOMIC_DIMENSIONS:
                raise ValueError("atomic views must be 768x1024")
        atomic_hashes = tuple(asset.sha256 for asset in self.atomic_views)
        if self.master_sheet.source_sha256 != atomic_hashes:
            raise ValueError("master sheet source hashes do not match atomic views")
        return self


def calculate_character_fingerprint(
    specification: CanonicalCharacterSpecification | dict[str, Any],
) -> str:
    if isinstance(specification, BaseModel):
        payload = specification.model_dump(mode="json", exclude={"character_fingerprint"})
    else:
        payload = dict(specification)
        payload.pop("character_fingerprint", None)
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_canonical_character_specification(
    path: Path,
) -> CanonicalCharacterSpecification:
    return CanonicalCharacterSpecification.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def load_and_validate_reference_package(
    manifest_path: Path,
    canonical_specification_path: Path,
    *,
    repository_root: Path,
) -> CharacterReferencePackageManifest:
    root = Path(repository_root).resolve()
    manifest = CharacterReferencePackageManifest.model_validate_json(
        Path(manifest_path).read_text(encoding="utf-8")
    )
    specification = load_canonical_character_specification(
        canonical_specification_path
    )
    if manifest.character_fingerprint != specification.character_fingerprint:
        raise ValueError("reference package character fingerprint mismatch")
    if manifest.canonical_spec_version != specification.canonical_spec_version:
        raise ValueError("reference package canonical specification version mismatch")

    atomic_content = tuple(
        (asset.asset_role, _validate_asset(asset, root))
        for asset in manifest.atomic_views
    )
    expected_master, source_hashes = _assemble_master_sheet_bytes(atomic_content)
    master_content = _validate_asset(manifest.master_sheet, root)
    if manifest.master_sheet.source_sha256 != source_hashes:
        raise ValueError("master sheet source hashes do not match atomic bytes")
    if master_content != expected_master:
        raise ValueError("master sheet is not the deterministic atomic derivative")

    assets = (manifest.master_sheet, *manifest.atomic_views)

    approval_path = _safe_package_path(manifest.approval_record_path, root)
    approval_bytes = approval_path.read_bytes()
    if hashlib.sha256(approval_bytes).hexdigest() != manifest.immutable_approval_sha256:
        raise ValueError("immutable approval SHA256 mismatch")
    approval = CharacterReferenceApprovalRecord.model_validate_json(approval_bytes)
    if approval.character_fingerprint != manifest.character_fingerprint:
        raise ValueError("approval character fingerprint mismatch")
    if approval.approval_timestamp != manifest.approval_timestamp:
        raise ValueError("approval timestamp mismatch")
    if approval.approver_role != manifest.approver_role:
        raise ValueError("approval role mismatch")
    expected_hashes = {asset.asset_role: asset.sha256 for asset in assets}
    if approval.asset_sha256 != expected_hashes:
        raise ValueError("approval asset hashes do not match the package")
    return manifest


def provider_facing_atomic_assets(
    manifest_path: Path,
    canonical_specification_path: Path,
    *,
    repository_root: Path,
) -> tuple[ReferenceAssetRecord, ...]:
    """Return only validated atomic assets in deterministic provider order."""
    manifest = load_and_validate_reference_package(
        manifest_path, canonical_specification_path, repository_root=repository_root
    )
    return manifest.atomic_views


def build_master_sheet(
    atomic_sources: Sequence[tuple[str, Path]],
    output_path: Path,
) -> MasterSheetBuildResult:
    """Assemble the four ordered atomic PNGs without crop, scaling, or labels."""
    roles = tuple(role for role, _ in atomic_sources)
    if roles != ATOMIC_VIEW_ORDER:
        raise ValueError("atomic sources are missing, duplicated, or out of order")
    content = []
    for role, path in atomic_sources:
        candidate = Path(path)
        if candidate.suffix.lower() != ".png" or not candidate.is_file():
            raise ValueError(f"atomic source must be an existing PNG: {role}")
        content.append((role, candidate.read_bytes()))
    master_bytes, source_hashes = _assemble_master_sheet_bytes(tuple(content))
    atomic_write_bytes(Path(output_path), master_bytes)
    return MasterSheetBuildResult(
        width=MASTER_SHEET_DIMENSIONS[0],
        height=MASTER_SHEET_DIMENSIONS[1],
        mime_type="image/png",
        sha256=hashlib.sha256(master_bytes).hexdigest(),
        source_sha256=source_hashes,
        assembly=MASTER_SHEET_ASSEMBLY,
    )


def _validate_asset(asset: ReferenceAssetRecord, root: Path) -> bytes:
    path = _safe_package_path(asset.path, root)
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != asset.sha256:
        raise ValueError(f"reference asset SHA256 mismatch: {asset.asset_role}")
    if path.suffix.lower() != ".png" or asset.mime_type != "image/png":
        raise ValueError(f"reference asset MIME mismatch: {asset.asset_role}")
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.load()
            actual_format = image.format
            dimensions = image.size
    except Exception:
        raise ValueError(f"reference asset decoding failed: {asset.asset_role}") from None
    if actual_format != "PNG":
        raise ValueError(f"reference asset MIME mismatch: {asset.asset_role}")
    if dimensions != (asset.width, asset.height):
        raise ValueError(f"reference asset dimensions mismatch: {asset.asset_role}")
    return content


def _assemble_master_sheet_bytes(
    atomic_content: Sequence[tuple[str, bytes]],
) -> tuple[bytes, tuple[str, ...]]:
    roles = tuple(role for role, _ in atomic_content)
    if roles != ATOMIC_VIEW_ORDER:
        raise ValueError("atomic sources are missing, duplicated, or out of order")
    images: list[Image.Image] = []
    source_hashes: list[str] = []
    for role, content in atomic_content:
        source_hashes.append(hashlib.sha256(content).hexdigest())
        try:
            with Image.open(io.BytesIO(content)) as image:
                image.load()
                if image.format != "PNG":
                    raise ValueError(f"atomic source MIME mismatch: {role}")
                if image.size != ATOMIC_DIMENSIONS:
                    raise ValueError(f"atomic source dimensions mismatch: {role}")
                images.append(image.convert("RGBA"))
        except ValueError:
            raise
        except Exception:
            raise ValueError(f"atomic source decoding failed: {role}") from None

    canvas = Image.new("RGBA", MASTER_SHEET_DIMENSIONS, (0, 0, 0, 0))
    positions = (
        (0, 0),
        (ATOMIC_DIMENSIONS[0], 0),
        (0, ATOMIC_DIMENSIONS[1]),
        (ATOMIC_DIMENSIONS[0], ATOMIC_DIMENSIONS[1]),
    )
    for image, position in zip(images, positions, strict=True):
        canvas.paste(image, position)
    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue(), tuple(source_hashes)


def _safe_package_path(relative_path: Path, root: Path) -> Path:
    path = Path(relative_path)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("reference package paths must be repository-relative")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("reference package path escapes the repository")
    if not resolved.is_file():
        raise FileNotFoundError(f"reference package file is missing: {path.as_posix()}")
    if resolved.is_symlink() or bool(getattr(resolved, "is_junction", lambda: False)()):
        raise ValueError("reference package files must not be links")
    return resolved


def _normalize_sha256(value: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError("expected a full SHA256 hex digest")
    return normalized


__all__ = [
    "ALL_ASSET_ROLES",
    "ATOMIC_DIMENSIONS",
    "ATOMIC_VIEW_ORDER",
    "CHARACTER_ID",
    "PACKAGE_ID",
    "MASTER_SHEET_ASSEMBLY",
    "MASTER_SHEET_DIMENSIONS",
    "ApprovalChecklist",
    "CanonicalCharacterSpecification",
    "CharacterReferenceApprovalRecord",
    "CharacterReferencePackageManifest",
    "MasterSheetBuildResult",
    "ReferenceAssetRecord",
    "calculate_character_fingerprint",
    "build_master_sheet",
    "load_and_validate_reference_package",
    "load_canonical_character_specification",
    "provider_facing_atomic_assets",
]
