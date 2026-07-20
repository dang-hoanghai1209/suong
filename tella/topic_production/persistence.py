"""Atomic persistence and reload for topic-production runtime state."""
from __future__ import annotations

import re
from pathlib import Path

from tella.atomic_write import atomic_write_json
from tella.visual_generation.models import CandidateMetadata

from .live_execution_models import DraftExecutionPreview, ProductionJobPaths
from .runtime import evaluate_execution_readiness, plan_resume, summarize_call_budget
from .runtime_models import ExecutionRunState


def production_job_paths(
    out_root: Path | str, *, job_id: str, scene_id: str
) -> ProductionJobPaths:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", job_id):
        raise ValueError("job_id may contain only letters, numbers, dot, underscore, and dash")
    if not re.fullmatch(r"scene_[0-9]{2}", scene_id):
        raise ValueError("invalid scene ID")
    job_dir = Path(out_root).resolve() / "topic_production" / job_id
    draft_dir = job_dir / "scenes" / scene_id / "draft"
    return ProductionJobPaths(
        job_dir=job_dir,
        run_plan_path=job_dir / "run_plan.json",
        runtime_state_path=job_dir / "runtime_state.json",
        manifest_path=job_dir / "manifest.json",
        candidate_base_path=draft_dir / "candidate_01.bin",
        candidate_metadata_path=draft_dir / "metadata.json",
    )


def persist_production_job(
    state: ExecutionRunState,
    paths: ProductionJobPaths,
    *,
    preview: DraftExecutionPreview,
    provider_metadata: CandidateMetadata | None = None,
) -> None:
    readiness = evaluate_execution_readiness(state)
    budget = summarize_call_budget(state)
    resume = plan_resume(state)
    atomic_write_json(paths.run_plan_path, state.run_plan.model_dump(mode="json"))
    atomic_write_json(paths.runtime_state_path, state.model_dump(mode="json"))
    atomic_write_json(
        paths.manifest_path,
        {
            "schema_version": 1,
            "job_id": state.run_plan.job_id,
            "topic": state.run_plan.topic,
            "planner_mode": state.run_plan.story_plan.planner_metadata.planner_mode.value,
            "production_eligible": (
                state.run_plan.story_plan.planner_metadata.production_eligible
            ),
            "execution_purpose": preview.execution_purpose,
            "planning_hash": state.run_plan.planning_hash,
            "selected_scene_id": preview.scene_id,
            "runtime_scenes": [
                {
                    "scene_id": scene.scene_id,
                    "status": scene.status.value,
                    "attempts": [item.model_dump(mode="json") for item in scene.generation_attempts],
                    "qc_records": [item.model_dump(mode="json") for item in scene.qc_records],
                    "accepted_candidate": (
                        scene.accepted_candidate.model_dump(mode="json")
                        if scene.accepted_candidate
                        else None
                    ),
                    "block_reasons": [item.value for item in scene.block_reasons],
                }
                for scene in state.scenes
            ],
            "event_history": [item.model_dump(mode="json") for item in state.event_history],
            "call_budget": budget.model_dump(mode="json"),
            "readiness": readiness.model_dump(mode="json"),
            "resume_plan": resume.model_dump(mode="json"),
            "external_calls": state.external_calls,
        },
    )
    if provider_metadata is not None:
        atomic_write_json(
            paths.candidate_metadata_path,
            provider_metadata.model_dump(mode="json"),
        )


def load_runtime_state(path: Path | str) -> ExecutionRunState:
    return ExecutionRunState.model_validate_json(Path(path).read_text(encoding="utf-8"))
