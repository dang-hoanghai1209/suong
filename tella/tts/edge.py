"""Edge TTS adapter — primary narration provider for Tella v1.

Uses Microsoft's free Edge TTS service through the ``edge-tts`` Python
package. No credentials, no quota worries, multi-language support
across all 8 Tella locales.

Pattern: copied from ktb-story-teller's ``core/tts/edge.py`` with
the retry-on-``NoAudioReceived`` + throttle fix that landed 2026-06-10.
"""
from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path

import edge_tts

logger = logging.getLogger("tella.tts.edge")

# Microsoft's Edge TTS sometimes returns NoAudioReceived under load —
# retry with backoff before giving up.
MAX_RETRIES = 4
RETRY_BACKOFF_SECONDS = (3.0, 7.0, 15.0, 30.0)
# Small jitter between requests prevents bursting Microsoft's rate limiter
# when the composer hits us with many concurrent scenes.
INTER_REQUEST_THROTTLE = 0.25


def _normalize_signed(value: str, unit: str) -> str:
    """edge-tts ≥ 7.x rejects unsigned percent / hertz values — prepend ``+``."""
    s = (value or "").strip()
    if not s or s == unit:
        return f"+0{unit}"
    if s[0] in ("+", "-"):
        return s
    # Bare number → assume positive.
    return f"+{s}"


async def synthesize(
    text: str,
    voice_name: str,
    out_path: Path,
    *,
    rate: str = "+0%",
    pitch: str = "+0Hz",
    volume: str = "+0%",
) -> Path:
    """Synthesize ``text`` with the named Edge voice and save to ``out_path``.

    Args:
        text:        UTF-8 text to narrate.
        voice_name:  Edge voice name (e.g. ``"en-US-GuyNeural"``). Use
                     :func:`tella.planner.voices.edge_voice_for` to pick
                     one for ``(language, gender)``.
        out_path:    MP3 output path. Parent dir created if needed.
        rate:        ``[+-]\\d{1,3}%`` (e.g. ``"-5%"``, ``"+5%"``).
        pitch:       Edge pitch string (rarely needed; default 0Hz).
        volume:      Edge volume string (default 0%).

    Returns:
        ``out_path`` on success.

    Raises:
        RuntimeError: when all retries fail (Edge service down, voice
            name typo'd, or text contains characters Edge refuses).
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("text is empty")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # edge-tts ≥ 7.x requires explicit sign on rate / pitch / volume.
    # Tella's voice_pace strings are already signed ("+5%", "-5%") — but a
    # bare "0%" is not, so normalize defensively.
    rate = _normalize_signed(rate, "%")
    volume = _normalize_signed(volume, "%")
    pitch = _normalize_signed(pitch, "Hz")

    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            communicate = edge_tts.Communicate(
                text=text,
                voice=voice_name,
                rate=rate,
                pitch=pitch,
                volume=volume,
            )
            await communicate.save(str(out_path))
            if not out_path.is_file() or out_path.stat().st_size < 256:
                raise RuntimeError(
                    f"edge-tts wrote {out_path.stat().st_size if out_path.is_file() else 0} bytes"
                )
            # Tiny jitter so concurrent callers don't burst-fire Microsoft.
            await asyncio.sleep(INTER_REQUEST_THROTTLE + random.uniform(0, 0.15))
            logger.info(
                "edge-tts saved %s (%d KB, voice=%s, rate=%s, attempt %d)",
                out_path.name, out_path.stat().st_size // 1024,
                voice_name, rate, attempt,
            )
            return out_path
        except Exception as exc:
            last_err = exc
            logger.warning(
                "edge-tts attempt %d/%d failed (%s): %s",
                attempt, MAX_RETRIES, type(exc).__name__, exc,
            )
            if attempt < MAX_RETRIES:
                backoff = RETRY_BACKOFF_SECONDS[
                    min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)
                ]
                await asyncio.sleep(backoff)

    raise RuntimeError(
        f"edge-tts failed after {MAX_RETRIES} attempts: {last_err}"
    )


__all__ = [
    "synthesize",
]
