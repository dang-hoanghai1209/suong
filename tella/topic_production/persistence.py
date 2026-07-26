"""Atomic persistence and reload for topic-production runtime state."""

from __future__ import annotations

import json
import re
from pathlib import Path

from tella.atomic_write import atomic_write_bytes
from tella.visual_generation.models import CandidateMetadata

from .duration_policy_projection import build_duration_policy_report
from .execution_models import ProductionRunPlan
from .live_execution_models import DraftExecutionPreview, ProductionJobPaths
from .runtime import (
    _revalidate_execution_state,
    evaluate_execution_readiness,
    plan_resume,
    summarize_call_budget,
)
from .runtime_models import ExecutionRunState


def _serialize_json_utf8(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def production_job_paths(out_root: Path | str, *, job_id: str, scene_id: str) -> ProductionJobPaths:
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
    candidate_payload = (
        provider_metadata.model_dump(mode="json") if provider_metadata is not None else None
    )
    persist_execution_snapshot(
        state,
        paths,
        execution_purpose=preview.execution_purpose,
        selected_scene_id=preview.scene_id,
        candidate_metadata=candidate_payload,
    )


def persist_execution_snapshot(
    state: ExecutionRunState,
    paths: ProductionJobPaths,
    *,
    execution_purpose: str,
    selected_scene_id: str,
    candidate_metadata: dict[str, object] | None = None,
) -> None:
    """Persist provider-neutral execution state and optional candidate metadata atomically."""

    validated_state = _revalidate_execution_state(state)
    duration_policy_report = build_duration_policy_report(validated_state.run_plan)
    readiness = evaluate_execution_readiness(validated_state)
    budget = summarize_call_budget(validated_state)
    resume = plan_resume(validated_state)
    run_plan_payload = validated_state.run_plan.model_dump(mode="json")
    runtime_state_payload = validated_state.model_dump(mode="json")
    manifest_payload = {
        "schema_version": 1,
        "job_id": validated_state.run_plan.job_id,
        "topic": validated_state.run_plan.topic,
        "planner_mode": (validated_state.run_plan.story_plan.planner_metadata.planner_mode.value),
        "production_eligible": (
            validated_state.run_plan.story_plan.planner_metadata.production_eligible
        ),
        "execution_purpose": execution_purpose,
        "planning_hash": validated_state.run_plan.planning_hash,
        "selected_scene_id": selected_scene_id,
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
            for scene in validated_state.scenes
        ],
        "event_history": [item.model_dump(mode="json") for item in validated_state.event_history],
        "call_budget": budget.model_dump(mode="json"),
        "readiness": readiness.model_dump(mode="json"),
        "resume_plan": resume.model_dump(mode="json"),
        "duration_policy": duration_policy_report.model_dump(mode="json"),
        "external_calls": validated_state.external_calls,
        "readiness_external_calls": validated_state.readiness_external_calls,
        "pollinations_readiness": (
            validated_state.pollinations_readiness.model_dump(mode="json")
            if validated_state.pollinations_readiness is not None
            else None
        ),
    }
    write_plan = [
        (paths.run_plan_path, _serialize_json_utf8(run_plan_payload)),
        (paths.runtime_state_path, _serialize_json_utf8(runtime_state_payload)),
        (paths.manifest_path, _serialize_json_utf8(manifest_payload)),
    ]
    if candidate_metadata is not None:
        write_plan.append((paths.candidate_metadata_path, _serialize_json_utf8(candidate_metadata)))

    for destination, content in write_plan:
        atomic_write_bytes(destination, content)


def load_runtime_state(path: Path | str) -> ExecutionRunState:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("persisted ExecutionRunState must be a JSON object")
    raw_run_plan = payload.get("run_plan")
    if not isinstance(raw_run_plan, dict):
        raise ValueError("persisted ExecutionRunState run_plan must be a JSON object")
    if "schema_version" not in raw_run_plan:
        raise ValueError("ProductionRunPlan schema_version is required")
    version = raw_run_plan["schema_version"]
    if type(version) is not int:
        raise ValueError("ProductionRunPlan schema_version must be an integer")
    if version == 2:
        migrated = ProductionRunPlan.migrate_schema_v2(raw_run_plan)
        payload["run_plan"] = migrated.model_dump(mode="python")
    elif version != 3:
        raise ValueError(f"unsupported ProductionRunPlan schema_version: {version}")
    return ExecutionRunState.model_validate(payload)
