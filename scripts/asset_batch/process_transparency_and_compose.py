from __future__ import annotations

import json
import math
import shutil
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


from PIL import Image, ImageChops, ImageDraw, ImageOps


ROOT = Path(r"D:\tella-assets-staging\mvp_v1")
PROCESSED_ROOT = Path(r"D:\tella-assets-staging\mvp_v1_processed")

ASSET_MANIFEST = ROOT / "asset_manifest.json"
OBJECT_MANIFEST = ROOT / "objects" / "object_manifest.json"
FEMALE_EXPRESSION_MANIFEST = (
    ROOT
    / "characters"
    / "female_01"
    / "female_01_expression_map.json"
)

PROCESSED_INDEX_FILE = PROCESSED_ROOT / "processed_asset_index.json"
PROCESSING_REPORT_FILE = PROCESSED_ROOT / "processing_report.json"

TEST_RENDER_DIR = PROCESSED_ROOT / "test_renders"
TEST_FRAME_FILE = (
    TEST_RENDER_DIR
    / "bedroom_night__female_01__sit_hug_knees__phone_dark__pillow.png"
)
TEST_SCENE_FILE = (
    TEST_RENDER_DIR
    / "bedroom_night__female_01__sit_hug_knees__phone_dark__pillow.scene.json"
)


BACKGROUND_FILE = (
    ROOT
    / "backgrounds"
    / "indoor"
    / "bedroom_night_01.png"
)

CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920

BACKGROUND_THRESHOLD = 34
TRIM_PADDING = 10
MIN_ALPHA_KEEP = 1


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def relative_to_root(path: Path) -> str:
    return path.relative_to(PROCESSED_ROOT).as_posix()


def gather_asset_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    asset_manifest = read_json(ASSET_MANIFEST)
    for asset in asset_manifest["assets"]:
        rel = asset["relative_path"]

        if rel.startswith("characters/") or rel.startswith("backgrounds/crowd/"):
            source_path = ROOT / rel
            processed_path = PROCESSED_ROOT / rel

            asset_type = "crowd" if rel.startswith("backgrounds/crowd/") else "character"

            records.append(
                {
                    "asset_id": asset["asset_id"],
                    "asset_type": asset_type,
                    "character_id": asset.get("character_id"),
                    "tier": asset.get("tier"),
                    "enabled_by_default": asset.get("enabled_by_default"),
                    "canonical": asset.get("canonical"),
                    "relative_path": rel,
                    "source_path": source_path,
                    "processed_path": processed_path,
                    "source_sheet_id": asset.get("source_sheet_id"),
                    "asset_group": asset.get("asset_group"),
                }
            )

    female_expression_manifest = read_json(FEMALE_EXPRESSION_MANIFEST)
    for item in female_expression_manifest["expressions"]:
        rel = item["relative_path"]
        source_path = ROOT / "characters" / "female_01" / rel
        processed_path = PROCESSED_ROOT / "characters" / "female_01" / rel

        records.append(
            {
                "asset_id": item["expression_id"],
                "asset_type": "expression",
                "character_id": "female_01",
                "tier": "core",
                "enabled_by_default": True,
                "canonical": True,
                "relative_path": str(
                    Path("characters") / "female_01" / rel
                ).replace("\\", "/"),
                "source_path": source_path,
                "processed_path": processed_path,
                "source_sheet_id": "female_01_expressions_r3_c3",
                "asset_group": "expressions",
            }
        )

    object_manifest = read_json(OBJECT_MANIFEST)
    for item in object_manifest["objects"]:
        rel = item["relative_path"]
        source_path = ROOT / rel
        processed_path = PROCESSED_ROOT / rel

        records.append(
            {
                "asset_id": item["asset_id"],
                "asset_type": "object",
                "character_id": None,
                "tier": "core",
                "enabled_by_default": True,
                "canonical": True,
                "relative_path": rel,
                "source_path": source_path,
                "processed_path": processed_path,
                "source_sheet_id": item.get("source_sheet_id"),
                "asset_group": item.get("category"),
            }
        )

    return records


def border_pixels(image: Image.Image) -> list[tuple[int, int, int]]:
    image = image.convert("RGBA")
    width, height = image.size
    pixels = image.load()

    samples: list[tuple[int, int, int]] = []

    for x in range(width):
        samples.append(pixels[x, 0][:3])
        samples.append(pixels[x, height - 1][:3])

    for y in range(height):
        samples.append(pixels[0, y][:3])
        samples.append(pixels[width - 1, y][:3])

    return samples


def mean_rgb(samples: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    r = round(sum(p[0] for p in samples) / len(samples))
    g = round(sum(p[1] for p in samples) / len(samples))
    b = round(sum(p[2] for p in samples) / len(samples))
    return (r, g, b)


def color_distance_sq(
    a: tuple[int, int, int],
    b: tuple[int, int, int],
) -> int:
    return (
        (a[0] - b[0]) ** 2
        + (a[1] - b[1]) ** 2
        + (a[2] - b[2]) ** 2
    )


def remove_edge_connected_background(
    image: Image.Image,
    threshold: int = BACKGROUND_THRESHOLD,
) -> tuple[Image.Image, dict[str, Any]]:
    rgba = image.convert("RGBA")
    width, height = rgba.size
    pixels = rgba.load()

    bg_samples = border_pixels(rgba)
    bg_color = mean_rgb(bg_samples)
    threshold_sq = threshold * threshold

    def is_bg_candidate(x: int, y: int) -> bool:
        r, g, b, a = pixels[x, y]
        if a == 0:
            return True
        return color_distance_sq((r, g, b), bg_color) <= threshold_sq

    visited = [[False] * width for _ in range(height)]
    queue: deque[tuple[int, int]] = deque()

    def enqueue_if_candidate(x: int, y: int) -> None:
        if 0 <= x < width and 0 <= y < height:
            if not visited[y][x] and is_bg_candidate(x, y):
                visited[y][x] = True
                queue.append((x, y))

    for x in range(width):
        enqueue_if_candidate(x, 0)
        enqueue_if_candidate(x, height - 1)

    for y in range(height):
        enqueue_if_candidate(0, y)
        enqueue_if_candidate(width - 1, y)

    while queue:
        x, y = queue.popleft()
        for nx, ny in (
            (x - 1, y),
            (x + 1, y),
            (x, y - 1),
            (x, y + 1),
        ):
            if 0 <= nx < width and 0 <= ny < height:
                if not visited[ny][nx] and is_bg_candidate(nx, ny):
                    visited[ny][nx] = True
                    queue.append((nx, ny))

    out = rgba.copy()
    out_pixels = out.load()

    removed_pixels = 0
    for y in range(height):
        for x in range(width):
            if visited[y][x]:
                r, g, b, _ = out_pixels[x, y]
                out_pixels[x, y] = (r, g, b, 0)
                removed_pixels += 1

    alpha = out.getchannel("A")
    bbox = alpha.getbbox()

    if bbox is None:
        trimmed = out
        trim_bbox = {
            "left": 0,
            "top": 0,
            "right": width,
            "bottom": height,
        }
    else:
        left, top, right, bottom = bbox
        left = max(0, left - TRIM_PADDING)
        top = max(0, top - TRIM_PADDING)
        right = min(width, right + TRIM_PADDING)
        bottom = min(height, bottom + TRIM_PADDING)

        trimmed = out.crop((left, top, right, bottom))
        trim_bbox = {
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
        }

    metadata = {
        "source_width": width,
        "source_height": height,
        "background_color_rgb": bg_color,
        "threshold": threshold,
        "removed_pixels": removed_pixels,
        "trim_bbox": trim_bbox,
        "trimmed_width": trimmed.width,
        "trimmed_height": trimmed.height,
    }

    return trimmed, metadata


def process_transparent_asset(record: dict[str, Any]) -> dict[str, Any]:
    source_path: Path = record["source_path"]
    processed_path: Path = record["processed_path"]

    if not source_path.is_file():
        raise FileNotFoundError(f"Missing source asset: {source_path}")

    ensure_dir(processed_path.parent)

    with Image.open(source_path) as image:
        image = ImageOps.exif_transpose(image).convert("RGBA")
        processed_image, metadata = remove_edge_connected_background(image)

    processed_image.save(
        processed_path,
        format="PNG",
        optimize=True,
    )

    return {
        **record,
        "processed_relative_path": processed_path.relative_to(PROCESSED_ROOT).as_posix(),
        "processing": metadata,
    }


def copy_backgrounds() -> list[dict[str, Any]]:
    background_records: list[dict[str, Any]] = []

    background_paths = [
        ROOT / "backgrounds" / "indoor" / "bedroom_day_01.png",
        ROOT / "backgrounds" / "indoor" / "bedroom_night_01.png",
        ROOT / "backgrounds" / "indoor" / "room_by_window_day_01.png",
        ROOT / "backgrounds" / "public_places" / "cafe_corner_day_01.png",
        ROOT / "backgrounds" / "outdoor" / "park_bench_day_01.png",
        ROOT / "backgrounds" / "outdoor" / "bus_stop_rain_day_01.png",
    ]

    for path in background_paths:
        if not path.is_file():
            raise FileNotFoundError(f"Missing background: {path}")

        processed_path = PROCESSED_ROOT / path.relative_to(ROOT)
        ensure_dir(processed_path.parent)
        shutil.copy2(path, processed_path)

        with Image.open(path) as image:
            width, height = image.size

        background_records.append(
            {
                "asset_id": path.stem,
                "asset_type": "background",
                "source_path": path.relative_to(ROOT).as_posix(),
                "processed_relative_path": processed_path.relative_to(PROCESSED_ROOT).as_posix(),
                "width": width,
                "height": height,
            }
        )

    return background_records


def select_best_asset(
    records: list[dict[str, Any]],
    *,
    asset_type: str,
    asset_id: str,
    character_id: str | None = None,
) -> dict[str, Any]:
    candidates = [
        record
        for record in records
        if record["asset_type"] == asset_type
        and record["asset_id"] == asset_id
        and (
            character_id is None
            or record.get("character_id") == character_id
        )
    ]

    if not candidates:
        raise RuntimeError(
            f"Could not resolve asset_type={asset_type}, "
            f"asset_id={asset_id}, character_id={character_id}"
        )

    def sort_key(record: dict[str, Any]) -> tuple[int, int, int]:
        enabled = 1 if record.get("enabled_by_default") else 0
        canonical = 1 if record.get("canonical") else 0

        tier_rank = 0
        tier = record.get("tier")
        if tier == "core":
            tier_rank = 3
        elif tier == "reference":
            tier_rank = 2
        elif tier == "optional":
            tier_rank = 1
        elif tier == "backup":
            tier_rank = 0

        return (enabled, canonical, tier_rank)

    candidates.sort(key=sort_key, reverse=True)
    return candidates[0]


def fit_background_cover(
    image: Image.Image,
    width: int,
    height: int,
) -> Image.Image:
    image = image.convert("RGB")

    scale = max(width / image.width, height / image.height)
    new_width = math.ceil(image.width * scale)
    new_height = math.ceil(image.height * scale)

    resized = image.resize((new_width, new_height), Image.LANCZOS)

    left = (new_width - width) // 2
    top = (new_height - height) // 2
    right = left + width
    bottom = top + height

    return resized.crop((left, top, right, bottom))


def scale_to_height(image: Image.Image, target_height: int) -> Image.Image:
    scale = target_height / image.height
    target_width = max(1, round(image.width * scale))
    return image.resize((target_width, target_height), Image.LANCZOS)


def paste_rgba(
    canvas: Image.Image,
    asset: Image.Image,
    x: int,
    y: int,
) -> None:
    canvas.alpha_composite(asset, (x, y))


def add_shadow(
    canvas: Image.Image,
    bbox: tuple[int, int, int, int],
    alpha: int = 45,
) -> None:
    x1, y1, x2, y2 = bbox
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(shadow)
    draw.ellipse((x1, y1, x2, y2), fill=(0, 0, 0, alpha))
    shadow = shadow.filter(ImageFilterSafe.blur(radius=8))
    canvas.alpha_composite(shadow)


class ImageFilterSafe:
    @staticmethod
    def blur(radius: int):
        from PIL import ImageFilter
        return ImageFilter.GaussianBlur(radius=radius)


def compose_test_frame(processed_records: list[dict[str, Any]]) -> dict[str, Any]:
    ensure_dir(TEST_RENDER_DIR)

    background_processed_path = PROCESSED_ROOT / "backgrounds" / "indoor" / "bedroom_night_01.png"
    if not background_processed_path.is_file():
        raise FileNotFoundError(
            f"Processed background missing: {background_processed_path}"
        )

    pose_record = select_best_asset(
        processed_records,
        asset_type="character",
        asset_id="sit_hug_knees",
        character_id="female_01",
    )

    phone_record = select_best_asset(
        processed_records,
        asset_type="object",
        asset_id="phone_dark",
    )

    pillow_record = select_best_asset(
        processed_records,
        asset_type="object",
        asset_id="pillow",
    )

    with Image.open(background_processed_path) as bg:
        background = fit_background_cover(bg, CANVAS_WIDTH, CANVAS_HEIGHT).convert("RGBA")

    with Image.open(PROCESSED_ROOT / pose_record["processed_relative_path"]) as img:
        character = img.convert("RGBA")

    with Image.open(PROCESSED_ROOT / phone_record["processed_relative_path"]) as img:
        phone = img.convert("RGBA")

    with Image.open(PROCESSED_ROOT / pillow_record["processed_relative_path"]) as img:
        pillow = img.convert("RGBA")

    canvas = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (255, 255, 255, 255))
    paste_rgba(canvas, background, 0, 0)

    character = scale_to_height(character, 820)
    pillow = scale_to_height(pillow, 180)
    phone = scale_to_height(phone, 92)

    character_x = (CANVAS_WIDTH - character.width) // 2
    character_y = CANVAS_HEIGHT - character.height - 210

    pillow_x = character_x - 120
    pillow_y = CANVAS_HEIGHT - pillow.height - 250

    phone_x = character_x + character.width - 100
    phone_y = CANVAS_HEIGHT - phone.height - 190

    add_shadow(
        canvas,
        (
            character_x + 80,
            character_y + character.height - 35,
            character_x + character.width - 80,
            character_y + character.height + 25,
        ),
        alpha=50,
    )

    add_shadow(
        canvas,
        (
            pillow_x + 12,
            pillow_y + pillow.height - 12,
            pillow_x + pillow.width - 12,
            pillow_y + pillow.height + 20,
        ),
        alpha=36,
    )

    add_shadow(
        canvas,
        (
            phone_x + 4,
            phone_y + phone.height - 4,
            phone_x + phone.width - 4,
            phone_y + phone.height + 10,
        ),
        alpha=30,
    )

    paste_rgba(canvas, pillow, pillow_x, pillow_y)
    paste_rgba(canvas, character, character_x, character_y)
    paste_rgba(canvas, phone, phone_x, phone_y)

    canvas.save(TEST_FRAME_FILE, format="PNG", optimize=True)

    scene_manifest = {
        "schema_version": 1,
        "generated_at_utc": utc_now(),
        "canvas": {
            "width": CANVAS_WIDTH,
            "height": CANVAS_HEIGHT,
        },
        "background": "backgrounds/indoor/bedroom_night_01.png",
        "character": {
            "character_id": "female_01",
            "asset_id": "sit_hug_knees",
            "relative_path": pose_record["processed_relative_path"],
            "placement": {
                "x": character_x,
                "y": character_y,
                "width": character.width,
                "height": character.height,
            },
        },
        "objects": [
            {
                "asset_id": "pillow",
                "relative_path": pillow_record["processed_relative_path"],
                "placement": {
                    "x": pillow_x,
                    "y": pillow_y,
                    "width": pillow.width,
                    "height": pillow.height,
                },
            },
            {
                "asset_id": "phone_dark",
                "relative_path": phone_record["processed_relative_path"],
                "placement": {
                    "x": phone_x,
                    "y": phone_y,
                    "width": phone.width,
                    "height": phone.height,
                },
            },
        ],
        "output": TEST_FRAME_FILE.name,
    }

    write_json(TEST_SCENE_FILE, scene_manifest)
    return scene_manifest


def main() -> None:
    ensure_dir(PROCESSED_ROOT)
    ensure_dir(TEST_RENDER_DIR)

    if not ASSET_MANIFEST.is_file():
        raise FileNotFoundError(f"Missing file: {ASSET_MANIFEST}")

    if not OBJECT_MANIFEST.is_file():
        raise FileNotFoundError(f"Missing file: {OBJECT_MANIFEST}")

    if not FEMALE_EXPRESSION_MANIFEST.is_file():
        raise FileNotFoundError(f"Missing file: {FEMALE_EXPRESSION_MANIFEST}")

    records = gather_asset_records()

    processed_records: list[dict[str, Any]] = []
    transparent_count = 0

    for record in records:
        processed = process_transparent_asset(record)
        processed_records.append(processed)
        transparent_count += 1

    copied_backgrounds = copy_backgrounds()
    scene_manifest = compose_test_frame(processed_records)

    processed_index = {
        "schema_version": 1,
        "generated_at_utc": utc_now(),
        "processed_root": str(PROCESSED_ROOT),
        "transparent_asset_count": transparent_count,
        "background_count": len(copied_backgrounds),
        "assets": [
            {
                "asset_id": record["asset_id"],
                "asset_type": record["asset_type"],
                "character_id": record.get("character_id"),
                "tier": record.get("tier"),
                "enabled_by_default": record.get("enabled_by_default"),
                "canonical": record.get("canonical"),
                "source_sheet_id": record.get("source_sheet_id"),
                "asset_group": record.get("asset_group"),
                "source_path": record["source_path"].relative_to(ROOT).as_posix(),
                "processed_relative_path": record["processed_relative_path"],
                "processing": record["processing"],
            }
            for record in processed_records
        ],
        "backgrounds": copied_backgrounds,
        "test_scene": scene_manifest,
    }

    write_json(PROCESSED_INDEX_FILE, processed_index)

    report = {
        "schema_version": 1,
        "generated_at_utc": utc_now(),
        "source_root": str(ROOT),
        "processed_root": str(PROCESSED_ROOT),
        "transparent_asset_count": transparent_count,
        "background_count": len(copied_backgrounds),
        "test_frame": str(TEST_FRAME_FILE),
        "test_scene_manifest": str(TEST_SCENE_FILE),
    }

    write_json(PROCESSING_REPORT_FILE, report)

    print("Transparency processing and test compositing completed successfully.")
    print(f"Source root            : {ROOT}")
    print(f"Processed root         : {PROCESSED_ROOT}")
    print(f"Transparent assets     : {transparent_count}")
    print(f"Copied backgrounds     : {len(copied_backgrounds)}")
    print(f"Processed asset index  : {PROCESSED_INDEX_FILE}")
    print(f"Processing report      : {PROCESSING_REPORT_FILE}")
    print(f"Test frame             : {TEST_FRAME_FILE}")
    print(f"Test scene manifest    : {TEST_SCENE_FILE}")


if __name__ == "__main__":
    main()
