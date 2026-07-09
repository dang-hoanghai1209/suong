"""Voice pace presets — 3 levels with per-theme defaults.

Tella exposes voice pacing as a small, semantic vocabulary instead of a
raw float slider. The wizard pre-selects the right preset based on the
theme; the user confirms with Enter or picks a different preset.

Three presets:

  ===========  ==========  ==============  ==================================
  Name         Edge rate   Google rate     When
  ===========  ==========  ==============  ==================================
  ``slow``     ``-10%``    ``0.90``        bedtime kids stories / parable / mindfulness
  ``minimalist_emotional`` theme default uses ``-3%`` for a softer but less broken read.
  ``medium``   ``0%``      ``1.00``        default narrative pace
  ``fast``     ``+15%``    ``1.15``        energetic, urgent, quiz-style
  ===========  ==========  ==============  ==================================

Per-theme pre-choice (resolved by :func:`resolve_pace` when no override
is given):

  ===============  ==============
  Theme            Default preset
  ===============  ==============
  ``parable``      ``slow``
  ``cinematic``    ``medium``
  ``playful``      ``slow``
  ``mindfulness``  ``slow``
  ``minimalist_emotional`` ``slow`` at ``-3%``
  ===============  ==============

Power user override: any ``"[+-]\\d{1,3}%"`` string passed via
``custom_edge_rate=`` becomes a custom :class:`VoicePace` with Google
rate auto-computed (clamped to Google's allowed ``[0.25, 4.0]``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class VoicePace:
    """One voice pacing choice with both Edge and Google rate encodings."""

    name: str            # 'slow' | 'medium' | 'fast' | 'custom'
    edge_rate: str       # '-5%' | '0%' | '+5%' | custom
    google_rate: float   # 0.92 | 1.00 | 1.05 | custom


SLOW = VoicePace(name="slow", edge_rate="-10%", google_rate=0.90)
EMOTIONAL_SLOW = VoicePace(name="slow", edge_rate="-3%", google_rate=0.97)
SYMBOLIC_SLOW = VoicePace(name="slow", edge_rate="-7%", google_rate=0.93)
MEDIUM = VoicePace(name="medium", edge_rate="0%", google_rate=1.00)
FAST = VoicePace(name="fast", edge_rate="+15%", google_rate=1.15)

PRESETS: dict[str, VoicePace] = {
    "slow": SLOW,
    "medium": MEDIUM,
    "fast": FAST,
}

THEME_DEFAULT_PACE: dict[str, VoicePace] = {
    "parable": SLOW,
    "cinematic": MEDIUM,
    # Playful's primary use case is bedtime kids stories — default to slow.
    # Was 'fast' originally for comedy/whimsy but those are edge cases now.
    "playful": SLOW,
    "mindfulness": SLOW,
    "minimalist_emotional": EMOTIONAL_SLOW,
    "minimalist_symbolic_reel": SYMBOLIC_SLOW,
}

# Themes Tella ships with — exported for the wizard's validation step.
KNOWN_THEMES: tuple[str, ...] = tuple(THEME_DEFAULT_PACE.keys())

_CUSTOM_RATE_RE = re.compile(r"^[+-]\d{1,3}%$")


def default_pace_for_theme(theme: str) -> VoicePace:
    """Return the pre-selected pace for ``theme`` (falls back to ``medium``)."""
    return THEME_DEFAULT_PACE.get(theme, MEDIUM)


def resolve_pace(
    *,
    theme: str,
    override: str | None = None,
    custom_edge_rate: str | None = None,
) -> VoicePace:
    """Pick the right pace given theme + user choices.

    Precedence: ``custom_edge_rate`` > ``override`` > theme default.

    Args:
        theme:             one of ``parable / cinematic / playful / mindfulness /
                           minimalist_emotional``
        override:          one of ``slow / medium / fast`` to pick a preset
                           explicitly, bypassing the theme default
        custom_edge_rate:  free-form Edge format (e.g. ``"+3%"``); converted
                           to a custom :class:`VoicePace`

    Raises:
        ValueError: when ``override`` is not a known preset, or
            ``custom_edge_rate`` doesn't match ``[+-]\\d{1,3}%``.
    """
    if custom_edge_rate:
        return _custom_to_pace(custom_edge_rate)
    if override:
        key = override.strip().lower()
        if key not in PRESETS:
            raise ValueError(
                f"unknown voice pace preset {override!r} — pick one of "
                f"{sorted(PRESETS)}"
            )
        return PRESETS[key]
    return default_pace_for_theme(theme)


def _custom_to_pace(edge_rate: str) -> VoicePace:
    """Convert e.g. ``'+3%'`` to a :class:`VoicePace` with computed Google rate."""
    s = (edge_rate or "").strip()
    if not _CUSTOM_RATE_RE.fullmatch(s):
        raise ValueError(
            f"voice rate must match [+-]\\d{{1,3}}% (e.g. '+3%' or '-7%'), "
            f"got {edge_rate!r}"
        )
    pct = int(s.rstrip("%"))
    google = max(0.25, min(4.0, 1.0 + pct / 100.0))
    return VoicePace(name="custom", edge_rate=s, google_rate=round(google, 3))


__all__ = [
    "FAST",
    "EMOTIONAL_SLOW",
    "KNOWN_THEMES",
    "MEDIUM",
    "PRESETS",
    "SLOW",
    "SYMBOLIC_SLOW",
    "THEME_DEFAULT_PACE",
    "VoicePace",
    "default_pace_for_theme",
    "resolve_pace",
]
