"""Strict filesystem adapter for decoded ScriptInput narration."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from .execution_models import ExecutionMode
from .script_input import (
    CharacterScopeRequest,
    HintedNarrationScriptInput,
    NarrationScriptInput,
    SceneHint,
)
from .strategy import VisualExecutionMode


# No repository-wide script size authority exists. One MiB is intentionally
# generous for narration while preventing unbounded adapter reads.
DEFAULT_MAX_SCRIPT_INPUT_BYTES = 1_048_576


class ScriptInputFileErrorCode(StrEnum):
    MISSING_FILE = "MISSING_FILE"
    UNREADABLE_FILE = "UNREADABLE_FILE"
    OVERSIZED_FILE = "OVERSIZED_FILE"
    INVALID_UTF8 = "INVALID_UTF8"
    SYMLINK_NOT_ALLOWED = "SYMLINK_NOT_ALLOWED"


class ScriptInputFileError(ValueError):
    def __init__(self, code: ScriptInputFileErrorCode, path: Path, reason: str):
        self.code = code
        self.path = path
        self.reason = reason
        super().__init__(f"{code.value}: {reason}")

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code.value, "reason": self.reason}


def narration_input_from_file(
    path: str | Path,
    *,
    language: str,
    scene_hints: list[SceneHint] | None = None,
    requested_scene_count_range: tuple[int, int] = (7, 8),
    visual_mode: VisualExecutionMode = VisualExecutionMode.ILLUSTRATED_SCENE,
    character_scope_request: CharacterScopeRequest | None = None,
    execution_mode: ExecutionMode = ExecutionMode.LIVE_PRODUCTION,
    max_bytes: int = DEFAULT_MAX_SCRIPT_INPUT_BYTES,
) -> NarrationScriptInput | HintedNarrationScriptInput:
    """Read a regular non-symlink file as strict UTF-8 decoded text."""

    candidate = Path(path)
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    is_junction = getattr(candidate, "is_junction", lambda: False)
    if candidate.is_symlink() or bool(is_junction()):
        raise ScriptInputFileError(
            ScriptInputFileErrorCode.SYMLINK_NOT_ALLOWED,
            candidate,
            "script input symlinks and junctions are not allowed",
        )
    try:
        size = candidate.stat().st_size
    except FileNotFoundError as exc:
        raise ScriptInputFileError(
            ScriptInputFileErrorCode.MISSING_FILE,
            candidate,
            "script input file does not exist",
        ) from exc
    except OSError as exc:
        raise ScriptInputFileError(
            ScriptInputFileErrorCode.UNREADABLE_FILE,
            candidate,
            "script input file metadata is unreadable",
        ) from exc
    if not candidate.is_file():
        raise ScriptInputFileError(
            ScriptInputFileErrorCode.MISSING_FILE,
            candidate,
            "script input path is not a regular file",
        )
    if size > max_bytes:
        raise ScriptInputFileError(
            ScriptInputFileErrorCode.OVERSIZED_FILE,
            candidate,
            f"script input exceeds {max_bytes} bytes",
        )
    try:
        content = candidate.read_bytes()
    except OSError as exc:
        raise ScriptInputFileError(
            ScriptInputFileErrorCode.UNREADABLE_FILE,
            candidate,
            "script input file cannot be read",
        ) from exc
    if len(content) > max_bytes:
        raise ScriptInputFileError(
            ScriptInputFileErrorCode.OVERSIZED_FILE,
            candidate,
            f"script input exceeds {max_bytes} bytes",
        )
    try:
        narration = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ScriptInputFileError(
            ScriptInputFileErrorCode.INVALID_UTF8,
            candidate,
            "script input is not valid UTF-8",
        ) from exc
    common = {
        "narration": narration,
        "language": language,
        "requested_scene_count_range": requested_scene_count_range,
        "visual_mode": visual_mode,
        "character_scope_request": character_scope_request or CharacterScopeRequest(),
        "execution_mode": execution_mode,
    }
    if scene_hints is not None:
        return HintedNarrationScriptInput(scene_hints=scene_hints, **common)
    return NarrationScriptInput(**common)


__all__ = [
    "DEFAULT_MAX_SCRIPT_INPUT_BYTES",
    "ScriptInputFileError",
    "ScriptInputFileErrorCode",
    "narration_input_from_file",
]
