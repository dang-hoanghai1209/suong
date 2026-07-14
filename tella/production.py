"""Versioned production contracts, state, caching, and safe resume decisions."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from tella.atomic_write import atomic_write_json
from tella._voice_pace import normalize_voice_rate

PRODUCTION_SCHEMA_VERSION = 1
CALLIRRHOE_RECIPE_ID = "practical_life_steps_callirrhoe_v1"
_SUBMISSION_KEYS = ("gemini", "edge", "image_provider", "retries", "fallbacks")
_TRANSPORT_KEYS = ("gemini", "image_provider")
_RESULT_PROVIDERS = ("gemini", "image_provider")


def _zero_submissions() -> dict[str, int]:
    return {key: 0 for key in _SUBMISSION_KEYS}


def _zero_transport_attempts() -> dict[str, int]:
    return {key: 0 for key in _TRANSPORT_KEYS}


def _zero_provider_results() -> dict[str, dict[str, int]]:
    return {
        provider: {"successful": 0, "failed": 0}
        for provider in _RESULT_PROVIDERS
    }


def _nonnegative_counts(value: Any, keys: tuple[str, ...]) -> dict[str, int]:
    source = value if isinstance(value, dict) else {}
    result: dict[str, int] = {}
    for key in keys:
        try:
            result[key] = max(0, int(source.get(key) or 0))
        except (TypeError, ValueError):
            # Accounting fields never grant artifact reuse.  Treat malformed
            # legacy counters as absent while artifact identity still fails
            # closed through its independent hash validation.
            result[key] = 0
    return result


def _invocation_limits(
    config: "ProductionConfig",
    *,
    max_tts_requests: int | None = None,
    max_image_requests: int | None = None,
) -> dict[str, int]:
    return {
        "gemini": (
            config.max_tts_requests
            if max_tts_requests is None
            else max(0, int(max_tts_requests))
        ),
        "edge": 0,
        "image_provider": (
            config.max_image_requests
            if max_image_requests is None
            else max(0, int(max_image_requests))
        ),
        "retries": 0,
        "fallbacks": 0,
    }


def image_request_accounting(plan: Any) -> dict[str, int]:
    """Reconstruct image request facts from persisted per-scene evidence."""
    scenes = [
        scene for scene in getattr(plan, "scenes", [])
        if getattr(scene, "kind", "scene") == "scene"
    ]
    submissions = sum(
        max(0, int(getattr(scene, "provider_request_count_for_scene", 0) or 0))
        for scene in scenes
    )
    transport_attempts = sum(
        max(0, int(getattr(scene, "actual_cloudflare_request_count_for_scene", 0) or 0))
        for scene in scenes
    )
    successful_responses = sum(
        max(0, int(getattr(scene, "ai_images_generated", 0) or 0))
        for scene in scenes
    )
    return {
        "submissions": submissions,
        "transport_attempts": transport_attempts,
        "successful": successful_responses,
        "failed": max(0, transport_attempts - successful_responses),
    }


def _persisted_accounting(job_dir: Path) -> dict[str, Any]:
    """Read counters without mutating a production job."""
    job_dir = Path(job_dir)
    manifest: dict[str, Any] = {}
    summary: dict[str, Any] = {}
    plan_data: dict[str, Any] = {}
    for path, target in (
        (job_dir / "production_manifest.json", manifest),
        (job_dir / "production_summary.json", summary),
        (job_dir / "plan.json", plan_data),
    ):
        if not path.is_file():
            continue
        try:
            target.update(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    manifest_submissions = _nonnegative_counts(
        manifest.get("external_submission_counts"), _SUBMISSION_KEYS
    )
    summary_submissions = _nonnegative_counts(
        summary.get("external_submission_counts"), _SUBMISSION_KEYS
    )
    submissions = {
        key: max(manifest_submissions[key], summary_submissions[key])
        for key in _SUBMISSION_KEYS
    }
    manifest_transport = _nonnegative_counts(
        manifest.get("external_transport_attempt_counts"), _TRANSPORT_KEYS
    )
    summary_transport = _nonnegative_counts(
        summary.get("external_transport_attempt_counts"), _TRANSPORT_KEYS
    )
    transport = {
        key: max(manifest_transport[key], summary_transport[key])
        for key in _TRANSPORT_KEYS
    }
    results = _zero_provider_results()
    manifest_results = manifest.get("provider_result_counts") or {}
    summary_results = summary.get("provider_result_counts") or {}
    for provider in _RESULT_PROVIDERS:
        from_manifest = _nonnegative_counts(
            manifest_results.get(provider), ("successful", "failed")
        )
        from_summary = _nonnegative_counts(
            summary_results.get(provider), ("successful", "failed")
        )
        results[provider] = {
            key: max(from_manifest[key], from_summary[key])
            for key in ("successful", "failed")
        }
    if plan_data:
        from tella.planner.models import TellaScenePlan
        try:
            plan = TellaScenePlan.model_validate(plan_data)
        except Exception:
            plan = None
        if plan is not None:
            images = image_request_accounting(plan)
            submissions["image_provider"] = max(
                submissions["image_provider"], images["submissions"]
            )
            transport["image_provider"] = max(
                transport["image_provider"], images["transport_attempts"]
            )
            results["image_provider"]["successful"] = max(
                results["image_provider"]["successful"], images["successful"]
            )
            results["image_provider"]["failed"] = max(
                results["image_provider"]["failed"], images["failed"]
            )
    if not transport["gemini"]:
        transport["gemini"] = submissions["gemini"]
    if (
        submissions["gemini"]
        and summary.get("failed_stage") == ProductionStage.narration_ready.value
        and summary.get("status") in {
            ProductionSummaryStatus.provider_failure.value,
            ProductionSummaryStatus.quota_failure.value,
        }
    ):
        results["gemini"]["failed"] = max(
            results["gemini"]["failed"], 1
        )
    return {
        "submissions": submissions,
        "transport_attempts": transport,
        "results": results,
    }


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
    model_config = ConfigDict(frozen=True, validate_default=True)
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

    @field_validator("voice_rate")
    @classmethod
    def _normalize_voice_rate(cls, value: str) -> str:
        return normalize_voice_rate(value)


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


def _canonical_config_dump(config: ProductionConfig) -> dict[str, Any]:
    payload = config.model_dump()
    payload["voice_rate"] = normalize_voice_rate(payload["voice_rate"])
    return payload


def production_fingerprint(config: ProductionConfig) -> str:
    return stable_hash(_canonical_config_dump(config))


def _legacy_neutral_rate_fingerprint(config: ProductionConfig) -> str:
    payload = _canonical_config_dump(config)
    if payload["voice_rate"] == "+0%":
        payload["voice_rate"] = "0%"
    return stable_hash(payload)


def _fingerprint_compatibility(
    manifest: dict[str, Any], config: ProductionConfig
) -> tuple[bool, str]:
    recorded = str(manifest.get("recipe_fingerprint") or "")
    expected_recipe = _canonical_config_dump(config)
    manifest_recipe = manifest.get("recipe")
    if not isinstance(manifest_recipe, dict):
        return False, "manifest recipe missing"
    canonical_manifest_recipe = dict(manifest_recipe)
    try:
        canonical_manifest_recipe["voice_rate"] = normalize_voice_rate(
            canonical_manifest_recipe.get("voice_rate")
        )
    except ValueError:
        return False, "manifest recipe voice rate invalid"
    if canonical_manifest_recipe != expected_recipe:
        return False, "manifest recipe configuration mismatch"
    if recorded == production_fingerprint(config):
        return True, "canonical"
    if (
        expected_recipe["voice_rate"] == "+0%"
        and recorded == _legacy_neutral_rate_fingerprint(config)
        and manifest_recipe.get("voice_rate") == "0%"
    ):
        return True, "legacy_neutral_rate_v1"
    return False, "recipe fingerprint mismatch"


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
    if isinstance(exc, (KeyboardInterrupt, InterruptedError, SystemExit)):
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


_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|credential|access[_-]?token)"
    r"\s*[:=]?\s*\S+"
)


def _safe_message(exc: BaseException) -> str:
    text = str(exc)
    secret_values = (
        value for name, value in os.environ.items()
        if value and any(marker in name.upper() for marker in (
            "KEY", "TOKEN", "AUTHORIZATION", "CREDENTIAL",
        ))
    )
    if _SECRET_RE.search(text) or any(value in text for value in secret_values):
        return "operation failed; credential-bearing details redacted"
    return text[:500]


def validate_production_voice_configuration(
    config: ProductionConfig,
    resolution: Any | None = None,
    recipe: Any | None = None,
) -> str:
    """Validate shared production planner/voice settings without provider work."""
    effective_rate = normalize_voice_rate(config.voice_rate)
    if resolution is not None and resolution.resolved_voice_rate:
        resolved_rate = normalize_voice_rate(resolution.resolved_voice_rate)
        if resolved_rate != effective_rate:
            raise ValueError(
                "resolved voice rate does not match the production recipe rate"
            )
    if recipe is not None:
        expected = {
            "recipe_id": config.recipe_id,
            "recipe_version": config.recipe_version,
            "planner_id": config.planner_id,
            "voice_profile_id": config.voice_profile,
            "minimum_scene_count": config.scene_count,
            "maximum_scene_count": config.scene_count,
        }
        mismatches = [
            field for field, value in expected.items()
            if getattr(recipe, field, None) != value
        ]
        if mismatches:
            raise ValueError(
                "production recipe configuration mismatch: "
                + ", ".join(mismatches)
            )
    return effective_rate


class ProductionRun:
    def __init__(
        self,
        job_dir: Path,
        config: ProductionConfig,
        *,
        resume: bool = False,
        script_identity: dict[str, Any] | None = None,
        max_tts_requests: int | None = None,
        max_image_requests: int | None = None,
    ):
        self.job_dir = Path(job_dir)
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.resume = resume
        self.script_identity = dict(script_identity or {})
        self.stage = ProductionStage.initialized
        self.last_successful_stage = ""
        persisted = _persisted_accounting(self.job_dir) if resume else {
            "submissions": _zero_submissions(),
            "transport_attempts": _zero_transport_attempts(),
            "results": _zero_provider_results(),
        }
        self.counts = dict(persisted["submissions"])
        self.transport_attempts = dict(persisted["transport_attempts"])
        self.provider_results = {
            key: dict(value) for key, value in persisted["results"].items()
        }
        self._accounting_baseline = {
            "submissions": dict(self.counts),
            "transport_attempts": dict(self.transport_attempts),
            "results": {
                key: dict(value) for key, value in self.provider_results.items()
            },
        }
        self.invocation_counts = _zero_submissions()
        self.invocation_transport_attempts = _zero_transport_attempts()
        self.invocation_provider_results = _zero_provider_results()
        self.invocation_limits = _invocation_limits(
            config,
            max_tts_requests=max_tts_requests,
            max_image_requests=max_image_requests,
        )
        self.artifacts: dict[str, str] = {}
        self.artifact_issues: dict[str, dict[str, str]] = {}
        if resume and self.summary_path.is_file():
            try:
                previous = json.loads(self.summary_path.read_text(encoding="utf-8"))
                self.last_successful_stage = str(
                    previous.get("last_successful_stage") or ""
                )
                for key, value in previous.get("generated_artifact_paths", {}).items():
                    self.artifacts[str(key)] = str(value)
            except (OSError, json.JSONDecodeError):
                pass
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
            "recipe": _canonical_config_dump(self.config),
            "recipe_fingerprint": production_fingerprint(self.config),
            "created_or_updated": datetime.now(timezone.utc).isoformat(),
            "resume_requested": self.resume,
            "external_submission_counts": self.counts,
            "external_transport_attempt_counts": self.transport_attempts,
            "provider_result_counts": self.provider_results,
            "current_invocation_submission_counts": self.invocation_counts,
            "current_invocation_transport_attempt_counts": self.invocation_transport_attempts,
            "current_invocation_provider_result_counts": self.invocation_provider_results,
            "current_invocation_request_limits": self.invocation_limits,
            "current_invocation_remaining_budget": self.remaining_invocation_budget(),
            **(
                {"canonical_script_identity": self.script_identity}
                if self.script_identity else {}
            ),
            **(extra or {}),
        }
        atomic_write_json(self.manifest_path, payload)

    def _persist_accounting_manifest(self) -> None:
        if not self.manifest_path.is_file():
            return
        current = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        current["external_submission_counts"] = dict(self.counts)
        current["external_transport_attempt_counts"] = dict(
            self.transport_attempts
        )
        current["provider_result_counts"] = {
            key: dict(value) for key, value in self.provider_results.items()
        }
        current["current_invocation_submission_counts"] = dict(
            self.invocation_counts
        )
        current["current_invocation_transport_attempt_counts"] = dict(
            self.invocation_transport_attempts
        )
        current["current_invocation_provider_result_counts"] = {
            key: dict(value)
            for key, value in self.invocation_provider_results.items()
        }
        current["current_invocation_request_limits"] = dict(
            self.invocation_limits
        )
        current["current_invocation_remaining_budget"] = (
            self.remaining_invocation_budget()
        )
        atomic_write_json(self.manifest_path, current)

    def remaining_invocation_budget(self) -> dict[str, int]:
        return {
            key: max(0, maximum - self.invocation_counts.get(key, 0))
            for key, maximum in self.invocation_limits.items()
        }

    def record_submission(
        self, provider: str, *, transport_attempts: int = 1
    ) -> None:
        if provider not in {"gemini", "edge", "image_provider"}:
            raise ValueError(f"unsupported production provider counter: {provider}")
        maximum = self.invocation_limits[provider]
        used = self.invocation_counts[provider]
        if used >= maximum:
            raise RuntimeError(
                f"{provider} request budget exhausted for current invocation: "
                f"used={used}, maximum={maximum}"
            )
        self.invocation_counts[provider] += 1
        self.counts[provider] = self.counts.get(provider, 0) + 1
        if provider in self.transport_attempts:
            attempts = max(0, int(transport_attempts))
            self.transport_attempts[provider] += attempts
            self.invocation_transport_attempts[provider] += attempts
        self._persist_accounting_manifest()

    def record_provider_result(self, provider: str, *, successful: bool) -> None:
        if provider not in self.provider_results:
            raise ValueError(f"unsupported production provider result: {provider}")
        key = "successful" if successful else "failed"
        self.provider_results[provider][key] += 1
        self.invocation_provider_results[provider][key] += 1
        self._persist_accounting_manifest()

    def record_image_stage(self, plan: Any, plan_path: Path) -> list[Path]:
        """Persist image-stage counts and hashes before later stages can fail."""
        accounting = image_request_accounting(plan)
        if accounting["submissions"] > self.invocation_limits["image_provider"]:
            raise RuntimeError(
                "image_provider request budget exhausted for current invocation: "
                f"used={accounting['submissions']}, "
                f"maximum={self.invocation_limits['image_provider']}"
            )
        baseline = self._accounting_baseline
        self.counts["image_provider"] = (
            baseline["submissions"]["image_provider"]
            + accounting["submissions"]
        )
        self.invocation_counts["image_provider"] = accounting["submissions"]
        self.transport_attempts["image_provider"] = (
            baseline["transport_attempts"]["image_provider"]
            + accounting["transport_attempts"]
        )
        self.invocation_transport_attempts["image_provider"] = accounting[
            "transport_attempts"
        ]
        for key in ("successful", "failed"):
            self.provider_results["image_provider"][key] = (
                baseline["results"]["image_provider"][key] + accounting[key]
            )
            self.invocation_provider_results["image_provider"][key] = accounting[key]
        images: list[Path] = []
        for scene in getattr(plan, "scenes", []):
            if getattr(scene, "kind", "scene") != "scene":
                continue
            relative = str(getattr(scene, "asset_path", "") or "")
            if not relative:
                continue
            path = self.job_dir / relative
            if path.is_file():
                images.append(path)
        self.artifacts["plan"] = str(plan_path)
        for index, path in enumerate(images, start=1):
            self.artifacts[f"image_{index}"] = str(path)
        self.record_artifact_hashes(
            {"plan": Path(plan_path)}, image_artifacts=images
        )
        self._persist_accounting_manifest()
        return images

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
        hashes = dict(current.get("artifact_hashes") or {})
        hashes.update({
            key: file_sha256(path) for key, path in artifacts.items() if path.is_file()
        })
        current["artifact_hashes"] = hashes
        if image_artifacts is not None:
            current["image_artifacts"] = [
                {
                    "path": str(path.relative_to(self.job_dir)),
                    "sha256": file_sha256(path),
                }
                for path in image_artifacts
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
                      safe_error_message: str = "",
                      persist_manifest: bool = True) -> None:
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
            "external_transport_attempt_counts": self.transport_attempts,
            "provider_result_counts": self.provider_results,
            "current_invocation_submission_counts": self.invocation_counts,
            "current_invocation_transport_attempt_counts": self.invocation_transport_attempts,
            "current_invocation_provider_result_counts": self.invocation_provider_results,
            "current_invocation_request_limits": self.invocation_limits,
            "current_invocation_remaining_budget": self.remaining_invocation_budget(),
            "generated_artifact_paths": self.artifacts,
            "preserved_artifact_paths": preserved,
            "invalid_or_missing_artifact_paths": {**auto_missing, **self.artifact_issues},
            "resumable": resumable,
            "recommended_resume_action": recommended,
            "resume_requested": self.resume,
            "completed_from_resume": (
                self.resume
                and resolved_status == ProductionSummaryStatus.completed
            ),
        }
        if persist_manifest:
            self._persist_accounting_manifest()
        atomic_write_json(self.summary_path, payload)

    def finalize_completed(
        self,
        *,
        plan_path: Path,
        plan_data: dict[str, Any],
        artifacts: dict[str, Path],
        image_artifacts: list[Path],
        qc_results: dict[str, str],
    ) -> None:
        """Persist stable plan, manifest, then completed summary in that order."""
        atomic_write_json(plan_path, plan_data)
        stable_artifacts = {"plan": Path(plan_path), **artifacts}
        missing = [key for key, path in stable_artifacts.items() if not path.is_file()]
        if missing:
            raise RuntimeError(
                "completed production artifacts are missing: " + ", ".join(missing)
            )
        if len(image_artifacts) != self.config.scene_count or any(
            not path.is_file() for path in image_artifacts
        ):
            raise RuntimeError("completed production requires seven image artifacts")

        current = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        current.update({
            "created_or_updated": datetime.now(timezone.utc).isoformat(),
            "resume_requested": self.resume,
            "completed_from_resume": self.resume,
            "completion_state": ProductionSummaryStatus.completed.value,
            "external_submission_counts": dict(self.counts),
            "external_transport_attempt_counts": dict(self.transport_attempts),
            "provider_result_counts": {
                key: dict(value) for key, value in self.provider_results.items()
            },
            "current_invocation_submission_counts": dict(self.invocation_counts),
            "current_invocation_transport_attempt_counts": dict(
                self.invocation_transport_attempts
            ),
            "current_invocation_provider_result_counts": {
                key: dict(value)
                for key, value in self.invocation_provider_results.items()
            },
            "current_invocation_request_limits": dict(self.invocation_limits),
            "current_invocation_remaining_budget": self.remaining_invocation_budget(),
            "artifact_hashes": {
                key: file_sha256(path) for key, path in stable_artifacts.items()
            },
            "image_artifacts": [
                {
                    "path": str(path.relative_to(self.job_dir)),
                    "sha256": file_sha256(path),
                }
                for path in image_artifacts
            ],
            "qc_results": dict(qc_results),
        })
        atomic_write_json(self.manifest_path, current)

        self.stage = ProductionStage.completed
        self.last_successful_stage = ProductionStage.completed.value
        for key, path in stable_artifacts.items():
            self.artifacts[key] = str(path)
        self.write_summary(
            ProductionSummaryStatus.completed.value,
            resumable=False,
            recommended="none",
            persist_manifest=False,
        )

    def fail(self, failed_stage: str, exc: BaseException) -> None:
        status, category = classify_error(exc)
        self.stage = ProductionStage.failed
        resumable = status != ProductionSummaryStatus.validation_failure.value
        self.write_summary(status, resumable=resumable,
                           recommended=(
                               f"resume from {failed_stage} after resolving {category}"
                               if resumable else
                               f"correct {category} before starting a new production job"
                           ),
                           failed_stage=failed_stage, error_category=category,
                           safe_error_message=_safe_message(exc))


def _completed_artifact_paths(job_dir: Path) -> dict[str, Path]:
    job_dir = Path(job_dir)
    return {
        "plan": job_dir / "plan.json",
        "raw_narration": job_dir / "assets" / "narration_raw.wav",
        "normalized_narration": job_dir / "assets" / "narration.wav",
        "alignment": job_dir / "alignment_metadata.json",
        "alignment_boundaries": job_dir / "alignment_boundaries.json",
        "tts_metadata": job_dir / "tts_metadata.json",
        "recipe": job_dir / "recipe.json",
        "music_metadata": job_dir / "music_metadata.json",
        "audio_qc": job_dir / "audio_qc.json",
        "prepared_music": job_dir / "_render" / "music_prepared.wav",
        "silent_video": job_dir / "_render" / "silent_video.mp4",
        "final_video": job_dir / "video.mp4",
        "video_qc": job_dir / "video_qc.json",
    }


def validate_completed_job_integrity(
    job_dir: Path,
    config: ProductionConfig,
) -> dict[str, Any]:
    """Validate completed metadata and every locally retained final artifact."""
    job_dir = Path(job_dir)
    manifest = json.loads(
        (job_dir / "production_manifest.json").read_text(encoding="utf-8")
    )
    summary = json.loads(
        (job_dir / "production_summary.json").read_text(encoding="utf-8")
    )
    errors: list[str] = []
    if manifest.get("recipe_fingerprint") != production_fingerprint(config):
        errors.append("recipe fingerprint mismatch")
    if manifest.get("completion_state") != ProductionSummaryStatus.completed.value:
        errors.append("manifest completion state mismatch")
    if summary.get("status") != ProductionSummaryStatus.completed.value:
        errors.append("summary status mismatch")
    if summary.get("current_stage") != ProductionStage.completed.value:
        errors.append("summary current stage mismatch")
    if summary.get("last_successful_stage") != ProductionStage.completed.value:
        errors.append("summary last successful stage mismatch")
    if manifest.get("resume_requested") != summary.get("resume_requested"):
        errors.append("manifest and summary resume state mismatch")
    if manifest.get("completed_from_resume") != summary.get("completed_from_resume"):
        errors.append("manifest and summary completion origin mismatch")

    artifacts = _completed_artifact_paths(job_dir)
    recorded = manifest.get("artifact_hashes") or {}
    for key, path in artifacts.items():
        if not path.is_file():
            errors.append(f"completed artifact missing: {key}")
        elif recorded.get(key) != file_sha256(path):
            errors.append(f"completed artifact hash mismatch: {key}")
    images = manifest.get("image_artifacts") or []
    if len(images) != config.scene_count:
        errors.append("completed image count mismatch")
    else:
        for item in images:
            path = job_dir / str(item.get("path") or "")
            if not path.is_file() or item.get("sha256") != file_sha256(path):
                errors.append(f"completed image hash mismatch: {item.get('path')}")
    for qc_name in ("audio_qc.json", "video_qc.json"):
        path = job_dir / qc_name
        if path.is_file():
            qc = json.loads(path.read_text(encoding="utf-8"))
            if qc.get("status") != "passed":
                errors.append(f"{qc_name} did not pass")
    return {
        "valid": not errors,
        "errors": errors,
        "resume_requested": manifest.get("resume_requested"),
        "completed_from_resume": manifest.get("completed_from_resume"),
        "artifact_hashes": {
            key: file_sha256(path) for key, path in artifacts.items() if path.is_file()
        },
    }


def repair_completed_job_metadata(
    job_dir: Path,
    config: ProductionConfig,
    *,
    completed_from_resume: bool,
) -> dict[str, Any]:
    """Repair metadata only after validating every retained local artifact."""
    job_dir = Path(job_dir)
    manifest_path = job_dir / "production_manifest.json"
    summary_path = job_dir / "production_summary.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if manifest.get("recipe_fingerprint") != production_fingerprint(config):
        raise RuntimeError("metadata repair refused: recipe fingerprint mismatch")
    if (
        summary.get("status") != ProductionSummaryStatus.completed.value
        or summary.get("current_stage") != ProductionStage.completed.value
        or summary.get("last_successful_stage") != ProductionStage.completed.value
    ):
        raise RuntimeError("metadata repair refused: job is not completed")
    if (manifest.get("qc_results") or {}) != {"audio": "passed", "video": "passed"}:
        raise RuntimeError("metadata repair refused: manifest QC did not pass")

    artifacts = _completed_artifact_paths(job_dir)
    missing = [key for key, path in artifacts.items() if not path.is_file()]
    if missing:
        raise RuntimeError(
            "metadata repair refused: completed artifacts missing: "
            + ", ".join(missing)
        )
    old_hashes = manifest.get("artifact_hashes") or {}
    for key, digest in old_hashes.items():
        if key == "plan":
            continue
        path = artifacts.get(key)
        if path is None or not path.is_file() or file_sha256(path) != digest:
            raise RuntimeError(
                f"metadata repair refused: existing artifact binding failed: {key}"
            )
    images = manifest.get("image_artifacts") or []
    if len(images) != config.scene_count:
        raise RuntimeError("metadata repair refused: seven image hashes are required")
    for item in images:
        path = job_dir / str(item.get("path") or "")
        if not path.is_file() or file_sha256(path) != item.get("sha256"):
            raise RuntimeError("metadata repair refused: image hash mismatch")

    audio_qc = json.loads((job_dir / "audio_qc.json").read_text(encoding="utf-8"))
    video_qc = json.loads((job_dir / "video_qc.json").read_text(encoding="utf-8"))
    alignment = json.loads(
        (job_dir / "alignment_metadata.json").read_text(encoding="utf-8")
    )
    music = json.loads((job_dir / "music_metadata.json").read_text(encoding="utf-8"))
    if audio_qc.get("status") != "passed" or video_qc.get("status") != "passed":
        raise RuntimeError("metadata repair refused: local QC did not pass")
    if alignment.get("wav_sha256") != file_sha256(artifacts["normalized_narration"]):
        raise RuntimeError("metadata repair refused: alignment WAV binding mismatch")
    if (
        music.get("selected_track") != config.music_track
        or music.get("music_profile_id") != config.music_profile
        or music.get("qc_result") != "passed"
    ):
        raise RuntimeError("metadata repair refused: music identity mismatch")

    accounting_before = _persisted_accounting(job_dir)
    artifact_hashes = {
        key: file_sha256(path) for key, path in artifacts.items()
    }
    manifest.update({
        "created_or_updated": datetime.now(timezone.utc).isoformat(),
        "resume_requested": bool(completed_from_resume),
        "completed_from_resume": bool(completed_from_resume),
        "completion_state": ProductionSummaryStatus.completed.value,
        "artifact_hashes": artifact_hashes,
    })
    summary.update({
        "status": ProductionSummaryStatus.completed.value,
        "current_stage": ProductionStage.completed.value,
        "last_successful_stage": ProductionStage.completed.value,
        "failed_stage": "",
        "error_category": "",
        "safe_error_message": "",
        "resumable": False,
        "recommended_resume_action": "none",
        "resume_requested": bool(completed_from_resume),
        "completed_from_resume": bool(completed_from_resume),
    })
    generated = dict(summary.get("generated_artifact_paths") or {})
    generated.update({key: str(path) for key, path in artifacts.items()})
    summary["generated_artifact_paths"] = generated
    summary["preserved_artifact_paths"] = dict(generated)
    summary["invalid_or_missing_artifact_paths"] = {}
    atomic_write_json(manifest_path, manifest)
    atomic_write_json(summary_path, summary)

    accounting_after = _persisted_accounting(job_dir)
    if accounting_after != accounting_before:
        raise RuntimeError("metadata repair changed persisted request accounting")
    result = validate_completed_job_integrity(job_dir, config)
    if not result["valid"]:
        raise RuntimeError(
            "metadata repair did not produce a valid completed job: "
            + "; ".join(result["errors"])
        )
    return result


def record_unhandled_production_failure(
    job_dir: Path,
    config: ProductionConfig,
    exc: BaseException,
) -> None:
    """Complete an initialized production summary at the outer CLI boundary."""
    summary_path = Path(job_dir) / "production_summary.json"
    if not summary_path.is_file():
        return
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if summary.get("status") != ProductionSummaryStatus.partial_failure.value:
        return
    if summary.get("failed_stage") and summary.get("error_category"):
        return
    next_stage = {
        "": ProductionStage.recipe_resolved.value,
        ProductionStage.initialized.value: ProductionStage.recipe_resolved.value,
        ProductionStage.recipe_resolved.value: ProductionStage.planned.value,
        ProductionStage.planned.value: ProductionStage.images_ready.value,
        ProductionStage.images_ready.value: ProductionStage.narration_ready.value,
        ProductionStage.narration_ready.value: ProductionStage.aligned.value,
        ProductionStage.aligned.value: ProductionStage.music_ready.value,
        ProductionStage.music_ready.value: ProductionStage.rendered.value,
        ProductionStage.rendered.value: ProductionStage.qc_passed.value,
        ProductionStage.qc_passed.value: ProductionStage.completed.value,
    }
    last_successful = str(summary.get("last_successful_stage") or "")
    failed_stage = next_stage.get(last_successful, last_successful or "production")
    status, category = classify_error(exc)
    resumable = status != ProductionSummaryStatus.validation_failure.value
    summary.update({
        "status": status,
        "current_stage": ProductionStage.failed.value,
        "failed_stage": failed_stage,
        "error_category": category,
        "safe_error_message": _safe_message(exc),
        "resumable": resumable,
        "recommended_resume_action": (
            f"resume from {failed_stage} after resolving {category}"
            if resumable else
            f"correct {category} before starting a new production job"
        ),
    })
    atomic_write_json(summary_path, summary)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_INVENTORY_EXCLUSIONS = {".tella-job.lock", ".reuse_plan.json"}


def source_inventory_sha256(job_dir: Path) -> str:
    """Hash a canonical full-SHA256 inventory without mutating the job."""
    root = Path(job_dir).resolve()
    inventory: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        if relative in _INVENTORY_EXCLUSIONS:
            continue
        is_junction = getattr(path, "is_junction", lambda: False)
        if path.is_symlink() or bool(is_junction()) or not path.is_file():
            continue
        inventory.append({
            "path": relative,
            "size": path.stat().st_size,
            "sha256": file_sha256(path),
        })
    encoded = json.dumps(
        inventory, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_legacy_image_resume_attestation(
    job_dir: Path,
    config: ProductionConfig,
) -> dict[str, Any]:
    """Build, but never auto-write, the explicit legacy full-hash attestation."""
    job_dir = Path(job_dir)
    manifest = json.loads(
        (job_dir / "production_manifest.json").read_text(encoding="utf-8")
    )
    plan_data = json.loads((job_dir / "plan.json").read_text(encoding="utf-8"))
    scenes = [item for item in plan_data.get("scenes", []) if item.get("kind") == "scene"]
    images = []
    for scene in scenes:
        relative = Path(str(scene.get("asset_path") or ""))
        path = job_dir / relative
        images.append({
            "scene_index": int(scene.get("scene_index") or 0),
            "provider": str(scene.get("image_provider") or ""),
            "path": relative.as_posix(),
            "sha256": file_sha256(path),
        })
    identity = manifest.get("canonical_script_identity") or {}
    return {
        "schema_version": 1,
        "attestation_type": "legacy_image_resume_full_sha256",
        "job_id": job_dir.name,
        "recipe_fingerprint": production_fingerprint(config),
        "canonical_script_sha256": identity.get("canonical_script_sha256", ""),
        "plan_sha256": file_sha256(job_dir / "plan.json"),
        "source_inventory_sha256": source_inventory_sha256(job_dir),
        "images": images,
    }


_OPERATIONAL_ATTESTATION_TYPE = "production_resume_full_sha256"
_RESUME_IMPLEMENTATION_PATHS = (
    "tella/production.py",
    "tella/cli.py",
    "tella/tts/synth_all.py",
    "music/library.json",
)


def _artifact_binding(job_dir: Path, relative: str) -> dict[str, Any]:
    path = Path(job_dir) / relative
    if not path.is_file():
        raise ValueError(f"required resume artifact is missing: {relative}")
    return {
        "path": Path(relative).as_posix(),
        "size": path.stat().st_size,
        "sha256": file_sha256(path),
    }


def _resume_implementation_bindings() -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    return {
        relative: file_sha256(root / relative)
        for relative in _RESUME_IMPLEMENTATION_PATHS
    }


def build_production_resume_attestation(
    job_dir: Path,
    config: ProductionConfig,
) -> dict[str, Any]:
    """Build a complete, credential-free resume attestation without mutation."""
    job_dir = Path(job_dir)
    manifest = json.loads(
        (job_dir / "production_manifest.json").read_text(encoding="utf-8")
    )
    summary = json.loads(
        (job_dir / "production_summary.json").read_text(encoding="utf-8")
    )
    tts_metadata = json.loads(
        (job_dir / "tts_metadata.json").read_text(encoding="utf-8")
    )
    identity = manifest.get("canonical_script_identity") or {}
    provider_metadata = tts_metadata.get("provider_metadata") or {}
    expected_voice = {
        "provider": config.provider,
        "model": config.model,
        "voice": config.voice,
        "style": config.style,
        "language": config.tts_language,
    }
    actual_voice = {
        "provider": provider_metadata.get("provider"),
        "model": provider_metadata.get("model"),
        "voice": provider_metadata.get("voice"),
        "style": provider_metadata.get("style"),
        "language": provider_metadata.get("language"),
    }
    if actual_voice != expected_voice:
        raise ValueError("TTS metadata identity does not match production recipe")
    canonical_sentences = identity.get("canonical_script_sentences") or []
    canonical_narration = " ".join(str(item) for item in canonical_sentences)
    expected_narration_hash = hashlib.sha256(
        canonical_narration.encode("utf-8")
    ).hexdigest()
    if (
        not canonical_sentences
        or provider_metadata.get("source_narration_text_hash")
        != expected_narration_hash
        or provider_metadata.get("fallback_used") is not False
    ):
        raise ValueError("TTS metadata narration identity is not reusable")

    normalized_binding = _artifact_binding(job_dir, "assets/narration.wav")
    alignment_metadata = json.loads(
        (job_dir / "alignment_metadata.json").read_text(encoding="utf-8")
    )
    alignment_boundaries = json.loads(
        (job_dir / "alignment_boundaries.json").read_text(encoding="utf-8")
    )
    if (
        alignment_metadata.get("wav_sha256") != normalized_binding["sha256"]
        or alignment_metadata.get("boundaries")
        != alignment_boundaries.get("boundaries")
    ):
        raise ValueError("alignment metadata is not bound to normalized narration")
    stage_order = [stage.value for stage in ProductionStage]
    last_successful_stage = str(summary.get("last_successful_stage") or "")
    if (
        last_successful_stage not in stage_order
        or stage_order.index(last_successful_stage)
        < stage_order.index(ProductionStage.aligned.value)
    ):
        raise ValueError("production stage metadata does not permit aligned reuse")

    catalogue_path = Path(__file__).resolve().parents[1] / "music" / "library.json"
    catalogue = json.loads(catalogue_path.read_text(encoding="utf-8"))
    tracks = {
        str(item.get("track_id") or ""): item
        for item in catalogue.get("tracks", [])
        if isinstance(item, dict)
    }
    track = tracks.get(config.music_track)
    if not track or config.recipe_id not in track.get("supported_recipes", []):
        raise ValueError("selected production music track is not recipe-compatible")

    image_items = manifest.get("image_artifacts") or []
    if len(image_items) != config.scene_count:
        raise ValueError("production manifest must bind exactly seven images")
    images: list[dict[str, Any]] = []
    for index, item in enumerate(image_items, start=1):
        binding = _artifact_binding(job_dir, str(item.get("path") or ""))
        if binding["sha256"] != item.get("sha256"):
            raise ValueError(f"manifest image hash mismatch for scene {index}")
        images.append({"scene_index": index, "provider": "cloudflare", **binding})

    accounting = _persisted_accounting(job_dir)
    reuse_plan = _artifact_binding(job_dir, ".reuse_plan.json")
    return {
        "schema_version": 1,
        "attestation_type": _OPERATIONAL_ATTESTATION_TYPE,
        "attestation_revision": 1,
        "job_id": job_dir.name,
        "recipe_id": config.recipe_id,
        "recipe_version": config.recipe_version,
        "recipe_fingerprint": production_fingerprint(config),
        "resume_implementation": _resume_implementation_bindings(),
        "canonical_script_identity": identity,
        "source_inventory_sha256": source_inventory_sha256(job_dir),
        "plan": _artifact_binding(job_dir, "plan.json"),
        "reuse_plan": reuse_plan,
        "recipe": _artifact_binding(job_dir, "recipe.json"),
        "images": images,
        "narration": {
            "raw": _artifact_binding(job_dir, "assets/narration_raw.wav"),
            "normalized": normalized_binding,
            "metadata": _artifact_binding(job_dir, "tts_metadata.json"),
            "identity": expected_voice,
        },
        "alignment": {
            "metadata": _artifact_binding(job_dir, "alignment_metadata.json"),
            "boundaries": _artifact_binding(job_dir, "alignment_boundaries.json"),
        },
        "stage_identity": {
            "status": summary.get("status"),
            "last_successful_stage": summary.get("last_successful_stage"),
            "failed_stage": summary.get("failed_stage"),
        },
        "music": {
            "track_id": config.music_track,
            "mix_profile": config.music_profile,
            "catalogue_sha256": file_sha256(catalogue_path),
            "supported_recipe": config.recipe_id,
        },
        "request_accounting": accounting,
    }


def validate_production_resume_attestation(
    job_dir: Path,
    config: ProductionConfig,
    attestation_path: Path | None,
) -> tuple[bool, str, dict[str, Any]]:
    """Validate an explicit operational attestation against current local state."""
    if attestation_path is None:
        return False, "operational resume attestation is required", {}
    root = Path(job_dir).resolve()
    path = Path(attestation_path)
    is_junction = getattr(path, "is_junction", lambda: False)
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return False, "operational resume attestation is missing", {}
    if (
        path.is_symlink()
        or bool(is_junction())
        or not resolved.is_file()
        or resolved == root
        or root in resolved.parents
    ):
        return False, "operational resume attestation must be outside the job", {}
    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
        if data.get("attestation_type") != _OPERATIONAL_ATTESTATION_TYPE:
            return False, "operational resume attestation type mismatch", {}
        expected = build_production_resume_attestation(root, config)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return False, f"operational resume attestation invalid: {exc}", {}
    if data != expected:
        return False, "operational resume attestation does not match current state", {}
    return True, "operational full-SHA256 attestation matched", data


def _validate_legacy_attestation(
    job_dir: Path,
    config: ProductionConfig,
    manifest: dict[str, Any],
    candidate_images: list[dict[str, str]],
    attestation_path: Path | None,
) -> tuple[bool, str]:
    if attestation_path is None:
        return False, (
            "legacy image hashes are 16-character SHA256 prefixes; explicit "
            "versioned full-SHA256 resume attestation required"
        )
    path = Path(attestation_path)
    root = Path(job_dir).resolve()
    is_junction = getattr(path, "is_junction", lambda: False)
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return False, "legacy resume attestation missing"
    if (
        path.is_symlink()
        or bool(is_junction())
        or not resolved.is_file()
        or resolved == root
        or root in resolved.parents
    ):
        return False, "legacy resume attestation must be a regular file outside the source job"
    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "legacy resume attestation invalid"
    identity = manifest.get("canonical_script_identity") or {}
    expected_scalars = {
        "schema_version": 1,
        "attestation_type": "legacy_image_resume_full_sha256",
        "job_id": Path(job_dir).name,
        "recipe_fingerprint": production_fingerprint(config),
        "canonical_script_sha256": identity.get("canonical_script_sha256", ""),
        "plan_sha256": file_sha256(Path(job_dir) / "plan.json"),
        "source_inventory_sha256": source_inventory_sha256(job_dir),
    }
    for key, expected in expected_scalars.items():
        if data.get(key) != expected:
            return False, f"legacy resume attestation {key} mismatch"
    items = data.get("images")
    if not isinstance(items, list) or len(items) != config.scene_count:
        return False, "legacy resume attestation must contain exactly seven images"
    candidates = {item["path"]: item["sha256"] for item in candidate_images}
    seen_indices: list[int] = []
    for item in items:
        if not isinstance(item, dict):
            return False, "legacy resume attestation image entry invalid"
        relative = str(item.get("path") or "")
        digest = str(item.get("sha256") or "").lower()
        try:
            index = int(item.get("scene_index"))
        except (TypeError, ValueError):
            return False, "legacy resume attestation scene index invalid"
        seen_indices.append(index)
        if item.get("provider") != "cloudflare":
            return False, "legacy resume attestation provider mismatch"
        if not _SHA256_RE.fullmatch(digest) or candidates.get(relative) != digest:
            return False, "legacy resume attestation image hash mismatch"
    if seen_indices != list(range(1, config.scene_count + 1)):
        return False, "legacy resume attestation scene indices mismatch"
    return True, "explicit versioned full-SHA256 resume attestation matched"


def _validated_legacy_plan_images(
    job_dir: Path,
    manifest: dict[str, Any],
    config: ProductionConfig,
    attestation_path: Path | None = None,
) -> tuple[list[dict[str, str]], bool, str]:
    """Validate pre-accounting image evidence without changing the source job."""
    identity = manifest.get("canonical_script_identity")
    if not isinstance(identity, dict) or not identity.get("canonical_script_sha256"):
        return [], False, "manifest image hashes missing"
    plan_path = job_dir / "plan.json"
    if not plan_path.is_file():
        return [], False, "legacy plan missing"
    try:
        from tella.planner.models import TellaScenePlan
        plan = TellaScenePlan.model_validate_json(
            plan_path.read_text(encoding="utf-8")
        )
    except Exception:
        return [], False, "legacy plan invalid"
    if plan.recipe_id != config.recipe_id or plan.recipe_version != config.recipe_version:
        return [], False, "legacy plan recipe mismatch"
    plan_identity = {
        "acceptance_suite_id": plan.acceptance_suite_id,
        "acceptance_suite_path": plan.acceptance_suite_path,
        "acceptance_case_id": plan.acceptance_case_id,
        "script_version": plan.source_script_version,
        "script_path": plan.source_script_path,
        "canonical_script_sha256": plan.canonical_script_sha256,
        "script_scene_count": plan.source_script_scene_count,
    }
    for key, value in plan_identity.items():
        if identity.get(key) != value:
            return [], False, f"legacy plan {key} mismatch"
    scenes = [scene for scene in plan.scenes if scene.kind == "scene"]
    if [scene.scene_index for scene in scenes] != list(
        range(1, config.scene_count + 1)
    ):
        return [], False, "legacy plan scene indices mismatch"
    expected_sentences = identity.get("canonical_script_sentences")
    if not isinstance(expected_sentences, list) or [
        scene.voice_script for scene in scenes
    ] != expected_sentences:
        return [], False, "legacy plan narration mismatch"
    roles = [
        f"step_{scene.step_number}"
        if scene.scene_role == "practical_step"
        else scene.scene_role
        for scene in scenes
    ]
    if roles != identity.get("expected_scene_roles"):
        return [], False, "legacy plan role mismatch"
    items: list[dict[str, str]] = []
    total_submissions = 0
    total_transport = 0
    root = job_dir.resolve()
    for scene in scenes:
        if scene.asset_status != "done" or scene.image_provider != "cloudflare":
            return [], False, "legacy image provider evidence incomplete"
        if scene.ai_images_generated < 1:
            return [], False, "legacy image success evidence missing"
        submissions = max(0, int(scene.provider_request_count_for_scene))
        transport = max(0, int(scene.actual_cloudflare_request_count_for_scene))
        if submissions < 1 or transport < 1:
            return [], False, "legacy image request evidence missing"
        total_submissions += submissions
        total_transport += transport
        relative = Path(scene.asset_path)
        if relative.is_absolute() or ".." in relative.parts:
            return [], False, "legacy image path is unsafe"
        path = job_dir / relative
        is_junction = getattr(path, "is_junction", lambda: False)
        resolved = path.resolve()
        if (
            path.is_symlink()
            or bool(is_junction())
            or root not in resolved.parents
            or not resolved.is_file()
        ):
            return [], False, "legacy image artifact missing or aliased"
        actual = file_sha256(resolved)
        if not scene.asset_hash or actual[:16] != scene.asset_hash:
            return [], False, "legacy image hash mismatch"
        items.append({"path": relative.as_posix(), "sha256": actual})
    if (
        total_submissions != int(plan.ai_images_requested)
        or total_transport != int(plan.image_request_budget_used_at_finish)
    ):
        return [], False, "legacy image accounting totals mismatch"
    valid, reason = _validate_legacy_attestation(
        job_dir, config, manifest, items, attestation_path
    )
    return items, valid, reason


def evaluate_resume(
    job_dir: Path,
    config: ProductionConfig,
    expected_script_identity: dict[str, Any] | None = None,
    resume_attestation_path: Path | None = None,
) -> dict[str, Any]:
    job_dir = Path(job_dir)
    result = {"compatible": False, "artifacts": {}, "resume_stage": "initialized",
              "reasons": [], "estimated_gemini_requests": 1, "estimated_image_requests": 7,
              "render_required": True, "fingerprint_compatibility": "not_evaluated",
              "reusable_image_count": 0,
              "operational_attestation_accepted": False,
              "operational_attestation_reason": "not evaluated",
              "maximum_gemini_sdk_attempts": config.tts_attempts,
              "application_retries": 0, "fallbacks": 0}
    manifest_path = job_dir / "production_manifest.json"
    if not manifest_path.is_file():
        result["reasons"].append("production manifest missing")
        return result
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        result["reasons"].append("production manifest invalid")
        return result
    fingerprint_valid, fingerprint_mode = _fingerprint_compatibility(manifest, config)
    result["fingerprint_compatibility"] = fingerprint_mode
    if not fingerprint_valid:
        result["reasons"].append(fingerprint_mode)
        return result
    if expected_script_identity is not None:
        recorded_identity = manifest.get("canonical_script_identity")
        expected_identity = dict(expected_script_identity)
        if recorded_identity != expected_identity:
            result["reasons"].append("canonical script identity mismatch")
            return result
    result["compatible"] = True
    operational_valid, operational_reason, operational_data = (
        validate_production_resume_attestation(
            job_dir, config, resume_attestation_path
        )
    )
    result["operational_attestation_accepted"] = operational_valid
    result["operational_attestation_reason"] = operational_reason
    attestation_hashes: dict[str, str] = {}
    if operational_valid:
        attestation_hashes = {
            "plan": operational_data["plan"]["sha256"],
            "recipe": operational_data["recipe"]["sha256"],
            "tts_metadata": operational_data["narration"]["metadata"]["sha256"],
            "raw_narration": operational_data["narration"]["raw"]["sha256"],
            "normalized_narration": operational_data["narration"]["normalized"]["sha256"],
            "alignment": operational_data["alignment"]["metadata"]["sha256"],
            "alignment_boundaries": operational_data["alignment"]["boundaries"]["sha256"],
        }
    legacy_images: list[dict[str, str]] = []
    legacy_plan_valid = False
    legacy_reason = ""
    if not manifest.get("image_artifacts"):
        legacy_images, legacy_plan_valid, legacy_reason = _validated_legacy_plan_images(
            job_dir, manifest, config, resume_attestation_path
        )
    checks = (
        ("plan", "plan.json", "planned", True),
        ("raw_narration", "assets/narration_raw.wav", "narration_ready", True),
        ("normalized_narration", "assets/narration.wav", "narration_ready", True),
        ("alignment", "alignment_metadata.json", "narration_ready", True),
        ("alignment_boundaries", "alignment_boundaries.json", "aligned", True),
        ("mixed_audio", "assets/final_mixed_audio.m4a", "music_ready", False),
        ("silent_video", "_render/silent_video.mp4", "rendered", True),
        ("final_video", "video.mp4", "completed", True),
    )
    metadata_hashes = manifest.get("artifact_hashes", {})
    upstream_valid = True
    for key, relative, stage, required_for_downstream in checks:
        path = job_dir / relative
        expected = metadata_hashes.get(key, "") or attestation_hashes.get(key, "")
        own_hash_valid = path.is_file() and bool(expected) and file_sha256(path) == expected
        if key == "plan" and not expected and legacy_plan_valid:
            own_hash_valid = True
        valid = own_hash_valid and upstream_valid
        if key in {"alignment", "alignment_boundaries", "mixed_audio", "silent_video", "final_video"} and not upstream_valid:
            reason = "upstream artifact invalid"
        else:
            reason = (
                "canonical legacy plan and image metadata validated"
                if key == "plan" and valid and not expected
                else ("hash matched" if valid else "missing or hash mismatch")
            )
        result["artifacts"][key] = {"path": str(path), "valid": valid,
                                    "reason": reason}
        if valid:
            result["resume_stage"] = stage
        if required_for_downstream:
            upstream_valid = upstream_valid and valid
    image_items = manifest.get("image_artifacts", []) or (
        legacy_images if legacy_plan_valid else []
    )
    image_valid = bool(image_items)
    for item in image_items:
        path = job_dir / item.get("path", "")
        if not path.is_file() or file_sha256(path) != item.get("sha256"):
            image_valid = False
    exactly_seven = len(image_items) == config.scene_count
    result["artifacts"]["images"] = {
        "valid": image_valid and exactly_seven,
        "reason": (
            legacy_reason if legacy_images and image_valid and exactly_seven
            else ("seven hashes matched" if image_valid and exactly_seven else "image hash mismatch")
        ),
    }
    if result["artifacts"]["images"]["valid"]:
        result["estimated_image_requests"] = 0
        result["reusable_image_count"] = len(image_items)
    else:
        for key in ("silent_video", "final_video"):
            if key in result["artifacts"]:
                result["artifacts"][key]["valid"] = False
                result["artifacts"][key]["reason"] = "image stage invalid"
    if result["artifacts"].get("raw_narration", {}).get("valid"):
        result["estimated_gemini_requests"] = 0
        result["maximum_gemini_sdk_attempts"] = 0
    qc = manifest.get("qc_results", {})
    final = result["artifacts"].get("final_video", {})
    qc_valid = qc.get("audio") == "passed" and qc.get("video") == "passed"
    if final.get("valid") and not qc_valid:
        final["valid"] = False
        final["reason"] = "audio and video QC passes are required"
    result["render_required"] = not bool(final.get("valid") and qc_valid)
    accounting = _persisted_accounting(job_dir)
    result["persisted_submission_counts"] = accounting["submissions"]
    result["persisted_transport_attempt_counts"] = accounting[
        "transport_attempts"
    ]
    result["persisted_provider_result_counts"] = accounting["results"]
    result["legacy_image_integrity"] = (
        "full_sha256_attestation" if legacy_plan_valid else (
            "prefix_only_not_reusable" if legacy_images else "not_applicable"
        )
    )
    next_stages = {
        ProductionStage.planned.value: ProductionStage.images_ready.value,
        ProductionStage.images_ready.value: ProductionStage.narration_ready.value,
        ProductionStage.narration_ready.value: ProductionStage.aligned.value,
        ProductionStage.aligned.value: ProductionStage.music_ready.value,
        ProductionStage.music_ready.value: ProductionStage.rendered.value,
        ProductionStage.rendered.value: ProductionStage.qc_passed.value,
        ProductionStage.qc_passed.value: ProductionStage.completed.value,
    }
    result["next_required_stage"] = next_stages.get(
        result["resume_stage"], result["resume_stage"]
    )
    return result


def require_reusable_narration(decision: dict[str, Any]) -> None:
    """Fail closed unless an operational attestation trusts through alignment."""
    if not decision.get("operational_attestation_accepted"):
        raise RuntimeError(
            "required narration reuse failed: "
            + str(decision.get("operational_attestation_reason") or "attestation rejected")
        )
    required = (
        "raw_narration",
        "normalized_narration",
        "alignment",
        "alignment_boundaries",
    )
    invalid = [
        key
        for key in required
        if not decision.get("artifacts", {}).get(key, {}).get("valid")
    ]
    if invalid or decision.get("resume_stage") != ProductionStage.aligned.value:
        detail = ", ".join(invalid) or str(decision.get("resume_stage"))
        raise RuntimeError(
            "required narration reuse failed before provider access: " + detail
        )


def dry_run_envelope(
    config: ProductionConfig,
    job_dir: Path,
    *,
    resume: bool,
    script_identity: dict[str, Any] | None = None,
    resume_attestation_path: Path | None = None,
    max_tts_requests: int | None = None,
    max_image_requests: int | None = None,
) -> dict[str, Any]:
    decision = evaluate_resume(
        job_dir, config, script_identity, resume_attestation_path
    ) if resume else {
        "compatible": False, "artifacts": {}, "resume_stage": "initialized",
        "reasons": ["fresh job"], "estimated_gemini_requests": 1,
        "estimated_image_requests": config.max_image_requests, "render_required": True,
    }
    cumulative = decision.get("persisted_submission_counts", _zero_submissions())
    cumulative_transport = decision.get(
        "persisted_transport_attempt_counts", _zero_transport_attempts()
    )
    invocation_limits = _invocation_limits(
        config,
        max_tts_requests=max_tts_requests,
        max_image_requests=max_image_requests,
    )
    return {
        "recipe_id": config.recipe_id, "recipe_version": config.recipe_version,
        "production_schema_version": config.schema_version,
        "planner": config.planner_version,
        "voice_profile": config.voice_profile, "provider": config.provider,
        "model": config.model, "voice": config.voice, "style": config.style,
        "effective_voice_rate": normalize_voice_rate(config.voice_rate),
        "maximum_gemini_requests": invocation_limits["gemini"],
        "maximum_gemini_sdk_attempts": (
            0 if invocation_limits["gemini"] == 0 else config.tts_attempts
        ),
        "maximum_image_requests": invocation_limits["image_provider"],
        "cumulative_submission_counts": cumulative,
        "cumulative_transport_attempt_counts": cumulative_transport,
        "current_invocation_submission_counts": _zero_submissions(),
        "current_invocation_transport_attempt_counts": _zero_transport_attempts(),
        "current_invocation_request_limits": invocation_limits,
        "current_invocation_remaining_budget": invocation_limits,
        "retry_policy": "no retries",
        "fallback_policy": "no provider, model, stock, or local placeholder fallback",
        "edge_fallback": 0, "model_fallback": 0,
        "stock_fallback": 0, "local_placeholder_fallback": 0,
        "asr_calls": 0, "music_provider_calls": 0,
        "alignment_mode": config.alignment_mode, "music_track": config.music_track,
        "music_profile": config.music_profile, "expected_output_directory": str(job_dir),
        "canonical_script_identity": dict(script_identity or {}),
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
    "apply_sentence_alignment", "build_legacy_image_resume_attestation",
    "build_production_resume_attestation",
    "dry_run_envelope", "evaluate_resume", "file_sha256", "get_production_config",
    "image_request_accounting",
    "production_fingerprint", "record_unhandled_production_failure", "stable_hash", "tts_cache_key",
    "repair_completed_job_metadata", "validate_completed_job_integrity",
    "require_reusable_narration", "source_inventory_sha256",
    "validate_production_resume_attestation",
    "validate_production_voice_configuration",
]
