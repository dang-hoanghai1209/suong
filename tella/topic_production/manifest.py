"""Offline production manifest composition."""
from __future__ import annotations

from .models import DualTierPolicy, ProductionManifest, ProductionSceneBrief, StoryPlan
from .readiness import evaluate_render_readiness
from .state import initialize_scenes
from .timing import build_scene_timings


def build_initial_manifest(
    *,
    job_id: str,
    plan: StoryPlan,
    briefs: list[ProductionSceneBrief],
    policy: DualTierPolicy | None = None,
) -> ProductionManifest:
    if [brief.source_beat_id for brief in briefs] != [
        beat.beat_id for beat in plan.semantic_beats
    ]:
        raise ValueError("scene briefs must map one-to-one to ordered semantic beats")
    resolved_policy = policy or DualTierPolicy()
    scenes = initialize_scenes(briefs, resolved_policy)
    readiness = evaluate_render_readiness(scenes)
    return ProductionManifest(
        job_id=job_id,
        topic=plan.topic,
        story_plan=plan,
        scene_briefs=briefs,
        timings=build_scene_timings(plan.semantic_beats),
        scenes=scenes,
        dual_tier_policy=resolved_policy,
        render_ready=readiness.ready,
        blocked_reasons=readiness.reasons,
        metadata={
            "external_calls": 0,
            "planner_mode": "deterministic_offline_fixture",
        },
    )


def refresh_manifest_readiness(manifest: ProductionManifest) -> ProductionManifest:
    readiness = evaluate_render_readiness(manifest.scenes)
    return manifest.model_copy(
        update={
            "render_ready": readiness.ready,
            "blocked_reasons": readiness.reasons,
        },
        deep=True,
    )
