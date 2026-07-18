"""Deterministic code-only backgrounds for the minimal compositor."""
from __future__ import annotations

import random
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

from tella.asset_library.visual_profiles import BACKGROUND_PROFILES, profile_for_mood

CANVAS_SIZE = (1080, 1920)
SUPPORTED_BACKGROUND_MODES = {"scenic_asset", "procedural_minimal"}


def resolve_background_mode(value: str | None = None) -> str:
    import os

    raw = value if value is not None else os.environ.get("TELLA_ASSET_BACKGROUND_MODE")
    mode = (raw or "scenic_asset").strip().lower()
    if mode not in SUPPORTED_BACKGROUND_MODES:
        choices = ", ".join(sorted(SUPPORTED_BACKGROUND_MODES))
        raise ValueError(
            f"Unsupported TELLA_ASSET_BACKGROUND_MODE={mode!r}; expected one of: {choices}"
        )
    return mode


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


def render_procedural_background(
    profile_name: str,
    seed: int,
    size: tuple[int, int] = CANVAS_SIZE,
) -> tuple[Image.Image, dict[str, Any]]:
    if profile_name not in BACKGROUND_PROFILES:
        raise ValueError(f"Unknown procedural background profile: {profile_name!r}")
    profile = BACKGROUND_PROFILES[profile_name]
    width, height = size
    start = _rgb(profile["base_color"])
    end = _rgb(profile["gradient_end"])
    image = Image.new("RGB", size, start)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(round(a + (b - a) * ratio) for a, b in zip(start, end))
        draw.line((0, y, width, y), fill=color)

    haze = Image.new("RGBA", size, (0, 0, 0, 0))
    haze_draw = ImageDraw.Draw(haze)
    haze_color = (*_rgb(profile["ground_haze"]), int(profile["ground_haze_opacity"]))
    haze_draw.ellipse((-width // 5, int(height * 0.70), int(width * 1.2), int(height * 1.08)), fill=haze_color)
    haze = haze.filter(ImageFilter.GaussianBlur(95))
    image = Image.alpha_composite(image.convert("RGBA"), haze)

    mask_size = (max(1, width // 4), max(1, height // 4))
    vignette_mask = Image.new("L", mask_size, 0)
    pixels = vignette_mask.load()
    center_x, center_y = mask_size[0] / 2, mask_size[1] * 0.47
    max_distance = (center_x**2 + center_y**2) ** 0.5
    for y in range(mask_size[1]):
        for x in range(mask_size[0]):
            distance = (((x - center_x) ** 2 + (y - center_y) ** 2) ** 0.5) / max_distance
            pixels[x, y] = round(max(0.0, min(1.0, (distance - 0.35) / 0.65)) * int(profile["vignette_opacity"]))
    vignette_mask = vignette_mask.resize(size, Image.Resampling.BICUBIC)
    vignette = Image.new("RGBA", size, (16, 12, 10, 0))
    vignette.putalpha(vignette_mask)
    image = Image.alpha_composite(image, vignette)

    rng = random.Random(int(seed))
    noise_size = (max(1, width // 8), max(1, height // 8))
    noise_values = bytes(rng.randrange(96, 160) for _ in range(noise_size[0] * noise_size[1]))
    noise = Image.frombytes("L", noise_size, noise_values).resize(size, Image.Resampling.BICUBIC)
    noise_layer = Image.merge("RGBA", (noise, noise, noise, Image.new("L", size, int(profile["texture_opacity"]))))
    image = Image.alpha_composite(image, noise_layer)

    metadata = {
        "mode": "procedural_minimal",
        "profile": profile_name,
        "base_color": profile["base_color"],
        "gradient": {
            "type": "vertical",
            "start_color": profile["base_color"],
            "end_color": profile["gradient_end"],
        },
        "vignette": {
            "enabled": True,
            "color": "#100C0A",
            "opacity": profile["vignette_opacity"],
        },
        "grounding_haze": {
            "enabled": True,
            "color": profile["ground_haze"],
            "opacity": profile["ground_haze_opacity"],
        },
        "texture": {"enabled": True, "opacity": profile["texture_opacity"]},
        "seed": int(seed),
    }
    return image, metadata


def render_background_for_mood(
    mood: str,
    seed: int,
    size: tuple[int, int] = CANVAS_SIZE,
) -> tuple[Image.Image, dict[str, Any]]:
    return render_procedural_background(profile_for_mood(mood), seed, size)


__all__ = [
    "CANVAS_SIZE",
    "SUPPORTED_BACKGROUND_MODES",
    "render_background_for_mood",
    "render_procedural_background",
    "resolve_background_mode",
]
