"""Validated local licensed music catalog and deterministic selection."""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tella.music.profiles import MusicProfile

_TRACK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,79}$")
_ENERGY_ORDER = {
    "low": 0,
    "medium_low": 1,
    "medium": 2,
    "medium_high": 3,
    "high": 4,
}


class MusicLibraryError(RuntimeError):
    pass


@dataclass(frozen=True)
class MusicTrack:
    track_id: str
    title: str
    creator: str
    source: str
    file_path: Path
    source_sha256: str
    source_duration: float
    moods: tuple[str, ...]
    supported_recipes: tuple[str, ...]
    energy: str
    intro_safe_seconds: float
    loop_safe: bool
    loop_start: float
    loop_end: float
    default_start_offset: float
    license_type: str
    license_reference: Path
    attribution_required: bool
    attribution_text: str
    content_id_registered: bool
    enabled: bool


@dataclass(frozen=True)
class MusicSelection:
    track: MusicTrack
    profile: MusicProfile
    reason: str
    seed: str
    recent_track_ids: tuple[str, ...]


def default_library_root() -> Path:
    configured = (os.environ.get("TELLA_MUSIC_LIBRARY") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "music"


def _resolve_local(root: Path, raw: str, field: str) -> Path:
    path = (root / raw).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise MusicLibraryError(f"{field} escapes music library root") from exc
    return path


def load_library(root: Path | None = None) -> dict[str, MusicTrack]:
    root = (root or default_library_root()).resolve()
    manifest = root / "library.json"
    if not manifest.is_file():
        raise MusicLibraryError(f"music library manifest is missing: {manifest}")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MusicLibraryError(f"music library manifest is invalid: {exc}") from exc

    tracks: dict[str, MusicTrack] = {}
    for raw in data.get("tracks", []):
        track_id = str(raw.get("track_id") or "").strip()
        if not _TRACK_ID_RE.fullmatch(track_id):
            raise MusicLibraryError(f"track has invalid stable track_id: {track_id!r}")
        if track_id in tracks:
            raise MusicLibraryError(f"duplicate music track_id: {track_id}")
        file_path = _resolve_local(root, str(raw.get("file_path") or ""), "file_path")
        if not file_path.is_file():
            raise MusicLibraryError(f"track {track_id} audio file is missing: {file_path}")
        license_type = str(raw.get("license_type") or "").strip()
        license_raw = str(raw.get("license_reference") or "").strip()
        if not license_type or not license_raw:
            raise MusicLibraryError(f"track {track_id} has missing license metadata")
        license_reference = _resolve_local(root, license_raw, "license_reference")
        if not license_reference.is_file():
            raise MusicLibraryError(
                f"track {track_id} license reference is missing: {license_reference}"
            )
        energy = str(raw.get("energy") or "").strip().lower()
        if energy not in _ENERGY_ORDER:
            raise MusicLibraryError(f"track {track_id} has invalid energy: {energy!r}")
        moods = tuple(sorted({str(item).strip().lower() for item in raw.get("moods", []) if str(item).strip()}))
        recipes = tuple(sorted({str(item).strip() for item in raw.get("supported_recipes", []) if str(item).strip()}))
        if not moods or not recipes:
            raise MusicLibraryError(f"track {track_id} needs moods and supported_recipes")
        track = MusicTrack(
            track_id=track_id,
            title=str(raw.get("title") or "").strip(),
            creator=str(raw.get("creator") or "").strip(),
            source=str(raw.get("source") or "").strip(),
            file_path=file_path,
            source_sha256=str(raw.get("source_sha256") or "").strip().upper(),
            source_duration=max(0.0, float(raw.get("source_duration") or 0.0)),
            moods=moods,
            supported_recipes=recipes,
            energy=energy,
            intro_safe_seconds=max(0.0, float(raw.get("intro_safe_seconds") or 0.0)),
            loop_safe=bool(raw.get("loop_safe", False)),
            loop_start=max(0.0, float(raw.get("loop_start") or 0.0)),
            loop_end=max(0.0, float(raw.get("loop_end") or 0.0)),
            default_start_offset=max(0.0, float(raw.get("default_start_offset") or 0.0)),
            license_type=license_type,
            license_reference=license_reference,
            attribution_required=bool(raw.get("attribution_required", False)),
            attribution_text=str(raw.get("attribution_text") or "").strip(),
            content_id_registered=bool(raw.get("content_id_registered", False)),
            enabled=bool(raw.get("enabled", True)),
        )
        if track.source_sha256:
            actual_hash = hashlib.sha256(file_path.read_bytes()).hexdigest().upper()
            if actual_hash != track.source_sha256:
                raise MusicLibraryError(
                    f"track {track_id} SHA256 does not match catalog metadata"
                )
            if not all((track.title, track.creator, track.source, track.source_duration)):
                raise MusicLibraryError(
                    f"track {track_id} has incomplete production provenance metadata"
                )
            license_text = license_reference.read_text(encoding="utf-8")
            required_license_facts = (
                f"Track ID: {track.track_id}",
                f"License type: {track.license_type}",
                track.source_sha256,
            )
            if any(fact not in license_text for fact in required_license_facts):
                raise MusicLibraryError(
                    f"track {track_id} license reference does not match catalog metadata"
                )
        if track.loop_safe and track.loop_end <= track.loop_start:
            raise MusicLibraryError(
                f"track {track_id} loop_end must be greater than loop_start"
            )
        if track.attribution_required and not track.attribution_text:
            raise MusicLibraryError(
                f"track {track_id} requires non-empty attribution_text"
            )
        tracks[track_id] = track
    return tracks


def read_recent_track_ids(history_path: Path, limit: int = 3) -> tuple[str, ...]:
    if not history_path.is_file():
        return ()
    ids: list[str] = []
    for line in history_path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        track_id = str(item.get("track_id") or "")
        if track_id:
            ids.append(track_id)
    return tuple(ids[-max(0, limit):])


def select_track(
    tracks: dict[str, MusicTrack],
    *,
    recipe_id: str,
    content_moods: set[str],
    narration_duration: float,
    profile: MusicProfile,
    seed: str,
    recent_track_ids: tuple[str, ...] = (),
    explicit_track_id: str = "",
) -> MusicSelection:
    candidates = [
        track
        for track in tracks.values()
        if track.enabled and recipe_id in track.supported_recipes
    ]
    if explicit_track_id:
        selected = tracks.get(explicit_track_id)
        if selected is None:
            raise MusicLibraryError(f"unknown music track: {explicit_track_id}")
        if not selected.enabled:
            raise MusicLibraryError(f"music track is disabled: {explicit_track_id}")
        if recipe_id not in selected.supported_recipes:
            raise MusicLibraryError(
                f"music track {explicit_track_id} does not support {recipe_id}"
            )
        return MusicSelection(
            selected,
            profile,
            "explicit track override",
            seed,
            recent_track_ids,
        )
    if not candidates:
        raise MusicLibraryError(f"no enabled music tracks support {recipe_id}")

    recent = set(recent_track_ids)
    non_recent = [track for track in candidates if track.track_id not in recent]
    pool = non_recent or candidates
    desired_moods = {mood.lower() for mood in content_moods} | set(profile.preferred_moods)

    def score(track: MusicTrack) -> tuple[int, int, str]:
        mood_score = len(desired_moods.intersection(track.moods))
        energy_distance = abs(_ENERGY_ORDER[track.energy] - _ENERGY_ORDER[profile.energy])
        duration_ok = int(track.loop_safe or narration_duration <= 30.0)
        return mood_score, duration_ok - energy_distance, track.track_id

    best_score = max(score(track)[:2] for track in pool)
    finalists = sorted(
        [track for track in pool if score(track)[:2] == best_score],
        key=lambda track: track.track_id,
    )
    digest = hashlib.sha256(
        f"{seed}|{recipe_id}|{profile.profile_id}|{'|'.join(t.track_id for t in finalists)}".encode("utf-8")
    ).digest()
    selected = finalists[int.from_bytes(digest[:8], "big") % len(finalists)]
    reason = (
        f"deterministic profile match moods={','.join(sorted(desired_moods.intersection(selected.moods)))} "
        f"energy={selected.energy} recent_avoided={selected.track_id not in recent}"
    )
    return MusicSelection(selected, profile, reason, seed, recent_track_ids)


def append_usage(history_path: Path, selection: MusicSelection, job_id: str) -> None:
    history_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "job_id": job_id,
        "recipe_id": selection.profile.recipe_id,
        "track_id": selection.track.track_id,
    }
    with history_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")


__all__ = [
    "MusicLibraryError",
    "MusicSelection",
    "MusicTrack",
    "append_usage",
    "default_library_root",
    "load_library",
    "read_recent_track_ids",
    "select_track",
]
