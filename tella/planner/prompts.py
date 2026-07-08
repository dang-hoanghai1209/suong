"""Gemini system prompts for Tella's story planner.

Two axes of variation:

  - ``theme``         : parable | cinematic | playful | mindfulness |
                        minimalist_emotional
                        (defines storytelling tone + image style suffix)
  - ``duration_mode`` : short (~70-130s total, ~10-18 beats)
                        detailed (~3-5min total, ~25-40 beats)

A "scene" in Tella IS a visual beat — one image per scene. Scene count is
NOT fixed by mode; it emerges from the narration. When the narration shifts
subject/action/location, that's a new scene. Short reflective stretches
collapse into one scene; action-heavy stretches expand into many. This
prevents the "single image lingering 20 seconds" monotony.

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
        "Vivid STORYTELLER narration, third-person, cinematic and immersive "
        "— concrete sensory detail, dramatic but not melodramatic, like a "
        "well-narrated film. Works for ANY subject: real events, fables, "
        "myths, fiction. Imagery: photorealistic, cinematic lighting, film "
        "grain, shallow depth of field, teal-orange color grade. CRITICAL: "
        "depict the story's ACTUAL subjects faithfully. If the story is "
        "about animals, creatures, or objects, render THEM as the characters "
        "(a tortoise is a tortoise, a hare is a hare) — NEVER replace them "
        "with human stand-ins. Only use human characters when the story is "
        "actually about people."
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
    "minimalist_emotional": (
        "QUIET emotional short-form narrator, calm and intimate, second-person "
        "OK. This is NOT a complex animated film and NOT realistic AI video. "
        "Write a static-illustration emotional short: each scene is one simple "
        "feeling or realization, spoken as one short natural sentence. Imagery: "
        "minimalist hand-drawn emotional doodle illustration, tiny simple "
        "character, generous negative space, thin imperfect black linework, "
        "flat muted color, warm taupe bedroom, soft environmental details. "
        "Use the SAME small simple girl in EVERY normal scene: short straight black "
        "bob ending at the chin, symmetrical bob shape, mustard yellow "
        "triangular dress, soft rust sleeves, dot eyes only, tiny nose, tiny "
        "neutral mouth, stick-like legs, simple mitten hands, exactly one head, "
        "full body visible. Character should occupy only about 35-45 percent of "
        "frame height and stay above the caption lane. Each image should feel like "
        "a complete emotional illustration scene, not only a character portrait: "
        "bed, window with thin curtains, bedside table, warm table lamp, books or "
        "folded blanket, soft wall shadows, dust or memory particles, muted floor "
        "and wall shapes. Use layered composition: foreground curtain edge or soft "
        "shadow, middle ground young woman, background room details. NO self-hug, NO body-touch "
        "emotion phrases, NO close-up face, NO anime, NO realistic anatomy, NO "
        "detailed hands, NO twisted torso, NO head/body direction mismatch, NO "
        "long loose hair strands, NO duplicate head, NO second face, NO face on "
        "heart or objects, NO extra characters unless explicitly needed."
    ),
}


# ─── Duration mode structure ────────────────────────────────────────────

_SHORT_STRUCTURE = """\
STRUCTURE — short mode (~70-130 seconds total, ~10-18 scenes):

Narrative arc (use phases, NOT scene counts):
  - HOOK     — opening beat that lands the listener in the world
  - SETUP    — protagonist + setting + central tension (a few beats)
  - RISING   — events escalate toward a turning point (the bulk of beats)
  - TURN     — the pivotal moment or insight (one tight beat)
  - CLOSE    — resolution + brief emotional landing

Each scene's voice_script — HARD CAPS:
  - 1 sentence (occasionally 2 short ones)
  - English: 8-18 words. Vietnamese: 12-22 chữ. NEVER exceed these.
  - Target 4-7 seconds of speech per scene.

If a passage would exceed the cap, SPLIT it into 2+ consecutive scenes with
the next sentence as its own beat. SHORTER beats are better than longer ones
— when the narration shifts subject, action, location, or POV, START A NEW
SCENE. One image lingering > 8 seconds of speech is a planning failure.

Total scene count emerges from content density — typically 10-18 for this
mode. The schema accepts 3-40. Aim for the upper half of this range when in
doubt; padding from 6 long beats to 12 tight beats is a quality WIN.

NO trailing periods on titles. Voice copy feels natural when spoken aloud
— no run-on sentences, no academic prose.
"""

_MINIMALIST_EMOTIONAL_SHORT_STRUCTURE = """\
STRUCTURE — minimalist_emotional short mode (~32-38 seconds total, exactly 7-8 scenes):

Plan this as a vertical emotional illustration reel, not a full story film.
Produce 8 scenes unless the topic truly only supports 7. Each scene should
hold on screen for about 3-5 seconds after TTS timing, so keep narration very
short.

Recommended 8-beat micro-structure:
  1. Hook / opening emotional image
  2. Emotional setup
  3. Context / small detail
  4. Pain or tension
  5. Low point / quiet sadness
  6. Reflection / realization
  7. Healing / acceptance
  8. Final memorable line

Each scene's voice_script — HARD CAPS:
  - Exactly 1 short sentence.
  - English: 6-12 words. Vietnamese: 8-16 words. NEVER exceed this.
  - Each sentence expresses ONE emotional idea only.

Each image_prompt:
  - One simple visual concept only.
  - No complex cinematic environments.
  - No crowded scenes, no multi-action montage.
  - Include the same quiet bedroom environment as soft supporting detail:
    bed on one side, window with thin curtains, small bedside table, warm
    table lamp, a few books or folded blanket, soft wall shadows, subtle dust
    or memory particles near the window, muted floor and wall shapes.
  - Use layered composition: foreground curtain edge or soft shadow, middle
    ground young woman, background bed/window/lamp/wall details.
  - Do not make the character too large; keep her about 35-45 percent of frame
    height in medium/wide shots with negative space around her.
  - Use exactly one safe symbolic pose/concept from this catalog:
    front_standing, side_sitting, side_walking, looking_at_light,
    holding_paper_heart, beside_lamp, beside_flower, under_scribble_cloud.
  - Prefer simple symbols: glowing paper heart, warm lamp, small flower,
    grey scribble cloud, thin line path, small warm light.
  - Never write direct body-emotion phrases such as "she hugs herself",
    "she touches her pain", "she holds her wounded body", "she embraces
    herself", or "her body carries sadness".
  - Good visual phrasing: "she stands beside a small glowing paper heart",
    "a grey scribble cloud floats above her", "a small warm light rests near
    her feet", "she sits quietly beside a tiny lamp", "she looks at a small
    flower growing from the ground".

Aim for total spoken narration around 32-38 seconds. Do not pad with long
sentences; the renderer holds the static illustration with subtle motion.
"""

_DETAILED_STRUCTURE = """\
STRUCTURE — detailed mode (~3-5 minutes total, ~25-40 scenes):

Narrative arc — use 3 acts as guidance, NOT a fixed beat count:

  Act 1 — Setup
    COLD OPEN  — striking image / question / line that hooks
    WORLD      — protagonist, setting, era, daily rhythm
    FLAW       — protagonist's hidden flaw, doubt, or desire
    CATALYST   — the event that pulls them into the journey

  Act 2 — Confrontation
    TRIAL      — obstacles, temptations, loss
    CLIMAX     — crisis peaks, decision must be made
    MENTOR     — a teacher / elder / inner voice speaks one line
    REFLECT    — protagonist sits with the choice
    PIVOT      — the choice made; action taken

  Act 3 — Resolution
    OUTCOME    — consequence of the pivot
    INSIGHT    — narrator distills the lesson (over multiple beats if needed)
    CLOSE      — final image + brief invitation to the listener

Each act spans MULTIPLE scenes — each scene is a visual cut, not an act.
A single act can take 3-12 scenes depending on the density of action in it.

Each scene's voice_script — HARD CAPS:
  - 1-2 sentences (occasionally 3 if they're each very short)
  - English: 10-25 words. Vietnamese: 15-35 chữ. NEVER exceed these.
  - Target 5-9 seconds of speech per scene.

If a passage would exceed the cap, SPLIT it into 2+ consecutive scenes with
the next sentence as its own beat. CUT TO A NEW SCENE whenever the narration
shifts subject, action, location, POV, or visual focus. A long internal-
reflection passage may legitimately be 2-3 scenes back-to-back showing
different angles of the same character thinking; a fast action passage may
be 5-6 short scenes in a row.

Total scene count emerges from content density — typically 25-40 for this
mode. The schema accepts up to 40. Aim for the upper half (30+) on most
detailed-mode topics; padding from 18 long beats to 30 tight beats is a
quality WIN. DO NOT collapse two visually distinct moments into one long
beat just to keep the count down.

NEVER truncate a sentence mid-thought across scenes. Voice copy reads like
spoken word, not text.
"""


# ─── Character + setting block (AI image mode only) ─────────────────────

_CHARACTER_LOCK_BLOCK = """\
CHARACTER + SETTING LOCK (because media_source == ai_image):

🌐  CRITICAL — WRITE EVERY VISUAL FIELD IN ENGLISH. ``characters[].identity``,
    ``setting_brief.location`` (and era/mood/time_of_day), and every scene's
    ``image_prompt`` MUST be in ENGLISH even when the narration language is
    Vietnamese/Japanese/etc. The image model (FLUX) was trained on English
    and produces random, inconsistent garbage when given non-English text.
    ONLY ``title`` and ``voice_script`` use the target language.

⚠️  AI image generators have NO MEMORY across scenes. If you write
    different image_prompts for the same character, the model renders a
    different look each time — viewer confusion. To prevent that you MUST
    emit a TOP-LEVEL ``characters`` cast + ``setting_brief``, and on EACH
    scene list which cast members appear (``character_names``). The planner
    prepends the matching identities to that scene's image_prompt.

characters shape (1-4 recurring subjects — include EVERY character the story
keeps coming back to, not just one):
  [
    {
      "name":     "<short label you will reuse in scenes, e.g. 'the hare', "
                  "'the tortoise', 'Lan'. May be in any language.>",
      "identity": "<ENGLISH ONLY. 10-20 word description. For a PERSON: age, "
                  "gender, hair, outfit, features. For an ANIMAL/CREATURE/"
                  "OBJECT: describe THAT animal/thing precisely (species, "
                  "colour, markings, any clothing/props) — do NOT turn it "
                  "into a human. WRITE IN ENGLISH even for non-English "
                  "narration.>",
      "role":     "protagonist | antagonist | mentor | supporting"
    }
  ]

  IMPORTANT: if the story is about animals (a fable, a children's tale),
  the characters ARE the animals. "the tortoise" identity = a real tortoise,
  "the hare" identity = a real hare — never a human athlete or person.

  Example casts:
    - Fable: [
        {"name":"the tortoise","identity":"a small green tortoise, domed
          brown shell, wrinkled friendly face, slow steady eyes","role":"protagonist"},
        {"name":"the hare","identity":"a sleek brown hare, long ears, lean
          legs, cocky smirk, bright alert eyes","role":"antagonist"}
      ]
    - Single human: [
        {"name":"Mai","identity":"70 yo Vietnamese woman, kind round face,
          silver hair in a bun, simple brown ao dai","role":"protagonist"}
      ]

setting_brief shape (ALL fields in ENGLISH):
  {
    "location":    "<ENGLISH. short location description, 4-15 words>",
    "era":         "<period, e.g. '1960s' or '19th century' or 'timeless'>",
    "mood":        "<single word: meditative | tense | warm | cold | hopeful | …>",
    "time_of_day": "<e.g. 'golden hour' | 'blue hour' | 'midnight' | 'noon'>"
  }

PER-SCENE: set ``character_names`` to the subset of cast names that appear in
that scene (e.g. ["the hare"], or ["the hare","the tortoise"] for both, or
[] for a pure scenery/establishing shot). Each scene image_prompt describes
ONLY the ACTION / CAMERA — the planner prepends the named characters'
identities + the setting automatically. So:
  ✗ "A cocky hare napping under a tree, cinematic"   (don't restate identity)
  ✓ "napping under a broad oak, low-angle, dappled light"  + character_names ["the hare"]
"""

_MINIMALIST_EMOTIONAL_CHARACTER_LOCK_BLOCK = """\
CHARACTER + SETTING LOCK (minimalist_emotional + ai_image):

All visual fields MUST be in ENGLISH. The image model has no memory, so make
the recurring character extremely simple and repeat the same template.

Emit exactly ONE recurring character unless the topic explicitly requires
another person:
  {
    "name": "the small girl",
    "identity": "one small simple girl, short straight black bob ending at chin, symmetrical bob, mustard yellow triangular dress, soft rust sleeves, dot eyes only, tiny nose, tiny neutral mouth, stick-like legs, mitten-like hands, exactly one head, full body visible",
    "role": "protagonist"
  }

Emit a simple setting_brief such as:
  {
    "location": "quiet warm taupe bedroom with bed, window curtains, bedside table, warm lamp, books or folded blanket, soft wall shadows",
    "era": "timeless",
    "mood": "quiet",
    "time_of_day": "soft evening"
  }

PER-SCENE:
  - Set character_names to ["the small girl"] for every scene where she appears.
  - Describe only one pose, object, or emotional moment in image_prompt.
  - Use one of the safe poses only: front_standing, side_sitting,
    side_walking, looking_at_light, holding_paper_heart, beside_lamp,
    beside_flower, under_scribble_cloud.
  - Do not restate a different hairstyle, outfit, face, age, or body type.
  - Avoid self-hug, arms crossing the body, hands touching chest/body/shoulders,
    back view with visible face, head facing camera while body is side-facing,
    twisted torso, complex hand gestures, hands behind back, lying down,
    kneeling, detailed fingers.
  - Every normal scene must include the same small girl; symbolic motifs appear
    beside or near her and must be plain, faceless objects.
  - For paper heart motifs, write "tiny flat paper heart symbol with no face,
    no eyes, no mouth" and never describe a small person, inner child, younger
    self, doll, baby, or second figure.
  - Frame safety: full body visible, head fully visible, feet fully visible,
    character within central safe area, bottom 25 percent mostly empty for
    captions, character about 35-45 percent of frame height, no cropped body.
  - The image should feel like a complete emotional illustration scene, not
    only a character portrait. Include soft room details: bed on one side,
    window with thin curtains, bedside table, warm table lamp, books or folded
    blanket, soft wall shadows, subtle dust or memory particles, muted floor
    and wall shapes.
  - Avoid extra characters unless the narration explicitly needs them.
"""


_STOCK_MODE_BLOCK = """\
STOCK MODE (because media_source != ai_image):

Set ``characters: []``, ``character_brief: null`` and ``setting_brief: null``
— stock content is random, character/setting locking is impossible.

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
  - character_names: list of cast names appearing in this scene (ai_image
                     mode). [] for a scenery shot. Omit / [] in stock modes.
  - asset_count    : 1 by default (one image per beat — that's what scenes
                     ARE in this planner). Only use 2-3 when a single beat
                     genuinely needs a montage (e.g. "she tried again, and
                     again, and again"). Most scenes should be 1.
  - kind           : "scene" (cover + outro are composer-side, not planner)
"""


# ─── Global rules ───────────────────────────────────────────────────────

_GLOBAL_RULES = """\
GLOBAL RULES:

  * ONLY voice_script + title use TARGET_LANG. EVERYTHING visual is ALWAYS
    English — image_prompt, stock_query, characters[].identity, and all
    setting_brief fields. FLUX + Pexels only understand English; non-English
    visual prompts produce random, inconsistent images.
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

_CLOSING_RULE = """\
ENDING (MANDATORY — never end abruptly):

The LAST scene MUST be a real closing — a line that resolves the story and
gives the listener a sense of completion (a final reflection, a moral, a
satisfying image, or a gentle send-off). It must feel like an ending, not a
sentence that just happens to be last. Do NOT stop mid-arc, mid-action, or on
a cliffhanger. A viewer should never think "wait, is that it?".
"""


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
    if theme == "minimalist_emotional" and duration_mode == "short":
        structure = _MINIMALIST_EMOTIONAL_SHORT_STRUCTURE
    else:
        structure = _SHORT_STRUCTURE if duration_mode == "short" else _DETAILED_STRUCTURE
    media_block = (
        _MINIMALIST_EMOTIONAL_CHARACTER_LOCK_BLOCK
        if theme == "minimalist_emotional" and media_source == "ai_image"
        else _CHARACTER_LOCK_BLOCK if media_source == "ai_image" else _STOCK_MODE_BLOCK
    )

    return f"""You are a creative story planner for Tella, a short-form video tool.

THEME: {theme}
TONE: {tone}

{structure}

{_CLOSING_RULE}

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
  2. Split the script into AS MANY scenes as the story needs — do NOT force
     a fixed count. CUT TO A NEW SCENE whenever the narration shifts subject,
     action, location, POV, or visual focus — that is the only criterion.
     Target 5-10 spoken seconds per scene (Vietnamese reads at ~14 chars/sec,
     English ~15 chars/sec). NEVER let a single scene exceed ~3 sentences or
     ~12 seconds of speech — one image lingering longer than that feels
     monotonous. A long story legitimately yields 25-40 scenes; a short one
     yields 5-12. Hard cap is 40 scenes (the schema rejects more).
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
  7. If media_source = ai_image: still emit the ``characters`` cast +
     ``setting_brief`` so character lock works, and set each scene's
     ``character_names`` to who appears in it. Build the cast from the
     recurring subjects across the script — if it is an animal fable, the
     cast members ARE the animals (describe them as animals, never humans).

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
        _MINIMALIST_EMOTIONAL_CHARACTER_LOCK_BLOCK
        if theme == "minimalist_emotional" and media_source == "ai_image"
        else _CHARACTER_LOCK_BLOCK if media_source == "ai_image" else _STOCK_MODE_BLOCK
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
