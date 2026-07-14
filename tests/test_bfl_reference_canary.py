from __future__ import annotations

import hashlib
import io
import json
import socket

import pytest
from PIL import Image

from scripts.benchmarks.bfl_reference_canary import (
    AUTHORIZATION_TOKEN,
    load_canary_config,
    main,
    validate_paid_prerequisites,
)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    calls = 0
    def forbidden(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("network is forbidden")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    yield
    assert calls == 0


def _config_path():
    from pathlib import Path
    return Path("configs/benchmarks/bfl_flux2_reference_canary_v1.json")


def test_validate_only_constructs_no_clients(capsys):
    assert main(["--config", str(_config_path()), "--mode", "validate-only"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "valid"
    assert result["clients_constructed"] == 0
    assert result["external_calls"] == 0
    assert result["maximum_submissions"] == 6


def test_paid_mode_fails_before_credentials_or_clients(tmp_path, monkeypatch):
    for name in ("BFL_API_KEY", "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID",
                 "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"):
        monkeypatch.delenv(name, raising=False)
    config = load_canary_config(_config_path())
    with pytest.raises(RuntimeError, match="BFL credential"):
        validate_paid_prerequisites(
            config, manifest_path=tmp_path / "missing.json",
            approval_record_sha256="a" * 64,
            authorization_token=AUTHORIZATION_TOKEN,
        )


def test_paid_mode_requires_deployment_confirmations_after_presence(tmp_path, monkeypatch):
    for name in ("BFL_API_KEY", "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID",
                 "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"):
        monkeypatch.setenv(name, "present-only-test-value")
    config = load_canary_config(_config_path())
    with pytest.raises(RuntimeError, match="private-bucket status"):
        validate_paid_prerequisites(
            config, manifest_path=tmp_path / "missing.json",
            approval_record_sha256="a" * 64,
            authorization_token=AUTHORIZATION_TOKEN,
        )


def test_paid_prerequisite_reference_identity_with_confirmed_policy(tmp_path, monkeypatch):
    for name in ("BFL_API_KEY", "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID",
                 "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"):
        monkeypatch.setenv(name, "present-only-test-value")
    raw = io.BytesIO()
    Image.new("RGB", (64, 64), "teal").save(raw, format="PNG")
    image = tmp_path / "approved.png"
    image.write_bytes(raw.getvalue())
    approval = "human approval record"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "version": 1, "image_path": str(image),
        "image_sha256": hashlib.sha256(raw.getvalue()).hexdigest(),
        "character_fingerprint": "a" * 64, "provenance": "human reviewed",
        "views": ["front_face", "three_quarter", "side_view", "full_body"],
        "anatomy_qc_passed": True, "style_qc_passed": True,
        "human_approved": True, "approval_record": approval,
    }), encoding="utf-8")
    data = json.loads(_config_path().read_text(encoding="utf-8"))
    data["transport_policy"]["private_bucket_status_confirmed"] = True
    data["transport_policy"]["r2_conditional_write_support_confirmed"] = True
    configured = tmp_path / "config.json"
    configured.write_text(json.dumps(data), encoding="utf-8")
    result = validate_paid_prerequisites(
        load_canary_config(configured), manifest_path=manifest,
        approval_record_sha256=hashlib.sha256(approval.encode()).hexdigest(),
        authorization_token=AUTHORIZATION_TOKEN,
    )
    assert result["external_calls"] == 0
    assert result["maximum_submissions"] == 6
