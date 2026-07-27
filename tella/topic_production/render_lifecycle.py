"""Durable contract for the authorized topic-production render lifecycle."""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .live_execution_models import ProductionJobPaths
from .narration_measurement import ProcessedNarrationArtifactBindingError
from .renderer_bridge import (
    AcceptedCandidateRendererBridge,
    AuthoritativeNarrationTimeline,
    RendererBridgeAuthorization,
    RendererPlanProfile,
)
from .runtime_models import ExecutionRunState


class AuthorizedRenderLifecycleStage(StrEnum):
    """Ordered failure stages owned by the authorized render lifecycle."""

    BIND = "bind"
    PERSIST = "persist"
    TIMELINE = "timeline"
    RENDER_PLAN = "render_plan"
    RENDER = "render"


class _DetachedAuthorityAccessModel(BaseModel):
    """Keep validated authority private and materialize a fresh copy on access."""

    _detached_authority_fields: ClassVar[frozenset[str]] = frozenset()

    def __getattribute__(self, name: str) -> object:
        value = super().__getattribute__(name)
        detached_fields = super().__getattribute__("_detached_authority_fields")
        if name in detached_fields and isinstance(value, BaseModel):
            return type(value).model_validate(value.model_dump(mode="python"))
        return value

    def __iter__(self) -> Iterator[tuple[str, object]]:
        for name in type(self).model_fields:
            yield name, getattr(self, name)

    def model_copy(
        self,
        *,
        update: dict[str, Any] | None = None,
        deep: bool = False,
    ) -> "_DetachedAuthorityAccessModel":
        payload = self.model_dump(mode="python")
        if deep:
            payload = deepcopy(payload)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)


class AuthorizedRenderLifecycleRequest(_DetachedAuthorityAccessModel):
    """Explicit authority and filesystem inputs for one future lifecycle run."""

    _detached_authority_fields = frozenset(
        {
            "state",
            "paths",
            "authorization",
            "profile",
        }
    )

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        strict=True,
    )

    state: ExecutionRunState
    paths: ProductionJobPaths
    processed_narration_path: Path
    authorization: RendererBridgeAuthorization
    profile: RendererPlanProfile
    execution_purpose: str = Field(min_length=1)
    selected_scene_id: str = Field(pattern=r"^scene_[0-9]{2}$")

    @field_validator("state", "paths", "authorization", "profile", mode="before")
    @classmethod
    def detach_authority_model(cls, value: object) -> object:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="python")
        return deepcopy(value)

    @field_validator("processed_narration_path")
    @classmethod
    def require_explicit_narration_path(cls, value: Path) -> Path:
        if str(value) in {"", "."}:
            raise ValueError("processed_narration_path must identify an explicit artifact")
        return value

    @field_validator("execution_purpose")
    @classmethod
    def require_canonical_execution_purpose(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("execution_purpose cannot contain surrounding whitespace")
        return value

    @model_validator(mode="after")
    def validate_authority_correspondence(self) -> "AuthorizedRenderLifecycleRequest":
        if self.authorization.job_id != self.state.run_plan.job_id:
            raise ValueError("renderer authorization job ID does not match runtime state")
        if self.selected_scene_id not in {scene.scene_id for scene in self.state.scenes}:
            raise ValueError("selected_scene_id is absent from runtime state")
        return self


@runtime_checkable
class AuthorizedRenderer(Protocol):
    """Future adapter that renders one authorized bridge with preserved timing."""

    async def __call__(
        self,
        authorized_plan: AcceptedCandidateRendererBridge,
        *,
        job_dir: Path,
        narration_artifact_path: Path,
    ) -> Path: ...


class AuthorizedRenderLifecycleOutcome(_DetachedAuthorityAccessModel):
    """Immutable authority and output identity from one completed lifecycle."""

    _detached_authority_fields = frozenset(
        {
            "state",
            "narration_timeline",
        }
    )

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        strict=True,
    )

    state: ExecutionRunState
    narration_timeline: AuthoritativeNarrationTimeline
    render_output_path: Path

    @field_validator("state", "narration_timeline", mode="before")
    @classmethod
    def detach_authority_model(cls, value: object) -> object:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="python")
        return deepcopy(value)

    @field_validator("render_output_path")
    @classmethod
    def require_explicit_render_output_path(cls, value: Path) -> Path:
        if str(value) in {"", "."}:
            raise ValueError("render_output_path must identify an explicit artifact")
        return value

    @model_validator(mode="after")
    def validate_completed_authority(self) -> "AuthorizedRenderLifecycleOutcome":
        measurement = self.state.processed_narration_measurement
        if measurement is None:
            raise ValueError("completed render lifecycle requires narration measurement")
        if self.narration_timeline.story_plan_sha256 != measurement.story_plan_sha256:
            raise ValueError("renderer timeline StoryPlan SHA-256 does not match measurement")
        if (
            self.narration_timeline.processed_duration_seconds
            != measurement.measured_duration_assessment.actual_duration_seconds
        ):
            raise ValueError("renderer timeline duration does not match measurement")
        if self.narration_timeline.narration_text != self.state.run_plan.story_plan.narration_text:
            raise ValueError("renderer timeline narration does not match runtime state")
        expected_scenes = tuple(
            (item.scene_id, item.order) for item in self.state.run_plan.scene_execution_plans
        )
        timeline_scenes = tuple(
            (item.scene_id, item.order) for item in self.narration_timeline.scene_timings
        )
        if timeline_scenes != expected_scenes:
            raise ValueError("renderer timeline scenes do not match runtime state")
        return self


class AuthorizedRenderLifecycleError(RuntimeError):
    """Stage context for an otherwise untyped lifecycle failure."""

    def __init__(
        self,
        stage: AuthorizedRenderLifecycleStage,
        cause: Exception,
    ) -> None:
        if not isinstance(stage, AuthorizedRenderLifecycleStage):
            raise TypeError("stage must be an AuthorizedRenderLifecycleStage")
        if isinstance(cause, ProcessedNarrationArtifactBindingError):
            raise TypeError("typed measurement binding errors must pass through unchanged")
        self.stage = stage
        self.original_exception = cause
        super().__init__(f"{stage.value}: {cause}")
        self.__cause__ = cause


@runtime_checkable
class AuthorizedRenderLifecycleCoordinator(Protocol):
    """Future BIND -> PERSIST -> TIMELINE -> RENDER_PLAN -> RENDER coordinator."""

    async def __call__(
        self,
        request: AuthorizedRenderLifecycleRequest,
        *,
        renderer: AuthorizedRenderer,
    ) -> AuthorizedRenderLifecycleOutcome: ...


__all__ = [
    "AuthorizedRenderLifecycleCoordinator",
    "AuthorizedRenderLifecycleError",
    "AuthorizedRenderLifecycleOutcome",
    "AuthorizedRenderLifecycleRequest",
    "AuthorizedRenderLifecycleStage",
    "AuthorizedRenderer",
]
