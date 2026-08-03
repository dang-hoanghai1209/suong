"""Mode-first provider-neutral scene routing remains network-free."""
from __future__ import annotations

from tella.asset_library.semantic_resolver import AssetLibraryRequest
from tella.topic_production.execution import (
    build_fixture_preview_run,
    build_production_run_plan,
)
from tella.topic_production.execution_models import (
    LocalCompositionRequest,
    ProductionRunPlan,
    ReferenceCatalog,
    VolumeLocalSceneInput,
)
from tella.topic_production.strategy import (
    LocalCoverageAssessment,
    LocalCoverageStatus,
    ProductionStrategyConfig,
    SceneDataSensitivity,
    VisualExecutionMode,
)
from tella.visual_generation.providers.kinds import ProviderKind


class CountingCoveredResolver:
    def __init__(self) -> None:
        self.calls = 0

    def assess(self, request: AssetLibraryRequest) -> LocalCoverageAssessment:
        self.calls += 1
        return LocalCoverageAssessment(
            status=LocalCoverageStatus.SATISFIED,
            reason=f"exact local coverage for {request.action}",
            selected_semantic_id="sit_hug_knees_sad",
        )


def _local_input(sensitivity: SceneDataSensitivity) -> VolumeLocalSceneInput:
    return VolumeLocalSceneInput(
        sensitivity=sensitivity,
        request=LocalCompositionRequest(
            character_id="female_01",
            action="sit_hug_knees",
            emotion="sad",
            location="bedroom",
            time_of_day="night",
            composition_preset="bedroom_floor_sitting",
            seed=10101,
        ),
    )


def _plan(
    visual_mode: VisualExecutionMode,
    sensitivity: SceneDataSensitivity,
    resolver: CountingCoveredResolver,
) -> ProductionRunPlan:
    fixture = build_fixture_preview_run(topic="mode-first routing", scene_count=7)
    return build_production_run_plan(
        job_id="mode-first-routing",
        story_plan=fixture.story_plan,
        scene_briefs=[scene.scene_brief for scene in fixture.scene_execution_plans],
        reference_catalog=ReferenceCatalog(),
        production_strategy=ProductionStrategyConfig.volume(visual_mode=visual_mode),
        volume_local_inputs={"scene_01": _local_input(sensitivity)},
        local_coverage_resolver=resolver,
    )


def test_local_mode_preserves_covered_local_route() -> None:
    resolver = CountingCoveredResolver()
    run = _plan(
        VisualExecutionMode.LOCAL_COMPOSITOR,
        SceneDataSensitivity.PRIVATE,
        resolver,
    )
    scene = run.scene_execution_plans[0]

    assert resolver.calls == 1
    assert scene.routing is not None
    assert scene.routing.route.selected_provider is ProviderKind.LOCAL_COMPOSITOR
    assert scene.local_execution is not None


def test_illustrated_mode_ignores_perfect_local_coverage_and_routes_cloudflare() -> None:
    resolver = CountingCoveredResolver()
    run = _plan(
        VisualExecutionMode.ILLUSTRATED_SCENE,
        SceneDataSensitivity.PRIVATE,
        resolver,
    )
    scene = run.scene_execution_plans[0]

    assert resolver.calls == 0
    assert scene.routing is not None
    assert scene.routing.route.selected_provider is ProviderKind.CLOUDFLARE_KLEIN_4B
    assert scene.local_execution is None


def test_local_only_illustrated_mode_fails_closed_without_local_degradation() -> None:
    resolver = CountingCoveredResolver()
    scene = _plan(
        VisualExecutionMode.ILLUSTRATED_SCENE,
        SceneDataSensitivity.LOCAL_ONLY,
        resolver,
    ).scene_execution_plans[0]

    assert resolver.calls == 0
    assert scene.routing is not None
    assert scene.routing.route.blocked is True
    assert scene.routing.route.selected_provider is None
    assert scene.routing.route.eligible_providers == ()
    assert scene.local_execution is None


def test_illustrated_privacy_routes_are_provider_safe() -> None:
    private_resolver = CountingCoveredResolver()
    public_resolver = CountingCoveredResolver()
    private = _plan(
        VisualExecutionMode.ILLUSTRATED_SCENE,
        SceneDataSensitivity.PRIVATE,
        private_resolver,
    ).scene_execution_plans[0]
    public = _plan(
        VisualExecutionMode.ILLUSTRATED_SCENE,
        SceneDataSensitivity.PUBLIC_SAFE,
        public_resolver,
    ).scene_execution_plans[0]

    assert private.routing is not None
    assert private.routing.route.eligible_providers == (
        ProviderKind.CLOUDFLARE_KLEIN_4B,
    )
    assert ProviderKind.POLLINATIONS not in private.routing.route.eligible_providers
    assert public.routing is not None
    assert public.routing.route.selected_provider is ProviderKind.CLOUDFLARE_KLEIN_4B
    assert public.routing.route.eligible_providers == (
        ProviderKind.CLOUDFLARE_KLEIN_4B,
        ProviderKind.POLLINATIONS,
    )
    assert private_resolver.calls == public_resolver.calls == 0


def test_same_story_semantics_support_both_modes() -> None:
    local = _plan(
        VisualExecutionMode.LOCAL_COMPOSITOR,
        SceneDataSensitivity.PRIVATE,
        CountingCoveredResolver(),
    )
    illustrated = _plan(
        VisualExecutionMode.ILLUSTRATED_SCENE,
        SceneDataSensitivity.PRIVATE,
        CountingCoveredResolver(),
    )

    assert local.story_plan == illustrated.story_plan
    assert local.manifest.scene_briefs == illustrated.manifest.scene_briefs
    assert [scene.scene_brief for scene in local.scene_execution_plans] == [
        scene.scene_brief for scene in illustrated.scene_execution_plans
    ]


def test_quality_default_preserves_existing_illustrated_draft_plan() -> None:
    run = build_fixture_preview_run(topic="quality illustrated behavior", scene_count=7)

    assert (
        run.production_strategy.visual_mode is VisualExecutionMode.ILLUSTRATED_SCENE
    )
    assert run.scene_execution_plans[0].draft.provider == "cloudflare-flux"
    assert run.scene_execution_plans[0].local_execution is None


def test_legacy_volume_plan_loads_with_generic_route_and_local_mode() -> None:
    current = _plan(
        VisualExecutionMode.LOCAL_COMPOSITOR,
        SceneDataSensitivity.PRIVATE,
        CountingCoveredResolver(),
    )
    legacy = current.model_dump(mode="json")
    del legacy["production_strategy"]["visual_mode"]
    scene = legacy["scene_execution_plans"][0]
    routing = scene.pop("routing")
    scene["local_execution"]["sensitivity"] = routing["sensitivity"]
    scene["local_execution"]["route"] = routing["route"]

    restored = ProductionRunPlan.model_validate(legacy)

    assert restored.production_strategy.visual_mode is VisualExecutionMode.LOCAL_COMPOSITOR
    assert restored.scene_execution_plans[0].routing is not None
    assert (
        restored.scene_execution_plans[0].routing.route.selected_provider
        is ProviderKind.LOCAL_COMPOSITOR
    )
