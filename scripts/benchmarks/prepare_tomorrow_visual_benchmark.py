"""Controlled, explicitly selected prepare-tomorrow visual benchmark harness."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tella.media.image_provider import CloudflareImageProvider
from tella.media.image_provider_contract import CharacterIdentityMode, validate_identity_mode
from tella.planner.practical_life_steps import plan_practical_life_steps_from_script
from tella.planner.practical_prompt_policy import validate_priority_prompt
from tella.visual_acceptance import (
    canonical_script_for_case,
    load_suite,
    visual_profile_for_case,
)


CASE_ID = "prepare_tomorrow_night_before"
SOURCE_JOB_ID = "prepare_tomorrow_source_02"
SUITE_PATH = Path("configs/acceptance/practical_life_steps_visual_v1.json")


def benchmark_execution_envelope() -> dict[str, Any]:
    return {
        "benchmark_case_id": CASE_ID,
        "source_job_id": SOURCE_JOB_ID,
        "fresh_candidate_count_per_scene": 1,
        "maximum_targeted_candidates_per_failed_scene": 2,
        "maximum_candidates_per_scene": 3,
        "maximum_cloudflare_submissions": 17,
        "maximum_transport_attempts_per_submission": 1,
        "automatic_provider_retries": 0,
        "fallbacks": 0,
        "gemini_submissions": 0,
        "narration_generation": 0,
        "music_processing": 0,
        "render_operations": 0,
        "stop_after_images": True,
        "reuse_rejected_images": False,
    }


def validate_benchmark(repository_root: Path) -> dict[str, Any]:
    root = Path(repository_root).resolve()
    suite = load_suite(root / SUITE_PATH, repository_root=root)
    case, script = canonical_script_for_case(suite, CASE_ID, root)
    profile = visual_profile_for_case(suite, CASE_ID, root)
    capabilities = CloudflareImageProvider().capabilities()
    identity_mode = validate_identity_mode(
        CharacterIdentityMode.approximate_character_continuity,
        capabilities,
    )
    plan = plan_practical_life_steps_from_script(
        user_script=script.canonical_narration_text.removesuffix("\n"),
        target_lang="vi",
        preserve_narration=True,
        visual_profile=profile,
    )
    prompt_sizes = [
        validate_priority_prompt(scene.provider_prompt_variant)
        for scene in plan.scenes
        if scene.kind == "scene"
    ]
    if any(size > capabilities.max_prompt_utf8_bytes for size in prompt_sizes):
        raise ValueError("benchmark prompt exceeds provider capability limit")
    return {
        "status": "validated_no_execution",
        "case_id": case.case_id,
        "script_path": case.canonical_script.script_path if case.canonical_script else "",
        "script_sha256": script.canonical_script_sha256,
        "visual_profile_id": profile.profile_id,
        "visual_profile_version": profile.schema_version,
        "identity_mode": identity_mode.value,
        "scene_count": len(prompt_sizes),
        "prompt_utf8_bytes": prompt_sizes,
        "provider_capabilities": capabilities.model_dump(mode="json"),
        "request_envelope": benchmark_execution_envelope(),
        "actual_provider_calls": 0,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", nargs="?", choices=("validate", "run"), default="validate",
        help="validate performs no provider calls; run requires a separately reviewed executor",
    )
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = validate_benchmark(args.repository_root)
    if args.command == "run":
        raise RuntimeError(
            "real benchmark execution is not enabled by this validation harness; "
            "attach a separately reviewed bounded executor before provider submission"
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
