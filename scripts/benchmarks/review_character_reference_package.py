"""Offline interactive approval for a complete four-view package draft."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tella.media.manual_package_approval import (
    CROSS_VIEW_CHECKS, PackageApprovalBlocked, VIEW_CHECKS,
    create_approval, validate_only, validate_package, verify_approval,
)


def _ask(prompt: str) -> str:
    return input(prompt).strip()


def interactive(*, repository_root: Path, package_id: str) -> dict[str, object]:
    data = validate_package(repository_root=repository_root, package_id=package_id)
    manifest = data["manifest"]
    print(f"Package: {package_id}\nCharacter: {manifest['character_id']}")
    for row in manifest["atomic_views"]:
        print(f"{row['asset_role']}: {row['path']} sha256={row['sha256']}")
    print(f"master_sheet: {manifest['master_sheet']['path']} sha256={manifest['master_sheet']['sha256']}")
    if _ask("Viewed master_sheet.png? Type yes: ").lower() != "yes":
        return {"status": "cancelled", "approval_created": 0}
    if _ask("Viewed all four atomic images at usable resolution? Type yes: ").lower() != "yes":
        return {"status": "cancelled", "approval_created": 0}
    per_view = {}
    for view, checks in VIEW_CHECKS.items():
        per_view[view] = {}
        for check in checks:
            answer = _ask(f"Confirm {view} {check}; type yes or no: ").lower()
            if answer not in {"yes", "no"}:
                raise PackageApprovalBlocked("every checklist answer must be explicit")
            per_view[view][check] = answer == "yes"
            if not per_view[view][check]:
                return {"status": "review_rejected", "approval_created": 0, "failed_check": check}
    cross = {}
    for check in CROSS_VIEW_CHECKS:
        answer = _ask(f"Confirm {check}; type yes or no: ").lower()
        if answer not in {"yes", "no"}:
            raise PackageApprovalBlocked("every checklist answer must be explicit")
        cross[check] = answer == "yes"
        if not cross[check]:
            return {"status": "review_rejected", "approval_created": 0, "failed_check": check}
    role = _ask("Approver role (required): ")
    notes = _ask("Substantive review notes (required): ")
    if not role or not notes:
        raise PackageApprovalBlocked("approver role and notes are required")
    manifest_digest = manifest["character_fingerprint"][:12]
    master_prefix = manifest["master_sheet"]["sha256"][:12]
    confirmations = {
        "package_id_confirmed": _ask(f"Confirm package ID {package_id}; type yes: ").lower() == "yes",
        "fingerprint_prefix_confirmed": _ask(f"Confirm fingerprint prefix {manifest_digest}; type yes: ").lower() == "yes",
        "master_sha256_prefix_confirmed": _ask(f"Confirm master SHA256 prefix {master_prefix}; type yes: ").lower() == "yes",
        "no_automatic_approval_confirmed": _ask("Confirm no image was approved automatically; type yes: ").lower() == "yes",
        "package_only_scope_confirmed": _ask("Confirm approval applies only to this four-view package; type yes: ").lower() == "yes",
    }
    if not all(confirmations.values()):
        return {"status": "cancelled", "approval_created": 0}
    path = create_approval(repository_root=repository_root, package_id=package_id, per_view_checklist=per_view,
                           cross_view_checklist=cross, master_sheet_viewed=True, atomic_views_viewed=True,
                           approver_role=role, review_notes=notes, confirmations=confirmations)
    return {"status": "package_approved", "approval_path": path.relative_to(repository_root.resolve()).as_posix(),
            "provider_execution_authorized": False, "scene_generation_authorized": False, "external_calls": 0}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("validate-only", "interactive-review", "verify-approval"), required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--package-id")
    args = parser.parse_args(argv)
    if args.mode == "validate-only":
        print(json.dumps(validate_only(), sort_keys=True))
        return 0
    if not args.package_id:
        parser.error("--package-id is required")
    result = interactive(repository_root=args.repository_root, package_id=args.package_id) if args.mode == "interactive-review" else verify_approval(repository_root=args.repository_root, package_id=args.package_id)
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PackageApprovalBlocked as exc:
        raise SystemExit(f"blocked: {exc}") from None
