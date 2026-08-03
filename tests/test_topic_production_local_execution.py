from __future__ import annotations

import json

import pytest
from PIL import Image

from tella.asset_library.semantic_resolver import AssetLibraryRequest, AssetLibraryResolutionError
from tella.topic_production import (
    LocalArtifactValidationError,
    LocalCompositionRequest,
    LocalCoverageAssessment,
    LocalCoverageStatus,
    LocalExecutionStatus,
    ProductionSceneStatus,
    ProductionStrategyConfig,
    ProviderAvailability,
    ReferenceCatalog,
    SceneDataSensitivity,
    SceneRoutingRequest,
    SemanticAssetCoverageResolver,
    VolumeLocalSceneInput,
    build_fixture_preview_run,
    build_production_run_plan,
    execute_local_scene,
    initialize_execution_state,
    load_runtime_state,
    local_production_job_paths,
    plan_resume,
    route_scene,
    summarize_call_budget,
)
from tella.visual_generation.providers import ProviderKind


class FixedCoverageResolver:
    def __init__(self, status=LocalCoverageStatus.SATISFIED, semantic_id="sit_hug_knees_sad"):
        self.status = status
        self.semantic_id = semantic_id
        self.requests: list[AssetLibraryRequest] = []

    def assess(self, request: AssetLibraryRequest) -> LocalCoverageAssessment:
        self.requests.append(request)
        return LocalCoverageAssessment(
            status=self.status,
            reason=f"controlled {self.status.value}",
            selected_semantic_id=self.semantic_id if self.status is LocalCoverageStatus.SATISFIED else None,
        )


def _local_request() -> LocalCompositionRequest:
    return LocalCompositionRequest(
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


def _state(resolver, *, sensitivity=SceneDataSensitivity.PRIVATE, job_id="local-volume"):
    fixture = build_fixture_preview_run(topic="local execution contract", scene_count=7)
    run = build_production_run_plan(
        job_id=job_id,
        story_plan=fixture.story_plan,
        scene_briefs=[scene.scene_brief for scene in fixture.scene_execution_plans],
        reference_catalog=ReferenceCatalog(),
        production_strategy=ProductionStrategyConfig.volume(),
        volume_local_inputs={
            "scene_01": VolumeLocalSceneInput(
                sensitivity=sensitivity,
                request=_local_request(),
            )
        },
        local_coverage_resolver=resolver,
    )
    return initialize_execution_state(run)


class CountingComposer:
    def __init__(self):
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
            "character": {
                "selected_semantic_id": "sit_hug_knees_sad",
                "selected_asset_id": "sit_hug_knees_backup",
                "processed_path": "fixture/characters/sit_hug_knees_backup.png",
                "selection_score": 150,
                "selection_reasons": ["exact_action +100", "exact_emotion +50"],
                "score_breakdown": {"total": 150},
                "quality_status": "approved",
                "fallback_reason": "",
            },
            "background": {"path": "fixture/backgrounds/bedroom_night_01.png"},
            "objects": [
                {"asset_id": "pillow", "processed_path": "fixture/objects/pillow.png"},
                {"asset_id": "phone_dark", "processed_path": "fixture/objects/phone.png"},
            ],
            "composition_preset": request["composition_preset"],
            "output": str(output_path),
        }


def test_local_covered_scene_executes_persists_and_consumes_zero_ai_calls(tmp_path):
    resolver = FixedCoverageResolver()
    composer = CountingComposer()
    state = _state(resolver)

    outcome = execute_local_scene(
        state,
        scene_id="scene_01",
        out_root=tmp_path,
        coverage_resolver=resolver,
        composer=composer,
    )

    assert outcome.status is LocalExecutionStatus.GENERATED
    assert composer.calls == 1
    assert outcome.paths.candidate_base_path.is_file()
    assert outcome.paths.runtime_state_path.is_file()
    assert outcome.attempt.provider_kind is ProviderKind.LOCAL_COMPOSITOR
    assert outcome.attempt.provider == ProviderKind.LOCAL_COMPOSITOR.value
    assert outcome.attempt.consumes_ai_call is False
    assert outcome.attempt.consumes_ai_retry is False
    assert outcome.external_calls == outcome.provider_reaching_calls == outcome.ai_calls == 0
    assert outcome.ai_retry_calls == 0
    assert outcome.state.external_calls == 0
    assert outcome.state.scenes[0].status is ProductionSceneStatus.DRAFT_GENERATED
    assert outcome.state.scenes[0].accepted_candidate is None
    budget = summarize_call_budget(outcome.state)
    assert budget.completed_calls == budget.retry_calls == 0
    assert plan_resume(outcome.state).scenes[0].action.value == "AWAIT_DRAFT_QC"

    restored = load_runtime_state(outcome.paths.runtime_state_path)
    assert restored == outcome.state
    metadata = json.loads(outcome.paths.candidate_metadata_path.read_text(encoding="utf-8"))
    assert metadata["provider"] == ProviderKind.LOCAL_COMPOSITOR.value
    assert metadata["selected_semantic_id"] == "sit_hug_knees_sad"
    assert metadata["composition_metadata"]["character"]["selected_asset_id"] == (
        "sit_hug_knees_backup"
    )


def test_resume_valid_candidate_does_not_render_or_duplicate_attempt(tmp_path):
    resolver = FixedCoverageResolver()
    composer = CountingComposer()
    first = execute_local_scene(
        _state(resolver),
        scene_id="scene_01",
        out_root=tmp_path,
        coverage_resolver=resolver,
        composer=composer,
    )
    resumed = execute_local_scene(
        load_runtime_state(first.paths.runtime_state_path),
        scene_id="scene_01",
        out_root=tmp_path,
        coverage_resolver=resolver,
        composer=composer,
    )

    assert resumed.status is LocalExecutionStatus.ALREADY_GENERATED
    assert resumed.rendered is False
    assert composer.calls == 1
    assert len(resumed.state.scenes[0].generation_attempts) == 1


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_missing_or_corrupt_persisted_local_artifact_fails_closed(tmp_path, damage):
    resolver = FixedCoverageResolver()
    composer = CountingComposer()
    first = execute_local_scene(
        _state(resolver),
        scene_id="scene_01",
        out_root=tmp_path,
        coverage_resolver=resolver,
        composer=composer,
    )
    artifact = first.paths.candidate_base_path
    if damage == "missing":
        artifact.unlink()
    else:
        artifact.write_bytes(b"not an image")

    with pytest.raises(LocalArtifactValidationError):
        execute_local_scene(
            first.state,
            scene_id="scene_01",
            out_root=tmp_path,
            coverage_resolver=resolver,
            composer=composer,
        )
    assert first.state.scenes[0].accepted_candidate is None
    assert composer.calls == 1


@pytest.mark.parametrize(
    "status",
    [LocalCoverageStatus.NOT_SATISFIED, LocalCoverageStatus.AMBIGUOUS_UNSAFE],
)
def test_uncovered_or_ambiguous_local_only_scene_fails_closed_without_artifact(
    tmp_path, status
):
    resolver = FixedCoverageResolver(status=status)
    composer = CountingComposer()
    outcome = execute_local_scene(
        _state(resolver, sensitivity=SceneDataSensitivity.LOCAL_ONLY),
        scene_id="scene_01",
        out_root=tmp_path,
        coverage_resolver=resolver,
        composer=composer,
    )

    assert outcome.status is LocalExecutionStatus.NOT_COVERED
    assert outcome.attempt is None
    assert composer.calls == 0
    assert not outcome.paths.candidate_base_path.exists()
    assert outcome.state.scenes[0].status is ProductionSceneStatus.DRAFT_PENDING


def test_uncovered_private_scene_reports_external_needed_without_fake_success(tmp_path):
    resolver = FixedCoverageResolver(status=LocalCoverageStatus.NOT_SATISFIED)
    outcome = execute_local_scene(
        _state(resolver),
        scene_id="scene_01",
        out_root=tmp_path,
        coverage_resolver=resolver,
        composer=CountingComposer(),
    )

    assert outcome.status is LocalExecutionStatus.EXTERNAL_PROVIDER_NEEDED
    assert outcome.attempt is None
    assert not outcome.paths.candidate_base_path.exists()
    assert outcome.external_calls == 0


def test_required_asset_disappearing_before_composition_fails_closed(tmp_path):
    resolver = FixedCoverageResolver()

    def missing_asset(*_args):
        raise AssetLibraryResolutionError(
            "required_object_missing",
            "required phone_dark fixture disappeared",
        )

    outcome = execute_local_scene(
        _state(resolver),
        scene_id="scene_01",
        out_root=tmp_path,
        coverage_resolver=resolver,
        composer=missing_asset,
    )

    assert outcome.status is LocalExecutionStatus.EXTERNAL_PROVIDER_NEEDED
    assert outcome.attempt is None
    assert not outcome.paths.candidate_base_path.exists()


def test_unrelated_existing_artifact_is_never_overwritten(tmp_path):
    resolver = FixedCoverageResolver()
    state = _state(resolver)
    paths = local_production_job_paths(
        tmp_path, job_id=state.run_plan.job_id, scene_id="scene_01"
    )
    paths.candidate_base_path.parent.mkdir(parents=True)
    paths.candidate_base_path.write_bytes(b"unrelated evidence")

    with pytest.raises(FileExistsError, match="without matching persisted attempt"):
        execute_local_scene(
            state,
            scene_id="scene_01",
            out_root=tmp_path,
            coverage_resolver=resolver,
            composer=CountingComposer(),
        )
    assert paths.candidate_base_path.read_bytes() == b"unrelated evidence"


def test_missing_asset_root_is_not_covered_without_creating_output(tmp_path):
    resolver = SemanticAssetCoverageResolver(
        asset_library_root=tmp_path / "missing-assets",
        semantics_path=tmp_path / "missing-semantics.json",
    )
    assessment = resolver.assess(
        AssetLibraryRequest(**_local_request().model_dump())
    )
    assert assessment.status is LocalCoverageStatus.NOT_SATISFIED


def test_identical_inputs_produce_deterministic_plan_and_composition_metadata(tmp_path):
    resolver_one = FixedCoverageResolver()
    resolver_two = FixedCoverageResolver()
    composer_one = CountingComposer()
    composer_two = CountingComposer()
    first = execute_local_scene(
        _state(resolver_one, job_id="deterministic-one"),
        scene_id="scene_01",
        out_root=tmp_path,
        coverage_resolver=resolver_one,
        composer=composer_one,
    )
    second = execute_local_scene(
        _state(resolver_two, job_id="deterministic-two"),
        scene_id="scene_01",
        out_root=tmp_path,
        coverage_resolver=resolver_two,
        composer=composer_two,
    )

    assert first.attempt.logical_request_hash == second.attempt.logical_request_hash
    assert first.attempt.artifact_sha256 == second.attempt.artifact_sha256
    first_metadata = dict(first.candidate_metadata.composition_metadata)
    second_metadata = dict(second.candidate_metadata.composition_metadata)
    first_metadata.pop("output")
    second_metadata.pop("output")
    assert first_metadata == second_metadata


def test_quality_mode_cannot_enter_local_volume_executor(tmp_path):
    quality = initialize_execution_state(
        build_fixture_preview_run(topic="quality remains unchanged", scene_count=7)
    )
    with pytest.raises(ValueError, match="requires Volume strategy"):
        execute_local_scene(quality, scene_id="scene_01", out_root=tmp_path)


def test_private_and_public_routing_invariants_remain_unchanged():
    uncovered = LocalCoverageAssessment(
        status=LocalCoverageStatus.NOT_SATISFIED,
        reason="controlled",
    )
    private = route_scene(
        SceneRoutingRequest(
            sensitivity=SceneDataSensitivity.PRIVATE,
            local_coverage=uncovered,
        ),
        ProviderAvailability(cloudflare_klein_4b=False, pollinations=True),
    )
    public = route_scene(
        SceneRoutingRequest(
            sensitivity=SceneDataSensitivity.PUBLIC_SAFE,
            local_coverage=uncovered,
        ),
        ProviderAvailability(cloudflare_klein_4b=False, pollinations=True),
    )
    assert private.blocked is True
    assert ProviderKind.POLLINATIONS not in private.eligible_providers
    assert public.selected_provider is ProviderKind.POLLINATIONS
