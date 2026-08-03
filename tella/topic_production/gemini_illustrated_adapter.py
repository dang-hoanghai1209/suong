"""Pure IllustratedSceneRequest to Gemini 3 Pro parity-preview mapping."""
from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tella.visual_generation.models import GenerationRequest
from tella.visual_generation.providers.gemini import (
    PRO_IMAGE_MODEL,
    gemini_image_model_contract,
    gemini_prompt,
    gemini_reference_mime_type,
)

from .illustrated_preparation import (
    IllustratedPhysicalReference,
    build_illustrated_prompt_contract,
    illustrated_authority_meanings,
    illustrated_reference_assets,
    prepare_illustrated_reference_plan,
)
from .illustrated_request import (
    IllustratedReferenceAuthority,
    IllustratedReferenceBinding,
    IllustratedSceneRequest,
    illustrated_scene_request_hash,
)
from .strategy import SceneDataSensitivity

GEMINI_PRO_IMAGE_SIZE = "1K"
GEMINI_PRO_ASPECT_RATIO = "9:16"
GEMINI_PRO_EXPECTED_WIDTH = 768
GEMINI_PRO_EXPECTED_HEIGHT = 1376


class GeminiBillingReadiness(StrEnum):
    UNKNOWN = "BILLING_READINESS_UNKNOWN"
    REQUIRES_CONFIRMATION = "PAID_BILLING_REQUIRES_TRUSTED_CONFIRMATION"


class GeminiPrivacyState(StrEnum):
    PUBLIC_SAFE = "PUBLIC_SAFE_PREVIEW"
    PRIVATE_REQUIRES_CONFIRMATION = "PRIVATE_PROVIDER_USE_REQUIRES_CONFIRMATION"
    LOCAL_ONLY_BLOCKED = "LOCAL_ONLY_PROVIDER_USE_BLOCKED"


class GeminiIllustratedExecutionPreview(BaseModel):
    """Deterministic local evidence for a future one-call Pro invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scene_id: str = Field(pattern=r"^scene_[0-9]{2}$")
    provider: Literal["gemini"] = "gemini"
    model: Literal["gemini-3-pro-image"] = PRO_IMAGE_MODEL
    aspect_ratio: Literal["9:16"] = GEMINI_PRO_ASPECT_RATIO
    image_size: Literal["1K"] = GEMINI_PRO_IMAGE_SIZE
    expected_width: Literal[768] = GEMINI_PRO_EXPECTED_WIDTH
    expected_height: Literal[1376] = GEMINI_PRO_EXPECTED_HEIGHT
    seed: Literal[None] = None
    determinism_limitation: Literal[
        "Gemini 3 Pro Image exposes no documented provider seed"
    ] = "Gemini 3 Pro Image exposes no documented provider seed"
    illustrated_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_request_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    prompt: str = ""
    provider_request: GenerationRequest | None = None
    semantic_bindings: list[IllustratedReferenceBinding] = Field(default_factory=list)
    reference_uploads: list[IllustratedPhysicalReference] = Field(default_factory=list)
    accepted_scene_chaining: Literal[False] = False
    billing_readiness: GeminiBillingReadiness
    privacy_state: GeminiPrivacyState
    privacy_blocked: bool
    reason: str = Field(min_length=1)
    transport_authorized: Literal[False] = False

    @model_validator(mode="after")
    def validate_privacy_boundary(self) -> "GeminiIllustratedExecutionPreview":
        if self.privacy_state is GeminiPrivacyState.LOCAL_ONLY_BLOCKED:
            if self.provider_request is not None or self.reference_uploads or self.prompt:
                raise ValueError("LOCAL_ONLY preview cannot prepare Gemini transport inputs")
        elif self.provider_request is None or self.provider_request_hash is None:
            raise ValueError("externalizable preview requires deterministic request evidence")
        if self.privacy_state is GeminiPrivacyState.PRIVATE_REQUIRES_CONFIRMATION:
            if (
                self.billing_readiness
                is not GeminiBillingReadiness.REQUIRES_CONFIRMATION
                or not self.privacy_blocked
            ):
                raise ValueError("PRIVATE Gemini preview must remain fail-closed")
        return self


def _validate_reference_limits(
    physical_references: list[IllustratedPhysicalReference],
) -> None:
    contract = gemini_image_model_contract(PRO_IMAGE_MODEL)
    if len(physical_references) > contract.max_reference_images:
        raise ValueError("Gemini Pro total reference limit exceeded")
    category_limits = (
        (
            IllustratedReferenceAuthority.CHARACTER_IDENTITY,
            contract.max_character_references,
            "character",
        ),
        (
            IllustratedReferenceAuthority.STYLE,
            contract.max_style_references,
            "style",
        ),
        (
            IllustratedReferenceAuthority.COMPOSITION,
            contract.max_context_references,
            "contextual",
        ),
    )
    for authority, maximum, label in category_limits:
        count = sum(
            authority in physical.authorities for physical in physical_references
        )
        if count > maximum:
            raise ValueError(f"Gemini Pro {label} reference limit exceeded")


def gemini_provider_request_hash(
    *,
    model: str,
    aspect_ratio: str,
    image_size: str,
    expected_width: int,
    expected_height: int,
    prompt: str,
    logical_request_hash: str,
    reference_uploads: list[IllustratedPhysicalReference],
) -> str:
    """Hash established Gemini provenance plus the submitted inline MIME values."""

    payload = {
        "provider": "gemini",
        "model": model,
        "response_format": {
            "type": "image",
            "mime_type": "image/jpeg",
            "aspect_ratio": aspect_ratio,
            "image_size": image_size,
        },
        "expected_geometry": [expected_width, expected_height],
        "prompt": prompt,
        "logical_request_hash": logical_request_hash,
        "references": [
            {
                "slot": physical.slot,
                "sha256": physical.sha256,
                "mime_type": gemini_reference_mime_type(Path(physical.path)),
                "reference_ids": physical.reference_ids,
                "declared_roles": physical.declared_roles,
                "authorities": [
                    authority.value for authority in physical.authorities
                ],
            }
            for physical in reference_uploads
        ],
        "seed": None,
        "previous_interaction_id": None,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_gemini_illustrated_preview(
    request: IllustratedSceneRequest,
) -> GeminiIllustratedExecutionPreview:
    """Build deterministic Gemini Pro request evidence without reading image bytes."""

    contract = gemini_image_model_contract(PRO_IMAGE_MODEL)
    if GEMINI_PRO_IMAGE_SIZE not in contract.supported_resolutions:
        raise RuntimeError("Gemini Pro parity resolution is outside the model contract")
    request_identity = illustrated_scene_request_hash(request)
    reference_plan = prepare_illustrated_reference_plan(request)
    if request.sensitivity is SceneDataSensitivity.LOCAL_ONLY:
        return GeminiIllustratedExecutionPreview(
            scene_id=request.scene_id,
            illustrated_request_hash=request_identity,
            semantic_bindings=reference_plan.semantic_bindings,
            billing_readiness=GeminiBillingReadiness.UNKNOWN,
            privacy_state=GeminiPrivacyState.LOCAL_ONLY_BLOCKED,
            privacy_blocked=True,
            reason="LOCAL_ONLY illustrated request cannot be externalized to Gemini",
        )

    _validate_reference_limits(reference_plan.physical_references)
    reference_guidance = [
        f"Reference image {physical.slot}: "
        f"{'; '.join(illustrated_authority_meanings(physical))}."
        for physical in reference_plan.physical_references
    ]
    prompt_contract = build_illustrated_prompt_contract(
        request,
        reference_guidance=reference_guidance,
    )
    provider_request = GenerationRequest(
        scene_id=request.scene_id,
        candidate_index=1,
        attempt=1,
        width=GEMINI_PRO_EXPECTED_WIDTH,
        height=GEMINI_PRO_EXPECTED_HEIGHT,
        aspect_ratio=GEMINI_PRO_ASPECT_RATIO,
        instruction=(
            f"{prompt_contract.instruction}\n\n[NEGATIVE CONSTRAINTS]\n"
            f"{prompt_contract.negative_instruction}"
        ),
        negative_instruction="Constraints are included in the complete semantic prompt.",
        references=illustrated_reference_assets(reference_plan),
        seed=None,
        reference_authority_contract="illustrated_scene_v1",
    )
    prompt = gemini_prompt(provider_request)
    request_hash = gemini_provider_request_hash(
        model=PRO_IMAGE_MODEL,
        aspect_ratio=GEMINI_PRO_ASPECT_RATIO,
        image_size=GEMINI_PRO_IMAGE_SIZE,
        expected_width=GEMINI_PRO_EXPECTED_WIDTH,
        expected_height=GEMINI_PRO_EXPECTED_HEIGHT,
        prompt=prompt,
        logical_request_hash=request_identity,
        reference_uploads=reference_plan.physical_references,
    )
    is_private = request.sensitivity is SceneDataSensitivity.PRIVATE
    return GeminiIllustratedExecutionPreview(
        scene_id=request.scene_id,
        illustrated_request_hash=request_identity,
        provider_request_hash=request_hash,
        prompt=prompt,
        provider_request=provider_request,
        semantic_bindings=reference_plan.semantic_bindings,
        reference_uploads=reference_plan.physical_references,
        billing_readiness=(
            GeminiBillingReadiness.REQUIRES_CONFIRMATION
            if is_private
            else GeminiBillingReadiness.UNKNOWN
        ),
        privacy_state=(
            GeminiPrivacyState.PRIVATE_REQUIRES_CONFIRMATION
            if is_private
            else GeminiPrivacyState.PUBLIC_SAFE
        ),
        privacy_blocked=is_private,
        reason=(
            "PRIVATE Gemini transport requires later trusted paid-billing confirmation"
            if is_private
            else "PUBLIC_SAFE preview prepared; transport remains unauthorized"
        ),
    )


GeminiReferenceUploadPlan = IllustratedPhysicalReference


__all__ = [
    "GEMINI_PRO_ASPECT_RATIO",
    "GEMINI_PRO_EXPECTED_HEIGHT",
    "GEMINI_PRO_EXPECTED_WIDTH",
    "GEMINI_PRO_IMAGE_SIZE",
    "GeminiBillingReadiness",
    "GeminiIllustratedExecutionPreview",
    "GeminiPrivacyState",
    "GeminiReferenceUploadPlan",
    "build_gemini_illustrated_preview",
    "gemini_provider_request_hash",
]
