"""Zero-network validation boundary for a future paid BFL + R2 canary.

This module intentionally does not import an S3 or HTTP SDK.  The paid-run
command validates every authorization prerequisite, then stops at the explicit
executor boundary until a separately reviewed live executor is supplied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tella.media.bfl_flux2_provider import BFLFlux2Config
from tella.media.image_provider_contract import ReferenceSheetManifest


AUTHORIZATION_TOKEN = "AUTHORIZE_BFL_R2_CANARY_01"
REQUIRED_R2_VARIABLES = (
    "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME",
)


class CanaryBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    initial_candidates_per_scene: int = Field(ge=1, le=1)
    targeted_candidates_per_failed_scene_max: int = Field(ge=0, le=1)
    bfl_submissions_max: int = Field(ge=1, le=6)
    create_transport_attempts_per_submission: int = Field(ge=1, le=1)
    automatic_retries: int = Field(ge=0, le=0)
    fallbacks: int = Field(ge=0, le=0)
    cloudflare_workers_ai: int = Field(ge=0, le=0)
    gemini: int = Field(ge=0, le=0)
    render_operations: int = Field(ge=0, le=0)


class CanaryTransportPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    private_bucket_status_confirmed: bool
    r2_conditional_write_support_confirmed: bool
    reference_url_ttl_seconds: int
    reference_url_safety_margin_seconds: float
    automatic_retries: int = Field(ge=0, le=0)
    fallbacks: int = Field(ge=0, le=0)


class CanaryConfig(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    schema_version: int = Field(ge=1)
    provider: str
    endpoint: str
    preview_opt_in: bool
    temporary_reference_store: str
    transport_policy: CanaryTransportPolicy
    request_budget: CanaryBudget

    @model_validator(mode="after")
    def controlled_ids(self) -> "CanaryConfig":
        if self.provider != "bfl_flux2_reference":
            raise ValueError("canary provider must be bfl_flux2_reference")
        if self.temporary_reference_store != "cloudflare_r2_private":
            raise ValueError("canary store must be cloudflare_r2_private")
        BFLFlux2Config(
            endpoint=self.endpoint,
            allow_preview=self.preview_opt_in,
            reference_url_ttl_seconds=self.transport_policy.reference_url_ttl_seconds,
            reference_url_safety_margin_seconds=(
                self.transport_policy.reference_url_safety_margin_seconds
            ),
        )
        return self


def load_canary_config(path: Path) -> CanaryConfig:
    return CanaryConfig.model_validate_json(path.read_text(encoding="utf-8"))


def validate_approved_manifest(
    path: Path, *, approval_record_sha256: str, maximum_reference_bytes: int = 20_000_000
) -> ReferenceSheetManifest:
    manifest = ReferenceSheetManifest.model_validate_json(path.read_text(encoding="utf-8"))
    if not manifest.human_approved:
        raise RuntimeError("reference manifest is not human-approved")
    approval_hash = hashlib.sha256(manifest.approval_record.encode("utf-8")).hexdigest()
    if approval_hash != approval_record_sha256.lower():
        raise RuntimeError("approval-record SHA256 mismatch")
    if manifest.image_path.stat().st_size > maximum_reference_bytes:
        raise RuntimeError("approved reference image exceeds local byte-size limit")
    content = manifest.image_path.read_bytes()
    if len(content) > maximum_reference_bytes:
        raise RuntimeError("approved reference image exceeds local byte-size limit")
    if hashlib.sha256(content).hexdigest() != manifest.image_sha256:
        raise RuntimeError("approved reference image SHA256 mismatch")
    return manifest


def validate_paid_prerequisites(
    config: CanaryConfig,
    *,
    manifest_path: Path,
    approval_record_sha256: str,
    authorization_token: str,
) -> dict[str, Any]:
    if authorization_token != AUTHORIZATION_TOKEN:
        raise RuntimeError("explicit paid-canary authorization is missing")
    if not os.environ.get("BFL_API_KEY"):
        raise RuntimeError("BFL credential is missing")
    if any(not os.environ.get(name) for name in REQUIRED_R2_VARIABLES):
        raise RuntimeError("R2 temporary reference credentials are incomplete")
    if not config.transport_policy.private_bucket_status_confirmed:
        raise RuntimeError("R2 private-bucket status is not confirmed")
    if not config.transport_policy.r2_conditional_write_support_confirmed:
        raise RuntimeError("R2 conditional-write support is not confirmed")
    manifest = validate_approved_manifest(
        manifest_path, approval_record_sha256=approval_record_sha256
    )
    return {
        "status": "paid_run_prerequisites_validated",
        "provider": config.provider,
        "temporary_reference_store": config.temporary_reference_store,
        "maximum_submissions": config.request_budget.bfl_submissions_max,
        "reference_sha256": manifest.image_sha256,
        "credentials_present": {"bfl": True, "r2_complete": True},
        "external_calls": 0,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--mode", choices=("validate-only", "paid-run"), required=True)
    parser.add_argument("--reference-manifest", type=Path)
    parser.add_argument("--approval-record-sha256")
    parser.add_argument("--authorization-token", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = load_canary_config(args.config)
    if args.mode == "validate-only":
        print(json.dumps({
            "status": "valid",
            "provider": config.provider,
            "temporary_reference_store": config.temporary_reference_store,
            "maximum_submissions": config.request_budget.bfl_submissions_max,
            "clients_constructed": 0,
            "external_calls": 0,
        }, sort_keys=True))
        return 0
    if args.reference_manifest is None or not args.approval_record_sha256:
        raise SystemExit("paid-run requires explicit manifest and approval-record SHA256")
    result = validate_paid_prerequisites(
        config,
        manifest_path=args.reference_manifest,
        approval_record_sha256=args.approval_record_sha256,
        authorization_token=args.authorization_token,
    )
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(
        "paid-run prerequisites passed; live executor is intentionally not installed"
    )


if __name__ == "__main__":
    raise SystemExit(main())
