from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from tella.topic_production.execution_enablement import (
    ExecutionEnablementAccessV1,
    ExecutionPackageV1,
)
from tella.topic_production.plan_only_application import dispatch_plan_only_request
from tests.test_plan_only_execution_readiness import (
    _acknowledge_all,
    _approve,
    _initialize_readiness,
    _readiness_ready_application,
)
from tests.test_plan_only_local_host import _http_request, _json_request, _running_host


_FALSE_LOCKS = (
    "full_render_enabled",
    "narration_generation_capability",
    "tts_capability",
    "audio_generation_capability",
    "audio_measurement_capability",
    "timeline_execution_authority",
    "renderer_execution_authority",
    "render_authority",
    "video_render_authority",
    "subtitle_generation_capability",
    "media_muxing_capability",
    "output_creation_capability",
    "execution_job_creation_capability",
    "final_media_capability",
)


def _approved_application(tmp_path):
    application, provider, run_id, scenes, visuals, compositions, timeline = (
        _readiness_ready_application(tmp_path)
    )
    report = _initialize_readiness(application, run_id, timeline)["report"]
    report = _acknowledge_all(application, run_id, report)["report"]
    report = _approve(application, run_id, report)["report"]
    return application, provider, run_id, scenes, visuals, compositions, timeline, report


def _create(application, run_id, report):
    return application.create_execution_package(
        run_id,
        {
            "schema_version": 1,
            "readiness_report_revision_id": report["report_revision_id"],
            "readiness_source_authority_sha256": report["source_authority_sha256"],
            "readiness_approval_id": report["approval"]["approval_id"],
            "explicit_confirmation": True,
            "note": "Seal this planning authority for separate narration review.",
        },
    )


def test_access_requires_current_approved_readiness(tmp_path) -> None:
    application, _, run_id, _, _, _, timeline = _readiness_ready_application(tmp_path)
    initial = application.get_execution_enablement_access(run_id)
    assert initial["execution_package_creation_authorized"] is False
    assert "READINESS_REPORT_UNAVAILABLE" in initial["blocker_codes"]
    report = _initialize_readiness(application, run_id, timeline)["report"]
    acknowledged = _acknowledge_all(application, run_id, report)["report"]
    before_approval = application.get_execution_enablement_access(run_id)
    assert before_approval["execution_package_creation_authorized"] is False
    assert "READINESS_REPORT_NOT_CURRENTLY_APPROVED" in before_approval["blocker_codes"]
    _approve(application, run_id, acknowledged)
    approved = application.get_execution_enablement_access(run_id)
    assert approved["execution_package_creation_authorized"] is True
    assert approved["blocker_codes"] == []
    assert approved["package"] is None
    assert all(approved[key] is False for key in _FALSE_LOCKS)


def test_package_creation_is_deterministic_bound_detached_and_duplicate_safe(
    tmp_path,
) -> None:
    application, provider, run_id, scenes, visuals, compositions, timeline, report = (
        _approved_application(tmp_path)
    )
    calls_before = len(provider.calls)
    result = _create(application, run_id, report)
    package = result["package"]
    assert result["error"] is None
    assert package["status"] == "SEALED_FOR_NARRATION_STAGE_REVIEW"
    assert package["execution_package_created"] is True
    assert package["eligible_for_narration_stage_review"] is True
    assert all(package[key] is False for key in _FALSE_LOCKS)
    authority = package["authority"]
    assert authority["readiness_report_revision_id"] == report["report_revision_id"]
    assert authority["readiness_approval_id"] == report["approval"]["approval_id"]
    assert authority["ordered_scene_ids"] == [item["scene_id"] for item in scenes["scenes"]]
    assert (
        authority["visual_collection_revision_ids"]
        == report["authority"]["visual_collection_revision_ids"]
    )
    assert authority["composition_collection_revision_id"] == compositions["collection_revision_id"]
    assert authority["accepted_timeline_revision_id"] == timeline["accepted_timeline_revision_id"]
    assert len(package["package_source_authority_sha256"]) == 64
    pristine = deepcopy(package)
    package["authority"]["ordered_scene_ids"][0] = "forged"
    package["review_history"][0]["note"] = "forged"
    access = application.get_execution_enablement_access(run_id)
    assert access["package"] == pristine
    assert access["eligible_for_narration_stage_review"] is True
    duplicate = _create(application, run_id, report)
    assert duplicate["error"]["code"] == "EXECUTION_PACKAGE_ALREADY_CREATED"
    assert application.get_execution_package_history(run_id)["packages"] == [pristine]
    assert len(provider.calls) == calls_before
    assert visuals


@pytest.mark.parametrize(
    "field",
    ["full_render_enabled", "tts_capability", "render_authority", "final_media_capability"],
)
def test_public_models_reject_forged_operational_capability(field, tmp_path) -> None:
    application, _, run_id, _, _, _, _, report = _approved_application(tmp_path)
    package = _create(application, run_id, report)["package"]
    forged_package = deepcopy(package)
    forged_package[field] = True
    with pytest.raises(ValidationError):
        ExecutionPackageV1.model_validate(forged_package)
    access = application.get_execution_enablement_access(run_id)
    forged_access = deepcopy(access)
    forged_access[field] = True
    with pytest.raises(ValidationError):
        ExecutionEnablementAccessV1.model_validate(forged_access)


def test_revision_request_and_cancellation_preserve_immutable_history(tmp_path) -> None:
    application, _, run_id, _, _, _, _, report = _approved_application(tmp_path)
    created = _create(application, run_id, report)["package"]
    revision = application.mutate_execution_package(
        run_id,
        "request-revision",
        {
            "schema_version": 1,
            "package_revision_id": created["package_revision_id"],
            "package_source_authority_sha256": created["package_source_authority_sha256"],
            "explicit_confirmation": True,
            "reason_code": "TIMELINE_CONCERN",
            "note": "Review narration alignment before a later stage.",
        },
    )["package"]
    assert revision["status"] == "REVISION_REQUESTED"
    assert revision["eligible_for_narration_stage_review"] is False
    cancelled = application.mutate_execution_package(
        run_id,
        "cancel-package",
        {
            "schema_version": 1,
            "package_revision_id": revision["package_revision_id"],
            "package_source_authority_sha256": revision["package_source_authority_sha256"],
            "explicit_confirmation": True,
            "reason_code": "EXECUTION_SCOPE_CONCERN",
            "note": "Cancel this package without changing planning authority.",
        },
    )["package"]
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["current"] is False
    assert all(cancelled[key] is False for key in _FALSE_LOCKS)
    stale = application.mutate_execution_package(
        run_id,
        "request-revision",
        {
            "schema_version": 1,
            "package_revision_id": created["package_revision_id"],
            "package_source_authority_sha256": created["package_source_authority_sha256"],
            "explicit_confirmation": True,
            "reason_code": "OTHER_BOUNDED_NOTE",
            "note": None,
        },
    )
    assert stale["error"]["code"] == "STALE_EXECUTION_PACKAGE"
    history = application.get_execution_package_history(run_id)["packages"]
    assert [item["status"] for item in history] == [
        "SEALED_FOR_NARRATION_STAGE_REVIEW",
        "REVISION_REQUESTED",
        "CANCELLED",
    ]
    assert history[0] == created


def test_readiness_clear_supersedes_without_rebinding(tmp_path) -> None:
    application, _, run_id, _, _, _, _, report = _approved_application(tmp_path)
    package = _create(application, run_id, report)["package"]
    application.mutate_execution_readiness(
        run_id,
        "clear-approval",
        {
            "schema_version": 1,
            "report_revision_id": report["report_revision_id"],
            "source_authority_sha256": report["source_authority_sha256"],
            "explicit_confirmation": True,
        },
    )
    access = application.get_execution_enablement_access(run_id)
    assert access["package"]["status"] == "SUPERSEDED"
    assert access["package"]["package_id"] == package["package_id"]
    assert access["package"]["authority"] == package["authority"]
    assert access["execution_package_creation_authorized"] is False
    assert access["eligible_for_narration_stage_review"] is False
    assert "READINESS_REPORT_NOT_CURRENTLY_APPROVED" in access["blocker_codes"]


def test_timeline_change_supersedes_package_and_rejects_stale_mutation(tmp_path) -> None:
    application, _, run_id, _, _, _, timeline, report = _approved_application(tmp_path)
    package = _create(application, run_id, report)["package"]
    segment = timeline["segments"][0]
    changed = application.mutate_timeline_segment(
        run_id,
        segment["segment_id"],
        "save",
        {
            "schema_version": 1,
            "base_collection_revision_id": timeline["collection_revision_id"],
            "base_segment_revision_id": segment["segment_revision_id"],
            "changes": {"note": "Change upstream planning authority."},
        },
    )
    assert changed["error"] is None
    access = application.get_execution_enablement_access(run_id)
    assert access["package"]["status"] == "SUPERSEDED"
    assert access["eligible_for_narration_stage_review"] is False
    assert (
        application.mutate_execution_package(
            run_id,
            "cancel-package",
            {
                "schema_version": 1,
                "package_revision_id": package["package_revision_id"],
                "package_source_authority_sha256": package["package_source_authority_sha256"],
                "explicit_confirmation": True,
                "reason_code": "TIMELINE_CONCERN",
                "note": None,
            },
        )["error"]["code"]
        == "STALE_EXECUTION_PACKAGE"
    )


def test_routes_and_real_loopback_remain_planning_only(tmp_path) -> None:
    application, provider, run_id, _, _, _, _, report = _approved_application(tmp_path)
    calls_before = len(provider.calls)
    prefix = f"/api/v1/plan-only/runs/{run_id}/execution-enablement"
    direct = dispatch_plan_only_request(application, method="GET", path=f"{prefix}/access")
    assert direct.status_code == 200
    assert (
        dispatch_plan_only_request(
            application, method="DELETE", path=f"{prefix}/access"
        ).status_code
        == 404
    )
    with _running_host(tmp_path, application=application) as address:
        status, created, _ = _json_request(
            address,
            "POST",
            f"{prefix}/create-package",
            {
                "schema_version": 1,
                "readiness_report_revision_id": report["report_revision_id"],
                "readiness_source_authority_sha256": report["source_authority_sha256"],
                "readiness_approval_id": report["approval"]["approval_id"],
                "explicit_confirmation": True,
                "note": None,
            },
        )
        assert status == 200
        package = created["package"]
        assert package["eligible_for_narration_stage_review"] is True
        status, history, _ = _json_request(address, "GET", f"{prefix}/history", None)
        assert status == 200
        assert history["packages"] == [package]
        status, _, _ = _json_request(address, "POST", f"{prefix}/execute", {})
        assert status == 404
        status, rejected, _ = _http_request(address, "DELETE", f"{prefix}/access")
        assert status == 405
        assert rejected["error"]["code"] == "METHOD_NOT_ALLOWED"
        status, malformed, _ = _http_request(
            address,
            "POST",
            f"{prefix}/create-package",
            body=b"{",
            headers={"Content-Type": "application/json", "Content-Length": "1"},
        )
        assert status == 400
        assert malformed["error"]["code"] == "MALFORMED_JSON"
    serialized = str(package).lower()
    for forbidden in (
        "artifact_path",
        "output_path",
        "command",
        "token",
        "credential",
        "job_id",
    ):
        assert forbidden not in serialized
    assert len(provider.calls) == calls_before


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"schema_version": True},
        {"schema_version": 1.0},
        {"schema_version": "1"},
        {
            "schema_version": 1,
            "readiness_report_revision_id": "readiness-report-revision-0001",
            "readiness_source_authority_sha256": "a" * 64,
            "readiness_approval_id": "readiness-approval-0001",
            "explicit_confirmation": False,
            "note": "https://example.invalid",
        },
    ],
)
def test_malformed_creation_requests_fail_closed(payload, tmp_path) -> None:
    application, _, run_id, _, _, _, _, _ = _approved_application(tmp_path)
    result = application.create_execution_package(run_id, payload)
    assert result["error"]["code"] == "INVALID_EXECUTION_PACKAGE_REQUEST"
    assert application.get_execution_package_history(run_id) is None
