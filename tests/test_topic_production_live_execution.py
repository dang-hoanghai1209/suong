"""Phase 3B.1 bounded draft executor tests; no test reaches a provider network."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

import tella.topic_production.live_execution as live_module
import tella.topic_production.reference_planning as reference_planning
from tella.topic_production import (
    FailureReason,
    ProductionSceneStatus,
    ResumeAction,
    TechnicalStatus,
    build_draft_execution_preview,
    build_infrastructure_canary_state,
    execute_draft_scene,
    load_runtime_state,
    plan_resume,
    select_draft_canary_scene,
)
from tella.visual_generation.models import CandidateMetadata, ProviderCapabilities
from tella.visual_generation.prompt_builder import instruction_hash, request_hash
from tella.visual_generation.providers.cloudflare_flux import (
    CloudflareFluxError,
    KLEIN_4B_MODEL,
)
from tella.visual_generation.references import sha256_file


TOPIC = "Ở một mình không có nghĩa là cô đơn."


def _reference_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "approved-references"
    root.mkdir()
    for index, definition in enumerate(reference_planning.APPROVED_REFERENCE_DEFINITIONS):
        Image.new("RGB", (90, 160), (40 + index * 10, 30, 30)).save(
            root / definition.filename
        )
    definitions = tuple(
        replace(definition, expected_sha256=sha256_file(root / definition.filename))
        for definition in reference_planning.APPROVED_REFERENCE_DEFINITIONS
    )
    monkeypatch.setattr(reference_planning, "APPROVED_REFERENCE_DEFINITIONS", definitions)
    return root


def _state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    return build_infrastructure_canary_state(
        topic=TOPIC,
        reference_root=_reference_root(tmp_path, monkeypatch),
    )


class FakeProvider:
    def __init__(self, mode: str = "success"):
        self.mode = mode
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
        if self.mode == "quota":
            raise CloudflareFluxError(
                stage="quota_exceeded",
                exception_class="CloudflareHTTPError",
                message="HTTP 429 quota exhausted",
                request_reached_provider=True,
                response_received=True,
                image_bytes_present=False,
            )
        if self.mode == "technical":
            raise CloudflareFluxError(
                stage="api_request",
                exception_class="TimeoutError",
                message="controlled timeout",
                request_reached_provider=True,
                response_received=False,
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

    async def edit_scene(self, source_path, request, output_path):
        raise AssertionError("edit/fallback must never be called")


def test_canary_selection_uses_first_safe_topic_production_scene(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state(tmp_path, monkeypatch)
    selection = select_draft_canary_scene(state)

    assert selection.scene_id == "scene_01"
    assert "non-relationship" in selection.reason
    assert state.run_plan.manifest.metadata["execution_purpose"] == "infrastructure_canary"


def test_generic_prompt_preserves_semantics_without_proof_quality_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state(tmp_path, monkeypatch)
    preview = build_draft_execution_preview(state, scene_id="scene_01")
    brief = state.scenes[0].execution_plan.scene_brief
    prompt = preview.request.instruction

    assert preview.prompt_profile == "topic_production_v1"
    for value in (
        brief.narrative_text,
        brief.meaning,
        brief.topic_intent,
        *brief.action,
        *brief.environment,
        *brief.objects,
        *brief.composition,
        *brief.negative_space_requirements,
    ):
        assert value in prompt
    assert all(item in preview.request.negative_instruction for item in brief.hard_negatives)
    assert "SCENE 1 QUALITY LOCK" not in prompt
    assert "four_scene_proof" not in prompt


def test_dry_run_persists_state_and_makes_zero_provider_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state(tmp_path, monkeypatch)
    provider = FakeProvider()

    import asyncio

    outcome = asyncio.run(
        execute_draft_scene(
            state,
            scene_id="scene_01",
            out_root=tmp_path / "out",
            dry_run=True,
            provider=provider,
        )
    )

    assert provider.calls == []
    assert outcome.external_calls == outcome.provider_invocations == 0
    assert outcome.paths.run_plan_path.is_file()
    assert outcome.paths.runtime_state_path.is_file()
    assert outcome.paths.manifest_path.is_file()


@pytest.mark.asyncio
async def test_live_authorization_required_before_provider_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state(tmp_path, monkeypatch)
    provider = FakeProvider()

    with pytest.raises(PermissionError, match="live authorization"):
        await execute_draft_scene(
            state,
            scene_id="scene_01",
            out_root=tmp_path / "out",
            dry_run=False,
            live_authorized=False,
            provider=provider,
        )
    assert provider.calls == []


@pytest.mark.asyncio
async def test_success_records_one_attempt_only_and_never_qc_or_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state(tmp_path, monkeypatch)
    provider = FakeProvider()
    outcome = await execute_draft_scene(
        state,
        scene_id="scene_01",
        out_root=tmp_path / "out",
        dry_run=False,
        live_authorized=True,
        provider=provider,
    )
    scene = outcome.state.scenes[0]

    assert len(provider.calls) == outcome.provider_invocations == 1
    assert outcome.external_calls == 0
    assert outcome.attempt.technical_status is TechnicalStatus.SUCCEEDED
    assert scene.status is ProductionSceneStatus.DRAFT_GENERATED
    assert len(scene.generation_attempts) == 1
    assert scene.qc_records == []
    assert scene.promotions == []
    assert scene.accepted_candidate is None
    assert outcome.provider_metadata.provider_request_hash == "d" * 64
    assert outcome.attempt.logical_request_hash == request_hash(provider.calls[0])
    assert outcome.attempt.planning_request_hash == outcome.preview.planning_request_hash


@pytest.mark.asyncio
async def test_existing_cloudflare_provider_factory_is_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state(tmp_path, monkeypatch)
    fake = FakeProvider()
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return fake

    monkeypatch.setattr(live_module, "CloudflareFluxSceneImageProvider", factory)
    outcome = await execute_draft_scene(
        state,
        scene_id="scene_01",
        out_root=tmp_path / "out",
        dry_run=False,
        live_authorized=True,
    )

    assert len(fake.calls) == 1
    assert captured == {
        "model": KLEIN_4B_MODEL,
        "width": 576,
        "height": 1024,
        "steps": 4,
        "timeout_seconds": 120.0,
        "tier": "draft",
        "intended_usage_class": "draft",
    }
    assert outcome.provider_invocations == 1


def test_required_reference_is_revalidated_before_provider_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state(tmp_path, monkeypatch)
    reference = Path(state.scenes[0].execution_plan.draft.references[0].path)
    reference.write_bytes(b"changed approved bytes")

    with pytest.raises(ValueError, match="missing or changed"):
        build_draft_execution_preview(state, scene_id="scene_01")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "technical_status", "failure_reason"),
    [
        ("quota", TechnicalStatus.PROVIDER_QUOTA_BLOCKED, FailureReason.PROVIDER_QUOTA_BLOCKED),
        (
            "technical",
            TechnicalStatus.TECHNICAL_GENERATION_FAIL,
            FailureReason.TECHNICAL_GENERATION_FAIL,
        ),
    ],
)
async def test_provider_failures_are_typed_persisted_and_never_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    technical_status: TechnicalStatus,
    failure_reason: FailureReason,
) -> None:
    state = _state(tmp_path, monkeypatch)
    provider = FakeProvider(mode)
    outcome = await execute_draft_scene(
        state,
        scene_id="scene_01",
        out_root=tmp_path / "out",
        dry_run=False,
        live_authorized=True,
        provider=provider,
    )

    assert len(provider.calls) == 1
    assert outcome.attempt.technical_status is technical_status
    assert outcome.state.scenes[0].status is ProductionSceneStatus.BLOCKED
    assert failure_reason in outcome.state.scenes[0].block_reasons
    assert load_runtime_state(outcome.paths.runtime_state_path) == outcome.state
    manifest = json.loads(outcome.paths.manifest_path.read_text(encoding="utf-8"))
    assert manifest["call_budget"]["retry_calls"] == 0
    assert manifest["call_budget"]["fallback_calls"] == 0


@pytest.mark.asyncio
async def test_persisted_draft_generated_resume_waits_for_qc_and_does_not_regenerate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeProvider()
    outcome = await execute_draft_scene(
        _state(tmp_path, monkeypatch),
        scene_id="scene_01",
        out_root=tmp_path / "out",
        dry_run=False,
        live_authorized=True,
        provider=provider,
    )
    restored = load_runtime_state(outcome.paths.runtime_state_path)
    action = {item.scene_id: item.action for item in plan_resume(restored).scenes}

    assert action["scene_01"] is ResumeAction.AWAIT_DRAFT_QC
    with pytest.raises(ValueError, match="requires DRAFT_PENDING"):
        await execute_draft_scene(
            restored,
            scene_id="scene_01",
            out_root=tmp_path / "out",
            dry_run=False,
            live_authorized=True,
            provider=provider,
        )
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_success_artifact_and_atomic_manifests_are_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome = await execute_draft_scene(
        _state(tmp_path, monkeypatch),
        scene_id="scene_01",
        out_root=tmp_path / "out",
        dry_run=False,
        live_authorized=True,
        provider=FakeProvider(),
    )
    artifact = Path(outcome.attempt.candidate_path)

    assert artifact.is_file()
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == outcome.attempt.artifact_sha256
    assert Image.open(artifact).size == (576, 1024)
    assert outcome.paths.candidate_metadata_path.is_file()
    assert not list(outcome.paths.job_dir.rglob("*.tmp"))
    manifest = json.loads(outcome.paths.manifest_path.read_text(encoding="utf-8"))
    assert manifest["readiness"]["ready"] is False
    assert manifest["external_calls"] == 0
