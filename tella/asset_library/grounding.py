"""Ground anchors and subtle contact-shadow geometry."""
from __future__ import annotations

from typing import Any

from PIL import Image, ImageDraw, ImageFilter


def trim_transparent(image: Image.Image) -> Image.Image:
    alpha = image.getchannel("A")
    bounds = alpha.getbbox()
    return image.crop(bounds) if bounds else image


def contact_shadow(
    canvas_size: tuple[int, int],
    *,
    center_x: int,
    ground_y: int,
    width: int,
    height: int,
    opacity: int,
    blur: int,
) -> tuple[Image.Image, dict[str, Any]]:
    layer = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    box = (
        round(center_x - width / 2),
        round(ground_y - height / 2),
        round(center_x + width / 2),
        round(ground_y + height / 2),
    )
    ImageDraw.Draw(layer).ellipse(box, fill=(35, 25, 21, opacity))
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    return layer, {
        "x": box[0],
        "y": box[1],
        "width": box[2] - box[0],
        "height": box[3] - box[1],
        "opacity": opacity,
        "blur_radius": blur,
        "color": "#231915",
    }


def character_shadow(
    canvas_size: tuple[int, int],
    placement: dict[str, int],
    pose_category: str,
) -> tuple[Image.Image, dict[str, Any]]:
    footprint_ratio = 0.62 if pose_category == "sitting" else 0.42
    return contact_shadow(
        canvas_size,
        center_x=placement["ground_x"],
        ground_y=placement["ground_y"] - 2,
        width=max(90, round(placement["width"] * footprint_ratio)),
        height=24 if pose_category == "sitting" else 18,
        opacity=40 if pose_category == "sitting" else 34,
        blur=12,
    )


def object_shadow(
    canvas_size: tuple[int, int],
    placement: dict[str, Any],
) -> tuple[Image.Image, dict[str, Any]]:
    return contact_shadow(
        canvas_size,
        center_x=round(placement["x"] + placement["width"] / 2),
        ground_y=round(placement["y"] + placement["height"] - 1),
        width=max(28, round(placement["width"] * 0.72)),
        height=max(8, round(placement["height"] * 0.10)),
        opacity=30,
        blur=7,
    )


__all__ = ["character_shadow", "contact_shadow", "object_shadow", "trim_transparent"]
