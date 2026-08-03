"""Architectural in-process authority boundary for approved StoryPlan producers.

The contract protects ordinary public construction, subclass definitions,
multiple inheritance, and standard public model/dataclass APIs.  It is neither
cryptographic provenance nor a sandbox or hostile-code security boundary.
Deliberately hostile in-process behavior remains out of scope, including custom
metaclasses that bypass this module's machinery, monkeypatching, reflection or
private sentinel extraction, direct private-state or ``object.__setattr__``
mutation, and interpreter-level manipulation.

This module provides no concrete producer or producer selection.
"""

from __future__ import annotations

from abc import ABC, ABCMeta, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
import inspect
import re
from typing import ClassVar, final

from pydantic import ValidationError

from .models import StoryPlan
from .script_input import NormalizedScriptInput, ScriptInputMode
from .story_plan_identity import canonical_story_plan_sha256


_PRODUCER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{2,79}$")
_PRODUCER_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,39}$")
_APPROVED_OUTPUT_AUTHORITY = object()
_SEALED_PRODUCER_MEMBERS = frozenset(
    {
        "__getattribute__",
        "__init__",
        "__setattr__",
        "produce",
        "producer_id",
        "producer_version",
        "supported_input_modes",
    }
)


class StoryPlanProducerErrorCode(StrEnum):
    STORYPLAN_PRODUCER_MODE_UNSUPPORTED = "STORYPLAN_PRODUCER_MODE_UNSUPPORTED"
    STORYPLAN_PRODUCER_OUTPUT_INVALID = "STORYPLAN_PRODUCER_OUTPUT_INVALID"


class StoryPlanProducerError(RuntimeError):
    """Stable failure at the approved-producer contract boundary."""

    def __init__(
        self,
        code: StoryPlanProducerErrorCode,
        detail: str,
        *,
        producer_id: str,
        input_mode: ScriptInputMode,
    ):
        self.code = code
        self.detail = detail
        self.producer_id = producer_id
        self.input_mode = input_mode
        super().__init__(f"{code.value}: {detail}")

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code.value,
            "detail": self.detail,
            "producer_id": self.producer_id,
            "input_mode": self.input_mode.value,
        }


@dataclass(frozen=True, slots=True, init=False)
class ApprovedStoryPlanProduction:
    """Immutable proof that a StoryPlan passed through the sealed wrapper."""

    story_plan: StoryPlan
    producer_id: str
    producer_version: str
    input_mode: ScriptInputMode
    story_plan_sha256: str

    def __new__(
        cls,
        *,
        _authority: object | None = None,
    ) -> "ApprovedStoryPlanProduction":
        if _authority is not _APPROVED_OUTPUT_AUTHORITY:
            raise TypeError(
                "ApprovedStoryPlanProduction is created only by ApprovedStoryPlanProducer.produce()"
            )
        return object.__new__(cls)


def _validate_producer_class(
    cls: type["ApprovedStoryPlanProducer"],
    namespace: dict[str, object],
) -> None:
    approved_base = ApprovedStoryPlanProducer
    shadowed = {
        member
        for member in _SEALED_PRODUCER_MEMBERS
        if inspect.getattr_static(cls, member) is not inspect.getattr_static(approved_base, member)
    }
    if shadowed:
        names = ", ".join(sorted(shadowed))
        raise TypeError(f"approved producer cannot override sealed members: {names}")

    if inspect.isabstract(cls):
        return

    for field in ("PRODUCER_ID", "PRODUCER_VERSION", "SUPPORTED_INPUT_MODES"):
        if field not in namespace:
            raise TypeError(
                f"{field} must be declared directly by every concrete producer implementation"
            )

    producer_id = namespace.get("PRODUCER_ID")
    producer_version = namespace.get("PRODUCER_VERSION")
    supported_modes = namespace.get("SUPPORTED_INPUT_MODES")
    if not isinstance(producer_id, str) or not _PRODUCER_ID_PATTERN.fullmatch(producer_id):
        raise TypeError("PRODUCER_ID must be a stable lowercase identifier")
    if not isinstance(producer_version, str) or not _PRODUCER_VERSION_PATTERN.fullmatch(
        producer_version
    ):
        raise TypeError("PRODUCER_VERSION must be a stable nonempty version")
    if (
        type(supported_modes) is not frozenset
        or not supported_modes
        or any(type(mode) is not ScriptInputMode for mode in supported_modes)
    ):
        raise TypeError("SUPPORTED_INPUT_MODES must be a nonempty frozenset of ScriptInputMode")

    cls._DECLARED_PRODUCER_ID = producer_id
    cls._DECLARED_PRODUCER_VERSION = producer_version
    cls._DECLARED_SUPPORTED_INPUT_MODES = supported_modes


class _ApprovedStoryPlanProducerMeta(ABCMeta):
    """Validate resolved producer authority after Python constructs the complete MRO."""

    def __new__(
        mcls,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, object],
        **kwargs: object,
    ) -> type:
        cls = super().__new__(mcls, name, bases, namespace, **kwargs)
        approved_base = globals().get("ApprovedStoryPlanProducer")
        if approved_base is not None and approved_base in cls.__mro__[1:]:
            _validate_producer_class(cls, namespace)
        return cls


class ApprovedStoryPlanProducer(ABC, metaclass=_ApprovedStoryPlanProducerMeta):
    """Sealed wrapper whose concrete subclasses directly declare independent authority."""

    PRODUCER_ID: ClassVar[str]
    PRODUCER_VERSION: ClassVar[str]
    SUPPORTED_INPUT_MODES: ClassVar[frozenset[ScriptInputMode]]

    __slots__ = (
        "_approved_producer_id",
        "_approved_producer_version",
        "_approved_supported_input_modes",
    )

    def __init__(self) -> None:
        object.__setattr__(
            self,
            "_approved_producer_id",
            type(self)._DECLARED_PRODUCER_ID,
        )
        object.__setattr__(
            self,
            "_approved_producer_version",
            type(self)._DECLARED_PRODUCER_VERSION,
        )
        object.__setattr__(
            self,
            "_approved_supported_input_modes",
            type(self)._DECLARED_SUPPORTED_INPUT_MODES,
        )

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("approved producer identity and capabilities are immutable")

    @property
    def producer_id(self) -> str:
        return self._approved_producer_id

    @property
    def producer_version(self) -> str:
        return self._approved_producer_version

    @property
    def supported_input_modes(self) -> frozenset[ScriptInputMode]:
        return self._approved_supported_input_modes

    @final
    def produce(
        self,
        normalized_input: NormalizedScriptInput,
    ) -> ApprovedStoryPlanProduction:
        """Validate input, invoke the implementation, and establish authority."""

        if not isinstance(normalized_input, NormalizedScriptInput):
            raise TypeError("approved producer requires NormalizedScriptInput")
        validated_input = NormalizedScriptInput.model_validate(
            normalized_input.model_dump(mode="python")
        )
        if validated_input.input_mode not in self.supported_input_modes:
            raise StoryPlanProducerError(
                StoryPlanProducerErrorCode.STORYPLAN_PRODUCER_MODE_UNSUPPORTED,
                "producer does not support the normalized input mode",
                producer_id=self.producer_id,
                input_mode=validated_input.input_mode,
            )

        # Implementation failures propagate unchanged: no output exists to classify.
        candidate = self._produce(validated_input)
        if not isinstance(candidate, StoryPlan):
            raise StoryPlanProducerError(
                StoryPlanProducerErrorCode.STORYPLAN_PRODUCER_OUTPUT_INVALID,
                "producer implementation did not return StoryPlan",
                producer_id=self.producer_id,
                input_mode=validated_input.input_mode,
            )
        try:
            story_plan = StoryPlan.model_validate(candidate.model_dump(mode="python"))
        except (ValidationError, ValueError) as error:
            raise StoryPlanProducerError(
                StoryPlanProducerErrorCode.STORYPLAN_PRODUCER_OUTPUT_INVALID,
                "producer implementation returned an invalid StoryPlan",
                producer_id=self.producer_id,
                input_mode=validated_input.input_mode,
            ) from error
        metadata = story_plan.planner_metadata
        if (
            metadata.planner_id != self.producer_id
            or metadata.planner_version != self.producer_version
        ):
            raise StoryPlanProducerError(
                StoryPlanProducerErrorCode.STORYPLAN_PRODUCER_OUTPUT_INVALID,
                "StoryPlan metadata does not match implementation-owned producer identity",
                producer_id=self.producer_id,
                input_mode=validated_input.input_mode,
            )

        approved = ApprovedStoryPlanProduction(_authority=_APPROVED_OUTPUT_AUTHORITY)
        object.__setattr__(approved, "story_plan", story_plan)
        object.__setattr__(approved, "producer_id", self.producer_id)
        object.__setattr__(approved, "producer_version", self.producer_version)
        object.__setattr__(approved, "input_mode", validated_input.input_mode)
        object.__setattr__(
            approved,
            "story_plan_sha256",
            canonical_story_plan_sha256(story_plan),
        )
        return approved

    @abstractmethod
    def _produce(self, normalized_input: NormalizedScriptInput) -> StoryPlan:
        """Return one StoryPlan; only code-reviewed implementations override this."""

        raise NotImplementedError


__all__ = [
    "ApprovedStoryPlanProducer",
    "ApprovedStoryPlanProduction",
    "StoryPlanProducerError",
    "StoryPlanProducerErrorCode",
]
