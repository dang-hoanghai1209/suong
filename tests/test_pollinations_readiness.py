from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tella.topic_production import (
    PollinationsBalanceState,
    PollinationsHealthState,
    PollinationsReadinessChecker,
    PollinationsReadinessReason,
    build_pollinations_readiness_snapshot,
    pollinations_readiness_decision,
)
from tella.visual_generation.providers import PollinationsConfig


CHECKED_AT = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


def _snapshot(**updates):
    values = {
        "enabled": True,
        "credential_present": True,
        "credential_valid": True,
        "model": "klein",
        "model_available": True,
        "balance_state": PollinationsBalanceState.POSITIVE,
        "health_state": PollinationsHealthState.HEALTHY,
        "checked_at": CHECKED_AT,
        "valid_for": timedelta(minutes=15),
    }
    values.update(updates)
    return build_pollinations_readiness_snapshot(**values)


def test_positive_balance_available_model_and_healthy_provider_are_eligible():
    snapshot = _snapshot()

    assert snapshot.eligible_for_generation is True
    assert snapshot.blocking_reason is PollinationsReadinessReason.READY


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"enabled": False}, PollinationsReadinessReason.DISABLED),
        (
            {"credential_present": False, "credential_valid": None},
            PollinationsReadinessReason.MISSING_CREDENTIAL,
        ),
        ({"credential_valid": False}, PollinationsReadinessReason.INVALID_CREDENTIAL),
        ({"model_available": False}, PollinationsReadinessReason.MODEL_UNAVAILABLE),
        (
            {"balance_state": PollinationsBalanceState.ZERO},
            PollinationsReadinessReason.ZERO_BALANCE,
        ),
        (
            {"health_state": PollinationsHealthState.UNHEALTHY},
            PollinationsReadinessReason.PROVIDER_UNHEALTHY,
        ),
        (
            {"balance_state": PollinationsBalanceState.UNKNOWN},
            PollinationsReadinessReason.READINESS_UNKNOWN,
        ),
    ],
)
def test_blocking_facts_are_typed_and_fail_closed(updates, reason):
    snapshot = _snapshot(**updates)

    assert snapshot.eligible_for_generation is False
    assert snapshot.blocking_reason is reason


def test_unknown_health_does_not_invent_an_sla_failure():
    snapshot = _snapshot(health_state=PollinationsHealthState.UNKNOWN)

    assert snapshot.eligible_for_generation is True
    assert snapshot.blocking_reason is PollinationsReadinessReason.READY


def test_snapshot_routing_is_deterministic_until_explicit_expiry():
    snapshot = _snapshot()
    at = CHECKED_AT + timedelta(minutes=5)

    first = pollinations_readiness_decision(snapshot, expected_model="klein", at=at)
    second = pollinations_readiness_decision(snapshot, expected_model="klein", at=at)
    stale = pollinations_readiness_decision(
        snapshot,
        expected_model="klein",
        at=CHECKED_AT + timedelta(minutes=15),
    )

    assert first == second
    assert first.eligible is True
    assert stale.eligible is False
    assert stale.reason is PollinationsReadinessReason.READINESS_UNKNOWN


def test_model_switching_is_rejected_by_snapshot_decision():
    decision = pollinations_readiness_decision(
        _snapshot(), expected_model="flux", at=CHECKED_AT
    )

    assert decision.eligible is False
    assert decision.reason is PollinationsReadinessReason.MODEL_UNAVAILABLE


def test_snapshot_schema_cannot_persist_secrets_or_raw_account_data():
    payload = _snapshot().model_dump(mode="json")
    serialized = _snapshot().model_dump_json().lower()

    assert "api_key" not in payload
    assert "authorization" not in serialized
    assert "bearer" not in serialized
    assert "balance" not in payload
    assert payload["balance_state"] == "positive"


class Response:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class ReadinessSender:
    def __init__(self, *, balance=0, valid=True):
        self.balance = balance
        self.valid = valid
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        url = kwargs["url"]
        if url.endswith("/account/key"):
            return Response({"valid": self.valid})
        if url.endswith("/account/balance"):
            return Response({"balance": self.balance})
        if url.endswith("/image/models"):
            return Response([{"name": "klein", "outputModalities": ["image"]}])
        if "/v1/models/status" in url:
            return Response([{"model": "klein", "status": "healthy"}])
        raise AssertionError("unexpected readiness endpoint")


@pytest.mark.asyncio
async def test_checker_uses_one_header_only_readiness_refresh_and_no_generation():
    secret = "sk_readiness_test_secret"
    sender = ReadinessSender(balance=0)
    checker = PollinationsReadinessChecker(
        config=PollinationsConfig(enabled=True, model="klein"),
        api_key_resolver=lambda: secret,
        request_sender=sender,
    )

    snapshot = await checker.check(checked_at=CHECKED_AT)

    assert snapshot.blocking_reason is PollinationsReadinessReason.ZERO_BALANCE
    assert snapshot.eligible_for_generation is False
    assert snapshot.readiness_calls == len(sender.calls) == 2
    assert {call["url"].split("gen.pollinations.ai")[-1].split("?")[0] for call in sender.calls} == {
        "/account/key",
        "/account/balance",
    }
    assert all(call["headers"] == {"Authorization": f"Bearer {secret}"} for call in sender.calls)
    assert all(secret not in call["url"] for call in sender.calls)
    assert all("prompt" not in call for call in sender.calls)
    assert secret not in snapshot.model_dump_json()


@pytest.mark.asyncio
async def test_checker_marks_positive_listed_healthy_model_ready():
    sender = ReadinessSender(balance=1)
    checker = PollinationsReadinessChecker(
        config=PollinationsConfig(enabled=True, model="klein"),
        api_key_resolver=lambda: "sk_positive_test_secret",
        request_sender=sender,
    )

    snapshot = await checker.check(checked_at=CHECKED_AT)

    assert snapshot.eligible_for_generation is True
    assert snapshot.blocking_reason is PollinationsReadinessReason.READY
    assert snapshot.model_available is True
    assert snapshot.balance_state is PollinationsBalanceState.POSITIVE
    assert snapshot.health_state is PollinationsHealthState.HEALTHY
    assert snapshot.readiness_calls == len(sender.calls) == 4


@pytest.mark.asyncio
async def test_invalid_credential_stops_readiness_refresh_after_key_check():
    sender = ReadinessSender(valid=False)
    checker = PollinationsReadinessChecker(
        config=PollinationsConfig(enabled=True, model="klein"),
        api_key_resolver=lambda: "sk_invalid_test_secret",
        request_sender=sender,
    )

    snapshot = await checker.check(checked_at=CHECKED_AT)

    assert snapshot.blocking_reason is PollinationsReadinessReason.INVALID_CREDENTIAL
    assert snapshot.readiness_calls == len(sender.calls) == 1
