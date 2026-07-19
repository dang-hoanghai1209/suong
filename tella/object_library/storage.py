"""Filesystem layout and record persistence."""

from __future__ import annotations

import json
import os
from pathlib import Path

from tella.atomic_write import atomic_write_bytes, atomic_write_json
from tella.object_library.models import ObjectRecord


def default_root() -> Path:
    return (
        Path(os.environ.get("TELLA_OBJECT_LIBRARY_ROOT", "object_library_data"))
        .expanduser()
        .resolve()
    )


class ObjectStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root).expanduser().resolve() if root else default_root()

    def initialize(self) -> None:
        for name in ("raw", "processed", "previews", "records", "manifests"):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def asset_path(self, area: str, record: ObjectRecord, extension: str) -> Path:
        return (
            self.root / area / record.source / f"{record.object_id}.{extension.lstrip('.').lower()}"
        )

    def write_raw(self, record: ObjectRecord, content: bytes) -> Path:
        path = self.asset_path("raw", record, record.original_format)
        atomic_write_bytes(path, content)
        return path

    def save_record(self, record: ObjectRecord) -> Path:
        path = self.root / "records" / f"{record.object_id}.json"
        atomic_write_json(path, record.model_dump(mode="json"))
        return path

    def load_records(self) -> list[ObjectRecord]:
        records = []
        directory = self.root / "records"
        if not directory.exists():
            return records
        for path in sorted(directory.glob("*.json")):
            try:
                records.append(
                    ObjectRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))
                )
            except (OSError, ValueError):
                continue
        return records
