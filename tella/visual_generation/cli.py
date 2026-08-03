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
from .providers.cloudflare_flux import (
    DEFAULT_HEIGHT as CLOUDFLARE_DEFAULT_HEIGHT,
    DEFAULT_MODEL as CLOUDFLARE_DEFAULT_MODEL,
    DEFAULT_WIDTH as CLOUDFLARE_DEFAULT_WIDTH,
    HTTP_TIMEOUT as CLOUDFLARE_HTTP_TIMEOUT,
    CloudflareFluxSceneImageProvider,
)
from .providers.gemini import DEFAULT_MODEL as GEMINI_DEFAULT_MODEL
from .providers.gemini import GeminiSceneImageProvider
from .references import REFERENCE_FILES, ReferenceMissingError
from .tiers import VisualQualityTier, resolve_visual_tier


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
    render.add_argument(
        "--provider", choices=("gemini", "cloudflare-flux", "existing")
    )
    render.add_argument("--tier", choices=tuple(item.value for item in VisualQualityTier))
    render.add_argument("--model")
    render.add_argument(
        "--resolution", choices=("0.5K", "1K", "2K", "4K"), default="1K"
    )
    render.add_argument(
        "--scene", choices=tuple(f"scene_{i:02d}" for i in range(1, 5))
    )
    render.add_argument("--width", type=int)
    render.add_argument("--height", type=int)
    render.add_argument("--seed", type=int)
    render.add_argument("--steps", type=int)
    render.add_argument("--timeout-seconds", type=float)
    render.add_argument(
        "--chain-accepted-scenes",
        action="store_true",
        help="explicitly use an accepted Scene 1 output as continuity input for Scenes 3-4",
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
    tier_config = None
    try:
        if args.tier:
            tier_config = resolve_visual_tier(
                args.tier,
                provider=args.provider,
                model=args.model,
                steps=args.steps,
                timeout_seconds=args.timeout_seconds,
            )
            provider = CloudflareFluxSceneImageProvider(
                model=tier_config.model,
                width=args.width or CLOUDFLARE_DEFAULT_WIDTH,
                height=args.height or CLOUDFLARE_DEFAULT_HEIGHT,
                steps=tier_config.steps,
                timeout_seconds=tier_config.timeout_seconds,
                tier=tier_config.tier.value,
                intended_usage_class=tier_config.output_intent,
            )
        elif args.provider == "gemini":
            provider = GeminiSceneImageProvider(
                model=args.model or GEMINI_DEFAULT_MODEL, resolution=args.resolution
            )
        elif args.provider == "cloudflare-flux":
            provider = CloudflareFluxSceneImageProvider(
                model=args.model or CLOUDFLARE_DEFAULT_MODEL,
                width=args.width or CLOUDFLARE_DEFAULT_WIDTH,
                height=args.height or CLOUDFLARE_DEFAULT_HEIGHT,
                steps=args.steps,
                timeout_seconds=(
                    args.timeout_seconds
                    if args.timeout_seconds is not None
                    else CLOUDFLARE_HTTP_TIMEOUT
                ),
            )
        else:
            provider = ExistingTellaProviderAdapter(
                name=os.environ.get("TELLA_IMAGE_PROVIDER") or "cloudflare"
            )
    except ValueError as exc:
        print(str(exc))
        return 2
    if provider is not None:
        plan = load_proof_plan(args.plan)
        caps = provider.capabilities()
        selected_scenes = (
            [scene for scene in plan.scenes if scene.scene_id == args.scene]
            if args.scene
            else plan.scenes
        )
        reference_counts = {
            scene.scene_id: len(
                {
                    REFERENCE_FILES[role][0]
                    for role in scene.reference_roles
                    if role in REFERENCE_FILES
                }
            )
            for scene in selected_scenes
        }
        print(
            json.dumps(
                {
                    "selected_tier": tier_config.tier.value if tier_config else None,
                    "selected_provider": caps.provider_id,
                    "selected_model": caps.model,
                    "intended_usage_class": (
                        tier_config.output_intent if tier_config else None
                    ),
                    "cost_posture": tier_config.cost_posture if tier_config else None,
                    "selected_scenes": [scene.scene_id for scene in selected_scenes],
                    "planned_candidate_images": (
                        1 if args.scene else len(plan.scenes) * plan.candidate_count
                    ),
                    "maximum_generation_calls": (
                        1
                        if args.scene
                        else len(plan.scenes) * plan.max_generation_attempts_per_scene
                    ),
                    "maximum_edit_calls": 0,
                    "credentials_present": (
                        provider.credentials_present() if not args.dry_run else None
                    ),
                    "supports_required_references": caps.supports_reference_images,
                    "live_opt_in": os.environ.get("TELLA_VISUAL_QUALITY_LIVE") == "1",
                    "requested_aspect_ratio": "9:16",
                    "requested_resolution": (
                        f"{provider.width}x{provider.height}"
                        if isinstance(provider, CloudflareFluxSceneImageProvider)
                        else args.resolution
                    ),
                    "requested_width": getattr(provider, "width", None),
                    "requested_height": getattr(provider, "height", None),
                    "reference_count": reference_counts,
                    "accepted_scene_chaining": (
                        args.chain_accepted_scenes if tier_config else True
                    ),
                    "steps": getattr(provider, "steps", None),
                    "timeout_seconds": getattr(provider, "timeout_seconds", None),
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
                seed_override=args.seed,
                tier=tier_config.tier.value if tier_config else None,
                intended_usage_class=(tier_config.output_intent if tier_config else None),
                chain_accepted_scenes=(
                    args.chain_accepted_scenes if tier_config else True
                ),
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
