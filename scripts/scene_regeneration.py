"""Command-line wrapper for safe scene-specific image regeneration."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from tella.scene_regeneration import load_corrections, regenerate_scenes


def _indices(value: str) -> list[int]:
    try:
        return [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("scene indices must be comma-separated integers") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a derived job by regenerating selected scene images.")
    parser.add_argument("--source-job", type=Path, required=True)
    parser.add_argument("--target-job-id", required=True)
    parser.add_argument("--scene-indices", type=_indices, required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--max-ai-images", type=int, required=True)
    parser.add_argument("--prompt-corrections", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--recover-stale-lock", action="store_true")
    parser.add_argument("--no-render", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = args.source_job.resolve()
    target = source.parent / args.target_job_id
    corrections = load_corrections(args.prompt_corrections)
    previous = {name: os.environ.get(name) for name in (
        "TELLA_CF_MAX_ACCOUNTS", "TELLA_CF_MAX_RETRIES_PER_ACCOUNT",
        "TELLA_ALLOW_LOCAL_IMAGE_FALLBACK", "TELLA_DISABLE_STOCK_FALLBACK",
    )}
    os.environ.update({
        "TELLA_CF_MAX_ACCOUNTS": "1", "TELLA_CF_MAX_RETRIES_PER_ACCOUNT": "1",
        "TELLA_ALLOW_LOCAL_IMAGE_FALLBACK": "0", "TELLA_DISABLE_STOCK_FALLBACK": "1",
    })
    try:
        result = asyncio.run(regenerate_scenes(
            source, target, scene_indices=args.scene_indices, reason=args.reason,
            max_ai_images=args.max_ai_images, corrections=corrections,
            dry_run=args.dry_run, no_render=args.no_render,
            recover_stale_lock=args.recover_stale_lock, output=args.output,
        ))
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered if args.json else (
        f"source={result['source_job_id']} target={result['target_job_id']} "
        f"regenerate={result['regenerated_scene_indices']} "
        f"reuse={result['reused_scene_indices']} requests={result.get('actual_image_request_count', 0)}"
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
