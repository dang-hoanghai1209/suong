"""Safe normalization for SVG and transparent PNG object assets."""

from __future__ import annotations

import hashlib
import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

from tella.atomic_write import atomic_write_bytes
from tella.object_library.models import ObjectRecord
from tella.object_library.storage import ObjectStore


class ProcessingError(ValueError):
    pass


DEFAULT_OBJECT_COLOR = "#F0E6D8"


def _load_cairosvg():
    """Load CairoSVG, using the bundled pycairo backend on Windows when needed."""
    try:
        import cairosvg

        return cairosvg
    except OSError:
        # cairosvg normally discovers cairo through cairocffi. Windows wheels for
        # pycairo provide the same backend without requiring a separate DLL install.
        import sys

        import cairo

        sys.modules["cairocffi"] = cairo
        for module_name in list(sys.modules):
            if module_name == "cairosvg" or module_name.startswith("cairosvg."):
                del sys.modules[module_name]
        import cairosvg

        return cairosvg


def _svg_dimensions(root: ET.Element) -> tuple[int | None, int | None]:
    view_box = root.attrib.get("viewBox", "").replace(",", " ").split()
    if len(view_box) == 4:
        try:
            return max(1, round(float(view_box[2]))), max(1, round(float(view_box[3])))
        except ValueError:
            pass

    def dimension(name: str) -> int | None:
        match = re.match(r"[0-9.]+", root.attrib.get(name, ""))
        return round(float(match.group())) if match else None

    return dimension("width"), dimension("height")


def normalize_svg(content: bytes) -> tuple[bytes, dict[str, object]]:
    if len(content) > 5_000_000:
        raise ProcessingError("SVG exceeds 5 MB safety limit")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ProcessingError(f"invalid SVG: {exc}") from exc
    if root.tag.rsplit("}", 1)[-1].lower() != "svg":
        raise ProcessingError("document root is not SVG")
    warnings = []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].lower()
        if tag in {"script", "foreignobject", "iframe"}:
            raise ProcessingError(f"unsafe SVG element: {tag}")
        for key in list(element.attrib):
            value = element.attrib[key].strip().lower()
            local_key = key.rsplit("}", 1)[-1].lower()
            if local_key.startswith("on"):
                del element.attrib[key]
                warnings.append("removed event attribute")
            elif local_key == "href" and (
                value.startswith("http:") or value.startswith("https:") or value.startswith("data:")
            ):
                raise ProcessingError("external SVG references are not allowed")
    width, height = _svg_dimensions(root)
    if not width or not height:
        raise ProcessingError("SVG has no usable width/height or viewBox")
    if width > 10000 or height > 10000:
        raise ProcessingError("SVG dimensions exceed 10000px safety limit")
    root.set("viewBox", root.attrib.get("viewBox", f"0 0 {width} {height}"))
    root.set("width", str(width))
    root.set("height", str(height))
    root.set("preserveAspectRatio", "xMidYMid meet")
    normalized = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    # Iconify's monochrome sets commonly use currentColor. A standalone PNG
    # otherwise defaults to black, which disappears on Tella's fixed dark brown.
    normalized = normalized.replace(b"currentColor", DEFAULT_OBJECT_COLOR.encode("ascii"))
    normalized = normalized.replace(b"currentcolor", DEFAULT_OBJECT_COLOR.lower().encode("ascii"))
    text = normalized.decode("utf-8", errors="ignore").lower()
    outline = "stroke=" in text and 'fill="none"' in text
    filled = "fill=" in text and 'fill="none"' not in text
    style = "mixed" if outline and filled else "outline" if outline else "filled"
    colors = set(re.findall(r"#[0-9a-f]{3,8}", text))
    return normalized, {
        "width": width,
        "height": height,
        "warnings": warnings,
        "color_mode": "multicolor" if len(colors) > 1 else "monochrome",
        "rendering_style": style,
    }


def normalize_png(content: bytes, canvas: int = 1024) -> tuple[bytes, bytes, dict[str, object]]:
    try:
        image = Image.open(io.BytesIO(content)).convert("RGBA")
        image.load()
    except Exception as exc:
        raise ProcessingError(f"invalid raster image: {exc}") from exc
    if image.width < 16 or image.height < 16:
        raise ProcessingError("raster dimensions are below 16px")
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            _red, _green, _blue, alpha = pixels[x, y]
            if alpha <= 2:
                pixels[x, y] = (0, 0, 0, 0)
    bbox = image.getchannel("A").getbbox()
    if not bbox:
        raise ProcessingError("raster has an empty alpha channel")
    warnings = []
    if bbox == (0, 0, image.width, image.height) and image.getchannel("A").getextrema() == (
        255,
        255,
    ):
        warnings.append("asset has no transparent background")
    cropped = image.crop(bbox)
    max_content = round(canvas * 0.92)
    cropped.thumbnail((max_content, max_content), Image.Resampling.LANCZOS)
    margin = max(4, round(max(cropped.size) * 0.04))
    output = Image.new(
        "RGBA", (cropped.width + margin * 2, cropped.height + margin * 2), (0, 0, 0, 0)
    )
    output.alpha_composite(cropped, (margin, margin))
    processed = io.BytesIO()
    output.save(processed, "PNG", optimize=True)
    preview_image = output.copy()
    preview_image.thumbnail((256, 256), Image.Resampling.LANCZOS)
    preview = io.BytesIO()
    preview_image.save(preview, "PNG", optimize=True)
    colors = cropped.convert("RGB").getcolors(maxcolors=4096)
    return (
        processed.getvalue(),
        preview.getvalue(),
        {
            "width": output.width,
            "height": output.height,
            "warnings": warnings,
            "color_mode": "monochrome" if colors and len(colors) <= 8 else "multicolor",
            "rendering_style": "unknown",
        },
    )


def process_record(record: ObjectRecord, store: ObjectStore) -> ObjectRecord:
    raw_path = Path(record.local_raw_path)
    try:
        content = raw_path.read_bytes()
        if record.original_format.lower() == "svg":
            normalized_svg, svg_metadata = normalize_svg(content)
            vector_path = store.asset_path("processed", record, "svg")
            atomic_write_bytes(vector_path, normalized_svg)
            try:
                cairosvg = _load_cairosvg()
            except (ImportError, OSError) as exc:
                raise ProcessingError(
                    "SVG rasterization requires CairoSVG and a Cairo backend"
                ) from exc
            raster = cairosvg.svg2png(
                bytestring=normalized_svg,
                output_width=1024,
                output_height=round(
                    1024 * int(svg_metadata["height"]) / int(svg_metadata["width"])
                ),
            )
            processed, preview, metadata = normalize_png(raster)
            metadata["rendering_style"] = svg_metadata["rendering_style"]
            metadata["color_mode"] = svg_metadata["color_mode"]
            metadata["warnings"] = [*svg_metadata["warnings"], *metadata["warnings"]]
            processed_path = store.asset_path("processed", record, "png")
            preview_path = store.asset_path("previews", record, "png")
            atomic_write_bytes(processed_path, processed)
            atomic_write_bytes(preview_path, preview)
        elif record.original_format.lower() == "png":
            processed, preview, metadata = normalize_png(content)
            processed_path = store.asset_path("processed", record, "png")
            preview_path = store.asset_path("previews", record, "png")
            atomic_write_bytes(processed_path, processed)
            atomic_write_bytes(preview_path, preview)
        else:
            raise ProcessingError(f"unsupported input format: {record.original_format}")
    except (OSError, ProcessingError) as exc:
        record.processing_status = "failed"
        record.quality_status = "failed"
        record.production_eligible = False
        record.processing_warnings = [str(exc)]
        store.save_record(record)
        return record
    record.local_processed_path = str(processed_path.resolve())
    record.preview_path = str(preview_path.resolve())
    record.width = int(metadata["width"])
    record.height = int(metadata["height"])
    record.aspect_ratio = round(record.width / record.height, 6)
    record.color_mode = str(metadata["color_mode"])
    record.rendering_style = str(metadata["rendering_style"])
    record.processing_warnings = list(metadata["warnings"])
    if record.license.name.strip().lower() in {"", "unknown", "unknown license"}:
        record.processing_warnings.append("license metadata is ambiguous")
    record.content_sha256 = hashlib.sha256(processed_path.read_bytes()).hexdigest()
    record.processing_status = "processed"
    record.quality_status = "review" if record.processing_warnings else "approved"
    record.production_eligible = not record.processing_warnings
    store.save_record(record)
    return record
