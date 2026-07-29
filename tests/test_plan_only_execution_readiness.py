from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from tella.topic_production.execution_readiness import (
    ExecutionReadinessAccessV1,
    ExecutionReadinessReportV1,
    LimitationCode,
)
from tests.test_plan_only_local_host import _http_request, _json_request, _running_host
from tests.test_plan_only_timeline_planning import (
    _initialize_timeline,
    _timeline_ready_application,
)


def _readiness_ready_application(tmp_path):
    application, provider, run_id, scenes, _, visuals, compositions = _timeline_ready_application(
        tmp_path
    )
    initialized = _initialize_timeline(application, run_id, compositions)
    timeline = initialized["access"]["collection"]
    accepted = application.review_timeline(
        run_id,
        "accept",
        {
            "schema_version": 1,
            "base_collection_revision_id": timeline["collection_revision_id"],
            "reason_code": "OTHER_BOUNDED_NOTE",
            "note": "Accepted for execution-readiness review only.",
        },
    )
    timeline = accepted["collection"]
    return application, provider, run_id, scenes, visuals, compositions, timeline


def _initialize_readiness(application, run_id, timeline):
    return application.initialize_execution_readiness(
        run_id,
        {
            "schema_version": 1,
            "timeline_collection_revision_id": timeline["collection_revision_id"],
            "accepted_timeline_revision_id": timeline["accepted_timeline_revision_id"],
        },
    )


def _acknowledge_all(application, run_id, report):
    return application.mutate_execution_readiness(
        run_id,
        "acknowledge",
        {
            "schema_version": 1,
            "report_revision_id": report["report_revision_id"],
            "source_authority_sha256": report["source_authority_sha256"],
            "limitation_codes": [item.value for item in LimitationCode],
            "acknowledged": True,
            "reviewer_note": "All planning-package limitations reviewed.",
        },
    )


def _approve(application, run_id, report):
    return application.mutate_execution_readiness(
        run_id,
        "approve",
        {
            "schema_version": 1,
            "report_revision_id": report["report_revision_id"],
            "source_authority_sha256": report["source_authority_sha256"],
            "purpose": "SEPARATE_EXECUTION_ENABLEMENT_TASK_REVIEW",
            "explicit_confirmation": True,
            "reviewer_note": "Approval is limited to a separate future task review.",
        },
    )


def test_access_locks_without_current_accepted_timeline(tmp_path) -> None:
    application, _, run_id, _, _, _, compositions = _timeline_ready_application(tmp_path)
    access = application.get_execution_readiness_access(run_id)
    assert access["review_available"] is False
    assert access["initialization_authorized"] is False
    assert access["mutation_authorized"] is False
    assert access["report"] is None
    assert "TIMELINE_NOT_CURRENTLY_ACCEPTED" in access["blocker_codes"]
    assert access["execution_job_creation_capability"] is False
    assert access["output_creation_capability"] is False
    assert compositions["overall_ready_for_timeline_planning"] is True


def test_deterministic_report_authority_ordering_and_idempotency(tmp_path) -> None:
    application, provider, run_id, scenes, _, compositions, timeline = _readiness_ready_application(
        tmp_path
    )
    calls_before = len(provider.calls)
    initialized = _initialize_readiness(application, run_id, timeline)
    report = initialized["report"]
    assert initialized["error"] is None
    assert report["status"] == "READY_FOR_EXECUTION_ENABLEMENT_REVIEW"
    assert report["planning_package_ready"] is True
    assert report["future_execution_enablement_review_eligible"] is True
    assert report["blocker_count"] == 0
    assert report["warning_count"] == 1
    assert report["informational_count"] == 1
    assert [item["stage"] for item in report["stage_checks"]] == [
        "RUN",
        "STORY_PLAN",
        "SCENE_PLANNING",
        "VISUAL_CANDIDATES",
        "COMPOSITION_PLANNING",
        "TIMELINE_PLANNING",
        "NARRATION_SOURCE",
        "ARTIFACT_INTEGRITY",
        "AUTHORITY_FRESHNESS",
        "EXECUTION_CAPABILITY_LOCKS",
        "PROCESS_LOCAL_LIMITATIONS",
    ]
    assert [item["scene_id"] for item in report["scene_checks"]] == [
        item["scene_id"] for item in scenes["scenes"]
    ]
    assert (
        report["authority"]["composition_collection_revision_id"]
        == compositions["collection_revision_id"]
    )
    assert (
        report["authority"]["timeline_collection_revision_id"] == timeline["collection_revision_id"]
    )
    assert (
        report["total_effective_timeline_duration_ms"] == timeline["effective_timeline_duration_ms"]
    )
    assert len(report["source_authority_sha256"]) == 64
    repeated = _initialize_readiness(application, run_id, timeline)
    assert repeated["report"] == report
    assert len(provider.calls) == calls_before


def test_report_scene_artifact_bindings_and_no_paths(tmp_path) -> None:
    application, _, run_id, _, visuals, _, timeline = _readiness_ready_application(tmp_path)
    report = _initialize_readiness(application, run_id, timeline)["report"]
    for scene in report["scene_checks"]:
        visual = visuals[scene["scene_id"]]
        candidate = next(
            item
            for item in visual["candidates"]
            if item["candidate_id"] == visual["current_accepted_candidate_id"]
        )
        assert scene["artifact_sha256"] == candidate["sha256"]
        assert scene["artifact_mime"] == candidate["mime_type"]
        assert scene["artifact_width"] == candidate["width"]
        assert scene["artifact_height"] == candidate["height"]
        assert scene["artifact_registered"] is True
        assert scene["artifact_technically_valid"] is True
    serialized = str(report)
    assert "artifact_url" not in serialized
    assert "output_path" not in serialized
    assert "renderer_command" not in serialized
    assert "execution_token" not in serialized


def test_acknowledgement_approval_and_clear_are_immutable(tmp_path) -> None:
    application, _, run_id, _, _, _, timeline = _readiness_ready_application(tmp_path)
    initial = _initialize_readiness(application, run_id, timeline)["report"]
    initial_copy = deepcopy(initial)
    blocked = _approve(application, run_id, initial)
    assert blocked["error"]["code"] == "READINESS_ACKNOWLEDGEMENTS_REQUIRED"

    acknowledged = _acknowledge_all(application, run_id, initial)
    report = acknowledged["report"]
    assert len(report["acknowledgements"]) == len(LimitationCode)
    assert all(item["acknowledged"] for item in report["limitations"])
    duplicate = _acknowledge_all(application, run_id, report)
    assert duplicate["error"]["code"] == "DUPLICATE_READINESS_ACKNOWLEDGEMENT"

    approved = _approve(application, run_id, report)
    report = approved["report"]
    assert report["status"] == "REVIEW_APPROVED_FOR_SEPARATE_TASK"
    assert report["approved_for_separate_execution_enablement_review"] is True
    assert report["approval"]["purpose"] == "SEPARATE_EXECUTION_ENABLEMENT_TASK_REVIEW"
    for key, value in report["approval"].items():
        if key.endswith("authority") or key.endswith("capability") or key == "full_render_enabled":
            assert value is False
    assert "execution_token" not in report["approval"]
    assert "job_id" not in report["approval"]

    cleared = application.mutate_execution_readiness(
        run_id,
        "clear-approval",
        {
            "schema_version": 1,
            "report_revision_id": report["report_revision_id"],
            "source_authority_sha256": report["source_authority_sha256"],
            "explicit_confirmation": True,
        },
    )
    final = cleared["report"]
    assert final["status"] == "READY_FOR_EXECUTION_ENABLEMENT_REVIEW"
    assert final["approval"] is None
    assert final["approved_for_separate_execution_enablement_review"] is False
    assert initial == initial_copy


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ("request-revision", "REVISION_REQUESTED"),
        ("reject", "REVIEW_REJECTED"),
    ],
)
def test_bounded_review_actions(operation, expected, tmp_path) -> None:
    application, _, run_id, _, _, _, timeline = _readiness_ready_application(tmp_path)
    report = _initialize_readiness(application, run_id, timeline)["report"]
    result = application.mutate_execution_readiness(
        run_id,
        operation,
        {
            "schema_version": 1,
            "report_revision_id": report["report_revision_id"],
            "source_authority_sha256": report["source_authority_sha256"],
            "reason_code": "OTHER_BOUNDED_NOTE",
            "note": "Planning authority needs another bounded review.",
        },
    )
    assert result["error"] is None
    assert result["report"]["status"] == expected
    assert result["report"]["approval"] is None


def test_stale_timeline_supersedes_report_and_acknowledgements(tmp_path) -> None:
    application, _, run_id, _, _, _, timeline = _readiness_ready_application(tmp_path)
    initial = _initialize_readiness(application, run_id, timeline)["report"]
    acknowledged = _acknowledge_all(application, run_id, initial)["report"]
    approved = _approve(application, run_id, acknowledged)["report"]
    first = timeline["segments"][0]
    changed = application.mutate_timeline_segment(
        run_id,
        first["segment_id"],
        "save",
        {
            "schema_version": 1,
            "base_collection_revision_id": timeline["collection_revision_id"],
            "base_segment_revision_id": first["segment_revision_id"],
            "changes": {"note": "Changed planning-only narration timing review."},
        },
    )
    assert changed["error"] is None
    access = application.get_execution_readiness_access(run_id)
    assert access["report"]["status"] == "SUPERSEDED"
    assert access["report"]["current"] is False
    assert access["mutation_authorized"] is False
    stale = _approve(application, run_id, approved)
    assert stale["error"]["code"] == "READINESS_NOT_AUTHORIZED"
    history = application.get_execution_readiness_history(run_id)
    assert history["reports"][0] == initial
    assert history["reports"][-1]["status"] == "SUPERSEDED"


def test_detached_outputs_and_strict_capability_models(tmp_path) -> None:
    application, _, run_id, _, _, _, timeline = _readiness_ready_application(tmp_path)
    report = _initialize_readiness(application, run_id, timeline)["report"]
    pristine = deepcopy(report)
    report["scene_checks"][0]["scene_id"] = "forged"
    assert application.get_execution_readiness_access(run_id)["report"] == pristine

    access = application.get_execution_readiness_access(run_id)
    forged = deepcopy(access)
    forged["renderer_execution_authority"] = True
    with pytest.raises(ValidationError):
        ExecutionReadinessAccessV1.model_validate(forged)

    forged_report = deepcopy(pristine)
    forged_report["blocker_count"] = 1
    with pytest.raises(ValidationError):
        ExecutionReadinessReportV1.model_validate(forged_report)


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": True},
        {"schema_version": 1.0},
        {"schema_version": "1"},
        {"schema_version": 2},
        {
            "schema_version": 1,
            "report_revision_id": "readiness-report-revision-0001",
            "source_authority_sha256": "a" * 64,
            "purpose": "EXECUTE",
            "explicit_confirmation": True,
        },
    ],
)
def test_malformed_requests_fail_closed(payload, tmp_path) -> None:
    application, _, run_id, _, _, _, timeline = _readiness_ready_application(tmp_path)
    report = _initialize_readiness(application, run_id, timeline)["report"]
    result = application.mutate_execution_readiness(
        run_id,
        "approve",
        {
            **payload,
            "report_revision_id": payload.get("report_revision_id", report["report_revision_id"]),
            "source_authority_sha256": payload.get(
                "source_authority_sha256", report["source_authority_sha256"]
            ),
        },
    )
    assert result["error"]["code"] == "INVALID_READINESS_APPROVAL"


def test_loopback_routes_and_zero_execution(tmp_path) -> None:
    application, provider, run_id, _, _, _, timeline = _readiness_ready_application(tmp_path)
    calls_before = len(provider.calls)
    prefix = f"/api/v1/plan-only/runs/{run_id}/execution-readiness"
    with _running_host(tmp_path, application=application) as address:
        status, initialized, _ = _json_request(
            address,
            "POST",
            f"{prefix}/initialize",
            {
                "schema_version": 1,
                "timeline_collection_revision_id": timeline["collection_revision_id"],
                "accepted_timeline_revision_id": timeline["accepted_timeline_revision_id"],
            },
        )
        assert status == 200
        report = initialized["report"]
        assert report["execution_job_creation_capability"] is False
        acknowledge_status, acknowledged, _ = _json_request(
            address,
            "POST",
            f"{prefix}/acknowledgements",
            {
                "schema_version": 1,
                "report_revision_id": report["report_revision_id"],
                "source_authority_sha256": report["source_authority_sha256"],
                "limitation_codes": [item.value for item in LimitationCode],
                "acknowledged": True,
                "reviewer_note": "Loopback planning limitations reviewed.",
            },
        )
        assert acknowledge_status == 200
        report = acknowledged["report"]
        approval_status, approved, _ = _json_request(
            address,
            "POST",
            f"{prefix}/approve-separate-task-review",
            {
                "schema_version": 1,
                "report_revision_id": report["report_revision_id"],
                "source_authority_sha256": report["source_authority_sha256"],
                "purpose": "SEPARATE_EXECUTION_ENABLEMENT_TASK_REVIEW",
                "explicit_confirmation": True,
                "reviewer_note": "Separate future task review only.",
            },
        )
        assert approval_status == 200
        report = approved["report"]
        assert report["approved_for_separate_execution_enablement_review"] is True
        assert report["timeline_execution_authority"] is False
        assert "execution_token" not in report
        refresh_status, refreshed, _ = _json_request(
            address,
            "POST",
            f"{prefix}/refresh",
            {
                "schema_version": 1,
                "timeline_collection_revision_id": timeline["collection_revision_id"],
                "accepted_timeline_revision_id": timeline["accepted_timeline_revision_id"],
            },
        )
        assert refresh_status == 200
        assert refreshed["report"] == report
        get_status, access, _ = _http_request(address, "GET", f"{prefix}/access")
        assert get_status == 200
        assert access["timeline_execution_authority"] is False
        first = timeline["segments"][0]
        changed = application.mutate_timeline_segment(
            run_id,
            first["segment_id"],
            "save",
            {
                "schema_version": 1,
                "base_collection_revision_id": timeline["collection_revision_id"],
                "base_segment_revision_id": first["segment_revision_id"],
                "changes": {"note": "Loopback upstream invalidation."},
            },
        )
        assert changed["error"] is None
        stale_status, stale_access, _ = _http_request(address, "GET", f"{prefix}/access")
        assert stale_status == 200
        assert stale_access["report"]["status"] == "SUPERSEDED"
        history_status, history, _ = _http_request(address, "GET", f"{prefix}/history")
        assert history_status == 200
        assert history["reports"][-1]["status"] == "SUPERSEDED"
        method_status, error, _ = _http_request(address, "DELETE", f"{prefix}/access")
        assert method_status == 405
        assert error["error"]["code"] == "METHOD_NOT_ALLOWED"
        unknown_status, _, _ = _http_request(address, "GET", f"{prefix}/execute")
        assert unknown_status == 404
    assert len(provider.calls) == calls_before
