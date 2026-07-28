"""Process-local, provider-free scene-planning authority for the PLAN_ONLY UI."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
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


class _SceneContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")


class ScenePlanStatus(StrEnum):
    DRAFT = "DRAFT"
    VALID = "VALID"
    WARNING = "WARNING"
    BLOCKED = "BLOCKED"
    ACCEPTED_FOR_VISUAL_PLANNING = "ACCEPTED_FOR_VISUAL_PLANNING"
    REVISION_REQUESTED = "REVISION_REQUESTED"


class SourceCoverageV1(_SceneContract):
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(gt=0)
    overlap_draft: StrictBool = False

    @model_validator(mode="after")
    def valid_interval(self) -> SourceCoverageV1:
        if self.end <= self.start:
            raise ValueError("source coverage end must be greater than start")
        return self


class SceneContinuityV1(_SceneContract):
    recurring_character_required: StrictBool
    anonymous_background_allowed: StrictBool
    identity_continuity_required: Literal[True] = True
    environment_continuity: Literal["PLANNING_REQUIRED"] = "PLANNING_REQUIRED"
    object_continuity: Literal["PLANNING_REQUIRED"] = "PLANNING_REQUIRED"
    visual_identity_verified: Literal[False] = False


class ScenePlanV1(_SceneContract):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=r"^plan-[0-9a-f]{20}$")
    source_story_revision_id: str = Field(pattern=r"^revision-[0-9]{4}$")
    scene_id: str = Field(pattern=r"^scene(?:_[0-9]{2}|-[a-z0-9-]+)$")
    scene_revision_id: str = Field(pattern=r"^scene-revision-[0-9]{4}-[0-9]{2}$")
    scene_revision_number: StrictInt = Field(ge=1)
    order: StrictInt = Field(ge=1)
    source_beat_id: str = Field(pattern=r"^beat_[0-9]{2}$")
    source_coverage: SourceCoverageV1
    narration_segment: str = Field(min_length=1)
    objective: str = Field(min_length=1, max_length=1000)
    emotional_intent: str = Field(min_length=1, max_length=500)
    environment: str = Field(max_length=500)
    character_action: str = Field(max_length=500)
    objects: tuple[str, ...] = Field(max_length=20)
    composition_guidance: str = Field(max_length=1000)
    continuity_notes: str = Field(max_length=1000)
    camera_motion_intent: str = Field(max_length=300)
    transition_intent: str = Field(max_length=500)
    planned_duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    planning_note: str = Field(max_length=1000)
    status: ScenePlanStatus
    warning_codes: tuple[str, ...] = ()
    blocker_codes: tuple[str, ...] = ()
    continuity: SceneContinuityV1
    created_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    @model_validator(mode="after")
    def validate_status(self) -> ScenePlanV1:
        if self.source_coverage.end - self.source_coverage.start != len(self.narration_segment):
            raise ValueError("scene narration must match its source coverage length")
        if len(self.warning_codes) != len(set(self.warning_codes)):
            raise ValueError("scene warning codes must be unique")
        if len(self.blocker_codes) != len(set(self.blocker_codes)):
            raise ValueError("scene blocker codes must be unique")
        if self.blocker_codes and self.status not in {
            ScenePlanStatus.BLOCKED,
            ScenePlanStatus.WARNING,
            ScenePlanStatus.DRAFT,
            ScenePlanStatus.REVISION_REQUESTED,
        }:
            raise ValueError("blocked scene cannot report an accepted or valid status")
        if self.status is ScenePlanStatus.ACCEPTED_FOR_VISUAL_PLANNING and (
            self.warning_codes or self.blocker_codes or self.source_coverage.overlap_draft
        ):
            raise ValueError("accepted scene must be free of warnings and blockers")
        return self


class ScenePlanRevisionSummaryV1(_SceneContract):
    collection_revision_id: str = Field(pattern=r"^scene-plan-revision-[0-9]{4}$")
    revision_number: StrictInt = Field(ge=1)
    created_at: str
    reason: str = Field(min_length=1, max_length=100)
    affected_scene_ids: tuple[str, ...] = Field(min_length=1)


class ScenePlanCollectionV1(_SceneContract):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=r"^plan-[0-9a-f]{20}$")
    source_story_revision_id: str = Field(pattern=r"^revision-[0-9]{4}$")
    accepted_story_revision_id: str = Field(pattern=r"^revision-[0-9]{4}$")
    collection_revision_id: str = Field(pattern=r"^scene-plan-revision-[0-9]{4}$")
    collection_revision_number: StrictInt = Field(ge=1)
    created_at: str
    reason: str = Field(min_length=1, max_length=100)
    source_narration_length: StrictInt = Field(gt=0)
    scenes: tuple[ScenePlanV1, ...] = Field(min_length=1)
    revision_history: tuple[ScenePlanRevisionSummaryV1, ...] = Field(min_length=1)
    target_duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    target_min_seconds: Literal[32.0] = 32.0
    target_max_seconds: Literal[38.0] = 38.0
    total_planned_duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    duration_valid: StrictBool
    source_coverage_valid: StrictBool
    ready_for_visual_planning: StrictBool
    render_authority: Literal[False] = False
    media_capability: Literal[False] = False
    process_local: Literal[True] = True

    @model_validator(mode="after")
    def validate_collection(self) -> ScenePlanCollectionV1:
        if (
            self.collection_revision_id
            != f"scene-plan-revision-{self.collection_revision_number:04d}"
            or self.source_story_revision_id != self.accepted_story_revision_id
        ):
            raise ValueError("collection identity must match its accepted StoryPlan source")
        expected = list(range(1, len(self.scenes) + 1))
        if [scene.order for scene in self.scenes] != expected:
            raise ValueError("scene positions must be unique and contiguous")
        ids = [scene.scene_id for scene in self.scenes]
        if len(ids) != len(set(ids)):
            raise ValueError("scene IDs must be unique")
        scene_revision_ids = [scene.scene_revision_id for scene in self.scenes]
        if len(scene_revision_ids) != len(set(scene_revision_ids)):
            raise ValueError("current scene revision IDs must be unique")
        if any(
            scene.run_id != self.run_id
            or scene.source_story_revision_id != self.source_story_revision_id
            for scene in self.scenes
        ):
            raise ValueError("scene authority identity must match its collection")
        history_numbers = [item.revision_number for item in self.revision_history]
        history_ids = [item.collection_revision_id for item in self.revision_history]
        if history_numbers != list(range(1, self.collection_revision_number + 1)):
            raise ValueError("collection revision history must be strictly ordered")
        if len(history_ids) != len(set(history_ids)):
            raise ValueError("collection revision IDs must be unique")
        if history_ids[-1] != self.collection_revision_id:
            raise ValueError("current collection revision must end history")
        current_summary = self.revision_history[-1]
        if current_summary.created_at != self.created_at or current_summary.reason != self.reason:
            raise ValueError("current collection summary must match collection metadata")
        total = sum(scene.planned_duration_seconds for scene in self.scenes)
        if abs(total - self.total_planned_duration_seconds) > 1e-9:
            raise ValueError("collection duration total must match current scenes")
        duration_valid = self.target_min_seconds <= total <= self.target_max_seconds
        if self.duration_valid is not duration_valid:
            raise ValueError("duration validity must match planned total")
        coverage_valid = _coverage_is_valid(self.scenes, expected_end=self.source_narration_length)
        if self.source_coverage_valid is not coverage_valid:
            raise ValueError("coverage validity must match current source coverage")
        ready = (
            coverage_valid
            and duration_valid
            and all(
                not scene.blocker_codes
                and not scene.warning_codes
                and not scene.source_coverage.overlap_draft
                for scene in self.scenes
            )
        )
        if self.ready_for_visual_planning is not ready:
            raise ValueError("visual-planning readiness must match planning constraints")
        return self


class ScenePlanAccessV1(_SceneContract):
    schema_version: Literal[1] = 1
    run_id: str
    editable: StrictBool
    blocker_codes: tuple[str, ...]
    collection: ScenePlanCollectionV1 | None
    render_authority: Literal[False] = False
    media_capability: Literal[False] = False
    process_local: Literal[True] = True

    @model_validator(mode="after")
    def validate_access(self) -> ScenePlanAccessV1:
        if self.editable is bool(self.blocker_codes):
            raise ValueError("scene-plan editability must match blocker absence")
        if self.collection is not None and self.collection.run_id != self.run_id:
            raise ValueError("scene-plan access identity must match its collection")
        return self


class ScenePlanEditChangesV1(_SceneContract):
    objective: str | None = Field(default=None, min_length=1, max_length=1000)
    emotional_intent: str | None = Field(default=None, min_length=1, max_length=500)
    environment: str | None = Field(default=None, max_length=500)
    character_action: str | None = Field(default=None, max_length=500)
    objects: tuple[str, ...] | None = Field(default=None, max_length=20)
    composition_guidance: str | None = Field(default=None, max_length=1000)
    continuity_notes: str | None = Field(default=None, max_length=1000)
    camera_motion_intent: str | None = Field(default=None, max_length=300)
    transition_intent: str | None = Field(default=None, max_length=500)
    planned_duration_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    planning_note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def require_change(self) -> ScenePlanEditChangesV1:
        if not self.model_fields_set:
            raise ValueError("at least one allowlisted scene change is required")
        for field_name in self.model_fields_set:
            value = getattr(self, field_name)
            values = value if isinstance(value, tuple) else (value,)
            for item in values:
                if isinstance(item, str) and (
                    len(item) > 1000
                    or any(ord(character) < 32 and character not in "\n\t" for character in item)
                ):
                    raise ValueError("scene planning text must be presentation-safe")
                if (
                    field_name == "objects"
                    and isinstance(item, str)
                    and not (1 <= len(item) <= 100)
                ):
                    raise ValueError("object labels must contain 1 to 100 characters")
        return self


class ScenePlanMutationBaseV1(_SceneContract):
    schema_version: Literal[1]
    base_collection_revision_id: str = Field(pattern=r"^scene-plan-revision-[0-9]{4}$")

    @model_validator(mode="before")
    @classmethod
    def exact_schema(cls, value: Any) -> Any:
        if isinstance(value, Mapping) and type(value.get("schema_version")) is not int:
            raise ValueError("schema_version must be the exact integer 1")
        return value


class InitializeScenePlanRequestV1(_SceneContract):
    schema_version: Literal[1]
    accepted_story_revision_id: str = Field(pattern=r"^revision-[0-9]{4}$")

    @model_validator(mode="before")
    @classmethod
    def exact_schema(cls, value: Any) -> Any:
        if isinstance(value, Mapping) and type(value.get("schema_version")) is not int:
            raise ValueError("schema_version must be the exact integer 1")
        return value


class SaveSceneRevisionRequestV1(ScenePlanMutationBaseV1):
    base_scene_revision_id: str = Field(pattern=r"^scene-revision-[0-9]{4}-[0-9]{2}$")
    changes: ScenePlanEditChangesV1


class ReorderSceneRequestV1(ScenePlanMutationBaseV1):
    scene_id: str
    direction: Literal["UP", "DOWN"]


class SplitSceneRequestV1(ScenePlanMutationBaseV1):
    scene_id: str
    base_scene_revision_id: str
    split_at: StrictInt = Field(gt=0)
    first_duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    second_duration_seconds: float = Field(gt=0, allow_inf_nan=False)


class MergeScenesRequestV1(ScenePlanMutationBaseV1):
    first_scene_id: str
    second_scene_id: str


class DuplicateSceneRequestV1(ScenePlanMutationBaseV1):
    scene_id: str


class SceneTransitionRequestV1(ScenePlanMutationBaseV1):
    scene_id: str
    base_scene_revision_id: str


class RequestSceneRevisionV1(SceneTransitionRequestV1):
    reason_code: Literal[
        "OBJECTIVE_NEEDS_REVISION",
        "CONTINUITY_NEEDS_REVISION",
        "DURATION_NEEDS_REVISION",
        "SOURCE_LINKAGE_NEEDS_REVISION",
        "OTHER_PLANNING_REVISION",
    ]
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def safe_note(self) -> RequestSceneRevisionV1:
        if self.note is not None and any(
            ord(character) < 32 and character not in "\n\t" for character in self.note
        ):
            raise ValueError("revision note must be presentation-safe")
        return self


class RestoreSceneRevisionRequestV1(ScenePlanMutationBaseV1):
    base_scene_revision_id: str
    restore_scene_revision_id: str
    reason: str = Field(min_length=1, max_length=500)


class ScenePlanErrorV1(_SceneContract):
    schema_version: Literal[1] = 1
    request_id: str
    status: Literal["BLOCKED", "FAILED"]
    code: str
    message: str
    retryable: Literal[False] = False
    field_errors: tuple[dict[str, str], ...] = ()
    details: dict[str, str] = Field(default_factory=dict)


class ScenePlanOperationResultV1(_SceneContract):
    schema_version: Literal[1] = 1
    access: ScenePlanAccessV1 | None = None
    error: ScenePlanErrorV1 | None = None

    @model_validator(mode="after")
    def require_result(self) -> ScenePlanOperationResultV1:
        if (self.access is None) == (self.error is None):
            raise ValueError("scene-plan operation requires access or error")
        return self


def _request_id(payload: object) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")
    return f"request-{hashlib.sha256(encoded).hexdigest()[:16]}"


def _detached(value: BaseModel) -> dict[str, object]:
    return json.loads(value.model_dump_json())


def _coverage_is_valid(scenes: tuple[ScenePlanV1, ...], *, expected_end: int) -> bool:
    canonical = [
        scene.source_coverage for scene in scenes if not scene.source_coverage.overlap_draft
    ]
    ordered = sorted(canonical, key=lambda item: (item.start, item.end))
    return (
        bool(ordered)
        and ordered[0].start == 0
        and ordered[-1].end == expected_end
        and all(
            item.start == ordered[index - 1].end for index, item in enumerate(ordered) if index > 0
        )
    )


def _timestamp(base: str, number: int) -> str:
    parsed = datetime.strptime(base, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return (parsed + timedelta(seconds=number - 1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _error(payload: object, code: str, message: str, *, failed: bool = False) -> dict[str, object]:
    return _detached(
        ScenePlanOperationResultV1(
            error=ScenePlanErrorV1(
                request_id=_request_id(payload),
                status="FAILED" if failed else "BLOCKED",
                code=code,
                message=message,
            )
        )
    )


class ScenePlanningStore:
    """Immutable revision store; caller supplies validated StoryPlan review snapshots."""

    def __init__(self) -> None:
        self._collections: dict[str, tuple[ScenePlanCollectionV1, ...]] = {}

    def _access(self, run_id: str, review: Mapping[str, Any] | None) -> ScenePlanAccessV1:
        blockers: list[str] = []
        if review is None:
            blockers.append("STORYPLAN_REVIEW_UNAVAILABLE")
        else:
            if review["review_status"] != "ACCEPTED_FOR_SCENE_PLANNING":
                blockers.append("STORYPLAN_NOT_ACCEPTED")
            if (
                review["accepted_revision_id"] is None
                or review["accepted_revision_id"] != review["current_revision_id"]
            ):
                blockers.append("ACCEPTED_STORYPLAN_NOT_CURRENT")
        current = self._collections.get(run_id, ())
        collection = current[-1] if current else None
        if collection is not None and review is not None:
            if collection.source_story_revision_id != review["current_revision_id"]:
                blockers.append("SCENE_PLAN_SOURCE_STORYPLAN_STALE")
        return ScenePlanAccessV1(
            run_id=run_id,
            editable=not blockers,
            blocker_codes=tuple(blockers),
            collection=collection,
        )

    def get_access(self, run_id: str, review: Mapping[str, Any] | None) -> dict[str, object]:
        return _detached(self._access(run_id, review))

    def initialize(
        self, run_id: str, review: Mapping[str, Any] | None, payload: object
    ) -> dict[str, object]:
        if (
            review is None
            or review["review_status"] != "ACCEPTED_FOR_SCENE_PLANNING"
            or review["accepted_revision_id"] is None
            or review["accepted_revision_id"] != review["current_revision_id"]
        ):
            return _error(
                payload,
                "SCENE_PLANNING_NOT_AUTHORIZED",
                "Scene planning is not authorized.",
            )
        try:
            request = InitializeScenePlanRequestV1.model_validate(payload)
        except ValidationError:
            return _error(
                payload,
                "INVALID_SCENE_PLAN_REQUEST",
                "The scene-plan request is malformed.",
                failed=True,
            )
        if request.accepted_story_revision_id != review["current_revision_id"]:
            return _error(
                payload, "STALE_STORYPLAN_REVISION", "The accepted StoryPlan revision changed."
            )
        revision = review["current_revision"]
        current = self._current(run_id)
        if current is not None and current.source_story_revision_id == revision["revision_id"]:
            return _detached(ScenePlanOperationResultV1(access=self._access(run_id, review)))
        story = revision["story_plan"]
        beats = {beat["beat_id"]: beat for beat in story["semantic_beats"]}
        identity = revision["identity_scope"]
        timestamp = revision["created_at"]
        collection_number = 1 if current is None else current.collection_revision_number + 1
        scenes = tuple(
            ScenePlanV1(
                run_id=run_id,
                source_story_revision_id=revision["revision_id"],
                scene_id=scene["scene_id"],
                scene_revision_id=(f"scene-revision-{collection_number:04d}-{scene['order']:02d}"),
                scene_revision_number=1,
                order=scene["order"],
                source_beat_id=scene["source_beat_id"],
                source_coverage=SourceCoverageV1(**beats[scene["source_beat_id"]]["source_span"]),
                narration_segment=beats[scene["source_beat_id"]]["narration_segment"],
                objective=scene["meaning"],
                emotional_intent=", ".join(scene["emotional_tone"]),
                environment="",
                character_action="",
                objects=(),
                composition_guidance=scene["visual_intent"],
                continuity_notes="",
                camera_motion_intent="",
                transition_intent=beats[scene["source_beat_id"]]["transition_intent"],
                planned_duration_seconds=scene["planned_duration_seconds"],
                planning_note="",
                status=ScenePlanStatus.VALID,
                continuity=SceneContinuityV1(
                    recurring_character_required=identity["recurring_female_required"],
                    anonymous_background_allowed=identity["anonymous_background_people_allowed"],
                ),
                created_at=timestamp,
            )
            for scene in story["scenes"]
        )
        reason = "INITIAL_DERIVATION" if current is None else "STORYPLAN_REVISION_DERIVATION"
        summary = ScenePlanRevisionSummaryV1(
            collection_revision_id=(f"scene-plan-revision-{collection_number:04d}"),
            revision_number=collection_number,
            created_at=timestamp,
            reason=reason,
            affected_scene_ids=tuple(scene.scene_id for scene in scenes),
        )
        collection = self._collection(
            run_id=run_id,
            source_revision=revision["revision_id"],
            number=collection_number,
            timestamp=timestamp,
            reason=reason,
            scenes=scenes,
            history=((summary,) if current is None else (*current.revision_history, summary)),
            target=story["target_duration_seconds"],
            source_narration_length=len(story["narration_text"]),
        )
        self._collections[run_id] = (
            (collection,) if current is None else (*self._collections[run_id], collection)
        )
        return _detached(ScenePlanOperationResultV1(access=self._access(run_id, review)))

    def _collection(
        self,
        *,
        run_id: str,
        source_revision: str,
        number: int,
        timestamp: str,
        reason: str,
        scenes: tuple[ScenePlanV1, ...],
        history: tuple[ScenePlanRevisionSummaryV1, ...],
        target: float,
        source_narration_length: int,
    ) -> ScenePlanCollectionV1:
        total = sum(scene.planned_duration_seconds for scene in scenes)
        coverage = _coverage_is_valid(scenes, expected_end=source_narration_length)
        duration = 32 <= total <= 38
        return ScenePlanCollectionV1(
            run_id=run_id,
            source_story_revision_id=source_revision,
            accepted_story_revision_id=source_revision,
            collection_revision_id=f"scene-plan-revision-{number:04d}",
            collection_revision_number=number,
            created_at=timestamp,
            reason=reason,
            source_narration_length=source_narration_length,
            scenes=scenes,
            revision_history=history,
            target_duration_seconds=target,
            total_planned_duration_seconds=total,
            duration_valid=duration,
            source_coverage_valid=coverage,
            ready_for_visual_planning=coverage
            and duration
            and all(
                not scene.blocker_codes
                and not scene.warning_codes
                and not scene.source_coverage.overlap_draft
                for scene in scenes
            ),
        )

    def _current(self, run_id: str) -> ScenePlanCollectionV1 | None:
        revisions = self._collections.get(run_id)
        return revisions[-1] if revisions else None

    def _authorized_current(
        self,
        run_id: str,
        review: Mapping[str, Any] | None,
        base_revision: str,
        payload: object,
    ) -> ScenePlanCollectionV1 | dict[str, object]:
        access = self._access(run_id, review)
        current = self._current(run_id)
        if not access.editable or current is None:
            return _error(
                payload, "SCENE_PLANNING_NOT_AUTHORIZED", "Scene planning is not authorized."
            )
        if current.collection_revision_id != base_revision:
            return _error(
                payload, "STALE_COLLECTION_REVISION", "The scene-plan collection changed."
            )
        return current

    def _commit(
        self,
        current: ScenePlanCollectionV1,
        review: Mapping[str, Any],
        scenes: tuple[ScenePlanV1, ...],
        reason: str,
        affected: tuple[str, ...],
    ) -> dict[str, object]:
        number = current.collection_revision_number + 1
        timestamp = _timestamp(current.created_at, number)
        summary = ScenePlanRevisionSummaryV1(
            collection_revision_id=f"scene-plan-revision-{number:04d}",
            revision_number=number,
            created_at=timestamp,
            reason=reason,
            affected_scene_ids=affected,
        )
        collection = self._collection(
            run_id=current.run_id,
            source_revision=current.source_story_revision_id,
            number=number,
            timestamp=timestamp,
            reason=reason,
            scenes=scenes,
            history=(*current.revision_history, summary),
            target=current.target_duration_seconds,
            source_narration_length=current.source_narration_length,
        )
        self._collections[current.run_id] = (*self._collections[current.run_id], collection)
        return _detached(ScenePlanOperationResultV1(access=self._access(current.run_id, review)))

    def _replace_scene(
        self,
        current: ScenePlanCollectionV1,
        scene_id: str,
        updates: Mapping[str, Any],
        *,
        status: ScenePlanStatus | None = None,
        warning_codes: tuple[str, ...] | None = None,
        blocker_codes: tuple[str, ...] | None = None,
    ) -> tuple[ScenePlanV1, ...] | None:
        selected = next((scene for scene in current.scenes if scene.scene_id == scene_id), None)
        if selected is None:
            return None
        payload = selected.model_dump(mode="python")
        payload.update(updates)
        payload["scene_revision_number"] = selected.scene_revision_number + 1
        payload["scene_revision_id"] = (
            f"scene-revision-{current.collection_revision_number + 1:04d}-{selected.order:02d}"
        )
        payload["created_at"] = _timestamp(
            current.created_at, current.collection_revision_number + 1
        )
        if status is not None:
            payload["status"] = status
        if warning_codes is not None:
            payload["warning_codes"] = warning_codes
        if blocker_codes is not None:
            payload["blocker_codes"] = blocker_codes
        replacement = ScenePlanV1.model_validate(payload)
        return tuple(
            replacement if scene.scene_id == scene_id else scene for scene in current.scenes
        )

    def save(
        self, run_id: str, review: Mapping[str, Any] | None, scene_id: str, payload: object
    ) -> dict[str, object] | None:
        try:
            request = SaveSceneRevisionRequestV1.model_validate(payload)
        except ValidationError:
            return _error(
                payload, "INVALID_SCENE_EDIT", "The scene edit is malformed.", failed=True
            )
        current = self._authorized_current(
            run_id, review, request.base_collection_revision_id, payload
        )
        if isinstance(current, dict):
            return current
        scene = next((item for item in current.scenes if item.scene_id == scene_id), None)
        if scene is None:
            return None
        if scene.scene_revision_id != request.base_scene_revision_id:
            return _error(payload, "STALE_SCENE_REVISION", "The scene revision changed.")
        changes = request.changes.model_dump(exclude_unset=True)
        if all(getattr(scene, key) == value for key, value in changes.items()):
            return _error(payload, "NO_SCENE_CHANGES", "The scene edit contains no changes.")
        scenes = self._replace_scene(
            current,
            scene_id,
            changes,
            status=ScenePlanStatus.VALID,
            warning_codes=(),
            blocker_codes=(),
        )
        assert scenes is not None and review is not None
        return self._commit(current, review, scenes, "SCENE_EDITED", (scene_id,))

    def reorder(
        self, run_id: str, review: Mapping[str, Any] | None, payload: object
    ) -> dict[str, object]:
        try:
            request = ReorderSceneRequestV1.model_validate(payload)
        except ValidationError:
            return _error(
                payload, "INVALID_REORDER_REQUEST", "The reorder request is malformed.", failed=True
            )
        current = self._authorized_current(
            run_id, review, request.base_collection_revision_id, payload
        )
        if isinstance(current, dict):
            return current
        index = next(
            (i for i, scene in enumerate(current.scenes) if scene.scene_id == request.scene_id), -1
        )
        destination = index - 1 if request.direction == "UP" else index + 1
        if index < 0:
            return _error(payload, "UNKNOWN_SCENE", "The requested scene was not found.")
        if destination < 0 or destination >= len(current.scenes):
            return _error(payload, "INVALID_SCENE_MOVE", "The scene cannot move in that direction.")
        ordered = list(current.scenes)
        ordered[index], ordered[destination] = ordered[destination], ordered[index]
        next_number = current.collection_revision_number + 1
        timestamp = _timestamp(current.created_at, next_number)
        scenes_list: list[ScenePlanV1] = []
        for order, item in enumerate(ordered, 1):
            item_payload = item.model_dump(mode="python")
            if item.order != order:
                item_payload.update(
                    order=order,
                    scene_revision_number=item.scene_revision_number + 1,
                    scene_revision_id=f"scene-revision-{next_number:04d}-{order:02d}",
                    created_at=timestamp,
                )
            scenes_list.append(ScenePlanV1.model_validate(item_payload))
        scenes = tuple(scenes_list)
        assert review is not None
        return self._commit(current, review, scenes, "SCENES_REORDERED", (request.scene_id,))

    def split(
        self, run_id: str, review: Mapping[str, Any] | None, payload: object
    ) -> dict[str, object]:
        try:
            request = SplitSceneRequestV1.model_validate(payload)
        except ValidationError:
            return _error(
                payload, "INVALID_SPLIT_REQUEST", "The split request is malformed.", failed=True
            )
        current = self._authorized_current(
            run_id, review, request.base_collection_revision_id, payload
        )
        if isinstance(current, dict):
            return current
        scene = next((item for item in current.scenes if item.scene_id == request.scene_id), None)
        if scene is None:
            return _error(payload, "UNKNOWN_SCENE", "The requested scene was not found.")
        if scene.scene_revision_id != request.base_scene_revision_id:
            return _error(payload, "STALE_SCENE_REVISION", "The scene revision changed.")
        if not scene.source_coverage.start < request.split_at < scene.source_coverage.end:
            return _error(
                payload, "INVALID_SOURCE_SPLIT", "The split point is outside the scene source span."
            )
        if (
            abs(
                request.first_duration_seconds
                + request.second_duration_seconds
                - scene.planned_duration_seconds
            )
            > 1e-9
        ):
            return _error(
                payload,
                "SPLIT_DURATION_MISMATCH",
                "Split durations must preserve the original duration.",
            )
        offset = request.split_at - scene.source_coverage.start
        first_text, second_text = scene.narration_segment[:offset], scene.narration_segment[offset:]
        if not first_text or not second_text:
            return _error(
                payload, "INVALID_SOURCE_SPLIT", "Both split narration segments must be non-empty."
            )
        next_number = current.collection_revision_number + 1
        base = scene.model_dump(mode="python")
        created = _timestamp(current.created_at, next_number)
        parts = []
        for suffix, start, end, text, duration in (
            (
                "a",
                scene.source_coverage.start,
                request.split_at,
                first_text,
                request.first_duration_seconds,
            ),
            (
                "b",
                request.split_at,
                scene.source_coverage.end,
                second_text,
                request.second_duration_seconds,
            ),
        ):
            part = dict(base)
            part.update(
                scene_id=f"scene-split-{next_number:04d}-{suffix}",
                scene_revision_id=f"scene-revision-{next_number:04d}-{scene.order + len(parts):02d}",
                scene_revision_number=1,
                source_coverage={"start": start, "end": end, "overlap_draft": False},
                narration_segment=text,
                planned_duration_seconds=duration,
                status=ScenePlanStatus.VALID,
                warning_codes=(),
                blocker_codes=(),
                created_at=created,
            )
            parts.append(ScenePlanV1.model_validate(part))
        expanded = [item for item in current.scenes if item.scene_id != scene.scene_id]
        expanded[scene.order - 1 : scene.order - 1] = parts
        scenes = tuple(
            ScenePlanV1.model_validate({**item.model_dump(mode="python"), "order": order})
            for order, item in enumerate(expanded, 1)
        )
        assert review is not None
        return self._commit(
            current, review, scenes, "SCENE_SPLIT", tuple(item.scene_id for item in parts)
        )

    def merge(
        self, run_id: str, review: Mapping[str, Any] | None, payload: object
    ) -> dict[str, object]:
        try:
            request = MergeScenesRequestV1.model_validate(payload)
        except ValidationError:
            return _error(
                payload, "INVALID_MERGE_REQUEST", "The merge request is malformed.", failed=True
            )
        current = self._authorized_current(
            run_id, review, request.base_collection_revision_id, payload
        )
        if isinstance(current, dict):
            return current
        first = next(
            (item for item in current.scenes if item.scene_id == request.first_scene_id), None
        )
        second = next(
            (item for item in current.scenes if item.scene_id == request.second_scene_id), None
        )
        if first is None or second is None:
            return _error(payload, "UNKNOWN_SCENE", "A requested scene was not found.")
        if second.order != first.order + 1:
            return _error(payload, "SCENES_NOT_ADJACENT", "Only adjacent scenes can be merged.")
        if (
            first.source_beat_id != second.source_beat_id
            or first.source_coverage.end != second.source_coverage.start
        ):
            return _error(
                payload,
                "INCOMPATIBLE_SOURCE_COVERAGE",
                "Merged scenes require contiguous coverage in one beat.",
            )
        number = current.collection_revision_number + 1
        merged_payload = first.model_dump(mode="python")
        merged_payload.update(
            scene_id=f"scene-merge-{number:04d}",
            scene_revision_id=f"scene-revision-{number:04d}-{first.order:02d}",
            scene_revision_number=1,
            source_coverage={
                "start": first.source_coverage.start,
                "end": second.source_coverage.end,
                "overlap_draft": False,
            },
            narration_segment=first.narration_segment + second.narration_segment,
            objective=f"{first.objective} / {second.objective}",
            planned_duration_seconds=first.planned_duration_seconds
            + second.planned_duration_seconds,
            planning_note="\n".join(filter(None, (first.planning_note, second.planning_note))),
            status=ScenePlanStatus.VALID,
            warning_codes=(),
            blocker_codes=(),
            created_at=_timestamp(current.created_at, number),
        )
        merged = ScenePlanV1.model_validate(merged_payload)
        items = [
            item
            for item in current.scenes
            if item.scene_id not in {first.scene_id, second.scene_id}
        ]
        items.insert(first.order - 1, merged)
        scenes = tuple(
            ScenePlanV1.model_validate({**item.model_dump(mode="python"), "order": order})
            for order, item in enumerate(items, 1)
        )
        assert review is not None
        return self._commit(current, review, scenes, "SCENES_MERGED", (merged.scene_id,))

    def duplicate(
        self, run_id: str, review: Mapping[str, Any] | None, payload: object
    ) -> dict[str, object]:
        try:
            request = DuplicateSceneRequestV1.model_validate(payload)
        except ValidationError:
            return _error(
                payload,
                "INVALID_DUPLICATE_REQUEST",
                "The duplicate request is malformed.",
                failed=True,
            )
        current = self._authorized_current(
            run_id, review, request.base_collection_revision_id, payload
        )
        if isinstance(current, dict):
            return current
        scene = next((item for item in current.scenes if item.scene_id == request.scene_id), None)
        if scene is None:
            return _error(payload, "UNKNOWN_SCENE", "The requested scene was not found.")
        number = current.collection_revision_number + 1
        duplicate_payload = scene.model_dump(mode="python")
        duplicate_payload.update(
            scene_id=f"scene-duplicate-{number:04d}",
            scene_revision_id=f"scene-revision-{number:04d}-{scene.order + 1:02d}",
            scene_revision_number=1,
            source_coverage={**scene.source_coverage.model_dump(), "overlap_draft": True},
            status=ScenePlanStatus.WARNING,
            warning_codes=("DUPLICATE_SOURCE_COVERAGE",),
            blocker_codes=("SOURCE_COVERAGE_OVERLAP_DRAFT",),
            created_at=_timestamp(current.created_at, number),
        )
        duplicate = ScenePlanV1.model_validate(duplicate_payload)
        items = list(current.scenes)
        items.insert(scene.order, duplicate)
        scenes = tuple(
            ScenePlanV1.model_validate({**item.model_dump(mode="python"), "order": order})
            for order, item in enumerate(items, 1)
        )
        assert review is not None
        return self._commit(
            current, review, scenes, "SCENE_DUPLICATED_AS_DRAFT", (duplicate.scene_id,)
        )

    def transition(
        self,
        run_id: str,
        review: Mapping[str, Any] | None,
        payload: object,
        *,
        accept: bool,
    ) -> dict[str, object]:
        model = SceneTransitionRequestV1 if accept else RequestSceneRevisionV1
        try:
            request = model.model_validate(payload)
        except ValidationError:
            return _error(
                payload,
                "INVALID_SCENE_TRANSITION",
                "The scene transition request is malformed.",
                failed=True,
            )
        current = self._authorized_current(
            run_id, review, request.base_collection_revision_id, payload
        )
        if isinstance(current, dict):
            return current
        scene = next((item for item in current.scenes if item.scene_id == request.scene_id), None)
        if scene is None:
            return _error(payload, "UNKNOWN_SCENE", "The requested scene was not found.")
        if scene.scene_revision_id != request.base_scene_revision_id:
            return _error(payload, "STALE_SCENE_REVISION", "The scene revision changed.")
        if accept:
            if scene.status is ScenePlanStatus.ACCEPTED_FOR_VISUAL_PLANNING:
                return _error(
                    payload,
                    "SCENE_ALREADY_ACCEPTED",
                    "The current scene revision is already accepted for visual planning.",
                )
            if scene.blocker_codes or scene.warning_codes or scene.source_coverage.overlap_draft:
                return _error(
                    payload,
                    "SCENE_ACCEPTANCE_BLOCKED",
                    "The scene has unresolved planning conditions.",
                )
            status = ScenePlanStatus.ACCEPTED_FOR_VISUAL_PLANNING
            updates: dict[str, Any] = {}
            reason = "SCENE_ACCEPTED_FOR_VISUAL_PLANNING"
        else:
            status = ScenePlanStatus.REVISION_REQUESTED
            request_revision = RequestSceneRevisionV1.model_validate(request)
            updates = {
                "planning_note": request_revision.note or scene.planning_note,
                "warning_codes": (request_revision.reason_code,),
            }
            reason = "SCENE_REVISION_REQUESTED"
        scenes = self._replace_scene(current, request.scene_id, updates, status=status)
        assert scenes is not None and review is not None
        return self._commit(current, review, scenes, reason, (request.scene_id,))

    def restore(
        self, run_id: str, review: Mapping[str, Any] | None, scene_id: str, payload: object
    ) -> dict[str, object] | None:
        try:
            request = RestoreSceneRevisionRequestV1.model_validate(payload)
        except ValidationError:
            return _error(
                payload, "INVALID_RESTORE_REQUEST", "The restore request is malformed.", failed=True
            )
        current = self._authorized_current(
            run_id, review, request.base_collection_revision_id, payload
        )
        if isinstance(current, dict):
            return current
        current_scene = next((item for item in current.scenes if item.scene_id == scene_id), None)
        if current_scene is None:
            return None
        if current_scene.scene_revision_id != request.base_scene_revision_id:
            return _error(payload, "STALE_SCENE_REVISION", "The scene revision changed.")
        prior = next(
            (
                scene
                for collection in self._collections[run_id]
                for scene in collection.scenes
                if scene.scene_id == scene_id
                and scene.scene_revision_id == request.restore_scene_revision_id
            ),
            None,
        )
        if prior is None:
            return _error(payload, "UNKNOWN_SCENE_REVISION", "The scene revision was not found.")
        editable = ScenePlanEditChangesV1.model_fields
        updates = {name: getattr(prior, name) for name in editable}
        scenes = self._replace_scene(
            current,
            scene_id,
            updates,
            status=prior.status,
            warning_codes=prior.warning_codes,
            blocker_codes=prior.blocker_codes,
        )
        assert scenes is not None and review is not None
        return self._commit(current, review, scenes, "SCENE_REVISION_RESTORED", (scene_id,))

    def collection_history(self, run_id: str) -> dict[str, object] | None:
        revisions = self._collections.get(run_id)
        if not revisions:
            return None
        return {
            "schema_version": 1,
            "run_id": run_id,
            "current_collection_revision_id": revisions[-1].collection_revision_id,
            "revisions": [item.model_dump(mode="json") for item in revisions[-1].revision_history],
        }

    def collection_revision(self, run_id: str, revision_id: str) -> dict[str, object] | None:
        revision = next(
            (
                item
                for item in self._collections.get(run_id, ())
                if item.collection_revision_id == revision_id
            ),
            None,
        )
        return None if revision is None else _detached(revision)

    def scene(self, run_id: str, scene_id: str) -> dict[str, object] | None:
        current = self._current(run_id)
        if current is None:
            return None
        scene = next((item for item in current.scenes if item.scene_id == scene_id), None)
        return None if scene is None else _detached(scene)

    def scene_history(self, run_id: str, scene_id: str) -> dict[str, object] | None:
        if run_id not in self._collections:
            return None
        revisions: list[ScenePlanV1] = []
        seen: set[str] = set()
        for collection in self._collections[run_id]:
            scene = next((item for item in collection.scenes if item.scene_id == scene_id), None)
            if scene is not None and scene.scene_revision_id not in seen:
                revisions.append(scene)
                seen.add(scene.scene_revision_id)
        if not revisions:
            return None
        return {
            "schema_version": 1,
            "run_id": run_id,
            "scene_id": scene_id,
            "current_scene_revision_id": revisions[-1].scene_revision_id,
            "revisions": [_detached(item) for item in revisions],
        }


__all__ = [
    "ScenePlanAccessV1",
    "ScenePlanCollectionV1",
    "ScenePlanOperationResultV1",
    "ScenePlanStatus",
    "ScenePlanningStore",
]
