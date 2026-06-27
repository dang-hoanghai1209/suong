"""Pexels free-stock-video adapter.

Same key as the photo adapter — Pexels accepts one ``Authorization``
header for both ``/v1/search`` (photos) and ``/videos/search`` (videos).

Pipeline per clip:
  1. Query ``/videos/search`` with orientation hint matching render canvas
  2. Filter to clips ≥ MIN_CLIP_DURATION_SECONDS (else they loop and the
     loop reset reads as a "fast cut" to viewers)
  3. Pick the smallest HD MP4 file matching orientation
  4. Download, strip audio (TTS narration is the only audio we want)
  5. Optionally extract a JPG frame sequence sidecar — composer uses
     this when the render path can't handle <video> reliably

Notes:
  * Uses ``ffmpeg`` from PATH. SETUP scripts should detect & warn if missing.
  * The frame-sequence sidecar is OPTIONAL — composer uses raw MP4 by default
    in v1 (ffmpeg drawtext + scale handles the clip natively). Frame
    extraction is kept for future HF integration.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import random
import shutil
from pathlib import Path
from typing import Literal

import httpx

logger = logging.getLogger("tella.media.stock_video")

PEXELS_VIDEO_SEARCH_URL = "https://api.pexels.com/videos/search"
HTTP_TIMEOUT = 60.0
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 1.5

MIN_CLIP_DURATION_SECONDS = 6   # below this, looping reads as fast cuts
FRAME_EXTRACTION_FPS = 24
FRAME_JPEG_QUALITY = 6

Orientation = Literal["portrait", "landscape", "square"]


def resolve_key() -> str | None:
    """Return one Pexels API key — same env vars as the photo adapter."""
    keys_csv = (os.environ.get("PEXELS_API_KEYS") or "").strip()
    if keys_csv:
        keys = [k.strip() for k in keys_csv.split(",") if k.strip()]
        if keys:
            return random.choice(keys)
    key = (os.environ.get("PEXELS_API_KEY") or "").strip()
    return key or None


def _orientation_for_dims(width: int, height: int) -> Orientation:
    if height > width * 1.15:
        return "portrait"
    if width > height * 1.15:
        return "landscape"
    return "square"


def _aspect_matches(file_w: int, file_h: int, target_orientation: Orientation) -> bool:
    if file_w == 0 or file_h == 0:
        return False
    file_orient = _orientation_for_dims(file_w, file_h)
    return file_orient == target_orientation or file_orient == "square"


def _pick_best_video_file(
    clip: dict, target_orientation: Orientation, *, max_height: int = 1920,
) -> dict | None:
    files = [
        f for f in (clip.get("video_files") or [])
        if (f.get("file_type") or "").startswith("video/")
        and f.get("link")
    ]
    if not files:
        return None
    candidates = [
        f for f in files
        if f.get("quality") in ("hd", "sd")
        and _aspect_matches(f.get("width") or 0, f.get("height") or 0, target_orientation)
        and (f.get("height") or 0) <= max_height
    ]
    candidates.sort(key=lambda f: (f.get("height") or 0))
    return candidates[-1] if candidates else files[0]


async def _ffmpeg_strip_audio(src: Path, dst: Path) -> None:
    """Strip audio from ``src`` MP4 → ``dst``. Re-encode video only if -c copy fails."""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(src),
        "-an", "-c:v", "copy",
        str(dst),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(src),
            "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            str(dst),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"ffmpeg strip-audio failed: "
                f"{stderr.decode('utf-8', errors='replace')[-300:]}"
            )


async def _ffmpeg_extract_frames(
    src: Path, frames_dir: Path,
    *,
    fps: int = FRAME_EXTRACTION_FPS,
    quality: int = FRAME_JPEG_QUALITY,
) -> int:
    """Extract ``src`` → ``frames_dir/f000.jpg, f001.jpg, …``. Returns frame count."""
    if frames_dir.exists():
        shutil.rmtree(frames_dir, ignore_errors=True)
    frames_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(frames_dir / "f%03d.jpg")
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(src),
        "-vf", f"fps={fps}",
        "-q:v", str(quality),
        "-start_number", "0",
        pattern,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg extract-frames failed: "
            f"{stderr.decode('utf-8', errors='replace')[-300:]}"
        )
    return len(sorted(frames_dir.glob("f*.jpg")))


async def search_and_download(
    query: str,
    out_path: Path,
    *,
    api_key: str | None = None,
    width: int = 1080,
    height: int = 1920,
    per_page: int = 15,
    min_duration: int = MIN_CLIP_DURATION_SECONDS,
    extract_frames: bool = False,
) -> Path:
    """Search Pexels Videos and save the best clip to ``out_path`` (forced .mp4 ext).

    Returns the final on-disk path. Raises ``RuntimeError`` on total failure.
    """
    if out_path.suffix.lower() != ".mp4":
        out_path = out_path.with_suffix(".mp4")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    key = api_key or resolve_key()
    if not key:
        raise RuntimeError(
            "Pexels Video: no API key (set PEXELS_API_KEY or PEXELS_API_KEYS)"
        )

    orientation = _orientation_for_dims(width, height)
    max_height = max(width, height)
    params = {
        "query": query.strip(),
        "orientation": orientation,
        "size": "medium",
        "per_page": per_page,
    }
    headers = {"Authorization": key}

    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                resp = await client.get(
                    PEXELS_VIDEO_SEARCH_URL, headers=headers, params=params,
                )
            if resp.status_code != 200:
                last_err = RuntimeError(
                    f"Pexels Video HTTP {resp.status_code}: {resp.text[:200]}"
                )
                logger.warning("pexels-video attempt %d -> %s", attempt, last_err)
                if resp.status_code in (401, 403, 429):
                    break
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue

            data = resp.json()
            all_clips = data.get("videos") or []
            clips = [c for c in all_clips if (c.get("duration") or 0) >= min_duration]
            if not clips:
                raise RuntimeError(
                    f"Pexels Video: no clips ≥{min_duration}s for {query!r} "
                    f"(orientation={orientation}, returned {len(all_clips)})"
                )

            pick = None
            clip_meta = None
            for c in clips:
                f = _pick_best_video_file(c, orientation, max_height=max_height)
                if f is not None:
                    pick = f
                    clip_meta = c
                    break
            if pick is None:
                raise RuntimeError("Pexels Video: no MP4 file matches orientation")

            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                vid_resp = await client.get(pick["link"])
            if vid_resp.status_code != 200 or not vid_resp.content:
                raise RuntimeError(f"Pexels Video download HTTP {vid_resp.status_code}")

            raw_path = out_path.with_suffix(".raw.mp4")
            raw_path.write_bytes(vid_resp.content)
            logger.info(
                "pexels-video saved %s (%d KB, %dx%d, %ds, query=%r)",
                raw_path.name, len(vid_resp.content) // 1024,
                pick.get("width", 0), pick.get("height", 0),
                (clip_meta or {}).get("duration", 0), query,
            )

            try:
                await _ffmpeg_strip_audio(raw_path, out_path)
            except Exception as exc:
                logger.warning("strip-audio failed (%s) — keeping raw", exc)
                raw_path.replace(out_path)
            finally:
                with contextlib.suppress(OSError):
                    raw_path.unlink()

            if extract_frames:
                frames_dir = out_path.with_name(out_path.stem + "_frames")
                try:
                    n = await _ffmpeg_extract_frames(out_path, frames_dir)
                    logger.info("extracted %d frames @ %dfps", n, FRAME_EXTRACTION_FPS)
                except Exception as exc:
                    logger.warning("frame extract failed: %s", exc)

            return out_path
        except (httpx.HTTPError, httpx.ReadTimeout) as exc:
            last_err = exc
            logger.warning("pexels-video network err attempt %d: %s", attempt, exc)
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)

    raise RuntimeError(f"Pexels Video failed after {MAX_RETRIES} attempts: {last_err}")


__all__ = [
    "FRAME_EXTRACTION_FPS",
    "MIN_CLIP_DURATION_SECONDS",
    "resolve_key",
    "search_and_download",
]
