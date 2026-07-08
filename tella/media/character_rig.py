"""Deterministic local character rig for minimalist emotional scenes."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

logger = logging.getLogger("tella.media.character_rig")

ASSET_ROOT = Path(__file__).resolve().parent / "character_assets"
DEFAULT_CHARACTER_ID = "girl_v1"
INK = "#4e3a31"
HAIR = "#201a17"
FACE = "#ddbda0"
BLUSH = "#c98272"
MUSTARD = "#d4a33a"
RUST = "#b86445"
PLACEHOLDER_SUFFIX = ".placeholder"
REQUIRED_PARTS = (
    "torso",
    "left_arm",
    "right_arm",
    "left_leg",
    "right_leg",
)
REQUIRED_EXPRESSIONS = (
    "neutral",
    "sad",
    "tired",
    "gentle_smile",
    "hopeful",
)
REQUIRED_POSES = (
    "front_standing",
    "side_sitting",
    "side_walking",
    "hugging_knees",
    "looking_up",
    "looking_down",
    "reaching_forward",
    "holding_paper_heart",
    "arms_open",
)

_WARNED_PARTS: set[Path] = set()
_WARNED_HEAD_FACE_PLACEHOLDER = False


def allow_placeholder_sprites() -> bool:
    value = (os.environ.get("TELLA_ALLOW_PLACEHOLDER_SPRITES") or "").strip()
    if value == "0":
        return False
    return True


@dataclass
class RigPart:
    part_id: str
    file: str
    pivot: tuple[int, int]
    default_position: tuple[int, int]
    neck_attach: tuple[int, int] | None
    z: int


@dataclass
class RigHead:
    base_file: str
    fallback_file: str
    pivot: tuple[int, int]
    neck_attach: tuple[int, int]
    z: int


@dataclass
class RigFace:
    expression_id: str
    file: str


@dataclass
class RigPose:
    pose_id: str
    emotion_tags: list[str]
    compatible_motifs: list[str]
    angles: dict[str, float]
    offsets: dict[str, tuple[int, int]]
    scale: float


@dataclass
class RigDefinition:
    character_id: str
    root: Path
    canvas_width: int
    canvas_height: int
    base_anchor: tuple[int, int]
    head: RigHead
    faces: dict[str, RigFace]
    emotion_to_expression: dict[str, str]
    parts: dict[str, RigPart]
    poses: dict[str, RigPose]
    safe_angle_limits: dict[str, tuple[float, float]]


@dataclass
class RigValidationIssue:
    level: str
    asset_id: str
    message: str


@dataclass
class RigRenderResult:
    image: Image.Image
    character_bbox: tuple[int, int, int, int] | None
    parts_used: list[str]
    is_placeholder_rig: bool
    selected_expression: str
    head_base_path: str
    face_path: str
    is_placeholder_head: bool
    is_placeholder_face: bool
    socket_alignment_fallback: bool
    socket_metadata: dict[str, Any]
    pose_id: str
    warnings: list[str]


def character_mode() -> str:
    mode = (os.environ.get("TELLA_MINIMALIST_CHARACTER_MODE") or "auto").strip().lower()
    if mode not in {"auto", "rig", "sprite"}:
        logger.warning(
            "invalid TELLA_MINIMALIST_CHARACTER_MODE=%r; using auto", mode
        )
        return "auto"
    return mode


def load_rig(character_id: str = DEFAULT_CHARACTER_ID) -> RigDefinition:
    root = ASSET_ROOT / character_id
    path = root / "rig.json"
    if not path.is_file():
        raise FileNotFoundError(f"rig definition not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    canvas = data["canvas"]
    anchor = data["base_anchor"]
    head_spec = data.get("head") or {}
    legacy_head = data.get("parts", {}).get("head", {})
    head = RigHead(
        base_file=str(head_spec.get("base_file") or legacy_head.get("file") or "parts/head_face_hair.png"),
        fallback_file=str(head_spec.get("fallback_file") or legacy_head.get("file") or "parts/head_face_hair.png"),
        pivot=(int((head_spec.get("pivot") or legacy_head.get("pivot") or {"x": 128})["x"]), int((head_spec.get("pivot") or legacy_head.get("pivot") or {"y": 128})["y"])),
        neck_attach=(
            int((head_spec.get("neck_attach") or {"x": 128})["x"]),
            int((head_spec.get("neck_attach") or {"y": 224})["y"]),
        ),
        z=int(head_spec.get("z", legacy_head.get("z", 60))),
    )
    faces = {
        expression_id: RigFace(expression_id=expression_id, file=str(spec["file"]))
        for expression_id, spec in data.get("faces", {}).items()
    }
    parts = {
        part_id: RigPart(
            part_id=part_id,
            file=str(spec["file"]),
            pivot=(int(spec["pivot"]["x"]), int(spec["pivot"]["y"])),
            default_position=(
                int(spec["default_position"]["x"]),
                int(spec["default_position"]["y"]),
            ),
            neck_attach=(
                (int(spec["neck_attach"]["x"]), int(spec["neck_attach"]["y"]))
                if "neck_attach" in spec
                else None
            ),
            z=int(spec.get("z", 0)),
        )
        for part_id, spec in data["parts"].items()
        if part_id != "head"
    }
    poses = {
        pose_id: RigPose(
            pose_id=pose_id,
            emotion_tags=list(spec.get("emotion_tags", [])),
            compatible_motifs=list(spec.get("compatible_motifs", [])),
            angles={k: float(v) for k, v in spec.get("angles", {}).items()},
            offsets={
                k: (int(v.get("x", 0)), int(v.get("y", 0)))
                for k, v in spec.get("offsets", {}).items()
            },
            scale=float(spec.get("scale", 1.0)),
        )
        for pose_id, spec in data["poses"].items()
    }
    limits = {
        part_id: (float(limit[0]), float(limit[1]))
        for part_id, limit in data.get("safe_angle_limits", {}).items()
    }
    return RigDefinition(
        character_id=str(data["character_id"]),
        root=root,
        canvas_width=int(canvas["width"]),
        canvas_height=int(canvas["height"]),
        base_anchor=(int(anchor["x"]), int(anchor["y"])),
        head=head,
        faces=faces,
        emotion_to_expression=dict(data.get("emotion_to_expression", {})),
        parts=parts,
        poses=poses,
        safe_angle_limits=limits,
    )


def ensure_rig_parts(character_id: str = DEFAULT_CHARACTER_ID) -> RigDefinition:
    global _WARNED_HEAD_FACE_PLACEHOLDER
    rig = load_rig(character_id)
    missing: list[Path] = []
    missing_config: list[str] = []
    missing_faces: list[Path] = []
    for part_id in REQUIRED_PARTS:
        part = rig.parts.get(part_id)
        if not part:
            missing_config.append(part_id)
            continue
        path = rig.root / part.file
        if not path.is_file():
            missing.append(path)
    head_path = rig.root / rig.head.base_file
    if not head_path.is_file() and not (rig.root / rig.head.fallback_file).is_file():
        missing.append(head_path)
    for expression_id in REQUIRED_EXPRESSIONS:
        face = rig.faces.get(expression_id)
        if not face:
            missing_config.append(f"faces.{expression_id}")
            continue
        path = rig.root / face.file
        if not path.is_file():
            missing_faces.append(path)
    if missing_config:
        raise FileNotFoundError(
            "Missing required minimalist rig part configs in rig.json: "
            + ", ".join(missing_config)
        )
    if (missing or missing_faces) and not allow_placeholder_sprites():
        raise FileNotFoundError(_missing_parts_message(rig, [*missing, *missing_faces]))
    if missing or missing_faces:
        create_placeholder_rig_parts(character_id)
    for part_id in REQUIRED_PARTS:
        if part_id not in rig.parts:
            continue
        path = rig.root / rig.parts[part_id].file
        if is_placeholder_part(rig, part_id):
            _warn_placeholder_part(path)
    if (
        not _WARNED_HEAD_FACE_PLACEHOLDER
        and (is_placeholder_head(rig) or any(is_placeholder_face(rig, e) for e in REQUIRED_EXPRESSIONS))
    ):
        _WARNED_HEAD_FACE_PLACEHOLDER = True
        logger.warning(
            "Using generated placeholder head/face/rig parts; replace them before production."
        )
    return rig


def create_placeholder_rig_parts(character_id: str = DEFAULT_CHARACTER_ID) -> None:
    rig = load_rig(character_id)
    parts_dir = rig.root / "parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    faces_dir = parts_dir / "faces"
    faces_dir.mkdir(parents=True, exist_ok=True)
    head_path = rig.root / rig.head.base_file
    if not head_path.is_file():
        img = _render_placeholder_head_base()
        img.save(head_path, "PNG")
        placeholder_marker(head_path).write_text(
            "Generated placeholder head base. Replace before production.\n",
            encoding="utf-8",
        )
        logger.info("created placeholder head base %s", head_path)
    for expression_id in REQUIRED_EXPRESSIONS:
        face = rig.faces.get(expression_id)
        if not face:
            continue
        path = rig.root / face.file
        if not path.is_file():
            img = _render_placeholder_face(expression_id)
            img.save(path, "PNG")
            placeholder_marker(path).write_text(
                "Generated placeholder face expression. Replace before production.\n",
                encoding="utf-8",
            )
            logger.info("created placeholder face %s", path)
    for part_id in REQUIRED_PARTS:
        part = rig.parts[part_id]
        path = rig.root / part.file
        if not path.is_file():
            img = _render_placeholder_part(part_id)
            img.save(path, "PNG")
            placeholder_marker(path).write_text(
                "Generated placeholder rig part. Replace before production.\n",
                encoding="utf-8",
            )
            logger.info("created placeholder rig part %s", path)
        _warn_placeholder_part(path)


def validate_rig(rig: RigDefinition) -> list[RigValidationIssue]:
    issues: list[RigValidationIssue] = []
    if not (0 <= rig.base_anchor[0] <= rig.canvas_width and 0 <= rig.base_anchor[1] <= rig.canvas_height):
        issues.append(RigValidationIssue("error", "base_anchor", "rig anchor is outside canvas"))
    head_path = _resolved_head_base_path(rig)
    head_size: tuple[int, int] | None = None
    if not head_path.is_file():
        issues.append(RigValidationIssue("error", "head_base", f"head base PNG missing: {head_path}"))
    else:
        with Image.open(head_path) as img:
            head_size = img.size
            if img.mode not in {"RGBA", "LA"} and "transparency" not in img.info:
                issues.append(RigValidationIssue("error", "head_base", "head base has no alpha channel"))
            if not (0 <= rig.head.pivot[0] <= img.width and 0 <= rig.head.pivot[1] <= img.height):
                issues.append(RigValidationIssue("error", "head_base", "head pivot is outside head canvas"))
            if not (0 <= rig.head.neck_attach[0] <= img.width and 0 <= rig.head.neck_attach[1] <= img.height):
                issues.append(RigValidationIssue("error", "head_base", "head neck_attach is outside head canvas"))
    if is_placeholder_head(rig):
        issues.append(RigValidationIssue("warning", "head_base", "placeholder head used; replace before production"))
    for expression_id in REQUIRED_EXPRESSIONS:
        face = rig.faces.get(expression_id)
        if not face:
            issues.append(RigValidationIssue("error", expression_id, "face config missing"))
            continue
        path = rig.root / face.file
        if not path.is_file():
            issues.append(RigValidationIssue("error", expression_id, f"face layer missing: {path}"))
            continue
        with Image.open(path) as img:
            if img.mode not in {"RGBA", "LA"} and "transparency" not in img.info:
                issues.append(RigValidationIssue("error", expression_id, "face layer has no alpha channel"))
            if head_size and img.size != head_size:
                issues.append(RigValidationIssue("error", expression_id, f"face layer size {img.size} != head base {head_size}"))
        if is_placeholder_face(rig, expression_id):
            issues.append(RigValidationIssue("warning", expression_id, "placeholder face used; replace before production"))
    for part_id in REQUIRED_PARTS:
        part = rig.parts.get(part_id)
        if not part:
            issues.append(RigValidationIssue("error", part_id, "rig part config missing"))
            continue
        path = rig.root / part.file
        if not path.is_file():
            issues.append(RigValidationIssue("error", part_id, f"rig part PNG missing: {path}"))
            continue
        try:
            with Image.open(path) as img:
                if img.mode not in {"RGBA", "LA"} and "transparency" not in img.info:
                    issues.append(RigValidationIssue("error", part_id, "rig part has no alpha channel"))
                if not (0 <= part.pivot[0] <= img.width and 0 <= part.pivot[1] <= img.height):
                    issues.append(RigValidationIssue("error", part_id, "pivot is outside part canvas"))
                if part_id == "torso" and not part.neck_attach:
                    issues.append(RigValidationIssue("error", part_id, "torso neck_attach missing"))
                if part.neck_attach and not (0 <= part.neck_attach[0] <= img.width and 0 <= part.neck_attach[1] <= img.height):
                    issues.append(RigValidationIssue("error", part_id, "neck_attach is outside part canvas"))
        except OSError as exc:
            issues.append(RigValidationIssue("error", part_id, f"cannot read rig part: {exc}"))
        if is_placeholder_part(rig, part_id):
            issues.append(RigValidationIssue("warning", part_id, "placeholder rig part in use; replace before production"))
    for pose_id in REQUIRED_POSES:
        if pose_id not in rig.poses:
            issues.append(RigValidationIssue("error", pose_id, "pose config missing"))
    hashes: dict[str, str] = {}
    for pose_id in rig.poses:
        try:
            result = render_rigged_character(rig.character_id, pose_id, emotion_tag="neutral")
        except Exception as exc:  # pragma: no cover - reported to QA script
            issues.append(RigValidationIssue("error", pose_id, f"pose render failed: {exc}"))
            continue
        for warning in result.warnings:
            issues.append(RigValidationIssue("warning", pose_id, warning))
        digest = _image_hash(result.image)
        if digest in hashes:
            issues.append(RigValidationIssue("warning", pose_id, f"rendered pose hash matches {hashes[digest]}"))
        hashes[digest] = pose_id
    return issues


def render_rigged_character(
    character_id: str = DEFAULT_CHARACTER_ID,
    pose_id: str = "front_standing",
    output_size: tuple[int, int] | None = None,
    *,
    scene: Any | None = None,
    emotion_tag: str = "",
) -> RigRenderResult:
    rig = ensure_rig_parts(character_id)
    original_pose_id = pose_id
    warnings: list[str] = []
    if pose_id not in rig.poses:
        warnings.append(f"pose config missing for {pose_id}; using front_standing")
        pose_id = "front_standing"
    pose = rig.poses[pose_id]
    expression = select_expression(scene, pose_id, rig, emotion_tag=emotion_tag, warnings=warnings)
    img, socket_metadata, socket_fallback, head_path, face_path = compose_parts(rig, pose, expression, warnings)
    bbox = img.getbbox()
    warnings.extend(validate_rig_pose_bounds(rig, pose, bbox))
    if output_size and output_size != img.size:
        img = img.resize(output_size, Image.LANCZOS)
        bbox = img.getbbox()
    return RigRenderResult(
        image=img,
        character_bbox=bbox,
        parts_used=[rig.parts[p].file for p in REQUIRED_PARTS if p in rig.parts],
        is_placeholder_rig=any(is_placeholder_part(rig, p) for p in REQUIRED_PARTS if p in rig.parts),
        selected_expression=expression,
        head_base_path=str(head_path.relative_to(rig.root.parent)) if head_path else "",
        face_path=str(face_path.relative_to(rig.root.parent)) if face_path else "",
        is_placeholder_head=is_placeholder_head(rig),
        is_placeholder_face=is_placeholder_face(rig, expression),
        socket_alignment_fallback=socket_fallback,
        socket_metadata=socket_metadata,
        pose_id=pose_id if original_pose_id == pose_id else "front_standing",
        warnings=warnings,
    )


def select_expression(
    scene: Any | None,
    pose_id: str,
    rig: RigDefinition,
    *,
    emotion_tag: str = "",
    warnings: list[str] | None = None,
) -> str:
    warnings = warnings if warnings is not None else []
    emotion = (emotion_tag or getattr(scene, "emotion_tag", "") or "").strip().lower()
    expression = rig.emotion_to_expression.get(emotion, "neutral")
    if expression not in rig.faces:
        warnings.append(
            f"expression {expression!r} for emotion {emotion!r} missing; using neutral"
        )
        expression = "neutral"
    logger.info(
        "selected expression pose=%s emotion_tag=%s selected_expression=%s",
        pose_id,
        emotion or "neutral",
        expression,
    )
    return expression


def compose_parts(
    rig: RigDefinition,
    pose: RigPose,
    expression: str = "neutral",
    warnings: list[str] | None = None,
) -> tuple[Image.Image, dict[str, Any], bool, Path, Path]:
    warnings = warnings if warnings is not None else []
    canvas = Image.new("RGBA", (rig.canvas_width, rig.canvas_height), (0, 0, 0, 0))
    render_items: list[tuple[int, Image.Image, tuple[int, int], str]] = []
    torso_socket_world: tuple[int, int] | None = None
    socket_metadata: dict[str, Any] = {}
    socket_fallback = False

    for part in rig.parts.values():
        angle = _safe_angle(rig, part.part_id, pose.angles.get(part.part_id, 0), warnings)
        offset = pose.offsets.get(part.part_id, (0, 0))
        position = _pose_position(rig, pose, part.default_position, offset)
        source = Image.open(rig.root / part.file).convert("RGBA")
        source, pivot, neck_attach = _scale_part(source, pose.scale, part.pivot, part.neck_attach)
        points = {"neck": neck_attach} if neck_attach else {}
        rotated, rotated_pivot, rotated_points = rotate_part_around_pivot(source, pivot, angle, points)
        x = position[0] - rotated_pivot[0]
        y = position[1] - rotated_pivot[1]
        if part.part_id == "torso" and "neck" in rotated_points:
            torso_socket_world = (x + rotated_points["neck"][0], y + rotated_points["neck"][1])
            socket_metadata["torso_neck_world"] = torso_socket_world
        render_items.append((part.z, rotated, (x, y), part.part_id))

    head_path = _resolved_head_base_path(rig)
    face_path = rig.root / rig.faces[expression].file
    head_img = Image.open(head_path).convert("RGBA")
    face_img = Image.open(face_path).convert("RGBA")
    if face_img.size != head_img.size:
        warnings.append(
            f"face layer {expression} size {face_img.size} != head base {head_img.size}; resizing"
        )
        face_img = face_img.resize(head_img.size, Image.LANCZOS)
    head_img.alpha_composite(face_img)
    head_img, head_pivot, head_attach = _scale_part(
        head_img,
        pose.scale,
        rig.head.pivot,
        rig.head.neck_attach,
    )
    head_angle = _safe_angle(rig, "head", pose.angles.get("head", 0), warnings)
    rotated_head, rotated_head_pivot, rotated_head_points = rotate_part_around_pivot(
        head_img,
        head_pivot,
        head_angle,
        {"neck": head_attach},
    )
    head_offset = pose.offsets.get("head", (0, 0))
    fallback_position = _pose_position(
        rig,
        pose,
        (rig.base_anchor[0], rig.parts["torso"].default_position[1] - 145),
        head_offset,
    )
    if torso_socket_world and "neck" in rotated_head_points:
        head_socket = rotated_head_points["neck"]
        overlap = max(8, int(10 * pose.scale))
        x = torso_socket_world[0] - head_socket[0]
        y = torso_socket_world[1] - head_socket[1] + overlap
        socket_metadata["head_neck_world"] = (x + head_socket[0], y + head_socket[1])
        socket_metadata["overlap_px"] = overlap
    else:
        socket_fallback = True
        warnings.append("socket alignment fallback used; missing transformed neck socket")
        x = fallback_position[0] - rotated_head_pivot[0]
        y = fallback_position[1] - rotated_head_pivot[1]
    render_items.append((rig.head.z, rotated_head, (int(x), int(y)), "head"))

    for _z, image, position, _part_id in sorted(render_items, key=lambda item: item[0]):
        canvas.alpha_composite(image, position)
    return canvas, socket_metadata, socket_fallback, head_path, face_path


def rotate_part_around_pivot(
    part_img: Image.Image,
    pivot: tuple[int, int],
    angle: float,
    points: dict[str, tuple[int, int]] | None = None,
) -> tuple[Image.Image, tuple[int, int], dict[str, tuple[int, int]]]:
    diag = int(math.ceil(math.hypot(part_img.width, part_img.height)))
    size = max(diag * 3, part_img.width * 3, part_img.height * 3, 64)
    center = size // 2
    temp = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    temp.alpha_composite(part_img, (center - pivot[0], center - pivot[1]))
    rotated = temp.rotate(angle, resample=Image.Resampling.BICUBIC, center=(center, center))
    bbox = rotated.getbbox()
    if not bbox:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0)), (0, 0), {}
    cropped = rotated.crop(bbox)
    rotated_points: dict[str, tuple[int, int]] = {}
    for key, point in (points or {}).items():
        temp_point = (center - pivot[0] + point[0], center - pivot[1] + point[1])
        rx, ry = _rotate_point(temp_point, (center, center), angle)
        rotated_points[key] = (int(round(rx - bbox[0])), int(round(ry - bbox[1])))
    return cropped, (center - bbox[0], center - bbox[1]), rotated_points


def validate_rig_pose_bounds(
    rig: RigDefinition,
    pose: RigPose,
    bbox: tuple[int, int, int, int] | None,
) -> list[str]:
    warnings: list[str] = []
    for part_id, angle in pose.angles.items():
        _safe_angle(rig, part_id, angle, warnings)
    if not bbox:
        warnings.append("rendered pose has no visible pixels")
        return warnings
    x0, y0, x1, y1 = bbox
    if x0 < 0 or y0 < 0 or x1 > rig.canvas_width or y1 > rig.canvas_height:
        warnings.append(f"rendered pose bbox outside rig canvas: {bbox}")
    height = y1 - y0
    if height < int(rig.canvas_height * 0.42):
        warnings.append(f"rendered pose bbox is small: {bbox}")
    if height > int(rig.canvas_height * 0.86):
        warnings.append(f"rendered pose bbox is large: {bbox}")
    return warnings


def compute_rig_hash(character_id: str = DEFAULT_CHARACTER_ID) -> str:
    rig = load_rig(character_id)
    h = hashlib.sha256()
    h.update((rig.root / "rig.json").read_bytes())
    head_path = _resolved_head_base_path(rig)
    if head_path.is_file():
        h.update(head_path.read_bytes())
    for expression_id in REQUIRED_EXPRESSIONS:
        face = rig.faces.get(expression_id)
        if face:
            path = rig.root / face.file
            if path.is_file():
                h.update(path.read_bytes())
    for part_id in REQUIRED_PARTS:
        part = rig.parts[part_id]
        path = rig.root / part.file
        if path.is_file():
            h.update(path.read_bytes())
    return h.hexdigest()[:16]


def is_placeholder_part(rig: RigDefinition, part_id: str) -> bool:
    part = rig.parts.get(part_id)
    if not part:
        return False
    path = rig.root / part.file
    if placeholder_marker(path).is_file():
        return True
    if not path.is_file():
        return False
    try:
        actual = _image_hash(Image.open(path).convert("RGBA"))
        expected = _image_hash(_render_placeholder_part(part_id))
        return actual == expected
    except OSError:
        return False


def is_placeholder_head(rig: RigDefinition) -> bool:
    path = _resolved_head_base_path(rig)
    if placeholder_marker(path).is_file():
        return True
    if not path.is_file() or path.name == Path(rig.head.fallback_file).name:
        return False
    try:
        actual = _image_hash(Image.open(path).convert("RGBA"))
        expected = _image_hash(_render_placeholder_head_base())
        return actual == expected
    except OSError:
        return False


def is_placeholder_face(rig: RigDefinition, expression_id: str) -> bool:
    face = rig.faces.get(expression_id)
    if not face:
        return False
    path = rig.root / face.file
    if placeholder_marker(path).is_file():
        return True
    if not path.is_file():
        return False
    try:
        actual = _image_hash(Image.open(path).convert("RGBA"))
        expected = _image_hash(_render_placeholder_face(expression_id))
        return actual == expected
    except OSError:
        return False


def placeholder_marker(path: Path) -> Path:
    return path.with_suffix(path.suffix + PLACEHOLDER_SUFFIX)


def _safe_angle(
    rig: RigDefinition,
    part_id: str,
    angle: float,
    warnings: list[str],
) -> float:
    low, high = rig.safe_angle_limits.get(part_id, (-45.0, 45.0))
    if angle < low or angle > high:
        warnings.append(f"{part_id} angle {angle:g} outside safe limits {low:g}..{high:g}; clamped")
        return max(low, min(high, angle))
    return angle


def _missing_parts_message(rig: RigDefinition, missing: list[Path]) -> str:
    rels = "\n".join(f"- {p}" for p in missing)
    return (
        "Missing required minimalist rig part PNGs:\n"
        f"{rels}\n\n"
        "Place transparent PNG rig parts under "
        f"{rig.root / 'parts'}, or set TELLA_ALLOW_PLACEHOLDER_SPRITES=1 while developing."
    )


def _warn_placeholder_part(path: Path) -> None:
    if path in _WARNED_PARTS:
        return
    _WARNED_PARTS.add(path)
    logger.warning(
        "Using generated placeholder rig parts; replace them before production. (%s)",
        path,
    )


def _image_hash(img: Image.Image) -> str:
    buf = BytesIO()
    img.save(buf, "PNG")
    return hashlib.sha256(buf.getvalue()).hexdigest()


def _resolved_head_base_path(rig: RigDefinition) -> Path:
    preferred = rig.root / rig.head.base_file
    if preferred.is_file():
        return preferred
    return rig.root / rig.head.fallback_file


def _pose_position(
    rig: RigDefinition,
    pose: RigPose,
    default_position: tuple[int, int],
    offset: tuple[int, int],
) -> tuple[int, int]:
    return (
        int((default_position[0] + offset[0] - rig.base_anchor[0]) * pose.scale + rig.base_anchor[0]),
        int((default_position[1] + offset[1] - rig.base_anchor[1]) * pose.scale + rig.base_anchor[1]),
    )


def _scale_part(
    source: Image.Image,
    scale: float,
    pivot: tuple[int, int],
    neck_attach: tuple[int, int] | None,
) -> tuple[Image.Image, tuple[int, int], tuple[int, int] | None]:
    if scale == 1.0:
        return source, pivot, neck_attach
    scaled = source.resize(
        (max(1, int(source.width * scale)), max(1, int(source.height * scale))),
        Image.LANCZOS,
    )
    scaled_pivot = (int(pivot[0] * scale), int(pivot[1] * scale))
    scaled_attach = (
        (int(neck_attach[0] * scale), int(neck_attach[1] * scale))
        if neck_attach
        else None
    )
    return scaled, scaled_pivot, scaled_attach


def _rotate_point(
    point: tuple[int, int],
    center: tuple[int, int],
    angle: float,
) -> tuple[float, float]:
    radians = math.radians(angle)
    dx = point[0] - center[0]
    dy = point[1] - center[1]
    return (
        center[0] + math.cos(radians) * dx + math.sin(radians) * dy,
        center[1] - math.sin(radians) * dx + math.cos(radians) * dy,
    )


def _render_placeholder_head_base() -> Image.Image:
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Longer soft bob sits below the neck socket to cover tiny attachment gaps.
    draw.rounded_rectangle((36, 18, 220, 238), radius=76, fill=HAIR)
    draw.ellipse((58, 54, 198, 196), fill=FACE, outline=INK, width=5)
    draw.pieslice((42, 20, 214, 132), 180, 360, fill=HAIR)
    draw.rectangle((50, 34, 206, 92), fill=HAIR)
    draw.rounded_rectangle((45, 150, 82, 235), radius=18, fill=HAIR)
    draw.rounded_rectangle((174, 150, 211, 235), radius=18, fill=HAIR)
    draw.line((58, 188, 198, 188), fill=HAIR, width=9)
    return img


def _render_placeholder_face(expression_id: str) -> Image.Image:
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if expression_id == "sad":
        draw.line((78, 102, 112, 112), fill=INK, width=4)
        draw.line((144, 112, 178, 102), fill=INK, width=4)
        draw.arc((108, 146, 150, 172), 200, 340, fill=INK, width=4)
    elif expression_id == "tired":
        draw.line((76, 110, 114, 112), fill=INK, width=4)
        draw.line((142, 112, 180, 110), fill=INK, width=4)
        draw.line((108, 154, 150, 154), fill=INK, width=4)
    elif expression_id == "gentle_smile":
        draw.arc((76, 98, 116, 122), 200, 330, fill=INK, width=4)
        draw.arc((140, 98, 180, 122), 210, 340, fill=INK, width=4)
        draw.arc((104, 138, 154, 166), 12, 168, fill=INK, width=4)
    elif expression_id == "hopeful":
        draw.arc((76, 94, 116, 118), 205, 335, fill=INK, width=4)
        draw.arc((140, 94, 180, 118), 205, 335, fill=INK, width=4)
        draw.line((80, 90, 112, 84), fill=INK, width=3)
        draw.line((144, 84, 176, 90), fill=INK, width=3)
        draw.arc((106, 138, 154, 164), 18, 162, fill=INK, width=4)
    else:
        draw.arc((76, 100, 116, 122), 200, 330, fill=INK, width=4)
        draw.arc((140, 100, 180, 122), 210, 340, fill=INK, width=4)
        draw.line((110, 152, 148, 152), fill=INK, width=4)
    draw.ellipse((70, 130, 100, 152), fill=BLUSH)
    draw.ellipse((156, 130, 186, 152), fill=BLUSH)
    return img


def _render_placeholder_part(part_id: str) -> Image.Image:
    if part_id == "head":
        img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((42, 20, 214, 224), radius=70, fill=HAIR)
        draw.ellipse((58, 58, 198, 198), fill=FACE, outline=INK, width=5)
        draw.rectangle((56, 34, 200, 88), fill=HAIR)
        draw.arc((74, 96, 118, 122), 200, 330, fill=INK, width=4)
        draw.arc((138, 96, 182, 122), 200, 330, fill=INK, width=4)
        draw.arc((104, 138, 152, 164), 12, 168, fill=INK, width=4)
        draw.ellipse((70, 130, 100, 152), fill=BLUSH)
        draw.ellipse((156, 130, 186, 152), fill=BLUSH)
        draw.line((56, 185, 200, 185), fill=HAIR, width=10)
        return img
    if part_id == "torso":
        img = Image.new("RGBA", (180, 260), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.polygon([(90, 18), (28, 238), (152, 238)], fill=MUSTARD, outline=INK)
        draw.line((56, 68, 124, 68), fill=RUST, width=15)
        return img
    if part_id in {"left_arm", "right_arm"}:
        img = Image.new("RGBA", (64, 230), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.line((16, 22, 36, 172), fill=RUST, width=16)
        draw.ellipse((24, 162, 52, 198), fill=FACE, outline=INK, width=3)
        if part_id == "right_arm":
            img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        return img
    if part_id in {"left_leg", "right_leg"}:
        img = Image.new("RGBA", (66, 250), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.line((24, 18, 34, 194), fill=INK, width=12)
        draw.line((33, 194, 50, 208), fill=INK, width=12)
        if part_id == "right_leg":
            img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        return img
    return Image.new("RGBA", (64, 64), (0, 0, 0, 0))


__all__ = [
    "REQUIRED_PARTS",
    "REQUIRED_EXPRESSIONS",
    "REQUIRED_POSES",
    "RigDefinition",
    "RigRenderResult",
    "RigValidationIssue",
    "character_mode",
    "compute_rig_hash",
    "compose_parts",
    "create_placeholder_rig_parts",
    "ensure_rig_parts",
    "is_placeholder_part",
    "is_placeholder_head",
    "is_placeholder_face",
    "load_rig",
    "render_rigged_character",
    "rotate_part_around_pivot",
    "select_expression",
    "validate_rig",
    "validate_rig_pose_bounds",
]
