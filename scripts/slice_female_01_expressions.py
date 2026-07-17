from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image


SOURCE_FILE = Path(
    r"D:\tella-assets-staging\mvp_v1\characters\female_01"
    r"\source_sheets\female_01_expressions_3x3.png"
)

OUTPUT_DIR = Path(
    r"D:\tella-assets-staging\mvp_v1\characters\female_01"
    r"\expressions"
)

MANIFEST_FILE = Path(
    r"D:\tella-assets-staging\mvp_v1\characters\female_01"
    r"\female_01_expression_map.json"
)

EXPRESSIONS = [
    {
        "id": "neutral",
        "description": "Neutral expression",
    },
    {
        "id": "slightly_sad",
        "description": "Slightly sad expression",
    },
    {
        "id": "deeply_sad_no_tears",
        "description": "Deeply sad but not crying",
    },
    {
        "id": "worried",
        "description": "Worried expression",
    },
    {
        "id": "looking_down_thoughtfully",
        "description": "Looking down thoughtfully",
    },
    {
        "id": "peaceful",
        "description": "Peaceful expression",
    },
    {
        "id": "soft_gentle_smile",
        "description": "Soft gentle smile",
    },
    {
        "id": "quietly_crying_one_tear",
        "description": "Quietly crying with one small tear",
    },
    {
        "id": "subtle_surprise",
        "description": "Surprised but subtle",
    },
]


def sha256_file(file_path: Path) -> str:
    digest = hashlib.sha256()

    with file_path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def main() -> None:
    if not SOURCE_FILE.is_file():
        raise FileNotFoundError(
            f"Source sheet does not exist: {SOURCE_FILE}"
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Remove only previously generated expression tiles.
    for old_file in OUTPUT_DIR.glob("*.png"):
        old_file.unlink()

    with Image.open(SOURCE_FILE) as source_image:
        image = source_image.convert("RGBA")
        width, height = image.size

        # Proportional boundaries avoid losing pixels when dimensions
        # are not exactly divisible by three.
        x_edges = [round(column * width / 3) for column in range(4)]
        y_edges = [round(row * height / 3) for row in range(4)]

        manifest_entries = []

        for index, expression in enumerate(EXPRESSIONS, start=1):
            row = (index - 1) // 3
            column = (index - 1) % 3

            left = x_edges[column]
            top = y_edges[row]
            right = x_edges[column + 1]
            bottom = y_edges[row + 1]

            bounding_box = (left, top, right, bottom)
            tile = image.crop(bounding_box)

            filename = f"{index:02d}_{expression['id']}.png"
            output_file = OUTPUT_DIR / filename

            tile.save(
                output_file,
                format="PNG",
                optimize=True,
            )

            manifest_entries.append(
                {
                    "index": index,
                    "row": row + 1,
                    "column": column + 1,
                    "expression_id": expression["id"],
                    "description": expression["description"],
                    "filename": filename,
                    "relative_path": f"expressions/{filename}",
                    "width": tile.width,
                    "height": tile.height,
                    "bbox": {
                        "left": left,
                        "top": top,
                        "right": right,
                        "bottom": bottom,
                    },
                    "sha256": sha256_file(output_file),
                }
            )

            print(
                f"[{index:02d}/09] "
                f"row={row + 1}, col={column + 1} "
                f"expression={expression['id']} "
                f"size={tile.width}x{tile.height} "
                f"file={output_file}"
            )

    manifest = {
        "schema_version": 1,
        "character_id": "female_01",
        "asset_type": "expression_sheet_mapping",
        "source_sheet": str(SOURCE_FILE),
        "source_sha256": sha256_file(SOURCE_FILE),
        "grid": {
            "rows": 3,
            "columns": 3,
            "reading_order": "left_to_right_top_to_bottom",
            "source_width": width,
            "source_height": height,
        },
        "output_directory": str(OUTPUT_DIR),
        "expression_count": len(manifest_entries),
        "expressions": manifest_entries,
    }

    MANIFEST_FILE.write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    generated_files = list(OUTPUT_DIR.glob("*.png"))

    if len(generated_files) != 9:
        raise RuntimeError(
            f"Expected 9 expression images, found {len(generated_files)}"
        )

    print()
    print("Expression slicing completed successfully.")
    print(f"Source size : {width}x{height}")
    print(f"Output count: {len(generated_files)}")
    print(f"Output dir  : {OUTPUT_DIR}")
    print(f"Manifest    : {MANIFEST_FILE}")


if __name__ == "__main__":
    main()
