from __future__ import annotations

from copy import deepcopy
import json

import pytest

from tella.topic_production.plan_only_application import (
    PlanOnlyApplication,
    ProductionRunViewV1,
    StoryPlanReviewOperationResultV1,
    StoryPlanReviewViewV1,
    StoryPlanRevisionHistoryV1,
    StoryPlanRevisionV1,
    dispatch_plan_only_request,
)


def _payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "input_mode": "TOPIC",
        "source_content": "A calm story about learning to trust uncertainty",
        "language": "en",
        "character_scope": "recurring_female",
        "requested_scene_count": 8,
    }


def _created() -> tuple[PlanOnlyApplication, str]:
    application = PlanOnlyApplication()
    result = application.create_run(_payload())
    run = result["run"]
    assert isinstance(run, dict)
    return application, str(run["run_id"])


def test_initial_review_is_versioned_detached_and_render_locked() -> None:
    application, run_id = _created()

    review = application.get_review(run_id)
    assert review is not None
    assert review["schema_version"] == 1
    assert review["review_status"] == "UNREVIEWED"
    assert review["current_revision_id"] == "revision-0001"
    assert review["accepted_revision_id"] is None
    assert review["scene_planning_accepted"] is False
    assert review["render_authority"] is False
    assert review["media_capability"] is False
    assert review["process_local"] is True
    assert review["current_revision"]["reasons"] == ["INITIAL_PLAN"]

    review["current_revision"]["story_plan"]["topic"] = "mutated"
    restored = application.get_review(run_id)
    assert restored is not None
    assert restored["current_revision"]["story_plan"]["topic"] != "mutated"


def test_acceptance_is_bounded_and_duplicate_rejects() -> None:
    application, run_id = _created()
    payload = {"schema_version": 1, "current_revision_id": "revision-0001"}

    accepted = application.accept_story_plan(run_id, payload)
    assert accepted is not None
    assert accepted["error"] is None
    assert accepted["review"]["review_status"] == "ACCEPTED_FOR_SCENE_PLANNING"
    assert accepted["review"]["scene_planning_accepted"] is True
    assert accepted["review"]["render_authority"] is False
    assert accepted["review"]["media_capability"] is False

    duplicate = application.accept_story_plan(run_id, payload)
    assert duplicate is not None
    assert duplicate["review"] is None
    assert duplicate["error"]["code"] == "REVIEW_ALREADY_ACCEPTED"


def test_acceptance_rejects_stale_and_malformed_requests() -> None:
    application, run_id = _created()

    stale = application.accept_story_plan(
        run_id,
        {"schema_version": 1, "current_revision_id": "revision-9999"},
    )
    assert stale is not None
    assert stale["error"]["code"] == "STALE_STORYPLAN_REVISION"

    for schema_version in (None, True, 1.0, "1", 2):
        malformed = application.accept_story_plan(
            run_id,
            {
                "schema_version": schema_version,
                "current_revision_id": "revision-0001",
            },
        )
        assert malformed is not None
        assert malformed["error"]["code"] == "INVALID_REVIEW_REQUEST"


@pytest.mark.parametrize("status", ["BLOCKED", "FAILED"])
def test_non_planned_runs_cannot_be_accepted_or_replanned(status: str) -> None:
    application, run_id = _created()
    run = application._runs[run_id]  # noqa: SLF001 - explicit unsafe-state policy probe
    payload = run.model_dump(mode="python")
    payload["status"] = status
    application._runs[run_id] = ProductionRunViewV1.model_construct(  # noqa: SLF001
        **payload
    )

    accepted = application.accept_story_plan(
        run_id,
        {"schema_version": 1, "current_revision_id": "revision-0001"},
    )
    replanned = application.request_replan(
        run_id,
        {
            "schema_version": 1,
            "base_revision_id": "revision-0001",
            "feedback": ["story_focus_incorrect"],
            "custom_note": None,
        },
    )

    assert accepted is not None
    assert replanned is not None
    assert accepted["error"]["code"] == "RUN_NOT_ACCEPTABLE"
    assert replanned["error"]["code"] == "RUN_NOT_REPLANNABLE"


def test_replan_creates_strictly_increasing_immutable_revision() -> None:
    application, run_id = _created()
    initial = application.get_revision(run_id, "revision-0001")
    assert initial is not None

    result = application.request_replan(
        run_id,
        {
            "schema_version": 1,
            "base_revision_id": "revision-0001",
            "feedback": [
                "story_focus_incorrect",
                "emotional_progression_weak",
                "custom_note",
            ],
            "custom_note": "Keep the opening focused on emotional uncertainty.",
        },
    )

    assert result is not None
    assert result["error"] is None
    assert result["review"]["review_status"] == "REPLAN_REQUESTED"
    assert result["review"]["current_revision_id"] == "revision-0002"
    assert result["revision"]["revision_number"] == 2
    assert result["revision"]["reasons"] == [
        "story_focus_incorrect",
        "emotional_progression_weak",
        "custom_note",
    ]
    assert application.get_revision(run_id, "revision-0001") == initial

    result["revision"]["story_plan"]["topic"] = "caller mutation"
    assert (
        application.get_revision(run_id, "revision-0002")["story_plan"]["topic"]
        != "caller mutation"
    )


def test_replan_rejects_unknown_duplicate_unordered_and_overlong_feedback() -> None:
    application, run_id = _created()
    invalid_feedback = [
        ["unknown"],
        ["story_focus_incorrect", "story_focus_incorrect"],
        ["custom_note", "story_focus_incorrect"],
    ]
    for feedback in invalid_feedback:
        result = application.request_replan(
            run_id,
            {
                "schema_version": 1,
                "base_revision_id": "revision-0001",
                "feedback": feedback,
                "custom_note": "note" if "custom_note" in feedback else None,
            },
        )
        assert result is not None
        assert result["error"]["code"] == "INVALID_REPLAN_REQUEST"

    overlong = application.request_replan(
        run_id,
        {
            "schema_version": 1,
            "base_revision_id": "revision-0001",
            "feedback": ["custom_note"],
            "custom_note": "x" * 501,
        },
    )
    assert overlong is not None
    assert overlong["error"]["code"] == "INVALID_REPLAN_REQUEST"


def test_replan_rejects_stale_revision_without_changing_history() -> None:
    application, run_id = _created()
    before = application.list_revisions(run_id)

    result = application.request_replan(
        run_id,
        {
            "schema_version": 1,
            "base_revision_id": "revision-9999",
            "feedback": ["scene_count_unsuitable"],
            "custom_note": None,
        },
    )

    assert result is not None
    assert result["error"]["code"] == "STALE_STORYPLAN_REVISION"
    assert application.list_revisions(run_id) == before


def test_revision_history_and_lookup_are_exact_ordered_and_detached() -> None:
    application, run_id = _created()
    application.request_replan(
        run_id,
        {
            "schema_version": 1,
            "base_revision_id": "revision-0001",
            "feedback": ["narration_too_short"],
            "custom_note": None,
        },
    )

    history = application.list_revisions(run_id)
    assert history is not None
    assert [item["revision_id"] for item in history["revisions"]] == [
        "revision-0001",
        "revision-0002",
    ]
    assert history["current_revision_id"] == "revision-0002"
    assert application.get_revision(run_id, "revision-9999") is None
    assert application.get_revision("plan-00000000000000000000", "revision-0001") is None

    history["revisions"][0]["reasons"][0] = "mutated"
    assert application.list_revisions(run_id)["revisions"][0]["reasons"] == ["INITIAL_PLAN"]


def test_review_models_round_trip_json_and_reject_forged_authority() -> None:
    application, run_id = _created()
    review = application.get_review(run_id)
    assert review is not None

    restored = StoryPlanReviewViewV1.model_validate_json(json.dumps(review, ensure_ascii=False))
    revision = StoryPlanRevisionV1.model_validate_json(
        json.dumps(review["current_revision"], ensure_ascii=False)
    )
    history = StoryPlanRevisionHistoryV1.model_validate(application.list_revisions(run_id))
    assert restored.current_revision_id == revision.revision_id
    assert history.current_revision_id == revision.revision_id

    forged = deepcopy(review)
    forged["render_authority"] = True
    with pytest.raises(ValueError):
        StoryPlanReviewViewV1.model_validate(forged)


def test_operation_response_is_json_safe_and_contains_no_secret_or_path() -> None:
    application, run_id = _created()
    result = application.accept_story_plan(
        run_id,
        {"schema_version": 1, "current_revision_id": "revision-0001"},
    )
    assert result is not None
    restored = StoryPlanReviewOperationResultV1.model_validate_json(
        json.dumps(result, ensure_ascii=False, allow_nan=False)
    )
    encoded = restored.model_dump_json()
    assert "credential" not in encoded.casefold()
    assert "api_key" not in encoded.casefold()
    assert "D:\\" not in encoded
    assert "/home/" not in encoded


def test_facade_exposes_exact_review_and_revision_routes_only() -> None:
    application, run_id = _created()

    assert (
        dispatch_plan_only_request(
            application,
            method="GET",
            path=f"/api/v1/plan-only/runs/{run_id}/review",
        ).status_code
        == 200
    )
    assert (
        dispatch_plan_only_request(
            application,
            method="POST",
            path=f"/api/v1/plan-only/runs/{run_id}/review/accept",
            body={"schema_version": 1, "current_revision_id": "revision-0001"},
        ).status_code
        == 200
    )
    assert (
        dispatch_plan_only_request(
            application,
            method="GET",
            path=f"/api/v1/plan-only/runs/{run_id}/revisions",
        ).status_code
        == 200
    )
    assert (
        dispatch_plan_only_request(
            application,
            method="GET",
            path=f"/api/v1/plan-only/runs/{run_id}/revisions/revision-0001",
        ).status_code
        == 200
    )
    assert (
        dispatch_plan_only_request(
            application,
            method="GET",
            path=f"/api/v1/plan-only/runs/{run_id}/unknown",
        ).status_code
        == 404
    )


def test_review_boundary_performs_no_external_calls() -> None:
    application, run_id = _created()
    review = application.get_review(run_id)
    assert review is not None
    assert review["current_revision"]["story_plan"]["planner_metadata"]["external_calls"] == 0
    assert review["render_authority"] is False
    assert review["media_capability"] is False
