"""Zero-network validation boundary for the bounded front-anchor harness."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.benchmarks.character_reference_bootstrap import load_and_validate_plan
from tella.media.front_anchor_harness import (
    LIVE_AUTHORIZATION_TOKEN,
    FrontHarnessBlocked,
    build_front_plan,
    cloudflare_adapter_audit,
    plan_initial_front_candidates,
    validate_live_front,
    validate_output_root,
)


def build_plan(*, config_path: Path, repository_root: Path, session_id: str):
    config = load_and_validate_plan(config_path, repository_root=repository_root)
    front = config.request_specs[0]
    return build_front_plan(
        session_id=session_id,
        character_fingerprint=config.character_fingerprint,
        prompt=front.prompt,
        prompt_sha256=front.prompt_sha256,
        generation_spec_version=config.generation_spec_version,
        repository_root=repository_root,
    )


def validate_only(
    *, config_path: Path, repository_root: Path, session_id: str
) -> dict[str, object]:
    plan = build_plan(
        config_path=config_path, repository_root=repository_root, session_id=session_id
    )
    output_root = validate_output_root(plan, repository_root=repository_root)
    requests = plan_initial_front_candidates(plan)
    audit = cloudflare_adapter_audit()
    live_blockers = []
    if not plan.adapter_exact_dimensions_proven:
        live_blockers.append("exact_768x1024_output_not_proven")
    if plan.adapter_retry_control != "caller_bounded" or plan.adapter_max_attempts_per_account != 1:
        live_blockers.append("one_attempt_no_retry_contract_not_proven")
    return {
        "status": "valid_no_execution",
        "provider": plan.provider_id,
        "model": plan.model,
        "character_fingerprint": plan.character_fingerprint,
        "asset_role": plan.asset_role,
        "prompt_sha256": plan.prompt_sha256,
        "dimensions": [plan.width, plan.height],
        "mime_type": plan.output_mime_type,
        "initial_candidates": len(requests),
        "targeted_candidates": 0,
        "maximum_submissions": 3,
        "automatic_retries": 0,
        "fallbacks": 0,
        "request_ids_available": False,
        "stage_b_requested": False,
        "output_root": plan.output_root.as_posix(),
        "output_root_resolved": str(output_root),
        "output_root_exists_before_execution": output_root.exists(),
        "live_front_status": "blocked",
        "live_front_blockers": live_blockers,
        "provider_audit": audit,
        "provider_clients_constructed": 0,
        "provider_calls": 0,
        "external_calls": 0,
        "generated_artifacts": 0,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--mode", choices=("validate-only", "live-front"), required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--session-id", default="front_anchor_bootstrap_validate_01")
    parser.add_argument("--authorization-token", default="")
    parser.add_argument("--allow-dirty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = validate_only(
        config_path=args.config,
        repository_root=args.repository_root,
        session_id=args.session_id,
    )
    if args.mode == "live-front":
        plan = build_plan(
            config_path=args.config,
            repository_root=args.repository_root,
            session_id=args.session_id,
        )
        try:
            validate_live_front(
                plan,
                repository_root=args.repository_root,
                authorization_token=args.authorization_token,
                clean_worktree=not args.allow_dirty,
            )
        except FrontHarnessBlocked as exc:
            result["live_front_status"] = "blocked"
            result["live_front_blockers"] = [str(exc)]
            result["authorization_token_required"] = LIVE_AUTHORIZATION_TOKEN
            print(json.dumps(result, sort_keys=True))
            return 2
        raise SystemExit(
            "live-front execution is intentionally not installed; provider operation requires a separate review"
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
