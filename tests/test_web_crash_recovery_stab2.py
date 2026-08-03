from __future__ import annotations

import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

import tella.web_jobs as web_jobs
import tella.web_process_control as process_control
from tella.web import ProductionWebServer
from tella.web_contract import WebJobStatus, WebRenderRequest
from tella.web_jobs import JobManager

_FIXTURE = Path(__file__).with_name("web_process_worker_fixture.py")
_JOB_ID = "web-0123456789abcdef01234567"
_MP4 = b"partial-or-validated-video"
_CREATE_CONNECTION = socket.create_connection
_TTS_PROJECTION = {
    "provider": "gemini",
    "narrator_profile": "gemini_callirrhoe_vi_gentle_emotional",
    "language": "en",
    "fallback_used": False,
    "continuous_narration": True,
}


def _request() -> WebRenderRequest:
    return WebRenderRequest(
        input_mode="script",
        content="A bounded recovery narration.",
        language="en",
        no_music=True,
    )


def _payload(*, status: str = "running", phase: str = "planning", progress: int = 25) -> dict:
    return {
        "schema_version": 1,
        "job_id": _JOB_ID,
        "status": status,
        "phase": phase,
        "progress": progress,
        "created_at": "2026-08-01T00:00:00Z",
        "started_at": "2026-08-01T00:00:01Z" if status != "queued" else None,
        "finished_at": None,
        "logs": [f"last safe phase: {phase}"],
        "error": None,
        "output_path": None,
        "output_bytes": None,
        "output_sha256": None,
        "probe": None,
        "plan_metadata": None,
        "tts_metadata": None,
        "storage": None,
        "recovery": None,
        "retry_job_id": None,
        "request": _request().model_dump(mode="json"),
        "ownership": {
            "server_instance": "a" * 32,
            "child_id": "b" * 32,
            "job_id": _JOB_ID,
            "started_at": "2026-08-01T00:00:01Z",
        },
    }


def _succeeded_payload() -> dict:
    payload = _payload(status="succeeded", phase="complete", progress=100)
    payload.update(
        finished_at="2026-08-01T00:00:03Z",
        ownership=None,
        output_path="video.mp4",
        output_bytes=len(_MP4),
        output_sha256=hashlib.sha256(_MP4).hexdigest(),
        probe={"duration_seconds": 1.5, "video_streams": 1, "audio_streams": 1},
        plan_metadata={},
        tts_metadata=dict(_TTS_PROJECTION),
    )
    return payload


def _write_job(root: Path, payload: dict, *, raw: bytes | None = None) -> Path:
    directory = root / _JOB_ID
    directory.mkdir(parents=True)
    metadata = directory / "job.json"
    metadata.write_bytes(raw if raw is not None else json.dumps(payload).encode("utf-8"))
    return metadata


def _probe(path: Path) -> dict[str, object]:
    assert path.read_bytes() == _MP4
    return {"duration_seconds": 1.5, "video_streams": 1, "audio_streams": 1}


def _manager(root: Path, *, autostart: bool = False) -> JobManager:
    return JobManager(
        root,
        media_probe=_probe,
        autostart=autostart,
        minimum_free_bytes=0,
        max_workers=1,
    )


@pytest.mark.parametrize(
    ("phase", "progress"),
    [
        ("planning", 25),
        ("visuals", 40),
        ("narration", 55),
        ("composition", 70),
        ("rendering", 85),
    ],
)
def test_running_phase_recovery_is_truthful_idempotent_and_retains_diagnostics(
    tmp_path: Path,
    phase: str,
    progress: int,
) -> None:
    metadata = _write_job(tmp_path, _payload(phase=phase, progress=progress))
    partial = metadata.parent / "video.mp4"
    partial.write_bytes(_MP4)

    first = _manager(tmp_path)
    recovered = first.get(_JOB_ID)
    persisted = json.loads(metadata.read_text(encoding="utf-8"))
    assert recovered.status is WebJobStatus.FAILED
    assert recovered.phase == phase
    assert recovered.progress == progress < 100
    assert recovered.output_available is False
    assert recovered.preview_url is recovered.download_url is None
    assert recovered.error is not None
    assert recovered.error.code == "SERVER_RESTART_INTERRUPTED"
    assert persisted["ownership"] is None
    assert persisted["output_path"] is persisted["probe"] is None
    assert persisted["recovery"]["previous_status"] == "running"
    assert partial.read_bytes() == _MP4
    recovery = persisted["recovery"]

    second = _manager(tmp_path)
    assert second.get(_JOB_ID) == recovered
    assert json.loads(metadata.read_text(encoding="utf-8"))["recovery"] == recovery


@pytest.mark.parametrize("publication_stage", ["mp4-created", "ffprobe-complete"])
def test_unpublished_mp4_never_becomes_authority_after_restart(
    tmp_path: Path,
    publication_stage: str,
) -> None:
    calls = 0

    def forbidden_probe(_path: Path) -> dict[str, object]:
        nonlocal calls
        calls += 1
        raise AssertionError("recovery must not probe an interrupted job")

    payload = _payload(phase="validating", progress=95)
    if publication_stage == "ffprobe-complete":
        payload["probe"] = {"duration_seconds": 1.5, "video_streams": 1, "audio_streams": 1}
        payload["output_path"] = "video.mp4"
        payload["output_bytes"] = len(_MP4)
    metadata = _write_job(tmp_path, payload)
    (metadata.parent / "video.mp4").write_bytes(_MP4)
    manager = JobManager(tmp_path, media_probe=forbidden_probe, autostart=False)
    assert manager.get(_JOB_ID).status is WebJobStatus.FAILED
    assert calls == 0
    with pytest.raises(KeyError):
        manager.artifact_path(_JOB_ID)


def test_queued_recovery_fails_without_execution_and_preserves_order_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload(status="queued", phase="queued", progress=0)
    payload["ownership"] = None
    metadata = _write_job(tmp_path, payload)
    monkeypatch.setattr(
        JobManager,
        "_start_child",
        lambda *_args, **_kwargs: pytest.fail("startup must not execute a persisted queued job"),
    )
    manager = _manager(tmp_path, autostart=True)
    time.sleep(0.05)
    recovered = manager.get(_JOB_ID)
    assert recovered.status is WebJobStatus.FAILED
    assert recovered.error is not None
    assert recovered.error.code == "SERVER_RESTART_INTERRUPTED"
    assert (
        json.loads(metadata.read_text(encoding="utf-8"))["recovery"]["previous_status"] == "queued"
    )
    assert not (metadata.parent / "video.mp4").exists()
    assert manager.close()


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_existing_terminal_failure_states_remain_terminal(tmp_path: Path, status: str) -> None:
    payload = _payload(status=status, phase=status, progress=42)
    payload["finished_at"] = "2026-08-01T00:00:02Z"
    payload["ownership"] = None
    payload["error"] = {"code": "PIPELINE_FAILED", "message": "Safe failure."}
    _write_job(tmp_path, payload)
    manager = _manager(tmp_path)
    result = manager.get(_JOB_ID)
    assert result.status.value == status
    assert result.progress == 42
    assert result.output_available is False


def test_existing_validated_success_remains_published(tmp_path: Path) -> None:
    payload = _succeeded_payload()
    metadata = _write_job(tmp_path, payload)
    (metadata.parent / "video.mp4").write_bytes(_MP4)
    before = metadata.read_bytes()
    manager = _manager(tmp_path)
    result = manager.get(_JOB_ID)
    assert result.status is WebJobStatus.SUCCEEDED
    assert result.output_available is True
    assert manager.artifact_path(_JOB_ID).read_bytes() == _MP4
    assert metadata.read_bytes() == before


@pytest.mark.parametrize(
    "damage",
    [
        "missing-mp4",
        "empty-mp4",
        "byte-count-mismatch",
        "sha-mismatch",
        "non-positive-duration",
        "missing-video-stream",
        "missing-audio-stream",
        "probe-failure",
        "missing-plan-metadata",
        "missing-tts-metadata",
    ],
)
def test_invalid_persisted_success_fails_authoritatively_and_idempotently(
    tmp_path: Path,
    damage: str,
) -> None:
    payload = _succeeded_payload()
    metadata = _write_job(tmp_path, payload)
    artifact = metadata.parent / "video.mp4"
    artifact.write_bytes(_MP4)
    media_probe = _probe
    if damage == "missing-mp4":
        artifact.unlink()
    elif damage == "empty-mp4":
        artifact.write_bytes(b"")
    elif damage == "byte-count-mismatch":
        payload["output_bytes"] += 1
    elif damage == "sha-mismatch":
        payload["output_sha256"] = "0" * 64
    elif damage == "non-positive-duration":
        payload["probe"]["duration_seconds"] = 0
    elif damage == "missing-video-stream":
        payload["probe"]["video_streams"] = 0
    elif damage == "missing-audio-stream":
        payload["probe"]["audio_streams"] = 0
    elif damage == "probe-failure":

        def failed_probe(_path: Path) -> dict[str, object]:
            raise RuntimeError("MP4_VALIDATION_FAILED")

        media_probe = failed_probe
    elif damage == "missing-plan-metadata":
        payload["plan_metadata"] = None
    elif damage == "missing-tts-metadata":
        payload["tts_metadata"] = None
    metadata.write_text(json.dumps(payload), encoding="utf-8")
    original_artifact = artifact.read_bytes() if artifact.exists() else None

    manager = JobManager(
        tmp_path,
        media_probe=media_probe,
        autostart=False,
        minimum_free_bytes=0,
        max_workers=1,
    )
    failed = manager.get(_JOB_ID)
    persisted = json.loads(metadata.read_text(encoding="utf-8"))
    assert failed.status is WebJobStatus.FAILED
    assert failed.phase == "failed"
    assert failed.progress == 99
    assert failed.output_available is False
    assert failed.preview_url is failed.download_url is None
    assert failed.error is not None
    assert failed.error.code == "PERSISTED_ARTIFACT_INVALID"
    assert persisted["status"] == "failed"
    assert persisted["phase"] == "failed"
    assert persisted["progress"] == 99
    assert persisted["created_at"] == payload["created_at"]
    assert persisted["started_at"] == payload["started_at"]
    assert persisted["finished_at"] == payload["finished_at"]
    assert persisted["logs"] == payload["logs"]
    assert persisted["request"] == payload["request"]
    assert persisted["recovery"]["previous_status"] == "succeeded"
    assert persisted["recovery"]["reason"] == "persisted_artifact_invalid"
    assert persisted["recovery"]["recovered_at"]
    for field in (
        "output_path",
        "output_bytes",
        "output_sha256",
        "probe",
        "plan_metadata",
        "tts_metadata",
        "ownership",
    ):
        assert persisted[field] is None
    if original_artifact is not None:
        assert artifact.read_bytes() == original_artifact

    first_restart = metadata.read_bytes()
    restarted = _manager(tmp_path)
    assert restarted.get(_JOB_ID) == failed
    assert metadata.read_bytes() == first_restart


def test_invalid_persisted_success_rejects_artifact_routes_and_retries_once(
    tmp_path: Path,
) -> None:
    payload = _succeeded_payload()
    payload["output_sha256"] = "0" * 64
    metadata = _write_job(tmp_path, payload)
    artifact = metadata.parent / "video.mp4"
    artifact.write_bytes(_MP4)
    manager = _manager(tmp_path)
    before_retry = manager.get(_JOB_ID)
    server = ProductionWebServer(("127.0.0.1", 0), manager)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    retry = None
    duplicate = None
    try:
        for action in ("preview", "download"):
            for headers in ({}, {"Range": "bytes=0-3"}):
                connection = HTTPConnection(*server.server_address, timeout=5)
                connection._create_connection = _CREATE_CONNECTION
                try:
                    connection.request(
                        "GET",
                        f"/api/jobs/{_JOB_ID}/{action}",
                        headers=headers,
                    )
                    response = connection.getresponse()
                    assert response.status == 404
                    response.read()
                finally:
                    connection.close()
        retry = manager.retry(_JOB_ID)
        duplicate = manager.retry(_JOB_ID)
        assert manager.get(_JOB_ID) == before_retry
        assert artifact.read_bytes() == _MP4
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert retry is not None
    assert duplicate is not None
    assert retry.job_id != _JOB_ID
    assert duplicate.job_id == retry.job_id


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b'{"schema_version":1',
        b"\xff\xfe\x00",
        b"{" + b" " * (256 * 1024) + b"}",
    ],
    ids=["empty", "truncated", "invalid-utf8", "oversized"],
)
def test_corrupt_metadata_is_bounded_quarantined_and_non_authoritative(
    tmp_path: Path,
    raw: bytes,
) -> None:
    metadata = _write_job(tmp_path, {}, raw=raw)
    manager = _manager(tmp_path)
    assert manager.list() == ()
    assert not metadata.exists()
    quarantined = tuple(metadata.parent.glob("job.corrupt-*.json"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == raw
    with pytest.raises(KeyError):
        manager.compact(_JOB_ID)
    with pytest.raises(KeyError):
        manager.artifact_path(_JOB_ID)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "mystery"),
        ("progress", True),
        ("progress", 101),
        ("created_at", "2026-99-99T00:00:00Z"),
        ("phase", "x" * 101),
    ],
)
def test_structurally_invalid_metadata_is_quarantined(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    payload = _payload()
    payload[field] = value
    metadata = _write_job(tmp_path, payload)
    assert _manager(tmp_path).list() == ()
    assert not metadata.exists()
    assert len(tuple(metadata.parent.glob("job.corrupt-*.json"))) == 1


@pytest.mark.parametrize("output_path", ["../video.mp4", "/tmp/video.mp4", "C:\\video.mp4"])
def test_invalid_succeeded_artifact_paths_fail_without_touching_outside_files(
    tmp_path: Path, output_path: str
) -> None:
    payload = _succeeded_payload()
    payload["output_path"] = output_path
    metadata = _write_job(tmp_path, payload)
    outside = tmp_path / "video.mp4"
    outside.write_bytes(_MP4)
    failed = _manager(tmp_path).get(_JOB_ID)
    assert failed.status is WebJobStatus.FAILED
    assert failed.output_available is False
    assert outside.read_bytes() == _MP4
    persisted = json.loads(metadata.read_text(encoding="utf-8"))
    assert persisted["error"]["code"] == "PERSISTED_ARTIFACT_INVALID"


def test_stale_or_malformed_pid_metadata_never_kills_a_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload()
    payload["ownership"]["pid"] = os.getpid()
    metadata = _write_job(tmp_path, payload)
    calls: list[object] = []
    monkeypatch.setattr(web_jobs, "_terminate_process_tree", lambda process: calls.append(process))
    assert _manager(tmp_path).list() == ()
    assert calls == []
    assert not metadata.exists()


def test_retry_is_distinct_idempotent_and_revalidates_disk(tmp_path: Path) -> None:
    free = SimpleNamespace(value=10_000)
    manager = JobManager(
        tmp_path,
        autostart=False,
        minimum_free_bytes=100,
        max_workers=1,
        disk_usage=lambda _: SimpleNamespace(free=free.value),
    )
    original = manager.create(_request())
    original_path = tmp_path / original.job_id / "job.json"
    diagnostic = tmp_path / original.job_id / "partial-diagnostic.bin"
    diagnostic.write_bytes(b"retain-original")
    cancelled = manager.cancel(original.job_id)
    before = original_path.read_bytes()
    retry = manager.retry(cancelled.job_id)
    duplicate = manager.retry(cancelled.job_id)
    assert retry.job_id != original.job_id
    assert duplicate.job_id == retry.job_id
    assert manager.get(original.job_id).status is WebJobStatus.CANCELLED
    assert json.loads(original_path.read_text(encoding="utf-8"))[
        "request"
    ] == _request().model_dump(mode="json")
    assert before != original_path.read_bytes()  # only the successor identity was added
    assert diagnostic.read_bytes() == b"retain-original"
    assert len(manager.list()) == 2

    second = manager.create(_request())
    manager.cancel(second.job_id)
    free.value = 99
    with pytest.raises(RuntimeError, match="^INSUFFICIENT_DISK_SPACE$"):
        manager.retry(second.job_id)
    assert len(manager.list()) == 3


def test_recovered_partial_artifact_is_not_previewable_and_explicit_compaction_is_safe(
    tmp_path: Path,
) -> None:
    metadata = _write_job(tmp_path, _payload(phase="rendering", progress=85))
    partial = metadata.parent / "video.mp4"
    partial.write_bytes(_MP4)
    manager = _manager(tmp_path)
    server = ProductionWebServer(("127.0.0.1", 0), manager)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection(*server.server_address, timeout=5)
    connection._create_connection = _CREATE_CONNECTION
    try:
        connection.request("GET", f"/api/jobs/{_JOB_ID}/preview")
        response = connection.getresponse()
        assert response.status == 404
        response.read()
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert partial.exists()

    restarted = _manager(tmp_path)
    result = restarted.compact(_JOB_ID)
    assert "video.mp4" in result.removed_entries
    assert restarted.get(_JOB_ID).status is WebJobStatus.FAILED
    assert _manager(tmp_path).get(_JOB_ID).status is WebJobStatus.FAILED


def test_parent_control_eof_revokes_worker_authority() -> None:
    read_fd, write_fd = os.pipe()
    revoked = threading.Event()
    with os.fdopen(read_fd, "rb", buffering=0) as reader:
        watcher = process_control.start_parent_control_watcher(reader, terminate=revoked.set)
        os.close(write_fd)
        assert revoked.wait(2)
        watcher.join(timeout=2)
        assert not watcher.is_alive()


class _ExitCalled(Exception):
    pass


def test_platform_fallback_never_targets_a_persisted_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    exits: list[int] = []

    def fake_exit(code: int) -> None:
        exits.append(code)
        raise _ExitCalled

    monkeypatch.setattr(process_control.os, "_exit", fake_exit)
    monkeypatch.setattr(process_control.os, "name", "nt")
    with pytest.raises(_ExitCalled):
        process_control.terminate_own_process_tree()

    monkeypatch.setattr(process_control.os, "name", "posix")
    groups: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(process_control.os, "getpgrp", lambda: 24680, raising=False)
    monkeypatch.setattr(
        process_control.os,
        "killpg",
        lambda group, sig: groups.append((group, sig)),
        raising=False,
    )
    with pytest.raises(_ExitCalled):
        process_control.terminate_own_process_tree()
    assert groups == [(24680, signal.SIGTERM)]
    assert exits == [70, 70]


def _pid_exists(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_abrupt_parent_death_revokes_worker_and_owned_subprocess(tmp_path: Path) -> None:
    root = tmp_path / "parent-death"
    root.mkdir()
    launcher = (
        "import time; from pathlib import Path; "
        "from tella.web_contract import WebRenderRequest; "
        "from tella.web_jobs import JobManager; "
        f"root=Path({str(root)!r}); fixture={str(_FIXTURE)!r}; "
        "manager=JobManager(root,max_workers=1,minimum_free_bytes=0,worker_module=fixture); "
        "job=manager.create(WebRenderRequest(input_mode='topic',content='owned-subprocess',"
        "language='en',no_music=True)); "
        "(root/'launcher-job-id').write_text(job.job_id,encoding='ascii'); "
        "time.sleep(60)"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", launcher],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
    )
    deadline = time.monotonic() + 10
    marker: Path | None = None
    while time.monotonic() < deadline:
        markers = tuple(root.glob("web-*.owned-child"))
        if markers:
            marker = markers[0]
            break
        time.sleep(0.02)
    assert marker is not None
    job_id = (root / "launcher-job-id").read_text(encoding="ascii")
    worker_pid = int((root / f"{job_id}.started").read_text(encoding="ascii"))
    owned_pid = int(marker.read_text(encoding="ascii"))
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(parent.pid), "/F"],
            check=False,
            capture_output=True,
            timeout=5,
            shell=False,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
    else:
        os.kill(parent.pid, signal.SIGKILL)
    parent.wait(timeout=5)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and (_pid_exists(worker_pid) or _pid_exists(owned_pid)):
        time.sleep(0.05)
    assert not _pid_exists(worker_pid)
    assert not _pid_exists(owned_pid)
    recovered = _manager(root)
    result = recovered.get(job_id)
    assert result.status is WebJobStatus.FAILED
    assert result.error is not None
    assert result.error.code == "SERVER_RESTART_INTERRUPTED"
    assert result.output_available is False
    assert recovered.close()


def test_recovery_redacts_secret_path_and_provider_body(tmp_path: Path, monkeypatch) -> None:
    secret = "stab2-secret-sentinel"
    monkeypatch.setenv("GEMINI_API_KEY", secret)
    payload = _payload(status="failed", phase="failed", progress=42)
    payload.update(
        finished_at="2026-08-01T00:00:03Z",
        ownership=None,
        logs=[f"Bearer {secret} C:\\private\\file HTTP 500: raw provider response"],
        error={
            "code": "unknown",
            "message": f"api_key={secret} provider response body: private body",
        },
    )
    _write_job(tmp_path, payload)
    serialized = json.dumps(_manager(tmp_path).get(_JOB_ID).model_dump(mode="json"))
    assert secret not in serialized
    assert "private body" not in serialized
    assert "C:\\private" not in serialized
    assert "[REDACTED]" in serialized
