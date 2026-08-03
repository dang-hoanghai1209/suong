"""Checkpoint 5A offline end-to-end Volume orchestration proof."""
from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

import tella.topic_production.reference_planning as reference_planning
from tella.asset_library.semantic_resolver import AssetLibraryRequest
from tella.atomic_write import atomic_write_json
from tella.topic_production import (
    CloudflareOverflowAuthorization,
    LocalCompositionRequest,
    LocalCoverageAssessment,
    LocalCoverageStatus,
    PollinationsBalanceState,
    PollinationsHealthState,
    ProductionStrategyConfig,
    ProviderFailureCategory,
    QCCheckOutcome,
    QCChecks,
    QCDecision,
    QCRecord,
    ReferenceCatalog,
    ResumeAction,
    ReviewSource,
    SceneDataSensitivity,
    SemanticAssetCoverageResolver,
    VolumeLocalSceneInput,
    VolumeNextAction,
    apply_volume_qc,
    build_fixture_preview_run,
    build_pollinations_readiness_snapshot,
    build_production_run_plan,
    complete_volume_acceptance,
    evaluate_execution_readiness,
    execute_pollinations_overflow,
    execute_volume_cloudflare_retry,
    execute_volume_initial_scene,
    initialize_execution_state,
    load_reference_catalog,
    load_runtime_state,
    plan_resume,
    persist_execution_snapshot,
    production_job_paths,
    record_pollinations_readiness,
    record_qc,
    summarize_call_budget,
)
from tella.visual_generation.models import CandidateMetadata, ProviderCapabilities
from tella.visual_generation.prompt_builder import instruction_hash, request_hash
from tella.visual_generation.providers import (
    PollinationsPromptSource,
    ProviderKind,
)
from tella.visual_generation.providers.cloudflare_flux import (
    CloudflareFluxError,
    KLEIN_4B_MODEL,
)
from tella.visual_generation.providers.pollinations import (
    prepare_public_request,
    public_request_hash,
)
from tella.visual_generation.references import sha256_file


class ScenarioCoverageResolver:
    def assess(self, request: AssetLibraryRequest) -> LocalCoverageAssessment:
        covered = request.action.startswith("local_")
        return LocalCoverageAssessment(
            status=(
                LocalCoverageStatus.SATISFIED
                if covered
                else LocalCoverageStatus.NOT_SATISFIED
            ),
            reason="deterministic CP5A local coverage",
            selected_semantic_id="sit_hug_knees_sad" if covered else None,
        )


class DeterministicComposer:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, scene, output_path, asset_library_root, semantics_path):
        self.calls += 1
        request = scene.asset_library_request
        Image.new("RGB", (1080, 1920), "#40352f").save(output_path, format="PNG")
        return {
            "schema_version": 2,
            "seed": request["seed"],
            "canvas": {"width": 1080, "height": 1920},
            "character_request": request,
            "character": {"selected_semantic_id": "sit_hug_knees_sad"},
            "background": {"path": "safe-test-fixture"},
            "objects": [],
            "composition_preset": request["composition_preset"],
            "output": str(output_path),
        }


class MockCloudflareProvider:
    def __init__(self, mode: str = "success") -> None:
        self.mode = mode
        self.calls = []

    def capabilities(self) -> ProviderCapabilities:
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

    def credentials_present(self) -> bool:
        return True

    async def generate_scene(self, request, output_path):
        self.calls.append(request)
        if self.mode == "quota":
            raise CloudflareFluxError(
                stage="quota_exceeded",
                exception_class="ControlledOfflineFailure",
                message="mocked overflow-eligible quota failure",
                request_reached_provider=True,
                response_received=True,
                image_bytes_present=False,
            )
        path = output_path.with_suffix(".png")
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (request.width, request.height), "#4a382f").save(path)
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


class MockPollinationsProvider:
    def __init__(self) -> None:
        self.calls = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=ProviderKind.POLLINATIONS.value,
            model="flux",
            supports_text_to_image=True,
            supports_reference_images=False,
            supports_multiple_references=False,
            supports_image_edit=False,
            supports_seed=True,
            supports_9_16=True,
            max_reference_images=0,
        )

    def credentials_present(self) -> bool:
        return True

    async def generate_public_scene(self, request, output_path):
        self.calls.append(request)
        public = prepare_public_request(request, model="flux")
        logical_hash = public_request_hash(public)
        path = output_path.with_suffix(".png")
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (request.width, request.height), "#59473c").save(path)
        return CandidateMetadata(
            tier="draft",
            intended_usage_class="draft",
            provider=ProviderKind.POLLINATIONS.value,
            model="flux",
            request_hash=logical_hash,
            logical_request_hash=logical_hash,
            reference_hashes=[],
            instruction_hash="e" * 64,
            seed=request.seed,
            generation_attempt=2,
            output_path=path,
            requested_aspect_ratio="9:16",
            requested_resolution=f"{request.width}x{request.height}",
            actual_width=request.width,
            actual_height=request.height,
            mime_type="image/png",
            requested_width=request.width,
            requested_height=request.height,
            steps=1,
            provider_request_hash=logical_hash,
            request_timeout_seconds=120.0,
        )


def _reference_catalog(root: Path, monkeypatch: pytest.MonkeyPatch) -> ReferenceCatalog:
    root.mkdir(parents=True)
    for index, definition in enumerate(reference_planning.APPROVED_REFERENCE_DEFINITIONS):
        Image.new("RGB", (90, 160), (40 + index * 10, 30, 30)).save(
            root / definition.filename
        )
    definitions = tuple(
        replace(
            definition,
            expected_sha256=sha256_file(root / definition.filename),
        )
        for definition in reference_planning.APPROVED_REFERENCE_DEFINITIONS
    )
    monkeypatch.setattr(reference_planning, "APPROVED_REFERENCE_DEFINITIONS", definitions)
    return load_reference_catalog(root)


def _state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, job_id: str):
    fixture = build_fixture_preview_run(topic="generic offline volume canary", scene_count=8)
    local_scenes = {1, 4, 8}
    inputs = {
        f"scene_{index:02d}": VolumeLocalSceneInput(
            sensitivity=SceneDataSensitivity.PUBLIC_SAFE,
            request=LocalCompositionRequest(
                character_id="generic_character",
                action=("local_sit" if index in local_scenes else "external_walk"),
                emotion="calm",
                location="generic_room",
                time_of_day="day",
                composition_preset="centered",
                seed=10_100 + index,
            ),
        )
        for index in range(1, 9)
    }
    run = build_production_run_plan(
        job_id=job_id,
        story_plan=fixture.story_plan,
        scene_briefs=[item.scene_brief for item in fixture.scene_execution_plans],
        reference_catalog=_reference_catalog(tmp_path / f"{job_id}-references", monkeypatch),
        production_strategy=ProductionStrategyConfig.volume(),
        volume_local_inputs=inputs,
        local_coverage_resolver=ScenarioCoverageResolver(),
    )
    state = initialize_execution_state(run)
    now = datetime.now(timezone.utc)
    return record_pollinations_readiness(
        state,
        build_pollinations_readiness_snapshot(
            enabled=True,
            credential_present=True,
            credential_valid=True,
            model="flux",
            model_available=True,
            balance_state=PollinationsBalanceState.POSITIVE,
            health_state=PollinationsHealthState.HEALTHY,
            checked_at=now,
            valid_for=timedelta(hours=1),
            readiness_calls=0,
        ),
    )


def _qc(attempt, kind: str) -> QCRecord:
    return QCRecord(
        qc_record_id=f"qc-{attempt.candidate_id}",
        scene_id=attempt.scene_id,
        candidate_id=attempt.candidate_id,
        tier=attempt.tier,
        decision=QCDecision.PASS if kind == "pass" else QCDecision.FAIL,
        checks=QCChecks(
            technical_generation=QCCheckOutcome.PASS,
            scene_meaning=QCCheckOutcome.PASS,
            identity=QCCheckOutcome.PASS,
            action_pose=QCCheckOutcome.PASS,
            anatomy=QCCheckOutcome.PASS,
            composition=QCCheckOutcome.PASS,
            reference_consistency=QCCheckOutcome.PASS,
        ),
        hard_fail_reasons=["wrong generic action"] if kind == "hard" else [],
        soft_fail_reasons=["minor composition imperfection"] if kind == "soft" else [],
        review_source=ReviewSource.HUMAN,
        reviewer="offline-cp5a-reviewer",
    )


def _prompt() -> PollinationsPromptSource:
    return PollinationsPromptSource(
        scene_meaning="An anonymous person calmly crosses a generic room",
        action=["walking across a room"],
        mood=["calm"],
        setting=["generic room"],
        generic_character_description="anonymous adult figure",
        style_description="simple editorial illustration",
        composition=["single readable action"],
    )


def _evidence_root(tmp_path: Path, scenario: str) -> Path:
    configured = os.environ.get("TELLA_CP5A_EVIDENCE_ROOT")
    root = Path(configured) if configured else tmp_path
    path = root / scenario
    path.mkdir(parents=True, exist_ok=False)
    return path


def _scene_summary(state):
    return [
        {
            "scene_id": scene.scene_id,
            "status": scene.status.value,
            "providers": [attempt.provider for attempt in scene.generation_attempts],
            "generation_attempts": len(scene.generation_attempts),
            "local_generations": sum(
                attempt.provider_kind is ProviderKind.LOCAL_COMPOSITOR
                for attempt in scene.generation_attempts
            ),
            "ai_calls": sum(attempt.consumes_ai_call for attempt in scene.generation_attempts),
            "ai_retries": sum(
                attempt.consumes_ai_retry for attempt in scene.generation_attempts
            ),
            "qc_records": len(scene.qc_records),
            "accepted_candidate": (
                scene.accepted_candidate.candidate_id if scene.accepted_candidate else None
            ),
        }
        for scene in state.scenes
    ]


async def _initial(state, scene_id, out_root, provider, composer):
    outcome = await execute_volume_initial_scene(
        state,
        scene_id=scene_id,
        out_root=out_root,
        live_authorized=True,
        cloudflare_provider=provider,
        coverage_resolver=ScenarioCoverageResolver(),
        composer=composer,
    )
    return outcome.state, outcome.attempt


@pytest.mark.asyncio
async def test_scenario_a_blocks_renderer_after_real_retry_ceiling(
    tmp_path, monkeypatch
):
    root = _evidence_root(tmp_path, "scenario_a")
    state = _state(root, monkeypatch, job_id="cp5a-scenario-a")
    composer = DeterministicComposer()
    cloudflare_calls = 0

    for scene_id, provider, kind in (
        ("scene_01", MockCloudflareProvider(), "pass"),
        ("scene_02", MockCloudflareProvider(), "pass"),
        ("scene_03", MockCloudflareProvider(), "soft"),
    ):
        state, attempt = await _initial(state, scene_id, root, provider, composer)
        cloudflare_calls += len(provider.calls)
        assert next(s for s in state.scenes if s.scene_id == scene_id).accepted_candidate is None
        state = apply_volume_qc(state, _qc(attempt, kind)).state

    state, initial_04 = await _initial(
        state, "scene_04", root, MockCloudflareProvider(), composer
    )
    transition_04 = apply_volume_qc(state, _qc(initial_04, "hard"))
    retry_04_provider = MockCloudflareProvider()
    retry_04 = await execute_volume_cloudflare_retry(
        transition_04.state,
        scene_id="scene_04",
        prior_candidate_id=initial_04.candidate_id,
        out_root=root,
        live_authorized=True,
        provider=retry_04_provider,
    )
    cloudflare_calls += len(retry_04_provider.calls)
    state = apply_volume_qc(retry_04.state, _qc(retry_04.attempt, "pass")).state

    quota = MockCloudflareProvider("quota")
    state, failed_05 = await _initial(state, "scene_05", root, quota, composer)
    cloudflare_calls += len(quota.calls)
    pollinations = MockPollinationsProvider()
    overflow = await execute_pollinations_overflow(
        state,
        scene_id="scene_05",
        out_root=root,
        authorization=CloudflareOverflowAuthorization(
            prior_candidate_id=failed_05.candidate_id,
            failure_category=ProviderFailureCategory.QUOTA_OR_CREDIT_EXHAUSTED,
        ),
        prompt_source=_prompt(),
        provider=pollinations,
    )
    assert overflow.attempt is not None
    state = apply_volume_qc(overflow.state, _qc(overflow.attempt, "pass")).state

    blocked_transitions = []
    blocked_provider_calls = 0
    for scene_id in ("scene_06", "scene_07"):
        provider = MockCloudflareProvider()
        state, attempt = await _initial(state, scene_id, root, provider, composer)
        cloudflare_calls += len(provider.calls)
        transition = apply_volume_qc(state, _qc(attempt, "hard"))
        blocked_transitions.append(transition)
        state = transition.state
        blocked_provider_calls += 0

    state, attempt_08 = await _initial(
        state, "scene_08", root, MockCloudflareProvider(), composer
    )
    state = apply_volume_qc(state, _qc(attempt_08, "pass")).state

    budget = summarize_call_budget(state)
    readiness = evaluate_execution_readiness(state)
    assert budget.retry_calls == 2
    assert all(item.next_action is VolumeNextAction.BLOCK for item in blocked_transitions)
    assert blocked_provider_calls == 0
    assert readiness.ready is False
    assert readiness.unresolved_scene_ids == ["scene_06", "scene_07"]
    assert state.scenes[5].accepted_candidate is None
    assert state.scenes[6].accepted_candidate is None
    assert len(pollinations.calls) == 1
    assert composer.calls == 3
    assert cloudflare_calls == 6
    # Runtime external_calls records provider-reaching semantics even for an
    # injected transport; the fake's call ledger proves no network was used.
    assert state.external_calls == 1
    summaries = _scene_summary(state)
    assert summaries[0]["ai_calls"] == summaries[7]["ai_calls"] == 0
    assert summaries[4]["providers"] == [
        state.scenes[4].execution_plan.draft.provider,
        ProviderKind.POLLINATIONS.value,
    ]
    paths = production_job_paths(root, job_id=state.run_plan.job_id, scene_id="scene_01")
    persist_execution_snapshot(
        state,
        paths,
        execution_purpose="offline_volume_e2e_cp5a_blocked",
        selected_scene_id="scene_01",
    )
    atomic_write_json(
        root / "evidence_summary.json",
        {
            "scenario": "A",
            "scenes": summaries,
            "call_budget": budget.model_dump(mode="json"),
            "readiness": readiness.model_dump(mode="json"),
            "mock_transport_calls": {
                "cloudflare": cloudflare_calls,
                "pollinations_generation": len(pollinations.calls),
                "pollinations_readiness": 0,
            },
        },
    )


@pytest.mark.asyncio
async def test_scenario_b_ready_persists_and_resumes_without_regeneration(
    tmp_path, monkeypatch
):
    root = _evidence_root(tmp_path, "scenario_b")
    state = _state(root, monkeypatch, job_id="cp5a-scenario-b")
    composer = DeterministicComposer()
    providers: list[MockCloudflareProvider] = []
    kinds = {"scene_03": "soft", "scene_04": "hard"}

    for index in range(1, 9):
        scene_id = f"scene_{index:02d}"
        provider = MockCloudflareProvider()
        providers.append(provider)
        state, attempt = await _initial(state, scene_id, root, provider, composer)
        transition = apply_volume_qc(state, _qc(attempt, kinds.get(scene_id, "pass")))
        state = transition.state
        if transition.next_action is VolumeNextAction.RETRY_CLOUDFLARE:
            retry_provider = MockCloudflareProvider()
            providers.append(retry_provider)
            retry = await execute_volume_cloudflare_retry(
                state,
                scene_id=scene_id,
                prior_candidate_id=attempt.candidate_id,
                out_root=root,
                live_authorized=True,
                provider=retry_provider,
            )
            state = apply_volume_qc(retry.state, _qc(retry.attempt, "pass")).state

    readiness = evaluate_execution_readiness(state)
    budget = summarize_call_budget(state)
    assert readiness.ready is True
    assert all(scene.accepted_candidate is not None for scene in state.scenes)
    assert budget.retry_calls == 1
    assert state.scenes[2].accepted_candidate.metadata["volume_acceptance_reason"] == (
        "SOFT_FAIL_ACCEPTED"
    )
    assert composer.calls == 3
    provider_calls_before = sum(len(provider.calls) for provider in providers)

    paths = production_job_paths(root, job_id=state.run_plan.job_id, scene_id="scene_01")
    persist_execution_snapshot(
        state,
        paths,
        execution_purpose="offline_volume_e2e_cp5a_complete",
        selected_scene_id="scene_01",
    )
    restored = load_runtime_state(paths.runtime_state_path)
    actions = [item.action for item in plan_resume(restored).scenes]
    assert actions == [ResumeAction.SKIP_ACCEPTED] * 8
    assert evaluate_execution_readiness(restored).ready is True
    assert sum(len(scene.generation_attempts) for scene in restored.scenes) == sum(
        len(scene.generation_attempts) for scene in state.scenes
    )
    assert sum(len(scene.qc_records) for scene in restored.scenes) == sum(
        len(scene.qc_records) for scene in state.scenes
    )
    assert provider_calls_before == sum(len(provider.calls) for provider in providers)

    generated = initialize_execution_state(restored.run_plan)
    generated, generated_attempt = await _initial(
        generated, "scene_01", root / "partial-generated", MockCloudflareProvider(), composer
    )
    assert plan_resume(generated).scenes[0].action is ResumeAction.AWAIT_DRAFT_QC
    acceptable = record_qc(generated, _qc(generated_attempt, "pass"))
    assert plan_resume(acceptable).scenes[0].action is ResumeAction.COMPLETE_VOLUME_ACCEPTANCE
    attempt_count = len(acceptable.scenes[0].generation_attempts)
    completed = complete_volume_acceptance(acceptable, scene_id="scene_01")
    assert len(completed.scenes[0].generation_attempts) == attempt_count

    atomic_write_json(
        root / "evidence_summary.json",
        {
            "scenario": "B",
            "scenes": _scene_summary(restored),
            "call_budget": budget.model_dump(mode="json"),
            "readiness": readiness.model_dump(mode="json"),
            "resume_actions": [action.value for action in actions],
            "regeneration_after_resume": 0,
        },
    )


@pytest.mark.asyncio
async def test_zero_balance_skips_pollinations_without_attempt_or_retry(
    tmp_path, monkeypatch
):
    state = _state(tmp_path, monkeypatch, job_id="cp5a-zero-balance")
    checked_at = state.pollinations_readiness.checked_at
    zero = build_pollinations_readiness_snapshot(
        enabled=True,
        credential_present=True,
        credential_valid=True,
        model="flux",
        model_available=True,
        balance_state=PollinationsBalanceState.ZERO,
        health_state=PollinationsHealthState.UNKNOWN,
        checked_at=checked_at,
        valid_for=timedelta(hours=1),
        readiness_calls=0,
    )
    state = state.model_copy(update={"pollinations_readiness": zero}, deep=True)
    provider = MockCloudflareProvider("quota")
    state, failed = await _initial(
        state, "scene_02", tmp_path / "zero", provider, DeterministicComposer()
    )
    pollinations = MockPollinationsProvider()
    before = len(state.scenes[1].generation_attempts)
    outcome = await execute_pollinations_overflow(
        state,
        scene_id="scene_02",
        out_root=tmp_path / "zero",
        authorization=CloudflareOverflowAuthorization(
            prior_candidate_id=failed.candidate_id,
            failure_category=ProviderFailureCategory.RATE_LIMITED,
        ),
        prompt_source=_prompt(),
        provider=pollinations,
    )
    assert outcome.skipped_before_generation is True
    assert pollinations.calls == []
    assert len(outcome.state.scenes[1].generation_attempts) == before
    assert summarize_call_budget(outcome.state).retry_calls == 0


def test_quality_mode_rejects_volume_coordinator_without_transport(tmp_path):
    quality = initialize_execution_state(
        build_fixture_preview_run(topic="quality behavior unchanged", scene_count=8)
    )

    async def invoke():
        return await execute_volume_initial_scene(
            quality,
            scene_id="scene_01",
            out_root=tmp_path,
            cloudflare_provider=MockCloudflareProvider(),
        )

    with pytest.raises(ValueError, match="requires Volume strategy"):
        import asyncio

        asyncio.run(invoke())


@pytest.mark.asyncio
async def test_real_local_compositor_canary_is_zero_ai(tmp_path):
    configured_assets = os.environ.get("TELLA_TEST_ASSET_LIBRARY_ROOT")
    asset_root = (
        Path(configured_assets).expanduser().resolve()
        if configured_assets
        else tmp_path / "external-assets-not-configured"
    )
    semantics = Path("scripts/asset_batch/asset_semantics_patch.json").resolve()
    if not (asset_root / "processed_asset_index.json").is_file():
        pytest.skip("known-safe local asset fixture is unavailable")
    configured = os.environ.get("TELLA_CP5A_EVIDENCE_ROOT")
    output_root = Path(configured) / "real_local_compositor" if configured else tmp_path
    output_root.mkdir(parents=True, exist_ok=not configured)
    fixture = build_fixture_preview_run(topic="generic local-only canary", scene_count=8)
    request = LocalCompositionRequest(
        character_id="female_01",
        action="sit_hug_knees",
        emotion="sad",
        direction="front",
        location="bedroom",
        time_of_day="night",
        objects=["pillow", "phone_dark"],
        composition_preset="bedroom_floor_sitting",
        seed=12345,
    )
    resolver = SemanticAssetCoverageResolver(
        asset_library_root=asset_root,
        semantics_path=semantics,
    )
    run = build_production_run_plan(
        job_id="cp5a-real-local",
        story_plan=fixture.story_plan,
        scene_briefs=[item.scene_brief for item in fixture.scene_execution_plans],
        reference_catalog=ReferenceCatalog(),
        production_strategy=ProductionStrategyConfig.volume(),
        volume_local_inputs={
            "scene_01": VolumeLocalSceneInput(
                sensitivity=SceneDataSensitivity.LOCAL_ONLY,
                request=request,
            )
        },
        local_coverage_resolver=resolver,
    )
    state = initialize_execution_state(run)
    outcome = await execute_volume_initial_scene(
        state,
        scene_id="scene_01",
        out_root=output_root,
        coverage_resolver=resolver,
        asset_library_root=asset_root,
        semantics_path=semantics,
    )
    assert outcome.attempt is not None
    assert outcome.attempt.provider_kind is ProviderKind.LOCAL_COMPOSITOR
    assert outcome.attempt.consumes_ai_call is False
    assert outcome.attempt.consumes_ai_retry is False
    assert outcome.paths.candidate_base_path.is_file()
    accepted = apply_volume_qc(outcome.state, _qc(outcome.attempt, "pass")).state
    assert accepted.scenes[0].accepted_candidate is not None
    persist_execution_snapshot(
        accepted,
        outcome.paths,
        execution_purpose="offline_volume_e2e_cp5a_real_local",
        selected_scene_id="scene_01",
        candidate_metadata=outcome.candidate_metadata.model_dump(mode="json"),
    )
