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

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ─── Vocabulary ────────────────────────────────────────────────────────

# ISO-639-1 — matches tella.ingest.topic_translator.SUPPORTED_LANGS.
Language = Literal["vi", "en", "ja", "ko", "zh", "de", "fr", "es"]
AspectRatio = Literal["9:16", "16:9"]
MediaSource = Literal["ai_image", "stock_photo", "stock_video"]
DurationMode = Literal["short", "detailed"]
Theme = Literal[
    "parable",
    "cinematic",
    "playful",
    "mindfulness",
    "minimalist_emotional",
    "minimalist_symbolic_reel",
]
VoicePaceName = Literal["slow", "medium", "fast", "custom"]
VoiceGender = Literal["male", "female"]
SceneKind = Literal["cover", "scene", "outro"]


class CharacterBrief(BaseModel):
    """One character's locked identity (AI image mode only).

    A story can have several of these (the "cast"). Each scene lists which
    cast members appear in it by ``name`` so the right identities get
    prepended to that scene's image prompt — this keeps a turtle a turtle
    and a rabbit a rabbit across every shot, instead of collapsing a
    multi-character fable into one stand-in.
    """

    model_config = ConfigDict(extra="ignore")

    name: str = Field(
        "",
        max_length=60,
        description="Short label used to reference this character from a "
        "scene's character_names, e.g. 'the rabbit', 'the turtle', 'Lan'. "
        "Use the SAME spelling in scene.character_names.",
    )
    identity: str = Field(
        ...,
        min_length=10,
        max_length=280,
        description="10-20 word physical description: species/age/gender, "
        "colours, outfit, distinguishing features. Copied verbatim into "
        "every scene's image_prompt where this character appears so the "
        "image model renders the same subject across the video. If the "
        "character is an animal or object, describe THAT — never swap it "
        "for a human stand-in.",
    )
    role: Literal["protagonist", "antagonist", "mentor", "narrator", "supporting"] = "protagonist"


class SettingBrief(BaseModel):
    """One-shot world/setting identity locked across all scenes."""

    model_config = ConfigDict(extra="ignore")

    location: str = Field(..., min_length=4, max_length=200)
    era: str = Field("timeless", max_length=80)
    mood: str = Field("neutral", max_length=80)
    time_of_day: str = Field("golden hour", max_length=80)


class CharacterSpec(BaseModel):
    """Job-scoped visual identity for one character."""

    model_config = ConfigDict(extra="ignore")

    character_id: str = Field(..., max_length=80)
    role: str = Field("main_character", max_length=80)
    gender_or_presentation: str = Field("", max_length=120)
    age_style: str = Field("", max_length=120)
    body_style: str = Field("", max_length=160)
    hair: str = Field("", max_length=200)
    face: str = Field("", max_length=220)
    outfit: str = Field("", max_length=220)
    palette: str = Field("", max_length=220)
    accessories: list[str] = Field(default_factory=list)
    emotional_range: list[str] = Field(default_factory=list)
    identity_lock_phrases: list[str] = Field(default_factory=list)
    negative_identity_phrases: list[str] = Field(default_factory=list)
    consistency_notes: str = Field("", max_length=500)


class StyleBible(BaseModel):
    """Visual style lock for a job."""

    model_config = ConfigDict(extra="ignore")

    style_name: str = Field("minimalist_emotional_reference", max_length=120)
    art_style_prompt: str = Field("", max_length=500)
    palette_prompt: str = Field("", max_length=300)
    linework_prompt: str = Field("", max_length=300)
    rendering_prompt: str = Field("", max_length=300)
    composition_prompt: str = Field("", max_length=400)
    background_prompt: str = Field("", max_length=300)
    motion_prompt: str = Field("", max_length=300)
    negative_prompt: str = Field("", max_length=700)
    aspect_ratio: AspectRatio = "9:16"
    safety_margin_notes: str = Field("", max_length=300)


class VisualBible(BaseModel):
    """Job-scoped visual continuity bible."""

    model_config = ConfigDict(extra="ignore")

    style_bible: StyleBible
    character_specs: list[CharacterSpec] = Field(default_factory=list)
    environment_locks: list[str] = Field(default_factory=list)
    palette_locks: list[str] = Field(default_factory=list)
    composition_locks: list[str] = Field(default_factory=list)
    global_negative_prompt: str = Field("", max_length=1000)
    continuity_rules: list[str] = Field(default_factory=list)


class CharacterReference(BaseModel):
    """Generated per-job reference image metadata."""

    model_config = ConfigDict(extra="ignore")

    character_id: str = Field("", max_length=80)
    reference_id: str = Field("", max_length=120)
    image_path: str = Field("", max_length=300)
    prompt_used: str = Field("", max_length=2000)
    status: str = Field("", max_length=40)
    score: float = 0.0
    selected: bool = False
    failure_reason: str = Field("", max_length=500)
    provider: str = Field("", max_length=80)
    seed: int | None = None
    hash: str = Field("", max_length=80)


class SceneVisualPlan(BaseModel):
    """Prompt plan used to generate one scene visual."""

    model_config = ConfigDict(extra="ignore")

    scene_index: int
    visual_prompt: str = Field("", max_length=4000)
    character_ids: list[str] = Field(default_factory=list)
    character_reference_ids: list[str] = Field(default_factory=list)
    previous_scene_reference_path: str = Field("", max_length=300)
    action: str = Field("", max_length=500)
    emotion_tag: str = Field("", max_length=80)
    pose_action_description: str = Field("", max_length=500)
    location: str = Field("", max_length=300)
    props: list[str] = Field(default_factory=list)
    continuity_notes: str = Field("", max_length=800)
    negative_prompt: str = Field("", max_length=1500)
    expected_character_count: int = 1
    expected_object_count: int = 0


class SceneQCResult(BaseModel):
    """Lightweight QC result for one generated scene image."""

    model_config = ConfigDict(extra="ignore")

    scene_index: int
    passed: bool = False
    final_passed: bool = False
    model_passed: bool = False
    model_qc_passed: bool = False
    basic_qc_passed: bool = False
    confidence: float = 0.0
    score: float = 0.0
    checks: dict[str, bool] = Field(default_factory=dict)
    failure_reasons: list[str] = Field(default_factory=list)
    repair_prompt: str = Field("", max_length=1500)
    attempt_count: int = 0
    qc_mode: str = Field("", max_length=20)
    vision_available: bool = False
    vision_model: str = Field("", max_length=120)
    vision_qc_call_count: int = 0
    qc_json_parse_attempt_count: int = 0
    raw_response_path: str = Field("", max_length=300)
    image_path: str = Field("", max_length=300)

    shot_type: str = Field("", max_length=60)
    body_visibility: str = Field("", max_length=80)
    pose_type: str = Field("", max_length=80)
    anatomy_expectation: str = Field("", max_length=500)

    main_character_visible: bool = False
    expected_character_count: int = 1
    character_count: int = 0
    head_count: int = 0
    arm_count: int = 0
    leg_count: int = 0
    visible_foot_count: int = 0
    visible_hand_count: int = 0
    has_extra_limbs: bool = False
    has_missing_limbs: bool = False
    has_duplicate_face: bool = False
    has_text_or_watermark: bool = False
    bad_crop: bool = False
    lower_body_visible: bool = False
    legs_visible: bool = False

    hairstyle_matches_spec: bool = True
    outfit_matches_spec: bool = True
    scene_matches_requested_action: bool = True
    identity_soft_fail: bool = False
    identity_hard_fail: bool = False
    action_soft_fail: bool = False
    action_hard_fail: bool = False
    action_mismatch_severity: Literal["none", "minor", "major"] = "none"
    action_mismatch_severity_history: list[str] = Field(default_factory=list)
    hairstyle_mismatch_streak: int = 0
    outfit_mismatch_streak: int = 0
    action_mismatch_streak: int = 0
    repeated_soft_fail_escalation_applied: bool = False
    repeated_soft_fail_escalation_reasons: list[str] = Field(default_factory=list)
    stopped_retry_loop_early_due_to_repeated_soft_fail: bool = False
    soft_fail_on_final_attempt: bool = False
    previous_attempt_identity_failures: list[str] = Field(default_factory=list)
    repeated_identity_failures: list[str] = Field(default_factory=list)
    escalation_applied: bool = False
    escalation_reasons: list[str] = Field(default_factory=list)

    anatomy_hard_fail: bool = False
    deterministic_override_applied: bool = False
    deterministic_override_reasons: list[str] = Field(default_factory=list)
    final_attempt_hard_fail_reasons: list[str] = Field(default_factory=list)
    final_attempt_soft_fail_reasons: list[str] = Field(default_factory=list)
    loop_stop_reason: str = Field("", max_length=300)
    loop_stop_reasons_all: list[str] = Field(default_factory=list)
    hard_fail_priority_reason: str = Field("", max_length=300)
    scene_image_attempt_count: int = 0
    remaining_scene_attempts: int = 0
    regeneration_reasons: list[str] = Field(default_factory=list)
    original_reference_paths: list[str] = Field(default_factory=list)


class Scene(BaseModel):
    """One scene = one TTS-narrated segment with 1-3 visual assets."""

    model_config = ConfigDict(extra="ignore")

    scene_index: int = Field(..., ge=0)
    kind: SceneKind = "scene"
    title: str = Field("", max_length=200)
    voice_script: str = Field(..., min_length=1, max_length=600)

    # Planner emits BOTH so composer can pick per media_source without
    # re-asking Gemini. Planner emits ``image_prompt`` as a short English FLUX
    # prompt; downstream character/style locking may expand it before save.
    # ``stock_query`` = 2-4 English keywords for Pexels.
    image_prompt: str = Field("", max_length=4000)
    stock_query: str = Field("", max_length=80)

    # Which cast members (by CharacterBrief.name) appear in this scene.
    # character_lock prepends those identities to image_prompt. Empty = an
    # establishing / scenery shot with no recurring character.
    character_names: list[str] = Field(default_factory=list)
    requested_characters: list[str] = Field(default_factory=list)
    required_characters: list[str] = Field(default_factory=list)
    cast_source: str = Field("", max_length=80)
    cast_fallback_applied: bool = False
    prompt_contains_secondary_character: bool = False
    scene_setting: str = Field("", max_length=80)
    scene_action: str = Field("", max_length=80)
    setting_source: str = Field("", max_length=80)
    action_source: str = Field("", max_length=80)
    prompt_setting_matches_story: bool = False
    prompt_action_matches_story: bool = False
    scene_meaning: str = Field("", max_length=300)
    symbolic_visual: str = Field("", max_length=300)
    emotional_metaphor: str = Field("", max_length=300)
    main_character_or_object: str = Field("", max_length=160)
    subtitle_highlight_words: list[str] = Field(default_factory=list)

    # 1-3 assets per scene. 1 = static Ken Burns; 2-3 = mini-montage with
    # crossfades inside the scene window.
    asset_count: int = Field(1, ge=1, le=3)

    # Media fetch status for debugging provider failures and local fallbacks.
    asset_status: Literal[
        "",
        "done",
        "reference_generated",
        "local_composed",
        "sanitized_retry",
        "abstract_fallback",
        "ai_provider_quota_exhausted",
        "ai_provider_failed",
        "reused_asset",
    ] = ""
    asset_error: str = Field("", max_length=300)
    pose_family: str = Field("", max_length=60)
    primary_motif: str = Field("", max_length=60)
    optional_secondary_motif: str = Field("", max_length=60)
    composition_hint: str = Field("", max_length=240)
    frame_safety_hint: str = Field("", max_length=300)
    emotion_tag: str = Field("", max_length=60)
    layout_template: str = Field("", max_length=80)
    character_id: str = Field("", max_length=80)
    character_mode: str = Field("", max_length=20)
    character_source: str = Field("", max_length=40)
    sprite_path: str = Field("", max_length=240)
    rig_parts_used: list[str] = Field(default_factory=list)
    is_placeholder_sprite: bool = False
    is_placeholder_rig: bool = False
    selected_expression: str = Field("", max_length=60)
    head_base_path: str = Field("", max_length=240)
    face_path: str = Field("", max_length=240)
    is_placeholder_head: bool = False
    is_placeholder_face: bool = False
    socket_alignment_fallback: bool = False
    compatible_motif_used: bool = False
    focal_anchor: str = Field("", max_length=80)
    character_bbox: str = Field("", max_length=80)
    motif_bbox: str = Field("", max_length=80)
    asset_hash: str = Field("", max_length=80)
    image_source: str = Field("", max_length=80)
    image_provider: str = Field("", max_length=80)
    used_local_fallback: bool = False
    asset_path: str = Field("", max_length=300)
    ai_provider_error_type: str = Field("", max_length=80)
    ai_provider_error_message: str = Field("", max_length=500)
    ai_provider_recoverable: bool = True
    local_fallback_allowed: bool = False
    reused_asset: bool = False
    reused_from_job_id: str = Field("", max_length=160)
    reused_asset_prompt_hash_mismatch: bool = False
    reuse_mode: str = Field("", max_length=40)
    asset_prompt_hash: str = Field("", max_length=80)
    nsfw_retry_attempted: bool = False
    nsfw_retry_succeeded: bool = False
    original_prompt_hash: str = Field("", max_length=80)
    sanitized_prompt_hash: str = Field("", max_length=80)
    sanitized_prompt_used: str = Field("", max_length=4000)
    original_prompt_summary: str = Field("", max_length=500)
    sanitized_prompt_summary: str = Field("", max_length=500)
    content_policy_blocked_count: int = 0
    ai_images_requested: int = 0
    ai_images_generated: int = 0
    ai_images_reused: int = 0
    visual_mode: str = Field("", max_length=40)
    provider: str = Field("", max_length=80)
    used_reference_conditioning: bool = False
    reference_paths: list[str] = Field(default_factory=list)
    previous_scene_reference_path: str = Field("", max_length=300)
    prompt_used: str = Field("", max_length=4000)
    negative_prompt_used: str = Field("", max_length=1500)
    attempt_count: int = 0
    attempts_actually_ran: int = 0
    max_attempts_allowed: int = 0
    qc_passed: bool = False
    final_passed: bool = False
    model_qc_passed: bool = False
    qc_mode: str = Field("", max_length=20)
    qc_confidence: float = 0.0
    qc_score: float = 0.0
    qc_failure_reasons: list[str] = Field(default_factory=list)
    repair_prompt: str = Field("", max_length=1500)
    selected_attempt_path: str = Field("", max_length=300)
    selected_best_failed_attempt: bool = False
    selected_best_failed_attempt_reason: str = Field("", max_length=300)
    best_attempt_ranking_summary: str = Field("", max_length=800)

    shot_type: str = Field("", max_length=60)
    body_visibility: str = Field("", max_length=80)
    pose_type: str = Field("", max_length=80)
    anatomy_expectation: str = Field("", max_length=500)

    used_reference_conditioning_on_repair: bool = False
    repair_reference_paths: list[str] = Field(default_factory=list)

    deterministic_override_applied: bool = False
    deterministic_override_reasons: list[str] = Field(default_factory=list)
    identity_soft_fail: bool = False
    identity_hard_fail: bool = False
    action_soft_fail: bool = False
    action_hard_fail: bool = False
    action_mismatch_severity: Literal["none", "minor", "major"] = "none"
    action_mismatch_severity_history: list[str] = Field(default_factory=list)
    hairstyle_matches_spec: bool = True
    outfit_matches_spec: bool = True
    scene_matches_requested_action: bool = True
    hairstyle_mismatch_streak: int = 0
    outfit_mismatch_streak: int = 0
    action_mismatch_streak: int = 0
    repeated_soft_fail_escalation_applied: bool = False
    repeated_soft_fail_escalation_reasons: list[str] = Field(default_factory=list)
    stopped_retry_loop_early_due_to_repeated_soft_fail: bool = False
    soft_fail_on_final_attempt: bool = False
    previous_attempt_identity_failures: list[str] = Field(default_factory=list)
    repeated_identity_failures: list[str] = Field(default_factory=list)
    escalation_applied: bool = False
    escalation_reasons: list[str] = Field(default_factory=list)
    final_attempt_hard_fail_reasons: list[str] = Field(default_factory=list)
    final_attempt_soft_fail_reasons: list[str] = Field(default_factory=list)
    loop_stop_reason: str = Field("", max_length=300)
    loop_stop_reasons_all: list[str] = Field(default_factory=list)
    anatomy_hard_fail: bool = False
    hard_fail_priority_reason: str = Field("", max_length=300)
    scene_image_attempt_count: int = 0
    remaining_scene_attempts: int = 0
    regeneration_reasons: list[str] = Field(default_factory=list)
    original_reference_paths: list[str] = Field(default_factory=list)

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
    # emptied for stock modes.
    #
    # ``characters`` is the cast (0-4 recurring subjects). ``character_brief``
    # is kept for backward compatibility / single-protagonist stories; when
    # ``characters`` is non-empty it takes precedence.
    characters: list[CharacterBrief] = Field(default_factory=list, max_length=4)
    character_brief: CharacterBrief | None = None
    primary_character: CharacterBrief | None = None
    secondary_character: CharacterBrief | None = None
    setting_brief: SettingBrief | None = None

    scenes: list[Scene] = Field(..., min_length=3, max_length=40)

    # Channel branding (composer pulls from channel preset). Only the name
    # is shown on screen; avatar is an optional image path.
    channel_name: str = ""
    channel_handle: str = ""
    channel_avatar: str = ""
    demo_mode: bool = False

    # Single continuous narration. The TTS layer synthesizes EVERY scene's
    # voice_script joined into one call (eliminates per-file leading/trailing
    # silence that compounds at scene boundaries). The render layer mixes
    # this onto the concatenated video-only stream at the very end.
    # Per-scene ``audio_duration`` still drives visual timing — it's derived
    # from this file's total duration × scene char proportion.
    narration_audio_filename: str = ""
    narration_audio_path: str = ""
    narration_duration: float = 0.0
    global_narration_text: str = Field("", max_length=12000)
    tts_continuous: bool = False
    tts_text_source: str = Field("", max_length=80)
    tts_max_pause_ms: int = 350
    tts_style: str = Field("", max_length=80)
    silence_postprocess_applied: bool = False
    original_narration_duration: float = 0.0
    processed_narration_duration: float = 0.0
    longest_silence_before: float = 0.0
    longest_silence_after: float = 0.0
    tts_provider: str = Field("", max_length=80)
    tts_voice: str = Field("", max_length=120)
    tts_language: str = Field("", max_length=20)
    tts_speed: float = 1.0
    tts_codec: str = Field("", max_length=20)
    tts_sample_rate: int = 0
    tts_fallback_used: bool = False
    tts_fallback_reason: str = Field("", max_length=500)
    tts_metadata: dict[str, Any] = Field(default_factory=dict)
    scene_timing_map: list[dict[str, float | int]] = Field(default_factory=list)
    subtitle_style: str = Field("", max_length=80)
    subtitle_segments: list[dict[str, Any]] = Field(default_factory=list)
    total_vision_qc_calls: int = 0
    total_scene_regeneration_attempts: int = 0
    total_qc_json_parse_attempts: int = 0
    ai_provider_error_type: str = Field("", max_length=80)
    ai_provider_error_message: str = Field("", max_length=500)
    ai_provider_recoverable: bool = True
    local_fallback_allowed: bool = False
    used_local_fallback: bool = False
    reused_asset: bool = False
    reused_from_job_id: str = Field("", max_length=160)
    reused_asset_prompt_hash_mismatch: bool = False
    reuse_mode: str = Field("", max_length=40)
    content_policy_blocked_count: int = 0
    ai_images_requested: int = 0
    ai_images_generated: int = 0
    ai_images_reused: int = 0

    total_duration: float = 0.0

    @model_validator(mode="after")
    def _validate_symbolic_reel_contract(self) -> "TellaScenePlan":
        if self.theme != "minimalist_symbolic_reel":
            return self

        missing: list[str] = []
        for scene in self.scenes:
            if scene.kind != "scene":
                continue
            if not (scene.scene_meaning or "").strip():
                missing.append(f"scene {scene.scene_index}: scene_meaning")
            if not (scene.symbolic_visual or "").strip():
                missing.append(f"scene {scene.scene_index}: symbolic_visual")
        if missing:
            raise ValueError(
                "minimalist_symbolic_reel requires non-empty symbolic scene "
                "fields: " + ", ".join(missing[:12])
            )
        return self


__all__ = [
    "AspectRatio",
    "CharacterBrief",
    "CharacterReference",
    "CharacterSpec",
    "DurationMode",
    "Language",
    "MediaSource",
    "Scene",
    "SceneQCResult",
    "SceneVisualPlan",
    "SceneKind",
    "SettingBrief",
    "TellaScenePlan",
    "Theme",
    "StyleBible",
    "VisualBible",
    "VoiceGender",
    "VoicePaceName",
]
