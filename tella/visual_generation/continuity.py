"""Bounded reference selection with stable masters and non-chained continuity."""
from __future__ import annotations

from pathlib import Path

from .models import ProviderCapabilities, ReferenceAsset, ReferencePack, SceneBrief
from .references import sha256_file


def select_references(
    scene: SceneBrief,
    catalog: dict[str, ReferenceAsset],
    capabilities: ProviderCapabilities,
    *,
    accepted_scenes: dict[str, Path] | None = None,
) -> ReferencePack:
    if not capabilities.supports_reference_images or capabilities.max_reference_images < 1:
        raise RuntimeError(
            f"provider {capabilities.provider_id} lacks required reference-image capability"
        )

    requested = [catalog[role] for role in scene.reference_roles if role in catalog]
    missing_roles = sorted(set(scene.reference_roles) - set(catalog))
    if missing_roles:
        raise ValueError(f"unknown reference roles for {scene.scene_id}: {missing_roles}")

    # Scenes 3 and 4 use the stable Scene 1 calibration result, never a rolling chain.
    accepted = accepted_scenes or {}
    if scene.scene_id in {"scene_03", "scene_04"} and "scene_01" in accepted:
        path = accepted["scene_01"].resolve()
        requested.append(
            ReferenceAsset(
                role="scene_01_accepted_continuity",
                semantic_roles=["scene_01_accepted_continuity"],
                path=path,
                sha256=sha256_file(path),
                source="accepted_scene",
                priority=4,
            )
        )

    requested.sort(key=lambda item: (item.priority, item.role))
    unique: list[ReferenceAsset] = []
    seen_paths: dict[Path, int] = {}
    seen_hashes: dict[str, int] = {}
    for reference in requested:
        resolved = reference.path.resolve()
        index = seen_paths.get(resolved)
        if index is None:
            index = seen_hashes.get(reference.sha256)
        if index is not None:
            prior = unique[index]
            roles = list(
                dict.fromkeys(
                    [
                        prior.role,
                        *prior.semantic_roles,
                        reference.role,
                        *reference.semantic_roles,
                    ]
                )
            )
            unique[index] = prior.model_copy(update={"semantic_roles": roles})
            continue
        seen_paths[resolved] = len(unique)
        seen_hashes[reference.sha256] = len(unique)
        unique.append(
            reference.model_copy(
                update={"semantic_roles": reference.semantic_roles or [reference.role]}
            )
        )

    limit = 1 if not capabilities.supports_multiple_references else capabilities.max_reference_images
    selected = unique[:limit]
    if not selected:
        raise RuntimeError(f"no usable reference selected for {scene.scene_id}")
    return ReferencePack(scene_id=scene.scene_id, references=selected)
