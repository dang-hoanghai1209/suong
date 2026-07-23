"""Provider-neutral complete-scene request and explicit anchor authority."""
from __future__ import annotations

from enum import StrEnum
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .execution_models import (
    ApprovedReference,
    ProductionRunPlan,
    ReferenceDecisionStatus,
)
from .models import ProductionSceneBrief
from .strategy import SceneDataSensitivity, VisualExecutionMode
from .visual_adapter import required_reference_roles


class IllustratedReferenceAuthority(StrEnum):
    STYLE = "style"
    CHARACTER_IDENTITY = "character_identity"
    COMPOSITION = "composition"


def reference_authority_for_role(role: str) -> IllustratedReferenceAuthority:
    if role == "style_anchor":
        return IllustratedReferenceAuthority.STYLE
    if "identity_anchor" in role:
        return IllustratedReferenceAuthority.CHARACTER_IDENTITY
    return IllustratedReferenceAuthority.COMPOSITION


class IllustratedReferenceBinding(BaseModel):
    """One explicitly approved role assignment for one physical reference."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    authority: IllustratedReferenceAuthority
    declared_role: str = Field(min_length=1)
    reference: ApprovedReference

    @model_validator(mode="after")
    def validate_explicit_authority(self) -> "IllustratedReferenceBinding":
        if self.declared_role not in self.reference.roles:
            raise ValueError("reference role authority must be explicitly declared")
        expected = reference_authority_for_role(self.declared_role)
        if self.authority is not expected:
            raise ValueError("reference authority does not match its explicitly declared role")
        if (
            self.authority is IllustratedReferenceAuthority.STYLE
            and not self.reference.style_scope
        ):
            raise ValueError("style anchor requires an explicit style scope")
        if (
            self.authority is IllustratedReferenceAuthority.CHARACTER_IDENTITY
            and not self.reference.identity_scope
        ):
            raise ValueError("character identity anchor requires an explicit identity scope")
        return self


class IllustratedSceneRequest(BaseModel):
    """Three-source request contract before any provider-specific adaptation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_profile: Literal["illustrated_scene_v1"] = "illustrated_scene_v1"
    scene_id: str = Field(pattern=r"^scene_[0-9]{2}$")
    semantic_scene: ProductionSceneBrief
    sensitivity: SceneDataSensitivity
    required_reference_roles: list[str] = Field(default_factory=list)
    style_anchors: list[IllustratedReferenceBinding] = Field(default_factory=list)
    character_identity_anchors: list[IllustratedReferenceBinding] = Field(
        default_factory=list
    )
    composition_references: list[IllustratedReferenceBinding] = Field(
        default_factory=list
    )
    accepted_scene_chaining: Literal[False] = False

    @property
    def externalizable(self) -> bool:
        return self.sensitivity is not SceneDataSensitivity.LOCAL_ONLY

    @model_validator(mode="after")
    def validate_sources_and_privacy(self) -> "IllustratedSceneRequest":
        if self.semantic_scene.scene_id != self.scene_id:
            raise ValueError("semantic scene ID must match illustrated request scene ID")
        if len(self.required_reference_roles) != len(set(self.required_reference_roles)):
            raise ValueError("required reference roles must be unique")
        buckets = (
            (IllustratedReferenceAuthority.STYLE, self.style_anchors),
            (
                IllustratedReferenceAuthority.CHARACTER_IDENTITY,
                self.character_identity_anchors,
            ),
            (IllustratedReferenceAuthority.COMPOSITION, self.composition_references),
        )
        bindings: list[IllustratedReferenceBinding] = []
        for authority, bucket in buckets:
            if any(binding.authority is not authority for binding in bucket):
                raise ValueError("reference binding is stored under the wrong authority")
            bindings.extend(bucket)
        keys = [
            (binding.authority, binding.declared_role, binding.reference.sha256)
            for binding in bindings
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate illustrated reference binding")
        undeclared = {
            binding.declared_role
            for binding in bindings
            if binding.declared_role not in self.required_reference_roles
        }
        if undeclared:
            raise ValueError(
                f"selected reference roles are not required by the scene: {sorted(undeclared)}"
            )
        selected_roles = {binding.declared_role for binding in bindings}
        required_style = {
            role for role in self.required_reference_roles if role == "style_anchor"
        }
        required_identity = {
            role for role in self.required_reference_roles if "identity_anchor" in role
        }
        missing_authoritative = (required_style | required_identity) - selected_roles
        if missing_authoritative:
            raise ValueError(
                "required authoritative anchors are missing: "
                + ", ".join(sorted(missing_authoritative))
            )
        if self.sensitivity is SceneDataSensitivity.PUBLIC_SAFE and any(
            binding.reference.sensitivity is not SceneDataSensitivity.PUBLIC_SAFE
            for binding in bindings
        ):
            raise ValueError("PUBLIC_SAFE illustrated request cannot carry private/local references")
        if self.externalizable and any(
            binding.reference.sensitivity is SceneDataSensitivity.LOCAL_ONLY
            for binding in bindings
        ):
            raise ValueError("externalizable illustrated request cannot carry LOCAL_ONLY references")
        return self


def build_illustrated_scene_request(
    run_plan: ProductionRunPlan,
    *,
    scene_id: str,
) -> IllustratedSceneRequest:
    """Build an illustrated request without provider adaptation or transport."""

    if (
        run_plan.production_strategy.visual_mode
        is not VisualExecutionMode.ILLUSTRATED_SCENE
    ):
        raise ValueError("IllustratedSceneRequest requires ILLUSTRATED_SCENE mode")
    scene = next(
        (item for item in run_plan.scene_execution_plans if item.scene_id == scene_id),
        None,
    )
    if scene is None:
        raise ValueError(f"unknown scene ID: {scene_id}")
    if scene.routing is None:
        raise ValueError("illustrated request requires a sensitivity-aware scene route")
    required_roles = required_reference_roles(scene.scene_brief)
    references_by_id = {
        reference.reference_id: reference for reference in scene.draft.references
    }
    bindings: list[IllustratedReferenceBinding] = []
    for decision in scene.draft.reference_decisions:
        if decision.status is not ReferenceDecisionStatus.SELECTED:
            continue
        assert decision.reference_id is not None
        reference = references_by_id.get(decision.reference_id)
        if reference is None:
            raise ValueError("selected reference decision has no approved reference")
        authority = reference_authority_for_role(decision.role)
        bindings.append(
            IllustratedReferenceBinding(
                authority=authority,
                declared_role=decision.role,
                reference=reference,
            )
        )
    return IllustratedSceneRequest(
        scene_id=scene.scene_id,
        semantic_scene=scene.scene_brief,
        sensitivity=scene.routing.sensitivity,
        required_reference_roles=required_roles,
        style_anchors=[
            item
            for item in bindings
            if item.authority is IllustratedReferenceAuthority.STYLE
        ],
        character_identity_anchors=[
            item
            for item in bindings
            if item.authority is IllustratedReferenceAuthority.CHARACTER_IDENTITY
        ],
        composition_references=[
            item
            for item in bindings
            if item.authority is IllustratedReferenceAuthority.COMPOSITION
        ],
    )


def illustrated_scene_request_hash(request: IllustratedSceneRequest) -> str:
    """Hash authoritative semantics and reference content, never local paths."""

    def anchor_identity(binding: IllustratedReferenceBinding) -> dict[str, object]:
        reference = binding.reference
        return {
            "authority": binding.authority.value,
            "declared_role": binding.declared_role,
            "reference_id": reference.reference_id,
            "sha256": reference.sha256,
            "approved_roles": sorted(reference.roles),
            "sensitivity": reference.sensitivity.value,
            "identity_scope": reference.identity_scope,
            "style_scope": reference.style_scope,
            "priority": reference.priority,
        }

    payload = {
        "request_profile": request.request_profile,
        "scene_id": request.scene_id,
        "semantic_scene": request.semantic_scene.model_dump(mode="json"),
        "sensitivity": request.sensitivity.value,
        "required_reference_roles": request.required_reference_roles,
        "style_anchors": [anchor_identity(item) for item in request.style_anchors],
        "character_identity_anchors": [
            anchor_identity(item) for item in request.character_identity_anchors
        ],
        "composition_references": [
            anchor_identity(item) for item in request.composition_references
        ],
        "accepted_scene_chaining": request.accepted_scene_chaining,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "IllustratedReferenceAuthority",
    "IllustratedReferenceBinding",
    "IllustratedSceneRequest",
    "build_illustrated_scene_request",
    "illustrated_scene_request_hash",
    "reference_authority_for_role",
]
