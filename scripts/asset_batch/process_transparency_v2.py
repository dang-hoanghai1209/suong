#!/usr/bin/env python
"""Deterministic, non-destructive transparency processing for Tella MVP V2."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from collections import deque
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageOps


SOURCE_ROOT = Path(r"D:\tella-assets-staging\mvp_v1")
V1_PROCESSED_ROOT = Path(r"D:\tella-assets-staging\mvp_v1_processed")
V2_ROOT = Path(r"D:\tella-assets-staging\mvp_v1_processed_v2")
SCRIPT_DIR = Path(__file__).resolve().parent
PROFILE_FILE = SCRIPT_DIR / "transparency_profiles.json"
QC_REPORT_FILE = V2_ROOT / "transparency_qc_report.json"
PROCESSED_INDEX_FILE = V2_ROOT / "processed_asset_index.json"
INSPECTION_REPORT_FILE = V2_ROOT / "inspection_report.json"

PALE_ASSETS = {
    "pillow", "paper_letter", "empty_cup", "curtain_simple", "floor_lamp", "photo_frame"
}
QC_KEYS = {
    "female_01:sit_hug_knees",
    "female_01:sit_hug_knees_backup",
    "female_01:walk_left",
    "female_01:wipe_tear",
    "male_01:stand_phone_one_hand",
    "male_01:sit_cross_leg_phone",
    "crowd_group_cheering_01",
    "pillow", "paper_letter", "curtain_simple", "flower_single",
}
INSPECTION_KEYS = {
    "female_01:sit_hug_knees", "female_01:sit_hug_knees_backup",
    "phone_dark", "pillow", "crowd_group_cheering_01",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def gather_asset_records(source_root: Path = SOURCE_ROOT) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    manifest = read_json(source_root / "asset_manifest.json")
    for asset in manifest["assets"]:
        relative = asset["relative_path"]
        if not (relative.startswith("characters/") or relative.startswith("backgrounds/crowd/")):
            continue
        asset_type = "crowd" if relative.startswith("backgrounds/crowd/") else "character"
        records.append({
            "asset_id": asset["asset_id"],
            "asset_type": asset_type,
            "character_id": asset.get("character_id"),
            "relative_path": relative,
            "source_sheet_id": asset.get("source_sheet_id"),
            "tier": asset.get("tier"),
            "canonical": bool(asset.get("canonical")),
            "enabled_by_default": bool(asset.get("enabled_by_default")),
        })

    expressions = read_json(
        source_root / "characters" / "female_01" / "female_01_expression_map.json"
    )
    for item in expressions["expressions"]:
        records.append({
            "asset_id": item["expression_id"],
            "asset_type": "expression",
            "character_id": "female_01",
            "relative_path": f'characters/female_01/{item["relative_path"]}',
            "source_sheet_id": "female_01_expressions_r3_c3",
            "tier": "reference",
            "canonical": True,
            "enabled_by_default": True,
        })

    objects = read_json(source_root / "objects" / "object_manifest.json")
    for item in objects["objects"]:
        records.append({
            "asset_id": item["asset_id"],
            "asset_type": "object",
            "character_id": None,
            "relative_path": item["relative_path"],
            "source_sheet_id": item.get("source_sheet_id"),
            "tier": "core",
            "canonical": True,
            "enabled_by_default": True,
        })
    return records


def record_key(record: dict[str, Any]) -> str:
    character = record.get("character_id")
    return f'{character}:{record["asset_id"]}' if character else record["asset_id"]


def select_profile(record: dict[str, Any], config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if record["asset_type"] == "crowd":
        name = "crowd"
    elif record["asset_type"] in {"character", "expression"}:
        name = "character"
    elif record["asset_id"] in PALE_ASSETS:
        name = "bright_pale_object"
    else:
        name = "object"
    override = config.get("asset_overrides", {}).get(record_key(record))
    if override is None:
        override = config.get("asset_overrides", {}).get(record["asset_id"], {})
    name = override.get("profile", name)
    profile = dict(config["profiles"][name])
    profile.update({key: value for key, value in override.items() if key != "profile"})
    return name, profile


def estimate_background_rgb(image: Image.Image) -> tuple[int, int, int]:
    """Use corner patches plus border samples; channel medians resist edge-touching subjects."""
    rgba = image.convert("RGBA")
    width, height = rgba.size
    px = rgba.load()
    patch = max(2, min(width, height) // 24)
    samples: list[tuple[int, int, int]] = []
    corners = ((0, 0), (width - patch, 0), (0, height - patch), (width - patch, height - patch))
    for left, top in corners:
        for y in range(top, min(height, top + patch)):
            for x in range(left, min(width, left + patch)):
                if px[x, y][3] > 0:
                    samples.append(px[x, y][:3])
    stride = max(1, (width + height) // 500)
    for x in range(0, width, stride):
        samples.extend((px[x, 0][:3], px[x, height - 1][:3]))
    for y in range(0, height, stride):
        samples.extend((px[0, y][:3], px[width - 1, y][:3]))
    if not samples:
        return (255, 255, 255)
    channels = [sorted(pixel[channel] for pixel in samples) for channel in range(3)]
    middle = len(samples) // 2
    return tuple(values[middle] for values in channels)  # type: ignore[return-value]


def color_distance(rgb: tuple[int, int, int], background: tuple[int, int, int]) -> float:
    return math.sqrt(sum((rgb[index] - background[index]) ** 2 for index in range(3)))


def edge_connected_soft_alpha(
    image: Image.Image, background: tuple[int, int, int], inner: float, outer: float
) -> tuple[Image.Image, int]:
    rgba = image.convert("RGBA")
    width, height = rgba.size
    source = list(rgba.getdata())
    distances = [color_distance(pixel[:3], background) for pixel in source]
    visited = bytearray(width * height)
    queue: deque[int] = deque()

    def enqueue(index: int) -> None:
        if not visited[index] and (source[index][3] == 0 or distances[index] <= outer):
            visited[index] = 1
            queue.append(index)

    for x in range(width):
        enqueue(x)
        enqueue((height - 1) * width + x)
    for y in range(height):
        enqueue(y * width)
        enqueue(y * width + width - 1)
    while queue:
        index = queue.popleft()
        x, y = index % width, index // width
        if x:
            enqueue(index - 1)
        if x + 1 < width:
            enqueue(index + 1)
        if y:
            enqueue(index - width)
        if y + 1 < height:
            enqueue(index + width)

    output: list[tuple[int, int, int, int]] = []
    removed = 0
    span = max(1.0, outer - inner)
    for index, pixel in enumerate(source):
        alpha = pixel[3]
        if visited[index]:
            t = max(0.0, min(1.0, (distances[index] - inner) / span))
            smooth = t * t * (3.0 - 2.0 * t)
            alpha = round(alpha * smooth)
        if alpha == 0:
            removed += 1
        output.append((pixel[0], pixel[1], pixel[2], alpha))
    result = Image.new("RGBA", rgba.size)
    result.putdata(output)
    return result, removed


def connected_components(alpha: Image.Image, threshold: int) -> list[dict[str, Any]]:
    width, height = alpha.size
    values = bytes(alpha.getdata())
    visited = bytearray(width * height)
    components: list[dict[str, Any]] = []
    for start, value in enumerate(values):
        if value < threshold or visited[start]:
            continue
        visited[start] = 1
        queue = deque([start])
        pixels: list[int] = []
        left = right = start % width
        top = bottom = start // width
        while queue:
            index = queue.popleft()
            pixels.append(index)
            x, y = index % width, index // width
            left, right = min(left, x), max(right, x)
            top, bottom = min(top, y), max(bottom, y)
            for ny in range(max(0, y - 1), min(height, y + 2)):
                base = ny * width
                for nx in range(max(0, x - 1), min(width, x + 2)):
                    neighbor = base + nx
                    if not visited[neighbor] and values[neighbor] >= threshold:
                        visited[neighbor] = 1
                        queue.append(neighbor)
        components.append({
            "area": len(pixels), "pixels": pixels,
            "bbox": [left, top, right + 1, bottom + 1],
            "center": [(left + right + 1) / 2, (top + bottom + 1) / 2],
        })
    components.sort(key=lambda component: component["area"], reverse=True)
    return components


def bbox_intersects(a: Iterable[float], b: Iterable[float]) -> bool:
    a1, a2, a3, a4 = a
    b1, b2, b3, b4 = b
    return a1 < b3 and a3 > b1 and a2 < b4 and a4 > b2


def clean_components(
    image: Image.Image, components: list[dict[str, Any]], profile: dict[str, Any]
) -> tuple[Image.Image, list[dict[str, Any]], list[dict[str, Any]]]:
    if not components:
        return image, [], []
    width, height = image.size
    total = width * height
    main = components[0]
    left, top, right, bottom = main["bbox"]
    expand = float(profile["main_bbox_expand_ratio"])
    expanded = [
        left - (right - left) * expand, top - (bottom - top) * expand,
        right + (right - left) * expand, bottom + (bottom - top) * expand,
    ]
    remove_regions = [
        [region[0] * width, region[1] * height, region[2] * width, region[3] * height]
        for region in profile.get("explicit_remove_regions", [])
    ]
    keep_regions = [
        [region[0] * width, region[1] * height, region[2] * width, region[3] * height]
        for region in profile.get("explicit_keep_regions", [])
    ]
    diagonal = math.hypot(width, height)
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for index, component in enumerate(components):
        bbox = component["bbox"]
        forced_remove = any(bbox_intersects(bbox, region) for region in remove_regions)
        forced_keep = any(bbox_intersects(bbox, region) for region in keep_regions)
        distance = math.hypot(
            component["center"][0] - main["center"][0],
            component["center"][1] - main["center"][1],
        ) / max(1.0, diagonal)
        area_ratio = component["area"] / total
        is_large = area_ratio >= float(profile["component_min_area_ratio"])
        near_main = bbox_intersects(bbox, expanded) or distance <= float(
            profile["maximum_component_distance_ratio"]
        )
        keep_small_near = bool(profile.get("keep_small_near_components", True))
        keep = index == 0 or forced_keep or (
            not forced_remove
            and near_main
            and (
                is_large
                or (
                    bool(profile["keep_multiple_large_components"])
                    and keep_small_near
                )
            )
        )
        (kept if keep else removed).append(component)
    if not removed:
        return image, kept, removed
    pixels = list(image.getdata())
    for component in removed:
        left, top, right, bottom = component["bbox"]
        # Clear a one-pixel apron as well as the thresholded component. This removes
        # the low-alpha residue surrounding rejected grid-cell fragments.
        for y in range(max(0, top - 1), min(height, bottom + 1)):
            for x in range(max(0, left - 1), min(width, right + 1)):
                index = y * width + x
                r, g, b, _ = pixels[index]
                pixels[index] = (r, g, b, 0)
    cleaned = Image.new("RGBA", image.size)
    cleaned.putdata(pixels)
    return cleaned, kept, removed


def defringe(image: Image.Image, background: tuple[int, int, int], strength: float) -> Image.Image:
    output: list[tuple[int, int, int, int]] = []
    for r, g, b, alpha in image.getdata():
        if alpha < 10:
            output.append((0, 0, 0, 0))
            continue
        if alpha >= 250:
            output.append((r, g, b, alpha))
            continue
        fraction = max(alpha / 255.0, 0.08)
        corrected = []
        local_strength = strength * (1.0 - alpha / 255.0)
        for value, bg in zip((r, g, b), background):
            estimate = max(0, min(255, round((value - bg * (1.0 - fraction)) / fraction)))
            corrected.append(round(value * (1.0 - local_strength) + estimate * local_strength))
        output.append((corrected[0], corrected[1], corrected[2], alpha))
    result = Image.new("RGBA", image.size)
    result.putdata(output)
    return result


def trim_image(image: Image.Image, padding: int) -> tuple[Image.Image, list[int]]:
    bbox = image.getchannel("A").getbbox()
    if bbox is None:
        return image, [0, 0, image.width, image.height]
    left, top, right, bottom = bbox
    desired = [left - padding, top - padding, right + padding, bottom + padding]
    clipped = [
        max(0, desired[0]), max(0, desired[1]),
        min(image.width, desired[2]), min(image.height, desired[3]),
    ]
    cropped = image.crop(tuple(clipped))
    # Preserve the requested safe padding even when a valid outline reaches the
    # source crop boundary. Negative trim coordinates describe that extension.
    output = Image.new(
        "RGBA",
        (desired[2] - desired[0], desired[3] - desired[1]),
        (0, 0, 0, 0),
    )
    output.alpha_composite(cropped, (clipped[0] - desired[0], clipped[1] - desired[1]))
    return output, desired


def warnings_for(
    source_size: tuple[int, int], output: Image.Image, trim_bbox: list[int], removed: list[dict[str, Any]]
) -> list[str]:
    alpha = output.getchannel("A")
    values = bytes(alpha.getdata())
    transparent_ratio = sum(value == 0 for value in values) / max(1, len(values))
    foreground_ratio = sum(value > 16 for value in values) / max(1, len(values))
    warnings: list[str] = []
    if transparent_ratio == 0:
        warnings.append("no transparent pixels")
    if foreground_ratio < 0.01:
        warnings.append("foreground ratio unexpectedly low")
    if foreground_ratio > 0.92:
        warnings.append("foreground ratio unexpectedly high")
    bbox = alpha.getbbox()
    if bbox and (bbox[0] == 0 or bbox[1] == 0 or bbox[2] == output.width or bbox[3] == output.height):
        warnings.append("foreground touching trim bounds")
    source_area = source_size[0] * source_size[1]
    if output.width * output.height < source_area * 0.04:
        warnings.append("unusual size reduction")
    if bbox is None:
        warnings.append("empty output")
    if removed:
        warnings.append("suspicious distant components")
    return warnings


def process_record(
    record: dict[str, Any], source_root: Path, output_root: Path, config: dict[str, Any]
) -> dict[str, Any]:
    source_path = source_root / record["relative_path"]
    output_path = output_root / record["relative_path"]
    profile_name, profile = select_profile(record, config)
    with Image.open(source_path) as opened:
        source = ImageOps.exif_transpose(opened).convert("RGBA")
    background = estimate_background_rgb(source)
    masked, _ = edge_connected_soft_alpha(
        source, background,
        float(profile["background_inner_distance"]),
        float(profile["background_outer_distance"]),
    )
    components = connected_components(masked.getchannel("A"), int(profile["component_alpha_threshold"]))
    cleaned, kept, removed = clean_components(masked, components, profile)
    cleaned = defringe(cleaned, background, float(profile["defringe_strength"]))
    trimmed, trim_bbox = trim_image(cleaned, int(profile["trim_padding"]))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    trimmed.save(output_path, format="PNG", optimize=True)
    alpha_values = bytes(trimmed.getchannel("A").getdata())
    report = {
        **record,
        "source_path": str(source_path),
        "processed_path": str(output_path),
        "source_dimensions": list(source.size),
        "processed_dimensions": list(trimmed.size),
        "detected_background_rgb": list(background),
        "transparent_pixel_ratio": round(sum(value == 0 for value in alpha_values) / len(alpha_values), 6),
        "source_component_count": len(components),
        "kept_component_count": len(kept),
        "removed_component_count": len(removed),
        "removed_component_bounding_boxes": [component["bbox"] for component in removed],
        "trim_bounding_box": trim_bbox,
        "warnings": warnings_for(source.size, trimmed, trim_bbox, removed),
        "processing_profile_name": profile_name,
        "processing_parameters": profile,
        "source_sha256": sha256_file(source_path),
        "processed_sha256": sha256_file(output_path),
    }
    return report


def copy_backgrounds(source_root: Path, output_root: Path) -> list[dict[str, Any]]:
    records = []
    for relative in (
        "backgrounds/indoor/bedroom_day_01.png",
        "backgrounds/indoor/bedroom_night_01.png",
        "backgrounds/indoor/room_by_window_day_01.png",
        "backgrounds/public_places/cafe_corner_day_01.png",
        "backgrounds/outdoor/park_bench_day_01.png",
        "backgrounds/outdoor/bus_stop_rain_day_01.png",
    ):
        source, target = source_root / relative, output_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        records.append({"asset_id": source.stem, "source_path": str(source), "processed_path": str(target)})
    return records


def qc_preview(image: Image.Image, title: str, target: Path) -> None:
    tile_width, tile_height = 420, 460
    canvas = Image.new("RGB", (tile_width * 3, tile_height), "white")
    draw = ImageDraw.Draw(canvas)
    backgrounds = ("checkerboard", "white", "dark")
    for column, background_name in enumerate(backgrounds):
        if background_name == "checkerboard":
            tile = Image.new("RGBA", (tile_width, tile_height), (245, 245, 245, 255))
            tile_draw = ImageDraw.Draw(tile)
            size = 24
            for y in range(0, tile_height, size):
                for x in range(0, tile_width, size):
                    if (x // size + y // size) % 2:
                        tile_draw.rectangle((x, y, x + size - 1, y + size - 1), fill=(205, 205, 205, 255))
        else:
            color = (255, 255, 255, 255) if background_name == "white" else (35, 38, 45, 255)
            tile = Image.new("RGBA", (tile_width, tile_height), color)
        displayed = image.copy()
        displayed.thumbnail((tile_width - 30, tile_height - 55), Image.Resampling.LANCZOS)
        tile.alpha_composite(displayed, ((tile_width - displayed.width) // 2, 28 + (tile_height - 55 - displayed.height) // 2))
        canvas.paste(tile.convert("RGB"), (column * tile_width, 0))
        draw.text((column * tile_width + 10, 8), background_name, fill=(190, 40, 40) if background_name == "dark" else "black")
    draw.text((10, tile_height - 20), title, fill="black")
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target, format="PNG", optimize=True)


def generate_qc_previews(reports: list[dict[str, Any]], output_root: Path) -> list[str]:
    generated = []
    for report in reports:
        key = record_key(report)
        if key not in QC_KEYS:
            continue
        safe_name = key.replace(":", "__")
        target = output_root / "qc_previews" / f"{safe_name}.png"
        with Image.open(report["processed_path"]) as opened:
            qc_preview(opened.convert("RGBA"), key, target)
        generated.append(str(target))
    return generated


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--output-root", type=Path, default=V2_ROOT)
    parser.add_argument("--profiles", type=Path, default=PROFILE_FILE)
    parser.add_argument("--only", nargs="*", default=[])
    parser.add_argument("--no-qc", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_root = args.source_root.resolve()
    output_root = args.output_root.resolve()
    protected_roots = {source_root, SOURCE_ROOT.resolve(), V1_PROCESSED_ROOT.resolve()}
    if any(output_root == protected or protected in output_root.parents for protected in protected_roots):
        raise RuntimeError("V2 output must not be either protected V1 root or a child of one")
    config = read_json(args.profiles)
    records = gather_asset_records(source_root)
    if args.only:
        wanted = set(args.only)
        records = [record for record in records if record_key(record) in wanted or record["asset_id"] in wanted]
    reports = [process_record(record, source_root, output_root, config) for record in records]
    backgrounds = copy_backgrounds(source_root, output_root) if not args.only else []
    previews = [] if args.no_qc else generate_qc_previews(reports, output_root)
    payload = {
        "schema_version": 2,
        "source_root": str(source_root),
        "processed_root": str(output_root),
        "asset_count": len(reports),
        "background_count": len(backgrounds),
        "qc_preview_count": len(previews),
        "assets": reports,
        "backgrounds": backgrounds,
        "qc_previews": previews,
    }
    write_json(output_root / QC_REPORT_FILE.name, payload)
    write_json(output_root / PROCESSED_INDEX_FILE.name, payload)
    if not args.only:
        inspected = [report for report in reports if record_key(report) in INSPECTION_KEYS]
        write_json(output_root / INSPECTION_REPORT_FILE.name, {
            "schema_version": 2,
            "inspection_scope": "verified candidates before applying semantic overlay",
            "findings": {
                "female_01:sit_hug_knees": "gentle/smiling canonical crop with two adjacent-cell shoe fragments",
                "female_01:sit_hug_knees_backup": "sad/withdrawn backup crop without adjacent-cell fragments",
            },
            "assets": inspected,
        })
    print(f"Processed {len(reports)} transparent assets and copied {len(backgrounds)} backgrounds")
    print(f"QC previews: {len(previews)}")
    print(f"Report: {output_root / QC_REPORT_FILE.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
