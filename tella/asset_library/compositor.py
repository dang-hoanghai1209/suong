"""Procedural minimal compositor for resolved Asset-library scenes."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from tella.asset_library.background_renderer import CANVAS_SIZE, render_background_for_mood
from tella.asset_library.composition_templates import OBJECT_SLOTS, resolve_layout_template
from tella.asset_library.grounding import character_shadow, object_shadow, trim_transparent
from tella.asset_library.visual_profiles import HARMONIZATION_PROFILE

OBJECT_TARGET_HEIGHTS = {
    "pillow": 135,
    "phone_dark": 108,
    "empty_cup": 120,
    "paper_letter": 125,
    "flower_single": 185,
    "tissue_box": 112,
    "handbag": 155,
}
OBJECT_ROLES = {
    "pillow": "comfort",
    "phone_dark": "waiting_or_disconnection",
    "empty_cup": "absence",
    "paper_letter": "memory_or_release",
    "flower_single": "healing",
    "tissue_box": "grief",
    "handbag": "departure",
}
OBJECT_SLOT_PREFERENCES = {
    "pillow": "behind_left",
    "phone_dark": "floor_right_near",
    "empty_cup": "floor_left_near",
    "paper_letter": "floor_left_near",
    "flower_single": "floor_right_near",
    "tissue_box": "floor_left_near",
    "handbag": "floor_right_near",
}
OBJECT_ROTATIONS = {
    "phone_dark": 12,
    "paper_letter": -7,
    "flower_single": 3,
}


def _scale_height(image: Image.Image, target_height: int) -> Image.Image:
    width = max(1, round(image.width * target_height / image.height))
    return image.resize((width, target_height), Image.Resampling.LANCZOS)


def _target_height(height_range: tuple[int, int], seed: int) -> int:
    minimum, maximum = height_range
    return minimum + (int(seed) % (maximum - minimum + 1))


def _prepare_character(
    path: str,
    template: dict[str, Any],
    seed: int,
) -> tuple[Image.Image, dict[str, Any]]:
    with Image.open(path) as opened:
        source = trim_transparent(opened.convert("RGBA"))
    target_height = _target_height(template["character_target_height_range"], seed)
    character = _scale_height(source, target_height)
    ground_x = round(CANVAS_SIZE[0] * template["character_anchor_x"])
    ground_y = int(template["ground_y"])
    x = round(ground_x - character.width / 2)
    y = ground_y - character.height
    safe = template["character_safe_zone"]
    if x < safe["x"] or x + character.width > safe["x"] + safe["width"]:
        raise ValueError("Procedural character placement exceeded horizontal safe bounds")
    if y < safe["y"] or y + character.height > safe["y"] + safe["height"]:
        raise ValueError("Procedural character placement exceeded vertical safe bounds")
    placement = {
        "x": x,
        "y": y,
        "width": character.width,
        "height": character.height,
        "target_height": target_height,
        "rotation_degrees": 0,
        "ground_x": ground_x,
        "ground_y": ground_y,
        "ground_anchor": {
            "x": ground_x,
            "y": ground_y,
            "strategy": "trimmed_visible_alpha_bottom",
        },
    }
    return character, placement


def _select_object_slots(asset_ids: list[str], valid_slots: list[str]) -> list[tuple[str, str]]:
    selected: list[tuple[str, str]] = []
    used: set[str] = set()
    for asset_id in asset_ids[:2]:
        preferred = OBJECT_SLOT_PREFERENCES.get(asset_id, "")
        slot = preferred if preferred in valid_slots and preferred not in used else ""
        if not slot:
            slot = next((item for item in valid_slots if item not in used), "")
        if not slot:
            break
        used.add(slot)
        selected.append((asset_id, slot))
    return selected


def _prepare_object(
    asset_id: str,
    path: str,
    slot_name: str,
) -> tuple[Image.Image, dict[str, Any]]:
    with Image.open(path) as opened:
        rendered = trim_transparent(opened.convert("RGBA"))
    rendered = _scale_height(rendered, OBJECT_TARGET_HEIGHTS.get(asset_id, 125))
    scale_y = 0.66 if asset_id == "phone_dark" else 1.0
    if scale_y != 1.0:
        rendered = rendered.resize(
            (rendered.width, max(1, round(rendered.height * scale_y))),
            Image.Resampling.LANCZOS,
        )
    rotation = OBJECT_ROTATIONS.get(asset_id, 0)
    if rotation:
        rendered = rendered.rotate(
            -rotation,
            resample=Image.Resampling.BICUBIC,
            expand=True,
        )
    slot = OBJECT_SLOTS[slot_name]
    x = round(CANVAS_SIZE[0] * slot["anchor_x"] - rendered.width / 2)
    y = int(slot["ground_y"]) - rendered.height
    placement = {
        "x": x,
        "y": y,
        "width": rendered.width,
        "height": rendered.height,
        "rotation_degrees": rotation,
        "scale_x": 1.0,
        "scale_y": scale_y,
        "slot": slot_name,
        "layer": slot["layer"],
        "floor_perspective_applied": asset_id == "phone_dark",
    }
    if placement["width"] <= 0 or placement["height"] <= 0:
        raise ValueError(f"Procedural object {asset_id!r} has invalid rendered dimensions")
    if x < 70 or x + rendered.width > CANVAS_SIZE[0] - 70:
        raise ValueError(f"Procedural object {asset_id!r} exceeded edge margins")
    return rendered, placement


def _apply_harmonization(canvas: Image.Image) -> tuple[Image.Image, dict[str, Any]]:
    settings = dict(HARMONIZATION_PROFILE)
    if not settings["enabled"]:
        return canvas, settings
    color = settings["overlay_color"].lstrip("#")
    rgb = tuple(int(color[index:index + 2], 16) for index in (0, 2, 4))
    overlay = Image.new("RGBA", CANVAS_SIZE, (*rgb, int(settings["overlay_opacity"])))
    return Image.alpha_composite(canvas, overlay), settings


def compose_procedural_scene(
    *,
    request: Any,
    resolution: Any,
    output_path: Path,
) -> dict[str, Any]:
    mood = request.background_mood or request.emotion or "reflective"
    background, background_metadata = render_background_for_mood(
        mood,
        request.seed,
        CANVAS_SIZE,
    )
    template_name, template = resolve_layout_template(
        request.layout_template,
        request.composition_preset,
    )
    canvas = background.copy()
    character, character_placement = _prepare_character(
        resolution.character_processed_path,
        template,
        request.seed,
    )

    object_pairs = _select_object_slots(
        list(resolution.object_paths),
        list(template["valid_object_slots"]),
    )
    prepared: dict[str, tuple[Image.Image, dict[str, Any], Image.Image, dict[str, Any]]] = {}
    for asset_id, slot_name in object_pairs:
        rendered, placement = _prepare_object(
            asset_id,
            resolution.object_paths[asset_id],
            slot_name,
        )
        shadow_layer, shadow_metadata = object_shadow(CANVAS_SIZE, placement)
        prepared[asset_id] = rendered, placement, shadow_layer, shadow_metadata

    rear = [asset_id for asset_id, (_, placement, _, _) in prepared.items() if placement["layer"] == "rear"]
    front = [asset_id for asset_id, (_, placement, _, _) in prepared.items() if placement["layer"] == "front"]
    layer_order = ["procedural_background", "background_effects"]
    layers: list[dict[str, Any]] = [
        {"layer": "procedural_background", "x": 0, "y": 0, "width": 1080, "height": 1920, "rotation_degrees": 0},
        {"layer": "background_effects", "x": 0, "y": 0, "width": 1080, "height": 1920, "rotation_degrees": 0},
    ]
    for asset_id in rear:
        rendered, placement, shadow_layer, shadow_metadata = prepared[asset_id]
        canvas = Image.alpha_composite(canvas, shadow_layer)
        layer_order.append(f"rear_object_shadow:{asset_id}")
        layers.append({"layer": f"rear_object_shadow:{asset_id}", **shadow_metadata})
        canvas.alpha_composite(rendered, (placement["x"], placement["y"]))
        layer_order.append(f"rear_object:{asset_id}")
        layers.append({"layer": f"rear_object:{asset_id}", **placement})

    char_shadow_layer, char_shadow_metadata = character_shadow(
        CANVAS_SIZE,
        character_placement,
        template["pose_category"],
    )
    canvas = Image.alpha_composite(canvas, char_shadow_layer)
    layer_order.append("character_contact_shadow")
    layers.append({"layer": "character_contact_shadow", **char_shadow_metadata})
    canvas.alpha_composite(character, (character_placement["x"], character_placement["y"]))
    layer_order.append("character")
    layers.append({"layer": "character", **character_placement})

    for asset_id in front:
        rendered, placement, shadow_layer, shadow_metadata = prepared[asset_id]
        canvas = Image.alpha_composite(canvas, shadow_layer)
        layer_order.append(f"front_object_shadow:{asset_id}")
        layers.append({"layer": f"front_object_shadow:{asset_id}", **shadow_metadata})
        canvas.alpha_composite(rendered, (placement["x"], placement["y"]))
        layer_order.append(f"front_object:{asset_id}")
        layers.append({"layer": f"front_object:{asset_id}", **placement})

    canvas, harmonization = _apply_harmonization(canvas)
    layer_order.append("global_harmonization")
    layers.append({"layer": "global_harmonization", "x": 0, "y": 0, "width": 1080, "height": 1920, "rotation_degrees": 0})
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, format="PNG", optimize=True)

    object_metadata = []
    for asset_id in (*rear, *front):
        _, placement, _, shadow_metadata = prepared[asset_id]
        object_metadata.append({
            "asset_id": asset_id,
            "role": OBJECT_ROLES.get(asset_id, "supporting_symbol"),
            "processed_path": resolution.object_paths[asset_id],
            "slot": placement["slot"],
            "placement": placement,
            "shadow": shadow_metadata,
        })
    omitted = [asset_id for asset_id in resolution.object_paths if asset_id not in prepared]
    object_warnings = list(resolution.object_warnings)
    if omitted:
        object_warnings.append({
            "code": "procedural_object_limit",
            "message": "Procedural minimal mode renders at most two meaningful objects",
            "object_ids": omitted,
        })

    return {
        "schema_version": 3,
        "seed": request.seed,
        "canvas": {"width": CANVAS_SIZE[0], "height": CANVAS_SIZE[1]},
        "background": background_metadata,
        "layout_template": template_name,
        "layout_constraints": {
            "character_safe_zone": template["character_safe_zone"],
            "subtitle_safe_zone": template["subtitle_safe_zone"],
            "edge_margins": template["edge_margins"],
            "lower_grounding_zone": template["lower_grounding_zone"],
        },
        "character_request": request.to_dict(),
        "character": {
            "selected_semantic_id": resolution.selected_semantic_id,
            "selected_asset_id": resolution.selected_source_asset_id,
            "processed_path": resolution.character_processed_path,
            "selection_score": resolution.selection_score,
            "selection_reasons": resolution.selection_reasons,
            "selected_tier": resolution.selected_tier,
            "enabled": resolution.enabled_flag,
            "canonical": resolution.canonical_flag,
            "production_eligible": resolution.production_eligible,
            "quality_status": resolution.quality_status,
            "fallback_reason": resolution.fallback_reason,
            "repeat_fallback_reason": "",
            "score_breakdown": resolution.score_breakdown,
            "placement": character_placement,
            "contact_shadow": char_shadow_metadata,
        },
        "objects": object_metadata,
        "object_warnings": object_warnings,
        "layer_order": layer_order,
        "layers": layers,
        "harmonization": harmonization,
        "output": str(output_path),
    }


__all__ = ["compose_procedural_scene"]
