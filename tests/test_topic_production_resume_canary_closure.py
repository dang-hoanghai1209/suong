from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tella.topic_production import (
    ExecutionRunState,
    FailureReason,
    GenerationTier,
    ProductionSceneStatus,
    ProcessedNarrationArtifactBindingError,
    ProcessedNarrationArtifactBindingFailure,
    PromotionReason,
    QCCheckOutcome,
    QCDecision,
    ResumeAction,
    block_scene,
    build_duration_policy_report,
    build_fixture_preview_run,
    build_production_run_plan,
    build_renderer_plan_from_accepted_candidates,
    clear_processed_narration_measurement,
    initialize_execution_state,
    load_reference_catalog,
    load_runtime_state,
    measure_and_bind_processed_narration_artifact,
    persist_execution_snapshot,
    plan_resume,
    production_job_paths,
    promote_scene_to_acceptance,
    record_generation_attempt,
    record_human_qc,
)
from tella.topic_production.duration_policy import DurationValueAuthority
from tella.topic_production.full_canary import (
    FULL_CANARY_STORY_PLAN_SHA256,
    build_full_canary_scene_briefs,
)
from tella.topic_production.story_plan_identity import canonical_story_plan_sha256
import tella.topic_production.narration_measurement as narration_measurement
import tella.topic_production.renderer_bridge as renderer_bridge
from tests.test_full_canary_contract import _story_plan as _full_canary_story_plan
from tests.test_topic_production_duration_policy_projection import (
    _state_with_durations as _duration_policy_lock_state,
)
from tests.test_topic_production_renderer_bridge import (
    _accepted_state_with_valid_images,
    _bridge as _renderer_bridge,
    _profile,
    _timeline,
)
from tests.test_topic_production_runtime import (
    _accept_draft,
    _attempt,
    _checks,
    _record_draft_pass,
    _state,
)


_DURATION_POLICY_PLANNING_HASH_LOCK = (
    "31ef6b7caac5a8a513109274e1f2d159678630d4e495bf2634a5e8c5e97ee87d"
)
_MEASURED_DURATION_SECONDS = 35.0


def _state_at_status(status: ProductionSceneStatus) -> ExecutionRunState:
    state = _state()
    scene_id = "scene_01"
    if status is ProductionSceneStatus.DRAFT_PENDING:
        return state
    if status is ProductionSceneStatus.PLANNED:
        scenes = list(state.scenes)
        scenes[0] = scenes[0].model_copy(update={"status": status})
        return state.model_copy(update={"scenes": scenes})
    if status is ProductionSceneStatus.BLOCKED:
        return block_scene(state, scene_id=scene_id, reason=FailureReason.REFERENCE_BLOCKED)
    if status is ProductionSceneStatus.ACCEPTED:
        return _accept_draft(state, scene_id)

    draft = _attempt(state, scene_id, GenerationTier.DRAFT)
    state = record_generation_attempt(state, draft)
    if status is ProductionSceneStatus.DRAFT_GENERATED:
        return state
    draft_passing = status is not ProductionSceneStatus.DRAFT_QC_FAIL
    state = record_human_qc(
        state,
        qc_record_id=f"qc-{draft.candidate_id}",
        scene_id=scene_id,
        candidate_id=draft.candidate_id,
        tier=GenerationTier.DRAFT,
        decision=QCDecision.PASS if draft_passing else QCDecision.FAIL,
        reviewer="resume-canary-test-reviewer",
        checks=(
            _checks()
            if draft_passing
            else _checks().model_copy(update={"scene_meaning": QCCheckOutcome.FAIL})
        ),
    )
    if status in {
        ProductionSceneStatus.DRAFT_QC_PASS,
        ProductionSceneStatus.DRAFT_QC_FAIL,
    }:
        return state

    state, _ = _record_draft_pass(_state(), scene_id)
    state = promote_scene_to_acceptance(
        state,
        scene_id=scene_id,
        reason=PromotionReason.HUMAN_REQUESTED_UPGRADE,
        authorized_by="resume-canary-test-operator",
    )
    if status is ProductionSceneStatus.ACCEPTANCE_PENDING:
        return state
    acceptance = _attempt(state, scene_id, GenerationTier.ACCEPTANCE)
    state = record_generation_attempt(state, acceptance)
    if status is ProductionSceneStatus.ACCEPTANCE_GENERATED:
        return state
    passing = status is ProductionSceneStatus.ACCEPTANCE_QC_PASS
    return record_human_qc(
        state,
        qc_record_id=f"qc-{acceptance.candidate_id}",
        scene_id=scene_id,
        candidate_id=acceptance.candidate_id,
        tier=GenerationTier.ACCEPTANCE,
        decision=QCDecision.PASS if passing else QCDecision.FAIL,
        reviewer="resume-canary-test-reviewer",
        checks=(
            _checks()
            if passing
            else _checks().model_copy(update={"scene_meaning": QCCheckOutcome.FAIL})
        ),
    )


def _legacy_payload(state: ExecutionRunState) -> dict[str, object]:
    payload = state.model_dump(mode="json")
    payload["schema_version"] = 1
    payload.pop("processed_narration_measurement")
    return payload


def _bind(
    state: ExecutionRunState,
    artifact_root: Path,
    artifact_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    probe_calls: list[Path],
) -> ExecutionRunState:
    def fixed_probe(path: Path, **_kwargs) -> float:
        probe_calls.append(path)
        return _MEASURED_DURATION_SECONDS

    monkeypatch.setattr(
        narration_measurement,
        "probe_single_audio_stream_duration",
        fixed_probe,
    )
    return measure_and_bind_processed_narration_artifact(
        state,
        artifact_path=artifact_path,
        artifact_root=artifact_root,
    )


def _persist(state: ExecutionRunState, out_root: Path):
    paths = production_job_paths(
        out_root,
        job_id=state.run_plan.job_id,
        scene_id="scene_01",
    )
    persist_execution_snapshot(
        state,
        paths,
        execution_purpose="synthetic-resume-canary-closure",
        selected_scene_id="scene_01",
    )
    return paths


def _bound_restart_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    directly_bound, authorization = _accepted_state_with_valid_images(artifact_root)
    state = clear_processed_narration_measurement(directly_bound)
    narration = artifact_root / "narration" / "final.mp3"
    probe_calls: list[Path] = []
    bound = _bind(state, artifact_root, narration, monkeypatch, probe_calls)
    paths = _persist(bound, tmp_path / "persisted")
    restored = load_runtime_state(paths.runtime_state_path)
    return artifact_root, narration, restored, authorization, paths, probe_calls


def _bridge_path(state, authorization, artifact_root: Path, artifact_path: Path):
    return build_renderer_plan_from_accepted_candidates(
        state,
        authorization=authorization,
        profile=_profile(),
        narration_timeline=_timeline(state),
        narration_artifact_path=artifact_path,
        artifact_root=artifact_root,
    )


_STATUS_ACTIONS = (
    (ProductionSceneStatus.PLANNED, ResumeAction.EXECUTE_DRAFT),
    (ProductionSceneStatus.DRAFT_PENDING, ResumeAction.EXECUTE_DRAFT),
    (ProductionSceneStatus.DRAFT_GENERATED, ResumeAction.AWAIT_DRAFT_QC),
    (ProductionSceneStatus.DRAFT_QC_PASS, ResumeAction.AWAIT_EXPLICIT_TIER_DECISION),
    (ProductionSceneStatus.DRAFT_QC_FAIL, ResumeAction.AWAIT_EXPLICIT_INTERVENTION),
    (ProductionSceneStatus.ACCEPTANCE_PENDING, ResumeAction.EXECUTE_ACCEPTANCE),
    (ProductionSceneStatus.ACCEPTANCE_GENERATED, ResumeAction.AWAIT_ACCEPTANCE_QC),
    (ProductionSceneStatus.ACCEPTANCE_QC_PASS, ResumeAction.READY_FOR_EXPLICIT_ACCEPTANCE),
    (ProductionSceneStatus.ACCEPTANCE_QC_FAIL, ResumeAction.AWAIT_EXPLICIT_INTERVENTION),
    (ProductionSceneStatus.ACCEPTED, ResumeAction.SKIP_ACCEPTED),
    (ProductionSceneStatus.BLOCKED, ResumeAction.REMAIN_BLOCKED),
)


@pytest.mark.parametrize(("status", "expected_action"), _STATUS_ACTIONS)
def test_legacy_schema_one_status_matrix_resumes_without_manifest_authority(
    tmp_path: Path,
    status: ProductionSceneStatus,
    expected_action: ResumeAction,
) -> None:
    state = _state_at_status(status)
    story_sha256 = canonical_story_plan_sha256(state.run_plan.story_plan)
    caller_payload = _legacy_payload(state)
    caller_before = deepcopy(caller_payload)
    runtime_path = tmp_path / "runtime_state.json"
    runtime_path.write_text(json.dumps(caller_payload), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "duration_policy": {
                    "schema_version": 2,
                    "measured_duration_assessment": {"actual_duration_seconds": 999.0},
                },
                "resume_plan": "forged",
            }
        ),
        encoding="utf-8",
    )

    restored = load_runtime_state(runtime_path)
    action = next(
        item.action for item in plan_resume(restored).scenes if item.scene_id == "scene_01"
    )

    assert tuple(item for item, _ in _STATUS_ACTIONS) == tuple(ProductionSceneStatus)
    assert restored.schema_version == 2
    assert restored.processed_narration_measurement is None
    assert canonical_story_plan_sha256(restored.run_plan.story_plan) == story_sha256
    assert action is expected_action
    assert caller_payload == caller_before


def test_legacy_all_accepted_cannot_reach_renderer_without_measurement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directly_bound, authorization = _accepted_state_with_valid_images(tmp_path)
    accepted = clear_processed_narration_measurement(directly_bound)
    payload = _legacy_payload(accepted)
    before_payload = deepcopy(payload)
    runtime_path = tmp_path / "runtime_state.json"
    runtime_path.write_text(json.dumps(payload), encoding="utf-8")
    restored = load_runtime_state(runtime_path)
    before_state = restored.model_dump(mode="python")
    sentinels: list[str] = []

    def forbidden(*_args, **_kwargs):
        sentinels.append("reached")
        raise AssertionError("renderer/media work preceded measurement validation")

    monkeypatch.setattr(renderer_bridge, "_inspect_processed_narration_artifact", forbidden)
    monkeypatch.setattr(renderer_bridge, "_stream_sha256", forbidden)
    monkeypatch.setattr(renderer_bridge.Image, "open", forbidden)
    monkeypatch.setattr(
        narration_measurement,
        "probe_single_audio_stream_duration",
        forbidden,
    )

    with pytest.raises(ValueError, match="processed narration measurement is required"):
        build_renderer_plan_from_accepted_candidates(
            restored,
            authorization=authorization,
            profile=_profile(),
            narration_timeline=_timeline(directly_bound),
            narration_artifact_path=tmp_path / "narration" / "final.mp3",
            artifact_root=tmp_path,
        )

    assert all(item.action is ResumeAction.SKIP_ACCEPTED for item in plan_resume(restored).scenes)
    assert restored.processed_narration_measurement is None
    assert restored.model_dump(mode="python") == before_state
    assert payload == before_payload
    assert sentinels == []


def test_bound_state_round_trip_revalidates_bytes_without_reprobing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_root, narration, restored, authorization, paths, probe_calls = _bound_restart_fixture(
        tmp_path, monkeypatch
    )
    measurement = restored.processed_narration_measurement
    assert measurement is not None
    expected_sha256 = hashlib.sha256(narration.read_bytes()).hexdigest()
    manifest = json.loads(paths.manifest_path.read_text(encoding="utf-8"))

    bridge = _renderer_bridge(restored, authorization, artifact_root=artifact_root)

    assert measurement.artifact_relative_path == "narration/final.mp3"
    assert measurement.artifact_sha256 == expected_sha256
    assert measurement.story_plan_sha256 == canonical_story_plan_sha256(
        restored.run_plan.story_plan
    )
    assert measurement.measurement_method == "ffprobe_single_audio_stream_v1"
    assert (
        measurement.measured_duration_assessment.value_authority is DurationValueAuthority.MEASURED
    )
    assert manifest["duration_policy"] == build_duration_policy_report(restored).model_dump(
        mode="json"
    )
    assert bridge.source_state_ready
    assert bridge.external_calls == bridge.files_written == 0
    assert probe_calls == [narration.resolve()]


@pytest.mark.parametrize("mutation", ["missing", "replaced", "moved", "directory", "outside"])
def test_bound_restart_artifact_mutations_fail_before_candidate_or_media_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    artifact_root, narration, restored, authorization, _, probe_calls = _bound_restart_fixture(
        tmp_path, monkeypatch
    )
    if mutation == "missing":
        narration.unlink()
    elif mutation == "replaced":
        narration.write_bytes(b"different narration bytes")
    elif mutation == "moved":
        narration.rename(artifact_root / "moved.mp3")
    elif mutation == "directory":
        narration.unlink()
        narration.mkdir()
    else:
        narration.unlink()
        outside = tmp_path / "outside.mp3"
        outside.write_bytes(b"outside narration bytes")
        try:
            narration.symlink_to(outside)
        except OSError:
            pytest.skip("symlink creation is unavailable")

    before_state = restored.model_dump(mode="python")
    before_tree = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    sentinels: list[str] = []

    def forbidden(*_args, **_kwargs):
        sentinels.append("reached")
        raise AssertionError("candidate/media work preceded narration validation")

    monkeypatch.setattr(renderer_bridge.Image, "open", forbidden)
    if mutation == "outside":
        with pytest.raises(ProcessedNarrationArtifactBindingError) as failure:
            _renderer_bridge(restored, authorization, artifact_root=artifact_root)
        assert (
            failure.value.category is ProcessedNarrationArtifactBindingFailure.ARTIFACT_OUTSIDE_ROOT
        )
    else:
        with pytest.raises((ProcessedNarrationArtifactBindingError, ValueError)):
            _renderer_bridge(restored, authorization, artifact_root=artifact_root)

    assert restored.model_dump(mode="python") == before_state
    assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")) == before_tree
    assert sentinels == []
    assert len(probe_calls) == 1


def test_clear_persist_and_rebind_makes_only_replacement_authoritative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    directly_bound, authorization = _accepted_state_with_valid_images(artifact_root)
    initial = clear_processed_narration_measurement(directly_bound)
    first = artifact_root / "narration" / "final.mp3"
    second = artifact_root / "narration" / "replacement.mp3"
    second.write_bytes(b"authoritative narration B")
    probe_calls: list[Path] = []

    bound_a = _bind(initial, artifact_root, first, monkeypatch, probe_calls)
    paths = _persist(bound_a, tmp_path / "persisted")
    restored_a = load_runtime_state(paths.runtime_state_path)
    measurement_a = restored_a.processed_narration_measurement
    assert measurement_a is not None

    with pytest.raises(ValueError, match="clear it before replacement"):
        _bind(restored_a, artifact_root, second, monkeypatch, probe_calls)
    assert restored_a.processed_narration_measurement == measurement_a

    cleared = clear_processed_narration_measurement(restored_a)
    assert clear_processed_narration_measurement(cleared) == cleared
    assert restored_a.processed_narration_measurement == measurement_a
    _persist(cleared, tmp_path / "persisted")
    restored_clear = load_runtime_state(paths.runtime_state_path)
    cleared_manifest = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
    assert restored_clear.processed_narration_measurement is None
    assert cleared_manifest["duration_policy"]["measured_duration_assessment"] is None

    bound_b = _bind(restored_clear, artifact_root, second, monkeypatch, probe_calls)
    assert _bind(bound_b, artifact_root, second, monkeypatch, probe_calls) == bound_b
    _persist(bound_b, tmp_path / "persisted")
    restored_b = load_runtime_state(paths.runtime_state_path)
    measurement_b = restored_b.processed_narration_measurement
    assert measurement_b is not None
    assert measurement_b.artifact_sha256 != measurement_a.artifact_sha256
    assert _bridge_path(restored_b, authorization, artifact_root, second).source_state_ready
    with pytest.raises(ValueError, match="path does not match"):
        _bridge_path(restored_b, authorization, artifact_root, first)


def test_stale_manifest_variants_cannot_change_state_resume_or_renderer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_root, narration, restored, authorization, paths, probe_calls = _bound_restart_fixture(
        tmp_path, monkeypatch
    )
    expected_state = restored.model_dump(mode="python")
    expected_resume = plan_resume(restored)
    canonical = build_duration_policy_report(restored).model_dump(mode="json")
    different_measured = deepcopy(canonical)
    different_measured["measured_duration_assessment"]["actual_duration_seconds"] = 999.0
    forged_count = deepcopy(canonical)
    forged_count["warning_count"] = 999
    malformed = deepcopy(canonical)
    malformed["measured_duration_assessment"] = "forged"
    variants: tuple[object, ...] = (
        {"schema_version": 1},
        {"schema_version": 1, "duration_policy": {"schema_version": 1}},
        {"schema_version": 1, "duration_policy": different_measured},
        {"schema_version": 1, "duration_policy": forged_count},
        {"schema_version": 1, "duration_policy": malformed},
        "{invalid manifest json",
    )

    for variant in variants:
        if isinstance(variant, str):
            paths.manifest_path.write_text(variant, encoding="utf-8")
        else:
            paths.manifest_path.write_text(json.dumps(variant), encoding="utf-8")
        loaded = load_runtime_state(paths.runtime_state_path)
        assert loaded.model_dump(mode="python") == expected_state
        assert plan_resume(loaded) == expected_resume
        assert _renderer_bridge(
            loaded,
            authorization,
            artifact_root=artifact_root,
        ).source_state_ready

        persist_execution_snapshot(
            loaded,
            paths,
            execution_purpose="stale-manifest-rebuild",
            selected_scene_id="scene_01",
        )
        rebuilt = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
        assert rebuilt["duration_policy"] == canonical
        assert (
            load_runtime_state(paths.runtime_state_path).model_dump(mode="python") == expected_state
        )

    assert narration.exists()
    assert probe_calls == [narration.resolve()]


def test_independent_duration_policy_planning_hash_lock_remains_exact() -> None:
    duration_policy_state = _duration_policy_lock_state((5.0,) * 7)

    assert duration_policy_state.run_plan.planning_hash == _DURATION_POLICY_PLANNING_HASH_LOCK


def test_current_full_canary_synthetic_authority_chain_is_state_based(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    story = _full_canary_story_plan()
    assert canonical_story_plan_sha256(story) == FULL_CANARY_STORY_PLAN_SHA256
    run = build_production_run_plan(
        job_id="full-canary-synthetic-authority",
        story_plan=story,
        scene_briefs=build_full_canary_scene_briefs(story),
        reference_catalog=load_reference_catalog(None),
    )
    initial_planning_hash = run.planning_hash
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    directly_bound, authorization = _accepted_state_with_valid_images(
        artifact_root,
        run=run,
    )
    state = clear_processed_narration_measurement(directly_bound)
    narration = artifact_root / "narration" / "final.mp3"
    narration_bytes = b"current full canary synthetic narration authority"
    narration.write_bytes(narration_bytes)
    probe_calls: list[Path] = []
    bound = _bind(state, artifact_root, narration, monkeypatch, probe_calls)
    paths = _persist(bound, tmp_path / "persisted")
    paths.manifest_path.write_text("{invalid stale manifest", encoding="utf-8")

    restored = load_runtime_state(paths.runtime_state_path)
    resume = plan_resume(restored)
    bridge = _renderer_bridge(restored, authorization, artifact_root=artifact_root)
    persist_execution_snapshot(
        restored,
        paths,
        execution_purpose="full-canary-synthetic-authority",
        selected_scene_id="scene_01",
    )
    manifest = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
    measurement = restored.processed_narration_measurement
    assert measurement is not None

    assert authorization.planning_hash == initial_planning_hash
    assert restored.run_plan.planning_hash == initial_planning_hash
    assert canonical_story_plan_sha256(restored.run_plan.story_plan) == (
        FULL_CANARY_STORY_PLAN_SHA256
    )
    assert measurement.artifact_sha256 == hashlib.sha256(narration_bytes).hexdigest()
    assert manifest["duration_policy"]["schema_version"] == 2
    assert manifest["duration_policy"] == build_duration_policy_report(restored).model_dump(
        mode="json"
    )
    assert all(item.action is ResumeAction.SKIP_ACCEPTED for item in resume.scenes)
    assert len(bridge.accepted_inputs) == 8
    assert bridge.external_calls == bridge.files_written == 0
    assert bridge.renderer_plan.processed_narration_duration == _MEASURED_DURATION_SECONDS
    assert probe_calls == [narration.resolve()]
    assert "final_video_duration" not in manifest["duration_policy"]


def test_pre_source_span_schema_one_payload_is_rejected_without_repair(
    tmp_path: Path,
) -> None:
    state = initialize_execution_state(
        build_fixture_preview_run(
            topic="unsupported pre-source-span payload",
            job_id="unsupported-pre-source-span",
        )
    )
    payload = _legacy_payload(state)
    for beat in payload["run_plan"]["story_plan"]["semantic_beats"]:
        beat.pop("source_span")
    before = deepcopy(payload)
    runtime_path = tmp_path / "runtime_state.json"
    runtime_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError) as failure:
        load_runtime_state(runtime_path)

    assert "source_span" in str(failure.value)
    assert payload == before
