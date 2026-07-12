"""Plan-level local music configuration and metadata persistence."""
from __future__ import annotations

import json
import os
from pathlib import Path

from tella.music.library import (
    MusicLibraryError,
    append_usage,
    default_library_root,
    load_library,
    read_recent_track_ids,
    select_track,
)
from tella.music.profiles import profile_for_recipe
from tella.planner.models import TellaScenePlan


def _content_moods(plan: TellaScenePlan) -> set[str]:
    moods = {
        str(scene.emotion_tag).strip().lower()
        for scene in plan.scenes
        if str(scene.emotion_tag).strip()
    }
    return moods


def write_music_metadata(plan: TellaScenePlan, job_dir: Path) -> Path:
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    path = job_dir / "music_metadata.json"
    path.write_text(
        json.dumps(plan.music_metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def configure_music(
    plan: TellaScenePlan,
    job_dir: Path,
    *,
    requested_track_id: str = "",
    requested_profile_id: str = "",
    no_music: bool = False,
) -> None:
    job_dir = Path(job_dir)
    plan.requested_music_track_id = requested_track_id
    plan.requested_music_profile_id = requested_profile_id
    plan.music_no_music = bool(no_music)
    plan.music_enabled = False
    plan.selected_music_track_id = ""
    plan.selected_music_profile_id = ""
    plan.selected_music_path = ""
    plan.music_selection_reason = ""
    plan.music_history_path = str(job_dir.parent / ".music_usage_history.jsonl")

    if no_music:
        plan.music_selection_reason = "music disabled by --no-music"
        plan.music_metadata = {
            "status": "disabled",
            "selected_track": "",
            "selection_reason": plan.music_selection_reason,
            "license": {},
        }
        write_music_metadata(plan, job_dir)
        return

    profile = profile_for_recipe(plan.recipe_id, requested_profile_id)
    if profile is None:
        plan.music_selection_reason = "recipe has no music profile"
        plan.music_metadata = {
            "status": "not_configured",
            "selected_track": "",
            "selection_reason": plan.music_selection_reason,
            "license": {},
        }
        write_music_metadata(plan, job_dir)
        return

    root = default_library_root()
    tracks = load_library(root)
    history_path = Path(plan.music_history_path)
    recent = read_recent_track_ids(history_path)
    seed = (
        os.environ.get("TELLA_MUSIC_SEED")
        or f"{job_dir.name}|{plan.recipe_id}|{plan.narration_duration:.3f}"
    )
    try:
        selection = select_track(
            tracks,
            recipe_id=plan.recipe_id,
            content_moods=_content_moods(plan),
            narration_duration=plan.narration_duration,
            profile=profile,
            seed=seed,
            recent_track_ids=recent,
            explicit_track_id=requested_track_id,
        )
    except MusicLibraryError:
        if requested_track_id:
            raise
        plan.music_selection_reason = "no compatible enabled local licensed track"
        plan.music_metadata = {
            "status": "not_available",
            "selected_track": "",
            "selection_reason": plan.music_selection_reason,
            "music_profile_id": profile.profile_id,
            "license": {},
        }
        write_music_metadata(plan, job_dir)
        return

    track = selection.track
    plan.music_enabled = True
    plan.selected_music_track_id = track.track_id
    plan.selected_music_profile_id = profile.profile_id
    plan.selected_music_path = str(track.file_path)
    plan.music_selection_reason = selection.reason
    plan.music_metadata = {
        "status": "selected",
        "selected_track": track.track_id,
        "selected_track_path": str(track.file_path),
        "selection_reason": selection.reason,
        "selection_seed": selection.seed,
        "recent_track_ids": list(selection.recent_track_ids),
        "music_profile_id": profile.profile_id,
        "profile_energy": profile.energy,
        "profile_moods": list(profile.preferred_moods),
        "license": {
            "license_type": track.license_type,
            "license_reference": str(track.license_reference),
            "attribution_required": track.attribution_required,
            "attribution_text": track.attribution_text,
        },
        "track": {
            "moods": list(track.moods),
            "energy": track.energy,
            "intro_safe_seconds": track.intro_safe_seconds,
            "loop_safe": track.loop_safe,
            "loop_start": track.loop_start,
            "loop_end": track.loop_end,
            "default_start_offset": track.default_start_offset,
        },
    }
    write_music_metadata(plan, job_dir)


def record_music_usage(plan: TellaScenePlan, job_dir: Path) -> None:
    if not plan.music_enabled or not plan.selected_music_track_id:
        return
    profile = profile_for_recipe(plan.recipe_id, plan.selected_music_profile_id)
    tracks = load_library(default_library_root())
    selection = select_track(
        tracks,
        recipe_id=plan.recipe_id,
        content_moods=_content_moods(plan),
        narration_duration=plan.narration_duration,
        profile=profile,
        seed=str(plan.music_metadata.get("selection_seed") or ""),
        explicit_track_id=plan.selected_music_track_id,
    )
    append_usage(Path(plan.music_history_path), selection, Path(job_dir).name)


__all__ = [
    "configure_music",
    "record_music_usage",
    "write_music_metadata",
]
