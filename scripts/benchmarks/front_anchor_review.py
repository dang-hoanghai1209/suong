"""Zero-network validation for the local front-anchor review boundary."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tella.media.front_anchor_harness import OUTPUT_ROOT_PREFIX


def validate_only(*, output_dir: Path) -> dict[str, object]:
    if output_dir.is_absolute() or ".." in output_dir.parts:
        raise ValueError("review output directory must be repository-relative")
    if output_dir.parts[: len(OUTPUT_ROOT_PREFIX.parts)] != OUTPUT_ROOT_PREFIX.parts:
        raise ValueError("review output directory must be under the ignored bootstrap directory")
    return {
        "status": "valid_no_execution",
        "output_dir": output_dir.as_posix(),
        "expected_candidate_count": 3,
        "expected_files": [
            "candidate_01.png", "candidate_02.png", "candidate_03.png",
            "candidates_manifest.json", "contact_sheet.png", "review_template.json",
        ],
        "auto_selection": False,
        "contact_sheet_local_only": True,
        "provider_calls": 0,
        "external_calls": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("validate-only",), required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("out/character_reference_bootstrap/front_anchor_review_validate_01"),
    )
    args = parser.parse_args(argv)
    print(json.dumps(validate_only(output_dir=args.output_dir), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
