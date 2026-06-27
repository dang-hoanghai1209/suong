"""Interactive wizard — the no-argument ``python -m tella`` experience.

Mirrors the friendly "answer a few questions" flow so a non-technical user
on a desktop can just run ``RUN.bat`` / ``./RUN.sh`` and get a video without
ever touching CLI flags. Power users can still pass flags (see ``tella.cli``)
to skip the wizard entirely.

All prompts are ASCII-only on purpose: Windows ``cmd.exe`` defaults to the
cp1252 code page and chokes on box-drawing / check-mark glyphs. We force
UTF-8 for the *content* (topic text, narration) but keep the UI plain.
"""
from __future__ import annotations

from dataclasses import dataclass

# Languages the planner + translator support (ISO-639-1).
_LANGS: list[tuple[str, str]] = [
    ("en", "English"),
    ("vi", "Tieng Viet"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
    ("zh", "Chinese"),
    ("de", "German"),
    ("fr", "French"),
    ("es", "Spanish"),
]

_ASPECTS: list[tuple[str, str]] = [
    ("9:16", "Vertical short  (TikTok / Reels / YouTube Shorts)"),
    ("16:9", "Horizontal      (YouTube / landscape)"),
]

_MEDIA: list[tuple[str, str]] = [
    ("ai_image", "AI image  - cinematic FLUX art, characters stay consistent across scenes"),
    ("stock_photo", "Stock photo - real Pexels photographs, fast"),
    ("stock_video", "Stock video - real Pexels video clips, most motion"),
]

_DURATIONS: list[tuple[str, str]] = [
    ("short", "Short    - 5-8 scenes, about 60-120s"),
    ("detailed", "Detailed - 12-20 scenes, about 4-6 minutes"),
]

_THEMES: list[tuple[str, str]] = [
    ("cinematic", "Cinematic   - documentary tone, photorealistic, dramatic lighting"),
    ("parable", "Parable     - meditative fable, watercolor / Ghibli imagery"),
    ("mindfulness", "Mindfulness - calm dharma-talk reflection, recurring monk character"),
    ("playful", "Playful     - upbeat children's-book tone, vibrant cartoon"),
]

_GENDERS: list[tuple[str, str]] = [
    ("male", "Male voice"),
    ("female", "Female voice"),
]


@dataclass
class WizardResult:
    topic: str
    target_lang: str
    aspect_ratio: str
    media_source: str
    duration_mode: str
    theme: str
    voice_gender: str


def _print_header() -> None:
    print()
    print("=" * 60)
    print("  Tella - turn a topic into a narrated story video")
    print("=" * 60)


def _ask_text(prompt: str) -> str:
    """Prompt for free text. Re-asks until non-empty. Ctrl+C aborts."""
    while True:
        try:
            value = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            raise KeyboardInterrupt
        if value:
            return value
        print("  (please type something)")


def _ask_choice(
    title: str,
    options: list[tuple[str, str]],
    default_index: int = 0,
) -> str:
    """Render a numbered menu and return the chosen option *value*.

    Empty input picks ``default_index``. Out-of-range / non-numeric input
    re-asks. Returns the option's first tuple element (the machine value).
    """
    print()
    print(title)
    for i, (_value, label) in enumerate(options, start=1):
        marker = "*" if (i - 1) == default_index else " "
        print(f"  {marker} {i}) {label}")
    while True:
        try:
            raw = input(f"Choose [{default_index + 1}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            raise KeyboardInterrupt
        if not raw:
            return options[default_index][0]
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(options):
                return options[n - 1][0]
        print(f"  (enter a number 1-{len(options)})")


def run_wizard() -> WizardResult:
    """Walk the user through every choice and return their selections."""
    _print_header()

    topic = _ask_text(
        "\nStep 1/7 - What is the story about?\n"
        "  (a topic in any language, e.g. \"the lighthouse keeper who learned to rest\")\n"
        "Topic: "
    )

    target_lang = _ask_choice(
        "Step 2/7 - Narration language",
        _LANGS,
        default_index=0,
    )

    aspect_ratio = _ask_choice(
        "Step 3/7 - Aspect ratio",
        _ASPECTS,
        default_index=0,
    )

    media_source = _ask_choice(
        "Step 4/7 - Where do the visuals come from?",
        _MEDIA,
        default_index=0,
    )

    duration_mode = _ask_choice(
        "Step 5/7 - How long?",
        _DURATIONS,
        default_index=0,
    )

    theme = _ask_choice(
        "Step 6/7 - Visual + narration theme",
        _THEMES,
        default_index=0,
    )

    voice_gender = _ask_choice(
        "Step 7/7 - Narrator voice",
        _GENDERS,
        default_index=0,
    )

    # ── Review ──────────────────────────────────────────────────────────
    def _label(options: list[tuple[str, str]], value: str) -> str:
        for v, lbl in options:
            if v == value:
                return lbl.split(" - ")[0].strip() if " - " in lbl else lbl.strip()
        return value

    print()
    print("-" * 60)
    print("  Ready to render:")
    print(f"    Topic     : {topic}")
    print(f"    Language  : {_label(_LANGS, target_lang)}")
    print(f"    Aspect    : {aspect_ratio}")
    print(f"    Visuals   : {_label(_MEDIA, media_source)}")
    print(f"    Length    : {_label(_DURATIONS, duration_mode)}")
    print(f"    Theme     : {_label(_THEMES, theme)}")
    print(f"    Voice     : {_label(_GENDERS, voice_gender)}")
    print("-" * 60)
    try:
        go = input("Start? [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        raise KeyboardInterrupt
    if go in ("n", "no"):
        raise KeyboardInterrupt

    return WizardResult(
        topic=topic,
        target_lang=target_lang,
        aspect_ratio=aspect_ratio,
        media_source=media_source,
        duration_mode=duration_mode,
        theme=theme,
        voice_gender=voice_gender,
    )


__all__ = ["WizardResult", "run_wizard"]
