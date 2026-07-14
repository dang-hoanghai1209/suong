"""R2-only reference-transport canary validation and live-executor gate.

Validate-only mode performs no client construction or network activity.  Live
mode validates every prerequisite, builds a redacted deterministic plan, and
then stops until a separately reviewed R2 executor is injected.  This module
does not import an image-generation provider.
"""
from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import os
import struct
import zlib
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


AUTHORIZATION_TOKEN = "AUTHORIZE_R2_REFERENCE_TRANSPORT_CANARY_01"
REQUIRED_R2_VARIABLES = (
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET_NAME",
)
REQUIRED_CLEANUP_BRANCHES = frozenset({
    "upload_ambiguity",
    "presign_failure",
    "verification_download_failure",
    "hash_mismatch",
    "mime_or_decoding_failure",
    "conditional_write_test_failure",
    "cancellation",
    "normal_success",
})


class R2CanaryImageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    width: Literal[64, 128]
    height: Literal[64, 128]
    mime_type: Literal["image/png"]
    pattern: Literal["tella_r2_canary_geometry_v1"]
    maximum_bytes: int = Field(ge=1024, le=1_000_000)


class R2CanaryTransportPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    presigned_get_ttl_seconds: int = Field(ge=60, le=900)
    private_bucket_status_confirmed: bool
    conditional_write_test_confirmed: bool
    https_presigned_get_required: Literal[True]


class R2CanaryBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    immutable_upload_attempts_max: int = Field(ge=3, le=3)
    verification_downloads_max: int = Field(ge=1, le=1)
    presign_operations_max: int = Field(ge=1, le=1)
    cleanup_attempts_max: int = Field(ge=1, le=2)
    automatic_retries: int = Field(ge=0, le=0)
    fallbacks: int = Field(ge=0, le=0)
    bfl_calls: int = Field(ge=0, le=0)
    cloudflare_workers_ai_calls: int = Field(ge=0, le=0)
    gemini_calls: int = Field(ge=0, le=0)
    render_operations: int = Field(ge=0, le=0)


class R2ReferenceTransportCanaryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    status: str
    mode: Literal["r2_reference_transport_only"]
    temporary_reference_store: Literal["cloudflare_r2_private"]
    test_image: R2CanaryImageConfig
    transport_policy: R2CanaryTransportPolicy
    request_budget: R2CanaryBudget
    cleanup_required_on: tuple[str, ...]
    persistent_metadata_allowlist: tuple[str, ...]
    blocking_prerequisites: tuple[str, ...]

    @model_validator(mode="after")
    def validate_cleanup_contract(self) -> "R2ReferenceTransportCanaryConfig":
        if set(self.cleanup_required_on) != REQUIRED_CLEANUP_BRANCHES:
            raise ValueError("R2 canary cleanup policy does not cover every terminal branch")
        if len(self.cleanup_required_on) != len(REQUIRED_CLEANUP_BRANCHES):
            raise ValueError("R2 canary cleanup policy contains duplicate branches")
        return self


def load_canary_config(path: Path) -> R2ReferenceTransportCanaryConfig:
    return R2ReferenceTransportCanaryConfig.model_validate_json(
        path.read_text(encoding="utf-8")
    )


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = binascii.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def deterministic_test_png(config: R2CanaryImageConfig) -> bytes:
    """Return stable, non-sensitive geometric PNG bytes without touching disk."""
    rows = bytearray()
    colors = (
        (31, 139, 135),
        (244, 162, 97),
        (38, 70, 83),
        (233, 196, 106),
    )
    for y in range(config.height):
        rows.append(0)
        for x in range(config.width):
            quadrant = (2 if y >= config.height // 2 else 0) + (
                1 if x >= config.width // 2 else 0
            )
            accent = 12 if (x + y) % 16 < 2 else 0
            color = colors[quadrant]
            rows.extend(min(255, channel + accent) for channel in color)
    header = struct.pack(">IIBBBBB", config.width, config.height, 8, 2, 0, 0, 0)
    content = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(rows), level=9))
        + _png_chunk(b"IEND", b"")
    )
    if len(content) > config.maximum_bytes:
        raise RuntimeError("deterministic R2 canary PNG exceeds configured byte limit")
    return content


def deterministic_image_diagnostic(
    config: R2ReferenceTransportCanaryConfig,
) -> dict[str, Any]:
    content = deterministic_test_png(config.test_image)
    return {
        "source_sha256": hashlib.sha256(content).hexdigest(),
        "byte_size": len(content),
        "mime_type": config.test_image.mime_type,
        "dimensions": [config.test_image.width, config.test_image.height],
        "pattern": config.test_image.pattern,
    }


def redact_presigned_url(url: str) -> dict[str, str]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise RuntimeError("R2 canary presigned URL must use HTTPS")
    return {"url_scheme": parsed.scheme, "url_host": parsed.hostname}


def validate_live_prerequisites(
    config: R2ReferenceTransportCanaryConfig, *, authorization_token: str
) -> dict[str, Any]:
    if authorization_token != AUTHORIZATION_TOKEN:
        raise RuntimeError("explicit R2 transport-canary authorization is missing")
    if any(not os.environ.get(name) for name in REQUIRED_R2_VARIABLES):
        raise RuntimeError("R2 transport-canary credentials are incomplete")
    if not config.transport_policy.private_bucket_status_confirmed:
        raise RuntimeError("R2 private-bucket status is not confirmed")
    if not config.transport_policy.conditional_write_test_confirmed:
        raise RuntimeError("R2 IfNoneMatch test authorization is not confirmed")
    return {
        "credentials_present": True,
        "private_bucket_status_confirmed": True,
        "conditional_write_test_confirmed": True,
        "authorization_valid": True,
    }


def proposed_live_operation(config: R2ReferenceTransportCanaryConfig) -> dict[str, Any]:
    return {
        "mode": config.mode,
        "test_image": deterministic_image_diagnostic(config),
        "steps": [
            "construct injected R2 S3-compatible client",
            "upload exact bytes with IfNoneMatch=*",
            "presign short-lived HTTPS GET",
            "download and verify exact bytes, MIME, PNG decode, and dimensions",
            "repeat conditional write and record observed conflict behavior",
            "verify identical existing object follows borrowed-object policy",
            "exercise conflicting content without overwriting the original",
            "delete the owned object and confirm absence",
        ],
        "cleanup_required_on": list(config.cleanup_required_on),
        "request_budget": config.request_budget.model_dump(),
        "external_calls": 0,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--mode", choices=("validate-only", "live-r2"), required=True)
    parser.add_argument("--authorization-token", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = load_canary_config(args.config)
    if args.mode == "validate-only":
        image = deterministic_image_diagnostic(config)
        print(json.dumps({
            "status": "valid",
            "mode": config.mode,
            "clients_constructed": 0,
            "external_calls": 0,
            "test_image": image,
            "request_budget": config.request_budget.model_dump(),
            "live_execution_blocked": True,
        }, sort_keys=True))
        return 0
    prerequisites = validate_live_prerequisites(
        config, authorization_token=args.authorization_token
    )
    print(json.dumps({
        "status": "live_prerequisites_validated",
        "prerequisites": prerequisites,
        "plan": proposed_live_operation(config),
        "clients_constructed": 0,
        "external_calls": 0,
    }, sort_keys=True))
    raise SystemExit(
        "live R2 executor is intentionally not installed; no client was constructed"
    )


if __name__ == "__main__":
    raise SystemExit(main())
