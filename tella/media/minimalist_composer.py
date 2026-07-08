"""Local Pillow composer for the minimalist_emotional theme.

This avoids asking an AI image model to redraw the same character in every
scene. The character, poses, and motifs are deliberately simple but stable.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

BG = "#b69a82"
INK = "#4e3a31"
HAIR = "#1f1a17"
FACE = "#d9b99d"
BLUSH = "#c98673"
MUSTARD = "#d4a33a"
RUST = "#b86445"
GLOW = "#f1cf74"
GREY = "#756a62"

POSES = (
    "front_standing",
    "side_sitting",
    "side_walking",
    "looking_at_light",
    "holding_paper_heart",
    "beside_lamp",
    "beside_flower",
    "under_scribble_cloud",
)

MOTIFS = (
    "lamp",
    "paper_heart",
    "scribble_cloud",
    "small_flower",
    "glowing_light",
    "empty_chair",
    "thin_path",
    "sunrise_circle",
    "tiny_bird",
    "small_window",
    "little_star",
    "seedling",
)


def compose_scene_image(
    out_path: Path,
    *,
    width: int = 768,
    height: int = 1344,
    pose_family: str = "front_standing",
    primary_motif: str = "paper_heart",
    scene_index: int = 1,
) -> Path:
    """Draw one complete static illustration and save it as JPEG."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    pose = pose_family if pose_family in POSES else "front_standing"
    motif = primary_motif if primary_motif in MOTIFS else "paper_heart"

    _draw_background_texture(draw, width, height, scene_index)

    # Keep the character comfortably above the caption lane. The lower 25%
    # starts at y ~= 1008 for 1344px; character feet stay around y 815-850.
    cx = int(width * 0.48)
    ground_y = int(height * 0.64)
    if pose in {"side_walking", "beside_flower"}:
        cx = int(width * 0.42)
    elif pose in {"beside_lamp", "side_sitting"}:
        cx = int(width * 0.46)

    _draw_motif(draw, motif, width, height, cx, ground_y, behind=True)
    _draw_character(draw, cx, ground_y, scale=1.0, pose=pose, motif=motif)
    _draw_motif(draw, motif, width, height, cx, ground_y, behind=False)

    img.save(out_path, "JPEG", quality=92)
    return out_path


def _draw_background_texture(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    scene_index: int,
) -> None:
    # A few low-contrast imperfect lines keep it hand-drawn without clutter.
    y = int(height * (0.19 + (scene_index % 4) * 0.035))
    draw.arc((80, y, width - 90, y + 80), 185, 350, fill="#a88c75", width=2)
    draw.arc((130, y + 140, width - 130, y + 210), 190, 345, fill="#aa907a", width=1)


def _draw_character(
    draw: ImageDraw.ImageDraw,
    cx: int,
    ground_y: int,
    *,
    scale: float,
    pose: str,
    motif: str,
) -> None:
    unit = int(86 * scale)
    head_r = int(32 * scale)
    head_cy = ground_y - int(245 * scale)
    body_top = head_cy + head_r + int(12 * scale)
    body_bottom = ground_y - int(72 * scale)
    side = pose in {"side_sitting", "side_walking"}

    if pose == "side_sitting":
        body_bottom = ground_y - int(98 * scale)
        head_cy = ground_y - int(238 * scale)
        body_top = head_cy + head_r + int(12 * scale)

    # Hair first, then face, so bob frames the head consistently.
    hair_box = (
        cx - head_r - int(8 * scale),
        head_cy - head_r - int(8 * scale),
        cx + head_r + int(8 * scale),
        head_cy + head_r + int(18 * scale),
    )
    draw.rounded_rectangle(hair_box, radius=int(30 * scale), fill=HAIR)
    face_box = (cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r)
    draw.ellipse(face_box, fill=FACE, outline=INK, width=max(2, int(3 * scale)))
    # Straight bob edge at chin.
    draw.line(
        (cx - head_r - 4, head_cy + head_r - 3, cx + head_r + 4, head_cy + head_r - 3),
        fill=HAIR,
        width=max(3, int(5 * scale)),
    )

    eye_dx = int(12 * scale)
    if side:
        draw.ellipse((cx + eye_dx - 3, head_cy - 5, cx + eye_dx + 3, head_cy + 1), fill=INK)
        draw.line((cx + 8, head_cy + 14, cx + 21, head_cy + 13), fill=INK, width=2)
        draw.ellipse((cx - 2, head_cy + 10, cx + 8, head_cy + 18), fill=BLUSH)
    else:
        draw.ellipse((cx - eye_dx - 3, head_cy - 5, cx - eye_dx + 3, head_cy + 1), fill=INK)
        draw.ellipse((cx + eye_dx - 3, head_cy - 5, cx + eye_dx + 3, head_cy + 1), fill=INK)
        mouth_y = head_cy + int(15 * scale)
        draw.arc((cx - 10, mouth_y - 5, cx + 10, mouth_y + 7), 10, 170, fill=INK, width=2)
        draw.ellipse((cx - 25, head_cy + 7, cx - 13, head_cy + 17), fill=BLUSH)
        draw.ellipse((cx + 13, head_cy + 7, cx + 25, head_cy + 17), fill=BLUSH)

    dress = [
        (cx, body_top),
        (cx - int(unit * 0.44), body_bottom),
        (cx + int(unit * 0.44), body_bottom),
    ]
    draw.polygon(dress, fill=MUSTARD, outline=INK)

    if pose == "holding_paper_heart" or motif == "paper_heart":
        hand_y = body_top + int(54 * scale)
        draw.line((cx - 28, body_top + 18, cx - 12, hand_y), fill=RUST, width=5)
        draw.line((cx + 28, body_top + 18, cx + 12, hand_y), fill=RUST, width=5)
        _draw_heart(draw, cx, hand_y - 4, int(18 * scale), fill="#c76550")
    elif pose == "looking_at_light":
        draw.line((cx - 28, body_top + 22, cx - 46, body_top + 70), fill=RUST, width=5)
        draw.line((cx + 28, body_top + 22, cx + 48, body_top + 44), fill=RUST, width=5)
    else:
        draw.line((cx - 27, body_top + 22, cx - 48, body_top + 82), fill=RUST, width=5)
        draw.line((cx + 27, body_top + 22, cx + 48, body_top + 82), fill=RUST, width=5)

    if pose == "side_sitting":
        seat_y = ground_y - 78
        draw.line((cx - 70, seat_y, cx + 75, seat_y), fill=INK, width=3)
        draw.line((cx - 42, body_bottom, cx + 35, seat_y + 18), fill=INK, width=4)
        draw.line((cx + 10, body_bottom, cx + 76, seat_y + 18), fill=INK, width=4)
    else:
        leg_top = body_bottom
        foot_y = ground_y - int(24 * scale)
        offset = int(18 * scale)
        if pose == "side_walking":
            draw.line((cx - 10, leg_top, cx - 48, foot_y), fill=INK, width=4)
            draw.line((cx + 12, leg_top, cx + 50, foot_y - 14), fill=INK, width=4)
        else:
            draw.line((cx - offset, leg_top, cx - offset - 8, foot_y), fill=INK, width=4)
            draw.line((cx + offset, leg_top, cx + offset + 8, foot_y), fill=INK, width=4)

    draw.arc((cx - 75, ground_y - 18, cx + 75, ground_y + 12), 0, 180, fill="#8b6f5d", width=2)


def _draw_motif(
    draw: ImageDraw.ImageDraw,
    motif: str,
    width: int,
    height: int,
    cx: int,
    ground_y: int,
    *,
    behind: bool,
) -> None:
    mx = cx + int(width * 0.23)
    my = ground_y - int(height * 0.21)

    background_motifs = {"small_window", "sunrise_circle", "empty_chair", "scribble_cloud"}
    if behind != (motif in background_motifs):
        return

    if motif == "lamp":
        draw.line((mx, ground_y - 105, mx, ground_y - 22), fill=INK, width=3)
        draw.polygon(
            [
                (mx - 35, ground_y - 115),
                (mx + 35, ground_y - 115),
                (mx + 22, ground_y - 75),
                (mx - 22, ground_y - 75),
            ],
            fill=GLOW,
            outline=INK,
        )
    elif motif == "paper_heart":
        _draw_heart(draw, mx, my + 38, 24, fill="#c76550")
    elif motif == "scribble_cloud":
        for i in range(3):
            draw.arc(
                (cx - 80 + i * 32, my - 70, cx + 35 + i * 32, my - 5),
                20,
                330,
                fill=GREY,
                width=3,
            )
    elif motif == "small_flower":
        _draw_flower(draw, mx, ground_y - 62)
    elif motif == "glowing_light":
        draw.ellipse((mx - 34, my - 34, mx + 34, my + 34), fill=GLOW, outline=INK)
    elif motif == "empty_chair":
        x, y = cx + 95, ground_y - 135
        draw.rectangle((x, y, x + 70, y + 58), outline=INK, width=3)
        draw.line((x, y + 58, x - 16, y + 104), fill=INK, width=3)
        draw.line((x + 70, y + 58, x + 84, y + 104), fill=INK, width=3)
    elif motif == "thin_path":
        draw.arc((cx - 130, ground_y - 35, cx + 220, ground_y + 90), 180, 350, fill=INK, width=3)
    elif motif == "sunrise_circle":
        draw.ellipse((mx - 42, ground_y - 205, mx + 42, ground_y - 121), fill=GLOW, outline=INK)
        draw.line((mx - 70, ground_y - 121, mx + 70, ground_y - 121), fill=INK, width=2)
    elif motif == "tiny_bird":
        draw.arc((mx - 34, my, mx, my + 28), 210, 340, fill=INK, width=3)
        draw.arc((mx, my, mx + 34, my + 28), 200, 330, fill=INK, width=3)
    elif motif == "small_window":
        x, y = mx - 50, my - 55
        draw.rectangle((x, y, x + 110, y + 95), outline=INK, width=3)
        draw.line((x + 55, y, x + 55, y + 95), fill=INK, width=2)
        draw.line((x, y + 48, x + 110, y + 48), fill=INK, width=2)
    elif motif == "little_star":
        _draw_star(draw, mx, my, 28)
    elif motif == "seedling":
        draw.line((mx, ground_y - 40, mx, ground_y - 95), fill=INK, width=3)
        draw.ellipse((mx - 28, ground_y - 95, mx, ground_y - 70), fill="#87966f", outline=INK)
        draw.ellipse((mx, ground_y - 95, mx + 28, ground_y - 70), fill="#87966f", outline=INK)


def _draw_heart(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, *, fill: str) -> None:
    points = [
        (cx, cy + size),
        (cx - size, cy),
        (cx - size // 2, cy - size),
        (cx, cy - size // 3),
        (cx + size // 2, cy - size),
        (cx + size, cy),
    ]
    draw.polygon(points, fill=fill, outline=INK)


def _draw_flower(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
    draw.line((x, y + 45, x, y - 8), fill=INK, width=3)
    for angle in range(0, 360, 72):
        dx = int(math.cos(math.radians(angle)) * 16)
        dy = int(math.sin(math.radians(angle)) * 12)
        draw.ellipse((x + dx - 10, y + dy - 10, x + dx + 10, y + dy + 10), fill=RUST, outline=INK)
    draw.ellipse((x - 8, y - 8, x + 8, y + 8), fill=GLOW, outline=INK)


def _draw_star(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int) -> None:
    points = []
    for i in range(10):
        r = size if i % 2 == 0 else size // 2
        angle = -math.pi / 2 + i * math.pi / 5
        points.append((cx + int(math.cos(angle) * r), cy + int(math.sin(angle) * r)))
    draw.polygon(points, fill=GLOW, outline=INK)


__all__ = ["MOTIFS", "POSES", "compose_scene_image"]
