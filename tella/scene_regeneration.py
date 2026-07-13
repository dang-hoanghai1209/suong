"""Safe derived-job regeneration for selected production scene images."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tella.atomic_write import atomic_write_text
from tella.planner.models import TellaScenePlan
from tella.planner.practical_life_steps_visuals import build_practical_provider_prompt
from tella.production import file_sha256, stable_hash
from tella.production_lock import ProductionJobLock


SCENE_REGENERATION_SCHEMA_VERSION = 1
_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|credential|access[_-]?token)\s*[:=]?\s*\S+"
)


def _safe_text(value: str, *, maximum: int = 1000) -> str:
    text = str(value or "").strip()
    if any(ord(char) < 32 and char not in "\n\t" for char in text):
        raise ValueError("correction text contains unsupported control characters")
    if len(text) > maximum:
        raise ValueError(f"correction text exceeds {maximum} characters")
    if _SECRET_RE.search(text):
        raise ValueError("correction text must not contain credentials or provider configuration")
    return text


def _redact(value: str) -> str:
    text = str(value or "")
    if _SECRET_RE.search(text):
        return "operation failed; credential-bearing details redacted"
    return text[:500]


def _atomic_deterministic_json(path: Path, payload: Any) -> Path:
    return atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", lambda: False)
    return path.is_symlink() or bool(is_junction())


def _normalize_job_paths(source_job: Path, target_job: Path) -> tuple[Path, Path]:
    raw_source, raw_target = Path(source_job).absolute(), Path(target_job).absolute()
    if _is_link_or_junction(raw_source) or (raw_target.exists() and _is_link_or_junction(raw_target)):
        raise ValueError("source and target job paths must not be symbolic links or junctions")
    if raw_source.exists() and raw_target.exists():
        try:
            if os.path.samefile(raw_source, raw_target):
                raise ValueError("source and target jobs resolve to the same filesystem object")
        except OSError:
            raise ValueError("source/target filesystem identity could not be verified")
    source, target = raw_source.resolve(), raw_target.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("source and target jobs must be separate non-nested directories")
    return source, target


class SceneCorrection(BaseModel):
    """Validated corrective constraints which augment, never replace, a prompt."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    scene_index: int = Field(gt=0)
    reason: str
    must_show: list[str] = Field(default_factory=list, max_length=12)
    must_not_show: list[str] = Field(default_factory=list, max_length=12)
    forbidden_text: bool = False
    object_state: str = ""
    requested_action: str = ""
    character_lock_notes: str = ""
    composition_notes: str = ""
    reviewer_notes: str = ""
    source_image_sha256: str = ""

    @field_validator(
        "reason", "object_state", "requested_action", "character_lock_notes",
        "composition_notes", "reviewer_notes", mode="before",
    )
    @classmethod
    def validate_text(cls, value: Any) -> str:
        return _safe_text(str(value or ""))

    @field_validator("must_show", "must_not_show", mode="before")
    @classmethod
    def validate_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("constraint values must be JSON arrays")
        return [_safe_text(str(item), maximum=300) for item in value]

    @field_validator("source_image_sha256")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        value = value.strip().lower()
        if value and not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("source_image_sha256 must be a full SHA256")
        return value

    @property
    def correction_hash(self) -> str:
        return stable_hash(self.model_dump())


def load_corrections(path: Path | None) -> dict[int, SceneCorrection]:
    if path is None:
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items = raw.get("corrections", []) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError("prompt corrections must be a list or contain a corrections list")
    corrections = [SceneCorrection.model_validate(item) for item in items]
    if len({item.scene_index for item in corrections}) != len(corrections):
        raise ValueError("prompt corrections contain duplicate scene indices")
    return {item.scene_index: item for item in corrections}


def build_corrected_provider_prompt(scene: Any, correction: SceneCorrection) -> str:
    base = build_practical_provider_prompt(scene)
    additions = ["Scene-specific corrective constraints:"]
    if correction.requested_action:
        additions.append(f"Requested visible action: {correction.requested_action}.")
    if correction.object_state:
        additions.append(f"Required static object state: {correction.object_state}.")
    if correction.must_show:
        additions.append("Must clearly show: " + "; ".join(correction.must_show) + ".")
    if correction.must_not_show:
        additions.append("Must not show: " + "; ".join(correction.must_not_show) + ".")
    if correction.character_lock_notes:
        additions.append("Preserve character identity: " + correction.character_lock_notes + ".")
    if correction.composition_notes:
        additions.append("Composition correction: " + correction.composition_notes + ".")
    if correction.forbidden_text:
        additions.append(
            "Absolutely no readable words, labels, logos, UI text, letters, numbers, "
            "captions, signs, signatures, or watermarks."
        )
    additions.append("Show the resulting state clearly; do not imply contradictory motion.")
    return " ".join((base, *additions))


def normalize_scene_indices(values: list[int] | tuple[int, ...]) -> list[int]:
    normalized = sorted(set(int(value) for value in values))
    if not normalized:
        raise ValueError("at least one scene index is required")
    if normalized[0] < 1 or normalized[-1] > 7:
        raise ValueError("scene indices must be in the inclusive range 1 through 7")
    return normalized


def _body_scenes(plan: TellaScenePlan) -> dict[int, Any]:
    scenes = {scene.scene_index: scene for scene in plan.scenes if scene.kind == "scene"}
    if sorted(scenes) != list(range(1, 8)):
        raise ValueError("source plan must contain exactly scene indices 1 through 7")
    return scenes


def _scene_path(source_job: Path, scene: Any) -> Path:
    relative = scene.asset_path or (scene.image_filenames[0] if scene.image_filenames else "")
    if not relative:
        raise ValueError(f"scene {scene.scene_index} has no image path")
    raw_path = source_job / relative
    if _is_link_or_junction(raw_path):
        raise ValueError(f"scene {scene.scene_index} image must not be a link or junction")
    path = raw_path.resolve()
    if source_job.resolve() not in path.parents or not path.is_file():
        raise ValueError(f"scene {scene.scene_index} image is missing or outside the source job")
    return path


def _source_record(source_job: Path, indices: list[int], corrections: dict[int, SceneCorrection]) -> dict[str, Any]:
    source_job = source_job.resolve()
    plan_path = source_job / "plan.json"
    manifest_path = source_job / "production_manifest.json"
    if not plan_path.is_file() or not manifest_path.is_file():
        raise ValueError("source job requires plan.json and production_manifest.json")
    if _is_link_or_junction(plan_path) or _is_link_or_junction(manifest_path):
        raise ValueError("source plan and manifest must be independent regular files")
    plan = TellaScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    scenes = _body_scenes(plan)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_plan_hash = str(manifest.get("artifact_hashes", {}).get("plan", ""))
    if expected_plan_hash and expected_plan_hash != file_sha256(plan_path):
        raise ValueError("source plan hash does not match production manifest")
    image_records = {
        Path(str(item.get("path", ""))).as_posix(): str(item.get("sha256", ""))
        for item in manifest.get("image_artifacts", []) if isinstance(item, dict)
    }
    if len(image_records) != 7:
        raise ValueError("source production manifest must identify exactly seven images")
    for scene in scenes.values():
        image = _scene_path(source_job, scene)
        relative = image.relative_to(source_job).as_posix()
        if not image_records.get(relative) or image_records[relative] != file_sha256(image):
            raise ValueError(f"scene {scene.scene_index} image hash does not match production manifest")
    for index in indices:
        image = _scene_path(source_job, scenes[index])
        expected = corrections.get(index)
        if expected and expected.source_image_sha256 and file_sha256(image) != expected.source_image_sha256:
            raise ValueError(f"scene {index} source image hash does not match correction template")
    unknown_corrections = sorted(set(corrections) - set(indices))
    if unknown_corrections:
        raise ValueError(f"corrections apply to unselected scenes: {unknown_corrections}")
    return {"plan": plan, "scenes": scenes, "manifest": manifest,
            "plan_path": plan_path, "manifest_path": manifest_path}


def build_regeneration_envelope(
    source_job: Path,
    target_job: Path,
    *,
    scene_indices: list[int],
    reason: str,
    max_ai_images: int,
    corrections: dict[int, SceneCorrection] | None = None,
    no_render: bool = False,
) -> dict[str, Any]:
    indices = normalize_scene_indices(scene_indices)
    if max_ai_images < len(indices):
        raise ValueError("image request budget is lower than selected-scene count")
    corrections = corrections or {}
    source = _source_record(Path(source_job), indices, corrections)
    return _regeneration_envelope_from_source(
        Path(source_job), Path(target_job), indices=indices, reason=reason,
        max_ai_images=max_ai_images, corrections=corrections,
        no_render=no_render, source=source,
    )


def _regeneration_envelope_from_source(
    source_job: Path,
    target_job: Path,
    *,
    indices: list[int],
    reason: str,
    max_ai_images: int,
    corrections: dict[int, SceneCorrection],
    no_render: bool,
    source: dict[str, Any],
) -> dict[str, Any]:
    reused = [index for index in range(1, 8) if index not in indices]
    return {
        "scene_regeneration_schema_version": SCENE_REGENERATION_SCHEMA_VERSION,
        "operation": "scene_regeneration",
        "source_job_id": Path(source_job).resolve().name,
        "source_job_path": str(Path(source_job).resolve()),
        "source_production_fingerprint": str(source["manifest"].get("recipe_fingerprint", "")),
        "source_manifest_sha256": file_sha256(source["manifest_path"]),
        "source_recipe_id": source["plan"].recipe_id,
        "source_recipe_version": source["plan"].recipe_version,
        "source_plan_sha256": file_sha256(source["plan_path"]),
        "target_job_id": Path(target_job).name,
        "target_job_path": str(Path(target_job).resolve()),
        "regenerated_scene_indices": indices,
        "reused_scene_indices": reused,
        "reason": _safe_text(reason, maximum=200),
        "prompt_correction_hash": stable_hash([
            corrections[index].model_dump() for index in indices if index in corrections
        ]),
        "image_provider": "cloudflare",
        "image_request_budget": len(indices),
        "configured_max_ai_images": int(max_ai_images),
        "expected_image_request_count": len(indices),
        "retry_count": 0,
        "fallback_count": 0,
        "regeneration_resume_supported": False,
        "partial_artifacts_reusable": False,
        "render_required": True,
        "render_requested": not no_render,
        "reused_artifacts": [
            "narration", "mixed_audio", "alignment", "boundary_metadata",
            "subtitles", "scene_timing", "music_metadata", "recipe", "voice_metadata",
        ],
        "invalidated_artifacts": [
            "video", "video_qc", "selected_scene_visual_qc", "completed_state",
        ],
        "external_calls_performed": 0,
        "render_operations_performed": 0,
    }


ImageProvider = Callable[[str, Path, int], Awaitable[Path | None]]
Renderer = Callable[[TellaScenePlan, Path, Path], Awaitable[Path]]


async def _default_image_provider(prompt: str, output: Path, scene_index: int) -> Path:
    from tella.media.ai_image import DEFAULT_HEIGHT, DEFAULT_MODEL, DEFAULT_WIDTH, generate_image
    return await generate_image(
        prompt, output, model=DEFAULT_MODEL, width=DEFAULT_WIDTH,
        height=DEFAULT_HEIGHT, seed=20260713 + scene_index,
        max_accounts=1, max_attempts_per_account=1,
    )


async def _default_renderer(plan: TellaScenePlan, target_job: Path, mixed_audio: Path) -> Path:
    from tella.render.pipeline import render
    from tella.music.audio import probe_duration
    video = await render(plan, target_job, preserve_timing=True,
                         existing_mixed_audio=mixed_audio)
    duration = await probe_duration(video)
    delta = abs(duration - plan.total_duration)
    video_qc = {
        "status": "passed" if delta <= 0.15 else "failed",
        "expected_duration": round(plan.total_duration, 3),
        "actual_duration": round(duration, 3),
        "duration_mismatch": round(delta, 3),
        "scene_count": len([scene for scene in plan.scenes if scene.kind == "scene"]),
    }
    _atomic_deterministic_json(target_job / "video_qc.json", video_qc)
    if video_qc["status"] != "passed":
        raise RuntimeError("video QC failed: final duration does not match preserved timing")
    return video


def _reusable_source_files(source: Path, selected_paths: set[Path]) -> list[Path]:
    excluded_names = {
        ".tella-job.lock", "video.mp4", "video_qc.json", "visual_qc.json",
        "production_manifest.json", "production_summary.json", "scene_regeneration.json",
    }
    files: list[Path] = []
    for item in sorted(source.rglob("*")):
        if not item.is_file() or item.resolve() in selected_paths:
            continue
        if _is_link_or_junction(item):
            raise ValueError(f"source artifact aliases are not allowed: {item}")
        relative = item.relative_to(source)
        if item.name in excluded_names or relative.parts[0] == "_render":
            continue
        files.append(item)
    return files


def _copy_reusable_tree(source: Path, target: Path, selected_paths: set[Path]) -> list[Path]:
    copied: list[Path] = []
    for item in _reusable_source_files(source, selected_paths):
        relative = item.relative_to(source)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, destination)
        if _is_link_or_junction(destination) or not destination.is_file():
            raise RuntimeError(f"target copy is not an independent regular file: {relative}")
        copied.append(destination)
    return copied


def _verify_independent_copies(
    source: Path,
    target: Path,
    expected_hashes: dict[str, str],
    copied: list[Path],
) -> None:
    copied_relatives = {path.relative_to(target).as_posix() for path in copied}
    for relative, expected in expected_hashes.items():
        source_path = source / relative
        if not source_path.is_file() or file_sha256(source_path) != expected:
            raise RuntimeError(f"source artifact changed while locked: {relative}")
        if relative not in copied_relatives:
            continue
        target_path = target / relative
        if file_sha256(target_path) != expected:
            raise RuntimeError(f"target copy hash mismatch: {relative}")
        try:
            if os.path.samefile(source_path, target_path):
                raise RuntimeError(f"target copy shares source file identity: {relative}")
        except OSError as exc:
            raise RuntimeError(f"target copy identity could not be verified: {relative}") from exc


def _find_mixed_audio(job: Path) -> Path:
    candidates = (
        job / "assets" / "final_mixed_audio.m4a",
        job / "_render" / "final_mixed_audio.m4a",
        job / "assets" / "final_mixed_audio_reused.m4a",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ValueError("source job has no accepted final mixed audio")


def _validate_image(path: Path) -> None:
    from PIL import Image
    try:
        with Image.open(path) as image:
            image.verify()
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"generated image failed technical validation: {path.name}") from exc


def _write_derived_state(target_job: Path, plan: TellaScenePlan,
                         metadata: dict[str, Any]) -> None:
    images = []
    for scene in (item for item in plan.scenes if item.kind == "scene"):
        relative = scene.asset_path or scene.image_filenames[0]
        image = target_job / relative
        images.append({"path": Path(relative).as_posix(), "sha256": file_sha256(image),
                       "scene_index": scene.scene_index})
    manifest = {
        "production_schema_version": 1,
        "derived_job_type": "scene_regeneration",
        "source_production_fingerprint": metadata["source_production_fingerprint"],
        "source_manifest_sha256": metadata["source_manifest_sha256"],
        "source_plan_sha256": metadata["source_plan_sha256"],
        "regeneration_fingerprint": stable_hash({
            "source_manifest_sha256": metadata["source_manifest_sha256"],
            "regenerated_scene_indices": metadata["regenerated_scene_indices"],
            "prompt_correction_hash": metadata["prompt_correction_hash"],
            "regenerated_image_hashes": metadata["regenerated_image_hashes"],
        }),
        "recipe_fingerprint": metadata["source_production_fingerprint"],
        "image_artifacts": images,
        "artifact_hashes": {
            "plan": file_sha256(target_job / "plan.json"),
            **{
                key: value for key, value in metadata["reused_audio_timing_subtitle_hashes"].items()
            },
        },
        "qc_results": {
            "audio": "passed" if metadata["qc_state"] == "passed" else "reused_source_pass",
            "video": "passed" if metadata["qc_state"] == "passed" else "pending",
        },
    }
    summary = {
        "status": "completed" if metadata["status"] == "completed" else "partial_failure",
        "current_stage": (
            "completed" if metadata["status"] == "completed"
            else ("images_ready" if metadata["render_state"] == "required" else "rendered")
        ),
        "last_successful_stage": (
            "completed" if metadata["status"] == "completed"
            else ("images_ready" if metadata["render_state"] == "required" else "rendered")
        ),
        "failed_stage": "",
        "error_category": "",
        "safe_error_message": "",
        "external_submission_counts": {
            "gemini": 0, "edge": 0,
            "image_provider": metadata["actual_image_request_count"],
            "retries": 0, "fallbacks": 0,
        },
        "resumable": metadata["status"] != "completed",
        "recommended_resume_action": (
            "render derived job locally" if metadata["render_state"] == "required"
            else (
                "human visual review required" if metadata["status"] == "completed"
                else "run technical video QC and human visual review"
            )
        ),
        "source_job_immutable": True,
    }
    _atomic_deterministic_json(target_job / "production_manifest.json", manifest)
    _atomic_deterministic_json(target_job / "production_summary.json", summary)


async def regenerate_scenes(
    source_job: Path,
    target_job: Path,
    *,
    scene_indices: list[int],
    reason: str,
    max_ai_images: int,
    corrections: dict[int, SceneCorrection] | None = None,
    dry_run: bool = False,
    no_render: bool = False,
    recover_stale_lock: bool = False,
    output: Path | None = None,
    image_provider: ImageProvider | None = None,
    renderer: Renderer | None = None,
) -> dict[str, Any]:
    corrections = corrections or {}
    indices = normalize_scene_indices(scene_indices)
    if max_ai_images < len(indices):
        raise ValueError("image request budget is lower than selected-scene count")
    source_job, target_job = _normalize_job_paths(Path(source_job), Path(target_job))
    if dry_run:
        envelope = build_regeneration_envelope(
            source_job, target_job, scene_indices=indices, reason=reason,
            max_ai_images=max_ai_images, corrections=corrections, no_render=no_render,
        )
        if output is not None:
            _atomic_deterministic_json(Path(output), envelope)
        return envelope
    provider = image_provider or _default_image_provider
    render_operation = renderer or _default_renderer
    created_target = not target_job.exists()
    if target_job.exists() and any(
        item.name != ".tella-job.lock" for item in target_job.iterdir()
    ):
        raise ValueError(
            "target job already contains artifacts; regeneration resume is unsupported, "
            "so use a new empty target job ID"
        )

    try:
        with ExitStack() as stack:
            for job in sorted((source_job, target_job), key=lambda path: str(path).casefold()):
                stack.enter_context(ProductionJobLock(
                    job, recipe_id="practical_life_steps_callirrhoe_v1",
                    operation="scene-regeneration",
                    recover_stale=recover_stale_lock,
                ))
            # Every authoritative source read and hash check occurs while both
            # locks are held. No target artifact or provider call precedes this.
            source = _source_record(source_job, indices, corrections)
            plan: TellaScenePlan = source["plan"]
            scenes = source["scenes"]
            envelope = _regeneration_envelope_from_source(
                source_job, target_job, indices=indices, reason=reason,
                max_ai_images=max_ai_images, corrections=corrections,
                no_render=no_render, source=source,
            )
            selected_paths = {
                _scene_path(source_job, scenes[index]).resolve() for index in indices
            }
            reusable_files = _reusable_source_files(source_job, selected_paths)
            source_snapshot = {
                path.relative_to(source_job).as_posix(): file_sha256(path)
                for path in [*reusable_files, *selected_paths]
            }
            copied = _copy_reusable_tree(source_job, target_job, selected_paths)
            _verify_independent_copies(source_job, target_job, source_snapshot, copied)
            metadata = {**envelope,
                        "created_at_utc": datetime.now(timezone.utc).isoformat(),
                        "actual_image_request_count": 0,
                        "regenerated_image_hashes": {}, "reused_image_hashes": {},
                        "provider_requests": [],
                        "reused_audio_timing_subtitle_hashes": {},
                        "render_state": "required", "qc_state": "pending",
                        "status": "partial_failure", "failure_classification": "",
                        "failure_stage": ""}
            for index in envelope["reused_scene_indices"]:
                source_image = _scene_path(source_job, scenes[index])
                metadata["reused_image_hashes"][str(index)] = file_sha256(source_image)
            for path in copied:
                relative = path.relative_to(target_job).as_posix()
                if any(token in relative.lower() for token in (
                    "narration", "audio", "alignment", "boundar", "subtitle", "timing", "music"
                )):
                    metadata["reused_audio_timing_subtitle_hashes"][relative] = file_sha256(path)
            _atomic_deterministic_json(target_job / "scene_regeneration.json", metadata)

            try:
                metadata["failure_stage"] = "selected_image_generation"
                for index in indices:
                    scene = scenes[index]
                    source_image = _scene_path(source_job, scene)
                    relative = source_image.relative_to(source_job)
                    destination = target_job / relative
                    correction = corrections.get(index) or SceneCorrection(
                        scene_index=index, reason=reason,
                        requested_action=scene.visual_action or scene.scene_action,
                    )
                    prompt = build_corrected_provider_prompt(scene, correction)
                    metadata["provider_requests"].append({
                        "scene_index": index,
                        "provider": "cloudflare",
                        "model": "@cf/black-forest-labs/flux-1-schnell",
                        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                        "correction": correction.model_dump(mode="json"),
                        "maximum_accounts": 1,
                        "maximum_transport_attempts": 1,
                        "retry_count": 0,
                        "fallback_count": 0,
                    })
                    metadata["actual_image_request_count"] += 1
                    result = await provider(prompt, destination, index)
                    if result is not None and Path(result).resolve() != destination.resolve():
                        raise RuntimeError("image provider returned an unexpected output path")
                    if not destination.is_file():
                        raise RuntimeError(f"image provider produced no file for scene {index}")
                    _validate_image(destination)
                    digest = file_sha256(destination)
                    metadata["regenerated_image_hashes"][str(index)] = digest
                metadata["status"] = "render_required" if no_render else "rendering"
                _atomic_deterministic_json(target_job / "scene_regeneration.json", metadata)
                if no_render:
                    metadata["render_state"] = "required"
                    metadata["qc_state"] = "pending"
                else:
                    metadata["failure_stage"] = "render"
                    mixed_audio = _find_mixed_audio(target_job)
                    video = await render_operation(plan, target_job, mixed_audio)
                    if not Path(video).is_file():
                        raise RuntimeError("renderer did not produce video.mp4")
                    metadata["render_operations_performed"] = 1
                    metadata["render_state"] = "completed"
                    audio_qc_path = target_job / "audio_qc.json"
                    video_qc_path = target_job / "video_qc.json"
                    if audio_qc_path.is_file() and video_qc_path.is_file():
                        audio_qc = json.loads(audio_qc_path.read_text(encoding="utf-8"))
                        video_qc = json.loads(video_qc_path.read_text(encoding="utf-8"))
                        if audio_qc.get("status") in {"passed", "warning"} and video_qc.get("status") == "passed":
                            metadata["qc_state"] = "passed"
                            metadata["status"] = "completed"
                        else:
                            metadata["qc_state"] = "failed"
                            metadata["status"] = "qc_failure"
                    else:
                        metadata["qc_state"] = "technical_qc_required"
                        metadata["status"] = "partial_failure"
                metadata["failure_stage"] = "metadata"
                _write_derived_state(target_job, plan, metadata)
                _atomic_deterministic_json(target_job / "scene_regeneration.json", metadata)
                if output is not None:
                    _atomic_deterministic_json(Path(output), metadata)
                return metadata
            except BaseException as exc:
                stage = metadata.get("failure_stage", "")
                metadata["status"] = (
                    "provider_failure" if stage == "selected_image_generation"
                    else ("render_failure" if stage == "render" else "partial_failure")
                )
                metadata["failure_classification"] = type(exc).__name__
                metadata["safe_error_message"] = _redact(str(exc))
                metadata["render_state"] = "required"
                _atomic_deterministic_json(target_job / "scene_regeneration.json", metadata)
                _atomic_deterministic_json(target_job / "production_summary.json", {
                    "status": metadata["status"],
                    "current_stage": "images_ready",
                    "last_successful_stage": "source_artifacts_reused",
                    "failed_stage": stage or "derived_metadata",
                    "error_category": metadata["failure_classification"],
                    "safe_error_message": metadata["safe_error_message"],
                    "external_submission_counts": {
                        "gemini": 0, "edge": 0,
                        "image_provider": metadata["actual_image_request_count"],
                        "retries": 0, "fallbacks": 0,
                    },
                    "resumable": False,
                    "regeneration_resume_supported": False,
                    "partial_artifacts_reusable": False,
                    "recommended_resume_action": (
                        "inspect preserved partial artifacts, then restart with a new empty target job ID"
                    ),
                    "source_job_immutable": True,
                })
                raise
    except BaseException:
        if created_target and target_job.exists() and not any(target_job.iterdir()):
            target_job.rmdir()
        raise


__all__ = [
    "SCENE_REGENERATION_SCHEMA_VERSION", "SceneCorrection",
    "build_corrected_provider_prompt", "build_regeneration_envelope",
    "load_corrections", "normalize_scene_indices", "regenerate_scenes",
]
