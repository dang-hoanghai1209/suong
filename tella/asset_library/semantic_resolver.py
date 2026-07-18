from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter


DEFAULT_ASSET_LIBRARY_ROOT = Path(r"D:\tella-assets-staging\mvp_v1_processed_v2")
SEMANTICS_DEFAULT_PATH = Path(__file__).resolve().parents[1] / ".." / "scripts" / "asset_batch" / "asset_semantics_patch.json"
PRESETS_DEFAULT_PATH = Path(__file__).resolve().parents[1] / ".." / "scripts" / "asset_batch" / "test_scene_presets.json"


class AssetLibraryResolutionError(RuntimeError):
    """Structured failure for a required asset-library dependency."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


COMPOSITION_PRESETS: dict[str, dict[str, Any]] = {
    "bedroom_floor_sitting": {
        "character": {"target_height": 700, "x": 320, "bottom": 220},
        "shadow": {"alpha": 26, "blur": 12, "height": 32, "inset_x": 80},
        "objects": {
            "pillow": {"target_height": 175, "x": 225, "bottom": 240, "rotation_degrees": 0, "layer": "pre_character"},
            "phone_dark": {"target_height": 120, "x": 610, "bottom": 180, "rotation_degrees": 10, "layer": "post_character"},
        },
    },
    "window_waiting": {
        "character": {"target_height": 690, "x": 300, "bottom": 230},
        "shadow": {"alpha": 24, "blur": 12, "height": 30, "inset_x": 80},
        "objects": {
            "phone_dark": {"target_height": 115, "x": 660, "bottom": 205, "rotation_degrees": 10, "layer": "post_character"},
        },
    },
    "cafe_sitting": {
        "character": {"target_height": 680, "x": 285, "bottom": 300},
        "shadow": {"alpha": 25, "blur": 12, "height": 30, "inset_x": 75},
        "objects": {
            "empty_cup": {"target_height": 150, "x": 145, "bottom": 270, "rotation_degrees": 0, "layer": "pre_character"},
            "phone_dark": {"target_height": 115, "x": 690, "bottom": 250, "rotation_degrees": 8, "layer": "post_character"},
        },
    },
    "bus_stop_waiting": {
        "character": {"target_height": 880, "x": 350, "bottom": 170},
        "shadow": {"alpha": 22, "blur": 12, "height": 28, "inset_x": 70},
        "objects": {},
    },
    "floor_reflection": {
        "character": {"target_height": 700, "x": 320, "bottom": 220},
        "shadow": {"alpha": 24, "blur": 12, "height": 30, "inset_x": 80},
        "objects": {
            "paper_letter": {"target_height": 160, "x": 170, "bottom": 180, "rotation_degrees": -8, "layer": "pre_character"},
            "flower_single": {"target_height": 240, "x": 680, "bottom": 175, "rotation_degrees": 4, "layer": "post_character"},
        },
    },
    "tear_wiping": {
        "character": {"target_height": 880, "x": 340, "bottom": 170},
        "shadow": {"alpha": 22, "blur": 12, "height": 28, "inset_x": 70},
        "objects": {
            "tissue_box": {"target_height": 150, "x": 150, "bottom": 170, "rotation_degrees": 0, "layer": "pre_character"},
            "phone_dark": {"target_height": 110, "x": 690, "bottom": 155, "rotation_degrees": 12, "layer": "post_character"},
        },
    },
    "park_acceptance": {
        "character": {"target_height": 760, "x": 310, "bottom": 240},
        "shadow": {"alpha": 22, "blur": 12, "height": 28, "inset_x": 80},
        "objects": {
            "flower_single": {"target_height": 220, "x": 700, "bottom": 210, "rotation_degrees": 0, "layer": "post_character"},
        },
    },
}


@dataclass(slots=True)
class AssetLibraryRequest:
    character_id: str
    action: str
    emotion: str
    direction: str
    location: str
    time_of_day: str
    objects: list[str] = field(default_factory=list)
    optional_objects: list[str] = field(default_factory=list)
    composition_preset: str = ""
    seed: int = 0
    base_seed: int = 0
    scene_duration: float = 0.0
    narration_segment: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "character_id": self.character_id,
            "action": self.action,
            "emotion": self.emotion,
            "direction": self.direction,
            "location": self.location,
            "time_of_day": self.time_of_day,
            "objects": list(self.objects),
            "optional_objects": list(self.optional_objects),
            "composition_preset": self.composition_preset,
            "seed": self.seed,
            "base_seed": self.base_seed,
            "duration": self.scene_duration,
            "narration_segment": self.narration_segment,
        }


@dataclass(slots=True)
class SemanticResolution:
    requested: AssetLibraryRequest
    selected_semantic_id: str
    selected_source_asset_id: str
    character_processed_path: str
    background_path: str
    object_paths: dict[str, str]
    object_warnings: list[dict[str, Any]]
    selection_score: int
    selection_reasons: list[str]
    score_breakdown: dict[str, int]
    selected_tier: str
    enabled_flag: bool
    canonical_flag: bool
    production_eligible: bool
    quality_status: str
    fallback_reason: str
    deterministic_seed: int
    metadata: dict[str, Any] = field(default_factory=dict)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _resolve_registry_root(asset_library_root: str | Path | None = None) -> Path:
    if asset_library_root:
        return Path(asset_library_root).expanduser().resolve()
    env = (os.environ.get("TELLA_ASSET_LIBRARY_ROOT") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_ASSET_LIBRARY_ROOT


def _resolve_semantics_path(semantics_path: str | Path | None = None) -> Path:
    if semantics_path:
        return Path(semantics_path).expanduser().resolve()
    env = (os.environ.get("TELLA_ASSET_LIBRARY_SEMANTICS_PATH") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return SEMANTICS_DEFAULT_PATH.resolve()


def _resolve_preset_path(preset_path: str | Path | None = None) -> Path:
    if preset_path:
        return Path(preset_path).expanduser().resolve()
    return PRESETS_DEFAULT_PATH.resolve()


def _semantic_score(
    candidate: dict[str, Any], request: AssetLibraryRequest
) -> tuple[int, list[str], dict[str, int]]:
    breakdown = {
        "exact_action": 0,
        "exact_emotion": 0,
        "related_emotion": 0,
        "exact_direction": 0,
        "enabled": 0,
        "canonical": 0,
        "core_tier": 0,
        "backup_tier": 0,
    }
    reasons: list[str] = []
    if candidate.get("action") == request.action:
        breakdown["exact_action"] = 100
        reasons.append("exact action +100")
    if candidate.get("emotion") == request.emotion:
        breakdown["exact_emotion"] = 50
        reasons.append("exact emotion +50")
    elif request.emotion in candidate.get("related_emotions", []):
        breakdown["related_emotion"] = 25
        reasons.append("related emotion +25")
    if candidate.get("direction") == request.direction:
        breakdown["exact_direction"] = 5
        reasons.append("exact direction +5")
    if candidate.get("enabled_by_default"):
        breakdown["enabled"] = 15
        reasons.append("enabled by default +15")
    if candidate.get("canonical"):
        breakdown["canonical"] = 10
        reasons.append("canonical +10")
    if candidate.get("tier") == "core":
        breakdown["core_tier"] = 10
        reasons.append("core tier +10")
    elif candidate.get("tier") == "backup":
        breakdown["backup_tier"] = -5
        reasons.append("backup tier -5")
    score = sum(breakdown.values())
    return score, reasons, {**breakdown, "total": score}


def _is_production_eligible(candidate: dict[str, Any]) -> bool:
    if candidate.get("production_eligible") is False:
        return False
    return True


def _safe_candidate_pool(semantics: dict[str, Any], request: AssetLibraryRequest) -> list[dict[str, Any]]:
    candidates = []
    for candidate in semantics.get("assets", []):
        if candidate.get("character_id") != request.character_id:
            continue
        if candidate.get("action") != request.action:
            continue
        if not _is_production_eligible(candidate):
            continue
        candidates.append(candidate)
    if candidates:
        return candidates
    return [
        candidate
        for candidate in semantics.get("assets", [])
        if candidate.get("character_id") == request.character_id
        and _is_production_eligible(candidate)
    ]


def select_semantic_asset(
    semantics_path: str | Path | None,
    asset_library_root: str | Path | None,
    request: AssetLibraryRequest,
) -> SemanticResolution:
    semantics_path = _resolve_semantics_path(semantics_path)
    asset_library_root = _resolve_registry_root(asset_library_root)
    if not semantics_path.is_file():
        raise FileNotFoundError(f"Semantic overlay not found: {semantics_path}")
    if not (asset_library_root / "processed_asset_index.json").is_file():
        raise FileNotFoundError(f"Asset registry not found: {asset_library_root}")

    semantics = _read_json(semantics_path)
    index = _read_json(asset_library_root / "processed_asset_index.json")
    candidates = _safe_candidate_pool(semantics, request)
    if not candidates:
        raise LookupError(f"No semantic candidates for {request.to_dict()}")

    ranked: list[tuple[int, str, dict[str, Any], list[str], dict[str, int]]] = []
    for candidate in candidates:
        score, reasons, score_breakdown = _semantic_score(candidate, request)
        tie_material = f"{request.seed}:{candidate['semantic_id']}".encode("utf-8")
        tie_break = hashlib.sha256(tie_material).hexdigest()
        ranked.append((score, tie_break, candidate, reasons, score_breakdown))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    score, _, selected, reasons, score_breakdown = ranked[0]

    try:
        character_record = _find_processed(
            index,
            selected["source_asset_id"],
            selected["character_id"],
        )
    except LookupError as exc:
        raise AssetLibraryResolutionError(
            "required_character_missing",
            str(exc),
            details={
                "character_id": selected["character_id"],
                "source_asset_id": selected["source_asset_id"],
            },
        ) from exc
    characters_root = asset_library_root / "characters"
    try:
        background_path = _resolve_background(
            asset_library_root,
            request.location,
            request.time_of_day,
        )
    except FileNotFoundError as exc:
        raise AssetLibraryResolutionError(
            "required_background_missing",
            str(exc),
            details={"location": request.location, "time_of_day": request.time_of_day},
        ) from exc
    object_paths: dict[str, str] = {}
    object_warnings: list[dict[str, Any]] = []
    optional_objects = set(request.optional_objects)
    for obj in request.objects:
        try:
            object_paths[obj] = _resolve_object_path(asset_library_root, obj)
        except FileNotFoundError as exc:
            if obj in optional_objects:
                object_warnings.append({
                    "code": "optional_object_missing",
                    "object_id": obj,
                    "message": str(exc),
                })
                continue
            raise AssetLibraryResolutionError(
                "required_object_missing",
                str(exc),
                details={"object_id": obj},
            ) from exc
    return SemanticResolution(
        requested=request,
        selected_semantic_id=selected["semantic_id"],
        selected_source_asset_id=selected["source_asset_id"],
        character_processed_path=str(character_record["processed_path"]),
        background_path=str(background_path),
        object_paths=object_paths,
        object_warnings=object_warnings,
        selection_score=score,
        selection_reasons=reasons,
        score_breakdown=score_breakdown,
        selected_tier=str(selected.get("tier") or ""),
        enabled_flag=bool(selected.get("enabled_by_default")),
        canonical_flag=bool(selected.get("canonical")),
        production_eligible=_is_production_eligible(selected),
        quality_status=str(selected.get("quality_status") or "approved"),
        fallback_reason=(
            "" if selected.get("action") == request.action
            else "no_production_eligible_exact_action"
        ),
        deterministic_seed=request.seed,
        metadata={
            "character_record": character_record,
            "index_path": str(asset_library_root / "processed_asset_index.json"),
            "background_key": f"{request.location}:{request.time_of_day}",
            "object_keys": list(request.objects),
            "object_warnings": object_warnings,
            "scene_preset": request.composition_preset,
            "characters_root": str(characters_root),
        },
    )


def _find_processed(index: dict[str, Any], asset_id: str, character_id: str | None = None) -> dict[str, Any]:
    matches = [asset for asset in index.get("assets", []) if asset.get("asset_id") == asset_id and (character_id is None or asset.get("character_id") == character_id)]
    if not matches:
        raise LookupError(f"Processed asset missing: {character_id}:{asset_id}")
    return matches[0]


def _resolve_background(asset_library_root: Path, location: str, time_of_day: str) -> Path:
    desired = f"{location}_{time_of_day}"
    background_name = desired.replace(" ", "_")
    for category in ("indoor", "outdoor", "public_places"):
        candidate = asset_library_root / "backgrounds" / category / f"{background_name}_01.png"
        if candidate.is_file():
            return candidate
        fallback = asset_library_root / "backgrounds" / category / f"{background_name}.png"
        if fallback.is_file():
            return fallback
    raise FileNotFoundError(f"Background asset not found for {location}/{time_of_day}")


def _resolve_object_path(asset_library_root: Path, object_name: str) -> str:
    object_name = object_name.strip().lower()
    index_path = asset_library_root / "processed_asset_index.json"
    index = _read_json(index_path)
    for asset in index.get("assets", []):
        if asset.get("asset_type") == "object" and asset.get("asset_id") == object_name:
            path = Path(str(asset.get("processed_path") or ""))
            if path.is_file():
                return str(path)
    raise FileNotFoundError(f"Object asset not found: {object_name}")


def build_production_scene_request(scene: Any) -> AssetLibraryRequest:
    payload = getattr(scene, "asset_library_request", None) or {}
    if not payload:
        payload = {
            "character_id": getattr(scene, "character_id", "") or "",
            "action": getattr(scene, "scene_action", "") or "",
            "emotion": getattr(scene, "emotion_tag", "") or "",
            "direction": getattr(scene, "direction", "") or "front",
            "location": getattr(scene, "scene_setting", "") or "",
            "time_of_day": getattr(scene, "time_of_day", "") or "",
            "objects": list(getattr(scene, "required_objects", []) or []),
            "composition_preset": getattr(scene, "composition_preset", "") or "",
            "seed": int(getattr(scene, "seed", 0) or 0),
        }
    return AssetLibraryRequest(
        character_id=str(payload.get("character_id", "")),
        action=str(payload.get("action", "")),
        emotion=str(payload.get("emotion", "")),
        direction=str(payload.get("direction", "front")),
        location=str(payload.get("location", "")),
        time_of_day=str(payload.get("time_of_day", "")),
        objects=[str(item) for item in payload.get("objects", []) or []],
        optional_objects=[str(item) for item in payload.get("optional_objects", []) or []],
        composition_preset=str(payload.get("composition_preset", "")),
        seed=int(payload.get("seed", 0) or 0),
        base_seed=int(payload.get("base_seed", 0) or 0),
        scene_duration=float(payload.get("duration", 0.0) or 0.0),
        narration_segment=str(payload.get("narration_segment", "") or ""),
    )


def compose_asset_library_scene(
    scene: Any,
    output_path: Path,
    asset_library_root: str | Path | None = None,
    semantics_path: str | Path | None = None,
) -> dict[str, Any]:
    request = build_production_scene_request(scene)
    resolution = select_semantic_asset(semantics_path, asset_library_root, request)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas_size = (1080, 1920)
    preset = COMPOSITION_PRESETS.get(
        request.composition_preset,
        COMPOSITION_PRESETS["bedroom_floor_sitting"],
    )
    background = Image.open(resolution.background_path).convert("RGBA")
    background = _fit_cover(background, canvas_size)
    canvas = background.copy()

    character = Image.open(resolution.character_processed_path).convert("RGBA")
    character_spec = preset["character"]
    character = _scale_height(character, int(character_spec["target_height"]))
    char_x = int(character_spec["x"])
    char_y = canvas_size[1] - character.height - int(character_spec["bottom"])
    shadow_spec = preset["shadow"]
    shadow_layer = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    shadow_inset = int(shadow_spec["inset_x"])
    shadow_height = int(shadow_spec["height"])
    shadow_box = (
        char_x + shadow_inset,
        char_y + character.height - shadow_height // 2,
        char_x + character.width - shadow_inset,
        char_y + character.height + shadow_height // 2,
    )
    shadow_draw.ellipse(shadow_box, fill=(0, 0, 0, int(shadow_spec["alpha"])))
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(float(shadow_spec["blur"])))
    canvas.alpha_composite(shadow_layer)

    object_specs = preset["objects"]
    prepared_objects: dict[str, tuple[Image.Image, dict[str, int]]] = {}
    for asset_id, path in resolution.object_paths.items():
        spec = object_specs.get(asset_id)
        if spec is None:
            raise AssetLibraryResolutionError(
                "composition_object_unsupported",
                f"Composition preset {request.composition_preset!r} has no placement for {asset_id!r}",
                details={"object_id": asset_id, "preset": request.composition_preset},
            )
        with Image.open(path) as opened:
            rendered = _scale_height(opened.convert("RGBA"), spec["target_height"])
        rotation_degrees = spec["rotation_degrees"]
        if rotation_degrees:
            rendered = rendered.rotate(
                -rotation_degrees,
                resample=Image.Resampling.BICUBIC,
                expand=True,
            )
        placement = {
            "x": spec["x"],
            "y": canvas_size[1] - rendered.height - spec["bottom"],
            "width": rendered.width,
            "height": rendered.height,
            "rotation_degrees": rotation_degrees,
        }
        prepared_objects[asset_id] = rendered, placement

    pre_character = [
        asset_id for asset_id, spec in object_specs.items()
        if spec.get("layer") == "pre_character" and asset_id in prepared_objects
    ]
    post_character = [
        asset_id for asset_id, spec in object_specs.items()
        if spec.get("layer") == "post_character" and asset_id in prepared_objects
    ]
    for asset_id in pre_character:
        rendered, placement = prepared_objects[asset_id]
        canvas.alpha_composite(rendered, (placement["x"], placement["y"]))
    canvas.alpha_composite(character, (char_x, char_y))
    for asset_id in post_character:
        rendered, placement = prepared_objects[asset_id]
        canvas.alpha_composite(rendered, (placement["x"], placement["y"]))

    canvas.convert("RGB").save(output_path, format="PNG", optimize=True)
    object_metadata = [
        {"asset_id": asset_id, "processed_path": resolution.object_paths[asset_id], "placement": prepared_objects[asset_id][1]}
        for asset_id in (*pre_character, *post_character)
    ]
    layer_order = ["background", "character_shadow", *pre_character, "character", *post_character]
    layers = [
        {"layer": "background", "x": 0, "y": 0, "width": canvas_size[0], "height": canvas_size[1], "rotation_degrees": 0},
        {"layer": "character_shadow", "x": shadow_box[0], "y": shadow_box[1], "width": shadow_box[2] - shadow_box[0], "height": shadow_box[3] - shadow_box[1], "rotation_degrees": 0},
        *[
            {"layer": asset_id, **prepared_objects[asset_id][1]}
            for asset_id in pre_character
        ],
        {"layer": "character", "x": char_x, "y": char_y, "width": character.width, "height": character.height, "rotation_degrees": 0},
        *[
            {"layer": asset_id, **prepared_objects[asset_id][1]}
            for asset_id in post_character
        ],
    ]
    metadata = {
        "schema_version": 2,
        "seed": request.seed,
        "canvas": {"width": canvas_size[0], "height": canvas_size[1]},
        "background": {"relative_path": Path(resolution.background_path).name, "path": resolution.background_path},
        "character_request": request.to_dict(),
        "character": {
            "selected_semantic_id": resolution.selected_semantic_id,
            "selected_asset_id": resolution.selected_source_asset_id,
            "processed_path": resolution.character_processed_path,
            "selection_score": resolution.selection_score,
            "selection_reasons": resolution.selection_reasons,
            "selected_tier": resolution.selected_tier,
            "enabled": resolution.enabled_flag,
            "canonical": resolution.canonical_flag,
            "production_eligible": resolution.production_eligible,
            "quality_status": resolution.quality_status,
            "fallback_reason": resolution.fallback_reason,
            "score_breakdown": resolution.score_breakdown,
            "placement": {"x": char_x, "y": char_y, "width": character.width, "height": character.height, "target_height": character_spec["target_height"], "rotation_degrees": 0},
        },
        "objects": object_metadata,
        "object_warnings": resolution.object_warnings,
        "layer_order": layer_order,
        "layers": layers,
        "output": str(output_path),
    }
    return metadata


def _fit_cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    width, height = size
    scale = max(width / image.width, height / image.height)
    resized = image.resize((math.ceil(image.width * scale), math.ceil(image.height * scale)), Image.Resampling.LANCZOS)
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _scale_height(image: Image.Image, target_height: int) -> Image.Image:
    width = max(1, round(image.width * target_height / image.height))
    return image.resize((width, target_height), Image.Resampling.LANCZOS)


__all__ = [
    "AssetLibraryRequest",
    "AssetLibraryResolutionError",
    "SemanticResolution",
    "build_production_scene_request",
    "compose_asset_library_scene",
    "select_semantic_asset",
]
