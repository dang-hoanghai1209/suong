"""Tella themes — load + apply visual theme presets.

JSON theme files ship in this folder:
  parable.json      — warm watercolor, Studio Ghibli, slow narrator -5%
  cinematic.json    — photorealistic, teal-orange grade, neutral
  playful.json      — vibrant cartoon, energetic narrator +5%
  mindfulness.json  — chú tiểu watercolor Buddhist warm tone, slow

Public API (filled in Phase 2):
  load_theme(name: str) -> ThemeSpec
  apply_theme_to_plan(theme: ThemeSpec, plan) -> None
"""
