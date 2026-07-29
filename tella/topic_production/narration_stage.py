"""Process-local narration/TTS artifact authority bound to a sealed package."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import threading
from typing import Any, Callable, Literal, Mapping

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

from tella.tts.audio_probe import probe_single_audio_stream_duration
from tella.tts.kiraap import (
    KIRAAP_IMPLEMENTATION_VERSION,
    KIRAAP_MODEL_DISPLAY_NAME,
    KIRAAP_MODEL_ID,
    KIRAAP_PROVIDER_ID,
    KiraAPTTSProvider,
)
from tella.tts.providers import TTSProvider, get_tts_provider

_SHA256 = r"^[0-9a-f]{64}$"
_IDENTITY = r"^[a-z0-9][a-z0-9._-]{0,127}$"
_MAX_AUDIO_BYTES = 20 * 1024 * 1024
_MAX_AUDIO_DURATION_MS = 10 * 60 * 1000


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @field_validator("schema_version", mode="before", check_fields=False)
    @classmethod
    def exact_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("schema_version must be exactly integer 1")
        return value


class _RendererLocks(_Contract):
    full_render_enabled: Literal[False] = False
    timeline_execution_authority: Literal[False] = False
    renderer_execution_authority: Literal[False] = False
    render_authority: Literal[False] = False
    video_render_authority: Literal[False] = False
    subtitle_generation_capability: Literal[False] = False
    media_muxing_capability: Literal[False] = False
    output_creation_capability: Literal[False] = False
    execution_job_creation_capability: Literal[False] = False
    final_media_capability: Literal[False] = False


class NarrationProviderConfigurationV1(_Contract):
    schema_version: Literal[1] = 1
    provider_configured: StrictBool
    provider_id: Literal["gemini", "kiraap-tts"]
    provider_display_name: Literal["Gemini TTS", "KiraAP TTS"]
    provider_implementation_version: Literal[
        "tella.tts.providers.GeminiTTSProvider.v1",
        "tella.tts.kiraap.KiraAPTTSProvider.v1",
    ]
    model_id: Literal["gemini-3.1-flash-tts-preview"]
    model_display_name: Literal["Gemini 3.1 Flash TTS Preview"]
    voice_id: Literal["Callirrhoe", "Kore"]
    voice_display_name: Literal["Callirrhoe", "Kore"]
    language: Literal["vi-VN"] = "vi-VN"
    style_profile_id: Literal[
        "gentle_female_soft_slow_no_whisper",
        "kiraap_kore_default",
    ]
    style_profile_version: Literal["1"] = "1"
    audio_format: Literal["audio/wav"] = "audio/wav"
    audio_validation_policy_version: Literal["narration_audio_validation_v1"] = (
        "narration_audio_validation_v1"
    )

    @model_validator(mode="after")
    def provider_contract(self) -> NarrationProviderConfigurationV1:
        expected = (
            (
                "Gemini TTS",
                "tella.tts.providers.GeminiTTSProvider.v1",
                "Callirrhoe",
                "gentle_female_soft_slow_no_whisper",
            )
            if self.provider_id == "gemini"
            else (
                "KiraAP TTS",
                KIRAAP_IMPLEMENTATION_VERSION,
                "Kore",
                "kiraap_kore_default",
            )
        )
        actual = (
            self.provider_display_name,
            self.provider_implementation_version,
            self.voice_id,
            self.style_profile_id,
        )
        if actual != expected or self.voice_display_name != self.voice_id:
            raise ValueError("narration provider configuration is inconsistent")
        return self


def _provider_configuration(
    provider: TTSProvider,
    configured: bool,
) -> NarrationProviderConfigurationV1:
    if provider.provider_name == KIRAAP_PROVIDER_ID:
        return NarrationProviderConfigurationV1(
            provider_configured=configured,
            provider_id=KIRAAP_PROVIDER_ID,
            provider_display_name="KiraAP TTS",
            provider_implementation_version=KIRAAP_IMPLEMENTATION_VERSION,
            model_id=KIRAAP_MODEL_ID,
            model_display_name=KIRAAP_MODEL_DISPLAY_NAME,
            voice_id="Kore",
            voice_display_name="Kore",
            style_profile_id="kiraap_kore_default",
        )
    return NarrationProviderConfigurationV1(
        provider_configured=configured,
        provider_id="gemini",
        provider_display_name="Gemini TTS",
        provider_implementation_version="tella.tts.providers.GeminiTTSProvider.v1",
        model_id="gemini-3.1-flash-tts-preview",
        model_display_name="Gemini 3.1 Flash TTS Preview",
        voice_id="Callirrhoe",
        voice_display_name="Callirrhoe",
        style_profile_id="gentle_female_soft_slow_no_whisper",
    )


class NarrationSourceV1(_Contract):
    schema_version: Literal[1] = 1
    narration_text: str = Field(min_length=1)
    narration_source_sha256: str = Field(pattern=_SHA256)
    character_count: StrictInt = Field(gt=0)
    utf8_byte_count: StrictInt = Field(gt=0)
    editable: Literal[False] = False

    @model_validator(mode="after")
    def exact_source(self) -> NarrationSourceV1:
        encoded = self.narration_text.encode("utf-8")
        if (
            hashlib.sha256(encoded).hexdigest() != self.narration_source_sha256
            or len(self.narration_text) != self.character_count
            or len(encoded) != self.utf8_byte_count
        ):
            raise ValueError("narration source identity is inconsistent")
        return self


class NarrationStageApprovalV1(_RendererLocks):
    schema_version: Literal[1] = 1
    approval_id: str = Field(pattern=r"^narration-stage-approval-[0-9]{4}$")
    approval_revision_id: str = Field(pattern=r"^narration-stage-approval-revision-[0-9]{4}$")
    run_id: str = Field(pattern=_IDENTITY)
    execution_package_id: str = Field(pattern=_IDENTITY)
    execution_package_revision_id: str = Field(pattern=_IDENTITY)
    package_source_authority_sha256: str = Field(pattern=_SHA256)
    narration_source_sha256: str = Field(pattern=_SHA256)
    accepted_timeline_revision_id: str = Field(pattern=_IDENTITY)
    effective_timeline_duration_ms: StrictInt = Field(gt=0)
    provider_id: str = Field(pattern=_IDENTITY)
    provider_implementation_version: str
    model_id: str
    voice_id: str
    language: str
    style_profile_id: str = Field(pattern=_IDENTITY)
    style_profile_version: str
    audio_format: Literal["audio/wav"]
    audio_validation_policy_version: str = Field(pattern=_IDENTITY)
    purpose: Literal["NARRATION_TTS_ARTIFACT_GENERATION"]
    narration_stage_source_authority_sha256: str = Field(pattern=_SHA256)
    current: StrictBool
    note: str | None = Field(default=None, max_length=500)
    created_at: str
    process_local: Literal[True] = True


class NarrationDurationComparisonV1(_Contract):
    schema_version: Literal[1] = 1
    measured_audio_duration_ms: StrictInt = Field(gt=0)
    accepted_timeline_duration_ms: StrictInt = Field(gt=0)
    duration_delta_ms: StrictInt
    duration_delta_ratio: float
    duration_alignment_status: Literal["WITHIN_POLICY", "OUTSIDE_POLICY"]
    timeline_realignment_required: StrictBool
    duration_policy_reason_code: Literal[
        "NARRATION_DURATION_WITHIN_TEN_PERCENT",
        "NARRATION_DURATION_REQUIRES_TIMELINE_REALIGNMENT",
    ]

    @model_validator(mode="after")
    def derived(self) -> NarrationDurationComparisonV1:
        delta = self.measured_audio_duration_ms - self.accepted_timeline_duration_ms
        ratio = delta / self.accepted_timeline_duration_ms
        within = abs(ratio) <= 0.10
        if (
            self.duration_delta_ms != delta
            or not math.isclose(self.duration_delta_ratio, ratio, rel_tol=0, abs_tol=1e-12)
            or (self.duration_alignment_status == "WITHIN_POLICY") != within
            or self.timeline_realignment_required == within
        ):
            raise ValueError("narration duration comparison is inconsistent")
        return self


class NarrationGenerationAttemptV1(_Contract):
    schema_version: Literal[1] = 1
    attempt_id: str = Field(pattern=r"^tts-generation-attempt-[0-9]{4}$")
    run_id: str = Field(pattern=_IDENTITY)
    request_sha256: str = Field(pattern=_SHA256)
    approval_revision_id: str = Field(pattern=_IDENTITY)
    status: Literal["SUCCEEDED", "FAILED"]
    failure_code: str | None = None
    retryable: StrictBool
    created_at: str

    @model_validator(mode="after")
    def failure_consistency(self) -> NarrationGenerationAttemptV1:
        if (self.status == "FAILED") != (self.failure_code is not None):
            raise ValueError("generation-attempt failure summary is inconsistent")
        return self


class NarrationAudioArtifactV1(_RendererLocks):
    schema_version: Literal[1] = 1
    artifact_id: str = Field(pattern=r"^narration-audio-artifact-[0-9]{4}$")
    artifact_revision_id: str = Field(pattern=r"^narration-audio-revision-[0-9]{4}$")
    run_id: str = Field(pattern=_IDENTITY)
    execution_package_id: str = Field(pattern=_IDENTITY)
    execution_package_revision_id: str = Field(pattern=_IDENTITY)
    package_source_authority_sha256: str = Field(pattern=_SHA256)
    narration_stage_approval_id: str = Field(pattern=_IDENTITY)
    narration_stage_approval_revision_id: str = Field(pattern=_IDENTITY)
    narration_source_sha256: str = Field(pattern=_SHA256)
    tts_request_sha256: str = Field(pattern=_SHA256)
    provider_id: str
    provider_implementation_version: str
    model_id: str
    voice_id: str
    language: str
    style_profile_id: str
    style_profile_version: str
    mime_type: Literal["audio/wav"]
    extension: Literal["wav"]
    audio_sha256: str = Field(pattern=_SHA256)
    byte_length: StrictInt = Field(gt=0, le=_MAX_AUDIO_BYTES)
    measured_duration_ms: StrictInt = Field(gt=0, le=_MAX_AUDIO_DURATION_MS)
    container: Literal["wav"]
    codec: Literal["pcm"]
    sample_rate_hz: StrictInt | None = Field(default=None, gt=0)
    channels: StrictInt | None = Field(default=None, gt=0)
    validation_policy_version: Literal["narration_audio_validation_v1"]
    artifact_contract_version: Literal["narration_audio_artifact_v1"]
    status: Literal[
        "GENERATED_FOR_REVIEW",
        "ACCEPTED_FOR_RENDERER_STAGE_REVIEW",
        "REJECTED",
        "REVISION_REQUESTED",
        "SUPERSEDED",
    ]
    current: StrictBool
    narration_audio_artifact_accepted: StrictBool
    eligible_for_renderer_stage_review: StrictBool
    duration_comparison: NarrationDurationComparisonV1
    superseded_reason: str | None = None
    created_at: str
    process_local: Literal[True] = True

    @model_validator(mode="after")
    def authority_consistency(self) -> NarrationAudioArtifactV1:
        accepted = self.status == "ACCEPTED_FOR_RENDERER_STAGE_REVIEW" and self.current
        if (
            self.narration_audio_artifact_accepted != accepted
            or self.eligible_for_renderer_stage_review != accepted
            or (self.status == "SUPERSEDED") != (self.superseded_reason is not None)
        ):
            raise ValueError("narration artifact authority is inconsistent")
        return self


class NarrationAudioReviewEntryV1(_Contract):
    schema_version: Literal[1] = 1
    review_id: str = Field(pattern=r"^narration-audio-review-[0-9]{4}$")
    artifact_revision_id: str = Field(pattern=_IDENTITY)
    action: Literal["GENERATED", "ACCEPTED", "REJECTED", "REGENERATION_REQUESTED"]
    purpose: Literal["SEPARATE_RENDERER_STAGE_REVIEW"] | None = None
    reason: str | None = Field(default=None, max_length=500)
    created_at: str


class NarrationStageAccessV1(_RendererLocks):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=_IDENTITY)
    blocker_codes: tuple[str, ...]
    narration_stage_review_capability: StrictBool
    narration_stage_approval_capability: StrictBool
    narration_generation_capability: StrictBool
    tts_capability: StrictBool
    audio_generation_capability: StrictBool
    audio_measurement_capability: StrictBool
    audio_artifact_creation_capability: StrictBool
    audio_artifact_review_capability: StrictBool
    eligible_for_renderer_stage_review: StrictBool
    execution_package_id: str | None = Field(default=None, pattern=_IDENTITY)
    execution_package_revision_id: str | None = Field(default=None, pattern=_IDENTITY)
    package_source_authority_sha256: str | None = Field(default=None, pattern=_SHA256)
    narration_source: NarrationSourceV1 | None
    provider_configuration: NarrationProviderConfigurationV1
    approval: NarrationStageApprovalV1 | None
    artifact: NarrationAudioArtifactV1 | None
    generation_in_progress: StrictBool
    attempt_count: StrictInt = Field(ge=0)
    artifact_history_count: StrictInt = Field(ge=0)
    review_history_count: StrictInt = Field(ge=0)
    process_local: Literal[True] = True
    restart_warning: Literal[
        "Host restart clears narration-stage authority; stored bytes are not rebound."
    ] = "Host restart clears narration-stage authority; stored bytes are not rebound."

    @model_validator(mode="after")
    def capability_consistency(self) -> NarrationStageAccessV1:
        package_bound = (
            self.execution_package_id is not None
            and self.execution_package_revision_id is not None
            and self.package_source_authority_sha256 is not None
            and self.narration_source is not None
        )
        if (
            self.narration_stage_review_capability != package_bound
            or self.narration_stage_approval_capability != package_bound
        ):
            raise ValueError("narration-stage review capability is inconsistent")
        generation = (
            package_bound
            and self.provider_configuration.provider_configured
            and self.approval is not None
            and self.approval.current
        )
        if any(
            value != generation
            for value in (
                self.narration_generation_capability,
                self.tts_capability,
                self.audio_generation_capability,
                self.audio_measurement_capability,
                self.audio_artifact_creation_capability,
            )
        ):
            raise ValueError("narration generation capability is inconsistent")
        review = (
            self.artifact is not None
            and self.artifact.current
            and self.artifact.status == "GENERATED_FOR_REVIEW"
        )
        if (
            self.audio_artifact_review_capability != review
            or self.eligible_for_renderer_stage_review
            != (self.artifact is not None and self.artifact.eligible_for_renderer_stage_review)
            or (self.generation_in_progress and not generation)
        ):
            raise ValueError("narration artifact capability is inconsistent")
        return self


class ApproveNarrationStageRequestV1(_Contract):
    schema_version: Literal[1]
    execution_package_id: str = Field(pattern=_IDENTITY)
    execution_package_revision_id: str = Field(pattern=_IDENTITY)
    package_source_authority_sha256: str = Field(pattern=_SHA256)
    explicit_external_provider_acknowledgement: Literal[True]
    explicit_confirmation: Literal[True]
    note: str | None = Field(default=None, max_length=500)


class GenerateNarrationRequestV1(_Contract):
    schema_version: Literal[1]
    execution_package_id: str = Field(pattern=_IDENTITY)
    execution_package_revision_id: str = Field(pattern=_IDENTITY)
    package_source_authority_sha256: str = Field(pattern=_SHA256)
    approval_id: str = Field(pattern=_IDENTITY)
    approval_revision_id: str = Field(pattern=_IDENTITY)
    explicit_confirmation: Literal[True]


class ReviewNarrationArtifactRequestV1(_Contract):
    schema_version: Literal[1]
    artifact_revision_id: str = Field(pattern=_IDENTITY)
    audio_sha256: str = Field(pattern=_SHA256)
    explicit_confirmation: Literal[True]
    reason: str | None = Field(default=None, min_length=1, max_length=500)


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _detached(value: BaseModel) -> dict[str, object]:
    return json.loads(value.model_dump_json())


def _timestamp(number: int) -> str:
    return f"2026-01-03T00:{number // 60:02d}:{number % 60:02d}Z"


def _error(code: str, message: str, *, retryable: bool = False) -> dict[str, object]:
    return {
        "schema_version": 1,
        "access": None,
        "approval": None,
        "attempt": None,
        "artifact": None,
        "error": {
            "schema_version": 1,
            "code": code,
            "message": message,
            "retryable": retryable,
        },
    }


def _safe_note(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        not value.strip()
        or "://" in value
        or "\\" in value
        or "<" in value
        or "`" in value
        or "$(" in value
        or any(ord(character) < 32 and character not in "\n\t" for character in value)
    ):
        raise ValueError("narration-stage note must be presentation-safe")
    return value


for _request_model in (
    ApproveNarrationStageRequestV1,
    ReviewNarrationArtifactRequestV1,
):
    _request_model.model_rebuild()


class NarrationStageStore:
    """Immutable metadata authority around one explicit TTS request at a time."""

    def __init__(
        self,
        *,
        provider: TTSProvider | None = None,
        artifact_root: Path | None = None,
        duration_probe: Callable[[Path], float] = probe_single_audio_stream_duration,
        provider_configured: bool | None = None,
    ) -> None:
        if provider is None:
            selected_provider = (os.environ.get("TELLA_TTS_PROVIDER") or "gemini").strip().lower()
            provider = get_tts_provider(selected_provider)
        self._provider = provider
        env_configured = (
            self._provider.configured
            if isinstance(self._provider, KiraAPTTSProvider)
            else bool(
                (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
            )
        )
        self._provider_configured = (
            env_configured if provider_configured is None else provider_configured
        )
        self._provider_configuration = _provider_configuration(
            self._provider,
            self._provider_configured,
        )
        self._artifact_root = Path(artifact_root or Path("out") / "narration-stage")
        self._duration_probe = duration_probe
        self._approvals: dict[str, tuple[NarrationStageApprovalV1, ...]] = {}
        self._attempts: dict[str, tuple[NarrationGenerationAttemptV1, ...]] = {}
        self._artifacts: dict[str, tuple[NarrationAudioArtifactV1, ...]] = {}
        self._reviews: dict[str, tuple[NarrationAudioReviewEntryV1, ...]] = {}
        self._artifact_paths: dict[tuple[str, str], Path] = {}
        self._in_progress: set[str] = set()
        self._state_lock = threading.Lock()
        self._identity = 1

    def provider_configuration(self, configured: bool) -> NarrationProviderConfigurationV1:
        if configured == self._provider_configuration.provider_configured:
            return self._provider_configuration
        payload = self._provider_configuration.model_dump(mode="python")
        payload["provider_configured"] = configured
        return NarrationProviderConfigurationV1.model_validate(payload)

    def _context(
        self,
        *,
        run: Mapping[str, Any] | None,
        execution_access: Mapping[str, Any] | None,
    ) -> tuple[tuple[str, ...], dict[str, Any] | None]:
        if run is None:
            return ("UNKNOWN_RUN",), None
        blockers: list[str] = []
        package = None if execution_access is None else execution_access.get("package")
        if not isinstance(package, Mapping):
            return ("EXECUTION_PACKAGE_REQUIRED",), None
        if (
            package.get("status") != "SEALED_FOR_NARRATION_STAGE_REVIEW"
            or package.get("current") is not True
            or package.get("eligible_for_narration_stage_review") is not True
        ):
            blockers.append("EXECUTION_PACKAGE_STALE")
        authority = package.get("authority")
        story = run.get("story_plan")
        narration = None if not isinstance(story, Mapping) else story.get("narration_text")
        if not isinstance(authority, Mapping) or not isinstance(narration, str) or not narration:
            blockers.append("NARRATION_SOURCE_UNAVAILABLE")
            return tuple(blockers), None
        narration_sha = hashlib.sha256(narration.encode("utf-8")).hexdigest()
        if narration_sha != authority.get("narration_source_sha256"):
            blockers.append("NARRATION_SOURCE_MISMATCH")
        lock_keys = (
            "full_render_enabled",
            "timeline_execution_authority",
            "renderer_execution_authority",
            "render_authority",
            "video_render_authority",
            "subtitle_generation_capability",
            "media_muxing_capability",
            "output_creation_capability",
            "execution_job_creation_capability",
            "final_media_capability",
        )
        if any(package.get(key) is not False for key in lock_keys):
            blockers.append("OPERATIONAL_CAPABILITY_UNEXPECTEDLY_ENABLED")
        source = NarrationSourceV1(
            narration_text=narration,
            narration_source_sha256=narration_sha,
            character_count=len(narration),
            utf8_byte_count=len(narration.encode("utf-8")),
        )
        source_projection = {
            "run_id": run["run_id"],
            "execution_package_id": package["package_id"],
            "execution_package_revision_id": package["package_revision_id"],
            "package_source_authority_sha256": package["package_source_authority_sha256"],
            "narration_source_sha256": narration_sha,
            "accepted_timeline_revision_id": authority["accepted_timeline_revision_id"],
            "effective_timeline_duration_ms": authority["effective_timeline_duration_ms"],
            "provider": self.provider_configuration(self._provider_configured).model_dump(
                mode="json"
            ),
        }
        return tuple(dict.fromkeys(blockers)), {
            "package": package,
            "authority": authority,
            "source": source,
            "source_sha256": _canonical_sha256(source_projection),
        }

    def _latest_approval(self, run_id: str) -> NarrationStageApprovalV1 | None:
        values = self._approvals.get(run_id, ())
        return values[-1] if values else None

    def _latest_artifact(self, run_id: str) -> NarrationAudioArtifactV1 | None:
        values = self._artifacts.get(run_id, ())
        return values[-1] if values else None

    def _supersede_if_stale(self, run_id: str, context: Mapping[str, Any] | None) -> None:
        approval = self._latest_approval(run_id)
        expected = None if context is None else context["source_sha256"]
        if (
            approval is not None
            and approval.current
            and (expected is None or approval.narration_stage_source_authority_sha256 != expected)
        ):
            payload = approval.model_dump(mode="python")
            payload["current"] = False
            self._approvals[run_id] = (
                *self._approvals[run_id][:-1],
                NarrationStageApprovalV1.model_validate(payload),
            )
        artifact = self._latest_artifact(run_id)
        if (
            artifact is not None
            and artifact.current
            and (
                context is None
                or artifact.package_source_authority_sha256
                != context["package"]["package_source_authority_sha256"]
                or artifact.narration_source_sha256 != context["source"].narration_source_sha256
            )
        ):
            payload = artifact.model_dump(mode="python")
            payload.update(
                status="SUPERSEDED",
                current=False,
                narration_audio_artifact_accepted=False,
                eligible_for_renderer_stage_review=False,
                superseded_reason="BOUND_NARRATION_AUTHORITY_CHANGED",
            )
            self._artifacts[run_id] = (
                *self._artifacts[run_id][:-1],
                NarrationAudioArtifactV1.model_validate(payload),
            )

    def access(
        self,
        *,
        run: Mapping[str, Any] | None,
        execution_access: Mapping[str, Any] | None,
    ) -> dict[str, object] | None:
        if run is None:
            return None
        run_id = str(run["run_id"])
        blockers, context = self._context(run=run, execution_access=execution_access)
        self._supersede_if_stale(run_id, None if blockers else context)
        approval = self._latest_approval(run_id)
        artifact = self._latest_artifact(run_id)
        valid = not blockers and context is not None
        projected_context = context if valid else None
        configured = self._provider_configured
        generation = valid and configured and approval is not None and approval.current
        if valid and not configured:
            blockers = (*blockers, "TTS_PROVIDER_NOT_CONFIGURED")
        return _detached(
            NarrationStageAccessV1(
                run_id=run_id,
                blocker_codes=blockers,
                narration_stage_review_capability=valid,
                narration_stage_approval_capability=valid,
                narration_generation_capability=generation,
                tts_capability=generation,
                audio_generation_capability=generation,
                audio_measurement_capability=generation,
                audio_artifact_creation_capability=generation,
                audio_artifact_review_capability=(
                    artifact is not None
                    and artifact.current
                    and artifact.status == "GENERATED_FOR_REVIEW"
                ),
                eligible_for_renderer_stage_review=(
                    artifact is not None and artifact.eligible_for_renderer_stage_review
                ),
                execution_package_id=(
                    None
                    if projected_context is None
                    else projected_context["package"]["package_id"]
                ),
                execution_package_revision_id=(
                    None
                    if projected_context is None
                    else projected_context["package"]["package_revision_id"]
                ),
                package_source_authority_sha256=(
                    None
                    if projected_context is None
                    else projected_context["package"]["package_source_authority_sha256"]
                ),
                narration_source=(
                    None if projected_context is None else projected_context["source"]
                ),
                provider_configuration=self.provider_configuration(configured),
                approval=approval,
                artifact=artifact,
                generation_in_progress=run_id in self._in_progress,
                attempt_count=len(self._attempts.get(run_id, ())),
                artifact_history_count=len(self._artifacts.get(run_id, ())),
                review_history_count=len(self._reviews.get(run_id, ())),
            )
        )

    def approve(
        self,
        *,
        run: Mapping[str, Any] | None,
        execution_access: Mapping[str, Any] | None,
        payload: object,
    ) -> dict[str, object]:
        try:
            request = ApproveNarrationStageRequestV1.model_validate(payload)
            note = _safe_note(request.note)
        except (ValidationError, ValueError):
            return _error(
                "INVALID_NARRATION_STAGE_APPROVAL",
                "Narration-stage approval request is malformed.",
            )
        blockers, context = self._context(run=run, execution_access=execution_access)
        if blockers or context is None or run is None:
            return _error(
                "EXECUTION_PACKAGE_STALE",
                "A current sealed execution package is required.",
            )
        package = context["package"]
        if (
            request.execution_package_id != package["package_id"]
            or request.execution_package_revision_id != package["package_revision_id"]
            or request.package_source_authority_sha256 != package["package_source_authority_sha256"]
        ):
            return _error(
                "EXECUTION_PACKAGE_STALE",
                "Execution-package identity changed before narration approval.",
            )
        run_id = str(run["run_id"])
        current = self._latest_approval(run_id)
        if current is not None and current.current:
            return _error(
                "NARRATION_STAGE_ALREADY_APPROVED",
                "The current package already has narration-stage approval.",
            )
        config = self.provider_configuration(self._provider_configured)
        authority = context["authority"]
        number = self._identity
        approval = NarrationStageApprovalV1(
            approval_id=f"narration-stage-approval-{number:04d}",
            approval_revision_id=f"narration-stage-approval-revision-{number:04d}",
            run_id=run_id,
            execution_package_id=package["package_id"],
            execution_package_revision_id=package["package_revision_id"],
            package_source_authority_sha256=package["package_source_authority_sha256"],
            narration_source_sha256=context["source"].narration_source_sha256,
            accepted_timeline_revision_id=authority["accepted_timeline_revision_id"],
            effective_timeline_duration_ms=authority["effective_timeline_duration_ms"],
            provider_id=config.provider_id,
            provider_implementation_version=config.provider_implementation_version,
            model_id=config.model_id,
            voice_id=config.voice_id,
            language=config.language,
            style_profile_id=config.style_profile_id,
            style_profile_version=config.style_profile_version,
            audio_format=config.audio_format,
            audio_validation_policy_version=config.audio_validation_policy_version,
            purpose="NARRATION_TTS_ARTIFACT_GENERATION",
            narration_stage_source_authority_sha256=context["source_sha256"],
            current=True,
            note=note,
            created_at=_timestamp(number),
        )
        self._identity += 1
        self._approvals[run_id] = (*self._approvals.get(run_id, ()), approval)
        return {
            "schema_version": 1,
            "access": None,
            "approval": _detached(approval),
            "attempt": None,
            "artifact": None,
            "error": None,
        }

    @staticmethod
    def _request_sha256(
        package: Mapping[str, Any],
        approval: NarrationStageApprovalV1,
    ) -> str:
        return _canonical_sha256(
            {
                "execution_package_id": package["package_id"],
                "execution_package_revision_id": package["package_revision_id"],
                "package_source_authority_sha256": package["package_source_authority_sha256"],
                "narration_source_sha256": approval.narration_source_sha256,
                "narration_stage_approval_id": approval.approval_id,
                "narration_stage_approval_revision_id": approval.approval_revision_id,
                "provider_id": approval.provider_id,
                "provider_implementation_version": approval.provider_implementation_version,
                "model_id": approval.model_id,
                "voice_id": approval.voice_id,
                "language": approval.language,
                "style_profile_id": approval.style_profile_id,
                "style_profile_version": approval.style_profile_version,
                "requested_audio_format": approval.audio_format,
                "validation_policy_version": approval.audio_validation_policy_version,
            }
        )

    def generate(
        self,
        *,
        run: Mapping[str, Any] | None,
        execution_access: Mapping[str, Any] | None,
        payload: object,
    ) -> dict[str, object]:
        try:
            request = GenerateNarrationRequestV1.model_validate(payload)
        except ValidationError:
            return _error(
                "INVALID_NARRATION_GENERATION_REQUEST",
                "Narration generation request is malformed.",
            )
        blockers, context = self._context(run=run, execution_access=execution_access)
        if blockers or context is None or run is None:
            return _error("EXECUTION_PACKAGE_STALE", "Execution package is stale.")
        run_id = str(run["run_id"])
        package = context["package"]
        approval = self._latest_approval(run_id)
        if (
            approval is None
            or not approval.current
            or request.approval_id != approval.approval_id
            or request.approval_revision_id != approval.approval_revision_id
            or request.execution_package_id != package["package_id"]
            or request.execution_package_revision_id != package["package_revision_id"]
            or request.package_source_authority_sha256 != package["package_source_authority_sha256"]
            or approval.narration_stage_source_authority_sha256 != context["source_sha256"]
        ):
            return _error(
                "NARRATION_STAGE_APPROVAL_STALE",
                "Current narration-stage approval is required.",
            )
        if not self._provider_configured:
            return _error(
                "TTS_PROVIDER_NOT_CONFIGURED",
                "The configured production TTS provider is unavailable.",
            )
        request_sha = self._request_sha256(package, approval)
        with self._state_lock:
            if run_id in self._in_progress:
                return _error(
                    "TTS_GENERATION_ALREADY_IN_PROGRESS",
                    "Narration generation is already in progress.",
                    retryable=True,
                )
            if any(
                item.request_sha256 == request_sha and item.status == "SUCCEEDED"
                for item in self._attempts.get(run_id, ())
            ):
                return _error(
                    "TTS_REQUEST_ALREADY_COMPLETED",
                    "This exact narration request already completed.",
                )
            self._in_progress.add(run_id)
        number = self._identity
        attempt_id = f"tts-generation-attempt-{number:04d}"
        self._identity += 1
        temp_path: Path | None = None
        try:
            self._artifact_root.mkdir(parents=True, exist_ok=True)
            artifact_id = f"narration-audio-artifact-{number:04d}"
            temp_path = self._artifact_root / f".{artifact_id}.tmp.wav"
            final_path = self._artifact_root / f"{artifact_id}.wav"
            if temp_path.exists():
                temp_path.unlink()
            result = asyncio.run(
                self._provider.synthesize(
                    context["source"].narration_text,
                    temp_path,
                    voice=approval.voice_id,
                    language=approval.language,
                    speed=0.85,
                    codec="wav",
                    sample_rate=24000,
                    metadata={
                        "model": approval.model_id,
                        "style": "Speak gently, softly, slowly, naturally; do not whisper.",
                    },
                )
            )
            if Path(result.audio_path).resolve() != temp_path.resolve():
                raise ValueError("provider returned an unexpected artifact path")
            if (
                result.provider != approval.provider_id
                or result.voice != approval.voice_id
                or result.language != approval.language
                or result.metadata.get("codec") != "wav"
            ):
                raise ValueError("TTS_RESPONSE_INVALID")
            content = temp_path.read_bytes()
            if not content:
                raise ValueError("AUDIO_ARTIFACT_EMPTY")
            if len(content) > _MAX_AUDIO_BYTES:
                raise ValueError("AUDIO_ARTIFACT_TOO_LARGE")
            if len(content) < 12 or content[:4] != b"RIFF" or content[8:12] != b"WAVE":
                raise ValueError("AUDIO_SIGNATURE_INVALID")
            measured_seconds = self._duration_probe(temp_path)
            if not math.isfinite(measured_seconds) or measured_seconds <= 0:
                raise ValueError("AUDIO_MEASUREMENT_FAILED")
            measured_ms = round(measured_seconds * 1000)
            if measured_ms <= 0 or measured_ms > _MAX_AUDIO_DURATION_MS:
                raise ValueError("AUDIO_MEASUREMENT_FAILED")
            audio_sha = hashlib.sha256(content).hexdigest()
            timeline_ms = approval.effective_timeline_duration_ms
            delta = measured_ms - timeline_ms
            ratio = delta / timeline_ms
            within = abs(ratio) <= 0.10
            comparison = NarrationDurationComparisonV1(
                measured_audio_duration_ms=measured_ms,
                accepted_timeline_duration_ms=timeline_ms,
                duration_delta_ms=delta,
                duration_delta_ratio=ratio,
                duration_alignment_status="WITHIN_POLICY" if within else "OUTSIDE_POLICY",
                timeline_realignment_required=not within,
                duration_policy_reason_code=(
                    "NARRATION_DURATION_WITHIN_TEN_PERCENT"
                    if within
                    else "NARRATION_DURATION_REQUIRES_TIMELINE_REALIGNMENT"
                ),
            )
            os.replace(temp_path, final_path)
            temp_path = None
            artifact = NarrationAudioArtifactV1(
                artifact_id=artifact_id,
                artifact_revision_id=f"narration-audio-revision-{number:04d}",
                run_id=run_id,
                execution_package_id=package["package_id"],
                execution_package_revision_id=package["package_revision_id"],
                package_source_authority_sha256=package["package_source_authority_sha256"],
                narration_stage_approval_id=approval.approval_id,
                narration_stage_approval_revision_id=approval.approval_revision_id,
                narration_source_sha256=approval.narration_source_sha256,
                tts_request_sha256=request_sha,
                provider_id=approval.provider_id,
                provider_implementation_version=approval.provider_implementation_version,
                model_id=approval.model_id,
                voice_id=approval.voice_id,
                language=approval.language,
                style_profile_id=approval.style_profile_id,
                style_profile_version=approval.style_profile_version,
                mime_type="audio/wav",
                extension="wav",
                audio_sha256=audio_sha,
                byte_length=len(content),
                measured_duration_ms=measured_ms,
                container="wav",
                codec="pcm",
                validation_policy_version="narration_audio_validation_v1",
                artifact_contract_version="narration_audio_artifact_v1",
                status="GENERATED_FOR_REVIEW",
                current=True,
                narration_audio_artifact_accepted=False,
                eligible_for_renderer_stage_review=False,
                duration_comparison=comparison,
                created_at=_timestamp(number),
            )
            attempt = NarrationGenerationAttemptV1(
                attempt_id=attempt_id,
                run_id=run_id,
                request_sha256=request_sha,
                approval_revision_id=approval.approval_revision_id,
                status="SUCCEEDED",
                retryable=False,
                created_at=_timestamp(number),
            )
            review = NarrationAudioReviewEntryV1(
                review_id=f"narration-audio-review-{number:04d}",
                artifact_revision_id=artifact.artifact_revision_id,
                action="GENERATED",
                created_at=_timestamp(number),
            )
            self._attempts[run_id] = (*self._attempts.get(run_id, ()), attempt)
            self._artifacts[run_id] = (*self._artifacts.get(run_id, ()), artifact)
            self._reviews[run_id] = (*self._reviews.get(run_id, ()), review)
            self._artifact_paths[(run_id, artifact_id)] = final_path
            return {
                "schema_version": 1,
                "access": None,
                "approval": None,
                "attempt": _detached(attempt),
                "artifact": _detached(artifact),
                "error": None,
            }
        except Exception as exc:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            detail = str(exc)
            typed_provider_codes = {
                "TTS_PROVIDER_NOT_CONFIGURED",
                "TTS_PROVIDER_AUTHENTICATION_FAILED",
                "TTS_PROVIDER_RATE_LIMITED",
                "TTS_PROVIDER_TIMEOUT",
                "TTS_PROVIDER_UNAVAILABLE",
                "TTS_PROVIDER_FAILED",
                "TTS_RESPONSE_INVALID",
                "AUDIO_MIME_UNSUPPORTED",
                "AUDIO_SIGNATURE_INVALID",
                "AUDIO_ARTIFACT_EMPTY",
                "AUDIO_ARTIFACT_TOO_LARGE",
            }
            code = (
                detail
                if detail in {*typed_provider_codes, "AUDIO_MEASUREMENT_FAILED"}
                else (
                    "TTS_PROVIDER_TIMEOUT"
                    if isinstance(exc, (TimeoutError, asyncio.TimeoutError))
                    else "TTS_PROVIDER_RATE_LIMITED"
                    if "429" in detail
                    else "TTS_PROVIDER_FAILED"
                )
            )
            attempt = NarrationGenerationAttemptV1(
                attempt_id=attempt_id,
                run_id=run_id,
                request_sha256=request_sha,
                approval_revision_id=approval.approval_revision_id,
                status="FAILED",
                failure_code=code,
                retryable=code
                in {
                    "TTS_PROVIDER_TIMEOUT",
                    "TTS_PROVIDER_RATE_LIMITED",
                    "TTS_PROVIDER_UNAVAILABLE",
                    "TTS_PROVIDER_FAILED",
                },
                created_at=_timestamp(number),
            )
            self._attempts[run_id] = (*self._attempts.get(run_id, ()), attempt)
            return {
                **_error(
                    code,
                    "Narration audio generation failed without creating an artifact.",
                    retryable=attempt.retryable,
                ),
                "attempt": _detached(attempt),
            }
        finally:
            with self._state_lock:
                self._in_progress.discard(run_id)

    def review(
        self,
        run_id: str,
        artifact_id: str,
        operation: Literal["accept", "reject", "request-regeneration"],
        payload: object,
    ) -> dict[str, object]:
        try:
            request = ReviewNarrationArtifactRequestV1.model_validate(payload)
            reason = _safe_note(request.reason)
        except (ValidationError, ValueError):
            return _error(
                "INVALID_NARRATION_ARTIFACT_REVIEW",
                "Narration artifact review request is malformed.",
            )
        current = self._latest_artifact(run_id)
        if (
            current is None
            or current.artifact_id != artifact_id
            or current.artifact_revision_id != request.artifact_revision_id
            or current.audio_sha256 != request.audio_sha256
            or not current.current
        ):
            return _error("AUDIO_ARTIFACT_STALE", "Narration artifact is stale.")
        if operation == "accept" and current.duration_comparison.timeline_realignment_required:
            return _error(
                "NARRATION_DURATION_OUT_OF_POLICY",
                "Timeline realignment is required before artifact acceptance.",
            )
        if operation == "accept" and current.narration_audio_artifact_accepted:
            return _error(
                "AUDIO_ARTIFACT_ALREADY_ACCEPTED",
                "Narration artifact is already accepted.",
            )
        number = self._identity
        self._identity += 1
        status = {
            "accept": "ACCEPTED_FOR_RENDERER_STAGE_REVIEW",
            "reject": "REJECTED",
            "request-regeneration": "REVISION_REQUESTED",
        }[operation]
        payload_data = current.model_dump(mode="python")
        payload_data.update(
            status=status,
            narration_audio_artifact_accepted=operation == "accept",
            eligible_for_renderer_stage_review=operation == "accept",
        )
        updated = NarrationAudioArtifactV1.model_validate(payload_data)
        if operation == "request-regeneration":
            approval = self._latest_approval(run_id)
            if approval is not None and approval.current:
                approval_payload = approval.model_dump(mode="python")
                approval_payload["current"] = False
                self._approvals[run_id] = (
                    *self._approvals[run_id][:-1],
                    NarrationStageApprovalV1.model_validate(approval_payload),
                )
        review = NarrationAudioReviewEntryV1(
            review_id=f"narration-audio-review-{number:04d}",
            artifact_revision_id=current.artifact_revision_id,
            action={
                "accept": "ACCEPTED",
                "reject": "REJECTED",
                "request-regeneration": "REGENERATION_REQUESTED",
            }[operation],
            purpose="SEPARATE_RENDERER_STAGE_REVIEW" if operation == "accept" else None,
            reason=reason,
            created_at=_timestamp(number),
        )
        self._artifacts[run_id] = (*self._artifacts[run_id], updated)
        self._reviews[run_id] = (*self._reviews.get(run_id, ()), review)
        return {
            "schema_version": 1,
            "access": None,
            "approval": None,
            "attempt": None,
            "artifact": _detached(updated),
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

    def audio(self, run_id: str, artifact_id: str) -> tuple[bytes, str, str] | None:
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
            or hashlib.sha256(content).hexdigest() != metadata["audio_sha256"]
        ):
            return None
        return content, str(metadata["mime_type"]), str(metadata["audio_sha256"])

    def history(self, run_id: str) -> dict[str, object] | None:
        if not any(
            (
                self._approvals.get(run_id),
                self._attempts.get(run_id),
                self._artifacts.get(run_id),
                self._reviews.get(run_id),
            )
        ):
            return None
        return {
            "schema_version": 1,
            "run_id": run_id,
            "approvals": [_detached(item) for item in self._approvals.get(run_id, ())],
            "attempts": [_detached(item) for item in self._attempts.get(run_id, ())],
            "artifacts": [_detached(item) for item in self._artifacts.get(run_id, ())],
            "reviews": [_detached(item) for item in self._reviews.get(run_id, ())],
            "process_local": True,
        }


__all__ = [
    "ApproveNarrationStageRequestV1",
    "GenerateNarrationRequestV1",
    "NarrationAudioArtifactV1",
    "NarrationAudioReviewEntryV1",
    "NarrationDurationComparisonV1",
    "NarrationGenerationAttemptV1",
    "NarrationProviderConfigurationV1",
    "NarrationSourceV1",
    "NarrationStageAccessV1",
    "NarrationStageApprovalV1",
    "NarrationStageStore",
    "ReviewNarrationArtifactRequestV1",
]
