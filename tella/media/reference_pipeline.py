"""Job-scoped character reference generation for minimalist reference mode."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from PIL import Image, ImageDraw

from tella.media.image_provider import ImageProvider
from tella.media.visual_qc import image_hash
from tella.planner.models import CharacterReference, CharacterSpec, VisualBible
from tella.planner.visual_prompts import build_reference_prompt

logger = logging.getLogger("tella.media.reference_pipeline")

REFERENCE_VARIANTS = (
    "neutral front view, standing, arms relaxed",
    "gentle three-quarter view, standing, calm expression",
    "simple side view, walking or turning softly",
)


async def generate_character_references(
    visual_bible: VisualBible,
    job_dir: Path,
    provider: ImageProvider,
    *,
    aspect: str = "9:16",
) -> list[CharacterReference]:
    references_dir = Path(job_dir) / "references"
    references_dir.mkdir(parents=True, exist_ok=True)
    references: list[CharacterReference] = []
    for character in visual_bible.character_specs:
        for index, variant in enumerate(REFERENCE_VARIANTS, start=1):
            reference_id = f"{character.character_id}_ref_{index:02d}"
            out = references_dir / f"{reference_id}.png"
            prompt = build_reference_prompt(character, visual_bible, variant)
            seed = 11000 + index * 97
            ref = CharacterReference(
                character_id=character.character_id,
                reference_id=reference_id,
                image_path=str(out.relative_to(job_dir)),
                prompt_used=prompt,
                status="pending",
                provider=provider.provider_name,
                seed=seed if provider.supports_seed() else None,
            )
            if out.is_file():
                ref.status = "generated"
                ref.hash = image_hash(out)
                ref.score = _score_reference(out)
                references.append(ref)
                continue
            try:
                await provider.generate_text_image(
                    prompt=prompt,
                    negative_prompt=visual_bible.global_negative_prompt,
                    aspect=aspect,
                    seed=seed if provider.supports_seed() else None,
                    out_path=out,
                    metadata={"reference_id": reference_id, "character_id": character.character_id},
                )
                ref.status = "generated"
                ref.hash = image_hash(out)
                ref.score = _score_reference(out)
            except Exception as exc:
                ref.status = "failed"
                ref.failure_reason = str(exc)[:500]
                logger.warning("reference generation failed %s: %s", reference_id, str(exc)[:160])
            references.append(ref)
    selected = select_best_references(references, visual_bible)
    save_reference_metadata(selected, job_dir)
    build_reference_sheet(selected, visual_bible, job_dir)
    return selected


def select_best_references(
    references: list[CharacterReference],
    visual_bible: VisualBible,
) -> list[CharacterReference]:
    selected: list[CharacterReference] = []
    for character in visual_bible.character_specs:
        generated = [
            ref for ref in references
            if ref.character_id == character.character_id and ref.status == "generated"
        ]
        generated.sort(key=lambda ref: (ref.score, ref.reference_id), reverse=True)
        for ref in generated[:2]:
            ref.selected = True
        selected.extend(ref for ref in references if ref.character_id == character.character_id)
    return selected


def build_reference_sheet(
    references: list[CharacterReference],
    visual_bible: VisualBible,
    job_dir: Path,
) -> Path:
    refs = [ref for ref in references if ref.status == "generated"]
    out = Path(job_dir) / "references" / "reference_sheet.png"
    cell_w, cell_h, label_h = 220, 330, 70
    cols = max(1, min(3, len(refs) or 1))
    rows = max(1, (len(refs) + cols - 1) // cols)
    sheet = Image.new("RGB", (cols * cell_w, rows * (cell_h + label_h)), "#eee6dc")
    draw = ImageDraw.Draw(sheet)
    if not refs:
        draw.text((16, 16), "No generated character references.", fill="#4e3a31")
        out.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(out, "PNG")
        return out
    for idx, ref in enumerate(refs):
        x = (idx % cols) * cell_w
        y = (idx // cols) * (cell_h + label_h)
        path = Path(job_dir) / ref.image_path
        with Image.open(path) as img:
            img = img.convert("RGB")
            img.thumbnail((cell_w, cell_h), Image.LANCZOS)
            sheet.paste(img, (x + (cell_w - img.width) // 2, y + (cell_h - img.height) // 2))
        draw.text(
            (x + 8, y + cell_h + 6),
            f"{ref.reference_id}\nscore {ref.score:.2f}\nselected {ref.selected}",
            fill="#4e3a31",
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, "PNG")
    return out


def save_reference_metadata(references: list[CharacterReference], job_dir: Path) -> Path:
    out = Path(job_dir) / "references" / "references.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps([ref.model_dump() for ref in references], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out


def _score_reference(path: Path) -> float:
    try:
        with Image.open(path) as img:
            bbox = img.convert("RGB").getbbox()
            if not bbox:
                return 0.0
            width, height = img.size
            visible_h = bbox[3] - bbox[1]
            if visible_h <= 0:
                return 0.0
            size_score = min(1.0, visible_h / max(1, height * 0.6))
            edge_penalty = 0.15 if bbox[0] <= 2 or bbox[1] <= 2 or bbox[2] >= width - 2 or bbox[3] >= height - 2 else 0.0
            return max(0.0, round(size_score - edge_penalty, 3))
    except OSError:
        return 0.0


def selected_reference_paths(references: list[CharacterReference], job_dir: Path) -> list[Path]:
    return [
        Path(job_dir) / ref.image_path
        for ref in references
        if ref.selected and ref.status == "generated"
    ]


__all__ = [
    "build_reference_sheet",
    "generate_character_references",
    "save_reference_metadata",
    "select_best_references",
    "selected_reference_paths",
]
