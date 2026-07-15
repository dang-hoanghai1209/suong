from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path

import pytest
from PIL import Image

from scripts.benchmarks.import_front_candidates import validate_only
from tella.media.bfl_front_anchor_orchestration import CHARACTER_FINGERPRINT, RepositoryState
from tella.media.manual_front_import import ManualImportBlocked, import_candidates, validate_source


CONFIG = Path("configs/character_references/practical_young_adult_male_teal_v1_bootstrap_v1.json")
PROMPT = json.loads(CONFIG.read_text(encoding="utf-8"))["request_specs"][0]["prompt"]
PROMPT_SHA = hashlib.sha256(PROMPT.encode()).hexdigest()


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    calls = 0
    def blocked(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("manual import tests must remain offline")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    yield
    assert calls == 0


def _state(_root):
    return RepositoryState(
        branch="feature/reference-conditioned-image-provider",
        tracked_clean=True, staged_zero=True,
    )


def _png(path: Path, color: str, size=(768, 1024), fmt="PNG"):
    Image.new("RGB", size, color).save(path, format=fmt)


def _repo(tmp_path):
    (tmp_path / ".git").mkdir()
    return tmp_path


def _import(tmp_path, sources, session="manual_import_test_01"):
    return import_candidates(
        repository_root=_repo(tmp_path), session_id=session, sources=sources,
        character_id="practical_young_adult_male_teal_v1",
        character_fingerprint=CHARACTER_FINGERPRINT, canonical_spec_version=1,
        generation_spec_version=1, prompt=PROMPT, prompt_sha256=PROMPT_SHA,
        state_reader=_state,
    )


def test_validate_only_needs_no_candidates_credentials_or_artifacts(tmp_path):
    result = validate_only(config_path=CONFIG, repository_root=Path.cwd(), session_id="manual_validate_01")
    assert result["paid_providers_optional"] is True
    assert result["provider_clients_constructed"] == result["credential_reads"] == 0
    assert result["external_calls"] == result["generated_artifacts"] == 0


def test_valid_import_preserves_exact_bytes_and_builds_unapproved_package(tmp_path):
    source_root = tmp_path / "sources"
    source_root.mkdir()
    sources = tuple(source_root / f"source_{i}.png" for i in range(1, 4))
    for path, color in zip(sources, ("red", "green", "blue"), strict=True):
        _png(path, color)
    before = [path.read_bytes() for path in sources]
    output = _import(tmp_path, sources)
    assert [output.joinpath(f"candidate_{i:02d}.png").read_bytes() for i in range(1, 4)] == before
    manifest = json.loads((output / "candidates_manifest.json").read_text(encoding="utf-8"))
    assert manifest["provider_id"] is None
    assert manifest["provider_calls"] == manifest["external_calls"] == 0
    assert [row["candidate_id"] for row in manifest["candidates"]] == ["candidate_01", "candidate_02", "candidate_03"]
    serialized = json.dumps(manifest)
    assert str(source_root) not in serialized and "://" not in serialized
    review = json.loads((output / "review_template.json").read_text(encoding="utf-8"))
    assert review["selected_candidate_id"] is None
    assert review["human_approved"] is False
    assert review["automatic_selection"] is False
    assert review["stage_b_allowed"] is False
    assert set(review["semantic_checklist"].values()) == {"pending_human_review"}
    assert (output / "contact_sheet.png").is_file()


def test_duplicate_bytes_keep_three_ids_and_record_group(tmp_path):
    sources = tuple(tmp_path / f"source_{i}.png" for i in range(1, 4))
    _png(sources[0], "red")
    sources[1].write_bytes(sources[0].read_bytes())
    _png(sources[2], "blue")
    output = _import(tmp_path, sources, "manual_duplicate_01")
    manifest = json.loads((output / "candidates_manifest.json").read_text())
    assert manifest["candidates"][0]["duplicate_group"] == "duplicate_01"
    assert manifest["candidates"][1]["duplicate_group"] == "duplicate_01"
    assert manifest["selected_candidate_id"] is None


@pytest.mark.parametrize("kind", ["jpeg", "webp", "dimensions", "malformed", "animated"])
def test_invalid_input_fails_before_final_publication(tmp_path, kind):
    sources = tuple(tmp_path / f"source_{i}.png" for i in range(1, 4))
    for path in sources:
        _png(path, "red")
    if kind == "jpeg":
        _png(sources[1], "red", fmt="JPEG")
    elif kind == "webp":
        _png(sources[1], "red", fmt="WEBP")
    elif kind == "dimensions":
        _png(sources[1], "red", size=(767, 1024))
    elif kind == "malformed":
        sources[1].write_bytes(b"not png")
    else:
        frames = [Image.new("RGB", (768, 1024), color) for color in ("red", "blue")]
        frames[0].save(sources[1], format="PNG", save_all=True, append_images=frames[1:])
    _repo(tmp_path)
    with pytest.raises(ManualImportBlocked):
        import_candidates(
            repository_root=tmp_path, session_id="failed_import_01", sources=sources,
            character_id="practical_young_adult_male_teal_v1",
            character_fingerprint=CHARACTER_FINGERPRINT, canonical_spec_version=1,
            generation_spec_version=1, prompt=PROMPT, prompt_sha256=PROMPT_SHA,
            state_reader=_state,
        )
    assert not (tmp_path / "out" / "character_reference_bootstrap" / "failed_import_01").exists()


def test_symlink_source_and_existing_session_fail_closed(tmp_path):
    source = tmp_path / "source.png"
    _png(source, "red")
    try:
        link = tmp_path / "link.png"
        link.symlink_to(source)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(ManualImportBlocked):
        validate_source(link)
