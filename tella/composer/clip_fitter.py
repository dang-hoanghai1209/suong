"""Stock video clip duration fitter.

When the user picks ``media_source=stock_video``, each scene gets a
Pexels clip of some fixed duration that doesn't match the narration
duration. This module decides how to reconcile them:

  - Clip ≥ narration            → TRIM from a random offset
  - 0.6×narration ≤ clip < narration → SLOW-MOTION stretch (0.6×..1× speed)
  - Clip < 0.6×narration        → LOOP with crossfade

The choice produces a ``ClipFit`` instruction the render layer feeds into
ffmpeg's ``setpts`` (slow), ``trim`` (trim), or ``concat`` (loop) filters.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Literal

Action = Literal["trim", "slow", "loop"]


@dataclass(frozen=True)
class ClipFit:
    """One fit instruction for a stock video clip → scene duration."""

    action: Action
    # action="trim": start offset into the clip, in seconds
    trim_start: float = 0.0
    # action="slow": output_dur = clip_dur / speed → speed in (0, 1]
    speed: float = 1.0
    # action="loop": number of full clip plays needed to cover narration
    n_loops: int = 1
    crossfade_sec: float = 0.0


SLOW_MOTION_FLOOR = 0.6   # clip ≥ 0.6× narration uses slow-motion stretch
MIN_SPEED = 0.6           # slowest allowed playback (below = unwatchable)
LOOP_CROSSFADE_SEC = 0.5  # crossfade between loop iterations


def fit(clip_duration: float, narration_duration: float, *, seed: int | None = None) -> ClipFit:
    """Pick the right fit instruction.

    Args:
        clip_duration:        Duration of the source clip, in seconds.
        narration_duration:   Required scene duration, in seconds.
        seed:                 Optional seed for trim_start randomization.

    Returns:
        :class:`ClipFit`. Caller feeds this into the render layer.
    """
    rnd = random.Random(seed) if seed is not None else random
    clip = max(0.1, float(clip_duration))
    nar = max(0.1, float(narration_duration))

    # Case 1: clip is long enough → trim.
    if clip >= nar:
        max_start = max(0.0, clip - nar)
        # Avoid the very first second (often a fade-in on Pexels clips) when
        # there's enough headroom to skip it.
        lower = min(1.0, max_start * 0.1)
        trim_start = rnd.uniform(lower, max_start)
        return ClipFit(action="trim", trim_start=round(trim_start, 2))

    # Case 2: clip ≥ 60% of narration → slow-motion stretch.
    if clip >= SLOW_MOTION_FLOOR * nar:
        speed = clip / nar
        return ClipFit(action="slow", speed=round(max(speed, MIN_SPEED), 4))

    # Case 3: clip is much shorter → loop with crossfade.
    n = max(2, math.ceil(nar / clip))
    return ClipFit(action="loop", n_loops=n, crossfade_sec=LOOP_CROSSFADE_SEC)


__all__ = [
    "LOOP_CROSSFADE_SEC",
    "MIN_SPEED",
    "SLOW_MOTION_FLOOR",
    "Action",
    "ClipFit",
    "fit",
]
