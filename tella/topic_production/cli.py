"""Network-free preview CLI for topic-aware production contracts."""
from __future__ import annotations

import argparse
import json

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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
