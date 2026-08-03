import asyncio
import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import tella.cli as cli
from tella.production_lock import (
    JobLockConflict,
    LOCK_FILENAME,
    ProductionJobLock,
)
from tella.recipes import get_recipe


def test_first_acquisition_conflict_independent_jobs_and_release(tmp_path):
    first = ProductionJobLock(tmp_path / "one").acquire()
    assert first.path.is_file()
    with pytest.raises(JobLockConflict):
        ProductionJobLock(tmp_path / "one").acquire()
    other = ProductionJobLock(tmp_path / "two").acquire()
    assert other.path.is_file()
    assert other.release()
    assert first.release()
    assert not first.path.exists()


@pytest.mark.parametrize(
    "exc", [RuntimeError("boom"), KeyboardInterrupt(), SystemExit(7)]
)
def test_context_exit_releases_on_exception_and_interrupt(tmp_path, exc):
    lock = ProductionJobLock(tmp_path / type(exc).__name__)
    with pytest.raises(type(exc)):
        with lock:
            raise exc
    assert not lock.path.exists()


def test_release_requires_matching_token_and_preserves_other_owner(tmp_path):
    lock = ProductionJobLock(tmp_path).acquire()
    metadata = json.loads(lock.path.read_text())
    metadata["lock_token"] = "another-owner"
    lock.path.write_text(json.dumps(metadata), encoding="utf-8")
    assert lock.release() is False
    assert lock.path.is_file()


def _stale_metadata(hostname=None, pid=999999, token="stale-token"):
    return {
        "lock_schema_version": 1,
        "lock_token": token,
        "pid": pid,
        "hostname": hostname or socket.gethostname(),
        "acquired_at_utc": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
        "job_id": "job",
        "recipe_id": "practical_life_steps_callirrhoe_v1",
        "operation": "production-run",
    }


def test_stale_recovery_is_explicit_conservative_and_same_host(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    path = job / LOCK_FILENAME
    path.write_text(json.dumps(_stale_metadata()), encoding="utf-8")
    with pytest.raises(JobLockConflict):
        ProductionJobLock(job, process_checker=lambda pid: False).acquire()
    recovered = ProductionJobLock(
        job, recover_stale=True, process_checker=lambda pid: False,
    ).acquire()
    assert recovered.release()

    path.write_text(json.dumps(_stale_metadata()), encoding="utf-8")
    with pytest.raises(JobLockConflict):
        ProductionJobLock(job, recover_stale=True, process_checker=lambda pid: True).acquire()
    assert path.is_file()

    path.write_text(json.dumps(_stale_metadata(hostname="remote.example")), encoding="utf-8")
    with pytest.raises(JobLockConflict):
        ProductionJobLock(job, recover_stale=True, process_checker=lambda pid: False).acquire()
    assert path.is_file()


def test_malformed_or_unverifiable_lock_is_never_recovered(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    path = job / LOCK_FILENAME
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(JobLockConflict, match="malformed"):
        ProductionJobLock(job, recover_stale=True, process_checker=lambda pid: False).acquire()
    assert path.read_text() == "not-json"

    path.write_text(json.dumps(_stale_metadata()), encoding="utf-8")
    with pytest.raises(JobLockConflict):
        ProductionJobLock(job, recover_stale=True, process_checker=lambda pid: None).acquire()
    assert path.is_file()


def test_stale_recovery_rechecks_token_immediately_before_delete(tmp_path, monkeypatch):
    job = tmp_path / "job"
    job.mkdir()
    path = job / LOCK_FILENAME
    path.write_text(json.dumps(_stale_metadata(token="original")), encoding="utf-8")
    lock = ProductionJobLock(job, recover_stale=True, process_checker=lambda pid: False)
    real_read = lock._read_existing
    calls = 0

    def racing_read():
        nonlocal calls
        calls += 1
        if calls == 2:
            changed = _stale_metadata(token="new-owner")
            path.write_text(json.dumps(changed), encoding="utf-8")
        return real_read()

    monkeypatch.setattr(lock, "_read_existing", racing_read)
    with pytest.raises(JobLockConflict):
        lock.acquire()
    assert json.loads(path.read_text())["lock_token"] == "new-owner"


def test_lock_metadata_is_safe(tmp_path):
    with ProductionJobLock(
        tmp_path / "safe-job",
        recipe_id="practical_life_steps_callirrhoe_v1",
        operation="Authorization Bearer secret API_KEY=secret",
    ) as lock:
        text = lock.path.read_text()
        metadata = json.loads(text)
        assert metadata["lock_schema_version"] == 1
        assert metadata["pid"] == os.getpid()
        assert metadata["job_id"] == "safe-job"
        assert metadata["operation"] == "redacted-operation"
        assert "Bearer secret" not in text
        assert "API_KEY" not in text


def test_exclusive_create_blocks_independent_process(tmp_path):
    job = tmp_path / "process-job"
    with ProductionJobLock(job):
        code = (
            "from tella.production_lock import ProductionJobLock,JobLockConflict;"
            f"p=r'''{job}''';"
            "\ntry:\n ProductionJobLock(p).acquire()\nexcept JobLockConflict:\n raise SystemExit(23)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=20,
        )
    assert result.returncode == 23


def test_production_dry_run_conflict_modifies_no_artifact_and_calls_nothing(tmp_path, monkeypatch):
    job = tmp_path / "locked"
    job.mkdir()
    sentinel = job / "sentinel.txt"
    sentinel.write_text("unchanged")
    forbidden = lambda *a, **k: pytest.fail("provider, media, socket, or render called")
    monkeypatch.setattr(cli, "run_pipeline", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    with ProductionJobLock(job):
        lock_before = (job / LOCK_FILENAME).read_bytes()
        result = cli.main([
            "--recipe", "practical_life_steps_callirrhoe_v1",
            "--production-dry-run", "--out", str(tmp_path), "--job-id", "locked",
        ])
        assert result == 2
        assert (job / LOCK_FILENAME).read_bytes() == lock_before
        assert sentinel.read_text() == "unchanged"
        assert set(item.name for item in job.iterdir()) == {LOCK_FILENAME, "sentinel.txt"}


def test_normal_and_resume_pipeline_lock_before_unlocked_work(tmp_path, monkeypatch):
    job = tmp_path / "production"
    forbidden_calls = []

    async def forbidden(**kwargs):
        forbidden_calls.append(kwargs)
        raise AssertionError("pipeline work started before lock")

    monkeypatch.setattr(cli, "_run_pipeline_unlocked", forbidden)
    kwargs = dict(
        topic="local", target_lang="vi", theme="practical_life_steps",
        media_source="ai_image", duration_mode="short", aspect_ratio="9:16",
        voice_pace_name=None, voice_rate_custom=None, voice_gender=None,
        out_root=tmp_path, job_id="production",
        recipe=get_recipe("practical_life_steps_callirrhoe_v1"),
    )
    with ProductionJobLock(job):
        with pytest.raises(JobLockConflict):
            asyncio.run(cli.run_pipeline(**kwargs))
        with pytest.raises(JobLockConflict):
            asyncio.run(cli.run_pipeline(**kwargs, resume=True))
    assert forbidden_calls == []


def test_all_production_modes_preserve_locked_job_byte_for_byte(tmp_path, monkeypatch):
    job = tmp_path / "all-modes"
    job.mkdir()
    (job / "existing.json").write_text('{"unchanged":true}', encoding="utf-8")
    external_calls = []

    async def forbidden_pipeline(**kwargs):
        external_calls.append("pipeline")
        raise AssertionError("production work started before lock admission")

    def forbidden_external(*args, **kwargs):
        external_calls.append("external")
        raise AssertionError("external work attempted")

    monkeypatch.setattr(cli, "_run_pipeline_unlocked", forbidden_pipeline)
    monkeypatch.setattr(socket, "create_connection", forbidden_external)
    recipe = get_recipe("practical_life_steps_callirrhoe_v1")
    kwargs = dict(
        topic="local", target_lang="vi", theme="practical_life_steps",
        media_source="ai_image", duration_mode="short", aspect_ratio="9:16",
        voice_pace_name=None, voice_rate_custom=None, voice_gender=None,
        out_root=tmp_path, job_id="all-modes", recipe=recipe,
    )

    def snapshot():
        return {
            str(path.relative_to(job)): path.read_bytes()
            for path in sorted(job.rglob("*")) if path.is_file()
        }

    with ProductionJobLock(job, recipe_id=recipe.recipe_id):
        before = snapshot()
        assert cli.main([
            "--recipe", recipe.recipe_id, "--production-dry-run",
            "--out", str(tmp_path), "--job-id", "all-modes",
        ]) == 2
        assert snapshot() == before
        with pytest.raises(JobLockConflict):
            asyncio.run(cli.run_pipeline(**kwargs))
        assert snapshot() == before
        with pytest.raises(JobLockConflict):
            asyncio.run(cli.run_pipeline(**kwargs, resume=True))
        assert snapshot() == before
    assert external_calls == []
