from __future__ import annotations

from decimal import Decimal
import inspect
import json

import pytest
from pydantic import ValidationError

from tella.topic_production import (
    AuthorizedRenderLifecycleOutcome,
    ExecutionRunState,
    clear_processed_narration_measurement,
)
from tella.topic_production.duration_policy import (
    DurationValueAuthority,
    assess_mvp_duration_target,
)
import tella.topic_production.narration_measurement as narration_measurement
import tella.topic_production.persistence as persistence
import tella.topic_production.renderer_bridge as renderer_bridge
from tests.test_topic_production_render_lifecycle_contract import _request
from tests.test_topic_production_renderer_bridge import _profile


def _state_with_measured_duration(tmp_path, duration: float) -> ExecutionRunState:
    state = _request(tmp_path).state
    measurement = state.processed_narration_measurement
    assert measurement is not None
    return state.model_copy(
        update={
            "processed_narration_measurement": measurement.model_copy(
                update={
                    "measured_duration_assessment": assess_mvp_duration_target(
                        duration,
                        value_authority=DurationValueAuthority.MEASURED,
                    )
                }
            )
        }
    )


def _timeline(tmp_path, *, duration: float = 35.0, transition: str = "subtle_crossfade"):
    return renderer_bridge.build_authoritative_narration_timeline(
        _state_with_measured_duration(tmp_path, duration),
        profile=_profile().model_copy(update={"transition_profile_id": transition}),
    )


@pytest.mark.parametrize("duration", [35.0, 45.1234567])
def test_builder_uses_only_bound_measured_total_and_accepts_outside_target(
    tmp_path,
    duration: float,
) -> None:
    state = _state_with_measured_duration(tmp_path, duration)
    before = state.model_dump(mode="python")

    timeline = renderer_bridge.build_authoritative_narration_timeline(
        state,
        profile=_profile().model_copy(
            update={
                "requested_production_duration_seconds": 32.0,
                "duration_target_seconds": 38.0,
            }
        ),
    )

    assert timeline.processed_duration_seconds == duration
    assert timeline.scene_timings[-1].end_seconds == duration
    assert timeline.render_timing_contract.expected_final_timeline_duration_seconds == duration
    assert timeline.render_timing_contract.duration_authority is DurationValueAuthority.MEASURED
    assert state.model_dump(mode="python") == before


def test_builder_signature_has_no_competing_duration_or_external_input() -> None:
    parameters = inspect.signature(
        renderer_bridge.build_authoritative_narration_timeline
    ).parameters

    assert tuple(parameters) == ("state", "profile")
    assert parameters["profile"].kind is inspect.Parameter.KEYWORD_ONLY


def test_missing_measurement_rejects_with_normal_validated_state(tmp_path) -> None:
    state = clear_processed_narration_measurement(_request(tmp_path).state)

    with pytest.raises(ValueError, match="measurement is required"):
        renderer_bridge.build_authoritative_narration_timeline(
            state,
            profile=_profile(),
        )


@pytest.mark.parametrize(
    ("authority", "story_sha256", "message"),
    [
        (DurationValueAuthority.PLANNED, None, "MEASURED authority"),
        (DurationValueAuthority.MEASURED, "0" * 64, "StoryPlan SHA-256"),
    ],
)
def test_builder_revalidates_forged_measurement_authority(
    tmp_path,
    authority: DurationValueAuthority,
    story_sha256: str | None,
    message: str,
) -> None:
    state = _request(tmp_path).state
    measurement = state.processed_narration_measurement
    assert measurement is not None
    assessment = measurement.measured_duration_assessment
    unsafe_assessment = type(assessment).model_construct(
        **{
            **{name: getattr(assessment, name) for name in type(assessment).model_fields},
            "value_authority": authority,
        }
    )
    unsafe_measurement = type(measurement).model_construct(
        **{
            **{name: getattr(measurement, name) for name in type(measurement).model_fields},
            "story_plan_sha256": story_sha256 or measurement.story_plan_sha256,
            "measured_duration_assessment": unsafe_assessment,
        }
    )
    unsafe_state = ExecutionRunState.model_construct(
        **{
            **{name: getattr(state, name) for name in ExecutionRunState.model_fields},
            "processed_narration_measurement": unsafe_measurement,
        }
    )

    with pytest.raises(ValidationError, match=message):
        renderer_bridge.build_authoritative_narration_timeline(
            unsafe_state,
            profile=_profile(),
        )


def test_builder_strictly_revalidates_unsafe_profile(tmp_path) -> None:
    profile = _profile()
    unsafe_profile = type(profile).model_construct(
        **{
            **{name: getattr(profile, name) for name in type(profile).model_fields},
            "requested_production_duration_seconds": "35.0",
        }
    )

    with pytest.raises(ValidationError, match="requested_production_duration_seconds"):
        renderer_bridge.build_authoritative_narration_timeline(
            _state_with_measured_duration(tmp_path, 35.0),
            profile=unsafe_profile,
        )


@pytest.mark.parametrize(
    ("transition_profile_id", "expected"),
    [
        ("subtle_crossfade", 0.8),
        ("clean_soft_cut", 0.0),
        ("clean_progressive_cut", 0.0),
    ],
)
def test_transition_profiles_resolve_to_committed_numeric_authority(
    tmp_path,
    transition_profile_id: str,
    expected: float,
) -> None:
    timeline = _timeline(tmp_path, transition=transition_profile_id)
    contract = timeline.render_timing_contract

    assert contract.configured_transition_duration_seconds == expected
    assert contract.effective_transition_duration_seconds == expected
    for timing in timeline.scene_timings[:-1]:
        assert timing.render_clip_duration_seconds == timing.duration_seconds + expected
    assert (
        timeline.scene_timings[-1].render_clip_duration_seconds
        == timeline.scene_timings[-1].duration_seconds
    )


def test_unknown_transition_profile_rejects(tmp_path) -> None:
    with pytest.raises(ValueError, match="unsupported renderer transition profile"):
        _timeline(tmp_path, transition="unknown_transition")


@pytest.mark.parametrize(
    "weights",
    [
        (1.0, 0.0),
        (1.0, -1.0),
        (1.0, float("nan")),
        (1.0, float("inf")),
        (1.0, float("-inf")),
    ],
)
def test_allocator_rejects_invalid_weights_without_fallback(weights) -> None:
    with pytest.raises(ValueError, match="planned scene duration"):
        renderer_bridge._allocate_measured_scene_durations(10.0, weights)


def test_allocator_is_exact_for_decimal_remainder_and_previous_drift_case() -> None:
    measured = 14.456381543211506

    total, durations = renderer_bridge._allocate_measured_scene_durations(
        measured,
        (1.0, 2.0),
    )

    assert total == Decimal("14.456381543211506")
    assert durations == (Decimal("4.818794"), Decimal("9.637587543211506"))
    assert sum(durations, Decimal("0")) == total
    assert float(total) == measured


def test_allocator_supports_one_scene_and_rejects_non_positive_result() -> None:
    total, durations = renderer_bridge._allocate_measured_scene_durations(
        0.0000004,
        (1.0,),
    )

    assert total == Decimal("0.0000004")
    assert durations == (total,)
    with pytest.raises(ValueError, match="positive slot"):
        renderer_bridge._allocate_measured_scene_durations(
            0.0000004,
            (1.0, 1.0),
        )


def test_many_scene_allocation_preserves_order_weights_and_exact_total(tmp_path) -> None:
    state = _state_with_measured_duration(tmp_path, 33.3333337)
    planned = tuple(item.timing.duration_seconds for item in state.run_plan.scene_execution_plans)

    first = renderer_bridge.build_authoritative_narration_timeline(
        state,
        profile=_profile(),
    )
    second = renderer_bridge.build_authoritative_narration_timeline(
        state,
        profile=_profile(),
    )

    assert first == second
    assert tuple(item.scene_id for item in first.scene_timings) == tuple(
        item.scene_id for item in state.run_plan.scene_execution_plans
    )
    assert tuple(item.order for item in first.scene_timings) == tuple(range(1, 9))
    assert first.scene_timings[-1].end_seconds == 33.3333337
    assert sum(
        (Decimal(str(item.duration_seconds)) for item in first.scene_timings),
        Decimal("0"),
    ) == Decimal("33.3333337")
    assert (
        tuple(item.timing.duration_seconds for item in state.run_plan.scene_execution_plans)
        == planned
    )


def test_contract_arrays_and_transition_are_exact_projection(tmp_path) -> None:
    timeline = _timeline(tmp_path)
    contract = timeline.render_timing_contract

    assert contract.scene_timeline_durations_seconds == tuple(
        item.duration_seconds for item in timeline.scene_timings
    )
    assert contract.scene_clip_durations_seconds == tuple(
        item.render_clip_duration_seconds for item in timeline.scene_timings
    )
    assert contract.scene_start_times_seconds == tuple(
        item.start_seconds for item in timeline.scene_timings
    )
    assert contract.scene_end_times_seconds == tuple(
        item.end_seconds for item in timeline.scene_timings
    )
    assert contract.timing_tolerance_seconds == 0.15
    assert contract.renderer_stretch_authorized is False


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload.update({"unexpected": True}), "extra_forbidden"),
        (
            lambda payload: payload.update({"expected_final_timeline_duration_seconds": 34.0}),
            "total",
        ),
        (
            lambda payload: payload.update({"scene_timeline_durations_seconds": (1.0,) * 8}),
            "effective transition|arrays",
        ),
        (
            lambda payload: payload.update({"effective_transition_duration_seconds": 0.4}),
            "effective transition",
        ),
        (
            lambda payload: payload.update({"timing_tolerance_seconds": float("nan")}),
            "timing_tolerance_seconds",
        ),
    ],
)
def test_contract_tampering_rejects(tmp_path, mutation, message: str) -> None:
    payload = _timeline(tmp_path).model_dump(mode="python")
    contract = dict(payload["render_timing_contract"])
    mutation(contract)
    payload["render_timing_contract"] = contract

    with pytest.raises(ValidationError, match=message):
        renderer_bridge.AuthoritativeNarrationTimeline.model_validate(payload)


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("narration_duration_seconds", 34.0, "authoritative totals"),
        ("scene_start_times_seconds", (0.0,) * 8, "contiguous"),
        ("scene_clip_durations_seconds", (1.0,) * 8, "clip arrays"),
    ],
)
def test_contract_direct_construction_rejects_internal_inconsistency(
    tmp_path,
    field_name: str,
    value: object,
    message: str,
) -> None:
    payload = _timeline(tmp_path).render_timing_contract.model_dump(mode="python")
    payload[field_name] = value

    with pytest.raises(ValidationError, match=message):
        renderer_bridge.AuthoritativeRenderTimingContract.model_validate(payload)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda payload: payload["scene_timings"].__setitem__(
                0,
                {
                    **payload["scene_timings"][0],
                    "scene_id": "scene_02",
                },
            ),
            "scene IDs",
        ),
        (
            lambda payload: payload.update({"scene_timings": payload["scene_timings"][1:]}),
            "scene",
        ),
        (
            lambda payload: payload["scene_timings"].__setitem__(
                1,
                {
                    **payload["scene_timings"][1],
                    "start_seconds": 0.0,
                },
            ),
            "end must equal start plus duration|contiguous",
        ),
        (
            lambda payload: payload["scene_timings"].__setitem__(
                -1,
                {
                    **payload["scene_timings"][-1],
                    "render_clip_duration_seconds": (
                        payload["scene_timings"][-1]["duration_seconds"] + 0.8
                    ),
                },
            ),
            "arrays|transition",
        ),
    ],
)
def test_timeline_structure_tampering_rejects(tmp_path, mutation, message: str) -> None:
    payload = _timeline(tmp_path).model_dump(mode="python")
    payload["scene_timings"] = list(payload["scene_timings"])
    mutation(payload)

    with pytest.raises(ValidationError, match=message):
        renderer_bridge.AuthoritativeNarrationTimeline.model_validate(payload)


def test_timeline_nested_authority_is_immutable_and_json_round_trips(tmp_path) -> None:
    timeline = _timeline(tmp_path)
    serialized = timeline.model_dump(mode="json")
    text = timeline.model_dump_json()

    with pytest.raises(AttributeError):
        timeline.scene_timings.clear()
    with pytest.raises(ValidationError, match="frozen"):
        timeline.scene_timings[0].duration_seconds = 99.0
    with pytest.raises(ValidationError, match="frozen"):
        timeline.render_timing_contract.effective_transition_duration_seconds = 0.0

    serialized["scene_timings"].clear()
    serialized["render_timing_contract"]["scene_end_times_seconds"].clear()
    assert len(timeline.scene_timings) == 8
    assert renderer_bridge.AuthoritativeNarrationTimeline.model_validate_json(text) == timeline
    assert json.loads(text)["scene_timings"]


def test_timeline_is_detached_from_caller_owned_state_and_profile(tmp_path) -> None:
    state = _state_with_measured_duration(tmp_path, 35.0)
    profile = _profile()
    timeline = renderer_bridge.build_authoritative_narration_timeline(
        state,
        profile=profile,
    )

    state.run_plan.scene_execution_plans.clear()
    profile_payload = profile.model_dump(mode="python")
    profile_payload["transition_profile_id"] = "clean_soft_cut"

    assert len(timeline.scene_timings) == 8
    assert timeline.render_timing_contract.configured_transition_duration_seconds == 0.8


def test_outcome_preserves_exact_unrounded_measured_float(tmp_path) -> None:
    state = _state_with_measured_duration(tmp_path, 14.456381543211506)
    timeline = renderer_bridge.build_authoritative_narration_timeline(
        state,
        profile=_profile(),
    )

    outcome = AuthorizedRenderLifecycleOutcome(
        state=state,
        narration_timeline=timeline,
        render_output_path=tmp_path / "video.mp4",
    )

    assert outcome.narration_timeline.processed_duration_seconds == (
        state.processed_narration_measurement.measured_duration_assessment.actual_duration_seconds
    )


def test_builder_cannot_reach_external_or_artifact_boundaries(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state_with_measured_duration(tmp_path, 35.0)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("measured timeline builder reached a forbidden boundary")

    monkeypatch.setattr(renderer_bridge, "_stream_sha256", forbidden)
    monkeypatch.setattr(
        narration_measurement,
        "probe_single_audio_stream_duration",
        forbidden,
    )
    monkeypatch.setattr(persistence, "persist_execution_snapshot", forbidden)
    monkeypatch.setattr(
        renderer_bridge,
        "build_renderer_plan_from_accepted_candidates",
        forbidden,
    )

    timeline = renderer_bridge.build_authoritative_narration_timeline(
        state,
        profile=_profile(),
    )

    assert timeline.processed_duration_seconds == 35.0
