"""Saved channels — a small name + avatar the wizard can pick from.

A "channel" is just a brand label (and optional avatar image) shown on the
video. Define one by dropping a folder under ``channels/`` at the repo root:

    channels/
      my-brand/
        channel.json     -> {"name": "My Brand"}
        avatar.png        -> optional square image (logo / face)

The wizard lists these so you don't retype the name every run. You can also
just type a fresh name in the wizard without saving a channel at all.

Only the ``name`` is shown on screen — there is no handle/slug on the video.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("tella.channels")

# Repo-root ``channels/`` (this file lives at tella/channels.py).
_CHANNELS_DIR = Path(__file__).resolve().parent.parent / "channels"

_AVATAR_NAMES = ("avatar.png", "avatar.jpg", "avatar.jpeg", "avatar.webp")


@dataclass(frozen=True)
class Channel:
    slug: str
    name: str
    avatar_path: str | None  # absolute path, or None


def _find_avatar(folder: Path) -> str | None:
    for n in _AVATAR_NAMES:
        p = folder / n
        if p.is_file():
            return str(p)
    return None


def list_channels() -> list[Channel]:
    """Return saved channels (sorted by name), skipping malformed folders."""
    if not _CHANNELS_DIR.is_dir():
        return []
    out: list[Channel] = []
    for folder in sorted(_CHANNELS_DIR.iterdir()):
        if not folder.is_dir():
            continue
        cfg = folder / "channel.json"
        if not cfg.is_file():
            continue
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            name = (data.get("name") or "").strip()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("skipping channel %s: %s", folder.name, exc)
            continue
        if not name:
            continue
        out.append(Channel(slug=folder.name, name=name, avatar_path=_find_avatar(folder)))
    out.sort(key=lambda c: c.name.lower())
    return out


__all__ = ["Channel", "list_channels"]
