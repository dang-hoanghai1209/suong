"""Process-local visual-candidate authority for accepted PLAN_ONLY scenes."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import re
import tempfile
from typing import Any, Literal, Protocol

from PIL import Image, UnidentifiedImageError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
    model_validator,
)

from tella.media.image_provider_contract import ImageProviderCapabilities
from tella.visual_generation.models import (
    Canvas,
    CharacterArchetype,
    ReferencePack,
    SceneBrief,
    StyleBible,
)
from tella.visual_generation.prompt_builder import (
    build_generation_request,
    request_hash,
)


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_PROVIDER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_MIME_BY_FORMAT = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
_EXTENSION_BY_MIME = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
_MAX_ARTIFACT_BYTES = 25 * 1024 * 1024


class VisualCandidateProvider(Protocol):
    """Approved still-image provider boundary used by the process-local store."""

    provider_name: str

    def capabilities(self) -> ImageProviderCapabilities: ...

    def is_configured(self) -> bool: ...

    async def generate_text_image(
        self,
        prompt: str,
        negative_prompt: str,
        aspect: str,
        seed: int | None,
        out_path: Path,
        metadata: dict[str, Any] | None = None,
    ) -> Any: ...


class _VisualContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )


class VisualCandidateStatus(StrEnum):
    REVIEW_PENDING = "REVIEW_PENDING"
    ACCEPTED_FOR_COMPOSITION_PLANNING = "ACCEPTED_FOR_COMPOSITION_PLANNING"
    REJECTED = "REJECTED"
    REVISION_REQUESTED = "REVISION_REQUESTED"
    INVALID = "INVALID"
    SUPERSEDED = "SUPERSEDED"


class VisualRequestStatus(StrEnum):
    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILED = "FAILED"


class VisualReviewAction(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    REVISION_REQUESTED = "REVISION_REQUESTED"
    SUPERSEDED = "SUPERSEDED"


class VisualRejectionReason(StrEnum):
    CHARACTER_IDENTITY_MISMATCH = "CHARACTER_IDENTITY_MISMATCH"
    POSE_MISMATCH = "POSE_MISMATCH"
    ACTION_MISMATCH = "ACTION_MISMATCH"
    ENVIRONMENT_MISMATCH = "ENVIRONMENT_MISMATCH"
    OBJECT_MISMATCH = "OBJECT_MISMATCH"
    COMPOSITION_MISMATCH = "COMPOSITION_MISMATCH"
    STYLE_MISMATCH = "STYLE_MISMATCH"
    CONTINUITY_MISMATCH = "CONTINUITY_MISMATCH"
    TEXT_OR_WATERMARK_PRESENT = "TEXT_OR_WATERMARK_PRESENT"
    INVALID_ANATOMY = "INVALID_ANATOMY"
    LOW_IMAGE_QUALITY = "LOW_IMAGE_QUALITY"
    DUPLICATE_CANDIDATE = "DUPLICATE_CANDIDATE"
    OTHER_BOUNDED_NOTE = "OTHER_BOUNDED_NOTE"


class VisualSourceCoverageV1(_VisualContract):
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(gt=0)

    @model_validator(mode="after")
    def require_interval(self) -> VisualSourceCoverageV1:
        if self.end <= self.start:
            raise ValueError("visual source coverage must be a positive interval")
        return self


class VisualProviderCapabilityV1(_VisualContract):
    available: StrictBool
    capability_label: str = Field(min_length=1, max_length=80)
    supports_9_16: Literal[True] = True
    maximum_candidate_count: Literal[4] = 4
    external_provider: Literal[True] = True
    reason_code: str | None = Field(default=None, max_length=100)


class VisualTechnicalValidationV1(_VisualContract):
    passed: StrictBool
    mime_valid: StrictBool
    dimensions_valid: StrictBool
    non_empty: StrictBool
    animation_free: StrictBool
    duplicate_free: StrictBool
    blocker_codes: tuple[str, ...] = ()


class VisualQCProjectionV1(_VisualContract):
    blocker_codes: tuple[str, ...] = ()
    warning_codes: tuple[str, ...] = ()
    information_codes: tuple[str, ...] = ("HUMAN_VISUAL_REVIEW_REQUIRED",)


class VisualReviewEntryV1(_VisualContract):
    review_id: str = Field(pattern=r"^visual-review-[0-9]{4}$")
    review_number: StrictInt = Field(ge=1)
    candidate_id: str = Field(pattern=r"^visual-candidate-[0-9]{4}-[0-9]{2}$")
    candidate_revision_id: str = Field(pattern=r"^visual-candidate-revision-[0-9]{4}$")
    action: VisualReviewAction
    reason_code: str
    note: str | None = Field(default=None, max_length=500)
    created_at: str


class VisualCandidateV1(_VisualContract):
    schema_version: Literal[1] = 1
    candidate_id: str = Field(pattern=r"^visual-candidate-[0-9]{4}-[0-9]{2}$")
    candidate_revision_id: str = Field(pattern=r"^visual-candidate-revision-[0-9]{4}$")
    candidate_revision_number: StrictInt = Field(ge=1)
    request_id: str = Field(pattern=r"^visual-request-[0-9]{4}$")
    attempt_id: str = Field(pattern=r"^visual-attempt-[0-9]{4}$")
    run_id: str
    story_revision_id: str
    scene_plan_collection_revision_id: str
    scene_id: str
    scene_revision_id: str
    semantic_beat_id: str
    source_coverage: VisualSourceCoverageV1
    status: VisualCandidateStatus
    artifact_url: str
    provider_capability_label: str
    model_label: str
    mime_type: Literal["image/png", "image/jpeg", "image/webp"]
    extension: Literal[".png", ".jpg", ".webp"]
    width: StrictInt = Field(ge=64, le=1920)
    height: StrictInt = Field(ge=64, le=1920)
    sha256: str = Field(pattern=_SHA256_PATTERN)
    logical_request_hash: str = Field(pattern=_SHA256_PATTERN)
    provider_request_hash: str = Field(pattern=_SHA256_PATTERN)
    technical_validation: VisualTechnicalValidationV1
    visual_qc: VisualQCProjectionV1
    review_history: tuple[VisualReviewEntryV1, ...] = ()
    accepted_for_composition_planning: StrictBool = False
    superseded_reason: str | None = None
    created_at: str

    @model_validator(mode="after")
    def validate_candidate_state(self) -> VisualCandidateV1:
        accepted = self.status is VisualCandidateStatus.ACCEPTED_FOR_COMPOSITION_PLANNING
        if self.accepted_for_composition_planning is not accepted:
            raise ValueError("candidate acceptance summary must match status")
        if accepted and (not self.technical_validation.passed or self.visual_qc.blocker_codes):
            raise ValueError("invalid or blocked candidate cannot be accepted")
        if self.extension != _EXTENSION_BY_MIME[self.mime_type]:
            raise ValueError("candidate MIME type and extension must match")
        return self


class VisualGenerationAttemptV1(_VisualContract):
    attempt_id: str = Field(pattern=r"^visual-attempt-[0-9]{4}$")
    attempt_number: StrictInt = Field(ge=1)
    request_id: str = Field(pattern=r"^visual-request-[0-9]{4}$")
    candidate_ids: tuple[str, ...]
    requested_candidate_count: StrictInt = Field(ge=1, le=4)
    returned_candidate_count: StrictInt = Field(ge=0, le=4)
    invalid_candidate_count: StrictInt = Field(ge=0, le=4)
    provider_capability_label: str
    model_label: str
    status: VisualRequestStatus
    reason_code: str
    created_at: str


class VisualGenerationRequestV1(_VisualContract):
    request_id: str = Field(pattern=r"^visual-request-[0-9]{4}$")
    request_number: StrictInt = Field(ge=1)
    visual_collection_revision_id: str
    candidate_count: StrictInt = Field(ge=1, le=4)
    aspect_ratio: Literal["9:16"]
    composition_emphasis: str | None = Field(default=None, max_length=300)
    correction_dimensions: tuple[VisualRejectionReason, ...] = ()
    note: str | None = Field(default=None, max_length=500)
    prompt_projection: str = Field(min_length=1, max_length=12_000)
    logical_request_hash: str = Field(pattern=_SHA256_PATTERN)
    attempt_ids: tuple[str, ...]
    status: VisualRequestStatus
    created_at: str


class VisualCandidateCollectionV1(_VisualContract):
    schema_version: Literal[1] = 1
    run_id: str
    story_revision_id: str
    scene_plan_collection_revision_id: str
    scene_id: str
    scene_revision_id: str
    semantic_beat_id: str
    source_coverage: VisualSourceCoverageV1
    visual_authority_version: Literal["visual_candidate_authority_v1"] = (
        "visual_candidate_authority_v1"
    )
    visual_collection_id: str = Field(pattern=r"^visual-collection-[0-9]{4}$")
    visual_collection_revision_id: str = Field(pattern=r"^visual-collection-revision-[0-9]{4}$")
    visual_collection_revision_number: StrictInt = Field(ge=1)
    current_request_id: str | None = None
    current_accepted_candidate_id: str | None = None
    requests: tuple[VisualGenerationRequestV1, ...] = ()
    attempts: tuple[VisualGenerationAttemptV1, ...] = ()
    candidates: tuple[VisualCandidateV1, ...] = ()
    render_authority: Literal[False] = False
    video_render_authority: Literal[False] = False
    final_media_capability: Literal[False] = False
    narration_generation_capability: Literal[False] = False
    tts_capability: Literal[False] = False
    process_local: Literal[True] = True
    created_at: str

    @model_validator(mode="after")
    def validate_collection(self) -> VisualCandidateCollectionV1:
        candidate_ids = [item.candidate_id for item in self.candidates]
        request_ids = [item.request_id for item in self.requests]
        attempt_ids = [item.attempt_id for item in self.attempts]
        if any(
            len(values) != len(set(values)) for values in (candidate_ids, request_ids, attempt_ids)
        ):
            raise ValueError("visual collection identities must be unique")
        if [item.request_number for item in self.requests] != list(
            range(1, len(self.requests) + 1)
        ) or [item.attempt_number for item in self.attempts] != list(
            range(1, len(self.attempts) + 1)
        ):
            raise ValueError("visual request and attempt ordering must be canonical")
        request_id_set = set(request_ids)
        attempt_id_set = set(attempt_ids)
        if any(
            item.request_id not in request_id_set
            or item.returned_candidate_count + item.invalid_candidate_count
            != item.requested_candidate_count
            or len(item.candidate_ids) != item.returned_candidate_count
            for item in self.attempts
        ) or any(
            item.request_id not in request_id_set or item.attempt_id not in attempt_id_set
            for item in self.candidates
        ):
            raise ValueError("visual request, attempt, and candidate identities must bind")
        reviews = sorted(
            (review for item in self.candidates for review in item.review_history),
            key=lambda item: item.review_number,
        )
        if (
            len({item.review_id for item in reviews}) != len(reviews)
            or [item.review_number for item in reviews] != list(range(1, len(reviews) + 1))
            or any(
                tuple(review.review_number for review in item.review_history)
                != tuple(sorted(review.review_number for review in item.review_history))
                or (
                    item.review_history
                    and item.review_history[-1].candidate_revision_id != item.candidate_revision_id
                )
                for item in self.candidates
            )
        ):
            raise ValueError("visual review history must be unique and canonically ordered")
        accepted = [
            item.candidate_id for item in self.candidates if item.accepted_for_composition_planning
        ]
        expected_acceptance = (
            []
            if self.current_accepted_candidate_id is None
            else [self.current_accepted_candidate_id]
        )
        if accepted != expected_acceptance:
            raise ValueError("visual collection must have at most one current acceptance")
        if self.current_request_id is not None and self.current_request_id not in request_ids:
            raise ValueError("current visual request must belong to collection")
        if self.current_request_id is not None and (
            not self.requests or self.current_request_id != self.requests[-1].request_id
        ):
            raise ValueError("current visual request must be the latest request")
        return self


class VisualCandidateAccessV1(_VisualContract):
    schema_version: Literal[1] = 1
    run_id: str
    scene_id: str
    request_authorized: StrictBool
    review_authorized: StrictBool
    blocker_codes: tuple[str, ...]
    current_story_revision_id: str | None
    accepted_story_revision_id: str | None
    current_scene_plan_collection_revision_id: str | None
    current_scene_revision_id: str | None
    current_visual_collection_revision_id: str | None
    current_request_id: str | None
    current_accepted_candidate_id: str | None
    provider_capability: VisualProviderCapabilityV1
    collection: VisualCandidateCollectionV1 | None
    render_authority: Literal[False] = False
    video_render_authority: Literal[False] = False
    final_media_capability: Literal[False] = False
    narration_generation_capability: Literal[False] = False
    tts_capability: Literal[False] = False
    process_local: Literal[True] = True

    @model_validator(mode="after")
    def validate_access(self) -> VisualCandidateAccessV1:
        authorized = not self.blocker_codes and self.provider_capability.available
        if self.request_authorized is not authorized:
            raise ValueError("visual request authorization must match blockers")
        if self.review_authorized is not (authorized and self.collection is not None):
            raise ValueError("visual review authorization must require current collection")
        if authorized and self.current_story_revision_id != self.accepted_story_revision_id:
            raise ValueError("visual authorization requires current accepted StoryPlan")
        if self.collection is not None and (
            self.collection.run_id != self.run_id
            or self.collection.scene_id != self.scene_id
            or self.collection.story_revision_id != self.current_story_revision_id
            or self.collection.scene_plan_collection_revision_id
            != self.current_scene_plan_collection_revision_id
            or self.collection.scene_revision_id != self.current_scene_revision_id
            or self.collection.visual_collection_revision_id
            != self.current_visual_collection_revision_id
            or self.collection.current_request_id != self.current_request_id
            or self.collection.current_accepted_candidate_id != self.current_accepted_candidate_id
        ):
            raise ValueError("visual access summaries must match current collection")
        return self


class GenerateVisualCandidatesRequestV1(_VisualContract):
    schema_version: Literal[1]
    visual_collection_revision_id: str
    scene_plan_collection_revision_id: str
    scene_revision_id: str
    candidate_count: StrictInt = Field(default=2, ge=1, le=4)
    aspect_ratio: Literal["9:16"] = "9:16"
    composition_emphasis: str | None = Field(default=None, max_length=300)
    correction_dimensions: tuple[VisualRejectionReason, ...] = ()
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="before")
    @classmethod
    def exact_schema(cls, value: Any) -> Any:
        if isinstance(value, Mapping) and type(value.get("schema_version")) is not int:
            raise ValueError("schema_version must be exact integer 1")
        return value

    @model_validator(mode="after")
    def validate_guidance(self) -> GenerateVisualCandidatesRequestV1:
        if len(set(self.correction_dimensions)) != len(self.correction_dimensions):
            raise ValueError("correction dimensions must be unique")
        for text in (self.composition_emphasis, self.note):
            if text is not None and (
                not text.strip()
                or "://" in text
                or any(ord(character) < 32 and character not in "\n\t" for character in text)
            ):
                raise ValueError("visual guidance must be bounded presentation-safe text")
        return self


class VisualCandidateMutationRequestV1(_VisualContract):
    schema_version: Literal[1]
    visual_collection_revision_id: str
    scene_plan_collection_revision_id: str
    scene_revision_id: str
    candidate_revision_id: str
    reason_code: VisualRejectionReason
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="before")
    @classmethod
    def exact_schema(cls, value: Any) -> Any:
        if isinstance(value, Mapping) and type(value.get("schema_version")) is not int:
            raise ValueError("schema_version must be exact integer 1")
        return value

    @model_validator(mode="after")
    def safe_note(self) -> VisualCandidateMutationRequestV1:
        if self.note is not None and (
            not self.note.strip()
            or "://" in self.note
            or any(ord(character) < 32 and character not in "\n\t" for character in self.note)
        ):
            raise ValueError("visual review note must be bounded presentation-safe text")
        return self


class VisualCandidateErrorV1(_VisualContract):
    schema_version: Literal[1] = 1
    code: str
    message: str
    retryable: StrictBool = False


class VisualCandidateOperationResultV1(_VisualContract):
    schema_version: Literal[1] = 1
    access: VisualCandidateAccessV1 | None = None
    error: VisualCandidateErrorV1 | None = None

    @model_validator(mode="after")
    def exactly_one_result(self) -> VisualCandidateOperationResultV1:
        if (self.access is None) == (self.error is None):
            raise ValueError("visual operation requires access or error")
        return self


class VisualArtifact:
    """Internal-only registered artifact response."""

    def __init__(self, *, content: bytes, mime_type: str, sha256: str) -> None:
        self.content = content
        self.mime_type = mime_type
        self.sha256 = sha256


def _detached(model: BaseModel) -> dict[str, object]:
    return json.loads(model.model_dump_json())


def _timestamp(number: int) -> str:
    return (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=number)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _error(code: str, message: str) -> dict[str, object]:
    return _detached(
        VisualCandidateOperationResultV1(error=VisualCandidateErrorV1(code=code, message=message))
    )


def _safe_provider_label(provider: VisualCandidateProvider | None) -> str:
    if provider is None:
        return "approved-still-image-provider"
    label = str(getattr(provider, "provider_name", "approved-still-image-provider"))
    return label if _SAFE_PROVIDER_PATTERN.fullmatch(label) else "approved-still-image-provider"


def _canonical_hash(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _style_bible() -> StyleBible:
    return StyleBible(
        style_id="tella-plan-only-visual-candidate-v1",
        canvas=Canvas(width=576, height=1024, aspect_ratio="9:16"),
        background=["restrained coherent scene environment"],
        drawing=["soft hand-drawn editorial illustration"],
        palette=["muted warm palette"],
        composition=["clear vertical focal hierarchy", "generous negative space"],
        lighting=["soft readable light"],
        negative_constraints=[
            "no text",
            "no logo",
            "no watermark",
            "no UI",
            "no duplicate character",
            "no unsafe anatomy",
        ],
        character_archetypes={
            "female": CharacterArchetype(
                character_id="female",
                identity_locks=[
                    "same recurring adult woman",
                    "consistent face, hair, outfit, and body proportions",
                ],
            )
        },
    )


def _generation_request(
    scene: Mapping[str, Any],
    *,
    candidate_index: int,
    attempt: int,
    composition_emphasis: str | None,
    corrections: tuple[VisualRejectionReason, ...],
    note: str | None,
) -> Any:
    environment = [str(scene.get("environment", ""))]
    environment.extend(f"required object: {item}" for item in scene.get("objects", ()))
    composition = [str(scene.get("composition_guidance", ""))]
    if composition_emphasis:
        composition.append(f"bounded composition emphasis: {composition_emphasis}")
    if corrections:
        composition.append("bounded corrections: " + ", ".join(item.value for item in corrections))
    if note:
        composition.append(f"review note: {note}")
    brief = SceneBrief(
        scene_id=str(scene["scene_id"]),
        scene_type="accepted_scene_plan",
        narrative_text=str(scene["narration_segment"]),
        narrative_meaning=str(scene["objective"]),
        characters=["female"],
        emotion=[str(scene["emotional_intent"])],
        action=[str(scene["character_action"]) or "restrained readable action"],
        environment_cues=[item for item in environment if item],
        symbolic_elements=[],
        composition=[item for item in composition if item],
        negative_constraints=[],
        reference_roles=["prompt_identity_continuity"],
    )
    return build_generation_request(
        brief,
        _style_bible(),
        ReferencePack(scene_id=brief.scene_id, references=[]),
        candidate_index=candidate_index,
        attempt=attempt,
        seed=10_000 + candidate_index * 101 + attempt,
    )


def _validate_artifact(
    path: Path,
    *,
    expected_path: Path,
    artifact_root: Path,
    prior_hashes: set[str],
) -> tuple[bytes, str, str, int, int, VisualTechnicalValidationV1]:
    resolved = path.resolve()
    expected_resolved = expected_path.resolve()
    if (
        resolved != expected_resolved
        or resolved.parent != artifact_root.resolve()
        or not path.is_file()
    ):
        raise ValueError("provider artifact path did not match registered output")
    size = path.stat().st_size
    if size <= 0:
        raise ValueError("provider returned an empty image artifact")
    if size > _MAX_ARTIFACT_BYTES:
        raise ValueError("provider returned an oversized image artifact")
    content = path.read_bytes()
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image_format = str(image.format or "").upper()
            width, height = image.size
            animated = bool(getattr(image, "is_animated", False))
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError("provider returned an invalid image artifact") from error
    mime = _MIME_BY_FORMAT.get(image_format)
    if mime is None or expected_path.suffix.lower() != _EXTENSION_BY_MIME[mime]:
        raise ValueError("provider artifact MIME type and extension mismatch")
    if animated:
        raise ValueError("animated visual candidates are unsupported")
    if not (64 <= width <= 1920 and 64 <= height <= 1920):
        raise ValueError("provider artifact dimensions are outside bounded limits")
    if abs(width / height - 9 / 16) > 0.01:
        raise ValueError("provider artifact aspect ratio is invalid")
    digest = hashlib.sha256(content).hexdigest()
    if digest in prior_hashes:
        raise ValueError("provider returned duplicate candidate bytes")
    return (
        content,
        mime,
        digest,
        width,
        height,
        VisualTechnicalValidationV1(
            passed=True,
            mime_valid=True,
            dimensions_valid=True,
            non_empty=True,
            animation_free=True,
            duplicate_free=True,
        ),
    )


class VisualCandidateStore:
    """Immutable candidate collections bound to current upstream scene authority."""

    def __init__(
        self,
        *,
        provider: VisualCandidateProvider | None,
        artifact_root: Path | None = None,
    ) -> None:
        self._provider = provider
        self._configured_root = artifact_root
        self._root: Path | None = None
        self._collections: dict[tuple[str, str], tuple[VisualCandidateCollectionV1, ...]] = {}
        self._artifact_paths: dict[str, Path] = {}
        self._pending_revisions: dict[
            tuple[str, str],
            tuple[VisualRejectionReason, str | None, str | None],
        ] = {}
        self._next_collection_id = 1
        self._next_collection_revision_id = 1
        self._next_request_id = 1
        self._next_attempt_id = 1
        self._next_review_id = 1
        self._next_candidate_revision_id = 1

    def _artifact_root(self) -> Path:
        if self._root is None:
            self._root = (
                self._configured_root.resolve()
                if self._configured_root is not None
                else Path(tempfile.mkdtemp(prefix="tella-visual-candidates-")).resolve()
            )
            self._root.mkdir(parents=True, exist_ok=True)
        return self._root

    def _provider_capability(self) -> VisualProviderCapabilityV1:
        label = _safe_provider_label(self._provider)
        if self._provider is None:
            return VisualProviderCapabilityV1(
                available=False,
                capability_label=label,
                reason_code="VISUAL_PROVIDER_NOT_CONFIGURED",
            )
        try:
            capabilities = self._provider.capabilities()
            available = bool(
                self._provider.is_configured()
                and capabilities.supports_text_to_image
                and capabilities.supports_seed
            )
        except Exception:
            available = False
        return VisualProviderCapabilityV1(
            available=available,
            capability_label=label,
            reason_code=None if available else "VISUAL_PROVIDER_UNAVAILABLE",
        )

    @staticmethod
    def _entry_blockers(
        *,
        run: Mapping[str, Any] | None,
        review: Mapping[str, Any] | None,
        scene_access: Mapping[str, Any] | None,
        scene_id: str,
    ) -> tuple[tuple[str, ...], Mapping[str, Any] | None, Mapping[str, Any] | None]:
        blockers: list[str] = []
        if run is None:
            return ("UNKNOWN_RUN",), None, None
        if run.get("status") != "PLANNED":
            blockers.append("RUN_NOT_PLANNED")
        if review is None or review.get("review_status") != "ACCEPTED_FOR_SCENE_PLANNING":
            blockers.append("STORYPLAN_NOT_ACCEPTED")
        elif review.get("accepted_revision_id") != review.get("current_revision_id"):
            blockers.append("STALE_ACCEPTED_STORYPLAN")
        collection = None if scene_access is None else scene_access.get("collection")
        if collection is None:
            blockers.append("SCENE_PLAN_UNAVAILABLE")
            return tuple(dict.fromkeys(blockers)), None, None
        current_revision = None if review is None else review.get("current_revision_id")
        if (
            collection.get("source_story_revision_id") != current_revision
            or collection.get("accepted_story_revision_id") != current_revision
        ):
            blockers.append("STALE_SCENE_PLAN_COLLECTION")
        if collection.get("source_coverage_valid") is not True:
            blockers.append("INVALID_SOURCE_COVERAGE")
        if collection.get("duration_valid") is not True:
            blockers.append("INVALID_SCENE_DURATION")
        selected = next(
            (item for item in collection.get("scenes", ()) if item.get("scene_id") == scene_id),
            None,
        )
        if selected is None:
            blockers.append("UNKNOWN_SCENE")
            return tuple(dict.fromkeys(blockers)), collection, None
        if selected.get("status") != "ACCEPTED_FOR_VISUAL_PLANNING":
            blockers.append("SCENE_NOT_ACCEPTED_FOR_VISUAL_PLANNING")
        if selected.get("warning_codes"):
            blockers.append("SCENE_WARNINGS_PRESENT")
        if selected.get("blocker_codes"):
            blockers.append("SCENE_BLOCKERS_PRESENT")
        continuity = selected.get("continuity")
        if not isinstance(continuity, Mapping) or (
            continuity.get("identity_continuity_required") is not True
            or continuity.get("visual_identity_verified") is not False
        ):
            blockers.append("INVALID_SCENE_CONTINUITY")
        return tuple(dict.fromkeys(blockers)), collection, selected

    def _current(self, run_id: str, scene_id: str) -> VisualCandidateCollectionV1 | None:
        history = self._collections.get((run_id, scene_id), ())
        return history[-1] if history else None

    def _derive(
        self,
        *,
        run_id: str,
        review: Mapping[str, Any],
        scene_collection: Mapping[str, Any],
        scene: Mapping[str, Any],
    ) -> VisualCandidateCollectionV1:
        history = self._collections.get((run_id, str(scene["scene_id"])), ())
        number = 1
        collection_id = self._next_collection_id
        collection_revision_id = self._next_collection_revision_id
        self._next_collection_id += 1
        self._next_collection_revision_id += 1
        collection = VisualCandidateCollectionV1(
            run_id=run_id,
            story_revision_id=str(review["current_revision_id"]),
            scene_plan_collection_revision_id=str(scene_collection["collection_revision_id"]),
            scene_id=str(scene["scene_id"]),
            scene_revision_id=str(scene["scene_revision_id"]),
            semantic_beat_id=str(scene["source_beat_id"]),
            source_coverage=VisualSourceCoverageV1(
                start=scene["source_coverage"]["start"],
                end=scene["source_coverage"]["end"],
            ),
            visual_collection_id=f"visual-collection-{collection_id:04d}",
            visual_collection_revision_id=(
                f"visual-collection-revision-{collection_revision_id:04d}"
            ),
            visual_collection_revision_number=number,
            created_at=_timestamp(number),
        )
        self._collections[(run_id, str(scene["scene_id"]))] = (*history, collection)
        return collection

    def _supersede_for_upstream_change(
        self,
        current: VisualCandidateCollectionV1,
    ) -> None:
        review_number = sum(len(item.review_history) for item in current.candidates)
        candidates: list[VisualCandidateV1] = []
        for item in current.candidates:
            if item.status is VisualCandidateStatus.SUPERSEDED:
                candidates.append(item)
                continue
            review_number += 1
            review_identity = self._next_review_id
            revision_identity = self._next_candidate_revision_id
            self._next_review_id += 1
            self._next_candidate_revision_id += 1
            revision_id = f"visual-candidate-revision-{revision_identity:04d}"
            candidates.append(
                VisualCandidateV1.model_validate(
                    {
                        **item.model_dump(mode="python"),
                        "status": VisualCandidateStatus.SUPERSEDED,
                        "accepted_for_composition_planning": False,
                        "superseded_reason": "UPSTREAM_SCENE_AUTHORITY_CHANGED",
                        "candidate_revision_number": item.candidate_revision_number + 1,
                        "candidate_revision_id": revision_id,
                        "review_history": (
                            *item.review_history,
                            VisualReviewEntryV1(
                                review_id=f"visual-review-{review_identity:04d}",
                                review_number=review_number,
                                candidate_id=item.candidate_id,
                                candidate_revision_id=revision_id,
                                action=VisualReviewAction.SUPERSEDED,
                                reason_code="UPSTREAM_SCENE_AUTHORITY_CHANGED",
                                created_at=_timestamp(review_number),
                            ),
                        ),
                    }
                )
            )
        if candidates:
            self._commit(
                current,
                candidates=tuple(candidates),
                current_accepted_candidate_id=None,
            )
        self._pending_revisions.pop((current.run_id, current.scene_id), None)

    def _access_model(
        self,
        *,
        run: Mapping[str, Any] | None,
        review: Mapping[str, Any] | None,
        scene_access: Mapping[str, Any] | None,
        scene_id: str,
        derive: bool,
    ) -> VisualCandidateAccessV1:
        run_id = "" if run is None else str(run.get("run_id", ""))
        blockers, scene_collection, scene = self._entry_blockers(
            run=run,
            review=review,
            scene_access=scene_access,
            scene_id=scene_id,
        )
        capability = self._provider_capability()
        current = self._current(run_id, scene_id)
        if not blockers and scene_collection is not None and scene is not None:
            source = (
                str(review["current_revision_id"]),
                str(scene_collection["collection_revision_id"]),
                str(scene["scene_revision_id"]),
            )
            bound = (
                None
                if current is None
                else (
                    current.story_revision_id,
                    current.scene_plan_collection_revision_id,
                    current.scene_revision_id,
                )
            )
            if bound != source:
                if derive:
                    if current is not None:
                        self._supersede_for_upstream_change(current)
                    current = self._derive(
                        run_id=run_id,
                        review=review,
                        scene_collection=scene_collection,
                        scene=scene,
                    )
                else:
                    current = None
        else:
            if (
                derive
                and current is not None
                and any(
                    item.status is not VisualCandidateStatus.SUPERSEDED
                    for item in current.candidates
                )
            ):
                self._supersede_for_upstream_change(current)
            current = None
        effective = blockers + (() if capability.available else (capability.reason_code or "",))
        return VisualCandidateAccessV1(
            run_id=run_id,
            scene_id=scene_id,
            request_authorized=not effective,
            review_authorized=not effective and current is not None,
            blocker_codes=tuple(item for item in effective if item),
            current_story_revision_id=(
                None if review is None else str(review.get("current_revision_id") or "") or None
            ),
            accepted_story_revision_id=(
                None if review is None else str(review.get("accepted_revision_id") or "") or None
            ),
            current_scene_plan_collection_revision_id=(
                None
                if scene_collection is None
                else str(scene_collection.get("collection_revision_id") or "") or None
            ),
            current_scene_revision_id=(
                None if scene is None else str(scene.get("scene_revision_id") or "") or None
            ),
            current_visual_collection_revision_id=(
                None if current is None else current.visual_collection_revision_id
            ),
            current_request_id=None if current is None else current.current_request_id,
            current_accepted_candidate_id=(
                None if current is None else current.current_accepted_candidate_id
            ),
            provider_capability=capability,
            collection=current,
        )

    def access(
        self,
        *,
        run: Mapping[str, Any] | None,
        review: Mapping[str, Any] | None,
        scene_access: Mapping[str, Any] | None,
        scene_id: str,
    ) -> dict[str, object]:
        return _detached(
            self._access_model(
                run=run,
                review=review,
                scene_access=scene_access,
                scene_id=scene_id,
                derive=True,
            )
        )

    def _authorized(
        self,
        *,
        run: Mapping[str, Any] | None,
        review: Mapping[str, Any] | None,
        scene_access: Mapping[str, Any] | None,
        scene_id: str,
        visual_revision_id: str,
        scene_collection_revision_id: str,
        scene_revision_id: str,
    ) -> tuple[VisualCandidateAccessV1, Mapping[str, Any]] | dict[str, object]:
        access = self._access_model(
            run=run,
            review=review,
            scene_access=scene_access,
            scene_id=scene_id,
            derive=False,
        )
        if not access.request_authorized or access.collection is None:
            return _error(
                "VISUAL_CANDIDATE_NOT_AUTHORIZED",
                "Current scene authority does not permit visual candidate mutation.",
            )
        collection = access.collection
        scene_collection = scene_access["collection"]
        selected = next(item for item in scene_collection["scenes"] if item["scene_id"] == scene_id)
        if (
            collection.visual_collection_revision_id != visual_revision_id
            or collection.scene_plan_collection_revision_id != scene_collection_revision_id
            or collection.scene_revision_id != scene_revision_id
            or selected["scene_revision_id"] != scene_revision_id
        ):
            return _error(
                "STALE_VISUAL_CANDIDATE_AUTHORITY",
                "Visual candidate authority changed.",
            )
        return access, selected

    def _commit(
        self,
        current: VisualCandidateCollectionV1,
        **updates: Any,
    ) -> VisualCandidateCollectionV1:
        payload = current.model_dump(mode="python")
        payload.update(updates)
        payload["visual_collection_revision_number"] = current.visual_collection_revision_number + 1
        payload["visual_collection_revision_id"] = (
            f"visual-collection-revision-{self._next_collection_revision_id:04d}"
        )
        self._next_collection_revision_id += 1
        payload["created_at"] = _timestamp(payload["visual_collection_revision_number"])
        committed = VisualCandidateCollectionV1.model_validate(payload)
        key = (current.run_id, current.scene_id)
        self._collections[key] = (*self._collections[key], committed)
        return committed

    def generate(
        self,
        *,
        run: Mapping[str, Any] | None,
        review: Mapping[str, Any] | None,
        scene_access: Mapping[str, Any] | None,
        scene_id: str,
        payload: object,
    ) -> dict[str, object]:
        try:
            request = GenerateVisualCandidatesRequestV1.model_validate(payload)
        except ValidationError:
            return _error(
                "INVALID_VISUAL_GENERATION_REQUEST",
                "The visual candidate request is malformed.",
            )
        authority = self._authorized(
            run=run,
            review=review,
            scene_access=scene_access,
            scene_id=scene_id,
            visual_revision_id=request.visual_collection_revision_id,
            scene_collection_revision_id=request.scene_plan_collection_revision_id,
            scene_revision_id=request.scene_revision_id,
        )
        if isinstance(authority, dict):
            return authority
        access, scene = authority
        current = access.collection
        assert current is not None and self._provider is not None
        pending = self._pending_revisions.get((current.run_id, current.scene_id))
        prior_request = (
            next(
                (
                    item
                    for item in reversed(current.requests)
                    if pending is not None and item.request_id == pending[2]
                ),
                None,
            )
            if pending is not None
            else None
        )
        effective_composition_emphasis = (
            request.composition_emphasis
            if request.composition_emphasis is not None
            else None
            if prior_request is None
            else prior_request.composition_emphasis
        )
        effective_corrections = tuple(
            dict.fromkeys(
                (
                    *((pending[0],) if pending is not None else ()),
                    *request.correction_dimensions,
                )
            )
        )
        effective_note = (
            request.note if request.note is not None else None if pending is None else pending[1]
        )
        request_number = len(current.requests) + 1
        attempt_number = len(current.attempts) + 1
        request_identity_number = self._next_request_id
        attempt_identity_number = self._next_attempt_id
        self._next_request_id += 1
        self._next_attempt_id += 1
        request_id = f"visual-request-{request_identity_number:04d}"
        attempt_id = f"visual-attempt-{attempt_identity_number:04d}"
        built = _generation_request(
            scene,
            candidate_index=1,
            attempt=attempt_number,
            composition_emphasis=effective_composition_emphasis,
            corrections=effective_corrections,
            note=effective_note,
        )
        logical_hash = _canonical_hash(
            {
                "authority": {
                    "run_id": current.run_id,
                    "story_revision_id": current.story_revision_id,
                    "scene_plan_collection_revision_id": (
                        current.scene_plan_collection_revision_id
                    ),
                    "scene_id": current.scene_id,
                    "scene_revision_id": current.scene_revision_id,
                },
                "candidate_count": request.candidate_count,
                "aspect_ratio": request.aspect_ratio,
                "composition_emphasis": effective_composition_emphasis,
                "correction_dimensions": [item.value for item in effective_corrections],
                "note": effective_note,
                "prompt": built.instruction,
                "negative_prompt": built.negative_instruction,
            }
        )
        prior_hashes = {item.sha256 for item in current.candidates}
        candidates: list[VisualCandidateV1] = []
        invalid_count = 0
        failure_codes: list[str] = []
        artifact_root = self._artifact_root()
        for index in range(1, request.candidate_count + 1):
            candidate_id = f"visual-candidate-{request_identity_number:04d}-{index:02d}"
            output_path = (artifact_root / f"{candidate_id}.png").resolve()
            if output_path.parent != artifact_root:
                return _error("ARTIFACT_PATH_INVALID", "Candidate artifact path is invalid.")
            candidate_request = _generation_request(
                scene,
                candidate_index=index,
                attempt=attempt_number,
                composition_emphasis=effective_composition_emphasis,
                corrections=effective_corrections,
                note=effective_note,
            )
            provider_identity = _canonical_hash(
                {
                    "logical_request_hash": logical_hash,
                    "provider_neutral_request_hash": request_hash(candidate_request),
                    "candidate_index": index,
                    "seed": 10_000 + index * 101 + attempt_number,
                    "aspect_ratio": "9:16",
                    "provider_capability": access.provider_capability.capability_label,
                }
            )
            try:
                generated = asyncio.run(
                    self._provider.generate_text_image(
                        prompt=candidate_request.instruction,
                        negative_prompt=candidate_request.negative_instruction,
                        aspect="9:16",
                        seed=10_000 + index * 101 + attempt_number,
                        out_path=output_path,
                        metadata={
                            "logical_request_hash": logical_hash,
                            "provider_request_hash": provider_identity,
                        },
                    )
                )
                actual_path = Path(getattr(generated, "output_path", output_path))
                content, mime, digest, width, height, technical = _validate_artifact(
                    actual_path,
                    expected_path=output_path,
                    artifact_root=artifact_root,
                    prior_hashes=prior_hashes,
                )
                prior_hashes.add(digest)
                self._artifact_paths[candidate_id] = output_path
                candidates.append(
                    VisualCandidateV1(
                        candidate_id=candidate_id,
                        candidate_revision_id=(
                            f"visual-candidate-revision-{self._next_candidate_revision_id:04d}"
                        ),
                        candidate_revision_number=1,
                        request_id=request_id,
                        attempt_id=attempt_id,
                        run_id=current.run_id,
                        story_revision_id=current.story_revision_id,
                        scene_plan_collection_revision_id=(
                            current.scene_plan_collection_revision_id
                        ),
                        scene_id=current.scene_id,
                        scene_revision_id=current.scene_revision_id,
                        semantic_beat_id=current.semantic_beat_id,
                        source_coverage=current.source_coverage,
                        status=VisualCandidateStatus.REVIEW_PENDING,
                        artifact_url=(
                            f"/api/v1/plan-only/runs/{current.run_id}/scene-plan/scenes/"
                            f"{current.scene_id}/visual-candidates/{candidate_id}/artifact"
                        ),
                        provider_capability_label=(access.provider_capability.capability_label),
                        model_label="approved-still-image-model",
                        mime_type=mime,
                        extension=_EXTENSION_BY_MIME[mime],
                        width=width,
                        height=height,
                        sha256=digest,
                        logical_request_hash=logical_hash,
                        provider_request_hash=provider_identity,
                        technical_validation=technical,
                        visual_qc=VisualQCProjectionV1(),
                        created_at=_timestamp(attempt_number),
                    )
                )
                self._next_candidate_revision_id += 1
            except TimeoutError:
                invalid_count += 1
                failure_codes.append("VISUAL_PROVIDER_TIMEOUT")
            except Exception:
                invalid_count += 1
                failure_codes.append("INVALID_PROVIDER_OUTPUT")
        returned = len(candidates)
        status = (
            VisualRequestStatus.SUCCESS
            if returned == request.candidate_count
            else VisualRequestStatus.PARTIAL_SUCCESS
            if returned
            else VisualRequestStatus.FAILED
        )
        reason = {
            VisualRequestStatus.SUCCESS: "VISUAL_CANDIDATES_GENERATED",
            VisualRequestStatus.PARTIAL_SUCCESS: "PARTIAL_CANDIDATE_BATCH",
            VisualRequestStatus.FAILED: (
                "VISUAL_PROVIDER_TIMEOUT"
                if failure_codes
                and all(code == "VISUAL_PROVIDER_TIMEOUT" for code in failure_codes)
                else "NO_VALID_CANDIDATE_RETURNED"
            ),
        }[status]
        generation_request = VisualGenerationRequestV1(
            request_id=request_id,
            request_number=request_number,
            visual_collection_revision_id=current.visual_collection_revision_id,
            candidate_count=request.candidate_count,
            aspect_ratio="9:16",
            composition_emphasis=effective_composition_emphasis,
            correction_dimensions=effective_corrections,
            note=effective_note,
            prompt_projection=built.instruction,
            logical_request_hash=logical_hash,
            attempt_ids=(attempt_id,),
            status=status,
            created_at=_timestamp(request_number),
        )
        attempt = VisualGenerationAttemptV1(
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            request_id=request_id,
            candidate_ids=tuple(item.candidate_id for item in candidates),
            requested_candidate_count=request.candidate_count,
            returned_candidate_count=returned,
            invalid_candidate_count=invalid_count,
            provider_capability_label=access.provider_capability.capability_label,
            model_label="approved-still-image-model",
            status=status,
            reason_code=reason,
            created_at=_timestamp(attempt_number),
        )
        committed = self._commit(
            current,
            current_request_id=request_id,
            requests=(*current.requests, generation_request),
            attempts=(*current.attempts, attempt),
            candidates=(*current.candidates, *candidates),
        )
        self._pending_revisions.pop((current.run_id, current.scene_id), None)
        return _detached(
            VisualCandidateOperationResultV1(
                access=self._access_model(
                    run=run,
                    review=review,
                    scene_access=scene_access,
                    scene_id=scene_id,
                    derive=False,
                ).model_copy(update={"collection": committed})
            )
        )

    def mutate(
        self,
        *,
        run: Mapping[str, Any] | None,
        review: Mapping[str, Any] | None,
        scene_access: Mapping[str, Any] | None,
        scene_id: str,
        candidate_id: str,
        operation: Literal["accept", "reject", "request-revision"],
        payload: object,
    ) -> dict[str, object]:
        try:
            request = VisualCandidateMutationRequestV1.model_validate(payload)
        except ValidationError:
            return _error(
                "INVALID_VISUAL_REVIEW_REQUEST",
                "The visual candidate review request is malformed.",
            )
        authority = self._authorized(
            run=run,
            review=review,
            scene_access=scene_access,
            scene_id=scene_id,
            visual_revision_id=request.visual_collection_revision_id,
            scene_collection_revision_id=request.scene_plan_collection_revision_id,
            scene_revision_id=request.scene_revision_id,
        )
        if isinstance(authority, dict):
            return authority
        access, _ = authority
        current = access.collection
        assert current is not None
        selected = next(
            (item for item in current.candidates if item.candidate_id == candidate_id),
            None,
        )
        if selected is None:
            return _error("UNKNOWN_VISUAL_CANDIDATE", "Visual candidate was not found.")
        if selected.candidate_revision_id != request.candidate_revision_id:
            return _error(
                "STALE_VISUAL_CANDIDATE_REVISION",
                "Visual candidate revision changed.",
            )
        if selected.status is VisualCandidateStatus.SUPERSEDED:
            return _error(
                "SUPERSEDED_VISUAL_CANDIDATE",
                "Superseded visual candidates cannot be mutated.",
            )
        if operation == "accept":
            if selected.status is VisualCandidateStatus.REJECTED:
                return _error(
                    "REJECTED_VISUAL_CANDIDATE",
                    "Rejected visual candidates cannot be accepted.",
                )
            if selected.accepted_for_composition_planning:
                return _error(
                    "VISUAL_CANDIDATE_ALREADY_ACCEPTED",
                    "Visual candidate is already accepted.",
                )
            if not selected.technical_validation.passed or selected.visual_qc.blocker_codes:
                return _error(
                    "VISUAL_CANDIDATE_ACCEPTANCE_BLOCKED",
                    "Visual candidate has unresolved blockers.",
                )
            next_status = VisualCandidateStatus.ACCEPTED_FOR_COMPOSITION_PLANNING
            action = VisualReviewAction.ACCEPTED
        elif operation == "reject":
            if selected.status is VisualCandidateStatus.REJECTED:
                return _error(
                    "VISUAL_CANDIDATE_ALREADY_REJECTED",
                    "Visual candidate is already rejected.",
                )
            next_status = VisualCandidateStatus.REJECTED
            action = VisualReviewAction.REJECTED
        else:
            next_status = VisualCandidateStatus.REVISION_REQUESTED
            action = VisualReviewAction.REVISION_REQUESTED
        review_number = sum(len(item.review_history) for item in current.candidates) + 1
        review_identity_number = self._next_review_id
        self._next_review_id += 1
        selected_revision_identity = self._next_candidate_revision_id
        self._next_candidate_revision_id += 1
        review_entry = VisualReviewEntryV1(
            review_id=f"visual-review-{review_identity_number:04d}",
            review_number=review_number,
            candidate_id=candidate_id,
            candidate_revision_id=(f"visual-candidate-revision-{selected_revision_identity:04d}"),
            action=action,
            reason_code=request.reason_code.value,
            note=request.note,
            created_at=_timestamp(review_number),
        )
        next_candidates: list[VisualCandidateV1] = []
        for item in current.candidates:
            payload_item = item.model_dump(mode="python")
            if (
                operation == "accept"
                and item.accepted_for_composition_planning
                and item.candidate_id != candidate_id
            ):
                supersede_number = review_number + 1
                supersede_identity_number = self._next_review_id
                supersede_revision_identity = self._next_candidate_revision_id
                self._next_review_id += 1
                self._next_candidate_revision_id += 1
                payload_item.update(
                    status=VisualCandidateStatus.SUPERSEDED,
                    accepted_for_composition_planning=False,
                    superseded_reason="NEWER_CANDIDATE_ACCEPTED",
                    candidate_revision_number=item.candidate_revision_number + 1,
                    candidate_revision_id=(
                        f"visual-candidate-revision-{supersede_revision_identity:04d}"
                    ),
                    review_history=(
                        *item.review_history,
                        VisualReviewEntryV1(
                            review_id=f"visual-review-{supersede_identity_number:04d}",
                            review_number=supersede_number,
                            candidate_id=item.candidate_id,
                            candidate_revision_id=(
                                f"visual-candidate-revision-{supersede_revision_identity:04d}"
                            ),
                            action=VisualReviewAction.SUPERSEDED,
                            reason_code="NEWER_CANDIDATE_ACCEPTED",
                            created_at=_timestamp(supersede_number),
                        ),
                    ),
                )
            elif item.candidate_id == candidate_id:
                payload_item.update(
                    status=next_status,
                    accepted_for_composition_planning=(
                        next_status is VisualCandidateStatus.ACCEPTED_FOR_COMPOSITION_PLANNING
                    ),
                    candidate_revision_number=item.candidate_revision_number + 1,
                    candidate_revision_id=(
                        f"visual-candidate-revision-{selected_revision_identity:04d}"
                    ),
                    review_history=(*item.review_history, review_entry),
                )
            next_candidates.append(VisualCandidateV1.model_validate(payload_item))
        committed = self._commit(
            current,
            candidates=tuple(next_candidates),
            current_accepted_candidate_id=(
                candidate_id
                if operation == "accept"
                else None
                if current.current_accepted_candidate_id == candidate_id
                else current.current_accepted_candidate_id
            ),
        )
        if operation == "request-revision":
            self._pending_revisions[(current.run_id, current.scene_id)] = (
                request.reason_code,
                request.note,
                selected.request_id,
            )
        return _detached(
            VisualCandidateOperationResultV1(
                access=self._access_model(
                    run=run,
                    review=review,
                    scene_access=scene_access,
                    scene_id=scene_id,
                    derive=False,
                ).model_copy(update={"collection": committed})
            )
        )

    def candidate(
        self,
        *,
        run: Mapping[str, Any] | None,
        review: Mapping[str, Any] | None,
        scene_access: Mapping[str, Any] | None,
        scene_id: str,
        candidate_id: str,
    ) -> dict[str, object] | None:
        run_id = "" if run is None else str(run.get("run_id", ""))
        if not run_id:
            return None
        selected = next(
            (
                item
                for collection in reversed(self._collections.get((run_id, scene_id), ()))
                for item in collection.candidates
                if item.candidate_id == candidate_id
            ),
            None,
        )
        return None if selected is None else _detached(selected)

    def artifact(
        self,
        *,
        run: Mapping[str, Any] | None,
        review: Mapping[str, Any] | None,
        scene_access: Mapping[str, Any] | None,
        scene_id: str,
        candidate_id: str,
    ) -> VisualArtifact | None:
        access = self._access_model(
            run=run,
            review=review,
            scene_access=scene_access,
            scene_id=scene_id,
            derive=False,
        )
        candidate = (
            None
            if not access.review_authorized or access.collection is None
            else next(
                (
                    item.model_dump(mode="json")
                    for item in access.collection.candidates
                    if item.candidate_id == candidate_id
                ),
                None,
            )
        )
        path = self._artifact_paths.get(candidate_id)
        if (
            candidate is None
            or path is None
            or self._root is None
            or path.resolve().parent != self._root.resolve()
            or not path.is_file()
        ):
            return None
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != candidate["sha256"]:
            return None
        return VisualArtifact(
            content=content,
            mime_type=str(candidate["mime_type"]),
            sha256=digest,
        )


__all__ = [
    "GenerateVisualCandidatesRequestV1",
    "VisualArtifact",
    "VisualCandidateAccessV1",
    "VisualCandidateCollectionV1",
    "VisualCandidateMutationRequestV1",
    "VisualCandidateOperationResultV1",
    "VisualCandidateProvider",
    "VisualCandidateStatus",
    "VisualCandidateStore",
    "VisualRejectionReason",
]
