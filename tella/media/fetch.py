"""Top-level media dispatcher — fetch every scene's asset for a plan.

Given a :class:`TellaScenePlan`, ``fetch_assets`` walks every body scene
and writes one asset file per scene into ``<job_dir>/assets/`` based on
the plan's ``media_source``:

  - ``ai_image``    → CF Workers AI FLUX → JPG
  - ``stock_photo`` → Pexels Photo       → JPG
  - ``stock_video`` → Pexels Video       → MP4

For v1 MVP each scene gets exactly 1 asset. Multi-asset per scene
(``Scene.asset_count`` > 1) is deferred to a later iteration — the field
is preserved on the plan for downstream consumers but the media layer
ignores it for now (see DECISIONS.md D-007).

Scenes are fetched concurrently up to ``MAX_CONCURRENT`` to keep render
turnaround tight. Failures bubble per scene — the dispatcher does NOT
swap providers (e.g. stock photo when stock video fails) because cross-
provider fallback would silently change what the user asked for.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import unicodedata
from pathlib import Path

from tella.media import ai_image, sprite_composer, stock_photo, stock_video
from tella.media.image_provider import get_image_provider
from tella.media.reference_pipeline import (
    generate_character_references,
    selected_reference_paths,
)
from tella.media.visual_qc import (
    apply_qc_result_to_scene,
    evaluate_scene_image,
    image_hash,
    infer_scene_anatomy_expectations,
    max_attempts,
    rank_qc_attempt,
    save_qc_result,
    strict_visual_qc,
    summarize_qc_attempts,
)
from tella.planner.models import SceneQCResult, StyleBible, TellaScenePlan, VisualBible
from tella.planner.visual_bible import build_visual_bible, save_visual_bible
from tella.planner.visual_prompts import build_scene_visual_plan, repair_prompt

logger = logging.getLogger("tella.media.fetch")

# Keep concurrency modest: bursting many simultaneous requests at one CF
# account triggers rate-limit 429s. 3 in flight + the global throttle in
# ai_image keeps us under the limit while still rendering quickly.
MAX_CONCURRENT = 3

# One stable seed per video keeps the AI-generated character looking the
# same across scenes (FLUX has no cross-call memory; a fixed seed + the
# locked identity text is the best text-only consistency lever we have).
_VIDEO_SEED = 73501


class _SymbolicQCFailure(RuntimeError):
    pass


class AIImageRequestBudgetError(RuntimeError):
    """Raised before network submission when the run request budget is spent."""

    error_type = "image_request_budget_exhausted"
    recoverable = False

    def __init__(self, *, used: int, maximum: int) -> None:
        super().__init__(
            "AI image request budget exhausted before Cloudflare submission: "
            f"used={used}, max={maximum}. No additional HTTP request was started."
        )
        self.used = used
        self.maximum = maximum


def _provider_prompt_hash(prompt: str) -> str:
    return hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()[:24]


class _CloudflareRequestBudget:
    def __init__(self, plan: TellaScenePlan, scenes: list, maximum: int | None) -> None:
        self.plan = plan
        self.scenes = scenes
        self.maximum = maximum
        self.used = 0
        self._lock = asyncio.Lock()
        budget_max = maximum or 0
        plan.image_request_budget_max = budget_max
        for scene in scenes:
            scene.image_request_budget_max = budget_max

    async def acquire(self, scene, prompt: str, stage: str) -> int:
        async with self._lock:
            if self.maximum is not None and self.used >= self.maximum:
                raise AIImageRequestBudgetError(
                    used=self.used,
                    maximum=self.maximum,
                )
            self.used += 1
            request_number = self.used
            self.plan.ai_images_requested += 1
            scene.ai_images_requested += 1
            scene.actual_cloudflare_request_count_for_scene += 1
            scene.provider_request_count_for_scene += 1
            scene.provider_prompt_stage_used = stage
            if stage == "policy_retry":
                scene.content_policy_retry_used = True
                scene.content_policy_attempt_count = max(
                    scene.content_policy_attempt_count,
                    2,
                )
            elif scene.content_policy_attempt_count == 0:
                scene.content_policy_attempt_count = 1
            denominator = str(self.maximum) if self.maximum is not None else "unlimited"
            logger.info(
                "cloudflare image request %d/%s scene=%02d stage=%s "
                "provider_prompt=%s provider_prompt_hash=%s",
                request_number,
                denominator,
                scene.scene_index,
                stage,
                json.dumps(prompt, ensure_ascii=False),
                _provider_prompt_hash(prompt),
            )
            return request_number

    def sync_finish_metadata(self) -> None:
        self.plan.image_request_budget_used_at_finish = self.used
        for scene in self.scenes:
            scene.image_request_budget_used_at_finish = self.used

# Generation dims fed to the AI image provider. Smaller than the 1080×1920 /
# 1920×1080 final canvas on purpose — the renderer upscales/crops, and a
# smaller image costs fewer CF Neurons so a free account lasts far longer.
_GEN_DIMS: dict[str, tuple[int, int]] = {
    "9:16": (768, 1344),
    "16:9": (1344, 768),
}

_NSFW_MARKERS = (
    "3030",
    "input prompt contains nsfw content",
    "nsfw",
)

_MINIMALIST_SAFE_PROMPT = (
    "same young woman character, one head and one face, short straight black "
    "bob ending at chin, small rounded face, gentle expressive eyes, tiny nose, "
    "soft melancholic mouth, cozy mustard yellow simple dress with natural fabric "
    "shape and soft rust sleeves, cute hand-drawn cartoon proportions, complete "
    "character visible, not a stick figure, not a triangle placeholder body"
)

_MINIMALIST_TWO_CHARACTER_SAFE_PROMPT = (
    "wide emotional scene shot with exactly two adult characters visible, young Vietnamese "
    "woman main character with short straight black bob hair and mustard yellow "
    "simple dress, young Vietnamese man secondary character with short dark hair "
    "and muted brown shirt, man clearly visible full body near doorway or window, "
    "large empty space between them, complete figures visible, not stick figures, "
    "not placeholder bodies"
)

_MINIMALIST_STYLE_LOCK = (
    "minimalist emotional hand-drawn cartoon illustration, soft clean lines, "
    "warm muted earthy palette, expressive cute but "
    "melancholic character, gentle flat color with subtle texture, soft "
    "environmental details matching the story setting, no stick figure, no primitive placeholder "
    "geometry, no realistic shading, no 3D, no anime, no photorealism, no text, "
    "no watermark"
)

_MINIMALIST_COMPOSITION_LOCK = (
    "vertical 9:16 medium-wide scene composition, character placed within central "
    "safe area, character occupies about 35-45 percent of frame height, generous "
    "negative space, complete character visible, head and feet visible, bottom "
    "25 percent calm for captions, no cropped head or feet, no extreme close-up, "
    "not a character portrait, layered scene: soft foreground edge or shadow, "
    "middle ground young woman, background environmental details from the story, "
    "soft shadows, subtle dust or memory particles, muted floor and wall shapes"
)

_MINIMALIST_ONE_CHARACTER_LOCK = (
    "exactly one head, no second head, no duplicate face, no second person, "
    "no doll-like duplicate figure, no face on objects, no face on heart, no "
    "tiny duplicate character, no nested person, symbolic objects must be "
    "plain and faceless but softly illustrated, no primitive circle placeholder "
    "object, no malformed anatomy, no extra limbs"
)

_MINIMALIST_TWO_CHARACTER_LOCK = (
    "exactly two people only, one young Vietnamese woman and one young "
    "Vietnamese man, both characters clearly visible, emotional distance, man "
    "turns away or stands apart, no romantic hugging, no wedding, no extra "
    "people, no duplicate face, no face on objects, no malformed anatomy, no "
    "extra limbs"
)

_MINIMALIST_POSES: dict[str, str] = {
    "front_standing": (
        "young woman standing front-facing, arms relaxed by sides, simple "
        "mitten hands, beside a tiny flat paper heart symbol with no face"
    ),
    "side_sitting": (
        "young woman sitting in side view, hands resting on knees, calm posture"
    ),
    "side_walking": (
        "young woman walking slowly in side profile, arms relaxed, beside a "
        "thin line path"
    ),
    "looking_at_light": (
        "young woman looking at a small glowing light floating nearby"
    ),
    "holding_paper_heart": (
        "young woman holding a tiny flat paper heart symbol with no face in "
        "simple mitten-like hands in front of the dress, paper heart has no "
        "eyes, no mouth, no face"
    ),
    "beside_lamp": "young woman sitting beside a small warm lamp",
    "beside_flower": "young woman standing beside a small flower",
    "under_scribble_cloud": (
        "young woman standing under a soft grey scribble cloud"
    ),
}

_MINIMALIST_MOTIFS = (
    "lamp",
    "paper_heart",
    "scribble_cloud",
    "small_flower",
    "glowing_light",
    "empty_chair",
    "thin_path",
    "sunrise_circle",
    "tiny_bird",
    "small_window",
    "little_star",
    "seedling",
)

_MOTIF_TO_POSE = {
    "lamp": "beside_lamp",
    "paper_heart": "holding_paper_heart",
    "scribble_cloud": "under_scribble_cloud",
    "small_flower": "beside_flower",
    "glowing_light": "looking_at_light",
    "empty_chair": "side_sitting",
    "thin_path": "side_walking",
    "sunrise_circle": "looking_at_light",
    "tiny_bird": "front_standing",
    "small_window": "side_sitting",
    "little_star": "looking_at_light",
    "seedling": "beside_flower",
}

_MOTIF_DESCRIPTIONS = {
    "lamp": "one small warm lamp or shop glow in the background",
    "paper_heart": (
        "one tiny flat mustard paper heart symbol with no face, no eyes, no "
        "mouth"
    ),
    "scribble_cloud": "one soft grey scribble cloud above the character",
    "small_flower": "one small flower growing from the ground",
    "glowing_light": "one small glowing light floating nearby",
    "empty_chair": "one simple empty chair shape beside the scene",
    "thin_path": "one thin line path under the character",
    "sunrise_circle": "one small muted sunrise circle near the horizon",
    "tiny_bird": "one tiny simple bird shape in the empty space",
    "small_window": "one simple window shape with soft light and dust particles",
    "little_star": "one little muted star above the character",
    "seedling": "one tiny seedling near the character's feet",
}

_COMPOSITIONS = (
    "medium-wide scene, character centered in middle safe area, background details matching the story, bottom 25 percent calm",
    "medium-wide scene, character slightly left of center, soft environmental shapes on right, muted shadows behind",
    "medium-wide scene, character centered above caption lane, soft dust particles in warm light",
    "medium-wide scene, character slightly right of center, background prop shapes on left, head and feet fully visible",
    "medium-wide scene, character centered, one small story prop nearby, generous negative space around her",
    "medium-wide scene, character in middle ground, foreground soft shadow, muted floor and wall or street shapes",
    "medium-wide scene, character side profile in central safe area, simple path or floor shape",
    "medium-wide scene, character small in middle third, soft background glow and quiet open space",
)

_MINIMALIST_FORBIDDEN_HINTS = (
    "hugging herself",
    "embracing herself",
    "holding herself",
    "touching herself",
    "touching her body",
    "touching her chest",
    "hands on body",
    "arms wrapped around herself",
    "wounded body",
    "broken body",
    "physical pain on body",
    "close-up face",
    "looking directly into camera",
    "back view with visible face",
    "twisted torso",
    "detailed hands",
    "detailed fingers",
    "realistic face",
    "anime face",
    "long flowing hair",
    "asymmetrical hair",
    "self-hug",
    "touching body",
)

_MOTIF_KEYWORDS = {
    "lamp": ("lamp", "room", "bed", "tired", "rest", "evening", "quiet"),
    "paper_heart": ("heart", "love", "accept", "heal", "gentle", "kind"),
    "scribble_cloud": ("sad", "heavy", "cloud", "stress", "pain", "hurt", "worry"),
    "small_flower": ("flower", "plant", "grow", "soft", "care"),
    "glowing_light": ("light", "glow", "hope", "warm", "peace"),
    "empty_chair": ("alone", "empty", "chair", "tired", "sit"),
    "thin_path": ("walk", "path", "step", "journey", "again", "start"),
    "sunrise_circle": ("morning", "sunrise", "new", "begin", "tomorrow"),
    "tiny_bird": ("free", "release", "breath", "quiet"),
    "small_window": ("window", "rain", "night", "look", "outside"),
    "little_star": ("wish", "remember", "small", "dream"),
    "seedling": ("seed", "grow", "begin", "return", "life"),
}

_SETTING_PROMPTS = {
    "street_sidewalk": (
        "outdoor sidewalk on a quiet city street, muted storefront shapes, "
        "soft evening light, simple street edge"
    ),
    "bakery_exterior": (
        "outdoor city sidewalk with a small warm bakery storefront visible, "
        "glass door, pastry display glow, no readable text"
    ),
    "bakery_entrance": (
        "bakery doorway and warm storefront entrance, interior glow visible "
        "through a glass door, no readable text"
    ),
    "bakery_interior": (
        "inside a small warm bakery, simple shelves, pastry shapes, cozy "
        "counter area, soft golden light"
    ),
    "bakery_counter": (
        "inside a bakery at a glass display counter with cakes and pastries, "
        "simple counter shapes, warm light"
    ),
    "exit_street": (
        "outside bakery on the sidewalk, bakery door behind her, quiet street, "
        "warm shop glow"
    ),
    "bedroom": (
        "quiet warm bedroom scene with bed on one side, window with thin "
        "curtains, small bedside table, warm table lamp, books or folded "
        "blanket, soft wall shadows, subtle dust near the window"
    ),
    "generic_emotional": (
        "quiet everyday setting matching the narration, soft environmental "
        "details, simple background shapes, muted floor and wall or street "
        "forms, generous negative space"
    ),
}

_ACTION_PROMPTS = {
    "walking_outside": "young woman walking slowly along the sidewalk outside",
    "noticing_bakery": (
        "young woman pauses and looks toward the small bakery storefront"
    ),
    "entering_shop": "young woman stepping through the bakery door",
    "choosing_cake": (
        "young woman choosing one small cake at the glass display counter"
    ),
    "holding_cake": (
        "young woman holding one small cake box or small paper bakery bag"
    ),
    "leaving_shop": (
        "young woman walking out of the bakery with the cake box or paper bag"
    ),
    "quiet_reflection": "young woman standing quietly in a small reflective moment",
    "standing_apart": (
        "young woman and young man standing apart with emotional distance"
    ),
}

_CLOUDFLARE_SAFE_ACTION_PROMPTS = {
    "walking_outside": (
        "A fully clothed adult woman in her 20s walking on a quiet sidewalk "
        "outside, modest mustard dress with rust sleeves, wholesome everyday "
        "hand-drawn cartoon illustration, warm muted colors, medium-wide shot, "
        "complete person visible from head to shoes, no text, no watermark."
    ),
    "noticing_bakery": (
        "A fully clothed adult woman in her 20s standing on a sidewalk and "
        "looking at a small bakery storefront with warm lights, wholesome "
        "everyday scene, hand-drawn cartoon illustration, warm muted colors, "
        "medium-wide shot, complete person visible from head to shoes, no text, "
        "no watermark."
    ),
    "entering_shop": (
        "A fully clothed adult woman in her 20s entering a bakery doorway, warm "
        "shop interior visible, family-safe everyday scene, hand-drawn cartoon "
        "illustration, warm muted colors, medium-wide shot, complete person "
        "visible from head to shoes, no text, no watermark."
    ),
    "choosing_cake": (
        "A fully clothed adult woman in her 20s choosing one pastry at a bakery "
        "display counter, cakes and pastries behind glass, wholesome everyday "
        "scene, hand-drawn cartoon illustration, warm muted colors, medium-wide "
        "shot, complete person visible from head to shoes, no text, no watermark."
    ),
    "holding_cake": (
        "A fully clothed adult woman in her 20s holding a small paper bakery bag "
        "or pastry box, calm expression, wholesome everyday scene, hand-drawn "
        "cartoon illustration, warm muted colors, medium-wide shot, complete "
        "person visible from head to shoes, no text, no watermark."
    ),
    "leaving_shop": (
        "A fully clothed adult woman in her 20s walking out of the bakery onto "
        "the sidewalk, holding a paper bakery bag, warm muted colors, wholesome "
        "everyday scene, hand-drawn cartoon illustration, medium-wide shot, "
        "complete person visible from head to shoes, no text, no watermark."
    ),
    "standing_apart": (
        "A fully clothed adult woman in her 20s and a fully clothed adult man "
        "standing apart in a quiet everyday setting, emotional distance, "
        "non-romantic family-safe scene, hand-drawn cartoon illustration, warm "
        "muted colors, medium-wide shot, complete people visible from head to "
        "shoes, no text, no watermark."
    ),
    "quiet_reflection": (
        "A fully clothed adult woman in her 20s standing quietly in a wholesome "
        "everyday scene, modest mustard dress with rust sleeves, hand-drawn "
        "cartoon illustration, warm muted colors, medium-wide shot, complete "
        "person visible from head to shoes, no text, no watermark."
    ),
}

_CLOUDFLARE_SAFE_SETTING_TAILS = {
    "street_sidewalk": "Quiet sidewalk outside, simple street shapes.",
    "bakery_exterior": "Small bakery storefront with warm lights.",
    "bakery_entrance": "Bakery doorway with warm shop interior visible.",
    "bakery_interior": "Warm bakery interior with shelves and a simple counter.",
    "bakery_counter": "Bakery display counter with cakes and pastries behind glass.",
    "exit_street": "Bakery door behind her, sidewalk outside.",
    "bedroom": "Quiet bedroom setting, fully clothed and family-safe.",
    "generic_emotional": "Quiet everyday setting with soft background shapes.",
}

_CLOUDFLARE_SAFE_RISKY_PATTERNS = (
    r"\byoung\b",
    r"\bcute\b",
    r"\btiny\b",
    r"\bsmall face\b",
    r"\bmouth\b",
    r"\bbody\b",
    r"\bfull body\b",
    r"\bchildlike\b",
    r"\bintimate\b",
    r"\bsensual\b",
    r"\bly(?:ing)? down\b",
    r"\bclose-up body details\b",
)

_CLOUDFLARE_SAFE_SYMBOLIC_RISKY_PATTERNS = (
    r"\badult\b",
    r"\bchild(?:ren)?\b",
    r"\bmedical\b",
    r"\bmask\b",
    r"\bfully clothed\b",
    r"\bnude\b",
    r"\bnsfw\b",
    r"\bghost\b",
    r"\bmonster\b",
    r"\bblob\b",
    r"\bcreature\b",
    r"\bsilhouettes?\b",
    r"\bcracked\b",
    r"\bshoulders?\b",
    r"\bmouth\b",
    r"\bbody[- ]part\b",
    r"\bclose[- ]up\b",
)

_SETTING_MATCH_TERMS = {
    "street_sidewalk": ("street", "sidewalk", "outdoor"),
    "bakery_exterior": ("bakery", "storefront", "sidewalk"),
    "bakery_entrance": ("bakery", "door", "entrance"),
    "bakery_interior": ("inside", "bakery", "shelves", "counter"),
    "bakery_counter": ("bakery", "display counter", "cake", "pastries"),
    "exit_street": ("outside bakery", "sidewalk", "street"),
    "bedroom": ("bedroom", "bed", "bedside", "curtains"),
    "generic_emotional": ("setting", "background", "environmental"),
}

_ACTION_MATCH_TERMS = {
    "walking_outside": ("walking", "sidewalk", "outside"),
    "noticing_bakery": ("looks", "bakery", "storefront"),
    "entering_shop": ("stepping", "bakery door", "door"),
    "choosing_cake": ("choosing", "cake", "display counter"),
    "holding_cake": ("holding", "cake box", "paper bakery bag"),
    "leaving_shop": ("walking out", "bakery", "paper bag", "cake box"),
    "quiet_reflection": ("quietly", "reflective"),
    "standing_apart": ("standing apart", "emotional distance"),
}

_BAKERY_SEQUENCE_BY_ORDER = {
    1: ("street_sidewalk", "walking_outside"),
    2: ("bakery_exterior", "noticing_bakery"),
    3: ("bakery_entrance", "entering_shop"),
    4: ("bakery_counter", "choosing_cake"),
    5: ("bakery_interior", "holding_cake"),
    6: ("exit_street", "leaving_shop"),
}

_BAKERY_TERMS = (
    "bakery",
    "cake",
    "pastry",
    "pastries",
    "tiem banh",
    "cua hang banh",
    "banh ngot",
    "hop banh",
    "tui giay",
)

_BEDROOM_EXPLICIT_TERMS = (
    "bedroom",
    "room",
    "bed ",
    "bedside",
    "blanket",
    "phong ngu",
    "can phong",
    "giuong",
    "den ngu",
)


def _safe_stem(text: str, max_len: int = 30) -> str:
    """Filesystem-safe slug for asset filenames."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", (text or "scene")).strip("_").lower()
    return (slug or "scene")[:max_len]


def _is_nsfw_prompt_rejection(exc: Exception) -> bool:
    if getattr(exc, "error_type", "") == "content_policy_blocked":
        return True
    msg = str(exc).lower()
    return any(marker in msg for marker in _NSFW_MARKERS)


def _is_cloudflare_code_3030(exc: Exception) -> bool:
    return (
        getattr(exc, "policy_code", 0) == 3030
        and getattr(exc, "error_type", "") == "content_policy_blocked"
    )


def _stock_fallback_disabled() -> bool:
    return (os.environ.get("TELLA_DISABLE_STOCK_FALLBACK") or "").strip() == "1"


def _ai_image_stock_fallback_forbidden(plan: TellaScenePlan) -> bool:
    return (
        plan.media_source == "ai_image"
        and plan.theme in {"minimalist_emotional", "minimalist_symbolic_reel"}
    )


def _minimalist_use_ai_scenes() -> bool:
    return (os.environ.get("TELLA_MINIMALIST_USE_AI_SCENES") or "").strip() == "1"


def _env_bool(name: str) -> bool:
    return (os.environ.get(name) or "").strip() == "1"


def _env_int_optional(name: str) -> int | None:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        logger.warning("invalid %s=%r; ignoring", name, raw)
        return None
    return max(0, value)


def _local_image_fallback_allowed() -> bool:
    return _env_bool("TELLA_ALLOW_LOCAL_IMAGE_FALLBACK")


def _reuse_assets_enabled() -> bool:
    return _env_bool("TELLA_REUSE_ASSETS") or bool((os.environ.get("TELLA_IMAGES_FROM_JOB") or "").strip())


def _reuse_assets_mode() -> str:
    raw = (os.environ.get("TELLA_REUSE_ASSETS_MODE") or "").strip().lower()
    if _env_bool("TELLA_ALLOW_MISMATCHED_REUSED_ASSETS"):
        return "loose_debug"
    if raw in {"loose", "loose_debug"}:
        return "loose_debug"
    return "strict"


def _skip_image_generation() -> bool:
    return _env_bool("TELLA_SKIP_IMAGE_GENERATION")


def _assert_provider_submission_allowed() -> None:
    if _skip_image_generation():
        raise RuntimeError(
            "Internal invariant violated: provider generation attempted while "
            "--skip-image-generation is active"
        )


def _asset_prompt_hash(prompt: str, *, width: int, height: int, seed: int | None) -> str:
    payload = json.dumps(
        {
            "prompt": prompt,
            "width": int(width),
            "height": int(height),
            "seed": seed,
            "provider": "cloudflare",
            "model": ai_image.DEFAULT_MODEL,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _record_ai_provider_error(scene, exc: Exception) -> str:
    error_type = getattr(exc, "error_type", "") or "provider_failed"
    message = str(exc)
    if error_type == "quota_exhausted":
        scene.asset_status = "ai_provider_quota_exhausted"
    else:
        scene.asset_status = "ai_provider_failed"
    scene.ai_provider_error_type = error_type
    scene.ai_provider_error_message = message[:500]
    if error_type == "content_policy_blocked":
        scene.content_policy_blocked_count += 1
    default_recoverable = error_type not in {
        "quota_exhausted",
        "rate_limited",
        "auth_error",
        "payment_required",
    }
    scene.ai_provider_recoverable = bool(
        getattr(exc, "recoverable", default_recoverable)
    )
    scene.asset_error = message[:300]
    return error_type


def _source_job_dir(job_dir: Path) -> Path | None:
    explicit_plan = (os.environ.get("TELLA_REUSE_PLAN_PATH") or "").strip()
    if explicit_plan:
        p = Path(explicit_plan)
        if p.is_file():
            return p.parent
    raw = (os.environ.get("TELLA_IMAGES_FROM_JOB") or "").strip()
    if not raw:
        return job_dir
    p = Path(raw)
    if not p.is_absolute():
        p = job_dir.parent / raw
    return p


def _source_job_id(job_dir: Path) -> str:
    source_dir = _source_job_dir(job_dir)
    return source_dir.name if source_dir is not None else ""


def _load_reuse_index(job_dir: Path) -> dict[tuple[int, str], dict]:
    if not _reuse_assets_enabled():
        return {}
    source_dir = _source_job_dir(job_dir)
    if source_dir is None:
        return {}
    plan_path = Path(os.environ.get("TELLA_REUSE_PLAN_PATH") or "") if os.environ.get("TELLA_REUSE_PLAN_PATH") else source_dir / "plan.json"
    if not plan_path.is_file():
        logger.warning("reuse-assets requested but plan not found: %s", plan_path)
        return {}
    try:
        data = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("reuse-assets could not read %s: %s", plan_path, exc)
        return {}
    index: dict[tuple[int, str], dict] = {}
    for item in data.get("scenes", []):
        if not isinstance(item, dict) or item.get("kind") != "scene":
            continue
        prompt_hash = str(item.get("asset_prompt_hash") or "")
        asset_path = str(item.get("asset_path") or (item.get("image_filenames") or [""])[0])
        if not prompt_hash or not asset_path:
            continue
        if item.get("used_local_fallback"):
            continue
        if item.get("image_source") not in {
            "ai_image_provider",
            "reference_guided_ai_image",
            "reused_asset",
        }:
            continue
        src = source_dir / asset_path
        if not src.is_file():
            continue
        index[(int(item.get("scene_index") or 0), prompt_hash)] = {
            "source_path": src,
            "source_asset_path": asset_path,
            "source_prompt_hash": prompt_hash,
            "source_job_id": source_dir.name,
            "source_scene_index": int(item.get("scene_index") or 0),
            "image_source": item.get("image_source") or "ai_image_provider",
            "image_provider": item.get("image_provider") or "cloudflare",
            "asset_status": item.get("asset_status") or "done",
        }
    logger.info("reuse-assets index loaded: %d reusable scene assets", len(index))
    return index


def _load_loose_reuse_index(job_dir: Path) -> dict[int, dict]:
    if not _reuse_assets_enabled():
        return {}
    source_dir = _source_job_dir(job_dir)
    if source_dir is None:
        return {}
    plan_path = Path(os.environ.get("TELLA_REUSE_PLAN_PATH") or "") if os.environ.get("TELLA_REUSE_PLAN_PATH") else source_dir / "plan.json"
    if not plan_path.is_file():
        logger.warning("loose reuse requested but plan not found: %s", plan_path)
        return {}
    try:
        data = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("loose reuse could not read %s: %s", plan_path, exc)
        return {}
    index: dict[int, dict] = {}
    for item in data.get("scenes", []):
        if not isinstance(item, dict) or item.get("kind") != "scene":
            continue
        asset_path = str(item.get("asset_path") or (item.get("image_filenames") or [""])[0])
        if not asset_path:
            continue
        if item.get("used_local_fallback"):
            continue
        if item.get("image_source") not in {
            "ai_image_provider",
            "reference_guided_ai_image",
            "reused_asset",
        }:
            continue
        src = source_dir / asset_path
        if not src.is_file():
            continue
        scene_index = int(item.get("scene_index") or 0)
        if scene_index <= 0:
            continue
        index[scene_index] = {
            "source_path": src,
            "source_asset_path": asset_path,
            "source_prompt_hash": str(item.get("asset_prompt_hash") or ""),
            "source_job_id": source_dir.name,
            "source_scene_index": scene_index,
            "image_source": item.get("image_source") or "ai_image_provider",
            "image_provider": item.get("image_provider") or "cloudflare",
            "asset_status": item.get("asset_status") or "done",
        }
    logger.info("loose reuse index loaded: %d reusable scene assets", len(index))
    return index


def _reuse_rejection_details(
    job_dir: Path,
    *,
    scene_index: int,
    prompt_hash: str,
    reuse_mode: str,
) -> tuple[bool, str]:
    source_dir = _source_job_dir(job_dir)
    if source_dir is None:
        return False, "no source job was configured"
    explicit_plan = (os.environ.get("TELLA_REUSE_PLAN_PATH") or "").strip()
    plan_path = Path(explicit_plan) if explicit_plan else source_dir / "plan.json"
    if not plan_path.is_file():
        return False, f"source plan is missing: {plan_path}"
    try:
        data = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"source plan could not be read: {exc}"

    candidates = [
        item
        for item in data.get("scenes", [])
        if isinstance(item, dict)
        and item.get("kind") == "scene"
        and int(item.get("scene_index") or 0) == scene_index
    ]
    if not candidates:
        return False, "source plan has no candidate for this scene index"

    reasons: list[str] = []
    for item in candidates:
        asset_path = str(
            item.get("asset_path") or (item.get("image_filenames") or [""])[0]
        )
        if not asset_path:
            reasons.append("candidate has no asset path")
            continue
        if not (source_dir / asset_path).is_file():
            reasons.append(f"candidate asset file is missing: {asset_path}")
            continue
        if item.get("used_local_fallback"):
            reasons.append("candidate used local fallback")
            continue
        source_hash = str(item.get("asset_prompt_hash") or "")
        if reuse_mode == "strict" and source_hash != prompt_hash:
            reasons.append("candidate prompt hash does not match")
            continue
        reasons.append("candidate metadata was not eligible for reuse")
    return True, "; ".join(dict.fromkeys(reasons))


def _skip_image_generation_reuse_error(
    job_dir: Path,
    scene,
    *,
    prompt_hash: str,
    reuse_mode: str,
    index_had_candidates: bool,
) -> RuntimeError:
    source_dir = _source_job_dir(job_dir)
    source_label = str(source_dir) if source_dir is not None else "<none>"
    had_plan_candidates, rejection_reason = _reuse_rejection_details(
        job_dir,
        scene_index=scene.scene_index,
        prompt_hash=prompt_hash,
        reuse_mode=reuse_mode,
    )
    return RuntimeError(
        "Image generation is disabled by --skip-image-generation, but no "
        f"reusable asset could be resolved for scene {scene.scene_index:02d} "
        f"from job {source_label}. No provider request was made. "
        f"reuse_mode={reuse_mode}; index_had_candidates="
        f"{str(index_had_candidates).lower()}; source_plan_had_candidates="
        f"{str(had_plan_candidates).lower()}; rejection_reason="
        f"{rejection_reason or 'unknown'}"
    )


def _minimalist_visual_mode() -> str:
    raw = (os.environ.get("TELLA_MINIMALIST_VISUAL_MODE") or "").strip().lower()
    if raw:
        if raw not in {"reference", "ai_scene", "curated_sprite", "rig"}:
            logger.warning("invalid TELLA_MINIMALIST_VISUAL_MODE=%r; using ai_scene", raw)
            return "ai_scene"
        return raw
    return "ai_scene"


def _require_reference_conditioning() -> bool:
    return (os.environ.get("TELLA_REQUIRE_REFERENCE_CONDITIONING") or "").strip() == "1"


def _use_previous_scene_reference() -> bool:
    return (os.environ.get("TELLA_USE_PREVIOUS_SCENE_REFERENCE") or "").strip() == "1"


def _ascii_key(text: str) -> str:
    raw = (text or "").casefold().replace("\u0111", "d")
    decomposed = unicodedata.normalize("NFKD", raw)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", ascii_only).strip()


def _story_text_for_scene(scene) -> str:
    return " ".join(str(part or "") for part in (scene.title, scene.voice_script))


def _all_story_text(scenes) -> str:
    return " ".join(_story_text_for_scene(scene) for scene in scenes)


def _has_phrase(key: str, phrases: tuple[str, ...]) -> bool:
    padded = f" {key} "
    for phrase in phrases:
        needle = f" {_ascii_key(phrase)} "
        if needle.strip() and needle in padded:
            return True
    return False


def _has_any_token(key: str, terms: tuple[str, ...]) -> bool:
    padded = f" {key} "
    return any(f" {_ascii_key(term)} " in padded for term in terms if term)


def _is_bakery_sequence(scenes) -> bool:
    story_key = _ascii_key(_all_story_text(scenes))
    prompt_key = _ascii_key(" ".join(str(getattr(s, "image_prompt", "") or "") for s in scenes))
    combined = f"{story_key} {prompt_key}".strip()
    if not _has_any_token(combined, _BAKERY_TERMS):
        return False
    action_terms = (
        "walk",
        "walking",
        "di bo",
        "street",
        "duong",
        "sidewalk",
        "via he",
        "look",
        "notice",
        "nhin thay",
        "thay",
        "enter",
        "vao tiem",
        "buoc vao",
        "choose",
        "chon",
        "counter",
        "quay",
        "display",
        "hold",
        "cam",
        "leave",
        "roi",
        "buoc ra",
    )
    score = sum(1 for term in action_terms if _has_any_token(combined, (term,)))
    bakery_mentions = sum(1 for term in _BAKERY_TERMS if _has_any_token(combined, (term,)))
    return score >= 2 or bakery_mentions >= 2


def _detect_minimalist_setting_action(scene, order_index: int, *, bakery_sequence: bool) -> tuple[str, str, str, str]:
    story_key = _ascii_key(_story_text_for_scene(scene))
    prompt_key = _ascii_key(getattr(scene, "image_prompt", "") or "")
    combined = f"{story_key} {prompt_key}".strip()

    if story_key and _has_any_token(story_key, _BEDROOM_EXPLICIT_TERMS):
        return "bedroom", _detect_action_from_key(story_key) or "quiet_reflection", "explicit_bedroom", "story_keywords"

    if bakery_sequence and order_index in _BAKERY_SEQUENCE_BY_ORDER:
        setting, action = _BAKERY_SEQUENCE_BY_ORDER[order_index]
        return setting, action, "bakery_sequence", "bakery_sequence"

    key = story_key or prompt_key
    if _has_any_token(key, ("display counter", "counter", "quay banh", "tu kinh", "ke banh")):
        return "bakery_counter", "choosing_cake", "story_keywords", "story_keywords"
    if _has_any_token(key, ("choose", "choosing", "chon", "lua")) and _has_any_token(combined, _BAKERY_TERMS):
        return "bakery_counter", "choosing_cake", "story_keywords", "story_keywords"
    if _has_any_token(key, ("enter", "entering", "step through", "buoc vao", "vao tiem", "mo cua")):
        return "bakery_entrance", "entering_shop", "story_keywords", "story_keywords"
    if _has_any_token(key, ("leave", "leaving", "walk out", "buoc ra", "roi tiem", "ra khoi")):
        return "exit_street", "leaving_shop", "story_keywords", "story_keywords"
    if _has_any_token(key, ("hold", "holding", "cam", "cake box", "paper bag", "hop banh", "tui giay")):
        return "bakery_interior", "holding_cake", "story_keywords", "story_keywords"
    if _has_any_token(key, ("bakery storefront", "storefront", "tiem banh", "cua hang banh")):
        return "bakery_exterior", "noticing_bakery", "story_keywords", "story_keywords"
    if _has_any_token(key, ("walk", "walking", "di bo", "street", "sidewalk", "duong", "via he", "outside", "ngoai duong")):
        return "street_sidewalk", "walking_outside", "story_keywords", "story_keywords"
    if _has_any_token(key, _BAKERY_TERMS):
        return "bakery_interior", _detect_action_from_key(key) or "quiet_reflection", "story_keywords", "story_keywords"
    if _scene_requires_secondary(scene):
        return "generic_emotional", "standing_apart", "cast_requirement", "cast_requirement"
    return "generic_emotional", _detect_action_from_key(key) or "quiet_reflection", "default", "default"


def _detect_action_from_key(key: str) -> str:
    if _has_any_token(key, ("walk", "walking", "di bo", "sidewalk", "street", "duong")):
        return "walking_outside"
    if _has_any_token(key, ("look", "looking", "notice", "noticing", "nhin", "thay")):
        return "noticing_bakery"
    if _has_any_token(key, ("enter", "entering", "buoc vao", "vao tiem")):
        return "entering_shop"
    if _has_any_token(key, ("choose", "choosing", "chon", "lua")):
        return "choosing_cake"
    if _has_any_token(key, ("hold", "holding", "cam", "cake box", "hop banh", "paper bag", "tui giay")):
        return "holding_cake"
    if _has_any_token(key, ("leave", "leaving", "walk out", "buoc ra", "roi")):
        return "leaving_shop"
    return ""


def _prompt_has_terms(prompt: str, terms: tuple[str, ...]) -> bool:
    key = _ascii_key(prompt)
    return all(_has_any_token(key, (term,)) for term in terms[:1]) or any(
        _has_phrase(key, (term,)) or _has_any_token(key, (term,))
        for term in terms
    )


def _update_prompt_match_metadata(scene) -> None:
    prompt = getattr(scene, "image_prompt", "") or ""
    setting = getattr(scene, "scene_setting", "") or "generic_emotional"
    action = getattr(scene, "scene_action", "") or "quiet_reflection"
    scene.prompt_setting_matches_story = _prompt_has_terms(
        prompt,
        _SETTING_MATCH_TERMS.get(setting, (setting,)),
    )
    scene.prompt_action_matches_story = _prompt_has_terms(
        prompt,
        _ACTION_MATCH_TERMS.get(action, (action,)),
    )


def _prompt_summary(prompt: str, max_len: int = 220) -> str:
    text = re.sub(r"\s+", " ", (prompt or "")).strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip(" ,") + "..."


def _cloudflare_safe_minimalist_prompt(scene) -> str:
    action_key = getattr(scene, "scene_action", "") or "quiet_reflection"
    if action_key not in _CLOUDFLARE_SAFE_ACTION_PROMPTS:
        action_key = "quiet_reflection"
    setting_key = getattr(scene, "scene_setting", "") or "generic_emotional"
    if setting_key not in _CLOUDFLARE_SAFE_SETTING_TAILS:
        setting_key = "generic_emotional"

    prompt = " ".join(
        p.strip()
        for p in (
            _CLOUDFLARE_SAFE_ACTION_PROMPTS[action_key],
            _CLOUDFLARE_SAFE_SETTING_TAILS[setting_key],
            "Wholesome everyday scene, fully clothed, modest outfit, family-safe, non-romantic.",
        )
        if p and p.strip()
    )
    if setting_key != "bedroom":
        prompt = re.sub(r"\bbedroom\b", "quiet setting", prompt, flags=re.IGNORECASE)
    for pattern in _CLOUDFLARE_SAFE_RISKY_PATTERNS:
        prompt = re.sub(pattern, "", prompt, flags=re.IGNORECASE)
    prompt = re.sub(r"\s{2,}", " ", prompt)
    prompt = re.sub(r"\s+([,.;])", r"\1", prompt)
    prompt = re.sub(r",\s*,", ",", prompt)
    return _limit_minimalist_prompt(prompt.strip(" ,"), max_len=900)


def _symbolic_provider_subject(scene) -> str:
    if getattr(scene, "visual_variant_id", ""):
        character = str(getattr(scene, "character_archetype", "") or "one quiet person").replace("_", " ")
        action = str(getattr(scene, "primary_action", "") or "holding").replace("_", " ")
        primary = str(getattr(scene, "primary_object", "") or "symbolic object").replace("_", " ")
        secondary = str(getattr(scene, "secondary_object", "") or "").replace("_", " ")
        subject = f"{character}, {action} with {primary}"
        if secondary:
            subject += f", {secondary} also visible"
        return subject

    key = _ascii_key(
        " ".join(
            str(part or "")
            for part in (
                getattr(scene, "scene_meaning", ""),
                getattr(scene, "emotional_metaphor", ""),
                getattr(scene, "symbolic_visual", ""),
                getattr(scene, "main_character_or_object", ""),
            )
        )
    )
    if any(term in key for term in ("box", "unseen effort", "unnoticed")):
        return "person carrying boxes while two other people pass nearby"
    if any(term in key for term in ("measuring", "balance scale", "comparison")):
        return "two people beside unequal measuring marks"
    if any(term in key for term in ("isolated", "crowd", "group of at least three")):
        return "one separated person beside a visible group of three people"
    if "moon" in key:
        return "seated person beneath a dim moon with a large stone nearby"
    if any(term in key for term in ("speech bubble", "quiet circle")):
        return "person inside a quiet circle with empty speech bubbles"
    if any(term in key for term in ("bird", "letting go", "placing a stone")):
        return "person placing down a stone while a small bird flies away"
    if any(term in key for term in ("calm smile", "dark cloud", "heavy cloud")):
        return "person with a calm expression and a dark cloud nearby"
    if any(term in key for term in ("carrying", "heavy stone", "burden")):
        return "person carrying a large stone"

    subject = getattr(scene, "main_character_or_object", "") or "simple symbolic object"
    for pattern in _CLOUDFLARE_SAFE_SYMBOLIC_RISKY_PATTERNS:
        subject = re.sub(pattern, "", subject, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", subject).strip(" ,.;") or "simple symbolic object"


def _cloudflare_safe_symbolic_prompt(scene) -> str:
    """Build the positive-only initial symbolic provider prompt."""
    selected_prompt = str(getattr(scene, "provider_prompt_variant", "") or "").strip()
    if selected_prompt:
        for pattern in _CLOUDFLARE_SAFE_SYMBOLIC_RISKY_PATTERNS:
            selected_prompt = re.sub(pattern, "", selected_prompt, flags=re.IGNORECASE)
        selected_prompt = re.sub(r"\s{2,}", " ", selected_prompt)
        selected_prompt = re.sub(r"\s+([,.;])", r"\1", selected_prompt)
        selected_prompt = re.sub(r",\s*,", ",", selected_prompt)
        return _limit_minimalist_prompt(selected_prompt.strip(" ,"), max_len=900)
    subject = _symbolic_provider_subject(scene)
    return (
        "minimalist hand-drawn emotional symbolic illustration, "
        f"{subject}, moderately dark warm taupe and muted brown-gray background, "
        "soft low-key light with readable contrast, soft brown pencil lines, "
        "flat muted earth colors, gentle calm melancholic mood, centered layout, "
        "generous negative space, low visual clutter, clear readable symbolic action"
    )


def _cloudflare_compact_symbolic_retry_prompt(scene) -> str:
    """Build one short policy retry independently from the long plan prompt."""
    return (
        "minimalist hand-drawn doodle, "
        f"{_symbolic_provider_subject(scene)}, warm dark taupe background, "
        "brown pencil lines, muted earth colors, gentle calm mood, "
        "simple clear composition"
    )


def _choose_motif(text: str, scene_index: int, used: set[str], previous: str) -> str:
    text_l = (text or "").lower()
    scored: list[tuple[int, int, str]] = []
    for i, motif in enumerate(_MINIMALIST_MOTIFS):
        score = sum(1 for word in _MOTIF_KEYWORDS.get(motif, ()) if word in text_l)
        if motif == previous:
            score -= 4
        if motif in used:
            score -= 1
        scored.append((score, -i, motif))
    scored.sort(reverse=True)
    chosen = scored[0][2]
    if scored[0][0] <= 0:
        start = (scene_index - 1) % len(_MINIMALIST_MOTIFS)
        for offset in range(len(_MINIMALIST_MOTIFS)):
            candidate = _MINIMALIST_MOTIFS[(start + offset) % len(_MINIMALIST_MOTIFS)]
            if candidate != previous and candidate not in used:
                return candidate
        chosen = _MINIMALIST_MOTIFS[start]
        if chosen == previous:
            chosen = _MINIMALIST_MOTIFS[(start + 1) % len(_MINIMALIST_MOTIFS)]
    return chosen


def _assign_minimalist_visual_plans(scenes) -> None:
    used: set[str] = set()
    previous_motif = ""
    bakery_sequence = _is_bakery_sequence(scenes)
    for idx, scene in enumerate(scenes, start=1):
        text = " ".join(
            str(part or "")
            for part in (scene.title, scene.voice_script)
        )
        setting, action, setting_source, action_source = _detect_minimalist_setting_action(
            scene,
            idx,
            bakery_sequence=bakery_sequence,
        )
        motif = _choose_motif(text, idx, used, previous_motif)
        pose = _MOTIF_TO_POSE[motif]
        composition = _COMPOSITIONS[(idx - 1) % len(_COMPOSITIONS)]

        scene.scene_setting = setting
        scene.scene_action = action
        scene.setting_source = setting_source
        scene.action_source = action_source
        scene.primary_motif = motif
        scene.pose_family = pose
        scene.optional_secondary_motif = ""
        scene.composition_hint = composition
        scene.frame_safety_hint = (
            "full body visible, head fully visible, feet fully visible, "
            "character within central safe area, character occupies about 35-45 "
            "percent of frame height, bottom 25 percent empty for captions"
        )

        used.add(motif)
        previous_motif = motif


def _normalize_prompt_for_compare(prompt: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (prompt or "").lower()).strip()


def scene_frame_hint(_: str = "") -> str:
    return (
        "full body visible, head fully visible, feet fully visible, character "
        "placed within central safe area, character occupies about 35-45 percent "
        "of frame height, keep bottom 25 percent mostly empty for captions"
    )


def _validate_minimalist_diversity(scenes) -> None:
    seen_prompts: dict[str, int] = {}
    pair_counts: dict[tuple[str, str], int] = {}
    previous_pair: tuple[str, str] | None = None
    for idx, scene in enumerate(scenes, start=1):
        pair = (scene.pose_family, scene.primary_motif)
        norm = _normalize_prompt_for_compare(scene.image_prompt)
        seen_prompts[norm] = seen_prompts.get(norm, 0) + 1
        pair_counts[pair] = pair_counts.get(pair, 0) + 1

        if previous_pair == pair or pair_counts[pair] > 2 or seen_prompts[norm] > 1:
            old_motif = scene.primary_motif
            used = {s.primary_motif for s in scenes[: idx - 1]}
            scene.primary_motif = _choose_motif(
                scene.voice_script + " " + scene.title,
                idx + len(used),
                used,
                previous_pair[1] if previous_pair else "",
            )
            scene.pose_family = _MOTIF_TO_POSE[scene.primary_motif]
            scene.composition_hint = _COMPOSITIONS[(idx + 2) % len(_COMPOSITIONS)]
            scene.frame_safety_hint = (
                "full body visible, head fully visible, feet fully visible, "
                "character within central safe area, character occupies about "
                "35-45 percent of frame height, bottom 25 percent empty for captions"
            )
            logger.warning(
                "scene %d: adjusted duplicate minimalist visual plan %s -> %s",
                scene.scene_index,
                old_motif,
                scene.primary_motif,
            )
        previous_pair = (scene.pose_family, scene.primary_motif)


def _prepare_minimalist_image_prompts(scenes) -> None:
    _assign_minimalist_visual_plans(scenes)
    _validate_minimalist_diversity(scenes)
    for scene in scenes:
        scene.image_prompt = _sanitize_minimalist_prompt(
            scene.image_prompt,
            pose_family=scene.pose_family,
            primary_motif=scene.primary_motif,
            composition_hint=scene.composition_hint,
            requires_secondary=_scene_requires_secondary(scene),
            scene_setting=scene.scene_setting,
            scene_action=scene.scene_action,
        )
        _update_prompt_match_metadata(scene)
    _validate_minimalist_diversity(scenes)
    for scene in scenes:
        scene.image_prompt = _sanitize_minimalist_prompt(
            scene.image_prompt,
            pose_family=scene.pose_family,
            primary_motif=scene.primary_motif,
            composition_hint=scene.composition_hint,
            requires_secondary=_scene_requires_secondary(scene),
            scene_setting=scene.scene_setting,
            scene_action=scene.scene_action,
        )
        _update_prompt_match_metadata(scene)
        logger.info(
            "scene %d visual prompt adherence: voice_script=%r scene_setting=%s "
            "scene_action=%s final image prompt summary=%s",
            scene.scene_index,
            (scene.voice_script or "")[:180],
            scene.scene_setting,
            scene.scene_action,
            _prompt_summary(scene.image_prompt),
        )


def _scene_requires_secondary(scene) -> bool:
    required = {str(item).strip().lower() for item in getattr(scene, "required_characters", [])}
    if "male" in required:
        return True
    names = {str(item).strip().lower() for item in (getattr(scene, "character_names", []) or [])}
    prompt = (getattr(scene, "image_prompt", "") or "").lower()
    return (
        bool(names & {"male memory", "secondary male", "young man"})
        or "young vietnamese man" in prompt
        or "young man" in prompt
    )


def _sanitize_minimalist_prompt(
    prompt: str,
    *,
    pose_family: str = "",
    primary_motif: str = "",
    composition_hint: str = "",
    requires_secondary: bool = False,
    scene_setting: str = "",
    scene_action: str = "",
) -> str:
    """Return a stable safe-pose prompt for minimalist emotional scenes."""
    prompt_l = (prompt or "").lower()

    motif = primary_motif if primary_motif in _MINIMALIST_MOTIFS else ""
    if not motif:
        motif = _choose_motif(prompt_l, 1, set(), "")
    pose_key = pose_family if pose_family in _MINIMALIST_POSES else _MOTIF_TO_POSE[motif]
    setting_key = scene_setting if scene_setting in _SETTING_PROMPTS else "generic_emotional"
    action_key = scene_action if scene_action in _ACTION_PROMPTS else ""
    setting_prompt = _SETTING_PROMPTS[setting_key]
    action_prompt = _ACTION_PROMPTS.get(action_key, "")
    composition = composition_hint or _COMPOSITIONS[0]
    pose_prompt = (
        "split composition: young woman seated or standing in the middle ground, "
        "young Vietnamese man clearly visible full body near the doorway or "
        "window, turned away, wide gap of empty floor between them"
        if requires_secondary else
        action_prompt or _MINIMALIST_POSES[pose_key]
    )

    prompt = ", ".join(
        (
            _MINIMALIST_TWO_CHARACTER_SAFE_PROMPT if requires_secondary else _MINIMALIST_SAFE_PROMPT,
            f"scene setting: {setting_prompt}",
            f"scene action: {action_prompt or pose_prompt}",
            pose_prompt,
            f"primary motif id: {motif}",
            f"primary motif: {_MOTIF_DESCRIPTIONS[motif]}",
            f"composition: {composition}",
            f"frame safety: {scene_frame_hint(composition_hint)}",
            _MINIMALIST_STYLE_LOCK,
            _MINIMALIST_COMPOSITION_LOCK,
            _MINIMALIST_TWO_CHARACTER_LOCK if requires_secondary else _MINIMALIST_ONE_CHARACTER_LOCK,
            "no exposed skin, no injury, no gore, no complex hand gesture",
        )
    )
    for phrase in _MINIMALIST_FORBIDDEN_HINTS:
        prompt = re.sub(re.escape(phrase), "", prompt, flags=re.IGNORECASE)
    prompt = re.sub(r"\s{2,}", " ", prompt)
    prompt = re.sub(r"\s+,", ",", prompt)
    return _limit_minimalist_prompt(prompt.strip(" ,"))


def _limit_minimalist_prompt(prompt: str, max_len: int = 1900) -> str:
    prompt = (prompt or "").strip(" ,")
    if len(prompt) <= max_len:
        return prompt
    parts = [p.strip() for p in prompt.split(",") if p.strip()]
    kept: list[str] = []
    total = 0
    for part in parts:
        candidate_len = len(part) + (2 if kept else 0)
        if total + candidate_len > max_len:
            break
        kept.append(part)
        total += candidate_len
    limited = ", ".join(kept).strip(" ,")
    if not limited:
        limited = prompt[:max_len].rsplit(" ", 1)[0].strip(" ,")
    return limited


def _minimalist_provider_prompt(scene) -> str:
    requires_secondary = _scene_requires_secondary(scene)
    pose = _MINIMALIST_POSES.get(
        getattr(scene, "pose_family", "") or "",
        "young woman standing calmly in the middle ground",
    )
    setting_key = getattr(scene, "scene_setting", "") or "generic_emotional"
    if setting_key not in _SETTING_PROMPTS:
        setting_key = "generic_emotional"
    action_key = getattr(scene, "scene_action", "") or "quiet_reflection"
    if action_key not in _ACTION_PROMPTS:
        action_key = "quiet_reflection"
    setting_part = _SETTING_PROMPTS[setting_key]
    action_part = _ACTION_PROMPTS[action_key]
    motif = _MOTIF_DESCRIPTIONS.get(
        getattr(scene, "primary_motif", "") or "",
        "one small warm symbolic prop near the character",
    )
    composition = (
        getattr(scene, "composition_hint", "") or
        "medium-wide emotional scene with generous negative space"
    )
    character_parts = (
        [
            "wide split-scene composition with exactly two adult characters visible",
            "young Vietnamese woman main character with short straight black bob hair and mustard yellow simple dress",
            "young Vietnamese man secondary character with short dark hair and muted brown shirt",
            "the man is clearly visible full body near the doorway or window",
            "the woman and man are separated by a wide empty floor gap",
            "the man is turned away or standing apart",
            "no romantic hugging, no wedding, no extra people",
        ]
        if requires_secondary else
        [
            "one young woman character with short straight black bob hair",
            "small rounded face with gentle expressive eyes",
            "cozy mustard yellow simple dress with rust sleeves",
        ]
    )
    pose_part = (
        "young woman seated or standing in middle ground while the young man stands apart near the doorway or window"
        if requires_secondary else
        action_part or pose
    )
    scale_part = (
        "both characters complete from head to feet, woman about 35 percent of frame height, man smaller but clearly visible"
        if requires_secondary else
        "character is a small middle-ground figure occupying only 35-45 percent of frame height"
    )
    scene.prompt_contains_secondary_character = bool(requires_secondary)
    return _limit_minimalist_prompt(
        ", ".join(
            [
                *character_parts,
                f"scene setting: {setting_part}",
                f"scene action: {action_part}",
                pose_part,
                motif,
                composition,
                "hand-drawn cartoon illustration",
                "soft clean linework",
                "warm muted earthy palette",
                "cute melancholic expression",
                "pulled-back medium-wide shot with generous negative space",
                scale_part,
                "complete character visible from head to feet",
                "vertical scene composition with calm open space around her",
                "soft foreground edge or shadow",
                "background details match the scene setting",
                "soft shadows and subtle dust or memory particles",
                "muted floor, wall, sidewalk, or shop shapes as appropriate",
                "no photorealism, no 3D, no text, no watermark",
            ]
        ),
        max_len=1500,
    )


def _sha256_short(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _record_asset(scene, path: Path) -> None:
    scene.asset_hash = _sha256_short(path)
    logger.info(
        "scene %d asset=%s hash=%s status=%s pose=%s motif=%s compatible=%s "
        "character_mode=%s character_source=%s emotion=%s expression=%s head=%s face=%s "
        "socket_fallback=%s placeholder_sprite=%s placeholder_rig=%s "
        "placeholder_head=%s placeholder_face=%s composition=%s",
        scene.scene_index,
        path.name,
        scene.asset_hash,
        scene.asset_status,
        scene.pose_family,
        scene.primary_motif,
        scene.compatible_motif_used,
        scene.character_mode,
        scene.character_source,
        scene.emotion_tag,
        scene.selected_expression,
        scene.head_base_path,
        scene.face_path,
        scene.socket_alignment_fallback,
        scene.is_placeholder_sprite,
        scene.is_placeholder_rig,
        scene.is_placeholder_head,
        scene.is_placeholder_face,
        scene.composition_hint,
    )


def _finalize_minimalist_scene_metadata(
    scene,
    *,
    visual_mode: str,
    provider: str,
    used_reference_conditioning: bool = False,
    reference_paths: list[str] | None = None,
) -> None:
    scene.visual_mode = scene.visual_mode or visual_mode
    scene.provider = scene.provider or provider
    scene.used_reference_conditioning = bool(used_reference_conditioning)
    if reference_paths is not None:
        scene.reference_paths = list(reference_paths)


def _set_image_source_metadata(
    scene,
    *,
    image_source: str,
    image_provider: str,
    asset_path: Path | str,
    job_dir: Path,
    used_local_fallback: bool = False,
) -> None:
    scene.image_source = image_source
    scene.image_provider = image_provider
    scene.used_local_fallback = bool(used_local_fallback)
    try:
        scene.asset_path = str(Path(asset_path).relative_to(job_dir)).replace("\\", "/")
    except ValueError:
        scene.asset_path = str(asset_path).replace("\\", "/")
    logger.info(
        "scene %d image_source=%s image_provider=%s used_local_fallback=%s "
        "asset_path=%s requested_characters=%s required_characters=%s "
        "cast_source=%s cast_fallback_applied=%s prompt_contains_secondary_character=%s",
        scene.scene_index,
        scene.image_source,
        scene.image_provider,
        scene.used_local_fallback,
        scene.asset_path,
        getattr(scene, "requested_characters", []),
        getattr(scene, "required_characters", []),
        getattr(scene, "cast_source", ""),
        getattr(scene, "cast_fallback_applied", False),
        getattr(scene, "prompt_contains_secondary_character", False),
    )


def _seed_for_scene(plan: TellaScenePlan, scene) -> int:
    if plan.theme == "minimalist_emotional":
        return _VIDEO_SEED + scene.scene_index * 101
    return _VIDEO_SEED


def _symbolic_expected_character_count(scene) -> int:
    selected_count = int(getattr(scene, "character_count", 0) or 0)
    if getattr(scene, "visual_variant_id", "") and selected_count > 0:
        return selected_count
    required = " ".join(scene.symbolic_qc_expected_subjects).lower()
    if "crowd" in required or "at least two adult human figures" in required:
        return 2
    return 0 if scene.cast_archetype == "symbolic_object" else 1


async def _fetch_minimalist_reference_assets(
    plan: TellaScenePlan,
    body_scenes,
    job_dir: Path,
    assets_dir: Path,
    width: int,
    height: int,
) -> None:
    provider = get_image_provider(os.environ.get("TELLA_IMAGE_PROVIDER") or "cloudflare")
    logger.info(
        "minimalist_emotional visual_mode=reference provider=%s supports_reference_conditioning=%s",
        provider.provider_name,
        provider.supports_reference_conditioning(),
    )
    if _require_reference_conditioning() and not provider.supports_reference_conditioning():
        raise RuntimeError(
            f"TELLA_REQUIRE_REFERENCE_CONDITIONING=1 but provider {provider.provider_name} "
            "does not support image-reference conditioning."
        )
    if not provider.supports_reference_conditioning():
        logger.warning(
            "Reference image conditioning is not available for this provider; using text lock only."
        )

    visual_bible = build_visual_bible(plan)
    save_visual_bible(visual_bible, job_dir)
    references = await generate_character_references(
        visual_bible,
        job_dir,
        provider,
        aspect=plan.aspect_ratio,
    )
    selected_refs = selected_reference_paths(references, job_dir)
    if not selected_refs:
        raise RuntimeError(
            "reference mode could not generate any usable character references; "
            "check image provider credentials and reference metadata."
        )

    previous_scene_path = ""
    previous_hashes: list[str] = []
    visual_plans: list[dict] = []
    total_vision_qc_calls = 0
    total_scene_regeneration_attempts = 0
    total_qc_json_parse_attempts = 0
    for scene in body_scenes:
        base = f"scene_{scene.scene_index:02d}_{_safe_stem(scene.title)}"
        use_previous = (
            _use_previous_scene_reference()
            and provider.supports_reference_conditioning()
            and previous_scene_path
        )
        infer_scene_anatomy_expectations(scene)
        scene_visual_plan = build_scene_visual_plan(
            scene,
            visual_bible,
            references,
            previous_scene_reference_path=previous_scene_path if use_previous else "",
        )
        visual_plans.append(scene_visual_plan.model_dump())

        prompt = scene_visual_plan.visual_prompt
        negative_prompt = scene_visual_plan.negative_prompt
        reference_inputs = list(selected_refs)
        if use_previous:
            reference_inputs.append(job_dir / previous_scene_path)
        original_reference_paths = [str(p.relative_to(job_dir)) for p in reference_inputs]
        scene.original_reference_paths = list(original_reference_paths)

        final_out = assets_dir / f"{base}.jpg"
        best_out: Path | None = None
        best_qc = None
        attempt_limit = max_attempts()
        scene.max_attempts_allowed = attempt_limit
        soft_fail_streaks: dict[str, int] = {}
        action_mismatch_severity_history: list[str] = []
        attempt_records: list[tuple[Path, SceneQCResult]] = []
        selected_best_failed_attempt = False
        selected_best_failed_attempt_reason = ""
        best_attempt_ranking_summary = ""
        for attempt in range(1, attempt_limit + 1):
            scene.attempt_count = attempt
            attempt_out = final_out if attempt == attempt_limit else assets_dir / f"{base}_attempt_{attempt}.jpg"
            if attempt > 1 and best_qc and best_qc.repair_prompt:
                prompt = best_qc.repair_prompt or repair_prompt(
                    scene_visual_plan.visual_prompt,
                    best_qc.failure_reasons,
                )
                scene.repair_reference_paths = [str(p.relative_to(job_dir)) for p in reference_inputs]
            try:
                result = await provider.generate_reference_image(
                    prompt=prompt,
                    references=reference_inputs,
                    negative_prompt=negative_prompt,
                    aspect=plan.aspect_ratio,
                    seed=_seed_for_scene(plan, scene) + attempt * 17 if provider.supports_seed() else None,
                    out_path=attempt_out,
                    metadata={
                        "scene_index": scene.scene_index,
                        "visual_mode": "reference",
                        "reference_ids": scene_visual_plan.character_reference_ids,
                    },
                )
            except Exception as exc:
                scene.asset_error = str(exc)[:300]
                if attempt >= attempt_limit:
                    raise
                logger.warning(
                    "scene %d reference generation attempt %d/%d failed: %s",
                    scene.scene_index,
                    attempt,
                    attempt_limit,
                    str(exc)[:160],
                )
                continue

            if attempt > 1:
                total_scene_regeneration_attempts += 1
            scene.prompt_used = result.prompt_used
            scene.negative_prompt_used = result.negative_prompt_used
            scene.provider = result.provider
            scene.used_reference_conditioning = result.used_reference_conditioning
            scene.reference_paths = [str(p.relative_to(job_dir)) for p in selected_refs]
            scene.previous_scene_reference_path = scene_visual_plan.previous_scene_reference_path
            if attempt > 1:
                scene.used_reference_conditioning_on_repair = bool(result.used_reference_conditioning)
            qc_result = evaluate_scene_image(
                scene,
                attempt_out,
                visual_bible,
                {
                    "aspect": plan.aspect_ratio,
                    "previous_hashes": previous_hashes,
                    "width": width,
                    "height": height,
                    "job_dir": job_dir,
                    "attempt": attempt,
                    "max_attempts_allowed": attempt_limit,
                    "is_final_attempt": attempt >= attempt_limit,
                    "expected_character_count": scene_visual_plan.expected_character_count,
                    "soft_fail_streaks": soft_fail_streaks,
                    "action_mismatch_severity_history": action_mismatch_severity_history,
                    "original_reference_paths": original_reference_paths,
                },
            )
            best_qc = qc_result
            total_vision_qc_calls += int(qc_result.vision_qc_call_count)
            total_qc_json_parse_attempts += int(qc_result.qc_json_parse_attempt_count)
            soft_fail_streaks = {
                "hairstyle": int(qc_result.hairstyle_mismatch_streak),
                "outfit": int(qc_result.outfit_mismatch_streak),
                "action": int(qc_result.action_mismatch_streak),
            }
            action_mismatch_severity_history = list(qc_result.action_mismatch_severity_history)
            attempt_records.append((attempt_out, qc_result))
            save_qc_result(qc_result, job_dir, attempt=attempt, final=False)
            if qc_result.passed:
                best_out = attempt_out
                break
            if qc_result.stopped_retry_loop_early_due_to_repeated_soft_fail:
                logger.warning(
                    "scene %d QC stopped retries early after repeated soft-fail escalation: %s",
                    scene.scene_index,
                    qc_result.repeated_soft_fail_escalation_reasons,
                )
                break
            logger.warning(
                "scene %d QC failed attempt %d/%d score=%.2f reasons=%s",
                scene.scene_index,
                attempt,
                attempt_limit,
                qc_result.score,
                qc_result.failure_reasons,
            )

        if best_out is None:
            if attempt_records:
                ranked_records = sorted(
                    enumerate(attempt_records),
                    key=lambda item: rank_qc_attempt(item[1][1], item[0]),
                )
                _, (best_out, best_qc) = ranked_records[0]
                selected_best_failed_attempt = True
                selected_best_failed_attempt_reason = (
                    "all attempts failed; selected least-bad attempt by QC ranking"
                )
                best_attempt_ranking_summary = summarize_qc_attempts(
                    [record[1] for record in attempt_records]
                )
            else:
                best_out = final_out if final_out.is_file() else attempt_out
            if strict_visual_qc() and best_qc and not best_qc.passed:
                raise RuntimeError(
                    f"scene {scene.scene_index} failed strict visual QC: {best_qc.failure_reasons}"
                )
        if best_qc:
            save_qc_result(best_qc, job_dir, final=True)
        if best_out != final_out:
            final_out.write_bytes(best_out.read_bytes())

        final_hash = image_hash(final_out)
        previous_hashes.append(final_hash)
        previous_scene_path = str(final_out.relative_to(job_dir))
        scene.image_filenames = [f"assets/{final_out.name}"]
        scene.asset_status = "reference_generated"
        scene.asset_error = ""
        scene.asset_hash = final_hash
        _finalize_minimalist_scene_metadata(
            scene,
            visual_mode="reference",
            provider=provider.provider_name,
            used_reference_conditioning=scene.used_reference_conditioning,
            reference_paths=[str(p.relative_to(job_dir)) for p in selected_refs],
        )
        _set_image_source_metadata(
            scene,
            image_source="reference_guided_ai_image",
            image_provider=provider.provider_name,
            asset_path=final_out,
            job_dir=job_dir,
            used_local_fallback=False,
        )
        scene.character_source = "reference_generated"
        scene.character_mode = "ai_reference"
        if best_qc:
            try:
                selected_attempt_path = str(best_out.relative_to(job_dir))
            except ValueError:
                selected_attempt_path = str(best_out)
            apply_qc_result_to_scene(
                scene,
                best_qc,
                selected_attempt_path=selected_attempt_path,
                attempts_actually_ran=len(attempt_records),
                max_attempts_allowed=attempt_limit,
                selected_best_failed_attempt=selected_best_failed_attempt,
                selected_best_failed_attempt_reason=selected_best_failed_attempt_reason,
                best_attempt_ranking_summary=best_attempt_ranking_summary,
            )
        _record_asset(scene, final_out)

    plan.total_vision_qc_calls = total_vision_qc_calls
    plan.total_scene_regeneration_attempts = total_scene_regeneration_attempts
    plan.total_qc_json_parse_attempts = total_qc_json_parse_attempts
    (job_dir / "visual_plans.json").write_text(
        json.dumps(visual_plans, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("fetch_assets: all %d minimalist reference scenes done", len(body_scenes))


def _force_character_mode_for_visual_mode(visual_mode: str) -> str | None:
    if visual_mode == "rig":
        return "rig"
    if visual_mode == "curated_sprite":
        return "auto"
    return None


async def fetch_assets(plan: TellaScenePlan, job_dir: Path) -> None:
    """Populate ``plan.scenes[i].image_filenames`` for every body scene.

    Mutates the plan in place. Writes to ``<job_dir>/assets/``.

    Raises:
        RuntimeError: when ANY scene's asset fetch fails. Callers wanting
            partial-success behaviour should wrap in their own try/except.
    """
    job_dir = Path(job_dir)
    assets_dir = job_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    body_scenes = [s for s in plan.scenes if s.kind == "scene"]
    if not body_scenes:
        return

    width, height = _GEN_DIMS.get(plan.aspect_ratio, _GEN_DIMS["9:16"])
    local_fallback_allowed = _local_image_fallback_allowed()
    skip_image_generation = _skip_image_generation()
    if skip_image_generation:
        logger.info("skip-image-generation active: provider submissions disabled")
    plan.local_fallback_allowed = local_fallback_allowed
    plan.used_local_fallback = False
    plan.reused_asset = False
    plan.reused_from_job_id = ""
    plan.reused_asset_prompt_hash_mismatch = False
    plan.reuse_mode = _reuse_assets_mode() if _reuse_assets_enabled() else "strict"
    plan.ai_images_requested = 0
    plan.ai_images_generated = 0
    plan.ai_images_reused = 0
    max_ai_images = _env_int_optional("TELLA_MAX_AI_IMAGES")
    reuse_index = _load_reuse_index(job_dir)
    loose_reuse_index = _load_loose_reuse_index(job_dir) if plan.reuse_mode == "loose_debug" else {}
    request_budget = _CloudflareRequestBudget(plan, body_scenes, max_ai_images)

    for scene in body_scenes:
        scene.local_fallback_allowed = local_fallback_allowed
        scene.reuse_mode = plan.reuse_mode
        scene.skip_image_generation = skip_image_generation

    if plan.reuse_mode == "loose_debug":
        logger.warning(
            "Using mismatched reused assets for debug only. Visuals may not match the current prompt."
        )

    if plan.media_source == "ai_image" and plan.theme == "minimalist_emotional":
        visual_mode = _minimalist_visual_mode()
        logger.info("minimalist_emotional visual_mode=%s", visual_mode)
        _prepare_minimalist_image_prompts(body_scenes)
        if visual_mode == "reference" and not skip_image_generation:
            await _fetch_minimalist_reference_assets(
                plan,
                body_scenes,
                job_dir,
                assets_dir,
                width,
                height,
            )
            return
        if visual_mode in {"curated_sprite", "rig"} and not skip_image_generation:
            logger.info(
                "minimalist_emotional local composition active; "
                "Cloudflare full-scene image generation is not called by default"
            )
            forced_character_mode = _force_character_mode_for_visual_mode(visual_mode)
            old_character_mode = os.environ.get("TELLA_MINIMALIST_CHARACTER_MODE")
            if forced_character_mode:
                os.environ["TELLA_MINIMALIST_CHARACTER_MODE"] = forced_character_mode
            job_state = sprite_composer.JobState(job_id=job_dir.name)
            try:
                for scene in body_scenes:
                    base = f"scene_{scene.scene_index:02d}_{_safe_stem(scene.title)}"
                    out = assets_dir / f"{base}.jpg"
                    result = sprite_composer.compose_scene(
                        scene,
                        out,
                        width,
                        height,
                        job_state,
                    )
                    scene.image_filenames = [f"assets/{out.name}"]
                    scene.asset_status = "local_composed"
                    scene.asset_error = ""
                    scene.asset_hash = result.asset_hash
                    _finalize_minimalist_scene_metadata(
                        scene,
                        visual_mode=visual_mode,
                        provider="local",
                    )
                    _set_image_source_metadata(
                        scene,
                        image_source="local_composer",
                        image_provider="local",
                        asset_path=out,
                        job_dir=job_dir,
                        used_local_fallback=False,
                    )
                    _record_asset(scene, out)
            finally:
                if forced_character_mode:
                    if old_character_mode is None:
                        os.environ.pop("TELLA_MINIMALIST_CHARACTER_MODE", None)
                    else:
                        os.environ["TELLA_MINIMALIST_CHARACTER_MODE"] = old_character_mode
            logger.info("fetch_assets: all %d minimalist local scenes done", len(body_scenes))
            return
        logger.info(
            "minimalist_emotional visual_mode=ai_scene; using full-scene AI generation without reference conditioning"
        )

    sem = asyncio.Semaphore(MAX_CONCURRENT)
    ai_fallback_state = sprite_composer.JobState(job_id=f"{job_dir.name}:fallback")
    symbolic_visual_bible = (
        VisualBible(style_bible=StyleBible())
        if plan.media_source == "ai_image" and plan.theme == "minimalist_symbolic_reel"
        else None
    )

    async def _generate_ai_image(
        scene,
        prompt: str,
        out: Path,
        *,
        seed: int | None,
        stage: str = "initial",
    ) -> None:
        _assert_provider_submission_allowed()

        async def _before_request() -> None:
            await request_budget.acquire(scene, prompt, stage)

        with ai_image.cloudflare_request_hook(_before_request):
            await ai_image.generate_image(
                prompt,
                out,
                width=width,
                height=height,
                seed=seed,
            )
        plan.ai_images_generated += 1
        scene.ai_images_generated += 1

    async def _generate_symbolic_image_with_qc(
        scene,
        prompt: str,
        final_out: Path,
        base: str,
    ) -> Path:
        assert symbolic_visual_bible is not None
        attempt_limit = max_attempts()
        scene.max_attempts_allowed = attempt_limit
        current_prompt = prompt
        symbolic_soft_fail_streaks: dict[str, int] = {}
        attempt_records: list[tuple[Path, SceneQCResult]] = []
        selected_out: Path | None = None
        selected_qc: SceneQCResult | None = None

        for attempt in range(1, attempt_limit + 1):
            scene.attempt_count = attempt
            attempt_out = (
                final_out
                if attempt == attempt_limit
                else assets_dir / f"{base}_attempt_{attempt}.jpg"
            )
            scene.prompt_used = current_prompt
            try:
                await _generate_ai_image(
                    scene,
                    current_prompt,
                    attempt_out,
                    seed=_seed_for_scene(plan, scene) + attempt * 17,
                    stage="initial",
                )
            except Exception as initial_exc:
                if not _is_cloudflare_code_3030(initial_exc):
                    raise
                scene.last_cloudflare_policy_code = 3030
                compact_prompt = _cloudflare_compact_symbolic_retry_prompt(scene)
                if compact_prompt == current_prompt:
                    raise RuntimeError(
                        "symbolic policy retry prompt unexpectedly matched the rejected prompt"
                    ) from initial_exc
                scene.provider_prompt_retry = compact_prompt
                scene.provider_prompt_retry_hash = _provider_prompt_hash(compact_prompt)
                try:
                    await _generate_ai_image(
                        scene,
                        compact_prompt,
                        attempt_out,
                        seed=_seed_for_scene(plan, scene) + attempt * 17 + 7,
                        stage="policy_retry",
                    )
                    scene.prompt_used = compact_prompt
                except Exception as retry_exc:
                    if not _is_cloudflare_code_3030(retry_exc):
                        raise
                    scene.last_cloudflare_policy_code = 3030
                    maximum = (
                        str(request_budget.maximum)
                        if request_budget.maximum is not None
                        else "unlimited"
                    )
                    raise ai_image.CloudflareAIError(
                        "Initial provider-safe prompt was rejected with Cloudflare "
                        "code 3030; one compact retry was attempted and rejected; "
                        "no third request was made. "
                        f"Scene requests={scene.actual_cloudflare_request_count_for_scene}; "
                        f"run requests={request_budget.used}/{maximum}. Relevant "
                        "provider prompt metadata is available in plan.json.",
                        error_type="content_policy_blocked",
                        status_code=400,
                        recoverable=True,
                        policy_code=3030,
                    ) from retry_exc
            if attempt > 1:
                plan.total_scene_regeneration_attempts += 1

            qc_result = evaluate_scene_image(
                scene,
                attempt_out,
                symbolic_visual_bible,
                {
                    "theme": plan.theme,
                    "aspect": plan.aspect_ratio,
                    "width": width,
                    "height": height,
                    "job_dir": job_dir,
                    "attempt": attempt,
                    "max_attempts_allowed": attempt_limit,
                    "is_final_attempt": attempt >= attempt_limit,
                    "expected_character_count": _symbolic_expected_character_count(scene),
                    "symbolic_qc_expected_subjects": list(
                        scene.symbolic_qc_expected_subjects
                    ),
                    "symbolic_soft_fail_streaks": symbolic_soft_fail_streaks,
                    "repaired_prompt_used": attempt > 1,
                },
            )
            selected_qc = qc_result
            attempt_records.append((attempt_out, qc_result))
            plan.total_vision_qc_calls += int(qc_result.vision_qc_call_count)
            plan.total_qc_json_parse_attempts += int(
                qc_result.qc_json_parse_attempt_count
            )
            symbolic_soft_fail_streaks = dict(
                qc_result.symbolic_soft_fail_streaks
            )
            save_qc_result(qc_result, job_dir, attempt=attempt, final=False)
            if qc_result.passed:
                selected_out = attempt_out
                break
            if qc_result.stopped_retry_loop_early_due_to_repeated_soft_fail:
                logger.warning(
                    "scene %d symbolic QC stopped after repeated soft-fail "
                    "escalation: %s",
                    scene.scene_index,
                    qc_result.symbolic_qc_hard_fail_reasons,
                )
                break
            current_prompt = qc_result.repair_prompt or repair_prompt(
                prompt,
                qc_result.failure_reasons,
            )
            logger.warning(
                "scene %d symbolic QC failed attempt %d/%d reasons=%s",
                scene.scene_index,
                attempt,
                attempt_limit,
                qc_result.symbolic_qc_failure_reasons,
            )

        selected_best_failed_attempt = False
        selected_best_failed_attempt_reason = ""
        ranking_summary = ""
        if selected_out is None and attempt_records:
            ranked_records = sorted(
                enumerate(attempt_records),
                key=lambda item: rank_qc_attempt(item[1][1], item[0]),
            )
            _, (selected_out, selected_qc) = ranked_records[0]
            selected_best_failed_attempt = True
            selected_best_failed_attempt_reason = (
                "all symbolic QC attempts failed; selected least-bad attempt "
                "for diagnostics only"
            )
            ranking_summary = summarize_qc_attempts(
                [record[1] for record in attempt_records]
            )

        if selected_qc is not None:
            save_qc_result(selected_qc, job_dir, final=True)
        if selected_out is not None and selected_out != final_out:
            final_out.write_bytes(selected_out.read_bytes())
        if selected_qc is not None and selected_out is not None:
            try:
                selected_attempt_path = str(selected_out.relative_to(job_dir))
            except ValueError:
                selected_attempt_path = str(selected_out)
            apply_qc_result_to_scene(
                scene,
                selected_qc,
                selected_attempt_path=selected_attempt_path,
                attempts_actually_ran=len(attempt_records),
                max_attempts_allowed=attempt_limit,
                selected_best_failed_attempt=selected_best_failed_attempt,
                selected_best_failed_attempt_reason=(
                    selected_best_failed_attempt_reason
                ),
                best_attempt_ranking_summary=ranking_summary,
            )

        if selected_best_failed_attempt:
            reasons = (
                selected_qc.symbolic_qc_failure_reasons
                if selected_qc is not None
                else ["symbolic QC failed without a result"]
            )
            scene.asset_error = f"symbolic visual QC failed: {reasons}"[:300]
            raise _SymbolicQCFailure(
                f"scene {scene.scene_index} failed symbolic visual QC after "
                f"{len(attempt_records)} attempt(s): {reasons}"
            )
        return final_out

    def _apply_reused_asset(
        scene,
        cached: dict,
        out: Path,
        *,
        prompt_hash: str,
        reuse_mode: str,
        prompt_hash_mismatch: bool,
    ) -> bool:
        src = Path(cached["source_path"])
        if not src.is_file():
            return False
        out.parent.mkdir(parents=True, exist_ok=True)
        if src.resolve() != out.resolve():
            shutil.copy2(src, out)
        scene.image_filenames = [f"assets/{out.name}"]
        scene.asset_status = "reused_asset"
        scene.asset_error = ""
        scene.reused_asset = True
        scene.ai_images_reused += 1
        scene.asset_prompt_hash = prompt_hash
        scene.reused_from_job_id = str(cached.get("source_job_id") or "")
        scene.reused_from_job = scene.reused_from_job_id
        scene.reused_from_scene = int(cached.get("source_scene_index") or 0)
        scene.reused_asset_path = str(cached.get("source_asset_path") or src)
        scene.reused_asset_prompt_hash_mismatch = bool(prompt_hash_mismatch)
        scene.reuse_mode = reuse_mode
        scene.reuse_assets_mode = (
            "loose" if reuse_mode == "loose_debug" else reuse_mode
        )
        scene.reuse_prompt_match = not prompt_hash_mismatch
        scene.reuse_mismatch_allowed = reuse_mode == "loose_debug"
        scene.skip_image_generation = _skip_image_generation()
        scene.provider_request_count_for_scene = 0
        scene.actual_cloudflare_request_count_for_scene = 0
        plan.reused_asset = True
        plan.reused_from_job_id = scene.reused_from_job_id
        plan.reused_asset_prompt_hash_mismatch = (
            plan.reused_asset_prompt_hash_mismatch or bool(prompt_hash_mismatch)
        )
        plan.reuse_mode = reuse_mode
        plan.ai_images_reused += 1
        _finalize_minimalist_scene_metadata(
            scene,
            visual_mode="ai_scene" if plan.theme == "minimalist_emotional" else scene.visual_mode,
            provider=str(cached.get("image_provider") or "cloudflare"),
        )
        _set_image_source_metadata(
            scene,
            image_source="reused_asset",
            image_provider=str(cached.get("image_provider") or "cloudflare"),
            asset_path=out,
            job_dir=job_dir,
            used_local_fallback=False,
        )
        _record_asset(scene, out)
        logger.info(
            "scene %02d reused asset from job=%s source_scene=%02d mode=%s "
            "prompt_match=%s provider_requests=0 source=%s",
            scene.scene_index,
            scene.reused_from_job_id,
            scene.reused_from_scene,
            scene.reuse_assets_mode,
            str(scene.reuse_prompt_match).lower(),
            src,
        )
        return True

    def _try_reuse_asset(scene, prompt_hash: str, out: Path) -> bool:
        if not reuse_index:
            return False
        cached = reuse_index.get((scene.scene_index, prompt_hash))
        if not cached:
            return False
        return _apply_reused_asset(
            scene,
            cached,
            out,
            prompt_hash=prompt_hash,
            reuse_mode="strict",
            prompt_hash_mismatch=False,
        )

    def _try_loose_reuse_asset(scene, prompt_hash: str, out: Path) -> bool:
        if not loose_reuse_index:
            return False
        cached = loose_reuse_index.get(scene.scene_index)
        if not cached:
            return False
        return _apply_reused_asset(
            scene,
            cached,
            out,
            prompt_hash=prompt_hash,
            reuse_mode="loose_debug",
            prompt_hash_mismatch=str(cached.get("source_prompt_hash") or "") != prompt_hash,
        )

    def _raise_minimalist_provider_error(scene, exc: Exception) -> None:
        error_type = getattr(exc, "error_type", "") or "provider_failed"
        message = str(exc)[:500]
        if scene.ai_provider_error_type != error_type or scene.ai_provider_error_message != message:
            error_type = _record_ai_provider_error(scene, exc)
        plan.ai_provider_error_type = error_type
        plan.ai_provider_error_message = scene.ai_provider_error_message
        plan.ai_provider_recoverable = scene.ai_provider_recoverable
        plan.content_policy_blocked_count = sum(
            int(getattr(s, "content_policy_blocked_count", 0))
            for s in plan.scenes
            if s.kind == "scene"
        )
        theme_label = str(plan.theme)
        provider_message = scene.ai_provider_error_message[:180]
        if error_type == "image_request_budget_exhausted":
            raise RuntimeError(message) from exc
        if error_type == "quota_exhausted":
            raise RuntimeError(
                "Cloudflare AI quota exhausted. No AI images were generated. "
                "Local fallback is disabled for production-quality "
                f"{theme_label} renders. Original AI provider error: "
                f"{provider_message}"
            ) from exc
        if error_type == "content_policy_blocked":
            if "no third request was made" in message.lower():
                raise RuntimeError(message) from exc
            raise RuntimeError(
                "Cloudflare AI content policy blocked a harmless scene prompt "
                "after provider-safe sanitation or retry. No placeholder video was rendered. "
                "Local fallback is disabled for production-quality "
                f"{theme_label} renders. Inspect plan.json for "
                "original_prompt_summary and sanitized_prompt_summary. "
                f"Original AI provider error: {provider_message}"
            ) from exc
        raise RuntimeError(
            "Cloudflare AI image generation failed. Local fallback is disabled "
            f"for production-quality {theme_label} renders. "
            f"Provider error type: {error_type}. Original AI provider error: "
            f"{provider_message}"
        ) from exc

    async def _fallback_to_stock_photo(scene, base: str) -> None:
        """Last-resort fetch when the primary provider fails. Pexels Photo
        is the safest fallback — no NSFW safety filter false-positives,
        no per-account quota that resets only daily.
        """
        _assert_provider_submission_allowed()
        out = assets_dir / f"{base}_fallback.jpg"
        if _ai_image_stock_fallback_forbidden(plan):
            provider_message = (
                scene.ai_provider_error_message
                or scene.asset_error
                or "unknown AI provider failure"
            )
            raise RuntimeError(
                "Stock photo fallback is disabled for "
                f"{plan.theme} with --media ai_image. Original AI provider "
                f"error: {provider_message[:180]}"
            )
        if _stock_fallback_disabled():
            raise RuntimeError(
                "stock fallback disabled by TELLA_DISABLE_STOCK_FALLBACK=1"
            )
        query = (
            scene.stock_query
            or scene.image_prompt[:60]
            or scene.title[:60]
            or "abstract"
        )
        await stock_photo.search_and_download(
            query, out, width=width, height=height,
        )
        scene.image_filenames = [f"assets/{out.name}"]
        scene.asset_status = scene.asset_status or "done"
        scene.image_source = "fallback"
        scene.image_provider = "pexels"
        scene.used_local_fallback = False
        scene.asset_path = f"assets/{out.name}"
        _record_asset(scene, out)
        logger.warning(
            "scene %d: AI image failed → fell through to Pexels Photo (query=%r)",
            scene.scene_index, query,
        )

    async def _one(scene_idx: int, scene) -> None:
        async with sem:
            base = f"scene_{scene.scene_index:02d}_{_safe_stem(scene.title)}"
            if plan.media_source == "ai_image":
                out = assets_dir / f"{base}.jpg"
                original_prompt_for_cf = scene.image_prompt
                prompt_for_cf = original_prompt_for_cf
                if plan.theme == "minimalist_emotional":
                    prompt_for_cf = _minimalist_provider_prompt(scene)
                elif plan.theme == "minimalist_symbolic_reel":
                    prompt_for_cf = _cloudflare_safe_symbolic_prompt(scene)
                prompt_hash = _asset_prompt_hash(
                    prompt_for_cf,
                    width=width,
                    height=height,
                    seed=_seed_for_scene(plan, scene) if plan.theme == "minimalist_emotional" else _VIDEO_SEED,
                )
                scene.asset_prompt_hash = prompt_hash
                if plan.theme == "minimalist_symbolic_reel":
                    scene.provider_prompt_initial = prompt_for_cf
                    scene.provider_prompt_initial_hash = _provider_prompt_hash(
                        prompt_for_cf
                    )
                    scene.provider_prompt_retry = ""
                    scene.provider_prompt_retry_hash = ""
                    scene.provider_prompt_stage_used = ""
                    scene.content_policy_retry_used = False
                    scene.content_policy_attempt_count = 0
                    scene.actual_cloudflare_request_count_for_scene = 0
                    scene.last_cloudflare_policy_code = 0
                    scene.original_prompt_hash = scene.original_prompt_hash or _asset_prompt_hash(
                        original_prompt_for_cf,
                        width=width,
                        height=height,
                        seed=_VIDEO_SEED,
                    )
                    scene.original_prompt_summary = scene.original_prompt_summary or _prompt_summary(
                        original_prompt_for_cf,
                        max_len=500,
                    )
                    scene.sanitized_prompt_hash = prompt_hash
                    scene.sanitized_prompt_used = prompt_for_cf
                    scene.sanitized_prompt_summary = _prompt_summary(
                        prompt_for_cf,
                        max_len=500,
                    )
                if plan.theme == "minimalist_emotional":
                    scene.original_prompt_hash = scene.original_prompt_hash or prompt_hash
                    scene.original_prompt_summary = scene.original_prompt_summary or _prompt_summary(
                        prompt_for_cf,
                        max_len=500,
                    )
                if _try_reuse_asset(scene, prompt_hash, out):
                    scene.prompt_used = prompt_for_cf
                    return
                if (
                    plan.reuse_mode == "loose_debug"
                    and _try_loose_reuse_asset(scene, prompt_hash, out)
                ):
                    scene.prompt_used = prompt_for_cf
                    return
                if skip_image_generation:
                    scene.ai_provider_error_type = "image_generation_skipped"
                    scene.ai_provider_error_message = (
                        "skip image generation enabled and no reusable asset was resolved"
                    )
                    scene.ai_provider_recoverable = False
                    scene.asset_status = "ai_provider_failed"
                    plan.ai_provider_error_type = scene.ai_provider_error_type
                    plan.ai_provider_error_message = scene.ai_provider_error_message
                    plan.ai_provider_recoverable = scene.ai_provider_recoverable
                    raise _skip_image_generation_reuse_error(
                        job_dir,
                        scene,
                        prompt_hash=prompt_hash,
                        reuse_mode=(
                            "loose"
                            if plan.reuse_mode == "loose_debug"
                            else plan.reuse_mode
                        ),
                        index_had_candidates=bool(
                            reuse_index or loose_reuse_index
                        ),
                    )
                try:
                    if plan.theme == "minimalist_symbolic_reel":
                        await _generate_symbolic_image_with_qc(
                            scene,
                            prompt_for_cf,
                            out,
                            base,
                        )
                    else:
                        await _generate_ai_image(
                            scene,
                            prompt_for_cf,
                            out,
                            # Other themes keep one video seed for continuity.
                            # Minimalist emotional gets a deterministic per-scene
                            # seed because prompt collapse showed up as duplicate
                            # images; the strict character/style lock carries
                            # identity consistency for that theme.
                            seed=_seed_for_scene(plan, scene),
                        )
                    scene.image_filenames = [f"assets/{out.name}"]
                    scene.asset_status = "done"
                    scene.asset_error = ""
                    if plan.theme == "minimalist_emotional":
                        _finalize_minimalist_scene_metadata(
                            scene,
                            visual_mode="ai_scene",
                            provider="cloudflare",
                        )
                        _set_image_source_metadata(
                            scene,
                            image_source="ai_image_provider",
                            image_provider="cloudflare",
                            asset_path=out,
                            job_dir=job_dir,
                            used_local_fallback=False,
                        )
                        scene.prompt_used = prompt_for_cf
                    elif plan.theme == "minimalist_symbolic_reel":
                        _finalize_minimalist_scene_metadata(
                            scene,
                            visual_mode="symbolic_listicle",
                            provider="cloudflare",
                        )
                        _set_image_source_metadata(
                            scene,
                            image_source="ai_image_provider",
                            image_provider="cloudflare",
                            asset_path=out,
                            job_dir=job_dir,
                            used_local_fallback=False,
                        )
                        scene.prompt_used = scene.prompt_used or prompt_for_cf
                    _record_asset(scene, out)
                except _SymbolicQCFailure:
                    raise
                except Exception as exc:
                    scene.asset_error = str(exc)[:300]
                    if plan.theme == "minimalist_emotional":
                        _record_ai_provider_error(scene, exc)
                        plan.content_policy_blocked_count = sum(
                            int(getattr(s, "content_policy_blocked_count", 0))
                            for s in plan.scenes
                            if s.kind == "scene"
                        )
                        if _is_nsfw_prompt_rejection(exc):
                            sanitized_prompt = _cloudflare_safe_minimalist_prompt(scene)
                            sanitized_out = assets_dir / f"{base}_safe.jpg"
                            sanitized_hash = _asset_prompt_hash(
                                sanitized_prompt,
                                width=width,
                                height=height,
                                seed=_seed_for_scene(plan, scene) + 17,
                            )
                            scene.nsfw_retry_attempted = True
                            scene.original_prompt_hash = scene.original_prompt_hash or prompt_hash
                            scene.original_prompt_summary = scene.original_prompt_summary or _prompt_summary(
                                prompt_for_cf,
                                max_len=500,
                            )
                            scene.sanitized_prompt_hash = sanitized_hash
                            scene.sanitized_prompt_used = sanitized_prompt
                            scene.sanitized_prompt_summary = _prompt_summary(
                                sanitized_prompt,
                                max_len=500,
                            )
                            scene.asset_prompt_hash = sanitized_hash
                            if _try_reuse_asset(scene, sanitized_hash, sanitized_out):
                                scene.prompt_used = sanitized_prompt
                                scene.nsfw_retry_succeeded = True
                                return
                            logger.warning(
                                "scene %d: CF content policy 3030 -> retry safe prompt "
                                "setting=%s action=%s original=%r sanitized=%r",
                                scene.scene_index,
                                scene.scene_setting,
                                scene.scene_action,
                                scene.original_prompt_summary,
                                scene.sanitized_prompt_summary,
                            )
                            try:
                                await _generate_ai_image(
                                    scene,
                                    sanitized_prompt,
                                    sanitized_out,
                                    seed=_seed_for_scene(plan, scene) + 17,
                                )
                                scene.image_filenames = [
                                    f"assets/{sanitized_out.name}"
                                ]
                                scene.asset_status = "sanitized_retry"
                                scene.asset_error = ""
                                scene.nsfw_retry_succeeded = True
                                _finalize_minimalist_scene_metadata(
                                    scene,
                                    visual_mode="ai_scene",
                                    provider="cloudflare",
                                )
                                _set_image_source_metadata(
                                    scene,
                                    image_source="ai_image_provider",
                                    image_provider="cloudflare",
                                    asset_path=sanitized_out,
                                    job_dir=job_dir,
                                    used_local_fallback=False,
                                )
                                scene.prompt_used = sanitized_prompt
                                _record_asset(scene, sanitized_out)
                                logger.info(
                                    "scene %d: sanitized content-policy retry succeeded",
                                    scene.scene_index,
                                )
                                return
                            except Exception as retry_exc:
                                scene.asset_error = str(retry_exc)[:300]
                                _record_ai_provider_error(scene, retry_exc)
                                plan.content_policy_blocked_count = sum(
                                    int(getattr(s, "content_policy_blocked_count", 0))
                                    for s in plan.scenes
                                    if s.kind == "scene"
                                )
                                logger.warning(
                                    "scene %d: sanitized content-policy retry failed "
                                    "setting=%s action=%s sanitized=%r error=%s",
                                    scene.scene_index,
                                    scene.scene_setting,
                                    scene.scene_action,
                                    scene.sanitized_prompt_summary,
                                    str(retry_exc)[:120],
                                )
                                if not local_fallback_allowed:
                                    _raise_minimalist_provider_error(scene, retry_exc)

                        if not local_fallback_allowed:
                            _raise_minimalist_provider_error(scene, exc)

                        fallback_out = assets_dir / f"{base}_fallback.jpg"
                        result = sprite_composer.compose_scene(
                            scene,
                            fallback_out,
                            width,
                            height,
                            ai_fallback_state,
                        )
                        scene.image_filenames = [f"assets/{fallback_out.name}"]
                        scene.asset_status = "abstract_fallback"
                        scene.asset_hash = result.asset_hash
                        scene.ai_provider_error_type = scene.ai_provider_error_type or "provider_failed"
                        scene.ai_provider_error_message = scene.ai_provider_error_message or scene.asset_error
                        plan.ai_provider_error_type = plan.ai_provider_error_type or scene.ai_provider_error_type
                        plan.ai_provider_error_message = plan.ai_provider_error_message or scene.ai_provider_error_message
                        plan.ai_provider_recoverable = scene.ai_provider_recoverable
                        plan.used_local_fallback = True
                        _finalize_minimalist_scene_metadata(
                            scene,
                            visual_mode="ai_scene",
                            provider="local_fallback",
                        )
                        _set_image_source_metadata(
                            scene,
                            image_source="fallback",
                            image_provider="local_composer",
                            asset_path=fallback_out,
                            job_dir=job_dir,
                            used_local_fallback=True,
                        )
                        _record_asset(scene, fallback_out)
                        return

                    if plan.theme == "minimalist_symbolic_reel":
                        _record_ai_provider_error(scene, exc)
                        if not local_fallback_allowed:
                            _raise_minimalist_provider_error(scene, exc)

                        fallback_out = assets_dir / f"{base}_fallback.jpg"
                        result = sprite_composer.compose_scene(
                            scene,
                            fallback_out,
                            width,
                            height,
                            ai_fallback_state,
                        )
                        scene.image_filenames = [f"assets/{fallback_out.name}"]
                        scene.asset_status = "abstract_fallback"
                        scene.asset_hash = result.asset_hash
                        plan.ai_provider_error_type = scene.ai_provider_error_type
                        plan.ai_provider_error_message = scene.ai_provider_error_message
                        plan.ai_provider_recoverable = scene.ai_provider_recoverable
                        plan.used_local_fallback = True
                        _finalize_minimalist_scene_metadata(
                            scene,
                            visual_mode="symbolic_listicle",
                            provider="local_fallback",
                        )
                        _set_image_source_metadata(
                            scene,
                            image_source="fallback",
                            image_provider="local_composer",
                            asset_path=fallback_out,
                            job_dir=job_dir,
                            used_local_fallback=True,
                        )
                        _record_asset(scene, fallback_out)
                        return

                    # Either daily neuron quota burned across every CF
                    # account, or the safety filter false-positived a
                    # specific scene's prompt. Either way, Pexels Photo
                    # always works — fall through so the user still gets
                    # a complete video instead of "all 5 accounts failed".
                    logger.warning(
                        "scene %d: AI image failed (%s) → fallback to Pexels",
                        scene.scene_index, str(exc)[:120],
                    )
                    await _fallback_to_stock_photo(scene, base)
            elif plan.media_source == "stock_photo":
                _assert_provider_submission_allowed()
                out = assets_dir / f"{base}.jpg"
                await stock_photo.search_and_download(
                    scene.stock_query or scene.image_prompt[:60],
                    out,
                    width=width,
                    height=height,
                )
                scene.image_filenames = [f"assets/{out.name}"]
                scene.asset_status = "done"
                scene.asset_error = ""
                _record_asset(scene, out)
            elif plan.media_source == "stock_video":
                _assert_provider_submission_allowed()
                out = assets_dir / f"{base}.mp4"
                try:
                    final = await stock_video.search_and_download(
                        scene.stock_query or scene.image_prompt[:60],
                        out,
                        width=width,
                        height=height,
                    )
                    scene.image_filenames = [f"assets/{final.name}"]
                    scene.asset_status = "done"
                    scene.asset_error = ""
                    _record_asset(scene, final)
                except Exception as exc:
                    scene.asset_error = str(exc)[:300]
                    logger.warning(
                        "scene %d: stock video failed (%s) → fallback to Pexels Photo",
                        scene.scene_index, str(exc)[:120],
                    )
                    await _fallback_to_stock_photo(scene, base)
            else:
                raise RuntimeError(
                    f"unknown media_source {plan.media_source!r}"
                )

    logger.info(
        "fetch_assets: %d scenes, source=%s, %dx%d",
        len(body_scenes), plan.media_source, width, height,
    )
    try:
        await asyncio.gather(*[_one(i, s) for i, s in enumerate(body_scenes)])
    finally:
        request_budget.sync_finish_metadata()
    logger.info("fetch_assets: all %d scenes done", len(body_scenes))


__all__ = [
    "MAX_CONCURRENT",
    "fetch_assets",
]
