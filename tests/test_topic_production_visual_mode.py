"""Declarative visual execution mode boundary for topic-production plans."""
from __future__ import annotations

from tella.topic_production.execution import build_production_run_plan
from tella.topic_production.execution_models import ProductionRunPlan, ReferenceCatalog
from tella.topic_production.planner import DeterministicTopicPlanner, build_scene_briefs
from tella.topic_production.strategy import (
    ProductionStrategy,
    ProductionStrategyConfig,
    VisualExecutionMode,
    VolumeProductionPolicy,
)


def _run_plan(strategy: ProductionStrategyConfig) -> ProductionRunPlan:
    story_plan = DeterministicTopicPlanner().plan(
        topic="Visual realization remains separate from story meaning.",
        scene_count=7,
    )
    return build_production_run_plan(
        job_id="visual-mode-boundary",
        story_plan=story_plan,
        scene_briefs=build_scene_briefs(story_plan),
        reference_catalog=ReferenceCatalog(),
        production_strategy=strategy,
    )


def test_strategy_factories_have_backward_compatible_visual_mode_defaults() -> None:
    assert (
        ProductionStrategyConfig.quality().visual_mode
        is VisualExecutionMode.ILLUSTRATED_SCENE
    )
    assert (
        ProductionStrategyConfig.volume().visual_mode
        is VisualExecutionMode.LOCAL_COMPOSITOR
    )


def test_economic_strategy_and_visual_mode_are_independently_configurable() -> None:
    volume_illustrated = ProductionStrategyConfig.volume(
        visual_mode=VisualExecutionMode.ILLUSTRATED_SCENE
    )
    quality_local = ProductionStrategyConfig.quality(
        visual_mode=VisualExecutionMode.LOCAL_COMPOSITOR
    )

    assert volume_illustrated.strategy is ProductionStrategy.VOLUME
    assert volume_illustrated.visual_mode is VisualExecutionMode.ILLUSTRATED_SCENE
    assert volume_illustrated.volume_policy == VolumeProductionPolicy()
    assert quality_local.strategy is ProductionStrategy.QUALITY
    assert quality_local.visual_mode is VisualExecutionMode.LOCAL_COMPOSITOR
    assert quality_local.volume_policy is None


def test_visual_mode_does_not_change_economic_policy_values() -> None:
    local = ProductionStrategyConfig.volume(
        visual_mode=VisualExecutionMode.LOCAL_COMPOSITOR
    )
    illustrated = ProductionStrategyConfig.volume(
        visual_mode=VisualExecutionMode.ILLUSTRATED_SCENE
    )

    assert local.strategy is illustrated.strategy is ProductionStrategy.VOLUME
    assert local.volume_policy == illustrated.volume_policy == VolumeProductionPolicy()


def test_visual_mode_round_trips_through_production_run_plan_serialization() -> None:
    run = _run_plan(
        ProductionStrategyConfig.volume(
            visual_mode=VisualExecutionMode.ILLUSTRATED_SCENE
        )
    )

    restored = ProductionRunPlan.model_validate_json(run.model_dump_json())

    assert restored == run
    assert restored.production_strategy.visual_mode is VisualExecutionMode.ILLUSTRATED_SCENE


def test_visual_mode_changes_plan_identity_without_changing_story_semantics() -> None:
    illustrated = _run_plan(
        ProductionStrategyConfig.quality(
            visual_mode=VisualExecutionMode.ILLUSTRATED_SCENE
        )
    )
    local = _run_plan(
        ProductionStrategyConfig.quality(
            visual_mode=VisualExecutionMode.LOCAL_COMPOSITOR
        )
    )

    assert illustrated.planning_hash != local.planning_hash
    assert illustrated.story_plan == local.story_plan
    assert illustrated.manifest.scene_briefs == local.manifest.scene_briefs
    assert illustrated.scene_execution_plans == local.scene_execution_plans
    assert illustrated.manifest == local.manifest
