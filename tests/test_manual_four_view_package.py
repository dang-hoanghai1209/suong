from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path

import pytest
from PIL import Image

from tella.media.bfl_front_anchor_orchestration import CHARACTER_FINGERPRINT, RepositoryState
from tella.media.manual_four_view_package import ManualPackageBlocked, import_views, validate_only, verify_draft
from tella.media.manual_front_import import SEMANTIC_CHECKS, import_candidates
from tella.media.manual_front_review import create_selection


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


def _front_session(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    source = tuple(tmp_path / f"front{i}.png" for i in range(3))
    for path, color in zip(source, ("red", "green", "blue"), strict=True):
        _png(path, color)
    session = import_candidates(repository_root=tmp_path, session_id="front_locked_01", sources=source,
                                character_id="practical_young_adult_male_teal_v1", character_fingerprint=CHARACTER_FINGERPRINT,
                                canonical_spec_version=1, generation_spec_version=1, prompt=PROMPT, prompt_sha256=PROMPT_SHA,
                                state_reader=_state)
    import tella.media.manual_front_review as review
    monkeypatch.setattr(review, "repository_state", _state)
    manifest = json.loads((session / "candidates_manifest.json").read_text())
    digest = manifest["candidates"][0]["copied_sha256"]
    create_selection(repository_root=tmp_path, session_id="front_locked_01", candidate_id="candidate_01",
                     checklist={key: True for key in SEMANTIC_CHECKS}, contact_sheet_viewed=True,
                     approver_role="project_owner", review_notes="front reviewed", selected_prefix=digest[:16])
    return session


def test_validate_only_has_no_execution():
    result = validate_only()
    assert result["required_roles"] == ["three_quarter_portrait", "side_profile", "full_body_neutral"]
    assert result["provider_clients_constructed"] == result["credential_reads"] == result["external_calls"] == 0


def test_imports_exact_bytes_builds_unapproved_draft_and_verifies(tmp_path, monkeypatch):
    _front_session(tmp_path, monkeypatch)
    sources = [tmp_path / name for name in ("three.png", "side.png", "body.png")]
    for path, color in zip(sources, ("yellow", "purple", "orange"), strict=True):
        _png(path, color)
    before = [path.read_bytes() for path in sources]
    package = import_views(repository_root=tmp_path, front_anchor_session_id="front_locked_01", package_id="package_01",
                           three_quarter=sources[0], side_profile=sources[1], full_body_neutral=sources[2])
    assert [((package / role).read_bytes()) for role in ("three_quarter_portrait.png", "side_profile.png", "full_body_neutral.png")] == before
    manifest = json.loads((package / "package_manifest.json").read_text())
    assert [row["asset_role"] for row in manifest["atomic_views"]] == ["front_portrait", "three_quarter_portrait", "side_profile", "full_body_neutral"]
    assert manifest["human_approved"] is False and manifest["production_use_allowed"] is False
    with Image.open(package / "master_sheet.png") as master:
        assert master.size == (1536, 2048)
    assert verify_draft(repository_root=tmp_path, package_id="package_01")["status"] == "valid"


def test_missing_or_changed_view_and_front_fail_closed(tmp_path, monkeypatch):
    _front_session(tmp_path, monkeypatch)
    missing = tmp_path / "missing.png"
    valid = tmp_path / "valid.png"
    _png(valid, "red")
    with pytest.raises(ManualPackageBlocked):
        import_views(repository_root=tmp_path, front_anchor_session_id="front_locked_01", package_id="bad",
                     three_quarter=missing, side_profile=valid, full_body_neutral=valid)
    assert not (tmp_path / "out/character_reference_packages/bad").exists()


def test_tampering_invalidates_draft(tmp_path, monkeypatch):
    _front_session(tmp_path, monkeypatch)
    sources = [tmp_path / name for name in ("three.png", "side.png", "body.png")]
    for path in sources:
        _png(path, "red")
    package = import_views(repository_root=tmp_path, front_anchor_session_id="front_locked_01", package_id="package_tamper",
                           three_quarter=sources[0], side_profile=sources[1], full_body_neutral=sources[2])
    (package / "side_profile.png").write_bytes((package / "side_profile.png").read_bytes() + b"x")
    with pytest.raises(ManualPackageBlocked):
        verify_draft(repository_root=tmp_path, package_id="package_tamper")
