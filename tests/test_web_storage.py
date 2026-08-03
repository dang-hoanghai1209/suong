from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import socket
import threading
from types import SimpleNamespace
import pytest

from tella.web import ProductionWebServer
from tella.web_contract import WebJobStatus, WebRenderRequest
from tella.web_jobs import JobManager
from tella.voice_profiles import get_voice_profile

_PROFILE = get_voice_profile("gemini_callirrhoe_vi_gentle_emotional")
_MP4 = b"validated-mp4-bytes"
_CREATE_CONNECTION = socket.create_connection


def _request() -> WebRenderRequest:
    return WebRenderRequest(
        input_mode="script",
        content="A short exact narration.",
        language="en",
        aspect_ratio="9:16",
        theme="cinematic",
        duration_mode="short",
        no_music=True,
    )


def _probe(path: Path) -> dict[str, object]:
    assert path.read_bytes() == _MP4
    return {"duration_seconds": 1.0, "video_streams": 1, "audio_streams": 1}


def _write_tts_metadata(job_dir: Path) -> None:
    (job_dir / "tts_metadata.json").write_text(
        json.dumps(
            {
                "requested_provider": "gemini",
                "tts_provider": "gemini",
                "tts_model": _PROFILE.model,
                "tts_voice": _PROFILE.voice,
                "tts_style": _PROFILE.style,
                "tts_language": "en",
                "fallback_used": False,
                "tts_continuous": True,
            }
        ),
        encoding="utf-8",
    )


def _write_intermediates(job_dir: Path) -> None:
    (job_dir / "assets").mkdir(exist_ok=True)
    (job_dir / "assets" / "narration.wav").write_bytes(b"RIFF-intermediate")
    (job_dir / "_render").mkdir(exist_ok=True)
    (job_dir / "_render" / "scene_01.mp4").write_bytes(b"scene")
    (job_dir / "plan.json").write_text("{}", encoding="utf-8")
    _write_tts_metadata(job_dir)
    for name in ("audio_qc.json", "music_metadata.json", "render_timing.json"):
        (job_dir / name).write_text("{}", encoding="utf-8")


async def _successful_runner(**kwargs: object) -> Path:
    job_dir = Path(str(kwargs["out_root"])) / str(kwargs["job_id"])
    _write_intermediates(job_dir)
    output = job_dir / "video.mp4"
    output.write_bytes(_MP4)
    return output


async def _failed_runner(**kwargs: object) -> Path:
    job_dir = Path(str(kwargs["out_root"])) / str(kwargs["job_id"])
    _write_intermediates(job_dir)
    (job_dir / "video.mp4").write_bytes(b"unpublished")
    raise RuntimeError("bounded test failure")


def _wait(manager: JobManager, job_id: str) -> object:
    async def wait_for_terminal() -> object:
        for _ in range(500):
            job = manager.get(job_id)
            if job.status not in {WebJobStatus.QUEUED, WebJobStatus.RUNNING}:
                return job
            await asyncio.sleep(0.01)
        raise AssertionError("job did not reach a terminal state")

    return asyncio.run(wait_for_terminal())


def _manager(
    root: Path,
    *,
    runner: object = _successful_runner,
    free: int = 10_000,
    minimum: int = 0,
    autostart: bool = True,
) -> JobManager:
    return JobManager(
        root,
        runner=runner,  # type: ignore[arg-type]
        media_probe=_probe,
        max_workers=1,
        minimum_free_bytes=minimum,
        disk_usage=lambda _: SimpleNamespace(free=free),
        autostart=autostart,
    )


def _start_server(manager: JobManager) -> tuple[ProductionWebServer, object]:
    server = ProductionWebServer(("127.0.0.1", 0), manager)
    executor = ThreadPoolExecutor(max_workers=1)
    executor.submit(server.serve_forever)
    return server, executor


def _request_http(
    server: ProductionWebServer,
    method: str,
    path: str,
    body: dict[str, object] | None = None,
) -> tuple[int, bytes]:
    encoded = json.dumps(body).encode("utf-8") if body is not None else None
    connection = HTTPConnection(*server.server_address, timeout=5)
    connection._create_connection = _CREATE_CONNECTION
    try:
        connection.request(
            method,
            path,
            body=encoded,
            headers={"Content-Type": "application/json"} if encoded else {},
        )
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


@pytest.mark.parametrize("free,allowed", [(999, False), (1000, True)])
def test_low_disk_guard_is_exact_and_creates_no_job_or_directory(
    tmp_path: Path, free: int, allowed: bool
) -> None:
    root = tmp_path / "output root with spaces"
    manager = _manager(root, free=free, minimum=1000, autostart=False)
    before = tuple(root.iterdir())

    if allowed:
        created = manager.create(_request())
        assert created.status is WebJobStatus.QUEUED
        assert (root / created.job_id / "job.json").is_file()
    else:
        with pytest.raises(RuntimeError, match="^INSUFFICIENT_DISK_SPACE$"):
            manager.create(_request())
        assert tuple(root.iterdir()) == before == ()


@pytest.mark.parametrize("value", ["-1", "1.0", "true", "  "])
def test_invalid_minimum_free_space_configuration_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("TELLA_WEB_MIN_FREE_BYTES", value)
    root = tmp_path / "jobs"
    manager = JobManager(
        root,
        runner=_successful_runner,
        media_probe=_probe,
        max_workers=1,
        disk_usage=lambda _: SimpleNamespace(free=10_000),
        autostart=False,
    )
    assert manager.storage_summary().submissions_allowed is False
    with pytest.raises(RuntimeError, match="^TELLA_WEB_MIN_FREE_BYTES_INVALID$"):
        manager.create(_request())
    assert tuple(root.iterdir()) == ()


def test_storage_summary_is_relative_safe_and_does_not_follow_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "jobs with spaces"
    manager = _manager(root, free=9000, minimum=1000, autostart=False)
    created = manager.create(_request())
    job_dir = root / created.job_id
    (job_dir / "assets").mkdir()
    (job_dir / "assets" / "small.bin").write_bytes(b"12345")
    outside = tmp_path / "outside-large.bin"
    outside.write_bytes(b"x" * 100_000)
    link = job_dir / "assets" / "outside-link"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    summary = manager.storage_summary()
    serialized = json.dumps(summary.model_dump(mode="json"))
    assert summary.submissions_allowed is True
    assert summary.free_disk_bytes == 9000
    assert summary.minimum_free_bytes == 1000
    assert len(summary.jobs) == 1
    assert summary.jobs[0].bytes < outside.stat().st_size
    assert str(root.resolve()) not in serialized
    assert str(outside.resolve()) not in serialized


def test_successful_compaction_is_idempotent_restart_safe_and_retains_artifact(
    tmp_path: Path,
) -> None:
    root = tmp_path / "web jobs"
    manager = _manager(root)
    created = manager.create(_request())
    finished = _wait(manager, created.job_id)
    assert finished.status is WebJobStatus.SUCCEEDED
    job_dir = root / created.job_id

    first = manager.compact(created.job_id)
    assert first.removed_bytes > 0
    assert set(first.removed_entries) == {
        "_render",
        "assets",
        "audio_qc.json",
        "music_metadata.json",
        "plan.json",
        "render_timing.json",
        "tts_metadata.json",
    }
    assert (job_dir / "job.json").is_file()
    assert manager.artifact_path(created.job_id).read_bytes() == _MP4
    assert not (job_dir / "assets").exists()

    second = manager.compact(created.job_id)
    assert second.removed_bytes == 0
    assert second.removed_entries == ()
    persisted = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert persisted["storage"]["complete"] is True
    manager.close()

    restarted = _manager(root, autostart=False)
    recovered = restarted.get(created.job_id)
    assert recovered.status is WebJobStatus.SUCCEEDED
    assert recovered.output_available is True
    assert restarted.artifact_path(created.job_id).read_bytes() == _MP4


def test_failed_job_cleanup_removes_unpublished_output_and_retains_truth(tmp_path: Path) -> None:
    root = tmp_path / "failed jobs"
    manager = _manager(root, runner=_failed_runner)
    created = manager.create(_request())
    failed = _wait(manager, created.job_id)
    assert failed.status is WebJobStatus.FAILED
    job_dir = root / created.job_id

    result = manager.compact(created.job_id)
    assert result.removed_bytes > 0
    assert not (job_dir / "video.mp4").exists()
    assert (job_dir / "job.json").is_file()
    persisted = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert persisted["status"] == "failed"
    assert persisted["storage"]["complete"] is True


def test_restart_drops_malformed_storage_metadata_without_exposing_paths(tmp_path: Path) -> None:
    root = tmp_path / "jobs"
    manager = _manager(root)
    created = manager.create(_request())
    _wait(manager, created.job_id)
    manager.compact(created.job_id)
    manager.close()
    metadata_path = root / created.job_id / "job.json"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    private_value = str((tmp_path / "private-storage-marker").resolve())
    payload["storage"]["compacted_at"] = private_value
    metadata_path.write_text(json.dumps(payload), encoding="utf-8")

    restarted = _manager(root, autostart=False)
    recovered = restarted.get(created.job_id)
    assert recovered.storage_compacted_at is None
    assert restarted.storage_summary().jobs[0].compacted_at is None
    assert private_value not in json.dumps(recovered.model_dump(mode="json"))


def test_active_jobs_and_noncanonical_ids_are_rejected_without_changes(tmp_path: Path) -> None:
    root = tmp_path / "jobs"
    manager = _manager(root, autostart=False)
    created = manager.create(_request())
    before = (root / created.job_id / "job.json").read_bytes()

    with pytest.raises(ValueError, match="^ACTIVE_JOB_STORAGE_LOCKED$"):
        manager.compact(created.job_id)
    with pytest.raises(KeyError):
        manager.compact("../outside")
    assert (root / created.job_id / "job.json").read_bytes() == before


def test_running_job_compaction_is_rejected_without_touching_artifacts(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    async def blocked_runner(**kwargs: object) -> Path:
        job_dir = Path(str(kwargs["out_root"])) / str(kwargs["job_id"])
        _write_intermediates(job_dir)
        started.set()
        assert await asyncio.to_thread(release.wait, 5)
        output = job_dir / "video.mp4"
        output.write_bytes(_MP4)
        return output

    root = tmp_path / "jobs"
    manager = _manager(root, runner=blocked_runner)
    created = manager.create(_request())
    assert started.wait(5)
    try:
        assert manager.get(created.job_id).status is WebJobStatus.RUNNING
        before = (root / created.job_id / "assets" / "narration.wav").read_bytes()
        with pytest.raises(ValueError, match="^ACTIVE_JOB_STORAGE_LOCKED$"):
            manager.compact(created.job_id)
        assert (root / created.job_id / "assets" / "narration.wav").read_bytes() == before
    finally:
        release.set()
        _wait(manager, created.job_id)
        manager.close()


def test_compaction_rejects_symlinks_unknown_entries_and_root_escape(tmp_path: Path) -> None:
    root = tmp_path / "jobs"
    manager = _manager(root)
    created = manager.create(_request())
    _wait(manager, created.job_id)
    job_dir = root / created.job_id
    unknown = job_dir / "unrelated.txt"
    unknown.write_text("retain", encoding="utf-8")
    before = manager.artifact_path(created.job_id).read_bytes()
    with pytest.raises(ValueError, match="^STORAGE_UNKNOWN_ENTRY$"):
        manager.compact(created.job_id)
    assert unknown.read_text(encoding="utf-8") == "retain"
    assert manager.artifact_path(created.job_id).read_bytes() == before
    unknown.unlink()

    outside = tmp_path / "outside"
    outside.mkdir()
    link = job_dir / "assets" / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    with pytest.raises(ValueError, match="^STORAGE_PATH_UNSAFE$"):
        manager.compact(created.job_id)
    assert outside.is_dir()
    assert manager.artifact_path(created.job_id).read_bytes() == before


def test_compaction_rejects_job_directory_symlink_outside_output_root(tmp_path: Path) -> None:
    root = tmp_path / "jobs"
    manager = _manager(root)
    created = manager.create(_request())
    _wait(manager, created.job_id)
    job_dir = root / created.job_id
    outside = tmp_path / "outside-job"
    job_dir.rename(outside)
    try:
        job_dir.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")

    with pytest.raises(ValueError, match="^STORAGE_PATH_UNSAFE$"):
        manager.compact(created.job_id)
    assert (outside / "job.json").is_file()
    assert (outside / "video.mp4").read_bytes() == _MP4


def test_compaction_and_download_access_are_safe_when_concurrent(tmp_path: Path) -> None:
    root = tmp_path / "jobs"
    manager = _manager(root)
    created = manager.create(_request())
    _wait(manager, created.job_id)

    server, server_executor = _start_server(manager)
    barrier = threading.Barrier(2)

    def download() -> tuple[int, bytes]:
        barrier.wait()
        return _request_http(server, "GET", f"/api/jobs/{created.job_id}/download")

    def compact() -> tuple[int, bytes]:
        barrier.wait()
        return _request_http(
            server,
            "POST",
            f"/api/jobs/{created.job_id}/compact",
            {"confirm": True},
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            download_result = executor.submit(download)
            compact_result = executor.submit(compact)
            assert download_result.result() == (200, _MP4)
            assert compact_result.result()[0] == 200
    finally:
        server.shutdown()
        server.server_close()
        server_executor.shutdown()

    assert manager.artifact_path(created.job_id).read_bytes() == _MP4


def test_api_low_disk_and_compaction_confirmation_are_fail_closed(tmp_path: Path) -> None:
    low_manager = _manager(tmp_path / "low", free=999, minimum=1000, autostart=False)
    server, executor = _start_server(low_manager)
    try:
        status, body = _request_http(
            server,
            "POST",
            "/api/jobs",
            _request().model_dump(mode="json"),
        )
        assert status == 507
        assert json.loads(body)["error"]["code"] == "INSUFFICIENT_DISK_SPACE"
        assert tuple((tmp_path / "low").iterdir()) == ()
    finally:
        server.shutdown()
        server.server_close()
        executor.shutdown()

    manager = _manager(tmp_path / "normal")
    created = manager.create(_request())
    _wait(manager, created.job_id)
    server, executor = _start_server(manager)
    try:
        status, body = _request_http(server, "GET", "/api/storage")
        assert status == 200
        storage_payload = json.loads(body)
        assert storage_payload["jobs"][0]["job_id"] == created.job_id
        assert str((tmp_path / "normal").resolve()) not in body.decode("utf-8")
        status, _ = _request_http(
            server,
            "POST",
            f"/api/jobs/{created.job_id}/compact",
            {"confirm": False},
        )
        assert status == 400
        assert (tmp_path / "normal" / created.job_id / "assets").is_dir()
        status, body = _request_http(
            server,
            "POST",
            f"/api/jobs/{created.job_id}/compact",
            {"confirm": True},
        )
        assert status == 200
        assert json.loads(body)["job_id"] == created.job_id
        status, preview = _request_http(server, "GET", f"/api/jobs/{created.job_id}/preview")
        assert status == 200
        assert preview == _MP4
    finally:
        server.shutdown()
        server.server_close()
        executor.shutdown()


def test_storage_paths_remain_cross_platform_and_inside_output_root(tmp_path: Path) -> None:
    root = tmp_path / "output root with spaces" / "nested"
    manager = _manager(root, autostart=False)
    created = manager.create(_request())
    canonical = (root / created.job_id).resolve()
    assert os.path.commonpath((root.resolve(), canonical)) == str(root.resolve())
    assert canonical.name == created.job_id
    assert manager.storage_summary().jobs[0].job_id == created.job_id
