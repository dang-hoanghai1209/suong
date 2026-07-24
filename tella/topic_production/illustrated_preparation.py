"""Provider-neutral reference preparation and illustrated prompt semantics."""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tella.visual_generation.models import ReferenceAsset

from .illustrated_request import (
    IllustratedPromptProfile,
    IllustratedReferenceAuthority,
    IllustratedReferenceBinding,
    IllustratedSceneRequest,
)


class IllustratedPhysicalReference(BaseModel):
    """One physical input retaining every approved semantic binding."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slot: int = Field(ge=0)
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_ids: list[str] = Field(min_length=1)
    declared_roles: list[str] = Field(min_length=1)
    authorities: list[IllustratedReferenceAuthority] = Field(min_length=1)
    bindings: list[IllustratedReferenceBinding] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_binding_identity(self) -> "IllustratedPhysicalReference":
        if any(binding.reference.sha256 != self.sha256 for binding in self.bindings):
            raise ValueError("physical reference bindings must share one content hash")
        if self.declared_roles != [binding.declared_role for binding in self.bindings]:
            raise ValueError("physical reference role provenance does not match bindings")
        if self.authorities != [binding.authority for binding in self.bindings]:
            raise ValueError("physical reference authority provenance does not match bindings")
        return self


class IllustratedReferencePlan(BaseModel):
    """Deterministically ordered semantics and SHA-deduplicated physical inputs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    semantic_bindings: list[IllustratedReferenceBinding] = Field(default_factory=list)
    physical_references: list[IllustratedPhysicalReference] = Field(default_factory=list)


class IllustratedPromptContract(BaseModel):
    """Provider-neutral complete-scene visual instructions."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    instruction: str = Field(min_length=1)
    negative_instruction: str = Field(min_length=1)


def prepare_illustrated_reference_plan(
    request: IllustratedSceneRequest,
) -> IllustratedReferencePlan:
    """Order semantic bindings and deduplicate physical inputs by approved SHA."""

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

    bindings = [
        *ordered(request.character_identity_anchors),
        *ordered(request.style_anchors),
        *ordered(request.composition_references),
    ]
    grouped: dict[str, list[IllustratedReferenceBinding]] = {}
    order: list[str] = []
    for binding in bindings:
        digest = binding.reference.sha256
        if digest not in grouped:
            grouped[digest] = []
            order.append(digest)
        grouped[digest].append(binding)

    physical_references = []
    for slot, digest in enumerate(order):
        physical_bindings = grouped[digest]
        physical_references.append(
            IllustratedPhysicalReference(
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
    return IllustratedReferencePlan(
        semantic_bindings=bindings,
        physical_references=physical_references,
    )


def illustrated_reference_assets(
    plan: IllustratedReferencePlan,
) -> list[ReferenceAsset]:
    """Map physical references into the existing provider-neutral transport model."""

    return [
        ReferenceAsset(
            role=physical.declared_roles[0],
            semantic_roles=physical.declared_roles,
            path=Path(physical.path),
            sha256=physical.sha256,
            source=(
                "master"
                if any(
                    authority
                    in {
                        IllustratedReferenceAuthority.CHARACTER_IDENTITY,
                        IllustratedReferenceAuthority.STYLE,
                    }
                    for authority in physical.authorities
                )
                else "scene_type"
            ),
            priority=min(
                binding.reference.priority for binding in physical.bindings
            ),
        )
        for physical in plan.physical_references
    ]


def build_illustrated_prompt_contract(
    request: IllustratedSceneRequest,
    *,
    reference_guidance: list[str] | None = None,
) -> IllustratedPromptContract:
    """Build one authoritative visual contract for every illustrated provider."""

    scene = request.semantic_scene
    sections = [
        (
            "COMPLETE SCENE",
            "Create one cohesive finished illustration representing the whole scene. "
            "Integrate the character, environment, objects, and symbols as one artwork, "
            "never as pasted elements or an assembled collage.",
        ),
        _visual_profile_section(request.prompt_profile),
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
            "; ".join(
                f"{key}: {value}" for key, value in sorted(scene.interaction.items())
            )
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
    if reference_guidance:
        sections.insert(3, ("REFERENCE AUTHORITY", " ".join(reference_guidance)))
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
    return IllustratedPromptContract(
        instruction=instruction,
        negative_instruction="; ".join(negatives),
    )


def _visual_profile_section(
    profile: IllustratedPromptProfile,
) -> tuple[str, str]:
    if profile is IllustratedPromptProfile.STANDARD:
        return (
            "STYLE INTENT",
            "Soft hand-drawn emotional editorial illustration; simple polished 2D design; "
            "warm muted earthy and pastel colors; gentle melancholic and healing atmosphere; "
            "cohesive integrated environment.",
        )
    return (
        "AUTHORITATIVE VISUAL TARGET",
        "Legacy accepted visual target: use a dark warm-brown/taupe atmospheric field "
        "with a restrained irregular cream focal treatment or vignette. Scale and place "
        "the focal region according to the current scene's authoritative composition. "
        "Use emotionally intentional negative space and editorial asymmetry, thin imperfect "
        "charcoal/warm-brown hand-drawn lines, matte muted pastel color, tactile paper-grain "
        "and chalky texture, simplified emotional editorial illustration, and an intimate "
        "quiet melancholic yet healing tone.",
    )


def illustrated_authority_meanings(
    physical: IllustratedPhysicalReference,
) -> list[str]:
    """Describe neutral semantic authority without provider reference syntax."""

    purposes = []
    if IllustratedReferenceAuthority.CHARACTER_IDENTITY in physical.authorities:
        purposes.append(
            "character identity only: hairstyle, simplified face, outfit, "
            "silhouette, and proportions; not scene composition"
        )
    if IllustratedReferenceAuthority.STYLE in physical.authorities:
        purposes.append(
            "visual style only: drawing language, line treatment, palette, "
            "texture, and atmosphere; not character identity"
        )
    if IllustratedReferenceAuthority.COMPOSITION in physical.authorities:
        purposes.append(
            "non-authoritative composition inspiration only; do not copy the source layout"
        )
    return purposes


__all__ = [
    "IllustratedPhysicalReference",
    "IllustratedPromptContract",
    "IllustratedReferencePlan",
    "build_illustrated_prompt_contract",
    "illustrated_authority_meanings",
    "illustrated_reference_assets",
    "prepare_illustrated_reference_plan",
]
