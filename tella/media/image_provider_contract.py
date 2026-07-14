"""Fail-closed capability and reference-conditioning contracts for image providers."""
from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CharacterIdentityMode(StrEnum):
    approximate_character_continuity = "approximate_character_continuity"
    reference_conditioned_character = "reference_conditioned_character"
    no_recurring_character = "no_recurring_character"


class ImageProviderCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_id: str = Field(min_length=1, max_length=80)
    supports_text_to_image: bool
    supports_reference_conditioning: bool
    supports_image_to_image: bool
    supports_structural_conditioning: bool
    supports_seed: bool
    supports_negative_prompt: bool
    max_prompt_utf8_bytes: int = Field(ge=1)
    max_reference_images: int = Field(ge=0)
    accepted_reference_mime_types: tuple[str, ...] = ()
    supports_character_identity_anchor: bool
    identity_anchor_verification: Literal[
        "unsupported", "provider_static", "per_request_verified"
    ] = "unsupported"
    provider_retry_control: Literal[
        "caller_bounded", "provider_managed", "uncontrolled"
    ]

    @model_validator(mode="after")
    def validate_reference_claims(self) -> "ImageProviderCapabilities":
        if not self.supports_reference_conditioning:
            if self.max_reference_images or self.accepted_reference_mime_types:
                raise ValueError("text-only provider cannot declare reference inputs")
            if self.supports_character_identity_anchor:
                raise ValueError("identity anchor requires reference conditioning")
            if self.identity_anchor_verification != "unsupported":
                raise ValueError("identity verification requires reference conditioning")
        if self.supports_character_identity_anchor:
            if self.identity_anchor_verification == "unsupported":
                raise ValueError("identity anchor requires a verification mode")
        elif self.identity_anchor_verification == "provider_static":
            raise ValueError("static identity anchor must be declared as supported")
        return self


class ReferenceConditioningConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    strength: float = Field(ge=0.0, le=1.0)


class ReferenceConditionedImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt: str = Field(min_length=1)
    canonical_reference_image_path: Path
    reference_image_sha256: str
    reference_sheet_version: int = Field(ge=1)
    character_fingerprint: str
    required_view_or_pose: str = Field(min_length=1, max_length=240)
    scene_action: str = Field(min_length=1, max_length=400)
    composition_family: str = Field(min_length=1, max_length=160)
    width: int = Field(ge=64, le=8192)
    height: int = Field(ge=64, le=8192)
    seed: int | None = None
    conditioning: ReferenceConditioningConfig

    @field_validator("reference_image_sha256", "character_fingerprint")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
            raise ValueError("expected a full SHA256 hex digest")
        return normalized


class ReferenceSheetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    image_path: Path
    image_sha256: str
    character_fingerprint: str
    provenance: str = Field(min_length=1)
    views: tuple[str, ...]
    anatomy_qc_passed: bool
    style_qc_passed: bool
    human_approved: bool
    approval_record: str = ""

    @field_validator("image_sha256", "character_fingerprint")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
            raise ValueError("expected a full SHA256 hex digest")
        return normalized

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "ReferenceSheetManifest":
        required = {"front_face", "three_quarter", "side_view", "full_body"}
        if not required.issubset(set(self.views)):
            raise ValueError("reference sheet is missing a required canonical view")
        if self.human_approved and not self.approval_record.strip():
            raise ValueError("human-approved reference requires an approval record")
        return self


_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def validate_reference_request(
    request: ReferenceConditionedImageRequest,
    capabilities: ImageProviderCapabilities,
) -> tuple[bytes, str]:
    """Validate reference bytes and capabilities without constructing a provider."""
    if not capabilities.supports_reference_conditioning:
        raise RuntimeError(
            f"provider {capabilities.provider_id} does not support reference conditioning"
        )
    if (
        not capabilities.supports_character_identity_anchor
        and capabilities.identity_anchor_verification != "per_request_verified"
    ):
        raise RuntimeError("provider lacks a proven character identity anchor")
    if capabilities.max_reference_images < 1:
        raise RuntimeError("provider accepts no reference images")
    if len(request.prompt.encode("utf-8")) > capabilities.max_prompt_utf8_bytes:
        raise ValueError("prompt exceeds provider UTF-8 byte limit")
    path = request.canonical_reference_image_path
    if not path.is_file():
        raise FileNotFoundError(f"canonical character reference is missing: {path}")
    mime = _MIME_BY_SUFFIX.get(path.suffix.lower(), "")
    if mime not in capabilities.accepted_reference_mime_types:
        raise ValueError(f"unsupported reference MIME type: {mime or 'unknown'}")
    reference_bytes = path.read_bytes()
    actual = hashlib.sha256(reference_bytes).hexdigest()
    if actual != request.reference_image_sha256:
        raise ValueError("canonical character reference SHA256 mismatch")
    return reference_bytes, mime


def validate_identity_mode(
    mode: CharacterIdentityMode | str,
    capabilities: ImageProviderCapabilities,
    *,
    reference_sheet: ReferenceSheetManifest | None = None,
) -> CharacterIdentityMode:
    selected = CharacterIdentityMode(mode)
    if selected is CharacterIdentityMode.approximate_character_continuity:
        if not capabilities.supports_text_to_image:
            raise RuntimeError("approximate continuity requires text-to-image support")
        return selected
    if selected is CharacterIdentityMode.no_recurring_character:
        return selected
    if not capabilities.supports_reference_conditioning:
        raise RuntimeError("reference-conditioned identity is unsupported by this provider")
    if (
        not capabilities.supports_character_identity_anchor
        and capabilities.identity_anchor_verification != "per_request_verified"
    ):
        raise RuntimeError("provider has no proven character identity anchor")
    if reference_sheet is None or not reference_sheet.human_approved:
        raise RuntimeError("reference-conditioned identity requires an approved reference sheet")
    if not reference_sheet.anatomy_qc_passed or not reference_sheet.style_qc_passed:
        raise RuntimeError("approved reference sheet has not passed anatomy and style QC")
    return selected


class ReferenceCapableProvider(Protocol):
    def capabilities(self) -> ImageProviderCapabilities: ...

    async def generate_reference_conditioned(
        self,
        *,
        request: ReferenceConditionedImageRequest,
        reference_bytes: bytes,
        reference_mime_type: str,
        out_path: Path,
    ) -> Any: ...


async def submit_reference_conditioned(
    request: ReferenceConditionedImageRequest,
    capabilities: ImageProviderCapabilities,
    provider_factory: Callable[[], ReferenceCapableProvider],
    *,
    out_path: Path,
    accounting: dict[str, int],
) -> Any:
    """Submit once after all fail-closed validation; never downgrade to text-only."""
    reference_bytes, mime = validate_reference_request(request, capabilities)
    provider = provider_factory()
    if provider.capabilities() != capabilities:
        raise RuntimeError("provider capability declaration changed before submission")
    accounting["image_provider_submissions"] = (
        int(accounting.get("image_provider_submissions", 0)) + 1
    )
    accounting["transport_attempts"] = int(accounting.get("transport_attempts", 0)) + 1
    return await provider.generate_reference_conditioned(
        request=request,
        reference_bytes=reference_bytes,
        reference_mime_type=mime,
        out_path=out_path,
    )


IdentityMatch = Literal["match", "soft_variation", "hard_mismatch"]


class ReferenceIdentityObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    hair_color: IdentityMatch
    hair_silhouette: IdentityMatch
    face_shape: IdentityMatch
    skin_tone: IdentityMatch
    outfit_details: IdentityMatch
    body_build: IdentityMatch
    line_style_and_palette: IdentityMatch


def evaluate_reference_identity(observation: ReferenceIdentityObservation) -> dict[str, Any]:
    values = observation.model_dump()
    hard = [name for name, value in values.items() if value == "hard_mismatch"]
    soft = [name for name, value in values.items() if value == "soft_variation"]
    return {
        "passed": not hard,
        "decision": "hard_fail" if hard else ("soft_warning" if soft else "pass"),
        "hard_mismatches": hard,
        "soft_variations": soft,
        "anchored_to_reference": True,
    }


class ReferenceVisualGates(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scene_index: int = Field(ge=1)
    identity: ReferenceIdentityObservation
    semantic_action_passed: bool
    anatomy_passed: bool
    props_passed: bool
    style_passed: bool
    composition_passed: bool
    subtitle_layout_passed: bool


def evaluate_reference_visual_gates(gates: ReferenceVisualGates) -> dict[str, Any]:
    identity = evaluate_reference_identity(gates.identity)
    independent = {
        "semantic_action": gates.semantic_action_passed,
        "anatomy": gates.anatomy_passed,
        "props": gates.props_passed,
        "style": gates.style_passed,
        "composition": gates.composition_passed,
        "subtitle_layout": gates.subtitle_layout_passed,
    }
    return {
        "scene_index": gates.scene_index,
        "passed": identity["passed"] and all(independent.values()),
        "identity": identity,
        "independent_gates": independent,
    }


def aggregate_reference_visual_gates(results: list[dict[str, Any]]) -> dict[str, Any]:
    indices = {int(item["scene_index"]) for item in results}
    complete = len(results) == 7 and indices == set(range(1, 8))
    failed = [int(item["scene_index"]) for item in results if not item.get("passed")]
    return {
        "passed": complete and not failed,
        "complete": complete,
        "failed_scene_indices": failed,
        "human_review_required": True,
        "human_review_checkpoints": ["reference_sheet", "seven_scene_contact_sheet", "final_video"],
    }


__all__ = [
    "CharacterIdentityMode", "ImageProviderCapabilities",
    "ReferenceConditionedImageRequest", "ReferenceConditioningConfig",
    "ReferenceIdentityObservation", "ReferenceSheetManifest", "ReferenceVisualGates",
    "aggregate_reference_visual_gates", "evaluate_reference_identity",
    "evaluate_reference_visual_gates", "submit_reference_conditioned",
    "validate_identity_mode", "validate_reference_request",
]
