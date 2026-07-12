"""Recipe-owned music profiles; no renderer constants live here."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MusicProfile:
    profile_id: str
    recipe_id: str
    preferred_moods: tuple[str, ...]
    energy: str
    base_gain_db: float
    ducking_threshold: float = 0.025
    ducking_ratio: float = 8.0
    ducking_attack_ms: int = 20
    ducking_release_ms: int = 450
    fade_in_seconds: float = 0.6
    fade_out_seconds: float = 0.9


PROFILES: dict[str, MusicProfile] = {
    "emotional_soft": MusicProfile(
        profile_id="emotional_soft",
        recipe_id="emotional_symbolic_v1",
        preferred_moods=("melancholic", "healing", "soft_piano", "ambient"),
        energy="low",
        base_gain_db=-24.0,
    ),
    "life_insight_steady": MusicProfile(
        profile_id="life_insight_steady",
        recipe_id="life_insight_symbolic_v1",
        preferred_moods=("restrained", "mature", "steady", "ambient"),
        energy="medium_low",
        base_gain_db=-22.0,
    ),
    "practical_calm_rhythm": MusicProfile(
        profile_id="practical_calm_rhythm",
        recipe_id="practical_life_steps_v1",
        preferred_moods=("encouraging", "calm", "light_rhythm"),
        energy="medium_low",
        base_gain_db=-21.0,
    ),
}

RECIPE_DEFAULT_PROFILES = {
    profile.recipe_id: profile.profile_id for profile in PROFILES.values()
}


def get_music_profile(profile_id: str) -> MusicProfile:
    try:
        return PROFILES[profile_id]
    except KeyError as exc:
        raise ValueError(f"unknown music profile: {profile_id}") from exc


def profile_for_recipe(recipe_id: str, override: str = "") -> MusicProfile | None:
    profile_id = override or RECIPE_DEFAULT_PROFILES.get(recipe_id, "")
    if not profile_id:
        return None
    profile = get_music_profile(profile_id)
    if profile.recipe_id != recipe_id:
        raise ValueError(
            f"music profile {profile_id} is for {profile.recipe_id}, not {recipe_id}"
        )
    return profile


__all__ = [
    "MusicProfile",
    "PROFILES",
    "RECIPE_DEFAULT_PROFILES",
    "get_music_profile",
    "profile_for_recipe",
]
