"""Pexels free-stock-photo adapter.

Free tier: 200 req/hour, 20k req/month. Get a key at
https://www.pexels.com/api/new/ and set ``PEXELS_API_KEY``.

For higher throughput, comma-separate keys in ``PEXELS_API_KEYS`` — each
call picks one at random.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import random
from pathlib import Path
from typing import Literal

import httpx

logger = logging.getLogger("tella.media.stock_photo")

PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"
HTTP_TIMEOUT = 30.0
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 1.5

Orientation = Literal["portrait", "landscape", "square"]


def resolve_key() -> str | None:
    """Return one Pexels API key (random pick from ``PEXELS_API_KEYS`` or
    the single ``PEXELS_API_KEY``). ``None`` when nothing is configured."""
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


async def search_and_download(
    query: str,
    out_path: Path,
    *,
    api_key: str | None = None,
    width: int = 1080,
    height: int = 1920,
    per_page: int = 5,
) -> Path:
    """Search Pexels Photos for ``query`` and save the best photo to ``out_path``.

    Raises:
        RuntimeError: when all retries fail or no photo matches.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    key = api_key or resolve_key()
    if not key:
        raise RuntimeError(
            "Pexels: no API key (set PEXELS_API_KEY or PEXELS_API_KEYS)"
        )

    orientation = _orientation_for_dims(width, height)
    params = {
        "query": query.strip(),
        "orientation": orientation,
        "per_page": per_page,
        "size": "large",
    }
    headers = {"Authorization": key}

    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                resp = await client.get(PEXELS_SEARCH_URL, headers=headers, params=params)

            if resp.status_code == 200:
                data = resp.json()
                photos = data.get("photos") or []
                if not photos:
                    raise RuntimeError(
                        f"Pexels Photo: no results for {query!r} (orientation={orientation})"
                    )
                src = photos[0].get("src", {})
                url = (
                    src.get(orientation)
                    or src.get("large2x")
                    or src.get("large")
                    or src.get("medium")
                    or src.get("original")
                )
                if not url:
                    raise RuntimeError("Pexels Photo: no usable size URL")

                async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                    img_resp = await client.get(url)
                if img_resp.status_code != 200 or not img_resp.content:
                    raise RuntimeError(
                        f"Pexels Photo: download HTTP {img_resp.status_code}"
                    )
                out_path.write_bytes(img_resp.content)
                logger.info(
                    "pexels-photo saved %s (%d KB, query=%r, by=%s)",
                    out_path.name, len(img_resp.content) // 1024,
                    query, photos[0].get("photographer", ""),
                )
                return out_path

            last_err = RuntimeError(
                f"Pexels Photo HTTP {resp.status_code}: {resp.text[:200]}"
            )
            logger.warning("pexels-photo attempt %d -> %s", attempt, last_err)
            if resp.status_code in (401, 403, 429):
                break
        except (httpx.HTTPError, httpx.ReadTimeout) as exc:
            last_err = exc
            logger.warning("pexels-photo attempt %d network err: %s", attempt, exc)
        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
        with contextlib.suppress(OSError):
            if out_path.is_file() and out_path.stat().st_size < 2048:
                out_path.unlink()

    raise RuntimeError(f"Pexels Photo failed after {MAX_RETRIES} attempts: {last_err}")


__all__ = [
    "resolve_key",
    "search_and_download",
]
