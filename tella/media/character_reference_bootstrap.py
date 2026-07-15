"""Fail-closed, provider-independent character-reference bootstrap workflow."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tella.media.character_reference_package import ATOMIC_VIEW_ORDER
from tella.media.image_provider_contract import ImageProviderCapabilities


FRONT_ROLE = "front_portrait"
REMAINING_VIEW_ORDER = ATOMIC_VIEW_ORDER[1:]
BOOTSTRAP_GLOBAL_SUBMISSION_MAX = 12


class BootstrapState(StrEnum):
    awaiting_front_generation = "awaiting_front_generation"
    awaiting_front_selection = "awaiting_front_selection"
    front_anchor_locked = "front_anchor_locked"
    generating_remaining_views = "generating_remaining_views"
    awaiting_atomic_qc = "awaiting_atomic_qc"
    atomic_views_accepted = "atomic_views_accepted"
    master_assembled = "master_assembled"
    awaiting_package_approval = "awaiting_package_approval"
    package_approved = "package_approved"
    blocked = "blocked"


class ViewSubmissionBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    initial_max: int = Field(ge=1)
    targeted_additional_max: int = Field(ge=0)
    total_max: int = Field(ge=1)

    @model_validator(mode="after")
    def totals_match(self) -> "ViewSubmissionBudget":
        if self.initial_max + self.targeted_additional_max != self.total_max:
            raise ValueError("view submission budget components must equal total_max")
        return self


VIEW_BUDGETS = {
    FRONT_ROLE: ViewSubmissionBudget(initial_max=3, targeted_additional_max=2, total_max=5),
    "three_quarter_portrait": ViewSubmissionBudget(
        initial_max=1, targeted_additional_max=1, total_max=2
    ),
    "side_profile": ViewSubmissionBudget(
        initial_max=1, targeted_additional_max=1, total_max=2
    ),
    "full_body_neutral": ViewSubmissionBudget(
        initial_max=1, targeted_additional_max=2, total_max=3
    ),
}


class ViewSubmissionCount(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    initial: int = Field(default=0, ge=0)
    targeted: int = Field(default=0, ge=0)

    @property
    def total(self) -> int:
        return self.initial + self.targeted


class SubmissionAccounting(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    per_view: dict[str, ViewSubmissionCount] = Field(
        default_factory=lambda: {role: ViewSubmissionCount() for role in ATOMIC_VIEW_ORDER}
    )
    total_image_submissions: int = Field(default=0, ge=0, le=BOOTSTRAP_GLOBAL_SUBMISSION_MAX)
    transport_attempts: int = Field(default=0, ge=0, le=BOOTSTRAP_GLOBAL_SUBMISSION_MAX)
    automatic_retries: Literal[0] = 0
    fallbacks: Literal[0] = 0

    @model_validator(mode="after")
    def accounting_is_exact(self) -> "SubmissionAccounting":
        if tuple(self.per_view) != ATOMIC_VIEW_ORDER:
            raise ValueError("submission accounting must use deterministic atomic-view order")
        if sum(item.total for item in self.per_view.values()) != self.total_image_submissions:
            raise ValueError("per-view submission totals do not match global total")
        if self.transport_attempts != self.total_image_submissions:
            raise ValueError("transport attempts must equal application submissions")
        for role, count in self.per_view.items():
            budget = VIEW_BUDGETS[role]
            if count.initial > budget.initial_max:
                raise ValueError(f"{role} initial submission budget exceeded")
            if count.targeted > budget.targeted_additional_max:
                raise ValueError(f"{role} targeted submission budget exceeded")
            if count.total > budget.total_max:
                raise ValueError(f"{role} total submission budget exceeded")
        return self


class BootstrapCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9_.-]+$")
    asset_role: Literal[
        "front_portrait", "three_quarter_portrait", "side_profile", "full_body_neutral"
    ]
    candidate_number: int = Field(ge=1)
    targeted: bool
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=160)
    request_id: str = Field(min_length=1, max_length=200)
    prompt_sha256: str
    image_sha256: str
    mime_type: Literal["image/png"]
    width: Literal[768]
    height: Literal[1024]
    rejection_reasons: tuple[str, ...] = ()

    @field_validator("prompt_sha256", "image_sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        return _sha256(value)

    @field_validator("provider", "model", "request_id")
    @classmethod
    def no_remote_or_secret_material(cls, value: str) -> str:
        lowered = value.lower()
        if "://" in value or any(
            marker in lowered
            for marker in ("authorization", "bearer ", "api_key", "secret_access_key")
        ):
            raise ValueError("bootstrap provenance must not contain URLs or credentials")
        return value


class HumanFrontSelectionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    selected_candidate_id: str = Field(
        min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9_.-]+$"
    )
    selected_image_sha256: str
    selector_role: str = Field(min_length=1, max_length=120)
    selected_at: datetime
    decision: Literal["bootstrap_identity_anchor"]
    final_package_approval: Literal[False] = False

    @field_validator("selected_image_sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        return _sha256(value)


class AtomicViewRequestPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_role: Literal[
        "front_portrait", "three_quarter_portrait", "side_profile", "full_body_neutral"
    ]
    stage: Literal["bootstrap_front", "reference_conditioned"]
    prompt: str = Field(min_length=1)
    negative_constraints: tuple[str, ...]
    prompt_sha256: str
    width: Literal[768]
    height: Literal[1024]
    output_mime_type: Literal["image/png"]
    anchor_sha256: str | None = None
    automatic_retries: Literal[0] = 0
    fallbacks: Literal[0] = 0

    @field_validator("prompt_sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        return _sha256(value)

    @model_validator(mode="after")
    def stage_binding(self) -> "AtomicViewRequestPlan":
        expected = hashlib.sha256(self.prompt.encode("utf-8")).hexdigest()
        if self.prompt_sha256 != expected:
            raise ValueError("request-plan prompt SHA256 mismatch")
        if self.asset_role == FRONT_ROLE:
            if self.stage != "bootstrap_front" or self.anchor_sha256 is not None:
                raise ValueError("front bootstrap plan must not use a reference anchor")
        else:
            if self.stage != "reference_conditioned" or self.anchor_sha256 is None:
                raise ValueError("remaining-view plan requires the locked front anchor")
            _sha256(self.anchor_sha256)
        return self


class AcceptedAtomicAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_role: Literal[
        "front_portrait", "three_quarter_portrait", "side_profile", "full_body_neutral"
    ]
    candidate_id: str
    image_sha256: str
    anatomy_qc_passed: Literal[True]
    style_qc_passed: Literal[True]
    identity_qc_passed: Literal[True]
    human_qc_passed: Literal[True]
    bootstrap_identity_anchor: bool = False

    @field_validator("image_sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        return _sha256(value)


class MasterDerivationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    assembly: Literal["deterministic_local_exact_2x2_v1"]
    source_sha256: tuple[str, str, str, str]
    master_sha256: str
    external_calls: Literal[0] = 0

    @field_validator("source_sha256")
    @classmethod
    def source_digests(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_sha256(value) for value in values)

    @field_validator("master_sha256")
    @classmethod
    def master_digest(cls, value: str) -> str:
        return _sha256(value)


class CharacterReferenceBootstrapManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    bootstrap_session_id: str = Field(
        min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9_.-]+$"
    )
    character_fingerprint: str
    generation_spec_version: Literal[1]
    state: BootstrapState = BootstrapState.awaiting_front_generation
    front_candidates: tuple[BootstrapCandidate, ...] = ()
    selected_candidate_id: str | None = None
    selected_front_anchor_sha256: str | None = None
    human_selection_record: HumanFrontSelectionRecord | None = None
    anchor_lock_timestamp: datetime | None = None
    per_view_request_plans: dict[str, AtomicViewRequestPlan] = Field(default_factory=dict)
    per_view_candidate_attempts: dict[str, tuple[BootstrapCandidate, ...]] = Field(
        default_factory=lambda: {role: () for role in ATOMIC_VIEW_ORDER}
    )
    submission_accounting: SubmissionAccounting = Field(default_factory=SubmissionAccounting)
    accepted_atomic_assets: dict[str, AcceptedAtomicAsset] = Field(default_factory=dict)
    master_derivation: MasterDerivationRecord | None = None
    final_package_approval_state: Literal["not_approved", "approved"] = "not_approved"
    final_approval_record_sha256: str | None = None
    failure_reasons: tuple[str, ...] = ()
    stop_reason: str | None = None

    @field_validator("character_fingerprint")
    @classmethod
    def digest(cls, value: str) -> str:
        return _sha256(value)

    @model_validator(mode="after")
    def lifecycle_consistency(self) -> "CharacterReferenceBootstrapManifest":
        if tuple(self.per_view_candidate_attempts) != ATOMIC_VIEW_ORDER:
            raise ValueError("candidate attempts must use deterministic atomic-view order")
        if self.selected_candidate_id is None:
            if any(
                value is not None
                for value in (
                    self.selected_front_anchor_sha256,
                    self.human_selection_record,
                    self.anchor_lock_timestamp,
                )
            ):
                raise ValueError("front-anchor metadata exists without a selected candidate")
        else:
            selected = [
                item for item in self.front_candidates if item.candidate_id == self.selected_candidate_id
            ]
            if len(selected) != 1:
                raise ValueError("selected front candidate must identify exactly one candidate")
            if self.selected_front_anchor_sha256 != selected[0].image_sha256:
                raise ValueError("selected front-anchor hash does not match candidate")
            if self.human_selection_record is None or self.anchor_lock_timestamp is None:
                raise ValueError("selected front anchor requires immutable human-selection metadata")
        if self.final_package_approval_state == "approved":
            if self.state != BootstrapState.package_approved:
                raise ValueError("final approval state requires package_approved workflow state")
            if self.final_approval_record_sha256 is None:
                raise ValueError("final package approval record SHA256 is required")
        _assert_safe_serialization(self.model_dump(mode="json"))
        return self


def new_bootstrap_manifest(
    *, bootstrap_session_id: str, character_fingerprint: str
) -> CharacterReferenceBootstrapManifest:
    return CharacterReferenceBootstrapManifest(
        schema_version=1,
        bootstrap_session_id=bootstrap_session_id,
        character_fingerprint=character_fingerprint,
        generation_spec_version=1,
    )


def record_candidate(
    manifest: CharacterReferenceBootstrapManifest,
    candidate: BootstrapCandidate,
    *,
    strict_qc_failure_recorded: bool = False,
) -> CharacterReferenceBootstrapManifest:
    _require_not_blocked_or_approved(manifest)
    role = candidate.asset_role
    if role == FRONT_ROLE:
        if manifest.state not in {
            BootstrapState.awaiting_front_generation,
            BootstrapState.awaiting_front_selection,
        }:
            raise RuntimeError("front candidates cannot be added after anchor selection")
    elif manifest.state != BootstrapState.generating_remaining_views:
        raise RuntimeError("remaining-view candidates require Stage B")
    if candidate.targeted and not strict_qc_failure_recorded:
        raise RuntimeError("targeted candidate requires a recorded strict-QC failure")
    existing = manifest.per_view_candidate_attempts[role]
    if candidate.candidate_number != len(existing) + 1:
        raise ValueError("candidate number is not the next deterministic attempt")
    if any(
        item.candidate_id == candidate.candidate_id
        for attempts in manifest.per_view_candidate_attempts.values()
        for item in attempts
    ):
        raise ValueError("candidate ID is duplicated")
    counts = manifest.submission_accounting.per_view[role]
    new_count = ViewSubmissionCount(
        initial=counts.initial + (0 if candidate.targeted else 1),
        targeted=counts.targeted + (1 if candidate.targeted else 0),
    )
    budget = VIEW_BUDGETS[role]
    if new_count.initial > budget.initial_max or new_count.targeted > budget.targeted_additional_max:
        raise RuntimeError(f"{role} submission budget exhausted")
    total = manifest.submission_accounting.total_image_submissions + 1
    if total > BOOTSTRAP_GLOBAL_SUBMISSION_MAX:
        raise RuntimeError("global bootstrap submission budget exhausted")
    per_view_counts = dict(manifest.submission_accounting.per_view)
    per_view_counts[role] = new_count
    accounting = SubmissionAccounting(
        per_view=per_view_counts,
        total_image_submissions=total,
        transport_attempts=total,
    )
    attempts = dict(manifest.per_view_candidate_attempts)
    attempts[role] = (*existing, candidate)
    front_candidates = (
        (*manifest.front_candidates, candidate)
        if role == FRONT_ROLE
        else manifest.front_candidates
    )
    state = (
        BootstrapState.awaiting_front_selection
        if role == FRONT_ROLE
        else BootstrapState.awaiting_atomic_qc
    )
    if role != FRONT_ROLE:
        state = BootstrapState.generating_remaining_views
    return manifest.model_copy(
        update={
            "state": state,
            "front_candidates": front_candidates,
            "per_view_candidate_attempts": attempts,
            "submission_accounting": accounting,
        }
    )


def select_front_anchor(
    manifest: CharacterReferenceBootstrapManifest,
    selection: HumanFrontSelectionRecord,
) -> CharacterReferenceBootstrapManifest:
    if manifest.state != BootstrapState.awaiting_front_selection:
        raise RuntimeError("front selection is not available in the current state")
    if manifest.selected_candidate_id is not None:
        raise RuntimeError("front anchor is already immutable")
    matches = [
        item for item in manifest.front_candidates if item.candidate_id == selection.selected_candidate_id
    ]
    if len(matches) != 1:
        raise ValueError("human selection must identify exactly one generated front candidate")
    candidate = matches[0]
    if candidate.image_sha256 != selection.selected_image_sha256:
        raise ValueError("human selection hash does not match candidate")
    accepted = dict(manifest.accepted_atomic_assets)
    accepted[FRONT_ROLE] = AcceptedAtomicAsset(
        asset_role=FRONT_ROLE,
        candidate_id=candidate.candidate_id,
        image_sha256=candidate.image_sha256,
        anatomy_qc_passed=True,
        style_qc_passed=True,
        identity_qc_passed=True,
        human_qc_passed=True,
        bootstrap_identity_anchor=True,
    )
    return manifest.model_copy(
        update={
            "state": BootstrapState.front_anchor_locked,
            "selected_candidate_id": candidate.candidate_id,
            "selected_front_anchor_sha256": candidate.image_sha256,
            "human_selection_record": selection,
            "anchor_lock_timestamp": selection.selected_at,
            "accepted_atomic_assets": accepted,
        }
    )


def begin_remaining_views(
    manifest: CharacterReferenceBootstrapManifest,
    *,
    anchor_bytes: bytes,
    request_plans: tuple[AtomicViewRequestPlan, ...],
) -> CharacterReferenceBootstrapManifest:
    if manifest.state != BootstrapState.front_anchor_locked:
        raise RuntimeError("Stage B requires one immutable selected front anchor")
    actual = hashlib.sha256(anchor_bytes).hexdigest()
    if actual != manifest.selected_front_anchor_sha256:
        return manifest.model_copy(
            update={
                "state": BootstrapState.blocked,
                "failure_reasons": (*manifest.failure_reasons, "front_anchor_sha256_mismatch"),
                "stop_reason": "immutable front anchor bytes changed",
            }
        )
    roles = tuple(plan.asset_role for plan in request_plans)
    if roles != REMAINING_VIEW_ORDER:
        raise ValueError("Stage-B request plans are missing, duplicated, or out of order")
    if any(plan.anchor_sha256 != actual for plan in request_plans):
        raise ValueError("every Stage-B request must use the same immutable anchor hash")
    plans = {plan.asset_role: plan for plan in request_plans}
    return manifest.model_copy(
        update={
            "state": BootstrapState.generating_remaining_views,
            "per_view_request_plans": plans,
        }
    )


def accept_atomic_candidate(
    manifest: CharacterReferenceBootstrapManifest,
    *,
    asset_role: str,
    candidate_id: str,
    anatomy_qc_passed: bool,
    style_qc_passed: bool,
    identity_qc_passed: bool,
    human_qc_passed: bool,
) -> CharacterReferenceBootstrapManifest:
    if manifest.state not in {
        BootstrapState.generating_remaining_views,
        BootstrapState.awaiting_atomic_qc,
    }:
        raise RuntimeError("atomic candidate acceptance is not available")
    if asset_role not in REMAINING_VIEW_ORDER:
        raise ValueError("only Stage-B atomic views can be accepted here")
    matches = [
        item
        for item in manifest.per_view_candidate_attempts[asset_role]
        if item.candidate_id == candidate_id
    ]
    if len(matches) != 1:
        raise ValueError("accepted candidate must identify exactly one submitted candidate")
    if not all((anatomy_qc_passed, style_qc_passed, identity_qc_passed, human_qc_passed)):
        raise RuntimeError("atomic candidate cannot be accepted before all QC gates pass")
    candidate = matches[0]
    accepted = dict(manifest.accepted_atomic_assets)
    accepted[asset_role] = AcceptedAtomicAsset(
        asset_role=asset_role,
        candidate_id=candidate_id,
        image_sha256=candidate.image_sha256,
        anatomy_qc_passed=True,
        style_qc_passed=True,
        identity_qc_passed=True,
        human_qc_passed=True,
    )
    state = (
        BootstrapState.atomic_views_accepted
        if tuple(accepted) == ATOMIC_VIEW_ORDER
        else BootstrapState.generating_remaining_views
    )
    return manifest.model_copy(update={"accepted_atomic_assets": accepted, "state": state})


def record_master_assembled(
    manifest: CharacterReferenceBootstrapManifest,
    derivation: MasterDerivationRecord,
) -> CharacterReferenceBootstrapManifest:
    if manifest.state != BootstrapState.atomic_views_accepted:
        raise RuntimeError("master assembly requires all four accepted atomic views")
    expected = tuple(
        manifest.accepted_atomic_assets[role].image_sha256 for role in ATOMIC_VIEW_ORDER
    )
    if derivation.source_sha256 != expected:
        raise ValueError("master source hashes do not match accepted atomic assets")
    return manifest.model_copy(
        update={"state": BootstrapState.master_assembled, "master_derivation": derivation}
    )


def begin_package_approval(
    manifest: CharacterReferenceBootstrapManifest,
) -> CharacterReferenceBootstrapManifest:
    if manifest.state != BootstrapState.master_assembled:
        raise RuntimeError("package approval requires a locally assembled master")
    return manifest.model_copy(update={"state": BootstrapState.awaiting_package_approval})


def approve_final_package(
    manifest: CharacterReferenceBootstrapManifest,
    *,
    approval_record_sha256: str,
) -> CharacterReferenceBootstrapManifest:
    if manifest.state != BootstrapState.awaiting_package_approval:
        raise RuntimeError("final package approval is not available")
    digest = _sha256(approval_record_sha256)
    return manifest.model_copy(
        update={
            "state": BootstrapState.package_approved,
            "final_package_approval_state": "approved",
            "final_approval_record_sha256": digest,
        }
    )


def require_provider_facing_approval(
    manifest: CharacterReferenceBootstrapManifest,
) -> tuple[AcceptedAtomicAsset, ...]:
    if manifest.state != BootstrapState.package_approved:
        raise RuntimeError("provider-facing reference use requires final human approval")
    return tuple(manifest.accepted_atomic_assets[role] for role in ATOMIC_VIEW_ORDER)


def validate_bootstrap_provider(capabilities: ImageProviderCapabilities) -> dict[str, object]:
    if not capabilities.supports_text_to_image:
        raise RuntimeError("bootstrap provider must support text-to-image generation")
    return {
        "provider_id": capabilities.provider_id,
        "stage": "bootstrap_front",
        "identity_guarantee": False,
        "postconditions_required": ["image/png", "768x1024"],
    }


def validate_reference_provider(
    capabilities: ImageProviderCapabilities,
    *,
    reference_transport_ready: bool,
    live_authorized: bool,
) -> dict[str, object]:
    if not capabilities.supports_reference_conditioning:
        raise RuntimeError("Stage-B provider must support reference conditioning")
    if capabilities.max_reference_images < 1 or "image/png" not in capabilities.accepted_reference_mime_types:
        raise RuntimeError("Stage-B provider must accept at least one PNG reference")
    if capabilities.identity_anchor_verification != "per_request_verified":
        raise RuntimeError("Stage-B provider must truthfully verify the anchor per request")
    if capabilities.provider_retry_control != "caller_bounded":
        raise RuntimeError("Stage-B provider retry behavior must be caller-bounded")
    if not reference_transport_ready:
        raise RuntimeError("Stage-B reference transport is not ready")
    if not live_authorized:
        raise RuntimeError("Stage-B live authorization is missing")
    return {
        "provider_id": capabilities.provider_id,
        "stage": "reference_conditioned",
        "anchor_verification": capabilities.identity_anchor_verification,
        "fallbacks": 0,
    }


def _require_not_blocked_or_approved(manifest: CharacterReferenceBootstrapManifest) -> None:
    if manifest.state in {BootstrapState.blocked, BootstrapState.package_approved}:
        raise RuntimeError("bootstrap workflow is terminal")


def _sha256(value: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise ValueError("expected a full SHA256 hex digest")
    return normalized


def _assert_safe_serialization(payload: dict[str, object]) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True).lower()
    forbidden = ("://", "authorization", "bearer ", "api_key", "secret_access_key")
    if any(marker in serialized for marker in forbidden):
        raise ValueError("bootstrap manifest contains a URL or credential material")


__all__ = [
    "BOOTSTRAP_GLOBAL_SUBMISSION_MAX",
    "FRONT_ROLE",
    "REMAINING_VIEW_ORDER",
    "VIEW_BUDGETS",
    "AcceptedAtomicAsset",
    "AtomicViewRequestPlan",
    "BootstrapCandidate",
    "BootstrapState",
    "CharacterReferenceBootstrapManifest",
    "HumanFrontSelectionRecord",
    "MasterDerivationRecord",
    "SubmissionAccounting",
    "ViewSubmissionBudget",
    "accept_atomic_candidate",
    "approve_final_package",
    "begin_package_approval",
    "begin_remaining_views",
    "new_bootstrap_manifest",
    "record_candidate",
    "record_master_assembled",
    "require_provider_facing_approval",
    "select_front_anchor",
    "validate_bootstrap_provider",
    "validate_reference_provider",
]
