from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.benchmarks.front_anchor_generation import main, validate_only
from tella.media.front_anchor_harness import (
    LIVE_AUTHORIZATION_TOKEN,
    FrontCandidateRequest,
    FrontHarnessBlocked,
    FrontHarnessPlan,
    FrontSubmissionAccounting,
    build_front_plan,
    cloudflare_adapter_audit,
    plan_initial_front_candidates,
    record_front_submission,
    validate_live_front,
    validate_output_root,
)


CONFIG_PATH = Path(
    "configs/character_references/"
    "practical_young_adult_male_teal_v1_bootstrap_v1.json"
)
FINGERPRINT = "4bb86c902dfedba848ad8ae43ef6dbd0bb41059be7fa1af816ecd85cc28fba5f"


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    calls = 0

    def forbidden(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("front harness tests must remain offline")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    yield
    assert calls == 0


def _plan(session_id: str = "front_harness_test_01") -> FrontHarnessPlan:
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    front = payload["request_specs"][0]
    return build_front_plan(
        session_id=session_id,
        character_fingerprint=FINGERPRINT,
        prompt=front["prompt"],
        prompt_sha256=front["prompt_sha256"],
        generation_spec_version=1,
        repository_root=Path.cwd(),
    )


def test_initial_plan_is_front_only_exactly_three_and_targeted_zero():
    plan = _plan()
    requests = plan_initial_front_candidates(plan)
    assert len(requests) == 3
    assert all(isinstance(item, FrontCandidateRequest) for item in requests)
    assert [item.candidate_number for item in requests] == [1, 2, 3]
    assert [item.asset_role for item in requests] == ["front_portrait"] * 3
    assert all(item.targeted is False for item in requests)
    assert all(item.output_path.name == f"candidate_{i:02d}.png" for i, item in enumerate(requests, 1))
    assert plan.targeted_candidates_max == 0
    assert plan.total_submissions_max == 3


def test_accounting_stops_at_three_and_retries_fallbacks_are_zero():
    accounting = FrontSubmissionAccounting()
    for _ in range(3):
        accounting = record_front_submission(accounting)
    assert accounting.submissions == 3
    assert accounting.transport_attempts == 3
    assert accounting.automatic_retries == accounting.fallbacks == 0
    with pytest.raises(FrontHarnessBlocked, match="exhausted"):
        record_front_submission(accounting)


def test_validate_only_requires_no_credentials_clients_or_artifacts(capsys):
    result = validate_only(
        config_path=CONFIG_PATH,
        repository_root=Path.cwd(),
        session_id="front_validate_only_test_01",
    )
    assert result["asset_role"] == "front_portrait"
    assert result["initial_candidates"] == 3
    assert result["targeted_candidates"] == 0
    assert result["stage_b_requested"] is False
    assert result["provider_clients_constructed"] == 0
    assert result["provider_calls"] == 0
    assert result["external_calls"] == 0
    assert result["generated_artifacts"] == 0
    assert result["live_front_status"] == "blocked"
    assert not Path(result["output_root_resolved"]).exists()
    assert main([
        "--config", str(CONFIG_PATH), "--mode", "validate-only",
        "--repository-root", ".", "--session-id", "front_validate_only_test_02",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["provider_calls"] == 0


def test_live_front_requires_exact_authorization_before_provider_construction(monkeypatch):
    plan = _plan()
    monkeypatch.setenv("CF_ACCOUNT_ID", "account-test")
    monkeypatch.setenv("CF_AI_TOKEN", "token-test")
    with pytest.raises(FrontHarnessBlocked, match="exact front-generation authorization"):
        validate_live_front(
            plan, repository_root=Path.cwd(), authorization_token="wrong", clean_worktree=True
        )


def test_live_front_missing_credentials_fails_without_dotenv_loading(monkeypatch):
    plan = _plan()
    monkeypatch.delenv("CF_ACCOUNTS", raising=False)
    monkeypatch.delenv("CF_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CF_AI_TOKEN", raising=False)
    with pytest.raises(FrontHarnessBlocked, match="credentials are missing"):
        validate_live_front(
            plan,
            repository_root=Path.cwd(),
            authorization_token=LIVE_AUTHORIZATION_TOKEN,
            clean_worktree=True,
        )


def test_live_front_is_blocked_when_actual_adapter_cannot_prove_dimensions(monkeypatch):
    plan = _plan()
    monkeypatch.setenv("CF_ACCOUNT_ID", "account-test")
    monkeypatch.setenv("CF_AI_TOKEN", "token-test")
    with pytest.raises(FrontHarnessBlocked, match="cannot prove exact 768x1024"):
        validate_live_front(
            plan,
            repository_root=Path.cwd(),
            authorization_token=LIVE_AUTHORIZATION_TOKEN,
            clean_worktree=True,
        )


def test_live_front_requires_clean_worktree_and_safe_output_root(monkeypatch):
    plan = _plan()
    monkeypatch.setenv("CF_ACCOUNT_ID", "account-test")
    monkeypatch.setenv("CF_AI_TOKEN", "token-test")
    with pytest.raises(FrontHarnessBlocked, match="clean source worktree"):
        validate_live_front(
            plan,
            repository_root=Path.cwd(),
            authorization_token=LIVE_AUTHORIZATION_TOKEN,
            clean_worktree=False,
        )
    assert validate_output_root(plan, repository_root=Path.cwd()).name == plan.session_id
    with pytest.raises(ValidationError, match="ignored bootstrap directory"):
        FrontHarnessPlan.model_validate(
            {**plan.model_dump(), "output_root": "other/path/session"}
        )


def test_actual_cloudflare_adapter_audit_is_explicit_and_not_inferred():
    audit = cloudflare_adapter_audit()
    assert audit["model"] == "@cf/black-forest-labs/flux-1-schnell"
    assert audit["payload_fields"] == ["prompt", "steps", "width", "height", "seed_optional"]
    assert audit["width_height_sent"] is True
    assert audit["exact_768x1024_proven"] is False
    assert audit["output_mime_validation"] is False
    assert audit["output_dimension_validation"] is False
    assert audit["timeout_seconds"] == 60.0
    assert audit["provider_default_attempts_per_account"] == 3
    assert audit["account_rotation"] is True
    assert audit["safety_retry"] is True
    assert audit["seed_supported"] is True
    assert audit["request_id_available"] is False
    assert audit["caller_fallback_possible"] is True
