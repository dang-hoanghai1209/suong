"""Central visual profiles for Asset-library procedural scenes."""
from __future__ import annotations

from typing import Any


BACKGROUND_PROFILES: dict[str, dict[str, Any]] = {
    "near_black": {
        "base_color": "#161412",
        "gradient_end": "#241D19",
        "vignette_opacity": 42,
        "ground_haze": "#342923",
        "ground_haze_opacity": 28,
        "texture_opacity": 5,
    },
    "dark_brown": {
        "base_color": "#2A211D",
        "gradient_end": "#45362F",
        "vignette_opacity": 36,
        "ground_haze": "#5A473D",
        "ground_haze_opacity": 24,
        "texture_opacity": 5,
    },
    "muted_taupe": {
        "base_color": "#66574F",
        "gradient_end": "#8B776B",
        "vignette_opacity": 30,
        "ground_haze": "#A18B7D",
        "ground_haze_opacity": 22,
        "texture_opacity": 4,
    },
    "warm_beige": {
        "base_color": "#9E8877",
        "gradient_end": "#C2AB98",
        "vignette_opacity": 24,
        "ground_haze": "#D5C0AD",
        "ground_haze_opacity": 22,
        "texture_opacity": 4,
    },
    "soft_warm_light": {
        "base_color": "#B8A18D",
        "gradient_end": "#D9C5AF",
        "vignette_opacity": 20,
        "ground_haze": "#E4D3C0",
        "ground_haze_opacity": 24,
        "texture_opacity": 3,
    },
}


MOOD_PROFILE_MAP = {
    "lonely": "dark_brown",
    "sad": "dark_brown",
    "worried": "dark_brown",
    "emotional_low_point": "near_black",
    "reflective": "muted_taupe",
    "healing": "warm_beige",
    "recovery": "warm_beige",
    "accepting": "soft_warm_light",
}


HARMONIZATION_PROFILE = {
    "enabled": True,
    "overlay_color": "#B78369",
    "overlay_opacity": 7,
    "contrast_compression": 0.02,
    "saturation_normalization": 0.0,
}


def profile_for_mood(mood: str) -> str:
    return MOOD_PROFILE_MAP.get((mood or "").strip().lower(), "muted_taupe")


__all__ = [
    "BACKGROUND_PROFILES",
    "HARMONIZATION_PROFILE",
    "MOOD_PROFILE_MAP",
    "profile_for_mood",
]
