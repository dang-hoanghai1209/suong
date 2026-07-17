#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps


DEFAULT_ROOT = Path(r"D:\tella-assets-staging\mvp_v1")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def relative_posix(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)


def proportional_edges(length: int, parts: int) -> list[int]:
    return [round(index * length / parts) for index in range(parts + 1)]


def crop_grid_cell(
    image: Image.Image,
    row: int,
    column: int,
    rows: int,
    columns: int,
    inset_px: int,
) -> tuple[Image.Image, dict[str, int]]:
    x_edges = proportional_edges(image.width, columns)
    y_edges = proportional_edges(image.height, rows)

    left = x_edges[column]
    top = y_edges[row]
    right = x_edges[column + 1]
    bottom = y_edges[row + 1]

    max_inset_x = max(0, (right - left - 1) // 4)
    max_inset_y = max(0, (bottom - top - 1) // 4)
    inset_x = min(max(0, inset_px), max_inset_x)
    inset_y = min(max(0, inset_px), max_inset_y)

    crop_left = left + inset_x
    crop_top = top + inset_y
    crop_right = right - inset_x
    crop_bottom = bottom - inset_y

    if crop_right <= crop_left or crop_bottom <= crop_top:
        raise ValueError(
            f"Invalid crop after inset: row={row + 1}, column={column + 1}"
        )

    bbox = {
        "left": crop_left,
        "top": crop_top,
        "right": crop_right,
        "bottom": crop_bottom,
    }
    return image.crop((crop_left, crop_top, crop_right, crop_bottom)), bbox


def save_png(image: Image.Image, output_path: Path, overwrite: bool) -> None:
    if output_path.exists() and not overwrite:
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if image.mode not in {"RGB", "RGBA", "L", "LA", "P"}:
        image = image.convert("RGBA")

    image.save(output_path, format="PNG", optimize=True)


def make_preview(
    title: str,
    generated_items: list[dict[str, Any]],
    root: Path,
    output_path: Path,
) -> None:
    if not generated_items:
        return

    font = ImageFont.load_default()
    tile_width = 240
    tile_height = 275
    columns = min(4, max(1, len(generated_items)))
    rows = (len(generated_items) + columns - 1) // columns
    header_height = 34

    canvas = Image.new(
        "RGB",
        (columns * tile_width, header_height + rows * tile_height),
        "white",
    )
    draw = ImageDraw.Draw(canvas)
    draw.text((10, 10), title, fill="black", font=font)

    for index, item in enumerate(generated_items):
        row = index // columns
        column = index % columns
        x = column * tile_width
        y = header_height + row * tile_height

        image_path = root / item["relative_path"]
        with Image.open(image_path) as tile:
            tile = ImageOps.exif_transpose(tile).convert("RGB")
            tile.thumbnail((tile_width - 20, tile_height - 45))

            paste_x = x + (tile_width - tile.width) // 2
            paste_y = y + 5
            canvas.paste(tile, (paste_x, paste_y))

        caption = f'{item["index"]:02d} {item["asset_id"]}'
        draw.text((x + 8, y + tile_height - 30), caption, fill="black", font=font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", optimize=True)


def validate_sheet(sheet: dict[str, Any]) -> None:
    required = {"sheet_id", "source", "output_dir", "mode", "items"}
    missing = required - sheet.keys()
    if missing:
        raise ValueError(
            f'Sheet "{sheet.get("sheet_id", "<unknown>")}" missing: {sorted(missing)}'
        )

    mode = sheet["mode"]
    items = sheet["items"]

    if mode == "grid":
        rows = int(sheet["rows"])
        columns = int(sheet["columns"])
        expected = rows * columns
        if len(items) != expected:
            raise ValueError(
                f'{sheet["sheet_id"]}: expected {expected} items for '
                f'{rows}x{columns}, received {len(items)}'
            )
    elif mode == "copy":
        if len(items) != 1:
            raise ValueError(f'{sheet["sheet_id"]}: copy mode requires exactly 1 item')
    else:
        raise ValueError(f'{sheet["sheet_id"]}: unsupported mode "{mode}"')

    seen_ids: set[str] = set()
    for item in items:
        asset_id = item["asset_id"]
        if asset_id in seen_ids:
            raise ValueError(f'{sheet["sheet_id"]}: duplicate asset_id "{asset_id}"')
        seen_ids.add(asset_id)


def clean_sheet_output(root: Path, sheet: dict[str, Any]) -> None:
    output_dir = (root / sheet["output_dir"]).resolve()
    root_resolved = root.resolve()

    if root_resolved not in output_dir.parents:
        raise RuntimeError(f"Unsafe output directory: {output_dir}")

    if output_dir.exists():
        shutil.rmtree(output_dir)


def process_grid_sheet(
    root: Path,
    sheet: dict[str, Any],
    overwrite: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source_path = root / sheet["source"]
    output_dir = root / sheet["output_dir"]
    rows = int(sheet["rows"])
    columns = int(sheet["columns"])
    inset_px = int(sheet.get("inset_px", 2))

    with Image.open(source_path) as source_image:
        source_image = ImageOps.exif_transpose(source_image)
        source_width, source_height = source_image.size
        generated_items: list[dict[str, Any]] = []

        for zero_index, item in enumerate(sheet["items"]):
            row = zero_index // columns
            column = zero_index % columns
            tile, bbox = crop_grid_cell(
                source_image,
                row,
                column,
                rows,
                columns,
                inset_px,
            )

            filename = f'{zero_index + 1:02d}_{item["asset_id"]}.png'
            output_path = output_dir / filename
            save_png(tile, output_path, overwrite=overwrite)

            generated = {
                **item,
                "index": zero_index + 1,
                "row": row + 1,
                "column": column + 1,
                "filename": filename,
                "relative_path": relative_posix(output_path, root),
                "width": tile.width,
                "height": tile.height,
                "bbox": bbox,
                "sha256": sha256_file(output_path),
                "source_sheet_id": sheet["sheet_id"],
            }
            generated_items.append(generated)

            state = "WROTE" if overwrite or not output_path.exists() else "KEPT"
            print(
                f'[{sheet["sheet_id"]}] {zero_index + 1:02d}/{len(sheet["items"]):02d} '
                f'{item["asset_id"]} -> {generated["relative_path"]}'
            )

    sheet_manifest = {
        "schema_version": 1,
        "generated_at_utc": utc_now_iso(),
        "sheet_id": sheet["sheet_id"],
        "character_id": sheet.get("character_id"),
        "asset_group": sheet.get("asset_group"),
        "mode": "grid",
        "reading_order": "left_to_right_top_to_bottom",
        "source": relative_posix(source_path, root),
        "source_sha256": sha256_file(source_path),
        "source_width": source_width,
        "source_height": source_height,
        "rows": rows,
        "columns": columns,
        "inset_px": inset_px,
        "output_dir": relative_posix(output_dir, root),
        "asset_count": len(generated_items),
        "assets": generated_items,
    }
    return sheet_manifest, generated_items


def process_copy_sheet(
    root: Path,
    sheet: dict[str, Any],
    overwrite: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source_path = root / sheet["source"]
    output_dir = root / sheet["output_dir"]
    item = sheet["items"][0]

    filename = f'01_{item["asset_id"]}{source_path.suffix.lower()}'
    output_path = output_dir / filename
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if overwrite or not output_path.exists():
        shutil.copy2(source_path, output_path)

    with Image.open(output_path) as image:
        width, height = image.size

    generated = {
        **item,
        "index": 1,
        "row": 1,
        "column": 1,
        "filename": filename,
        "relative_path": relative_posix(output_path, root),
        "width": width,
        "height": height,
        "bbox": None,
        "sha256": sha256_file(output_path),
        "source_sheet_id": sheet["sheet_id"],
    }

    print(f'[{sheet["sheet_id"]}] copied -> {generated["relative_path"]}')

    sheet_manifest = {
        "schema_version": 1,
        "generated_at_utc": utc_now_iso(),
        "sheet_id": sheet["sheet_id"],
        "character_id": sheet.get("character_id"),
        "asset_group": sheet.get("asset_group"),
        "mode": "copy",
        "source": relative_posix(source_path, root),
        "source_sha256": sha256_file(source_path),
        "source_width": width,
        "source_height": height,
        "output_dir": relative_posix(output_dir, root),
        "asset_count": 1,
        "assets": [generated],
    }
    return sheet_manifest, [generated]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch slice and map Tella character asset sheets."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help=f"Asset root directory. Default: {DEFAULT_ROOT}",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("asset_batch_config.json"),
        help="Path to JSON configuration.",
    )
    parser.add_argument(
        "--only",
        nargs="*",
        default=[],
        help="Process only the listed sheet_id values.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite generated PNG files.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete each selected sheet output directory before generating.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail immediately when a configured source sheet is missing.",
    )
    parser.add_argument(
        "--no-preview",
        action="store_true",
        help="Do not create contact-sheet previews.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List configured sheet IDs and exit.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.expanduser().resolve()
    config_path = args.config.expanduser().resolve()

    if not config_path.is_file():
        print(f"ERROR: config not found: {config_path}", file=sys.stderr)
        return 2

    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    sheets = config["sheets"]

    for sheet in sheets:
        validate_sheet(sheet)

    if args.list:
        for sheet in sheets:
            print(
                f'{sheet["sheet_id"]}: {sheet["mode"]} '
                f'[{sheet.get("character_id", "background")}] '
                f'-> {sheet["source"]}'
            )
        return 0

    selected_ids = set(args.only)
    if selected_ids:
        known_ids = {sheet["sheet_id"] for sheet in sheets}
        unknown = selected_ids - known_ids
        if unknown:
            print(f"ERROR: unknown --only sheet IDs: {sorted(unknown)}", file=sys.stderr)
            return 2
        sheets = [sheet for sheet in sheets if sheet["sheet_id"] in selected_ids]

    root.mkdir(parents=True, exist_ok=True)
    manifests_dir = root / "manifests"
    previews_dir = root / "previews"

    processed_sheet_manifests: list[dict[str, Any]] = []
    all_assets: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for sheet in sheets:
        source_path = root / sheet["source"]

        if not source_path.is_file():
            message = f'Missing source: {source_path}'
            if args.strict:
                print(f"ERROR: {message}", file=sys.stderr)
                return 3

            print(f'SKIP [{sheet["sheet_id"]}] {message}')
            skipped.append(
                {
                    "sheet_id": sheet["sheet_id"],
                    "source": sheet["source"],
                    "reason": "source_missing",
                }
            )
            continue

        if args.clean:
            clean_sheet_output(root, sheet)

        if sheet["mode"] == "grid":
            sheet_manifest, generated_items = process_grid_sheet(
                root,
                sheet,
                overwrite=args.overwrite,
            )
        else:
            sheet_manifest, generated_items = process_copy_sheet(
                root,
                sheet,
                overwrite=args.overwrite,
            )

        sheet_manifest_path = manifests_dir / f'{sheet["sheet_id"]}.json'
        write_json(sheet_manifest_path, sheet_manifest)
        processed_sheet_manifests.append(sheet_manifest)
        all_assets.extend(generated_items)

        if not args.no_preview:
            preview_path = previews_dir / f'{sheet["sheet_id"]}_preview.png'
            make_preview(
                sheet["sheet_id"],
                generated_items,
                root,
                preview_path,
            )

    active_assets = [
        asset
        for asset in all_assets
        if bool(asset.get("enabled_by_default", False))
    ]

    master_manifest = {
        "schema_version": 1,
        "generated_at_utc": utc_now_iso(),
        "root": str(root),
        "configured_sheet_count": len(sheets),
        "processed_sheet_count": len(processed_sheet_manifests),
        "skipped_sheet_count": len(skipped),
        "asset_count": len(all_assets),
        "active_asset_count": len(active_assets),
        "skipped": skipped,
        "sheets": processed_sheet_manifests,
        "assets": all_assets,
    }
    write_json(root / "asset_manifest.json", master_manifest)

    active_index = {
        "schema_version": 1,
        "generated_at_utc": utc_now_iso(),
        "asset_count": len(active_assets),
        "assets": [
            {
                "asset_id": asset["asset_id"],
                "character_id": asset.get("character_id"),
                "asset_group": asset.get("asset_group"),
                "tier": asset.get("tier"),
                "relative_path": asset["relative_path"],
                "aliases": asset.get("aliases", []),
                "source_sheet_id": asset["source_sheet_id"],
            }
            for asset in active_assets
        ],
    }
    write_json(root / "active_asset_index.json", active_index)

    print()
    print("Batch asset processing completed.")
    print(f"Root             : {root}")
    print(f"Processed sheets : {len(processed_sheet_manifests)}")
    print(f"Skipped sheets   : {len(skipped)}")
    print(f"Generated assets : {len(all_assets)}")
    print(f"Active assets    : {len(active_assets)}")
    print(f"Master manifest  : {root / 'asset_manifest.json'}")
    print(f"Active index     : {root / 'active_asset_index.json'}")
    if not args.no_preview:
        print(f"Previews         : {previews_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
