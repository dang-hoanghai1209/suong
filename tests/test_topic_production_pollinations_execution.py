from __future__ import annotations

import hashlib
import io
import json

import pytest
from PIL import Image

from tella.asset_library.semantic_resolver import AssetLibraryRequest
from tella.topic_production import (
    CloudflareOverflowAuthorization,
    GenerationAttempt,
    GenerationTier,
    LocalCompositionRequest,
    LocalCoverageAssessment,
    LocalCoverageStatus,
    ProductionSceneStatus,
    ProductionStrategyConfig,
    ProviderFailureCategory,
    ReferenceCatalog,
    SceneDataSensitivity,
    TechnicalStatus,
    VolumeLocalSceneInput,
    build_fixture_preview_run,
    build_production_run_plan,
    execute_pollinations_overflow,
    initialize_execution_state,
    pollinations_failover_decision,
    record_generation_attempt,
    summarize_call_budget,
)
from tella.visual_generation.providers import (
    PollinationsConfig,
    PollinationsError,
    PollinationsErrorCategory,
    PollinationsPrivacyMetadata,
    PollinationsPromptSource,
    PollinationsSceneImageProvider,
    ProviderKind,
)


class UncoveredResolver:
    def assess(self, request: AssetLibraryRequest) -> LocalCoverageAssessment:
        return LocalCoverageAssessment(
            status=LocalCoverageStatus.NOT_SATISFIED,
            reason=f"no exact local action for {request.action}",
        )


def _state(*, sensitivity=SceneDataSensitivity.PUBLIC_SAFE, job_id="pollinations-overflow"):
    fixture = build_fixture_preview_run(topic="public safe overflow", scene_count=7)
    request = LocalCompositionRequest(
        character_id="female_01",
        action="unsupported_public_action",
        emotion="calm",
        location="generic_room",
        time_of_day="night",
        composition_preset="bedroom_floor_sitting",
        seed=10101,
    )
    run = build_production_run_plan(
        job_id=job_id,
        story_plan=fixture.story_plan,
        scene_briefs=[scene.scene_brief for scene in fixture.scene_execution_plans],
        reference_catalog=ReferenceCatalog(),
        production_strategy=ProductionStrategyConfig.volume(),
        volume_local_inputs={
            "scene_01": VolumeLocalSceneInput(
                sensitivity=sensitivity,
                request=request,
            )
        },
        local_coverage_resolver=UncoveredResolver(),
    )
    state = initialize_execution_state(run)
    scene = state.scenes[0]
    draft = scene.execution_plan.draft
    prior = GenerationAttempt(
        scene_id="scene_01",
        tier=GenerationTier.DRAFT,
        provider=draft.provider,
        model=draft.model,
        seed=draft.seed,
        candidate_id="scene_01-cloudflare-candidate-01",
        planning_request_hash=draft.logical_visual_request_hash,
        logical_request_hash=draft.logical_visual_request_hash,
        reference_hashes=[],
        technical_status=TechnicalStatus.PROVIDER_QUOTA_BLOCKED,
        technical_failure_reason="controlled Cloudflare quota failure",
    )
    state = record_generation_attempt(state, prior)
    return state.model_copy(update={"external_calls": 1}, deep=True), prior


def _prompt():
    return PollinationsPromptSource(
        scene_meaning="A calm person creates distance from a phone",
        action=["placing a phone on a distant table"],
        mood=["quiet resolve"],
        setting=["generic warm room at night"],
        generic_character_description="an anonymous adult figure",
        style_description="soft hand-drawn editorial illustration",
        composition=["one readable action"],
    )


def _png():
    stream = io.BytesIO()
    Image.new("RGB", (576, 1024), "#45372f").save(stream, format="PNG")
    return stream.getvalue()


class Response:
    status_code = 200
    content = _png()
    text = ""


class Sender:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return Response()


def _provider(sender, token="sk_test_only"):
    return PollinationsSceneImageProvider(
        config=PollinationsConfig(enabled=True),
        api_key_resolver=lambda: token,
        request_sender=sender,
    )


@pytest.mark.asyncio
async def test_public_safe_overflow_creates_attempt_and_preserves_qc_boundary(tmp_path):
    state, prior = _state()
    sender = Sender()
    outcome = await execute_pollinations_overflow(
        state,
        scene_id="scene_01",
        out_root=tmp_path,
        authorization=CloudflareOverflowAuthorization(
            prior_candidate_id=prior.candidate_id,
            failure_category=ProviderFailureCategory.RATE_LIMITED,
        ),
        prompt_source=_prompt(),
        provider=_provider(sender),
    )

    scene = outcome.state.scenes[0]
    assert len(sender.calls) == 1
    assert outcome.provider_reaching_calls == outcome.external_calls == 1
    assert outcome.ai_calls == outcome.ai_retry_calls == 1
    assert outcome.state.external_calls == 2
    assert scene.status is ProductionSceneStatus.DRAFT_GENERATED
    assert len(scene.generation_attempts) == 2
    assert outcome.attempt.provider_kind is ProviderKind.POLLINATIONS
    assert outcome.attempt.consumes_ai_call is True
    assert outcome.attempt.consumes_ai_retry is True
    assert outcome.attempt.reference_hashes == []
    assert scene.accepted_candidate is None
    budget = summarize_call_budget(outcome.state)
    assert budget.completed_calls == 2
    assert budget.retry_calls == 1
    assert outcome.paths.candidate_base_path.with_suffix(".png").is_file()
    assert outcome.paths.runtime_state_path.is_file()


@pytest.mark.asyncio
async def test_persisted_pollinations_metadata_contains_no_token_or_private_payload(tmp_path):
    secret = "sk_sensitive_test_token"
    state, prior = _state(job_id="sanitized-persistence")
    outcome = await execute_pollinations_overflow(
        state,
        scene_id="scene_01",
        out_root=tmp_path,
        authorization=CloudflareOverflowAuthorization(
            prior_candidate_id=prior.candidate_id,
            failure_category=ProviderFailureCategory.PROVIDER_UNAVAILABLE,
        ),
        prompt_source=_prompt(),
        provider=_provider(Sender(), token=secret),
    )
    persisted = outcome.paths.candidate_metadata_path.read_text(encoding="utf-8")
    runtime = outcome.paths.runtime_state_path.read_text(encoding="utf-8")
    combined = persisted + runtime
    assert secret not in combined
    assert "Authorization" not in combined
    assert r"D:\private" not in combined
    assert "reference_hashes" in persisted
    assert json.loads(persisted)["reference_hashes"] == []


@pytest.mark.asyncio
async def test_private_scene_cannot_invoke_pollinations_overflow(tmp_path):
    state, prior = _state(sensitivity=SceneDataSensitivity.PRIVATE, job_id="private-block")
    sender = Sender()
    with pytest.raises(PermissionError, match="PUBLIC_SAFE"):
        await execute_pollinations_overflow(
            state,
            scene_id="scene_01",
            out_root=tmp_path,
            authorization=CloudflareOverflowAuthorization(
                prior_candidate_id=prior.candidate_id,
                failure_category=ProviderFailureCategory.RATE_LIMITED,
            ),
            prompt_source=_prompt(),
            provider=_provider(sender),
        )
    assert sender.calls == []


@pytest.mark.asyncio
async def test_private_attachment_is_blocked_before_transport(tmp_path):
    state, prior = _state(job_id="attachment-block")
    sender = Sender()
    with pytest.raises(PollinationsError) as raised:
        await execute_pollinations_overflow(
            state,
            scene_id="scene_01",
            out_root=tmp_path,
            authorization=CloudflareOverflowAuthorization(
                prior_candidate_id=prior.candidate_id,
                failure_category=ProviderFailureCategory.TIMEOUT,
            ),
            prompt_source=_prompt(),
            privacy=PollinationsPrivacyMetadata(private_reference_present=True),
            provider=_provider(sender),
        )
    assert raised.value.category is PollinationsErrorCategory.UNSAFE_REQUEST_BLOCKED_LOCALLY
    assert sender.calls == []


@pytest.mark.asyncio
async def test_pollinations_failure_is_one_attempt_and_remains_blocked(tmp_path):
    state, prior = _state(job_id="pollinations-timeout")
    sender = Sender(error=TimeoutError("controlled timeout"))
    outcome = await execute_pollinations_overflow(
        state,
        scene_id="scene_01",
        out_root=tmp_path,
        authorization=CloudflareOverflowAuthorization(
            prior_candidate_id=prior.candidate_id,
            failure_category=ProviderFailureCategory.PROVIDER_UNAVAILABLE,
        ),
        prompt_source=_prompt(),
        provider=_provider(sender),
    )
    assert len(sender.calls) == 1
    assert outcome.attempt.technical_status is TechnicalStatus.TECHNICAL_GENERATION_FAIL
    assert outcome.state.scenes[0].status is ProductionSceneStatus.BLOCKED
    assert outcome.state.scenes[0].accepted_candidate is None


@pytest.mark.parametrize(
    "category",
    [ProviderFailureCategory.SOFT_QC_FAIL, ProviderFailureCategory.QUALITY_INSUFFICIENT],
)
def test_quality_failures_never_authorize_pollinations(category):
    decision = pollinations_failover_decision(
        sensitivity=SceneDataSensitivity.PUBLIC_SAFE,
        failed_provider=ProviderKind.CLOUDFLARE_KLEIN_4B,
        failure=category,
    )
    assert decision.eligible is False
    assert decision.next_provider is None


def test_request_hash_is_sanitized_semantic_identity_only():
    state, _ = _state(job_id="hash-contract")
    prompt_digest = hashlib.sha256(_prompt().model_dump_json().encode()).hexdigest()
    serialized = state.run_plan.model_dump_json()
    assert prompt_digest not in serialized
    assert ProviderKind.POLLINATIONS in state.scenes[0].execution_plan.local_execution.route.eligible_providers
