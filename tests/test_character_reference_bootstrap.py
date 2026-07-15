from __future__ import annotations

import hashlib
import json
import socket
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from tella.media.bfl_flux2_provider import BFLFlux2Config, BFLFlux2ReferenceProvider
from tella.media.character_reference_bootstrap import (
    BOOTSTRAP_GLOBAL_SUBMISSION_MAX,
    VIEW_BUDGETS,
    AtomicViewRequestPlan,
    BootstrapCandidate,
    BootstrapState,
    HumanFrontSelectionRecord,
    MasterDerivationRecord,
    SubmissionAccounting,
    accept_atomic_candidate,
    approve_final_package,
    begin_package_approval,
    begin_remaining_views,
    new_bootstrap_manifest,
    record_candidate,
    record_master_assembled,
    require_provider_facing_approval,
    select_front_anchor,
    validate_bootstrap_provider,
    validate_reference_provider,
)
from tella.media.character_reference_package import ATOMIC_VIEW_ORDER
from tella.media.image_provider import CloudflareImageProvider


FINGERPRINT = "4bb86c902dfedba848ad8ae43ef6dbd0bb41059be7fa1af816ecd85cc28fba5f"
NOW = datetime(2026, 7, 15, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    calls = 0

    def forbidden(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("bootstrap tests must remain offline")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    yield
    assert calls == 0


def _manifest():
    return new_bootstrap_manifest(
        bootstrap_session_id="practical_teal_bootstrap_01",
        character_fingerprint=FINGERPRINT,
    )


def _candidate(role: str, number: int, *, targeted: bool = False) -> BootstrapCandidate:
    return BootstrapCandidate(
        candidate_id=f"{role}_{number}",
        asset_role=role,
        candidate_number=number,
        targeted=targeted,
        provider="mock_provider",
        model="mock_model_v1",
        request_id=f"request-{role}-{number}",
        prompt_sha256=hashlib.sha256(f"prompt-{role}".encode()).hexdigest(),
        image_sha256=hashlib.sha256(f"image-{role}-{number}".encode()).hexdigest(),
        mime_type="image/png",
        width=768,
        height=1024,
        rejection_reasons=("strict_qc_failed",) if targeted else (),
    )


def _selection(candidate: BootstrapCandidate) -> HumanFrontSelectionRecord:
    return HumanFrontSelectionRecord(
        selected_candidate_id=candidate.candidate_id,
        selected_image_sha256=candidate.image_sha256,
        selector_role="character_reference_reviewer",
        selected_at=NOW,
        decision="bootstrap_identity_anchor",
    )


def _plan(role: str, anchor_sha256: str) -> AtomicViewRequestPlan:
    prompt = f"Create the canonical {role} using the immutable front anchor."
    return AtomicViewRequestPlan(
        asset_role=role,
        stage="reference_conditioned",
        prompt=prompt,
        negative_constraints=("no extra person", "no text", "no malformed anatomy"),
        prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
        width=768,
        height=1024,
        output_mime_type="image/png",
        anchor_sha256=anchor_sha256,
    )


def _locked_manifest():
    manifest = _manifest()
    front = _candidate("front_portrait", 1)
    manifest = record_candidate(manifest, front)
    return select_front_anchor(manifest, _selection(front)), front


def _stage_b_manifest():
    manifest, front = _locked_manifest()
    anchor_bytes = b"immutable-front-anchor"
    front = front.model_copy(
        update={"image_sha256": hashlib.sha256(anchor_bytes).hexdigest()}
    )
    manifest = _manifest()
    manifest = record_candidate(manifest, front)
    manifest = select_front_anchor(manifest, _selection(front))
    plans = tuple(_plan(role, front.image_sha256) for role in ATOMIC_VIEW_ORDER[1:])
    return begin_remaining_views(
        manifest, anchor_bytes=anchor_bytes, request_plans=plans
    ), front, anchor_bytes


def test_stage_b_requires_one_selected_immutable_anchor():
    with pytest.raises(RuntimeError, match="immutable selected front anchor"):
        begin_remaining_views(_manifest(), anchor_bytes=b"x", request_plans=())


def test_multiple_front_candidates_require_exactly_one_human_selection():
    manifest = record_candidate(_manifest(), _candidate("front_portrait", 1))
    manifest = record_candidate(manifest, _candidate("front_portrait", 2))
    chosen = manifest.front_candidates[1]
    selected = select_front_anchor(manifest, _selection(chosen))
    assert selected.selected_candidate_id == chosen.candidate_id
    assert selected.human_selection_record.final_package_approval is False
    assert selected.state is BootstrapState.front_anchor_locked
    with pytest.raises(RuntimeError, match="current state"):
        select_front_anchor(selected, _selection(manifest.front_candidates[0]))


def test_unselected_or_unknown_front_candidate_cannot_become_reference():
    manifest = record_candidate(_manifest(), _candidate("front_portrait", 1))
    unknown = _candidate("front_portrait", 2)
    with pytest.raises(ValueError, match="exactly one generated front candidate"):
        select_front_anchor(manifest, _selection(unknown))


def test_anchor_byte_mismatch_blocks_all_remaining_views():
    manifest, _ = _locked_manifest()
    plans = tuple(
        _plan(role, manifest.selected_front_anchor_sha256)
        for role in ATOMIC_VIEW_ORDER[1:]
    )
    blocked = begin_remaining_views(
        manifest, anchor_bytes=b"changed-anchor-bytes", request_plans=plans
    )
    assert blocked.state is BootstrapState.blocked
    assert blocked.failure_reasons == ("front_anchor_sha256_mismatch",)
    with pytest.raises(RuntimeError, match="workflow is terminal"):
        record_candidate(blocked, _candidate("side_profile", 1))


def test_all_stage_b_requests_use_same_anchor_hash_and_order():
    manifest, front, anchor_bytes = _stage_b_manifest()
    assert tuple(manifest.per_view_request_plans) == ATOMIC_VIEW_ORDER[1:]
    assert {
        plan.anchor_sha256 for plan in manifest.per_view_request_plans.values()
    } == {front.image_sha256}
    bad = list(manifest.per_view_request_plans.values())
    bad[1] = bad[1].model_copy(update={"anchor_sha256": "f" * 64})
    locked = _manifest()
    locked_front = _candidate("front_portrait", 1).model_copy(
        update={"image_sha256": hashlib.sha256(anchor_bytes).hexdigest()}
    )
    locked = record_candidate(locked, locked_front)
    locked = select_front_anchor(locked, _selection(locked_front))
    with pytest.raises(ValueError, match="same immutable anchor hash"):
        begin_remaining_views(locked, anchor_bytes=anchor_bytes, request_plans=tuple(bad))


def test_targeted_candidates_require_failure_and_view_budget_is_enforced():
    manifest = _manifest()
    for number in range(1, 4):
        manifest = record_candidate(manifest, _candidate("front_portrait", number))
    with pytest.raises(RuntimeError, match="strict-QC failure"):
        record_candidate(manifest, _candidate("front_portrait", 4, targeted=True))
    manifest = record_candidate(
        manifest,
        _candidate("front_portrait", 4, targeted=True),
        strict_qc_failure_recorded=True,
    )
    manifest = record_candidate(
        manifest,
        _candidate("front_portrait", 5, targeted=True),
        strict_qc_failure_recorded=True,
    )
    with pytest.raises(RuntimeError, match="budget exhausted"):
        record_candidate(
            manifest,
            _candidate("front_portrait", 6, targeted=True),
            strict_qc_failure_recorded=True,
        )
    assert manifest.submission_accounting.per_view["front_portrait"].total == 5


def test_fixed_budget_totals_are_twelve_with_zero_retry_and_fallback():
    assert {role: budget.total_max for role, budget in VIEW_BUDGETS.items()} == {
        "front_portrait": 5,
        "three_quarter_portrait": 2,
        "side_profile": 2,
        "full_body_neutral": 3,
    }
    assert sum(item.total_max for item in VIEW_BUDGETS.values()) == 12
    assert BOOTSTRAP_GLOBAL_SUBMISSION_MAX == 12
    accounting = SubmissionAccounting()
    assert accounting.automatic_retries == accounting.fallbacks == 0
    with pytest.raises(ValidationError):
        SubmissionAccounting(total_image_submissions=13, transport_attempts=13)


def test_master_requires_all_four_qc_accepted_atomic_assets():
    manifest, front, _ = _stage_b_manifest()
    with pytest.raises(RuntimeError, match="all four accepted"):
        record_master_assembled(
            manifest,
            MasterDerivationRecord(
                assembly="deterministic_local_exact_2x2_v1",
                source_sha256=(front.image_sha256,) * 4,
                master_sha256="a" * 64,
            ),
        )
    for role in ATOMIC_VIEW_ORDER[1:]:
        candidate = _candidate(role, 1)
        manifest = record_candidate(manifest, candidate)
        manifest = accept_atomic_candidate(
            manifest,
            asset_role=role,
            candidate_id=candidate.candidate_id,
            anatomy_qc_passed=True,
            style_qc_passed=True,
            identity_qc_passed=True,
            human_qc_passed=True,
        )
    assert manifest.state is BootstrapState.atomic_views_accepted
    hashes = tuple(manifest.accepted_atomic_assets[role].image_sha256 for role in ATOMIC_VIEW_ORDER)
    manifest = record_master_assembled(
        manifest,
        MasterDerivationRecord(
            assembly="deterministic_local_exact_2x2_v1",
            source_sha256=hashes,
            master_sha256="a" * 64,
        ),
    )
    assert manifest.state is BootstrapState.master_assembled
    manifest = begin_package_approval(manifest)
    manifest = approve_final_package(manifest, approval_record_sha256="b" * 64)
    provider_assets = require_provider_facing_approval(manifest)
    assert tuple(asset.asset_role for asset in provider_assets) == ATOMIC_VIEW_ORDER


def test_no_provider_facing_use_or_final_approval_before_human_qc():
    manifest, _, _ = _stage_b_manifest()
    with pytest.raises(RuntimeError, match="final human approval"):
        require_provider_facing_approval(manifest)
    with pytest.raises(RuntimeError, match="not available"):
        approve_final_package(manifest, approval_record_sha256="a" * 64)
    candidate = _candidate("side_profile", 1)
    manifest = record_candidate(manifest, candidate)
    with pytest.raises(RuntimeError, match="all QC gates"):
        accept_atomic_candidate(
            manifest,
            asset_role="side_profile",
            candidate_id=candidate.candidate_id,
            anatomy_qc_passed=True,
            style_qc_passed=True,
            identity_qc_passed=False,
            human_qc_passed=True,
        )


def test_provider_capability_mismatch_fails_closed_without_construction():
    cloudflare = CloudflareImageProvider().capabilities()
    assessment = validate_bootstrap_provider(cloudflare)
    assert assessment["identity_guarantee"] is False
    with pytest.raises(RuntimeError, match="reference conditioning"):
        validate_reference_provider(
            cloudflare, reference_transport_ready=True, live_authorized=True
        )

    bfl_without_store = BFLFlux2ReferenceProvider(
        config=BFLFlux2Config(), reference_store=None, transport=object(), api_key=None
    ).capabilities()
    with pytest.raises(RuntimeError, match="reference conditioning"):
        validate_reference_provider(
            bfl_without_store, reference_transport_ready=True, live_authorized=True
        )

    bfl_ready_capability = BFLFlux2ReferenceProvider(
        config=BFLFlux2Config(), reference_store=object(), transport=object(), api_key=None
    ).capabilities()
    with pytest.raises(RuntimeError, match="transport is not ready"):
        validate_reference_provider(
            bfl_ready_capability, reference_transport_ready=False, live_authorized=True
        )
    with pytest.raises(RuntimeError, match="authorization is missing"):
        validate_reference_provider(
            bfl_ready_capability, reference_transport_ready=True, live_authorized=False
        )


def test_manifest_serialization_contains_no_complete_url_or_credential():
    serialized = json.dumps(_manifest().model_dump(mode="json"), sort_keys=True)
    lowered = serialized.lower()
    assert "://" not in serialized
    assert "authorization" not in lowered
    assert "bearer " not in lowered
    assert "api_key" not in lowered
    assert "secret_access_key" not in lowered
    with pytest.raises(ValidationError, match="URLs or credentials"):
        _candidate("front_portrait", 1).model_copy(
            update={"request_id": "https://provider.invalid/signed?secret=x"}
        ).__class__.model_validate(
            {
                **_candidate("front_portrait", 1).model_dump(),
                "request_id": "https://provider.invalid/signed?secret=x",
            }
        )
