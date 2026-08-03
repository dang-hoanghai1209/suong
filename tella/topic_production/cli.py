"""Network-free preview CLI for topic-aware production contracts."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .execution import build_fixture_preview_run
from .manifest import build_initial_manifest
from .planner import DeterministicTopicPlanner, build_scene_briefs, validate_topic_fidelity


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tella.topic_production")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--language", default="vi")
    parser.add_argument("--scene-count", type=int, choices=(7, 8), default=8)
    parser.add_argument("--target-duration", type=float, default=35.0)
    parser.add_argument("--job-id", default="topic-preview")
    return parser


def build_production_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tella.topic_production plan-production")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--language", default="vi")
    parser.add_argument("--scene-count", type=int, choices=(7, 8), default=8)
    parser.add_argument("--target-duration", type=float, default=35.0)
    parser.add_argument("--job-id", default="topic-production-preview")
    parser.add_argument(
        "--reference-root",
        type=Path,
        help="approved static reference pack (or set TELLA_APPROVED_REFERENCE_ROOT)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "plan-production":
        args = build_production_parser().parse_args(raw[1:])
        run_plan = build_fixture_preview_run(
            topic=args.topic,
            language=args.language,
            scene_count=args.scene_count,
            target_duration_seconds=args.target_duration,
            job_id=args.job_id,
            reference_root=(
                args.reference_root or os.environ.get("TELLA_APPROVED_REFERENCE_ROOT")
            ),
        )
        print(
            json.dumps(
                {
                    "plan_label": run_plan.plan_label,
                    "planner_mode": run_plan.story_plan.planner_metadata.planner_mode.value,
                    "production_eligible": (
                        run_plan.story_plan.planner_metadata.production_eligible
                    ),
                    "job_id": run_plan.job_id,
                    "topic": run_plan.topic,
                    "planning_hash": run_plan.planning_hash,
                    "scenes": [
                        {
                            "scene_id": scene.scene_id,
                            "type": scene.scene_brief.scene_type.value,
                            "meaning": scene.scene_brief.meaning,
                            "duration_seconds": scene.timing.duration_seconds,
                            "seed": scene.draft.seed,
                            "draft_model": scene.draft.model,
                            "references": [
                                {"id": item.reference_id, "roles": item.roles}
                                for item in scene.draft.references
                            ],
                            "reference_decisions": [
                                {"role": item.role, "status": item.status.value}
                                for item in scene.draft.reference_decisions
                            ],
                            "acceptance_priority": scene.acceptance_policy.priority.value,
                            "initial_state": scene.initial_status.value,
                        }
                        for scene in run_plan.scene_execution_plans
                    ],
                    "render_readiness": run_plan.manifest.render_ready,
                    "blocked_reasons": run_plan.manifest.blocked_reasons,
                    "external_calls": run_plan.external_calls,
                },
                ensure_ascii=True,
                indent=2,
            )
        )
        return 0
    args = build_parser().parse_args(raw)
    plan = DeterministicTopicPlanner().plan(
        topic=args.topic,
        language=args.language,
        scene_count=args.scene_count,
        target_duration_seconds=args.target_duration,
    )
    briefs = build_scene_briefs(plan)
    fidelity = validate_topic_fidelity(plan, briefs)
    manifest = build_initial_manifest(job_id=args.job_id, plan=plan, briefs=briefs)
    print(
        json.dumps(
            {
                "story_plan": plan.model_dump(mode="json"),
                "scene_briefs": [brief.model_dump(mode="json") for brief in briefs],
                "timings": [timing.model_dump(mode="json") for timing in manifest.timings],
                "initial_states": {
                    scene.brief.scene_id: scene.status.value for scene in manifest.scenes
                },
                "topic_fidelity": fidelity.model_dump(mode="json"),
                "render_readiness": manifest.render_ready,
                "blocked_reasons": manifest.blocked_reasons,
                "external_calls": 0,
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0
