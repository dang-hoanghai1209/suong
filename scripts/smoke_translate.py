"""Smoke test — `tella.ingest.topic_translator`.

Usage::

    python scripts/smoke_translate.py "câu chuyện cô bé Lọ Lem" en
    python scripts/smoke_translate.py "the day the internet went down" vi
    python scripts/smoke_translate.py "una historia de coraje" ja

Set ``GEMINI_API_KEY`` in your environment (or `.env` with python-dotenv
auto-loaded by setuptools entry; we load it explicitly here for the script).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow running from anywhere — ensure repo root is on sys.path.
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO / ".env")
except ImportError:
    pass

from tella.ingest.topic_translator import SUPPORTED_LANGS, translate  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        print(
            f"\nSupported target_lang: {sorted(SUPPORTED_LANGS)}",
            file=sys.stderr,
        )
        return 2

    topic = argv[1]
    target = argv[2].lower()
    source_hint = argv[3] if len(argv) > 3 else None

    if not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GEMINI_API_KEYS"):
        print(
            "ERROR: GEMINI_API_KEY missing. "
            "Get a free key at https://aistudio.google.com/apikey "
            "and add it to .env",
            file=sys.stderr,
        )
        return 1

    print(f"Input topic:   {topic!r}")
    print(f"Target lang:   {target}")
    if source_hint:
        print(f"Source hint:   {source_hint}")
    print()
    print("Calling Gemini Flash Lite...")

    result = translate(topic, target, source_lang_hint=source_hint)

    print()
    print(f"Translated:    {result.translated_topic}")
    print(f"Source lang:   {result.source_language_detected}")
    print(f"Needed trans:  {result.needs_translation}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
