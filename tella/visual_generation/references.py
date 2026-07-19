"""Reference discovery, validation, and hashing."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .models import ReferenceAsset

REFERENCE_FILES = {
    "style_anchor": ("scene_01_style_anchor.png", "master", 2),
    "female_identity_anchor": ("scene_01_style_anchor.png", "master", 1),
    "couple_identity_anchor": ("scene_02_couple_anchor.png", "master", 1),
    "daily_vignette_reference": ("scene_03_daily_vignette.png", "scene_type", 3),
    "emotional_metaphor_reference": (
        "scene_04_emotional_metaphor.png",
        "scene_type",
        3,
    ),
}


class ReferenceMissingError(FileNotFoundError):
    """Raised with every missing required proof reference."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_reference_catalog(root: Path | str) -> dict[str, ReferenceAsset]:
    root_path = Path(root).resolve()
    missing = sorted({
        filename
        for filename, _, _ in REFERENCE_FILES.values()
        if not (root_path / filename).is_file()
    })
    if missing:
        formatted = ", ".join(str(root_path / filename) for filename in missing)
        raise ReferenceMissingError(f"required visual references missing: {formatted}")

    catalog: dict[str, ReferenceAsset] = {}
    for role, (filename, source, priority) in REFERENCE_FILES.items():
        path = (root_path / filename).resolve()
        catalog[role] = ReferenceAsset(
            role=role,
            path=path,
            sha256=sha256_file(path),
            source=source,
            priority=priority,
        )
    return catalog
