"""Pure IllustratedSceneRequest to Cloudflare draft-preview mapping."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tella.visual_generation.models import GenerationRequest, ReferenceAsset
from tella.visual_generation.providers.cloudflare_flux import (
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    CloudflareFluxSceneImageProvider,
    cloudflare_prompt,
    provider_request_hash,
)
from tella.visual_generation.tiers import VisualQualityTier, resolve_visual_tier

from .execution import deterministic_scene_seed
from .illustrated_request import (
    IllustratedReferenceAuthority,
    IllustratedReferenceBinding,
    IllustratedSceneRequest,
    illustrated_scene_request_hash,
)
from .strategy import SceneDataSensitivity


class CloudflareReferenceUploadPlan(BaseModel):
    """One physical multipart slot with all approved semantic authorities retained."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slot: int = Field(ge=0)
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_ids: list[str] = Field(min_length=1)
    declared_roles: list[str] = Field(min_length=1)
    authorities: list[IllustratedReferenceAuthority] = Field(min_length=1)
    bindings: list[IllustratedReferenceBinding] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_binding_identity(self) -> "CloudflareReferenceUploadPlan":
        if any(binding.reference.sha256 != self.sha256 for binding in self.bindings):
            raise ValueError("physical upload bindings must share one content hash")
        if self.declared_roles != [binding.declared_role for binding in self.bindings]:
            raise ValueError("physical upload role provenance does not match bindings")
        if self.authorities != [binding.authority for binding in self.bindings]:
            raise ValueError("physical upload authority provenance does not match bindings")
        return self


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
    reference_uploads: list[CloudflareReferenceUploadPlan] = Field(
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


def _ordered_bindings(
    request: IllustratedSceneRequest,
) -> list[IllustratedReferenceBinding]:
    def ordered(
        values: list[IllustratedReferenceBinding],
    ) -> list[IllustratedReferenceBinding]:
        return sorted(
            values,
            key=lambda item: (
                item.reference.priority,
                item.reference.reference_id,
                item.declared_role,
                item.reference.sha256,
            ),
        )

    return [
        *ordered(request.character_identity_anchors),
        *ordered(request.style_anchors),
        *ordered(request.composition_references),
    ]


def _upload_plan(
    request: IllustratedSceneRequest,
) -> tuple[list[IllustratedReferenceBinding], list[CloudflareReferenceUploadPlan]]:
    bindings = _ordered_bindings(request)
    grouped: dict[str, list[IllustratedReferenceBinding]] = {}
    order: list[str] = []
    for binding in bindings:
        digest = binding.reference.sha256
        if digest not in grouped:
            grouped[digest] = []
            order.append(digest)
        grouped[digest].append(binding)
    uploads: list[CloudflareReferenceUploadPlan] = []
    for slot, digest in enumerate(order):
        physical_bindings = grouped[digest]
        uploads.append(
            CloudflareReferenceUploadPlan(
                slot=slot,
                path=physical_bindings[0].reference.path,
                sha256=digest,
                reference_ids=list(
                    dict.fromkeys(
                        binding.reference.reference_id
                        for binding in physical_bindings
                    )
                ),
                declared_roles=[
                    binding.declared_role for binding in physical_bindings
                ],
                authorities=[binding.authority for binding in physical_bindings],
                bindings=physical_bindings,
            )
        )
    return bindings, uploads


def _authority_guidance(uploads: list[CloudflareReferenceUploadPlan]) -> list[str]:
    guidance: list[str] = []
    for upload in uploads:
        purposes: list[str] = []
        if IllustratedReferenceAuthority.CHARACTER_IDENTITY in upload.authorities:
            purposes.append(
                "character identity only: hairstyle, simplified face, outfit, "
                "silhouette, and proportions; not scene composition"
            )
        if IllustratedReferenceAuthority.STYLE in upload.authorities:
            purposes.append(
                "visual style only: drawing language, line treatment, palette, "
                "texture, and atmosphere; not character identity"
            )
        if IllustratedReferenceAuthority.COMPOSITION in upload.authorities:
            purposes.append(
                "non-authoritative composition inspiration only; do not copy the source layout"
            )
        guidance.append(f"Reference image {upload.slot}: {'; '.join(purposes)}.")
    return guidance


def _complete_scene_instructions(
    request: IllustratedSceneRequest,
    uploads: list[CloudflareReferenceUploadPlan],
) -> tuple[str, str]:
    scene = request.semantic_scene
    sections = [
        (
            "COMPLETE SCENE",
            "Create one cohesive finished illustration representing the whole scene. "
            "Integrate the character, environment, objects, and symbols as one artwork, "
            "never as pasted elements or an assembled collage.",
        ),
        (
            "STYLE INTENT",
            "Soft hand-drawn emotional editorial illustration; simple polished 2D design; "
            "warm muted earthy and pastel colors; gentle melancholic and healing atmosphere; "
            "cohesive integrated environment.",
        ),
        (
            "CHARACTER IDENTITY",
            "; ".join(scene.identity_requirements)
            or "Use a simple, stable, recognizable character design.",
        ),
        ("STORY BEAT", scene.meaning),
        ("NARRATIVE CONTEXT", scene.narrative_text),
        ("EMOTIONAL INTENT", "; ".join(scene.emotional_tone)),
        ("ACTION", "; ".join(scene.action) or "one restrained readable action"),
        (
            "INTERACTION",
            "; ".join(f"{key}: {value}" for key, value in sorted(scene.interaction.items()))
            or "none required",
        ),
        ("ENVIRONMENT", "; ".join(scene.environment) or "minimal integrated environment"),
        ("OBJECTS", "; ".join(scene.objects) or "none required"),
        ("VISUAL METAPHOR", "; ".join(scene.symbols) or "none required"),
        (
            "COMPOSITION",
            "; ".join(
                [
                    *scene.composition,
                    *scene.negative_space_requirements,
                    *scene.visual_hierarchy,
                ]
            )
            or "one coherent vertical composition",
        ),
    ]
    authority_guidance = _authority_guidance(uploads)
    if authority_guidance:
        sections.insert(3, ("REFERENCE AUTHORITY", " ".join(authority_guidance)))
    instruction = "\n\n".join(f"[{title}]\n{body}" for title, body in sections)
    negatives = list(
        dict.fromkeys(
            [
                *scene.hard_negatives,
                "no realism",
                "no anime",
                "no 3D rendering",
                "no readable text, logo, watermark, caption, or UI",
                "no pasted character, pasted object, sprite, prefab layer, or collage appearance",
            ]
        )
    )
    return instruction, "; ".join(negatives)


def build_cloudflare_illustrated_preview(
    request: IllustratedSceneRequest,
) -> CloudflareIllustratedExecutionPreview:
    """Map an illustrated request to an exact zero-call Cloudflare draft preview."""

    tier = resolve_visual_tier(VisualQualityTier.DRAFT)
    request_identity = illustrated_scene_request_hash(request)
    seed = deterministic_scene_seed(request.semantic_scene.order)
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
            semantic_bindings=_ordered_bindings(request),
            blocked=True,
            reason="LOCAL_ONLY illustrated request cannot be externalized to Cloudflare",
            privacy_authorized_for_cloudflare=False,
        )

    bindings, uploads = _upload_plan(request)
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
    instruction, negative_instruction = _complete_scene_instructions(request, uploads)
    references = [
        ReferenceAsset(
            role=upload.declared_roles[0],
            semantic_roles=upload.declared_roles,
            path=Path(upload.path),
            sha256=upload.sha256,
            source=(
                "master"
                if any(
                    authority
                    in {
                        IllustratedReferenceAuthority.CHARACTER_IDENTITY,
                        IllustratedReferenceAuthority.STYLE,
                    }
                    for authority in upload.authorities
                )
                else "scene_type"
            ),
            priority=min(binding.reference.priority for binding in upload.bindings),
        )
        for upload in uploads
    ]
    provider_request = GenerationRequest(
        scene_id=request.scene_id,
        candidate_index=1,
        attempt=1,
        width=DEFAULT_WIDTH,
        height=DEFAULT_HEIGHT,
        aspect_ratio="9:16",
        instruction=instruction,
        negative_instruction=negative_instruction,
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
        semantic_bindings=bindings,
        reference_uploads=uploads,
        blocked=False,
        reason="deterministic Cloudflare illustrated draft preview; transport not authorized",
        privacy_authorized_for_cloudflare=True,
    )


__all__ = [
    "CloudflareIllustratedExecutionPreview",
    "CloudflareReferenceUploadPlan",
    "build_cloudflare_illustrated_preview",
]
