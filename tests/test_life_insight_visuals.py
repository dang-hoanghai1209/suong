import asyncio
import json
import re
from pathlib import Path

import pytest
from PIL import Image

from tella.media import fetch
from tella.planner.life_insight import plan_life_insight_from_script
from tella.planner.life_insight_visuals import (
    LIFE_INSIGHT_SYMBOLIC_CATALOG,
    apply_life_insight_visuals,
    build_life_insight_provider_prompt,
)
from tella.planner.models import Scene, TellaScenePlan
from tella.recipes import get_recipe
from tella.render.pipeline import _apply_image_grade, _build_bg_filter
from tella.render.text_overlay import (
    _REEL_MINIMAL_CAPTION_CENTER_Y_RATIO,
    _REEL_MINIMAL_CAPTION_SAFE_BOTTOM_RATIO,
    _REEL_MINIMAL_CAPTION_SAFE_TOP_RATIO,
    _reel_minimal_caption_top_y,
)
from tella.subtitles import subtitle_text_for_style
from tella.themes.loader import load_theme


_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "script_life_insight_test.txt"


def _plan() -> TellaScenePlan:
    return plan_life_insight_from_script(
        user_script=_SCRIPT.read_text(encoding="utf-8"),
        target_lang="vi",
    )


def _preview_plan(count: int = 2) -> TellaScenePlan:
    plan = _plan()
    plan.scenes = plan.scenes[:count]
    return plan


def _clear_fetch_env(monkeypatch) -> None:
    for name in (
        "TELLA_ALLOW_LOCAL_IMAGE_FALLBACK",
        "TELLA_REUSE_ASSETS",
        "TELLA_REUSE_ASSETS_MODE",
        "TELLA_ALLOW_MISMATCHED_REUSED_ASSETS",
        "TELLA_SKIP_IMAGE_GENERATION",
        "TELLA_IMAGES_FROM_JOB",
        "TELLA_REUSE_PLAN_PATH",
        "TELLA_MAX_AI_IMAGES",
        "TELLA_SCENE_QC",
    ):
        monkeypatch.delenv(name, raising=False)


def test_recipe_and_theme_route_to_life_insight_visual_profiles():
    recipe = get_recipe("life_insight_symbolic_v1")
    theme = load_theme(recipe.visual_theme_id)

    assert theme.name == "life_insight_symbolic"
    assert recipe.subtitle_style_id == "insight_reel"
    assert recipe.transition_profile_id == "clean_soft_cut"
    assert recipe.motion_profile_id == "controlled_slow_pan"
    assert theme.transition == "cut"
    assert theme.color_palette.bg == "#343b42"
    assert theme.color_palette.accent == "#d58a45"


def test_planner_enriches_every_scene_with_deterministic_visual_metadata():
    first = _plan()
    second = _plan()

    fields = [
        (
            scene.visual_variant_id,
            scene.symbolic_visual,
            scene.main_character_or_object,
            scene.composition_pattern,
            scene.provider_prompt_variant,
        )
        for scene in first.scenes
    ]
    assert fields == [
        (
            scene.visual_variant_id,
            scene.symbolic_visual,
            scene.main_character_or_object,
            scene.composition_pattern,
            scene.provider_prompt_variant,
        )
        for scene in second.scenes
    ]
    assert first.visual_identity_id == "life_insight_symbolic_v1"
    assert first.palette_id == "blue_gray_charcoal_amber_v1"
    assert first.line_style_id == "clean_charcoal_editorial_v1"
    assert first.subtitle_style == "insight_reel"
    assert all(scene.visual_mode == "life_insight_symbolic" for scene in first.scenes)
    assert all(scene.cast_archetype == "adult_woman_or_man" for scene in first.scenes)
    assert all(scene.provider_prompt_variant for scene in first.scenes)


def test_adjacent_scenes_do_not_repeat_composition_or_catalog_variant():
    scenes = _plan().scenes

    assert all(
        current.composition_pattern != previous.composition_pattern
        for previous, current in zip(scenes, scenes[1:])
    )
    assert all(
        current.visual_variant_id != previous.visual_variant_id
        for previous, current in zip(scenes, scenes[1:])
    )


def test_catalog_covers_general_life_insight_symbol_families():
    assert set(LIFE_INSIGHT_SYMBOLIC_CATALOG) == {
        "locked_question",
        "interpreting_signals",
        "contrasting_behavior",
        "revealed_truth",
        "messages_and_thoughts",
        "time_and_priority",
        "uncertain_position",
        "disappearing_presence",
        "boundary_path_scale",
        "mirror_standard",
    }


def test_provider_prompts_are_compact_positive_and_wordless():
    for scene in _plan().scenes:
        prompt = build_life_insight_provider_prompt(scene)
        lower = prompt.lower()

        assert prompt == scene.provider_prompt_variant
        assert len(prompt) < 700
        assert "wordless unbranded artwork" in lower
        assert re.search(r"\b(no|without|avoid|forbidden|prohibited)\b", lower) is None
        assert not any(word in lower for word in ("caption", "lettering", "watermark", "logo"))


def test_emotional_symbolic_theme_and_plan_are_unchanged():
    emotional_theme = load_theme("minimalist_symbolic_reel")
    emotional = TellaScenePlan(
        title="Emotional baseline",
        language="vi",
        aspect_ratio="9:16",
        media_source="ai_image",
        duration_mode="short",
        theme="minimalist_symbolic_reel",
        subtitle_style="reel_minimal",
        scenes=[
            Scene(
                scene_index=index,
                title=f"Scene {index}",
                voice_script=f"Caption {index}",
                image_prompt=f"Prompt {index}",
                scene_meaning=f"Meaning {index}",
                symbolic_visual=f"Visual {index}",
            )
            for index in range(1, 4)
        ],
    )
    before = emotional.model_dump()

    assert apply_life_insight_visuals(emotional) is emotional
    assert emotional.model_dump() == before
    assert emotional_theme.color_palette.bg == "#504845"
    assert emotional_theme.transition == "crossfade"
    assert emotional_theme.ken_burns.end_scale == pytest.approx(1.04)


def test_life_grading_is_recipe_specific_and_preserves_source(tmp_path):
    source = tmp_path / "source.png"
    life_out = tmp_path / "life.png"
    emotional_out = tmp_path / "emotional.png"
    Image.new("RGB", (80, 120), "#c8c8c8").save(source)
    source_before = source.read_bytes()
    life_grade = load_theme("life_insight_symbolic").image_grade
    emotional_grade = load_theme("minimalist_symbolic_reel").image_grade

    _apply_image_grade(source, life_out, canvas_w=80, canvas_h=120, grade=life_grade)
    _apply_image_grade(
        source,
        emotional_out,
        canvas_w=80,
        canvas_h=120,
        grade=emotional_grade,
    )

    assert source.read_bytes() == source_before
    assert life_out.read_bytes() != emotional_out.read_bytes()
    with Image.open(life_out) as image:
        red, green, blue = image.getpixel((40, 60))
    assert blue >= green >= red


def test_insight_subtitles_keep_existing_safe_zone_and_sanitation():
    top = _reel_minimal_caption_top_y(
        canvas_h=1920,
        block_h=104,
        safe_top=285,
        safe_bottom=1635,
    )
    result = subtitle_text_for_style("\uFEFF\u200BNoi dung", "insight_reel")

    assert _REEL_MINIMAL_CAPTION_CENTER_Y_RATIO == pytest.approx(0.79)
    assert _REEL_MINIMAL_CAPTION_SAFE_TOP_RATIO == pytest.approx(0.72)
    assert _REEL_MINIMAL_CAPTION_SAFE_BOTTOM_RATIO == pytest.approx(0.84)
    assert top >= int(1920 * 0.72)
    assert top + 104 <= int(1920 * 0.84)
    assert result.text == "Noi dung"


def test_motion_profile_routes_to_controlled_pan_without_changing_default_zoom():
    controlled = _build_bg_filter(
        is_video=False,
        canvas_w=1080,
        canvas_h=1920,
        duration=4.0,
        ken_burns_max_scale=1.025,
        motion_profile="controlled_slow_pan",
        scene_index=1,
    )
    legacy = _build_bg_filter(
        is_video=False,
        canvas_w=1080,
        canvas_h=1920,
        duration=4.0,
        ken_burns_max_scale=1.04,
    )

    assert "0.35+0.30*on" in controlled
    assert "iw/2-(iw/zoom/2)" in legacy
    assert "0.35+0.30*on" not in legacy


def test_provider_variant_and_shared_two_request_budget_are_used(
    monkeypatch,
    tmp_path,
):
    _clear_fetch_env(monkeypatch)
    monkeypatch.setenv("TELLA_MAX_AI_IMAGES", "2")
    monkeypatch.setenv("TELLA_SCENE_QC", "off")
    plan = _preview_plan()
    calls: list[str] = []

    async def fake_generate(prompt, out, **kwargs):
        await fetch.ai_image._notify_before_cloudflare_request()
        calls.append(prompt)
        Image.new("RGB", (32, 48), "#4d5860").save(out)

    def fail_qc(*args, **kwargs):
        raise AssertionError("life insight preview must not invoke symbolic image QC")

    monkeypatch.setattr(fetch.ai_image, "generate_image", fake_generate)
    monkeypatch.setattr(fetch, "evaluate_scene_image", fail_qc)

    asyncio.run(fetch.fetch_assets(plan, tmp_path))

    assert calls == [scene.provider_prompt_variant for scene in plan.scenes]
    assert plan.ai_images_requested == 2
    assert plan.image_request_budget_max == 2
    assert plan.image_request_budget_used_at_finish == 2
    assert all(scene.actual_cloudflare_request_count_for_scene == 1 for scene in plan.scenes)
    assert all(scene.image_provider == "cloudflare" for scene in plan.scenes)


def test_matching_reuse_makes_zero_provider_calls(monkeypatch, tmp_path):
    _clear_fetch_env(monkeypatch)
    source_job = tmp_path / "source"
    current_job = tmp_path / "current"
    (source_job / "assets").mkdir(parents=True)
    plan = _preview_plan()
    width, height = fetch._GEN_DIMS["9:16"]
    records = []
    for scene in plan.scenes:
        prompt_hash = fetch._asset_prompt_hash(
            scene.provider_prompt_variant,
            width=width,
            height=height,
            seed=fetch._VIDEO_SEED,
        )
        rel = f"assets/scene_{scene.scene_index:02d}.jpg"
        Image.new("RGB", (32, 48), "#4d5860").save(source_job / rel)
        records.append(
            {
                "scene_index": scene.scene_index,
                "kind": "scene",
                "asset_prompt_hash": prompt_hash,
                "asset_path": rel,
                "image_source": "ai_image_provider",
                "image_provider": "cloudflare",
                "asset_status": "done",
                "used_local_fallback": False,
            }
        )
    (source_job / "plan.json").write_text(
        json.dumps({"scenes": records}, indent=2),
        encoding="utf-8",
    )

    async def fail_provider(*args, **kwargs):
        raise AssertionError("provider called despite a matching reusable asset")

    monkeypatch.setattr(fetch.ai_image, "generate_image", fail_provider)
    monkeypatch.setenv("TELLA_REUSE_ASSETS", "1")
    monkeypatch.setenv("TELLA_SKIP_IMAGE_GENERATION", "1")
    monkeypatch.setenv("TELLA_IMAGES_FROM_JOB", str(source_job))

    asyncio.run(fetch.fetch_assets(plan, current_job))

    assert plan.ai_images_requested == 0
    assert plan.image_request_budget_used_at_finish == 0
    assert plan.ai_images_reused == 2
    assert all(scene.reused_asset for scene in plan.scenes)
    assert all(scene.provider_request_count_for_scene == 0 for scene in plan.scenes)
