"""Process-local timeline planning over accepted scene compositions."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum
import hashlib
import json
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
    model_validator,
)


_SHA256 = r"^[0-9a-f]{64}$"
_MAX_TIMELINE_MS = 10 * 60 * 1000


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    @model_validator(mode="before")
    @classmethod
    def exact_schema_version(cls, value: object) -> object:
        if (
            isinstance(value, Mapping)
            and "schema_version" in value
            and (type(value["schema_version"]) is not int or value["schema_version"] != 1)
        ):
            raise ValueError("schema_version must be integer 1")
        return value


class TimelineStatus(StrEnum):
    VALID = "VALID"
    WARNING = "WARNING"
    BLOCKED = "BLOCKED"
    ACCEPTED_FOR_EXECUTION_REVIEW = "ACCEPTED_FOR_EXECUTION_REVIEW"
    REVISION_REQUESTED = "REVISION_REQUESTED"
    SUPERSEDED = "SUPERSEDED"


class NarrationAlignmentStatus(StrEnum):
    ALIGNED = "ALIGNED"
    WARNING = "WARNING"
    BLOCKED = "BLOCKED"
    REVISION_REQUESTED = "REVISION_REQUESTED"
    SUPERSEDED = "SUPERSEDED"


class TimelineReviewReason(StrEnum):
    DURATION_OUT_OF_RANGE = "DURATION_OUT_OF_RANGE"
    NARRATION_WINDOW_UNSUITABLE = "NARRATION_WINDOW_UNSUITABLE"
    TRANSITION_TIMING_UNSUITABLE = "TRANSITION_TIMING_UNSUITABLE"
    SOURCE_ALIGNMENT_MISMATCH = "SOURCE_ALIGNMENT_MISMATCH"
    CONTINUITY_MISMATCH = "CONTINUITY_MISMATCH"
    OTHER_BOUNDED_NOTE = "OTHER_BOUNDED_NOTE"


class SourceCoverageV1(_Contract):
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self) -> SourceCoverageV1:
        if self.end <= self.start:
            raise ValueError("timeline source coverage must be ordered")
        return self


class NarrationAlignmentV1(_Contract):
    alignment_id: str = Field(pattern=r"^narration-alignment-[0-9]{4}$")
    canonical_narration_segment: str = Field(min_length=1)
    source_coverage: SourceCoverageV1
    semantic_beat_id: str
    timeline_start_ms: StrictInt = Field(ge=0, le=_MAX_TIMELINE_MS)
    timeline_end_ms: StrictInt = Field(gt=0, le=_MAX_TIMELINE_MS)
    planned_window_start_ms: StrictInt = Field(ge=0, le=_MAX_TIMELINE_MS)
    planned_window_end_ms: StrictInt = Field(gt=0, le=_MAX_TIMELINE_MS)
    status: NarrationAlignmentStatus
    source_coverage_valid: StrictBool
    warning_codes: tuple[str, ...] = ()
    blocker_codes: tuple[str, ...] = ()
    measured_audio_alignment_available: Literal[False] = False
    tts_alignment_available: Literal[False] = False

    @model_validator(mode="after")
    def consistent(self) -> NarrationAlignmentV1:
        if self.timeline_end_ms <= self.timeline_start_ms:
            raise ValueError("timeline narration bounds must be ordered")
        if not (
            self.timeline_start_ms
            <= self.planned_window_start_ms
            < self.planned_window_end_ms
            <= self.timeline_end_ms
        ):
            raise ValueError("planned narration window must remain inside its segment")
        expected = (
            NarrationAlignmentStatus.BLOCKED
            if self.blocker_codes
            else NarrationAlignmentStatus.WARNING
            if self.warning_codes
            else NarrationAlignmentStatus.ALIGNED
        )
        if self.status not in {
            expected,
            NarrationAlignmentStatus.REVISION_REQUESTED,
            NarrationAlignmentStatus.SUPERSEDED,
        }:
            raise ValueError("narration alignment status is inconsistent")
        if not self.source_coverage_valid and not self.blocker_codes:
            raise ValueError("invalid source coverage requires a blocker")
        return self


class TimelineSegmentV1(_Contract):
    schema_version: Literal[1] = 1
    segment_id: str = Field(pattern=r"^timeline-segment-[0-9]{4}$")
    segment_revision_id: str = Field(pattern=r"^timeline-segment-revision-[0-9]{4}$")
    segment_revision_number: StrictInt = Field(ge=1)
    position: StrictInt = Field(ge=1)
    run_id: str
    story_revision_id: str
    narration_source_sha256: str = Field(pattern=_SHA256)
    scene_plan_collection_revision_id: str
    scene_id: str
    scene_revision_id: str
    visual_collection_revision_id: str
    accepted_candidate_id: str
    accepted_candidate_revision_id: str
    candidate_artifact_sha256: str = Field(pattern=_SHA256)
    composition_collection_revision_id: str
    composition_id: str
    composition_revision_id: str
    semantic_beat_id: str
    source_coverage: SourceCoverageV1
    canonical_narration_segment: str = Field(min_length=1)
    scene_planned_duration_ms: StrictInt = Field(gt=0, le=60_000)
    duration_ms: StrictInt = Field(gt=0, le=60_000)
    start_ms: StrictInt = Field(ge=0, le=_MAX_TIMELINE_MS)
    end_ms: StrictInt = Field(gt=0, le=_MAX_TIMELINE_MS)
    transition_in_intent: str
    transition_in_ms: StrictInt = Field(ge=0, le=2_000)
    transition_out_intent: str
    transition_out_ms: StrictInt = Field(ge=0, le=2_000)
    effective_visible_duration_ms: StrictInt = Field(gt=0, le=60_000)
    narration_alignment: NarrationAlignmentV1
    motion_intent_summary: str
    continuity_codes: tuple[str, ...] = ()
    warning_codes: tuple[str, ...] = ()
    blocker_codes: tuple[str, ...] = ()
    status: TimelineStatus
    note: str | None = Field(default=None, max_length=500)
    changed_fields: tuple[str, ...] = ()
    superseded_reason: str | None = None
    created_at: str
    timeline_contract_version: Literal["timeline_planning_v1"] = "timeline_planning_v1"
    accepted_for_execution_review: Literal[False] = False
    timeline_execution_authority: Literal[False] = False
    narration_generation_capability: Literal[False] = False
    tts_capability: Literal[False] = False
    audio_generation_capability: Literal[False] = False
    renderer_execution_authority: Literal[False] = False
    render_authority: Literal[False] = False
    video_render_authority: Literal[False] = False
    final_media_capability: Literal[False] = False

    @model_validator(mode="after")
    def consistent(self) -> TimelineSegmentV1:
        if self.end_ms != self.start_ms + self.duration_ms:
            raise ValueError("timeline segment end must equal start plus duration")
        if self.effective_visible_duration_ms != self.duration_ms - self.transition_in_ms:
            raise ValueError("effective visible duration must subtract transition overlap once")
        if self.transition_in_ms >= self.duration_ms or self.transition_out_ms >= self.duration_ms:
            raise ValueError("timeline transition cannot consume a segment")
        if self.transition_out_ms > self.duration_ms // 4:
            raise ValueError("timeline transition exceeds the bounded scene fraction")
        alignment = self.narration_alignment
        if (
            alignment.timeline_start_ms != self.start_ms
            or alignment.timeline_end_ms != self.end_ms
            or alignment.canonical_narration_segment != self.canonical_narration_segment
            or alignment.source_coverage != self.source_coverage
            or alignment.semantic_beat_id != self.semantic_beat_id
        ):
            raise ValueError("narration alignment must match segment authority")
        if len(self.changed_fields) != len(set(self.changed_fields)):
            raise ValueError("timeline changed-field audit must be unique")
        if self.status is TimelineStatus.SUPERSEDED and self.superseded_reason is None:
            raise ValueError("superseded timeline segment requires a reason")
        return self


class TimelineReviewEntryV1(_Contract):
    review_id: str = Field(pattern=r"^timeline-review-[0-9]{4}$")
    action: Literal["ACCEPTED", "REVISION_REQUESTED", "SUPERSEDED"]
    reason_code: str
    note: str | None = Field(default=None, max_length=500)
    collection_revision_id: str
    created_at: str


class TimelineCollectionV1(_Contract):
    schema_version: Literal[1] = 1
    collection_id: str = Field(pattern=r"^timeline-collection-[0-9]{4}$")
    collection_revision_id: str = Field(pattern=r"^timeline-collection-revision-[0-9]{4}$")
    collection_revision_number: StrictInt = Field(ge=1)
    run_id: str
    story_revision_id: str
    narration_source_sha256: str = Field(pattern=_SHA256)
    source_authority_sha256: str = Field(pattern=_SHA256)
    scene_plan_collection_revision_id: str
    composition_collection_revision_id: str
    segments: tuple[TimelineSegmentV1, ...] = Field(min_length=1)
    total_segment_count: StrictInt = Field(gt=0)
    total_planned_duration_ms: StrictInt = Field(gt=0, le=_MAX_TIMELINE_MS)
    total_transition_overlap_ms: StrictInt = Field(ge=0, le=_MAX_TIMELINE_MS)
    effective_timeline_duration_ms: StrictInt = Field(gt=0, le=_MAX_TIMELINE_MS)
    target_min_ms: StrictInt = Field(gt=0)
    target_max_ms: StrictInt = Field(gt=0)
    narration_coverage_valid: StrictBool
    ordering_valid: StrictBool
    duration_valid: StrictBool
    transition_valid: StrictBool
    warning_segment_count: StrictInt = Field(ge=0)
    blocked_segment_count: StrictInt = Field(ge=0)
    overall_ready_for_execution_review: StrictBool
    status: TimelineStatus
    accepted_for_execution_review: StrictBool
    accepted_timeline_revision_id: str | None
    review_history: tuple[TimelineReviewEntryV1, ...] = ()
    created_at: str
    process_local: Literal[True] = True
    full_render_enabled: Literal[False] = False
    timeline_execution_authority: Literal[False] = False
    narration_generation_capability: Literal[False] = False
    tts_capability: Literal[False] = False
    audio_generation_capability: Literal[False] = False
    renderer_execution_authority: Literal[False] = False
    render_authority: Literal[False] = False
    video_render_authority: Literal[False] = False
    final_media_capability: Literal[False] = False

    @model_validator(mode="after")
    def summaries(self) -> TimelineCollectionV1:
        if [item.position for item in self.segments] != list(range(1, len(self.segments) + 1)):
            raise ValueError("timeline ordering must be contiguous")
        for values in (
            [item.segment_id for item in self.segments],
            [item.segment_revision_id for item in self.segments],
            [item.scene_id for item in self.segments],
        ):
            if len(values) != len(set(values)):
                raise ValueError("timeline segment identities must be unique")
        if self.total_segment_count != len(self.segments):
            raise ValueError("timeline segment count is inconsistent")
        if any(
            item.run_id != self.run_id
            or item.story_revision_id != self.story_revision_id
            or item.narration_source_sha256 != self.narration_source_sha256
            or item.scene_plan_collection_revision_id != self.scene_plan_collection_revision_id
            or item.composition_collection_revision_id != self.composition_collection_revision_id
            for item in self.segments
        ):
            raise ValueError("timeline source bindings are inconsistent")
        planned = sum(item.duration_ms for item in self.segments)
        overlap = sum(item.transition_out_ms for item in self.segments[:-1])
        effective = planned - overlap
        ordering = all(
            item.start_ms
            == (
                0
                if index == 0
                else self.segments[index - 1].end_ms - self.segments[index - 1].transition_out_ms
            )
            and item.transition_in_ms
            == (0 if index == 0 else self.segments[index - 1].transition_out_ms)
            for index, item in enumerate(self.segments)
        )
        transition = self.segments[-1].transition_out_ms == 0 and all(
            item.transition_out_ms <= item.duration_ms // 4 for item in self.segments
        )
        duration = self.target_min_ms <= effective <= self.target_max_ms
        warning = sum(bool(item.warning_codes) for item in self.segments)
        blocked = sum(bool(item.blocker_codes) for item in self.segments)
        ready = (
            self.narration_coverage_valid and ordering and duration and transition and blocked == 0
        )
        if (
            self.total_planned_duration_ms,
            self.total_transition_overlap_ms,
            self.effective_timeline_duration_ms,
            self.ordering_valid,
            self.duration_valid,
            self.transition_valid,
            self.warning_segment_count,
            self.blocked_segment_count,
            self.overall_ready_for_execution_review,
        ) != (planned, overlap, effective, ordering, duration, transition, warning, blocked, ready):
            raise ValueError("timeline collection summaries are inconsistent")
        accepted = self.status is TimelineStatus.ACCEPTED_FOR_EXECUTION_REVIEW
        if (
            self.accepted_for_execution_review != accepted
            or (self.accepted_timeline_revision_id is not None) != accepted
            or (accepted and self.accepted_timeline_revision_id != self.collection_revision_id)
            or (accepted and not ready)
        ):
            raise ValueError("timeline acceptance summary is inconsistent")
        if len({item.review_id for item in self.review_history}) != len(self.review_history):
            raise ValueError("timeline review identities must be unique")
        return self


class TimelineAccessV1(_Contract):
    schema_version: Literal[1] = 1
    run_id: str
    editable: StrictBool
    initialization_authorized: StrictBool
    blocker_codes: tuple[str, ...]
    current_story_revision_id: str | None
    current_scene_plan_collection_revision_id: str | None
    current_composition_collection_revision_id: str | None
    collection: TimelineCollectionV1 | None
    process_local: Literal[True] = True
    full_render_enabled: Literal[False] = False
    timeline_execution_authority: Literal[False] = False
    narration_generation_capability: Literal[False] = False
    tts_capability: Literal[False] = False
    audio_generation_capability: Literal[False] = False
    renderer_execution_authority: Literal[False] = False
    render_authority: Literal[False] = False
    video_render_authority: Literal[False] = False
    final_media_capability: Literal[False] = False


class InitializeTimelineRequestV1(_Contract):
    schema_version: Literal[1]
    composition_collection_revision_id: str


class TimelineSegmentEditV1(_Contract):
    narration_window_start_ms: StrictInt | None = Field(default=None, ge=0, le=_MAX_TIMELINE_MS)
    narration_window_end_ms: StrictInt | None = Field(default=None, gt=0, le=_MAX_TIMELINE_MS)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def meaningful(self) -> TimelineSegmentEditV1:
        if not self.model_fields_set:
            raise ValueError("timeline edit requires an allowlisted field")
        if self.note is not None and (
            not self.note.strip()
            or "://" in self.note
            or "<" in self.note
            or any(ord(character) < 32 and character not in "\n\t" for character in self.note)
        ):
            raise ValueError("timeline note must be presentation-safe")
        return self


class SaveTimelineSegmentRequestV1(_Contract):
    schema_version: Literal[1]
    base_collection_revision_id: str
    base_segment_revision_id: str
    changes: TimelineSegmentEditV1


class RestoreTimelineSegmentRequestV1(_Contract):
    schema_version: Literal[1]
    base_collection_revision_id: str
    base_segment_revision_id: str
    restore_segment_revision_id: str
    reason: str = Field(min_length=1, max_length=300)


class ReviewTimelineRequestV1(_Contract):
    schema_version: Literal[1]
    base_collection_revision_id: str
    reason_code: TimelineReviewReason
    note: str | None = Field(default=None, max_length=500)


def planned_seconds_to_ms(value: object) -> int:
    """Round Decimal(str(seconds)) half up to the nearest integer millisecond."""

    if type(value) not in {int, float, str, Decimal} or isinstance(value, bool):
        raise ValueError("planned duration must be a decimal-compatible number")
    decimal = Decimal(str(value))
    if not decimal.is_finite() or decimal <= 0:
        raise ValueError("planned duration must be positive and finite")
    milliseconds = int((decimal * Decimal(1000)).quantize(Decimal("1"), ROUND_HALF_UP))
    if not 0 < milliseconds <= _MAX_TIMELINE_MS:
        raise ValueError("planned duration is outside timeline bounds")
    return milliseconds


def _detached(model: BaseModel) -> dict[str, object]:
    return json.loads(model.model_dump_json())


def _timestamp(number: int) -> str:
    return (datetime(2026, 3, 1, tzinfo=UTC) + timedelta(seconds=number)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _error(code: str, message: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "access": None,
        "collection": None,
        "error": {
            "schema_version": 1,
            "code": code,
            "message": message,
            "retryable": False,
        },
    }


class TimelinePlanningStore:
    """Immutable process-local timeline collections."""

    def __init__(self) -> None:
        self._histories: dict[str, tuple[TimelineCollectionV1, ...]] = {}
        self._segment_histories: dict[tuple[str, str], tuple[TimelineSegmentV1, ...]] = {}
        self._collection_identity = 1
        self._collection_revision_identity = 1
        self._segment_identity = 1
        self._segment_revision_identity = 1
        self._alignment_identity = 1
        self._review_identity = 1

    def _current(self, run_id: str) -> TimelineCollectionV1 | None:
        history = self._histories.get(run_id, ())
        return history[-1] if history else None

    @staticmethod
    def _context(
        *,
        run: Mapping[str, Any] | None,
        review: Mapping[str, Any] | None,
        scene_access: Mapping[str, Any] | None,
        composition_access: Mapping[str, Any] | None,
    ) -> tuple[list[str], dict[str, Any] | None]:
        blockers: list[str] = []
        if run is None:
            return ["UNKNOWN_RUN"], None
        if run.get("status") != "PLANNED":
            blockers.append("RUN_NOT_PLANNED")
        if review is None or review.get("review_status") != "ACCEPTED_FOR_SCENE_PLANNING":
            blockers.append("STORYPLAN_NOT_ACCEPTED")
        elif review.get("current_revision_id") != review.get("accepted_revision_id"):
            blockers.append("STALE_ACCEPTED_STORYPLAN")
        story = None if review is None else review.get("current_revision", {}).get("story_plan")
        narration = None if not isinstance(story, Mapping) else story.get("narration_text")
        if not isinstance(narration, str) or not narration:
            blockers.append("CANONICAL_NARRATION_UNAVAILABLE")
        scenes = None if scene_access is None else scene_access.get("collection")
        if not isinstance(scenes, Mapping):
            blockers.append("SCENE_PLAN_UNAVAILABLE")
        elif (
            scenes.get("source_coverage_valid") is not True
            or scenes.get("duration_valid") is not True
            or review is None
            or scenes.get("source_story_revision_id") != review.get("current_revision_id")
            or any(
                item.get("status") != "ACCEPTED_FOR_VISUAL_PLANNING"
                for item in scenes.get("scenes", ())
            )
        ):
            blockers.append("STALE_OR_INVALID_SCENE_PLAN")
        compositions = None if composition_access is None else composition_access.get("collection")
        if not isinstance(compositions, Mapping):
            blockers.append("COMPOSITION_COLLECTION_UNAVAILABLE")
        elif compositions.get("overall_ready_for_timeline_planning") is not True or any(
            item.get("accepted_for_timeline_planning") is not True
            or item.get("validation", {}).get("valid") is not True
            for item in compositions.get("compositions", ())
        ):
            blockers.append("COMPOSITIONS_NOT_ACCEPTED")
        if blockers or not all(isinstance(item, Mapping) for item in (story, scenes, compositions)):
            return list(dict.fromkeys(blockers)), None
        scene_items = list(scenes["scenes"])
        composition_items = list(compositions["compositions"])
        if len(scene_items) != len(composition_items) or [
            item["scene_id"] for item in scene_items
        ] != [item["scene_id"] for item in composition_items]:
            return ["COMPOSITION_SCENE_ORDER_MISMATCH"], None
        for scene, composition in zip(scene_items, composition_items, strict=True):
            coverage = scene["source_coverage"]
            if (
                scene["scene_revision_id"] != composition["scene_revision_id"]
                or scene["source_beat_id"] != composition["semantic_beat_id"]
                or coverage["start"] != composition["source_coverage"]["start"]
                or coverage["end"] != composition["source_coverage"]["end"]
                or narration[coverage["start"] : coverage["end"]] != scene["narration_segment"]
            ):
                return ["TIMELINE_SOURCE_BINDING_MISMATCH"], None
        authority_projection = {
            "story_revision_id": review["current_revision_id"],
            "narration_text": narration,
            "semantic_beats": story["semantic_beats"],
            "scene_collection_revision_id": scenes["collection_revision_id"],
            "scenes": [
                {
                    key: item[key]
                    for key in (
                        "order",
                        "scene_id",
                        "scene_revision_id",
                        "source_beat_id",
                        "source_coverage",
                        "narration_segment",
                        "planned_duration_seconds",
                        "status",
                        "continuity",
                    )
                }
                for item in scene_items
            ],
            "composition_collection_revision_id": compositions["collection_revision_id"],
            "compositions": [
                {
                    key: item[key]
                    for key in (
                        "position",
                        "scene_id",
                        "scene_revision_id",
                        "visual_collection_revision_id",
                        "accepted_candidate_id",
                        "accepted_candidate_revision_id",
                        "accepted_candidate_sha256",
                        "composition_id",
                        "composition_revision_id",
                        "planned_duration_seconds",
                        "motion_intent",
                        "transition_intent",
                        "transition_duration_seconds",
                        "accepted_for_timeline_planning",
                    )
                }
                for item in composition_items
            ],
        }
        return [], {
            "story": story,
            "narration": narration,
            "narration_sha256": _sha256_text(narration),
            "scenes": scenes,
            "compositions": compositions,
            "scene_items": scene_items,
            "composition_items": composition_items,
            "source_authority_sha256": _canonical_sha256(authority_projection),
        }

    def _supersede(self, current: TimelineCollectionV1, reason: str) -> None:
        segments = []
        for item in current.segments:
            revision_id = f"timeline-segment-revision-{self._segment_revision_identity:04d}"
            self._segment_revision_identity += 1
            payload = item.model_dump(mode="python")
            payload.update(
                segment_revision_id=revision_id,
                segment_revision_number=item.segment_revision_number + 1,
                status=TimelineStatus.SUPERSEDED,
                superseded_reason=reason,
                changed_fields=("status",),
                narration_alignment={
                    **item.narration_alignment.model_dump(mode="python"),
                    "status": NarrationAlignmentStatus.SUPERSEDED,
                },
                created_at=_timestamp(self._segment_revision_identity),
            )
            updated = TimelineSegmentV1.model_validate(payload)
            segments.append(updated)
            key = (current.run_id, item.segment_id)
            self._segment_histories[key] = (*self._segment_histories[key], updated)
        self._commit(
            current,
            tuple(segments),
            status=TimelineStatus.SUPERSEDED,
            accepted=False,
            accepted_revision=None,
            review=TimelineReviewEntryV1(
                review_id=f"timeline-review-{self._review_identity:04d}",
                action="SUPERSEDED",
                reason_code=reason,
                collection_revision_id=f"timeline-collection-revision-{self._collection_revision_identity:04d}",
                created_at=_timestamp(self._review_identity),
            ),
        )
        self._review_identity += 1

    def access(
        self,
        *,
        run: Mapping[str, Any] | None,
        review: Mapping[str, Any] | None,
        scene_access: Mapping[str, Any] | None,
        composition_access: Mapping[str, Any] | None,
    ) -> dict[str, object] | None:
        if run is None:
            return None
        run_id = str(run["run_id"])
        blockers, context = self._context(
            run=run,
            review=review,
            scene_access=scene_access,
            composition_access=composition_access,
        )
        current = self._current(run_id)
        if current is not None and context is not None:
            if current.source_authority_sha256 != context["source_authority_sha256"]:
                if current.status is not TimelineStatus.SUPERSEDED:
                    self._supersede(current, "UPSTREAM_TIMELINE_AUTHORITY_CHANGED")
                current = None
            elif current.status is TimelineStatus.SUPERSEDED:
                current = None
        elif current is not None and blockers:
            if current.status is not TimelineStatus.SUPERSEDED:
                self._supersede(current, "UPSTREAM_TIMELINE_AUTHORITY_CHANGED")
            current = None
        return _detached(
            TimelineAccessV1(
                run_id=run_id,
                editable=not blockers and current is not None,
                initialization_authorized=not blockers and context is not None,
                blocker_codes=tuple(blockers),
                current_story_revision_id=(
                    None if review is None else str(review.get("current_revision_id"))
                ),
                current_scene_plan_collection_revision_id=(
                    None if context is None else str(context["scenes"]["collection_revision_id"])
                ),
                current_composition_collection_revision_id=(
                    None
                    if context is None
                    else str(context["compositions"]["collection_revision_id"])
                ),
                collection=current,
            )
        )

    def _initial_segments(
        self, run_id: str, context: Mapping[str, Any]
    ) -> tuple[TimelineSegmentV1, ...]:
        output: list[TimelineSegmentV1] = []
        previous_end = 0
        previous_transition = 0
        previous_intent = "CUT"
        for index, (scene, composition) in enumerate(
            zip(context["scene_items"], context["composition_items"], strict=True)
        ):
            duration = planned_seconds_to_ms(scene["planned_duration_seconds"])
            transition_out = (
                planned_seconds_to_ms(composition["transition_duration_seconds"])
                if composition["transition_duration_seconds"]
                else 0
            )
            start = 0 if index == 0 else previous_end - previous_transition
            end = start + duration
            revision_id = f"timeline-segment-revision-{self._segment_revision_identity:04d}"
            segment_id = f"timeline-segment-{self._segment_identity:04d}"
            alignment_id = f"narration-alignment-{self._alignment_identity:04d}"
            self._segment_identity += 1
            self._segment_revision_identity += 1
            self._alignment_identity += 1
            coverage = SourceCoverageV1(
                start=scene["source_coverage"]["start"],
                end=scene["source_coverage"]["end"],
            )
            narration = str(scene["narration_segment"])
            alignment = NarrationAlignmentV1(
                alignment_id=alignment_id,
                canonical_narration_segment=narration,
                source_coverage=coverage,
                semantic_beat_id=scene["source_beat_id"],
                timeline_start_ms=start,
                timeline_end_ms=end,
                planned_window_start_ms=start,
                planned_window_end_ms=end,
                status=NarrationAlignmentStatus.ALIGNED,
                source_coverage_valid=True,
            )
            output.append(
                TimelineSegmentV1(
                    segment_id=segment_id,
                    segment_revision_id=revision_id,
                    segment_revision_number=1,
                    position=index + 1,
                    run_id=run_id,
                    story_revision_id=context["story"]["revision_id"]
                    if "revision_id" in context["story"]
                    else context["compositions"]["story_revision_id"],
                    narration_source_sha256=context["narration_sha256"],
                    scene_plan_collection_revision_id=context["scenes"]["collection_revision_id"],
                    scene_id=scene["scene_id"],
                    scene_revision_id=scene["scene_revision_id"],
                    visual_collection_revision_id=composition["visual_collection_revision_id"],
                    accepted_candidate_id=composition["accepted_candidate_id"],
                    accepted_candidate_revision_id=composition["accepted_candidate_revision_id"],
                    candidate_artifact_sha256=composition["accepted_candidate_sha256"],
                    composition_collection_revision_id=context["compositions"][
                        "collection_revision_id"
                    ],
                    composition_id=composition["composition_id"],
                    composition_revision_id=composition["composition_revision_id"],
                    semantic_beat_id=scene["source_beat_id"],
                    source_coverage=coverage,
                    canonical_narration_segment=narration,
                    scene_planned_duration_ms=duration,
                    duration_ms=duration,
                    start_ms=start,
                    end_ms=end,
                    transition_in_intent=previous_intent,
                    transition_in_ms=previous_transition,
                    transition_out_intent=composition["transition_intent"],
                    transition_out_ms=transition_out,
                    effective_visible_duration_ms=duration - previous_transition,
                    narration_alignment=alignment,
                    motion_intent_summary=composition["motion_intent"]["mode"],
                    continuity_codes=tuple(scene["continuity"].keys()),
                    status=TimelineStatus.VALID,
                    created_at=_timestamp(self._segment_revision_identity),
                )
            )
            previous_end = end
            previous_transition = transition_out
            previous_intent = str(composition["transition_intent"])
        return tuple(output)

    def initialize(
        self,
        *,
        run: Mapping[str, Any] | None,
        review: Mapping[str, Any] | None,
        scene_access: Mapping[str, Any] | None,
        composition_access: Mapping[str, Any] | None,
        payload: object,
    ) -> dict[str, object]:
        try:
            request = InitializeTimelineRequestV1.model_validate(payload)
        except ValidationError:
            return _error(
                "INVALID_TIMELINE_INITIALIZATION",
                "Timeline initialization request is malformed.",
            )
        blockers, context = self._context(
            run=run,
            review=review,
            scene_access=scene_access,
            composition_access=composition_access,
        )
        if run is None or context is None or blockers:
            return _error(
                "TIMELINE_NOT_AUTHORIZED",
                "Current planning authority does not permit timeline initialization.",
            )
        if (
            request.composition_collection_revision_id
            != context["compositions"]["collection_revision_id"]
        ):
            return _error(
                "STALE_TIMELINE_AUTHORITY",
                "Composition authority changed before timeline initialization.",
            )
        run_id = str(run["run_id"])
        current = self._current(run_id)
        if current is not None and current.status is not TimelineStatus.SUPERSEDED:
            return _error(
                "TIMELINE_ALREADY_INITIALIZED",
                "A current timeline collection already exists.",
            )
        segments = self._initial_segments(run_id, context)
        collection = self._new_collection(
            run_id=run_id,
            context=context,
            segments=segments,
        )
        for item in segments:
            key = (run_id, item.segment_id)
            self._segment_histories[key] = (
                *self._segment_histories.get(key, ()),
                item,
            )
        return {
            "schema_version": 1,
            "access": _detached(
                TimelineAccessV1(
                    run_id=run_id,
                    editable=True,
                    initialization_authorized=True,
                    blocker_codes=(),
                    current_story_revision_id=collection.story_revision_id,
                    current_scene_plan_collection_revision_id=collection.scene_plan_collection_revision_id,
                    current_composition_collection_revision_id=collection.composition_collection_revision_id,
                    collection=collection,
                )
            ),
            "collection": None,
            "error": None,
        }

    def _new_collection(
        self,
        *,
        run_id: str,
        context: Mapping[str, Any],
        segments: tuple[TimelineSegmentV1, ...],
    ) -> TimelineCollectionV1:
        collection = TimelineCollectionV1(
            collection_id=f"timeline-collection-{self._collection_identity:04d}",
            collection_revision_id=f"timeline-collection-revision-{self._collection_revision_identity:04d}",
            collection_revision_number=1,
            run_id=run_id,
            story_revision_id=segments[0].story_revision_id,
            narration_source_sha256=context["narration_sha256"],
            source_authority_sha256=context["source_authority_sha256"],
            scene_plan_collection_revision_id=context["scenes"]["collection_revision_id"],
            composition_collection_revision_id=context["compositions"]["collection_revision_id"],
            segments=segments,
            total_segment_count=len(segments),
            total_planned_duration_ms=sum(item.duration_ms for item in segments),
            total_transition_overlap_ms=sum(item.transition_out_ms for item in segments[:-1]),
            effective_timeline_duration_ms=sum(item.duration_ms for item in segments)
            - sum(item.transition_out_ms for item in segments[:-1]),
            target_min_ms=planned_seconds_to_ms(context["scenes"]["target_min_seconds"]),
            target_max_ms=planned_seconds_to_ms(context["scenes"]["target_max_seconds"]),
            narration_coverage_valid=self._coverage_valid(segments, len(context["narration"])),
            ordering_valid=True,
            duration_valid=planned_seconds_to_ms(context["scenes"]["target_min_seconds"])
            <= sum(item.duration_ms for item in segments)
            - sum(item.transition_out_ms for item in segments[:-1])
            <= planned_seconds_to_ms(context["scenes"]["target_max_seconds"]),
            transition_valid=segments[-1].transition_out_ms == 0,
            warning_segment_count=sum(bool(item.warning_codes) for item in segments),
            blocked_segment_count=sum(bool(item.blocker_codes) for item in segments),
            overall_ready_for_execution_review=(
                self._coverage_valid(segments, len(context["narration"]))
                and planned_seconds_to_ms(context["scenes"]["target_min_seconds"])
                <= sum(item.duration_ms for item in segments)
                - sum(item.transition_out_ms for item in segments[:-1])
                <= planned_seconds_to_ms(context["scenes"]["target_max_seconds"])
                and segments[-1].transition_out_ms == 0
            ),
            status=TimelineStatus.VALID,
            accepted_for_execution_review=False,
            accepted_timeline_revision_id=None,
            created_at=_timestamp(self._collection_revision_identity),
        )
        self._collection_identity += 1
        self._collection_revision_identity += 1
        self._histories[run_id] = (*self._histories.get(run_id, ()), collection)
        return collection

    @staticmethod
    def _coverage_valid(segments: tuple[TimelineSegmentV1, ...], narration_length: int) -> bool:
        previous = 0
        for item in segments:
            if item.source_coverage.start != previous:
                return False
            previous = item.source_coverage.end
        return previous == narration_length

    def _commit(
        self,
        current: TimelineCollectionV1,
        segments: tuple[TimelineSegmentV1, ...],
        *,
        status: TimelineStatus | None = None,
        accepted: bool | None = None,
        accepted_revision: str | None | object = ...,
        review: TimelineReviewEntryV1 | None = None,
    ) -> TimelineCollectionV1:
        revision_id = f"timeline-collection-revision-{self._collection_revision_identity:04d}"
        effective_status = status or current.status
        effective_accepted = current.accepted_for_execution_review if accepted is None else accepted
        effective_accepted_revision = (
            current.accepted_timeline_revision_id if accepted_revision is ... else accepted_revision
        )
        if effective_accepted:
            effective_accepted_revision = revision_id
        payload = current.model_dump(mode="python")
        payload.update(
            collection_revision_id=revision_id,
            collection_revision_number=current.collection_revision_number + 1,
            segments=segments,
            total_planned_duration_ms=sum(item.duration_ms for item in segments),
            total_transition_overlap_ms=sum(item.transition_out_ms for item in segments[:-1]),
            effective_timeline_duration_ms=sum(item.duration_ms for item in segments)
            - sum(item.transition_out_ms for item in segments[:-1]),
            warning_segment_count=sum(bool(item.warning_codes) for item in segments),
            blocked_segment_count=sum(bool(item.blocker_codes) for item in segments),
            status=effective_status,
            accepted_for_execution_review=effective_accepted,
            accepted_timeline_revision_id=effective_accepted_revision,
            review_history=(
                current.review_history
                if review is None
                else (
                    *current.review_history,
                    TimelineReviewEntryV1.model_validate(
                        {
                            **review.model_dump(mode="python"),
                            "collection_revision_id": revision_id,
                        }
                    ),
                )
            ),
            created_at=_timestamp(self._collection_revision_identity),
        )
        payload["duration_valid"] = (
            current.target_min_ms
            <= payload["effective_timeline_duration_ms"]
            <= current.target_max_ms
        )
        payload["overall_ready_for_execution_review"] = (
            payload["narration_coverage_valid"]
            and payload["ordering_valid"]
            and payload["duration_valid"]
            and payload["transition_valid"]
            and payload["blocked_segment_count"] == 0
        )
        self._collection_revision_identity += 1
        committed = TimelineCollectionV1.model_validate(payload)
        self._histories[current.run_id] = (*self._histories[current.run_id], committed)
        return committed

    def _mutation_authority(
        self,
        run_id: str,
        segment_id: str,
        collection_revision_id: str,
        segment_revision_id: str,
    ) -> tuple[TimelineCollectionV1, TimelineSegmentV1] | dict[str, object]:
        current = self._current(run_id)
        if current is None or current.collection_revision_id != collection_revision_id:
            return _error("STALE_TIMELINE_COLLECTION", "Timeline collection changed.")
        selected = next((item for item in current.segments if item.segment_id == segment_id), None)
        if selected is None:
            return _error("UNKNOWN_TIMELINE_SEGMENT", "Timeline segment was not found.")
        if selected.segment_revision_id != segment_revision_id:
            return _error("STALE_TIMELINE_SEGMENT", "Timeline segment revision changed.")
        if current.status is TimelineStatus.SUPERSEDED:
            return _error("SUPERSEDED_TIMELINE", "Superseded timeline cannot be mutated.")
        return current, selected

    def save(self, run_id: str, segment_id: str, payload: object) -> dict[str, object]:
        try:
            request = SaveTimelineSegmentRequestV1.model_validate(payload)
        except ValidationError:
            return _error("INVALID_TIMELINE_EDIT", "Timeline edit is malformed or unsupported.")
        authority = self._mutation_authority(
            run_id,
            segment_id,
            request.base_collection_revision_id,
            request.base_segment_revision_id,
        )
        if isinstance(authority, dict):
            return authority
        current, selected = authority
        changes = request.changes.model_dump(exclude_unset=True, mode="python")
        alignment_payload = selected.narration_alignment.model_dump(mode="python")
        if "narration_window_start_ms" in changes:
            alignment_payload["planned_window_start_ms"] = changes.pop("narration_window_start_ms")
        if "narration_window_end_ms" in changes:
            alignment_payload["planned_window_end_ms"] = changes.pop("narration_window_end_ms")
        proposed_note = changes.pop("note", selected.note)
        if (
            alignment_payload == selected.narration_alignment.model_dump(mode="python")
            and proposed_note == selected.note
        ):
            return _error("NO_OP_TIMELINE_EDIT", "Timeline edit changed no planning values.")
        revision_id = f"timeline-segment-revision-{self._segment_revision_identity:04d}"
        self._segment_revision_identity += 1
        try:
            updated = TimelineSegmentV1.model_validate(
                {
                    **selected.model_dump(mode="python"),
                    "segment_revision_id": revision_id,
                    "segment_revision_number": selected.segment_revision_number + 1,
                    "narration_alignment": alignment_payload,
                    "note": proposed_note,
                    "changed_fields": tuple(sorted(request.changes.model_fields_set)),
                    "status": TimelineStatus.VALID,
                    "created_at": _timestamp(self._segment_revision_identity),
                }
            )
        except ValidationError:
            return _error(
                "INVALID_TIMELINE_EDIT",
                "Timeline edit violates bounded narration-window policy.",
            )
        segments = tuple(
            updated if item.segment_id == segment_id else item for item in current.segments
        )
        committed = self._commit(
            current,
            segments,
            status=TimelineStatus.VALID,
            accepted=False,
            accepted_revision=None,
        )
        key = (run_id, segment_id)
        self._segment_histories[key] = (*self._segment_histories[key], updated)
        return {
            "schema_version": 1,
            "access": None,
            "collection": _detached(committed),
            "error": None,
        }

    @staticmethod
    def _segment_source_identity(item: TimelineSegmentV1) -> tuple[object, ...]:
        payload = item.model_dump(mode="python")
        for key in (
            "segment_revision_id",
            "segment_revision_number",
            "narration_alignment",
            "note",
            "changed_fields",
            "status",
            "superseded_reason",
            "created_at",
        ):
            payload.pop(key)
        return tuple(sorted(payload.items(), key=lambda pair: pair[0]))

    def restore(self, run_id: str, segment_id: str, payload: object) -> dict[str, object]:
        try:
            request = RestoreTimelineSegmentRequestV1.model_validate(payload)
        except ValidationError:
            return _error("INVALID_TIMELINE_RESTORE", "Timeline restore request is malformed.")
        authority = self._mutation_authority(
            run_id,
            segment_id,
            request.base_collection_revision_id,
            request.base_segment_revision_id,
        )
        if isinstance(authority, dict):
            return authority
        current, selected = authority
        source = next(
            (
                item
                for item in self._segment_histories[(run_id, segment_id)]
                if item.segment_revision_id == request.restore_segment_revision_id
            ),
            None,
        )
        if source is None:
            return _error(
                "UNKNOWN_TIMELINE_SEGMENT_REVISION",
                "Historical timeline segment revision was not found.",
            )
        if self._segment_source_identity(source) != self._segment_source_identity(selected):
            return _error(
                "INCOMPATIBLE_TIMELINE_RESTORE",
                "Historical timeline source authority differs from current authority.",
            )
        revision_id = f"timeline-segment-revision-{self._segment_revision_identity:04d}"
        self._segment_revision_identity += 1
        restored = TimelineSegmentV1.model_validate(
            {
                **source.model_dump(mode="python"),
                "segment_revision_id": revision_id,
                "segment_revision_number": selected.segment_revision_number + 1,
                "changed_fields": ("restore_segment_revision_id",),
                "status": TimelineStatus.VALID,
                "created_at": _timestamp(self._segment_revision_identity),
            }
        )
        segments = tuple(
            restored if item.segment_id == segment_id else item for item in current.segments
        )
        committed = self._commit(
            current,
            segments,
            status=TimelineStatus.VALID,
            accepted=False,
            accepted_revision=None,
        )
        key = (run_id, segment_id)
        self._segment_histories[key] = (*self._segment_histories[key], restored)
        return {
            "schema_version": 1,
            "access": None,
            "collection": _detached(committed),
            "error": None,
        }

    def review(
        self,
        run_id: str,
        operation: Literal["accept", "request-revision"],
        payload: object,
    ) -> dict[str, object]:
        try:
            request = ReviewTimelineRequestV1.model_validate(payload)
        except ValidationError:
            return _error("INVALID_TIMELINE_REVIEW", "Timeline review request is malformed.")
        current = self._current(run_id)
        if current is None or current.collection_revision_id != request.base_collection_revision_id:
            return _error("STALE_TIMELINE_COLLECTION", "Timeline collection changed.")
        if current.status is TimelineStatus.SUPERSEDED:
            return _error("SUPERSEDED_TIMELINE", "Superseded timeline cannot be reviewed.")
        if operation == "accept" and current.accepted_for_execution_review:
            return _error("TIMELINE_ALREADY_ACCEPTED", "Timeline is already accepted.")
        if operation == "accept" and not current.overall_ready_for_execution_review:
            return _error(
                "TIMELINE_ACCEPTANCE_BLOCKED",
                "Timeline has unresolved planning blockers.",
            )
        status = (
            TimelineStatus.ACCEPTED_FOR_EXECUTION_REVIEW
            if operation == "accept"
            else TimelineStatus.REVISION_REQUESTED
        )
        review = TimelineReviewEntryV1(
            review_id=f"timeline-review-{self._review_identity:04d}",
            action="ACCEPTED" if operation == "accept" else "REVISION_REQUESTED",
            reason_code=request.reason_code.value,
            note=request.note,
            collection_revision_id="timeline-collection-revision-0000",
            created_at=_timestamp(self._review_identity),
        )
        self._review_identity += 1
        committed = self._commit(
            current,
            current.segments,
            status=status,
            accepted=operation == "accept",
            accepted_revision=None,
            review=review,
        )
        return {
            "schema_version": 1,
            "access": None,
            "collection": _detached(committed),
            "error": None,
        }

    def segment(self, run_id: str, segment_id: str) -> dict[str, object] | None:
        current = self._current(run_id)
        if current is not None:
            selected = next(
                (item for item in current.segments if item.segment_id == segment_id),
                None,
            )
            if selected is not None:
                return _detached(selected)
        history = self._segment_histories.get((run_id, segment_id), ())
        return None if not history else _detached(history[-1])

    def segment_history(self, run_id: str, segment_id: str) -> dict[str, object] | None:
        history = self._segment_histories.get((run_id, segment_id))
        return (
            None
            if history is None
            else {
                "schema_version": 1,
                "run_id": run_id,
                "segment_id": segment_id,
                "revisions": [_detached(item) for item in history],
            }
        )

    def collection_history(self, run_id: str) -> dict[str, object] | None:
        history = self._histories.get(run_id)
        return (
            None
            if history is None
            else {
                "schema_version": 1,
                "run_id": run_id,
                "revisions": [_detached(item) for item in history],
            }
        )

    def collection_revision(self, run_id: str, revision_id: str) -> dict[str, object] | None:
        selected = next(
            (
                item
                for item in self._histories.get(run_id, ())
                if item.collection_revision_id == revision_id
            ),
            None,
        )
        return None if selected is None else _detached(selected)


__all__ = [
    "NarrationAlignmentStatus",
    "TimelinePlanningStore",
    "TimelineStatus",
    "planned_seconds_to_ms",
]
