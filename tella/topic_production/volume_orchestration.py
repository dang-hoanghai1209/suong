"""Minimal route-dispatching coordinator for Volume initial generation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from tella.visual_generation.providers.base import SceneImageProvider
from tella.visual_generation.providers.kinds import ProviderKind

from .live_execution import execute_draft_scene
from .live_execution_models import DraftExecutionOutcome
from .local_coverage import LocalSceneCoverageResolver
from .local_execution import LocalComposer, LocalExecutionOutcome, execute_local_scene
from .runtime_models import ExecutionRunState
from .strategy import ProductionStrategy


async def execute_volume_initial_scene(
    state: ExecutionRunState,
    *,
    scene_id: str,
    out_root: Path | str,
    live_authorized: bool = False,
    cloudflare_provider: SceneImageProvider | None = None,
    coverage_resolver: LocalSceneCoverageResolver | None = None,
    asset_library_root: str | Path | None = None,
    semantics_path: str | Path | None = None,
    composer: LocalComposer | None = None,
) -> LocalExecutionOutcome | DraftExecutionOutcome:
    """Dispatch one Volume scene to its precomputed initial route exactly once."""

    if state.run_plan.production_strategy.strategy is not ProductionStrategy.VOLUME:
        raise ValueError("Volume initial coordinator requires Volume strategy")
    scene = next((item for item in state.scenes if item.scene_id == scene_id), None)
    if scene is None:
        raise ValueError(f"unknown scene ID: {scene_id}")
    routing = scene.execution_plan.routing
    if routing is None:
        raise ValueError("Volume scene requires a sensitivity-aware route")
    selected = routing.route.selected_provider
    if selected is ProviderKind.LOCAL_COMPOSITOR:
        kwargs: dict[str, Any] = {}
        if composer is not None:
            kwargs["composer"] = composer
        return execute_local_scene(
            state,
            scene_id=scene_id,
            out_root=out_root,
            coverage_resolver=coverage_resolver,
            asset_library_root=asset_library_root,
            semantics_path=semantics_path,
            **kwargs,
        )
    if selected is ProviderKind.CLOUDFLARE_KLEIN_4B:
        return await execute_draft_scene(
            state,
            scene_id=scene_id,
            out_root=out_root,
            dry_run=False,
            live_authorized=live_authorized,
            provider=cloudflare_provider,
        )
    raise PermissionError(routing.route.reason)


__all__ = ["execute_volume_initial_scene"]
