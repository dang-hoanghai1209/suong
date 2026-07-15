"""Offline terminal review for a manually imported front-anchor session."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tella.media.manual_front_review import (
    FrontReviewBlocked, SEMANTIC_CHECKS, create_selection, validate_only,
    validate_session, verify_selection,
)


def _ask(prompt: str) -> str:
    return input(prompt).strip()


def interactive(*, repository_root: Path, session_id: str) -> dict[str, object]:
    data = validate_session(repository_root=repository_root, session_id=session_id)
    manifest = data["manifest"]
    print(f"Session: {manifest.session_id}")
    print(f"Character: {manifest.character_id}")
    print("Open contact_sheet.png before continuing.")
    if _ask("Have you viewed the contact sheet? Type yes: ").lower() != "yes":
        return {"status": "cancelled", "selection_created": 0}
    for row in manifest.candidates:
        print(f"{row.candidate_id}: {row.copied_sha256} ({row.semantic_review_status})")
        if row.duplicate_group:
            print(f"  duplicate warning: {row.duplicate_group}")
    candidate = _ask("Select exactly candidate_01, candidate_02, or candidate_03: ")
    if candidate not in {"candidate_01", "candidate_02", "candidate_03"}:
        raise FrontReviewBlocked("unknown or multiple candidate selection")
    row = data["rows"][candidate]
    if row.duplicate_group and _ask("Duplicate candidate; explicitly confirm with yes: ").lower() != "yes":
        return {"status": "cancelled", "selection_created": 0}
    checklist: dict[str, bool] = {}
    for name in SEMANTIC_CHECKS:
        answer = _ask(f"Confirm {name}; type yes or no: ").lower()
        if answer not in {"yes", "no"}:
            raise FrontReviewBlocked("each checklist answer must be explicit")
        checklist[name] = answer == "yes"
        if not checklist[name]:
            return {"status": "review_rejected", "selection_created": 0, "failed_check": name}
    role = _ask("Approver role (required): ")
    notes = _ask("Review notes (required): ")
    if not role or not notes:
        raise FrontReviewBlocked("approver role and review notes are required")
    prefix = _ask("Confirm selected SHA256 prefix: ")
    if _ask(f"Confirm final selection {candidate}; type yes: ").lower() != "yes":
        return {"status": "cancelled", "selection_created": 0}
    path = create_selection(repository_root=repository_root, session_id=session_id,
                            candidate_id=candidate, checklist=checklist,
                            contact_sheet_viewed=True, approver_role=role,
                            review_notes=notes, selected_prefix=prefix)
    return {"status": "front_anchor_locked", "selection_path": path.relative_to(repository_root).as_posix(),
            "selected_candidate_id": candidate, "provider_calls": 0, "external_calls": 0}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("validate-only", "interactive-review", "verify-selection"), required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--session-id")
    args = parser.parse_args(argv)
    if args.mode == "validate-only":
        print(json.dumps(validate_only(), sort_keys=True))
        return 0
    if not args.session_id:
        parser.error("--session-id is required for this mode")
    result = interactive(repository_root=args.repository_root, session_id=args.session_id) if args.mode == "interactive-review" else verify_selection(repository_root=args.repository_root, session_id=args.session_id)
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FrontReviewBlocked as exc:
        raise SystemExit(f"blocked: {exc}") from None
