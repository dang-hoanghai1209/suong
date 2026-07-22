"""Stable provider identities shared by planning and provider adapters."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ProviderKind(StrEnum):
    """Provider identities; an identity does not authorize its use for a scene."""

    LOCAL_COMPOSITOR = "local_compositor"
    CLOUDFLARE_KLEIN_4B = "cloudflare_klein_4b"
    POLLINATIONS = "pollinations"


class ProviderDescriptor(BaseModel):
    """Non-operational capabilities used by routing and accounting."""

    model_config = ConfigDict(frozen=True)

    kind: ProviderKind
    external: bool
    consumes_ai_call: bool
    paid: bool = False


def describe_provider(kind: ProviderKind) -> ProviderDescriptor:
    """Return explicit provider properties without constructing a live adapter."""

    if kind is ProviderKind.LOCAL_COMPOSITOR:
        return ProviderDescriptor(kind=kind, external=False, consumes_ai_call=False)
    if kind is ProviderKind.CLOUDFLARE_KLEIN_4B:
        return ProviderDescriptor(kind=kind, external=True, consumes_ai_call=True)
    if kind is ProviderKind.POLLINATIONS:
        return ProviderDescriptor(kind=kind, external=True, consumes_ai_call=True)
    raise ValueError(f"unsupported provider kind: {kind}")


__all__ = ["ProviderDescriptor", "ProviderKind", "describe_provider"]
