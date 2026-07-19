"""Controlled, evaluation-only comparison of object style families."""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

from tella.atomic_write import atomic_write_bytes, atomic_write_json
from tella.object_library.models import SourceCandidate
from tella.object_library.processor import _load_cairosvg, normalize_png, normalize_svg
from tella.object_library.service import ObjectIngestionService

FIXED_BACKGROUND = "#332821"
FAMILIES = ("mdi", "material-symbols", "boxicons")
MODES = ("normalized", "tella_adapted")

ROLE_QUERIES: dict[str, tuple[str, ...]] = {
    "phone": ("phone", "smartphone", "mobile phone"),
    "coffee_cup": ("coffee", "coffee cup", "mug"),
    "letter": ("love letter", "envelope", "mail"),
    "flower": ("flower", "flower stem"),
    "book": ("book",),
    "umbrella": ("umbrella",),
    "headphones": ("headphones",),
    "movie_ticket": ("movie ticket", "cinema ticket", "ticket"),
    "popcorn": ("popcorn",),
    "tissue_box": ("tissue box", "facial tissue"),
}

ROLE_TERMS = {
    "phone": {"phone", "smartphone", "mobile"},
    "coffee_cup": {"coffee", "cup", "mug"},
    "letter": {"letter", "envelope", "mail"},
    "flower": {"flower", "stem"},
    "book": {"book"},
    "umbrella": {"umbrella"},
    "headphones": {"headphones", "headphone"},
    "movie_ticket": {"movie", "cinema", "ticket"},
    "popcorn": {"popcorn"},
    "tissue_box": {"tissue", "box"},
}

FAMILY_VARIANTS = {
    "mdi": ("outline", "base"),
    "material-symbols": ("outline-rounded", "rounded", "outline", "base"),
    # Unqualified Boxicons are the regular outline set; "filled" is explicit.
    "boxicons": ("base", "outline"),
}


@dataclass(frozen=True)
class SelectedCandidate:
    role: str
    query: str
    family: str
    variant: str
    candidate: SourceCandidate
    score: int


def family_candidates(candidates: list[SourceCandidate], family: str) -> list[SourceCandidate]:
    """Restrict candidates to one exact Iconify collection prefix."""
    return [item for item in candidates if item.style_family == family]


def candidate_variant(candidate: SourceCandidate) -> str:
    name = candidate.source_object_id.split(":", 1)[-1].lower()
    if "outline-rounded" in name:
        return "outline-rounded"
    if name.endswith("-rounded") or "-rounded-" in name:
        return "rounded"
    if "outline" in name or "regular" in name:
        return "outline"
    if "filled" in name or "-fill" in name or "-solid" in name:
        return "filled"
    return "base"


def _tokens(value: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", value.lower()) if token}


def rank_family_candidates(
    role: str,
    family: str,
    queried: list[tuple[str, SourceCandidate]],
) -> list[SelectedCandidate]:
    preferences = FAMILY_VARIANTS[family]
    terms = ROLE_TERMS[role]
    ranked = []
    for query, candidate in queried:
        if candidate.style_family != family:
            continue
        name_tokens = _tokens(candidate.canonical_label)
        overlap = len(name_tokens.intersection(terms))
        if not overlap:
            continue
        variant = candidate_variant(candidate)
        variant_score = 100 - preferences.index(variant) * 20 if variant in preferences else -25
        exact = 15 if name_tokens == _tokens(query) else 0
        specificity = min(15, len(name_tokens) * 5)
        license_score = 10 if candidate.license.name.lower() not in {"", "unknown"} else -100
        score = min(1, overlap) * 50 + variant_score + exact + specificity + license_score
        ranked.append(SelectedCandidate(role, query, family, variant, candidate, score))
    return sorted(ranked, key=lambda item: (-item.score, item.candidate.source_object_id))


def select_family_candidate(
    role: str,
    family: str,
    queried: list[tuple[str, SourceCandidate]],
) -> SelectedCandidate | None:
    ranked = rank_family_candidates(role, family, queried)
    return ranked[0] if ranked else None


def adapt_svg(normalized_svg: bytes) -> bytes:
    """Apply deliberately light, semantics-preserving Tella harmonization."""
    root = ET.fromstring(normalized_svg)
    for element in root.iter():
        if "stroke" in element.attrib and element.attrib.get("stroke", "").lower() != "none":
            element.set("stroke-linecap", "round")
            element.set("stroke-linejoin", "round")
    output = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return output.replace(b"#F0E6D8", b"#EAD9C8").replace(b"#f0e6d8", b"#ead9c8")


def render_svg(svg: bytes) -> tuple[bytes, bytes, dict[str, object]]:
    root = ET.fromstring(svg)
    view_box = root.attrib.get("viewBox", "0 0 24 24").split()
    width, height = float(view_box[2]), float(view_box[3])
    raster = _load_cairosvg().svg2png(
        bytestring=svg,
        output_width=1024,
        output_height=max(1, round(1024 * height / width)),
    )
    return normalize_png(raster)


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def evaluate_families(
    service: ObjectIngestionService,
    output_root: Path,
) -> dict[str, Any]:
    """Acquire and render a small isolated family benchmark."""
    output_root.mkdir(parents=True, exist_ok=True)
    evaluation: dict[str, Any] = {
        "schema_version": 1,
        "background": FIXED_BACKGROUND,
        "evaluation_only": True,
        "families": {},
        "roles": list(ROLE_QUERIES),
    }
    query_results: dict[str, list[tuple[str, SourceCandidate]]] = {}
    for role, queries in ROLE_QUERIES.items():
        queried = []
        for query in queries:
            for candidate in service.search(query, limit=64, sources=["iconify"]):
                queried.append((query, candidate))
        query_results[role] = queried

    for family in FAMILIES:
        family_payload = {"coverage": 0, "missing_roles": [], "objects": {}}
        for role in ROLE_QUERIES:
            selection = select_family_candidate(role, family, query_results[role])
            if selection is None:
                family_payload["missing_roles"].append(role)
                continue
            raw = service.fetch_candidate(selection.candidate)
            normalized, svg_metadata = normalize_svg(raw)
            adapted = adapt_svg(normalized)
            object_dir = output_root / "objects" / family / role
            object_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(object_dir / "raw.svg", raw)
            record: dict[str, Any] = {
                "semantic_role": role,
                "query": selection.query,
                "source_object_id": selection.candidate.source_object_id,
                "collection": selection.candidate.style_family,
                "style_family": family,
                "style_variant": selection.variant,
                "selection_score": selection.score,
                "license": selection.candidate.license.model_dump(mode="json"),
                "svg_metadata": svg_metadata,
                "evaluation_only": True,
                "production_eligible": False,
                "modes": {},
            }
            for mode, svg in (("normalized", normalized), ("tella_adapted", adapted)):
                processed, preview, png_metadata = render_svg(svg)
                atomic_write_bytes(object_dir / f"{mode}.svg", svg)
                atomic_write_bytes(object_dir / f"{mode}.png", processed)
                atomic_write_bytes(object_dir / f"{mode}_preview.png", preview)
                record["modes"][mode] = {
                    "svg_path": str((object_dir / f"{mode}.svg").resolve()),
                    "png_path": str((object_dir / f"{mode}.png").resolve()),
                    "svg_sha256": _sha(svg),
                    "png_sha256": _sha(processed),
                    "png_metadata": png_metadata,
                }
            family_payload["coverage"] += 1
            family_payload["objects"][role] = record
        evaluation["families"][family] = family_payload
    metadata_dir = output_root / "metadata"
    atomic_write_json(metadata_dir / "evaluation.json", evaluation)
    license_summary = {
        family: sorted(
            {
                (item["license"]["name"], item["license"]["url"])
                for item in payload["objects"].values()
            }
        )
        for family, payload in evaluation["families"].items()
    }
    atomic_write_json(metadata_dir / "license_summary.json", license_summary)
    return evaluation


def _fit_object(path: str | Path, size: int) -> Image.Image:
    image = Image.open(path).convert("RGBA")
    image.thumbnail((size, size), Image.Resampling.LANCZOS)
    return image


def generate_contact_sheets(evaluation: dict[str, Any], output_root: Path) -> list[Path]:
    roles = list(ROLE_QUERIES)
    contact_root = output_root / "contact_sheets"
    contact_root.mkdir(parents=True, exist_ok=True)
    outputs = []
    cell, label_height = 240, 34
    for family in FAMILIES:
        for mode in MODES:
            sheet = Image.new("RGB", (cell * 5, (cell + label_height) * 2), FIXED_BACKGROUND)
            draw = ImageDraw.Draw(sheet)
            for index, role in enumerate(roles):
                x, y = (index % 5) * cell, (index // 5) * (cell + label_height)
                record = evaluation["families"][family]["objects"].get(role)
                if record:
                    image = _fit_object(record["modes"][mode]["png_path"], 190)
                    sheet.paste(
                        image,
                        (x + (cell - image.width) // 2, y + (cell - image.height) // 2),
                        image,
                    )
                else:
                    draw.line((x + 70, y + 70, x + 170, y + 170), fill="#8C7161", width=4)
                    draw.line((x + 170, y + 70, x + 70, y + 170), fill="#8C7161", width=4)
                draw.text((x + 8, y + cell + 7), role, fill="#F0E6D8")
            path = contact_root / f"{family.replace('-', '_')}_{mode}.png"
            sheet.save(path, "PNG", optimize=True)
            outputs.append(path)

    cross = Image.new(
        "RGB",
        (220 * (len(FAMILIES) + 1), 190 * len(roles)),
        FIXED_BACKGROUND,
    )
    draw = ImageDraw.Draw(cross)
    for column, family in enumerate(FAMILIES, 1):
        draw.text((column * 220 + 8, 8), family, fill="#F0E6D8")
    for row, role in enumerate(roles):
        y = row * 190
        draw.text((8, y + 80), role, fill="#F0E6D8")
        for column, family in enumerate(FAMILIES, 1):
            record = evaluation["families"][family]["objects"].get(role)
            if record:
                image = _fit_object(record["modes"]["tella_adapted"]["png_path"], 150)
                cross.paste(image, (column * 220 + (220 - image.width) // 2, y + 25), image)
            else:
                draw.text((column * 220 + 78, y + 82), "MISSING", fill="#B48A73")
    cross_path = contact_root / "cross_family_by_role.png"
    cross.save(cross_path, "PNG", optimize=True)
    outputs.append(cross_path)
    return outputs


def render_character_context(
    character_path: Path,
    object_paths: list[Path],
    output: Path,
) -> Path:
    canvas = Image.new("RGBA", (1080, 1920), FIXED_BACKGROUND)
    character = Image.open(character_path).convert("RGBA")
    character.thumbnail((700, 700), Image.Resampling.LANCZOS)
    char_x, ground = (1080 - character.width) // 2, 1710
    char_y = ground - character.height
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse(
        (char_x + 30, ground - 12, char_x + character.width - 30, ground + 20),
        fill=(0, 0, 0, 90),
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(16)))
    canvas.alpha_composite(character, (char_x, char_y))
    slots = ((110, ground), (790, ground))
    for path, (x, baseline) in zip(object_paths, slots):
        image = _fit_object(path, 210)
        y = baseline - image.height
        object_shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(object_shadow).ellipse(
            (x - 8, baseline - 8, x + image.width + 8, baseline + 10),
            fill=(0, 0, 0, 100),
        )
        canvas.alpha_composite(object_shadow.filter(ImageFilter.GaussianBlur(9)))
        canvas.alpha_composite(image, (x, y))
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output, "PNG", optimize=True)
    return output


def metadata_hashes(evaluation: dict[str, Any]) -> dict[str, Any]:
    return {
        family: {
            role: {mode: record["modes"][mode]["png_sha256"] for mode in MODES}
            for role, record in payload["objects"].items()
        }
        for family, payload in evaluation["families"].items()
    }


__all__ = [
    "FAMILIES",
    "FIXED_BACKGROUND",
    "MODES",
    "ROLE_QUERIES",
    "SelectedCandidate",
    "adapt_svg",
    "candidate_variant",
    "evaluate_families",
    "family_candidates",
    "generate_contact_sheets",
    "metadata_hashes",
    "rank_family_candidates",
    "render_character_context",
    "select_family_candidate",
]
