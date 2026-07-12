"""Static, versioned contracts for production and lab video recipes."""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RecipeNotFoundError(ValueError):
    """Raised when a requested recipe id or version is not registered."""


class RecipeDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    recipe_id: str = Field(..., pattern=r"^[a-z][a-z0-9_]*$", max_length=80)
    recipe_version: int = Field(..., ge=1)
    display_name: str = Field(..., min_length=2, max_length=120)
    status: Literal["production", "lab"]
    narrative_mode: str = Field(..., min_length=2, max_length=80)
    planner_id: str = Field(..., min_length=2, max_length=80)
    visual_theme_id: str = Field(..., min_length=2, max_length=80)
    voice_profile_id: str = Field(..., min_length=2, max_length=80)
    subtitle_style_id: str = Field(..., min_length=2, max_length=80)
    transition_profile_id: str = Field(..., min_length=2, max_length=80)
    motion_profile_id: str = Field(..., min_length=2, max_length=80)
    minimum_scene_count: int = Field(..., ge=1)
    maximum_scene_count: int = Field(..., ge=1)
    minimum_duration_seconds: float = Field(..., gt=0)
    target_duration_seconds: float = Field(..., gt=0)
    maximum_duration_seconds: float = Field(..., gt=0)
    narration_mode: Literal["continuous", "per_scene"]
    aspect_ratio: Literal["9:16", "16:9"]
    supports_voice_override: bool = True
    supports_scene_qc: bool = True
    supports_asset_reuse: bool = True

    @model_validator(mode="after")
    def _validate_contract(self) -> "RecipeDefinition":
        if self.minimum_scene_count > self.maximum_scene_count:
            raise ValueError("minimum_scene_count must not exceed maximum_scene_count")
        if not (
            self.minimum_duration_seconds
            <= self.target_duration_seconds
            <= self.maximum_duration_seconds
        ):
            raise ValueError(
                "duration contract must satisfy minimum <= target <= maximum"
            )
        if self.status == "production":
            errors = []
            if self.minimum_scene_count < 7 or self.maximum_scene_count > 8:
                errors.append("production scene range must stay within 7-8")
            if (
                self.minimum_duration_seconds < 32
                or self.maximum_duration_seconds > 38
            ):
                errors.append("production duration range must stay within 32-38 seconds")
            if self.aspect_ratio != "9:16":
                errors.append("production aspect ratio must be 9:16")
            if self.narration_mode != "continuous":
                errors.append("production narration mode must be continuous")
            if errors:
                raise ValueError("; ".join(errors))
        return self

    @property
    def scene_range(self) -> list[int]:
        return [self.minimum_scene_count, self.maximum_scene_count]

    @property
    def duration_range(self) -> list[float]:
        return [self.minimum_duration_seconds, self.maximum_duration_seconds]


_EMOTIONAL_SYMBOLIC_V1 = RecipeDefinition(
    recipe_id="emotional_symbolic_v1",
    recipe_version=1,
    display_name="Emotional Symbolic Reflection",
    status="production",
    narrative_mode="emotional_reflection",
    planner_id="symbolic_emotional",
    visual_theme_id="minimalist_symbolic_reel",
    voice_profile_id="soft_female_vi",
    subtitle_style_id="reel_minimal",
    transition_profile_id="subtle_crossfade",
    motion_profile_id="slow_ken_burns",
    minimum_scene_count=7,
    maximum_scene_count=8,
    minimum_duration_seconds=32,
    target_duration_seconds=35,
    maximum_duration_seconds=38,
    narration_mode="continuous",
    aspect_ratio="9:16",
    supports_voice_override=True,
    supports_scene_qc=True,
    supports_asset_reuse=True,
)

_LIFE_INSIGHT_SYMBOLIC_V1 = RecipeDefinition(
    recipe_id="life_insight_symbolic_v1",
    recipe_version=1,
    display_name="Life Insight Symbolic",
    status="production",
    narrative_mode="life_insight",
    planner_id="life_insight_symbolic",
    visual_theme_id="life_insight_symbolic",
    voice_profile_id="firm_male_vi",
    subtitle_style_id="insight_reel",
    transition_profile_id="clean_soft_cut",
    motion_profile_id="controlled_slow_pan",
    minimum_scene_count=7,
    maximum_scene_count=8,
    minimum_duration_seconds=32,
    target_duration_seconds=35,
    maximum_duration_seconds=38,
    narration_mode="continuous",
    aspect_ratio="9:16",
    supports_voice_override=True,
    supports_scene_qc=True,
    supports_asset_reuse=True,
)

_REGISTRY: dict[str, dict[int, RecipeDefinition]] = {
    _EMOTIONAL_SYMBOLIC_V1.recipe_id: {
        _EMOTIONAL_SYMBOLIC_V1.recipe_version: _EMOTIONAL_SYMBOLIC_V1,
    },
    _LIFE_INSIGHT_SYMBOLIC_V1.recipe_id: {
        _LIFE_INSIGHT_SYMBOLIC_V1.recipe_version: _LIFE_INSIGHT_SYMBOLIC_V1,
    },
}


def get_recipe(recipe_id: str, version: int | None = None) -> RecipeDefinition:
    versions = _REGISTRY.get((recipe_id or "").strip())
    if not versions:
        available = ", ".join(sorted(_REGISTRY))
        raise RecipeNotFoundError(
            f"unknown recipe {recipe_id!r}; available recipes: {available}"
        )
    resolved_version = max(versions) if version is None else int(version)
    recipe = versions.get(resolved_version)
    if recipe is None:
        available = ", ".join(f"v{item}" for item in sorted(versions))
        raise RecipeNotFoundError(
            f"unknown version v{resolved_version} for recipe {recipe_id!r}; "
            f"available versions: {available}"
        )
    return recipe


def list_recipes() -> list[RecipeDefinition]:
    return [get_recipe(recipe_id) for recipe_id in sorted(_REGISTRY)]


def format_recipe_list() -> str:
    lines = []
    for recipe in list_recipes():
        lines.append(
            f"{recipe.recipe_id} v{recipe.recipe_version} [{recipe.status}] "
            f"{recipe.display_name} | theme={recipe.visual_theme_id} "
            f"planner={recipe.planner_id} scenes={recipe.minimum_scene_count}-"
            f"{recipe.maximum_scene_count} duration="
            f"{recipe.minimum_duration_seconds:g}-"
            f"{recipe.maximum_duration_seconds:g}s aspect={recipe.aspect_ratio} "
            f"narration={recipe.narration_mode}"
        )
    return "\n".join(lines)


def estimate_plan_duration(plan: Any) -> float:
    """Estimate continuous narration length without invoking TTS."""
    scenes = [scene for scene in plan.scenes if scene.kind == "scene"]
    words = sum(
        len(re.findall(r"\b[^\W_]+\b", scene.voice_script or "", flags=re.UNICODE))
        for scene in scenes
    )
    pauses = max(0, len(scenes) - 1) * 0.35
    return round(words / 3.0 + pauses, 2)


def validate_recipe_run(
    recipe: RecipeDefinition,
    *,
    scene_count: int | None = None,
    estimated_duration_seconds: float | None = None,
    aspect_ratio: str | None = None,
    narration_mode: str | None = None,
) -> list[str]:
    errors: list[str] = []
    if scene_count is not None:
        if scene_count < recipe.minimum_scene_count:
            errors.append(
                f"scene count {scene_count} is below recipe minimum "
                f"{recipe.minimum_scene_count}"
            )
        if scene_count > recipe.maximum_scene_count:
            errors.append(
                f"scene count {scene_count} exceeds recipe maximum "
                f"{recipe.maximum_scene_count}"
            )
    if estimated_duration_seconds is not None:
        if estimated_duration_seconds < recipe.minimum_duration_seconds:
            errors.append(
                f"estimated duration {estimated_duration_seconds:.2f}s is below recipe "
                f"minimum {recipe.minimum_duration_seconds:g}s"
            )
        if estimated_duration_seconds > recipe.maximum_duration_seconds:
            errors.append(
                f"estimated duration {estimated_duration_seconds:.2f}s exceeds recipe "
                f"maximum {recipe.maximum_duration_seconds:g}s"
            )
    if aspect_ratio is not None and aspect_ratio != recipe.aspect_ratio:
        errors.append(
            f"aspect ratio {aspect_ratio} does not match recipe requirement "
            f"{recipe.aspect_ratio}"
        )
    if narration_mode is not None and narration_mode != recipe.narration_mode:
        errors.append(
            f"narration mode {narration_mode} does not match recipe requirement "
            f"{recipe.narration_mode}"
        )
    return errors


def recipe_manifest(
    recipe: RecipeDefinition,
    *,
    validation_status: str,
    validation_errors: list[str] | None = None,
    estimated_duration_seconds: float | None = None,
    voice_resolution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        **recipe.model_dump(),
        "recipe_status": recipe.status,
        "recipe_scene_range": recipe.scene_range,
        "recipe_duration_range": recipe.duration_range,
        "recipe_validation_status": validation_status,
        "recipe_validation_errors": list(validation_errors or []),
        "estimated_duration_seconds": estimated_duration_seconds,
        **(voice_resolution or {}),
    }


def apply_recipe_metadata(
    plan: Any,
    recipe: RecipeDefinition,
    *,
    validation_status: str,
    validation_errors: list[str] | None = None,
) -> None:
    plan.recipe_id = recipe.recipe_id
    plan.recipe_version = recipe.recipe_version
    plan.recipe_status = recipe.status
    plan.narrative_mode = recipe.narrative_mode
    plan.planner_id = recipe.planner_id
    plan.visual_theme_id = recipe.visual_theme_id
    plan.voice_profile_id = recipe.voice_profile_id
    plan.subtitle_style_id = recipe.subtitle_style_id
    plan.transition_profile_id = recipe.transition_profile_id
    plan.motion_profile_id = recipe.motion_profile_id
    plan.recipe_scene_range = recipe.scene_range
    plan.recipe_duration_range = recipe.duration_range
    plan.recipe_validation_status = validation_status
    plan.recipe_validation_errors = list(validation_errors or [])


__all__ = [
    "RecipeDefinition",
    "RecipeNotFoundError",
    "apply_recipe_metadata",
    "estimate_plan_duration",
    "format_recipe_list",
    "get_recipe",
    "list_recipes",
    "recipe_manifest",
    "validate_recipe_run",
]
