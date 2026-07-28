from __future__ import annotations

from copy import deepcopy
import json

import pytest

from tella.topic_production.composition_planning import (
    CompositionStatus,
    NormalizedCropV1,
    NormalizedGeometryV1,
)
from tella.topic_production.plan_only_application import (
    PLAN_ONLY_API_PREFIX,
    dispatch_plan_only_request,
)
from tests.test_plan_only_local_host import _http_request, _json_request, _running_host
from tests.test_plan_only_visual_candidates import (
    DeterministicVisualProvider,
    _accepted_scene_application,
    _generate_payload,
    _mutation_payload,
)


def _accepted_visual_application(tmp_path):
    provider = DeterministicVisualProvider()
    application, run_id, _, scene = _accepted_scene_application(tmp_path, provider)
    access = application.get_visual_candidate_access(run_id, scene["scene_id"])
    generated = application.generate_visual_candidates(
        run_id, scene["scene_id"], _generate_payload(access, count=1)
    )
    collection = generated["access"]["collection"]
    candidate = collection["candidates"][0]
    accepted = application.mutate_visual_candidate(
        run_id,
        scene["scene_id"],
        candidate["candidate_id"],
        "accept",
        _mutation_payload(collection, candidate),
    )
    return application, provider, run_id, scene, accepted["access"]["collection"]


def _initialize(application, run_id, scene_collection_revision_id):
    return application.initialize_compositions(
        run_id,
        {
            "schema_version": 1,
            "scene_plan_collection_revision_id": scene_collection_revision_id,
        },
    )


def test_access_and_deterministic_partial_initialization(tmp_path) -> None:
    application, provider, run_id, scene, visual = _accepted_visual_application(tmp_path)
    access = application.get_composition_access(run_id)
    assert access["initialization_authorized"] is True
    assert "MISSING_ACCEPTED_VISUAL" in access["blocker_codes"]

    result = _initialize(application, run_id, visual["scene_plan_collection_revision_id"])
    collection = result["access"]["collection"]
    composition = collection["compositions"][0]
    assert composition["scene_id"] == scene["scene_id"]
    assert composition["accepted_candidate_id"] == visual["current_accepted_candidate_id"]
    assert composition["accepted_candidate_sha256"] == visual["candidates"][0]["sha256"]
    assert composition["position"] == 1
    assert collection["missing_scene_ids"]
    assert collection["overall_ready_for_timeline_planning"] is False
    assert result["access"]["editable"] is True
    assert provider.calls and len(provider.calls) == 1
    assert all(
        composition[key] is False
        for key in (
            "render_authority",
            "renderer_execution_authority",
            "video_render_authority",
            "timeline_execution_authority",
            "final_media_capability",
            "narration_generation_capability",
            "tts_capability",
        )
    )


def test_save_restore_accept_revision_and_detachment(tmp_path) -> None:
    application, _, run_id, scene, visual = _accepted_visual_application(tmp_path)
    initialized = _initialize(application, run_id, visual["scene_plan_collection_revision_id"])
    collection = initialized["access"]["collection"]
    first = collection["compositions"][0]
    saved = application.mutate_composition(
        run_id,
        scene["scene_id"],
        "save",
        {
            "schema_version": 1,
            "base_collection_revision_id": collection["collection_revision_id"],
            "base_composition_revision_id": first["composition_revision_id"],
            "changes": {
                "fit_mode": "MANUAL_CROP",
                "crop": {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8},
                "overlay_color": "#1A2B3C",
                "motion_intent": {
                    "mode": "SLOW_ZOOM_IN",
                    "start_scale": 1.0,
                    "end_scale": 1.1,
                    "start_anchor_x": 0.5,
                    "start_anchor_y": 0.5,
                    "end_anchor_x": 0.52,
                    "end_anchor_y": 0.48,
                },
                "transition_intent": "CROSSFADE",
                "transition_duration_seconds": 0.4,
            },
        },
    )
    assert saved["error"] is None
    second = saved["collection"]["compositions"][0]
    assert second["composition_revision_number"] == 2
    assert first["fit_mode"] == "COVER"
    history_before = application.get_composition_history(run_id, scene["scene_id"])
    history_alias = deepcopy(history_before)
    history_before["revisions"][0]["fit_mode"] = "MANUAL_CROP"
    assert application.get_composition_history(run_id, scene["scene_id"]) == history_alias

    restored = application.mutate_composition(
        run_id,
        scene["scene_id"],
        "restore",
        {
            "schema_version": 1,
            "base_collection_revision_id": saved["collection"]["collection_revision_id"],
            "base_composition_revision_id": second["composition_revision_id"],
            "restore_composition_revision_id": first["composition_revision_id"],
            "reason": "Restore original bounded framing.",
        },
    )
    third = restored["collection"]["compositions"][0]
    assert third["composition_revision_number"] == 3
    assert third["fit_mode"] == "COVER"

    accepted = application.mutate_composition(
        run_id,
        scene["scene_id"],
        "accept",
        {
            "schema_version": 1,
            "base_collection_revision_id": restored["collection"]["collection_revision_id"],
            "base_composition_revision_id": third["composition_revision_id"],
            "reason_code": "OTHER_BOUNDED_NOTE",
            "note": "Accepted for timeline planning only.",
        },
    )
    final = accepted["collection"]["compositions"][0]
    assert final["status"] == CompositionStatus.ACCEPTED_FOR_TIMELINE_PLANNING
    assert final["accepted_for_timeline_planning"] is True
    assert final["timeline_execution_authority"] is False


@pytest.mark.parametrize(
    "changes",
    [
        {
            "placement": {
                "x": True,
                "y": 0.0,
                "width": 1.0,
                "height": 1.0,
                "anchor_x": 0.5,
                "anchor_y": 0.5,
                "scale": 1.0,
                "rotation_degrees": 0.0,
                "opacity": 1.0,
            }
        },
        {
            "placement": {
                "x": float("nan"),
                "y": 0.0,
                "width": 1.0,
                "height": 1.0,
                "anchor_x": 0.5,
                "anchor_y": 0.5,
                "scale": 1.0,
                "rotation_degrees": 0.0,
                "opacity": 1.0,
            }
        },
        {"fit_mode": "MANUAL_CROP", "crop": {"x": 0.9, "y": 0.0, "width": 0.2, "height": 1.0}},
        {"overlay_color": "url(https://example.invalid/x)"},
        {"artifact_url": "C:/secret.png"},
        {"transition_duration_seconds": 99.0},
    ],
)
def test_edit_allowlist_fails_closed(tmp_path, changes) -> None:
    application, _, run_id, scene, visual = _accepted_visual_application(tmp_path)
    initialized = _initialize(application, run_id, visual["scene_plan_collection_revision_id"])
    collection = initialized["access"]["collection"]
    composition = collection["compositions"][0]
    result = application.mutate_composition(
        run_id,
        scene["scene_id"],
        "save",
        {
            "schema_version": 1,
            "base_collection_revision_id": collection["collection_revision_id"],
            "base_composition_revision_id": composition["composition_revision_id"],
            "changes": changes,
        },
    )
    assert result["error"]["code"] == "INVALID_COMPOSITION_EDIT"
    assert application.get_composition(run_id, scene["scene_id"]) == composition


def test_stale_write_no_op_and_duplicate_accept_reject(tmp_path) -> None:
    application, _, run_id, scene, visual = _accepted_visual_application(tmp_path)
    initialized = _initialize(application, run_id, visual["scene_plan_collection_revision_id"])
    collection = initialized["access"]["collection"]
    composition = collection["compositions"][0]
    base = {
        "schema_version": 1,
        "base_collection_revision_id": collection["collection_revision_id"],
        "base_composition_revision_id": composition["composition_revision_id"],
    }
    no_op = application.mutate_composition(
        run_id,
        scene["scene_id"],
        "save",
        {**base, "changes": {"fit_mode": "COVER"}},
    )
    assert no_op["error"]["code"] == "NO_OP_COMPOSITION_EDIT"
    stale = application.mutate_composition(
        run_id,
        scene["scene_id"],
        "save",
        {
            **base,
            "base_collection_revision_id": "composition-collection-revision-9999",
            "changes": {"fit_mode": "CONTAIN"},
        },
    )
    assert stale["error"]["code"] == "STALE_COMPOSITION_COLLECTION"

    accepted = application.mutate_composition(
        run_id,
        scene["scene_id"],
        "accept",
        {**base, "reason_code": "OTHER_BOUNDED_NOTE", "note": None},
    )
    final = accepted["collection"]["compositions"][0]
    duplicate = application.mutate_composition(
        run_id,
        scene["scene_id"],
        "accept",
        {
            "schema_version": 1,
            "base_collection_revision_id": accepted["collection"]["collection_revision_id"],
            "base_composition_revision_id": final["composition_revision_id"],
            "reason_code": "OTHER_BOUNDED_NOTE",
            "note": None,
        },
    )
    assert duplicate["error"]["code"] == "COMPOSITION_ALREADY_ACCEPTED"


def test_upstream_candidate_change_supersedes_without_rebinding(tmp_path) -> None:
    application, _, run_id, scene, visual = _accepted_visual_application(tmp_path)
    initialized = _initialize(application, run_id, visual["scene_plan_collection_revision_id"])
    original = initialized["access"]["collection"]["compositions"][0]
    candidate = visual["candidates"][0]
    rejected = application.mutate_visual_candidate(
        run_id,
        scene["scene_id"],
        candidate["candidate_id"],
        "reject",
        _mutation_payload(visual, candidate),
    )
    assert rejected["error"] is None
    access = application.get_composition_access(run_id)
    assert access["collection"] is None
    assert access["editable"] is False
    history = application.get_composition_history(run_id, scene["scene_id"])
    superseded = history["revisions"][-1]
    assert superseded["status"] == "SUPERSEDED"
    assert superseded["accepted_candidate_id"] == original["accepted_candidate_id"]


def test_models_are_json_safe_immutable_and_reject_nonfinite() -> None:
    geometry = NormalizedGeometryV1(
        x=0.0,
        y=0.0,
        width=1.0,
        height=1.0,
        anchor_x=0.5,
        anchor_y=0.5,
        scale=1.0,
        rotation_degrees=0.0,
        opacity=1.0,
    )
    assert json.loads(geometry.model_dump_json())["scale"] == 1.0
    with pytest.raises(Exception):
        NormalizedCropV1(x=0.0, y=0.0, width=float("inf"), height=1.0)
    with pytest.raises(Exception):
        geometry.x = 0.2


def test_composition_routes_are_exact_and_methods_fail_closed(tmp_path) -> None:
    application, _, run_id, _, visual = _accepted_visual_application(tmp_path)
    prefix = f"{PLAN_ONLY_API_PREFIX}/runs/{run_id}/compositions"
    initialized = dispatch_plan_only_request(
        application,
        method="POST",
        path=f"{prefix}/initialize",
        body={
            "schema_version": 1,
            "scene_plan_collection_revision_id": visual["scene_plan_collection_revision_id"],
        },
    )
    assert initialized.status_code == 200
    assert (
        dispatch_plan_only_request(application, method="GET", path=f"{prefix}/unknown").status_code
        == 404
    )
    assert dispatch_plan_only_request(application, method="DELETE", path=prefix).status_code == 404


def test_loopback_composition_routes_and_zero_provider_execution(tmp_path) -> None:
    application, provider, run_id, _, visual = _accepted_visual_application(tmp_path)
    calls_before = len(provider.calls)
    prefix = f"/api/v1/plan-only/runs/{run_id}/compositions"
    with _running_host(tmp_path, application=application) as address:
        status, initialized, _ = _json_request(
            address,
            "POST",
            f"{prefix}/initialize",
            {
                "schema_version": 1,
                "scene_plan_collection_revision_id": visual["scene_plan_collection_revision_id"],
            },
        )
        assert status == 200
        assert initialized["access"]["collection"]["compositions"]
        get_status, access, _ = _http_request(address, "GET", f"{prefix}/access")
        assert get_status == 200
        assert access["render_authority"] is False
        method_status, method_error, _ = _http_request(address, "DELETE", f"{prefix}/access")
        assert method_status == 405
        assert method_error["error"]["code"] == "METHOD_NOT_ALLOWED"
        malformed_status, malformed, _ = _http_request(
            address,
            "POST",
            f"{prefix}/initialize",
            body=b"{",
            headers={"Content-Type": "application/json", "Content-Length": "1"},
        )
        assert malformed_status == 400
        assert malformed["error"]["code"] == "MALFORMED_JSON"
    assert len(provider.calls) == calls_before
