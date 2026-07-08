"""Sprite-pack local composer for minimalist emotional scenes.

The production path is manifest + transparent PNG sprites. Placeholder PNGs
are generated only when missing so the pack can later be replaced by curated
art without changing runtime composition code.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from io import BytesIO
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from tella.media import character_rig

logger = logging.getLogger("tella.media.sprite_composer")

ASSET_ROOT = Path(__file__).resolve().parent / "character_assets"
DEFAULT_CHARACTER_ID = "girl_v1"
BG = "#b69a82"
BG_LINE = "#a88c75"
INK = "#4e3a31"
HAIR = "#201a17"
FACE = "#ddbda0"
BLUSH = "#c98272"
MUSTARD = "#d4a33a"
RUST = "#b86445"
GLOW = "#f1cf74"
GREY = "#756a62"
GREEN = "#87966f"
PLACEHOLDER_SUFFIX = ".placeholder"
_WARNED_PLACEHOLDERS: set[Path] = set()


@dataclass
class Manifest:
    character_id: str
    root: Path
    canvas_width: int
    canvas_height: int
    anchor_x: int
    anchor_y: int
    poses: dict[str, dict[str, Any]]
    motifs: dict[str, dict[str, Any]]
    backgrounds: dict[str, dict[str, Any]]


@dataclass
class CuratedPoseManifest:
    character_id: str
    root: Path
    manifest_path: Path
    canvas_width: int
    canvas_height: int
    anchor_x: int
    anchor_y: int
    poses: dict[str, dict[str, Any]]


@dataclass
class JobState:
    job_id: str = "default"
    last_pose: str = ""
    pose_counts: dict[str, int] = field(default_factory=dict)
    last_motif: str = ""
    motif_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class Layout:
    template: str
    anchor_x: int
    anchor_y: int
    sprite_scale: float
    motif_x: int
    motif_y: int
    motif_layer: str
    focal_x: int
    focal_y: int


@dataclass
class ComposeResult:
    output_path: Path
    asset_hash: str
    pose_id: str
    motif_id: str
    layout_template: str
    character_id: str
    sprite_path: Path
    character_mode: str
    character_source: str
    rig_parts_used: list[str]
    is_placeholder_sprite: bool
    is_placeholder_rig: bool
    selected_expression: str
    head_base_path: str
    face_path: str
    is_placeholder_head: bool
    is_placeholder_face: bool
    socket_alignment_fallback: bool
    compatible_motif_used: bool
    focal_anchor: tuple[int, int]
    character_bbox: tuple[int, int, int, int] | None
    motif_bbox: tuple[int, int, int, int] | None


@dataclass
class ValidationIssue:
    level: str
    asset_id: str
    message: str


@dataclass
class CharacterSource:
    image: Image.Image
    mode: str
    source: str
    pose_id: str
    sprite_path: Path | None
    rig_parts_used: list[str]
    is_placeholder_sprite: bool
    is_placeholder_rig: bool
    selected_expression: str = ""
    head_base_path: str = ""
    face_path: str = ""
    is_placeholder_head: bool = False
    is_placeholder_face: bool = False
    socket_alignment_fallback: bool = False
    curated_manifest_path: Path | None = None


def load_manifest(character_id: str = DEFAULT_CHARACTER_ID) -> Manifest:
    root = ASSET_ROOT / character_id
    path = root / "manifest.json"
    if not path.is_file():
        raise FileNotFoundError(f"sprite manifest not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    canvas = data["canvas"]
    anchor = data["anchor"]
    return Manifest(
        character_id=data["character_id"],
        root=root,
        canvas_width=int(canvas["width"]),
        canvas_height=int(canvas["height"]),
        anchor_x=int(anchor["x"]),
        anchor_y=int(anchor["y"]),
        poses=dict(data["poses"]),
        motifs=dict(data.get("motifs", {})),
        backgrounds=dict(data.get("backgrounds", {})),
    )


def load_curated_pose_manifest(character_id: str = DEFAULT_CHARACTER_ID) -> CuratedPoseManifest:
    root = ASSET_ROOT / character_id
    path = root / "curated_pose_manifest.json"
    if not path.is_file():
        raise FileNotFoundError(f"curated pose manifest not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    canvas = data["canvas"]
    anchor = data["anchor"]
    return CuratedPoseManifest(
        character_id=str(data["character_id"]),
        root=root,
        manifest_path=path,
        canvas_width=int(canvas["width"]),
        canvas_height=int(canvas["height"]),
        anchor_x=int(anchor["x"]),
        anchor_y=int(anchor["y"]),
        poses=dict(data.get("poses", {})),
    )


def allow_placeholder_sprites() -> bool:
    value = (os.environ.get("TELLA_ALLOW_PLACEHOLDER_SPRITES") or "").strip()
    if value == "0":
        return False
    # Permissive default while the sprite pack is being developed. Set
    # TELLA_ALLOW_PLACEHOLDER_SPRITES=0 in production to fail on missing art.
    return True


def ensure_sprite_pack(character_id: str = DEFAULT_CHARACTER_ID) -> Manifest:
    manifest = load_manifest(character_id)
    for rel in ("poses", "motifs", "backgrounds"):
        (manifest.root / rel).mkdir(parents=True, exist_ok=True)

    for pose_id, spec in manifest.poses.items():
        path = manifest.root / spec["file"]
        if not path.is_file():
            if not allow_placeholder_sprites():
                raise FileNotFoundError(_missing_sprite_message(manifest, [path]))
            create_placeholder_sprite(character_id, pose_id)
        if is_placeholder_sprite(manifest, pose_id):
            _warn_placeholder(path)

    for motif_id, spec in manifest.motifs.items():
        path = manifest.root / spec["file"]
        if not path.is_file():
            _create_placeholder_motif(path, motif_id)

    for bg_id, spec in manifest.backgrounds.items():
        path = manifest.root / spec["file"]
        if not path.is_file():
            _create_placeholder_background(path, bg_id)
    return manifest


def ensure_curated_pose_folder(character_id: str = DEFAULT_CHARACTER_ID) -> CuratedPoseManifest:
    curated = load_curated_pose_manifest(character_id)
    (curated.root / "curated_poses").mkdir(parents=True, exist_ok=True)
    return curated


def _ensure_non_pose_assets(manifest: Manifest) -> None:
    for rel in ("poses", "motifs", "backgrounds"):
        (manifest.root / rel).mkdir(parents=True, exist_ok=True)
    for motif_id, spec in manifest.motifs.items():
        path = manifest.root / spec["file"]
        if not path.is_file():
            _create_placeholder_motif(path, motif_id)
    for bg_id, spec in manifest.backgrounds.items():
        path = manifest.root / spec["file"]
        if not path.is_file():
            _create_placeholder_background(path, bg_id)


def load_sprite(character_id: str, pose_id: str) -> Image.Image:
    manifest = ensure_sprite_pack(character_id)
    if pose_id not in manifest.poses:
        raise KeyError(f"unknown pose {pose_id!r} for {character_id}")
    path = manifest.root / manifest.poses[pose_id]["file"]
    return Image.open(path).convert("RGBA")


def create_placeholder_sprite(character_id: str, pose_id: str) -> Path:
    manifest = load_manifest(character_id)
    if pose_id not in manifest.poses:
        raise KeyError(f"unknown pose {pose_id!r} for {character_id}")
    path = manifest.root / manifest.poses[pose_id]["file"]
    path.parent.mkdir(parents=True, exist_ok=True)

    img = _render_placeholder_sprite(manifest, pose_id)
    img.save(path, "PNG")
    placeholder_marker(path).write_text(
        "Generated placeholder sprite. Replace this PNG before production.\n",
        encoding="utf-8",
    )
    logger.info("created placeholder sprite %s", path)
    return path


def placeholder_marker(path: Path) -> Path:
    return path.with_suffix(path.suffix + PLACEHOLDER_SUFFIX)


def _warn_placeholder(path: Path) -> None:
    if path in _WARNED_PLACEHOLDERS:
        return
    _WARNED_PLACEHOLDERS.add(path)
    logger.warning(
        "Using generated placeholder sprite %s; replace it before production.",
        path,
    )


def is_placeholder_sprite(manifest: Manifest, pose_id: str) -> bool:
    if pose_id not in manifest.poses:
        return False
    path = manifest.root / manifest.poses[pose_id]["file"]
    if placeholder_marker(path).is_file():
        return True
    if not path.is_file():
        return False
    try:
        actual = _image_bytes_hash(Image.open(path).convert("RGBA"))
        expected = _image_bytes_hash(_render_placeholder_sprite(manifest, pose_id))
        return actual == expected
    except OSError:
        return False


def validate_sprite_pack(manifest: Manifest) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    hashes: dict[str, str] = {}
    missing: list[Path] = []
    if not (
        0 <= manifest.anchor_x <= manifest.canvas_width
        and 0 <= manifest.anchor_y <= manifest.canvas_height
    ):
        issues.append(ValidationIssue("error", "anchor", "manifest anchor is outside canvas"))

    for pose_id, spec in manifest.poses.items():
        path = manifest.root / spec["file"]
        if not path.is_file():
            missing.append(path)
            issues.append(ValidationIssue("error", pose_id, f"missing pose PNG: {path}"))
            continue
        try:
            with Image.open(path) as img:
                if img.mode not in {"RGBA", "LA"} and "transparency" not in img.info:
                    issues.append(
                        ValidationIssue("error", pose_id, "pose PNG has no alpha channel")
                    )
                if img.size != (manifest.canvas_width, manifest.canvas_height):
                    issues.append(
                        ValidationIssue(
                            "error",
                            pose_id,
                            "pose PNG size "
                            f"{img.size} != manifest canvas "
                            f"{(manifest.canvas_width, manifest.canvas_height)}",
                        )
                    )
                digest = _image_bytes_hash(img.convert("RGBA"))
                if digest in hashes:
                    issues.append(
                        ValidationIssue(
                            "warning",
                            pose_id,
                            f"pose image hash matches {hashes[digest]}",
                        )
                    )
                hashes[digest] = pose_id
        except OSError as exc:
            issues.append(ValidationIssue("error", pose_id, f"cannot read PNG: {exc}"))
        if is_placeholder_sprite(manifest, pose_id):
            issues.append(
                ValidationIssue(
                    "warning",
                    pose_id,
                    "placeholder sprite in use; replace before production",
                )
            )

    if missing and not allow_placeholder_sprites():
        raise FileNotFoundError(_missing_sprite_message(manifest, missing))
    return issues


def validate_curated_pose_pack(curated: CuratedPoseManifest) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    hashes: dict[str, str] = {}
    if not (
        0 <= curated.anchor_x <= curated.canvas_width
        and 0 <= curated.anchor_y <= curated.canvas_height
    ):
        issues.append(ValidationIssue("error", "curated_anchor", "curated anchor is outside canvas"))

    for pose_id, spec in curated.poses.items():
        path = curated.root / spec["file"]
        if not path.is_file():
            issues.append(ValidationIssue("warning", pose_id, f"missing curated pose PNG: {path}"))
            continue
        try:
            with Image.open(path) as img:
                if img.mode not in {"RGBA", "LA"} and "transparency" not in img.info:
                    issues.append(ValidationIssue("error", pose_id, "curated pose has no alpha channel"))
                if img.size != (curated.canvas_width, curated.canvas_height):
                    issues.append(
                        ValidationIssue(
                            "error",
                            pose_id,
                            f"curated pose size {img.size} != manifest canvas {(curated.canvas_width, curated.canvas_height)}",
                        )
                    )
                bbox = img.convert("RGBA").getbbox()
                if not bbox:
                    issues.append(ValidationIssue("error", pose_id, "curated pose has no visible pixels"))
                else:
                    h = bbox[3] - bbox[1]
                    if h < int(curated.canvas_height * 0.35):
                        issues.append(ValidationIssue("warning", pose_id, f"curated pose bbox is small: {bbox}"))
                    if h > int(curated.canvas_height * 0.9):
                        issues.append(ValidationIssue("warning", pose_id, f"curated pose bbox is large: {bbox}"))
                digest = _image_bytes_hash(img.convert("RGBA"))
                if digest in hashes:
                    issues.append(ValidationIssue("warning", pose_id, f"curated pose image hash matches {hashes[digest]}"))
                hashes[digest] = pose_id
        except OSError as exc:
            issues.append(ValidationIssue("error", pose_id, f"cannot read curated pose PNG: {exc}"))
        if is_placeholder_curated_pose(curated, pose_id):
            issues.append(ValidationIssue("warning", pose_id, "placeholder curated pose in use; do not use for production"))
    return issues


def _missing_sprite_message(manifest: Manifest, missing: list[Path]) -> str:
    rels = "\n".join(f"- {p}" for p in missing)
    return (
        "Missing required minimalist sprite PNGs:\n"
        f"{rels}\n\n"
        "Place transparent PNG sprites under "
        f"{manifest.root / 'poses'} with the filenames listed in manifest.json, "
        "or set TELLA_ALLOW_PLACEHOLDER_SPRITES=1 while developing."
    )


def _missing_curated_message(curated: CuratedPoseManifest, missing: list[Path]) -> str:
    rels = "\n".join(f"- {p}" for p in missing)
    return (
        "Missing required curated full-body pose PNGs:\n"
        f"{rels}\n\n"
        "Place transparent 600x900 PNGs under "
        f"{curated.root / 'curated_poses'}, or use "
        "TELLA_MINIMALIST_CHARACTER_MODE=auto with TELLA_ALLOW_PLACEHOLDER_SPRITES=1 "
        "to fall back to rig mode while developing."
    )


def is_placeholder_curated_pose(curated: CuratedPoseManifest, pose_id: str) -> bool:
    if pose_id not in curated.poses:
        return False
    path = curated.root / curated.poses[pose_id]["file"]
    return placeholder_marker(path).is_file()


def _available_curated_pose_ids(curated: CuratedPoseManifest, *, allow_placeholders: bool = False) -> list[str]:
    available: list[str] = []
    for pose_id, spec in curated.poses.items():
        path = curated.root / spec["file"]
        if not path.is_file():
            continue
        if not allow_placeholders and is_placeholder_curated_pose(curated, pose_id):
            continue
        available.append(pose_id)
    return available


def _ensure_curated_sprite_mode_ready(curated: CuratedPoseManifest) -> None:
    missing = [
        curated.root / spec["file"]
        for spec in curated.poses.values()
        if not (curated.root / spec["file"]).is_file()
    ]
    placeholders = [
        curated.root / spec["file"]
        for pose_id, spec in curated.poses.items()
        if is_placeholder_curated_pose(curated, pose_id)
    ]
    if not allow_placeholder_sprites() and (missing or placeholders):
        raise FileNotFoundError(_missing_curated_message(curated, [*missing, *placeholders]))
    if missing and allow_placeholder_sprites():
        for pose_id, spec in curated.poses.items():
            path = curated.root / spec["file"]
            if not path.is_file():
                create_placeholder_curated_pose(curated, pose_id)


def create_placeholder_curated_pose(curated: CuratedPoseManifest, pose_id: str) -> Path:
    if pose_id not in curated.poses:
        raise KeyError(f"unknown curated pose {pose_id!r} for {curated.character_id}")
    path = curated.root / curated.poses[pose_id]["file"]
    path.parent.mkdir(parents=True, exist_ok=True)
    img = _render_placeholder_curated_pose(curated, pose_id)
    img.save(path, "PNG")
    placeholder_marker(path).write_text(
        "Generated placeholder curated full-body pose. Replace before production.\n",
        encoding="utf-8",
    )
    logger.warning(
        "created development-only placeholder curated pose %s; do not use for production",
        path,
    )
    return path


def select_pose(scene, job_state: JobState, manifest: Manifest) -> str:
    emotion = _emotion_for_scene(scene)
    scene.emotion_tag = emotion
    candidates = [
        pose_id
        for pose_id, spec in manifest.poses.items()
        if emotion in spec.get("emotion_tags", [])
    ]
    if not candidates:
        candidates = [
            pose_id
            for pose_id, spec in manifest.poses.items()
            if any(tag in spec.get("emotion_tags", []) for tag in ("neutral", "calm", "acceptance"))
        ]
    if len(candidates) < 3:
        candidates = list(dict.fromkeys([*candidates, *manifest.poses.keys()]))
    if len(candidates) > 1 and job_state.last_pose in candidates:
        candidates = [p for p in candidates if p != job_state.last_pose]

    underused = [p for p in candidates if job_state.pose_counts.get(p, 0) < 2]
    if underused:
        candidates = underused

    idx = _stable_index(job_state.job_id, scene.scene_index, emotion, len(candidates))
    pose_id = candidates[idx]
    logger.info(
        "scene %d emotion=%s candidates=%s selected_pose=%s previous_pose=%s counts=%s",
        scene.scene_index,
        emotion,
        candidates,
        pose_id,
        job_state.last_pose,
        dict(job_state.pose_counts),
    )
    job_state.last_pose = pose_id
    job_state.pose_counts[pose_id] = job_state.pose_counts.get(pose_id, 0) + 1
    scene.pose_family = pose_id
    return pose_id


def select_curated_pose(
    scene,
    job_state: JobState,
    curated: CuratedPoseManifest,
    *,
    available_pose_ids: list[str] | None = None,
) -> str:
    emotion = _emotion_for_scene(scene)
    scene.emotion_tag = emotion
    allowed = set(available_pose_ids or curated.poses.keys())
    candidates = [
        pose_id
        for pose_id, spec in curated.poses.items()
        if pose_id in allowed and emotion in spec.get("emotion_tags", [])
    ]
    if not candidates:
        candidates = [
            pose_id
            for pose_id, spec in curated.poses.items()
            if pose_id in allowed
            and any(tag in spec.get("emotion_tags", []) for tag in ("neutral", "calm", "reflection"))
        ]
    if len(candidates) < 3:
        candidates = list(dict.fromkeys([*candidates, *[p for p in curated.poses if p in allowed]]))
    if not candidates:
        raise FileNotFoundError("No usable curated full-body pose sprites are available.")
    if len(candidates) > 1 and job_state.last_pose in candidates:
        candidates = [p for p in candidates if p != job_state.last_pose]

    underused = [p for p in candidates if job_state.pose_counts.get(p, 0) < 2]
    if underused:
        candidates = underused

    idx = _stable_index(job_state.job_id, scene.scene_index, emotion, len(candidates))
    pose_id = candidates[idx]
    logger.info(
        "scene %d emotion=%s curated_candidates=%s selected_curated_pose=%s previous_pose=%s counts=%s",
        scene.scene_index,
        emotion,
        candidates,
        pose_id,
        job_state.last_pose,
        dict(job_state.pose_counts),
    )
    job_state.last_pose = pose_id
    job_state.pose_counts[pose_id] = job_state.pose_counts.get(pose_id, 0) + 1
    scene.pose_family = pose_id
    return pose_id


def select_motif(
    scene,
    pose_id: str,
    job_state: JobState,
    manifest: Manifest,
    pose_specs: dict[str, dict[str, Any]] | None = None,
) -> str:
    wanted = (scene.primary_motif or "").strip()
    specs = pose_specs or manifest.poses
    compatible = specs.get(pose_id, {}).get("compatible_motifs", ["any"])
    if "any" in compatible:
        candidates = list(manifest.motifs)
    else:
        candidates = [m for m in compatible if m in manifest.motifs]
    if wanted in candidates and wanted != job_state.last_motif:
        motif_id = wanted
    else:
        if len(candidates) > 1 and job_state.last_motif in candidates:
            candidates = [m for m in candidates if m != job_state.last_motif]
        underused = [m for m in candidates if job_state.motif_counts.get(m, 0) < 2]
        if underused:
            candidates = underused
        idx = _stable_index(job_state.job_id, scene.scene_index, pose_id, len(candidates))
        motif_id = candidates[idx]

    logger.info(
        "scene %d pose=%s motif=%s previous_motif=%s motif_counts=%s",
        scene.scene_index,
        pose_id,
        motif_id,
        job_state.last_motif,
        dict(job_state.motif_counts),
    )
    job_state.last_motif = motif_id
    job_state.motif_counts[motif_id] = job_state.motif_counts.get(motif_id, 0) + 1
    scene.primary_motif = motif_id
    return motif_id


def choose_layout(
    scene,
    pose_id: str,
    motif_id: str,
    width: int,
    height: int,
    job_state: JobState,
) -> Layout:
    caption_top = int(height * 0.75)
    jitter_x = _stable_jitter(job_state.job_id, scene.scene_index, "x", 15)
    jitter_y = _stable_jitter(job_state.job_id, scene.scene_index, "y", 10)
    scale = 0.56

    templates = {
        "side_sitting": "side_sitting_with_motif_right",
        "sitting_sad": "side_sitting_with_motif_right",
        "sitting_by_lamp": "side_sitting_with_motif_right",
        "side_walking": "walking_on_path",
        "walking_away": "walking_on_path",
        "walking_forward_soft": "walking_on_path",
        "looking_up": "character_center_motif_above",
        "looking_up_hopeful": "character_center_motif_above",
        "looking_down": "character_center_motif_right",
        "looking_down_tired": "character_center_motif_right",
        "reaching_forward": "character_left_motif_right",
        "reaching_forward_hopeful": "character_center_motif_right",
        "holding_paper_heart": "character_center_motif_right",
        "arms_open": "character_center_motif_above",
        "arms_open_relief": "character_center_motif_above",
        "hugging_knees": "character_left_motif_right",
        "hugging_knees_sad": "character_left_motif_right",
        "standing_lonely": "character_left_motif_right",
    }
    template = templates.get(pose_id, "character_center_motif_right")

    anchor_x = int(width * 0.48) + jitter_x
    anchor_y = int(height * 0.64) + jitter_y
    if "left" in template:
        anchor_x = int(width * 0.40) + jitter_x
    elif "right" in template:
        anchor_x = int(width * 0.55) + jitter_x
    if pose_id in {"side_sitting", "hugging_knees"}:
        anchor_y = int(height * 0.62) + jitter_y
    anchor_y = min(anchor_y, caption_top - 155)

    placement = manifest_placement(motif_id)
    if placement == "sky":
        motif_x, motif_y, layer = int(width * 0.64) - jitter_x, int(height * 0.28), "behind"
    elif placement == "background":
        motif_x, motif_y, layer = int(width * 0.68), int(height * 0.31), "behind"
    elif placement == "head":
        motif_x, motif_y, layer = anchor_x, anchor_y - int(height * 0.36), "front"
    elif placement == "hands":
        motif_x = anchor_x + int(width * 0.02)
        motif_y = anchor_y - int(height * 0.25)
        layer = "front"
    else:
        side = -1 if anchor_x > width // 2 else 1
        motif_x = anchor_x + side * int(width * 0.23)
        motif_y = anchor_y - int(height * 0.08)
        layer = "behind"

    layout = Layout(
        template=template,
        anchor_x=anchor_x,
        anchor_y=anchor_y,
        sprite_scale=scale,
        motif_x=motif_x,
        motif_y=motif_y,
        motif_layer=layer,
        focal_x=anchor_x,
        focal_y=anchor_y - int(height * 0.21),
    )
    logger.info(
        "scene %d layout=%s motif=%s rule=%s pos=(%d,%d) layer=%s",
        scene.scene_index,
        template,
        motif_id,
        placement,
        motif_x,
        motif_y,
        layer,
    )
    return layout


def compose_scene(
    scene,
    output_path: Path,
    width: int,
    height: int,
    job_state: JobState,
) -> ComposeResult:
    manifest = load_manifest(DEFAULT_CHARACTER_ID)
    curated = ensure_curated_pose_folder(DEFAULT_CHARACTER_ID)
    _ensure_non_pose_assets(manifest)
    configured_mode = character_rig.character_mode()
    curated_available = _available_curated_pose_ids(curated)
    use_curated = configured_mode == "sprite" or (
        configured_mode == "auto" and bool(curated_available)
    )
    if use_curated:
        if configured_mode == "sprite":
            _ensure_curated_sprite_mode_ready(curated)
            available = _available_curated_pose_ids(
                curated,
                allow_placeholders=allow_placeholder_sprites(),
            )
        else:
            available = curated_available
        pose_id = select_curated_pose(scene, job_state, curated, available_pose_ids=available)
        pose_specs = curated.poses
    else:
        if configured_mode == "auto":
            logger.info(
                "no non-placeholder curated full-body poses available; auto mode uses rig fallback"
            )
        pose_id = select_pose(scene, job_state, manifest)
        pose_specs = manifest.poses
    motif_id = select_motif(scene, pose_id, job_state, manifest, pose_specs=pose_specs)
    layout = choose_layout(scene, pose_id, motif_id, width, height, job_state)

    img = draw_background(width, height, scene.scene_index)
    character = _load_character_image(manifest, pose_id, scene, curated=curated, use_curated=use_curated)
    motif = draw_or_load_motif(manifest, motif_id)

    motif_bbox = None
    if layout.motif_layer == "behind":
        motif_bbox = place_motif(img, motif, layout)
    character_bbox = paste_sprite(img, character.image, manifest, layout)
    if layout.motif_layer == "front":
        motif_bbox = place_motif(img, motif, layout)

    layout = validate_caption_safe(img, layout)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(output_path, "JPEG", quality=92)
    asset_hash = compute_asset_hash(output_path)

    scene.pose_family = pose_id
    scene.primary_motif = motif_id
    scene.layout_template = layout.template
    scene.character_id = manifest.character_id
    scene.character_mode = character.mode
    scene.character_source = character.source
    scene.sprite_path = (
        str(character.sprite_path.relative_to(manifest.root.parent))
        if character.sprite_path
        else ""
    )
    scene.rig_parts_used = character.rig_parts_used
    scene.is_placeholder_sprite = character.is_placeholder_sprite
    scene.is_placeholder_rig = character.is_placeholder_rig
    scene.selected_expression = character.selected_expression
    scene.head_base_path = character.head_base_path
    scene.face_path = character.face_path
    scene.is_placeholder_head = character.is_placeholder_head
    scene.is_placeholder_face = character.is_placeholder_face
    scene.socket_alignment_fallback = character.socket_alignment_fallback
    scene.compatible_motif_used = _is_compatible_motif(manifest, pose_id, motif_id, pose_specs=pose_specs)
    scene.focal_anchor = f"{layout.focal_x},{layout.focal_y}"
    scene.character_bbox = _format_bbox(character_bbox)
    scene.motif_bbox = _format_bbox(motif_bbox)
    scene.composition_hint = (
        f"{layout.template}, anchor=({layout.anchor_x},{layout.anchor_y}), "
        f"motif=({layout.motif_x},{layout.motif_y}), character_mode={character.mode}, "
        f"character_source={character.source}, "
        f"expression={character.selected_expression}"
    )
    scene.frame_safety_hint = (
        "caption-safe validated; character and motif stay above bottom caption zone"
    )

    return ComposeResult(
        output_path=output_path,
        asset_hash=asset_hash,
        pose_id=pose_id,
        motif_id=motif_id,
        layout_template=layout.template,
        character_id=manifest.character_id,
        sprite_path=character.sprite_path or Path(),
        character_mode=character.mode,
        character_source=character.source,
        rig_parts_used=character.rig_parts_used,
        is_placeholder_sprite=scene.is_placeholder_sprite,
        is_placeholder_rig=scene.is_placeholder_rig,
        selected_expression=scene.selected_expression,
        head_base_path=scene.head_base_path,
        face_path=scene.face_path,
        is_placeholder_head=scene.is_placeholder_head,
        is_placeholder_face=scene.is_placeholder_face,
        socket_alignment_fallback=scene.socket_alignment_fallback,
        compatible_motif_used=scene.compatible_motif_used,
        focal_anchor=(layout.focal_x, layout.focal_y),
        character_bbox=character_bbox,
        motif_bbox=motif_bbox,
    )


def draw_background(width: int, height: int, scene_index: int) -> Image.Image:
    img = Image.new("RGBA", (width, height), BG)
    draw = ImageDraw.Draw(img)
    y = int(height * (0.20 + (scene_index % 4) * 0.035))
    draw.arc((80, y, width - 90, y + 80), 185, 350, fill=BG_LINE, width=2)
    draw.arc((130, y + 150, width - 130, y + 220), 190, 345, fill="#aa907a", width=1)
    return img


def _load_character_image(
    manifest: Manifest,
    pose_id: str,
    scene=None,
    *,
    curated: CuratedPoseManifest | None = None,
    use_curated: bool = False,
) -> CharacterSource:
    configured_mode = character_rig.character_mode()
    if use_curated:
        if curated is None:
            curated = ensure_curated_pose_folder(manifest.character_id)
        spec = curated.poses[pose_id]
        sprite_path = curated.root / spec["file"]
        if not sprite_path.is_file():
            if configured_mode == "sprite" and allow_placeholder_sprites():
                create_placeholder_curated_pose(curated, pose_id)
            else:
                raise FileNotFoundError(_missing_curated_message(curated, [sprite_path]))
        placeholder = is_placeholder_curated_pose(curated, pose_id)
        if placeholder:
            _warn_placeholder(sprite_path)
        logger.info(
            "selected character mode=%s source=curated_full_body pose=%s emotion=%s "
            "expression=%s sprite=%s placeholder_sprite=%s",
            configured_mode,
            pose_id,
            getattr(scene, "emotion_tag", ""),
            spec.get("expression", ""),
            sprite_path,
            placeholder,
        )
        return CharacterSource(
            image=Image.open(sprite_path).convert("RGBA"),
            mode="sprite",
            source="curated_full_body",
            pose_id=pose_id,
            sprite_path=sprite_path,
            rig_parts_used=[],
            is_placeholder_sprite=placeholder,
            is_placeholder_rig=False,
            selected_expression=str(spec.get("expression", "")),
            curated_manifest_path=curated.manifest_path,
        )

    sprite_path = manifest.root / manifest.poses[pose_id]["file"]
    sprite_exists = sprite_path.is_file()
    sprite_placeholder = is_placeholder_sprite(manifest, pose_id) if sprite_exists else False

    use_sprite = False
    if use_sprite:
        if not sprite_path.is_file():
            if not allow_placeholder_sprites():
                raise FileNotFoundError(_missing_sprite_message(manifest, [sprite_path]))
            create_placeholder_sprite(manifest.character_id, pose_id)
        placeholder = is_placeholder_sprite(manifest, pose_id)
        if placeholder:
            _warn_placeholder(sprite_path)
        logger.info(
            "selected character mode=%s pose=%s sprite=%s placeholder_sprite=%s",
            configured_mode,
            pose_id,
            sprite_path,
            placeholder,
        )
        return CharacterSource(
            image=Image.open(sprite_path).convert("RGBA"),
            mode="sprite",
            source="legacy_sprite",
            pose_id=pose_id,
            sprite_path=sprite_path,
            rig_parts_used=[],
            is_placeholder_sprite=placeholder,
            is_placeholder_rig=False,
        )

    try:
        rigged = character_rig.render_rigged_character(
            manifest.character_id,
            pose_id,
            scene=scene,
        )
    except Exception as exc:
        logger.warning(
            "rig render failed for pose=%s (%s); falling back to front_standing",
            pose_id,
            str(exc)[:160],
        )
        rigged = character_rig.render_rigged_character(
            manifest.character_id,
            "front_standing",
            scene=scene,
        )
    for warning in rigged.warnings:
        logger.warning("rig pose %s: %s", pose_id, warning)
    logger.info(
        "selected character mode=%s effective=rig pose=%s emotion=%s expression=%s "
        "head=%s face=%s socket_fallback=%s placeholder_rig=%s parts=%s",
        configured_mode,
        pose_id,
        getattr(scene, "emotion_tag", ""),
        rigged.selected_expression,
        rigged.head_base_path,
        rigged.face_path,
        rigged.socket_alignment_fallback,
        rigged.is_placeholder_rig,
        rigged.parts_used,
    )
    return CharacterSource(
        image=rigged.image,
        mode="rig",
        source="rig",
        pose_id=pose_id,
        sprite_path=None,
        rig_parts_used=rigged.parts_used,
        is_placeholder_sprite=sprite_placeholder,
        is_placeholder_rig=rigged.is_placeholder_rig,
        selected_expression=rigged.selected_expression,
        head_base_path=rigged.head_base_path,
        face_path=rigged.face_path,
        is_placeholder_head=rigged.is_placeholder_head,
        is_placeholder_face=rigged.is_placeholder_face,
        socket_alignment_fallback=rigged.socket_alignment_fallback,
    )


def draw_or_load_motif(manifest: Manifest, motif_id: str) -> Image.Image:
    if motif_id not in manifest.motifs:
        motif_id = "paper_heart"
    path = manifest.root / manifest.motifs[motif_id]["file"]
    if not path.is_file():
        _create_placeholder_motif(path, motif_id)
    return Image.open(path).convert("RGBA")


def place_motif(img: Image.Image, motif: Image.Image, layout: Layout) -> tuple[int, int, int, int]:
    w = int(motif.width * 0.72)
    h = int(motif.height * 0.72)
    motif = motif.resize((w, h), Image.LANCZOS)
    x = layout.motif_x - w // 2
    y = layout.motif_y - h // 2
    img.alpha_composite(motif, (x, y))
    return (x, y, x + w, y + h)


def paste_sprite(
    img: Image.Image,
    sprite: Image.Image,
    manifest: Manifest,
    layout: Layout,
) -> tuple[int, int, int, int]:
    w = int(sprite.width * layout.sprite_scale)
    h = int(sprite.height * layout.sprite_scale)
    sprite = sprite.resize((w, h), Image.LANCZOS)
    anchor_x = int(manifest.anchor_x * layout.sprite_scale)
    anchor_y = int(manifest.anchor_y * layout.sprite_scale)
    x = layout.anchor_x - anchor_x
    y = layout.anchor_y - anchor_y
    img.alpha_composite(sprite, (x, y))
    bbox = sprite.getbbox()
    if not bbox:
        return (x, y, x + w, y + h)
    return (x + bbox[0], y + bbox[1], x + bbox[2], y + bbox[3])


def validate_caption_safe(img: Image.Image, layout: Layout) -> Layout:
    # Sprite anchors are already placed above the caption lane. Keep this as a
    # logged validation hook for future curated sprites with different bounds.
    caption_top = int(img.height * 0.75)
    if layout.anchor_y > caption_top - 120:
        old = layout.anchor_y
        layout.anchor_y = caption_top - 120
        layout.focal_y -= old - layout.anchor_y
        logger.warning("caption-safe correction: anchor_y %d -> %d", old, layout.anchor_y)
    return layout


def compute_asset_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def manifest_placement(motif_id: str) -> str:
    manifest = load_manifest(DEFAULT_CHARACTER_ID)
    return str(manifest.motifs.get(motif_id, {}).get("placement", "ground"))


def _emotion_for_scene(scene) -> str:
    text = " ".join([scene.title or "", scene.voice_script or ""]).lower()
    checks = [
        ("self_kindness", ("kind", "accept", "love", "thương", "dịu")),
        ("tired", ("tired", "mệt", "exhaust", "weary")),
        ("sadness", ("sad", "buồn", "hurt", "pain", "đau")),
        ("loneliness", ("alone", "lonely", "một mình", "empty")),
        ("reflection", ("think", "reflect", "nhìn lại", "quiet", "im lặng")),
        ("trying_again", ("again", "start", "bắt đầu", "try")),
        ("hope", ("hope", "light", "sáng", "hy vọng")),
        ("healing", ("heal", "chữa", "peace", "bình yên")),
        ("relief", ("relief", "relax", "thở", "nhẹ")),
    ]
    for tag, words in checks:
        if any(word in text for word in words):
            return tag
    return scene.emotion_tag or "calm"


def _stable_index(job_id: str, scene_index: int, salt: str, modulo: int) -> int:
    if modulo <= 1:
        return 0
    raw = f"{job_id}:{scene_index}:{salt}".encode("utf-8")
    return int(hashlib.sha256(raw).hexdigest()[:8], 16) % modulo


def _stable_jitter(job_id: str, scene_index: int, salt: str, amount: int) -> int:
    raw = f"{job_id}:{scene_index}:{salt}".encode("utf-8")
    value = int(hashlib.sha256(raw).hexdigest()[:8], 16)
    return value % (amount * 2 + 1) - amount


def _create_placeholder_background(path: Path, bg_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (768, 1344), BG)
    if bg_id == "taupe_horizon_lines":
        draw = ImageDraw.Draw(img)
        draw.arc((90, 400, 680, 480), 185, 350, fill=BG_LINE, width=2)
        draw.arc((130, 570, 620, 650), 190, 345, fill="#aa907a", width=1)
    img.save(path, "PNG")


def _render_placeholder_sprite(manifest: Manifest, pose_id: str) -> Image.Image:
    img = Image.new("RGBA", (manifest.canvas_width, manifest.canvas_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    _draw_placeholder_girl(draw, manifest, pose_id)
    return img


def _render_placeholder_curated_pose(curated: CuratedPoseManifest, pose_id: str) -> Image.Image:
    img = Image.new("RGBA", (curated.canvas_width, curated.canvas_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    adapter = Manifest(
        character_id=curated.character_id,
        root=curated.root,
        canvas_width=curated.canvas_width,
        canvas_height=curated.canvas_height,
        anchor_x=curated.anchor_x,
        anchor_y=curated.anchor_y,
        poses={},
        motifs={},
        backgrounds={},
    )
    legacy_pose = {
        "sitting_sad": "side_sitting",
        "sitting_by_lamp": "side_sitting",
        "hugging_knees_sad": "hugging_knees",
        "looking_down_tired": "looking_down",
        "looking_up_hopeful": "looking_up",
        "walking_away": "side_walking",
        "walking_forward_soft": "side_walking",
        "reaching_forward_hopeful": "reaching_forward",
        "arms_open_relief": "arms_open",
    }.get(pose_id, "front_standing")
    _draw_placeholder_girl(draw, adapter, legacy_pose)
    draw.rectangle((24, 24, curated.canvas_width - 24, 74), fill=(230, 181, 168, 180))
    draw.text((34, 40), f"PLACEHOLDER CURATED: {pose_id}", fill=INK)
    return img


def _image_bytes_hash(img: Image.Image) -> str:
    buf = BytesIO()
    img.save(buf, "PNG")
    return hashlib.sha256(buf.getvalue()).hexdigest()


def _is_compatible_motif(
    manifest: Manifest,
    pose_id: str,
    motif_id: str,
    pose_specs: dict[str, dict[str, Any]] | None = None,
) -> bool:
    specs = pose_specs or manifest.poses
    compatible = specs.get(pose_id, {}).get("compatible_motifs", [])
    return "any" in compatible or motif_id in compatible


def _format_bbox(bbox: tuple[int, int, int, int] | None) -> str:
    if not bbox:
        return ""
    return ",".join(str(int(v)) for v in bbox)


def _create_placeholder_motif(path: Path, motif_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (220, 220), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = 110, 112
    if motif_id == "lamp":
        draw.line((110, 80, 110, 170), fill=INK, width=4)
        draw.polygon([(70, 78), (150, 78), (136, 122), (84, 122)], fill=GLOW, outline=INK)
    elif motif_id == "paper_heart":
        _heart(draw, cx, cy, 42, RUST)
    elif motif_id == "scribble_cloud":
        for i in range(4):
            draw.arc((40 + i * 28, 70, 112 + i * 26, 128), 20, 330, fill=GREY, width=4)
    elif motif_id in {"small_flower", "seedling"}:
        _flower(draw, cx, cy + 38)
    elif motif_id in {"glowing_light", "sunrise_circle"}:
        draw.ellipse((60, 62, 160, 162), fill=GLOW, outline=INK, width=3)
    elif motif_id == "empty_chair":
        draw.rectangle((72, 60, 150, 125), outline=INK, width=4)
        draw.line((72, 125, 52, 178), fill=INK, width=4)
        draw.line((150, 125, 172, 178), fill=INK, width=4)
    elif motif_id == "thin_path":
        draw.arc((25, 95, 195, 190), 180, 350, fill=INK, width=4)
    elif motif_id == "tiny_bird":
        draw.arc((65, 90, 110, 125), 210, 340, fill=INK, width=4)
        draw.arc((110, 90, 155, 125), 200, 330, fill=INK, width=4)
    elif motif_id == "small_window":
        draw.rectangle((55, 55, 165, 155), outline=INK, width=4)
        draw.line((110, 55, 110, 155), fill=INK, width=3)
        draw.line((55, 105, 165, 105), fill=INK, width=3)
    elif motif_id == "little_star":
        _star(draw, cx, cy, 44)
    else:
        _heart(draw, cx, cy, 38, RUST)
    img.save(path, "PNG")


def _draw_placeholder_girl(draw: ImageDraw.ImageDraw, manifest: Manifest, pose_id: str) -> None:
    ax, ay = manifest.anchor_x, manifest.anchor_y
    head_c = (ax, ay - 560)
    head_r = 72
    seated = pose_id in {"side_sitting", "hugging_knees"}
    side = pose_id in {"side_sitting", "side_walking", "looking_down"}
    if seated:
        head_c = (ax - 15, ay - 475)

    # Soft bob hair, then face on top.
    draw.rounded_rectangle(
        (head_c[0] - 88, head_c[1] - 92, head_c[0] + 88, head_c[1] + 98),
        radius=74,
        fill=HAIR,
    )
    draw.ellipse(
        (head_c[0] - head_r, head_c[1] - head_r, head_c[0] + head_r, head_c[1] + head_r),
        fill=FACE,
        outline=INK,
        width=5,
    )
    draw.line((head_c[0] - 82, head_c[1] + 64, head_c[0] + 82, head_c[1] + 64), fill=HAIR, width=10)
    if side:
        draw.ellipse((head_c[0] + 18, head_c[1] - 8, head_c[0] + 28, head_c[1] + 2), fill=INK)
        draw.line(
            (head_c[0] + 10, head_c[1] + 34, head_c[0] + 42, head_c[1] + 30),
            fill=INK,
            width=4,
        )
        draw.ellipse((head_c[0] - 8, head_c[1] + 22, head_c[0] + 18, head_c[1] + 42), fill=BLUSH)
    else:
        draw.ellipse((head_c[0] - 32, head_c[1] - 8, head_c[0] - 22, head_c[1] + 2), fill=INK)
        draw.ellipse((head_c[0] + 22, head_c[1] - 8, head_c[0] + 32, head_c[1] + 2), fill=INK)
        draw.arc(
            (head_c[0] - 24, head_c[1] + 28, head_c[0] + 24, head_c[1] + 50),
            10,
            170,
            fill=INK,
            width=4,
        )
        draw.ellipse((head_c[0] - 58, head_c[1] + 18, head_c[0] - 32, head_c[1] + 40), fill=BLUSH)
        draw.ellipse((head_c[0] + 32, head_c[1] + 18, head_c[0] + 58, head_c[1] + 40), fill=BLUSH)

    body_top = head_c[1] + 92
    body_bottom = ay - (170 if seated else 135)
    draw.polygon(
        [(ax, body_top), (ax - 105, body_bottom), (ax + 105, body_bottom)],
        fill=MUSTARD,
        outline=INK,
    )

    _draw_pose_limbs(draw, ax, ay, body_top, body_bottom, pose_id)


def _draw_pose_limbs(
    draw: ImageDraw.ImageDraw,
    ax: int,
    ay: int,
    top: int,
    bottom: int,
    pose_id: str,
) -> None:
    sleeve_y = top + 54
    if pose_id == "holding_paper_heart":
        draw.line((ax - 64, sleeve_y, ax - 22, sleeve_y + 92), fill=RUST, width=12)
        draw.line((ax + 64, sleeve_y, ax + 22, sleeve_y + 92), fill=RUST, width=12)
        _heart(draw, ax, sleeve_y + 100, 36, RUST)
    elif pose_id == "reaching_forward":
        draw.line((ax - 60, sleeve_y, ax - 126, sleeve_y + 68), fill=RUST, width=12)
        draw.line((ax + 60, sleeve_y, ax + 140, sleeve_y + 35), fill=RUST, width=12)
    elif pose_id == "arms_open":
        draw.line((ax - 60, sleeve_y, ax - 150, sleeve_y - 30), fill=RUST, width=12)
        draw.line((ax + 60, sleeve_y, ax + 150, sleeve_y - 30), fill=RUST, width=12)
    elif pose_id == "hugging_knees":
        draw.line((ax - 50, sleeve_y, ax - 92, bottom + 58), fill=RUST, width=12)
        draw.line((ax + 50, sleeve_y, ax + 75, bottom + 54), fill=RUST, width=12)
    else:
        draw.line((ax - 64, sleeve_y, ax - 120, sleeve_y + 130), fill=RUST, width=12)
        draw.line((ax + 64, sleeve_y, ax + 120, sleeve_y + 130), fill=RUST, width=12)

    if pose_id == "side_sitting":
        draw.line((ax - 70, bottom, ax + 25, ay - 48), fill=INK, width=9)
        draw.line((ax + 20, bottom, ax + 125, ay - 48), fill=INK, width=9)
    elif pose_id == "hugging_knees":
        draw.arc((ax - 115, bottom - 10, ax + 15, ay - 35), 190, 345, fill=INK, width=9)
        draw.arc((ax - 5, bottom - 10, ax + 125, ay - 35), 190, 345, fill=INK, width=9)
    elif pose_id == "side_walking":
        draw.line((ax - 35, bottom, ax - 120, ay - 20), fill=INK, width=9)
        draw.line((ax + 35, bottom, ax + 120, ay - 60), fill=INK, width=9)
    else:
        draw.line((ax - 45, bottom, ax - 65, ay - 24), fill=INK, width=9)
        draw.line((ax + 45, bottom, ax + 65, ay - 24), fill=INK, width=9)


def _heart(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, fill: str) -> None:
    points = [
        (cx, cy + size),
        (cx - size, cy),
        (cx - size // 2, cy - size),
        (cx, cy - size // 3),
        (cx + size // 2, cy - size),
        (cx + size, cy),
    ]
    draw.polygon(points, fill=fill, outline=INK)


def _flower(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
    draw.line((x, y + 48, x, y - 12), fill=INK, width=4)
    for angle in range(0, 360, 72):
        dx = int(math.cos(math.radians(angle)) * 19)
        dy = int(math.sin(math.radians(angle)) * 15)
        draw.ellipse((x + dx - 12, y + dy - 12, x + dx + 12, y + dy + 12), fill=RUST, outline=INK)
    draw.ellipse((x - 9, y - 9, x + 9, y + 9), fill=GLOW, outline=INK)


def _star(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int) -> None:
    points = []
    for i in range(10):
        r = size if i % 2 == 0 else size // 2
        angle = -math.pi / 2 + i * math.pi / 5
        points.append((cx + int(math.cos(angle) * r), cy + int(math.sin(angle) * r)))
    draw.polygon(points, fill=GLOW, outline=INK)


__all__ = [
    "ComposeResult",
    "CharacterSource",
    "JobState",
    "Layout",
    "Manifest",
    "compose_scene",
    "compute_asset_hash",
    "create_placeholder_sprite",
    "ensure_sprite_pack",
    "is_placeholder_sprite",
    "load_manifest",
    "load_sprite",
    "select_motif",
    "select_pose",
    "validate_sprite_pack",
]
