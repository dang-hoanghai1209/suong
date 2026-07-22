"""First-class, zero-AI execution for Volume scenes covered by local assets."""
from __future__ import annotations

import os
import tempfile
from enum import StrEnum
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Literal

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from tella.asset_library.semantic_resolver import (
    AssetLibraryResolutionError,
    compose_asset_library_scene,
)
from tella.visual_generation.providers.kinds import ProviderKind
from tella.visual_generation.references import sha256_file

from .live_execution_models import ProductionJobPaths
from .local_coverage import (
    LocalSceneCoverageResolver,
    SemanticAssetCoverageResolver,
    to_asset_library_request,
)
from .models import GenerationTier, ProductionSceneStatus
from .persistence import persist_execution_snapshot, production_job_paths
from .runtime import record_local_generation_attempt
from .runtime_models import ExecutionRunState, GenerationAttempt, TechnicalStatus
from .strategy import LocalCoverageStatus, ProductionStrategy, SceneDataSensitivity

LOCAL_COMPOSITOR_MODEL = "semantic_asset_compositor_v2"


class LocalExecutionStatus(StrEnum):
    GENERATED = "generated"
    ALREADY_GENERATED = "already_generated"
    EXTERNAL_PROVIDER_NEEDED = "external_provider_needed"
    NOT_COVERED = "not_covered"


class LocalCandidateMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    scene_id: str = Field(pattern=r"^scene_[0-9]{2}$")
    candidate_id: str
    provider: Literal[ProviderKind.LOCAL_COMPOSITOR] = ProviderKind.LOCAL_COMPOSITOR
    model: Literal["semantic_asset_compositor_v2"] = LOCAL_COMPOSITOR_MODEL
    logical_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_path: Path
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    mime_type: Literal["image/png"] = "image/png"
    selected_semantic_id: str
    composition_metadata: dict[str, Any]
    provider_reaching_calls: Literal[0] = 0
    ai_calls: Literal[0] = 0
    ai_retry_calls: Literal[0] = 0


class LocalExecutionOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: LocalExecutionStatus
    state: ExecutionRunState
    paths: ProductionJobPaths
    reason: str
    attempt: GenerationAttempt | None = None
    candidate_metadata: LocalCandidateMetadata | None = None
    rendered: bool = False
    provider_reaching_calls: Literal[0] = 0
    external_calls: Literal[0] = 0
    ai_calls: Literal[0] = 0
    ai_retry_calls: Literal[0] = 0


class LocalArtifactValidationError(RuntimeError):
    pass


LocalComposer = Callable[[Any, Path, str | Path | None, str | Path | None], dict[str, Any]]


def local_production_job_paths(
    out_root: Path | str, *, job_id: str, scene_id: str
) -> ProductionJobPaths:
    base = production_job_paths(out_root, job_id=job_id, scene_id=scene_id)
    local_dir = base.job_dir / "scenes" / scene_id / "draft" / "local_compositor"
    return base.model_copy(
        update={
            "candidate_base_path": local_dir / "candidate_01.png",
            "candidate_metadata_path": local_dir / "metadata.json",
        }
    )


def _validate_local_artifact(
    attempt: GenerationAttempt,
    *,
    expected_path: Path,
    expected_width: int,
    expected_height: int,
) -> tuple[int, int, str]:
    if attempt.candidate_path is None or Path(attempt.candidate_path).resolve() != expected_path:
        raise LocalArtifactValidationError("persisted local attempt points to an unexpected artifact")
    if not expected_path.is_file():
        raise LocalArtifactValidationError("persisted local artifact is missing")
    if sha256_file(expected_path) != attempt.artifact_sha256:
        raise LocalArtifactValidationError("persisted local artifact hash does not match state")
    try:
        with Image.open(expected_path) as image:
            image.load()
            width, height = image.size
            image_format = (image.format or "").upper()
    except (OSError, UnidentifiedImageError) as exc:
        raise LocalArtifactValidationError("persisted local artifact is not decodable") from exc
    if image_format != "PNG" or (width, height) != (expected_width, expected_height):
        raise LocalArtifactValidationError("persisted local artifact format or dimensions are invalid")
    return width, height, sha256_file(expected_path)


def _not_covered_outcome(
    state: ExecutionRunState,
    paths: ProductionJobPaths,
    *,
    scene_id: str,
    sensitivity: SceneDataSensitivity,
    reason: str,
) -> LocalExecutionOutcome:
    status = (
        LocalExecutionStatus.NOT_COVERED
        if sensitivity is SceneDataSensitivity.LOCAL_ONLY
        else LocalExecutionStatus.EXTERNAL_PROVIDER_NEEDED
    )
    persist_execution_snapshot(
        state,
        paths,
        execution_purpose="volume_local_first",
        selected_scene_id=scene_id,
        candidate_metadata={
            "status": status.value,
            "scene_id": scene_id,
            "provider": ProviderKind.LOCAL_COMPOSITOR.value,
            "reason": reason,
            "provider_reaching_calls": 0,
            "ai_calls": 0,
            "ai_retry_calls": 0,
        },
    )
    return LocalExecutionOutcome(status=status, state=state, paths=paths, reason=reason)


def execute_local_scene(
    state: ExecutionRunState,
    *,
    scene_id: str,
    out_root: Path | str,
    coverage_resolver: LocalSceneCoverageResolver | None = None,
    asset_library_root: str | Path | None = None,
    semantics_path: str | Path | None = None,
    composer: LocalComposer = compose_asset_library_scene,
) -> LocalExecutionOutcome:
    """Render one authorized local candidate and record it in immutable runtime state."""

    if state.run_plan.production_strategy.strategy is not ProductionStrategy.VOLUME:
        raise ValueError("local-first execution requires Volume strategy")
    scene = next((item for item in state.scenes if item.scene_id == scene_id), None)
    if scene is None:
        raise ValueError(f"unknown scene ID: {scene_id}")
    local_plan = scene.execution_plan.local_execution
    if local_plan is None:
        raise ValueError("scene has no local execution plan")
    paths = local_production_job_paths(
        out_root, job_id=state.run_plan.job_id, scene_id=scene_id
    )
    expected_path = paths.candidate_base_path.resolve()
    prior = [
        item
        for item in scene.generation_attempts
        if item.provider_kind is ProviderKind.LOCAL_COMPOSITOR
    ]
    if prior:
        if len(prior) != 1 or prior[0].technical_status is not TechnicalStatus.SUCCEEDED:
            raise LocalArtifactValidationError("persisted local attempt state is inconsistent")
        width, height, artifact_sha = _validate_local_artifact(
            prior[0],
            expected_path=expected_path,
            expected_width=local_plan.expected_width,
            expected_height=local_plan.expected_height,
        )
        metadata = LocalCandidateMetadata(
            scene_id=scene_id,
            candidate_id=prior[0].candidate_id,
            logical_request_hash=local_plan.logical_request_hash,
            artifact_path=expected_path,
            artifact_sha256=artifact_sha,
            width=width,
            height=height,
            selected_semantic_id=str(prior[0].metadata["selected_semantic_id"]),
            composition_metadata=dict(prior[0].metadata["composition"]),
        )
        return LocalExecutionOutcome(
            status=LocalExecutionStatus.ALREADY_GENERATED,
            state=state,
            paths=paths,
            reason="matching persisted local candidate is valid; render skipped",
            attempt=prior[0],
            candidate_metadata=metadata,
        )
    if scene.status is not ProductionSceneStatus.DRAFT_PENDING:
        raise ValueError(f"local executor requires DRAFT_PENDING, got {scene.status.value}")
    if local_plan.route.selected_provider is not ProviderKind.LOCAL_COMPOSITOR:
        return _not_covered_outcome(
            state,
            paths,
            scene_id=scene_id,
            sensitivity=local_plan.sensitivity,
            reason=local_plan.coverage.reason,
        )
    resolver = coverage_resolver or SemanticAssetCoverageResolver(
        asset_library_root=asset_library_root,
        semantics_path=semantics_path,
    )
    current_coverage = resolver.assess(to_asset_library_request(local_plan.request))
    if (
        current_coverage.status is not LocalCoverageStatus.SATISFIED
        or current_coverage.selected_semantic_id != local_plan.coverage.selected_semantic_id
    ):
        return _not_covered_outcome(
            state,
            paths,
            scene_id=scene_id,
            sensitivity=local_plan.sensitivity,
            reason=f"local coverage changed or is unsafe: {current_coverage.reason}",
        )
    if expected_path.exists():
        raise FileExistsError("local artifact target exists without matching persisted attempt")
    expected_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{expected_path.stem}.", suffix=".png", dir=expected_path.parent
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        scene_proxy = SimpleNamespace(asset_library_request=local_plan.request.model_dump())
        composition = composer(
            scene_proxy,
            temporary_path,
            asset_library_root,
            semantics_path,
        )
        temporary_attempt = GenerationAttempt(
            scene_id=scene_id,
            tier=GenerationTier.DRAFT,
            provider=ProviderKind.LOCAL_COMPOSITOR.value,
            provider_kind=ProviderKind.LOCAL_COMPOSITOR,
            model=LOCAL_COMPOSITOR_MODEL,
            seed=local_plan.request.seed,
            candidate_id=f"{scene_id}-local-candidate-01",
            candidate_path=str(temporary_path.resolve()),
            artifact_sha256=sha256_file(temporary_path),
            planning_request_hash=local_plan.logical_request_hash,
            logical_request_hash=local_plan.logical_request_hash,
            reference_hashes=[],
            technical_status=TechnicalStatus.SUCCEEDED,
            consumes_ai_call=False,
            consumes_ai_retry=False,
        )
        width, height, artifact_sha = _validate_local_artifact(
            temporary_attempt,
            expected_path=temporary_path.resolve(),
            expected_width=local_plan.expected_width,
            expected_height=local_plan.expected_height,
        )
        os.replace(temporary_path, expected_path)
    except (FileNotFoundError, LookupError, AssetLibraryResolutionError) as exc:
        temporary_path.unlink(missing_ok=True)
        return _not_covered_outcome(
            state,
            paths,
            scene_id=scene_id,
            sensitivity=local_plan.sensitivity,
            reason=f"local compositor dependencies are not covered: {exc}",
        )
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise

    composition = {**composition, "output": str(expected_path)}
    selected_semantic_id = str(
        composition.get("character", {}).get("selected_semantic_id")
        or current_coverage.selected_semantic_id
    )
    metadata = LocalCandidateMetadata(
        scene_id=scene_id,
        candidate_id=f"{scene_id}-local-candidate-01",
        logical_request_hash=local_plan.logical_request_hash,
        artifact_path=expected_path,
        artifact_sha256=artifact_sha,
        width=width,
        height=height,
        selected_semantic_id=selected_semantic_id,
        composition_metadata=composition,
    )
    attempt = GenerationAttempt(
        scene_id=scene_id,
        tier=GenerationTier.DRAFT,
        provider=ProviderKind.LOCAL_COMPOSITOR.value,
        provider_kind=ProviderKind.LOCAL_COMPOSITOR,
        model=LOCAL_COMPOSITOR_MODEL,
        seed=local_plan.request.seed,
        candidate_id=metadata.candidate_id,
        candidate_path=str(expected_path),
        artifact_sha256=artifact_sha,
        planning_request_hash=local_plan.logical_request_hash,
        logical_request_hash=local_plan.logical_request_hash,
        reference_hashes=[],
        technical_status=TechnicalStatus.SUCCEEDED,
        consumes_ai_call=False,
        consumes_ai_retry=False,
        metadata={
            "selected_semantic_id": selected_semantic_id,
            "coverage_reason": current_coverage.reason,
            "composition": composition,
            "provider_reaching_calls": 0,
            "ai_calls": 0,
            "ai_retry_calls": 0,
        },
    )
    updated = record_local_generation_attempt(state, attempt)
    persist_execution_snapshot(
        updated,
        paths,
        execution_purpose="volume_local_first",
        selected_scene_id=scene_id,
        candidate_metadata=metadata.model_dump(mode="json"),
    )
    return LocalExecutionOutcome(
        status=LocalExecutionStatus.GENERATED,
        state=updated,
        paths=paths,
        reason="local compositor produced a validated candidate",
        attempt=attempt,
        candidate_metadata=metadata,
        rendered=True,
    )


__all__ = [
    "LOCAL_COMPOSITOR_MODEL",
    "LocalArtifactValidationError",
    "LocalCandidateMetadata",
    "LocalExecutionOutcome",
    "LocalExecutionStatus",
    "execute_local_scene",
    "local_production_job_paths",
]
