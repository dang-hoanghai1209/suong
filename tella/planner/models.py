"""Pydantic data models for Tella's planner output.

A Tella plan = title + global metadata + scenes. Each scene contains both
an AI image prompt AND a stock keyword (planner emits both; composer picks
which to feed downstream based on ``media_source``). This mirrors VCM's
``mix`` mode and lets us swap media sources without re-running the planner.

Multi-asset extension over VCM: ``Scene.asset_count`` (1-3) signals to the
media layer how many visuals to fetch per scene — composer then interleaves
them with Ken Burns / crossfade transitions inside the scene's duration.

Character + setting briefs are top-level (not per-scene) because they're
*locked* for the whole video — same protagonist, same setting. The planner
emits them once; ``tella.planner.character_lock`` prepends them to each
scene's ``image_prompt`` for AI image mode. Stock photo/video modes ignore
the briefs (random stock content can't honour character locking).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ─── Vocabulary ────────────────────────────────────────────────────────

# ISO-639-1 — matches tella.ingest.topic_translator.SUPPORTED_LANGS.
Language = Literal["vi", "en", "ja", "ko", "zh", "de", "fr", "es"]
AspectRatio = Literal["9:16", "16:9"]
MediaSource = Literal["ai_image", "stock_photo", "stock_video"]
DurationMode = Literal["short", "detailed"]
Theme = Literal["parable", "cinematic", "playful", "mindfulness"]
VoicePaceName = Literal["slow", "medium", "fast", "custom"]
VoiceGender = Literal["male", "female"]
SceneKind = Literal["cover", "scene", "outro"]


class CharacterBrief(BaseModel):
    """One-shot character identity locked across all scenes (AI image mode only)."""

    model_config = ConfigDict(extra="ignore")

    identity: str = Field(
        ...,
        min_length=10,
        max_length=280,
        description="10-15 word physical description: age, gender, hair, "
        "outfit, distinguishing features. Copied verbatim into every "
        "scene's image_prompt so FLUX renders the same face / clothes "
        "across the video.",
    )
    role: Literal["protagonist", "antagonist", "mentor", "narrator"] = "protagonist"


class SettingBrief(BaseModel):
    """One-shot world/setting identity locked across all scenes."""

    model_config = ConfigDict(extra="ignore")

    location: str = Field(..., min_length=4, max_length=200)
    era: str = Field("timeless", max_length=80)
    mood: str = Field("neutral", max_length=80)
    time_of_day: str = Field("golden hour", max_length=80)


class Scene(BaseModel):
    """One scene = one TTS-narrated segment with 1-3 visual assets."""

    model_config = ConfigDict(extra="ignore")

    scene_index: int = Field(..., ge=0)
    kind: SceneKind = "scene"
    title: str = Field("", max_length=200)
    voice_script: str = Field(..., min_length=1, max_length=600)

    # Planner emits BOTH so composer can pick per media_source without
    # re-asking Gemini. ``image_prompt`` = English FLUX prompt (15-30 words),
    # ``stock_query`` = 2-4 English keywords for Pexels.
    image_prompt: str = Field("", max_length=600)
    stock_query: str = Field("", max_length=80)

    # 1-3 assets per scene. 1 = static Ken Burns; 2-3 = mini-montage with
    # crossfades inside the scene window.
    asset_count: int = Field(1, ge=1, le=3)

    # ── Filled by media + composer downstream ────────────────────
    image_filenames: list[str] = Field(default_factory=list)
    """Relative paths to fetched assets. len == asset_count after media step."""

    # Stock-video frame-sequence sidecar (mirrors VCM pattern).
    frames_dirs: list[str] = Field(default_factory=list)
    frames_counts: list[int] = Field(default_factory=list)
    frames_fps: int = 0

    audio_filename: str = ""
    audio_duration: float = 0.0
    duration: float = 0.0
    start: float = 0.0


class TellaScenePlan(BaseModel):
    """Top-level plan returned by the planner."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(..., min_length=2, max_length=160)
    language: Language = "en"
    aspect_ratio: AspectRatio = "9:16"
    media_source: MediaSource = "ai_image"
    duration_mode: DurationMode = "short"
    theme: Theme = "cinematic"

    # Voice settings (resolved by CLI from theme + user overrides before
    # the planner runs — planner just receives these as inputs and echoes
    # them back on the plan so the composer + TTS know what to do).
    voice_pace_name: VoicePaceName = "medium"
    voice_edge_rate: str = Field("0%", pattern=r"^[+-]?\d{1,3}%$")
    voice_google_rate: float = Field(1.00, ge=0.25, le=4.0)
    voice_gender: VoiceGender = "male"
    voice_name: str = ""

    # Character + setting locking — populated by planner for ai_image mode,
    # set to None for stock modes.
    character_brief: CharacterBrief | None = None
    setting_brief: SettingBrief | None = None

    scenes: list[Scene] = Field(..., min_length=3, max_length=24)

    # Channel branding (composer pulls from channel preset).
    channel_name: str = ""
    channel_handle: str = ""
    demo_mode: bool = False

    total_duration: float = 0.0


__all__ = [
    "AspectRatio",
    "CharacterBrief",
    "DurationMode",
    "Language",
    "MediaSource",
    "Scene",
    "SceneKind",
    "SettingBrief",
    "TellaScenePlan",
    "Theme",
    "VoiceGender",
    "VoicePaceName",
]
