from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.benchmarks.character_reference_bootstrap import (
    BootstrapPlanConfig,
    load_and_validate_plan,
    main,
    validate_only,
)
from tella.media.character_reference_bootstrap import (
    BOOTSTRAP_GLOBAL_SUBMISSION_MAX,
    VIEW_BUDGETS,
)
from tella.media.character_reference_package import ATOMIC_VIEW_ORDER


CONFIG_PATH = Path(
    "configs/character_references/"
    "practical_young_adult_male_teal_v1_bootstrap_v1.json"
)
DOC_PATH = Path(
    "docs/character_reference/practical_young_adult_male_teal_v1_bootstrap.md"
)
FINGERPRINT = "4bb86c902dfedba848ad8ae43ef6dbd0bb41059be7fa1af816ecd85cc28fba5f"


@pytest.fixture(autouse=True)
def _block_network_and_provider_calls(monkeypatch):
    calls = {"network": 0, "provider": 0}

    def network_forbidden(*args, **kwargs):
        calls["network"] += 1
        raise AssertionError("bootstrap-plan tests must remain offline")

    async def provider_forbidden(*args, **kwargs):
        calls["provider"] += 1
        raise AssertionError("validate-only must not invoke a provider")

    monkeypatch.setattr(socket, "create_connection", network_forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", network_forbidden)
    monkeypatch.setattr(
        "tella.media.image_provider.CloudflareImageProvider.generate_text_image",
        provider_forbidden,
    )
    monkeypatch.setattr(
        "tella.media.bfl_flux2_provider.BFLFlux2ReferenceProvider.generate_with_references",
        provider_forbidden,
    )
    yield
    assert calls == {"network": 0, "provider": 0}


def _payload() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def test_plan_loads_against_canonical_character_and_exact_view_order():
    plan = load_and_validate_plan(CONFIG_PATH, repository_root=Path.cwd())
    assert plan.character_fingerprint == FINGERPRINT
    assert tuple(item.asset_role for item in plan.request_specs) == ATOMIC_VIEW_ORDER
    assert all((item.width, item.height) == (768, 1024) for item in plan.request_specs)
    assert all(item.output_mime_type == "image/png" for item in plan.request_specs)


def test_all_four_prompts_are_hashed_and_include_approved_constraints():
    plan = load_and_validate_plan(CONFIG_PATH, repository_root=Path.cwd())
    assert len(plan.request_specs) == 4
    assert plan.shared_anatomy_constraints
    assert plan.shared_negative_constraints
    joined_anatomy = " ".join(plan.shared_anatomy_constraints).lower()
    joined_negative = " ".join(plan.shared_negative_constraints).lower()
    assert "two naturally connected arms" in joined_anatomy
    assert "five digits correctly represented or implied" in joined_anatomy
    assert "no text" in joined_negative
    assert "no extra person" in joined_negative
    for request in plan.request_specs:
        assert hashlib.sha256(request.prompt.encode("utf-8")).hexdigest() == request.prompt_sha256
        assert request.constraints_profile == "approved_practical_character_anatomy_and_negative_v1"
        assert len(request.prompt.encode("utf-8")) <= 2000


def test_front_is_unreferenced_and_remaining_views_bind_exact_anchor():
    plan = load_and_validate_plan(CONFIG_PATH, repository_root=Path.cwd())
    front, *remaining = plan.request_specs
    assert front.stage == "bootstrap_front"
    assert front.anchor_binding == "none"
    assert all(item.stage == "reference_conditioned" for item in remaining)
    assert all(
        item.anchor_binding == "selected_front_anchor_sha256_exact"
        for item in remaining
    )


def test_request_budget_matches_typed_workflow_and_has_no_retry_or_fallback():
    plan = load_and_validate_plan(CONFIG_PATH, repository_root=Path.cwd())
    configured = {
        role: getattr(plan.request_budget, role).total_max for role in ATOMIC_VIEW_ORDER
    }
    assert configured == {role: value.total_max for role, value in VIEW_BUDGETS.items()}
    assert sum(configured.values()) == BOOTSTRAP_GLOBAL_SUBMISSION_MAX == 12
    assert plan.request_budget.transport_attempts_per_submission_max == 1
    assert plan.request_budget.automatic_retries == 0
    assert plan.request_budget.fallbacks == 0


def test_provider_assessment_is_truthful_and_not_authorized():
    plan = load_and_validate_plan(CONFIG_PATH, repository_root=Path.cwd())
    cloudflare, bfl = plan.provider_assessment
    assert cloudflare.provider_id == "cloudflare"
    assert cloudflare.text_to_image is True
    assert cloudflare.reference_conditioning is False
    assert cloudflare.identity_guarantee is False
    assert bfl.provider_id == "bfl_flux2_reference"
    assert bfl.reference_conditioning is True
    assert bfl.text_to_image is False
    assert bfl.identity_guarantee is False
    assert "private reference transport" in bfl.requirements
    assert "BFL live authorization" in bfl.requirements
    assert not cloudflare.execution_authorized and not bfl.execution_authorized


def test_validate_only_constructs_no_clients_and_makes_no_calls(capsys):
    result = validate_only(CONFIG_PATH, repository_root=Path.cwd())
    assert result["status"] == "valid_no_execution"
    assert result["provider_clients_constructed"] == 0
    assert result["image_provider_calls"] == 0
    assert result["external_calls"] == 0
    assert result["image_generation"] == 0
    assert main(["--config", str(CONFIG_PATH), "--mode", "validate-only"]) == 0
    emitted = json.loads(capsys.readouterr().out)
    assert emitted == result


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "wrong_order"])
def test_missing_duplicate_or_wrong_order_request_plan_fails(mutation):
    payload = _payload()
    if mutation == "missing":
        payload["request_specs"].pop()
    elif mutation == "duplicate":
        payload["request_specs"][1] = dict(payload["request_specs"][0])
    else:
        payload["request_specs"][0], payload["request_specs"][1] = (
            payload["request_specs"][1],
            payload["request_specs"][0],
        )
    with pytest.raises(ValidationError, match="missing, duplicated, or out of order"):
        BootstrapPlanConfig.model_validate(payload)


def test_budget_or_prompt_tampering_fails_closed():
    payload = _payload()
    payload["request_budget"]["front_portrait"]["total_max"] = 6
    with pytest.raises(ValidationError, match="differs from workflow contract"):
        BootstrapPlanConfig.model_validate(payload)
    payload = _payload()
    payload["request_specs"][0]["prompt"] += " changed"
    with pytest.raises(ValidationError, match="prompt SHA256 mismatch"):
        BootstrapPlanConfig.model_validate(payload)


def test_config_manifest_and_docs_contain_no_secret_or_complete_url():
    material = CONFIG_PATH.read_text(encoding="utf-8") + DOC_PATH.read_text(encoding="utf-8")
    lowered = material.lower()
    assert "://" not in material
    assert "authorization:" not in lowered
    assert "bearer " not in lowered
    assert "api_key=" not in lowered
    assert "secret_access_key" not in lowered
    assert "d:\\" not in lowered


def test_documentation_preserves_human_and_execution_boundaries():
    text = " ".join(DOC_PATH.read_text(encoding="utf-8").split())
    assert "must not be described as guaranteeing identity" in text
    assert "human reviewer must select exactly one candidate" in text
    assert "not final package approval" in text
    assert "same anchor bytes" in text
    assert "Automatic retries and fallbacks are zero" in text
    assert "does not authorize or implement provider execution" in text
