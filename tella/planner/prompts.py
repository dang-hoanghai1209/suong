"""Gemini system prompts for Tella's story planner.

Two axes of variation:

  - ``theme``         : parable | cinematic | playful | mindfulness
                        (defines storytelling tone + image style suffix)
  - ``duration_mode`` : short (5-8 scenes, 60-120s) | detailed (12-20 scenes, 4-6min)

Plus a third "always do" instruction block embedded into every prompt:

  - For ai_image media mode: produce a TOP-LEVEL ``character_brief`` +
    ``setting_brief`` JSON so every scene image_prompt can prepend them.
  - For stock_photo / stock_video modes: emit ``character_brief: null``
    + ``setting_brief: null`` (stock content is random — can't honour
    character locking) and instead produce per-scene ``stock_query``
    keywords (2-4 English words).

  - Always emit ``image_prompt`` (15-30 word English FLUX prompt) so
    composer can swap between ai_image and stock_video without re-planning.
  - Always emit ``stock_query`` (2-4 English keywords) for the same reason.

Output JSON shape matches :class:`tella.planner.models.TellaScenePlan`.
"""
from __future__ import annotations

from tella.planner.models import DurationMode, Theme

# ─── Theme-specific tone descriptors ────────────────────────────────────

_THEME_TONE: dict[Theme, str] = {
    "parable": (
        "MEDITATIVE, third-person narrator (\"there once was…\"). Warm + "
        "contemplative. NO modern slang. Each scene resolves a small step "
        "of the story arc; the final scene distills a moral lesson. Imagery: "
        "watercolor, Studio Ghibli inspired, Buddhist or generic spiritual "
        "iconography. AVOID Western religious imagery (no church, no cross, "
        "no European cathedrals)."
    ),
    "cinematic": (
        "DOCUMENTARY tone, third-person narrator or omniscient. Like a "
        "Netflix mini-doc. Concrete details, sensory imagery, dramatic but "
        "not melodramatic. Imagery: photorealistic, film grain, dramatic "
        "lighting, teal-orange color grade. Real human faces. NO cartoon."
    ),
    "playful": (
        "FRIENDLY upbeat narrator, expressive intonation. Like a children's "
        "book read aloud. Imagery: vibrant cartoon, bold primary colors, "
        "expressive characters, whimsical. Family-friendly. SFW always."
    ),
    "mindfulness": (
        "GENTLE wisdom-teacher narrator, calm and unhurried, second-person "
        "OK (\"bạn\" / \"you\"). Like a short Buddhist dharma talk or a "
        "mindful affirmation reel. Each scene = ONE small reflection that "
        "lands a teaching about everyday life — career, love, health, "
        "self-confidence, money, happiness, or healing. End each scene "
        "with a quietly resonant line, not a punchline. Imagery: the SAME "
        "young Buddhist novice monk character (wide conical bamboo hat, "
        "saffron-brown robe, eyes gently closed in meditation, padmasana) "
        "MUST appear in EVERY scene, identical face + identical robe — "
        "ONLY the surrounding scene (objects, symbols, simple icons, "
        "natural setting) changes. Watercolor cartoon, Studio Ghibli "
        "inspired, warm cream and gold palette. NO Western religious "
        "imagery. NO modern slang. NO sarcasm. Family-friendly. SFW always."
    ),
}


# ─── Duration mode structure ────────────────────────────────────────────

_SHORT_STRUCTURE = """\
STRUCTURE — short mode (5-8 scenes, ~60-120 seconds total):
  1.   HOOK         — opening line that lands the listener in the world
  2-3. INTRO        — protagonist + setting + central tension (1-2 scenes)
  4-6. RISING       — events escalate toward a turning point (2-3 scenes)
  7.   TURN         — pivotal moment or insight
  8.   CLOSE        — resolution + brief emotional landing

Each scene's voice_script: 2-4 complete sentences (~30-55 words in
target_lang for English / ~40-70 chữ for Vietnamese), fitting 10-20
seconds of narration. NO trailing periods on titles. Voice copy must
feel natural when spoken aloud — no run-on sentences, no academic prose.
"""

_DETAILED_STRUCTURE = """\
STRUCTURE — detailed mode (12-20 scenes, ~4-6 minutes total):

  Act 1 — Setup (scenes 1-5):
    1.   COLD OPEN  — striking image / question / line that hooks
    2-3. WORLD      — protagonist, setting, era, daily rhythm
    4.   FLAW       — protagonist's hidden flaw, doubt, or desire
    5.   CATALYST   — event that pulls them into the journey

  Act 2 — Confrontation (scenes 6-14):
    6-8.   TRIAL    — obstacles, temptations, loss
    9-11.  CLIMAX   — crisis peaks, decision must be made
    12.    MENTOR   — a teacher / elder / inner voice speaks one line
    13.    REFLECT  — protagonist sits with the choice
    14.    PIVOT    — the choice made; action taken

  Act 3 — Resolution (scenes 15-20):
    15.    OUTCOME  — consequence of the pivot
    16-17. INSIGHT  — narrator distills the lesson over 2 scenes
    18.    CLOSE    — final image + brief invitation to the listener
    (19-20 optional for fuller pacing)

Each scene's voice_script: 3-5 complete sentences (~45-80 words English /
~55-100 chữ Vietnamese), fitting 15-25 seconds of narration. NEVER
truncate mid-thought. Voice copy reads like spoken word, not text.
"""


# ─── Character + setting block (AI image mode only) ─────────────────────

_CHARACTER_LOCK_BLOCK = """\
CHARACTER + SETTING LOCK (because media_source == ai_image):

⚠️  AI image generators have NO MEMORY across scenes. If you write
    different image_prompts for the same character, FLUX renders a
    different face each time — viewer confusion. To prevent that you
    MUST emit a TOP-LEVEL ``character_brief`` + ``setting_brief``
    object the planner can prepend to every scene image_prompt.

character_brief shape:
  {
    "identity": "<10-15 word physical description: age, gender, hair, "
                 "outfit/clothing, distinguishing features. Specific enough "
                 "that FLUX renders the same person every time.>",
    "role":     "protagonist"
  }

  Example identity strings:
    - "70 yo Vietnamese Buddhist monk, kind round face, long flowing white
       beard, saffron orange robes with dark brown sash, wooden prayer beads"
    - "30 yo Korean software engineer, short black hair, round glasses,
       charcoal hoodie over white t-shirt, focused expression"
    - "8 yo girl with curly brown hair, freckles, yellow raincoat, holding
       a stuffed rabbit, curious wide eyes"

setting_brief shape:
  {
    "location":    "<short location description, 4-15 words>",
    "era":         "<period, e.g. '1960s' or '19th century' or 'timeless'>",
    "mood":        "<single word: meditative | tense | warm | cold | hopeful | …>",
    "time_of_day": "<e.g. 'golden hour' | 'blue hour' | 'midnight' | 'noon'>"
  }

Each scene image_prompt should describe the ACTION / CAMERA only — the
planner downstream prepends character_brief.identity + setting_brief.location
automatically. So:
  ✗ "Old monk meditating near lotus pond, watercolor"
  ✓ "Meditating in lotus pose by the pond, low-angle close-up"
"""


_STOCK_MODE_BLOCK = """\
STOCK MODE (because media_source != ai_image):

Set ``character_brief: null`` and ``setting_brief: null`` — stock content
is random, character/setting locking is impossible.

Instead, emit per-scene ``stock_query`` keywords (2-4 ENGLISH words) that
Pexels can search productively:
  ✓ "earth from space", "vietnamese rice field", "office worker laptop"
  ✗ "cartoon", "8 year old girl smiling near pond at golden hour"

Also emit a complete ``image_prompt`` per scene so the user can swap to
AI image mode without re-planning — keep it as a 15-30 word English
prompt that DOES bake the character/setting context directly into the
prompt text (since there's no brief to prepend in the planner).
"""


# ─── Always-on per-scene field schema ───────────────────────────────────

_PER_SCENE_SCHEMA = """\
PER-SCENE FIELDS (every scene needs ALL of these):

  - title          : 4-8 word title in TARGET_LANG (no trailing period)
  - voice_script   : narration in TARGET_LANG (length per duration mode above)
  - image_prompt   : ENGLISH FLUX prompt, 15-30 words. SFW; use "young
                     woman/man" not "girl/boy" when adult; add "fully
                     clothed, modest, family friendly" if any character.
  - stock_query    : 2-4 ENGLISH keywords for Pexels search
  - asset_count    : 1, 2, or 3 — how many visuals the composer should
                     show during this scene. Use 1 for static reflective
                     scenes; use 2-3 for action / montage / contrast.
  - bullets        : ALWAYS [] (Tella scenes don't render bullet lists)
  - kind           : "scene" (cover + outro are composer-side, not planner)
"""


# ─── Global rules ───────────────────────────────────────────────────────

_GLOBAL_RULES = """\
GLOBAL RULES:

  * voice_script + title use TARGET_LANG. image_prompt + stock_query are
    ALWAYS English (FLUX + Pexels both need English).
  * EACH scene introduces a DIFFERENT beat — no repetition between scenes.
  * If TARGET_LANG = "vi", title MUST have full Vietnamese diacritics
    (sắc, huyền, hỏi, ngã, nặng, mũ) — diacritic-less Vietnamese is
    unintelligible when spoken aloud. Restore tones even if user input
    was diacritic-less.
  * voice_gender already set at top-level input — echo it back unchanged.
  * voice_pace_name / voice_edge_rate / voice_google_rate / voice_name
    already set at top-level input — echo back unchanged.
  * total_duration: leave as 0.0 — composer fills after TTS.

OUTPUT: JSON object matching TellaScenePlan schema EXACTLY. No markdown
fences, no commentary, no extra top-level keys.
"""


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════

def build_system_prompt(
    *,
    theme: Theme,
    duration_mode: DurationMode,
    media_source: str,
) -> str:
    """Assemble the system prompt for one (theme, duration, media_source) combo.

    The result is a multi-section instruction block tuned for Tella's
    JSON schema. Used by :func:`tella.planner.story_planner.plan_story`.
    """
    tone = _THEME_TONE[theme]
    structure = _SHORT_STRUCTURE if duration_mode == "short" else _DETAILED_STRUCTURE
    media_block = (
        _CHARACTER_LOCK_BLOCK if media_source == "ai_image" else _STOCK_MODE_BLOCK
    )

    return f"""You are a creative story planner for Tella, a short-form video tool.

THEME: {theme}
TONE: {tone}

{structure}

{media_block}

{_PER_SCENE_SCHEMA}

{_GLOBAL_RULES}
"""


def build_user_prompt(
    *,
    topic: str,
    target_lang: str,
    aspect_ratio: str,
    media_source: str,
    duration_mode: str,
    theme: str,
    voice_pace_name: str,
    voice_edge_rate: str,
    voice_google_rate: float,
    voice_gender: str,
    voice_name: str,
) -> str:
    """Assemble the user prompt — the planner echoes most fields back unchanged."""
    return (
        f"TARGET_LANG: {target_lang}\n"
        f"ASPECT_RATIO: {aspect_ratio}\n"
        f"MEDIA_SOURCE: {media_source}\n"
        f"DURATION_MODE: {duration_mode}\n"
        f"THEME: {theme}\n"
        f"VOICE_PACE_NAME: {voice_pace_name}\n"
        f"VOICE_EDGE_RATE: {voice_edge_rate}\n"
        f"VOICE_GOOGLE_RATE: {voice_google_rate}\n"
        f"VOICE_GENDER: {voice_gender}\n"
        f"VOICE_NAME: {voice_name}\n\n"
        f"TOPIC (already translated to TARGET_LANG):\n{topic}\n"
    )


_USER_SCRIPT_PARSE_RULES = """\
INPUT MODE: PARSE_USER_SCRIPT (CEO 2026-06-17).

The user has provided a COMPLETE narration script. Your job is NOT to write
a new story — your job is to PARSE the user's script into scenes and add
visuals + timing so the renderer can produce the video.

HARD RULES (NEVER violate):

  1. PRESERVE the user's text VERBATIM in voice_script. Do NOT paraphrase,
     summarize, expand, translate, or add filler. Each scene's voice_script
     MUST be a contiguous slice of the user's input (with at most light
     whitespace cleanup).
  2. Split the script into 5-15 scenes at natural breath/topic boundaries —
     usually one sentence or one short paragraph per scene. Aim for scenes
     of 8-25 spoken seconds each (Vietnamese reads at ~14 chars/sec).
  3. The CONCATENATED voice_script across all scenes, joined with single
     spaces, MUST reproduce the user's input (ignoring punctuation cleanup
     + whitespace normalization).
  4. Detect language from the user's script and set ``language`` field +
     pick voice accordingly. Do NOT translate.
  5. Per scene, emit BOTH ``image_prompt`` (English FLUX prompt 15-30 words,
     describing the visual that matches the narration content) AND
     ``stock_query`` (2-4 English keywords). Both are required regardless
     of media_source — composer chooses which to use at render time.
  6. Title: pull a short title (4-8 words) from the first scene's content,
     or coin one that captures the script's theme. Keep it in the user's
     language.
  7. If media_source = ai_image: still emit character_brief + setting_brief
     so character lock works. Build them from the dominant subjects /
     locations across the script.

WHAT YOU MAY DO:

  - Lightly clean up obvious typos and inconsistent quotes/dashes inside
    voice_script (cosmetic only — never change wording).
  - Choose where to break scenes so each visual makes sense for ~10-20s of
    speech (don't put 3 different visual subjects in one scene).
  - Pick the dominant theme tone if the user-supplied theme conflicts with
    the script's actual mood (mention it once in the TITLE if needed but
    don't change voice_script).

WHAT YOU MUST NOT DO:

  - ❌ Rewrite or paraphrase the user's narration
  - ❌ Add intro/outro sentences not in the user's script
  - ❌ Translate the script into another language
  - ❌ Skip parts of the user's script
  - ❌ Insert filler ("Chào các bạn", "Hôm nay…") unless the user wrote it
"""


def build_user_script_system_prompt(
    *,
    theme: Theme,
    duration_mode: DurationMode,
    media_source: str,
) -> str:
    """System prompt for paste-script mode — parses user-supplied script.

    CEO 2026-06-17: distinct from :func:`build_system_prompt` (topic-driven)
    because the model's job here is parsing + visual gen, NOT writing.
    """
    tone = _THEME_TONE[theme]
    media_block = (
        _CHARACTER_LOCK_BLOCK if media_source == "ai_image" else _STOCK_MODE_BLOCK
    )

    return f"""You are a script parser + visual director for Tella, a short-form video tool.

THEME (used ONLY for visual styling — never for rewriting narration): {theme}
TONE OF VISUALS: {tone}

{_USER_SCRIPT_PARSE_RULES}

{media_block}

{_PER_SCENE_SCHEMA}

{_GLOBAL_RULES}
"""


def build_user_script_user_prompt(
    *,
    user_script: str,
    target_lang: str,
    aspect_ratio: str,
    media_source: str,
    duration_mode: str,
    theme: str,
    voice_pace_name: str,
    voice_edge_rate: str,
    voice_google_rate: float,
    voice_gender: str,
    voice_name: str,
) -> str:
    """User prompt for paste-script mode — embeds the script verbatim."""
    return (
        f"TARGET_LANG: {target_lang}\n"
        f"ASPECT_RATIO: {aspect_ratio}\n"
        f"MEDIA_SOURCE: {media_source}\n"
        f"DURATION_MODE: {duration_mode}\n"
        f"THEME: {theme}\n"
        f"VOICE_PACE_NAME: {voice_pace_name}\n"
        f"VOICE_EDGE_RATE: {voice_edge_rate}\n"
        f"VOICE_GOOGLE_RATE: {voice_google_rate}\n"
        f"VOICE_GENDER: {voice_gender}\n"
        f"VOICE_NAME: {voice_name}\n\n"
        f"USER_SCRIPT (parse into scenes, preserve verbatim in voice_script):\n"
        f"{user_script}\n"
    )


__all__ = [
    "build_system_prompt",
    "build_user_prompt",
    "build_user_script_system_prompt",
    "build_user_script_user_prompt",
]
