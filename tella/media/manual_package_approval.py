"""Offline interactive approval and verification for a four-view package draft."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tella.atomic_write import atomic_write_json
from tella.media.character_reference_package import ATOMIC_VIEW_ORDER
from tella.media.manual_four_view_package import (
    ManualPackageBlocked, _package_path, _sha, verify_draft,
)


APPROVAL_FILENAME = "package_approval.json"
VIEW_CHECKS = {
    "front_portrait": tuple(f"front_check_{i:02d}" for i in range(1, 21)),
    "three_quarter_portrait": tuple(f"three_quarter_check_{i:02d}" for i in range(1, 18)),
    "side_profile": tuple(f"side_profile_check_{i:02d}" for i in range(1, 17)),
    "full_body_neutral": tuple(f"full_body_check_{i:02d}" for i in range(1, 23)),
}
CROSS_VIEW_CHECKS = tuple(f"cross_view_check_{i:02d}" for i in range(1, 26))
ALL_CHECKS = tuple(name for role in ATOMIC_VIEW_ORDER for name in VIEW_CHECKS[role]) + CROSS_VIEW_CHECKS
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")


class PackageApprovalBlocked(RuntimeError):
    pass


class PackageApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: int = Field(1, frozen=True)
    package_id: str
    source_kind: str = "manual_import"
    character_id: str
    character_fingerprint: str
    canonical_spec_version: int
    generation_spec_version: int
    prompt_sha256: str
    draft_manifest: str = "package_manifest.json"
    draft_manifest_sha256: str
    package_review_template: str = "package_review_template.json"
    package_review_template_sha256: str
    front_anchor_session_id: str
    front_anchor_selection_record: str = "front_anchor_selection.json"
    front_anchor_selection_sha256: str
    atomic_views: tuple[dict[str, Any], ...]
    master_sheet: dict[str, Any]
    per_view_checklist: dict[str, dict[str, bool]]
    cross_view_checklist: dict[str, bool]
    master_sheet_viewed: bool
    atomic_views_viewed: bool
    anatomy_qc_passed: bool
    style_qc_passed: bool
    cross_view_identity_qc_passed: bool
    human_approved: bool
    automatic_selection: bool
    approver_role: str
    review_notes: str
    package_id_confirmed: bool
    fingerprint_prefix_confirmed: bool
    master_sha256_prefix_confirmed: bool
    no_automatic_approval_confirmed: bool
    package_only_scope_confirmed: bool
    approval_timestamp: str
    immutable_package_approval_sha256: str
    resulting_package_state: str = "package_approved"
    reference_package_use_allowed: bool = True
    provider_execution_authorized: bool = False
    scene_generation_authorized: bool = False

    @field_validator("character_fingerprint", "prompt_sha256", "draft_manifest_sha256",
                     "package_review_template_sha256", "front_anchor_selection_sha256",
                     "immutable_package_approval_sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        value = value.lower()
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("expected SHA256")
        return value

    @model_validator(mode="after")
    def valid(self) -> "PackageApproval":
        if tuple(item["asset_role"] for item in self.atomic_views) != ATOMIC_VIEW_ORDER:
            raise ValueError("atomic views are missing or out of order")
        if set(self.per_view_checklist) != set(ATOMIC_VIEW_ORDER) or set(self.cross_view_checklist) != set(CROSS_VIEW_CHECKS):
            raise ValueError("complete approval checklist is required")
        if not all(all(values.values()) for values in self.per_view_checklist.values()) or not all(self.cross_view_checklist.values()):
            raise ValueError("every approval checklist item must pass")
        if not all((self.master_sheet_viewed, self.atomic_views_viewed, self.anatomy_qc_passed,
                    self.style_qc_passed, self.cross_view_identity_qc_passed, self.human_approved,
                    self.package_id_confirmed, self.fingerprint_prefix_confirmed,
                    self.master_sha256_prefix_confirmed, self.no_automatic_approval_confirmed,
                    self.package_only_scope_confirmed, self.reference_package_use_allowed)):
            raise ValueError("complete explicit package approval is required")
        if self.automatic_selection or self.provider_execution_authorized or self.scene_generation_authorized:
            raise ValueError("automatic or provider authorization is forbidden")
        if not self.approver_role.strip() or not self.review_notes.strip():
            raise ValueError("approver role and substantive notes are required")
        return self


def _canonical(payload: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in payload.items() if key != "immutable_package_approval_sha256"}
    return json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _selection_data(root: Path, manifest: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    session = (root / Path("out/character_reference_bootstrap") / manifest["front_anchor_session_id"]).resolve()
    path = session / "front_anchor_selection.json"
    if not path.is_file():
        raise PackageApprovalBlocked("front-anchor selection is missing")
    return path, json.loads(path.read_text(encoding="utf-8"))


def validate_package(*, repository_root: Path, package_id: str, allow_existing_approval: bool = False) -> dict[str, Any]:
    root = repository_root.resolve(strict=True)
    if not _SAFE_ID.fullmatch(package_id):
        raise PackageApprovalBlocked("unsafe package ID")
    try:
        result = verify_draft(repository_root=root, package_id=package_id)
    except ManualPackageBlocked as exc:
        raise PackageApprovalBlocked(str(exc)) from exc
    package = _package_path(root, package_id)
    manifest = json.loads((package / "package_manifest.json").read_text(encoding="utf-8"))
    if (package / APPROVAL_FILENAME).exists() and not allow_existing_approval:
        raise PackageApprovalBlocked("package approval already exists")
    review = json.loads((package / "package_review_template.json").read_text(encoding="utf-8"))
    if review.get("human_approved") is not False or review.get("final_package_approval") is not False:
        raise PackageApprovalBlocked("package review template is not unapproved")
    selection_path, selection = _selection_data(root, manifest)
    return {"root": root, "package": package, "manifest": manifest, "review": review,
            "selection_path": selection_path, "selection": selection, "draft": result}


def _build_approval(data: dict[str, Any], *, per_view: dict[str, dict[str, bool]], cross_view: dict[str, bool],
                    master_viewed: bool, atomics_viewed: bool, role: str, notes: str,
                    confirmations: dict[str, bool], now: datetime) -> PackageApproval:
    manifest = data["manifest"]
    package: Path = data["package"]
    atomic = tuple(manifest["atomic_views"])
    payload: dict[str, Any] = {
        "schema_version": 1, "package_id": manifest["package_id"], "source_kind": manifest["source_kind"],
        "character_id": manifest["character_id"], "character_fingerprint": manifest["character_fingerprint"],
        "canonical_spec_version": manifest["canonical_spec_version"], "generation_spec_version": manifest["generation_spec_version"],
        "prompt_sha256": manifest["prompt_sha256"], "draft_manifest": "package_manifest.json",
        "draft_manifest_sha256": _sha(package / "package_manifest.json"),
        "package_review_template": "package_review_template.json",
        "package_review_template_sha256": _sha(package / "package_review_template.json"),
        "front_anchor_session_id": manifest["front_anchor_session_id"], "front_anchor_selection_record": "front_anchor_selection.json",
        "front_anchor_selection_sha256": _sha(data["selection_path"]), "atomic_views": atomic,
        "master_sheet": manifest["master_sheet"], "per_view_checklist": per_view, "cross_view_checklist": cross_view,
        "master_sheet_viewed": master_viewed, "atomic_views_viewed": atomics_viewed,
        "anatomy_qc_passed": True, "style_qc_passed": True, "cross_view_identity_qc_passed": True,
        "human_approved": True, "automatic_selection": False, "approver_role": role, "review_notes": notes,
        **confirmations, "approval_timestamp": now.astimezone(timezone.utc).isoformat(),
        "resulting_package_state": "package_approved", "reference_package_use_allowed": True,
        "provider_execution_authorized": False, "scene_generation_authorized": False,
    }
    payload["immutable_package_approval_sha256"] = hashlib.sha256(_canonical(payload)).hexdigest()
    return PackageApproval.model_validate(payload)


def create_approval(*, repository_root: Path, package_id: str, per_view_checklist: dict[str, dict[str, bool]],
                    cross_view_checklist: dict[str, bool], master_sheet_viewed: bool, atomic_views_viewed: bool,
                    approver_role: str, review_notes: str, confirmations: dict[str, bool],
                    now: datetime | None = None) -> Path:
    data = validate_package(repository_root=repository_root, package_id=package_id)
    if not master_sheet_viewed or not atomic_views_viewed:
        raise PackageApprovalBlocked("all package images must be viewed")
    record = _build_approval(data, per_view=per_view_checklist, cross_view=cross_view_checklist,
                             master_viewed=master_sheet_viewed, atomics_viewed=atomic_views_viewed,
                             role=approver_role, notes=review_notes,
                             confirmations=confirmations, now=now or datetime.now(timezone.utc))
    path = data["package"] / APPROVAL_FILENAME
    atomic_write_json(path, record.model_dump(mode="json"), ensure_ascii=False)
    return path


def verify_approval(*, repository_root: Path, package_id: str) -> dict[str, Any]:
    root = repository_root.resolve(strict=True)
    package = _package_path(root, package_id)
    approval_path = package / APPROVAL_FILENAME
    if not approval_path.is_file():
        raise PackageApprovalBlocked("package approval is missing")
    data = validate_package(repository_root=root, package_id=package_id, allow_existing_approval=True)
    payload = json.loads(approval_path.read_text(encoding="utf-8"))
    if payload.get("immutable_package_approval_sha256") != hashlib.sha256(_canonical(payload)).hexdigest():
        raise PackageApprovalBlocked("package approval hash mismatch")
    try:
        record = PackageApproval.model_validate(payload)
    except Exception as exc:
        raise PackageApprovalBlocked("package approval is invalid") from exc
    manifest = data["manifest"]
    if record.package_id != package_id or record.draft_manifest_sha256 != _sha(package / record.draft_manifest):
        raise PackageApprovalBlocked("approval does not bind the current draft")
    if record.package_review_template_sha256 != _sha(package / record.package_review_template):
        raise PackageApprovalBlocked("approval does not bind the current review template")
    if record.front_anchor_selection_sha256 != _sha(data["selection_path"]):
        raise PackageApprovalBlocked("approval does not bind the front anchor")
    for row in manifest["atomic_views"]:
        if _sha(package / row["path"]) != row["sha256"]:
            raise PackageApprovalBlocked("atomic bytes changed")
    master = package / manifest["master_sheet"]["path"]
    if _sha(master) != manifest["master_sheet"]["sha256"]:
        raise PackageApprovalBlocked("master sheet changed")
    return {"status": "valid", "package_id": package_id, "package_state": record.resulting_package_state,
            "reference_package_use_allowed": True, "provider_execution_authorized": False,
            "scene_generation_authorized": False, "external_calls": 0}


def validate_only() -> dict[str, Any]:
    return {"status": "valid_no_execution", "provider_clients_constructed": 0,
            "credential_reads": 0, "external_calls": 0, "approval_created": 0,
            "expected_interactive_command": "review_character_reference_package.py --mode interactive-review --package-id <package-id>"}


__all__ = ["ALL_CHECKS", "CROSS_VIEW_CHECKS", "PackageApprovalBlocked", "VIEW_CHECKS",
           "create_approval", "validate_only", "validate_package", "verify_approval"]
