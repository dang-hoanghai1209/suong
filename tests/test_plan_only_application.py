from __future__ import annotations

from copy import deepcopy
import json
import subprocess

import pytest

from tella.topic_production.models import PlannerMode, StoryPlan
from tella.topic_production.plan_only_application import (
    PLAN_ONLY_API_PREFIX,
    PlanOnlyApplication,
    PlanOnlyCreateResultV1,
    PlanOnlyDeterministicTopicProducer,
    ProductionCapabilitiesV1,
    ProductionDashboardViewV1,
    ProductionRunViewV1,
    dispatch_plan_only_request,
)
from tella.topic_production.script_input import NormalizedScriptInput, ScriptInputMode
from tella.topic_production.story_plan_producer import ApprovedStoryPlanProducer


def _request(
    *,
    topic: str = "Learning to trust uncertainty",
    input_mode: str = "TOPIC",
    character_scope: str = "recurring_female",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "input_mode": input_mode,
        "source_content": topic,
        "language": "en",
        "character_scope": character_scope,
        "requested_scene_count": 8,
    }


def _created(app: PlanOnlyApplication, **changes: object) -> dict[str, object]:
    payload = {**_request(), **changes}
    result = PlanOnlyCreateResultV1.model_validate(app.create_run(payload))
    assert result.error is None
    assert result.run is not None
    return result.run.model_dump(mode="json")


def test_valid_plan_only_request_returns_canonical_detached_projection() -> None:
    app = PlanOnlyApplication()

    run = _created(app)

    assert run["execution_mode"] == "PLAN_ONLY"
    assert run["status"] == "PLANNED"
    assert run["current_stage"] == "planned"
    assert run["render_readiness"]["ready"] is False
    assert [beat["order"] for beat in run["story_plan"]["semantic_beats"]] == list(range(1, 9))
    assert [scene["source_beat_id"] for scene in run["story_plan"]["scenes"]] == [
        f"beat_{order:02d}" for order in range(1, 9)
    ]
    assert [row["order"] for row in run["timeline"]["rows"]] == list(range(1, 9))
    assert all(row["render_clip_duration_seconds"] is None for row in run["timeline"]["rows"])


@pytest.mark.parametrize(
    "schema_version",
    [None, True, 1.0, "1", 2],
)
def test_malformed_schema_version_returns_typed_failed_result(
    schema_version: object,
) -> None:
    result = PlanOnlyCreateResultV1.model_validate(
        PlanOnlyApplication().create_run({**_request(), "schema_version": schema_version})
    )

    assert result.run is None
    assert result.error is not None
    assert result.error.status == "FAILED"
    assert result.error.code == "INVALID_PLAN_ONLY_REQUEST"
    assert result.error.field_errors


@pytest.mark.parametrize(
    ("input_mode", "character_scope", "code"),
    [
        ("NARRATION", "recurring_female", "STORYPLAN_NARRATION_SEGMENTER_MISSING"),
        ("TOPIC", "recurring_male", "UNSUPPORTED_IDENTITY_POLICY"),
        ("TOPIC", "family", "UNSUPPORTED_IDENTITY_POLICY"),
        ("TOPIC", "unresolved", "UNRESOLVED_IDENTITY_EVIDENCE"),
    ],
)
def test_unsupported_input_returns_typed_blocked_result(
    input_mode: str,
    character_scope: str,
    code: str,
) -> None:
    result = PlanOnlyCreateResultV1.model_validate(
        PlanOnlyApplication().create_run(
            _request(input_mode=input_mode, character_scope=character_scope)
        )
    )

    assert result.run is None
    assert result.error is not None
    assert result.error.status == "BLOCKED"
    assert result.error.code == code


def test_run_identity_and_output_are_deterministic() -> None:
    first = _created(PlanOnlyApplication())
    second = _created(PlanOnlyApplication())

    assert first == second
    assert first["run_id"].startswith("plan-")


def test_lookup_is_exact_and_returned_values_are_detached() -> None:
    app = PlanOnlyApplication()
    created = _created(app)
    run_id = str(created["run_id"])
    first = app.get_run(run_id)
    second = app.get_run(run_id)
    assert first == second
    assert first is not second
    assert app.get_run(f"{run_id}-unknown") is None

    assert first is not None
    first["status"] = "FAILED"
    story = first["story_plan"]
    assert isinstance(story, dict)
    beats = story["semantic_beats"]
    assert isinstance(beats, list)
    beats.reverse()

    later = app.get_run(run_id)
    assert later is not None
    assert later["status"] == "PLANNED"
    later_story = later["story_plan"]
    assert isinstance(later_story, dict)
    assert [beat["order"] for beat in later_story["semantic_beats"]] == list(range(1, 9))


def test_list_runs_is_stable_and_validated() -> None:
    app = PlanOnlyApplication()
    run_ids = {
        str(_created(app, source_content=topic)["run_id"])
        for topic in ("Quiet courage", "Gentle trust")
    }

    first = ProductionDashboardViewV1.model_validate(app.list_runs())
    second = ProductionDashboardViewV1.model_validate(app.list_runs())

    assert first == second
    assert {item.run_id for item in first.runs} == run_ids
    assert list(first.runs) == sorted(first.runs, key=lambda item: (item.created_at, item.run_id))


def test_capability_payload_has_exact_false_render_gates() -> None:
    capabilities = ProductionCapabilitiesV1.model_validate(PlanOnlyApplication().capabilities())

    assert capabilities.supported_execution_modes == ("PLAN_ONLY",)
    assert capabilities.supported_input_modes == ("TOPIC",)
    assert type(capabilities.backend_render_capability) is bool
    assert type(capabilities.synthetic_closure_passed) is bool
    assert type(capabilities.live_canary_passed) is bool
    assert type(capabilities.full_render_enabled) is bool
    assert not any(
        (
            capabilities.backend_render_capability,
            capabilities.synthetic_closure_passed,
            capabilities.live_canary_passed,
            capabilities.full_render_enabled,
        )
    )
    assert all(type(code) is str for code in capabilities.render_lock_reason_codes)


def test_concrete_producer_is_approved_but_never_render_eligible() -> None:
    producer = PlanOnlyDeterministicTopicProducer()
    app = PlanOnlyApplication(producer)
    run = _created(app)
    assert run["status"] == "PLANNED"

    request = _request()
    result = app.create_run(request)
    approved_run = PlanOnlyCreateResultV1.model_validate(result).run
    assert approved_run is not None
    assert producer.supported_input_modes == frozenset({ScriptInputMode.TOPIC})

    from tella.topic_production.script_input import (
        CharacterRoleRequest,
        CharacterScopeRequest,
        RecurringCharacterRequest,
        TopicScriptInput,
        normalize_script_input,
    )

    normalized = normalize_script_input(
        TopicScriptInput(
            topic="Planning authority only",
            language="en",
            requested_scene_count_range=(8, 8),
            character_scope_request=CharacterScopeRequest(
                recurring_characters=(
                    RecurringCharacterRequest(
                        character_id="recurring_woman",
                        role=CharacterRoleRequest.FEMALE,
                    ),
                )
            ),
        )
    )
    production = producer.produce(normalized)
    assert production.story_plan.planner_metadata.planner_mode is PlannerMode.PRODUCTION
    assert not production.story_plan.planner_metadata.production_eligible


class _FailingProducer(ApprovedStoryPlanProducer):
    PRODUCER_ID = "test_only.plan_only_failure"
    PRODUCER_VERSION = "v1"
    SUPPORTED_INPUT_MODES = frozenset({ScriptInputMode.TOPIC})

    def _produce(self, normalized_input: NormalizedScriptInput) -> StoryPlan:
        del normalized_input
        raise RuntimeError("private failure detail")


def test_planning_failure_is_sanitized() -> None:
    result = PlanOnlyCreateResultV1.model_validate(
        PlanOnlyApplication(_FailingProducer()).create_run(_request())
    )

    assert result.run is None
    assert result.error is not None
    assert result.error.code == "PLAN_ONLY_PLANNING_FAILED"
    encoded = result.model_dump_json()
    assert "private failure detail" not in encoded
    assert "Traceback" not in encoded


def test_serialization_round_trip_and_no_secret_leakage() -> None:
    payload = _created(PlanOnlyApplication())
    encoded = json.dumps(payload, allow_nan=False, sort_keys=True)
    restored = ProductionRunViewV1.model_validate_json(encoded)

    assert restored.model_dump(mode="json") == payload
    lowered = encoded.casefold()
    for forbidden in (
        "api_key",
        "credential",
        "cloudflare",
        "gemini",
        "pollinations",
        "provider_request",
        "d:\\",
        "/users/",
    ):
        assert forbidden not in lowered


def test_facade_routes_exact_operations_without_id_rewriting() -> None:
    app = PlanOnlyApplication()
    created = dispatch_plan_only_request(
        app,
        method="POST",
        path=f"{PLAN_ONLY_API_PREFIX}/runs",
        body=_request(),
    )
    assert created.status_code == 200
    assert created.payload is not None
    result = PlanOnlyCreateResultV1.model_validate(created.payload)
    assert result.run is not None
    run_id = result.run.run_id

    assert (
        dispatch_plan_only_request(
            app,
            method="GET",
            path=f"{PLAN_ONLY_API_PREFIX}/runs/{run_id}",
        ).status_code
        == 200
    )
    assert (
        dispatch_plan_only_request(
            app,
            method="GET",
            path=f"{PLAN_ONLY_API_PREFIX}/runs/{run_id}-warning",
        ).status_code
        == 404
    )


def test_plan_only_boundary_invokes_no_external_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("external process invocation is forbidden")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)

    app = PlanOnlyApplication()
    _created(app)
    app.capabilities()
    app.list_runs()


def test_caller_request_is_not_mutated() -> None:
    request = _request()
    before = deepcopy(request)

    PlanOnlyApplication().create_run(request)

    assert request == before
