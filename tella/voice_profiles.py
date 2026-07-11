"""Recipe-aware voice profile definitions and deterministic resolution."""
from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("tella.voice_profiles")


class VoiceProfileNotFoundError(ValueError):
    """Raised when a requested voice profile is not registered."""


class VoiceProfileDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    profile_id: str = Field(..., pattern=r"^[a-z][a-z0-9_]*$", max_length=80)
    provider: str = Field(..., min_length=2, max_length=40)
    voice: str = Field(..., min_length=2, max_length=120)
    rate: str = Field(..., pattern=r"^[+-]?\d{1,3}%$")
    role: str = Field(..., min_length=2, max_length=160)
    suitable_narrative_modes: list[str] = Field(..., min_length=1)


class VoiceResolution(BaseModel):
    model_config = ConfigDict(frozen=True)

    requested_voice_profile_id: str = ""
    resolved_voice_profile_id: str = ""
    voice_resolution_source: Literal[
        "explicit_cli",
        "cli_profile",
        "recipe_profile",
        "cli_profile_with_cli_override",
        "recipe_profile_with_cli_override",
        "legacy_default",
    ]
    resolved_tts_provider: str = "edge"
    resolved_voice: str = ""
    resolved_voice_rate: str = ""
    recipe_voice_override_applied: bool = False
    voice_profile_compatibility_status: Literal[
        "compatible",
        "warning",
        "not_checked",
        "not_applicable",
    ] = "not_checked"
    direct_override_fields: list[str] = Field(default_factory=list)


_PROFILES = {
    profile.profile_id: profile
    for profile in (
        VoiceProfileDefinition(
            profile_id="soft_female_vi",
            provider="edge",
            voice="vi-VN-HoaiMyNeural",
            rate="-10%",
            role="gentle emotional narrator",
            suitable_narrative_modes=["emotional_reflection"],
        ),
        VoiceProfileDefinition(
            profile_id="firm_male_vi",
            provider="edge",
            voice="vi-VN-NamMinhNeural",
            rate="-5%",
            role="calm direct insight narrator",
            suitable_narrative_modes=["life_insight"],
        ),
        VoiceProfileDefinition(
            profile_id="clear_female_vi",
            provider="edge",
            voice="vi-VN-HoaiMyNeural",
            rate="-2%",
            role="clear practical guide",
            suitable_narrative_modes=["practical_steps"],
        ),
    )
}


def get_voice_profile(profile_id: str) -> VoiceProfileDefinition:
    key = (profile_id or "").strip()
    profile = _PROFILES.get(key)
    if profile is None:
        available = ", ".join(sorted(_PROFILES))
        raise VoiceProfileNotFoundError(
            f"unknown voice profile {profile_id!r}; available profiles: {available}"
        )
    return profile


def list_voice_profiles() -> list[VoiceProfileDefinition]:
    return [_PROFILES[key] for key in sorted(_PROFILES)]


def format_voice_profile_list() -> str:
    return "\n".join(
        f"{profile.profile_id} | provider={profile.provider} "
        f"voice={profile.voice} rate={profile.rate} role={profile.role} "
        f"modes={','.join(profile.suitable_narrative_modes)}"
        for profile in list_voice_profiles()
    )


def validate_voice_profiles() -> list[str]:
    errors: list[str] = []
    for profile_id, profile in _PROFILES.items():
        if profile.profile_id != profile_id:
            errors.append(
                f"registry key {profile_id!r} does not match profile id "
                f"{profile.profile_id!r}"
            )
        if profile.provider != "edge":
            errors.append(
                f"profile {profile_id} uses unsupported provider {profile.provider!r}"
            )
        if not profile.voice.startswith("vi-VN-"):
            errors.append(
                f"profile {profile_id} voice {profile.voice!r} is not Vietnamese"
            )

    from tella.recipes import list_recipes

    for recipe in list_recipes():
        if recipe.voice_profile_id not in _PROFILES:
            errors.append(
                f"recipe {recipe.recipe_id} references unknown voice profile "
                f"{recipe.voice_profile_id!r}"
            )
    return errors


def resolve_voice(
    *,
    explicit_provider: str | None = None,
    explicit_voice: str | None = None,
    explicit_voice_rate: str | None = None,
    explicit_profile_id: str | None = None,
    recipe_profile_id: str | None = None,
    narrative_mode: str | None = None,
    legacy_provider: str = "edge",
    legacy_voice: str = "",
    legacy_rate: str = "",
) -> VoiceResolution:
    requested_profile_id = (explicit_profile_id or recipe_profile_id or "").strip()
    explicit_profile = (
        get_voice_profile(explicit_profile_id) if explicit_profile_id else None
    )
    recipe_profile = get_voice_profile(recipe_profile_id) if recipe_profile_id else None

    if explicit_profile is not None:
        base_source = "cli_profile"
        profile = explicit_profile
    elif recipe_profile is not None:
        base_source = "recipe_profile"
        profile = recipe_profile
    else:
        base_source = "legacy_default"
        profile = None

    if profile is not None:
        provider = profile.provider
        voice = profile.voice
        rate = profile.rate
        compatibility = _compatibility(profile, narrative_mode)
    else:
        provider = (legacy_provider or "edge").strip().lower()
        voice = (legacy_voice or "").strip()
        rate = (legacy_rate or "").strip()
        compatibility = "not_checked"

    direct_overrides: list[str] = []
    if explicit_provider is not None:
        provider = explicit_provider.strip().lower()
        direct_overrides.append("provider")
    if explicit_voice is not None:
        voice = explicit_voice.strip()
        direct_overrides.append("voice")
    if explicit_voice_rate is not None:
        rate = explicit_voice_rate.strip()
        direct_overrides.append("rate")

    if profile is not None:
        source = (
            f"{base_source}_with_cli_override"
            if direct_overrides
            else base_source
        )
    else:
        source = "explicit_cli" if direct_overrides else "legacy_default"
        if direct_overrides:
            compatibility = "not_applicable"

    recipe_override = False
    if recipe_profile is not None:
        recipe_override = (
            provider,
            voice,
            rate,
        ) != (
            recipe_profile.provider,
            recipe_profile.voice,
            recipe_profile.rate,
        )

    return VoiceResolution(
        requested_voice_profile_id=requested_profile_id,
        resolved_voice_profile_id=profile.profile_id if profile else "",
        voice_resolution_source=source,
        resolved_tts_provider=provider,
        resolved_voice=voice,
        resolved_voice_rate=rate,
        recipe_voice_override_applied=recipe_override,
        voice_profile_compatibility_status=compatibility,
        direct_override_fields=direct_overrides,
    )


def _compatibility(
    profile: VoiceProfileDefinition,
    narrative_mode: str | None,
) -> str:
    if not narrative_mode:
        return "not_checked"
    if narrative_mode in profile.suitable_narrative_modes:
        return "compatible"
    logger.warning(
        "voice profile %s is not suggested for narrative mode %s; suggested modes=%s",
        profile.profile_id,
        narrative_mode,
        ",".join(profile.suitable_narrative_modes),
    )
    return "warning"


def apply_voice_resolution_metadata(plan: Any, resolution: VoiceResolution) -> None:
    resolved_voice = resolution.resolved_voice or plan.voice_name
    resolved_rate = resolution.resolved_voice_rate or plan.voice_edge_rate
    plan.requested_voice_profile_id = resolution.requested_voice_profile_id
    plan.resolved_voice_profile_id = resolution.resolved_voice_profile_id
    plan.voice_resolution_source = resolution.voice_resolution_source
    plan.resolved_tts_provider = resolution.resolved_tts_provider
    plan.resolved_voice = resolved_voice
    plan.resolved_voice_rate = resolved_rate
    plan.recipe_voice_override_applied = resolution.recipe_voice_override_applied
    plan.voice_profile_compatibility_status = (
        resolution.voice_profile_compatibility_status
    )
    if resolution.resolved_voice:
        plan.voice_name = resolution.resolved_voice
    if resolution.resolved_voice_rate:
        plan.voice_edge_rate = resolution.resolved_voice_rate


__all__ = [
    "VoiceProfileDefinition",
    "VoiceProfileNotFoundError",
    "VoiceResolution",
    "apply_voice_resolution_metadata",
    "format_voice_profile_list",
    "get_voice_profile",
    "list_voice_profiles",
    "resolve_voice",
    "validate_voice_profiles",
]
