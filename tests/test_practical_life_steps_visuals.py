import asyncio
import json
import re
from pathlib import Path

import pytest
from PIL import Image

import tella.cli as cli
from tella.media import ai_image, fetch
from tella.planner.practical_life_steps import plan_practical_life_steps_from_script
from tella.planner.practical_life_steps_visuals import build_practical_provider_prompt
from tella.recipes import get_recipe
from tella.render.pipeline import (
    _apply_image_grade,
    _build_bg_filter,
    _practical_motion_profile,
    _resolve_font_file,
)
from tella.render.text_overlay import (
    _REEL_MINIMAL_CAPTION_CENTER_Y_RATIO,
    _REEL_MINIMAL_CAPTION_SAFE_BOTTOM_RATIO,
    _REEL_MINIMAL_CAPTION_SAFE_TOP_RATIO,
    practical_step_badge_layout,
    practical_step_badge_text,
)
from tella.subtitles import subtitle_text_for_style
from tella.themes.loader import load_theme


_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = _ROOT / "script_practical_life_steps_test.txt"


def _plan():
    return plan_practical_life_steps_from_script(
        user_script=_FIXTURE.read_text(encoding="utf-8"),
        target_lang="vi",
    )


def _step_preview_plan():
    plan = _plan()
    plan.scenes = plan.scenes[2:5]
    return plan


def _clear_fetch_env(monkeypatch):
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
        "TELLA_DISABLE_STOCK_FALLBACK",
    ):
        monkeypatch.delenv(name, raising=False)


def test_practical_theme_resolves_with_distinct_palette_and_profiles():
    recipe = get_recipe("practical_life_steps_v1")
    theme = load_theme(recipe.visual_theme_id)

    assert theme.name == "practical_life_steps"
    assert theme.color_palette.bg == "#eef0e7"
    assert theme.color_palette.primary == "#5b7f76"
    assert theme.color_palette.accent == "#df8668"
    assert theme.transition == "cut"
    assert recipe.subtitle_style_id == "practical_steps_reel"
    assert recipe.transition_profile_id == "clean_progressive_cut"
    assert recipe.motion_profile_id == "gentle_progressive_motion"


def test_planner_routes_validated_visual_metadata_without_reinferring_narration():
    plan = _plan()
    steps = [scene for scene in plan.scenes if scene.step_number]

    assert plan.visual_identity_id == "practical_life_steps_v1"
    assert plan.palette_id == "pale_sage_teal_coral_v1"
    assert plan.subtitle_style == "practical_steps_reel"
    assert all(scene.visual_mode == "practical_life_steps" for scene in plan.scenes)
    assert "writing one objective" in steps[0].provider_prompt_variant
    assert "phone" in steps[1].provider_prompt_variant
    assert "books and loose study papers" in steps[2].provider_prompt_variant
    assert [scene.subtitle_highlight_words for scene in steps] == [
        ["viết"],
        ["đặt"],
        ["chuẩn bị"],
    ]


def test_practical_compositions_are_deterministic_and_non_repeating():
    first = _plan()
    second = _plan()

    assert [scene.composition_pattern for scene in first.scenes] == [
        scene.composition_pattern for scene in second.scenes
    ]
    assert all(
        current.composition_pattern != previous.composition_pattern
        for previous, current in zip(first.scenes, first.scenes[1:])
    )


def test_provider_prompts_are_compact_positive_safe_and_wordless():
    forbidden = re.compile(
        r"\b(?:readable text|readable writing|readable numbers?|step labels?|"
        r"calendar writing|ui text|logos?|watermarks?|signs?|speech-bubble text)\b",
        flags=re.IGNORECASE,
    )
    for scene in _plan().scenes:
        prompt = build_practical_provider_prompt(scene)

        assert prompt == scene.provider_prompt_variant
        assert len(prompt) < 900
        assert "wordless unbranded artwork" in prompt.lower()
        assert forbidden.search(prompt) is None
        assert re.search(r"\b(?:no|avoid|forbidden|prohibited)\b", prompt.lower()) is None


def test_practical_subtitle_uses_existing_safe_zone_and_two_highlights_max():
    plan = _plan()

    assert _REEL_MINIMAL_CAPTION_CENTER_Y_RATIO == pytest.approx(0.79)
    assert _REEL_MINIMAL_CAPTION_SAFE_TOP_RATIO == pytest.approx(0.72)
    assert _REEL_MINIMAL_CAPTION_SAFE_BOTTOM_RATIO == pytest.approx(0.84)
    assert all(len(scene.subtitle_highlight_words) <= 2 for scene in plan.scenes)
    result = subtitle_text_for_style("\uFEFFHướng dẫn rõ ràng", "practical_steps_reel")
    assert result.text == "Hướng dẫn rõ ràng"


def test_step_badges_only_exist_for_three_practical_steps():
    plan = _plan()

    assert [
        practical_step_badge_text(
            plan.subtitle_style,
            scene.step_number if scene.scene_role == "practical_step" else 0,
        )
        for scene in plan.scenes
    ] == ["", "", "BƯỚC 1", "BƯỚC 2", "BƯỚC 3", "", ""]


def test_step_badge_layout_is_above_subtitle_region_and_inside_safe_zone():
    layout = practical_step_badge_layout(
        subtitle_style="practical_steps_reel",
        step_number=1,
        canvas_w=1080,
        safe_top=285,
        safe_left=90,
        font_file=_resolve_font_file(),
    )

    assert layout is not None
    assert int(layout["x"]) >= 90
    assert int(layout["y"]) >= 285
    assert int(layout["y"]) + int(layout["height"]) < int(1920 * 0.72)


def test_practical_motion_profiles_are_role_and_step_specific():
    plan = _plan()

    assert [_practical_motion_profile(scene) for scene in plan.scenes] == [
        "practical_zoom_in",
        "practical_pan_left_to_right",
        "practical_pan_left_to_right",
        "practical_pan_right_to_left",
        "practical_zoom_in",
        "practical_pull_back",
        "practical_stable_hold",
    ]
    pan = _build_bg_filter(
        is_video=False,
        canvas_w=1080,
        canvas_h=1920,
        duration=4.0,
        ken_burns_max_scale=1.025,
        motion_profile="practical_pan_right_to_left",
        scene_index=4,
    )
    pull = _build_bg_filter(
        is_video=False,
        canvas_w=1080,
        canvas_h=1920,
        duration=4.0,
        ken_burns_max_scale=1.025,
        motion_profile="practical_pull_back",
    )
    assert "0.65-0.30*on" in pan
    assert "max(1.025-" in pull


def test_practical_grade_is_distinct_and_never_mutates_source(tmp_path):
    source = tmp_path / "source.png"
    practical_out = tmp_path / "practical.png"
    life_out = tmp_path / "life.png"
    Image.new("RGB", (80, 120), "#d5d5ce").save(source)
    source_before = source.read_bytes()
    practical = load_theme("practical_life_steps").image_grade
    life = load_theme("life_insight_symbolic").image_grade

    _apply_image_grade(source, practical_out, canvas_w=80, canvas_h=120, grade=practical)
    _apply_image_grade(source, life_out, canvas_w=80, canvas_h=120, grade=life)

    assert practical.enabled is True
    assert practical.brightness == pytest.approx(1.02)
    assert practical.contrast == pytest.approx(1.04)
    assert practical.saturation == pytest.approx(0.96)
    assert practical.overlay_color == "#eef1e8"
    assert practical.overlay_opacity == pytest.approx(0.06)
    assert source.read_bytes() == source_before
    assert practical_out.read_bytes() != life_out.read_bytes()


def test_existing_emotional_and_life_themes_are_unchanged():
    emotional = load_theme("minimalist_symbolic_reel")
    life = load_theme("life_insight_symbolic")

    assert emotional.color_palette.bg == "#504845"
    assert emotional.transition == "crossfade"
    assert emotional.image_grade.brightness == pytest.approx(0.90)
    assert life.color_palette.bg == "#343b42"
    assert life.transition == "cut"
    assert life.image_grade.brightness == pytest.approx(0.93)


def test_three_step_provider_route_uses_shared_budget_without_qc(monkeypatch, tmp_path):
    _clear_fetch_env(monkeypatch)
    monkeypatch.setenv("TELLA_MAX_AI_IMAGES", "3")
    monkeypatch.setenv("TELLA_SCENE_QC", "off")
    monkeypatch.setenv("TELLA_DISABLE_STOCK_FALLBACK", "1")
    plan = _step_preview_plan()
    calls: list[str] = []

    async def fake_generate(prompt, out, **kwargs):
        await fetch.ai_image._notify_before_cloudflare_request()
        calls.append(prompt)
        Image.new("RGB", (32, 48), "#eef0e7").save(out)

    def fail_qc(*args, **kwargs):
        raise AssertionError("practical preview must not invoke symbolic vision QC")

    monkeypatch.setattr(fetch.ai_image, "generate_image", fake_generate)
    monkeypatch.setattr(fetch, "evaluate_scene_image", fail_qc)

    asyncio.run(fetch.fetch_assets(plan, tmp_path))

    assert calls == [scene.provider_prompt_variant for scene in plan.scenes]
    assert plan.ai_images_requested == 3
    assert plan.image_request_budget_max == 3
    assert plan.image_request_budget_used_at_finish == 3
    assert plan.total_vision_qc_calls == 0
    assert all(scene.image_provider == "cloudflare" for scene in plan.scenes)


def test_shared_retry_constant_and_budget_contract_are_unchanged():
    assert ai_image.MAX_RETRIES_PER_ACCOUNT == 3
    assert fetch.MAX_CONCURRENT == 3


def test_strict_matching_reuse_makes_zero_provider_calls(monkeypatch, tmp_path):
    _clear_fetch_env(monkeypatch)
    source_job = tmp_path / "source"
    current_job = tmp_path / "current"
    (source_job / "assets").mkdir(parents=True)
    plan = _step_preview_plan()
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
        Image.new("RGB", (32, 48), "#eef0e7").save(source_job / rel)
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
        raise AssertionError("provider called despite strict matching reuse")

    monkeypatch.setattr(fetch.ai_image, "generate_image", fail_provider)
    monkeypatch.setenv("TELLA_REUSE_ASSETS", "1")
    monkeypatch.setenv("TELLA_SKIP_IMAGE_GENERATION", "1")
    monkeypatch.setenv("TELLA_IMAGES_FROM_JOB", str(source_job))

    asyncio.run(fetch.fetch_assets(plan, current_job))

    assert plan.ai_images_requested == 0
    assert plan.ai_images_reused == 3
    assert plan.image_request_budget_used_at_finish == 0


def test_preview_indices_select_after_full_recipe_validation(monkeypatch, tmp_path):
    selected: list[int] = []

    async def fake_fetch(plan, job_dir):
        selected.extend(scene.scene_index for scene in plan.scenes)
        for scene in plan.scenes:
            scene.image_filenames = [f"assets/scene_{scene.scene_index:02d}.jpg"]

    async def fake_tts(plan, job_dir, **kwargs):
        plan.narration_audio_filename = "narration.mp3"
        plan.narration_duration = 9.0
        for scene in plan.scenes:
            scene.audio_duration = 3.0

    async def fake_render(plan, job_dir):
        return Path(job_dir) / "video.mp4"

    monkeypatch.setattr(cli, "fetch_assets", fake_fetch)
    monkeypatch.setattr(cli, "synthesize_all", fake_tts)
    monkeypatch.setattr(cli, "render", fake_render)

    result = asyncio.run(
        cli.run_pipeline(
            topic="exact script",
            target_lang="vi",
            theme="practical_life_steps",
            media_source="ai_image",
            duration_mode="short",
            aspect_ratio="9:16",
            voice_pace_name=None,
            voice_rate_custom="-2%",
            voice_gender="female",
            out_root=tmp_path,
            job_id="preview",
            user_script=_FIXTURE.read_text(encoding="utf-8"),
            preview_scene_indices=[3, 4, 5],
            recipe=get_recipe("practical_life_steps_v1"),
        )
    )

    data = json.loads((tmp_path / "preview" / "plan.json").read_text(encoding="utf-8"))
    assert result == tmp_path / "preview" / "video.mp4"
    assert selected == [3, 4, 5]
    assert data["recipe_validation_status"] == "passed"
    assert [scene["scene_index"] for scene in data["scenes"]] == [3, 4, 5]


def test_normal_cli_render_route_is_active_with_external_pipeline_mocked(
    monkeypatch,
    tmp_path,
):
    captured = {}

    async def fake_pipeline(**kwargs):
        captured.update(kwargs)
        return Path(kwargs["out_root"]) / kwargs["job_id"] / "video.mp4"

    def fail_external(*args, **kwargs):
        raise AssertionError("external subsystem was called outside mocked pipeline")

    monkeypatch.setattr(cli, "run_pipeline", fake_pipeline)
    monkeypatch.setattr(cli, "fetch_assets", fail_external)
    monkeypatch.setattr(cli, "synthesize_all", fail_external)
    monkeypatch.setattr(cli, "render", fail_external)

    result = cli.main(
        [
            "--script-file",
            str(_FIXTURE),
            "--recipe",
            "practical_life_steps_v1",
            "--lang",
            "vi",
            "--out",
            str(tmp_path),
            "--job-id",
            "active_route",
        ]
    )

    assert result == 0
    assert captured["theme"] == "practical_life_steps"
    assert captured["recipe"].recipe_id == "practical_life_steps_v1"
    assert captured["dry_run_plan"] is False


def test_direct_full_route_validates_seven_scenes_before_mocked_boundaries(
    monkeypatch,
    tmp_path,
):
    selected: list[int] = []

    async def fake_fetch(plan, job_dir):
        selected.extend(scene.scene_index for scene in plan.scenes)
        for scene in plan.scenes:
            scene.image_filenames = [f"assets/scene_{scene.scene_index:02d}.jpg"]

    async def fake_tts(plan, job_dir, **kwargs):
        plan.narration_audio_filename = "narration.mp3"
        plan.narration_duration = 35.0
        for scene in plan.scenes:
            scene.audio_duration = 5.0

    async def fake_render(plan, job_dir):
        return Path(job_dir) / "video.mp4"

    monkeypatch.setattr(cli, "fetch_assets", fake_fetch)
    monkeypatch.setattr(cli, "synthesize_all", fake_tts)
    monkeypatch.setattr(cli, "render", fake_render)

    result = asyncio.run(
        cli.run_pipeline(
            topic="exact script",
            target_lang="vi",
            theme="practical_life_steps",
            media_source="ai_image",
            duration_mode="short",
            aspect_ratio="9:16",
            voice_pace_name=None,
            voice_rate_custom="-2%",
            voice_gender="female",
            out_root=tmp_path,
            job_id="full_route",
            user_script=_FIXTURE.read_text(encoding="utf-8"),
            recipe=get_recipe("practical_life_steps_v1"),
        )
    )

    data = json.loads(
        (tmp_path / "full_route" / "plan.json").read_text(encoding="utf-8")
    )
    assert result == tmp_path / "full_route" / "video.mp4"
    assert selected == [1, 2, 3, 4, 5, 6, 7]
    assert data["recipe_validation_status"] == "passed"
    assert len(data["scenes"]) == 7
