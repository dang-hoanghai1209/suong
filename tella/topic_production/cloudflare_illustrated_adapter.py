"""Pure IllustratedSceneRequest to Cloudflare draft-preview mapping."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tella.visual_generation.models import GenerationRequest
from tella.visual_generation.providers.cloudflare_flux import (
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    CloudflareFluxSceneImageProvider,
    cloudflare_prompt,
    provider_request_hash,
)
from tella.visual_generation.tiers import VisualQualityTier, resolve_visual_tier

from .execution import deterministic_scene_seed
from .illustrated_preparation import (
    IllustratedPhysicalReference,
    build_illustrated_prompt_contract,
    illustrated_authority_meanings,
    illustrated_reference_assets,
    prepare_illustrated_reference_plan,
)
from .illustrated_request import (
    IllustratedReferenceBinding,
    IllustratedSceneRequest,
    illustrated_scene_request_hash,
)
from .strategy import SceneDataSensitivity


class CloudflareIllustratedExecutionPreview(BaseModel):
    """Sanitized zero-call proof of the future Cloudflare invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scene_id: str = Field(pattern=r"^scene_[0-9]{2}$")
    provider: Literal["cloudflare-flux"] = "cloudflare-flux"
    model: str = Field(min_length=1)
    width: int = Field(ge=64)
    height: int = Field(ge=64)
    steps: int = Field(ge=1)
    timeout_seconds: float = Field(gt=0)
    seed: int = Field(ge=0)
    illustrated_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_request_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    prompt: str = ""
    provider_request: GenerationRequest | None = None
    semantic_bindings: list[IllustratedReferenceBinding] = Field(default_factory=list)
    reference_uploads: list[IllustratedPhysicalReference] = Field(
        default_factory=list
    )
    blocked: bool
    reason: str = Field(min_length=1)
    privacy_authorized_for_cloudflare: bool
    transport_authorized: Literal[False] = False
    external_calls: Literal[0] = 0
    provider_reaching_calls: Literal[0] = 0

    @model_validator(mode="after")
    def validate_blocked_boundary(self) -> "CloudflareIllustratedExecutionPreview":
        if self.blocked:
            if self.privacy_authorized_for_cloudflare:
                raise ValueError("blocked preview cannot authorize Cloudflare privacy boundary")
            if self.provider_request is not None or self.provider_request_hash is not None:
                raise ValueError("blocked preview cannot contain a provider request")
            if self.prompt or self.reference_uploads:
                raise ValueError("blocked preview cannot prepare transport inputs")
        else:
            if not self.privacy_authorized_for_cloudflare:
                raise ValueError("unblocked preview requires Cloudflare privacy authorization")
            if self.provider_request is None or self.provider_request_hash is None:
                raise ValueError("unblocked preview requires a complete provider request")
        return self


def build_cloudflare_illustrated_preview(
    request: IllustratedSceneRequest,
) -> CloudflareIllustratedExecutionPreview:
    """Map an illustrated request to an exact zero-call Cloudflare draft preview."""

    tier = resolve_visual_tier(VisualQualityTier.DRAFT)
    request_identity = illustrated_scene_request_hash(request)
    seed = deterministic_scene_seed(request.semantic_scene.order)
    reference_plan = prepare_illustrated_reference_plan(request)
    if request.sensitivity is SceneDataSensitivity.LOCAL_ONLY:
        return CloudflareIllustratedExecutionPreview(
            scene_id=request.scene_id,
            model=tier.model,
            width=DEFAULT_WIDTH,
            height=DEFAULT_HEIGHT,
            steps=tier.steps,
            timeout_seconds=tier.timeout_seconds,
            seed=seed,
            illustrated_request_hash=request_identity,
            semantic_bindings=reference_plan.semantic_bindings,
            blocked=True,
            reason="LOCAL_ONLY illustrated request cannot be externalized to Cloudflare",
            privacy_authorized_for_cloudflare=False,
        )

    uploads = reference_plan.physical_references
    provider = CloudflareFluxSceneImageProvider(
        model=tier.model,
        width=DEFAULT_WIDTH,
        height=DEFAULT_HEIGHT,
        steps=tier.steps,
        timeout_seconds=tier.timeout_seconds,
        tier=tier.tier.value,
        intended_usage_class=tier.output_intent,
        allow_text_only=True,
        credential_resolver=lambda: [],
    )
    if len(uploads) > provider.capabilities().max_reference_images:
        raise ValueError("illustrated reference upload plan exceeds Cloudflare capacity")
    reference_guidance = [
        f"Reference image {physical.slot}: "
        f"{'; '.join(illustrated_authority_meanings(physical))}."
        for physical in reference_plan.physical_references
    ]
    prompt_contract = build_illustrated_prompt_contract(
        request,
        reference_guidance=reference_guidance,
    )
    references = illustrated_reference_assets(reference_plan)
    provider_request = GenerationRequest(
        scene_id=request.scene_id,
        candidate_index=1,
        attempt=1,
        width=DEFAULT_WIDTH,
        height=DEFAULT_HEIGHT,
        aspect_ratio="9:16",
        instruction=prompt_contract.instruction,
        negative_instruction=prompt_contract.negative_instruction,
        references=references,
        seed=seed,
        reference_authority_contract="illustrated_scene_v1",
    )
    prompt = cloudflare_prompt(provider_request)
    invocation_hash = provider_request_hash(
        request=provider_request,
        prompt=prompt,
        model=tier.model,
        width=DEFAULT_WIDTH,
        height=DEFAULT_HEIGHT,
        steps=tier.steps,
        logical_request_hash=request_identity,
    )
    return CloudflareIllustratedExecutionPreview(
        scene_id=request.scene_id,
        model=tier.model,
        width=DEFAULT_WIDTH,
        height=DEFAULT_HEIGHT,
        steps=tier.steps,
        timeout_seconds=tier.timeout_seconds,
        seed=seed,
        illustrated_request_hash=request_identity,
        provider_request_hash=invocation_hash,
        prompt=prompt,
        provider_request=provider_request,
        semantic_bindings=reference_plan.semantic_bindings,
        reference_uploads=uploads,
        blocked=False,
        reason="deterministic Cloudflare illustrated draft preview; transport not authorized",
        privacy_authorized_for_cloudflare=True,
    )


CloudflareReferenceUploadPlan = IllustratedPhysicalReference


__all__ = [
    "CloudflareIllustratedExecutionPreview",
    "CloudflareReferenceUploadPlan",
    "build_cloudflare_illustrated_preview",
]
