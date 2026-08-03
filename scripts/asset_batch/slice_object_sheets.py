from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps


ROOT = Path(r"D:\tella-assets-staging\mvp_v1")

MANIFEST_DIR = ROOT / "objects" / "manifests"
MASTER_MANIFEST = ROOT / "objects" / "object_manifest.json"


SHEETS: list[dict[str, Any]] = [
    {
        "sheet_id": "objects_furniture_r3_c3",
        "category": "furniture",
        "source": (
            ROOT
            / "objects"
            / "source_sheets"
            / "objects_furniture_r3_c3.png"
        ),
        "output_dir": ROOT / "objects" / "furniture",
        "rows": 3,
        "columns": 3,
        "items": [
            {
                "asset_id": "bed_single",
                "description": "Single bed",
            },
            {
                "asset_id": "chair_simple",
                "description": "Simple chair",
            },
            {
                "asset_id": "desk_small",
                "description": "Small desk",
            },
            {
                "asset_id": "side_table",
                "description": "Side table",
            },
            {
                "asset_id": "sofa_simple",
                "description": "Simple sofa",
            },
            {
                "asset_id": "window_simple",
                "description": "Simple window",
            },
            {
                "asset_id": "door_closed",
                "description": "Closed door",
            },
            {
                "asset_id": "floor_lamp",
                "description": "Floor lamp",
            },
            {
                "asset_id": "mirror_standing",
                "description": "Standing mirror",
            },
        ],
    },
    {
        "sheet_id": "objects_emotional_r3_c3",
        "category": "emotional",
        "source": (
            ROOT
            / "objects"
            / "source_sheets"
            / "objects_emotional_r3_c3.png"
        ),
        "output_dir": ROOT / "objects" / "emotional",
        "rows": 3,
        "columns": 3,
        "items": [
            {
                "asset_id": "phone_dark",
                "description": "Dark smartphone",
            },
            {
                "asset_id": "paper_letter",
                "description": "Paper letter or envelope",
            },
            {
                "asset_id": "photo_frame",
                "description": "Photo frame",
            },
            {
                "asset_id": "empty_cup",
                "description": "Empty cup",
            },
            {
                "asset_id": "flower_single",
                "description": "Single flower",
            },
            {
                "asset_id": "wilted_flower",
                "description": "Wilted flower",
            },
            {
                "asset_id": "tissue_box",
                "description": "Tissue box",
            },
            {
                "asset_id": "book_closed",
                "description": "Closed book",
            },
            {
                "asset_id": "small_handbag",
                "description": "Small handbag",
            },
        ],
    },
    {
        "sheet_id": "objects_room_props_r2_c3",
        "category": "room_props",
        "source": (
            ROOT
            / "objects"
            / "source_sheets"
            / "objects_room_props_r2_c3.png"
        ),
        "output_dir": ROOT / "objects" / "room_props",
        "rows": 2,
        "columns": 3,
        "items": [
            {
                "asset_id": "pillow",
                "description": "Pillow",
            },
            {
                "asset_id": "blanket_folded",
                "description": "Folded blanket",
            },
            {
                "asset_id": "potted_plant",
                "description": "Potted plant",
            },
            {
                "asset_id": "curtain_simple",
                "description": "Simple curtain",
            },
            {
                "asset_id": "rug_simple",
                "description": "Simple rug",
            },
            {
                "asset_id": "wall_clock",
                "description": "Wall clock",
            },
        ],
    },
]


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )


def sha256_file(file_path: Path) -> str:
    digest = hashlib.sha256()

    with file_path.open("rb") as file:
        for chunk in iter(
            lambda: file.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def relative_path(file_path: Path) -> str:
    return file_path.relative_to(ROOT).as_posix()


def write_json(
    file_path: Path,
    payload: dict[str, Any],
) -> None:
    file_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    file_path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def proportional_edges(
    length: int,
    parts: int,
) -> list[int]:
    return [
        round(index * length / parts)
        for index in range(parts + 1)
    ]


def validate_sheet(sheet: dict[str, Any]) -> None:
    expected_count = (
        sheet["rows"]
        * sheet["columns"]
    )

    actual_count = len(sheet["items"])

    if actual_count != expected_count:
        raise ValueError(
            f'{sheet["sheet_id"]}: expected '
            f"{expected_count} mapped objects, "
            f"received {actual_count}"
        )

    asset_ids = [
        item["asset_id"]
        for item in sheet["items"]
    ]

    if len(asset_ids) != len(set(asset_ids)):
        raise ValueError(
            f'{sheet["sheet_id"]}: duplicate asset IDs'
        )


def process_sheet(
    sheet: dict[str, Any],
) -> dict[str, Any]:
    validate_sheet(sheet)

    source_file: Path = sheet["source"]
    output_dir: Path = sheet["output_dir"]
    rows: int = sheet["rows"]
    columns: int = sheet["columns"]

    if not source_file.is_file():
        raise FileNotFoundError(
            f"Source sheet does not exist: {source_file}"
        )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Chỉ xóa PNG đã được sinh trong output folder.
    for old_file in output_dir.glob("*.png"):
        old_file.unlink()

    with Image.open(source_file) as source_image:
        image = ImageOps.exif_transpose(
            source_image
        ).convert("RGBA")

        source_width = image.width
        source_height = image.height

        x_edges = proportional_edges(
            source_width,
            columns,
        )

        y_edges = proportional_edges(
            source_height,
            rows,
        )

        manifest_objects = []

        for zero_index, item in enumerate(
            sheet["items"]
        ):
            index = zero_index + 1
            row = zero_index // columns
            column = zero_index % columns

            left = x_edges[column]
            top = y_edges[row]
            right = x_edges[column + 1]
            bottom = y_edges[row + 1]

            tile = image.crop(
                (
                    left,
                    top,
                    right,
                    bottom,
                )
            )

            filename = (
                f"{index:02d}_"
                f'{item["asset_id"]}.png'
            )

            output_file = (
                output_dir
                / filename
            )

            tile.save(
                output_file,
                format="PNG",
                optimize=True,
            )

            object_entry = {
                "index": index,
                "row": row + 1,
                "column": column + 1,
                "asset_id": item["asset_id"],
                "description": item["description"],
                "category": sheet["category"],
                "filename": filename,
                "relative_path": relative_path(
                    output_file
                ),
                "width": tile.width,
                "height": tile.height,
                "bbox": {
                    "left": left,
                    "top": top,
                    "right": right,
                    "bottom": bottom,
                },
                "background_removed": False,
                "sha256": sha256_file(
                    output_file
                ),
            }

            manifest_objects.append(
                object_entry
            )

            print(
                f'[{sheet["sheet_id"]}] '
                f"{index:02d}/"
                f'{len(sheet["items"]):02d} '
                f"row={row + 1}, "
                f"col={column + 1}, "
                f'object={item["asset_id"]}, '
                f"size={tile.width}x{tile.height}"
            )

    manifest = {
        "schema_version": 1,
        "generated_at_utc": utc_now(),
        "asset_type": "object_sheet_mapping",
        "sheet_id": sheet["sheet_id"],
        "category": sheet["category"],
        "source_sheet": relative_path(
            source_file
        ),
        "source_sha256": sha256_file(
            source_file
        ),
        "grid": {
            "rows": rows,
            "columns": columns,
            "reading_order": (
                "left_to_right_"
                "top_to_bottom"
            ),
            "source_width": source_width,
            "source_height": source_height,
        },
        "output_directory": relative_path(
            output_dir
        ),
        "object_count": len(
            manifest_objects
        ),
        "objects": manifest_objects,
    }

    manifest_path = (
        MANIFEST_DIR
        / f'{sheet["sheet_id"]}_map.json'
    )

    write_json(
        manifest_path,
        manifest,
    )

    print(
        f"Manifest: {manifest_path}"
    )
    print()

    return manifest


def main() -> None:
    MANIFEST_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    sheet_manifests = []
    all_objects = []

    for sheet in SHEETS:
        manifest = process_sheet(
            sheet
        )

        sheet_manifests.append(
            {
                "sheet_id": manifest["sheet_id"],
                "category": manifest["category"],
                "source_sheet": manifest[
                    "source_sheet"
                ],
                "object_count": manifest[
                    "object_count"
                ],
            }
        )

        for object_entry in manifest["objects"]:
            all_objects.append(
                {
                    **object_entry,
                    "source_sheet_id": manifest[
                        "sheet_id"
                    ],
                }
            )

    master_manifest = {
        "schema_version": 1,
        "generated_at_utc": utc_now(),
        "asset_type": "object_library",
        "sheet_count": len(
            sheet_manifests
        ),
        "object_count": len(
            all_objects
        ),
        "sheets": sheet_manifests,
        "objects": all_objects,
    }

    write_json(
        MASTER_MANIFEST,
        master_manifest,
    )

    print(
        "Object mapping completed successfully."
    )
    print(
        f"Processed sheets : "
        f"{len(sheet_manifests)}"
    )
    print(
        f"Generated objects: "
        f"{len(all_objects)}"
    )
    print(
        f"Master manifest  : "
        f"{MASTER_MANIFEST}"
    )


if __name__ == "__main__":
    main()
