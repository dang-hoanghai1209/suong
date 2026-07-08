"""Render a QA contact sheet for the minimalist emotional character pack."""
from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

from tella.media import character_rig, sprite_composer
from tella.planner.models import Scene


THUMB_W = 192
THUMB_H = 336
LABEL_H = 76
COLS = 4
SCENE_W = 768
SCENE_H = 1344


def main() -> int:
    manifest = sprite_composer.ensure_sprite_pack()
    curated = sprite_composer.ensure_curated_pose_folder(manifest.character_id)
    rig = character_rig.ensure_rig_parts(manifest.character_id)
    curated_issues = sprite_composer.validate_curated_pose_pack(curated)
    sprite_issues = sprite_composer.validate_sprite_pack(manifest)
    rig_issues = character_rig.validate_rig(rig)
    out_dir = Path("out") / "dev"
    out_dir.mkdir(parents=True, exist_ok=True)

    warning_lines = [
        f"{i.level.upper()} curated {i.asset_id}: {i.message}" for i in curated_issues
    ]
    warning_lines.extend(
        f"{i.level.upper()} sprite {i.asset_id}: {i.message}" for i in sprite_issues
    )
    warning_lines.extend(
        f"{i.level.upper()} rig {i.asset_id}: {i.message}" for i in rig_issues
    )
    for line in warning_lines:
        print(f"[{line}]")

    cells: list[tuple[str, Image.Image]] = []
    cells.extend(_curated_pose_cells(curated))
    cells.extend(_curated_pose_on_canvas_cells(manifest, curated))
    cells.extend(_curated_pose_motif_cells(manifest, curated, out_dir))
    cells.extend(_raw_rig_part_cells(rig))
    cells.extend(_expression_comparison_cells(rig))
    cells.extend(_rendered_rig_pose_cells(rig))
    cells.extend(_rig_pose_on_canvas_cells(manifest, rig))
    cells.extend(_rig_pose_motif_cells(manifest, rig, out_dir))
    cells.extend(_full_pose_sprite_cells(manifest))

    sheet = _build_sheet(cells, warning_lines)
    out_path = out_dir / "minimalist_contact_sheet.png"
    sheet.save(out_path, "PNG")
    print(out_path)
    return 0


def _curated_pose_cells(curated) -> list[tuple[str, Image.Image]]:
    cells = [("SECTION\ncurated full-body poses", _blank_cell("#d8c7b8"))]
    for pose_id, spec in curated.poses.items():
        path = curated.root / spec["file"]
        if not path.is_file():
            label = (
                f"curated\n{pose_id}\nMISSING\nexpr {spec.get('expression','')}\n"
                f"{','.join(spec.get('emotion_tags', [])[:3])}"
            )
            cells.append((label, _blank_cell("#e6b5a8")))
            continue
        img = Image.open(path).convert("RGBA")
        marker = (
            "PLACEHOLDER_CURATED"
            if sprite_composer.is_placeholder_curated_pose(curated, pose_id)
            else "CURATED_READY"
        )
        label = (
            f"curated\n{pose_id}\nexpr {spec.get('expression','')}\n{marker}\n"
            f"bbox {_format_bbox(img.getbbox())}"
        )
        cells.append((label, _fit_on_rig_canvas(img)))
    return cells


def _curated_pose_on_canvas_cells(manifest, curated) -> list[tuple[str, Image.Image]]:
    cells = [("SECTION\ncurated poses on scene canvas", _blank_cell("#d8c7b8"))]
    job_state = sprite_composer.JobState(job_id="contact_curated_scene")
    for i, (pose_id, spec) in enumerate(curated.poses.items(), start=1):
        path = curated.root / spec["file"]
        if not path.is_file():
            cells.append((f"curated scene\n{pose_id}\nMISSING", _blank_cell("#e6b5a8")))
            continue
        scene = Scene(scene_index=i, voice_script=pose_id, image_prompt="", stock_query="")
        scene.emotion_tag = (spec.get("emotion_tags") or ["calm"])[0]
        layout = sprite_composer.choose_layout(scene, pose_id, "paper_heart", SCENE_W, SCENE_H, job_state)
        img = sprite_composer.draw_background(SCENE_W, SCENE_H, i)
        sprite = Image.open(path).convert("RGBA")
        bbox = sprite_composer.paste_sprite(img, sprite, manifest, layout)
        label = (
            f"source curated\n{pose_id}\nexpr {spec.get('expression','')}\n"
            f"anchor {layout.anchor_x},{layout.anchor_y}\nbbox {_format_bbox(bbox)}"
        )
        cells.append((label, img.convert("RGB").resize((THUMB_W, THUMB_H), Image.LANCZOS)))
    return cells


def _curated_pose_motif_cells(manifest, curated, out_dir: Path) -> list[tuple[str, Image.Image]]:
    cells = [("SECTION\ncurated pose + compatible motifs", _blank_cell("#d8c7b8"))]
    job_state = sprite_composer.JobState(job_id="contact_curated_motif")
    for i, (pose_id, spec) in enumerate(curated.poses.items(), start=1):
        path = curated.root / spec["file"]
        if not path.is_file():
            continue
        compatible = spec.get("compatible_motifs", [])[:3]
        for motif_id in compatible:
            scene = Scene(scene_index=i, voice_script=f"{pose_id} {motif_id}", image_prompt="", stock_query="")
            scene.emotion_tag = (spec.get("emotion_tags") or ["calm"])[0]
            layout = sprite_composer.choose_layout(scene, pose_id, motif_id, SCENE_W, SCENE_H, job_state)
            img = sprite_composer.draw_background(SCENE_W, SCENE_H, i)
            motif = sprite_composer.draw_or_load_motif(manifest, motif_id)
            if layout.motif_layer == "behind":
                motif_bbox = sprite_composer.place_motif(img, motif, layout)
            else:
                motif_bbox = None
            sprite_composer.paste_sprite(img, Image.open(path).convert("RGBA"), manifest, layout)
            if layout.motif_layer == "front":
                motif_bbox = sprite_composer.place_motif(img, motif, layout)
            output = out_dir / f"contact_curated_{pose_id}_{motif_id}.jpg"
            img.convert("RGB").save(output, "JPEG", quality=90)
            label = (
                f"source curated\n{pose_id}\n{motif_id}\nexpr {spec.get('expression','')}\n"
                f"motif {_format_bbox(motif_bbox)}"
            )
            cells.append((label, img.convert("RGB").resize((THUMB_W, THUMB_H), Image.LANCZOS)))
    return cells


def _raw_rig_part_cells(rig) -> list[tuple[str, Image.Image]]:
    cells = [("SECTION\nraw rig parts", _blank_cell("#d8c7b8"))]
    head_path = rig.root / rig.head.base_file
    if head_path.is_file():
        img = Image.open(head_path).convert("RGBA")
        marker = "PLACEHOLDER_HEAD" if character_rig.is_placeholder_head(rig) else "CURATED_HEAD"
        label = (
            f"head_base\n{marker}\npivot {rig.head.pivot[0]},{rig.head.pivot[1]}\n"
            f"neck {rig.head.neck_attach[0]},{rig.head.neck_attach[1]}"
        )
        cells.append((label, _fit(_on_neutral_bg(img))))
    else:
        cells.append((f"head_base\nMISSING\n{rig.head.base_file}", _blank_cell("#e6b5a8")))
    for expression_id in character_rig.REQUIRED_EXPRESSIONS:
        face = rig.faces.get(expression_id)
        path = rig.root / face.file if face else Path()
        if not face or not path.is_file():
            cells.append((f"face {expression_id}\nMISSING", _blank_cell("#e6b5a8")))
            continue
        marker = "PLACEHOLDER_FACE" if character_rig.is_placeholder_face(rig, expression_id) else "CURATED_FACE"
        img = Image.open(path).convert("RGBA")
        cells.append((f"face {expression_id}\n{marker}\n{Path(face.file).name}", _fit(_on_neutral_bg(img))))
    for part_id in character_rig.REQUIRED_PARTS:
        part = rig.parts.get(part_id)
        if not part:
            cells.append((f"{part_id}\nMISSING", _blank_cell("#e6b5a8")))
            continue
        path = rig.root / part.file
        if not path.is_file():
            cells.append((f"{part_id}\nMISSING\n{part.file}", _blank_cell("#e6b5a8")))
            continue
        img = Image.open(path).convert("RGBA")
        preview = Image.new("RGBA", (max(img.width, 256), max(img.height, 256)), "#eee6dc")
        preview.alpha_composite(img, ((preview.width - img.width) // 2, (preview.height - img.height) // 2))
        marker = "PLACEHOLDER_PART" if character_rig.is_placeholder_part(rig, part_id) else "CURATED_PART"
        label = f"{part_id}\n{marker}\npivot {part.pivot[0]},{part.pivot[1]}\n{Path(part.file).name}"
        cells.append((label, _fit(preview.convert("RGB"))))
    return cells


def _expression_comparison_cells(rig) -> list[tuple[str, Image.Image]]:
    cells = [("SECTION\nhead expression comparison", _blank_cell("#d8c7b8"))]
    head = Image.open(rig.root / rig.head.base_file).convert("RGBA")
    for expression_id in character_rig.REQUIRED_EXPRESSIONS:
        face = rig.faces[expression_id]
        composed = head.copy()
        composed.alpha_composite(Image.open(rig.root / face.file).convert("RGBA"))
        label = f"expression\n{expression_id}\nhead + {Path(face.file).name}"
        cells.append((label, _fit(_on_neutral_bg(composed))))
    return cells


def _rendered_rig_pose_cells(rig) -> list[tuple[str, Image.Image]]:
    cells = [("SECTION\nrendered rig poses", _blank_cell("#d8c7b8"))]
    hashes: dict[str, str] = {}
    for pose_id in rig.poses:
        emotion = _emotion_for_pose(pose_id)
        result = character_rig.render_rigged_character(rig.character_id, pose_id, emotion_tag=emotion)
        digest = _image_digest(result.image)
        duplicate = f"\ndupe {hashes[digest]}" if digest in hashes else ""
        hashes[digest] = pose_id
        marker = "PLACEHOLDER_PARTS" if result.is_placeholder_rig else "CURATED_PARTS"
        bbox = result.character_bbox or ("", "", "", "")
        label = (
            f"mode rig\n{pose_id}\nexpr {result.selected_expression}\n"
            f"{marker}{duplicate}\nbbox {_format_bbox(bbox)}"
        )
        cells.append((label, _fit_on_rig_canvas(result.image)))
    return cells


def _rig_pose_on_canvas_cells(manifest, rig) -> list[tuple[str, Image.Image]]:
    cells = [("SECTION\nrig pose on scene canvas", _blank_cell("#d8c7b8"))]
    job_state = sprite_composer.JobState(job_id="contact_rig_scene")
    for i, pose_id in enumerate(rig.poses, start=1):
        scene = Scene(scene_index=i, voice_script=pose_id, image_prompt="", stock_query="")
        scene.emotion_tag = _emotion_for_pose(pose_id)
        layout = sprite_composer.choose_layout(scene, pose_id, "paper_heart", SCENE_W, SCENE_H, job_state)
        img = sprite_composer.draw_background(SCENE_W, SCENE_H, i)
        result = character_rig.render_rigged_character(rig.character_id, pose_id, scene=scene)
        bbox = sprite_composer.paste_sprite(img, result.image, manifest, layout)
        label = (
            f"mode rig/auto\n{pose_id}\nexpr {result.selected_expression}\n"
            f"anchor {layout.anchor_x},{layout.anchor_y}\n{layout.template}\nbbox {_format_bbox(bbox)}"
        )
        cells.append((label, img.convert("RGB").resize((THUMB_W, THUMB_H), Image.LANCZOS)))
    return cells


def _rig_pose_motif_cells(manifest, rig, out_dir: Path) -> list[tuple[str, Image.Image]]:
    cells = [("SECTION\nrig pose + compatible motifs", _blank_cell("#d8c7b8"))]
    job_state = sprite_composer.JobState(job_id="contact_rig_motif")
    for i, pose_id in enumerate(rig.poses, start=1):
        compatible = manifest.poses.get(pose_id, {}).get("compatible_motifs", ["any"])
        if "any" in compatible:
            compatible = list(manifest.motifs)[:4]
        for motif_id in compatible:
            scene = Scene(
                scene_index=i,
                voice_script=f"{pose_id} with {motif_id}",
                image_prompt="contact sheet",
                stock_query="contact",
                pose_family=pose_id,
                primary_motif=motif_id,
            )
            layout = sprite_composer.choose_layout(scene, pose_id, motif_id, SCENE_W, SCENE_H, job_state)
            img = sprite_composer.draw_background(SCENE_W, SCENE_H, i)
            scene.emotion_tag = _emotion_for_pose(pose_id)
            result = character_rig.render_rigged_character(rig.character_id, pose_id, scene=scene)
            character = result.image
            motif = sprite_composer.draw_or_load_motif(manifest, motif_id)
            motif_bbox = None
            if layout.motif_layer == "behind":
                motif_bbox = sprite_composer.place_motif(img, motif, layout)
            sprite_composer.paste_sprite(img, character, manifest, layout)
            if layout.motif_layer == "front":
                motif_bbox = sprite_composer.place_motif(img, motif, layout)
            output = out_dir / f"contact_rig_{pose_id}_{motif_id}.jpg"
            img.convert("RGB").save(output, "JPEG", quality=90)
            label = (
                f"mode rig\n{pose_id}\nexpr {result.selected_expression}\n{motif_id}\n{layout.template}\n"
                f"motif {_format_bbox(motif_bbox)}"
            )
            cells.append((label, img.convert("RGB").resize((THUMB_W, THUMB_H), Image.LANCZOS)))
    return cells


def _full_pose_sprite_cells(manifest) -> list[tuple[str, Image.Image]]:
    cells = [("SECTION\nfull-pose sprite mode", _blank_cell("#d8c7b8"))]
    job_state = sprite_composer.JobState(job_id="contact_sprite_mode")
    for i, (pose_id, spec) in enumerate(manifest.poses.items(), start=1):
        path = manifest.root / spec["file"]
        if not path.is_file():
            cells.append((f"mode sprite\n{pose_id}\nMISSING\n{spec['file']}", _blank_cell("#e6b5a8")))
            continue
        scene = Scene(scene_index=i, voice_script=pose_id, image_prompt="", stock_query="")
        layout = sprite_composer.choose_layout(scene, pose_id, "paper_heart", SCENE_W, SCENE_H, job_state)
        img = sprite_composer.draw_background(SCENE_W, SCENE_H, i)
        sprite = Image.open(path).convert("RGBA")
        bbox = sprite_composer.paste_sprite(img, sprite, manifest, layout)
        marker = "PLACEHOLDER_SPRITE" if sprite_composer.is_placeholder_sprite(manifest, pose_id) else "CURATED_SPRITE"
        label = (
            f"mode sprite\n{pose_id}\n{marker}\n{Path(spec['file']).name}\n"
            f"bbox {_format_bbox(bbox)}"
        )
        cells.append((label, img.convert("RGB").resize((THUMB_W, THUMB_H), Image.LANCZOS)))
    return cells


def _build_sheet(cells: list[tuple[str, Image.Image]], warnings: list[str]) -> Image.Image:
    warning_h = 40 + 18 * min(len(warnings), 16)
    cell_h = THUMB_H + LABEL_H
    rows = (len(cells) + COLS - 1) // COLS
    sheet = Image.new("RGB", (COLS * THUMB_W, warning_h + rows * cell_h), "#eee6dc")
    draw = ImageDraw.Draw(sheet)
    mode = character_rig.character_mode()
    draw.text((10, 8), f"Minimalist character QA contact sheet - TELLA_MINIMALIST_CHARACTER_MODE={mode}", fill="#4e3a31")
    y = 28
    if warnings:
        for line in warnings[:16]:
            draw.text((10, y), line[:120], fill="#8a3b2e")
            y += 18
    else:
        draw.text((10, y), "No validation warnings.", fill="#4e3a31")

    for idx, (label, thumb) in enumerate(cells):
        x = (idx % COLS) * THUMB_W
        y = warning_h + (idx // COLS) * cell_h
        sheet.paste(thumb.convert("RGB"), (x, y))
        draw.text((x + 8, y + THUMB_H + 5), label[:220], fill="#4e3a31")
    return sheet


def _fit_on_rig_canvas(img: Image.Image) -> Image.Image:
    bg = Image.new("RGBA", img.size, "#eee6dc")
    bg.alpha_composite(img)
    return bg.convert("RGB").resize((THUMB_W, THUMB_H), Image.LANCZOS)


def _fit(img: Image.Image) -> Image.Image:
    img = img.convert("RGB")
    img.thumbnail((THUMB_W, THUMB_H), Image.LANCZOS)
    out = Image.new("RGB", (THUMB_W, THUMB_H), "#eee6dc")
    out.paste(img, ((THUMB_W - img.width) // 2, (THUMB_H - img.height) // 2))
    return out


def _on_neutral_bg(img: Image.Image) -> Image.Image:
    bg = Image.new("RGBA", img.size, "#eee6dc")
    bg.alpha_composite(img)
    return bg.convert("RGB")


def _emotion_for_pose(pose_id: str) -> str:
    mapping = {
        "side_sitting": "sadness",
        "hugging_knees": "loneliness",
        "side_walking": "healing",
        "looking_up": "hope",
        "looking_down": "tired",
        "reaching_forward": "trying_again",
        "holding_paper_heart": "self_kindness",
        "arms_open": "relief",
    }
    return mapping.get(pose_id, "neutral")


def _blank_cell(color: str) -> Image.Image:
    img = Image.new("RGB", (THUMB_W, THUMB_H), color)
    draw = ImageDraw.Draw(img)
    draw.rectangle((8, 8, THUMB_W - 8, THUMB_H - 8), outline="#4e3a31", width=2)
    return img


def _format_bbox(bbox) -> str:
    if not bbox:
        return ""
    return ",".join(str(int(v)) for v in bbox)


def _image_digest(img: Image.Image) -> str:
    buf = BytesIO()
    img.convert("RGBA").save(buf, "PNG")
    return hashlib.sha256(buf.getvalue()).hexdigest()[:16]


if __name__ == "__main__":
    raise SystemExit(main())
