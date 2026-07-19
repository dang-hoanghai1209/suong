"""Small CLI surface for four-scene proof rendering."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from .orchestrator import load_proof_plan, render_proof
from .providers.existing import ExistingTellaProviderAdapter
from .providers.gemini import DEFAULT_MODEL, GeminiSceneImageProvider
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
    render.add_argument("--live", action="store_true")
    render.add_argument("--provider", choices=("gemini", "existing"), default="existing")
    render.add_argument("--model", default=DEFAULT_MODEL)
    render.add_argument(
        "--resolution", choices=("0.5K", "1K", "2K", "4K"), default="1K"
    )
    render.add_argument(
        "--scene", choices=tuple(f"scene_{i:02d}" for i in range(1, 5))
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv(Path(__file__).parents[2] / ".env", override=False)
    args = build_parser().parse_args(argv)
    if args.command != "render-proof":
        return 2
    if args.dry_run and args.live:
        print("--dry-run and --live are mutually exclusive")
        return 2
    if not args.dry_run and not args.live:
        print("LIVE_VISUAL_ACCEPTANCE_NOT_RUN_OPT_IN_REQUIRED")
        return 2
    if args.dry_run:
        provider = None
    elif args.provider == "gemini":
        provider = GeminiSceneImageProvider(
            model=args.model, resolution=args.resolution
        )
    else:
        provider = ExistingTellaProviderAdapter(
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
                    "selected_scenes": (
                        [args.scene] if args.scene else [scene.scene_id for scene in plan.scenes]
                    ),
                    "planned_candidate_images": (
                        1 if args.scene else len(plan.scenes) * plan.candidate_count
                    ),
                    "maximum_generation_calls": (
                        1
                        if args.scene
                        else len(plan.scenes) * plan.max_generation_attempts_per_scene
                    ),
                    "maximum_edit_calls": 0,
                    "credentials_present": provider.credentials_present(),
                    "supports_required_references": caps.supports_reference_images,
                    "live_opt_in": os.environ.get("TELLA_VISUAL_QUALITY_LIVE") == "1",
                    "requested_aspect_ratio": "9:16",
                    "requested_resolution": args.resolution,
                    "reference_count": "scene-dependent (1-3)",
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
                scene_id=args.scene,
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
