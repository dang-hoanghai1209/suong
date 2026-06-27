"""Synthesize TTS audio for every body scene in a plan.

Walks ``plan.scenes`` and produces ``<job_dir>/assets/scene_NN.mp3`` for
each scene. Mutates the plan in place: sets ``scene.audio_filename`` +
``scene.audio_duration`` (measured via ffprobe).

Concurrency: up to MAX_CONCURRENT scenes synthesize in parallel. Edge TTS
already adds an inter-request throttle inside :mod:`tella.tts.edge` so
this just amplifies the steady-state rate.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from tella.planner.models import TellaScenePlan
from tella.tts import edge, google

logger = logging.getLogger("tella.tts.synth_all")

MAX_CONCURRENT = 4


async def _ffprobe_duration(path: Path) -> float:
    """Return audio duration in seconds via ffprobe."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed for {path.name}: "
            f"{stderr.decode('utf-8', errors='replace')[-200:]}"
        )
    try:
        return float(stdout.decode("ascii").strip() or "0")
    except ValueError:
        return 0.0


def _safe_stem(text: str, max_len: int = 30) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", (text or "scene")).strip("_").lower()
    return (slug or "scene")[:max_len]


async def synthesize_all(
    plan: TellaScenePlan,
    job_dir: Path,
    *,
    google_tts_api_key: str = "",
    google_tts_voice: str = "",
) -> None:
    """Synthesize TTS for every body scene; populate audio_filename + duration.

    Mutates the plan in place. Writes MP3s to ``<job_dir>/assets/``.

    Voice provider priority (CEO 2026-06-14, ported from Briefa):
      1. Google Cloud TTS Chirp 3 HD — when ``google_tts_api_key`` AND
         ``google_tts_voice`` are both set AND ``google.is_dead()`` is False.
         Chirp 3 HD voice quality is materially better than Edge for VN /
         EN news-style narration.
      2. Edge TTS (existing) — always-on fallback. Used when no Google key,
         when Google's per-session kill switch flips, or when Google call
         returns non-200.

    Raises:
        RuntimeError: when both providers fail for any scene.
    """
    job_dir = Path(job_dir)
    assets_dir = job_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    body_scenes = [s for s in plan.scenes if s.kind == "scene"]
    if not body_scenes:
        return

    sem = asyncio.Semaphore(MAX_CONCURRENT)
    google_enabled = bool(google_tts_api_key) and bool(google_tts_voice)

    async def _one(scene) -> None:
        async with sem:
            base = f"scene_{scene.scene_index:02d}_{_safe_stem(scene.title)}"
            out = assets_dir / f"{base}.mp3"
            used_provider = "edge"
            # ── Try Google Chirp 3 HD first when configured ──
            if google_enabled and not google.is_dead():
                ok = await google.synth_google(
                    text=scene.voice_script,
                    voice_name=google_tts_voice,
                    rate=plan.voice_edge_rate,
                    api_key=google_tts_api_key,
                    out_path=out,
                )
                if ok:
                    used_provider = "google"
            # ── Edge TTS — primary path when Google off, fallback when Google fails ──
            if used_provider == "edge":
                await edge.synthesize(
                    scene.voice_script,
                    plan.voice_name,
                    out,
                    rate=plan.voice_edge_rate,
                )
            scene.audio_filename = f"assets/{out.name}"
            scene.audio_duration = round(await _ffprobe_duration(out), 2)

    logger.info(
        "synthesize_all: %d scenes, primary=%s, edge_fallback=%s @ %s",
        len(body_scenes),
        f"google:{google_tts_voice}" if google_enabled else "edge",
        plan.voice_name,
        plan.voice_edge_rate,
    )
    await asyncio.gather(*[_one(s) for s in body_scenes])
    logger.info("synthesize_all: all %d scenes done", len(body_scenes))


__all__ = [
    "MAX_CONCURRENT",
    "synthesize_all",
]
