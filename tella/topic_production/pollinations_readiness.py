"""Sanitized, reusable Pollinations readiness checks for Volume routing."""
from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tella.visual_generation.providers.pollinations import PollinationsConfig


class PollinationsReadinessReason(StrEnum):
    DISABLED = "disabled"
    MISSING_CREDENTIAL = "missing_credential"
    INVALID_CREDENTIAL = "invalid_credential"
    MODEL_UNAVAILABLE = "model_unavailable"
    ZERO_BALANCE = "zero_balance"
    PROVIDER_UNHEALTHY = "provider_unhealthy"
    READINESS_UNKNOWN = "readiness_unknown"
    READY = "ready"


class PollinationsBalanceState(StrEnum):
    UNKNOWN = "unknown"
    ZERO = "zero"
    POSITIVE = "positive"


class PollinationsHealthState(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class PollinationsReadinessSnapshot(BaseModel):
    """Secret-free run-level facts; safe to persist and reuse until expiry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool
    credential_present: bool
    credential_valid: bool | None
    model: str = Field(min_length=1)
    model_available: bool | None
    balance_state: PollinationsBalanceState
    health_state: PollinationsHealthState
    eligible_for_generation: bool
    blocking_reason: PollinationsReadinessReason
    checked_at: datetime
    valid_until: datetime
    readiness_calls: int = Field(default=0, ge=0, le=4)

    @model_validator(mode="after")
    def validate_consistency(self) -> "PollinationsReadinessSnapshot":
        expected = _readiness_reason(
            enabled=self.enabled,
            credential_present=self.credential_present,
            credential_valid=self.credential_valid,
            model_available=self.model_available,
            balance_state=self.balance_state,
            health_state=self.health_state,
        )
        if self.blocking_reason is not expected:
            raise ValueError("Pollinations readiness reason does not match its safe facts")
        if self.eligible_for_generation is not (expected is PollinationsReadinessReason.READY):
            raise ValueError("Pollinations eligibility does not match its blocking reason")
        if self.valid_until <= self.checked_at:
            raise ValueError("Pollinations readiness expiry must follow its check time")
        return self

    def is_fresh(self, *, at: datetime | None = None) -> bool:
        moment = at or datetime.now(timezone.utc)
        return self.checked_at <= moment < self.valid_until


class PollinationsReadinessDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    eligible: bool
    reason: PollinationsReadinessReason
    detail: str = Field(min_length=1)


def _readiness_reason(
    *,
    enabled: bool,
    credential_present: bool,
    credential_valid: bool | None,
    model_available: bool | None,
    balance_state: PollinationsBalanceState,
    health_state: PollinationsHealthState,
) -> PollinationsReadinessReason:
    if not enabled:
        return PollinationsReadinessReason.DISABLED
    if not credential_present:
        return PollinationsReadinessReason.MISSING_CREDENTIAL
    if credential_valid is False:
        return PollinationsReadinessReason.INVALID_CREDENTIAL
    if credential_valid is None:
        return PollinationsReadinessReason.READINESS_UNKNOWN
    if balance_state is PollinationsBalanceState.ZERO:
        return PollinationsReadinessReason.ZERO_BALANCE
    if model_available is False:
        return PollinationsReadinessReason.MODEL_UNAVAILABLE
    if model_available is None:
        return PollinationsReadinessReason.READINESS_UNKNOWN
    if balance_state is PollinationsBalanceState.UNKNOWN:
        return PollinationsReadinessReason.READINESS_UNKNOWN
    if health_state is PollinationsHealthState.UNHEALTHY:
        return PollinationsReadinessReason.PROVIDER_UNHEALTHY
    return PollinationsReadinessReason.READY


def build_pollinations_readiness_snapshot(
    *,
    enabled: bool,
    credential_present: bool,
    credential_valid: bool | None,
    model: str,
    model_available: bool | None,
    balance_state: PollinationsBalanceState,
    health_state: PollinationsHealthState,
    checked_at: datetime,
    valid_for: timedelta = timedelta(minutes=15),
    readiness_calls: int = 0,
) -> PollinationsReadinessSnapshot:
    reason = _readiness_reason(
        enabled=enabled,
        credential_present=credential_present,
        credential_valid=credential_valid,
        model_available=model_available,
        balance_state=balance_state,
        health_state=health_state,
    )
    return PollinationsReadinessSnapshot(
        enabled=enabled,
        credential_present=credential_present,
        credential_valid=credential_valid,
        model=model,
        model_available=model_available,
        balance_state=balance_state,
        health_state=health_state,
        eligible_for_generation=reason is PollinationsReadinessReason.READY,
        blocking_reason=reason,
        checked_at=checked_at,
        valid_until=checked_at + valid_for,
        readiness_calls=readiness_calls,
    )


def pollinations_readiness_decision(
    snapshot: PollinationsReadinessSnapshot | None,
    *,
    expected_model: str,
    at: datetime | None = None,
) -> PollinationsReadinessDecision:
    if snapshot is None:
        return PollinationsReadinessDecision(
            eligible=False,
            reason=PollinationsReadinessReason.READINESS_UNKNOWN,
            detail="Pollinations has no run-level readiness snapshot",
        )
    if snapshot.model != expected_model:
        return PollinationsReadinessDecision(
            eligible=False,
            reason=PollinationsReadinessReason.MODEL_UNAVAILABLE,
            detail="Pollinations readiness was checked for a different model",
        )
    if not snapshot.is_fresh(at=at):
        return PollinationsReadinessDecision(
            eligible=False,
            reason=PollinationsReadinessReason.READINESS_UNKNOWN,
            detail="Pollinations readiness snapshot is stale and requires explicit refresh",
        )
    return PollinationsReadinessDecision(
        eligible=snapshot.eligible_for_generation,
        reason=snapshot.blocking_reason,
        detail=(
            "Pollinations readiness permits PUBLIC_SAFE Volume overflow"
            if snapshot.eligible_for_generation
            else f"Pollinations skipped before generation: {snapshot.blocking_reason.value}"
        ),
    )


ReadinessSender = Callable[..., Awaitable[Any]]


class PollinationsReadinessChecker:
    """Perform one explicit readiness refresh; never sends prompts or media."""

    def __init__(
        self,
        *,
        config: PollinationsConfig | None = None,
        api_key_resolver: Callable[[], str] | None = None,
        request_sender: ReadinessSender | None = None,
        freshness: timedelta = timedelta(minutes=15),
    ) -> None:
        self.config = config or PollinationsConfig(model="klein")
        self._api_key_resolver = api_key_resolver or self._environment_api_key
        self._request_sender = request_sender or _readiness_get_once
        self._freshness = freshness

    def _environment_api_key(self) -> str:
        return (os.environ.get(self.config.api_key_env) or "").strip()

    async def check(self, *, checked_at: datetime | None = None) -> PollinationsReadinessSnapshot:
        moment = checked_at or datetime.now(timezone.utc)
        key = self._api_key_resolver()
        if not self.config.enabled or not key:
            return build_pollinations_readiness_snapshot(
                enabled=self.config.enabled,
                credential_present=bool(key),
                credential_valid=None,
                model=self.config.model,
                model_available=None,
                balance_state=PollinationsBalanceState.UNKNOWN,
                health_state=PollinationsHealthState.UNKNOWN,
                checked_at=moment,
                valid_for=self._freshness,
            )

        headers = {"Authorization": f"Bearer {key}"}
        calls = 0

        async def fetch(path: str) -> Any | None:
            nonlocal calls
            calls += 1
            try:
                response = await self._request_sender(
                    url=f"{self.config.base_url.rstrip('/')}{path}",
                    headers=headers,
                    timeout_seconds=min(self.config.timeout_seconds, 30.0),
                )
            except Exception:
                return None
            if int(getattr(response, "status_code", 0)) != 200:
                return {"_status": int(getattr(response, "status_code", 0))}
            try:
                return response.json()
            except Exception:
                return None

        key_info = await fetch("/account/key")
        credential_valid = (
            True
            if isinstance(key_info, dict) and key_info.get("valid") is True
            else False
            if isinstance(key_info, dict)
            and (key_info.get("valid") is False or key_info.get("_status") in {401, 403})
            else None
        )
        if credential_valid is not True:
            return build_pollinations_readiness_snapshot(
                enabled=True,
                credential_present=True,
                credential_valid=credential_valid,
                model=self.config.model,
                model_available=None,
                balance_state=PollinationsBalanceState.UNKNOWN,
                health_state=PollinationsHealthState.UNKNOWN,
                checked_at=moment,
                valid_for=self._freshness,
                readiness_calls=calls,
            )

        balance_payload = await fetch("/account/balance")
        balance = balance_payload.get("balance") if isinstance(balance_payload, dict) else None
        balance_state = (
            PollinationsBalanceState.POSITIVE
            if isinstance(balance, (int, float)) and balance > 0
            else PollinationsBalanceState.ZERO
            if isinstance(balance, (int, float)) and balance <= 0
            else PollinationsBalanceState.UNKNOWN
        )
        if balance_state is PollinationsBalanceState.ZERO:
            return build_pollinations_readiness_snapshot(
                enabled=True,
                credential_present=True,
                credential_valid=True,
                model=self.config.model,
                model_available=None,
                balance_state=balance_state,
                health_state=PollinationsHealthState.UNKNOWN,
                checked_at=moment,
                valid_for=self._freshness,
                readiness_calls=calls,
            )
        models_payload = await fetch("/image/models")
        model_available = _model_is_listed(models_payload, self.config.model)
        health_payload = await fetch("/v1/models/status?minutes=60")
        health_state = _model_health(health_payload, self.config.model)
        return build_pollinations_readiness_snapshot(
            enabled=True,
            credential_present=True,
            credential_valid=True,
            model=self.config.model,
            model_available=model_available,
            balance_state=balance_state,
            health_state=health_state,
            checked_at=moment,
            valid_for=self._freshness,
            readiness_calls=calls,
        )


def _rows(payload: Any) -> list[dict[str, Any]] | None:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "models", "rows", "results"):
            if isinstance(payload.get(key), list):
                return [item for item in payload[key] if isinstance(item, dict)]
    return None


def _model_identifier(item: dict[str, Any]) -> str:
    return str(
        item.get("id") or item.get("name") or item.get("model") or item.get("model_id") or ""
    ).lower()


def _model_is_listed(payload: Any, model: str) -> bool | None:
    rows = _rows(payload)
    if rows is None:
        return None
    return any(_model_identifier(item) == model.lower() for item in rows)


def _model_health(payload: Any, model: str) -> PollinationsHealthState:
    rows = _rows(payload)
    if rows is None:
        return PollinationsHealthState.UNKNOWN
    match = next((item for item in rows if _model_identifier(item) == model.lower()), None)
    if match is None:
        return PollinationsHealthState.UNKNOWN
    status = str(match.get("status", "")).lower()
    if status in {"unhealthy", "unavailable", "offline", "down"}:
        return PollinationsHealthState.UNHEALTHY
    if match.get("healthy") is False or match.get("available") is False:
        return PollinationsHealthState.UNHEALTHY
    if status in {"healthy", "available", "online", "up"}:
        return PollinationsHealthState.HEALTHY
    if match.get("healthy") is True or match.get("available") is True:
        return PollinationsHealthState.HEALTHY
    return PollinationsHealthState.UNKNOWN


async def _readiness_get_once(**kwargs: Any) -> httpx.Response:
    async with httpx.AsyncClient(timeout=float(kwargs["timeout_seconds"])) as client:
        return await client.get(kwargs["url"], headers=kwargs["headers"])


__all__ = [
    "PollinationsBalanceState",
    "PollinationsHealthState",
    "PollinationsReadinessChecker",
    "PollinationsReadinessDecision",
    "PollinationsReadinessReason",
    "PollinationsReadinessSnapshot",
    "build_pollinations_readiness_snapshot",
    "pollinations_readiness_decision",
]
