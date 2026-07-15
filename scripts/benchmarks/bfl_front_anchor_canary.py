"""Validate-only and explicitly gated three-candidate direct-BFL canary."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from scripts.benchmarks.character_reference_bootstrap import load_and_validate_plan
from tella.media.bfl_front_anchor_provider import (
    AUTHORIZATION_TOKEN,
    BFLFrontAnchorConfig,
    PROVIDER_ID,
)
from tella.media.front_anchor_harness import validate_output_root


SEEDS = (17001, 17002, 17003)


def validate_only(*, config_path: Path, repository_root: Path, session_id: str) -> dict[str, object]:
    config = load_and_validate_plan(config_path, repository_root=repository_root)
    front = config.request_specs[0]
    plan = BFLFrontAnchorConfig()
    output_root = validate_output_root(
        type("Plan", (), {"output_root": Path("out") / "character_reference_bootstrap" / session_id})(),
        repository_root=repository_root,
    )
    return {
        "status": "valid_no_execution",
        "provider_id": PROVIDER_ID,
        "endpoint_path": "/v1/flux-pro-1.1",
        "character_fingerprint": config.character_fingerprint,
        "prompt_sha256": front.prompt_sha256,
        "dimensions": [plan.width, plan.height],
        "output_format": plan.output_format,
        "prompt_upsampling": plan.prompt_upsampling,
        "seeds": list(SEEDS),
        "initial_candidates": 3,
        "targeted_candidates": 0,
        "maximum_submissions": 3,
        "create_attempts_max": 3,
        "polling_bounded": True,
        "result_downloads_max": 3,
        "automatic_retries": 0,
        "fallbacks": 0,
        "authorization_required": AUTHORIZATION_TOKEN,
        "output_root": (Path("out") / "character_reference_bootstrap" / session_id).as_posix(),
        "output_root_resolved": str(output_root),
        "provider_clients_constructed": 0,
        "provider_calls": 0,
        "external_calls": 0,
        "generated_artifacts": 0,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--mode", choices=("validate-only", "live-front-bfl"), required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--session-id", default="bfl_front_anchor_validate_01")
    parser.add_argument("--authorization-token", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = validate_only(
        config_path=args.config, repository_root=args.repository_root, session_id=args.session_id
    )
    if args.mode == "live-front-bfl":
        result["status"] = "blocked_no_execution"
        if args.authorization_token != AUTHORIZATION_TOKEN:
            result["live_blocker"] = "exact BFL front-anchor authorization is required"
        else:
            result["live_blocker"] = "live execution requires separately reviewed BFL transport and credential gate"
        result["credential_present"] = bool(os.environ.get("BFL_API_KEY"))
        print(json.dumps(result, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
