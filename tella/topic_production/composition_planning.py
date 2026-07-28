"""Process-local composition-planning authority over accepted visual candidates."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
import json
import math
import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    ValidationError,
    model_validator,
)


_SHA256 = r"^[0-9a-f]{64}$"
_HEX = re.compile(r"^#[0-9A-F]{6}(?:[0-9A-F]{2})?$")


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


class CompositionStatus(StrEnum):
    DRAFT = "DRAFT"
    VALID = "VALID"
    WARNING = "WARNING"
    BLOCKED = "BLOCKED"
    ACCEPTED_FOR_TIMELINE_PLANNING = "ACCEPTED_FOR_TIMELINE_PLANNING"
    REVISION_REQUESTED = "REVISION_REQUESTED"
    SUPERSEDED = "SUPERSEDED"


class FitMode(StrEnum):
    CONTAIN = "CONTAIN"
    COVER = "COVER"
    FIT_WIDTH = "FIT_WIDTH"
    FIT_HEIGHT = "FIT_HEIGHT"
    MANUAL_CROP = "MANUAL_CROP"


class MotionIntentMode(StrEnum):
    STATIC = "STATIC"
    SLOW_ZOOM_IN = "SLOW_ZOOM_IN"
    SLOW_ZOOM_OUT = "SLOW_ZOOM_OUT"
    PAN_LEFT = "PAN_LEFT"
    PAN_RIGHT = "PAN_RIGHT"
    PAN_UP = "PAN_UP"
    PAN_DOWN = "PAN_DOWN"
    CUSTOM_START_END = "CUSTOM_START_END"


class TransitionIntent(StrEnum):
    CUT = "CUT"
    CROSSFADE = "CROSSFADE"
    FADE_THROUGH_COLOR = "FADE_THROUGH_COLOR"
    DIP_TO_BLACK = "DIP_TO_BLACK"
    HOLD_THEN_CUT = "HOLD_THEN_CUT"


class CompositionReviewReason(StrEnum):
    FRAMING_UNSUITABLE = "FRAMING_UNSUITABLE"
    CROP_UNSUITABLE = "CROP_UNSUITABLE"
    SUBJECT_POSITION_UNSUITABLE = "SUBJECT_POSITION_UNSUITABLE"
    CHARACTER_SCALE_INCONSISTENT = "CHARACTER_SCALE_INCONSISTENT"
    SAFE_ZONE_CONFLICT = "SAFE_ZONE_CONFLICT"
    LAYER_ORDER_INCORRECT = "LAYER_ORDER_INCORRECT"
    MOTION_INTENT_UNSUITABLE = "MOTION_INTENT_UNSUITABLE"
    TRANSITION_INTENT_UNSUITABLE = "TRANSITION_INTENT_UNSUITABLE"
    CONTINUITY_MISMATCH = "CONTINUITY_MISMATCH"
    COLOR_TREATMENT_UNSUITABLE = "COLOR_TREATMENT_UNSUITABLE"
    OTHER_BOUNDED_NOTE = "OTHER_BOUNDED_NOTE"


class NormalizedGeometryV1(_Contract):
    x: StrictFloat = Field(ge=0.0, le=1.0)
    y: StrictFloat = Field(ge=0.0, le=1.0)
    width: StrictFloat = Field(gt=0.0, le=1.0)
    height: StrictFloat = Field(gt=0.0, le=1.0)
    anchor_x: StrictFloat = Field(ge=0.0, le=1.0)
    anchor_y: StrictFloat = Field(ge=0.0, le=1.0)
    scale: StrictFloat = Field(ge=0.5, le=2.0)
    rotation_degrees: StrictFloat = Field(ge=-10.0, le=10.0)
    opacity: StrictFloat = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def finite_and_visible(self) -> NormalizedGeometryV1:
        values = tuple(self.model_dump().values())
        if not all(math.isfinite(item) for item in values):
            raise ValueError("composition geometry must be finite")
        if self.x + self.width > 1.0 or self.y + self.height > 1.0:
            raise ValueError("composition geometry must remain in the output frame")
        return self


class NormalizedCropV1(_Contract):
    x: StrictFloat = Field(ge=0.0, le=1.0)
    y: StrictFloat = Field(ge=0.0, le=1.0)
    width: StrictFloat = Field(gt=0.0, le=1.0)
    height: StrictFloat = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def bounded_crop(self) -> NormalizedCropV1:
        if not all(math.isfinite(item) for item in self.model_dump().values()):
            raise ValueError("crop values must be finite")
        if self.x + self.width > 1.0 or self.y + self.height > 1.0:
            raise ValueError("crop must remain inside source bounds")
        if self.width * self.height < 0.1:
            raise ValueError("crop visible area is too small")
        return self


class SafeMarginsV1(_Contract):
    top: Literal[0.08] = 0.08
    bottom: Literal[0.12] = 0.12
    left: Literal[0.06] = 0.06
    right: Literal[0.06] = 0.06
    title_safe: Literal[0.1] = 0.1
    subtitle_safe: Literal[0.16] = 0.16


class CompositionLayerV1(_Contract):
    layer_id: str = Field(pattern=r"^composition-layer-[0-9]{2}$")
    layer_type: Literal["ACCEPTED_VISUAL", "SAFE_COLOR_WASH", "SAFE_GRADIENT_OVERLAY"]
    z_order: StrictInt = Field(ge=0, le=2)
    enabled: StrictBool
    geometry: NormalizedGeometryV1
    opacity: StrictFloat = Field(ge=0.0, le=1.0)
    color: str | None = None

    @model_validator(mode="after")
    def safe_color(self) -> CompositionLayerV1:
        if self.color is not None and not _HEX.fullmatch(self.color):
            raise ValueError("composition colors must be uppercase hexadecimal")
        if self.layer_type == "ACCEPTED_VISUAL" and (
            self.z_order != 0 or not self.enabled or self.color is not None
        ):
            raise ValueError("accepted visual layer is required at z-order zero")
        return self


class MotionIntentV1(_Contract):
    mode: MotionIntentMode
    start_scale: StrictFloat = Field(ge=0.8, le=1.5)
    end_scale: StrictFloat = Field(ge=0.8, le=1.5)
    start_anchor_x: StrictFloat = Field(ge=0.0, le=1.0)
    start_anchor_y: StrictFloat = Field(ge=0.0, le=1.0)
    end_anchor_x: StrictFloat = Field(ge=0.0, le=1.0)
    end_anchor_y: StrictFloat = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def bounded_motion(self) -> MotionIntentV1:
        values = tuple(self.model_dump().values())[1:]
        if not all(math.isfinite(item) for item in values):
            raise ValueError("motion values must be finite")
        if abs(self.end_scale - self.start_scale) > 0.35:
            raise ValueError("motion scale delta is too large")
        if (
            abs(self.end_anchor_x - self.start_anchor_x) > 0.35
            or abs(self.end_anchor_y - self.start_anchor_y) > 0.35
        ):
            raise ValueError("motion anchor delta is too large")
        return self


class CompositionValidationV1(_Contract):
    valid: StrictBool
    blocker_codes: tuple[str, ...] = ()
    warning_codes: tuple[str, ...] = ()
    information_codes: tuple[str, ...] = ("HUMAN_COMPOSITION_REVIEW_REQUIRED",)


class CompositionReviewEntryV1(_Contract):
    review_id: str = Field(pattern=r"^composition-review-[0-9]{4}$")
    action: Literal["ACCEPTED", "REVISION_REQUESTED", "SUPERSEDED"]
    reason_code: str
    note: str | None = Field(default=None, max_length=500)
    composition_revision_id: str
    created_at: str


class SourceCoverageV1(_Contract):
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self) -> SourceCoverageV1:
        if self.end <= self.start:
            raise ValueError("source coverage must be ordered")
        return self


class SceneCompositionV1(_Contract):
    schema_version: Literal[1] = 1
    composition_id: str = Field(pattern=r"^scene-composition-[0-9]{4}$")
    composition_revision_id: str = Field(pattern=r"^composition-revision-[0-9]{4}$")
    composition_revision_number: StrictInt = Field(ge=1)
    position: StrictInt = Field(ge=1)
    run_id: str
    story_revision_id: str
    scene_plan_collection_revision_id: str
    scene_id: str
    scene_revision_id: str
    visual_collection_revision_id: str
    accepted_candidate_id: str
    accepted_candidate_revision_id: str
    accepted_candidate_sha256: str = Field(pattern=_SHA256)
    artifact_url: str
    source_width: StrictInt = Field(ge=64, le=1920)
    source_height: StrictInt = Field(ge=64, le=1920)
    source_mime: Literal["image/png", "image/jpeg", "image/webp"]
    source_coverage: SourceCoverageV1
    semantic_beat_id: str
    planned_duration_seconds: StrictFloat = Field(gt=0.0, le=60.0)
    aspect_ratio: Literal["9:16"] = "9:16"
    composition_contract_version: Literal["composition_planning_v1"] = "composition_planning_v1"
    status: CompositionStatus
    fit_mode: FitMode
    crop: NormalizedCropV1
    placement: NormalizedGeometryV1
    safe_margins: SafeMarginsV1
    layers: tuple[CompositionLayerV1, ...]
    motion_intent: MotionIntentV1
    transition_intent: TransitionIntent
    transition_duration_seconds: StrictFloat = Field(ge=0.0, le=2.0)
    note: str | None = Field(default=None, max_length=500)
    validation: CompositionValidationV1
    continuity_codes: tuple[str, ...] = ()
    changed_fields: tuple[str, ...] = ()
    review_history: tuple[CompositionReviewEntryV1, ...] = ()
    accepted_for_timeline_planning: StrictBool = False
    superseded_reason: str | None = None
    created_at: str
    render_authority: Literal[False] = False
    renderer_execution_authority: Literal[False] = False
    video_render_authority: Literal[False] = False
    timeline_execution_authority: Literal[False] = False
    final_media_capability: Literal[False] = False
    narration_generation_capability: Literal[False] = False
    tts_capability: Literal[False] = False

    @model_validator(mode="after")
    def consistent(self) -> SceneCompositionV1:
        if [item.z_order for item in self.layers] != list(range(len(self.layers))):
            raise ValueError("composition layer ordering must be contiguous")
        if len({item.layer_id for item in self.layers}) != len(self.layers):
            raise ValueError("composition layer identities must be unique")
        if not self.layers or self.layers[0].layer_type != "ACCEPTED_VISUAL":
            raise ValueError("composition requires an accepted visual base layer")
        accepted = self.status is CompositionStatus.ACCEPTED_FOR_TIMELINE_PLANNING
        if accepted != self.accepted_for_timeline_planning:
            raise ValueError("composition acceptance summary must match status")
        if accepted and not self.validation.valid:
            raise ValueError("blocked composition cannot be accepted")
        if self.fit_mode is not FitMode.MANUAL_CROP and self.crop != NormalizedCropV1(
            x=0.0, y=0.0, width=1.0, height=1.0
        ):
            raise ValueError("non-manual fit modes require the full source crop")
        if self.transition_duration_seconds > self.planned_duration_seconds * 0.25:
            raise ValueError("transition exceeds bounded scene-duration fraction")
        if "://" in self.artifact_url or not self.artifact_url.startswith("/api/v1/plan-only/"):
            raise ValueError("composition artifact URL must be registered and same-origin")
        if self.source_width * 16 != self.source_height * 9:
            raise ValueError("composition source must preserve the accepted 9:16 ratio")
        if len(self.changed_fields) != len(set(self.changed_fields)):
            raise ValueError("composition changed-field audit must be unique")
        if len({item.review_id for item in self.review_history}) != len(self.review_history):
            raise ValueError("composition review identities must be unique")
        return self


class CompositionCollectionV1(_Contract):
    schema_version: Literal[1] = 1
    collection_id: str = Field(pattern=r"^composition-collection-[0-9]{4}$")
    collection_revision_id: str = Field(pattern=r"^composition-collection-revision-[0-9]{4}$")
    collection_revision_number: StrictInt = Field(ge=1)
    run_id: str
    story_revision_id: str
    scene_plan_collection_revision_id: str
    compositions: tuple[SceneCompositionV1, ...]
    missing_scene_ids: tuple[str, ...]
    total_scene_count: StrictInt = Field(ge=1)
    valid_composition_count: StrictInt = Field(ge=0)
    warning_composition_count: StrictInt = Field(ge=0)
    blocked_composition_count: StrictInt = Field(ge=0)
    accepted_for_timeline_planning_count: StrictInt = Field(ge=0)
    overall_ready_for_timeline_planning: StrictBool
    created_at: str
    render_authority: Literal[False] = False
    renderer_execution_authority: Literal[False] = False
    video_render_authority: Literal[False] = False
    timeline_execution_authority: Literal[False] = False
    final_media_capability: Literal[False] = False

    @model_validator(mode="after")
    def summaries(self) -> CompositionCollectionV1:
        if [item.position for item in self.compositions] != list(
            range(1, len(self.compositions) + 1)
        ):
            raise ValueError("composition ordering must be contiguous")
        for values in (
            [item.scene_id for item in self.compositions],
            [item.composition_id for item in self.compositions],
            [item.composition_revision_id for item in self.compositions],
        ):
            if len(values) != len(set(values)):
                raise ValueError("composition identities must be unique")
        if len(self.missing_scene_ids) != len(set(self.missing_scene_ids)):
            raise ValueError("missing scene identities must be unique")
        if set(self.missing_scene_ids).intersection(item.scene_id for item in self.compositions):
            raise ValueError("present and missing composition scenes must be disjoint")
        if len(self.compositions) + len(self.missing_scene_ids) != self.total_scene_count:
            raise ValueError("composition collection scene count is inconsistent")
        if any(
            item.run_id != self.run_id
            or item.story_revision_id != self.story_revision_id
            or item.scene_plan_collection_revision_id != self.scene_plan_collection_revision_id
            for item in self.compositions
        ):
            raise ValueError("composition collection source bindings are inconsistent")
        valid = sum(item.validation.valid for item in self.compositions)
        warning = sum(bool(item.validation.warning_codes) for item in self.compositions)
        blocked = len(self.compositions) - valid + len(self.missing_scene_ids)
        accepted = sum(item.accepted_for_timeline_planning for item in self.compositions)
        ready = not self.missing_scene_ids and accepted == self.total_scene_count
        if (
            self.valid_composition_count,
            self.warning_composition_count,
            self.blocked_composition_count,
            self.accepted_for_timeline_planning_count,
            self.overall_ready_for_timeline_planning,
        ) != (valid, warning, blocked, accepted, ready):
            raise ValueError("composition collection summaries are inconsistent")
        return self


class CompositionAccessV1(_Contract):
    schema_version: Literal[1] = 1
    run_id: str
    editable: StrictBool
    initialization_authorized: StrictBool
    blocker_codes: tuple[str, ...]
    current_story_revision_id: str | None
    current_scene_plan_collection_revision_id: str | None
    collection: CompositionCollectionV1 | None
    process_local: Literal[True] = True
    render_authority: Literal[False] = False
    renderer_execution_authority: Literal[False] = False
    video_render_authority: Literal[False] = False
    timeline_execution_authority: Literal[False] = False
    final_media_capability: Literal[False] = False
    narration_generation_capability: Literal[False] = False
    tts_capability: Literal[False] = False


class InitializeCompositionRequestV1(_Contract):
    schema_version: Literal[1]
    scene_plan_collection_revision_id: str


class CompositionEditV1(_Contract):
    fit_mode: FitMode | None = None
    crop: NormalizedCropV1 | None = None
    placement: NormalizedGeometryV1 | None = None
    motion_intent: MotionIntentV1 | None = None
    transition_intent: TransitionIntent | None = None
    transition_duration_seconds: StrictFloat | None = Field(default=None, ge=0.0, le=2.0)
    overlay_color: str | None = None
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def meaningful_safe_edit(self) -> CompositionEditV1:
        if not self.model_fields_set:
            raise ValueError("composition edit must contain a changed field")
        if self.overlay_color is not None:
            if not _HEX.fullmatch(self.overlay_color):
                raise ValueError("overlay color must be uppercase hexadecimal")
        for value in (self.note,):
            if value is not None and (not value.strip() or "://" in value or "<" in value):
                raise ValueError("composition note must be presentation-safe")
        return self


class SaveCompositionRequestV1(_Contract):
    schema_version: Literal[1]
    base_collection_revision_id: str
    base_composition_revision_id: str
    changes: CompositionEditV1


class RestoreCompositionRequestV1(_Contract):
    schema_version: Literal[1]
    base_collection_revision_id: str
    base_composition_revision_id: str
    restore_composition_revision_id: str
    reason: str = Field(min_length=1, max_length=300)


class ReviewCompositionRequestV1(_Contract):
    schema_version: Literal[1]
    base_collection_revision_id: str
    base_composition_revision_id: str
    reason_code: CompositionReviewReason
    note: str | None = Field(default=None, max_length=500)


def _detached(model: BaseModel) -> dict[str, object]:
    return json.loads(model.model_dump_json())


def _timestamp(number: int) -> str:
    return (datetime(2026, 2, 1, tzinfo=UTC) + timedelta(seconds=number)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


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


class CompositionPlanningStore:
    """Immutable process-local composition collections."""

    def __init__(self) -> None:
        self._histories: dict[str, tuple[CompositionCollectionV1, ...]] = {}
        self._composition_histories: dict[tuple[str, str], tuple[SceneCompositionV1, ...]] = {}
        self._collection_identity = 1
        self._collection_revision_identity = 1
        self._composition_identity = 1
        self._composition_revision_identity = 1
        self._review_identity = 1

    def _current(self, run_id: str) -> CompositionCollectionV1 | None:
        history = self._histories.get(run_id, ())
        return history[-1] if history else None

    @staticmethod
    def _composition_source_identity(item: SceneCompositionV1) -> tuple[object, ...]:
        return (
            item.position,
            item.run_id,
            item.story_revision_id,
            item.scene_plan_collection_revision_id,
            item.scene_id,
            item.scene_revision_id,
            item.visual_collection_revision_id,
            item.accepted_candidate_id,
            item.accepted_candidate_revision_id,
            item.accepted_candidate_sha256,
            item.artifact_url,
            item.source_width,
            item.source_height,
            item.source_mime,
            item.source_coverage,
            item.semantic_beat_id,
            item.planned_duration_seconds,
            item.aspect_ratio,
            item.continuity_codes,
        )

    @staticmethod
    def _accepted_sources(
        scene_access: Mapping[str, Any] | None,
        visual_accesses: Mapping[str, Mapping[str, Any]],
        artifact_validity: Mapping[str, bool],
    ) -> tuple[list[tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]], list[str]]:
        collection = None if scene_access is None else scene_access.get("collection")
        if not isinstance(collection, Mapping):
            return [], []
        sources = []
        missing = []
        for scene in collection.get("scenes", ()):
            scene_id = str(scene["scene_id"])
            visual = visual_accesses.get(scene_id)
            visual_collection = None if visual is None else visual.get("collection")
            accepted_id = (
                None
                if not isinstance(visual_collection, Mapping)
                else visual_collection.get("current_accepted_candidate_id")
            )
            candidate = (
                None
                if accepted_id is None
                else next(
                    (
                        item
                        for item in visual_collection.get("candidates", ())
                        if item.get("candidate_id") == accepted_id
                    ),
                    None,
                )
            )
            if (
                scene.get("status") != "ACCEPTED_FOR_VISUAL_PLANNING"
                or candidate is None
                or candidate.get("status") != "ACCEPTED_FOR_COMPOSITION_PLANNING"
                or candidate.get("technical_validation", {}).get("passed") is not True
                or candidate.get("visual_qc", {}).get("blocker_codes")
                or not artifact_validity.get(scene_id, False)
            ):
                missing.append(scene_id)
            else:
                sources.append((scene, visual_collection, candidate))
        return sources, missing

    @staticmethod
    def _global_blockers(
        run: Mapping[str, Any] | None,
        review: Mapping[str, Any] | None,
        scene_access: Mapping[str, Any] | None,
    ) -> list[str]:
        blockers = []
        if run is None:
            return ["UNKNOWN_RUN"]
        if run.get("status") != "PLANNED":
            blockers.append("RUN_NOT_PLANNED")
        if review is None or review.get("review_status") != "ACCEPTED_FOR_SCENE_PLANNING":
            blockers.append("STORYPLAN_NOT_ACCEPTED")
        elif review.get("current_revision_id") != review.get("accepted_revision_id"):
            blockers.append("STALE_ACCEPTED_STORYPLAN")
        collection = None if scene_access is None else scene_access.get("collection")
        if not isinstance(collection, Mapping):
            blockers.append("SCENE_PLAN_UNAVAILABLE")
        elif review is not None and (
            collection.get("source_story_revision_id") != review.get("current_revision_id")
            or collection.get("source_coverage_valid") is not True
            or collection.get("duration_valid") is not True
        ):
            blockers.append("STALE_OR_INVALID_SCENE_PLAN")
        return blockers

    @staticmethod
    def _binding(
        source: tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]],
    ) -> tuple[Any, ...]:
        scene, visual, candidate = source
        return (
            scene["scene_revision_id"],
            visual["visual_collection_revision_id"],
            candidate["candidate_id"],
            candidate["candidate_revision_id"],
            candidate["sha256"],
            candidate["width"],
            candidate["height"],
            candidate["mime_type"],
            tuple(sorted(candidate["source_coverage"].items())),
            float(scene["planned_duration_seconds"]),
            tuple(sorted(scene["continuity"].items())),
        )

    def _supersede(self, current: CompositionCollectionV1, reason: str) -> None:
        changed = []
        for item in current.compositions:
            if item.status is CompositionStatus.SUPERSEDED:
                changed.append(item)
                continue
            revision_id = f"composition-revision-{self._composition_revision_identity:04d}"
            self._composition_revision_identity += 1
            review = CompositionReviewEntryV1(
                review_id=f"composition-review-{self._review_identity:04d}",
                action="SUPERSEDED",
                reason_code=reason,
                composition_revision_id=revision_id,
                created_at=_timestamp(self._review_identity),
            )
            self._review_identity += 1
            updated = SceneCompositionV1.model_validate(
                {
                    **item.model_dump(mode="python"),
                    "composition_revision_id": revision_id,
                    "composition_revision_number": item.composition_revision_number + 1,
                    "status": CompositionStatus.SUPERSEDED,
                    "accepted_for_timeline_planning": False,
                    "superseded_reason": reason,
                    "review_history": (*item.review_history, review),
                    "changed_fields": ("review_history", "status"),
                }
            )
            changed.append(updated)
            key = (current.run_id, item.scene_id)
            self._composition_histories[key] = (*self._composition_histories[key], updated)
        if changed:
            self._commit(current, tuple(changed), current.missing_scene_ids)

    def access(
        self,
        *,
        run: Mapping[str, Any] | None,
        review: Mapping[str, Any] | None,
        scene_access: Mapping[str, Any] | None,
        visual_accesses: Mapping[str, Mapping[str, Any]],
        artifact_validity: Mapping[str, bool],
    ) -> dict[str, object] | None:
        if run is None:
            return None
        run_id = str(run["run_id"])
        blockers = self._global_blockers(run, review, scene_access)
        sources, missing = self._accepted_sources(scene_access, visual_accesses, artifact_validity)
        current = self._current(run_id)
        if current is not None:
            source_by_scene = {str(item[0]["scene_id"]): item for item in sources}
            stale = any(
                item.scene_id not in source_by_scene
                or self._binding(source_by_scene[item.scene_id])
                != (
                    item.scene_revision_id,
                    item.visual_collection_revision_id,
                    item.accepted_candidate_id,
                    item.accepted_candidate_revision_id,
                    item.accepted_candidate_sha256,
                    item.source_width,
                    item.source_height,
                    item.source_mime,
                    tuple(sorted(item.source_coverage.model_dump().items())),
                    item.planned_duration_seconds,
                    tuple(
                        sorted(
                            (
                                code,
                                source_by_scene[item.scene_id][0]["continuity"][code],
                            )
                            for code in item.continuity_codes
                        )
                    ),
                )
                for item in current.compositions
            )
            if stale and any(
                item.status is not CompositionStatus.SUPERSEDED for item in current.compositions
            ):
                self._supersede(current, "UPSTREAM_COMPOSITION_AUTHORITY_CHANGED")
                current = None
            elif all(item.status is CompositionStatus.SUPERSEDED for item in current.compositions):
                current = None
        effective = tuple(
            dict.fromkeys((*blockers, *(("MISSING_ACCEPTED_VISUAL",) if missing else ())))
        )
        return _detached(
            CompositionAccessV1(
                run_id=run_id,
                editable=not blockers and current is not None and bool(current.compositions),
                initialization_authorized=not blockers and bool(sources),
                blocker_codes=effective,
                current_story_revision_id=(
                    None if review is None else str(review["current_revision_id"])
                ),
                current_scene_plan_collection_revision_id=(
                    None
                    if scene_access is None
                    or not isinstance(scene_access.get("collection"), Mapping)
                    else str(scene_access["collection"]["collection_revision_id"])
                ),
                collection=current,
            )
        )

    def _initial_composition(
        self,
        run_id: str,
        story_revision_id: str,
        scene_collection_revision_id: str,
        position: int,
        source: tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]],
    ) -> SceneCompositionV1:
        scene, visual, candidate = source
        composition_id = f"scene-composition-{self._composition_identity:04d}"
        revision_id = f"composition-revision-{self._composition_revision_identity:04d}"
        self._composition_identity += 1
        self._composition_revision_identity += 1
        placement = NormalizedGeometryV1(
            x=0.0,
            y=0.0,
            width=1.0,
            height=1.0,
            anchor_x=0.5,
            anchor_y=0.5,
            scale=1.0,
            rotation_degrees=0.0,
            opacity=1.0,
        )
        return SceneCompositionV1(
            composition_id=composition_id,
            composition_revision_id=revision_id,
            composition_revision_number=1,
            position=position,
            run_id=run_id,
            story_revision_id=story_revision_id,
            scene_plan_collection_revision_id=scene_collection_revision_id,
            scene_id=scene["scene_id"],
            scene_revision_id=scene["scene_revision_id"],
            visual_collection_revision_id=visual["visual_collection_revision_id"],
            accepted_candidate_id=candidate["candidate_id"],
            accepted_candidate_revision_id=candidate["candidate_revision_id"],
            accepted_candidate_sha256=candidate["sha256"],
            artifact_url=candidate["artifact_url"],
            source_width=candidate["width"],
            source_height=candidate["height"],
            source_mime=candidate["mime_type"],
            source_coverage=candidate["source_coverage"],
            semantic_beat_id=scene["source_beat_id"],
            planned_duration_seconds=float(scene["planned_duration_seconds"]),
            status=CompositionStatus.VALID,
            fit_mode=FitMode.COVER,
            crop=NormalizedCropV1(x=0.0, y=0.0, width=1.0, height=1.0),
            placement=placement,
            safe_margins=SafeMarginsV1(),
            layers=(
                CompositionLayerV1(
                    layer_id="composition-layer-00",
                    layer_type="ACCEPTED_VISUAL",
                    z_order=0,
                    enabled=True,
                    geometry=placement,
                    opacity=1.0,
                ),
            ),
            motion_intent=MotionIntentV1(
                mode=MotionIntentMode.STATIC,
                start_scale=1.0,
                end_scale=1.0,
                start_anchor_x=0.5,
                start_anchor_y=0.5,
                end_anchor_x=0.5,
                end_anchor_y=0.5,
            ),
            transition_intent=TransitionIntent.CUT,
            transition_duration_seconds=0.0,
            validation=CompositionValidationV1(valid=True),
            continuity_codes=tuple(scene.get("continuity", {}).keys()),
            created_at=_timestamp(self._composition_revision_identity),
        )

    def initialize(
        self,
        *,
        run: Mapping[str, Any] | None,
        review: Mapping[str, Any] | None,
        scene_access: Mapping[str, Any] | None,
        visual_accesses: Mapping[str, Mapping[str, Any]],
        artifact_validity: Mapping[str, bool],
        payload: object,
    ) -> dict[str, object]:
        try:
            request = InitializeCompositionRequestV1.model_validate(payload)
        except ValidationError:
            return _error(
                "INVALID_COMPOSITION_INITIALIZATION",
                "Composition initialization request is malformed.",
            )
        if run is None or review is None or scene_access is None:
            return _error("COMPOSITION_NOT_AUTHORIZED", "Composition planning is unavailable.")
        blockers = self._global_blockers(run, review, scene_access)
        scene_collection = scene_access["collection"]
        if (
            request.scene_plan_collection_revision_id != scene_collection["collection_revision_id"]
            or blockers
        ):
            return _error("STALE_COMPOSITION_AUTHORITY", "Composition source authority changed.")
        sources, missing = self._accepted_sources(scene_access, visual_accesses, artifact_validity)
        if not sources:
            return _error("MISSING_ACCEPTED_VISUAL", "No accepted visual candidate is available.")
        run_id = str(run["run_id"])
        current = self._current(run_id)
        if current is not None and any(
            item.status is not CompositionStatus.SUPERSEDED for item in current.compositions
        ):
            return _error(
                "COMPOSITION_ALREADY_INITIALIZED", "Current composition collection already exists."
            )
        source_by_scene = {str(item[0]["scene_id"]): item for item in sources}
        compositions = []
        for scene in scene_collection["scenes"]:
            source = source_by_scene.get(str(scene["scene_id"]))
            if source is not None:
                compositions.append(
                    self._initial_composition(
                        run_id,
                        review["current_revision_id"],
                        scene_collection["collection_revision_id"],
                        len(compositions) + 1,
                        source,
                    )
                )
        self._new_collection(
            run_id,
            review["current_revision_id"],
            scene_collection["collection_revision_id"],
            tuple(compositions),
            tuple(missing),
            len(scene_collection["scenes"]),
        )
        for item in compositions:
            key = (run_id, item.scene_id)
            self._composition_histories[key] = (
                *self._composition_histories.get(key, ()),
                item,
            )
        return {
            "schema_version": 1,
            "access": self.access(
                run=run,
                review=review,
                scene_access=scene_access,
                visual_accesses=visual_accesses,
                artifact_validity=artifact_validity,
            ),
            "error": None,
        }

    def _new_collection(
        self,
        run_id: str,
        story_revision_id: str,
        scene_collection_revision_id: str,
        compositions: tuple[SceneCompositionV1, ...],
        missing: tuple[str, ...],
        total: int,
    ) -> CompositionCollectionV1:
        collection = CompositionCollectionV1(
            collection_id=f"composition-collection-{self._collection_identity:04d}",
            collection_revision_id=f"composition-collection-revision-{self._collection_revision_identity:04d}",
            collection_revision_number=1,
            run_id=run_id,
            story_revision_id=story_revision_id,
            scene_plan_collection_revision_id=scene_collection_revision_id,
            compositions=compositions,
            missing_scene_ids=missing,
            total_scene_count=total,
            valid_composition_count=sum(item.validation.valid for item in compositions),
            warning_composition_count=sum(
                bool(item.validation.warning_codes) for item in compositions
            ),
            blocked_composition_count=len(compositions)
            - sum(item.validation.valid for item in compositions)
            + len(missing),
            accepted_for_timeline_planning_count=sum(
                item.accepted_for_timeline_planning for item in compositions
            ),
            overall_ready_for_timeline_planning=not missing
            and all(item.accepted_for_timeline_planning for item in compositions)
            and len(compositions) == total,
            created_at=_timestamp(self._collection_revision_identity),
        )
        self._collection_identity += 1
        self._collection_revision_identity += 1
        self._histories[run_id] = (*self._histories.get(run_id, ()), collection)
        return collection

    def _commit(
        self,
        current: CompositionCollectionV1,
        compositions: tuple[SceneCompositionV1, ...],
        missing: tuple[str, ...],
    ) -> CompositionCollectionV1:
        payload = current.model_dump(mode="python")
        payload.update(
            collection_revision_id=f"composition-collection-revision-{self._collection_revision_identity:04d}",
            collection_revision_number=current.collection_revision_number + 1,
            compositions=compositions,
            missing_scene_ids=missing,
            valid_composition_count=sum(item.validation.valid for item in compositions),
            warning_composition_count=sum(
                bool(item.validation.warning_codes) for item in compositions
            ),
            blocked_composition_count=len(compositions)
            - sum(item.validation.valid for item in compositions)
            + len(missing),
            accepted_for_timeline_planning_count=sum(
                item.accepted_for_timeline_planning for item in compositions
            ),
            overall_ready_for_timeline_planning=not missing
            and all(item.accepted_for_timeline_planning for item in compositions)
            and len(compositions) == current.total_scene_count,
            created_at=_timestamp(self._collection_revision_identity),
        )
        self._collection_revision_identity += 1
        committed = CompositionCollectionV1.model_validate(payload)
        self._histories[current.run_id] = (*self._histories[current.run_id], committed)
        return committed

    def _mutation_authority(
        self, run_id: str, scene_id: str, collection_revision: str, composition_revision: str
    ) -> tuple[CompositionCollectionV1, SceneCompositionV1] | dict[str, object]:
        current = self._current(run_id)
        if current is None or current.collection_revision_id != collection_revision:
            return _error("STALE_COMPOSITION_COLLECTION", "Composition collection changed.")
        selected = next((item for item in current.compositions if item.scene_id == scene_id), None)
        if selected is None:
            return _error("UNKNOWN_COMPOSITION", "Scene composition was not found.")
        if selected.composition_revision_id != composition_revision:
            return _error("STALE_COMPOSITION_REVISION", "Scene composition revision changed.")
        if selected.status is CompositionStatus.SUPERSEDED:
            return _error("SUPERSEDED_COMPOSITION", "Superseded composition cannot be mutated.")
        return current, selected

    def save(self, run_id: str, scene_id: str, payload: object) -> dict[str, object]:
        try:
            request = SaveCompositionRequestV1.model_validate(payload)
        except ValidationError:
            return _error(
                "INVALID_COMPOSITION_EDIT", "Composition edit is malformed or unsupported."
            )
        authority = self._mutation_authority(
            run_id,
            scene_id,
            request.base_collection_revision_id,
            request.base_composition_revision_id,
        )
        if isinstance(authority, dict):
            return authority
        current, selected = authority
        changes = request.changes.model_dump(exclude_unset=True, mode="python")
        updated_payload = selected.model_dump(mode="python")
        if "overlay_color" in changes:
            color = changes.pop("overlay_color")
            layers = [
                item.model_dump(mode="python")
                for item in selected.layers
                if item.layer_type != "SAFE_COLOR_WASH"
            ]
            if color is not None:
                layers.append(
                    CompositionLayerV1(
                        layer_id=f"composition-layer-{len(layers):02d}",
                        layer_type="SAFE_COLOR_WASH",
                        z_order=len(layers),
                        enabled=True,
                        geometry=selected.placement,
                        opacity=0.2,
                        color=color,
                    )
                )
            changes["layers"] = tuple(layers)
        if all(updated_payload.get(key) == value for key, value in changes.items()):
            return _error(
                "NO_OP_COMPOSITION_EDIT", "Composition edit did not change current values."
            )
        revision_id = f"composition-revision-{self._composition_revision_identity:04d}"
        self._composition_revision_identity += 1
        updated_payload.update(changes)
        updated_payload.update(
            composition_revision_id=revision_id,
            composition_revision_number=selected.composition_revision_number + 1,
            status=CompositionStatus.VALID,
            accepted_for_timeline_planning=False,
            changed_fields=tuple(sorted(changes)),
            created_at=_timestamp(self._composition_revision_identity),
        )
        try:
            updated = SceneCompositionV1.model_validate(updated_payload)
        except ValidationError:
            return _error(
                "INVALID_COMPOSITION_EDIT", "Composition edit violates bounded planning policy."
            )
        compositions = tuple(
            updated if item.scene_id == scene_id else item for item in current.compositions
        )
        self._commit(current, compositions, current.missing_scene_ids)
        self._composition_histories[(run_id, scene_id)] = (
            *self._composition_histories[(run_id, scene_id)],
            updated,
        )
        return {"schema_version": 1, "collection": _detached(self._current(run_id)), "error": None}

    def restore(self, run_id: str, scene_id: str, payload: object) -> dict[str, object]:
        try:
            request = RestoreCompositionRequestV1.model_validate(payload)
        except ValidationError:
            return _error(
                "INVALID_COMPOSITION_RESTORE", "Composition restore request is malformed."
            )
        authority = self._mutation_authority(
            run_id,
            scene_id,
            request.base_collection_revision_id,
            request.base_composition_revision_id,
        )
        if isinstance(authority, dict):
            return authority
        current, selected = authority
        source = next(
            (
                item
                for item in self._composition_histories[(run_id, scene_id)]
                if item.composition_revision_id == request.restore_composition_revision_id
            ),
            None,
        )
        if source is None:
            return _error("UNKNOWN_COMPOSITION_REVISION", "Composition revision was not found.")
        if self._composition_source_identity(source) != self._composition_source_identity(selected):
            return _error(
                "INCOMPATIBLE_COMPOSITION_RESTORE",
                "Historical composition source authority differs from the current source.",
            )
        payload_item = source.model_dump(mode="python")
        payload_item.update(
            composition_revision_id=f"composition-revision-{self._composition_revision_identity:04d}",
            composition_revision_number=selected.composition_revision_number + 1,
            status=CompositionStatus.VALID,
            accepted_for_timeline_planning=False,
            review_history=selected.review_history,
            changed_fields=("restore_composition_revision_id",),
            created_at=_timestamp(self._composition_revision_identity),
        )
        self._composition_revision_identity += 1
        restored = SceneCompositionV1.model_validate(payload_item)
        self._commit(
            current,
            tuple(restored if item.scene_id == scene_id else item for item in current.compositions),
            current.missing_scene_ids,
        )
        self._composition_histories[(run_id, scene_id)] = (
            *self._composition_histories[(run_id, scene_id)],
            restored,
        )
        return {"schema_version": 1, "collection": _detached(self._current(run_id)), "error": None}

    def review(
        self,
        run_id: str,
        scene_id: str,
        operation: Literal["accept", "request-revision"],
        payload: object,
    ) -> dict[str, object]:
        try:
            request = ReviewCompositionRequestV1.model_validate(payload)
        except ValidationError:
            return _error("INVALID_COMPOSITION_REVIEW", "Composition review request is malformed.")
        authority = self._mutation_authority(
            run_id,
            scene_id,
            request.base_collection_revision_id,
            request.base_composition_revision_id,
        )
        if isinstance(authority, dict):
            return authority
        current, selected = authority
        if operation == "accept" and selected.accepted_for_timeline_planning:
            return _error("COMPOSITION_ALREADY_ACCEPTED", "Composition is already accepted.")
        if operation == "accept" and not selected.validation.valid:
            return _error("COMPOSITION_ACCEPTANCE_BLOCKED", "Composition has unresolved blockers.")
        revision_id = f"composition-revision-{self._composition_revision_identity:04d}"
        self._composition_revision_identity += 1
        action = "ACCEPTED" if operation == "accept" else "REVISION_REQUESTED"
        status = (
            CompositionStatus.ACCEPTED_FOR_TIMELINE_PLANNING
            if operation == "accept"
            else CompositionStatus.REVISION_REQUESTED
        )
        review = CompositionReviewEntryV1(
            review_id=f"composition-review-{self._review_identity:04d}",
            action=action,
            reason_code=request.reason_code.value,
            note=request.note,
            composition_revision_id=revision_id,
            created_at=_timestamp(self._review_identity),
        )
        self._review_identity += 1
        updated = SceneCompositionV1.model_validate(
            {
                **selected.model_dump(mode="python"),
                "composition_revision_id": revision_id,
                "composition_revision_number": selected.composition_revision_number + 1,
                "status": status,
                "accepted_for_timeline_planning": operation == "accept",
                "review_history": (*selected.review_history, review),
                "changed_fields": ("review_history", "status"),
                "created_at": _timestamp(self._composition_revision_identity),
            }
        )
        self._commit(
            current,
            tuple(updated if item.scene_id == scene_id else item for item in current.compositions),
            current.missing_scene_ids,
        )
        self._composition_histories[(run_id, scene_id)] = (
            *self._composition_histories[(run_id, scene_id)],
            updated,
        )
        return {"schema_version": 1, "collection": _detached(self._current(run_id)), "error": None}

    def composition(self, run_id: str, scene_id: str) -> dict[str, object] | None:
        current = self._current(run_id)
        if current is not None:
            selected = next(
                (item for item in current.compositions if item.scene_id == scene_id), None
            )
            if selected is not None:
                return _detached(selected)
        history = self._composition_histories.get((run_id, scene_id), ())
        return None if not history else _detached(history[-1])

    def composition_history(self, run_id: str, scene_id: str) -> dict[str, object] | None:
        history = self._composition_histories.get((run_id, scene_id))
        return (
            None
            if history is None
            else {
                "schema_version": 1,
                "run_id": run_id,
                "scene_id": scene_id,
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
    "CompositionPlanningStore",
    "CompositionStatus",
    "FitMode",
    "MotionIntentMode",
    "TransitionIntent",
]
