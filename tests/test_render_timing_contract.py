"""Network-free tests for the production render-timeline contract."""
from __future__ import annotations

import pytest

from tella.composer.compose import compose_timing
from tella.composer.timing import (
    build_render_timing_plan,
    validate_actual_render_duration,
)
from tella.planner.models import Scene, TellaScenePlan


def _timeline(
    count: int,
    requested: float,
    *,
    narration: float = 0.0,
    transition: float = 0.0,
):
    return build_render_timing_plan(
        [requested / count] * count,
        requested_duration=requested,
        narration_duration=narration,
        configured_transition_duration=transition,
    )


def _plan(
    count: int = 3,
    *,
    requested: float = 12.0,
    narration: float = 12.0,
    theme: str = "minimalist_emotional",
) -> TellaScenePlan:
    return TellaScenePlan(
        title="Timing contract",
        theme=theme,
        requested_production_duration_seconds=requested,
        narration_duration=narration,
        scenes=[
            Scene(
                scene_index=index,
                voice_script=f"Scene {index}",
                audio_duration=narration / count,
            )
            for index in range(1, count + 1)
        ],
    )


def test_timing_math_without_transitions() -> None:
    timing = _timeline(3, 12.0)

    assert timing.authority == "requested_production_duration"
    assert timing.scene_timeline_durations == (4.0, 4.0, 4.0)
    assert timing.scene_clip_durations == (4.0, 4.0, 4.0)
    assert timing.total_transition_overlap == 0.0
    assert timing.expected_final_duration == 12.0


def test_crossfade_clip_durations_compensate_for_overlap() -> None:
    timing = _timeline(3, 12.0, transition=0.8)

    assert timing.effective_transition_duration == 0.8
    assert timing.scene_clip_durations == (4.8, 4.8, 4.0)
    assert timing.total_transition_overlap == 1.6
    assert (
        sum(timing.scene_clip_durations) - timing.total_transition_overlap
        == pytest.approx(12.0)
    )


@pytest.mark.parametrize("count,requested", [(7, 32.0), (8, 35.0), (8, 38.0)])
def test_standard_production_timelines_remain_on_target(
    count: int, requested: float
) -> None:
    timing = _timeline(count, requested, transition=0.8)

    assert timing.expected_final_duration == pytest.approx(requested)
    assert (
        sum(timing.scene_clip_durations) - timing.total_transition_overlap
        == pytest.approx(requested)
    )


def test_continuous_narration_is_authoritative_and_not_truncated() -> None:
    timing = _timeline(3, 12.0, narration=15.0, transition=0.8)

    assert timing.authority == "continuous_narration"
    assert timing.authoritative_duration == 15.0
    assert timing.expected_final_duration == 15.0
    assert timing.requested_duration_status == "differs_from_authoritative_narration"


def test_shorter_narration_reconciles_without_a_frozen_tail() -> None:
    timing = _timeline(3, 12.0, narration=8.426688, transition=0.8)

    assert timing.expected_final_duration == pytest.approx(8.426688)
    assert timing.scene_clip_durations[-1] == timing.scene_timeline_durations[-1]
    assert all(
        clip - slot == pytest.approx(0.8)
        for clip, slot in zip(
            timing.scene_clip_durations[:-1],
            timing.scene_timeline_durations[:-1],
        )
    )
    assert timing.requested_duration_delta == pytest.approx(-3.573312)


def test_renderer_uses_capped_effective_crossfade_for_short_scene_slots(
    monkeypatch,
    tmp_path,
) -> None:
    from tella.render import pipeline

    plan = _plan(count=8, requested=35.0, narration=24.827438)
    weights = [2.766913, 4.112979, 3.888635, 2.61735, 2.019099, 2.168662, 4.561668, 2.692132]
    for scene, weight in zip(plan.scenes, weights, strict=True):
        scene.audio_duration = weight
        scene.image_filenames = [f"assets/scene_{scene.scene_index:02d}.png"]
    plan.narration_audio_filename = "assets/narration.mp3"

    captured: dict[str, float] = {}

    async def fake_render_scene(*, out_path, duration, **kwargs):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.touch()

    async def fake_concat(scene_paths, scene_durations, out_path, *, transition_duration):
        captured["transition_duration"] = transition_duration
        raise RuntimeError("stop after transition capture")

    monkeypatch.setattr(pipeline, "_prepare_image_asset_for_render", lambda source, output, **kwargs: (source, "a" * 64, False))
    monkeypatch.setattr(pipeline, "_render_scene", fake_render_scene)
    monkeypatch.setattr(pipeline, "_concat_scenes_xfade", fake_concat)

    with pytest.raises(RuntimeError, match="stop after transition capture"):
        import asyncio

        asyncio.run(pipeline.render(plan, tmp_path))

    effective = float(plan.render_timing_contract["effective_transition_duration_seconds"])
    assert effective < 0.8
    assert captured["transition_duration"] == pytest.approx(effective)


def test_three_scene_compose_persists_explicit_timing_semantics() -> None:
    plan = _plan()

    compose_timing(plan, transition_duration=0.8)

    assert plan.total_duration == 12.0
    assert [scene.duration for scene in plan.scenes] == [4.0, 4.0, 4.0]
    assert [scene.render_clip_duration for scene in plan.scenes] == [4.8, 4.8, 4.0]
    assert plan.scene_timing_map[0] == {
        "scene_index": 1,
        "start": 0.0,
        "duration": 4.0,
        "timeline_duration": 4.0,
        "render_clip_duration": 4.8,
        "outgoing_transition_overlap": 0.8,
    }


def test_timing_contract_round_trips_identically_on_resume() -> None:
    plan = _plan(count=8, requested=35.0, narration=34.25)
    compose_timing(plan, transition_duration=0.8)

    restored = TellaScenePlan.model_validate_json(plan.model_dump_json())

    assert restored.render_timing_contract == plan.render_timing_contract
    assert restored.scene_timing_map == plan.scene_timing_map
    assert [scene.render_clip_duration for scene in restored.scenes] == [
        scene.render_clip_duration for scene in plan.scenes
    ]


def test_actual_duration_validation_fails_closed_outside_tolerance() -> None:
    metadata = _timeline(3, 12.0, transition=0.8).metadata()

    result = validate_actual_render_duration(metadata, 11.7)

    assert result["actual_duration_status"] == "failed"
    assert result["actual_duration_delta_seconds"] == pytest.approx(-0.3)
    assert "tolerance 0.150s" in str(result["actual_duration_failure_reason"])


def test_quality_mode_cut_timing_remains_unchanged() -> None:
    plan = _plan(theme="cinematic")

    compose_timing(plan)

    assert plan.total_duration == 12.0
    assert [scene.render_clip_duration for scene in plan.scenes] == [4.0] * 3
