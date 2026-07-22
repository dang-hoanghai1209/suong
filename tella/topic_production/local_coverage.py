"""Fail-closed coverage adapter for the existing semantic asset resolver."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol

from tella.asset_library.semantic_resolver import (
    AssetLibraryRequest,
    AssetLibraryResolutionError,
    SemanticResolution,
    select_semantic_asset,
)

from .execution_models import (
    LocalCompositionRequest,
    LocalExecutionPlan,
    VolumeLocalSceneInput,
)
from .strategy import (
    LocalCoverageAssessment,
    LocalCoverageStatus,
    SceneRoutingRequest,
    route_scene,
)


class LocalSceneCoverageResolver(Protocol):
    """Contract implemented by local scene-capability resolvers."""

    def assess(self, request: AssetLibraryRequest) -> LocalCoverageAssessment: ...


class SemanticAssetCoverageResolver:
    """Read-only adapter over the established V2 semantic resolver and asset roots."""

    def __init__(
        self,
        *,
        asset_library_root: str | Path | None = None,
        semantics_path: str | Path | None = None,
    ) -> None:
        self.asset_library_root = asset_library_root
        self.semantics_path = semantics_path

    def assess(self, request: AssetLibraryRequest) -> LocalCoverageAssessment:
        try:
            resolution = select_semantic_asset(
                self.semantics_path,
                self.asset_library_root,
                request,
            )
        except (FileNotFoundError, LookupError, AssetLibraryResolutionError) as exc:
            return LocalCoverageAssessment(
                status=LocalCoverageStatus.NOT_SATISFIED,
                reason=f"semantic resolver cannot satisfy required local assets: {exc}",
            )
        return assess_semantic_resolution(resolution)


def to_asset_library_request(request: LocalCompositionRequest) -> AssetLibraryRequest:
    return AssetLibraryRequest(**request.model_dump())


def plan_local_scene_execution(
    scene_id: str,
    scene_input: VolumeLocalSceneInput,
    resolver: LocalSceneCoverageResolver,
) -> LocalExecutionPlan:
    """Assess and route one explicitly configured Volume scene without side effects."""

    coverage = resolver.assess(to_asset_library_request(scene_input.request))
    route = route_scene(
        SceneRoutingRequest(
            sensitivity=scene_input.sensitivity,
            local_coverage=coverage,
        )
    )
    payload = {
        "scene_id": scene_id,
        "sensitivity": scene_input.sensitivity.value,
        "request": scene_input.request.model_dump(mode="json"),
        "coverage": coverage.model_dump(mode="json"),
        "route": route.model_dump(mode="json"),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return LocalExecutionPlan(
        sensitivity=scene_input.sensitivity,
        request=scene_input.request,
        coverage=coverage,
        route=route,
        logical_request_hash=hashlib.sha256(encoded).hexdigest(),
    )


def assess_semantic_resolution(resolution: SemanticResolution) -> LocalCoverageAssessment:
    """Require an exact, approved result; uncertain semantic fallback is not coverage."""

    if not resolution.production_eligible:
        return LocalCoverageAssessment(
            status=LocalCoverageStatus.AMBIGUOUS_UNSAFE,
            reason="semantic resolution selected a production-ineligible asset",
            selected_semantic_id=resolution.selected_semantic_id,
        )
    if resolution.quality_status != "approved":
        return LocalCoverageAssessment(
            status=LocalCoverageStatus.AMBIGUOUS_UNSAFE,
            reason=f"semantic resolution quality is {resolution.quality_status!r}, not approved",
            selected_semantic_id=resolution.selected_semantic_id,
        )
    if resolution.fallback_reason:
        return LocalCoverageAssessment(
            status=LocalCoverageStatus.NOT_SATISFIED,
            reason=f"semantic fallback is insufficient: {resolution.fallback_reason}",
            selected_semantic_id=resolution.selected_semantic_id,
        )
    return LocalCoverageAssessment(
        status=LocalCoverageStatus.SATISFIED,
        reason="existing semantic resolver found exact production-approved local assets",
        selected_semantic_id=resolution.selected_semantic_id,
    )


__all__ = [
    "LocalSceneCoverageResolver",
    "SemanticAssetCoverageResolver",
    "assess_semantic_resolution",
    "plan_local_scene_execution",
    "to_asset_library_request",
]
