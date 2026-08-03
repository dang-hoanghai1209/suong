"""Canonical, local-only narration scripts for controlled acceptance cases."""
from __future__ import annotations

import hashlib
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


CANONICAL_SCRIPT_VERSION = 1


class CanonicalScriptReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    script_version: int = Field(ge=1)
    script_path: str = Field(min_length=1, max_length=300)
    canonical_script_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    script_source: Literal["human_reviewed"]
    script_required: bool = True

    @field_validator("script_version")
    @classmethod
    def supported_version(cls, value: int) -> int:
        if value != CANONICAL_SCRIPT_VERSION:
            raise ValueError(f"unsupported canonical script version: {value}")
        return value

    @field_validator("script_path")
    @classmethod
    def safe_repository_path(cls, value: str) -> str:
        if "\\" in value:
            raise ValueError("canonical script path must use repository-relative POSIX separators")
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "." in path.parts:
            raise ValueError("canonical script path must not escape the repository")
        return value


class CanonicalScript(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    script_version: int
    script_path: str
    canonical_script_sha256: str
    script_source: str
    sentences: tuple[str, ...]
    canonical_narration_text: str
    scene_count: int

    def identity(self) -> dict[str, object]:
        return {
            "script_version": self.script_version,
            "script_path": self.script_path,
            "canonical_script_sha256": self.canonical_script_sha256,
            "script_source": self.script_source,
            "script_scene_count": self.scene_count,
        }


def canonicalize_script_bytes(
    content: bytes,
    *,
    expected_scene_count: int = 7,
) -> tuple[tuple[str, ...], str, str]:
    """Return sentences, canonical LF/NFC text, and its stable SHA256.

    An optional UTF-8 BOM is supported and removed. Exactly one final LF is
    included in the canonical representation.
    """
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("canonical script is not valid UTF-8") from exc
    if text.startswith("\ufeff"):
        text = text[1:]
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines or any(line == "" for line in lines):
        raise ValueError("canonical script must not contain blank lines")
    if any(line != line.strip() for line in lines):
        raise ValueError("canonical script lines must not contain leading or trailing whitespace")
    if len(lines) != expected_scene_count:
        raise ValueError(
            f"canonical script requires exactly {expected_scene_count} lines; got {len(lines)}"
        )
    canonical = "\n".join(lines) + "\n"
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return tuple(lines), canonical, digest


def load_canonical_script(
    reference: CanonicalScriptReference,
    repository_root: Path,
    *,
    expected_scene_count: int = 7,
) -> CanonicalScript:
    root = Path(repository_root).resolve()
    candidate = root.joinpath(*PurePosixPath(reference.script_path).parts)
    is_junction = getattr(candidate, "is_junction", lambda: False)
    if candidate.is_symlink() or bool(is_junction()):
        raise ValueError("canonical script must not be a symlink or junction")
    resolved = candidate.resolve()
    if resolved == root or root not in resolved.parents:
        raise ValueError("canonical script path escapes the repository")
    if not resolved.is_file():
        raise ValueError("canonical script file is missing or is not a regular file")
    sentences, canonical, digest = canonicalize_script_bytes(
        resolved.read_bytes(), expected_scene_count=expected_scene_count
    )
    if digest != reference.canonical_script_sha256:
        raise ValueError("canonical script SHA256 mismatch")
    return CanonicalScript(
        script_version=reference.script_version,
        script_path=reference.script_path,
        canonical_script_sha256=digest,
        script_source=reference.script_source,
        sentences=sentences,
        canonical_narration_text=canonical,
        scene_count=len(sentences),
    )


__all__ = [
    "CANONICAL_SCRIPT_VERSION",
    "CanonicalScript",
    "CanonicalScriptReference",
    "canonicalize_script_bytes",
    "load_canonical_script",
]
