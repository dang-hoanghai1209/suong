"""Smoke test — `tella.planner.story_planner`.

Usage::

    python scripts/smoke_plan.py "the story of cinderella" en --theme parable --media ai_image --duration short

Full flag list::

    --topic       (positional 1)
    --lang        (positional 2)
    --theme       parable | cinematic | playful | mindfulness   (default cinematic)
    --media       ai_image | stock_photo | stock_video         (default ai_image)
    --duration    short | detailed                              (default short)
    --aspect      9:16 | 16:9                                   (default 9:16)
    --pace        slow | medium | fast                          (default = theme)
    --gender      male | female                                 (default = theme)
"""
from __future__ import annotations

import argparse
import json
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

from tella._voice_pace import PRESETS, default_pace_for_theme  # noqa: E402
from tella.planner.story_planner import plan  # noqa: E402


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Tella planner smoke")
    p.add_argument("topic")
    p.add_argument("lang")
    p.add_argument("--theme", default="cinematic",
                   choices=["parable", "cinematic", "playful", "mindfulness"])
    p.add_argument("--media", default="ai_image", dest="media_source",
                   choices=["ai_image", "stock_photo", "stock_video"])
    p.add_argument("--duration", default="short", dest="duration_mode",
                   choices=["short", "detailed"])
    p.add_argument("--aspect", default="9:16", choices=["9:16", "16:9"])
    p.add_argument("--pace", default=None, choices=["slow", "medium", "fast"])
    p.add_argument("--gender", default=None, choices=["male", "female"])
    p.add_argument("--save", default=None, help="Save the plan JSON to this path")
    args = p.parse_args(argv[1:])

    if not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GEMINI_API_KEYS"):
        print("ERROR: GEMINI_API_KEY missing. See .env.example.", file=sys.stderr)
        return 1

    voice_pace = PRESETS[args.pace] if args.pace else default_pace_for_theme(args.theme)

    print(f"Topic:        {args.topic!r}")
    print(f"Lang:         {args.lang}")
    print(f"Theme:        {args.theme}")
    print(f"Media source: {args.media_source}")
    print(f"Duration:     {args.duration_mode}")
    print(f"Aspect:       {args.aspect}")
    print(f"Voice pace:   {voice_pace.name} ({voice_pace.edge_rate})")
    print(f"Voice gender: {args.gender or 'theme default'}")
    print()
    print("Calling Gemini planner...")

    result = plan(
        topic=args.topic,
        target_lang=args.lang,
        aspect_ratio=args.aspect,
        media_source=args.media_source,
        duration_mode=args.duration_mode,
        theme=args.theme,
        voice_pace=voice_pace,
        voice_gender=args.gender,
    )

    print()
    print(f"=== Plan: {result.title!r} ({len(result.scenes)} scenes) ===")
    if result.character_brief:
        print(f"  character: {result.character_brief.identity}")
        print(f"  role:      {result.character_brief.role}")
    if result.setting_brief:
        print(f"  setting:   {result.setting_brief.location}  ({result.setting_brief.era}, "
              f"{result.setting_brief.time_of_day})")
    print(f"  voice:     {result.voice_name} @ {result.voice_edge_rate} "
          f"(gender={result.voice_gender}, pace={result.voice_pace_name})")
    print()
    for s in result.scenes:
        head = f"[{s.scene_index}] {s.title}  (assets={s.asset_count})"
        print(head)
        # Word-wrap voice_script at 84 chars.
        line = "    "
        for w in s.voice_script.split():
            if len(line) + 1 + len(w) > 84:
                print(line)
                line = "    "
            line += (" " if line.strip() else "") + w
        if line.strip():
            print(line)
        if s.image_prompt:
            print(f"    img:   {s.image_prompt[:100]}{'...' if len(s.image_prompt) > 100 else ''}")
        if s.stock_query:
            print(f"    stock: {s.stock_query}")
        print()

    if args.save:
        Path(args.save).write_text(
            json.dumps(result.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Plan JSON saved → {args.save}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
