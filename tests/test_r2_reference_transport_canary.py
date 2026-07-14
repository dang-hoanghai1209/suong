from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path
from urllib.parse import urlencode, urlunsplit

import pytest

from scripts.benchmarks.r2_reference_transport_canary import (
    AUTHORIZATION_TOKEN,
    REQUIRED_CLEANUP_BRANCHES,
    deterministic_image_diagnostic,
    deterministic_test_png,
    load_canary_config,
    main,
    redact_presigned_url,
    validate_live_prerequisites,
)


CONFIG_PATH = Path("configs/benchmarks/r2_reference_transport_canary_v1.json")


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    calls = 0

    def forbidden(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("network is forbidden in R2 canary tests")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    yield
    assert calls == 0


def _confirmed_config(tmp_path: Path):
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    payload["transport_policy"]["private_bucket_status_confirmed"] = True
    payload["transport_policy"]["conditional_write_test_confirmed"] = True
    path = tmp_path / "confirmed.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return load_canary_config(path)


def test_validate_only_constructs_zero_clients_and_needs_no_credentials(
    monkeypatch, capsys
):
    for name in (
        "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"
    ):
        monkeypatch.delenv(name, raising=False)
    assert main(["--config", str(CONFIG_PATH), "--mode", "validate-only"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "valid"
    assert result["clients_constructed"] == 0
    assert result["external_calls"] == 0
    assert result["live_execution_blocked"] is True
    assert result["request_budget"]["immutable_upload_attempts_max"] == 3
    assert result["request_budget"]["automatic_retries"] == 0


def test_deterministic_test_png_and_hash_are_stable():
    config = load_canary_config(CONFIG_PATH)
    first = deterministic_test_png(config.test_image)
    second = deterministic_test_png(config.test_image)
    assert first == second
    assert first.startswith(b"\x89PNG\r\n\x1a\n")
    assert hashlib.sha256(first).hexdigest() == (
        "99ac29d0e49ebcb6a8ed06859beb8d6d59c1c926198c2d66b1a940ac97db2ceb"
    )
    diagnostic = deterministic_image_diagnostic(config)
    assert diagnostic["dimensions"] == [64, 64]
    assert diagnostic["byte_size"] == len(first)


def test_missing_authorization_blocks_before_credentials(tmp_path, monkeypatch):
    config = _confirmed_config(tmp_path)
    for name in (
        "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"
    ):
        monkeypatch.setenv(name, "present-test-value")
    with pytest.raises(RuntimeError, match="authorization is missing"):
        validate_live_prerequisites(config, authorization_token="wrong")


def test_missing_credentials_block_live_mode(tmp_path, monkeypatch):
    config = _confirmed_config(tmp_path)
    for name in (
        "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="credentials are incomplete"):
        validate_live_prerequisites(
            config, authorization_token=AUTHORIZATION_TOKEN
        )


def test_unrelated_image_provider_credential_is_ignored(monkeypatch, capsys):
    monkeypatch.setenv("BFL_API_KEY", "must-not-be-read")
    assert main(["--config", str(CONFIG_PATH), "--mode", "validate-only"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "valid"
    source = Path(
        "scripts/benchmarks/r2_reference_transport_canary.py"
    ).read_text(encoding="utf-8")
    assert "BFL_API_KEY" not in source
    assert "bfl_flux2_provider" not in source


def test_signed_url_is_redacted_without_query_persistence():
    url = urlunsplit((
        "https", "private.r2.example", "/reference.png",
        urlencode({"signature": "DO-NOT-PERSIST"}), "",
    ))
    redacted = redact_presigned_url(url)
    serialized = json.dumps(redacted)
    assert redacted == {"url_scheme": "https", "url_host": "private.r2.example"}
    assert "DO-NOT-PERSIST" not in serialized
    assert "signature" not in serialized


def test_cleanup_policy_covers_every_terminal_branch_once():
    config = load_canary_config(CONFIG_PATH)
    assert set(config.cleanup_required_on) == REQUIRED_CLEANUP_BRANCHES
    assert len(config.cleanup_required_on) == len(REQUIRED_CLEANUP_BRANCHES)


def test_live_executor_remains_separately_gated(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "confirmed.json"
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    payload["transport_policy"]["private_bucket_status_confirmed"] = True
    payload["transport_policy"]["conditional_write_test_confirmed"] = True
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    for name in (
        "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"
    ):
        monkeypatch.setenv(name, "present-test-value")
    with pytest.raises(SystemExit, match="executor is intentionally not installed"):
        main([
            "--config", str(config_path),
            "--mode", "live-r2",
            "--authorization-token", AUTHORIZATION_TOKEN,
        ])
    output = json.loads(capsys.readouterr().out)
    assert output["clients_constructed"] == 0
    assert output["external_calls"] == 0
