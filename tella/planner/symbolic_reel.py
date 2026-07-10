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

_PREFLIGHT_REPAIRS = {
    "burden": (
        "one adult carrying a large cracked stone on their shoulders, clear "
        "adult proportions and a readable burden posture"
    ),
    "hidden_hurt": (
        "one adult showing a small calm smile while a dark cracked shape or heavy "
        "cloud is clearly visible behind their shoulders, no mask and no medical "
        "imagery"
    ),
    "comparison": (
        "two clearly drawn adult figures with unequal measuring marks, a balance "
        "scale, or another explicit comparison cue, no black silhouettes"
    ),
    "unseen_effort": (
        "one adult carrying a visible stack of heavy boxes or stones while at "
        "least two nearby adults walk past without noticing"
    ),
    "lonely_crowd": (
        "one isolated adult spatially separated from one clearly visible group of "
        "at least three adults"
    ),
    "nighttime_sadness": (
        "one adult sitting alone beneath a large dim moon with a heavy stone "
        "resting beside them, no ocean, ship, anchor poster, ghost, or creature"
    ),
    "silence": (
        "one adult inside a quiet circle with crossed-out or empty speech bubbles, "
        "no mouth or body-part close-up"
    ),
    "letting_go": (
        "one adult placing a stone down or opening both hands while a small bird "
        "flies away"
    ),
}

_PREFLIGHT_MAIN_SUBJECTS = {
    "burden": "adult carrying a large cracked stone on their shoulders",
    "hidden_hurt": "adult figure with calm smile and hidden burden",
    "comparison": "two adult figures and a visible comparison cue",
    "unseen_effort": "adult carrying visible weight while others pass",
    "lonely_crowd": "isolated adult and a group of at least three adults",
    "nighttime_sadness": "adult under a dim moon with a nearby stone",
    "silence": "adult in a quiet circle with empty speech bubbles",
    "letting_go": "adult putting down a stone or releasing a bird",
}


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
    body_scenes = [scene for scene in plan.scenes if scene.kind == "scene"]
    for idx, scene in enumerate(body_scenes, start=1):
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
        _reset_symbolic_image_qc(scene)

    preflight_symbolic_reel_plan(plan)

    for scene in body_scenes:
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


def preflight_symbolic_reel_plan(plan: TellaScenePlan) -> None:
    """Repair risky symbolic visuals before any image-provider request."""
    if plan.theme != "minimalist_symbolic_reel":
        return

    aggregate_reasons: list[str] = []
    original_visuals: dict[str, str] = {}
    repaired_any = False
    for scene in (item for item in plan.scenes if item.kind == "scene"):
        was_preflight_repaired = scene.symbolic_preflight_repaired
        original_visual = (
            scene.symbolic_preflight_original_visual or scene.symbolic_visual or ""
        ).strip()
        original_visuals[str(scene.scene_index)] = original_visual
        scene.symbolic_preflight_original_visual = original_visual
        scene.symbolic_visual = original_visual
        if was_preflight_repaired:
            scene.main_character_or_object = original_visual[:160]
        scene_type = _symbolic_scene_type(scene)
        reasons = _preflight_risk_reasons(original_visual)
        if scene_type == "nighttime_sadness" and not _visual_has_human(original_visual):
            reasons.append("unreadable_object_only_metaphor")
        if scene_type and _scene_visual_needs_repair(scene_type, original_visual):
            reasons.append(f"scene_type_requires_concrete_composition:{scene_type}")

        reasons = _unique_strings(reasons)
        if reasons:
            replacement = _PREFLIGHT_REPAIRS.get(scene_type) or _generic_preflight_repair(scene)
            scene.symbolic_visual = replacement
            scene.main_character_or_object = _PREFLIGHT_MAIN_SUBJECTS.get(
                scene_type,
                "ordinary adult and one concrete readable emotional symbol",
            )
            scene.cast_archetype = "adult_woman_or_man"
            scene.symbolic_preflight_status = "repaired"
            scene.symbolic_preflight_failure_reasons = reasons
            scene.symbolic_preflight_repaired = True
            aggregate_reasons.extend(
                f"scene_{scene.scene_index:02d}:{reason}" for reason in reasons
            )
            repaired_any = True
        else:
            if scene_type:
                scene.cast_archetype = "adult_woman_or_man"
            scene.symbolic_preflight_status = "passed"
            scene.symbolic_preflight_failure_reasons = []
            scene.symbolic_preflight_repaired = False

    plan.symbolic_preflight_status = "repaired" if repaired_any else "passed"
    plan.symbolic_preflight_failure_reasons = _unique_strings(aggregate_reasons)
    plan.symbolic_preflight_repaired = repaired_any
    plan.symbolic_preflight_original_visual = original_visuals


def _reset_symbolic_image_qc(scene) -> None:
    scene.symbolic_qc_passed = False
    scene.symbolic_qc_attempts = 0
    scene.symbolic_qc_failure_reasons = []
    scene.symbolic_qc_last_failure_reason = ""
    scene.symbolic_qc_repaired_prompt_used = False
    scene.symbolic_qc_final_status = "planned"
    scene.symbolic_meaning_matches = None
    scene.symbolic_visual_matches = None
    scene.metaphor_is_readable = None
    scene.visual_identity_matches = None
    scene.adult_age_policy_matches = None
    scene.style_matches_symbolic_reel = None
    scene.subject_scale_matches = None
    scene.forbidden_drift_detected = None
    scene.forbidden_drift_types = []
    scene.symbolic_qc_hard_fail_reasons = []
    scene.symbolic_qc_soft_fail_reasons = []
    scene.symbolic_soft_fail_streaks = {}


def _symbolic_scene_type(scene) -> str:
    primary_key = _ascii_key(
        " ".join(
            part
            for part in (scene.scene_meaning, scene.emotional_metaphor)
            if part
        )
    )
    scene_type = _classify_symbolic_key(primary_key, allow_silence=True)
    if scene_type:
        return scene_type

    visual_key = _ascii_key(scene.symbolic_visual)
    scene_type = _classify_symbolic_key(visual_key, allow_silence=True)
    if scene_type:
        return scene_type

    voice_key = _ascii_key(" ".join(part for part in (scene.title, scene.voice_script) if part))
    return _classify_symbolic_key(voice_key, allow_silence=False)


def _classify_symbolic_key(key: str, *, allow_silence: bool) -> str:
    if _contains_any_phrase(
        key,
        (
            "trying to appear okay while hurt",
            "trying to look okay while hurt",
            "the facade of being okay",
            "hiding exhaustion",
            "pretending to be okay",
            "trying to look okay",
            "appearing okay while hurt",
            "emotional facade",
            "look okay while hurt",
            "hurt inside",
            "co to ra on",
            "gia vo on",
            "to ra on nhung dau ben trong",
        ),
    ):
        return "hidden_hurt"
    if any(term in key for term in ("compar", "so sanh")):
        return "comparison"
    if _contains_any_phrase(
        key,
        (
            "unrecognized effort",
            "invisible growth",
            "effort is unseen",
            "unseen effort",
            "effort unnoticed",
            "effort not recognized",
            "no one sees the effort",
            "co gang khong duoc nhin thay",
            "no luc khong ai thay",
            "co gang khong ai thay",
        ),
    ):
        return "unseen_effort"
    if _contains_any_phrase(
        key,
        (
            "lonely in a crowd",
            "loneliness in a crowd",
            "alone in a crowd",
            "isolation despite being surrounded",
            "co don giua dam dong",
            "mot minh giua dam dong",
        ),
    ):
        return "lonely_crowd"
    if _contains_any_phrase(
        key,
        (
            "sadness feels heavier at night",
            "nighttime heaviness",
            "weight of night",
            "nighttime sadness",
            "night feels heavier",
            "sadness at night",
            "heavier at night",
            "dem xuong nang hon",
            "noi buon ve dem",
            "noi buon trong dem",
        ),
    ):
        return "nighttime_sadness"
    if _contains_any_phrase(
        key,
        ("letting go", "let go", "buong xuong"),
    ):
        return "letting_go"
    if _contains_any_phrase(
        key,
        (
            "emotional burden",
            "hidden burden",
            "burden of silence",
            "carrying a burden",
            "carrying hidden",
            "carry a heavy stone",
            "carrying a heavy stone",
            "heavy stone on shoulders",
            "ganh nang",
            "mang theo rat lau",
        ),
    ):
        return "burden"
    if allow_silence and _contains_any_phrase(
        key,
        ("silence", "silent", "im lang", "khong noi ra"),
    ):
        return "silence"
    return ""


def _scene_visual_needs_repair(scene_type: str, visual: str) -> bool:
    key = _ascii_key(visual)
    has_human = _visual_has_human(visual)
    if scene_type == "burden":
        return not (
            has_human
            and any(term in key for term in ("carry", "carrying", "shoulder"))
            and any(term in key for term in ("stone", "weight", "burden"))
        )
    if scene_type == "hidden_hurt":
        return not (
            has_human
            and "smile" in key
            and any(term in key for term in ("cloud", "crack"))
        )
    if scene_type == "comparison":
        return not (
            has_human
            and any(term in key for term in ("two", "2"))
            and any(
                term in key
                for term in ("measuring", "scale", "comparison", "unequal")
            )
        )
    if scene_type == "unseen_effort":
        return not (
            has_human
            and any(term in key for term in ("carry", "carrying"))
            and any(term in key for term in ("boxes", "stones", "weight", "burden"))
            and any(term in key for term in ("nearby", "pass", "not noticing"))
        )
    if scene_type == "lonely_crowd":
        return not (
            has_human
            and any(term in key for term in ("group", "crowd", "adults"))
            and any(term in key for term in ("three", "3"))
            and any(term in key for term in ("separated", "apart", "isolated"))
        )
    if scene_type == "nighttime_sadness":
        return not (
            has_human
            and "sitting" in key
            and "moon" in key
            and any(term in key for term in ("stone", "weight"))
        )
    if scene_type == "silence":
        return not (
            has_human
            and "circle" in key
            and any(term in key for term in ("speech bubble", "speech bubbles"))
        )
    if scene_type == "letting_go":
        return not (
            has_human
            and any(
                term in key
                for term in ("placing a stone", "opening", "open hands", "releasing")
            )
            and any(term in key for term in ("bird", "stone"))
        )
    return True


def _visual_has_human(visual: str) -> bool:
    key = _ascii_key(visual)
    return any(
        term in f" {key} "
        for term in (
            " adult ",
            " person ",
            " figure ",
            " woman ",
            " man ",
        )
    )


def _preflight_risk_reasons(visual: str) -> list[str]:
    key = _ascii_key(visual)
    risk_key = key
    for allowed_negative in (
        "no medical mask",
        "no mask",
        "no black silhouettes",
        "no silhouette",
        "no blob",
        "no ghost",
        "no monster",
        "no mouth or body part close up",
        "not only a plant in shadow",
        "no object only poster composition",
    ):
        risk_key = risk_key.replace(allowed_negative, "")
    reasons: list[str] = []
    if "medical mask" in risk_key:
        reasons.append("medical_mask_visual")
    elif _contains_term(risk_key, ("mask",)):
        reasons.append("ambiguous_mask_visual")
    if "silhouette" in risk_key:
        reasons.append("silhouette_visual")
    if _contains_term(risk_key, ("blob",)):
        reasons.append("blob_visual")
    if _contains_term(risk_key, ("ghost",)):
        reasons.append("ghost_visual")
    if _contains_term(risk_key, ("monster",)):
        reasons.append("monster_visual")
    if "closed mouth" in risk_key:
        reasons.append("closed_mouth_close_up")
    if "close up" in risk_key and any(
        part in risk_key
        for part in ("mouth", "eye", "hand", "arm", "leg", "chest", "body part")
    ):
        reasons.append("body_part_close_up")
    if any(term in risk_key for term in ("abstract shadow", "vague shadow")):
        reasons.append("vague_abstract_shadow")
    if any(
        term in risk_key
        for term in (
            "object only",
            "abstract object",
            "abstract shape",
            "unreadable object",
            "vague object",
            "plant in shadow",
            "many dots",
            "abstract dots",
            "heavy moon",
            "moon and anchor",
        )
    ):
        reasons.append("unreadable_object_only_metaphor")
    return reasons


def _generic_preflight_repair(scene) -> str:
    meaning = (scene.scene_meaning or "one clear emotional idea").strip()
    return (
        "one ordinary adult interacting with one concrete paper heart or stone "
        f"through a clear action representing {meaning}, no mask, silhouette, "
        "blob, ghost, monster, or body-part close-up"
    )[:300]


def _unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


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
            "loneliness in a crowd",
            "alone in a crowd",
            "lonely among people",
            "isolation despite being surrounded",
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


__all__ = ["enforce_symbolic_reel_plan", "preflight_symbolic_reel_plan"]
