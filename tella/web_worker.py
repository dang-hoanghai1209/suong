"""Spawn-safe production worker for one validated local-web render job."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
import re
import sys
import threading

from tella.cli import run_pipeline
from tella.web_contract import MAX_WEB_REQUEST_BYTES, WebInputMode, WebRenderRequest
from tella.web_process_control import prepare_worker_process_guard, start_parent_control_watcher

_JOB_ID = re.compile(r"^web-[0-9a-f]{24}$")
_INSTANCE_ID = re.compile(r"^[0-9a-f]{32}$")
_MAX_EVENT_CHARS = 4_000
_PROTOCOL_LOCK = threading.Lock()


def _emit(event: dict[str, object]) -> None:
    encoded = json.dumps(
        event,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    with _PROTOCOL_LOCK:
        sys.stdout.write(encoded + "\n")
        sys.stdout.flush()


class _ProtocolLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage().replace("\r", " ").replace("\n", " ")
            _emit({"type": "log", "message": message[:_MAX_EVENT_CHARS]})
        except Exception:
            return


def _error_code(exc: Exception) -> str:
    text = str(exc)
    for code in (
        "MP4_VALIDATION_FAILED",
        "MP4_STREAMS_REQUIRED",
        "MP4_ARTIFACT_INVALID",
        "WEB_TTS_AUTHORITY_INVALID",
    ):
        if code in text:
            return code
    if "TTS provider gemini failed" in text:
        return "GEMINI_TTS_FAILED"
    return "PIPELINE_FAILED"


async def _execute(request: WebRenderRequest, *, output_root: Path, job_id: str) -> Path:
    return await run_pipeline(
        topic=request.content if request.input_mode == WebInputMode.TOPIC else "exact script",
        target_lang=request.language,
        theme=request.theme,
        media_source=request.media_source,
        duration_mode=request.duration_mode,
        aspect_ratio=request.aspect_ratio,
        voice_pace_name=None,
        voice_rate_custom=None,
        voice_gender=None,
        out_root=output_root,
        job_id=job_id,
        google_tts_api_key="",
        google_tts_voice="",
        user_script=request.content if request.input_mode == WebInputMode.SCRIPT else None,
        allow_local_image_fallback=False,
        dry_run_plan=False,
        stop_after_images=False,
        tts_continuous=True,
        no_music=request.no_music,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--server-instance", required=True)
    parser.add_argument("--child-id", required=True)
    args = parser.parse_args(argv)
    if (
        not _JOB_ID.fullmatch(args.job_id)
        or not _INSTANCE_ID.fullmatch(args.server_instance)
        or not _INSTANCE_ID.fullmatch(args.child_id)
    ):
        return 2
    output_root = Path(args.output_root).resolve()
    raw_request = sys.stdin.buffer.readline(MAX_WEB_REQUEST_BYTES + 2)
    if not raw_request.endswith(b"\n") or len(raw_request) > MAX_WEB_REQUEST_BYTES + 1:
        return 2
    try:
        request = WebRenderRequest.model_validate_json(raw_request[:-1], strict=True)
    except ValueError:
        return 2
    process_guard = prepare_worker_process_guard()
    if process_guard is None:
        return 2
    start_parent_control_watcher(sys.stdin.buffer, terminate=process_guard)

    root_logger = logging.getLogger()
    previous_level = root_logger.level
    handler = _ProtocolLogHandler(logging.INFO)
    if root_logger.getEffectiveLevel() > logging.INFO:
        root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)
    try:
        _emit({"type": "log", "message": "Starting real Tella production pipeline."})
        output = asyncio.run(_execute(request, output_root=output_root, job_id=args.job_id))
        _emit({"type": "success", "output_path": str(Path(output).resolve())})
        return 0
    except Exception as exc:
        _emit({"type": "failure", "code": _error_code(exc)})
        return 1
    finally:
        root_logger.removeHandler(handler)
        root_logger.setLevel(previous_level)


if __name__ == "__main__":
    raise SystemExit(main())
