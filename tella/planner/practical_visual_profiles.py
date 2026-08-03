"""Typed, data-driven visual profiles for practical-life scene plans."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def character_spec_fingerprint(spec: dict[str, Any]) -> str:
    encoded = json.dumps(
        spec, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class PracticalVisualSceneProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scene_index: int = Field(ge=1)
    scene_role: str = Field(min_length=1, max_length=80)
    setting: str = Field(min_length=1, max_length=300)
    primary_action: str = Field(min_length=1, max_length=500)
    primary_prop: str = Field(min_length=1, max_length=200)
    secondary_props: tuple[str, ...] = Field(min_length=1, max_length=12)
    body_pose: str = Field(min_length=1, max_length=240)
    character_placement: str = Field(min_length=1, max_length=240)
    camera_framing: str = Field(min_length=1, max_length=200)
    composition_family: str = Field(min_length=1, max_length=160)
    emotional_state: str = Field(min_length=1, max_length=160)
    semantic_hard_negatives: tuple[str, ...] = ()
    symbolic_qc_expectations: tuple[str, ...] = ()
    planning_overlay_strategy: str = Field("none", max_length=120)
    subtitle_safe_lower_fraction: float = Field(0.0, ge=0.0, le=1.0)

    @field_validator("secondary_props", "semantic_hard_negatives", "symbolic_qc_expectations")
    @classmethod
    def nonempty_items(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("visual-profile list items must not be blank")
        return value


class PracticalVisualProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(1, ge=1)
    profile_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    identity_mode: str = Field(min_length=1, max_length=80)
    identity_continuity_strategy: str = Field(min_length=1, max_length=120)
    identity_acceptance_standard: str = Field(min_length=1, max_length=240)
    character_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_character_spec: dict[str, Any]
    character_identity_prompt: str = Field(min_length=1, max_length=1000)
    identity_invariants: tuple[str, ...] = Field(min_length=1)
    forbidden_identity_changes: tuple[str, ...] = Field(min_length=1)
    cast_archetype: str = Field(min_length=1, max_length=80)
    style_instruction: str = Field(min_length=1, max_length=300)
    global_hard_negatives: tuple[str, ...] = Field(min_length=1)
    subtitle_layout_policy_id: str = Field(min_length=1, max_length=80)
    scenes: tuple[PracticalVisualSceneProfile, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_profile(self) -> "PracticalVisualProfile":
        indices = [scene.scene_index for scene in self.scenes]
        if len(indices) != len(set(indices)):
            raise ValueError("visual profile contains duplicate scene indices")
        if indices != list(range(1, len(indices) + 1)):
            raise ValueError("visual profile scene indices must be ordered and contiguous")
        actual = character_spec_fingerprint(self.canonical_character_spec)
        if actual != self.character_fingerprint:
            raise ValueError("visual profile character fingerprint mismatch")
        return self


def load_practical_visual_profile(
    path: Path,
    *,
    expected_profile_id: str | None = None,
    expected_scene_roles: tuple[str, ...] | None = None,
) -> PracticalVisualProfile:
    candidate = Path(path)
    if not candidate.is_file():
        raise FileNotFoundError(f"visual profile is missing: {candidate}")
    if candidate.is_symlink() or bool(getattr(candidate, "is_junction", lambda: False)()):
        raise ValueError("visual profile must not be a symlink or junction")
    profile = PracticalVisualProfile.model_validate_json(
        candidate.read_text(encoding="utf-8")
    )
    if expected_profile_id is not None and profile.profile_id != expected_profile_id:
        raise ValueError(
            f"unknown visual profile ID {expected_profile_id!r}; "
            f"file declares {profile.profile_id!r}"
        )
    if expected_scene_roles is not None:
        actual = tuple(scene.scene_role for scene in profile.scenes)
        if actual != expected_scene_roles:
            raise ValueError(
                f"visual profile scene-role order mismatch: expected {expected_scene_roles}, "
                f"received {actual}"
            )
    return profile


__all__ = [
    "PracticalVisualProfile",
    "PracticalVisualSceneProfile",
    "character_spec_fingerprint",
    "load_practical_visual_profile",
]
