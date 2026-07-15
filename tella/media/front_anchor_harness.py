"""Fail-closed planning and accounting for a bounded front-anchor harness."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tella.media import ai_image


FRONT_ROLE = "front_portrait"
LIVE_AUTHORIZATION_TOKEN = "AUTHORIZE_FRONT_ANCHOR_GENERATION_01"
INITIAL_FRONT_CANDIDATES = 3
TARGETED_FRONT_CANDIDATES = 0
TOTAL_FRONT_SUBMISSIONS = 3
OUTPUT_ROOT_PREFIX = Path("out") / "character_reference_bootstrap"


class FrontHarnessPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    session_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")
    provider_id: Literal["cloudflare", "bfl_flux_1_1_pro_front_anchor"]
    model: str = Field(min_length=1, max_length=160)
    character_id: Literal["practical_young_adult_male_teal_v1"]
    character_fingerprint: str
    generation_spec_version: Literal[1]
    asset_role: Literal["front_portrait"]
    prompt: str = Field(min_length=1)
    prompt_sha256: str
    width: Literal[768]
    height: Literal[1024]
    output_mime_type: Literal["image/png"]
    output_root: Path
    maximum_output_bytes: int = Field(default=20_000_000, ge=1024, le=100_000_000)
    initial_candidates_max: Literal[3] = 3
    targeted_candidates_max: Literal[0] = 0
    total_submissions_max: Literal[3] = 3
    automatic_retries: Literal[0] = 0
    fallbacks: Literal[0] = 0
    adapter_exact_dimensions_proven: Literal[False] = False
    adapter_retry_control: Literal["provider_managed"] = "provider_managed"
    adapter_max_attempts_per_account: int = Field(ge=1)
    adapter_max_accounts: int | None = Field(default=None, ge=1)
    seed_supported: Literal[True] = True
    request_id_available: Literal[False] = False

    @field_validator("character_fingerprint", "prompt_sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("expected a full SHA256 hex digest")
        return normalized

    @model_validator(mode="after")
    def contract(self) -> "FrontHarnessPlan":
        if hashlib.sha256(self.prompt.encode("utf-8")).hexdigest() != self.prompt_sha256:
            raise ValueError("front prompt SHA256 mismatch")
        if self.total_submissions_max != self.initial_candidates_max + self.targeted_candidates_max:
            raise ValueError("front submission budget components do not match total")
        if self.output_root.is_absolute() or ".." in self.output_root.parts:
            raise ValueError("front output root must be repository-relative")
        expected_prefix = OUTPUT_ROOT_PREFIX.parts
        if self.output_root.parts[: len(expected_prefix)] != expected_prefix:
            raise ValueError("front output root must be under the ignored bootstrap directory")
        if len(self.prompt.encode("utf-8")) > 2000:
            raise ValueError("front prompt exceeds the local provider UTF-8 limit")
        return self


class FrontCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_number: Literal[1, 2, 3]
    candidate_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")
    asset_role: Literal["front_portrait"]
    output_path: Path
    targeted: Literal[False] = False
    seed: int


class FrontSubmissionAccounting(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    submissions: int = Field(default=0, ge=0, le=3)
    transport_attempts: int = Field(default=0, ge=0, le=3)
    automatic_retries: Literal[0] = 0
    fallbacks: Literal[0] = 0

    @model_validator(mode="after")
    def attempts_match(self) -> "FrontSubmissionAccounting":
        if self.transport_attempts != self.submissions:
            raise ValueError("transport attempts must equal front submissions")
        return self


class FrontHarnessBlocked(RuntimeError):
    """The live boundary cannot satisfy the exact approved contract."""


def build_front_plan(
    *,
    session_id: str,
    character_fingerprint: str,
    prompt: str,
    prompt_sha256: str,
    generation_spec_version: int,
    repository_root: Path,
) -> FrontHarnessPlan:
    return FrontHarnessPlan(
        schema_version=1,
        session_id=session_id,
        provider_id="cloudflare",
        model=ai_image.DEFAULT_MODEL,
        character_id="practical_young_adult_male_teal_v1",
        character_fingerprint=character_fingerprint,
        generation_spec_version=generation_spec_version,
        asset_role=FRONT_ROLE,
        prompt=prompt,
        prompt_sha256=prompt_sha256,
        width=768,
        height=1024,
        output_mime_type="image/png",
        output_root=(Path("out") / "character_reference_bootstrap" / session_id),
        adapter_exact_dimensions_proven=False,
        adapter_retry_control="provider_managed",
        adapter_max_attempts_per_account=ai_image.MAX_RETRIES_PER_ACCOUNT,
        adapter_max_accounts=None,
    )


def plan_initial_front_candidates(plan: FrontHarnessPlan) -> tuple[FrontCandidateRequest, ...]:
    if plan.targeted_candidates_max != 0 or plan.total_submissions_max != 3:
        raise FrontHarnessBlocked("initial front harness contract is not exactly three candidates")
    return tuple(
        FrontCandidateRequest(
            candidate_number=number,
            candidate_id=f"{plan.session_id}_candidate_{number:02d}",
            asset_role=FRONT_ROLE,
            output_path=plan.output_root / f"candidate_{number:02d}.png",
            seed=10_000 + number,
        )
        for number in range(1, INITIAL_FRONT_CANDIDATES + 1)
    )


def record_front_submission(
    accounting: FrontSubmissionAccounting,
) -> FrontSubmissionAccounting:
    if accounting.submissions >= TOTAL_FRONT_SUBMISSIONS:
        raise FrontHarnessBlocked("front initial submission budget exhausted")
    next_count = accounting.submissions + 1
    return FrontSubmissionAccounting(submissions=next_count, transport_attempts=next_count)


def validate_output_root(plan: FrontHarnessPlan, *, repository_root: Path) -> Path:
    root = repository_root.resolve()
    resolved = (root / plan.output_root).resolve()
    if not resolved.is_relative_to(root / OUTPUT_ROOT_PREFIX):
        raise ValueError("front output path escapes the ignored bootstrap directory")
    return resolved


def validate_live_front(
    plan: FrontHarnessPlan,
    *,
    repository_root: Path,
    authorization_token: str,
    clean_worktree: bool = True,
) -> None:
    """Validate the live boundary without constructing a provider or reading dotenv."""
    validate_output_root(plan, repository_root=repository_root)
    if authorization_token != LIVE_AUTHORIZATION_TOKEN:
        raise FrontHarnessBlocked("exact front-generation authorization is required")
    if not _process_credentials_present():
        raise FrontHarnessBlocked("Cloudflare process credentials are missing")
    if not clean_worktree:
        raise FrontHarnessBlocked("front generation requires a clean source worktree")
    if not plan.adapter_exact_dimensions_proven:
        raise FrontHarnessBlocked(
            "Cloudflare adapter cannot prove exact 768x1024 output; live front generation is blocked"
        )
    if plan.adapter_retry_control != "caller_bounded" or plan.adapter_max_attempts_per_account != 1:
        raise FrontHarnessBlocked(
            "Cloudflare adapter retry behavior cannot satisfy one-attempt front budget"
        )


def _process_credentials_present() -> bool:
    accounts = (os.environ.get("CF_ACCOUNTS") or "").strip()
    if accounts:
        return True
    return bool(
        (os.environ.get("CF_ACCOUNT_ID") or "").strip()
        and (os.environ.get("CF_AI_TOKEN") or "").strip()
    )


def cloudflare_adapter_audit() -> dict[str, object]:
    """Return facts read from the actual local Cloudflare adapter, no I/O."""
    return {
        "model": ai_image.DEFAULT_MODEL,
        "payload_fields": ["prompt", "steps", "width", "height", "seed_optional"],
        "width_height_sent": True,
        "exact_768x1024_proven": False,
        "model_training_note": "adapter documentation states FLUX trains at 1024x1024",
        "output_accepts": ["raw response bytes", "base64 image in JSON"],
        "output_mime_validation": False,
        "output_dimension_validation": False,
        "response_byte_limit": None,
        "timeout_seconds": ai_image.HTTP_TIMEOUT,
        "provider_default_attempts_per_account": ai_image.MAX_RETRIES_PER_ACCOUNT,
        "account_rotation": True,
        "safety_retry": True,
        "seed_supported": True,
        "request_id_available": False,
        "submission_accounting": "no adapter submission counter; request hook only",
        "caller_fallback_possible": True,
    }


__all__ = [
    "FRONT_ROLE",
    "INITIAL_FRONT_CANDIDATES",
    "LIVE_AUTHORIZATION_TOKEN",
    "FrontCandidateRequest",
    "FrontHarnessBlocked",
    "FrontHarnessPlan",
    "FrontSubmissionAccounting",
    "build_front_plan",
    "cloudflare_adapter_audit",
    "plan_initial_front_candidates",
    "record_front_submission",
    "validate_live_front",
    "validate_output_root",
]
