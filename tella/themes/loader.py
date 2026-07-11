"""Load + validate theme JSON presets from this package.

Each theme is a JSON file next to this module. ``load_theme(name)`` reads,
validates the required keys, and returns a :class:`ThemeSpec`. Adding a
new theme = drop a new JSON file into this folder + extend
``KNOWN_THEMES`` in :mod:`tella._voice_pace` if you want a default pace
mapping (otherwise it falls back to ``medium``).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class KenBurns:
    start_scale: float = 1.0
    end_scale: float = 1.08
    easing: str = "linear"


@dataclass(frozen=True)
class ColorPalette:
    primary: str
    accent: str
    bg: str
    text: str


@dataclass(frozen=True)
class ImageGrade:
    enabled: bool = False
    brightness: float = 1.0
    contrast: float = 1.0
    saturation: float = 1.0
    overlay_color: str = "#000000"
    overlay_opacity: float = 0.0


@dataclass(frozen=True)
class ThemeSpec:
    """A loaded theme JSON. Immutable so the renderer can pass it around safely."""

    name: str
    display_name: str
    description: str
    image_style_suffix: str
    color_palette: ColorPalette
    font_family: str
    voice_pace_default: str       # slow | medium | fast
    voice_gender_default: str     # male | female
    transition: str               # fade | crossfade | cut
    ken_burns: KenBurns = field(default_factory=KenBurns)
    image_grade: ImageGrade = field(default_factory=ImageGrade)


_REQUIRED_KEYS = (
    "name", "display_name", "description", "image_style_suffix",
    "color_palette", "font_family", "voice_pace_default",
    "voice_gender_default", "transition", "ken_burns",
)


def list_themes() -> list[str]:
    """Return the names of all theme JSONs in this folder, sorted."""
    return sorted(p.stem for p in _THIS_DIR.glob("*.json"))


def load_theme(name: str) -> ThemeSpec:
    """Load + validate theme JSON ``<name>.json``.

    Raises:
        FileNotFoundError: when the JSON file isn't shipped with the package.
        ValueError: when required keys are missing or color palette is incomplete.
    """
    name = name.strip().lower()
    path = _THIS_DIR / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"Theme {name!r} not found at {path}. "
            f"Available themes: {list_themes()}"
        )

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    missing = [k for k in _REQUIRED_KEYS if k not in data]
    if missing:
        raise ValueError(
            f"Theme {name!r} missing required keys: {missing}"
        )

    palette_data = data["color_palette"]
    for k in ("primary", "accent", "bg", "text"):
        if k not in palette_data:
            raise ValueError(
                f"Theme {name!r} color_palette missing key {k!r}"
            )

    kb_data = data["ken_burns"]
    grade_data = data.get("image_grade") or {}
    return ThemeSpec(
        name=data["name"],
        display_name=data["display_name"],
        description=data["description"],
        image_style_suffix=data["image_style_suffix"],
        color_palette=ColorPalette(
            primary=palette_data["primary"],
            accent=palette_data["accent"],
            bg=palette_data["bg"],
            text=palette_data["text"],
        ),
        font_family=data["font_family"],
        voice_pace_default=data["voice_pace_default"],
        voice_gender_default=data["voice_gender_default"],
        transition=data["transition"],
        ken_burns=KenBurns(
            start_scale=float(kb_data.get("start_scale", 1.0)),
            end_scale=float(kb_data.get("end_scale", 1.08)),
            easing=str(kb_data.get("easing", "linear")),
        ),
        image_grade=ImageGrade(
            enabled=bool(grade_data.get("enabled", False)),
            brightness=float(grade_data.get("brightness", 1.0)),
            contrast=float(grade_data.get("contrast", 1.0)),
            saturation=float(grade_data.get("saturation", 1.0)),
            overlay_color=str(grade_data.get("overlay_color", "#000000")),
            overlay_opacity=float(grade_data.get("overlay_opacity", 0.0)),
        ),
    )


__all__ = [
    "ColorPalette",
    "ImageGrade",
    "KenBurns",
    "ThemeSpec",
    "list_themes",
    "load_theme",
]
