"""Preflight contracts for the three-scene manual live Volume canary."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from tella.asset_library.semantic_resolver import AssetLibraryRequest
from tella.topic_production import (
    AcceptancePriority,
    ExecutionMode,
    LocalCompositionRequest,
    LocalCoverageAssessment,
    LocalCoverageStatus,
    ProductionStrategyConfig,
    ReferenceCatalog,
    SceneComplexity,
    SceneDataSensitivity,
    SceneType,
    VolumeLocalSceneInput,
    build_draft_execution_preview,
    build_production_run_plan,
    build_topic_production_request,
    initialize_execution_state,
    load_runtime_state,
    persist_execution_snapshot,
    production_job_paths,
)
from tella.topic_production.models import (
    PlannerMetadata,
    PlannerMode,
    ProductionSceneBrief,
    ReferenceStrategy,
    SemanticBeat,
    StoryPlan,
)
from tella.visual_generation.providers import ProviderKind


class ThreeSceneCoverageResolver:
    def assess(self, request: AssetLibraryRequest) -> LocalCoverageAssessment:
        covered = request.action.startswith("local_")
        return LocalCoverageAssessment(
            status=(
                LocalCoverageStatus.SATISFIED
                if covered
                else LocalCoverageStatus.NOT_SATISFIED
            ),
            reason="manual three-scene production coverage",
            selected_semantic_id="sit_hug_knees_sad" if covered else None,
        )


def _story(*, production=True) -> StoryPlan:
    segments = [
        "A quiet evening begins with a pause.",
        "A small grounded action creates room to breathe.",
        "The final moment opens toward calm possibility.",
    ]
    beats = [
        SemanticBeat(
            beat_id=f"beat_{index:02d}",
            order=index,
            narration_segment=segment,
            semantic_purpose=f"generic emotional beat {index}",
            emotional_state="calm",
            transition_intent="gentle continuation",
            visual_intent=f"generic safe scene {index}",
            duration_seconds=4.0,
        )
        for index, segment in enumerate(segments, start=1)
    ]
    return StoryPlan(
        topic="three quiet generic moments",
        language="en",
        target_duration_seconds=12.0,
        requested_scene_count=3,
        narration_text=" ".join(segments),
        emotional_arc=["quiet", "grounded", "hopeful"],
        topic_intent="show a simple movement from pause toward calm",
        semantic_beats=beats,
        planner_metadata=PlannerMetadata(
            planner_id="manual_topic_input" if production else "fixture",
            planner_version="manual_v1",
            normalized_topic="three quiet generic moments",
            topic_concepts=["quiet", "calm"],
            deterministic_key="1234567890abcdef",
            semantic_evaluator="manual_review",
            planner_mode=PlannerMode.PRODUCTION if production else PlannerMode.FIXTURE,
            production_eligible=production,
        ),
    )


def _briefs() -> list[ProductionSceneBrief]:
    actions = ["sit_hug_knees", "stand_hands_clasped", "walk calmly through open space"]
    scene_types = [
        SceneType.SOLO_EMOTIONAL_VIGNETTE,
        SceneType.SELF_COMPASSION,
        SceneType.JOURNEY_TRANSITION,
    ]
    return [
        ProductionSceneBrief(
            scene_id=f"scene_{index:02d}",
            order=index,
            scene_type=scene_types[index - 1],
            narrative_text=_story().semantic_beats[index - 1].narration_segment,
            meaning=f"generic safe emotional moment {index}",
            emotional_tone=["calm"],
            topic_intent="show a simple movement from pause toward calm",
            characters=[],
            identity_requirements=[],
            continuity_requirements=[],
            action=[actions[index - 1]],
            interaction={},
            environment=["generic uncluttered room"],
            objects=[],
            symbols=[],
            composition=["single anonymous figure in a clear vertical composition"],
            negative_space_requirements=["clear upper frame"],
            visual_hierarchy=["one readable action"],
            reference_roles=[],
            reference_strategy=ReferenceStrategy(
                strategy="manual_public_safe_text_only",
                accepted_scene_chaining=False,
            ),
            hard_negatives=["no readable text", "no identifiable person"],
            complexity=SceneComplexity.SIMPLE,
            acceptance_priority=AcceptancePriority.STANDARD,
            source_beat_id=f"beat_{index:02d}",
            duration_seconds=4.0,
        )
        for index in range(1, 4)
    ]


def _inputs() -> dict[str, VolumeLocalSceneInput]:
    return {
        "scene_01": VolumeLocalSceneInput(
            sensitivity=SceneDataSensitivity.LOCAL_ONLY,
            request=LocalCompositionRequest(
                character_id="female_01",
                action="local_sit_hug_knees",
                emotion="sad",
                location="bedroom",
                time_of_day="night",
                composition_preset="bedroom_floor_sitting",
                seed=10101,
            ),
        ),
        "scene_02": VolumeLocalSceneInput(
            sensitivity=SceneDataSensitivity.LOCAL_ONLY,
            request=LocalCompositionRequest(
                character_id="female_01",
                action="local_stand_hands_clasped",
                emotion="calm",
                location="bedroom",
                time_of_day="night",
                composition_preset="centered",
                seed=10202,
            ),
        ),
        "scene_03": VolumeLocalSceneInput(
            sensitivity=SceneDataSensitivity.PUBLIC_SAFE,
            request=LocalCompositionRequest(
                character_id="generic_anonymous_figure",
                action="external_generic_walk",
                emotion="calm",
                location="generic_room",
                time_of_day="day",
                composition_preset="centered",
                seed=10303,
            ),
        ),
    }


def _run():
    return build_production_run_plan(
        job_id="volume-live-cp5b-contract",
        story_plan=_story(),
        scene_briefs=_briefs(),
        reference_catalog=ReferenceCatalog(),
        execution_mode=ExecutionMode.LIVE_PRODUCTION,
        production_strategy=ProductionStrategyConfig.volume(),
        volume_local_inputs=_inputs(),
        local_coverage_resolver=ThreeSceneCoverageResolver(),
    )


def test_manual_three_scene_volume_plan_validates_and_persists(tmp_path):
    run = _run()
    state = initialize_execution_state(run)

    assert len(run.scene_execution_plans) == len(state.scenes) == 3
    assert run.story_plan.planner_metadata.production_eligible is True
    assert [
        item.routing.route.selected_provider for item in run.scene_execution_plans
    ] == [
        ProviderKind.LOCAL_COMPOSITOR,
        ProviderKind.LOCAL_COMPOSITOR,
        ProviderKind.CLOUDFLARE_KLEIN_4B,
    ]
    paths = production_job_paths(tmp_path, job_id=run.job_id, scene_id="scene_01")
    persist_execution_snapshot(
        state,
        paths,
        execution_purpose="manual_three_scene_contract",
        selected_scene_id="scene_01",
    )
    assert load_runtime_state(paths.runtime_state_path) == state


def test_public_safe_external_scene_builds_text_only_cloudflare_preview():
    state = initialize_execution_state(_run())
    preview = build_draft_execution_preview(state, scene_id="scene_03")

    assert preview.request.references == []
    assert preview.candidate_count == preview.maximum_provider_calls == 1
    assert preview.retry_calls == preview.fallback_calls == 0
    assert "generic_anonymous_figure" not in preview.request.instruction


def test_non_public_scene_cannot_cross_text_only_request_boundary():
    run = _run()
    scene = run.scene_execution_plans[2]
    routing = scene.routing.model_copy(
        update={"sensitivity": SceneDataSensitivity.PRIVATE}, deep=True
    )
    private_scene = scene.model_copy(update={"routing": routing}, deep=True)

    with pytest.raises(ValueError, match="references are missing"):
        build_topic_production_request(private_scene)


def test_three_scene_fixture_plan_is_rejected():
    with pytest.raises(ValidationError, match="three-scene manual production"):
        _story(production=False)
