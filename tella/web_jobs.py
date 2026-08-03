"""Persisted serial job execution for the local production web application."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from dataclasses import dataclass
import json
import logging
import math
import os
from pathlib import Path
import queue
import re
import signal
import shutil
import subprocess
import sys
import threading
from types import MappingProxyType
from typing import Any
import uuid

from tella.atomic_write import atomic_write_json
from tella.cli import run_pipeline
from tella.tts.gemini_registry import resolve_style, resolve_voice
from tella.web_contract import (
    WebInputMode,
    WebJobError,
    WebJobStatus,
    WebJobView,
    WebRenderRequest,
    utc_now,
)
from tella.voice_profiles import get_voice_profile
from tella.web_image_fallback import (
    WEB_POLLINATIONS_FALLBACK_ENV,
    WEB_SENSITIVITY_POLICY_ENV,
    WEB_SENSITIVITY_POLICY_ID,
    pollinations_web_readiness,
)

_JOB_ID = re.compile(r"^web-[0-9a-f]{24}$")
_PHASES = {
    "step 1/6": ("input", 10),
    "step 2/6": ("planning", 25),
    "step 3/6": ("visuals", 40),
    "step 4/6": ("narration", 55),
    "step 5/6": ("composition", 70),
    "step 6/6": ("rendering", 85),
}
_MAX_LOGS = 500
_MAX_LOG_CHARS = 500
_MAX_SANITIZER_INPUT_CHARS = 4_000
_MAX_IPC_LINE_BYTES = 8_192
_MAX_IPC_EVENTS = 512
_MAX_WEB_WORKERS_ENV = "TELLA_WEB_MAX_WORKERS"
_PUBLIC_ERROR_CODES = {
    "GEMINI_TTS_FAILED",
    "JOB_MANAGER_SHUTDOWN_INCOMPLETE",
    "MP4_ARTIFACT_INVALID",
    "MP4_STREAMS_REQUIRED",
    "MP4_VALIDATION_FAILED",
    "PERSISTED_ARTIFACT_INVALID",
    "PIPELINE_FAILED",
    "WORKER_IPC_INVALID",
    "WORKER_PROCESS_EXITED",
    "WORKER_SHUTDOWN_TERMINATED",
    "SERVER_RESTART_INTERRUPTED_JOB",
    "SERVER_SHUTDOWN_CANCELLED_JOB",
    "WEB_TTS_AUTHORITY_INVALID",
}
_WEB_NARRATOR_PROFILE_ID = "gemini_callirrhoe_vi_gentle_emotional"
_WEB_NARRATOR_PROFILE = get_voice_profile(_WEB_NARRATOR_PROFILE_ID)
_SECRET_ENV_NAMES = (
    "GEMINI_API_KEY",
    "GEMINI_API_KEYS",
    "GOOGLE_API_KEY",
    "GOOGLE_TTS_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "CF_ACCOUNTS",
    "CF_ACCOUNT_ID",
    "CF_AI_TOKEN",
    "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_API_TOKEN",
    "POLLINATIONS_API_KEY",
    "PEXELS_API_KEY",
    "PEXELS_API_KEYS",
    "XAI_API_KEY",
)

_CHILD_BASE_ENV_NAMES = (
    "APPDATA",
    "COMSPEC",
    "HOME",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "LANG",
    "LOCALAPPDATA",
    "NO_PROXY",
    "PATH",
    "PATHEXT",
    "PROGRAMDATA",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "REQUESTS_CA_BUNDLE",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SystemRoot",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "WINDIR",
    "https_proxy",
    "http_proxy",
    "no_proxy",
)
_WEB_ENVIRONMENT = MappingProxyType(
    {
        "TELLA_TTS_PROVIDER": "gemini",
        "TELLA_STRICT_TTS_PROVIDER": "1",
        "TELLA_TTS_MODEL": _WEB_NARRATOR_PROFILE.model,
        "TELLA_TTS_VOICE": _WEB_NARRATOR_PROFILE.voice,
        "TELLA_TTS_STYLE": _WEB_NARRATOR_PROFILE.style,
        "TELLA_TTS_LANGUAGE": "auto",
        "TELLA_TTS_CODEC": "wav",
        "TELLA_TTS_SAMPLE_RATE": "24000",
        "TELLA_TTS_SPEED": "1.0",
        "TELLA_TTS_CONTINUOUS": "1",
        "TELLA_TTS_MAX_PAUSE_MS": "350",
        "TELLA_MAX_TTS_REQUESTS": "1",
        "TELLA_TTS_CACHE_ENABLED": "0",
        "TELLA_TTS_RESUME_RAW": "",
        "TELLA_NO_TTS_RETRY": "0",
        "GOOGLE_TTS_PROVIDER": "",
        "TELLA_GEMINI_PROCESS_CREDENTIAL_NAME": "",
        "TELLA_ASSET_LIBRARY_V2": "0",
        "TELLA_ASSET_LIBRARY_ROOT": "",
        "TELLA_ASSET_LIBRARY_SEMANTICS_PATH": "",
        "TELLA_IMAGE_PROVIDER": "cloudflare",
        "TELLA_ALLOW_LOCAL_IMAGE_FALLBACK": "0",
        "TELLA_SKIP_IMAGE_GENERATION": "0",
        "TELLA_REUSE_ASSETS": "0",
        "TELLA_REUSE_ASSETS_MODE": "strict",
        "TELLA_ALLOW_MISMATCHED_REUSED_ASSETS": "0",
        "TELLA_IMAGES_FROM_JOB": "",
        "TELLA_REUSE_PLAN_PATH": "",
        "TELLA_REQUIRE_REUSED_SCENE_INDICES": "",
        "TELLA_MAX_AI_IMAGES": "",
        "TELLA_MINIMALIST_VISUAL_MODE": "ai_scene",
        "TELLA_MINIMALIST_USE_AI_SCENES": "1",
        "TELLA_MINIMALIST_CHARACTER_MODE": "auto",
        "TELLA_ALLOW_PLACEHOLDER_SPRITES": "0",
        "TELLA_REQUIRE_REFERENCE_CONDITIONING": "0",
        "TELLA_USE_PREVIOUS_SCENE_REFERENCE": "0",
        "TELLA_DISABLE_STOCK_FALLBACK": "1",
        WEB_POLLINATIONS_FALLBACK_ENV: "1",
        WEB_SENSITIVITY_POLICY_ENV: WEB_SENSITIVITY_POLICY_ID,
        "TELLA_RENDER_MOTION_PROFILE": "practical_pull_back",
        "TELLA_SCENE_QC": "basic",
        "TELLA_SCENE_MAX_ATTEMPTS": "2",
        "TELLA_QC_JSON_PARSE_ATTEMPTS": "2",
        "TELLA_STRICT_VISUAL_QC": "0",
        "TELLA_STRICT_CAPTION_SAFE_AREA": "0",
        "TELLA_VISION_QC_MODEL": "",
        "TELLA_SCENE_QC_MODEL": "",
        "CHANNEL_NAME": "",
        "CHANNEL_AVATAR": "",
        "DEMO_MODE": "0",
        "TELLA_DEMO_MODE": "0",
    }
)


def _web_narrator_profile_ready() -> bool:
    if (
        _WEB_NARRATOR_PROFILE.provider != "gemini"
        or _WEB_NARRATOR_PROFILE.model != "gemini-2.5-flash-preview-tts"
        or _WEB_NARRATOR_PROFILE.voice != "Callirrhoe"
        or _WEB_NARRATOR_PROFILE.style != "gentle_emotional"
        or _WEB_NARRATOR_PROFILE.language != "vi-VN"
        or _WEB_NARRATOR_PROFILE.automatic_edge_fallback_enabled
        or _WEB_NARRATOR_PROFILE.automatic_model_fallback_enabled
    ):
        return False
    try:
        registered = resolve_voice(
            _WEB_NARRATOR_PROFILE.voice,
            _WEB_NARRATOR_PROFILE.model,
        )
        resolve_style(_WEB_NARRATOR_PROFILE.style)
    except ValueError:
        return False
    return (
        registered.provider == "gemini"
        and registered.canonical_name == _WEB_NARRATOR_PROFILE.voice
        and registered.benchmark_language == _WEB_NARRATOR_PROFILE.language
    )


_WINDOWS_ABSOLUTE_PATH = re.compile(r"(?<![\w])(?:[A-Za-z]:[\\/][^\r\n\t<>|?*]+)")
_POSIX_ABSOLUTE_PATH = re.compile(r"(?<![\w:])/(?:[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*)")
_QUERY_SECRET = re.compile(r"(?i)([?&](?:key|api_key|token|access_token|authorization)=)[^&#\s]+")
_HEADER_SECRET = re.compile(
    r"(?i)\b(authorization|api[-_ ]?key|access[-_ ]?token|token)"
    r"\s*[:=]\s*(?:bearer\s+)?[^\s,;]+"
)
_BEARER_SECRET = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_URL_CREDENTIALS = re.compile(r"(?i)\bhttps?://[^/\s:@]+:[^@\s/]+@[^\s]+")
_PROVIDER_RESPONSE_BODY = re.compile(
    r"(?is)\b(HTTP\s+\d{3}|provider errors?|response body)\s*[:=-]\s*.+"
)

PipelineRunner = Callable[..., Awaitable[Path]]
MediaProbe = Callable[[Path], dict[str, object]]


def _web_max_workers_setting(value: str | None = None) -> tuple[bool, int]:
    raw = os.environ.get(_MAX_WEB_WORKERS_ENV) if value is None else value
    if raw is None:
        return True, 1
    normalized = raw.strip()
    if normalized not in {"1", "2"}:
        return False, 1
    return True, int(normalized)


def _child_environment() -> dict[str, str]:
    """Build the complete child environment without inheriting mutable web policy."""
    environment = {
        name: value for name in _CHILD_BASE_ENV_NAMES if (value := os.environ.get(name)) is not None
    }
    environment.update(
        {name: value for name in _SECRET_ENV_NAMES if (value := os.environ.get(name)) is not None}
    )
    environment.update(_WEB_ENVIRONMENT)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return environment


@dataclass
class _ActiveChild:
    job_id: str
    process: subprocess.Popen[bytes]
    events: queue.Queue[dict[str, object]]
    reader: threading.Thread
    terminal: dict[str, object] | None = None
    invalid_ipc: bool = False


def _read_child_events(
    stream: Any,
    events: queue.Queue[dict[str, object]],
) -> None:
    try:
        while True:
            line = stream.readline(_MAX_IPC_LINE_BYTES + 1)
            if not line:
                return
            if len(line) > _MAX_IPC_LINE_BYTES or not line.endswith(b"\n"):
                events.put({"type": "invalid"}, timeout=1)
                return
            try:
                event = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                events.put({"type": "invalid"}, timeout=1)
                return
            if not isinstance(event, dict):
                events.put({"type": "invalid"}, timeout=1)
                return
            events.put(event, timeout=1)
    except (OSError, queue.Full):
        try:
            events.put_nowait({"type": "invalid"})
        except queue.Full:
            return


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
            timeout=5,
            shell=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            return


def _configured_secret_values() -> tuple[str, ...]:
    values: set[str] = set()
    for name in _SECRET_ENV_NAMES:
        raw = (os.environ.get(name) or "").strip().strip("'\"")
        if not raw:
            continue
        values.add(raw)
        if name in {"GEMINI_API_KEYS", "PEXELS_API_KEYS"}:
            values.update(piece.strip() for piece in raw.split(",") if piece.strip())
        elif name == "CF_ACCOUNTS":
            for piece in raw.split(";"):
                account, separator, token = piece.strip().partition(":")
                if separator:
                    values.update(value for value in (account, token) if value)
    return tuple(sorted((value for value in values if len(value) >= 4), key=len, reverse=True))


def sanitize_public_text(value: object, *, private_paths: tuple[Path, ...] = ()) -> str:
    """Return a bounded browser-safe projection of an internal message."""
    result = str(value)[:_MAX_SANITIZER_INPUT_CHARS]
    for path in private_paths:
        rendered = str(path)
        if rendered:
            result = result.replace(rendered, "[PRIVATE_PATH]")
    for secret in _configured_secret_values():
        result = result.replace(secret, "[REDACTED]")
    result = _QUERY_SECRET.sub(r"\1[REDACTED]", result)
    result = _HEADER_SECRET.sub(r"\1: [REDACTED]", result)
    result = _BEARER_SECRET.sub("Bearer [REDACTED]", result)
    result = _URL_CREDENTIALS.sub("[REDACTED_URL]", result)
    result = _PROVIDER_RESPONSE_BODY.sub(r"\1: [PROVIDER_RESPONSE_REDACTED]", result)
    result = _WINDOWS_ABSOLUTE_PATH.sub("[PRIVATE_PATH]", result)
    result = _POSIX_ABSOLUTE_PATH.sub("[PRIVATE_PATH]", result)
    return result.replace("\r", " ").replace("\n", " ")[:_MAX_LOG_CHARS]


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def probe_mp4(path: Path) -> dict[str, object]:
    """Require a readable positive-duration MP4 with audio and video."""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type",
        "-of",
        "json",
        str(path),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("MP4_VALIDATION_FAILED") from exc
    if completed.returncode != 0:
        raise RuntimeError("MP4_VALIDATION_FAILED")
    try:
        payload = json.loads(completed.stdout)
        duration = float(payload["format"]["duration"])
        streams = payload["streams"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("MP4_VALIDATION_FAILED") from exc
    if not math.isfinite(duration) or not isinstance(streams, list):
        raise RuntimeError("MP4_VALIDATION_FAILED")
    if not all(isinstance(item, dict) for item in streams):
        raise RuntimeError("MP4_VALIDATION_FAILED")
    video = sum(item.get("codec_type") == "video" for item in streams)
    audio = sum(item.get("codec_type") == "audio" for item in streams)
    if duration <= 0 or video < 1 or audio < 1:
        raise RuntimeError("MP4_STREAMS_REQUIRED")
    return {
        "duration_seconds": duration,
        "video_streams": video,
        "audio_streams": audio,
    }


def _safe_metadata(path: Path, keys: tuple[str, ...]) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        source = _read_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    result: dict[str, object] = {}
    for key in keys:
        if key not in source:
            continue
        value = source[key]
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            result[key] = list(value)
    if path.name == "plan.json" and isinstance(source.get("scenes"), list):
        result["scene_count"] = len(source["scenes"])
    return result


def _validated_web_tts_metadata(path: Path) -> dict[str, object]:
    """Validate the persisted TTS authority and return its public projection."""
    try:
        source = _read_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("WEB_TTS_AUTHORITY_INVALID") from exc
    expected = {
        "requested_provider": "gemini",
        "tts_provider": "gemini",
        "tts_model": _WEB_NARRATOR_PROFILE.model,
        "tts_voice": _WEB_NARRATOR_PROFILE.voice,
        "tts_style": _WEB_NARRATOR_PROFILE.style,
        "fallback_used": False,
        "tts_continuous": True,
    }
    if any(source.get(key) != value for key, value in expected.items()):
        raise RuntimeError("WEB_TTS_AUTHORITY_INVALID")
    language = source.get("tts_language")
    if not isinstance(language, str) or not language:
        raise RuntimeError("WEB_TTS_AUTHORITY_INVALID")
    return {
        "provider": "gemini",
        "narrator_profile": _WEB_NARRATOR_PROFILE_ID,
        "language": language,
        "fallback_used": False,
        "continuous_narration": True,
    }


class _JobLogHandler(logging.Handler):
    def __init__(self, manager: JobManager, job_id: str, thread_id: int) -> None:
        super().__init__(logging.INFO)
        self.manager = manager
        self.job_id = job_id
        self.thread_id = thread_id

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread != self.thread_id:
            return
        message = record.getMessage()
        self.manager.record_log(self.job_id, message)
        lowered = message.lower()
        for marker, (phase, progress) in _PHASES.items():
            if marker in lowered:
                self.manager.update_progress(self.job_id, phase, progress)
                break


class JobManager:
    """Own the parent queue, bounded child processes, and artifact publication."""

    def __init__(
        self,
        output_root: Path,
        *,
        runner: PipelineRunner = run_pipeline,
        media_probe: MediaProbe = probe_mp4,
        autostart: bool = True,
        max_workers: int | None = None,
        worker_module: str = "tella.web_worker",
    ) -> None:
        self.output_root = Path(output_root).resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._runner = runner
        self._media_probe = media_probe
        self._process_mode = runner is run_pipeline
        if max_workers is None:
            self._worker_config_valid, configured_workers = _web_max_workers_setting()
        elif type(max_workers) is not int or max_workers not in {1, 2}:
            raise ValueError("TELLA_WEB_MAX_WORKERS must be the integer 1 or 2")
        else:
            self._worker_config_valid, configured_workers = True, max_workers
        if not self._process_mode and configured_workers != 1:
            raise ValueError("custom in-process runners require max_workers=1")
        self.max_workers = configured_workers
        self._worker_module = worker_module
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._wake = threading.Event()
        self._active_children: dict[str, _ActiveChild] = {}
        self._closing = False
        self._closed = False
        self._stop_enqueued = False
        self._load()
        if autostart:
            self.start()

    def start(self) -> None:
        with self._lock:
            if self._closing or self._closed:
                raise RuntimeError("JOB_MANAGER_CLOSING")
            if self._worker is not None:
                return
            self._worker = threading.Thread(
                target=self._worker_main,
                name="tella-web-process-scheduler",
                daemon=True,
            )
            self._worker.start()

    def close(self, *, timeout: float = 10.0) -> bool:
        """Stop accepting work and leave no owned production child behind."""
        if timeout < 0:
            raise ValueError("shutdown timeout must be non-negative")
        with self._lock:
            self._closing = True
            for job_id, payload in self._jobs.items():
                if payload["status"] != WebJobStatus.QUEUED.value:
                    continue
                payload.update(
                    status=WebJobStatus.CANCELLED.value,
                    phase="shutdown-cancelled",
                    finished_at=utc_now(),
                    error={
                        "code": "SERVER_SHUTDOWN_CANCELLED_JOB",
                        "message": "The server stopped before this queued job started.",
                    },
                )
                self._persist(job_id)
            worker = self._worker
            if worker is None:
                self._closed = True
                return True
            if not self._stop_enqueued:
                self._queue.put(None)
                self._stop_enqueued = True
            self._wake.set()
        worker.join(timeout=timeout)
        if worker.is_alive() and self._process_mode:
            self._terminate_active_children()
            self._wake.set()
            worker.join(timeout=5)
        if worker.is_alive():
            return False
        with self._lock:
            if self._worker is worker:
                self._worker = None
            self._closed = True
        return True

    def _job_dir(self, job_id: str) -> Path:
        if not _JOB_ID.fullmatch(job_id):
            raise KeyError(job_id)
        path = (self.output_root / job_id).resolve()
        try:
            path.relative_to(self.output_root)
        except ValueError as exc:
            raise KeyError(job_id) from exc
        return path

    def _persist(self, job_id: str) -> None:
        atomic_write_json(self._job_dir(job_id) / "job.json", self._jobs[job_id])

    def _load(self) -> None:
        for metadata_path in sorted(self.output_root.glob("web-*/job.json")):
            try:
                payload = _read_json(metadata_path)
                job_id = str(payload["job_id"])
                self._job_dir(job_id)
                WebRenderRequest.model_validate(payload["request"])
                status = WebJobStatus(str(payload["status"]))
            except (KeyError, OSError, ValueError, json.JSONDecodeError):
                continue
            payload["logs"] = [
                sanitize_public_text(
                    value,
                    private_paths=(self.output_root, metadata_path.parent),
                )
                for value in payload.get("logs", [])
            ][-_MAX_LOGS:]
            if isinstance(payload.get("error"), dict):
                loaded_code = str(payload["error"].get("code") or "")
                payload["error"] = {
                    "code": (
                        loaded_code if loaded_code in _PUBLIC_ERROR_CODES else "PIPELINE_FAILED"
                    ),
                    "message": sanitize_public_text(
                        payload["error"].get("message") or "Production pipeline failed.",
                        private_paths=(self.output_root, metadata_path.parent),
                    ),
                }
            payload["plan_metadata"] = self._safe_loaded_plan_metadata(payload.get("plan_metadata"))
            payload["tts_metadata"] = self._safe_loaded_tts_metadata(payload.get("tts_metadata"))
            self._jobs[job_id] = payload
            if status in {WebJobStatus.QUEUED, WebJobStatus.RUNNING}:
                payload.update(
                    status=WebJobStatus.FAILED.value,
                    phase="interrupted",
                    finished_at=utc_now(),
                    error={
                        "code": "SERVER_RESTART_INTERRUPTED_JOB",
                        "message": "The server restarted before this job completed.",
                    },
                )
                self._persist(job_id)
            elif status is WebJobStatus.SUCCEEDED:
                try:
                    artifact = self.artifact_path(job_id)
                    self._media_probe(artifact)
                    payload["output_bytes"] = artifact.stat().st_size
                except (KeyError, OSError, RuntimeError):
                    payload.update(
                        status=WebJobStatus.FAILED.value,
                        phase="artifact-invalid",
                        progress=min(int(payload.get("progress", 0)), 99),
                        finished_at=utc_now(),
                        output_path=None,
                        output_bytes=None,
                        probe=None,
                        error={
                            "code": "PERSISTED_ARTIFACT_INVALID",
                            "message": "The persisted video artifact is missing or invalid.",
                        },
                    )
                    self._persist(job_id)

    def create(self, request: WebRenderRequest) -> WebJobView:
        job_id = f"web-{uuid.uuid4().hex[:24]}"
        with self._lock:
            if self._closing or self._closed:
                raise RuntimeError("JOB_MANAGER_CLOSING")
            if not self._worker_config_valid:
                raise RuntimeError("TELLA_WEB_MAX_WORKERS_INVALID")
            self._job_dir(job_id).mkdir(parents=True, exist_ok=False)
            self._jobs[job_id] = {
                "schema_version": 1,
                "job_id": job_id,
                "status": WebJobStatus.QUEUED.value,
                "phase": "queued",
                "progress": 0,
                "created_at": utc_now(),
                "started_at": None,
                "finished_at": None,
                "logs": [],
                "error": None,
                "output_path": None,
                "output_bytes": None,
                "probe": None,
                "request": request.model_dump(mode="json"),
            }
            self._persist(job_id)
            self._queue.put(job_id)
            self._wake.set()
            return self.get(job_id)

    def get(self, job_id: str) -> WebJobView:
        with self._lock:
            payload = self._jobs.get(job_id)
            if payload is None:
                raise KeyError(job_id)
            status = WebJobStatus(payload["status"])
            probe = payload.get("probe") or {}
            output_available = status is WebJobStatus.SUCCEEDED
            base = f"/api/jobs/{job_id}"
            return WebJobView(
                job_id=job_id,
                status=status,
                phase=payload["phase"],
                progress=payload["progress"],
                created_at=payload["created_at"],
                started_at=payload.get("started_at"),
                finished_at=payload.get("finished_at"),
                logs=tuple(payload.get("logs") or ()),
                error=(
                    WebJobError.model_validate(payload["error"]) if payload.get("error") else None
                ),
                output_available=output_available,
                preview_url=f"{base}/preview" if output_available else None,
                download_url=f"{base}/download" if output_available else None,
                duration_seconds=probe.get("duration_seconds"),
                video_streams=int(probe.get("video_streams") or 0),
                audio_streams=int(probe.get("audio_streams") or 0),
                output_bytes=payload.get("output_bytes"),
                plan_metadata=payload.get("plan_metadata"),
                tts_metadata=payload.get("tts_metadata"),
                request=WebRenderRequest.model_validate(payload["request"]),
            )

    def list(self) -> tuple[WebJobView, ...]:
        with self._lock:
            ordered = sorted(
                self._jobs,
                key=lambda item: self._jobs[item]["created_at"],
                reverse=True,
            )
        return tuple(self.get(job_id) for job_id in ordered)

    def cancel(self, job_id: str) -> WebJobView:
        with self._lock:
            payload = self._jobs.get(job_id)
            if payload is None:
                raise KeyError(job_id)
            if payload["status"] != WebJobStatus.QUEUED.value:
                raise ValueError("ONLY_QUEUED_JOB_CAN_BE_CANCELLED")
            payload.update(
                status=WebJobStatus.CANCELLED.value,
                phase="cancelled",
                finished_at=utc_now(),
                error=None,
            )
            self._persist(job_id)
            self._wake.set()
        return self.get(job_id)

    def record_log(self, job_id: str, message: str) -> None:
        safe = self._sanitize(job_id, message)
        with self._lock:
            logs = self._jobs[job_id]["logs"]
            logs.append(safe)
            del logs[:-_MAX_LOGS]
            self._persist(job_id)

    def update_progress(self, job_id: str, phase: str, progress: int) -> None:
        with self._lock:
            payload = self._jobs[job_id]
            if payload["status"] != WebJobStatus.RUNNING.value:
                return
            payload["phase"] = phase
            payload["progress"] = max(int(payload["progress"]), min(progress, 99))
            self._persist(job_id)

    def artifact_path(self, job_id: str) -> Path:
        with self._lock:
            payload = self._jobs.get(job_id)
            if payload is None or payload["status"] != WebJobStatus.SUCCEEDED.value:
                raise KeyError(job_id)
            relative = payload.get("output_path")
            if not isinstance(relative, str):
                raise KeyError(job_id)
        job_dir = self._job_dir(job_id)
        artifact = (job_dir / relative).resolve(strict=True)
        try:
            artifact.relative_to(job_dir)
        except ValueError as exc:
            raise KeyError(job_id) from exc
        if not artifact.is_file() or artifact.suffix.lower() != ".mp4":
            raise KeyError(job_id)
        return artifact

    def _worker_main(self) -> None:
        if self._process_mode:
            self._process_worker_main()
            return
        self._inline_worker_main()

    def _inline_worker_main(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                if job_id is None:
                    return
                with self._lock:
                    if self._jobs[job_id]["status"] == WebJobStatus.CANCELLED.value:
                        continue
                self._execute(job_id)
            finally:
                self._queue.task_done()

    def _process_worker_main(self) -> None:
        stop_requested = False
        while True:
            while not stop_requested and len(self._active_children) < self.max_workers:
                try:
                    job_id = self._queue.get_nowait()
                except queue.Empty:
                    break
                if job_id is None:
                    stop_requested = True
                    self._queue.task_done()
                    break
                with self._lock:
                    cancelled = self._jobs[job_id]["status"] == WebJobStatus.CANCELLED.value
                if cancelled:
                    self._queue.task_done()
                    continue
                try:
                    self._start_child(job_id)
                except Exception:
                    self._fail_job(job_id, "WORKER_PROCESS_EXITED")
                    self._queue.task_done()

            for job_id in tuple(self._active_children):
                self._monitor_child(job_id)

            if stop_requested and not self._active_children:
                return
            self._wake.wait(0.02)
            self._wake.clear()

    def _start_child(self, job_id: str) -> None:
        with self._lock:
            payload = self._jobs[job_id]
            request = WebRenderRequest.model_validate(payload["request"])
            payload.update(
                status=WebJobStatus.RUNNING.value,
                phase="starting",
                progress=1,
                started_at=utc_now(),
                finished_at=None,
                error=None,
            )
            self._persist(job_id)
        worker_entrypoint = (
            [str(Path(self._worker_module).resolve())]
            if self._worker_module.endswith(".py")
            else ["-m", self._worker_module]
        )
        command = [
            sys.executable,
            *worker_entrypoint,
            "--job-id",
            job_id,
            "--output-root",
            str(self.output_root),
        ]
        process_kwargs: dict[str, object] = {}
        if os.name == "nt":
            process_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            process_kwargs["start_new_session"] = True
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(Path.cwd()),
            env=_child_environment(),
            shell=False,
            **process_kwargs,
        )
        try:
            assert process.stdin is not None
            process.stdin.write(request.model_dump_json().encode("utf-8"))
            process.stdin.close()
        except Exception:
            _terminate_process_tree(process)
            raise
        assert process.stdout is not None
        events: queue.Queue[dict[str, object]] = queue.Queue(maxsize=_MAX_IPC_EVENTS)
        reader = threading.Thread(
            target=_read_child_events,
            args=(process.stdout, events),
            name=f"tella-web-ipc-{job_id}",
            daemon=True,
        )
        child = _ActiveChild(
            job_id=job_id,
            process=process,
            events=events,
            reader=reader,
        )
        with self._lock:
            self._active_children[job_id] = child
        reader.start()

    def _monitor_child(self, job_id: str) -> None:
        child = self._active_children[job_id]
        self._drain_child_events(child)
        return_code = child.process.poll()
        if child.invalid_ipc:
            _terminate_process_tree(child.process)
            return_code = child.process.poll()
        if return_code is None:
            return
        child.reader.join(timeout=0.5)
        self._drain_child_events(child)
        if child.invalid_ipc:
            self._fail_job(job_id, "WORKER_IPC_INVALID")
        elif child.terminal is None:
            self._fail_job(job_id, "WORKER_PROCESS_EXITED")
        elif child.terminal["type"] == "failure":
            self._fail_job(job_id, str(child.terminal["code"]))
        elif return_code != 0:
            self._fail_job(job_id, "WORKER_PROCESS_EXITED")
        else:
            try:
                self._publish_output(job_id, str(child.terminal["output_path"]))
            except Exception as exc:
                self._fail_job(job_id, self._error_code(exc))
        child.process.stdout.close() if child.process.stdout is not None else None
        with self._lock:
            self._active_children.pop(job_id, None)
        self._queue.task_done()
        self._wake.set()

    def _drain_child_events(self, child: _ActiveChild) -> None:
        while True:
            try:
                event = child.events.get_nowait()
            except queue.Empty:
                return
            event_type = event.get("type")
            if event_type == "log":
                message = event.get("message")
                if not isinstance(message, str) or len(message) > _MAX_SANITIZER_INPUT_CHARS:
                    child.invalid_ipc = True
                    continue
                self.record_log(child.job_id, message)
                lowered = message.lower()
                for marker, (phase, progress) in _PHASES.items():
                    if marker in lowered:
                        self.update_progress(child.job_id, phase, progress)
                        break
                continue
            if event_type == "success":
                output_path = event.get("output_path")
                valid = isinstance(output_path, str) and 0 < len(output_path) <= 4_096
                terminal = {"type": "success", "output_path": output_path}
            elif event_type == "failure":
                code = event.get("code")
                valid = isinstance(code, str) and code in _PUBLIC_ERROR_CODES
                terminal = {"type": "failure", "code": code}
            else:
                valid = False
                terminal = {}
            if not valid or child.terminal is not None:
                child.invalid_ipc = True
            else:
                child.terminal = terminal

    def _terminate_active_children(self) -> None:
        with self._lock:
            children = tuple(self._active_children.values())
        for child in children:
            self._fail_job(child.job_id, "WORKER_SHUTDOWN_TERMINATED")
            _terminate_process_tree(child.process)

    def _publish_output(self, job_id: str, output: str | Path) -> None:
        self.update_progress(job_id, "validating", 95)
        job_dir = self._job_dir(job_id)
        resolved = Path(output).resolve(strict=True)
        resolved.relative_to(job_dir)
        if resolved.suffix.lower() != ".mp4" or resolved.stat().st_size <= 0:
            raise RuntimeError("MP4_ARTIFACT_INVALID")
        probe = self._media_probe(resolved)
        relative = resolved.relative_to(job_dir).as_posix()
        plan_metadata = _safe_metadata(
            job_dir / "plan.json",
            (
                "title",
                "language",
                "theme",
                "media_source",
                "aspect_ratio",
                "total_duration",
                "tts_provider",
                "primary_image_provider",
                "fallback_image_provider",
                "pollinations_fallback_used",
                "pollinations_fallback_attempt_count",
                "resolved_image_providers",
            ),
        )
        tts_metadata = _validated_web_tts_metadata(job_dir / "tts_metadata.json")
        with self._lock:
            payload = self._jobs[job_id]
            if payload["status"] != WebJobStatus.RUNNING.value:
                return
            payload.update(
                status=WebJobStatus.SUCCEEDED.value,
                phase="complete",
                progress=100,
                finished_at=utc_now(),
                output_path=relative,
                output_bytes=resolved.stat().st_size,
                probe=probe,
                plan_metadata=plan_metadata,
                tts_metadata=tts_metadata,
                error=None,
            )
            self._persist(job_id)

    def _fail_job(self, job_id: str, code: str) -> None:
        safe_code = code if code in _PUBLIC_ERROR_CODES else "PIPELINE_FAILED"
        with self._lock:
            payload = self._jobs[job_id]
            if payload["status"] != WebJobStatus.RUNNING.value:
                return
            payload.update(
                status=WebJobStatus.FAILED.value,
                phase="failed",
                progress=min(int(payload["progress"]), 99),
                finished_at=utc_now(),
                output_path=None,
                output_bytes=None,
                probe=None,
                error={
                    "code": safe_code,
                    "message": self._public_error_message(job_id, RuntimeError(safe_code)),
                },
            )
            self._persist(job_id)

    def _execute(self, job_id: str) -> None:
        with self._lock:
            payload = self._jobs[job_id]
            payload.update(
                status=WebJobStatus.RUNNING.value,
                phase="starting",
                progress=1,
                started_at=utc_now(),
                finished_at=None,
                error=None,
            )
            self._persist(job_id)
            request = WebRenderRequest.model_validate(payload["request"])
        handler = _JobLogHandler(self, job_id, threading.get_ident())
        root_logger = logging.getLogger()
        previous_level = root_logger.level
        if root_logger.getEffectiveLevel() > logging.INFO:
            root_logger.setLevel(logging.INFO)
        root_logger.addHandler(handler)
        try:
            self.record_log(job_id, "Starting real Tella production pipeline.")
            with self._strict_gemini_environment():
                output = asyncio.run(
                    self._runner(
                        topic=request.content
                        if request.input_mode == WebInputMode.TOPIC
                        else "exact script",
                        target_lang=request.language,
                        theme=request.theme,
                        media_source=request.media_source,
                        duration_mode=request.duration_mode,
                        aspect_ratio=request.aspect_ratio,
                        voice_pace_name=None,
                        voice_rate_custom=None,
                        voice_gender=None,
                        out_root=self.output_root,
                        job_id=job_id,
                        google_tts_api_key="",
                        google_tts_voice="",
                        user_script=(
                            request.content if request.input_mode == WebInputMode.SCRIPT else None
                        ),
                        allow_local_image_fallback=False,
                        dry_run_plan=False,
                        stop_after_images=False,
                        tts_continuous=True,
                        no_music=request.no_music,
                    )
                )
            self.update_progress(job_id, "validating", 95)
            job_dir = self._job_dir(job_id)
            resolved = Path(output).resolve(strict=True)
            resolved.relative_to(job_dir)
            if resolved.suffix.lower() != ".mp4" or resolved.stat().st_size <= 0:
                raise RuntimeError("MP4_ARTIFACT_INVALID")
            probe = self._media_probe(resolved)
            relative = resolved.relative_to(job_dir).as_posix()
            plan_metadata = _safe_metadata(
                job_dir / "plan.json",
                (
                    "title",
                    "language",
                    "theme",
                    "media_source",
                    "aspect_ratio",
                    "total_duration",
                    "tts_provider",
                    "primary_image_provider",
                    "fallback_image_provider",
                    "pollinations_fallback_used",
                    "pollinations_fallback_attempt_count",
                    "resolved_image_providers",
                ),
            )
            tts_metadata = _validated_web_tts_metadata(job_dir / "tts_metadata.json")
            with self._lock:
                payload = self._jobs[job_id]
                payload.update(
                    status=WebJobStatus.SUCCEEDED.value,
                    phase="complete",
                    progress=100,
                    finished_at=utc_now(),
                    output_path=relative,
                    output_bytes=resolved.stat().st_size,
                    probe=probe,
                    plan_metadata=plan_metadata,
                    tts_metadata=tts_metadata,
                    error=None,
                )
                self._persist(job_id)
        except Exception as exc:
            with self._lock:
                payload = self._jobs[job_id]
                payload.update(
                    status=WebJobStatus.FAILED.value,
                    phase="failed",
                    progress=min(int(payload["progress"]), 99),
                    finished_at=utc_now(),
                    output_path=None,
                    output_bytes=None,
                    probe=None,
                    error={
                        "code": self._error_code(exc),
                        "message": self._public_error_message(job_id, exc),
                    },
                )
                self._persist(job_id)
        finally:
            root_logger.removeHandler(handler)
            root_logger.setLevel(previous_level)

    def _sanitize(self, job_id: str, value: str) -> str:
        return sanitize_public_text(
            value,
            private_paths=(self.output_root, self._job_dir(job_id)),
        )

    def _public_error_message(self, job_id: str, exc: Exception) -> str:
        code = self._error_code(exc)
        messages = {
            "GEMINI_TTS_FAILED": ("Gemini narration failed. Check server readiness and try again."),
            "WEB_TTS_AUTHORITY_INVALID": (
                "Narration authority validation failed; no video was published."
            ),
            "MP4_VALIDATION_FAILED": "The rendered MP4 could not be validated.",
            "MP4_STREAMS_REQUIRED": "The rendered MP4 must contain audio and video.",
            "MP4_ARTIFACT_INVALID": "The rendered MP4 artifact is invalid.",
            "WORKER_IPC_INVALID": "The render worker returned an invalid status message.",
            "WORKER_PROCESS_EXITED": "The render worker exited before publishing a result.",
            "WORKER_SHUTDOWN_TERMINATED": (
                "The server stopped this running render before it completed."
            ),
            "PIPELINE_FAILED": "Production pipeline failed. Review the sanitized job logs.",
        }
        return self._sanitize(job_id, messages[code])

    @staticmethod
    def _safe_loaded_plan_metadata(value: object) -> dict[str, object] | None:
        if not isinstance(value, dict):
            return None
        allowed = {
            "title",
            "language",
            "theme",
            "media_source",
            "aspect_ratio",
            "total_duration",
            "tts_provider",
            "scene_count",
            "primary_image_provider",
            "fallback_image_provider",
            "pollinations_fallback_used",
            "pollinations_fallback_attempt_count",
            "resolved_image_providers",
        }
        return {
            key: item
            for key, item in value.items()
            if key in allowed
            and (
                isinstance(item, (str, int, float, bool))
                or (isinstance(item, list) and all(isinstance(value, str) for value in item))
            )
        }

    @staticmethod
    def _safe_loaded_tts_metadata(value: object) -> dict[str, object] | None:
        if not isinstance(value, dict):
            return None
        allowed = {
            "provider",
            "narrator_profile",
            "language",
            "fallback_used",
            "continuous_narration",
        }
        return {
            key: item
            for key, item in value.items()
            if key in allowed and isinstance(item, (str, bool))
        }

    @staticmethod
    def _error_code(exc: Exception) -> str:
        text = str(exc)
        for code in (
            "MP4_VALIDATION_FAILED",
            "MP4_STREAMS_REQUIRED",
            "MP4_ARTIFACT_INVALID",
            "WEB_TTS_AUTHORITY_INVALID",
            "WORKER_IPC_INVALID",
            "WORKER_PROCESS_EXITED",
            "WORKER_SHUTDOWN_TERMINATED",
        ):
            if code in text:
                return code
        if "TTS provider gemini failed" in text:
            return "GEMINI_TTS_FAILED"
        return "PIPELINE_FAILED"

    @contextmanager
    def _strict_gemini_environment(self):
        previous = {name: os.environ.get(name) for name in _WEB_ENVIRONMENT}
        os.environ.update(_WEB_ENVIRONMENT)
        try:
            yield
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def web_readiness(
    output_root: Path,
    request: WebRenderRequest | None = None,
    *,
    media_source: str | None = None,
) -> dict[str, object]:
    root = Path(output_root).resolve()
    gemini_ready = bool(
        (os.environ.get("GEMINI_API_KEY") or "").strip()
        or (os.environ.get("GEMINI_API_KEYS") or "").strip()
        or (os.environ.get("GOOGLE_API_KEY") or "").strip()
    )
    narrator_profile_ready = _web_narrator_profile_ready()
    worker_configuration_ready, max_workers = _web_max_workers_setting()
    selected_media_source = (
        request.media_source if request is not None else media_source or "ai_image"
    )
    if selected_media_source != "ai_image":
        raise ValueError("unsupported media source")
    if selected_media_source == "ai_image":
        from tella.media.ai_image import resolve_all_credentials

        image_ready = bool(resolve_all_credentials())
        image_missing = [] if image_ready else ["CF_ACCOUNTS", "CF_ACCOUNT_ID", "CF_AI_TOKEN"]
        image_guidance = "Configure CF_ACCOUNTS or CF_ACCOUNT_ID plus CF_AI_TOKEN."
    else:
        image_ready = bool(
            (os.environ.get("PEXELS_API_KEY") or "").strip()
            or (os.environ.get("PEXELS_API_KEYS") or "").strip()
        )
        image_missing = [] if image_ready else ["PEXELS_API_KEY", "PEXELS_API_KEYS"]
        image_guidance = "Configure PEXELS_API_KEY or PEXELS_API_KEYS."
    gemini_missing = (
        []
        if gemini_ready
        else [
            "GEMINI_API_KEY",
            "GEMINI_API_KEYS",
            "GOOGLE_API_KEY",
        ]
    )
    checks: dict[str, dict[str, object]] = {
        "gemini_planning": {
            "ready": gemini_ready,
            "code": "GEMINI_PLANNING_NOT_CONFIGURED",
            "missing_variables": gemini_missing,
            "guidance": "Set GEMINI_API_KEY, GEMINI_API_KEYS, or GOOGLE_API_KEY.",
        },
        "gemini_narration": {
            "ready": gemini_ready and narrator_profile_ready,
            "code": "GEMINI_NARRATION_NOT_CONFIGURED",
            "missing_variables": gemini_missing,
            "guidance": "Set GEMINI_API_KEY, GEMINI_API_KEYS, or GOOGLE_API_KEY.",
            "profile": _WEB_NARRATOR_PROFILE_ID,
        },
        "image_route": {
            "ready": image_ready,
            "code": "IMAGE_ROUTE_NOT_CONFIGURED",
            "missing_variables": image_missing,
            "guidance": image_guidance,
            "required": True,
        },
        "ffmpeg": {
            "ready": shutil.which("ffmpeg") is not None,
            "code": "FFMPEG_NOT_AVAILABLE",
            "missing_variables": [],
            "guidance": "Install ffmpeg and add it to PATH.",
            "required": True,
        },
        "ffprobe": {
            "ready": shutil.which("ffprobe") is not None,
            "code": "FFPROBE_NOT_AVAILABLE",
            "missing_variables": [],
            "guidance": "Install ffprobe and add it to PATH.",
            "required": True,
        },
        "output": {
            "ready": False,
            "code": "OUTPUT_NOT_WRITABLE",
            "missing_variables": ["TELLA_WEB_OUTPUT_DIR"],
            "guidance": "Make TELLA_WEB_OUTPUT_DIR writable.",
            "required": True,
        },
        "worker_pool": {
            "ready": worker_configuration_ready,
            "code": "WEB_WORKER_CONFIGURATION_INVALID",
            "missing_variables": ([] if worker_configuration_ready else [_MAX_WEB_WORKERS_ENV]),
            "guidance": "Set TELLA_WEB_MAX_WORKERS to 1 or 2.",
            "required": True,
            "max_workers": max_workers if worker_configuration_ready else None,
        },
    }
    pollinations_width, pollinations_height = (
        (1344, 768) if request is not None and request.aspect_ratio == "16:9" else (768, 1344)
    )
    pollinations = pollinations_web_readiness(
        width=pollinations_width,
        height=pollinations_height,
        policy_id=WEB_SENSITIVITY_POLICY_ID,
    )
    checks["pollinations_fallback"] = {
        "ready": pollinations["eligible"],
        "required": False,
        "code": "POLLINATIONS_FALLBACK_NOT_READY",
        "missing_variables": (
            [] if pollinations["credential_present"] else ["POLLINATIONS_API_KEY"]
        ),
        "guidance": "Optional fallback; configure POLLINATIONS_API_KEY server-side.",
        "reason": pollinations["reason"],
        "model": pollinations["model"],
    }
    try:
        root.mkdir(parents=True, exist_ok=True)
        marker = root / f".write-check-{uuid.uuid4().hex}"
        marker.write_bytes(b"ok")
        marker.unlink()
        checks["output"]["ready"] = True
        checks["output"]["missing_variables"] = []
    except OSError:
        pass
    failed_checks = [
        {
            "code": str(item["code"]),
            "missing_variables": list(item["missing_variables"]),
        }
        for item in checks.values()
        if item.get("required", True) and not bool(item["ready"])
    ]
    return {
        "schema_version": 1,
        "ready": all(bool(item["ready"]) for item in checks.values() if item.get("required", True)),
        "checks": checks,
        "tts_provider": "gemini",
        "strict_tts_provider": True,
        "narrator_profile": _WEB_NARRATOR_PROFILE_ID,
        "media_source": selected_media_source,
        "max_workers": max_workers if worker_configuration_ready else None,
        "failed_checks": failed_checks,
    }


__all__ = [
    "JobManager",
    "MediaProbe",
    "PipelineRunner",
    "probe_mp4",
    "sanitize_public_text",
    "web_readiness",
]
