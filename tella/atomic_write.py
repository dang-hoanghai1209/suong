"""Crash-safe atomic writes for small critical local state files."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _fsync_parent_best_effort(parent: Path) -> None:
    if os.name == "nt" or not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = None
    try:
        descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)


def atomic_write_text(path: Path | str, text: str, encoding: str = "utf-8") -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    os.close(descriptor)
    try:
        with open(temporary, "w", encoding=encoding, newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        _fsync_parent_best_effort(destination.parent)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return destination


def atomic_write_json(
    path: Path | str,
    payload: Any,
    *,
    ensure_ascii: bool = False,
    indent: int | None = 2,
    encoding: str = "utf-8",
) -> Path:
    serialized = json.dumps(payload, ensure_ascii=ensure_ascii, indent=indent)
    return atomic_write_text(path, serialized, encoding=encoding)


__all__ = ["atomic_write_json", "atomic_write_text"]
