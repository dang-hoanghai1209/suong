from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from scripts.benchmarks.bfl_front_anchor_canary import SEEDS, main, validate_only


CONFIG = Path("configs/character_references/practical_young_adult_male_teal_v1_bootstrap_v1.json")


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    calls = 0

    def forbidden(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("BFL front canary tests must remain offline")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    yield
    assert calls == 0


def test_validate_only_is_zero_call_and_fixed_budget(tmp_path):
    result = validate_only(config_path=CONFIG, repository_root=Path.cwd(), session_id="bfl_front_test")
    assert result["provider_id"] == "bfl_flux_1_1_pro_front_anchor"
    assert result["endpoint_path"] == "/v1/flux-pro-1.1"
    assert result["dimensions"] == [768, 1024]
    assert result["output_format"] == "png"
    assert result["prompt_upsampling"] is False
    assert result["seeds"] == list(SEEDS) and len(set(SEEDS)) == 3
    assert result["maximum_submissions"] == 3
    assert result["automatic_retries"] == result["fallbacks"] == 0
    assert result["provider_clients_constructed"] == result["provider_calls"] == 0


def test_live_mode_is_gated_and_does_not_construct_provider(capsys, monkeypatch):
    monkeypatch.delenv("BFL_API_KEY", raising=False)
    assert main(["--config", str(CONFIG), "--mode", "live-front-bfl", "--repository-root", "."]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "blocked_no_execution"
    assert result["provider_calls"] == 0
    assert result["credential_present"] is False

