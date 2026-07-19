"""Small CLI surface for four-scene proof rendering."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from .orchestrator import load_proof_plan, render_proof
from .providers.existing import ExistingTellaProviderAdapter
from .references import ReferenceMissingError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tella.visual_generation")
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("render-proof", help="render or validate the four-scene proof")
    render.add_argument("--plan", type=Path, required=True)
    render.add_argument("--style", type=Path, required=True)
    render.add_argument("--reference-root", type=Path, required=True)
    render.add_argument("--out", type=Path, default=Path("out"))
    render.add_argument("--job-id", required=True)
    render.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "render-proof":
        return 2
    provider = None if args.dry_run else ExistingTellaProviderAdapter(
        name=os.environ.get("TELLA_IMAGE_PROVIDER") or "cloudflare"
    )
    if not args.dry_run and provider is not None:
        plan = load_proof_plan(args.plan)
        caps = provider.capabilities()
        print(
            json.dumps(
                {
                    "selected_provider": caps.provider_id,
                    "selected_model": caps.model,
                    "planned_candidate_images": len(plan.scenes) * plan.candidate_count,
                    "maximum_generation_calls": (
                        len(plan.scenes) * plan.max_generation_attempts_per_scene
                    ),
                    "edits_may_add_calls": plan.max_repairs_per_candidate > 0,
                    "credentials_present": provider.credentials_present(),
                    "supports_required_references": caps.supports_reference_images,
                    "live_opt_in": os.environ.get("TELLA_VISUAL_QUALITY_LIVE") == "1",
                },
                indent=2,
            )
        )
    try:
        summary = asyncio.run(
            render_proof(
                plan_path=args.plan,
                style_path=args.style,
                reference_root=args.reference_root,
                out_root=args.out,
                job_id=args.job_id,
                dry_run=args.dry_run,
                provider=provider,
            )
        )
    except ReferenceMissingError as exc:
        print("LIVE_VISUAL_ACCEPTANCE_BLOCKED_REFERENCE_MISSING")
        print(str(exc))
        return 2
    except (RuntimeError, ValueError) as exc:
        print(str(exc))
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0
