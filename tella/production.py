"""Versioned production contracts, state, caching, and safe resume decisions."""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from tella.atomic_write import atomic_write_json

PRODUCTION_SCHEMA_VERSION = 1
CALLIRRHOE_RECIPE_ID = "practical_life_steps_callirrhoe_v1"


class ProductionStage(StrEnum):
    initialized = "initialized"
    recipe_resolved = "recipe_resolved"
    planned = "planned"
    images_ready = "images_ready"
    narration_ready = "narration_ready"
    aligned = "aligned"
    music_ready = "music_ready"
    rendered = "rendered"
    qc_passed = "qc_passed"
    completed = "completed"
    failed = "failed"


class ProductionSummaryStatus(StrEnum):
    completed = "completed"
    provider_failure = "provider_failure"
    quota_failure = "quota_failure"
    validation_failure = "validation_failure"
    render_failure = "render_failure"
    qc_failure = "qc_failure"
    interrupted = "interrupted"
    partial_failure = "partial_failure"


class ProductionConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    schema_version: int = PRODUCTION_SCHEMA_VERSION
    recipe_id: str = CALLIRRHOE_RECIPE_ID
    recipe_version: int = 1
    planner_id: str = "practical_life_steps"
    planner_version: str = "practical_life_steps_v1"
    scene_count: int = 7
    language: str = "vi"
    aspect_ratio: str = "9:16"
    width: int = 1080
    height: int = 1920
    fps: int = 30
    voice_profile: str = "gemini_callirrhoe_vi_natural_smile"
    provider: str = "gemini"
    model: str = "gemini-3.1-flash-tts-preview"
    voice: str = "Callirrhoe"
    style: str = "natural_vocal_smile"
    tts_language: str = "vi-VN"
    voice_rate: str = "0%"
    voice_registry_version: int = 1
    max_tts_requests: int = 1
    tts_attempts: int = 1
    tts_retry: bool = False
    edge_fallback: bool = False
    model_fallback: bool = False
    post_tts_atempo: bool = False
    duration_fitting: bool = False
    alignment_enabled: bool = True
    alignment_algorithm_version: str = "sentence_energy_alignment_v1"
    alignment_mode: str = "sentence_silence"
    alignment_search_window_seconds: float = 1.25
    alignment_minimum_scene_duration: float = 2.0
    alignment_asr_enabled: bool = False
    alignment_manual_overrides: dict[str, float] = Field(default_factory=dict)
    alignment_fallback: str = "deterministic_text_weighted"
    subtitle_intervals_follow_alignment: bool = True
    scene_intervals_follow_alignment: bool = True
    music_track: str = "practical_calm_01"
    music_profile: str = "practical_calm_rhythm"
    music_gain_db: float = -11.0
    ducking_threshold: float = 0.025
    ducking_ratio: float = 2.5
    ducking_attack_ms: int = 25
    ducking_release_ms: int = 300
    fade_in_seconds: float = 0.6
    fade_out_seconds: float = 0.9
    track_offset_seconds: float = 8.0
    music_loop: bool = False
    subtitle_style: str = "practical_steps_reel"
    motion_profile: str = "gentle_progressive_motion"
    transition_profile: str = "clean_progressive_cut"
    video_codec: str = "h264"
    audio_codec: str = "aac"
    audio_qc_required: bool = True
    video_qc_required: bool = True
    max_image_requests: int = 7


CALLIRRHOE_PRODUCTION_CONFIG = ProductionConfig()


class LocalTTSCache:
    """Credential-free, content-addressed storage for raw provider audio."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def lookup(self, key: str, destination: Path) -> bool:
        audio = self.root / f"{key}.wav"
        metadata = self.root / f"{key}.json"
        if not audio.is_file() or not metadata.is_file():
            return False
        try:
            record = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if record.get("cache_key") != key or record.get("raw_audio_sha256") != file_sha256(audio):
            return False
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(audio, destination)
        return True

    def evaluate(self, key: str) -> dict[str, Any]:
        audio = self.root / f"{key}.wav"
        metadata = self.root / f"{key}.json"
        valid = False
        if audio.is_file() and metadata.is_file():
            try:
                record = json.loads(metadata.read_text(encoding="utf-8"))
                valid = (
                    record.get("cache_key") == key
                    and record.get("raw_audio_sha256") == file_sha256(audio)
                )
            except (OSError, json.JSONDecodeError):
                valid = False
        return {
            "cache_key": key,
            "cache_hit": valid,
            "raw_audio_path": str(audio) if valid else "",
            "estimated_gemini_requests": 0 if valid else 1,
        }

    def store(self, key: str, raw_audio: Path, metadata: dict[str, Any]) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self.root / f"{key}.wav"
        shutil.copyfile(raw_audio, destination)
        safe_metadata = {
            item: value for item, value in metadata.items()
            if not any(secret in item.lower() for secret in ("key", "token", "authorization", "credential"))
        }
        safe_metadata.update({
            "cache_key": key,
            "raw_audio_sha256": file_sha256(destination),
        })
        (self.root / f"{key}.json").write_text(
            json.dumps(safe_metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return destination


def get_production_config(recipe_id: str) -> ProductionConfig | None:
    return CALLIRRHOE_PRODUCTION_CONFIG if recipe_id == CALLIRRHOE_RECIPE_ID else None


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def production_fingerprint(config: ProductionConfig) -> str:
    return stable_hash(config.model_dump())


def tts_cache_key(*, provider: str, model: str, voice: str, style: str,
                  language: str, canonical_narration_sha256: str,
                  serialized_provider_input_sha256: str, request_format_version: str,
                  voice_registry_version: int) -> str:
    return stable_hash({
        "provider": provider, "model": model, "voice": voice, "style": style,
        "language": language, "canonical_narration_sha256": canonical_narration_sha256,
        "serialized_provider_input_sha256": serialized_provider_input_sha256,
        "request_format_version": request_format_version,
        "voice_registry_version": voice_registry_version,
    })


def classify_error(exc: BaseException) -> tuple[str, str]:
    text = str(exc)
    upper = text.upper()
    if isinstance(exc, (KeyboardInterrupt, InterruptedError)):
        return "interrupted", "interrupted"
    if "429" in text or "RESOURCE_EXHAUSTED" in upper:
        return "quota_failure", "quota_or_rate_limit"
    if "400" in text or "INVALID_ARGUMENT" in upper:
        return "provider_failure", "invalid_request"
    if "401" in text or "403" in text or "PERMISSION" in upper:
        return "provider_failure", "credential_or_permission"
    if any(code in text for code in ("500", "502", "503", "504")) or "SERVER" in upper:
        return "provider_failure", "transient_server_failure"
    if "AUDIO" in upper and ("MISSING" in upper or "MALFORMED" in upper or "EMPTY" in upper):
        return "provider_failure", "malformed_or_missing_audio"
    if "QC" in upper:
        return "qc_failure", "qc_failure"
    if "RENDER" in upper or "FFMPEG" in upper:
        return "render_failure", "render_failure"
    if isinstance(exc, (ValueError, FileNotFoundError)):
        return "validation_failure", "validation_failure"
    return "partial_failure", "unexpected_failure"


def _safe_message(exc: BaseException) -> str:
    text = str(exc)
    for marker in ("API_KEY", "Authorization", "Bearer "):
        if marker.lower() in text.lower():
            return "provider failure; credential details redacted"
    return text[:500]


class ProductionRun:
    def __init__(self, job_dir: Path, config: ProductionConfig, *, resume: bool = False):
        self.job_dir = Path(job_dir)
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.resume = resume
        self.stage = ProductionStage.initialized
        self.last_successful_stage = ""
        self.counts = {"gemini": 0, "edge": 0, "image_provider": 0,
                       "retries": 0, "fallbacks": 0}
        self.artifacts: dict[str, str] = {}
        self.artifact_issues: dict[str, dict[str, str]] = {}
        if not (resume and self.manifest_path.is_file()):
            self.write_manifest()
        self.write_summary("partial_failure", resumable=True,
                           recommended="continue from initialized stage")

    @property
    def manifest_path(self) -> Path:
        return self.job_dir / "production_manifest.json"

    @property
    def summary_path(self) -> Path:
        return self.job_dir / "production_summary.json"

    def write_manifest(self, extra: dict[str, Any] | None = None) -> None:
        payload = {
            "production_schema_version": PRODUCTION_SCHEMA_VERSION,
            "recipe": self.config.model_dump(),
            "recipe_fingerprint": production_fingerprint(self.config),
            "created_or_updated": datetime.now(timezone.utc).isoformat(),
            "resume_requested": self.resume,
            **(extra or {}),
        }
        atomic_write_json(self.manifest_path, payload)

    def record_artifact_hashes(
        self,
        artifacts: dict[str, Path],
        *,
        image_artifacts: list[Path] | None = None,
        qc_results: dict[str, str] | None = None,
    ) -> None:
        """Persist verified artifact identities without recording secret inputs."""
        current: dict[str, Any] = {}
        if self.manifest_path.is_file():
            current = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        current["artifact_hashes"] = {
            key: file_sha256(path) for key, path in artifacts.items() if path.is_file()
        }
        current["image_artifacts"] = [
            {
                "path": str(path.relative_to(self.job_dir)),
                "sha256": file_sha256(path),
            }
            for path in (image_artifacts or [])
            if path.is_file()
        ]
        if qc_results is not None:
            current["qc_results"] = dict(qc_results)
        atomic_write_json(self.manifest_path, current)

    def advance(self, stage: ProductionStage, artifacts: dict[str, Path | str] | None = None) -> None:
        self.stage = stage
        self.last_successful_stage = stage.value
        for key, value in (artifacts or {}).items():
            self.artifacts[key] = str(value)
        self.write_summary("completed" if stage == ProductionStage.completed else "partial_failure",
                           resumable=stage != ProductionStage.completed,
                           recommended="none" if stage == ProductionStage.completed else f"continue from {stage.value}")

    def record_artifact_issue(
        self, key: str, path: Path | str, *, status: str, reason: str
    ) -> None:
        if status not in {"failed", "missing", "invalid"}:
            raise ValueError("artifact issue status must be failed, missing, or invalid")
        self.artifact_issues[key] = {
            "path": str(path), "status": status, "reason": reason[:200],
        }

    def write_summary(self, status: str, *, resumable: bool, recommended: str,
                      failed_stage: str = "", error_category: str = "",
                      safe_error_message: str = "") -> None:
        resolved_status = ProductionSummaryStatus(status)
        preserved = {
            key: value for key, value in self.artifacts.items() if Path(value).is_file()
        }
        auto_missing = {
            key: {"path": value, "status": "missing", "reason": "recorded artifact is missing"}
            for key, value in self.artifacts.items() if not Path(value).is_file()
        }
        payload = {
            "status": resolved_status.value,
            "current_stage": self.stage.value,
            "last_successful_stage": self.last_successful_stage,
            "failed_stage": failed_stage,
            "error_category": error_category,
            "safe_error_message": safe_error_message,
            "external_submission_counts": self.counts,
            "generated_artifact_paths": self.artifacts,
            "preserved_artifact_paths": preserved,
            "invalid_or_missing_artifact_paths": {**auto_missing, **self.artifact_issues},
            "resumable": resumable,
            "recommended_resume_action": recommended,
        }
        atomic_write_json(self.summary_path, payload)

    def fail(self, failed_stage: str, exc: BaseException) -> None:
        status, category = classify_error(exc)
        self.stage = ProductionStage.failed
        self.write_summary(status, resumable=True,
                           recommended=f"resume from {failed_stage} after resolving {category}",
                           failed_stage=failed_stage, error_category=category,
                           safe_error_message=_safe_message(exc))


def evaluate_resume(job_dir: Path, config: ProductionConfig) -> dict[str, Any]:
    job_dir = Path(job_dir)
    result = {"compatible": False, "artifacts": {}, "resume_stage": "initialized",
              "reasons": [], "estimated_gemini_requests": 1, "estimated_image_requests": 7,
              "render_required": True}
    manifest_path = job_dir / "production_manifest.json"
    if not manifest_path.is_file():
        result["reasons"].append("production manifest missing")
        return result
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        result["reasons"].append("production manifest invalid")
        return result
    if manifest.get("recipe_fingerprint") != production_fingerprint(config):
        result["reasons"].append("recipe fingerprint mismatch")
        return result
    result["compatible"] = True
    checks = (
        ("plan", "plan.json", "planned"),
        ("raw_narration", "assets/narration_raw.wav", "narration_ready"),
        ("normalized_narration", "assets/narration.wav", "narration_ready"),
        ("alignment", "alignment_metadata.json", "aligned"),
        ("mixed_audio", "assets/final_mixed_audio.m4a", "music_ready"),
        ("silent_video", "_render/silent_video.mp4", "rendered"),
        ("final_video", "video.mp4", "completed"),
    )
    metadata_hashes = manifest.get("artifact_hashes", {})
    upstream_valid = True
    for key, relative, stage in checks:
        path = job_dir / relative
        expected = metadata_hashes.get(key, "")
        own_hash_valid = path.is_file() and bool(expected) and file_sha256(path) == expected
        valid = own_hash_valid and upstream_valid
        if key in {"alignment", "mixed_audio", "silent_video", "final_video"} and not upstream_valid:
            reason = "upstream artifact invalid"
        else:
            reason = "hash matched" if valid else "missing or hash mismatch"
        result["artifacts"][key] = {"path": str(path), "valid": valid,
                                    "reason": reason}
        if valid:
            result["resume_stage"] = stage
        if key in {"plan", "raw_narration", "normalized_narration", "alignment", "mixed_audio", "silent_video"}:
            upstream_valid = upstream_valid and valid
    image_valid = True
    for item in manifest.get("image_artifacts", []):
        path = job_dir / item.get("path", "")
        if not path.is_file() or file_sha256(path) != item.get("sha256"):
            image_valid = False
    result["artifacts"]["images"] = {"valid": image_valid and len(manifest.get("image_artifacts", [])) == 7,
                                      "reason": "seven hashes matched" if image_valid else "image hash mismatch"}
    if result["artifacts"]["images"]["valid"]:
        result["estimated_image_requests"] = 0
    else:
        for key in ("silent_video", "final_video"):
            if key in result["artifacts"]:
                result["artifacts"][key]["valid"] = False
                result["artifacts"][key]["reason"] = "image stage invalid"
    if result["artifacts"].get("raw_narration", {}).get("valid"):
        result["estimated_gemini_requests"] = 0
    qc = manifest.get("qc_results", {})
    final = result["artifacts"].get("final_video", {})
    qc_valid = qc.get("audio") == "passed" and qc.get("video") == "passed"
    if final.get("valid") and not qc_valid:
        final["valid"] = False
        final["reason"] = "audio and video QC passes are required"
    result["render_required"] = not bool(final.get("valid") and qc_valid)
    return result


def dry_run_envelope(config: ProductionConfig, job_dir: Path, *, resume: bool) -> dict[str, Any]:
    decision = evaluate_resume(job_dir, config) if resume else {
        "compatible": False, "artifacts": {}, "resume_stage": "initialized",
        "reasons": ["fresh job"], "estimated_gemini_requests": 1,
        "estimated_image_requests": config.max_image_requests, "render_required": True,
    }
    return {
        "recipe_id": config.recipe_id, "recipe_version": config.recipe_version,
        "production_schema_version": config.schema_version,
        "planner": config.planner_version,
        "voice_profile": config.voice_profile, "provider": config.provider,
        "model": config.model, "voice": config.voice, "style": config.style,
        "maximum_gemini_requests": config.max_tts_requests,
        "maximum_image_requests": config.max_image_requests,
        "retry_policy": "no retries", "fallback_policy": "no provider or model fallback",
        "alignment_mode": config.alignment_mode, "music_track": config.music_track,
        "music_profile": config.music_profile, "expected_output_directory": str(job_dir),
        "resume_evaluation": decision,
        "estimated_requests_after_resume": {
            "gemini": decision["estimated_gemini_requests"],
            "images": decision["estimated_image_requests"],
        },
        "render_required": decision["render_required"],
        "external_calls_performed": 0, "render_operations_performed": 0,
    }


def apply_sentence_alignment(plan: Any, job_dir: Path, config: ProductionConfig) -> dict[str, Any]:
    """Apply the shared local aligner to seven production narration units."""
    from tella.tts.sentence_alignment import AlignmentConfig, align_sentences

    scenes = [scene for scene in plan.scenes if scene.kind == "scene"]
    if len(scenes) != config.scene_count:
        raise ValueError("production alignment requires exactly seven scenes")
    narration = Path(plan.narration_audio_path)
    result = align_sentences(
        narration,
        [scene.voice_script for scene in scenes],
        total_duration=plan.narration_duration,
        config=AlignmentConfig(
            search_window_seconds=config.alignment_search_window_seconds,
            minimum_scene_duration=config.alignment_minimum_scene_duration,
        ),
    )
    for scene, interval in zip(scenes, result["scene_intervals"]):
        scene.start = interval["start"]
        scene.duration = interval["duration"]
        scene.audio_duration = interval["duration"]
    plan.total_duration = result["audio_duration"]
    metadata = {
        **result,
        "alignment_mode": config.alignment_mode,
        "asr_enabled": False,
        "manual_overrides": [],
        "subtitle_intervals_equal_scene_intervals": True,
    }
    job_dir = Path(job_dir)
    (job_dir / "alignment_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (job_dir / "alignment_boundaries.json").write_text(
        json.dumps({
            "boundaries": result["boundaries"],
            "diagnostics": result["boundary_diagnostics"],
        }, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return metadata


__all__ = [
    "CALLIRRHOE_PRODUCTION_CONFIG", "CALLIRRHOE_RECIPE_ID", "PRODUCTION_SCHEMA_VERSION",
    "LocalTTSCache", "ProductionConfig", "ProductionRun", "ProductionStage",
    "ProductionSummaryStatus", "classify_error",
    "apply_sentence_alignment", "dry_run_envelope", "evaluate_resume", "file_sha256", "get_production_config",
    "production_fingerprint", "stable_hash", "tts_cache_key",
]
