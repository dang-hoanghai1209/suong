"""Process-local bounded MP4 renderer-stage authority."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from enum import StrEnum
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import threading
from typing import Any, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

from tella.planner.models import Scene, TellaScenePlan
from tella.render.pipeline import render
from tella.visual_generation.providers.kinds import ProviderKind

from .duration_policy import DurationValueAuthority, assess_mvp_duration_target
from .renderer_bridge import (
    AcceptedCandidateRendererBridge,
    RendererAcceptedCandidateInput,
    build_authoritative_timeline_from_registered_scenes,
    build_renderer_bridge_from_registered_authority,
)
from .runtime_models import ProcessedNarrationDurationMeasurement

_SHA256 = r"^[0-9a-f]{64}$"
_IDENTITY = r"^[a-z0-9][a-z0-9._-]{0,127}$"
_MAX_MP4_BYTES = 500 * 1024 * 1024
_MAX_REASON_LENGTH = 500


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stream_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _timestamp(number: int) -> str:
    return f"2026-01-04T00:{number // 60:02d}:{number % 60:02d}Z"


def _detached(value: BaseModel) -> dict[str, object]:
    return json.loads(value.model_dump_json())


def _revalidate_json(model_type: type[BaseModel], value: object) -> BaseModel:
    return model_type.model_validate_json(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    )


def _safe_note(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > _MAX_REASON_LENGTH:
        raise ValueError("note must contain 1-500 characters")
    return normalized


def _error(code: str, message: str, *, retryable: bool = False) -> dict[str, object]:
    return {
        "schema_version": 1,
        "access": None,
        "approval": None,
        "render_package": None,
        "job": None,
        "artifact": None,
        "error": {
            "schema_version": 1,
            "code": code,
            "message": message,
            "retryable": retryable,
        },
    }


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @field_validator("schema_version", mode="before", check_fields=False)
    @classmethod
    def exact_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("schema_version must be exactly integer 1")
        return value


class _FinalMediaLocks(_Contract):
    full_render_enabled: Literal[False] = False
    final_media_capability: Literal[False] = False
    release_authority: Literal[False] = False
    publication_authority: Literal[False] = False
    output_creation_capability: Literal[False] = False
    subtitle_generation_capability: Literal[False] = False


class RenderJobStatus(StrEnum):
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED_FOR_BOUNDED_RENDER = "APPROVED_FOR_BOUNDED_RENDER"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    SUCCEEDED_FOR_REVIEW = "SUCCEEDED_FOR_REVIEW"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"


class RendererConfigurationV1(_Contract):
    schema_version: Literal[1] = 1
    renderer_configured: StrictBool
    renderer_implementation_id: Literal["tella.render.pipeline.render"] = (
        "tella.render.pipeline.render"
    )
    renderer_implementation_version: Literal["1"] = "1"
    renderer_bridge_contract_version: Literal["accepted_candidate_renderer_bridge_v1"] = (
        "accepted_candidate_renderer_bridge_v1"
    )
    ffmpeg_available: StrictBool
    ffmpeg_capability_version: Literal["ffmpeg_argument_list_local_v1"] = (
        "ffmpeg_argument_list_local_v1"
    )
    ffprobe_available: StrictBool
    ffprobe_capability_version: Literal["ffprobe_mp4_stream_validation_v1"] = (
        "ffprobe_mp4_stream_validation_v1"
    )
    local_only: Literal[True] = True
    shell_allowed: Literal[False] = False

    @model_validator(mode="after")
    def configured_consistency(self) -> RendererConfigurationV1:
        if self.renderer_configured != (self.ffmpeg_available and self.ffprobe_available):
            raise ValueError("renderer configuration summary is inconsistent")
        return self


class RendererOutputProfileV1(_Contract):
    schema_version: Literal[1] = 1
    profile_id: Literal["vertical_emotional_mp4"] = "vertical_emotional_mp4"
    profile_version: Literal["1"] = "1"
    container: Literal["mp4"] = "mp4"
    mime_type: Literal["video/mp4"] = "video/mp4"
    width: Literal[1080] = 1080
    height: Literal[1920] = 1920
    aspect_ratio: Literal["9:16"] = "9:16"
    frame_rate: Literal[30] = 30
    video_codec: Literal["h264"] = "h264"
    audio_codec: Literal["aac"] = "aac"
    pixel_format: Literal["yuv420p"] = "yuv420p"
    validation_policy_version: Literal["basic_mp4_validation_v1"] = "basic_mp4_validation_v1"


class RenderInputAuthorityV1(_Contract):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=_IDENTITY)
    execution_package_id: str = Field(pattern=_IDENTITY)
    execution_package_revision_id: str = Field(pattern=_IDENTITY)
    package_source_authority_sha256: str = Field(pattern=_SHA256)
    story_plan_sha256: str = Field(pattern=_SHA256)
    narration_source_sha256: str = Field(pattern=_SHA256)
    narration_artifact_id: str = Field(pattern=_IDENTITY)
    narration_artifact_revision_id: str = Field(pattern=_IDENTITY)
    narration_audio_sha256: str = Field(pattern=_SHA256)
    measured_narration_duration_ms: StrictInt = Field(gt=0)
    timeline_collection_revision_id: str = Field(pattern=_IDENTITY)
    accepted_timeline_revision_id: str = Field(pattern=_IDENTITY)
    timeline_source_authority_sha256: str = Field(pattern=_SHA256)
    scene_ids: tuple[str, ...] = Field(min_length=1)
    scene_revision_ids: tuple[str, ...] = Field(min_length=1)
    visual_candidate_ids: tuple[str, ...] = Field(min_length=1)
    visual_candidate_revision_ids: tuple[str, ...] = Field(min_length=1)
    visual_artifact_sha256s: tuple[str, ...] = Field(min_length=1)
    composition_ids: tuple[str, ...] = Field(min_length=1)
    composition_revision_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def aligned(self) -> RenderInputAuthorityV1:
        arrays = (
            self.scene_ids,
            self.scene_revision_ids,
            self.visual_candidate_ids,
            self.visual_candidate_revision_ids,
            self.visual_artifact_sha256s,
            self.composition_ids,
            self.composition_revision_ids,
        )
        if any(len(values) != len(self.scene_ids) for values in arrays[1:]):
            raise ValueError("renderer input authority arrays must be aligned")
        if tuple(self.scene_ids) != tuple(
            f"scene_{index:02d}" for index in range(1, len(self.scene_ids) + 1)
        ):
            raise ValueError("renderer input scene order must be canonical")
        if any(len(values) != len(set(values)) for values in arrays[:-1]):
            raise ValueError("renderer input identities must be unique")
        return self


class RendererStageApprovalV1(_FinalMediaLocks):
    schema_version: Literal[1] = 1
    approval_id: str = Field(pattern=r"^renderer-stage-approval-[0-9]{4}$")
    approval_revision_id: str = Field(pattern=r"^renderer-stage-approval-revision-[0-9]{4}$")
    purpose: Literal["BOUNDED_MP4_RENDER_EXECUTION"]
    input_authority: RenderInputAuthorityV1
    renderer_configuration: RendererConfigurationV1
    output_profile: RendererOutputProfileV1
    renderer_stage_source_authority_sha256: str = Field(pattern=_SHA256)
    current: StrictBool
    note: str | None = Field(default=None, max_length=_MAX_REASON_LENGTH)
    created_at: str
    process_local: Literal[True] = True


class RenderPackageV1(_FinalMediaLocks):
    schema_version: Literal[1] = 1
    render_package_id: str = Field(pattern=r"^render-package-[0-9]{4}$")
    render_package_revision_id: str = Field(pattern=r"^render-package-revision-[0-9]{4}$")
    render_package_revision: Literal[1] = 1
    approval_id: str = Field(pattern=_IDENTITY)
    approval_revision_id: str = Field(pattern=_IDENTITY)
    input_authority: RenderInputAuthorityV1
    renderer_configuration: RendererConfigurationV1
    output_profile: RendererOutputProfileV1
    render_package_sha256: str = Field(pattern=_SHA256)
    current: StrictBool
    created_at: str
    process_local: Literal[True] = True

    @model_validator(mode="after")
    def canonical_fingerprint(self) -> RenderPackageV1:
        projection = self.model_dump(
            mode="json",
            exclude={
                "render_package_sha256",
                "current",
                "created_at",
                "process_local",
                "full_render_enabled",
                "final_media_capability",
                "release_authority",
                "publication_authority",
                "output_creation_capability",
                "subtitle_generation_capability",
            },
        )
        if _canonical_sha256(projection) != self.render_package_sha256:
            raise ValueError("render package SHA-256 is inconsistent")
        return self


class RenderJobProgressV1(_Contract):
    schema_version: Literal[1] = 1
    completed_units: StrictInt = Field(ge=0, le=1)
    total_units: Literal[1] = 1
    percent: StrictInt = Field(ge=0, le=100)

    @model_validator(mode="after")
    def derived(self) -> RenderJobProgressV1:
        if self.percent != self.completed_units * 100:
            raise ValueError("render progress is inconsistent")
        return self


class RenderJobV1(_FinalMediaLocks):
    schema_version: Literal[1] = 1
    job_id: str = Field(pattern=r"^render-job-[0-9]{4}$")
    job_attempt_id: str = Field(pattern=r"^render-job-attempt-[0-9]{4}$")
    run_id: str = Field(pattern=_IDENTITY)
    render_package_id: str = Field(pattern=_IDENTITY)
    render_package_revision_id: str = Field(pattern=_IDENTITY)
    render_package_sha256: str = Field(pattern=_SHA256)
    status: RenderJobStatus
    progress: RenderJobProgressV1
    failure_code: str | None = None
    cancellation_reason: str | None = Field(default=None, max_length=_MAX_REASON_LENGTH)
    artifact_id: str | None = Field(default=None, pattern=_IDENTITY)
    created_at: str
    updated_at: str
    current: StrictBool
    process_local: Literal[True] = True

    @model_validator(mode="after")
    def status_consistency(self) -> RenderJobV1:
        failed = self.status is RenderJobStatus.FAILED
        cancelled = self.status in {
            RenderJobStatus.CANCEL_REQUESTED,
            RenderJobStatus.CANCELLED,
        }
        succeeded = self.status is RenderJobStatus.SUCCEEDED_FOR_REVIEW
        if (
            failed != (self.failure_code is not None)
            or cancelled != (self.cancellation_reason is not None)
            or succeeded != (self.artifact_id is not None)
            or self.progress.completed_units != int(succeeded)
        ):
            raise ValueError("render job status summary is inconsistent")
        return self


class Mp4MetadataV1(_Contract):
    schema_version: Literal[1] = 1
    duration_ms: StrictInt = Field(gt=0)
    width: StrictInt = Field(gt=0)
    height: StrictInt = Field(gt=0)
    video_codec: str = Field(min_length=1)
    audio_codec: str = Field(min_length=1)
    video_stream_count: Literal[1]
    audio_stream_count: Literal[1]


class RenderArtifactV1(_FinalMediaLocks):
    schema_version: Literal[1] = 1
    artifact_id: str = Field(pattern=r"^render-artifact-[0-9]{4}$")
    artifact_revision_id: str = Field(pattern=r"^render-artifact-revision-[0-9]{4}$")
    run_id: str = Field(pattern=_IDENTITY)
    render_package_id: str = Field(pattern=_IDENTITY)
    render_package_revision_id: str = Field(pattern=_IDENTITY)
    render_package_sha256: str = Field(pattern=_SHA256)
    job_id: str = Field(pattern=_IDENTITY)
    mime_type: Literal["video/mp4"] = "video/mp4"
    extension: Literal["mp4"] = "mp4"
    mp4_sha256: str = Field(pattern=_SHA256)
    byte_length: StrictInt = Field(gt=0, le=_MAX_MP4_BYTES)
    metadata: Mp4MetadataV1
    status: Literal[
        "GENERATED_FOR_REVIEW",
        "ACCEPTED_FOR_FINAL_MEDIA_QC_REVIEW",
        "REJECTED",
        "RERENDER_REQUESTED",
        "SUPERSEDED",
    ]
    current: StrictBool
    render_artifact_accepted_for_qc: StrictBool
    eligible_for_final_media_qc_review: StrictBool
    superseded_reason: str | None = None
    created_at: str
    process_local: Literal[True] = True

    @model_validator(mode="after")
    def state_consistency(self) -> RenderArtifactV1:
        accepted = self.status == "ACCEPTED_FOR_FINAL_MEDIA_QC_REVIEW" and self.current
        if (
            self.render_artifact_accepted_for_qc != accepted
            or self.eligible_for_final_media_qc_review != accepted
            or (self.status == "SUPERSEDED") != (self.superseded_reason is not None)
        ):
            raise ValueError("render artifact authority is inconsistent")
        return self


class RenderReviewEntryV1(_Contract):
    schema_version: Literal[1] = 1
    review_id: str = Field(pattern=r"^render-review-[0-9]{4}$")
    artifact_revision_id: str = Field(pattern=_IDENTITY)
    action: Literal["GENERATED", "ACCEPTED_FOR_QC", "REJECTED", "RERENDER_REQUESTED"]
    purpose: Literal["SEPARATE_FINAL_MEDIA_QC_REVIEW"] | None = None
    reason: str | None = Field(default=None, max_length=_MAX_REASON_LENGTH)
    created_at: str


class RendererStageAccessV1(_FinalMediaLocks):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=_IDENTITY)
    blocker_codes: tuple[str, ...]
    renderer_stage_review_capability: StrictBool
    renderer_stage_approval_capability: StrictBool
    render_package_creation_capability: StrictBool
    bounded_render_execution_capability: StrictBool
    render_job_creation_capability: StrictBool
    render_job_cancellation_capability: StrictBool
    mp4_artifact_creation_capability: StrictBool
    render_artifact_review_capability: StrictBool
    eligible_for_final_media_qc_review: StrictBool
    input_authority: RenderInputAuthorityV1 | None
    renderer_configuration: RendererConfigurationV1
    output_profile: RendererOutputProfileV1
    approval: RendererStageApprovalV1 | None
    render_package: RenderPackageV1 | None
    job: RenderJobV1 | None
    artifact: RenderArtifactV1 | None
    process_local: Literal[True] = True
    restart_warning: Literal[
        "Host restart clears renderer-stage authority; stored MP4 bytes are not rebound."
    ] = "Host restart clears renderer-stage authority; stored MP4 bytes are not rebound."

    @model_validator(mode="after")
    def capabilities(self) -> RendererStageAccessV1:
        unlocked = not self.blocker_codes and self.input_authority is not None
        package_capability = unlocked and self.approval is not None and self.approval.current
        execution = (
            package_capability
            and self.render_package is not None
            and self.render_package.current
            and self.job is None
        )
        active = self.job is not None and self.job.status in {
            RenderJobStatus.QUEUED,
            RenderJobStatus.RUNNING,
            RenderJobStatus.CANCEL_REQUESTED,
        }
        review = (
            self.artifact is not None
            and self.artifact.current
            and self.artifact.status == "GENERATED_FOR_REVIEW"
        )
        if (
            self.renderer_stage_review_capability != unlocked
            or self.renderer_stage_approval_capability != unlocked
            or self.render_package_creation_capability != package_capability
            or self.bounded_render_execution_capability != execution
            or self.render_job_creation_capability != execution
            or self.render_job_cancellation_capability != active
            or self.mp4_artifact_creation_capability != execution
            or self.render_artifact_review_capability != review
            or self.eligible_for_final_media_qc_review
            != (self.artifact is not None and self.artifact.eligible_for_final_media_qc_review)
        ):
            raise ValueError("renderer-stage capability projection is inconsistent")
        return self


class ApproveRendererStageRequestV1(_Contract):
    schema_version: Literal[1]
    execution_package_id: str = Field(pattern=_IDENTITY)
    execution_package_revision_id: str = Field(pattern=_IDENTITY)
    package_source_authority_sha256: str = Field(pattern=_SHA256)
    narration_artifact_id: str = Field(pattern=_IDENTITY)
    narration_artifact_revision_id: str = Field(pattern=_IDENTITY)
    narration_audio_sha256: str = Field(pattern=_SHA256)
    explicit_local_resource_acknowledgement: Literal[True]
    explicit_confirmation: Literal[True]
    note: str | None = Field(default=None, max_length=_MAX_REASON_LENGTH)


class CreateRenderPackageRequestV1(_Contract):
    schema_version: Literal[1]
    approval_id: str = Field(pattern=_IDENTITY)
    approval_revision_id: str = Field(pattern=_IDENTITY)
    explicit_confirmation: Literal[True]


class StartRenderJobRequestV1(_Contract):
    schema_version: Literal[1]
    render_package_id: str = Field(pattern=_IDENTITY)
    render_package_revision_id: str = Field(pattern=_IDENTITY)
    render_package_sha256: str = Field(pattern=_SHA256)
    explicit_confirmation: Literal[True]


class CancelRenderJobRequestV1(_Contract):
    schema_version: Literal[1]
    job_attempt_id: str = Field(pattern=_IDENTITY)
    explicit_confirmation: Literal[True]
    reason: str = Field(min_length=1, max_length=_MAX_REASON_LENGTH)


class ReviewRenderArtifactRequestV1(_Contract):
    schema_version: Literal[1]
    artifact_revision_id: str = Field(pattern=_IDENTITY)
    mp4_sha256: str = Field(pattern=_SHA256)
    explicit_confirmation: Literal[True]
    reason: str | None = Field(default=None, max_length=_MAX_REASON_LENGTH)


class RenderProbe(Protocol):
    def __call__(self, path: Path) -> Mapping[str, object]: ...


class RendererExecutor(Protocol):
    def __call__(
        self,
        bridge: AcceptedCandidateRendererBridge,
        *,
        job_dir: Path,
        narration_artifact_path: Path,
        cancellation_requested: threading.Event,
    ) -> Path: ...


def probe_mp4_artifact(path: Path, *, ffprobe_binary: str = "ffprobe") -> Mapping[str, object]:
    command = [
        ffprobe_binary,
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,codec_name,width,height:format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        completed = subprocess.run(
            command,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise ValueError("MP4_PROBE_FAILED") from exc
    if completed.returncode != 0 or len(completed.stdout) > 1_000_000:
        raise ValueError("MP4_PROBE_FAILED")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("MP4_PROBE_FAILED") from exc
    streams = payload.get("streams")
    format_payload = payload.get("format")
    if not isinstance(streams, list) or not isinstance(format_payload, Mapping):
        raise ValueError("MP4_PROBE_FAILED")
    video = [item for item in streams if item.get("codec_type") == "video"]
    audio = [item for item in streams if item.get("codec_type") == "audio"]
    if len(video) != 1 or len(audio) != 1:
        raise ValueError("MP4_STREAM_POLICY_FAILED")
    try:
        duration = float(format_payload["duration"])
        width = int(video[0]["width"])
        height = int(video[0]["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("MP4_PROBE_FAILED") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("MP4_PROBE_FAILED")
    return {
        "duration_ms": round(duration * 1000),
        "width": width,
        "height": height,
        "video_codec": str(video[0].get("codec_name", "")),
        "audio_codec": str(audio[0].get("codec_name", "")),
        "video_stream_count": 1,
        "audio_stream_count": 1,
    }


class LocalAuthorizedRenderer:
    """Adapter from the sealed bridge to the repository's FFmpeg renderer."""

    def __call__(
        self,
        bridge: AcceptedCandidateRendererBridge,
        *,
        job_dir: Path,
        narration_artifact_path: Path,
        cancellation_requested: threading.Event,
    ) -> Path:
        if cancellation_requested.is_set():
            raise ValueError("RENDER_CANCELLED")
        snapshot = bridge.require_builder_authorization()
        plan = snapshot.renderer_plan
        plan.narration_audio_filename = str(narration_artifact_path)
        try:
            return asyncio.run(
                asyncio.wait_for(
                    render(
                        plan,
                        job_dir,
                        preserve_timing=True,
                    ),
                    timeout=600,
                )
            )
        except TimeoutError as exc:
            raise ValueError("RENDERER_TIMEOUT") from exc


class RendererStageStore:
    """One-worker process-local renderer authority and artifact registry."""

    def __init__(
        self,
        *,
        renderer: RendererExecutor | None = None,
        artifact_root: Path | None = None,
        probe: RenderProbe = probe_mp4_artifact,
        renderer_configured: bool | None = None,
    ) -> None:
        ffmpeg_available = shutil.which("ffmpeg") is not None
        ffprobe_available = shutil.which("ffprobe") is not None
        if renderer_configured is not None:
            ffmpeg_available = renderer_configured
            ffprobe_available = renderer_configured
        self._configuration = RendererConfigurationV1(
            renderer_configured=ffmpeg_available and ffprobe_available,
            ffmpeg_available=ffmpeg_available,
            ffprobe_available=ffprobe_available,
        )
        self._profile = RendererOutputProfileV1()
        self._renderer = renderer or LocalAuthorizedRenderer()
        self._artifact_root = Path(artifact_root or Path("out") / "renderer-stage")
        self._probe = probe
        self._approvals: dict[str, tuple[RendererStageApprovalV1, ...]] = {}
        self._packages: dict[str, tuple[RenderPackageV1, ...]] = {}
        self._jobs: dict[str, tuple[RenderJobV1, ...]] = {}
        self._artifacts: dict[str, tuple[RenderArtifactV1, ...]] = {}
        self._reviews: dict[str, tuple[RenderReviewEntryV1, ...]] = {}
        self._artifact_paths: dict[tuple[str, str], Path] = {}
        self._contexts: dict[str, dict[str, Any]] = {}
        self._cancel_events: dict[tuple[str, str], threading.Event] = {}
        self._futures: dict[tuple[str, str], Future[None]] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tella-renderer")
        self._lock = threading.RLock()
        self._identity = 1

    def close(self) -> None:
        with self._lock:
            for event in self._cancel_events.values():
                event.set()
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _latest(self, values: Mapping[str, tuple[Any, ...]], run_id: str) -> Any | None:
        entries = values.get(run_id, ())
        return entries[-1] if entries else None

    def _context(self, context: Mapping[str, Any]) -> tuple[tuple[str, ...], dict[str, Any] | None]:
        blockers: list[str] = []
        run = context.get("run")
        execution = context.get("execution_access")
        narration = context.get("narration_access")
        timeline = context.get("timeline_access")
        composition = context.get("composition_access")
        visual_records = context.get("visual_records")
        audio_bytes = context.get("audio_bytes")
        if not all(
            isinstance(value, Mapping)
            for value in (run, execution, narration, timeline, composition)
        ):
            return ("RENDER_INPUT_MISSING",), None
        package = execution.get("package")
        artifact = narration.get("artifact")
        timeline_collection = timeline.get("collection")
        composition_collection = composition.get("collection")
        if (
            not isinstance(package, Mapping)
            or package.get("current") is not True
            or package.get("status") != "SEALED_FOR_NARRATION_STAGE_REVIEW"
        ):
            blockers.append("RENDER_PACKAGE_STALE")
        if (
            not isinstance(artifact, Mapping)
            or artifact.get("current") is not True
            or artifact.get("status") != "ACCEPTED_FOR_RENDERER_STAGE_REVIEW"
            or artifact.get("eligible_for_renderer_stage_review") is not True
        ):
            blockers.append("ACCEPTED_NARRATION_ARTIFACT_REQUIRED")
        if (
            not isinstance(timeline_collection, Mapping)
            or timeline_collection.get("accepted_for_execution_review") is not True
        ):
            blockers.append("RENDER_INPUT_MISSING")
        if (
            not isinstance(composition_collection, Mapping)
            or composition_collection.get("overall_ready_for_timeline_planning") is not True
        ):
            blockers.append("RENDER_INPUT_MISSING")
        if not isinstance(visual_records, tuple) or not visual_records:
            blockers.append("RENDER_INPUT_MISSING")
        if not isinstance(audio_bytes, bytes) or not audio_bytes:
            blockers.append("RENDER_INPUT_MISSING")
        if blockers:
            return tuple(dict.fromkeys(blockers)), None
        authority = package["authority"]
        segments = tuple(timeline_collection["segments"])
        compositions = tuple(composition_collection["compositions"])
        if len(segments) != len(compositions) or len(segments) != len(visual_records):
            return ("RENDER_INPUT_MISSING",), None
        if hashlib.sha256(audio_bytes).hexdigest() != artifact["audio_sha256"]:
            return ("RENDER_INPUT_HASH_MISMATCH",), None
        for segment, composition_item, record in zip(
            segments, compositions, visual_records, strict=True
        ):
            candidate = record.get("candidate")
            content = record.get("content")
            if (
                not isinstance(candidate, Mapping)
                or not isinstance(content, bytes)
                or hashlib.sha256(content).hexdigest() != candidate.get("sha256")
                or segment["candidate_artifact_sha256"] != candidate["sha256"]
                or composition_item["accepted_candidate_sha256"] != candidate["sha256"]
                or segment["accepted_candidate_id"] != candidate["candidate_id"]
                or composition_item["accepted_candidate_id"] != candidate["candidate_id"]
            ):
                return ("RENDER_INPUT_HASH_MISMATCH",), None
        input_authority = RenderInputAuthorityV1(
            run_id=run["run_id"],
            execution_package_id=package["package_id"],
            execution_package_revision_id=package["package_revision_id"],
            package_source_authority_sha256=package["package_source_authority_sha256"],
            story_plan_sha256=_canonical_sha256(run["story_plan"]),
            narration_source_sha256=authority["narration_source_sha256"],
            narration_artifact_id=artifact["artifact_id"],
            narration_artifact_revision_id=artifact["artifact_revision_id"],
            narration_audio_sha256=artifact["audio_sha256"],
            measured_narration_duration_ms=artifact["measured_duration_ms"],
            timeline_collection_revision_id=timeline_collection["collection_revision_id"],
            accepted_timeline_revision_id=timeline_collection["accepted_timeline_revision_id"],
            timeline_source_authority_sha256=timeline_collection["source_authority_sha256"],
            scene_ids=tuple(item["scene_id"] for item in segments),
            scene_revision_ids=tuple(item["scene_revision_id"] for item in segments),
            visual_candidate_ids=tuple(item["accepted_candidate_id"] for item in segments),
            visual_candidate_revision_ids=tuple(
                record["candidate"]["candidate_revision_id"] for record in visual_records
            ),
            visual_artifact_sha256s=tuple(item["candidate_artifact_sha256"] for item in segments),
            composition_ids=tuple(item["composition_id"] for item in compositions),
            composition_revision_ids=tuple(
                item["composition_revision_id"] for item in compositions
            ),
        )
        projection = {
            "input_authority": input_authority.model_dump(mode="json"),
            "renderer_configuration": self._configuration.model_dump(mode="json"),
            "output_profile": self._profile.model_dump(mode="json"),
        }
        return (), {
            "input_authority": input_authority,
            "source_sha256": _canonical_sha256(projection),
            "run": deepcopy(run),
            "package": deepcopy(package),
            "artifact": deepcopy(artifact),
            "segments": deepcopy(segments),
            "compositions": deepcopy(compositions),
            "visual_records": deepcopy(visual_records),
            "audio_bytes": bytes(audio_bytes),
        }

    def _supersede(self, run_id: str, source_sha256: str | None) -> None:
        approval = self._latest(self._approvals, run_id)
        if (
            approval is not None
            and approval.current
            and approval.renderer_stage_source_authority_sha256 != source_sha256
        ):
            payload = approval.model_dump(mode="python")
            payload["current"] = False
            self._approvals[run_id] = (
                *self._approvals[run_id][:-1],
                RendererStageApprovalV1.model_validate(payload),
            )
        package = self._latest(self._packages, run_id)
        if (
            package is not None
            and package.current
            and (
                approval is None
                or not approval.current
                or approval.renderer_stage_source_authority_sha256 != source_sha256
            )
        ):
            payload = package.model_dump(mode="python")
            payload["current"] = False
            self._packages[run_id] = (
                *self._packages[run_id][:-1],
                RenderPackageV1.model_validate(payload),
            )
            package = self._latest(self._packages, run_id)
        artifact = self._latest(self._artifacts, run_id)
        if artifact is not None and artifact.current and (package is None or not package.current):
            payload = artifact.model_dump(mode="python")
            payload.update(
                status="SUPERSEDED",
                current=False,
                render_artifact_accepted_for_qc=False,
                eligible_for_final_media_qc_review=False,
                superseded_reason="BOUND_RENDER_AUTHORITY_CHANGED",
            )
            self._artifacts[run_id] = (
                *self._artifacts[run_id][:-1],
                RenderArtifactV1.model_validate(payload),
            )
        job = self._latest(self._jobs, run_id)
        if (
            job is not None
            and job.current
            and (package is None or not package.current)
            and job.status
            not in {
                RenderJobStatus.CANCELLED,
                RenderJobStatus.FAILED,
                RenderJobStatus.SUPERSEDED,
            }
        ):
            payload = job.model_dump(mode="python")
            payload.update(
                status=RenderJobStatus.SUPERSEDED,
                progress=RenderJobProgressV1(completed_units=0, percent=0),
                artifact_id=None,
                current=False,
                updated_at=_timestamp(self._identity),
            )
            self._jobs[run_id] = (
                *self._jobs[run_id][:-1],
                RenderJobV1.model_validate(payload),
            )
            event = self._cancel_events.get((run_id, job.job_id))
            if event is not None:
                event.set()

    def access(self, **context: Any) -> dict[str, object] | None:
        run = context.get("run")
        if not isinstance(run, Mapping):
            return None
        run_id = str(run["run_id"])
        blockers, projected = self._context(context)
        if not self._configuration.renderer_configured:
            blockers = (*blockers, "RENDERER_NOT_CONFIGURED")
        self._supersede(
            run_id, None if blockers or projected is None else projected["source_sha256"]
        )
        if projected is not None and not blockers:
            self._contexts[run_id] = projected
        approval = self._latest(self._approvals, run_id)
        package = self._latest(self._packages, run_id)
        job = self._latest(self._jobs, run_id)
        artifact = self._latest(self._artifacts, run_id)
        current_job = (
            job
            if job is not None
            and job.current
            and job.status
            not in {
                RenderJobStatus.CANCELLED,
                RenderJobStatus.FAILED,
                RenderJobStatus.SUPERSEDED,
            }
            else None
        )
        unlocked = not blockers and projected is not None
        package_capability = unlocked and approval is not None and approval.current
        execution = (
            package_capability and package is not None and package.current and current_job is None
        )
        active = current_job is not None and current_job.status in {
            RenderJobStatus.QUEUED,
            RenderJobStatus.RUNNING,
            RenderJobStatus.CANCEL_REQUESTED,
        }
        return _detached(
            RendererStageAccessV1(
                run_id=run_id,
                blocker_codes=tuple(dict.fromkeys(blockers)),
                renderer_stage_review_capability=unlocked,
                renderer_stage_approval_capability=unlocked,
                render_package_creation_capability=package_capability,
                bounded_render_execution_capability=execution,
                render_job_creation_capability=execution,
                render_job_cancellation_capability=active,
                mp4_artifact_creation_capability=execution,
                render_artifact_review_capability=(
                    artifact is not None
                    and artifact.current
                    and artifact.status == "GENERATED_FOR_REVIEW"
                ),
                eligible_for_final_media_qc_review=(
                    artifact is not None and artifact.eligible_for_final_media_qc_review
                ),
                input_authority=None if projected is None else projected["input_authority"],
                renderer_configuration=self._configuration,
                output_profile=self._profile,
                approval=approval,
                render_package=package,
                job=current_job,
                artifact=artifact,
            )
        )

    def approve(self, *, payload: object, **context: Any) -> dict[str, object]:
        try:
            request = ApproveRendererStageRequestV1.model_validate(payload)
            note = _safe_note(request.note)
        except (ValidationError, ValueError):
            return _error("RENDERER_STAGE_APPROVAL_REQUIRED", "Renderer approval is malformed.")
        access = self.access(**context)
        if access is None or access["renderer_stage_approval_capability"] is not True:
            return _error("ACCEPTED_NARRATION_ARTIFACT_REQUIRED", "Renderer stage is locked.")
        authority = _revalidate_json(RenderInputAuthorityV1, access["input_authority"])
        assert isinstance(authority, RenderInputAuthorityV1)
        if (
            request.execution_package_id != authority.execution_package_id
            or request.execution_package_revision_id != authority.execution_package_revision_id
            or request.package_source_authority_sha256 != authority.package_source_authority_sha256
            or request.narration_artifact_id != authority.narration_artifact_id
            or request.narration_artifact_revision_id != authority.narration_artifact_revision_id
            or request.narration_audio_sha256 != authority.narration_audio_sha256
        ):
            return _error("RENDERER_STAGE_APPROVAL_STALE", "Renderer approval is stale.")
        run_id = authority.run_id
        number = self._identity
        self._identity += 1
        source_sha = self._contexts[run_id]["source_sha256"]
        approval = RendererStageApprovalV1(
            approval_id=f"renderer-stage-approval-{number:04d}",
            approval_revision_id=f"renderer-stage-approval-revision-{number:04d}",
            purpose="BOUNDED_MP4_RENDER_EXECUTION",
            input_authority=authority,
            renderer_configuration=self._configuration,
            output_profile=self._profile,
            renderer_stage_source_authority_sha256=source_sha,
            current=True,
            note=note,
            created_at=_timestamp(number),
        )
        self._approvals[run_id] = (*self._approvals.get(run_id, ()), approval)
        return {
            "schema_version": 1,
            "access": None,
            "approval": _detached(approval),
            "render_package": None,
            "job": None,
            "artifact": None,
            "error": None,
        }

    def create_package(self, *, payload: object, **context: Any) -> dict[str, object]:
        try:
            request = CreateRenderPackageRequestV1.model_validate(payload)
        except ValidationError:
            return _error(
                "RENDERER_STAGE_APPROVAL_REQUIRED", "Render-package request is malformed."
            )
        access = self.access(**context)
        if access is None or access["render_package_creation_capability"] is not True:
            return _error("RENDERER_STAGE_APPROVAL_REQUIRED", "Current approval is required.")
        approval = _revalidate_json(RendererStageApprovalV1, access["approval"])
        assert isinstance(approval, RendererStageApprovalV1)
        if (
            request.approval_id != approval.approval_id
            or request.approval_revision_id != approval.approval_revision_id
        ):
            return _error("RENDERER_STAGE_APPROVAL_STALE", "Renderer approval is stale.")
        existing = self._latest(self._packages, approval.input_authority.run_id)
        if existing is not None and existing.current:
            return _error("RENDER_REQUEST_ALREADY_COMPLETED", "A current render package exists.")
        number = self._identity
        self._identity += 1
        base = {
            "schema_version": 1,
            "render_package_id": f"render-package-{number:04d}",
            "render_package_revision_id": f"render-package-revision-{number:04d}",
            "render_package_revision": 1,
            "approval_id": approval.approval_id,
            "approval_revision_id": approval.approval_revision_id,
            "input_authority": approval.input_authority,
            "renderer_configuration": self._configuration,
            "output_profile": self._profile,
        }
        fingerprint_base = {
            **base,
            "input_authority": approval.input_authority.model_dump(mode="json"),
            "renderer_configuration": self._configuration.model_dump(mode="json"),
            "output_profile": self._profile.model_dump(mode="json"),
        }
        package = RenderPackageV1(
            **base,
            render_package_sha256=_canonical_sha256(fingerprint_base),
            current=True,
            created_at=_timestamp(number),
        )
        run_id = approval.input_authority.run_id
        self._packages[run_id] = (*self._packages.get(run_id, ()), package)
        return {
            "schema_version": 1,
            "access": None,
            "approval": None,
            "render_package": _detached(package),
            "job": None,
            "artifact": None,
            "error": None,
        }

    def start(self, *, payload: object, **context: Any) -> dict[str, object]:
        try:
            request = StartRenderJobRequestV1.model_validate(payload)
        except ValidationError:
            return _error("RENDER_PACKAGE_STALE", "Render start request is malformed.")
        with self._lock:
            if any(
                entries
                and entries[-1].current
                and entries[-1].status
                in {
                    RenderJobStatus.QUEUED,
                    RenderJobStatus.RUNNING,
                    RenderJobStatus.CANCEL_REQUESTED,
                }
                for entries in self._jobs.values()
            ):
                return _error(
                    "RENDER_ALREADY_IN_PROGRESS",
                    "This application instance already owns one active render.",
                )
            access = self.access(**context)
            if access is None or access["render_job_creation_capability"] is not True:
                return _error("RENDER_ALREADY_IN_PROGRESS", "A render cannot be started.")
            package = _revalidate_json(RenderPackageV1, access["render_package"])
            assert isinstance(package, RenderPackageV1)
            if (
                request.render_package_id != package.render_package_id
                or request.render_package_revision_id != package.render_package_revision_id
                or request.render_package_sha256 != package.render_package_sha256
            ):
                return _error("RENDER_PACKAGE_STALE", "Render package is stale.")
            run_id = package.input_authority.run_id
            number = self._identity
            self._identity += 1
            job = RenderJobV1(
                job_id=f"render-job-{number:04d}",
                job_attempt_id=f"render-job-attempt-{number:04d}",
                run_id=run_id,
                render_package_id=package.render_package_id,
                render_package_revision_id=package.render_package_revision_id,
                render_package_sha256=package.render_package_sha256,
                status=RenderJobStatus.QUEUED,
                progress=RenderJobProgressV1(completed_units=0, percent=0),
                created_at=_timestamp(number),
                updated_at=_timestamp(number),
                current=True,
            )
            self._jobs[run_id] = (*self._jobs.get(run_id, ()), job)
            cancel_event = threading.Event()
            key = (run_id, job.job_id)
            self._cancel_events[key] = cancel_event
            snapshot = deepcopy(self._contexts[run_id])
            future = self._executor.submit(
                self._execute,
                run_id,
                job,
                package,
                snapshot,
                cancel_event,
            )
            self._futures[key] = future
        return {
            "schema_version": 1,
            "access": None,
            "approval": None,
            "render_package": None,
            "job": _detached(job),
            "artifact": None,
            "error": None,
        }

    def _replace_job(self, run_id: str, job: RenderJobV1, **updates: object) -> RenderJobV1:
        payload = job.model_dump(mode="python")
        payload.update(updates)
        updated = RenderJobV1.model_validate(payload)
        self._jobs[run_id] = (*self._jobs[run_id][:-1], updated)
        return updated

    def _build_bridge(
        self,
        package: RenderPackageV1,
        context: Mapping[str, Any],
        job_dir: Path,
    ) -> tuple[AcceptedCandidateRendererBridge, Path]:
        authority = package.input_authority
        audio_path = job_dir / "narration.wav"
        audio_path.write_bytes(context["audio_bytes"])
        if _stream_sha256(audio_path) != authority.narration_audio_sha256:
            raise ValueError("RENDER_INPUT_HASH_MISMATCH")
        visual_inputs: list[RendererAcceptedCandidateInput] = []
        scenes: list[Scene] = []
        segments = context["segments"]
        for index, (segment, record) in enumerate(
            zip(segments, context["visual_records"], strict=True),
            start=1,
        ):
            candidate = record["candidate"]
            extension = {"image/png": "png", "image/jpeg": "jpg"}[candidate["mime_type"]]
            image_path = job_dir / f"scene_{index:02d}.{extension}"
            image_path.write_bytes(record["content"])
            if _stream_sha256(image_path) != candidate["sha256"]:
                raise ValueError("RENDER_INPUT_HASH_MISMATCH")
            visual_inputs.append(
                RendererAcceptedCandidateInput(
                    scene_id=segment["scene_id"],
                    order=index,
                    candidate_id=candidate["candidate_id"],
                    artifact_path=image_path.resolve(),
                    artifact_sha256=candidate["sha256"],
                    image_format="PNG" if extension == "png" else "JPEG",
                    width=candidate["width"],
                    height=candidate["height"],
                    provider_kind=ProviderKind.POLLINATIONS,
                    provider=candidate["provider_capability_label"],
                    model=candidate["model_label"],
                    seed=0,
                    planning_request_hash=candidate["logical_request_hash"],
                    logical_request_hash=candidate["logical_request_hash"],
                    provider_request_hash=candidate["provider_request_hash"],
                    reference_hashes=(),
                    qc_record_id=candidate["candidate_revision_id"],
                )
            )
        measured_seconds = authority.measured_narration_duration_ms / 1000.0
        transition_profile_id = (
            "subtle_crossfade"
            if any(segment["transition_out_ms"] > 0 for segment in segments[:-1])
            else "clean_soft_cut"
        )
        timeline = build_authoritative_timeline_from_registered_scenes(
            story_plan_sha256=authority.story_plan_sha256,
            narration_text=context["run"]["story_plan"]["narration_text"],
            measured_duration_seconds=measured_seconds,
            requested_duration_seconds=context["artifact"]["duration_comparison"][
                "accepted_timeline_duration_ms"
            ]
            / 1000.0,
            transition_profile_id=transition_profile_id,
            scene_weights=tuple(
                (segment["scene_id"], index, segment["duration_ms"] / 1000.0)
                for index, segment in enumerate(segments, start=1)
            ),
        )
        for timing, segment, visual in zip(
            timeline.scene_timings, segments, visual_inputs, strict=True
        ):
            scenes.append(
                Scene(
                    kind="scene",
                    scene_index=timing.order,
                    title="",
                    voice_script=segment["canonical_narration_segment"],
                    scene_setting="Accepted composition",
                    scene_action="Accepted timeline",
                    scene_meaning=segment["semantic_beat_id"],
                    image_source="accepted_candidate_registry",
                    image_provider=visual.provider,
                    provider=visual.provider,
                    asset_status="done",
                    asset_count=1,
                    asset_path=str(visual.artifact_path),
                    selected_attempt_path=str(visual.artifact_path),
                    image_filenames=[str(visual.artifact_path)],
                    qc_passed=True,
                    final_passed=True,
                    scene_image_attempt_count=1,
                    visual_mode="illustrated_scene",
                    used_local_fallback=False,
                    local_fallback_allowed=False,
                    audio_duration=timing.duration_seconds,
                    duration=timing.duration_seconds,
                    render_clip_duration=timing.render_clip_duration_seconds,
                    start=timing.start_seconds,
                )
            )
        contract = timeline.render_timing_contract
        plan = TellaScenePlan(
            title="Topic production render",
            language=context["run"]["story_plan"]["language"],
            aspect_ratio="9:16",
            media_source="ai_image",
            theme="minimalist_emotional",
            recipe_id="topic_production_renderer_stage",
            recipe_version=1,
            recipe_status="accepted",
            narrative_mode="topic_production",
            planner_id="topic_production",
            visual_theme_id="minimalist_emotional",
            voice_profile_id="accepted_narration_artifact",
            subtitle_style_id="minimal",
            transition_profile_id=transition_profile_id,
            motion_profile_id="slow_ken_burns",
            recipe_scene_range=[len(scenes), len(scenes)],
            recipe_duration_range=[measured_seconds, measured_seconds],
            recipe_validation_status="passed",
            scenes=scenes,
            global_narration_text=timeline.narration_text,
            tts_continuous=True,
            tts_text_source="accepted_narration_artifact",
            narration_audio_filename=str(audio_path.resolve()),
            narration_duration=measured_seconds,
            processed_narration_duration=measured_seconds,
            requested_production_duration_seconds=(
                context["artifact"]["duration_comparison"]["accepted_timeline_duration_ms"] / 1000.0
            ),
            duration_target_seconds=measured_seconds,
            scene_timing_map=[
                {
                    "scene_index": timing.order,
                    "start": timing.start_seconds,
                    "duration": timing.duration_seconds,
                    "timeline_duration": timing.duration_seconds,
                    "render_clip_duration": timing.render_clip_duration_seconds,
                    "outgoing_transition_overlap": round(
                        timing.render_clip_duration_seconds - timing.duration_seconds, 6
                    ),
                    "end": timing.end_seconds,
                }
                for timing in timeline.scene_timings
            ],
            render_timing_contract=contract.model_dump(mode="python"),
            total_duration=measured_seconds,
            subtitle_style="minimal",
            music_enabled=False,
            local_fallback_allowed=False,
            used_local_fallback=False,
        )
        measurement = ProcessedNarrationDurationMeasurement(
            schema_version=1,
            artifact_relative_path="narration.wav",
            artifact_sha256=authority.narration_audio_sha256,
            story_plan_sha256=authority.story_plan_sha256,
            measurement_method="ffprobe_single_audio_stream_v1",
            measured_duration_assessment=assess_mvp_duration_target(
                float(measured_seconds),
                value_authority=DurationValueAuthority.MEASURED,
            ),
        )
        bridge = build_renderer_bridge_from_registered_authority(
            job_id=authority.run_id,
            processed_narration_measurement=measurement,
            renderer_plan=plan,
            accepted_inputs=tuple(visual_inputs),
        )
        return bridge, audio_path

    def _execute(
        self,
        run_id: str,
        queued_job: RenderJobV1,
        package: RenderPackageV1,
        context: Mapping[str, Any],
        cancel_event: threading.Event,
    ) -> None:
        job_dir = self._artifact_root / f".{queued_job.job_id}.tmp"
        final_path: Path | None = None
        try:
            with self._lock:
                current = self._latest(self._jobs, run_id)
                if current != queued_job:
                    return
                if cancel_event.is_set():
                    self._replace_job(
                        run_id,
                        current,
                        status=RenderJobStatus.CANCELLED,
                        cancellation_reason="Cancelled before renderer execution.",
                        updated_at=_timestamp(self._identity),
                    )
                    return
                current = self._replace_job(
                    run_id,
                    current,
                    status=RenderJobStatus.RUNNING,
                    updated_at=_timestamp(self._identity),
                )
            job_dir.mkdir(parents=True, exist_ok=False)
            bridge, audio_path = self._build_bridge(package, context, job_dir)
            rendered_path = self._renderer(
                bridge,
                job_dir=job_dir,
                narration_artifact_path=audio_path,
                cancellation_requested=cancel_event,
            )
            rendered_path = Path(rendered_path).resolve(strict=True)
            if rendered_path.parent != job_dir.resolve() or not rendered_path.is_file():
                raise ValueError("RENDERER_FAILED")
            if cancel_event.is_set():
                raise ValueError("RENDER_CANCELLED")
            size = rendered_path.stat().st_size
            if size == 0:
                raise ValueError("MP4_ARTIFACT_EMPTY")
            if size > _MAX_MP4_BYTES:
                raise ValueError("MP4_ARTIFACT_TOO_LARGE")
            with rendered_path.open("rb") as stream:
                signature = stream.read(12)
            if len(signature) < 12 or signature[4:8] != b"ftyp":
                raise ValueError("MP4_SIGNATURE_INVALID")
            metadata = Mp4MetadataV1.model_validate(self._probe(rendered_path))
            profile = package.output_profile
            if (
                metadata.width != profile.width
                or metadata.height != profile.height
                or metadata.video_codec != profile.video_codec
                or metadata.audio_codec != profile.audio_codec
            ):
                raise ValueError("MP4_STREAM_POLICY_FAILED")
            expected_ms = package.input_authority.measured_narration_duration_ms
            tolerance = max(500, round(expected_ms * 0.05))
            if abs(metadata.duration_ms - expected_ms) > tolerance:
                raise ValueError("MP4_DURATION_OUT_OF_POLICY")
            number = self._identity
            self._identity += 1
            artifact_id = f"render-artifact-{number:04d}"
            self._artifact_root.mkdir(parents=True, exist_ok=True)
            final_path = self._artifact_root / f"{artifact_id}.mp4"
            if final_path.exists():
                raise ValueError("RENDERER_FAILED")
            os.replace(rendered_path, final_path)
            artifact = RenderArtifactV1(
                artifact_id=artifact_id,
                artifact_revision_id=f"render-artifact-revision-{number:04d}",
                run_id=run_id,
                render_package_id=package.render_package_id,
                render_package_revision_id=package.render_package_revision_id,
                render_package_sha256=package.render_package_sha256,
                job_id=queued_job.job_id,
                mp4_sha256=_stream_sha256(final_path),
                byte_length=final_path.stat().st_size,
                metadata=metadata,
                status="GENERATED_FOR_REVIEW",
                current=True,
                render_artifact_accepted_for_qc=False,
                eligible_for_final_media_qc_review=False,
                created_at=_timestamp(number),
            )
            review = RenderReviewEntryV1(
                review_id=f"render-review-{number:04d}",
                artifact_revision_id=artifact.artifact_revision_id,
                action="GENERATED",
                created_at=_timestamp(number),
            )
            with self._lock:
                current = self._latest(self._jobs, run_id)
                if current is None or current.job_id != queued_job.job_id:
                    final_path.unlink(missing_ok=True)
                    return
                if cancel_event.is_set():
                    final_path.unlink(missing_ok=True)
                    self._replace_job(
                        run_id,
                        current,
                        status=RenderJobStatus.CANCELLED,
                        cancellation_reason=current.cancellation_reason
                        or "Cancellation requested during renderer execution.",
                        updated_at=_timestamp(number),
                    )
                    return
                self._artifacts[run_id] = (*self._artifacts.get(run_id, ()), artifact)
                self._reviews[run_id] = (*self._reviews.get(run_id, ()), review)
                self._artifact_paths[(run_id, artifact_id)] = final_path
                self._replace_job(
                    run_id,
                    current,
                    status=RenderJobStatus.SUCCEEDED_FOR_REVIEW,
                    progress=RenderJobProgressV1(completed_units=1, percent=100),
                    artifact_id=artifact_id,
                    updated_at=_timestamp(number),
                )
        except Exception as exc:
            code = "RENDERER_TIMEOUT" if isinstance(exc, TimeoutError) else str(exc)
            if code not in {
                "RENDER_CANCELLED",
                "RENDER_INPUT_MISSING",
                "RENDER_INPUT_HASH_MISMATCH",
                "RENDERER_TIMEOUT",
                "RENDERER_FAILED",
                "MP4_ARTIFACT_EMPTY",
                "MP4_ARTIFACT_TOO_LARGE",
                "MP4_SIGNATURE_INVALID",
                "MP4_PROBE_FAILED",
                "MP4_STREAM_POLICY_FAILED",
                "MP4_DURATION_OUT_OF_POLICY",
            }:
                code = "RENDERER_FAILED"
            with self._lock:
                current = self._latest(self._jobs, run_id)
                if current is not None and current.job_id == queued_job.job_id:
                    if code == "RENDER_CANCELLED" or cancel_event.is_set():
                        self._replace_job(
                            run_id,
                            current,
                            status=RenderJobStatus.CANCELLED,
                            cancellation_reason=current.cancellation_reason
                            or "Cancellation requested during renderer execution.",
                            updated_at=_timestamp(self._identity),
                        )
                    else:
                        self._replace_job(
                            run_id,
                            current,
                            status=RenderJobStatus.FAILED,
                            failure_code=code,
                            updated_at=_timestamp(self._identity),
                        )
            if final_path is not None:
                final_path.unlink(missing_ok=True)
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

    def job(self, run_id: str, job_id: str) -> dict[str, object] | None:
        selected = next(
            (item for item in reversed(self._jobs.get(run_id, ())) if item.job_id == job_id),
            None,
        )
        return None if selected is None else _detached(selected)

    def cancel(self, run_id: str, job_id: str, payload: object) -> dict[str, object]:
        try:
            request = CancelRenderJobRequestV1.model_validate(payload)
            reason = _safe_note(request.reason)
        except (ValidationError, ValueError):
            return _error("RENDER_CANCELLED", "Cancellation request is malformed.")
        with self._lock:
            current = self._latest(self._jobs, run_id)
            if (
                current is None
                or current.job_id != job_id
                or current.job_attempt_id != request.job_attempt_id
                or current.status
                not in {
                    RenderJobStatus.QUEUED,
                    RenderJobStatus.RUNNING,
                    RenderJobStatus.CANCEL_REQUESTED,
                }
            ):
                return _error("RENDER_CANCELLED", "Render job cannot be cancelled.")
            event = self._cancel_events[(run_id, job_id)]
            event.set()
            updated = self._replace_job(
                run_id,
                current,
                status=RenderJobStatus.CANCEL_REQUESTED,
                cancellation_reason=reason,
                updated_at=_timestamp(self._identity),
            )
        return {
            "schema_version": 1,
            "access": None,
            "approval": None,
            "render_package": None,
            "job": _detached(updated),
            "artifact": None,
            "error": None,
        }

    def artifact(self, run_id: str, artifact_id: str) -> dict[str, object] | None:
        selected = next(
            (
                item
                for item in reversed(self._artifacts.get(run_id, ()))
                if item.artifact_id == artifact_id
            ),
            None,
        )
        return None if selected is None else _detached(selected)

    def video(self, run_id: str, artifact_id: str) -> tuple[bytes, str, str] | None:
        metadata = self.artifact(run_id, artifact_id)
        path = self._artifact_paths.get((run_id, artifact_id))
        if metadata is None or path is None:
            return None
        try:
            content = path.read_bytes()
        except OSError:
            return None
        if (
            len(content) != metadata["byte_length"]
            or hashlib.sha256(content).hexdigest() != metadata["mp4_sha256"]
        ):
            return None
        return content, "video/mp4", str(metadata["mp4_sha256"])

    def review(
        self,
        run_id: str,
        artifact_id: str,
        operation: Literal["accept-for-qc", "reject", "request-rerender"],
        payload: object,
    ) -> dict[str, object]:
        try:
            request = ReviewRenderArtifactRequestV1.model_validate(payload)
            reason = _safe_note(request.reason)
        except (ValidationError, ValueError):
            return _error("RENDER_ARTIFACT_STALE", "Render review request is malformed.")
        current = self._latest(self._artifacts, run_id)
        if (
            current is None
            or current.artifact_id != artifact_id
            or current.artifact_revision_id != request.artifact_revision_id
            or current.mp4_sha256 != request.mp4_sha256
            or not current.current
        ):
            return _error("RENDER_ARTIFACT_STALE", "Render artifact is stale.")
        if operation == "accept-for-qc" and current.render_artifact_accepted_for_qc:
            return _error("RENDER_ARTIFACT_ALREADY_ACCEPTED", "Artifact is already accepted.")
        number = self._identity
        self._identity += 1
        payload_data = current.model_dump(mode="python")
        payload_data.update(
            status={
                "accept-for-qc": "ACCEPTED_FOR_FINAL_MEDIA_QC_REVIEW",
                "reject": "REJECTED",
                "request-rerender": "RERENDER_REQUESTED",
            }[operation],
            render_artifact_accepted_for_qc=operation == "accept-for-qc",
            eligible_for_final_media_qc_review=operation == "accept-for-qc",
        )
        updated = RenderArtifactV1.model_validate(payload_data)
        if operation == "request-rerender":
            job = self._latest(self._jobs, run_id)
            if job is not None and job.current:
                job_payload = job.model_dump(mode="python")
                job_payload["current"] = False
                self._jobs[run_id] = (
                    *self._jobs[run_id][:-1],
                    RenderJobV1.model_validate(job_payload),
                )
        review = RenderReviewEntryV1(
            review_id=f"render-review-{number:04d}",
            artifact_revision_id=current.artifact_revision_id,
            action={
                "accept-for-qc": "ACCEPTED_FOR_QC",
                "reject": "REJECTED",
                "request-rerender": "RERENDER_REQUESTED",
            }[operation],
            purpose=("SEPARATE_FINAL_MEDIA_QC_REVIEW" if operation == "accept-for-qc" else None),
            reason=reason,
            created_at=_timestamp(number),
        )
        self._artifacts[run_id] = (*self._artifacts[run_id], updated)
        self._reviews[run_id] = (*self._reviews.get(run_id, ()), review)
        return {
            "schema_version": 1,
            "access": None,
            "approval": None,
            "render_package": None,
            "job": None,
            "artifact": _detached(updated),
            "error": None,
        }

    def history(self, run_id: str) -> dict[str, object] | None:
        if not any(
            (
                self._approvals.get(run_id),
                self._packages.get(run_id),
                self._jobs.get(run_id),
                self._artifacts.get(run_id),
                self._reviews.get(run_id),
            )
        ):
            return None
        return {
            "schema_version": 1,
            "run_id": run_id,
            "approvals": [_detached(item) for item in self._approvals.get(run_id, ())],
            "render_packages": [_detached(item) for item in self._packages.get(run_id, ())],
            "jobs": [_detached(item) for item in self._jobs.get(run_id, ())],
            "artifacts": [_detached(item) for item in self._artifacts.get(run_id, ())],
            "reviews": [_detached(item) for item in self._reviews.get(run_id, ())],
            "process_local": True,
        }


__all__ = [
    "ApproveRendererStageRequestV1",
    "CancelRenderJobRequestV1",
    "CreateRenderPackageRequestV1",
    "LocalAuthorizedRenderer",
    "Mp4MetadataV1",
    "RenderArtifactV1",
    "RenderInputAuthorityV1",
    "RenderJobProgressV1",
    "RenderJobStatus",
    "RenderJobV1",
    "RenderPackageV1",
    "RenderReviewEntryV1",
    "RendererConfigurationV1",
    "RendererExecutor",
    "RendererOutputProfileV1",
    "RendererStageAccessV1",
    "RendererStageApprovalV1",
    "RendererStageStore",
    "ReviewRenderArtifactRequestV1",
    "StartRenderJobRequestV1",
    "probe_mp4_artifact",
]
