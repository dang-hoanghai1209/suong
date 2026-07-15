"""Offline human review and immutable locking for a manual front import."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tella.atomic_write import atomic_write_json
from tella.media.bfl_front_anchor_orchestration import (
    CHARACTER_FINGERPRINT, CHARACTER_ID, OUTPUT_PREFIX, REQUIRED_BRANCH,
    repository_state,
)
from tella.media.manual_front_import import (
    EXPECTED_SIZE, ManualImportManifest, ManualImportBlocked, SEMANTIC_CHECKS,
    validate_source,
)
from tella.media.character_reference_bootstrap import BootstrapState


SELECTION_FILENAME = "front_anchor_selection.json"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")


class FrontReviewBlocked(RuntimeError):
    """Raised when a manual review session is not integrity-safe."""


class FrontAnchorSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: int = Field(1, frozen=True)
    session_id: str
    source_kind: str = "manual_import"
    character_id: str
    character_fingerprint: str
    canonical_spec_version: int
    generation_spec_version: int
    prompt_sha256: str
    imported_manifest: str = "candidates_manifest.json"
    imported_manifest_sha256: str
    review_template: str = "review_template.json"
    review_template_sha256: str
    contact_sheet: str = "contact_sheet.png"
    contact_sheet_sha256: str
    selected_candidate_id: str
    selected_candidate_filename: str
    selected_candidate_sha256: str
    selected_candidate_mime: str = "image/png"
    selected_candidate_dimensions: tuple[int, int] = EXPECTED_SIZE
    duplicate_warning: bool
    semantic_checklist: dict[str, bool]
    contact_sheet_viewed: bool
    human_approved: bool
    automatic_selection: bool
    approver_role: str
    review_notes: str
    approval_timestamp: str
    stage_b_allowed: bool = False
    immutable_selection_sha256: str
    resulting_bootstrap_state: str = BootstrapState.front_anchor_locked.value

    @field_validator("character_fingerprint", "prompt_sha256", "imported_manifest_sha256",
                     "review_template_sha256", "contact_sheet_sha256", "selected_candidate_sha256",
                     "immutable_selection_sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        value = value.lower()
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("expected SHA256")
        return value

    @model_validator(mode="after")
    def valid_approval(self) -> "FrontAnchorSelection":
        if self.source_kind != "manual_import" or self.character_id != CHARACTER_ID:
            raise ValueError("manual front selection identity mismatch")
        if self.character_fingerprint != CHARACTER_FINGERPRINT:
            raise ValueError("manual front selection fingerprint mismatch")
        if self.selected_candidate_id not in {"candidate_01", "candidate_02", "candidate_03"}:
            raise ValueError("unknown selected candidate")
        if self.selected_candidate_filename != f"{self.selected_candidate_id}.png":
            raise ValueError("selected filename does not match candidate")
        if set(self.semantic_checklist) != set(SEMANTIC_CHECKS) or not all(self.semantic_checklist.values()):
            raise ValueError("complete semantic checklist is required")
        if not (self.contact_sheet_viewed and self.human_approved and not self.automatic_selection):
            raise ValueError("explicit human approval is required")
        if not self.approver_role.strip() or not self.review_notes.strip():
            raise ValueError("approver role and review notes are required")
        if self.resulting_bootstrap_state != BootstrapState.front_anchor_locked.value:
            raise ValueError("selection must lock only the front anchor")
        safe = json.dumps(self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True).lower()
        if any(marker in safe for marker in ("://", "api_key", "authorization", "bearer ", "secret")):
            raise ValueError("selection contains forbidden material")
        return self


def _digest_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_selection_payload(payload: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in payload.items() if key != "immutable_selection_sha256"}
    return json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _selection_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_selection_payload(payload)).hexdigest()


def _session_path(root: Path, session_id: str) -> Path:
    if not _SAFE_ID.fullmatch(session_id):
        raise FrontReviewBlocked("unsafe session ID")
    approved = (root / OUTPUT_PREFIX).resolve()
    session = (root / OUTPUT_PREFIX / session_id).resolve()
    if not session.is_relative_to(approved) or any(
        parent.exists() and parent.is_symlink() for parent in (session, *session.parents)
        if parent != approved.parent
    ):
        raise FrontReviewBlocked("session path escapes approved output root")
    return session


def validate_session(*, repository_root: Path, session_id: str, allow_existing_selection: bool = False) -> dict[str, Any]:
    root = repository_root.resolve(strict=True)
    if not (root / ".git").exists():
        raise FrontReviewBlocked("repository root is invalid")
    state = repository_state(root)
    if state.branch != REQUIRED_BRANCH or not state.tracked_clean or not state.staged_zero:
        raise FrontReviewBlocked("source-state policy failed")
    session = _session_path(root, session_id)
    if not session.is_dir() or session.is_symlink():
        raise FrontReviewBlocked("manual import session is missing")
    required = ("candidates_manifest.json", "review_template.json", "contact_sheet.png")
    if any(not (session / name).is_file() for name in required):
        raise FrontReviewBlocked("manual review artifacts are incomplete")
    if (session / SELECTION_FILENAME).exists() and not allow_existing_selection:
        raise FrontReviewBlocked("immutable selection already exists")
    try:
        manifest = ManualImportManifest.model_validate_json((session / required[0]).read_bytes())
        review = json.loads((session / required[1]).read_text(encoding="utf-8"))
    except Exception as exc:
        raise FrontReviewBlocked("manual import metadata is invalid") from exc
    if manifest.session_state != "awaiting_front_selection":
        raise FrontReviewBlocked("session is not awaiting front selection")
    if review.get("session_id") != session_id or tuple(
        item.get("candidate_id") for item in review.get("candidates", ())
    ) != ("candidate_01", "candidate_02", "candidate_03"):
        raise FrontReviewBlocked("review template candidate identity mismatch")
    if review.get("selected_candidate_id") is not None or review.get("human_approved") is not False:
        raise FrontReviewBlocked("review template is already selected")
    rows = {}
    for row in manifest.candidates:
        path = session / row.image_filename
        if path.is_symlink() or not path.is_file():
            raise FrontReviewBlocked("candidate is missing or symlinked")
        try:
            content, digest = validate_source(path)
        except ManualImportBlocked as exc:
            raise FrontReviewBlocked(str(exc)) from exc
        if digest != row.source_sha256 or digest != row.copied_sha256 or len(content) != row.byte_size:
            raise FrontReviewBlocked("candidate bytes or manifest hash changed")
        rows[row.candidate_id] = row
    if len(rows) != 3:
        raise FrontReviewBlocked("exactly three candidates are required")
    try:
        with Image.open(session / "contact_sheet.png") as image:
            if image.format != "PNG":
                raise FrontReviewBlocked("contact sheet is not PNG")
    except FrontReviewBlocked:
        raise
    except Exception as exc:
        raise FrontReviewBlocked("contact sheet is invalid") from exc
    return {"root": root, "session": session, "manifest": manifest, "review": review, "rows": rows}


def _build_selection(data: dict[str, Any], *, candidate_id: str, checklist: dict[str, bool],
                     contact_sheet_viewed: bool, approver_role: str, review_notes: str,
                     selected_prefix: str, now: datetime) -> FrontAnchorSelection:
    session: Path = data["session"]
    manifest: ManualImportManifest = data["manifest"]
    row = data["rows"].get(candidate_id)
    if row is None:
        raise FrontReviewBlocked("unknown candidate")
    if not all(checklist.values()):
        raise FrontReviewBlocked("semantic review failed")
    if not selected_prefix or not row.copied_sha256.startswith(selected_prefix.lower()):
        raise FrontReviewBlocked("selected SHA256 confirmation does not match")
    payload: dict[str, Any] = {
        "schema_version": 1, "session_id": manifest.session_id, "source_kind": "manual_import",
        "character_id": manifest.character_id, "character_fingerprint": manifest.character_fingerprint,
        "canonical_spec_version": manifest.canonical_spec_version,
        "generation_spec_version": manifest.generation_spec_version, "prompt_sha256": manifest.prompt_sha256,
        "imported_manifest": "candidates_manifest.json", "imported_manifest_sha256": _digest_bytes(session / "candidates_manifest.json"),
        "review_template": "review_template.json", "review_template_sha256": _digest_bytes(session / "review_template.json"),
        "contact_sheet": "contact_sheet.png", "contact_sheet_sha256": _digest_bytes(session / "contact_sheet.png"),
        "selected_candidate_id": candidate_id, "selected_candidate_filename": row.image_filename,
        "selected_candidate_sha256": row.copied_sha256, "selected_candidate_mime": row.mime,
        "selected_candidate_dimensions": list(row.dimensions), "duplicate_warning": row.duplicate_group is not None,
        "semantic_checklist": checklist, "contact_sheet_viewed": contact_sheet_viewed,
        "human_approved": True, "automatic_selection": False, "approver_role": approver_role,
        "review_notes": review_notes, "approval_timestamp": now.astimezone(timezone.utc).isoformat(),
        "stage_b_allowed": False, "resulting_bootstrap_state": BootstrapState.front_anchor_locked.value,
    }
    payload["immutable_selection_sha256"] = _selection_hash(payload)
    return FrontAnchorSelection.model_validate(payload)


def verify_selection(*, repository_root: Path, session_id: str) -> dict[str, Any]:
    root = repository_root.resolve(strict=True)
    session = _session_path(root, session_id)
    selection_path = session / SELECTION_FILENAME
    if not selection_path.is_file():
        raise FrontReviewBlocked("immutable selection record is missing")
    data = validate_session(repository_root=root, session_id=session_id, allow_existing_selection=True)
    try:
        payload = json.loads(selection_path.read_text(encoding="utf-8"))
        record = FrontAnchorSelection.model_validate(payload)
    except Exception as exc:
        raise FrontReviewBlocked("selection record is invalid") from exc
    if payload.get("immutable_selection_sha256") != _selection_hash(payload):
        raise FrontReviewBlocked("immutable selection SHA256 mismatch")
    if record.imported_manifest_sha256 != _digest_bytes(session / record.imported_manifest):
        raise FrontReviewBlocked("imported manifest changed")
    if record.review_template_sha256 != _digest_bytes(session / record.review_template):
        raise FrontReviewBlocked("review template changed")
    if record.contact_sheet_sha256 != _digest_bytes(session / record.contact_sheet):
        raise FrontReviewBlocked("contact sheet changed")
    row = data["rows"].get(record.selected_candidate_id)
    if row is None or row.copied_sha256 != record.selected_candidate_sha256:
        raise FrontReviewBlocked("selected candidate bytes do not match")
    return {"status": "valid", "session_id": session_id, "selected_candidate_id": record.selected_candidate_id,
            "selected_candidate_sha256": record.selected_candidate_sha256,
            "state": record.resulting_bootstrap_state, "final_package_approved": False,
            "provider_calls": 0, "external_calls": 0}


def create_selection(*, repository_root: Path, session_id: str, candidate_id: str,
                     checklist: dict[str, bool], contact_sheet_viewed: bool,
                     approver_role: str, review_notes: str, selected_prefix: str,
                     now: datetime | None = None) -> Path:
    data = validate_session(repository_root=repository_root, session_id=session_id)
    if not contact_sheet_viewed:
        raise FrontReviewBlocked("contact sheet must be confirmed as viewed")
    record = _build_selection(data, candidate_id=candidate_id, checklist=checklist,
                              contact_sheet_viewed=contact_sheet_viewed, approver_role=approver_role,
                              review_notes=review_notes, selected_prefix=selected_prefix,
                              now=now or datetime.now(timezone.utc))
    path = data["session"] / SELECTION_FILENAME
    if path.exists():
        raise FrontReviewBlocked("immutable selection already exists")
    atomic_write_json(path, record.model_dump(mode="json"), ensure_ascii=False)
    return path


def validate_only() -> dict[str, Any]:
    return {"status": "valid_no_execution", "provider_clients_constructed": 0,
            "credential_reads": 0, "external_calls": 0, "selection_created": 0,
            "expected_interactive_command": "review_front_anchor.py --mode interactive-review --session-id <session-id>"}


__all__ = ["FrontAnchorSelection", "FrontReviewBlocked", "create_selection", "validate_only",
           "validate_session", "verify_selection"]
