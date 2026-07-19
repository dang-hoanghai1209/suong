"""Style Bible loading and validation."""
from __future__ import annotations

import json
from pathlib import Path

from .models import StyleBible


def load_style_bible(path: Path | str) -> StyleBible:
    source = Path(path)
    return StyleBible.model_validate_json(source.read_text(encoding="utf-8"))


def write_style_snapshot(style: StyleBible, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(style.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
