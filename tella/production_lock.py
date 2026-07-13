"""Cross-platform exclusive local lock for a production job directory."""
from __future__ import annotations

import json
import os
import re
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

LOCK_SCHEMA_VERSION = 1
LOCK_FILENAME = ".tella-job.lock"


class JobLockConflict(RuntimeError):
    pass


def _safe_operation(value: str) -> str:
    text = re.sub(r"[\r\n\t]+", " ", str(value or "production")).strip()[:80]
    if any(marker in text.lower() for marker in ("key", "token", "authorization", "bearer", "credential")):
        return "redacted-operation"
    return text or "production"


def _process_running_posix(pid: int) -> bool | None:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None


def _process_running_windows(pid: int) -> bool | None:
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            return False if error == 87 else None
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return None
            return exit_code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, ImportError):
        return None


def process_running(pid: int) -> bool | None:
    if not isinstance(pid, int) or pid <= 0:
        return None
    return _process_running_windows(pid) if os.name == "nt" else _process_running_posix(pid)


class ProductionJobLock:
    def __init__(
        self,
        job_dir: Path | str,
        *,
        recipe_id: str = "",
        operation: str = "production",
        recover_stale: bool = False,
        stale_after_seconds: float = 30.0,
        process_checker: Callable[[int], bool | None] = process_running,
    ):
        self.job_dir = Path(job_dir)
        self.path = self.job_dir / LOCK_FILENAME
        self.recipe_id = str(recipe_id or "")[:80]
        self.operation = _safe_operation(operation)
        self.recover_stale = recover_stale
        self.stale_after_seconds = max(1.0, float(stale_after_seconds))
        self.process_checker = process_checker
        self.token = uuid.uuid4().hex
        self.acquired = False

    def _metadata(self) -> dict:
        return {
            "lock_schema_version": LOCK_SCHEMA_VERSION,
            "lock_token": self.token,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "acquired_at_utc": datetime.now(timezone.utc).isoformat(),
            "job_id": self.job_dir.name,
            "recipe_id": self.recipe_id,
            "operation": self.operation,
        }

    def _read_existing(self) -> dict | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def _recover_if_allowed(self, metadata: dict | None) -> bool:
        if not self.recover_stale or not metadata:
            return False
        try:
            hostname = str(metadata["hostname"])
            pid = int(metadata["pid"])
            acquired = datetime.fromisoformat(str(metadata["acquired_at_utc"]).replace("Z", "+00:00"))
            token = str(metadata["lock_token"])
        except (KeyError, TypeError, ValueError):
            return False
        age = (datetime.now(timezone.utc) - acquired.astimezone(timezone.utc)).total_seconds()
        if hostname != socket.gethostname() or age < self.stale_after_seconds:
            return False
        if self.process_checker(pid) is not False:
            return False
        current = self._read_existing()
        if not current or current.get("lock_token") != token:
            return False
        try:
            self.path.unlink()
            return True
        except OSError:
            return False

    def acquire(self) -> "ProductionJobLock":
        self.job_dir.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError as exc:
                owner = self._read_existing()
                if attempt == 0 and self._recover_if_allowed(owner):
                    continue
                details = "metadata malformed or unavailable"
                if owner:
                    details = (
                        f"pid={owner.get('pid', 'unknown')} "
                        f"host={owner.get('hostname', 'unknown')} "
                        f"acquired={owner.get('acquired_at_utc', 'unknown')}"
                    )
                raise JobLockConflict(f"production job is already locked: {details}") from exc
            try:
                payload = json.dumps(self._metadata(), ensure_ascii=True, indent=2).encode("utf-8")
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                self.acquired = True
                return self
            except BaseException:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                self.path.unlink(missing_ok=True)
                raise
        raise JobLockConflict("production job lock could not be acquired")

    def release(self) -> bool:
        if not self.acquired:
            return False
        metadata = self._read_existing()
        if not metadata or metadata.get("lock_token") != self.token:
            self.acquired = False
            return False
        try:
            self.path.unlink()
            return True
        finally:
            self.acquired = False

    def __enter__(self) -> "ProductionJobLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self.release()
        return False


__all__ = [
    "JobLockConflict", "LOCK_FILENAME", "LOCK_SCHEMA_VERSION",
    "ProductionJobLock", "process_running",
]
