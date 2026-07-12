import asyncio
import json
import re
import socket
from pathlib import Path

import pytest
from PIL import Image

import tella.cli as cli
from tella.composer.compose import compose_timing
from tella.media import ai_image, fetch
from tella.planner.practical_life_steps import plan_practical_life_steps_from_script
from tella.planner.practical_life_steps_visuals import build_practical_provider_prompt
from tella.recipes import apply_recipe_metadata, get_recipe
from tella.render.pipeline import (
    _apply_image_grade,
    _build_bg_filter,
    _practical_motion_profile,
    _render_progress_message,
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
from tella.tts import edge
from tella.tts import duration_fit


_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = _ROOT / "script_practical_life_steps_test.txt"


def _plan():
    return plan_practical_life_steps_from_script(
        user_script=_FIXTURE.read_text(encoding="utf-8"),
        target_lang="vi",
    )


def _step_preview_plan():
    plan = _plan()
    apply_recipe_metadata(
        plan,
        get_recipe("practical_life_steps_v1"),
        validation_status="passed",
    )
    plan.scenes = plan.scenes[2:5]
    return plan


def _write_practical_reuse_source(source_job, plan, scene_indices):
    (source_job / "assets").mkdir(parents=True)
    width, height = fetch._GEN_DIMS["9:16"]
    records = []
    for scene in plan.scenes:
        if scene.scene_index not in scene_indices:
            continue
        prompt = scene.provider_prompt_variant
        prompt_hash = fetch._asset_prompt_hash(
            prompt,
            width=width,
            height=height,
            seed=fetch._VIDEO_SEED,
        )
        rel = f"assets/scene_{scene.scene_index:02d}.jpg"
        asset = source_job / rel
        Image.new("RGB", (32, 48), "#eef0e7").save(asset)
        records.append(
            {
                "scene_index": scene.scene_index,
                "kind": "scene",
                "scene_role": scene.scene_role,
                "step_number": scene.step_number,
                "visual_action": scene.visual_action,
                "provider_prompt_initial_hash": fetch._provider_prompt_hash(prompt),
                "asset_prompt_hash": prompt_hash,
                "asset_hash": fetch._sha256_short(asset),
                "asset_path": rel,
                "image_source": "ai_image_provider",
                "image_provider": "cloudflare",
                "asset_status": "done",
                "used_local_fallback": False,
            }
        )
    (source_job / "plan.json").write_text(
        json.dumps(
            {
                "recipe_id": plan.recipe_id,
                "recipe_version": plan.recipe_version,
                "aspect_ratio": plan.aspect_ratio,
                "visual_theme_id": plan.visual_theme_id,
                "theme": plan.theme,
                "scenes": records,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


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
        "TELLA_AI_IMAGE_SEQUENTIAL",
        "TELLA_CF_MAX_ACCOUNTS",
        "TELLA_CF_MAX_RETRIES_PER_ACCOUNT",
        "TELLA_EDGE_MAX_RETRIES",
        "TELLA_REQUIRE_REUSED_SCENE_INDICES",
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


@pytest.mark.parametrize(
    ("recipe_id", "theme", "voice", "subtitle", "transition", "motion"),
    [
        (
            "emotional_symbolic_v1",
            "minimalist_symbolic_reel",
            "soft_female_vi",
            "reel_minimal",
            "subtle_crossfade",
            "slow_ken_burns",
        ),
        (
            "life_insight_symbolic_v1",
            "life_insight_symbolic",
            "firm_male_vi",
            "insight_reel",
            "clean_soft_cut",
            "controlled_slow_pan",
        ),
        (
            "practical_life_steps_v1",
            "practical_life_steps",
            "clear_female_vi",
            "practical_steps_reel",
            "clean_progressive_cut",
            "gentle_progressive_motion",
        ),
    ],
)
def test_provider_bounds_do_not_change_recipe_defaults(
    recipe_id,
    theme,
    voice,
    subtitle,
    transition,
    motion,
):
    recipe = get_recipe(recipe_id)

    assert recipe.visual_theme_id == theme
    assert recipe.voice_profile_id == voice
    assert recipe.subtitle_style_id == subtitle
    assert recipe.transition_profile_id == transition
    assert recipe.motion_profile_id == motion


def test_provider_bounds_add_no_cli_flags_and_leave_ordinary_defaults():
    args = cli.build_arg_parser().parse_args(
        [
            "--script-file",
            str(_FIXTURE),
            "--recipe",
            "practical_life_steps_v1",
            "--lang",
            "vi",
        ]
    )

    assert args.media_source == "ai_image"
    assert args.aspect == "9:16"
    assert args.reuse_assets is False
    assert args.allow_local_image_fallback is False
    assert not any(
        action.dest
        in {
            "cf_max_accounts",
            "cf_max_retries_per_account",
            "edge_max_retries",
            "ai_image_sequential",
        }
        for action in cli.build_arg_parser()._actions
    )


def test_render_progress_reports_original_index_and_execution_order():
    message = _render_progress_message(4, 2, 3, 4.25)

    assert message == (
        "rendered scene original_scene=04 execution=2/3 "
        "(4.25s, video-only)"
    )
    assert "rendered scene 4/3" not in message


def test_practical_duration_fit_uses_actual_audio_and_preserves_original(
    monkeypatch,
    tmp_path,
):
    plan = _plan()
    apply_recipe_metadata(
        plan,
        get_recipe("practical_life_steps_v1"),
        validation_status="passed",
    )
    source = tmp_path / "assets" / "narration.mp3"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"original-edge-audio")
    plan.narration_audio_path = str(source)
    plan.narration_audio_filename = "assets/narration.mp3"
    for index, scene in enumerate(plan.scenes, start=1):
        scene.audio_duration = float(index)
    durations = iter((28.465, 32.5, 32.5))

    async def fake_probe(path):
        return next(durations)

    async def fake_atempo(source_path, destination, tempo):
        destination.write_bytes(source_path.read_bytes() + b"-fitted")

    monkeypatch.setattr(duration_fit, "probe_duration", fake_probe)
    monkeypatch.setattr(duration_fit, "_run_atempo", fake_atempo)

    asyncio.run(duration_fit.reconcile_practical_narration_duration(plan, tmp_path))
    compose_timing(plan)

    assert plan.planner_estimated_duration_seconds == pytest.approx(37.77)
    assert plan.original_narration_duration_seconds == pytest.approx(28.465)
    assert plan.duration_fit_target_seconds == pytest.approx(32.5)
    assert plan.duration_fit_tempo == pytest.approx(28.465 / 32.5)
    assert plan.duration_fit_scale == pytest.approx(32.5 / 28.465)
    assert plan.fitted_narration_duration_seconds == pytest.approx(32.5)
    assert plan.duration_fit_applied is True
    assert plan.narration_duration == pytest.approx(32.5)
    assert plan.total_duration == pytest.approx(32.5, abs=0.03)
    assert Path(plan.source_narration_path).name == "narration_original.mp3"
    assert Path(plan.fitted_narration_path).name == "narration_duration_fitted.mp3"
    assert (tmp_path / "assets" / "narration_original.mp3").read_bytes() == b"original-edge-audio"
    assert (tmp_path / "assets" / "narration_duration_fitted.mp3").is_file()
    assert plan.tts_metadata["narration_audio_path"].endswith("narration.mp3")
    assert plan.tts_metadata["narration_duration"] == pytest.approx(32.5)
    assert plan.tts_metadata["local_post_tts_tempo_correction_applied"] is True
    steps = [scene for scene in plan.scenes if scene.step_number]
    assert [scene.step_number for scene in steps] == [1, 2, 3]
    assert [scene.subtitle_highlight_words for scene in steps] == [
        ["vi\u1ebft"],
        ["\u0111\u1eb7t"],
        ["chu\u1ea9n b\u1ecb"],
    ]


def test_practical_duration_fit_leaves_in_range_audio_unchanged(
    monkeypatch,
    tmp_path,
):
    plan = _plan()
    apply_recipe_metadata(
        plan,
        get_recipe("practical_life_steps_v1"),
        validation_status="passed",
    )
    source = tmp_path / "assets" / "narration.mp3"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"within-range")
    plan.narration_audio_path = str(source)

    async def fake_probe(path):
        return 35.0

    async def fail_atempo(*args, **kwargs):
        raise AssertionError("atempo called for in-range audio")

    monkeypatch.setattr(duration_fit, "probe_duration", fake_probe)
    monkeypatch.setattr(duration_fit, "_run_atempo", fail_atempo)

    asyncio.run(duration_fit.reconcile_practical_narration_duration(plan, tmp_path))

    assert plan.duration_fit_required is False
    assert plan.duration_fit_applied is False
    assert plan.narration_duration == pytest.approx(35.0)
    assert source.read_bytes() == b"within-range"
    assert not (tmp_path / "assets" / "narration_duration_fitted.mp3").exists()


def test_practical_duration_fit_uses_pitch_preserving_ffmpeg_atempo(
    monkeypatch,
    tmp_path,
):
    commands: list[tuple[str, ...]] = []
    source = tmp_path / "source.mp3"
    destination = tmp_path / "fitted.mp3"
    source.write_bytes(b"audio")

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_subprocess(*cmd, **kwargs):
        commands.append(tuple(cmd))
        destination.write_bytes(b"fitted")
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)
    asyncio.run(duration_fit._run_atempo(source, destination, 0.875846154))

    command = commands[0]
    assert "-filter:a" in command
    assert "atempo=0.875846154" in command
    assert not any("asetrate" in part or "rubberband" in part for part in command)


def test_practical_duration_fit_rejects_unsafe_tempo(monkeypatch, tmp_path):
    plan = _plan()
    apply_recipe_metadata(
        plan,
        get_recipe("practical_life_steps_v1"),
        validation_status="passed",
    )
    source = tmp_path / "assets" / "narration.mp3"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"too-short")
    plan.narration_audio_path = str(source)

    async def fake_probe(path):
        return 20.0

    async def fail_atempo(*args, **kwargs):
        raise AssertionError("unsafe tempo reached ffmpeg")

    monkeypatch.setattr(duration_fit, "probe_duration", fake_probe)
    monkeypatch.setattr(duration_fit, "_run_atempo", fail_atempo)

    with pytest.raises(RuntimeError, match="outside safe range"):
        asyncio.run(duration_fit.reconcile_practical_narration_duration(plan, tmp_path))

    assert plan.duration_fit_applied is False
    assert plan.duration_fit_within_safe_range is False
    assert plan.actual_duration_validation_status == "failed"


@pytest.mark.parametrize(
    "recipe_id",
    ["emotional_symbolic_v1", "life_insight_symbolic_v1"],
)
def test_duration_fit_does_not_change_other_recipes(recipe_id, tmp_path):
    plan = _plan()
    plan.recipe_id = recipe_id
    before = plan.model_dump()

    asyncio.run(duration_fit.reconcile_practical_narration_duration(plan, tmp_path))

    assert plan.model_dump() == before


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
    assert ai_image._MIN_REQUEST_INTERVAL == pytest.approx(0.45)
    assert edge.MAX_RETRIES == 4
    assert edge.RETRY_BACKOFF_SECONDS == (3.0, 7.0, 15.0, 30.0)


def test_bounded_provider_environment_controls_preserve_defaults(monkeypatch):
    _clear_fetch_env(monkeypatch)
    assert ai_image._positive_env_int(
        "TELLA_CF_MAX_RETRIES_PER_ACCOUNT",
        ai_image.MAX_RETRIES_PER_ACCOUNT,
    ) == 3
    assert edge._max_retries() == edge.MAX_RETRIES

    monkeypatch.setenv("TELLA_CF_MAX_RETRIES_PER_ACCOUNT", "1")
    monkeypatch.setenv("TELLA_CF_MAX_ACCOUNTS", "1")
    monkeypatch.setenv("TELLA_EDGE_MAX_RETRIES", "1")

    assert ai_image._positive_env_int(
        "TELLA_CF_MAX_RETRIES_PER_ACCOUNT",
        ai_image.MAX_RETRIES_PER_ACCOUNT,
    ) == 1
    assert ai_image._positive_env_int("TELLA_CF_MAX_ACCOUNTS", 5) == 1
    assert edge._max_retries() == 1


def test_bounded_cloudflare_and_edge_attempts_are_enforced(monkeypatch, tmp_path):
    _clear_fetch_env(monkeypatch)
    monkeypatch.setenv("TELLA_CF_MAX_RETRIES_PER_ACCOUNT", "1")
    monkeypatch.setenv("TELLA_CF_MAX_ACCOUNTS", "1")
    monkeypatch.setenv("TELLA_EDGE_MAX_RETRIES", "1")
    cloudflare_calls = 0
    edge_calls = 0

    class FailingCloudflareClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            nonlocal cloudflare_calls
            cloudflare_calls += 1
            raise ai_image.httpx.ReadTimeout("bounded timeout")

    class FailingCommunicate:
        def __init__(self, **kwargs):
            pass

        async def save(self, path):
            nonlocal edge_calls
            edge_calls += 1
            raise RuntimeError("bounded TTS failure")

    async def no_throttle():
        return None

    monkeypatch.setattr(
        ai_image,
        "resolve_all_credentials",
        lambda: [("account-one", "token-one"), ("account-two", "token-two")],
    )
    monkeypatch.setattr(ai_image, "_throttle", no_throttle)
    monkeypatch.setattr(
        ai_image.httpx,
        "AsyncClient",
        lambda **kwargs: FailingCloudflareClient(),
    )
    monkeypatch.setattr(edge.edge_tts, "Communicate", FailingCommunicate)

    with pytest.raises(ai_image.CloudflareAIError):
        asyncio.run(ai_image.generate_image("local test", tmp_path / "image.jpg"))
    with pytest.raises(RuntimeError, match="failed after 1 attempts"):
        asyncio.run(
            edge.synthesize(
                "local test",
                "vi-VN-HoaiMyNeural",
                tmp_path / "audio.mp3",
            )
        )

    assert cloudflare_calls == 1
    assert edge_calls == 1


def test_direct_provider_defaults_keep_retry_rotation_throttle_and_backoff(
    monkeypatch,
    tmp_path,
):
    _clear_fetch_env(monkeypatch)
    cloudflare_urls: list[str] = []
    edge_calls = 0

    class FailingCloudflareClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            cloudflare_urls.append(url)
            raise ai_image.httpx.ReadTimeout("default retry test")

    class FailingCommunicate:
        def __init__(self, **kwargs):
            pass

        async def save(self, path):
            nonlocal edge_calls
            edge_calls += 1
            raise RuntimeError("default TTS retry test")

    async def no_throttle():
        return None

    monkeypatch.setattr(
        ai_image,
        "resolve_all_credentials",
        lambda: [("account-a", "token-a"), ("account-b", "token-b")],
    )
    monkeypatch.setattr(ai_image, "_throttle", no_throttle)
    monkeypatch.setattr(ai_image, "RETRY_BACKOFF_SECONDS", 0.0)
    monkeypatch.setattr(
        ai_image.httpx,
        "AsyncClient",
        lambda **kwargs: FailingCloudflareClient(),
    )
    monkeypatch.setattr(edge, "RETRY_BACKOFF_SECONDS", (0.0, 0.0, 0.0, 0.0))
    monkeypatch.setattr(edge.edge_tts, "Communicate", FailingCommunicate)

    with pytest.raises(ai_image.CloudflareAIError):
        asyncio.run(ai_image.generate_image("local test", tmp_path / "image.jpg"))
    with pytest.raises(RuntimeError, match="failed after 4 attempts"):
        asyncio.run(
            edge.synthesize(
                "local test",
                "vi-VN-HoaiMyNeural",
                tmp_path / "audio.mp3",
            )
        )

    assert ai_image.MAX_RETRIES_PER_ACCOUNT == 3
    assert ai_image._MIN_REQUEST_INTERVAL == pytest.approx(0.45)
    assert len(cloudflare_urls) == 6
    assert sum("account-a" in url for url in cloudflare_urls) == 3
    assert sum("account-b" in url for url in cloudflare_urls) == 3
    assert edge.MAX_RETRIES == 4
    assert edge_calls == 4


def test_strict_matching_reuse_makes_zero_provider_calls(monkeypatch, tmp_path):
    _clear_fetch_env(monkeypatch)
    source_job = tmp_path / "source"
    current_job = tmp_path / "current"
    plan = _step_preview_plan()
    _write_practical_reuse_source(
        source_job,
        plan,
        {scene.scene_index for scene in plan.scenes},
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
    assert all(scene.reuse_eligible for scene in plan.scenes)
    assert all(scene.reuse_match_reason == "strict practical metadata matched" for scene in plan.scenes)
    assert all(scene.reused_asset_hash == scene.asset_hash for scene in plan.scenes)


def test_partial_exact_reuse_generates_only_missing_scenes(monkeypatch, tmp_path):
    _clear_fetch_env(monkeypatch)
    source_job = tmp_path / "approved_preview"
    current_job = tmp_path / "full"
    plan = _plan()
    apply_recipe_metadata(
        plan,
        get_recipe("practical_life_steps_v1"),
        validation_status="passed",
    )
    _write_practical_reuse_source(source_job, plan, {3, 4, 5})
    generated: list[int] = []

    async def fake_generate(prompt, out, **kwargs):
        await fetch.ai_image._notify_before_cloudflare_request()
        scene = next(
            item for item in plan.scenes if item.provider_prompt_variant == prompt
        )
        generated.append(scene.scene_index)
        Image.new("RGB", (32, 48), "#eef0e7").save(out)

    monkeypatch.setattr(fetch.ai_image, "generate_image", fake_generate)
    monkeypatch.setenv("TELLA_REUSE_ASSETS", "1")
    monkeypatch.setenv("TELLA_IMAGES_FROM_JOB", str(source_job))
    monkeypatch.setenv("TELLA_MAX_AI_IMAGES", "4")
    monkeypatch.setenv("TELLA_SCENE_QC", "off")
    monkeypatch.setenv("TELLA_DISABLE_STOCK_FALLBACK", "1")
    monkeypatch.setenv("TELLA_REQUIRE_REUSED_SCENE_INDICES", "3,4,5")

    asyncio.run(fetch.fetch_assets(plan, current_job))

    assert generated == [1, 2, 6, 7]
    assert plan.ai_images_requested == 4
    assert plan.ai_images_reused == 3
    for scene in plan.scenes:
        assert scene.reuse_source_job_id == source_job.name
        if scene.scene_index in {3, 4, 5}:
            assert scene.image_source == "reused_asset"
            assert scene.provider_request_count_for_scene == 0
            assert scene.reuse_eligible is True
            assert scene.reuse_mismatch_reason == ""
            assert scene.reused_asset_hash == scene.asset_hash
        else:
            assert scene.image_source == "ai_image_provider"
            assert scene.provider_request_count_for_scene == 1
            assert scene.reuse_eligible is False
            assert scene.reuse_mismatch_reason == (
                "source plan has no candidate for this scene index"
            )


def test_local_rerender_strictly_reuses_all_seven_images_without_providers(
    monkeypatch,
    tmp_path,
):
    _clear_fetch_env(monkeypatch)
    source_job = tmp_path / "source"
    target_job = tmp_path / "target"
    plan = _plan()
    apply_recipe_metadata(
        plan,
        get_recipe("practical_life_steps_v1"),
        validation_status="passed",
    )
    _write_practical_reuse_source(source_job, plan, set(range(1, 8)))

    async def fail_provider(*args, **kwargs):
        raise AssertionError("image provider called during local rerender")

    async def fail_edge(*args, **kwargs):
        raise AssertionError("Edge called during local rerender")

    monkeypatch.setattr(fetch.ai_image, "generate_image", fail_provider)
    monkeypatch.setattr(edge, "synthesize", fail_edge)
    monkeypatch.setenv("TELLA_REUSE_ASSETS", "1")
    monkeypatch.setenv("TELLA_SKIP_IMAGE_GENERATION", "1")
    monkeypatch.setenv("TELLA_IMAGES_FROM_JOB", str(source_job))
    monkeypatch.setenv("TELLA_REUSE_ASSETS_MODE", "strict")
    monkeypatch.setenv("TELLA_REQUIRE_REUSED_SCENE_INDICES", "1,2,3,4,5,6,7")
    monkeypatch.setenv("TELLA_ALLOW_LOCAL_IMAGE_FALLBACK", "0")
    monkeypatch.setenv("TELLA_DISABLE_STOCK_FALLBACK", "1")

    asyncio.run(fetch.fetch_assets(plan, target_job))

    assert plan.ai_images_requested == 0
    assert plan.ai_images_generated == 0
    assert plan.ai_images_reused == 7
    assert plan.used_local_fallback is False
    assert all(scene.image_source == "reused_asset" for scene in plan.scenes)
    assert all(scene.reuse_eligible for scene in plan.scenes)
    assert all(scene.provider_request_count_for_scene == 0 for scene in plan.scenes)


def test_zero_network_full_prototype_simulation(
    monkeypatch,
    tmp_path,
):
    _clear_fetch_env(monkeypatch)
    recipe = get_recipe("practical_life_steps_v1")
    source_job = tmp_path / "practical_steps_preview_01"
    planned = _plan()
    apply_recipe_metadata(
        planned,
        recipe,
        validation_status="passed",
    )
    _write_practical_reuse_source(source_job, planned, {3, 4, 5})
    provider_scenes: list[int] = []
    tts_calls: list[str] = []
    validation_seen: list[str] = []
    render_calls: list[int] = []
    socket_calls: list[tuple] = []
    real_fetch = cli.fetch_assets

    def fail_socket(*args, **kwargs):
        socket_calls.append(args)
        raise AssertionError("real socket call attempted")

    async def fake_generate(prompt, out, **kwargs):
        await fetch.ai_image._notify_before_cloudflare_request()
        scene = next(
            item for item in planned.scenes if item.provider_prompt_variant == prompt
        )
        provider_scenes.append(scene.scene_index)
        Image.new("RGB", (32, 48), "#eef0e7").save(out)

    async def observed_fetch(plan, job_dir):
        assert len(plan.scenes) == 7
        assert plan.recipe_validation_status == "passed"
        validation_seen.append(plan.recipe_validation_status)
        await real_fetch(plan, job_dir)

    async def fake_tts(plan, job_dir, **kwargs):
        assert len(plan.scenes) == 7
        tts_calls.append(plan.global_narration_text)
        plan.narration_audio_filename = "assets/narration.mp3"
        plan.narration_audio_path = str(Path(job_dir) / "assets" / "narration.mp3")
        plan.narration_duration = 35.0
        plan.tts_provider = "edge"
        plan.tts_voice = "vi-VN-HoaiMyNeural"
        for scene in plan.scenes:
            scene.audio_duration = 5.0

    async def fake_render(plan, job_dir):
        render_calls.append(len(plan.scenes))
        assert 32.0 <= plan.total_duration <= 38.0
        return Path(job_dir) / "video.mp4"

    async def fake_local_duration_boundary(*args, **kwargs):
        return None

    monkeypatch.setattr(socket, "create_connection", fail_socket)
    monkeypatch.setattr(fetch.ai_image, "generate_image", fake_generate)
    monkeypatch.setattr(cli, "fetch_assets", observed_fetch)
    monkeypatch.setattr(cli, "synthesize_all", fake_tts)
    monkeypatch.setattr(cli, "render", fake_render)
    monkeypatch.setattr(
        cli,
        "reconcile_practical_narration_duration",
        fake_local_duration_boundary,
    )
    monkeypatch.setattr(
        cli,
        "validate_actual_video_duration",
        fake_local_duration_boundary,
    )
    monkeypatch.setenv("TELLA_AI_IMAGE_SEQUENTIAL", "1")
    monkeypatch.setenv("TELLA_CF_MAX_ACCOUNTS", "1")
    monkeypatch.setenv("TELLA_CF_MAX_RETRIES_PER_ACCOUNT", "1")
    monkeypatch.setenv("TELLA_EDGE_MAX_RETRIES", "1")
    monkeypatch.setenv("TELLA_SCENE_QC", "off")
    monkeypatch.setenv("TELLA_ALLOW_LOCAL_IMAGE_FALLBACK", "0")
    monkeypatch.setenv("TELLA_DISABLE_STOCK_FALLBACK", "1")
    monkeypatch.setenv("TELLA_REQUIRE_REUSED_SCENE_INDICES", "3,4,5")

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
            job_id="practical_life_steps_full_prototype_01",
            user_script=_FIXTURE.read_text(encoding="utf-8"),
            reuse_assets=True,
            images_from_job=str(source_job),
            reuse_assets_mode="strict",
            max_ai_images=4,
            recipe=recipe,
        )
    )

    output_plan = json.loads(
        (
            tmp_path
            / "practical_life_steps_full_prototype_01"
            / "plan.json"
        ).read_text(encoding="utf-8")
    )
    scenes = {item["scene_index"]: item for item in output_plan["scenes"]}

    assert result == (
        tmp_path / "practical_life_steps_full_prototype_01" / "video.mp4"
    )
    assert validation_seen == ["passed"]
    assert provider_scenes == [1, 2, 6, 7]
    assert len(tts_calls) == 1
    assert render_calls == [7]
    assert socket_calls == []
    assert output_plan["ai_images_requested"] == 4
    assert output_plan["ai_images_reused"] == 3
    assert output_plan["total_duration"] == pytest.approx(35.0)
    assert all(scenes[index]["reuse_eligible"] for index in {3, 4, 5})
    assert all(
        scenes[index]["image_source"] == "reused_asset"
        for index in {3, 4, 5}
    )
    assert all(
        scenes[index]["provider_request_count_for_scene"] == 1
        for index in {1, 2, 6, 7}
    )


def test_sequential_partial_generation_stops_on_first_provider_failure(
    monkeypatch,
    tmp_path,
):
    _clear_fetch_env(monkeypatch)
    source_job = tmp_path / "approved_preview"
    current_job = tmp_path / "full"
    plan = _plan()
    apply_recipe_metadata(
        plan,
        get_recipe("practical_life_steps_v1"),
        validation_status="passed",
    )
    _write_practical_reuse_source(source_job, plan, {3, 4, 5})
    calls: list[int] = []

    async def fail_generate(prompt, out, **kwargs):
        await fetch.ai_image._notify_before_cloudflare_request()
        scene = next(
            item for item in plan.scenes if item.provider_prompt_variant == prompt
        )
        calls.append(scene.scene_index)
        raise RuntimeError("bounded provider failure")

    monkeypatch.setattr(fetch.ai_image, "generate_image", fail_generate)
    monkeypatch.setenv("TELLA_REUSE_ASSETS", "1")
    monkeypatch.setenv("TELLA_IMAGES_FROM_JOB", str(source_job))
    monkeypatch.setenv("TELLA_MAX_AI_IMAGES", "4")
    monkeypatch.setenv("TELLA_AI_IMAGE_SEQUENTIAL", "1")
    monkeypatch.setenv("TELLA_ALLOW_LOCAL_IMAGE_FALLBACK", "0")
    monkeypatch.setenv("TELLA_DISABLE_STOCK_FALLBACK", "1")

    with pytest.raises(RuntimeError, match="stock fallback disabled"):
        asyncio.run(fetch.fetch_assets(plan, current_job))

    assert calls == [1]
    assert plan.ai_images_requested == 1


def test_practical_reuse_rejects_visual_action_mismatch(monkeypatch, tmp_path):
    _clear_fetch_env(monkeypatch)
    source_job = tmp_path / "source"
    current_job = tmp_path / "current"
    plan = _step_preview_plan()
    _write_practical_reuse_source(source_job, plan, {3, 4, 5})
    source_plan_path = source_job / "plan.json"
    source_plan = json.loads(source_plan_path.read_text(encoding="utf-8"))
    source_plan["scenes"][0]["visual_action"] = "different action"
    source_plan_path.write_text(json.dumps(source_plan, indent=2), encoding="utf-8")
    generated: list[int] = []

    async def fake_generate(prompt, out, **kwargs):
        await fetch.ai_image._notify_before_cloudflare_request()
        scene = next(
            item for item in plan.scenes if item.provider_prompt_variant == prompt
        )
        generated.append(scene.scene_index)
        Image.new("RGB", (32, 48), "#eef0e7").save(out)

    monkeypatch.setattr(fetch.ai_image, "generate_image", fake_generate)
    monkeypatch.setenv("TELLA_REUSE_ASSETS", "1")
    monkeypatch.setenv("TELLA_IMAGES_FROM_JOB", str(source_job))
    monkeypatch.setenv("TELLA_DISABLE_STOCK_FALLBACK", "1")

    asyncio.run(fetch.fetch_assets(plan, current_job))

    assert generated == [3]
    assert plan.scenes[0].reuse_eligible is False
    assert plan.scenes[0].reuse_mismatch_reason == "visual action does not match"
    assert all(scene.reuse_eligible for scene in plan.scenes[1:])


def test_required_practical_reuse_mismatch_aborts_before_provider(
    monkeypatch,
    tmp_path,
):
    _clear_fetch_env(monkeypatch)
    source_job = tmp_path / "source"
    current_job = tmp_path / "current"
    plan = _step_preview_plan()
    _write_practical_reuse_source(source_job, plan, {3, 4, 5})
    source_plan_path = source_job / "plan.json"
    source_plan = json.loads(source_plan_path.read_text(encoding="utf-8"))
    source_plan["scenes"][0]["visual_action"] = "different action"
    source_plan_path.write_text(json.dumps(source_plan, indent=2), encoding="utf-8")

    async def fail_provider(*args, **kwargs):
        raise AssertionError("provider called for required reused scene")

    monkeypatch.setattr(fetch.ai_image, "generate_image", fail_provider)
    monkeypatch.setenv("TELLA_REUSE_ASSETS", "1")
    monkeypatch.setenv("TELLA_IMAGES_FROM_JOB", str(source_job))
    monkeypatch.setenv("TELLA_REQUIRE_REUSED_SCENE_INDICES", "3,4,5")

    with pytest.raises(
        RuntimeError,
        match="required strict reuse failed before provider submission for scene 03",
    ):
        asyncio.run(fetch.fetch_assets(plan, current_job))

    assert plan.ai_images_requested == 0
    assert plan.scenes[0].reuse_mismatch_reason == "visual action does not match"


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

    async def fake_local_duration_boundary(*args, **kwargs):
        return None

    monkeypatch.setattr(cli, "fetch_assets", fake_fetch)
    monkeypatch.setattr(cli, "synthesize_all", fake_tts)
    monkeypatch.setattr(cli, "render", fake_render)
    monkeypatch.setattr(
        cli,
        "reconcile_practical_narration_duration",
        fake_local_duration_boundary,
    )
    monkeypatch.setattr(
        cli,
        "validate_actual_video_duration",
        fake_local_duration_boundary,
    )

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

    async def fake_local_duration_boundary(*args, **kwargs):
        return None

    monkeypatch.setattr(cli, "fetch_assets", fake_fetch)
    monkeypatch.setattr(cli, "synthesize_all", fake_tts)
    monkeypatch.setattr(cli, "render", fake_render)
    monkeypatch.setattr(
        cli,
        "reconcile_practical_narration_duration",
        fake_local_duration_boundary,
    )
    monkeypatch.setattr(
        cli,
        "validate_actual_video_duration",
        fake_local_duration_boundary,
    )

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
