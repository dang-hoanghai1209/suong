"""Smoke test — `tella.ingest.keyword_expander`.

Usage::

    python scripts/smoke_expand.py "lonely childhood" en
    python scripts/smoke_expand.py "tuổi thơ một mình" vi
    python scripts/smoke_expand.py "first day at school" ja 3   # custom N

Set ``GEMINI_API_KEY`` in your environment (or `.env`).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO / ".env")
except ImportError:
    pass

from tella.ingest.keyword_expander import DEFAULT_N, expand  # noqa: E402
from tella.ingest.topic_translator import SUPPORTED_LANGS  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        print(
            f"\nSupported target_lang: {sorted(SUPPORTED_LANGS)}",
            file=sys.stderr,
        )
        return 2

    keyword = argv[1]
    target = argv[2].lower()
    n = int(argv[3]) if len(argv) > 3 else DEFAULT_N

    if not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GEMINI_API_KEYS"):
        print(
            "ERROR: GEMINI_API_KEY missing. "
            "Get a free key at https://aistudio.google.com/apikey "
            "and add it to .env",
            file=sys.stderr,
        )
        return 1

    print(f"Keyword:       {keyword!r}")
    print(f"Target lang:   {target}")
    print(f"N directions:  {n}")
    print()
    print("Calling Gemini Flash...")

    result = expand(keyword, target, n=n)

    print()
    print(f"=== {len(result.directions)} story directions ({result.target_language}) ===")
    for d in result.directions:
        print()
        print(f"  [{d.index}] {d.title}   ({d.tone})")
        # 2-line wrap synopsis at ~80 chars for readability.
        words = d.synopsis.split()
        line = "      "
        for w in words:
            if len(line) + 1 + len(w) > 84:
                print(line)
                line = "      "
            line += (" " if line.strip() else "") + w
        if line.strip():
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
