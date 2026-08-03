from types import SimpleNamespace

import pytest

from tella.topic_production import (
    LocalCoverageAssessment,
    LocalCoverageStatus,
    ProductionStrategy,
    ProductionStrategyConfig,
    ProviderAvailability,
    ReferenceCatalog,
    SceneDataSensitivity,
    SceneGenerationCapability,
    SceneRoutingRequest,
    VolumeProductionPolicy,
    VolumeQCAction,
    VolumeQCSeverity,
    assess_semantic_resolution,
    build_draft_execution_preview,
    build_fixture_preview_run,
    build_production_run_plan,
    initialize_execution_state,
    route_scene,
    volume_qc_disposition,
)
from tella.visual_generation.providers import ProviderKind, describe_provider
from tella.visual_generation.providers.cloudflare_flux import KLEIN_4B_MODEL


def _request(
    sensitivity: SceneDataSensitivity,
    coverage: LocalCoverageStatus = LocalCoverageStatus.NOT_SATISFIED,
) -> SceneRoutingRequest:
    return SceneRoutingRequest(
        sensitivity=sensitivity,
        local_coverage=LocalCoverageAssessment(
            status=coverage,
            reason="controlled test assessment",
        ),
    )


def test_private_scene_routes_to_cloudflare_and_never_pollinations():
    route = route_scene(_request(SceneDataSensitivity.PRIVATE))

    assert route.capability is SceneGenerationCapability.PRIVATE_AI
    assert route.selected_provider is ProviderKind.CLOUDFLARE_KLEIN_4B
    assert route.eligible_providers == (ProviderKind.CLOUDFLARE_KLEIN_4B,)
    assert ProviderKind.POLLINATIONS not in route.eligible_providers


def test_private_scene_does_not_privacy_downgrade_when_cloudflare_is_unavailable():
    route = route_scene(
        _request(SceneDataSensitivity.PRIVATE),
        ProviderAvailability(cloudflare_klein_4b=False, pollinations=True),
    )

    assert route.blocked is True
    assert route.selected_provider is None
    assert route.available_route == ()
    assert ProviderKind.POLLINATIONS not in route.eligible_providers


def test_public_safe_scene_can_use_pollinations_as_overflow():
    route = route_scene(
        _request(SceneDataSensitivity.PUBLIC_SAFE),
        ProviderAvailability(cloudflare_klein_4b=False, pollinations=True),
    )

    assert route.eligible_providers == (
        ProviderKind.CLOUDFLARE_KLEIN_4B,
        ProviderKind.POLLINATIONS,
    )
    assert route.selected_provider is ProviderKind.POLLINATIONS


@pytest.mark.parametrize(
    "coverage",
    [LocalCoverageStatus.NOT_SATISFIED, LocalCoverageStatus.AMBIGUOUS_UNSAFE],
)
def test_local_only_scene_never_reaches_external_providers(coverage):
    route = route_scene(_request(SceneDataSensitivity.LOCAL_ONLY, coverage))

    assert route.blocked is True
    assert route.selected_provider is None
    assert route.eligible_providers == (ProviderKind.LOCAL_COMPOSITOR,)


def test_local_covered_scene_selects_compositor_without_ai_call():
    route = route_scene(
        _request(SceneDataSensitivity.PRIVATE, LocalCoverageStatus.SATISFIED)
    )

    assert route.capability is SceneGenerationCapability.LOCAL_COVERED
    assert route.selected_provider is ProviderKind.LOCAL_COMPOSITOR
    assert route.consumes_ai_call is False
    assert describe_provider(route.selected_provider).external is False


def test_routing_is_deterministic_for_identical_input():
    request = _request(SceneDataSensitivity.PUBLIC_SAFE)
    availability = ProviderAvailability()

    assert route_scene(request, availability) == route_scene(request, availability)
    assert route_scene(request, availability).model_dump_json() == route_scene(
        request, availability
    ).model_dump_json()


def test_volume_policy_defaults_are_one_shot_and_free_first():
    policy = VolumeProductionPolicy()

    assert policy.initial_candidates_per_scene == 1
    assert policy.soft_fail_retry_budget == 0
    assert policy.hard_fail_retry_per_scene == 1
    assert policy.max_ai_retries_per_run == 2
    assert policy.paid_fallback_allowed is False
    assert policy.auto_premium_promotion is False
    assert policy.generated_scene_chaining is False

    with pytest.raises(ValueError):
        VolumeProductionPolicy(hard_fail_retry_per_scene=2)
    with pytest.raises(ValueError):
        VolumeProductionPolicy(max_ai_retries_per_run=3)


def test_volume_strategy_requires_its_explicit_policy():
    with pytest.raises(ValueError, match="requires an explicit Volume policy"):
        ProductionStrategyConfig(strategy=ProductionStrategy.VOLUME)

    config = ProductionStrategyConfig.volume()
    assert config.strategy is ProductionStrategy.VOLUME
    assert config.volume_policy == VolumeProductionPolicy()


def test_quality_mode_keeps_existing_dual_tier_defaults():
    run = build_fixture_preview_run(topic="quality regression", scene_count=7)

    assert run.production_strategy == ProductionStrategyConfig.quality()
    assert run.production_strategy.volume_policy is None
    assert run.scene_execution_plans[0].draft.model == KLEIN_4B_MODEL
    assert run.scene_execution_plans[0].acceptance.model.endswith("flux-2-dev")
    assert run.scene_execution_plans[0].acceptance.promotion_requires_explicit_qc is True
    assert run.scene_execution_plans[0].draft.accepted_scene_chaining is False


def test_volume_policy_is_threaded_through_state_but_live_execution_fails_closed():
    quality_run = build_fixture_preview_run(topic="volume contract", scene_count=7)
    volume_run = build_production_run_plan(
        job_id="volume-contract",
        story_plan=quality_run.story_plan,
        scene_briefs=[scene.scene_brief for scene in quality_run.scene_execution_plans],
        reference_catalog=ReferenceCatalog(),
        production_strategy=ProductionStrategyConfig.volume(),
    )
    state = initialize_execution_state(volume_run)

    assert state.run_plan.production_strategy.strategy is ProductionStrategy.VOLUME
    assert state.run_plan.production_strategy.volume_policy == VolumeProductionPolicy()
    with pytest.raises(ValueError, match="explicit non-local Cloudflare route"):
        build_draft_execution_preview(state, scene_id="scene_01")


def test_volume_soft_failure_is_acceptable_without_retry_or_automatic_acceptance():
    disposition = volume_qc_disposition(
        VolumeQCSeverity.SOFT_FAIL,
        scene_hard_retries_used=0,
        run_ai_retries_used=0,
    )

    assert disposition.action is VolumeQCAction.ACCEPTABLE
    assert disposition.consumes_retry_budget is False
    assert not hasattr(disposition, "accepted_candidate")


def test_volume_hard_failure_obeys_scene_and_run_retry_ceilings():
    retry = volume_qc_disposition(
        VolumeQCSeverity.HARD_FAIL,
        scene_hard_retries_used=0,
        run_ai_retries_used=1,
    )
    scene_block = volume_qc_disposition(
        VolumeQCSeverity.HARD_FAIL,
        scene_hard_retries_used=1,
        run_ai_retries_used=1,
    )
    run_block = volume_qc_disposition(
        VolumeQCSeverity.HARD_FAIL,
        scene_hard_retries_used=0,
        run_ai_retries_used=2,
    )

    assert retry.action is VolumeQCAction.RETRY_OR_REROUTE
    assert retry.consumes_retry_budget is True
    assert scene_block.action is VolumeQCAction.BLOCK
    assert run_block.action is VolumeQCAction.BLOCK


def test_local_coverage_requires_exact_approved_semantic_resolution():
    exact = SimpleNamespace(
        production_eligible=True,
        quality_status="approved",
        fallback_reason="",
        selected_semantic_id="sit_hug_knees_sad",
    )
    fallback = SimpleNamespace(
        production_eligible=True,
        quality_status="approved",
        fallback_reason="no_production_eligible_exact_action",
        selected_semantic_id="stand_front",
    )
    unsafe = SimpleNamespace(
        production_eligible=True,
        quality_status="source_truncated",
        fallback_reason="",
        selected_semantic_id="stand_wave",
    )

    assert assess_semantic_resolution(exact).status is LocalCoverageStatus.SATISFIED
    assert assess_semantic_resolution(fallback).status is LocalCoverageStatus.NOT_SATISFIED
    assert assess_semantic_resolution(unsafe).status is LocalCoverageStatus.AMBIGUOUS_UNSAFE
