"""Fail-closed coverage adapter for the existing semantic asset resolver."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from tella.asset_library.semantic_resolver import (
    AssetLibraryRequest,
    AssetLibraryResolutionError,
    SemanticResolution,
    select_semantic_asset,
)

from .strategy import LocalCoverageAssessment, LocalCoverageStatus


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
]
