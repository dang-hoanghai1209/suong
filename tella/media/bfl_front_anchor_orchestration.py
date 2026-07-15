"""Bounded live orchestration for the three-candidate BFL front canary."""
from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from tella.atomic_write import atomic_write_json
from tella.media.bfl_front_anchor_provider import (
    AUTHORIZATION_TOKEN,
    BFLFrontAnchorError,
    BFLFrontAnchorRequest,
    PROVIDER_ID,
)


REQUIRED_BRANCH = "feature/reference-conditioned-image-provider"
CHARACTER_ID = "practical_young_adult_male_teal_v1"
CHARACTER_FINGERPRINT = "4bb86c902dfedba848ad8ae43ef6dbd0bb41059be7fa1af816ecd85cc28fba5f"
SEEDS = (17001, 17002, 17003)
OUTPUT_PREFIX = Path("out") / "character_reference_bootstrap"


class LiveFrontBlocked(RuntimeError):
    pass


class LiveFrontPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+$", min_length=1, max_length=120)
    character_id: Literal["practical_young_adult_male_teal_v1"]
    character_fingerprint: str
    canonical_spec_version: Literal[1]
    generation_spec_version: Literal[1]
    prompt: str = Field(min_length=1)
    prompt_sha256: str
    asset_role: Literal["front_portrait"]
    width: Literal[768]
    height: Literal[1024]
    output_format: Literal["png"]
    prompt_upsampling: Literal[False]
    seeds: tuple[Literal[17001], Literal[17002], Literal[17003]]
    maximum_submissions: Literal[3]
    targeted_submissions: Literal[0]
    retries: Literal[0]
    fallbacks: Literal[0]
    output_root: Path

    @field_validator("character_fingerprint", "prompt_sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        normalized = value.lower()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("expected SHA256")
        return normalized

    @model_validator(mode="after")
    def fixed_contract(self) -> "LiveFrontPlan":
        if self.character_fingerprint != CHARACTER_FINGERPRINT:
            raise ValueError("canonical character fingerprint mismatch")
        if hashlib.sha256(self.prompt.encode()).hexdigest() != self.prompt_sha256:
            raise ValueError("front prompt SHA256 mismatch")
        if self.seeds != SEEDS:
            raise ValueError("front seeds differ from fixed canary plan")
        expected = OUTPUT_PREFIX / self.session_id
        if self.output_root != expected or self.output_root.is_absolute() or ".." in self.output_root.parts:
            raise ValueError("unsafe front session output root")
        return self


class CandidateState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    seed: int
    status: Literal[
        "planned", "in_progress", "completed", "failed", "cancelled",
        "not_attempted", "not_attempted_due_to_fail_closed",
    ] = "planned"
    request_id: str | None = None
    image_filename: str
    image_sha256: str | None = None
    mime: str | None = None
    dimensions: list[int] | None = None
    byte_size: int | None = None
    safe_failure_category: str | None = None
    create_submissions: int = 0
    create_attempts: int = 0
    poll_operations: int = 0
    result_downloads: int = 0
    retries: Literal[0] = 0
    fallbacks: Literal[0] = 0


class LiveFrontManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    session_id: str
    character_id: str
    character_fingerprint: str
    canonical_spec_version: int
    generation_spec_version: int
    prompt_sha256: str
    provider_id: Literal["bfl_flux_1_1_pro_front_anchor"] = PROVIDER_ID
    endpoint_path: Literal["/v1/flux-pro-1.1"] = "/v1/flux-pro-1.1"
    authorization_verified: Literal[True] = True
    credential_present: Literal[True] = True
    session_state: Literal[
        "running", "completed_candidates", "completed_awaiting_human_review",
        "partial_failed", "cancelled",
    ] = "running"
    started_timestamp: str
    finalized_timestamp: str | None = None
    candidates: list[CandidateState]
    total_accounting: dict[str, int] = Field(default_factory=dict)
    stop_reason: str | None = None
    automatic_selection: Literal[False] = False
    stage_b_requested: Literal[False] = False
    review_artifacts_created: bool = False

    @model_validator(mode="after")
    def bounded(self) -> "LiveFrontManifest":
        submissions = sum(row.create_submissions for row in self.candidates)
        attempts = sum(row.create_attempts for row in self.candidates)
        downloads = sum(row.result_downloads for row in self.candidates)
        if submissions > 3 or attempts > 3 or downloads > 3:
            raise ValueError("front canary accounting exceeds fixed budget")
        serialized = json.dumps(self.model_dump(mode="json"), sort_keys=True).lower()
        forbidden = ("://", "authorization", "x-key", "cookie", "api_key", "bearer ")
        if any(item in serialized for item in forbidden):
            # The safe boolean field is explicitly allowed.
            sanitized = serialized.replace('"authorization_verified": true', "")
            if any(item in sanitized for item in forbidden):
                raise ValueError("front session manifest contains forbidden material")
        return self


@dataclass(frozen=True)
class RepositoryState:
    branch: str
    tracked_clean: bool
    staged_zero: bool


class CandidateProvider(Protocol):
    accounting: dict[str, int]

    async def generate(self, request: BFLFrontAnchorRequest, out_path: Path) -> Any: ...


@dataclass
class ProviderBundle:
    provider: CandidateProvider
    close: Callable[[], Any]


@dataclass(frozen=True)
class LiveFrontResult:
    exit_code: int
    status: str
    manifest_path: Path | None


def repository_state(root: Path) -> RepositoryState:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()

    branch = git("branch", "--show-current")
    tracked = git("status", "--porcelain", "--untracked-files=no") == ""
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=root, check=False
    ).returncode == 0
    return RepositoryState(branch=branch, tracked_clean=tracked, staged_zero=staged)


def validate_non_secret_gates(
    *, plan: LiveFrontPlan, repository_root: Path, authorization_token: str,
    state_reader: Callable[[Path], RepositoryState] = repository_state,
) -> Path:
    if authorization_token != AUTHORIZATION_TOKEN:
        raise LiveFrontBlocked("exact BFL front-anchor authorization is required")
    root = repository_root.resolve(strict=True)
    if not (root / ".git").exists():
        raise LiveFrontBlocked("repository root is invalid")
    state = state_reader(root)
    if state.branch != REQUIRED_BRANCH:
        raise LiveFrontBlocked("source branch is not approved")
    if not state.tracked_clean or not state.staged_zero:
        raise LiveFrontBlocked("source worktree must be clean with zero staged changes")
    # Pydantic validates identity, versions, prompt, view, dimensions, format,
    # upsampling, seeds, budgets, retries, fallbacks, and session-name policy.
    output = (root / plan.output_root).resolve()
    approved = (root / OUTPUT_PREFIX).resolve()
    if not output.is_relative_to(approved):
        raise LiveFrontBlocked("front output path escapes approved root")
    for parent in (output, *output.parents):
        if parent == approved.parent:
            break
        if parent.exists() and parent.is_symlink():
            raise LiveFrontBlocked("front output path contains a symlink")
    if output.exists() and any(output.iterdir()):
        raise LiveFrontBlocked("front output session already exists or is foreign-owned")
    return output


async def execute_live_front(
    *, plan: LiveFrontPlan, repository_root: Path, authorization_token: str,
    credential_reader: Callable[[], SecretStr | None],
    provider_factory: Callable[[SecretStr], ProviderBundle],
    state_reader: Callable[[Path], RepositoryState] = repository_state,
    review_finalizer: Callable[[LiveFrontPlan, LiveFrontManifest, Path], None] | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> LiveFrontResult:
    try:
        output = validate_non_secret_gates(
            plan=plan, repository_root=repository_root,
            authorization_token=authorization_token, state_reader=state_reader,
        )
    except Exception:
        return LiveFrontResult(exit_code=2, status="blocked_no_execution", manifest_path=None)

    credential = credential_reader()
    if credential is None or not credential.get_secret_value():
        return LiveFrontResult(exit_code=2, status="blocked_no_execution", manifest_path=None)
    bundle = provider_factory(credential)
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "candidates_manifest.json"
    rows = [
        CandidateState(candidate_id=f"candidate_{index:02d}", seed=seed,
                       image_filename=f"candidate_{index:02d}.png")
        for index, seed in enumerate(SEEDS, 1)
    ]
    manifest = LiveFrontManifest(
        session_id=plan.session_id, character_id=plan.character_id,
        character_fingerprint=plan.character_fingerprint,
        canonical_spec_version=plan.canonical_spec_version,
        generation_spec_version=plan.generation_spec_version,
        prompt_sha256=plan.prompt_sha256,
        started_timestamp=now().isoformat(), candidates=rows,
    )
    _write_manifest(manifest_path, manifest)
    try:
        for index, row in enumerate(manifest.candidates):
            row.status = "in_progress"
            destination = output / row.image_filename
            if destination.exists():
                raise LiveFrontBlocked("candidate output already exists")
            before = dict(bundle.provider.accounting)
            try:
                result = await bundle.provider.generate(
                    BFLFrontAnchorRequest(prompt=plan.prompt, seed=row.seed), destination
                )
            except asyncio.CancelledError:
                row.status = "cancelled"
                row.safe_failure_category = "cancelled"
                _apply_accounting(row, before, bundle.provider.accounting)
                _mark_later(manifest, index, "not_attempted")
                manifest.session_state = "cancelled"
                manifest.stop_reason = "operator_cancelled"
                manifest.finalized_timestamp = now().isoformat()
                manifest.total_accounting = dict(bundle.provider.accounting)
                _write_manifest(manifest_path, manifest)
                raise
            except Exception as exc:
                row.status = "failed"
                row.safe_failure_category = (
                    exc.category if isinstance(exc, BFLFrontAnchorError) else "provider_failure"
                )
                _apply_accounting(row, before, bundle.provider.accounting)
                _mark_later(manifest, index, "not_attempted_due_to_fail_closed")
                manifest.session_state = "partial_failed"
                manifest.stop_reason = row.safe_failure_category
                manifest.finalized_timestamp = now().isoformat()
                manifest.total_accounting = dict(bundle.provider.accounting)
                _write_manifest(manifest_path, manifest)
                return LiveFrontResult(3, "partial_failed", manifest_path)
            content = destination.read_bytes()
            metadata = result.metadata
            row.status = "completed"
            row.request_id = metadata.get("request_id")
            row.image_sha256 = hashlib.sha256(content).hexdigest()
            row.mime = "image/png"
            row.dimensions = [768, 1024]
            row.byte_size = len(content)
            _apply_accounting(row, before, bundle.provider.accounting)
            manifest.total_accounting = dict(bundle.provider.accounting)
            _write_manifest(manifest_path, manifest)
        manifest.session_state = "completed_candidates"
        if review_finalizer is not None:
            review_finalizer(plan, manifest, output)
            manifest.session_state = "completed_awaiting_human_review"
            manifest.review_artifacts_created = True
        manifest.finalized_timestamp = now().isoformat()
        _write_manifest(manifest_path, manifest)
        return LiveFrontResult(0, manifest.session_state, manifest_path)
    finally:
        closed = bundle.close()
        if asyncio.iscoroutine(closed):
            await closed


def _apply_accounting(row: CandidateState, before: dict[str, int], after: dict[str, int]) -> None:
    def delta(key: str) -> int:
        return max(0, int(after.get(key, 0)) - int(before.get(key, 0)))
    row.create_submissions = delta("application_image_submissions")
    row.create_attempts = delta("bfl_create_attempts")
    row.poll_operations = delta("bfl_poll_attempts")
    row.result_downloads = delta("bfl_result_download_attempts")


def _mark_later(manifest: LiveFrontManifest, index: int, status: str) -> None:
    for later in manifest.candidates[index + 1:]:
        later.status = status


def _write_manifest(path: Path, manifest: LiveFrontManifest) -> None:
    atomic_write_json(path, manifest.model_dump(mode="json"), ensure_ascii=False)


__all__ = [
    "CHARACTER_FINGERPRINT", "LiveFrontBlocked", "LiveFrontManifest",
    "LiveFrontPlan", "LiveFrontResult", "ProviderBundle", "RepositoryState",
    "SEEDS", "execute_live_front", "repository_state", "validate_non_secret_gates",
]
