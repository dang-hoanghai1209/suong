"""Provider-free domain contracts for future script-to-video entry points.

The authoritative source value is DECODED_TEXT_CONTENT: a Python Unicode
string. This module does not retain input encoding metadata or raw file bytes.
It never strips, cleans, normalizes, or rewrites authoritative source text.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol, Self
import unicodedata

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from .execution_models import ExecutionMode
from .strategy import VisualExecutionMode

if TYPE_CHECKING:
    from .models import StoryPlan


MAX_SCENE_HINTS = 64
MAX_HINT_ITEMS = 8
MAX_HINT_ITEM_LENGTH = 120


class _ValidatedFrozenModel(BaseModel):
    """Frozen model whose copies and nested instances are always revalidated."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
    )

    def model_copy(
        self,
        *,
        update: dict[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(mode="python")
        if deep:
            payload = deepcopy(payload)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)


class ScriptInputMode(StrEnum):
    TOPIC = "TOPIC"
    NARRATION = "NARRATION"
    NARRATION_WITH_SCENE_HINTS = "NARRATION_WITH_SCENE_HINTS"


class SourceContentAuthority(StrEnum):
    DECODED_TEXT_CONTENT = "DECODED_TEXT_CONTENT"


class NarrationAuthority(StrEnum):
    STORY_PLAN = "story_plan"
    EXACT_SOURCE = "exact_source"


class CharacterRoleRequest(StrEnum):
    FEMALE = "female"
    MALE = "male"
    CHILD = "child"
    UNKNOWN = "unknown"


class RelationshipScope(StrEnum):
    NONE = "none"
    COUPLE = "couple"
    FAMILY = "family"
    UNRESOLVED = "unresolved"


class RecurringCharacterRequest(_ValidatedFrozenModel):
    """Narrow input declaration; not a replacement for planner character types."""

    character_id: str = Field(min_length=1, max_length=80)
    role: CharacterRoleRequest

    @field_validator("character_id")
    @classmethod
    def normalize_character_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("character_id cannot be blank")
        if _contains_control_characters(normalized):
            raise ValueError("character_id cannot contain control characters")
        return normalized


class CharacterScopeRequest(_ValidatedFrozenModel):
    """Caller declaration only; absence is unresolved, never female."""

    recurring_characters: tuple[RecurringCharacterRequest, ...] = Field(
        default_factory=tuple, max_length=8
    )
    anonymous_background_people: bool = False
    relationship: RelationshipScope = RelationshipScope.NONE

    @model_validator(mode="after")
    def canonicalize_characters(self) -> "CharacterScopeRequest":
        by_id: dict[str, RecurringCharacterRequest] = {}
        for character in self.recurring_characters:
            key = character.character_id.casefold()
            prior = by_id.get(key)
            if prior is not None:
                error = (
                    "contradictory recurring character roles"
                    if prior.role is not character.role
                    else "duplicate recurring character declaration"
                )
                raise PydanticCustomError(
                    "INVALID_IDENTITY_DECLARATION",
                    error,
                )
            by_id[key] = character
        canonical = sorted(
            by_id.values(),
            key=lambda item: (
                item.character_id.casefold(),
                item.role.value,
                item.character_id,
            ),
        )
        object.__setattr__(self, "recurring_characters", tuple(canonical))
        return self


HintItem = Annotated[str, Field(min_length=1, max_length=MAX_HINT_ITEM_LENGTH)]


class SceneHint(_ValidatedFrozenModel):
    """Bounded provider-neutral semantic guidance, never execution authority."""

    target_beat_id: str | None = Field(default=None, pattern=r"^beat_[0-9]{2,4}$")
    target_scene_order: int | None = Field(default=None, ge=1)
    action: str | None = Field(default=None, min_length=1, max_length=300)
    pose: str | None = Field(default=None, min_length=1, max_length=200)
    environment: str | None = Field(default=None, min_length=1, max_length=300)
    objects: tuple[HintItem, ...] = Field(default_factory=tuple, max_length=MAX_HINT_ITEMS)
    symbols: tuple[HintItem, ...] = Field(default_factory=tuple, max_length=MAX_HINT_ITEMS)
    composition_direction: str | None = Field(default=None, min_length=1, max_length=400)
    focal_scale: str | None = Field(default=None, min_length=1, max_length=120)
    focal_treatment: str | None = Field(default=None, min_length=1, max_length=300)

    @field_validator(
        "action",
        "pose",
        "environment",
        "composition_direction",
        "focal_scale",
        "focal_treatment",
    )
    @classmethod
    def normalize_bounded_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("scene hint text cannot be blank")
        if _contains_control_characters(normalized):
            raise ValueError("scene hint text cannot contain control characters")
        return normalized

    @field_validator("objects", "symbols")
    @classmethod
    def normalize_priority_items(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = value.strip()
            if not item:
                raise ValueError("scene hint items cannot be blank")
            if _contains_control_characters(item):
                raise ValueError("scene hint items cannot contain control characters")
            key = item.casefold()
            if key in seen:
                raise ValueError("scene hint items must be unique after normalization")
            seen.add(key)
            normalized.append(item)
        return tuple(normalized)

    @model_validator(mode="after")
    def validate_target_and_content(self) -> "SceneHint":
        if (self.target_beat_id is None) == (self.target_scene_order is None):
            raise ValueError("scene hint requires exactly one target beat ID or scene order")
        if self.target_order < 1:
            raise ValueError("scene hint target order must be positive")
        if not any(
            (
                self.action,
                self.pose,
                self.environment,
                self.objects,
                self.symbols,
                self.composition_direction,
                self.focal_scale,
                self.focal_treatment,
            )
        ):
            raise ValueError("scene hint must contain semantic guidance")
        return self

    @property
    def target_order(self) -> int:
        if self.target_scene_order is not None:
            return self.target_scene_order
        assert self.target_beat_id is not None
        return int(self.target_beat_id.removeprefix("beat_"))

    @property
    def stable_target_key(self) -> tuple[int, str]:
        return self.target_order, (self.target_beat_id or f"scene_{self.target_scene_order:04d}")

    def semantic_text(self) -> str:
        return " ".join(
            str(value)
            for value in (
                self.action,
                self.pose,
                self.environment,
                *self.objects,
                *self.symbols,
                self.composition_direction,
                self.focal_scale,
                self.focal_treatment,
            )
            if value is not None
        )


class StoryPlanHintValidator(Protocol):
    """Stage-2 semantic boundary; target-only binding is intentionally separate."""

    def validate(self, *, hints: tuple[SceneHint, ...], story_plan: StoryPlan) -> None: ...


def _contains_control_characters(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)


def _validate_scene_count_range(value: tuple[int, int]) -> tuple[int, int]:
    minimum, maximum = value
    if minimum < 1 or maximum < 1 or minimum > maximum:
        raise ValueError("requested scene-count range must be positive and ordered")
    return value


def _canonicalize_hints(
    hints: tuple[SceneHint, ...],
    requested_range: tuple[int, int],
) -> tuple[SceneHint, ...]:
    maximum = requested_range[1]
    targets: dict[int, SceneHint] = {}
    for hint in hints:
        if hint.target_order > maximum:
            raise ValueError("scene hint target exceeds the input-local requested scene maximum")
        if hint.target_order in targets:
            raise ValueError("scene hint semantic targets must be unique")
        targets[hint.target_order] = hint
    return tuple(sorted(targets.values(), key=lambda item: item.stable_target_key))


class _ScriptInputBase(_ValidatedFrozenModel):
    language: str = Field(min_length=2, max_length=12)
    requested_scene_count_range: tuple[int, int] = (7, 8)
    visual_mode: VisualExecutionMode = VisualExecutionMode.ILLUSTRATED_SCENE
    character_scope_request: CharacterScopeRequest = Field(default_factory=CharacterScopeRequest)
    execution_mode: ExecutionMode = ExecutionMode.LIVE_PRODUCTION

    @field_validator("language")
    @classmethod
    def normalize_language(cls, value: str) -> str:
        normalized = value.strip().lower().replace("_", "-")
        if not normalized:
            raise ValueError("language cannot be blank")
        return normalized

    @field_validator("requested_scene_count_range")
    @classmethod
    def validate_scene_count_range(cls, value: tuple[int, int]) -> tuple[int, int]:
        return _validate_scene_count_range(value)


class TopicScriptInput(_ScriptInputBase):
    mode: Literal[ScriptInputMode.TOPIC] = ScriptInputMode.TOPIC
    topic: str

    @field_validator("topic")
    @classmethod
    def validate_topic(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("topic cannot be empty")
        return value


class NarrationScriptInput(_ScriptInputBase):
    mode: Literal[ScriptInputMode.NARRATION] = ScriptInputMode.NARRATION
    narration: str

    @field_validator("narration")
    @classmethod
    def validate_narration(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("narration cannot be empty")
        return value


class HintedNarrationScriptInput(_ScriptInputBase):
    mode: Literal[ScriptInputMode.NARRATION_WITH_SCENE_HINTS] = (
        ScriptInputMode.NARRATION_WITH_SCENE_HINTS
    )
    narration: str
    scene_hints: tuple[SceneHint, ...] = Field(min_length=1, max_length=MAX_SCENE_HINTS)

    @field_validator("narration")
    @classmethod
    def validate_narration(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("narration cannot be empty")
        return value

    @model_validator(mode="after")
    def canonicalize_hint_targets(self) -> "HintedNarrationScriptInput":
        object.__setattr__(
            self,
            "scene_hints",
            _canonicalize_hints(
                tuple(self.scene_hints),
                self.requested_scene_count_range,
            ),
        )
        return self


ScriptInput = Annotated[
    TopicScriptInput | NarrationScriptInput | HintedNarrationScriptInput,
    Field(discriminator="mode"),
]
_SCRIPT_INPUT_ADAPTER = TypeAdapter(ScriptInput)


def parse_script_input(value: ScriptInput | dict[str, object]) -> ScriptInput:
    return _SCRIPT_INPUT_ADAPTER.validate_python(value)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _logical_payload(value: "NormalizedScriptInput") -> dict[str, object]:
    return {
        "input_mode": value.input_mode.value,
        "text_authority": value.text_authority.value,
        "source_sha256": _sha256_text(value.source_content),
        "language": value.language,
        "narration_authority": value.narration_authority.value,
        "narration_generation_permitted": value.narration_generation_permitted,
        "requested_scene_count_range": list(value.requested_scene_count_range),
        "scene_hints": [
            hint.model_dump(mode="json", exclude_none=True) for hint in value.scene_hints
        ],
        "visual_mode": value.visual_mode.value,
        "character_scope_request": value.character_scope_request.model_dump(mode="json"),
        "execution_mode": value.execution_mode.value,
    }


def _logical_hash(value: "NormalizedScriptInput") -> str:
    return hashlib.sha256(
        json.dumps(
            _logical_payload(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


class NormalizedScriptInput(_ValidatedFrozenModel):
    """Frozen authority whose serialized hashes are mandatory derived invariants."""

    input_mode: ScriptInputMode
    text_authority: Literal[SourceContentAuthority.DECODED_TEXT_CONTENT] = (
        SourceContentAuthority.DECODED_TEXT_CONTENT
    )
    source_content: str = Field(min_length=1)
    source_sha256: str = Field(default="", pattern=r"^[0-9a-f]{64}$")
    logical_input_hash: str = Field(default="", pattern=r"^[0-9a-f]{64}$")
    language: str = Field(min_length=2, max_length=12)
    narration_authority: NarrationAuthority
    narration_generation_permitted: bool
    requested_scene_count_range: tuple[int, int]
    scene_hints: tuple[SceneHint, ...] = Field(
        default_factory=tuple,
        max_length=MAX_SCENE_HINTS,
    )
    visual_mode: VisualExecutionMode
    character_scope_request: CharacterScopeRequest
    execution_mode: ExecutionMode

    @field_validator("language")
    @classmethod
    def normalize_language(cls, value: str) -> str:
        return _ScriptInputBase.normalize_language(value)

    @field_validator("requested_scene_count_range")
    @classmethod
    def validate_scene_count_range(cls, value: tuple[int, int]) -> tuple[int, int]:
        return _validate_scene_count_range(value)

    @model_validator(mode="before")
    @classmethod
    def reject_empty_supplied_hashes(cls, value: Any) -> Any:
        if isinstance(value, dict):
            for field in ("source_sha256", "logical_input_hash"):
                if field in value and not value[field]:
                    raise ValueError(f"caller-supplied {field} cannot be empty")
        return value

    @model_validator(mode="after")
    def validate_authority_and_hashes(self) -> "NormalizedScriptInput":
        hints = _canonicalize_hints(
            tuple(self.scene_hints),
            self.requested_scene_count_range,
        )
        object.__setattr__(self, "scene_hints", hints)
        if not self.source_content.strip():
            raise ValueError("source_content cannot be whitespace-only")
        if self.input_mode is ScriptInputMode.TOPIC:
            if (
                self.narration_authority is not NarrationAuthority.STORY_PLAN
                or not self.narration_generation_permitted
                or hints
            ):
                raise ValueError("normalized TOPIC authority fields are inconsistent")
        else:
            if (
                self.narration_authority is not NarrationAuthority.EXACT_SOURCE
                or self.narration_generation_permitted
            ):
                raise ValueError("normalized NARRATION authority fields are inconsistent")
            if (
                self.input_mode is ScriptInputMode.NARRATION
                and hints
                or self.input_mode is ScriptInputMode.NARRATION_WITH_SCENE_HINTS
                and not hints
            ):
                raise ValueError("normalized narration hint fields are inconsistent")
        expected_source = _sha256_text(self.source_content)
        expected_logical = _logical_hash(self)
        if self.source_sha256 and self.source_sha256 != expected_source:
            raise ValueError("source_sha256 does not match current decoded source content")
        if self.logical_input_hash and self.logical_input_hash != expected_logical:
            raise ValueError("logical_input_hash does not match current normalized semantics")
        object.__setattr__(self, "source_sha256", expected_source)
        object.__setattr__(self, "logical_input_hash", expected_logical)
        return self

    def model_copy(
        self,
        *,
        update: dict[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        """Validated copy that recomputes hashes after semantic updates."""

        payload = self.model_dump(mode="python")
        payload.pop("source_sha256", None)
        payload.pop("logical_input_hash", None)
        if deep:
            payload = deepcopy(payload)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)


def normalize_script_input(
    value: ScriptInput | dict[str, object],
) -> NormalizedScriptInput:
    request = parse_script_input(value)
    if isinstance(request, TopicScriptInput):
        source = request.topic
        authority = NarrationAuthority.STORY_PLAN
        generation_permitted = True
        hints: tuple[SceneHint, ...] = ()
    else:
        source = request.narration
        authority = NarrationAuthority.EXACT_SOURCE
        generation_permitted = False
        hints = (
            tuple(request.scene_hints) if isinstance(request, HintedNarrationScriptInput) else ()
        )
    return NormalizedScriptInput(
        input_mode=request.mode,
        source_content=source,
        language=request.language,
        narration_authority=authority,
        narration_generation_permitted=generation_permitted,
        requested_scene_count_range=request.requested_scene_count_range,
        scene_hints=hints,
        visual_mode=request.visual_mode,
        character_scope_request=request.character_scope_request,
        execution_mode=request.execution_mode,
    )


__all__ = [
    "CharacterRoleRequest",
    "CharacterScopeRequest",
    "HintedNarrationScriptInput",
    "NarrationAuthority",
    "NarrationScriptInput",
    "NormalizedScriptInput",
    "RecurringCharacterRequest",
    "RelationshipScope",
    "SceneHint",
    "ScriptInput",
    "ScriptInputMode",
    "SourceContentAuthority",
    "StoryPlanHintValidator",
    "TopicScriptInput",
    "normalize_script_input",
    "parse_script_input",
]
