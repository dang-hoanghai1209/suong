"""One-shot adapter from a topic-production scene plan to the validated provider."""
from __future__ import annotations

import time
from pathlib import Path

from PIL import Image

from tella.visual_generation.models import CandidateMetadata
from tella.visual_generation.prompt_builder import request_hash
from tella.visual_generation.providers.base import (
    SceneImageProvider,
    validate_provider_capabilities,
)
from tella.visual_generation.providers.cloudflare_flux import (
    CloudflareFluxError,
    CloudflareFluxSceneImageProvider,
)
from tella.visual_generation.references import sha256_file
from tella.visual_generation.tiers import VisualQualityTier, resolve_visual_tier

from .execution import build_fixture_preview_run
from .execution_models import ReferenceDecisionStatus
from .live_execution_models import (
    CanarySelection,
    DraftExecutionOutcome,
    DraftExecutionPreview,
)
from .models import (
    AcceptancePriority,
    GenerationTier,
    ProductionSceneStatus,
    SceneComplexity,
    SceneType,
)
from .persistence import persist_production_job, production_job_paths
from .production_prompt import PROMPT_PROFILE, build_topic_production_request
from .runtime import initialize_execution_state, record_generation_attempt
from .runtime_models import ExecutionRunState, GenerationAttempt, TechnicalStatus


def build_infrastructure_canary_state(
    *,
    topic: str,
    reference_root: Path | str,
    job_id: str = "topic_production_canary_01",
) -> ExecutionRunState:
    run_plan = build_fixture_preview_run(
        topic=topic,
        scene_count=8,
        target_duration_seconds=35.0,
        job_id=job_id,
        reference_root=reference_root,
    )
    manifest = run_plan.manifest.model_copy(
        update={
            "metadata": {
                **run_plan.manifest.metadata,
                "execution_purpose": "infrastructure_canary",
            }
        },
        deep=True,
    )
    return initialize_execution_state(run_plan.model_copy(update={"manifest": manifest}, deep=True))


def select_draft_canary_scene(state: ExecutionRunState) -> CanarySelection:
    blocked = {
        ReferenceDecisionStatus.REFERENCE_BLOCKED_REQUIRED_IDENTITY,
        ReferenceDecisionStatus.REFERENCE_BLOCKED_REQUIRED_STYLE,
    }
    for scene in state.scenes:
        plan = scene.execution_plan
        if (
            scene.status is ProductionSceneStatus.DRAFT_PENDING
            and plan.scene_brief.scene_type is not SceneType.RELATIONSHIP_VIGNETTE
            and plan.scene_brief.complexity is not SceneComplexity.COMPLEX
            and plan.acceptance_policy.priority is AcceptancePriority.STANDARD
            and plan.draft.references
            and not any(item.status in blocked for item in plan.draft.reference_decisions)
        ):
            return CanarySelection(
                scene_id=scene.scene_id,
                reason=(
                    "first ordered non-relationship, standard-priority, non-complex "
                    "recurring-character scene with validated required references"
                ),
            )
    raise ValueError("no safe draft canary scene is available")


def build_draft_execution_preview(
    state: ExecutionRunState, *, scene_id: str
) -> DraftExecutionPreview:
    scene = next((item for item in state.scenes if item.scene_id == scene_id), None)
    if scene is None:
        raise ValueError(f"unknown scene ID: {scene_id}")
    if scene.status is not ProductionSceneStatus.DRAFT_PENDING:
        raise ValueError(f"draft executor requires DRAFT_PENDING, got {scene.status.value}")
    plan = scene.execution_plan
    tier = resolve_visual_tier(VisualQualityTier.DRAFT)
    if (plan.draft.provider, plan.draft.model, plan.draft.steps, plan.draft.timeout_seconds) != (
        tier.provider,
        tier.model,
        tier.steps,
        tier.timeout_seconds,
    ):
        raise ValueError("scene draft request does not match validated draft tier")
    if plan.draft.accepted_scene_chaining:
        raise ValueError("accepted-scene chaining must remain disabled")
    blocking = [
        item
        for item in plan.draft.reference_decisions
        if item.status
        in {
            ReferenceDecisionStatus.REFERENCE_BLOCKED_REQUIRED_IDENTITY,
            ReferenceDecisionStatus.REFERENCE_BLOCKED_REQUIRED_STYLE,
        }
    ]
    if blocking:
        raise ValueError("required reference validation blocks provider execution")
    for reference in plan.draft.references:
        path = Path(reference.path)
        if not path.is_file() or sha256_file(path) != reference.sha256:
            raise ValueError(f"approved reference is missing or changed: {reference.reference_id}")
    request = build_topic_production_request(plan)
    logical_hash = request_hash(request)
    return DraftExecutionPreview(
        job_id=state.run_plan.job_id,
        topic=state.run_plan.topic,
        planner_mode=state.run_plan.story_plan.planner_metadata.planner_mode.value,
        production_eligible=state.run_plan.story_plan.planner_metadata.production_eligible,
        execution_purpose="infrastructure_canary",
        scene_id=scene_id,
        scene_type=plan.scene_brief.scene_type.value,
        meaning=plan.scene_brief.meaning,
        duration_seconds=plan.timing.duration_seconds,
        prompt_profile=PROMPT_PROFILE,
        request=request,
        planning_request_hash=plan.draft.logical_visual_request_hash,
        logical_request_hash=logical_hash,
    )


def _validate_success(
    metadata: CandidateMetadata,
    preview: DraftExecutionPreview,
    *,
    expected_provider: str,
    expected_model: str,
) -> tuple[str, int, int, str]:
    request = preview.request
    if (metadata.provider, metadata.model) != (expected_provider, expected_model):
        raise ValueError("provider metadata identity mismatch")
    if metadata.seed != request.seed:
        raise ValueError("provider metadata seed mismatch")
    if metadata.logical_request_hash != preview.logical_request_hash:
        raise ValueError("provider logical request hash mismatch")
    if not metadata.provider_request_hash:
        raise ValueError("provider request hash is missing")
    if metadata.reference_hashes != [item.sha256 for item in request.references]:
        raise ValueError("provider reference hash traceability mismatch")
    path = metadata.output_path.resolve()
    if not path.is_file():
        raise ValueError("provider artifact path is missing")
    artifact_sha = sha256_file(path)
    with Image.open(path) as image:
        image.load()
        width, height = image.size
        image_format = (image.format or "").upper()
    mime_by_format = {"PNG": "image/png", "JPEG": "image/jpeg"}
    mime = mime_by_format.get(image_format)
    if mime is None or metadata.mime_type != mime:
        raise ValueError("provider artifact MIME/decode validation failed")
    if (width, height) != (request.width, request.height):
        raise ValueError("provider artifact dimensions do not match request")
    if (metadata.actual_width, metadata.actual_height) != (width, height):
        raise ValueError("provider dimension metadata mismatch")
    return str(path), width, height, artifact_sha


async def execute_draft_scene(
    state: ExecutionRunState,
    *,
    scene_id: str,
    out_root: Path | str,
    dry_run: bool = True,
    live_authorized: bool = False,
    provider: SceneImageProvider | None = None,
) -> DraftExecutionOutcome:
    preview = build_draft_execution_preview(state, scene_id=scene_id)
    paths = production_job_paths(out_root, job_id=state.run_plan.job_id, scene_id=scene_id)
    persist_production_job(state, paths, preview=preview)
    if dry_run:
        return DraftExecutionOutcome(
            preview=preview,
            state=state,
            paths=paths,
            dry_run=True,
        )
    if not live_authorized:
        raise PermissionError("explicit live authorization is required")
    injected = provider is not None
    runtime_scene = state.scenes[[item.scene_id for item in state.scenes].index(scene_id)]
    expected_provider = runtime_scene.execution_plan.draft.provider
    expected_model = runtime_scene.execution_plan.draft.model
    selected_provider = provider or CloudflareFluxSceneImageProvider(
        model=expected_model,
        width=preview.request.width,
        height=preview.request.height,
        steps=4,
        timeout_seconds=120.0,
        tier="draft",
        intended_usage_class="draft",
    )
    capabilities = selected_provider.capabilities()
    validate_provider_capabilities(capabilities)
    if (capabilities.provider_id, capabilities.model) != (expected_provider, expected_model):
        raise ValueError("provider does not match authorized draft plan")
    if not selected_provider.credentials_present():
        raise PermissionError("live provider credentials are missing")
    started = time.monotonic()
    provider_metadata: CandidateMetadata | None = None
    provider_returned = False
    provider_invocations = 1
    external_calls = 0
    candidate_id = f"{scene_id}-draft-candidate-01"
    try:
        provider_metadata = await selected_provider.generate_scene(
            preview.request, paths.candidate_base_path
        )
        provider_returned = True
        artifact_path, width, height, artifact_sha = _validate_success(
            provider_metadata,
            preview,
            expected_provider=expected_provider,
            expected_model=expected_model,
        )
        attempt = GenerationAttempt(
            scene_id=scene_id,
            tier=GenerationTier.DRAFT,
            provider=provider_metadata.provider,
            model=provider_metadata.model,
            seed=provider_metadata.seed or preview.request.seed or 0,
            candidate_id=candidate_id,
            candidate_path=artifact_path,
            artifact_sha256=artifact_sha,
            planning_request_hash=preview.planning_request_hash,
            logical_request_hash=preview.logical_request_hash,
            provider_request_hash=provider_metadata.provider_request_hash,
            reference_hashes=provider_metadata.reference_hashes,
            technical_status=TechnicalStatus.SUCCEEDED,
            metadata={
                "mime_type": provider_metadata.mime_type,
                "actual_width": width,
                "actual_height": height,
                "prompt_profile": PROMPT_PROFILE,
            },
        )
        external_calls = 0 if injected else 1
    except Exception as exc:
        quota = isinstance(exc, CloudflareFluxError) and exc.stage == "quota_exceeded"
        reached = provider_returned or (
            isinstance(exc, CloudflareFluxError) and exc.request_reached_provider
        )
        external_calls = 0 if injected else int(reached)
        attempt = GenerationAttempt(
            scene_id=scene_id,
            tier=GenerationTier.DRAFT,
            provider=capabilities.provider_id,
            model=capabilities.model,
            seed=preview.request.seed or 0,
            candidate_id=candidate_id,
            planning_request_hash=preview.planning_request_hash,
            logical_request_hash=preview.logical_request_hash,
            reference_hashes=[item.sha256 for item in preview.request.references],
            technical_status=(
                TechnicalStatus.PROVIDER_QUOTA_BLOCKED
                if quota
                else TechnicalStatus.TECHNICAL_GENERATION_FAIL
            ),
            technical_failure_reason=str(exc),
            metadata={"prompt_profile": PROMPT_PROFILE},
        )
    latency_ms = round((time.monotonic() - started) * 1000)
    if provider_metadata is not None:
        provider_metadata = provider_metadata.model_copy(update={"latency_ms": latency_ms})
    attempt = attempt.model_copy(
        update={"metadata": {**attempt.metadata, "provider_latency_ms": latency_ms}},
        deep=True,
    )
    updated = record_generation_attempt(state, attempt)
    updated = updated.model_copy(
        update={"external_calls": state.external_calls + external_calls}, deep=True
    )
    persist_production_job(
        updated,
        paths,
        preview=preview,
        provider_metadata=provider_metadata,
    )
    return DraftExecutionOutcome(
        preview=preview,
        state=updated,
        attempt=attempt,
        provider_metadata=provider_metadata,
        paths=paths,
        dry_run=False,
        provider_invocations=provider_invocations,
        external_calls=external_calls,
        provider_latency_ms=latency_ms,
    )
