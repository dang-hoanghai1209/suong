from __future__ import annotations

from copy import deepcopy
import json

from tella.topic_production.plan_only_application import (
    PlanOnlyApplication,
    dispatch_plan_only_request,
)
from tella.topic_production.scene_planning import (
    ScenePlanAccessV1,
    ScenePlanCollectionV1,
)


def _created(*, accepted: bool = True) -> tuple[PlanOnlyApplication, str]:
    application = PlanOnlyApplication()
    result = application.create_run(
        {
            "schema_version": 1,
            "input_mode": "TOPIC",
            "source_content": "A calm story about learning to trust uncertainty",
            "language": "en",
            "character_scope": "recurring_female",
            "requested_scene_count": 8,
        }
    )
    run = result["run"]
    assert isinstance(run, dict)
    run_id = str(run["run_id"])
    if accepted:
        result = application.accept_story_plan(
            run_id,
            {"schema_version": 1, "current_revision_id": "revision-0001"},
        )
        assert result is not None and result["error"] is None
    return application, run_id


def _initialized() -> tuple[PlanOnlyApplication, str, dict[str, object]]:
    application, run_id = _created()
    result = application.initialize_scene_plan(
        run_id,
        {"schema_version": 1, "accepted_story_revision_id": "revision-0001"},
    )
    assert result is not None and result["error"] is None
    return application, run_id, result["access"]["collection"]


def _base(collection: dict[str, object]) -> str:
    return str(collection["collection_revision_id"])


def test_initialization_requires_current_accepted_storyplan_and_is_deterministic() -> None:
    application, run_id = _created(accepted=False)
    locked = application.get_scene_plan(run_id)
    assert locked is not None
    assert locked["editable"] is False
    assert locked["blocker_codes"] == [
        "STORYPLAN_NOT_ACCEPTED",
        "ACCEPTED_STORYPLAN_NOT_CURRENT",
    ]
    rejected = application.initialize_scene_plan(
        run_id,
        {"schema_version": 1, "accepted_story_revision_id": "revision-0001"},
    )
    assert rejected is not None
    assert rejected["error"]["code"] == "SCENE_PLANNING_NOT_AUTHORIZED"

    application.accept_story_plan(
        run_id, {"schema_version": 1, "current_revision_id": "revision-0001"}
    )
    initialized = application.initialize_scene_plan(
        run_id,
        {"schema_version": 1, "accepted_story_revision_id": "revision-0001"},
    )
    assert initialized is not None
    collection = initialized["access"]["collection"]
    assert collection["collection_revision_id"] == "scene-plan-revision-0001"
    assert [scene["scene_id"] for scene in collection["scenes"]] == [
        f"scene_{number:02d}" for number in range(1, 9)
    ]
    assert [scene["scene_revision_id"] for scene in collection["scenes"]] == [
        f"scene-revision-0001-{number:02d}" for number in range(1, 9)
    ]
    assert collection["source_story_revision_id"] == "revision-0001"
    assert collection["render_authority"] is False
    assert collection["media_capability"] is False


def test_replan_invalidates_existing_scene_plan_authority() -> None:
    application, run_id, _ = _initialized()
    result = application.request_replan(
        run_id,
        {
            "schema_version": 1,
            "base_revision_id": "revision-0001",
            "feedback": ["story_focus_incorrect"],
            "custom_note": None,
        },
    )
    assert result is not None and result["error"] is None
    access = application.get_scene_plan(run_id)
    assert access is not None
    assert access["editable"] is False
    assert "STORYPLAN_NOT_ACCEPTED" in access["blocker_codes"]
    assert "SCENE_PLAN_SOURCE_STORYPLAN_STALE" in access["blocker_codes"]

    accepted = application.accept_story_plan(
        run_id,
        {"schema_version": 1, "current_revision_id": "revision-0002"},
    )
    assert accepted is not None and accepted["error"] is None
    reinitialized = application.initialize_scene_plan(
        run_id,
        {"schema_version": 1, "accepted_story_revision_id": "revision-0002"},
    )
    assert reinitialized is not None and reinitialized["error"] is None
    collection = reinitialized["access"]["collection"]
    assert collection["source_story_revision_id"] == "revision-0002"
    assert collection["collection_revision_id"] == "scene-plan-revision-0002"
    assert [item["reason"] for item in collection["revision_history"]] == [
        "INITIAL_DERIVATION",
        "STORYPLAN_REVISION_DERIVATION",
    ]


def test_save_is_allowlisted_optimistic_detached_and_immutable() -> None:
    application, run_id, collection = _initialized()
    scene = collection["scenes"][0]
    prior = deepcopy(collection)
    result = application.mutate_scene_plan(
        run_id,
        "save",
        {
            "schema_version": 1,
            "base_collection_revision_id": _base(collection),
            "base_scene_revision_id": scene["scene_revision_id"],
            "changes": {"environment": "Quiet kitchen", "planning_note": "Keep warm light."},
        },
        scene_id=scene["scene_id"],
    )
    assert result is not None and result["error"] is None
    updated = result["access"]["collection"]
    assert updated["collection_revision_id"] == "scene-plan-revision-0002"
    assert updated["scenes"][0]["environment"] == "Quiet kitchen"
    assert collection == prior

    stale = application.mutate_scene_plan(
        run_id,
        "save",
        {
            "schema_version": 1,
            "base_collection_revision_id": _base(collection),
            "base_scene_revision_id": scene["scene_revision_id"],
            "changes": {"environment": "Stale"},
        },
        scene_id=scene["scene_id"],
    )
    assert stale is not None
    assert stale["error"]["code"] == "STALE_COLLECTION_REVISION"

    forbidden = application.mutate_scene_plan(
        run_id,
        "save",
        {
            "schema_version": 1,
            "base_collection_revision_id": updated["collection_revision_id"],
            "base_scene_revision_id": updated["scenes"][0]["scene_revision_id"],
            "changes": {"run_id": "forged"},
        },
        scene_id=scene["scene_id"],
    )
    assert forbidden is not None
    assert forbidden["error"]["code"] == "INVALID_SCENE_EDIT"


def test_noop_nonpositive_and_stale_scene_edits_reject() -> None:
    application, run_id, collection = _initialized()
    scene = collection["scenes"][0]
    common = {
        "schema_version": 1,
        "base_collection_revision_id": _base(collection),
        "base_scene_revision_id": scene["scene_revision_id"],
    }
    no_op = application.mutate_scene_plan(
        run_id,
        "save",
        {**common, "changes": {"objective": scene["objective"]}},
        scene_id=scene["scene_id"],
    )
    assert no_op is not None and no_op["error"]["code"] == "NO_SCENE_CHANGES"
    invalid = application.mutate_scene_plan(
        run_id,
        "save",
        {**common, "changes": {"planned_duration_seconds": 0}},
        scene_id=scene["scene_id"],
    )
    assert invalid is not None and invalid["error"]["code"] == "INVALID_SCENE_EDIT"
    stale = application.mutate_scene_plan(
        run_id,
        "save",
        {
            **common,
            "base_scene_revision_id": "scene-revision-9999-01",
            "changes": {"environment": "x"},
        },
        scene_id=scene["scene_id"],
    )
    assert stale is not None and stale["error"]["code"] == "STALE_SCENE_REVISION"


def test_reorder_preserves_coverage_and_rejects_edge_move() -> None:
    application, run_id, collection = _initialized()
    rejected = application.mutate_scene_plan(
        run_id,
        "reorder",
        {
            "schema_version": 1,
            "base_collection_revision_id": _base(collection),
            "scene_id": "scene_01",
            "direction": "UP",
        },
    )
    assert rejected is not None and rejected["error"]["code"] == "INVALID_SCENE_MOVE"
    moved = application.mutate_scene_plan(
        run_id,
        "reorder",
        {
            "schema_version": 1,
            "base_collection_revision_id": _base(collection),
            "scene_id": "scene_02",
            "direction": "UP",
        },
    )
    assert moved is not None and moved["error"] is None
    current = moved["access"]["collection"]
    assert [scene["scene_id"] for scene in current["scenes"][:2]] == ["scene_02", "scene_01"]
    assert current["source_coverage_valid"] is True


def test_split_merge_and_source_coverage_are_exact() -> None:
    application, run_id, collection = _initialized()
    scene = collection["scenes"][0]
    start = scene["source_coverage"]["start"]
    end = scene["source_coverage"]["end"]
    split_at = start + (end - start) // 2
    split = application.mutate_scene_plan(
        run_id,
        "split",
        {
            "schema_version": 1,
            "base_collection_revision_id": _base(collection),
            "scene_id": scene["scene_id"],
            "base_scene_revision_id": scene["scene_revision_id"],
            "split_at": split_at,
            "first_duration_seconds": scene["planned_duration_seconds"] / 2,
            "second_duration_seconds": scene["planned_duration_seconds"] / 2,
        },
    )
    assert split is not None and split["error"] is None
    split_collection = split["access"]["collection"]
    first, second = split_collection["scenes"][:2]
    assert first["source_coverage"]["start"] == start
    assert first["source_coverage"]["end"] == second["source_coverage"]["start"]
    assert second["source_coverage"]["end"] == end
    assert split_collection["source_coverage_valid"] is True

    merged = application.mutate_scene_plan(
        run_id,
        "merge",
        {
            "schema_version": 1,
            "base_collection_revision_id": _base(split_collection),
            "first_scene_id": first["scene_id"],
            "second_scene_id": second["scene_id"],
        },
    )
    assert merged is not None and merged["error"] is None
    assert merged["access"]["collection"]["scenes"][0]["source_coverage"] == {
        "start": start,
        "end": end,
        "overlap_draft": False,
    }


def test_split_rejects_invalid_coverage_and_duration() -> None:
    application, run_id, collection = _initialized()
    scene = collection["scenes"][0]
    for split_at, first, second, code in (
        (
            scene["source_coverage"]["start"],
            1.0,
            scene["planned_duration_seconds"] - 1,
            "INVALID_SPLIT_REQUEST",
        ),
        (
            scene["source_coverage"]["start"] + 1,
            1.0,
            scene["planned_duration_seconds"],
            "SPLIT_DURATION_MISMATCH",
        ),
    ):
        result = application.mutate_scene_plan(
            run_id,
            "split",
            {
                "schema_version": 1,
                "base_collection_revision_id": _base(collection),
                "scene_id": scene["scene_id"],
                "base_scene_revision_id": scene["scene_revision_id"],
                "split_at": split_at,
                "first_duration_seconds": first,
                "second_duration_seconds": second,
            },
        )
        assert result is not None and result["error"]["code"] == code


def test_nonadjacent_merge_rejects() -> None:
    application, run_id, collection = _initialized()
    result = application.mutate_scene_plan(
        run_id,
        "merge",
        {
            "schema_version": 1,
            "base_collection_revision_id": _base(collection),
            "first_scene_id": "scene_01",
            "second_scene_id": "scene_03",
        },
    )
    assert result is not None and result["error"]["code"] == "SCENES_NOT_ADJACENT"


def test_duplicate_draft_blocks_acceptance() -> None:
    application, run_id, collection = _initialized()
    result = application.mutate_scene_plan(
        run_id,
        "duplicate",
        {
            "schema_version": 1,
            "base_collection_revision_id": _base(collection),
            "scene_id": "scene_01",
        },
    )
    assert result is not None and result["error"] is None
    current = result["access"]["collection"]
    duplicate = current["scenes"][1]
    assert duplicate["status"] == "WARNING"
    assert duplicate["source_coverage"]["overlap_draft"] is True
    accepted = application.mutate_scene_plan(
        run_id,
        "accept",
        {
            "schema_version": 1,
            "base_collection_revision_id": _base(current),
            "scene_id": duplicate["scene_id"],
            "base_scene_revision_id": duplicate["scene_revision_id"],
        },
        scene_id=duplicate["scene_id"],
    )
    assert accepted is not None
    assert accepted["error"]["code"] == "SCENE_ACCEPTANCE_BLOCKED"


def test_scene_acceptance_and_revision_request_create_revisions() -> None:
    application, run_id, collection = _initialized()
    scene = collection["scenes"][0]
    accepted = application.mutate_scene_plan(
        run_id,
        "accept",
        {
            "schema_version": 1,
            "base_collection_revision_id": _base(collection),
            "scene_id": scene["scene_id"],
            "base_scene_revision_id": scene["scene_revision_id"],
        },
        scene_id=scene["scene_id"],
    )
    assert accepted is not None and accepted["error"] is None
    current = accepted["access"]["collection"]
    assert current["scenes"][0]["status"] == "ACCEPTED_FOR_VISUAL_PLANNING"
    assert current["render_authority"] is False

    requested = application.mutate_scene_plan(
        run_id,
        "request-revision",
        {
            "schema_version": 1,
            "base_collection_revision_id": _base(current),
            "scene_id": scene["scene_id"],
            "base_scene_revision_id": current["scenes"][0]["scene_revision_id"],
            "reason_code": "CONTINUITY_NEEDS_REVISION",
            "note": "Clarify the handoff.",
        },
        scene_id=scene["scene_id"],
    )
    assert requested is not None and requested["error"] is None
    assert requested["access"]["collection"]["scenes"][0]["status"] == "REVISION_REQUESTED"


def test_restore_prior_scene_revision_creates_new_revision() -> None:
    application, run_id, collection = _initialized()
    original = collection["scenes"][0]
    edited = application.mutate_scene_plan(
        run_id,
        "save",
        {
            "schema_version": 1,
            "base_collection_revision_id": _base(collection),
            "base_scene_revision_id": original["scene_revision_id"],
            "changes": {"environment": "Changed environment"},
        },
        scene_id=original["scene_id"],
    )
    assert edited is not None and edited["error"] is None
    current = edited["access"]["collection"]
    restored = application.mutate_scene_plan(
        run_id,
        "restore",
        {
            "schema_version": 1,
            "base_collection_revision_id": _base(current),
            "base_scene_revision_id": current["scenes"][0]["scene_revision_id"],
            "restore_scene_revision_id": original["scene_revision_id"],
            "reason": "Restore accepted planning detail",
        },
        scene_id=original["scene_id"],
    )
    assert restored is not None and restored["error"] is None
    restored_scene = restored["access"]["collection"]["scenes"][0]
    assert restored_scene["environment"] == original["environment"]
    assert restored_scene["scene_revision_id"] != original["scene_revision_id"]


def test_exact_lookups_history_json_roundtrip_and_detachment() -> None:
    application, run_id, collection = _initialized()
    scene_id = collection["scenes"][0]["scene_id"]
    assert application.get_planned_scene(run_id, scene_id)["scene_id"] == scene_id
    assert application.get_planned_scene(run_id, "scene_unknown") is None
    history = application.get_scene_plan_history(run_id)
    assert history["current_collection_revision_id"] == "scene-plan-revision-0001"
    assert application.get_scene_plan_revision(run_id, "scene-plan-revision-0001") is not None
    assert application.get_scene_plan_revision(run_id, "scene-plan-revision-9999") is None
    assert (
        application.get_planned_scene_history(run_id, scene_id)["revisions"][0]["scene_id"]
        == scene_id
    )

    access = application.get_scene_plan(run_id)
    validated = ScenePlanAccessV1.model_validate_json(json.dumps(access))
    ScenePlanCollectionV1.model_validate_json(json.dumps(access["collection"]))
    access["collection"]["scenes"][0]["objective"] = "forged"
    restored = application.get_scene_plan(run_id)
    assert restored["collection"]["scenes"][0]["objective"] != "forged"
    assert validated.render_authority is False


def test_facade_routes_and_unknown_paths_are_bounded() -> None:
    application, run_id, _ = _initialized()
    prefix = f"/api/v1/plan-only/runs/{run_id}/scene-plan"
    assert dispatch_plan_only_request(application, method="GET", path=prefix).status_code == 200
    assert (
        dispatch_plan_only_request(
            application, method="GET", path=f"{prefix}/scenes/scene_01"
        ).status_code
        == 200
    )
    assert (
        dispatch_plan_only_request(
            application, method="GET", path=f"{prefix}/revisions/scene-plan-revision-0001"
        ).status_code
        == 200
    )
    assert (
        dispatch_plan_only_request(
            application, method="GET", path=f"{prefix}/scenes/missing"
        ).status_code
        == 404
    )
    assert (
        dispatch_plan_only_request(application, method="GET", path=f"{prefix}/unknown").status_code
        == 404
    )


def test_scene_planning_contains_no_secret_or_execution_authority() -> None:
    application, run_id, _ = _initialized()
    payload = json.dumps(application.get_scene_plan(run_id), sort_keys=True)
    assert "render_authority" in payload
    assert '"render_authority": false' in payload
    assert '"media_capability": false' in payload
    assert "password" not in payload.lower()
    assert "api_key" not in payload.lower()
    assert ":\\" not in payload
    assert "external_calls" not in payload
