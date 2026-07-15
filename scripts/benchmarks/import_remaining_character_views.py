"""Offline import and draft assembly for the three remaining character views."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tella.media.manual_four_view_package import ManualPackageBlocked, import_views, validate_only, verify_draft


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("validate-only", "import-views", "verify-draft"), required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--front-anchor-session-id")
    parser.add_argument("--package-id")
    parser.add_argument("--three-quarter", type=Path)
    parser.add_argument("--side-profile", type=Path)
    parser.add_argument("--full-body-neutral", type=Path)
    args = parser.parse_args(argv)
    if args.mode == "validate-only":
        print(json.dumps(validate_only(), sort_keys=True))
        return 0
    if not args.package_id:
        parser.error("--package-id is required")
    if args.mode == "verify-draft":
        print(json.dumps(verify_draft(repository_root=args.repository_root, package_id=args.package_id), sort_keys=True))
        return 0
    required = (args.front_anchor_session_id, args.three_quarter, args.side_profile, args.full_body_neutral)
    if any(value is None for value in required):
        parser.error("import-views requires front-anchor session and all three view sources")
    output = import_views(repository_root=args.repository_root, front_anchor_session_id=args.front_anchor_session_id,
                          package_id=args.package_id, three_quarter=args.three_quarter,
                          side_profile=args.side_profile, full_body_neutral=args.full_body_neutral)
    print(json.dumps({"status": "published", "package_path": output.relative_to(args.repository_root.resolve()).as_posix(), "external_calls": 0}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ManualPackageBlocked as exc:
        raise SystemExit(f"blocked: {exc}") from None
