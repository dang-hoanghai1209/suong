from __future__ import annotations

import hashlib
import json
import socket
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from tella.media.front_anchor_harness import build_front_plan
from tella.media.front_anchor_review import (
    FrontCandidateArtifact,
    FrontCandidateManifest,
    FrontVisualSignals,
    build_candidate_manifest,
    build_contact_sheet,
    make_review_template,
    record_human_selection,
    run_candidate_qc,
    to_bootstrap_selection,
    write_candidate_manifest,
    write_review_template,
)


FINGERPRINT = "4bb86c902dfedba848ad8ae43ef6dbd0bb41059be7fa1af816ecd85cc28fba5f"
NOW = datetime(2026, 7, 15, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    calls = 0

    def forbidden(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("front-review tests must remain offline")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    yield
    assert calls == 0


def _signals(**overrides) -> FrontVisualSignals:
    values = {name: True for name in FrontVisualSignals.model_fields}
    values.update(overrides)
    return FrontVisualSignals(**values)


def _plan() -> object:
    prompt = "Create one exact front portrait anchor."
    return build_front_plan(
        session_id="front_review_test_01",
        character_fingerprint=FINGERPRINT,
        prompt=prompt,
        prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
        generation_spec_version=1,
        repository_root=Path.cwd(),
    )


def _png(path: Path, color: str = "#5b7f76", size: tuple[int, int] = (768, 1024)):
    Image.new("RGB", size, color).save(path, format="PNG")


def _qc_set(tmp_path: Path, *, signals: FrontVisualSignals | None = None):
    rows = []
    for number in range(1, 4):
        path = tmp_path / f"candidate_{number:02d}.png"
        _png(path, ("#5b7f76", "#26332f", "#eef0e7")[number - 1])
        rows.append(
            run_candidate_qc(
                candidate_id=f"candidate_{number:02d}",
                candidate_number=number,
                path=path,
                provider="cloudflare",
                model="@cf/black-forest-labs/flux-1-schnell",
                request_id=None,
                seed=10_000 + number,
                signals=signals or _signals(),
            )
        )
    return tuple(rows)


def test_valid_png_qc_passes_all_automated_gates(tmp_path):
    qc = _qc_set(tmp_path)[0]
    assert qc.decoded_png is True
    assert (qc.width, qc.height) == (768, 1024)
    assert qc.mime_type == "image/png"
    assert qc.passed is True
    assert qc.eligible_for_selection is True
    assert qc.hard_failures == ()


@pytest.mark.parametrize("kind", ["mime", "dimensions", "oversized", "malformed"])
def test_wrong_mime_dimensions_oversize_and_malformed_outputs_fail_closed(tmp_path, kind):
    path = tmp_path / "candidate.png"
    if kind == "mime":
        Image.new("RGB", (768, 1024), "#5b7f76").save(path, format="JPEG")
    elif kind == "dimensions":
        _png(path, size=(767, 1024))
    elif kind == "oversized":
        _png(path)
    else:
        path.write_bytes(b"not a png")
    qc = run_candidate_qc(
        candidate_id="candidate_01",
        candidate_number=1,
        path=path,
        provider="cloudflare",
        model="model",
        request_id=None,
        seed=1,
        signals=_signals(),
        maximum_bytes=1 if kind == "oversized" else 20_000_000,
    )
    assert qc.passed is False
    assert qc.eligible_for_selection is False
    assert qc.hard_failures
    if kind == "mime":
        assert "mime_type" in qc.hard_failures
    if kind == "dimensions":
        assert "dimensions" in qc.hard_failures
    if kind == "oversized":
        assert "byte_size" in qc.hard_failures
    if kind == "malformed":
        assert "png_decode" in qc.hard_failures


def test_duplicate_response_bytes_are_separate_records_but_ineligible(tmp_path):
    path = tmp_path / "candidate.png"
    _png(path)
    first = run_candidate_qc(
        candidate_id="candidate_01", candidate_number=1, path=path,
        provider="cloudflare", model="model", request_id=None, seed=1,
        signals=_signals(),
    )
    second = run_candidate_qc(
        candidate_id="candidate_02", candidate_number=2, path=path,
        provider="cloudflare", model="model", request_id=None, seed=2,
        signals=_signals(), previous_sha256={first.image_sha256},
    )
    assert first.candidate_id != second.candidate_id
    assert second.duplicate_response is True
    assert second.passed is False
    assert second.eligible_for_selection is False


def test_hard_qc_candidate_cannot_be_selected_and_no_auto_selection(tmp_path):
    qcs = _qc_set(tmp_path)
    failed = run_candidate_qc(
        candidate_id="candidate_01", candidate_number=1, path=qcs[0].output_path,
        provider="cloudflare", model="model", request_id=None, seed=1,
        signals=_signals(no_coral_clothing=False),
    )
    qcs = (failed, qcs[1], qcs[2])
    manifest = build_candidate_manifest(
        plan=_plan(), qcs=qcs,
        contact_sheet_path=Path("out/character_reference_bootstrap/front_review_test_01/contact_sheet.png"),
        review_template_path=Path("out/character_reference_bootstrap/front_review_test_01/review_template.json"),
    )
    template = make_review_template(manifest)
    assert template.selected_candidate_id is None
    checklist = {key: True for key in template.checklist}
    incomplete = template.model_copy(update={"checklist": checklist})
    with pytest.raises(ValueError, match="failed or unknown"):
        record_human_selection(
            incomplete,
            candidate_id="candidate_01",
            approver_role="reviewer",
            selection_timestamp=NOW,
            review_notes="failed candidate",
        )


def test_human_selection_requires_all_checks_and_records_immutable_hash(tmp_path):
    qcs = _qc_set(tmp_path)
    manifest = build_candidate_manifest(
        plan=_plan(), qcs=qcs,
        contact_sheet_path=Path("out/character_reference_bootstrap/front_review_test_01/contact_sheet.png"),
        review_template_path=Path("out/character_reference_bootstrap/front_review_test_01/review_template.json"),
    )
    template = make_review_template(manifest)
    with pytest.raises(ValueError, match="checklist"):
        record_human_selection(
            template,
            candidate_id="candidate_01",
            approver_role="reviewer",
            selection_timestamp=NOW,
            review_notes="not yet",
        )
    approved = record_human_selection(
        template.model_copy(update={"checklist": {key: True for key in template.checklist}}),
        candidate_id="candidate_01",
        approver_role="reviewer",
        selection_timestamp=NOW,
        review_notes="suitable anchor",
    )
    assert approved.human_approved is True
    assert approved.immutable_selection_sha256
    anchor = to_bootstrap_selection(approved)
    assert anchor.selected_candidate_id == "candidate_01"
    with pytest.raises(ValueError, match="already immutable"):
        record_human_selection(
            approved,
            candidate_id="candidate_02",
            approver_role="reviewer",
            selection_timestamp=NOW,
            review_notes="replacement",
        )


def test_contact_sheet_is_local_and_candidate_pixels_remain_unchanged(tmp_path):
    qcs = _qc_set(tmp_path)
    before = [item.output_path.read_bytes() for item in qcs]
    manifest = build_candidate_manifest(
        plan=_plan(), qcs=qcs,
        contact_sheet_path=tmp_path / "contact_sheet.png",
        review_template_path=tmp_path / "review_template.json",
    )
    digest = build_contact_sheet(manifest=manifest, output_path=tmp_path / "contact_sheet.png")
    assert len(digest) == 64
    with Image.open(tmp_path / "contact_sheet.png") as sheet:
        assert sheet.size == (1536, 2048)
        assert sheet.format == "PNG"
    assert [item.output_path.read_bytes() for item in qcs] == before


def test_manifest_and_review_files_are_written_without_selection(tmp_path):
    qcs = _qc_set(tmp_path)
    manifest = build_candidate_manifest(
        plan=_plan(), qcs=qcs,
        contact_sheet_path=tmp_path / "contact_sheet.png",
        review_template_path=tmp_path / "review_template.json",
    )
    template = make_review_template(manifest)
    write_candidate_manifest(manifest, tmp_path / "candidates_manifest.json")
    write_review_template(template, tmp_path / "review_template.json")
    assert json.loads((tmp_path / "candidates_manifest.json").read_text())[
        "selected_candidate_id"
    ] is None
    assert json.loads((tmp_path / "review_template.json").read_text())[
        "human_approved"
    ] is False


def test_manifest_rejects_url_or_credential_material():
    with pytest.raises(ValidationError, match="URLs or credentials"):
        FrontCandidateArtifact(
            candidate_id="candidate_01", candidate_number=1,
            output_path=Path("out/candidate_01.png"), provider="https://invalid",
            model="model", request_id=None, seed=1, image_sha256="a" * 64,
            mime_type="image/png", width=768, height=1024, byte_size=100,
        )


def test_manifest_rejects_automatic_selection(tmp_path):
    qcs = _qc_set(tmp_path)
    with pytest.raises(ValidationError, match="Input should be None"):
        FrontCandidateManifest(
            schema_version=1, session_id="x", character_fingerprint=FINGERPRINT,
            provider="cloudflare", model="model", candidates=tuple(
                FrontCandidateArtifact(
                    candidate_id=item.candidate_id, candidate_number=item.candidate_number,
                    output_path=item.output_path, provider="cloudflare", model="model",
                    image_sha256=item.image_sha256, mime_type="image/png", width=768,
                    height=1024, byte_size=item.byte_size,
                ) for item in qcs
            ), qc_results=qcs, submission_count=3, transport_attempt_count=3,
            automatic_retries=0, fallbacks=0, selected_candidate_id="candidate_01",
            contact_sheet_path=Path("contact.png"), review_template_path=Path("review.json"),
        )
