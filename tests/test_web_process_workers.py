from __future__ import annotations

import json
import os
from pathlib import Path
import time

import pytest

from tella.web_contract import WebJobStatus, WebRenderRequest
from tella.web_jobs import JobManager, web_readiness

_FIXTURE = Path(__file__).with_name("web_process_worker_fixture.py")


def _request(content: str) -> WebRenderRequest:
    return WebRenderRequest(
        input_mode="topic",
        content=content,
        language="en",
        no_music=True,
    )


def _probe(_path: Path) -> dict[str, object]:
    return {"duration_seconds": 1.0, "video_streams": 1, "audio_streams": 1}


def _manager(root: Path, *, max_workers: int) -> JobManager:
    return JobManager(
        root,
        media_probe=_probe,
        max_workers=max_workers,
        worker_module=str(_FIXTURE),
    )


def _wait_for(predicate, *, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("timed out waiting for process-worker condition")
        time.sleep(0.02)


def test_max_workers_one_preserves_serial_execution(tmp_path: Path) -> None:
    manager = _manager(tmp_path, max_workers=1)
    first = manager.create(_request("gate"))
    second = manager.create(_request("gate"))
    try:
        _wait_for(lambda: (tmp_path / f"{first.job_id}.started").is_file())
        assert not (tmp_path / f"{second.job_id}.started").exists()
        assert manager.get(second.job_id).status is WebJobStatus.QUEUED
        (tmp_path / "release").write_text("go", encoding="ascii")
        _wait_for(lambda: manager.get(second.job_id).status is WebJobStatus.SUCCEEDED)
        assert manager.get(first.job_id).status is WebJobStatus.SUCCEEDED
    finally:
        assert manager.close(timeout=5)


def test_max_workers_two_overlap_and_third_remains_queued(tmp_path: Path) -> None:
    manager = _manager(tmp_path, max_workers=2)
    jobs = [manager.create(_request("gate")) for _ in range(3)]
    try:
        _wait_for(
            lambda: len(list(tmp_path.glob("web-*.started"))) >= 2,
        )
        started = list(tmp_path.glob("web-*.started"))
        assert len(started) == 2
        assert manager.get(jobs[2].job_id).status is WebJobStatus.QUEUED
        (tmp_path / "release").write_text("go", encoding="ascii")
        _wait_for(
            lambda: all(manager.get(job.job_id).status is WebJobStatus.SUCCEEDED for job in jobs)
        )
    finally:
        assert manager.close(timeout=5)


def test_child_environments_are_isolated_and_hostile_parent_is_neutralized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELLA_TTS_MODEL", "hostile-parent-model")
    monkeypatch.setenv("TELLA_ALLOW_LOCAL_IMAGE_FALLBACK", "1")
    manager = _manager(tmp_path, max_workers=2)
    jobs = [manager.create(_request(f"environment:worker-{index}")) for index in range(2)]
    try:
        _wait_for(lambda: len(list(tmp_path.glob("web-*.environment.json"))) == 2)
        (tmp_path / "release").write_text("go", encoding="ascii")
        _wait_for(
            lambda: all(manager.get(job.job_id).status is WebJobStatus.SUCCEEDED for job in jobs)
        )
        records = [
            json.loads((tmp_path / f"{job.job_id}.environment.json").read_text(encoding="utf-8"))
            for job in jobs
        ]
        assert all(record["expected_model"] for record in records)
        assert all(record["local_fallback_disabled"] for record in records)
        assert records[0]["token_sha256"] != records[1]["token_sha256"]
        assert os.environ["TELLA_TTS_MODEL"] == "hostile-parent-model"
        assert os.environ["TELLA_ALLOW_LOCAL_IMAGE_FALLBACK"] == "1"
    finally:
        assert manager.close(timeout=5)


@pytest.mark.parametrize(
    ("content", "code"),
    [("crash", "WORKER_PROCESS_EXITED"), ("malformed-ipc", "WORKER_IPC_INVALID")],
)
def test_child_crash_and_malformed_ipc_fail_closed(
    content: str,
    code: str,
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path, max_workers=1)
    job = manager.create(_request(content))
    try:
        _wait_for(lambda: manager.get(job.job_id).status is WebJobStatus.FAILED)
        result = manager.get(job.job_id)
        assert result.error is not None
        assert result.error.code == code
        assert result.output_available is False
        with pytest.raises(KeyError):
            manager.artifact_path(job.job_id)
    finally:
        assert manager.close(timeout=5)


def test_duplicate_polling_does_not_duplicate_process_work(tmp_path: Path) -> None:
    manager = _manager(tmp_path, max_workers=1)
    job = manager.create(_request("gate"))
    try:
        _wait_for(lambda: (tmp_path / f"{job.job_id}.started").is_file())
        for _ in range(50):
            assert manager.get(job.job_id).status is WebJobStatus.RUNNING
        assert len(list(tmp_path.glob(f"{job.job_id}.started"))) == 1
        (tmp_path / "release").write_text("go", encoding="ascii")
        _wait_for(lambda: manager.get(job.job_id).status is WebJobStatus.SUCCEEDED)
    finally:
        assert manager.close(timeout=5)


def test_utf8_request_and_output_root_with_spaces_are_spawn_safe(tmp_path: Path) -> None:
    root = tmp_path / "jobs có khoảng trống"
    manager = _manager(root, max_workers=1)
    job = manager.create(_request("Một bài học nhỏ về sự kiên nhẫn."))
    try:
        _wait_for(lambda: manager.get(job.job_id).status is WebJobStatus.SUCCEEDED)
        assert manager.artifact_path(job.job_id).is_file()
    finally:
        assert manager.close(timeout=5)


def test_child_logs_redact_credentials_and_private_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "test-child-secret-value"
    monkeypatch.setenv("GEMINI_API_KEY", secret)
    manager = _manager(tmp_path, max_workers=1)
    job = manager.create(_request("log-secret"))
    try:
        _wait_for(lambda: manager.get(job.job_id).status is WebJobStatus.SUCCEEDED)
        serialized = json.dumps(manager.get(job.job_id).model_dump(mode="json"))
        assert secret not in serialized
        assert str(tmp_path) not in serialized
        assert "[REDACTED]" in serialized
        assert "[PRIVATE_PATH]" in serialized
    finally:
        assert manager.close(timeout=5)


def test_queued_cancellation_and_restart_recovery_remain_authoritative(tmp_path: Path) -> None:
    manager = _manager(tmp_path, max_workers=1)
    active = manager.create(_request("gate"))
    queued = manager.create(_request("gate"))
    try:
        _wait_for(lambda: manager.get(active.job_id).status is WebJobStatus.RUNNING)
        assert manager.cancel(queued.job_id).status is WebJobStatus.CANCELLED
        assert not (tmp_path / f"{queued.job_id}.started").exists()
        (tmp_path / "release").write_text("go", encoding="ascii")
        _wait_for(lambda: manager.get(active.job_id).status is WebJobStatus.SUCCEEDED)
    finally:
        assert manager.close(timeout=5)
    recovered = JobManager(tmp_path, media_probe=_probe, autostart=False)
    assert recovered.get(active.job_id).status is WebJobStatus.SUCCEEDED
    assert recovered.get(queued.job_id).status is WebJobStatus.CANCELLED
    assert recovered.close()


def test_shutdown_terminates_owned_child_and_persists_failure(tmp_path: Path) -> None:
    manager = _manager(tmp_path, max_workers=1)
    job = manager.create(_request("hang"))
    marker = tmp_path / f"{job.job_id}.started"
    _wait_for(lambda: marker.is_file() and marker.stat().st_size > 0)
    pid = int(marker.read_text(encoding="ascii"))

    assert manager.close(timeout=0.01)
    result = manager.get(job.job_id)
    assert result.status is WebJobStatus.FAILED
    assert result.error is not None
    assert result.error.code == "WORKER_SHUTDOWN_TERMINATED"
    with pytest.raises(OSError):
        os.kill(pid, 0)
    assert manager.close(timeout=0)


@pytest.mark.parametrize("value", ["0", "3", "-1", "1.0", "true", ""])
def test_invalid_worker_configuration_fails_readiness_and_submission(
    value: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELLA_WEB_MAX_WORKERS", value)
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setenv("CF_ACCOUNT_ID", "test")
    monkeypatch.setenv("CF_AI_TOKEN", "test")
    monkeypatch.setattr("tella.web_jobs.shutil.which", lambda _name: "tool")
    readiness = web_readiness(tmp_path)
    assert readiness["ready"] is False
    assert readiness["checks"]["worker_pool"]["ready"] is False
    manager = JobManager(tmp_path / "jobs", autostart=False)
    with pytest.raises(RuntimeError, match="TELLA_WEB_MAX_WORKERS_INVALID"):
        manager.create(_request("blocked"))
    assert manager.close()


def test_default_worker_count_is_one_and_browser_cannot_set_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TELLA_WEB_MAX_WORKERS", raising=False)
    manager = JobManager(tmp_path, autostart=False)
    assert manager.max_workers == 1
    payload = _request("safe").model_dump()
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        WebRenderRequest.model_validate({**payload, "max_workers": 2})
    assert manager.close()
