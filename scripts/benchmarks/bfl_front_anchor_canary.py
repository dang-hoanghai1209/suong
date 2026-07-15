"""Validate-only and explicitly gated three-candidate direct-BFL canary."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

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


def build_live_plan(*, config_path: Path, repository_root: Path, session_id: str):
    from tella.media.bfl_front_anchor_orchestration import LiveFrontPlan, SEEDS

    config = load_and_validate_plan(config_path, repository_root=repository_root)
    front = config.request_specs[0]
    return LiveFrontPlan(
        session_id=session_id,
        character_id=config.character_id,
        character_fingerprint=config.character_fingerprint,
        canonical_spec_version=1,
        generation_spec_version=config.generation_spec_version,
        prompt=front.prompt,
        prompt_sha256=front.prompt_sha256,
        asset_role=front.asset_role,
        width=front.width,
        height=front.height,
        output_format="png",
        prompt_upsampling=False,
        seeds=SEEDS,
        maximum_submissions=3,
        targeted_submissions=0,
        retries=0,
        fallbacks=0,
        output_root=Path("out") / "character_reference_bootstrap" / session_id,
    )


def _live_provider_factory(key):
    from tella.media.bfl_front_anchor_orchestration import ProviderBundle
    from tella.media.bfl_front_anchor_provider import BFLFrontAnchorConfig, BFLFrontAnchorProvider
    from tella.media.bfl_front_anchor_transport import build_bfl_front_anchor_http_transport

    transport = build_bfl_front_anchor_http_transport(key)
    provider = BFLFrontAnchorProvider(
        config=BFLFrontAnchorConfig(), transport=transport, api_key=key, accounting={}
    )
    return ProviderBundle(provider=provider, close=transport.close)


async def execute_live_mode(args) -> Any:
    from pydantic import SecretStr
    from tella.media.bfl_front_anchor_orchestration import execute_live_front

    plan = build_live_plan(
        config_path=args.config, repository_root=args.repository_root,
        session_id=args.session_id,
    )
    return await execute_live_front(
        plan=plan,
        repository_root=args.repository_root,
        authorization_token=args.authorization_token,
        credential_reader=lambda: (
            SecretStr(os.environ["BFL_API_KEY"]) if "BFL_API_KEY" in os.environ else None
        ),
        provider_factory=_live_provider_factory,
    )


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
        live = asyncio.run(execute_live_mode(args))
        result["status"] = live.status
        result["manifest_path"] = (
            live.manifest_path.as_posix() if live.manifest_path is not None else None
        )
        print(json.dumps(result, sort_keys=True))
        return live.exit_code
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
