"""Fail-closed final-render eligibility checks."""
from __future__ import annotations

from .models import GenerationTier, ProductionScene, ProductionSceneStatus, ReadinessResult


def evaluate_render_readiness(scenes: list[ProductionScene]) -> ReadinessResult:
    reasons: dict[str, list[str]] = {}
    seen: set[str] = set()
    for scene in scenes:
        scene_id = scene.brief.scene_id
        scene_reasons: list[str] = []
        if scene_id in seen:
            scene_reasons.append("duplicate required scene ID")
        seen.add(scene_id)
        if scene.status is not ProductionSceneStatus.ACCEPTED:
            scene_reasons.append(f"scene status is {scene.status.value}, not ACCEPTED")
        candidate = scene.accepted_candidate
        if candidate is None:
            scene_reasons.append("accepted candidate artifact is missing")
        if scene.accepted_source_tier is None:
            scene_reasons.append("accepted source tier is missing")
        elif candidate is not None and candidate.tier is not scene.accepted_source_tier:
            scene_reasons.append("accepted source tier does not match candidate tier")
        if candidate is not None and candidate.tier not in {
            GenerationTier.DRAFT,
            GenerationTier.ACCEPTANCE,
        }:
            scene_reasons.append("accepted candidate tier is invalid")
        if scene_reasons:
            reasons[scene_id] = scene_reasons
    unresolved = list(reasons)
    return ReadinessResult(
        ready=bool(scenes) and not unresolved,
        unresolved_scene_ids=unresolved,
        reasons=reasons,
    )
