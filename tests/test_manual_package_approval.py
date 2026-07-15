from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path

import pytest
from PIL import Image

from tella.media.bfl_front_anchor_orchestration import CHARACTER_FINGERPRINT, RepositoryState
from tella.media.character_reference_package import ATOMIC_VIEW_ORDER
from tella.media.manual_four_view_package import import_views
from tella.media.manual_front_import import SEMANTIC_CHECKS, import_candidates
from tella.media.manual_front_review import create_selection
from tella.media.manual_package_approval import (
    CROSS_VIEW_CHECKS, PackageApprovalBlocked, VIEW_CHECKS,
    create_approval, validate_only, verify_approval,
)


CONFIG = Path("configs/character_references/practical_young_adult_male_teal_v1_bootstrap_v1.json")
_cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
PROMPT = _cfg["request_specs"][0]["prompt"]
PROMPT_SHA = hashlib.sha256(PROMPT.encode()).hexdigest()


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    calls = []
    def blocked(*args, **kwargs):
        calls.append(args)
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    yield
    assert calls == []


def _state(_root):
    return RepositoryState(branch="feature/reference-conditioned-image-provider", tracked_clean=True, staged_zero=True)


def _png(path: Path, color: str):
    Image.new("RGB", (768, 1024), color).save(path, format="PNG")


def _draft(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    front_sources = tuple(tmp_path / f"f{i}.png" for i in range(3))
    for path, color in zip(front_sources, ("red", "green", "blue"), strict=True):
        _png(path, color)
    session = import_candidates(repository_root=tmp_path, session_id="front_approved_01", sources=front_sources,
                                character_id="practical_young_adult_male_teal_v1", character_fingerprint=CHARACTER_FINGERPRINT,
                                canonical_spec_version=1, generation_spec_version=1, prompt=PROMPT, prompt_sha256=PROMPT_SHA,
                                state_reader=_state)
    import tella.media.manual_front_review as front_review
    monkeypatch.setattr(front_review, "repository_state", _state)
    manifest = json.loads((session / "candidates_manifest.json").read_text())
    digest = manifest["candidates"][0]["copied_sha256"]
    create_selection(repository_root=tmp_path, session_id="front_approved_01", candidate_id="candidate_01",
                     checklist={key: True for key in SEMANTIC_CHECKS}, contact_sheet_viewed=True,
                     approver_role="project_owner", review_notes="front approved", selected_prefix=digest[:16])
    remaining = [tmp_path / f"{name}.png" for name in ("three", "side", "body")]
    for path, color in zip(remaining, ("yellow", "purple", "orange"), strict=True):
        _png(path, color)
    return import_views(repository_root=tmp_path, front_anchor_session_id="front_approved_01", package_id="package_approved_01",
                        three_quarter=remaining[0], side_profile=remaining[1], full_body_neutral=remaining[2])


def _approval_args():
    return {
        "per_view_checklist": {role: {key: True for key in VIEW_CHECKS[role]} for role in ATOMIC_VIEW_ORDER},
        "cross_view_checklist": {key: True for key in CROSS_VIEW_CHECKS},
        "master_sheet_viewed": True, "atomic_views_viewed": True,
        "approver_role": "project_owner", "review_notes": "All four views reviewed at usable resolution.",
        "confirmations": {"package_id_confirmed": True, "fingerprint_prefix_confirmed": True,
                           "master_sha256_prefix_confirmed": True, "no_automatic_approval_confirmed": True,
                           "package_only_scope_confirmed": True},
    }


def test_validate_only_is_zero_execution():
    result = validate_only()
    assert result["provider_clients_constructed"] == result["credential_reads"] == result["external_calls"] == 0


def test_successful_approval_is_immutable_and_provider_blocked(tmp_path, monkeypatch):
    _draft(tmp_path, monkeypatch)
    path = create_approval(repository_root=tmp_path, package_id="package_approved_01", **_approval_args())
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["human_approved"] is True
    assert payload["reference_package_use_allowed"] is True
    assert payload["provider_execution_authorized"] is False
    assert verify_approval(repository_root=tmp_path, package_id="package_approved_01")["status"] == "valid"
    with pytest.raises(PackageApprovalBlocked, match="already exists"):
        create_approval(repository_root=tmp_path, package_id="package_approved_01", **_approval_args())


def test_atomic_tamper_invalidates_approval(tmp_path, monkeypatch):
    package = _draft(tmp_path, monkeypatch)
    create_approval(repository_root=tmp_path, package_id="package_approved_01", **_approval_args())
    (package / "side_profile.png").write_bytes((package / "side_profile.png").read_bytes() + b"tamper")
    with pytest.raises(PackageApprovalBlocked):
        verify_approval(repository_root=tmp_path, package_id="package_approved_01")


def test_missing_checklist_or_confirmation_cannot_approve(tmp_path, monkeypatch):
    _draft(tmp_path, monkeypatch)
    args = _approval_args()
    args["cross_view_checklist"] = {key: True for key in CROSS_VIEW_CHECKS[:-1]}
    with pytest.raises(Exception):
        create_approval(repository_root=tmp_path, package_id="package_approved_01", **args)
