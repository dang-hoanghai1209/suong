#!/usr/bin/env python
"""Semantic asset selection and deterministic V2 test-scene composition."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter


SCRIPT_DIR = Path(__file__).resolve().parent
V2_ROOT = Path(r"D:\tella-assets-staging\mvp_v1_processed_v2")
SEMANTICS_FILE = SCRIPT_DIR / "asset_semantics_patch.json"
PRESETS_FILE = SCRIPT_DIR / "test_scene_presets.json"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def semantic_score(candidate: dict[str, Any], request: dict[str, Any]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    if candidate["action"] == request["action"]:
        score += 100
        reasons.append("exact action +100")
    if candidate.get("emotion") == request.get("emotion"):
        score += 50
        reasons.append("exact emotion +50")
    elif request.get("emotion") in candidate.get("related_emotions", []):
        score += 25
        reasons.append("related emotion +25")
    if candidate.get("direction") == request.get("direction"):
        score += 5
        reasons.append("exact direction +5")
    if candidate.get("enabled_by_default"):
        score += 15
        reasons.append("enabled by default +15")
    if candidate.get("canonical"):
        score += 10
        reasons.append("canonical +10")
    if candidate.get("tier") == "core":
        score += 10
        reasons.append("core tier +10")
    elif candidate.get("tier") == "backup":
        score -= 5
        reasons.append("backup tier -5")
    return score, reasons


def select_semantic_asset(
    semantics: dict[str, Any], request: dict[str, Any], seed: int
) -> dict[str, Any]:
    candidates = [
        candidate for candidate in semantics["assets"]
        if candidate["character_id"] == request["character_id"]
        and candidate["action"] == request["action"]
    ]
    if not candidates:
        raise LookupError(f"No semantic candidates for {request}")
    ranked = []
    for candidate in candidates:
        score, reasons = semantic_score(candidate, request)
        tie_material = f'{seed}:{candidate["semantic_id"]}'.encode("utf-8")
        tie_break = hashlib.sha256(tie_material).hexdigest()
        ranked.append((score, tie_break, candidate, reasons))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    score, _, selected, reasons = ranked[0]
    return {**selected, "selection_score": score, "selection_reasons": reasons}


def fit_cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    width, height = size
    scale = max(width / image.width, height / image.height)
    resized = image.resize((math.ceil(image.width * scale), math.ceil(image.height * scale)), Image.Resampling.LANCZOS)
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def scale_height(image: Image.Image, target_height: int) -> Image.Image:
    width = max(1, round(image.width * target_height / image.height))
    return image.resize((width, target_height), Image.Resampling.LANCZOS)


def find_processed(index: dict[str, Any], asset_id: str, character_id: str | None = None) -> dict[str, Any]:
    matches = [
        asset for asset in index["assets"]
        if asset["asset_id"] == asset_id
        and (character_id is None or asset.get("character_id") == character_id)
    ]
    if not matches:
        raise LookupError(f"Processed asset missing: {character_id}:{asset_id}")
    return matches[0]


def placement(asset: Image.Image, config: dict[str, Any], canvas_height: int) -> tuple[int, int]:
    return int(config["x"]), canvas_height - int(config["bottom"]) - asset.height


def render_scene(
    output_root: Path, preset: dict[str, Any], semantics: dict[str, Any], index: dict[str, Any]
) -> dict[str, Any]:
    canvas_size = (int(preset["canvas"]["width"]), int(preset["canvas"]["height"]))
    selected = select_semantic_asset(semantics, preset["character_request"], int(preset["seed"]))
    character_record = find_processed(index, selected["source_asset_id"], selected["character_id"])
    pillow_record = find_processed(index, "pillow")
    phone_record = find_processed(index, "phone_dark")
    background_path = output_root / preset["background"]
    with Image.open(background_path) as opened:
        background = fit_cover(opened.convert("RGB"), canvas_size).convert("RGBA")
    with Image.open(character_record["processed_path"]) as opened:
        character = scale_height(opened.convert("RGBA"), int(preset["character"]["target_height"]))
    object_records = {"pillow": pillow_record, "phone_dark": phone_record}
    prepared: dict[str, Image.Image] = {}
    object_configs = {item["asset_id"]: item for item in preset["objects"]}
    for asset_id, record in object_records.items():
        with Image.open(record["processed_path"]) as opened:
            asset = scale_height(opened.convert("RGBA"), int(object_configs[asset_id]["target_height"]))
        rotation = float(object_configs[asset_id].get("rotation", 0))
        if rotation:
            asset = asset.rotate(-rotation, resample=Image.Resampling.BICUBIC, expand=True)
        prepared[asset_id] = asset

    canvas = background.copy()
    character_x, character_y = placement(character, preset["character"], canvas_size[1])
    shadow_config = preset["character_shadow"]
    shadow_layer = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    inset = int(shadow_config["inset_x"])
    shadow_height = int(shadow_config["height"])
    shadow_draw.ellipse(
        (
            character_x + inset,
            character_y + character.height - shadow_height // 2,
            character_x + character.width - inset,
            character_y + character.height + shadow_height // 2,
        ),
        fill=(0, 0, 0, int(shadow_config["alpha"])),
    )
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(float(shadow_config["blur"])))
    canvas.alpha_composite(shadow_layer)

    placements: dict[str, dict[str, Any]] = {}
    for asset_id in ("pillow",):
        asset = prepared[asset_id]
        x, y = placement(asset, object_configs[asset_id], canvas_size[1])
        canvas.alpha_composite(asset, (x, y))
        placements[asset_id] = {
            "x": x, "y": y, "width": asset.width, "height": asset.height,
            "target_height": object_configs[asset_id]["target_height"],
            "rotation_degrees_clockwise": object_configs[asset_id].get("rotation", 0),
        }
    canvas.alpha_composite(character, (character_x, character_y))
    phone = prepared["phone_dark"]
    phone_x, phone_y = placement(phone, object_configs["phone_dark"], canvas_size[1])
    canvas.alpha_composite(phone, (phone_x, phone_y))
    placements["phone_dark"] = {
        "x": phone_x, "y": phone_y, "width": phone.width, "height": phone.height,
        "target_height_before_rotation": object_configs["phone_dark"]["target_height"],
        "rotation_degrees_clockwise": object_configs["phone_dark"].get("rotation", 0),
    }

    output_dir = output_root / "test_renders"
    output_path = output_dir / preset["output"]
    output_dir.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, format="PNG", optimize=True)
    scene = {
        "schema_version": 2,
        "seed": preset["seed"],
        "canvas": preset["canvas"],
        "background": {"relative_path": preset["background"], "path": str(background_path)},
        "character_request": preset["character_request"],
        "character": {
            "selected_semantic_id": selected["semantic_id"],
            "selected_asset_id": selected["source_asset_id"],
            "source_sheet_id": character_record["source_sheet_id"],
            "processed_path": character_record["processed_path"],
            "selection_score": selected["selection_score"],
            "selection_reasons": selected["selection_reasons"],
            "placement": {
                "x": character_x, "y": character_y, "width": character.width,
                "height": character.height, "target_height": preset["character"]["target_height"],
            },
            "shadow": shadow_config,
        },
        "objects": [
            {"asset_id": asset_id, "processed_path": object_records[asset_id]["processed_path"], "placement": placements[asset_id]}
            for asset_id in ("pillow", "phone_dark")
        ],
        "layer_order": preset["layer_order"],
        "output": str(output_path),
    }
    scene_path = output_path.with_suffix(".scene.json")
    write_json(scene_path, scene)
    print(f'Rendered: {output_path}')
    print(f'Selected: {selected["semantic_id"]} ({selected["selection_score"]})')
    return scene


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=V2_ROOT)
    parser.add_argument("--preset", default="bedroom_night_sad")
    parser.add_argument("--semantics", type=Path, default=SEMANTICS_FILE)
    parser.add_argument("--presets", type=Path, default=PRESETS_FILE)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_root = args.output_root.resolve()
    index = read_json(output_root / "processed_asset_index.json")
    semantics = read_json(args.semantics)
    presets = read_json(args.presets)
    render_scene(output_root, presets["presets"][args.preset], semantics, index)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
