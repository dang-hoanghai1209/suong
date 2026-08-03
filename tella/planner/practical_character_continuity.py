"""Reusable deterministic character-continuity policy for practical visuals."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from enum import StrEnum
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class CanonicalCharacterSpec:
    spec_version: int = 1
    presentation: str = "one young adult male"
    hair: str = "short dark hair with a fixed rounded side-swept silhouette"
    face: str = "simple softly angular face with small dark eyes and a short straight nose"
    skin_tone: str = "fixed warm medium-light skin tone"
    top: str = "teal long-sleeve top with a small round collar and narrow fixed cuffs"
    trousers: str = "dark charcoal trousers"
    shoulder_to_head_ratio: str = "approximately 2.4 shoulder widths per head width"
    body_build: str = "slim average adult build"
    line_style: str = "uniform clean charcoal line, approximately 3 px at 768x1344"
    palette: str = "teal, dark charcoal, warm medium-light skin, warm orange accents"


CANONICAL_CHARACTER = CanonicalCharacterSpec()


def canonical_character_payload() -> dict[str, Any]:
    """Return a detached serializable copy of the immutable specification."""
    return asdict(CANONICAL_CHARACTER)


def canonical_character_fingerprint() -> str:
    encoded = json.dumps(
        canonical_character_payload(), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_identity_prompt() -> str:
    spec = CANONICAL_CHARACTER
    return (
        f"CFP {canonical_character_fingerprint()}; fixed young adult male: "
        "short dark rounded side-swept hair; softly angular face; warm medium-light skin; "
        "teal round-collar narrow-cuff long-sleeve top; charcoal trousers; slim build; "
        "shoulders/head 2.4; uniform 3px charcoal lines; teal/orange palette."
    )


IDENTITY_INVARIANTS = (
    "young adult male presentation and age group",
    "short dark rounded side-swept hair silhouette",
    "softly angular simple face shape",
    "warm medium-light skin tone",
    "teal long-sleeve top with round collar and narrow cuffs",
    "dark charcoal trousers",
    "slim average build and approximate 2.4:1 shoulder-to-head ratio",
    "uniform clean charcoal line and fixed teal/orange palette",
)

FORBIDDEN_IDENTITY_CHANGES = (
    "different gender presentation or age group",
    "different hair color or major hair silhouette",
    "different top color, collar, or cuff design",
    "clearly different body build",
    "missing head or duplicated person",
)


class IdentityMode(StrEnum):
    approximate_character_continuity = "approximate_character_continuity"
    reference_conditioned_character = "reference_conditioned_character"

_HARD_IDENTITY_FIELDS = (
    "gender_age_matches", "hair_color_matches", "hair_silhouette_matches",
    "top_color_matches", "body_build_matches",
    "head_present", "single_person",
)
_SOFT_IDENTITY_FIELDS = (
    "face_shape_matches", "minor_face_details_match", "hands_sufficient",
    "perspective_proportions_match",
)


def classify_identity(
    observation: Mapping[str, bool],
    *,
    mode: IdentityMode | str = IdentityMode.approximate_character_continuity,
) -> dict[str, Any]:
    """Classify one scene using the recipe's explicit hard/soft identity rules."""
    selected = IdentityMode(mode)
    hard = [name for name in _HARD_IDENTITY_FIELDS if observation.get(name) is False]
    soft = [name for name in _SOFT_IDENTITY_FIELDS if observation.get(name) is False]
    if selected is IdentityMode.reference_conditioned_character and soft:
        hard.extend(soft)
        soft = []
    return {
        "decision": "hard_fail" if hard else ("soft_fail" if soft else "pass"),
        "hard_failure_fields": hard,
        "soft_failure_fields": soft,
        "recognizable_designed_character": not hard,
        "exact_pixel_identity_claimed": False,
        "identity_mode": selected.value,
    }


def resolve_identity_mode(
    requested: IdentityMode | str,
    *,
    provider_supports_reference_conditioning: bool,
) -> IdentityMode:
    selected = IdentityMode(requested)
    if (
        selected is IdentityMode.reference_conditioned_character
        and not provider_supports_reference_conditioning
    ):
        raise RuntimeError(
            "reference_conditioned_character requires proven provider image-reference support"
        )
    return selected


def aggregate_identity_decisions(scene_results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    hard_scenes = [
        int(item["scene_index"]) for item in scene_results
        if item.get("decision") == "hard_fail"
    ]
    soft_scenes = [
        int(item["scene_index"]) for item in scene_results
        if item.get("decision") == "soft_fail"
    ]
    complete = len(scene_results) == 7 and {int(item["scene_index"]) for item in scene_results} == set(range(1, 8))
    return {
        "passed": complete and not hard_scenes,
        "complete": complete,
        "hard_failure_scene_indices": hard_scenes,
        "soft_failure_scene_indices": soft_scenes,
        "acceptance_standard": "recognizable_designed_character",
        "exact_pixel_identity_claimed": False,
    }


def aggregate_visual_decisions(scene_results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    identity = aggregate_identity_decisions(scene_results)
    semantic_contradictions = [
        int(item["scene_index"]) for item in scene_results
        if item.get("semantic_contradiction") is True
    ]
    semantic_failures = [
        int(item["scene_index"]) for item in scene_results
        if item.get("semantic_passed") is False
    ]
    return {
        **identity,
        "passed": bool(identity["passed"] and not semantic_contradictions and not semantic_failures),
        "semantic_contradiction_scene_indices": semantic_contradictions,
        "semantic_failure_scene_indices": semantic_failures,
    }


def validate_symbol_only_overlay(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an optional generic planning-grid overlay without editing pixels."""
    allowed_shapes = {"empty_box", "colored_circle"}
    shapes = list(metadata.get("shapes", []))
    failures: list[str] = []
    if metadata.get("intersects_character", True):
        failures.append("overlay_intersects_character")
    if metadata.get("contains_text", True) or metadata.get("contains_digits", True):
        failures.append("overlay_contains_text_or_digits")
    if metadata.get("task_specific_raster_repair", True):
        failures.append("task_specific_raster_repair")
    if not metadata.get("generic_reusable", False):
        failures.append("overlay_not_generic_reusable")
    if not shapes or any(shape not in allowed_shapes for shape in shapes):
        failures.append("unsupported_overlay_shape")
    return {"passed": not failures, "failure_reasons": failures}


def generated_text_is_hard_failure(observation: Mapping[str, bool]) -> bool:
    return any(bool(observation.get(key)) for key in (
        "readable_text", "pseudo_text", "labels", "digits",
    ))


__all__ = [
    "CANONICAL_CHARACTER", "FORBIDDEN_IDENTITY_CHANGES", "IDENTITY_INVARIANTS",
    "IdentityMode", "aggregate_identity_decisions", "aggregate_visual_decisions", "canonical_character_fingerprint",
    "canonical_character_payload", "canonical_identity_prompt", "classify_identity",
    "generated_text_is_hard_failure", "validate_symbol_only_overlay",
    "resolve_identity_mode",
]
