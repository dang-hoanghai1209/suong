"""Zero-network validation for the practical character-reference bootstrap plan."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tella.media.character_reference_bootstrap import (
    BOOTSTRAP_GLOBAL_SUBMISSION_MAX,
    VIEW_BUDGETS,
)
from tella.media.character_reference_package import (
    ATOMIC_DIMENSIONS,
    ATOMIC_VIEW_ORDER,
    load_canonical_character_specification,
)


class ConfigViewBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    initial_max: int = Field(ge=1)
    targeted_additional_max: int = Field(ge=0)
    total_max: int = Field(ge=1)


class ConfigBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    front_portrait: ConfigViewBudget
    three_quarter_portrait: ConfigViewBudget
    side_profile: ConfigViewBudget
    full_body_neutral: ConfigViewBudget
    global_image_submissions_max: Literal[12]
    transport_attempts_per_submission_max: Literal[1]
    automatic_retries: Literal[0]
    fallbacks: Literal[0]


class RequestSpecification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    asset_role: Literal[
        "front_portrait", "three_quarter_portrait", "side_profile", "full_body_neutral"
    ]
    stage: Literal["bootstrap_front", "reference_conditioned"]
    prompt: str = Field(min_length=1)
    prompt_sha256: str
    width: Literal[768]
    height: Literal[1024]
    output_mime_type: Literal["image/png"]
    anchor_binding: Literal["none", "selected_front_anchor_sha256_exact"]
    constraints_profile: Literal["approved_practical_character_anatomy_and_negative_v1"]

    @field_validator("prompt_sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("expected a full SHA256 hex digest")
        return normalized

    @model_validator(mode="after")
    def validate_request(self) -> "RequestSpecification":
        if hashlib.sha256(self.prompt.encode("utf-8")).hexdigest() != self.prompt_sha256:
            raise ValueError("request prompt SHA256 mismatch")
        if self.asset_role == "front_portrait":
            if self.stage != "bootstrap_front" or self.anchor_binding != "none":
                raise ValueError("front request must be an unreferenced bootstrap plan")
        elif (
            self.stage != "reference_conditioned"
            or self.anchor_binding != "selected_front_anchor_sha256_exact"
        ):
            raise ValueError("remaining request must bind the exact selected front anchor")
        return self


class ProviderAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_id: Literal["cloudflare", "bfl_flux2_reference"]
    eligible_stage: Literal[
        "bootstrap_front_candidate_only", "remaining_reference_conditioned_views_only"
    ]
    text_to_image: bool
    reference_conditioning: bool
    identity_guarantee: Literal[False]
    requirements: tuple[str, ...]
    execution_authorized: Literal[False]


class BootstrapPlanConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1]
    workflow_id: Literal["practical_young_adult_male_teal_v1_bootstrap_v1"]
    package_id: Literal["practical_young_adult_male_teal_v1_package_v1"]
    character_id: Literal["practical_young_adult_male_teal_v1"]
    character_fingerprint: str
    canonical_spec_path: Path
    generation_spec_version: Literal[1]
    request_budget: ConfigBudget
    shared_anatomy_constraints: tuple[str, ...]
    shared_negative_constraints: tuple[str, ...]
    request_specs: tuple[RequestSpecification, ...]
    provider_assessment: tuple[ProviderAssessment, ...]

    @model_validator(mode="after")
    def exact_contract(self) -> "BootstrapPlanConfig":
        roles = tuple(item.asset_role for item in self.request_specs)
        if roles != ATOMIC_VIEW_ORDER:
            raise ValueError("request specs are missing, duplicated, or out of order")
        configured = {
            role: getattr(self.request_budget, role) for role in ATOMIC_VIEW_ORDER
        }
        for role, expected in VIEW_BUDGETS.items():
            if configured[role].model_dump() != expected.model_dump():
                raise ValueError(f"{role} request budget differs from workflow contract")
        if sum(item.total_max for item in configured.values()) != BOOTSTRAP_GLOBAL_SUBMISSION_MAX:
            raise ValueError("global request budget does not equal per-view maxima")
        if not self.shared_anatomy_constraints or not self.shared_negative_constraints:
            raise ValueError("approved anatomy and negative constraints are required")
        provider_ids = tuple(item.provider_id for item in self.provider_assessment)
        if provider_ids != ("cloudflare", "bfl_flux2_reference"):
            raise ValueError("provider assessment is missing or out of order")
        cloudflare, bfl = self.provider_assessment
        if cloudflare.reference_conditioning or cloudflare.identity_guarantee:
            raise ValueError("Cloudflare must remain a non-guaranteed text-only bootstrap option")
        if not bfl.reference_conditioning or bfl.text_to_image:
            raise ValueError("BFL assessment must remain Stage-B reference-only")
        return self


def load_and_validate_plan(path: Path, *, repository_root: Path) -> BootstrapPlanConfig:
    config = BootstrapPlanConfig.model_validate_json(path.read_text(encoding="utf-8"))
    root = repository_root.resolve()
    spec_path = config.canonical_spec_path
    if spec_path.is_absolute() or ".." in spec_path.parts:
        raise ValueError("canonical specification path must be repository-relative")
    specification = load_canonical_character_specification(root / spec_path)
    if specification.character_fingerprint != config.character_fingerprint:
        raise ValueError("bootstrap character fingerprint mismatch")
    return config


def validate_only(path: Path, *, repository_root: Path) -> dict[str, object]:
    config = load_and_validate_plan(path, repository_root=repository_root)
    return {
        "status": "valid_no_execution",
        "workflow_id": config.workflow_id,
        "atomic_view_order": list(ATOMIC_VIEW_ORDER),
        "atomic_dimensions": list(ATOMIC_DIMENSIONS),
        "maximum_image_submissions": config.request_budget.global_image_submissions_max,
        "automatic_retries": config.request_budget.automatic_retries,
        "fallbacks": config.request_budget.fallbacks,
        "front_selection_required": True,
        "stage_b_anchor_binding": "selected_front_anchor_sha256_exact",
        "provider_clients_constructed": 0,
        "image_provider_calls": 0,
        "external_calls": 0,
        "image_generation": 0,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--mode", choices=("validate-only",), required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = validate_only(args.config, repository_root=args.repository_root)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
