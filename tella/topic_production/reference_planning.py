"""Validated approved-static-reference catalog and semantic resolution."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tella.visual_generation.references import sha256_file

from .execution_models import (
    ApprovedReference,
    ReferenceCatalog,
    ReferenceDecision,
    ReferenceDecisionStatus,
)
from .models import ProductionSceneBrief, SceneType
from .visual_adapter import required_reference_roles


class ApprovedReferenceValidationError(ValueError):
    """An approved filename exists but no longer has its reviewed bytes."""


@dataclass(frozen=True)
class ApprovedReferenceDefinition:
    reference_id: str
    filename: str
    expected_sha256: str
    roles: tuple[str, ...]
    supported_scene_types: tuple[str, ...] = ()
    identity_scope: str | None = None
    style_scope: str | None = None
    priority: int = 3


APPROVED_REFERENCE_DEFINITIONS = (
    ApprovedReferenceDefinition(
        reference_id="scene_01_style_anchor",
        filename="scene_01_style_anchor.png",
        expected_sha256="ac7775d1e07aa81aea835a61efc6df19de112fbd694ec815c341ca029f7dec72",
        roles=("female_identity_anchor", "style_anchor"),
        identity_scope="recurring_female",
        style_scope="soft_emotional_reference_v1",
        priority=1,
    ),
    ApprovedReferenceDefinition(
        reference_id="scene_02_couple_anchor",
        filename="scene_02_couple_anchor.png",
        expected_sha256="5aecb6e3f7343a6eeaf964d61927fdaf14a42cda5821adcb58bbff526f4ee1f3",
        roles=("couple_identity_anchor",),
        supported_scene_types=(SceneType.RELATIONSHIP_VIGNETTE.value,),
        identity_scope="recurring_couple",
        style_scope="soft_emotional_reference_v1",
        priority=1,
    ),
    ApprovedReferenceDefinition(
        reference_id="scene_03_daily_vignette",
        filename="scene_03_daily_vignette.png",
        expected_sha256="4c5c7755f3fdab278769a85d1eb345e2f2a7ae954d63c92dbcd674e242c7a3af",
        roles=("daily_vignette_reference",),
        supported_scene_types=(SceneType.ORGANIC_DAILY_VIGNETTE.value,),
    ),
    ApprovedReferenceDefinition(
        reference_id="scene_04_emotional_metaphor",
        filename="scene_04_emotional_metaphor.png",
        expected_sha256="d2e93186705a6ae882bbcecb2945e97fb87def338dd5c895e00412ce89921f7c",
        roles=("emotional_metaphor_reference",),
        supported_scene_types=(SceneType.EMOTIONAL_METAPHOR.value,),
    ),
)


def load_reference_catalog(root: Path | str | None) -> ReferenceCatalog:
    """Load known approved definitions only, validating every available file hash."""
    root_path = Path(root).resolve() if root is not None else None
    references: list[ApprovedReference] = []
    unavailable: dict[str, str] = {}
    for definition in APPROVED_REFERENCE_DEFINITIONS:
        path = (root_path / definition.filename).resolve() if root_path is not None else None
        if path is None or not path.is_file():
            reason = f"approved static asset unavailable: {definition.filename}"
            unavailable.update({role: reason for role in definition.roles})
            continue
        actual_sha256 = sha256_file(path)
        if actual_sha256 != definition.expected_sha256:
            raise ApprovedReferenceValidationError(
                f"approved reference hash mismatch for {definition.reference_id}: "
                f"expected {definition.expected_sha256}, got {actual_sha256}"
            )
        references.append(
            ApprovedReference(
                reference_id=definition.reference_id,
                path=str(path),
                sha256=actual_sha256,
                roles=list(definition.roles),
                supported_scene_types=list(definition.supported_scene_types),
                identity_scope=definition.identity_scope,
                style_scope=definition.style_scope,
                priority=definition.priority,
                metadata={"source": "validated_static_pack", "validated_sha256": True},
            )
        )
    return ReferenceCatalog(references=references, unavailable_roles=unavailable)


def _role_priority(role: str) -> int:
    if "identity_anchor" in role:
        return 1
    if role == "style_anchor":
        return 2
    return 3


def _missing_status(role: str) -> ReferenceDecisionStatus:
    if "identity_anchor" in role:
        return ReferenceDecisionStatus.REFERENCE_BLOCKED_REQUIRED_IDENTITY
    if role == "style_anchor":
        return ReferenceDecisionStatus.REFERENCE_BLOCKED_REQUIRED_STYLE
    return ReferenceDecisionStatus.NO_COMPOSITION_REFERENCE_AVAILABLE


def resolve_references(
    brief: ProductionSceneBrief, catalog: ReferenceCatalog
) -> tuple[list[ApprovedReference], list[ReferenceDecision]]:
    by_role: dict[str, list[ApprovedReference]] = {}
    for reference in catalog.references:
        for role in reference.roles:
            by_role.setdefault(role, []).append(reference)
    selected: list[ApprovedReference] = []
    decisions: list[ReferenceDecision] = []
    for role in required_reference_roles(brief):
        compatible = [
            item
            for item in by_role.get(role, [])
            if not item.supported_scene_types
            or brief.scene_type.value in item.supported_scene_types
        ]
        compatible.sort(key=lambda item: (item.priority, item.reference_id))
        priority = _role_priority(role)
        if compatible:
            item = compatible[0]
            selected.append(item)
            decisions.append(
                ReferenceDecision(
                    role=role,
                    status=ReferenceDecisionStatus.SELECTED,
                    reason="highest-priority compatible approved static reference",
                    reference_id=item.reference_id,
                    path=item.path,
                    sha256=item.sha256,
                    priority=priority,
                )
            )
            continue
        decisions.append(
            ReferenceDecision(
                role=role,
                status=_missing_status(role),
                reason=catalog.unavailable_roles.get(
                    role, "no compatible approved static reference is cataloged"
                ),
                priority=priority,
            )
        )
    decisions.sort(key=lambda item: (item.priority, item.role))
    unique: list[ApprovedReference] = []
    seen_hashes: set[str] = set()
    selected_by_id = {item.reference_id: item for item in selected}
    for decision in decisions:
        if decision.status is not ReferenceDecisionStatus.SELECTED:
            continue
        assert decision.reference_id is not None
        item = selected_by_id[decision.reference_id]
        if item.sha256 not in seen_hashes:
            unique.append(item)
            seen_hashes.add(item.sha256)
    return unique, decisions
