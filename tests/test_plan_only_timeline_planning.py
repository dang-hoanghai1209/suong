from __future__ import annotations

from copy import deepcopy

import pytest

from tella.topic_production.timeline_planning import planned_seconds_to_ms
from tests.test_plan_only_composition_planning import _initialize as initialize_compositions
from tests.test_plan_only_local_host import _http_request, _json_request, _running_host
from tests.test_plan_only_visual_candidates import (
    DeterministicVisualProvider,
    _accepted_scene_application,
    _generate_payload,
    _mutation_payload,
)


def _timeline_ready_application(tmp_path):
    provider = DeterministicVisualProvider()
    application, run_id, scene_collection, first_scene = _accepted_scene_application(
        tmp_path, provider
    )
    current = scene_collection
    for scene in current["scenes"][1:]:
        transitioned = application.mutate_scene_plan(
            run_id,
            "accept",
            {
                "schema_version": 1,
                "base_collection_revision_id": current["collection_revision_id"],
                "scene_id": scene["scene_id"],
                "base_scene_revision_id": scene["scene_revision_id"],
            },
            scene_id=scene["scene_id"],
        )
        current = transitioned["access"]["collection"]

    visuals = {}
    for scene in current["scenes"]:
        access = application.get_visual_candidate_access(run_id, scene["scene_id"])
        generated = application.generate_visual_candidates(
            run_id, scene["scene_id"], _generate_payload(access, count=1)
        )
        visual = generated["access"]["collection"]
        candidate = visual["candidates"][0]
        accepted = application.mutate_visual_candidate(
            run_id,
            scene["scene_id"],
            candidate["candidate_id"],
            "accept",
            _mutation_payload(visual, candidate),
        )
        visuals[scene["scene_id"]] = accepted["access"]["collection"]

    composition_result = initialize_compositions(
        application,
        run_id,
        current["collection_revision_id"],
    )
    composition_collection = composition_result["access"]["collection"]
    for composition in list(composition_collection["compositions"]):
        reviewed = application.mutate_composition(
            run_id,
            composition["scene_id"],
            "accept",
            {
                "schema_version": 1,
                "base_collection_revision_id": composition_collection["collection_revision_id"],
                "base_composition_revision_id": composition["composition_revision_id"],
                "reason_code": "OTHER_BOUNDED_NOTE",
                "note": "Accepted for timeline planning only.",
            },
        )
        composition_collection = reviewed["collection"]
    assert composition_collection["overall_ready_for_timeline_planning"] is True
    return (
        application,
        provider,
        run_id,
        current,
        first_scene,
        visuals,
        composition_collection,
    )


def _initialize_timeline(application, run_id, composition_collection):
    return application.initialize_timeline(
        run_id,
        {
            "schema_version": 1,
            "composition_collection_revision_id": composition_collection["collection_revision_id"],
        },
    )


@pytest.mark.parametrize(
    ("seconds", "milliseconds"),
    [
        (1, 1000),
        (1.2344, 1234),
        (1.2345, 1235),
        ("4.375", 4375),
    ],
)
def test_decimal_duration_conversion_is_stable(seconds, milliseconds) -> None:
    assert planned_seconds_to_ms(seconds) == milliseconds


@pytest.mark.parametrize("value", [True, 0, -1, float("nan"), float("inf")])
def test_decimal_duration_conversion_rejects_invalid_values(value) -> None:
    with pytest.raises(ValueError):
        planned_seconds_to_ms(value)


def test_access_requires_all_current_accepted_compositions(tmp_path) -> None:
    provider = DeterministicVisualProvider()
    application, run_id, _, _ = _accepted_scene_application(tmp_path, provider)
    access = application.get_timeline_access(run_id)
    assert access["editable"] is False
    assert access["initialization_authorized"] is False
    assert "COMPOSITION_COLLECTION_UNAVAILABLE" in access["blocker_codes"]
    assert access["timeline_execution_authority"] is False
    assert access["audio_generation_capability"] is False


def test_deterministic_initialization_and_integer_arithmetic(tmp_path) -> None:
    (
        application,
        provider,
        run_id,
        scenes,
        _,
        _,
        compositions,
    ) = _timeline_ready_application(tmp_path)
    calls_before = len(provider.calls)
    result = _initialize_timeline(application, run_id, compositions)
    collection = result["access"]["collection"]
    assert [item["scene_id"] for item in collection["segments"]] == [
        item["scene_id"] for item in scenes["scenes"]
    ]
    previous = None
    for item in collection["segments"]:
        assert type(item["start_ms"]) is int
        assert type(item["end_ms"]) is int
        assert type(item["duration_ms"]) is int
        assert item["end_ms"] == item["start_ms"] + item["duration_ms"]
        assert (
            item["effective_visible_duration_ms"] == item["duration_ms"] - item["transition_in_ms"]
        )
        if previous is not None:
            assert item["start_ms"] == previous["end_ms"] - previous["transition_out_ms"]
            assert item["transition_in_ms"] == previous["transition_out_ms"]
        previous = item
        coverage = item["source_coverage"]
        assert (
            item["canonical_narration_segment"]
            == scenes["scenes"][item["position"] - 1]["narration_segment"]
        )
        assert item["narration_alignment"]["planned_window_start_ms"] == item["start_ms"]
        assert item["narration_alignment"]["planned_window_end_ms"] == item["end_ms"]
        assert coverage == {
            "start": scenes["scenes"][item["position"] - 1]["source_coverage"]["start"],
            "end": scenes["scenes"][item["position"] - 1]["source_coverage"]["end"],
        }
    assert collection["total_planned_duration_ms"] == sum(
        item["duration_ms"] for item in collection["segments"]
    )
    assert collection["effective_timeline_duration_ms"] == (
        collection["total_planned_duration_ms"] - collection["total_transition_overlap_ms"]
    )
    assert collection["narration_coverage_valid"] is True
    assert collection["ordering_valid"] is True
    assert len(provider.calls) == calls_before


def test_save_restore_accept_and_detached_history(tmp_path) -> None:
    application, _, run_id, _, _, _, compositions = _timeline_ready_application(tmp_path)
    initialized = _initialize_timeline(application, run_id, compositions)
    collection = initialized["access"]["collection"]
    first = collection["segments"][0]
    saved = application.mutate_timeline_segment(
        run_id,
        first["segment_id"],
        "save",
        {
            "schema_version": 1,
            "base_collection_revision_id": collection["collection_revision_id"],
            "base_segment_revision_id": first["segment_revision_id"],
            "changes": {
                "narration_window_start_ms": first["start_ms"] + 100,
                "narration_window_end_ms": first["end_ms"] - 100,
                "note": "Bounded planning window.",
            },
        },
    )
    assert saved["error"] is None
    second = saved["collection"]["segments"][0]
    assert second["segment_revision_number"] == 2
    assert first["narration_alignment"]["planned_window_start_ms"] == first["start_ms"]

    history = application.get_timeline_segment_history(run_id, first["segment_id"])
    detached = deepcopy(history)
    history["revisions"][0]["note"] = "mutated caller copy"
    assert application.get_timeline_segment_history(run_id, first["segment_id"]) == detached

    restored = application.mutate_timeline_segment(
        run_id,
        first["segment_id"],
        "restore",
        {
            "schema_version": 1,
            "base_collection_revision_id": saved["collection"]["collection_revision_id"],
            "base_segment_revision_id": second["segment_revision_id"],
            "restore_segment_revision_id": first["segment_revision_id"],
            "reason": "Restore original narration window.",
        },
    )
    third = restored["collection"]["segments"][0]
    assert third["segment_revision_number"] == 3
    assert third["narration_alignment"]["planned_window_start_ms"] == first["start_ms"]

    accepted = application.review_timeline(
        run_id,
        "accept",
        {
            "schema_version": 1,
            "base_collection_revision_id": restored["collection"]["collection_revision_id"],
            "reason_code": "OTHER_BOUNDED_NOTE",
            "note": "Accepted for execution review only.",
        },
    )
    final = accepted["collection"]
    assert final["accepted_for_execution_review"] is True
    assert final["accepted_timeline_revision_id"] == final["collection_revision_id"]
    assert final["timeline_execution_authority"] is False
    assert final["narration_generation_capability"] is False


@pytest.mark.parametrize(
    "changes",
    [
        {"narration_window_start_ms": True},
        {"narration_window_start_ms": -1},
        {"narration_window_start_ms": 999_999},
        {"canonical_narration_segment": "forged"},
        {"audio_path": "C:/secret.wav"},
        {"note": "url(https://example.invalid)"},
    ],
)
def test_timeline_edit_allowlist_fails_closed(tmp_path, changes) -> None:
    application, _, run_id, _, _, _, compositions = _timeline_ready_application(tmp_path)
    initialized = _initialize_timeline(application, run_id, compositions)
    collection = initialized["access"]["collection"]
    segment = collection["segments"][0]
    result = application.mutate_timeline_segment(
        run_id,
        segment["segment_id"],
        "save",
        {
            "schema_version": 1,
            "base_collection_revision_id": collection["collection_revision_id"],
            "base_segment_revision_id": segment["segment_revision_id"],
            "changes": changes,
        },
    )
    assert result["error"]["code"] == "INVALID_TIMELINE_EDIT"
    assert application.get_timeline_segment(run_id, segment["segment_id"]) == segment


def test_composition_change_supersedes_timeline_without_rebinding(tmp_path) -> None:
    application, _, run_id, _, _, _, compositions = _timeline_ready_application(tmp_path)
    initialized = _initialize_timeline(application, run_id, compositions)
    timeline = initialized["access"]["collection"]
    composition = compositions["compositions"][0]
    changed = application.mutate_composition(
        run_id,
        composition["scene_id"],
        "save",
        {
            "schema_version": 1,
            "base_collection_revision_id": compositions["collection_revision_id"],
            "base_composition_revision_id": composition["composition_revision_id"],
            "changes": {"note": "New composition planning note."},
        },
    )
    assert changed["error"] is None
    access = application.get_timeline_access(run_id)
    assert access["collection"] is None
    assert access["editable"] is False
    history = application.get_timeline_collection_history(run_id)
    superseded = history["revisions"][-1]
    assert superseded["status"] == "SUPERSEDED"
    assert superseded["source_authority_sha256"] == timeline["source_authority_sha256"]


def test_loopback_routes_and_zero_execution(tmp_path) -> None:
    application, provider, run_id, _, _, _, compositions = _timeline_ready_application(tmp_path)
    calls_before = len(provider.calls)
    prefix = f"/api/v1/plan-only/runs/{run_id}/timeline"
    with _running_host(tmp_path, application=application) as address:
        status, initialized, _ = _json_request(
            address,
            "POST",
            f"{prefix}/initialize",
            {
                "schema_version": 1,
                "composition_collection_revision_id": compositions["collection_revision_id"],
            },
        )
        assert status == 200
        assert initialized["access"]["collection"]["segments"]
        get_status, access, _ = _http_request(address, "GET", f"{prefix}/access")
        assert get_status == 200
        assert access["timeline_execution_authority"] is False
        method_status, method_error, _ = _http_request(address, "DELETE", f"{prefix}/access")
        assert method_status == 405
        assert method_error["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert len(provider.calls) == calls_before
