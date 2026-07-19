"""Raster contact sheet generation for human QC."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from tella.object_library.models import ObjectRecord


def generate_contact_sheet(
    records: list[ObjectRecord],
    output: str | Path,
    columns: int = 5,
    cell_size: int = 240,
    background: str = "#1B1512",
) -> Path:
    raster = [
        item
        for item in records
        if item.preview_path.lower().endswith(".png") and Path(item.preview_path).is_file()
    ]
    if not raster:
        raise ValueError(
            "No raster previews are available; SVG previews remain directly browser-viewable"
        )
    rows = (len(raster) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell_size, rows * (cell_size + 36)), background)
    draw = ImageDraw.Draw(sheet)
    for index, record in enumerate(raster):
        x, y = (index % columns) * cell_size, (index // columns) * (cell_size + 36)
        with Image.open(record.preview_path).convert("RGBA") as preview:
            preview.thumbnail((cell_size - 24, cell_size - 24), Image.Resampling.LANCZOS)
            sheet.paste(
                preview,
                (x + (cell_size - preview.width) // 2, y + (cell_size - preview.height) // 2),
                preview,
            )
        draw.text((x + 8, y + cell_size + 7), record.canonical_label[:32], fill="#E8D9C8")
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination, "PNG", optimize=True)
    return destination
