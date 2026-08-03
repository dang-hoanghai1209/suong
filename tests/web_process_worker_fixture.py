"""Process-only worker fixture for deterministic local-web scheduler tests."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from tella.web_process_control import prepare_worker_process_guard, start_parent_control_watcher


def _emit(event: dict[str, object]) -> None:
    print(json.dumps(event, sort_keys=True, separators=(",", ":")), flush=True)


def _write_success_artifacts(root: Path, job_id: str) -> Path:
    job_dir = root / job_id
    artifact = job_dir / "video.mp4"
    artifact.write_bytes(b"bounded-process-worker-mp4")
    (job_dir / "plan.json").write_text(
        json.dumps(
            {
                "title": "Process worker fixture",
                "language": "en",
                "theme": "cinematic",
                "media_source": "ai_image",
                "aspect_ratio": "9:16",
                "total_duration": 1.0,
                "tts_provider": "gemini",
                "scenes": [{}],
            }
        ),
        encoding="utf-8",
    )
    (job_dir / "tts_metadata.json").write_text(
        json.dumps(
            {
                "requested_provider": "gemini",
                "tts_provider": "gemini",
                "tts_model": "gemini-2.5-flash-preview-tts",
                "tts_voice": "Callirrhoe",
                "tts_style": "gentle_emotional",
                "tts_language": "vi-VN",
                "fallback_used": False,
                "tts_continuous": True,
            }
        ),
        encoding="utf-8",
    )
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--server-instance", required=True)
    parser.add_argument("--child-id", required=True)
    args = parser.parse_args()
    request = json.loads(sys.stdin.buffer.readline())
    process_guard = prepare_worker_process_guard()
    if process_guard is None:
        return 2
    start_parent_control_watcher(sys.stdin.buffer, terminate=process_guard)
    content = str(request["content"])
    root = Path(args.output_root)
    started = root / f"{args.job_id}.started"
    started.write_text(str(os.getpid()), encoding="ascii")
    _emit({"type": "log", "message": "step 2/6 fixture planning"})

    if content == "crash":
        os._exit(17)
    if content == "malformed-ipc":
        print("not-json", flush=True)
        return 0
    if content == "oversized-ipc":
        print("x" * 9_000, flush=True)
        return 0
    if content == "eof":
        return 0
    if content == "invalid-terminal":
        _emit({"type": "success", "output_path": None})
        return 0
    if content == "failure":
        _emit({"type": "failure", "code": "PIPELINE_FAILED"})
        return 1
    if content == "log-secret":
        _emit(
            {
                "type": "log",
                "message": (
                    f"Authorization: Bearer {os.environ.get('GEMINI_API_KEY', '')} "
                    f"private root {root.resolve()}"
                ),
            }
        )
    if content.startswith("environment:"):
        expected = "gemini-2.5-flash-preview-tts"
        observed = os.environ.get("TELLA_TTS_MODEL", "")
        hostile = os.environ.get("TELLA_ALLOW_LOCAL_IMAGE_FALLBACK", "")
        token = content.partition(":")[2]
        (root / f"{args.job_id}.environment.json").write_text(
            json.dumps(
                {
                    "expected_model": observed == expected,
                    "local_fallback_disabled": hostile == "0",
                    "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                }
            ),
            encoding="utf-8",
        )
        os.environ["TELLA_TTS_MODEL"] = token
    if content in {"gate", "hang"} or content.startswith("environment:"):
        deadline = time.monotonic() + 20
        while not (root / "release").exists():
            if time.monotonic() >= deadline:
                _emit({"type": "failure", "code": "PIPELINE_FAILED"})
                return 1
            time.sleep(0.02)

    if content == "owned-subprocess":
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        (root / f"{args.job_id}.owned-child").write_text(str(child.pid), encoding="ascii")
        while True:
            time.sleep(0.05)

    artifact = _write_success_artifacts(root, args.job_id)
    _emit({"type": "success", "output_path": str(artifact.resolve())})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
