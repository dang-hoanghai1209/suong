from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path

import pytest
from PIL import Image

from tella.media.bfl_front_anchor_orchestration import CHARACTER_FINGERPRINT, RepositoryState
from tella.media.manual_front_import import SEMANTIC_CHECKS, import_candidates
from tella.media.manual_front_review import (
    FrontReviewBlocked, create_selection, validate_only, validate_session, verify_selection,
)


CONFIG = Path("configs/character_references/practical_young_adult_male_teal_v1_bootstrap_v1.json")
_cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
PROMPT = _cfg["request_specs"][0]["prompt"]
PROMPT_SHA = hashlib.sha256(PROMPT.encode()).hexdigest()


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    calls = []
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("network")))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("network")))
    yield
    assert calls == []


def _state(_root):
    return RepositoryState(branch="feature/reference-conditioned-image-provider", tracked_clean=True, staged_zero=True)


def _png(path: Path, color: str):
    Image.new("RGB", (768, 1024), color).save(path, format="PNG")


def _session(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    src = tuple(tmp_path / f"s{i}.png" for i in range(3))
    for path, color in zip(src, ("red", "green", "blue"), strict=True):
        _png(path, color)
    out = import_candidates(
        repository_root=tmp_path, session_id="review_session_01", sources=src,
        character_id="practical_young_adult_male_teal_v1", character_fingerprint=CHARACTER_FINGERPRINT,
        canonical_spec_version=1, generation_spec_version=1, prompt=PROMPT,
        prompt_sha256=PROMPT_SHA, state_reader=_state,
    )
    import tella.media.manual_front_review as review
    monkeypatch.setattr(review, "repository_state", _state)
    return out


def test_validate_only_is_zero_execution():
    result = validate_only()
    assert result["provider_clients_constructed"] == 0
    assert result["credential_reads"] == result["external_calls"] == 0


def test_integrity_validation_and_explicit_selection_lock(tmp_path, monkeypatch):
    _session(tmp_path, monkeypatch)
    data = validate_session(repository_root=tmp_path, session_id="review_session_01")
    assert data["manifest"].session_state == "awaiting_front_selection"
    checklist = {name: True for name in SEMANTIC_CHECKS}
    digest = data["rows"]["candidate_01"].copied_sha256
    selected = create_selection(
        repository_root=tmp_path, session_id="review_session_01", candidate_id="candidate_01",
        checklist=checklist, contact_sheet_viewed=True, approver_role="project_owner",
        review_notes="Reviewed all required semantic properties.", selected_prefix=digest[:12],
    )
    assert selected.name == "front_anchor_selection.json"
    result = verify_selection(repository_root=tmp_path, session_id="review_session_01")
    assert result["state"] == "front_anchor_locked"
    assert result["final_package_approved"] is False
    assert json.loads(selected.read_text())["automatic_selection"] is False
    with pytest.raises(FrontReviewBlocked, match="immutable selection"):
        create_selection(
            repository_root=tmp_path, session_id="review_session_01", candidate_id="candidate_02",
            checklist=checklist, contact_sheet_viewed=True, approver_role="project_owner",
            review_notes="replacement", selected_prefix="a",
        )


def test_changed_candidate_and_manifest_fail_verification(tmp_path, monkeypatch):
    out = _session(tmp_path, monkeypatch)
    data = validate_session(repository_root=tmp_path, session_id="review_session_01")
    checklist = {name: True for name in SEMANTIC_CHECKS}
    digest = data["rows"]["candidate_02"].copied_sha256
    create_selection(repository_root=tmp_path, session_id="review_session_01", candidate_id="candidate_02",
                     checklist=checklist, contact_sheet_viewed=True, approver_role="project_owner",
                     review_notes="candidate reviewed", selected_prefix=digest[:16])
    candidate = out / "candidate_02.png"
    candidate.write_bytes(candidate.read_bytes() + b"tamper")
    with pytest.raises(FrontReviewBlocked):
        verify_selection(repository_root=tmp_path, session_id="review_session_01")


def test_empty_or_failed_checklist_never_creates_selection(tmp_path, monkeypatch):
    _session(tmp_path, monkeypatch)
    with pytest.raises(FrontReviewBlocked):
        create_selection(repository_root=tmp_path, session_id="review_session_01", candidate_id="candidate_01",
                         checklist={name: False for name in SEMANTIC_CHECKS}, contact_sheet_viewed=True,
                         approver_role="project_owner", review_notes="no", selected_prefix="a")
    assert not (tmp_path / "out/character_reference_bootstrap/review_session_01/front_anchor_selection.json").exists()
