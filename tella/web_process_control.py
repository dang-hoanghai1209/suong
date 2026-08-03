"""Private parent-control authority for one process-isolated web worker."""

from __future__ import annotations

import os
import signal
import threading
from typing import BinaryIO, Callable


def terminate_own_process_tree() -> None:
    """Terminate this worker and only the process tree rooted beneath it."""
    if os.name == "nt":
        # Windows workers use prepare_worker_process_guard() instead. Reaching
        # this fallback means the kill-on-close authority was unavailable.
        os._exit(70)

    try:
        os.killpg(os.getpgrp(), signal.SIGTERM)
    except ProcessLookupError:
        pass
    os._exit(70)


def prepare_worker_process_guard() -> Callable[[], None] | None:
    """Bind this Windows worker tree to a private kill-on-close Job Object."""
    if os.name != "nt":
        return terminate_own_process_tree

    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class BASIC_LIMITS(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class EXTENDED_LIMITS(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BASIC_LIMITS),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    limits = EXTENDED_LIMITS()
    limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
    configured = kernel32.SetInformationJobObject(
        job,
        9,  # JobObjectExtendedLimitInformation
        ctypes.byref(limits),
        ctypes.sizeof(limits),
    )
    assigned = configured and kernel32.AssignProcessToJobObject(
        job,
        kernel32.GetCurrentProcess(),
    )
    if not assigned:
        kernel32.CloseHandle(job)
        return None

    close_lock = threading.Lock()
    closed = False

    def revoke() -> None:
        nonlocal closed
        with close_lock:
            if closed:
                return
            closed = True
            kernel32.CloseHandle(job)

    return revoke


def start_parent_control_watcher(
    control: BinaryIO,
    *,
    terminate: Callable[[], None] = terminate_own_process_tree,
) -> threading.Thread:
    """Terminate the worker when its inherited parent-owned pipe reaches EOF."""

    def watch() -> None:
        try:
            # The parent never writes after the one request line. Any byte or EOF
            # therefore revokes the unique control channel for this worker.
            os.read(control.fileno(), 1)
        except OSError:
            pass
        terminate()

    watcher = threading.Thread(
        target=watch,
        name="tella-web-parent-control",
        daemon=True,
    )
    watcher.start()
    return watcher


__all__ = [
    "prepare_worker_process_guard",
    "start_parent_control_watcher",
    "terminate_own_process_tree",
]
