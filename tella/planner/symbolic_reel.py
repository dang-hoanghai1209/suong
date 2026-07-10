"""Deterministic repairs for the minimalist_symbolic_reel theme."""
from __future__ import annotations

import re
import unicodedata

from tella.planner.models import TellaScenePlan

_VISUAL_IDENTITY_ID = "symbolic_dusk_taupe_v1"
_PALETTE_ID = "dusk_taupe_earth_limited_v1"
_LINE_STYLE_ID = "soft_rough_pencil_consistent_v1"
_CAST_ARCHETYPE_SET = (
    "symbolic_object",
    "adult_woman",
    "adult_man",
    "adult_woman_or_man",
)
_AGE_POLICY = "adult_only_unless_script_explicitly_requests_other_age"
_OUTFIT_STYLE_FAMILY = "simple_timeless_muted_earth_clothing"
_SUBJECT_SCALE_PROFILE = "small_to_medium_subject_with_negative_space"

_SYMBOLIC_STYLE = (
    "minimalist hand-drawn emotional doodle illustration, dark warm taupe "
    "background, warm dusk-like muted brown-gray backdrop, soft low-key ambient "
    "light, simple expressive character or symbolic object, soft rough pencil "
    "lines, flat muted earthy colors, consistent earthy palette, centered "
    "composition, lots of negative space, low visual clutter, stronger emotional "
    "depth, clear tonal contrast, gentle melancholic mood, not black, not cold "
    "gray, no text, no watermark, no realistic rendering, no 3D, no anime, no "
    "complex background"
)

_SYMBOLIC_VISUALS = (
    "small paper heart with one soft crack",
    "tiny glowing dot beside a gray cloud",
    "single empty chair drawn as a soft outline",
    "mustard thread untangling into a loose circle",
    "small red seed under a transparent glass dome",
    "simple figure standing beside a low gray shadow",
    "folded note with a muted red corner",
    "small sprout growing from a thin pencil line",
)

_METAPHORS = (
    "a feeling that is still tender but no longer hidden",
    "a small hope staying alive inside tired days",
    "the quiet space left by someone absent",
    "a knot of worry becoming easier to hold",
    "care returning slowly in a protected place",
    "sadness becoming a soft shape outside the self",
    "words kept gently instead of carried heavily",
    "new calm growing from a very small beginning",
)

_STOPWORDS = {
    "a", "an", "and", "the", "to", "of", "in", "on", "with", "for", "but",
    "is", "are", "was", "were", "she", "he", "her", "him", "you", "your",
    "co", "ay", "mot", "minh", "va", "la", "da", "duoc", "trong", "nhung",
    "ngay", "hon", "khong", "cho", "roi", "that", "this", "still",
}

_PHRASE_HIGHLIGHTS = (
    "m\u1ed9t m\u00ecnh",
    "im l\u1eb7ng",
    "bu\u00f4ng xu\u1ed1ng",
    "kh\u00f4ng n\u00f3i ra",
)

_SETTING_TERMS = {
    "bedroom": "quiet bedroom suggested by the script",
    "bed": "quiet bedroom suggested by the script",
    "window": "simple window shape requested by the script",
    "curtain": "simple curtain shape requested by the script",
    "room": "quiet room requested by the script",
    "phong": "quiet room requested by the script",
    "giuong": "quiet bedroom suggested by the script",
    "cua so": "simple window shape requested by the script",
}

_FEMALE_ARCHETYPE_TERMS = (
    "woman",
    "female",
    "co ay",
    "nguoi phu nu",
)

_MALE_ARCHETYPE_TERMS = (
    "man",
    "male",
    "anh ay",
    "nguoi dan ong",
)

_HUMAN_ARCHETYPE_TERMS = (
    *_FEMALE_ARCHETYPE_TERMS,
    *_MALE_ARCHETYPE_TERMS,
    "person",
    "people",
    "human",
    "figure",
    "nguoi",
)

_EXPLICIT_NON_ADULT_TERMS = (
    "child",
    "children",
    "kid",
    "little boy",
    "little girl",
    "baby",
    "infant",
    "tre em",
    "dua tre",
    "em be",
    "be trai",
    "be gai",
)


def enforce_symbolic_reel_plan(plan: TellaScenePlan) -> None:
    """Stamp symbolic scene metadata and safe plain-background prompts."""
    if plan.theme != "minimalist_symbolic_reel":
        return

    plan.subtitle_style = "reel_minimal"
    plan.visual_identity_id = _VISUAL_IDENTITY_ID
    plan.cast_archetype_set = list(_CAST_ARCHETYPE_SET)
    plan.age_policy = _AGE_POLICY
    plan.palette_id = _PALETTE_ID
    plan.line_style_id = _LINE_STYLE_ID
    plan.outfit_style_family = _OUTFIT_STYLE_FAMILY
    plan.subject_scale_profile = _SUBJECT_SCALE_PROFILE
    for idx, scene in enumerate((s for s in plan.scenes if s.kind == "scene"), start=1):
        seed_text = " ".join(
            p for p in (scene.title, scene.voice_script, scene.scene_meaning) if p
        ).strip()
        scene.scene_meaning = scene.scene_meaning or _meaning_from_text(seed_text)
        scene.symbolic_visual = scene.symbolic_visual or _SYMBOLIC_VISUALS[(idx - 1) % len(_SYMBOLIC_VISUALS)]
        scene.emotional_metaphor = scene.emotional_metaphor or _METAPHORS[(idx - 1) % len(_METAPHORS)]
        scene.main_character_or_object = scene.main_character_or_object or scene.symbolic_visual
        scene.subtitle_highlight_words = scene.subtitle_highlight_words or _highlight_words(scene.voice_script)
        scene.visual_mode = "symbolic_listicle"
        scene.visual_identity_id = plan.visual_identity_id
        scene.cast_archetype = _cast_archetype_for_scene(scene)
        scene.age_policy = plan.age_policy
        scene.palette_id = plan.palette_id
        scene.line_style_id = plan.line_style_id
        scene.outfit_style_family = plan.outfit_style_family
        scene.subject_scale_profile = plan.subject_scale_profile
        (
            scene.symbolic_qc_expected_subjects,
            scene.symbolic_qc_expectations,
        ) = _symbolic_qc_expectations(scene)
        scene.symbolic_qc_final_status = "planned"
        scene.image_prompt = _symbolic_prompt(scene)
        scene.stock_query = scene.stock_query or "symbolic emotional doodle"
        scene.character_names = []
        scene.requested_characters = []
        scene.required_characters = []


def _symbolic_prompt(scene) -> str:
    explicit_setting = _explicit_setting_phrase(
        " ".join([scene.title or "", scene.voice_script or "", scene.scene_meaning or ""])
    )
    parts = [
        _SYMBOLIC_STYLE,
        _symbolic_identity_prompt(scene),
        f"scene meaning: {scene.scene_meaning}",
        f"symbolic visual: {scene.symbolic_visual}",
        f"emotional metaphor: {scene.emotional_metaphor}",
        f"main character or object: {scene.main_character_or_object}",
    ]
    if explicit_setting:
        parts.append(explicit_setting)
    parts.append(
        "very limited background detail, plain symbolic composition, no multiple unnecessary characters"
    )
    return ", ".join(p.strip(" ,") for p in parts if p and p.strip(" ,"))


def _symbolic_identity_prompt(scene) -> str:
    explicit_non_adult = _contains_term(
        _ascii_key(scene.voice_script),
        _EXPLICIT_NON_ADULT_TERMS,
    )
    age_constraint = (
        "this scene explicitly requests a non-adult age; follow only that "
        "explicit script age"
        if explicit_non_adult
        else "adult woman or adult man only unless the script explicitly asks "
        "otherwise, adult age band, no child"
    )
    return (
        f"global visual identity id: {scene.visual_identity_id}, "
        f"palette id: {scene.palette_id}, line style id: {scene.line_style_id}, "
        "use the same illustration language and the same line thickness feel "
        "across every scene, use the same limited dusk-taupe earthy palette, "
        "all human figures must belong to the same understated adult symbolic "
        f"visual family, cast archetype: {scene.cast_archetype}, {age_constraint}, "
        f"outfit style family: {scene.outfit_style_family}, simple timeless "
        "muted-earth clothing for any human figure, subject scale profile: "
        f"{scene.subject_scale_profile}, small-to-medium subjects with generous "
        "negative space, human figures may vary by scene and no single recurring "
        "protagonist is required, no medical mask, no ghost, no monster, no blob "
        "creature, no unrelated photorealistic figures"
    )


def _cast_archetype_for_scene(scene) -> str:
    if _contains_term(_ascii_key(scene.voice_script), _EXPLICIT_NON_ADULT_TERMS):
        return "script_explicit_non_adult_human"
    key = _ascii_key(
        " ".join(
            part
            for part in (
                scene.title,
                scene.voice_script,
                scene.scene_meaning,
                scene.symbolic_visual,
                scene.main_character_or_object,
            )
            if part
        )
    )
    has_female = _contains_term(key, _FEMALE_ARCHETYPE_TERMS)
    has_male = _contains_term(key, _MALE_ARCHETYPE_TERMS)
    if has_female and not has_male:
        return "adult_woman"
    if has_male and not has_female:
        return "adult_man"
    if has_female or has_male or _contains_term(key, _HUMAN_ARCHETYPE_TERMS):
        return "adult_woman_or_man"
    return "symbolic_object"


def _contains_term(key: str, terms: tuple[str, ...]) -> bool:
    padded = f" {key} "
    return any(f" {term} " in padded for term in terms)


def _symbolic_qc_expectations(scene) -> tuple[list[str], list[str]]:
    key = _ascii_key(
        " ".join(
            part
            for part in (
                scene.voice_script,
                scene.scene_meaning,
                scene.emotional_metaphor,
                scene.symbolic_visual,
            )
            if part
        )
    )
    subjects: list[str]
    if _contains_any_phrase(
        key,
        (
            "lonely in a crowd",
            "alone in a crowd",
            "lonely among people",
            "co don giua dam dong",
            "mot minh giua dam dong",
        ),
    ):
        subjects = [
            "one clearly isolated adult figure",
            "one clearly visible small group or crowd",
        ]
    elif any(term in key for term in ("compar", "so sanh")):
        subjects = [
            "at least two adult human figures or one unmistakable comparison symbol",
        ]
    elif _contains_any_phrase(
        key,
        ("look okay while hurt", "okay while hurt inside", "fine while hurt inside"),
    ):
        subjects = [
            "one ordinary adult figure",
            "one readable inner-hurt symbol such as a cracked paper heart",
        ]
    elif _contains_any_phrase(
        key,
        ("sadness feels heavier at night", "heavier at night", "sadness at night"),
    ):
        subjects = [
            "one ordinary adult or concrete sadness symbol",
            "one clear night cue",
            "one readable weight or heaviness symbol",
        ]
    elif _contains_any_phrase(
        key,
        ("effort is unseen", "unseen effort", "co gang khong ai thay"),
    ):
        subjects = [
            "one readable effort or carrying symbol",
            "one ordinary adult or concrete effort object",
        ]
    else:
        subjects = [
            scene.symbolic_visual
            or scene.main_character_or_object
            or "one concrete readable symbolic subject"
        ]

    expectations = [
        "scene meaning is recognizable from the actual image",
        "required symbolic subjects are visibly present",
        "emotional metaphor is readable at a glance",
        f"visual identity matches {scene.visual_identity_id}",
        f"human age follows {scene.age_policy}",
        f"palette matches {scene.palette_id}",
        f"line style matches {scene.line_style_id}",
        f"subject scale follows {scene.subject_scale_profile}",
        "no forbidden archetype or photorealistic drift",
    ]
    return subjects, expectations


def _contains_any_phrase(key: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in key for phrase in phrases)


def _meaning_from_text(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return "one quiet emotional idea"
    return text[:260].rstrip(" ,.;:")


def _highlight_words(text: str, limit: int = 3) -> list[str]:
    highlights: list[str] = []
    text_key = f" {_ascii_key(text)} "
    for phrase in _PHRASE_HIGHLIGHTS:
        phrase_key = _ascii_key(phrase)
        if phrase_key and f" {phrase_key} " in text_key:
            highlights.append(phrase)
            if len(highlights) >= limit:
                return highlights

    words = re.findall(r"[\w\u00c0-\u1ef9]+", text or "", flags=re.UNICODE)
    scored: list[str] = []
    seen: set[str] = set()
    seen.update(_ascii_key(item) for item in highlights)
    for word in words:
        key = _ascii_key(word)
        if len(key) < 3 or key in _STOPWORDS or key in seen:
            continue
        seen.add(key)
        scored.append(word)
    return [*highlights, *scored[: max(0, limit - len(highlights))]]


def _explicit_setting_phrase(text: str) -> str:
    key = _ascii_key(text)
    for term, phrase in _SETTING_TERMS.items():
        if f" {_ascii_key(term)} " in f" {key} ":
            return phrase
    return ""


def _ascii_key(text: str) -> str:
    raw = (text or "").casefold().replace("\u0111", "d")
    decomposed = unicodedata.normalize("NFKD", raw)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", ascii_only).strip()


__all__ = ["enforce_symbolic_reel_plan"]
