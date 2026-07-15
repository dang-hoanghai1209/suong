"""Provider-free import and verification of an unapproved four-view package draft."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from tella.atomic_write import atomic_write_json, atomic_write_bytes
from tella.media.character_reference_package import (
    ATOMIC_VIEW_ORDER, MASTER_SHEET_ASSEMBLY,
    MASTER_SHEET_DIMENSIONS, build_master_sheet,
)
from tella.media.manual_front_import import ManualImportBlocked, validate_source
from tella.media.manual_front_review import FrontReviewBlocked, verify_selection


PACKAGE_ROOT = Path("out") / "character_reference_packages"
PACKAGE_SCHEMA_VERSION = 1
PACKAGE_STATE = "draft_awaiting_human_qc"


class ManualPackageBlocked(RuntimeError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_id(value: str) -> None:
    if not value or len(value) > 120 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-" for c in value):
        raise ManualPackageBlocked("unsafe package ID")


def _package_path(root: Path, package_id: str) -> Path:
    _safe_id(package_id)
    approved = (root / PACKAGE_ROOT).resolve()
    output = (root / PACKAGE_ROOT / package_id).resolve()
    if not output.is_relative_to(approved):
        raise ManualPackageBlocked("package path escapes approved root")
    for parent in (output, *output.parents):
        if parent != approved.parent and parent.exists() and parent.is_symlink():
            raise ManualPackageBlocked("package path contains symlink")
    return output


def _check_png(path: Path) -> tuple[bytes, str]:
    if path.is_symlink() or not path.is_file():
        raise ManualPackageBlocked("view source must be a regular non-symlink file")
    try:
        content, digest = validate_source(path)
    except ManualImportBlocked as exc:
        raise ManualPackageBlocked(str(exc)) from exc
    return content, digest


def _front_anchor(root: Path, session_id: str) -> tuple[Path, dict[str, Any]]:
    try:
        result = verify_selection(repository_root=root, session_id=session_id)
    except FrontReviewBlocked as exc:
        raise ManualPackageBlocked(str(exc)) from exc
    session = (root / Path("out/character_reference_bootstrap") / session_id).resolve()
    record_path = session / "front_anchor_selection.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if result.get("state") != "front_anchor_locked" or record.get("human_approved") is not True:
        raise ManualPackageBlocked("front anchor is not locked and human-approved")
    candidate = session / record["selected_candidate_filename"]
    content, digest = _check_png(candidate)
    if digest != record["selected_candidate_sha256"]:
        raise ManualPackageBlocked("locked front anchor bytes changed")
    return candidate, {"record": record, "session": session, "content": content, "sha256": digest}


def _review_template(manifest: dict[str, Any]) -> dict[str, Any]:
    checklist = {
        "same_face": "pending_human_review", "same_age": "pending_human_review",
        "same_eyes_and_facial_spacing": "pending_human_review", "same_nose_and_jaw": "pending_human_review",
        "same_hairstyle": "pending_human_review", "same_skin_tone": "pending_human_review",
        "same_body_build": "pending_human_review", "same_outfit": "pending_human_review",
        "same_footwear": "pending_human_review", "same_illustration_style": "pending_human_review",
        "correct_anatomy": "pending_human_review", "no_duplicated_or_missing_limbs": "pending_human_review",
        "no_extra_person": "pending_human_review", "no_props": "pending_human_review",
        "no_unauthorized_accessories_or_logos": "pending_human_review", "no_text": "pending_human_review",
        "no_watermark": "pending_human_review", "no_coral_clothing": "pending_human_review",
        "master_matches_atomic_views": "pending_human_review",
    }
    return {
        "schema_version": 1, "package_id": manifest["package_id"],
        "human_approved": False, "selected_or_accepted_views": [],
        "approval_role": "", "approval_timestamp": None, "notes": "",
        "final_package_approval": False, "production_use_allowed": False,
        "front_anchor_provenance": {"session_id": manifest["front_anchor_session_id"],
                                     "selection_sha256": manifest["front_anchor_selection_sha256"]},
        "per_view_review": {role: {"mechanical_validation": "passed", "semantic_review": "pending_human_review"}
                            for role in ATOMIC_VIEW_ORDER},
        "cross_view_review": checklist,
    }


def import_views(*, repository_root: Path, front_anchor_session_id: str, package_id: str,
                 three_quarter: Path, side_profile: Path, full_body_neutral: Path,
                 now: datetime | None = None) -> Path:
    root = repository_root.resolve(strict=True)
    if not (root / ".git").exists():
        raise ManualPackageBlocked("repository root is invalid")
    front, anchor = _front_anchor(root, front_anchor_session_id)
    output = _package_path(root, package_id)
    if output.exists():
        raise ManualPackageBlocked("package already exists or is foreign-owned")
    sources = (("front_portrait", front), ("three_quarter_portrait", three_quarter),
               ("side_profile", side_profile), ("full_body_neutral", full_body_neutral))
    validated: list[tuple[str, Path, bytes, str]] = []
    for role, path in sources:
        content, digest = _check_png(path)
        validated.append((role, path, content, digest))
    approved = (root / PACKAGE_ROOT).resolve()
    approved.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{package_id}.", suffix=".tmp", dir=approved))
    try:
        atomic_rows: list[dict[str, Any]] = []
        for role, _, content, digest in validated:
            destination = temporary / f"{role}.png"
            atomic_write_bytes(destination, content)
            if _sha(destination) != digest:
                raise ManualPackageBlocked(f"copied {role} hash mismatch")
            atomic_rows.append({"asset_role": role, "path": f"{role}.png", "mime_type": "image/png",
                                "width": 768, "height": 1024, "byte_size": len(content), "sha256": digest})
        master_result = build_master_sheet(tuple((role, temporary / f"{role}.png") for role, *_ in validated), temporary / "master_sheet.png")
        manifest = {
            "schema_version": PACKAGE_SCHEMA_VERSION, "package_id": package_id,
            "package_state": PACKAGE_STATE, "source_kind": "manual_import",
            "character_id": anchor["record"]["character_id"],
            "character_fingerprint": anchor["record"]["character_fingerprint"],
            "canonical_spec_version": anchor["record"]["canonical_spec_version"],
            "generation_spec_version": anchor["record"]["generation_spec_version"],
            "prompt_sha256": anchor["record"]["prompt_sha256"],
            "front_anchor_session_id": front_anchor_session_id,
            "front_anchor_selection_record": "front_anchor_selection.json",
            "front_anchor_selection_sha256": _sha(anchor["session"] / "front_anchor_selection.json"),
            "front_anchor_candidate_id": anchor["record"]["selected_candidate_id"],
            "front_anchor_candidate_sha256": anchor["sha256"],
            "atomic_views": atomic_rows, "master_sheet": {"path": "master_sheet.png", "mime_type": "image/png",
                "width": master_result.width, "height": master_result.height, "sha256": master_result.sha256,
                "source_sha256": list(master_result.source_sha256), "assembly": MASTER_SHEET_ASSEMBLY},
            "provider_calls": 0, "external_calls": 0, "anatomy_qc": "pending_human_review",
            "style_qc": "pending_human_review", "cross_view_identity_qc": "pending_human_review",
            "human_approved": False, "automatic_selection": False, "final_package_approval": False,
            "production_use_allowed": False, "created_timestamp": (now or datetime.now(timezone.utc)).isoformat(),
        }
        atomic_write_json(temporary / "package_manifest.json", manifest, ensure_ascii=False)
        atomic_write_json(temporary / "package_review_template.json", _review_template(manifest), ensure_ascii=False)
        os.replace(temporary, output)
        return output
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def verify_draft(*, repository_root: Path, package_id: str) -> dict[str, Any]:
    root = repository_root.resolve(strict=True)
    package = _package_path(root, package_id)
    manifest_path = package / "package_manifest.json"
    review_path = package / "package_review_template.json"
    if not manifest_path.is_file() or not review_path.is_file():
        raise ManualPackageBlocked("package draft metadata is incomplete")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("package_state") != PACKAGE_STATE or manifest.get("human_approved") is not False or manifest.get("production_use_allowed") is not False:
        raise ManualPackageBlocked("package draft state is not unapproved")
    roles = tuple(row.get("asset_role") for row in manifest.get("atomic_views", ()))
    if roles != ATOMIC_VIEW_ORDER:
        raise ManualPackageBlocked("atomic view order is invalid")
    anchor = _front_anchor(root, manifest["front_anchor_session_id"])
    if manifest["front_anchor_selection_sha256"] != _sha(anchor[1]["session"] / "front_anchor_selection.json"):
        raise ManualPackageBlocked("front selection record changed")
    for row in manifest["atomic_views"]:
        content, digest = _check_png(package / row["path"])
        if digest != row["sha256"] or digest != (anchor[1]["sha256"] if row["asset_role"] == "front_portrait" else digest):
            raise ManualPackageBlocked(f"atomic {row['asset_role']} changed")
    master = package / manifest["master_sheet"]["path"]
    if _sha(master) != manifest["master_sheet"]["sha256"]:
        raise ManualPackageBlocked("master sheet changed")
    with Image.open(master) as image:
        if image.format != "PNG" or image.size != MASTER_SHEET_DIMENSIONS:
            raise ManualPackageBlocked("master sheet geometry changed")
    return {"status": "valid", "package_id": package_id, "package_state": PACKAGE_STATE,
            "provider_facing_use": "blocked", "human_approved": False,
            "provider_calls": 0, "external_calls": 0}


def validate_only() -> dict[str, Any]:
    return {"status": "valid_no_execution", "required_roles": list(ATOMIC_VIEW_ORDER[1:]),
            "provider_clients_constructed": 0, "credential_reads": 0, "external_calls": 0,
            "generated_artifacts": 0, "automatic_selection": False}


__all__ = ["ManualPackageBlocked", "import_views", "validate_only", "verify_draft"]
