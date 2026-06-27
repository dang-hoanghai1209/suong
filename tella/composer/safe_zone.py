"""Safe-zone math for Tella's text overlays.

For 9:16 vertical video, the platform UI eats the top + bottom + left +
right edges of the canvas. Critical text (title, captions, brand) must
sit inside this band:

    Canvas 1080×1920
    Safe content: x = 90..990 (width 900), y = 285..1635 (height 1350)
    = exactly 4:5 within 9:16

For 16:9 horizontal video, no platform UI eats the canvas — we leave a
small margin (60 px) for aesthetic breathing room only.

See also: ``[[reference_reels_layout_safezones]]`` for the derivation.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SafeZone:
    canvas_w: int
    canvas_h: int
    left: int
    top: int
    right: int     # exclusive — like a "x + width" coordinate
    bottom: int    # exclusive

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def cx(self) -> int:
        return (self.left + self.right) // 2

    @property
    def cy(self) -> int:
        return (self.top + self.bottom) // 2


# Pre-computed for the two aspect ratios Tella renders at.
SAFE_ZONE_9_16 = SafeZone(canvas_w=1080, canvas_h=1920, left=90, top=285, right=990, bottom=1635)
SAFE_ZONE_16_9 = SafeZone(canvas_w=1920, canvas_h=1080, left=60, top=60, right=1860, bottom=1020)


def safe_zone_for(aspect_ratio: str) -> SafeZone:
    """Return the safe zone for an aspect ratio string."""
    if aspect_ratio == "9:16":
        return SAFE_ZONE_9_16
    if aspect_ratio == "16:9":
        return SAFE_ZONE_16_9
    raise ValueError(f"unsupported aspect ratio {aspect_ratio!r}")


def render_dims_for(aspect_ratio: str) -> tuple[int, int]:
    """Return ``(width, height)`` pixel dimensions for ``aspect_ratio``."""
    z = safe_zone_for(aspect_ratio)
    return z.canvas_w, z.canvas_h


__all__ = [
    "SAFE_ZONE_9_16",
    "SAFE_ZONE_16_9",
    "SafeZone",
    "render_dims_for",
    "safe_zone_for",
]
