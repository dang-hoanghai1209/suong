"""Central layout templates and object slots for procedural scenes."""
from __future__ import annotations

from typing import Any


OBJECT_SLOTS: dict[str, dict[str, Any]] = {
    "floor_left_near": {"anchor_x": 0.24, "ground_y": 1705, "layer": "front"},
    "floor_right_near": {"anchor_x": 0.76, "ground_y": 1705, "layer": "front"},
    "floor_left_far": {"anchor_x": 0.17, "ground_y": 1635, "layer": "rear"},
    "floor_right_far": {"anchor_x": 0.83, "ground_y": 1635, "layer": "rear"},
    "beside_character": {"anchor_x": 0.68, "ground_y": 1685, "layer": "front"},
    "behind_left": {"anchor_x": 0.30, "ground_y": 1640, "layer": "rear"},
    "behind_right": {"anchor_x": 0.70, "ground_y": 1640, "layer": "rear"},
}


COMPOSITION_TEMPLATES: dict[str, dict[str, Any]] = {
    "floor_sit_center": {
        "pose_category": "sitting",
        "character_anchor_x": 0.50,
        "character_target_height_range": (590, 640),
        "ground_y": 1680,
        "character_safe_zone": {"x": 120, "y": 760, "width": 840, "height": 960},
        "subtitle_safe_zone": {"x": 90, "y": 190, "width": 900, "height": 430},
        "edge_margins": {"left": 90, "right": 90, "top": 140, "bottom": 150},
        "lower_grounding_zone": {"top": 1540, "bottom": 1740},
        "valid_object_slots": ["floor_left_near", "floor_right_near", "behind_left"],
    },
    "stand_center": {
        "pose_category": "standing",
        "character_anchor_x": 0.50,
        "character_target_height_range": (630, 680),
        "ground_y": 1690,
        "character_safe_zone": {"x": 170, "y": 720, "width": 740, "height": 1010},
        "subtitle_safe_zone": {"x": 90, "y": 190, "width": 900, "height": 430},
        "edge_margins": {"left": 110, "right": 110, "top": 140, "bottom": 140},
        "lower_grounding_zone": {"top": 1570, "bottom": 1740},
        "valid_object_slots": ["floor_left_near", "floor_right_near", "behind_left", "behind_right"],
    },
    "stand_left": {
        "pose_category": "standing",
        "character_anchor_x": 0.38,
        "character_target_height_range": (620, 670),
        "ground_y": 1690,
        "character_safe_zone": {"x": 100, "y": 730, "width": 760, "height": 1000},
        "subtitle_safe_zone": {"x": 90, "y": 190, "width": 900, "height": 430},
        "edge_margins": {"left": 90, "right": 90, "top": 140, "bottom": 140},
        "lower_grounding_zone": {"top": 1570, "bottom": 1740},
        "valid_object_slots": ["floor_left_near", "floor_right_near", "behind_right"],
    },
    "stand_right": {
        "pose_category": "standing",
        "character_anchor_x": 0.62,
        "character_target_height_range": (620, 670),
        "ground_y": 1690,
        "character_safe_zone": {"x": 220, "y": 730, "width": 760, "height": 1000},
        "subtitle_safe_zone": {"x": 90, "y": 190, "width": 900, "height": 430},
        "edge_margins": {"left": 90, "right": 90, "top": 140, "bottom": 140},
        "lower_grounding_zone": {"top": 1570, "bottom": 1740},
        "valid_object_slots": ["floor_left_near", "floor_right_near", "behind_left"],
    },
    "low_emotional_focus": {
        "pose_category": "sitting",
        "character_anchor_x": 0.48,
        "character_target_height_range": (560, 610),
        "ground_y": 1690,
        "character_safe_zone": {"x": 150, "y": 840, "width": 780, "height": 890},
        "subtitle_safe_zone": {"x": 90, "y": 170, "width": 900, "height": 470},
        "edge_margins": {"left": 100, "right": 100, "top": 140, "bottom": 140},
        "lower_grounding_zone": {"top": 1570, "bottom": 1750},
        "valid_object_slots": ["floor_left_near", "floor_right_near", "floor_left_far"],
    },
}


LEGACY_PRESET_TEMPLATE_MAP = {
    "bedroom_floor_sitting": "floor_sit_center",
    "window_waiting": "floor_sit_center",
    "cafe_sitting": "low_emotional_focus",
    "bus_stop_waiting": "stand_center",
    "floor_reflection": "floor_sit_center",
    "tear_wiping": "stand_left",
    "park_acceptance": "stand_center",
}


def resolve_layout_template(requested: str, legacy_preset: str) -> tuple[str, dict[str, Any]]:
    name = requested or LEGACY_PRESET_TEMPLATE_MAP.get(legacy_preset, "stand_center")
    try:
        return name, COMPOSITION_TEMPLATES[name]
    except KeyError as exc:
        raise ValueError(f"Unknown procedural composition template: {name!r}") from exc


__all__ = [
    "COMPOSITION_TEMPLATES",
    "LEGACY_PRESET_TEMPLATE_MAP",
    "OBJECT_SLOTS",
    "resolve_layout_template",
]
