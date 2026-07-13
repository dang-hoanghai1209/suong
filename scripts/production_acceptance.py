"""Local-only production visual acceptance command."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tella.visual_acceptance import (
    corrections_from_review, initialize_review, load_review, load_suite,
    report_acceptance,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Initialize, validate, and report human visual acceptance.")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create an unreviewed seven-scene review file")
    init.add_argument("--job", type=Path, required=True)
    init.add_argument("--output", type=Path, required=True)
    validate = commands.add_parser("validate", help="validate a versioned suite")
    validate.add_argument("--suite", type=Path, required=True)
    report = commands.add_parser("report", help="aggregate existing jobs and human reviews")
    report.add_argument("--suite", type=Path, required=True)
    report.add_argument("--jobs-root", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)
    corrections = commands.add_parser("corrections", help="create editable correction templates")
    corrections.add_argument("--review", type=Path, required=True)
    corrections.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init":
        payload = initialize_review(args.job, args.output).model_dump(mode="json")
        exit_code = 0
    elif args.command == "validate":
        suite = load_suite(args.suite)
        payload = {"valid": True, "suite_id": suite.suite_id, "case_count": len(suite.cases)}
        exit_code = 0
    elif args.command == "corrections":
        payload = corrections_from_review(load_review(args.review), args.output)
        exit_code = 0
    else:
        payload = report_acceptance(load_suite(args.suite), args.jobs_root, args.output)
        exit_code = int(payload["exit_code"])
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
