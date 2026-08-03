from __future__ import annotations

import asyncio
import logging
import re

from PIL import Image
import pytest

from tella.composer.timing import build_render_timing_plan
from tella.media import fetch
from tella.planner.models import Scene, TellaScenePlan
from tella.render.pipeline import (
    OUTPUT_FPS,
    _build_bg_filter,
    _render_motion_profile_override,
    _select_scene_motion_profile,
)
from tella.subtitles import subtitle_text_for_style
from tella import web_jobs as web_jobs_module


def _plan(*, aspect_ratio: str = "9:16", theme: str = "cinematic") -> TellaScenePlan:
    return TellaScenePlan(
        title="Web pull-back authority",
        language="en",
        aspect_ratio=aspect_ratio,
        media_source="ai_image",
        duration_mode="short",
        theme=theme,
        scenes=[
            Scene(
                scene_index=index,
                kind="scene",
                title=f"Scene {index}",
                voice_script=f"Narration {index}.",
                image_prompt=f"Illustration {index}",
                stock_query="legacy fallback",
                scene_meaning=f"Meaning {index}",
                symbolic_visual=f"Symbolic visual {index}",
                scene_role="hook" if index == 1 else "practical_step",
                step_number=index - 1,
            )
            for index in range(1, 4)
        ],
    )


def _zoom_values(filter_chain: str, frame_count: int) -> list[float]:
    match = re.search(r"zoompan=z='max\((\d+\.\d+)-(\d+\.\d+)\*on,1\.0\)'", filter_chain)
    assert match is not None
    initial, step = (float(value) for value in match.groups())
    return [max(initial - step * frame, 1.0) for frame in range(frame_count)]


def test_web_environment_selects_only_registered_pull_back_profile() -> None:
    assert web_jobs_module._WEB_ENVIRONMENT["TELLA_RENDER_MOTION_PROFILE"] == (
        "practical_pull_back"
    )


def test_motion_override_is_absent_by_default_and_rejects_expressions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TELLA_RENDER_MOTION_PROFILE", raising=False)
    assert _render_motion_profile_override() is None

    monkeypatch.setenv("TELLA_RENDER_MOTION_PROFILE", "zoom+0.01")
    with pytest.raises(ValueError, match="unsupported TELLA_RENDER_MOTION_PROFILE"):
        _render_motion_profile_override()


def test_web_override_precedes_role_specific_motion_without_changing_legacy() -> None:
    plan = _plan(theme="practical_life_steps")
    scene = plan.scenes[0]

    legacy = _select_scene_motion_profile(
        plan,
        scene,
        default_profile="gentle_progressive_motion",
        ken_burns_max_scale=1.03,
        override_profile=None,
    )
    web = _select_scene_motion_profile(
        plan,
        scene,
        default_profile="gentle_progressive_motion",
        ken_burns_max_scale=1.03,
        override_profile="practical_pull_back",
    )

    assert legacy == ("practical_zoom_in", 1.03)
    assert web == ("practical_pull_back", 1.03)


_WEB_THEME_ROLE_CASES = (
    ("cinematic", "", 0),
    ("cinematic", "opening", 0),
    ("mindfulness", "closing", 0),
    ("playful", "call_to_action", 0),
    ("parable", "opening", 0),
    ("minimalist_emotional", "closing", 0),
    ("minimalist_symbolic_reel", "opening", 0),
    ("life_insight_symbolic", "hook", 0),
    ("life_insight_symbolic", "conclusion", 0),
    ("life_insight_symbolic", "closing", 0),
    ("practical_life_steps", "hook", 0),
    ("practical_life_steps", "context", 0),
    ("practical_life_steps", "context_part_one", 0),
    ("practical_life_steps", "context_part_two", 0),
    ("practical_life_steps", "practical_step", 1),
    ("practical_life_steps", "practical_step", 2),
    ("practical_life_steps", "practical_step", 3),
    ("practical_life_steps", "common_mistake", 0),
    ("practical_life_steps", "today_action", 0),
    ("practical_life_steps", "closing", 0),
    ("practical_life_steps", "call_to_action", 0),
)


@pytest.mark.parametrize("duration", [0.05, 5.5, 60.0], ids=["short", "typical", "long"])
@pytest.mark.parametrize(
    ("aspect_ratio", "canvas"),
    [("9:16", (1080, 1920)), ("16:9", (1920, 1080))],
)
@pytest.mark.parametrize(
    ("theme", "scene_role", "step_number"),
    _WEB_THEME_ROLE_CASES,
)
def test_web_pull_back_is_final_authority_for_complete_theme_role_matrix(
    theme: str,
    scene_role: str,
    step_number: int,
    aspect_ratio: str,
    canvas: tuple[int, int],
    duration: float,
) -> None:
    plan = _plan(aspect_ratio=aspect_ratio, theme=theme)
    scene = plan.scenes[0].model_copy(update={"scene_role": scene_role, "step_number": step_number})

    profile, scale = _select_scene_motion_profile(
        plan,
        scene,
        default_profile=plan.motion_profile_id or "slow_ken_burns",
        ken_burns_max_scale=1.03,
        override_profile="practical_pull_back",
    )
    chain = _build_bg_filter(
        is_video=False,
        canvas_w=canvas[0],
        canvas_h=canvas[1],
        duration=duration,
        ken_burns_max_scale=scale,
        motion_profile=profile,
    )

    assert profile == "practical_pull_back"
    assert "zoompan=z='max(1.03-" in chain


@pytest.mark.parametrize(
    ("theme", "scene_role", "step_number", "default_profile", "expected"),
    [
        ("cinematic", "", 0, "slow_ken_burns", "slow_ken_burns"),
        ("cinematic", "opening", 0, "controlled_slow_pan", "controlled_slow_pan"),
        (
            "life_insight_symbolic",
            "conclusion",
            0,
            "controlled_slow_pan",
            "controlled_slow_hold",
        ),
        (
            "life_insight_symbolic",
            "opening",
            0,
            "controlled_slow_pan",
            "controlled_slow_pan",
        ),
        ("practical_life_steps", "hook", 0, "gentle_progressive_motion", "practical_zoom_in"),
        (
            "practical_life_steps",
            "context",
            0,
            "gentle_progressive_motion",
            "practical_pan_left_to_right",
        ),
        (
            "practical_life_steps",
            "practical_step",
            1,
            "gentle_progressive_motion",
            "practical_pan_left_to_right",
        ),
        (
            "practical_life_steps",
            "practical_step",
            2,
            "gentle_progressive_motion",
            "practical_pan_right_to_left",
        ),
        (
            "practical_life_steps",
            "practical_step",
            3,
            "gentle_progressive_motion",
            "practical_zoom_in",
        ),
        (
            "practical_life_steps",
            "common_mistake",
            0,
            "gentle_progressive_motion",
            "practical_pull_back",
        ),
        (
            "practical_life_steps",
            "closing",
            0,
            "gentle_progressive_motion",
            "practical_stable_hold",
        ),
        (
            "practical_life_steps",
            "call_to_action",
            0,
            "gentle_progressive_motion",
            "practical_zoom_in",
        ),
    ],
)
def test_legacy_renderer_profile_precedence_is_unchanged_without_override(
    theme: str,
    scene_role: str,
    step_number: int,
    default_profile: str,
    expected: str,
) -> None:
    plan = _plan(theme=theme)
    scene = plan.scenes[0].model_copy(update={"scene_role": scene_role, "step_number": step_number})

    profile, _ = _select_scene_motion_profile(
        plan,
        scene,
        default_profile=default_profile,
        ken_burns_max_scale=1.03,
        override_profile=None,
    )

    assert profile == expected


@pytest.mark.parametrize("duration", [0.05, 5.5, 60.0])
@pytest.mark.parametrize("canvas", [(1080, 1920), (1920, 1080)])
def test_pull_back_is_centered_monotonic_floored_and_duration_driven(
    duration: float,
    canvas: tuple[int, int],
) -> None:
    canvas_w, canvas_h = canvas
    frame_count = max(2, int(duration * OUTPUT_FPS))
    chain = _build_bg_filter(
        is_video=False,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        duration=duration,
        ken_burns_max_scale=1.03,
        motion_profile="practical_pull_back",
    )
    values = _zoom_values(chain, frame_count)

    assert chain.startswith(
        f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=increase,"
        f"crop={canvas_w}:{canvas_h}"
    )
    assert "x='iw/2-(iw/zoom/2)'" in chain
    assert "y='ih/2-(ih/zoom/2)'" in chain
    assert f"d={frame_count}" in chain
    assert values[0] == pytest.approx(1.03)
    assert all(current >= following for current, following in zip(values, values[1:]))
    assert min(values) >= 1.0
    assert values[-1] == pytest.approx(1.0, abs=0.002)


def test_narration_timing_transition_and_subtitle_authorities_are_unchanged() -> None:
    timing = build_render_timing_plan(
        [1.0, 2.0, 1.0],
        requested_duration=35.0,
        narration_duration=11.25,
        configured_transition_duration=0.8,
    )
    subtitle = subtitle_text_for_style("Narration remains authoritative.", "classic")

    assert timing.authority == "continuous_narration"
    assert timing.authoritative_duration == 11.25
    assert timing.expected_final_duration == 11.25
    assert timing.scene_timeline_durations == (2.8125, 5.625, 2.8125)
    assert timing.effective_transition_duration == 0.8
    assert subtitle.text == "Narration remains authoritative."


def test_legacy_cli_fallback_call_and_log_remain_accurate(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("TELLA_DISABLE_STOCK_FALLBACK", raising=False)
    monkeypatch.delenv("TELLA_RENDER_MOTION_PROFILE", raising=False)
    monkeypatch.setattr(fetch, "MAX_CONCURRENT", 1)
    calls = {"cloudflare": 0, "pexels": 0}

    async def fail_cloudflare(*_args: object, **_kwargs: object) -> None:
        calls["cloudflare"] += 1
        raise RuntimeError("controlled legacy failure")

    async def fake_pexels(_query, out_path, *, width, height):
        calls["pexels"] += 1
        Image.new("RGB", (width, height), "#42677a").save(out_path)
        return out_path

    monkeypatch.setattr(fetch.ai_image, "generate_image", fail_cloudflare)
    monkeypatch.setattr(fetch.stock_photo, "search_and_download", fake_pexels)
    caplog.set_level(logging.WARNING, logger="tella.media.fetch")

    asyncio.run(fetch.fetch_assets(_plan(), tmp_path))

    assert calls == {"cloudflare": 3, "pexels": 3}
    assert "fallback to Pexels" in caplog.text
    assert "stock fallback is disabled" not in caplog.text
