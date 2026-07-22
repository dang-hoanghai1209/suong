from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest
from PIL import Image

from tella.asset_library.semantic_resolver import AssetLibraryRequest
import tella.topic_production.reference_planning as reference_planning
from tella.topic_production import (
    ExecutionRunState,
    GenerationAttempt,
    GenerationTier,
    LocalCompositionRequest,
    LocalCoverageAssessment,
    LocalCoverageStatus,
    ProductionSceneStatus,
    ProductionStrategyConfig,
    PromotionReason,
    QCCheckOutcome,
    QCChecks,
    QCDecision,
    QCRecord,
    ReferenceCatalog,
    ResumeAction,
    ReviewSource,
    SceneDataSensitivity,
    TechnicalStatus,
    VolumeAcceptanceReason,
    VolumeCandidateDisposition,
    VolumeLocalSceneInput,
    VolumeNextAction,
    apply_volume_qc,
    build_draft_execution_preview,
    build_fixture_preview_run,
    build_production_run_plan,
    complete_volume_acceptance,
    execute_draft_scene,
    initialize_execution_state,
    load_reference_catalog,
    plan_resume,
    promote_scene_to_acceptance,
    record_generation_attempt,
    record_local_generation_attempt,
    record_qc,
    record_volume_retry_attempt,
    summarize_call_budget,
)
from tella.visual_generation.providers import ProviderKind
from tella.visual_generation.models import CandidateMetadata, ProviderCapabilities
from tella.visual_generation.prompt_builder import instruction_hash, request_hash
from tella.visual_generation.providers.cloudflare_flux import KLEIN_4B_MODEL


class CoverageResolver:
    def __init__(self, status: LocalCoverageStatus):
        self.status = status

    def assess(self, request: AssetLibraryRequest) -> LocalCoverageAssessment:
        return LocalCoverageAssessment(
            status=self.status,
            reason=f"controlled {self.status.value}",
            selected_semantic_id=(
                "generic_sitting_pose"
                if self.status is LocalCoverageStatus.SATISFIED
                else None
            ),
        )


def _local_request(seed: int = 10101) -> LocalCompositionRequest:
    return LocalCompositionRequest(
        character_id="generic_character",
        action="sit_quietly",
        emotion="calm",
        location="generic_room",
        time_of_day="night",
        composition_preset="centered",
        seed=seed,
    )


def _state(
    *,
    sensitivity: SceneDataSensitivity = SceneDataSensitivity.PUBLIC_SAFE,
    covered: bool = False,
    scene_count: int = 1,
    job_id: str = "volume-execution",
    reference_catalog: ReferenceCatalog | None = None,
):
    fixture = build_fixture_preview_run(topic="one shot volume execution", scene_count=7)
    inputs = {
        f"scene_{index:02d}": VolumeLocalSceneInput(
            sensitivity=sensitivity,
            request=_local_request(seed=10100 + index),
        )
        for index in range(1, scene_count + 1)
    }
    run = build_production_run_plan(
        job_id=job_id,
        story_plan=fixture.story_plan,
        scene_briefs=[scene.scene_brief for scene in fixture.scene_execution_plans],
        reference_catalog=reference_catalog or ReferenceCatalog(),
        production_strategy=ProductionStrategyConfig.volume(),
        volume_local_inputs=inputs,
        local_coverage_resolver=CoverageResolver(
            LocalCoverageStatus.SATISFIED
            if covered
            else LocalCoverageStatus.NOT_SATISFIED
        ),
    )
    return initialize_execution_state(run)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _initial_attempt(state, scene_id="scene_01", *, local=False):
    scene = next(item for item in state.scenes if item.scene_id == scene_id)
    if local:
        plan = scene.execution_plan.local_execution
        assert plan is not None
        candidate_id = f"{scene_id}-local-initial"
        return GenerationAttempt(
            scene_id=scene_id,
            tier=GenerationTier.DRAFT,
            provider=ProviderKind.LOCAL_COMPOSITOR.value,
            provider_kind=ProviderKind.LOCAL_COMPOSITOR,
            model="semantic_asset_compositor_v2",
            seed=plan.request.seed,
            candidate_id=candidate_id,
            candidate_path=f"fixtures/{candidate_id}.png",
            artifact_sha256=_digest(candidate_id),
            planning_request_hash=plan.logical_request_hash,
            logical_request_hash=plan.logical_request_hash,
            reference_hashes=[],
            technical_status=TechnicalStatus.SUCCEEDED,
            simulated=True,
            consumes_ai_call=False,
            consumes_ai_retry=False,
        )
    draft = scene.execution_plan.draft
    candidate_id = f"{scene_id}-cloudflare-initial"
    return GenerationAttempt(
        scene_id=scene_id,
        tier=GenerationTier.DRAFT,
        provider=draft.provider,
        provider_kind=ProviderKind.CLOUDFLARE_KLEIN_4B,
        model=draft.model,
        seed=draft.seed,
        candidate_id=candidate_id,
        candidate_path=f"fixtures/{candidate_id}.png",
        artifact_sha256=_digest(candidate_id),
        planning_request_hash=draft.logical_visual_request_hash,
        logical_request_hash=draft.logical_visual_request_hash,
        reference_hashes=[item.sha256 for item in draft.references],
        technical_status=TechnicalStatus.SUCCEEDED,
        simulated=True,
        consumes_ai_call=True,
        consumes_ai_retry=False,
    )


def _retry_attempt(state, scene_id="scene_01", *, suffix="retry-01"):
    scene = next(item for item in state.scenes if item.scene_id == scene_id)
    draft = scene.execution_plan.draft
    candidate_id = f"{scene_id}-cloudflare-{suffix}"
    return GenerationAttempt(
        scene_id=scene_id,
        tier=GenerationTier.DRAFT,
        provider=draft.provider,
        provider_kind=ProviderKind.CLOUDFLARE_KLEIN_4B,
        model=draft.model,
        seed=draft.seed,
        candidate_id=candidate_id,
        candidate_path=f"fixtures/{candidate_id}.png",
        artifact_sha256=_digest(candidate_id),
        planning_request_hash=draft.logical_visual_request_hash,
        logical_request_hash=draft.logical_visual_request_hash,
        reference_hashes=[item.sha256 for item in draft.references],
        technical_status=TechnicalStatus.SUCCEEDED,
        simulated=True,
        consumes_ai_call=True,
        consumes_ai_retry=True,
    )


def _checks() -> QCChecks:
    return QCChecks(
        technical_generation=QCCheckOutcome.PASS,
        scene_meaning=QCCheckOutcome.PASS,
        identity=QCCheckOutcome.PASS,
        action_pose=QCCheckOutcome.PASS,
        anatomy=QCCheckOutcome.PASS,
        composition=QCCheckOutcome.PASS,
        reference_consistency=QCCheckOutcome.PASS,
    )


def _qc(attempt, *, kind="pass", suffix="01") -> QCRecord:
    decision = QCDecision.PASS if kind == "pass" else QCDecision.FAIL
    return QCRecord(
        qc_record_id=f"qc-{attempt.candidate_id}-{suffix}",
        scene_id=attempt.scene_id,
        candidate_id=attempt.candidate_id,
        tier=GenerationTier.DRAFT,
        decision=decision,
        checks=_checks(),
        hard_fail_reasons=["completely wrong requested action"] if kind == "hard" else [],
        soft_fail_reasons=["minor composition imperfection"] if kind == "soft" else [],
        review_source=ReviewSource.HUMAN,
        reviewer="volume-test-reviewer",
    )


def _record_initial(state, scene_id="scene_01", *, local=False):
    attempt = _initial_attempt(state, scene_id, local=local)
    if local:
        return record_local_generation_attempt(state, attempt), attempt
    return record_generation_attempt(state, attempt), attempt


def _state_with_references(tmp_path, monkeypatch):
    root = tmp_path / "approved-references"
    root.mkdir()
    for index, definition in enumerate(reference_planning.APPROVED_REFERENCE_DEFINITIONS):
        Image.new("RGB", (90, 160), (40 + index * 10, 30, 30)).save(
            root / definition.filename
        )
    definitions = tuple(
        replace(
            definition,
            expected_sha256=hashlib.sha256((root / definition.filename).read_bytes()).hexdigest(),
        )
        for definition in reference_planning.APPROVED_REFERENCE_DEFINITIONS
    )
    monkeypatch.setattr(reference_planning, "APPROVED_REFERENCE_DEFINITIONS", definitions)
    return _state(
        job_id="volume-cloudflare-initial",
        reference_catalog=load_reference_catalog(root),
    )


class FakeCloudflareProvider:
    def __init__(self):
        self.calls = []

    def capabilities(self):
        return ProviderCapabilities(
            provider_id="cloudflare-flux",
            model=KLEIN_4B_MODEL,
            supports_text_to_image=True,
            supports_reference_images=True,
            supports_multiple_references=True,
            supports_image_edit=False,
            supports_seed=True,
            supports_9_16=True,
            max_reference_images=4,
        )

    def credentials_present(self):
        return True

    async def generate_scene(self, request, output_path):
        self.calls.append(request)
        path = output_path.with_suffix(".png")
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (request.width, request.height), "#40352f").save(path)
        logical_hash = request_hash(request)
        return CandidateMetadata(
            tier="draft",
            intended_usage_class="draft",
            provider="cloudflare-flux",
            model=KLEIN_4B_MODEL,
            request_hash=logical_hash,
            logical_request_hash=logical_hash,
            reference_hashes=[item.sha256 for item in request.references],
            instruction_hash=instruction_hash(request),
            seed=request.seed,
            generation_attempt=1,
            output_path=path,
            reference_roles=[item.semantic_roles for item in request.references],
            requested_aspect_ratio="9:16",
            requested_resolution=f"{request.width}x{request.height}",
            actual_width=request.width,
            actual_height=request.height,
            mime_type="image/png",
            requested_width=request.width,
            requested_height=request.height,
            steps=4,
            provider_request_hash="d" * 64,
            request_timeout_seconds=120.0,
        )

    async def edit_scene(self, source_path, request, output_path):
        raise AssertionError("Volume initial execution must not edit or fall back")


def test_volume_allows_only_one_initial_candidate_and_does_not_accept_before_qc():
    state, attempt = _record_initial(_state())

    assert state.scenes[0].accepted_candidate is None
    assert attempt.consumes_ai_call is True
    assert attempt.consumes_ai_retry is False
    with pytest.raises(ValueError, match="not authorized|call budget"):
        record_generation_attempt(
            state,
            _initial_attempt(state).model_copy(update={"candidate_id": "duplicate-initial"}),
        )


def test_volume_external_initial_preview_is_one_shot_cloudflare_only(tmp_path, monkeypatch):
    state = _state_with_references(tmp_path, monkeypatch)
    preview = build_draft_execution_preview(state, scene_id="scene_01")

    assert preview.execution_purpose == "volume_initial_cloudflare"
    assert preview.candidate_count == preview.maximum_provider_calls == 1
    assert preview.retry_calls == preview.fallback_calls == 0
    assert state.scenes[0].execution_plan.draft.provider == "cloudflare-flux"


@pytest.mark.asyncio
async def test_volume_initial_cloudflare_execution_makes_one_injected_call(tmp_path, monkeypatch):
    state = _state_with_references(tmp_path, monkeypatch)
    provider = FakeCloudflareProvider()

    outcome = await execute_draft_scene(
        state,
        scene_id="scene_01",
        out_root=tmp_path,
        dry_run=False,
        live_authorized=True,
        provider=provider,
    )

    assert len(provider.calls) == outcome.provider_invocations == 1
    assert outcome.attempt is not None
    assert outcome.attempt.provider_kind is ProviderKind.CLOUDFLARE_KLEIN_4B
    assert outcome.attempt.consumes_ai_call is True
    assert outcome.attempt.consumes_ai_retry is False
    assert outcome.state.scenes[0].status is ProductionSceneStatus.DRAFT_GENERATED
    assert outcome.state.scenes[0].accepted_candidate is None


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("pass", VolumeAcceptanceReason.VOLUME_USABLE),
        ("soft", VolumeAcceptanceReason.SOFT_FAIL_ACCEPTED),
    ],
)
def test_usable_volume_qc_accepts_through_authoritative_registry_without_retry(kind, reason):
    state, attempt = _record_initial(_state(job_id=f"usable-{kind}"))
    transition = apply_volume_qc(state, _qc(attempt, kind=kind))
    scene = transition.state.scenes[0]

    assert transition.disposition is VolumeCandidateDisposition.ACCEPTABLE_FOR_VOLUME
    assert transition.next_action is VolumeNextAction.ACCEPTED
    assert transition.acceptance_reason is reason
    assert transition.retry_budget_required is False
    assert scene.status is ProductionSceneStatus.ACCEPTED
    assert scene.accepted_candidate is not None
    assert scene.accepted_candidate.candidate_id == attempt.candidate_id
    assert scene.accepted_candidate.accepted_by == "volume_policy"
    assert scene.qc_records[-1].decision is QCDecision.PASS
    assert scene.promotions == []
    assert summarize_call_budget(transition.state).retry_calls == 0


def test_volume_cannot_promote_to_premium_acceptance_tier():
    state, attempt = _record_initial(_state(job_id="no-premium"))
    state = record_qc(state, _qc(attempt))

    with pytest.raises(PermissionError, match="prohibits premium"):
        promote_scene_to_acceptance(
            state,
            scene_id="scene_01",
            reason=PromotionReason.HIGH_ACCEPTANCE_PRIORITY,
            authorized_by="test",
        )


def test_hard_fail_authorizes_at_most_one_cloudflare_retry_for_scene():
    state, initial = _record_initial(_state(job_id="one-hard-retry"))
    hard = apply_volume_qc(state, _qc(initial, kind="hard", suffix="initial"))

    assert hard.next_action is VolumeNextAction.RETRY_CLOUDFLARE
    assert hard.retry_provider is ProviderKind.CLOUDFLARE_KLEIN_4B
    assert summarize_call_budget(hard.state).retry_calls == 0

    retry = _retry_attempt(hard.state)
    retried = record_volume_retry_attempt(
        hard.state, retry, prior_candidate_id=initial.candidate_id
    )
    assert summarize_call_budget(retried).retry_calls == 1
    second_hard = apply_volume_qc(retried, _qc(retry, kind="hard", suffix="retry"))

    assert second_hard.next_action is VolumeNextAction.BLOCK
    assert second_hard.state.scenes[0].status is ProductionSceneStatus.BLOCKED
    with pytest.raises(PermissionError, match="per-scene"):
        record_volume_retry_attempt(
            second_hard.state,
            _retry_attempt(second_hard.state, suffix="retry-02"),
            prior_candidate_id=retry.candidate_id,
        )


def test_run_level_retry_ceiling_blocks_third_ai_retry_and_survives_serialization():
    state = _state(scene_count=3, job_id="run-retry-ceiling")
    retry_attempts = []
    for index in (1, 2):
        scene_id = f"scene_{index:02d}"
        state, initial = _record_initial(state, scene_id)
        hard = apply_volume_qc(state, _qc(initial, kind="hard", suffix="initial"))
        retry = _retry_attempt(hard.state, scene_id)
        state = record_volume_retry_attempt(
            hard.state, retry, prior_candidate_id=initial.candidate_id
        )
        retry_attempts.append(retry)
    restored = ExecutionRunState.model_validate_json(state.model_dump_json())
    assert summarize_call_budget(restored).retry_calls == 2

    restored, third_initial = _record_initial(restored, "scene_03")
    third = apply_volume_qc(
        restored, _qc(third_initial, kind="hard", suffix="initial")
    )

    assert third.next_action is VolumeNextAction.BLOCK
    assert third.state.scenes[2].status is ProductionSceneStatus.BLOCKED
    assert summarize_call_budget(third.state).retry_calls == 2


def test_local_initial_is_zero_ai_and_private_hard_fail_reroutes_to_cloudflare():
    state, local = _record_initial(
        _state(
            covered=True,
            sensitivity=SceneDataSensitivity.PRIVATE,
            job_id="local-private-hard",
        ),
        local=True,
    )
    hard = apply_volume_qc(state, _qc(local, kind="hard"))

    assert local.consumes_ai_call is False
    assert local.consumes_ai_retry is False
    assert hard.next_action is VolumeNextAction.RETRY_CLOUDFLARE
    assert hard.retry_provider is ProviderKind.CLOUDFLARE_KLEIN_4B
    assert ProviderKind.POLLINATIONS is not hard.retry_provider

    retry = _retry_attempt(hard.state)
    retried = record_volume_retry_attempt(
        hard.state, retry, prior_candidate_id=local.candidate_id
    )
    assert summarize_call_budget(retried).completed_calls == 1
    assert summarize_call_budget(retried).retry_calls == 1


def test_local_only_hard_fail_blocks_without_externalizing():
    state, local = _record_initial(
        _state(
            covered=True,
            sensitivity=SceneDataSensitivity.LOCAL_ONLY,
            job_id="local-only-hard",
        ),
        local=True,
    )
    transition = apply_volume_qc(state, _qc(local, kind="hard"))

    assert transition.next_action is VolumeNextAction.BLOCK
    assert transition.retry_provider is None
    assert transition.state.scenes[0].status is ProductionSceneStatus.BLOCKED
    assert summarize_call_budget(transition.state).completed_calls == 0
    assert summarize_call_budget(transition.state).retry_calls == 0


def test_resume_skips_accepted_waits_for_qc_and_completes_interrupted_acceptance():
    base = _state(scene_count=3, job_id="volume-resume")
    base, first = _record_initial(base, "scene_01")
    accepted = apply_volume_qc(base, _qc(first)).state
    accepted, _ = _record_initial(accepted, "scene_02")
    accepted, third = _record_initial(accepted, "scene_03")
    incomplete = record_qc(accepted, _qc(third))
    actions = {item.scene_id: item.action for item in plan_resume(incomplete).scenes}

    assert actions["scene_01"] is ResumeAction.SKIP_ACCEPTED
    assert actions["scene_02"] is ResumeAction.AWAIT_DRAFT_QC
    assert actions["scene_03"] is ResumeAction.COMPLETE_VOLUME_ACCEPTANCE

    completed = complete_volume_acceptance(incomplete, scene_id="scene_03")
    assert completed.scenes[2].accepted_candidate is not None
    assert len(completed.scenes[2].generation_attempts) == 1


def test_quality_mode_cannot_use_volume_acceptance_transition():
    quality = initialize_execution_state(
        build_fixture_preview_run(topic="quality remains manual", scene_count=7)
    )
    quality, attempt = _record_initial(quality)

    with pytest.raises(ValueError, match="requires Volume strategy"):
        apply_volume_qc(quality, _qc(attempt))
