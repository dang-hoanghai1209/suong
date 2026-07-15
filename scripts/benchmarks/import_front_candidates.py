"""Offline validation and import of three user-supplied front candidates."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.benchmarks.character_reference_bootstrap import load_and_validate_plan
from tella.media.manual_front_import import import_candidates


def validate_only(*, config_path: Path, repository_root: Path, session_id: str) -> dict[str, object]:
    config = load_and_validate_plan(config_path, repository_root=repository_root)
    front = config.request_specs[0]
    return {
        "status": "valid_no_execution",
        "workflow": "manual_front_candidate_import",
        "manual_import_available": True,
        "paid_providers_optional": True,
        "provider_clients_constructed": 0,
        "credential_reads": 0,
        "external_calls": 0,
        "generated_artifacts": 0,
        "character_id": config.character_id,
        "character_fingerprint": config.character_fingerprint,
        "prompt_sha256": front.prompt_sha256,
        "expected_dimensions": [front.width, front.height],
        "expected_mime": front.output_mime_type,
        "candidate_count": 3,
        "output_root": (Path("out") / "character_reference_bootstrap" / session_id).as_posix(),
        "semantic_qc": "pending_human_review",
        "automatic_selection": False,
        "stage_b_requested": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("validate-only", "import"), required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--candidate-01", type=Path)
    parser.add_argument("--candidate-02", type=Path)
    parser.add_argument("--candidate-03", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.mode == "validate-only":
        print(json.dumps(validate_only(
            config_path=args.config, repository_root=args.repository_root,
            session_id=args.session_id,
        ), sort_keys=True))
        return 0
    sources = (args.candidate_01, args.candidate_02, args.candidate_03)
    if any(path is None for path in sources):
        print(json.dumps({"status": "blocked_no_execution", "safe_error": "exactly_three_candidates_required"}))
        return 2
    config = load_and_validate_plan(args.config, repository_root=args.repository_root)
    front = config.request_specs[0]
    try:
        output = import_candidates(
            repository_root=args.repository_root, session_id=args.session_id,
            sources=sources, character_id=config.character_id,
            character_fingerprint=config.character_fingerprint,
            canonical_spec_version=1,
            generation_spec_version=config.generation_spec_version,
            prompt=front.prompt, prompt_sha256=front.prompt_sha256,
        )
    except Exception:
        print(json.dumps({"status": "blocked_no_execution", "safe_error": "manual_import_validation_failed"}))
        return 2
    print(json.dumps({
        "status": "awaiting_front_selection", "output_root": output.relative_to(args.repository_root.resolve()).as_posix(),
        "provider_calls": 0, "external_calls": 0, "automatic_selection": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
