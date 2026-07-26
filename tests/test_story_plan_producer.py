from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import inspect
import json
from typing import ClassVar

import pytest
from pydantic import ValidationError

import tella.topic_production as topic_production
from tella.topic_production import story_plan_producer
from tella.topic_production.models import (
    PlannerMetadata,
    PlannerMode,
    SemanticBeat,
    StoryPlan,
)
from tella.topic_production.script_input import (
    NarrationScriptInput,
    NormalizedScriptInput,
    ScriptInputMode,
    TopicScriptInput,
    normalize_script_input,
)
from tella.topic_production.story_plan_identity import canonical_story_plan_sha256
from tella.topic_production.story_plan_coverage import assign_fixture_source_spans
from tella.topic_production.story_plan_producer import (
    ApprovedStoryPlanProducer,
    ApprovedStoryPlanProduction,
    StoryPlanProducerError,
    StoryPlanProducerErrorCode,
)


def _story_plan(
    *,
    producer_id: str = "test_only.topic_storyplan",
    producer_version: str = "fixture_v1",
) -> StoryPlan:
    narration_text, segments, spans = assign_fixture_source_spans(
        tuple(f"segment {order}" for order in range(1, 8))
    )
    beats = tuple(
        SemanticBeat(
            beat_id=f"beat_{order:02d}",
            order=order,
            source_span=spans[order - 1],
            narration_segment=segments[order - 1],
            semantic_purpose=f"meaning {order}",
            emotional_state=f"state {order}",
            transition_intent=f"transition {order}",
            visual_intent=f"visual intent {order}",
            duration_seconds=5.0,
        )
        for order in range(1, 8)
    )
    return StoryPlan(
        topic="quiet courage",
        language="en",
        target_duration_seconds=35.0,
        requested_scene_count=7,
        narration_text=narration_text,
        emotional_arc=tuple(f"state {order}" for order in range(1, 8)),
        topic_intent="test-only semantic fixture",
        semantic_beats=beats,
        planner_metadata=PlannerMetadata(
            planner_id=producer_id,
            planner_version=producer_version,
            normalized_topic="quiet courage",
            topic_concepts=("quiet", "courage"),
            deterministic_key="0123456789abcdef",
            planner_mode=PlannerMode.FIXTURE,
            production_eligible=False,
        ),
    )


def _topic_input() -> NormalizedScriptInput:
    return normalize_script_input(
        TopicScriptInput(
            topic="quiet courage",
            language="en",
        )
    )


def _narration_input() -> NormalizedScriptInput:
    return normalize_script_input(
        NarrationScriptInput(
            narration="Exact narration.",
            language="en",
        )
    )


class _TestOnlyTopicProducer(ApprovedStoryPlanProducer):
    """Test-only fixture; no production module imports or selects this class."""

    PRODUCER_ID = "test_only.topic_storyplan"
    PRODUCER_VERSION = "fixture_v1"
    SUPPORTED_INPUT_MODES = frozenset({ScriptInputMode.TOPIC})
    calls: ClassVar[int] = 0

    def _produce(self, normalized_input: NormalizedScriptInput) -> StoryPlan:
        assert normalized_input.input_mode is ScriptInputMode.TOPIC
        type(self).calls += 1
        return _story_plan(
            producer_id=self.producer_id,
            producer_version=self.producer_version,
        )


class _InvalidObjectProducer(ApprovedStoryPlanProducer):
    PRODUCER_ID = "test_only.invalid_object"
    PRODUCER_VERSION = "fixture_v1"
    SUPPORTED_INPUT_MODES = frozenset({ScriptInputMode.TOPIC})

    def _produce(self, normalized_input: NormalizedScriptInput) -> StoryPlan:
        del normalized_input
        return object()  # type: ignore[return-value]


class _InvalidCopyProducer(ApprovedStoryPlanProducer):
    PRODUCER_ID = "test_only.invalid_copy"
    PRODUCER_VERSION = "fixture_v1"
    SUPPORTED_INPUT_MODES = frozenset({ScriptInputMode.TOPIC})

    def _produce(self, normalized_input: NormalizedScriptInput) -> StoryPlan:
        del normalized_input
        story = _story_plan(
            producer_id=self.producer_id,
            producer_version=self.producer_version,
        )
        return story.model_copy(update={"semantic_beats": ()})


class _MismatchedMetadataProducer(ApprovedStoryPlanProducer):
    PRODUCER_ID = "test_only.mismatched_metadata"
    PRODUCER_VERSION = "fixture_v1"
    SUPPORTED_INPUT_MODES = frozenset({ScriptInputMode.TOPIC})

    def _produce(self, normalized_input: NormalizedScriptInput) -> StoryPlan:
        del normalized_input
        return _story_plan()


def _dynamic_producer_namespace() -> dict[str, object]:
    def produce_story(
        self: ApprovedStoryPlanProducer,
        normalized_input: NormalizedScriptInput,
    ) -> StoryPlan:
        del normalized_input
        return _story_plan(
            producer_id=self.producer_id,
            producer_version=self.producer_version,
        )

    return {
        "PRODUCER_ID": "test_only.dynamic",
        "PRODUCER_VERSION": "fixture_v1",
        "SUPPORTED_INPUT_MODES": frozenset({ScriptInputMode.TOPIC}),
        "_produce": produce_story,
    }


def test_producer_identity_and_capabilities_are_implementation_owned():
    producer = _TestOnlyTopicProducer()

    assert producer.producer_id == _TestOnlyTopicProducer.PRODUCER_ID
    assert producer.producer_version == _TestOnlyTopicProducer.PRODUCER_VERSION
    assert producer.supported_input_modes == frozenset({ScriptInputMode.TOPIC})
    assert tuple(inspect.signature(producer.produce).parameters) == ("normalized_input",)
    with pytest.raises(AttributeError):
        producer.producer_id = "caller.claimed"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        producer._approved_producer_version = "caller_claimed"  # type: ignore[misc]


def test_supported_mode_returns_immutable_approved_output():
    producer = _TestOnlyTopicProducer()
    output = producer.produce(_topic_input())

    assert isinstance(output, ApprovedStoryPlanProduction)
    assert output.producer_id == producer.producer_id
    assert output.producer_version == producer.producer_version
    assert output.input_mode is ScriptInputMode.TOPIC
    assert output.story_plan_sha256 == canonical_story_plan_sha256(output.story_plan)
    with pytest.raises(FrozenInstanceError):
        output.producer_id = "caller.claimed"  # type: ignore[misc]


def test_unsupported_mode_fails_before_implementation_executes():
    producer = _TestOnlyTopicProducer()
    _TestOnlyTopicProducer.calls = 0

    with pytest.raises(StoryPlanProducerError) as raised:
        producer.produce(_narration_input())

    assert raised.value.code is StoryPlanProducerErrorCode.STORYPLAN_PRODUCER_MODE_UNSUPPORTED
    assert raised.value.producer_id == producer.producer_id
    assert _TestOnlyTopicProducer.calls == 0


def test_non_storyplan_output_is_rejected():
    with pytest.raises(StoryPlanProducerError) as raised:
        _InvalidObjectProducer().produce(_topic_input())

    assert raised.value.code is StoryPlanProducerErrorCode.STORYPLAN_PRODUCER_OUTPUT_INVALID


def test_invalid_storyplan_copy_is_rejected_by_validated_copy_semantics():
    with pytest.raises(ValidationError):
        _InvalidCopyProducer().produce(_topic_input())


def test_storyplan_metadata_must_match_implementation_identity():
    with pytest.raises(StoryPlanProducerError) as raised:
        _MismatchedMetadataProducer().produce(_topic_input())

    assert raised.value.code is StoryPlanProducerErrorCode.STORYPLAN_PRODUCER_OUTPUT_INVALID


def test_public_execution_wrapper_cannot_be_overridden():
    with pytest.raises(TypeError, match="sealed members"):

        class _OverrideProducer(ApprovedStoryPlanProducer):
            PRODUCER_ID = "test_only.override"
            PRODUCER_VERSION = "fixture_v1"
            SUPPORTED_INPUT_MODES = frozenset({ScriptInputMode.TOPIC})

            def produce(  # type: ignore[override]
                self,
                normalized_input: NormalizedScriptInput,
            ) -> ApprovedStoryPlanProduction:
                raise AssertionError(normalized_input)

            def _produce(
                self,
                normalized_input: NormalizedScriptInput,
            ) -> StoryPlan:
                raise AssertionError(normalized_input)


@pytest.mark.parametrize(
    "override",
    [
        classmethod(lambda cls, normalized_input: (cls, normalized_input)),
        staticmethod(lambda normalized_input: normalized_input),
        property(lambda self: self),
    ],
)
def test_public_execution_wrapper_rejects_descriptor_overrides(override: object):
    namespace = _dynamic_producer_namespace()
    namespace["produce"] = override

    with pytest.raises(TypeError, match="sealed members: produce"):
        type("_DescriptorOverrideProducer", (ApprovedStoryPlanProducer,), namespace)


def test_preceding_produce_mixin_is_rejected_from_resolved_mro():
    class _ProduceMixin:
        def produce(self, normalized_input: NormalizedScriptInput) -> object:
            return normalized_input

    with pytest.raises(TypeError, match="sealed members: produce"):

        class _InvalidProducer(_ProduceMixin, ApprovedStoryPlanProducer):
            PRODUCER_ID = "test_only.mro_produce"
            PRODUCER_VERSION = "fixture_v1"
            SUPPORTED_INPUT_MODES = frozenset({ScriptInputMode.TOPIC})

            def _produce(self, normalized_input: NormalizedScriptInput) -> StoryPlan:
                raise AssertionError(normalized_input)


def test_first_indirect_produce_mixin_authority_class_fails_deterministically():
    class _ProduceMixin:
        def produce(self, normalized_input: NormalizedScriptInput) -> object:
            return normalized_input

    with pytest.raises(TypeError, match="sealed members: produce"):

        class _InvalidIntermediate(_ProduceMixin, ApprovedStoryPlanProducer):
            PRODUCER_ID = "test_only.mro_indirect"
            PRODUCER_VERSION = "fixture_v1"
            SUPPORTED_INPUT_MODES = frozenset({ScriptInputMode.TOPIC})

            def _produce(self, normalized_input: NormalizedScriptInput) -> StoryPlan:
                raise AssertionError(normalized_input)


def test_noncooperative_mixin_cannot_skip_resolved_mro_validation():
    class _NonCooperativeProduceMixin:
        def __init_subclass__(cls, **kwargs: object) -> None:
            del cls, kwargs

        def produce(self, normalized_input: NormalizedScriptInput) -> object:
            return normalized_input

    with pytest.raises(TypeError, match="sealed members: produce"):
        type(
            "_NonCooperativeInvalidProducer",
            (_NonCooperativeProduceMixin, ApprovedStoryPlanProducer),
            _dynamic_producer_namespace(),
        )


def test_mixin_after_approved_base_is_allowed_when_sealed_member_still_resolves_to_base():
    class _LaterProduceMixin:
        def produce(self, normalized_input: NormalizedScriptInput) -> object:
            return normalized_input

    class _ValidProducer(ApprovedStoryPlanProducer, _LaterProduceMixin):
        PRODUCER_ID = "test_only.mro_after"
        PRODUCER_VERSION = "fixture_v1"
        SUPPORTED_INPUT_MODES = frozenset({ScriptInputMode.TOPIC})

        def _produce(self, normalized_input: NormalizedScriptInput) -> StoryPlan:
            del normalized_input
            return _story_plan(
                producer_id=self.producer_id,
                producer_version=self.producer_version,
            )

    assert inspect.getattr_static(_ValidProducer, "produce") is inspect.getattr_static(
        ApprovedStoryPlanProducer,
        "produce",
    )
    assert isinstance(_ValidProducer().produce(_topic_input()), ApprovedStoryPlanProduction)


def test_harmless_preceding_mixin_is_allowed():
    class _HelperMixin:
        def helper(self) -> str:
            return "helper"

    class _ValidProducer(_HelperMixin, ApprovedStoryPlanProducer):
        PRODUCER_ID = "test_only.mro_helper"
        PRODUCER_VERSION = "fixture_v1"
        SUPPORTED_INPUT_MODES = frozenset({ScriptInputMode.TOPIC})

        def _produce(self, normalized_input: NormalizedScriptInput) -> StoryPlan:
            del normalized_input
            return _story_plan(
                producer_id=self.producer_id,
                producer_version=self.producer_version,
            )

    producer = _ValidProducer()
    assert producer.helper() == "helper"
    assert isinstance(producer.produce(_topic_input()), ApprovedStoryPlanProduction)


@pytest.mark.parametrize(
    ("member", "replacement"),
    [
        ("__init__", lambda self: None),
        ("__setattr__", lambda self, name, value: object.__setattr__(self, name, value)),
        ("__getattribute__", lambda self, name: object.__getattribute__(self, name)),
        ("producer_id", property(lambda self: "caller.claimed")),
        ("producer_version", property(lambda self: "caller_v1")),
        ("supported_input_modes", property(lambda self: frozenset())),
    ],
)
def test_preceding_mixin_cannot_replace_sealed_authority_member(
    member: str,
    replacement: object,
):
    mixin = type("_AuthorityMixin", (), {member: replacement})

    with pytest.raises(TypeError, match=rf"sealed members: {member}"):
        type(
            "_InvalidAuthorityProducer",
            (mixin, ApprovedStoryPlanProducer),
            _dynamic_producer_namespace(),
        )


def test_every_constructible_producer_resolves_sealed_surface_to_approved_base():
    for producer_type in (
        _TestOnlyTopicProducer,
        _InvalidObjectProducer,
        _InvalidCopyProducer,
        _MismatchedMetadataProducer,
    ):
        for member in (
            "__getattribute__",
            "__init__",
            "__setattr__",
            "produce",
            "producer_id",
            "producer_version",
            "supported_input_modes",
        ):
            assert inspect.getattr_static(producer_type, member) is inspect.getattr_static(
                ApprovedStoryPlanProducer,
                member,
            )


def test_approved_producer_remains_abstract_without_produce_implementation():
    with pytest.raises(TypeError, match="abstract method '_produce'"):
        ApprovedStoryPlanProducer()


def test_abstract_intermediate_may_omit_declarations_before_first_concrete_implementation():
    class _AbstractIntermediate(ApprovedStoryPlanProducer):
        pass

    assert inspect.isabstract(_AbstractIntermediate)
    with pytest.raises(TypeError, match="abstract method '_produce'"):
        _AbstractIntermediate()

    class _FirstConcreteProducer(_AbstractIntermediate):
        PRODUCER_ID = "test_only.first_concrete"
        PRODUCER_VERSION = "fixture_v1"
        SUPPORTED_INPUT_MODES = frozenset({ScriptInputMode.TOPIC})

        def _produce(self, normalized_input: NormalizedScriptInput) -> StoryPlan:
            del normalized_input
            return _story_plan(
                producer_id=self.producer_id,
                producer_version=self.producer_version,
            )

    output = _FirstConcreteProducer().produce(_topic_input())
    assert output.producer_id == "test_only.first_concrete"


def test_concrete_subclass_must_redeclare_all_authority_values():
    with pytest.raises(
        TypeError,
        match=("PRODUCER_ID must be declared directly by every concrete producer implementation"),
    ):

        class _InvalidInheritedProducer(_TestOnlyTopicProducer):
            pass


@pytest.mark.parametrize(
    ("declarations", "missing_field"),
    [
        ({"PRODUCER_ID": "test_only.partial"}, "PRODUCER_VERSION"),
        ({"PRODUCER_VERSION": "fixture_v2"}, "PRODUCER_ID"),
        (
            {"SUPPORTED_INPUT_MODES": frozenset({ScriptInputMode.TOPIC})},
            "PRODUCER_ID",
        ),
        (
            {
                "PRODUCER_ID": "test_only.partial",
                "PRODUCER_VERSION": "fixture_v2",
            },
            "SUPPORTED_INPUT_MODES",
        ),
        (
            {
                "PRODUCER_ID": "test_only.partial",
                "SUPPORTED_INPUT_MODES": frozenset({ScriptInputMode.TOPIC}),
            },
            "PRODUCER_VERSION",
        ),
        (
            {
                "PRODUCER_VERSION": "fixture_v2",
                "SUPPORTED_INPUT_MODES": frozenset({ScriptInputMode.TOPIC}),
            },
            "PRODUCER_ID",
        ),
    ],
)
def test_concrete_subclass_rejects_partial_direct_declarations(
    declarations: dict[str, object],
    missing_field: str,
):
    with pytest.raises(
        TypeError,
        match=(
            rf"{missing_field} must be declared directly "
            "by every concrete producer implementation"
        ),
    ):
        type(
            "_InvalidPartialProducer",
            (_TestOnlyTopicProducer,),
            declarations,
        )


_MISSING = object()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("PRODUCER_ID", _MISSING, "PRODUCER_ID"),
        ("PRODUCER_ID", "", "PRODUCER_ID"),
        ("PRODUCER_ID", "   ", "PRODUCER_ID"),
        ("PRODUCER_ID", "INVALID/ID", "PRODUCER_ID"),
        ("PRODUCER_ID", 17, "PRODUCER_ID"),
        ("PRODUCER_ID", property(lambda self: "test_only.property"), "PRODUCER_ID"),
        ("PRODUCER_VERSION", _MISSING, "PRODUCER_VERSION"),
        ("PRODUCER_VERSION", "", "PRODUCER_VERSION"),
        ("PRODUCER_VERSION", "   ", "PRODUCER_VERSION"),
        ("PRODUCER_VERSION", "invalid/version", "PRODUCER_VERSION"),
        ("PRODUCER_VERSION", 17, "PRODUCER_VERSION"),
        ("PRODUCER_VERSION", property(lambda self: "fixture_v1"), "PRODUCER_VERSION"),
        ("SUPPORTED_INPUT_MODES", _MISSING, "SUPPORTED_INPUT_MODES"),
        ("SUPPORTED_INPUT_MODES", frozenset(), "SUPPORTED_INPUT_MODES"),
        ("SUPPORTED_INPUT_MODES", {ScriptInputMode.TOPIC}, "SUPPORTED_INPUT_MODES"),
        ("SUPPORTED_INPUT_MODES", [ScriptInputMode.TOPIC], "SUPPORTED_INPUT_MODES"),
        ("SUPPORTED_INPUT_MODES", frozenset({17}), "SUPPORTED_INPUT_MODES"),
        ("SUPPORTED_INPUT_MODES", frozenset({"TOPIC"}), "SUPPORTED_INPUT_MODES"),
    ],
)
def test_invalid_producer_declaration_fails_at_class_definition(
    field: str,
    value: object,
    message: str,
):
    namespace = _dynamic_producer_namespace()
    if value is _MISSING:
        namespace.pop(field)
    else:
        namespace[field] = value

    with pytest.raises(TypeError, match=message):
        type("_InvalidDeclarationProducer", (ApprovedStoryPlanProducer,), namespace)


def test_public_class_declaration_mutation_does_not_change_captured_authority():
    class _CapturedProducer(ApprovedStoryPlanProducer):
        PRODUCER_ID = "test_only.captured"
        PRODUCER_VERSION = "fixture_v1"
        SUPPORTED_INPUT_MODES = frozenset({ScriptInputMode.TOPIC})

        def _produce(self, normalized_input: NormalizedScriptInput) -> StoryPlan:
            del normalized_input
            return _story_plan(
                producer_id=self.producer_id,
                producer_version=self.producer_version,
            )

    existing = _CapturedProducer()
    _CapturedProducer.PRODUCER_ID = "caller.changed"
    _CapturedProducer.PRODUCER_VERSION = "caller_v9"
    _CapturedProducer.SUPPORTED_INPUT_MODES = frozenset({ScriptInputMode.NARRATION})
    created_after_mutation = _CapturedProducer()

    with pytest.raises(
        TypeError,
        match=("PRODUCER_ID must be declared directly by every concrete producer implementation"),
    ):

        class _InvalidInheritedProducer(_CapturedProducer):
            pass

    class _IndependentProducer(_CapturedProducer):
        PRODUCER_ID = "test_only.independent"
        PRODUCER_VERSION = "fixture_v2"
        SUPPORTED_INPUT_MODES = frozenset({ScriptInputMode.TOPIC})

    for producer in (existing, created_after_mutation):
        output = producer.produce(_topic_input())
        assert producer.producer_id == "test_only.captured"
        assert producer.producer_version == "fixture_v1"
        assert producer.supported_input_modes == frozenset({ScriptInputMode.TOPIC})
        assert output.producer_id == "test_only.captured"
        assert output.producer_version == "fixture_v1"

    independent = _IndependentProducer()
    output = independent.produce(_topic_input())
    assert independent.producer_id == "test_only.independent"
    assert independent.producer_version == "fixture_v2"
    assert output.producer_id == "test_only.independent"


def test_unsafe_normalized_input_with_stale_hashes_is_rejected():
    producer = _TestOnlyTopicProducer()
    payload = _topic_input().model_dump(mode="python")
    payload["source_content"] = "tampered without updated hashes"
    unsafe = NormalizedScriptInput.model_construct(**payload)

    with pytest.raises(ValidationError, match="source_sha256"):
        producer.produce(unsafe)


def test_produce_rejects_non_authoritative_input_type():
    with pytest.raises(TypeError, match="requires NormalizedScriptInput"):
        _TestOnlyTopicProducer().produce(object())  # type: ignore[arg-type]


def test_unsafe_storyplan_construct_is_revalidated_and_rejected():
    class _UnsafeOutputProducer(ApprovedStoryPlanProducer):
        PRODUCER_ID = "test_only.unsafe_output"
        PRODUCER_VERSION = "fixture_v1"
        SUPPORTED_INPUT_MODES = frozenset({ScriptInputMode.TOPIC})

        def _produce(self, normalized_input: NormalizedScriptInput) -> StoryPlan:
            del normalized_input
            payload = _story_plan(
                producer_id=self.producer_id,
                producer_version=self.producer_version,
            ).model_dump(mode="python")
            payload["semantic_beats"] = ()
            return StoryPlan.model_construct(**payload)

    with pytest.raises(StoryPlanProducerError) as raised:
        _UnsafeOutputProducer().produce(_topic_input())

    assert raised.value.code is StoryPlanProducerErrorCode.STORYPLAN_PRODUCER_OUTPUT_INVALID


def test_approved_producer_rejects_legacy_storyplan_without_source_spans():
    class _LegacyOutputProducer(ApprovedStoryPlanProducer):
        PRODUCER_ID = "test_only.legacy_output"
        PRODUCER_VERSION = "fixture_v1"
        SUPPORTED_INPUT_MODES = frozenset({ScriptInputMode.TOPIC})

        def _produce(self, normalized_input: NormalizedScriptInput) -> StoryPlan:
            del normalized_input
            payload = _story_plan(
                producer_id=self.producer_id,
                producer_version=self.producer_version,
            ).model_dump(mode="python")
            for beat in payload["semantic_beats"]:
                beat.pop("source_span")
            return StoryPlan.model_construct(**payload)

    with pytest.raises(StoryPlanProducerError) as raised:
        _LegacyOutputProducer().produce(_topic_input())

    assert raised.value.code is StoryPlanProducerErrorCode.STORYPLAN_PRODUCER_OUTPUT_INVALID


def test_storyplan_subclass_is_detached_and_normalized_before_approval():
    class _DivergentStoryPlan(StoryPlan):
        extra_runtime_state: str

        def model_dump(self, *args: object, **kwargs: object) -> dict[str, object]:
            payload = super().model_dump(*args, **kwargs)
            payload["topic"] = "serialized authority"
            return payload

    class _DivergentOutputProducer(ApprovedStoryPlanProducer):
        PRODUCER_ID = "test_only.divergent_output"
        PRODUCER_VERSION = "fixture_v1"
        SUPPORTED_INPUT_MODES = frozenset({ScriptInputMode.TOPIC})

        def _produce(self, normalized_input: NormalizedScriptInput) -> StoryPlan:
            del normalized_input
            payload = _story_plan(
                producer_id=self.producer_id,
                producer_version=self.producer_version,
            ).model_dump(mode="python")
            payload["extra_runtime_state"] = "not approved"
            return _DivergentStoryPlan.model_validate(payload)

    output = _DivergentOutputProducer().produce(_topic_input())

    assert type(output.story_plan) is StoryPlan
    assert output.story_plan.topic == "serialized authority"
    assert not hasattr(output.story_plan, "extra_runtime_state")
    assert output.story_plan_sha256 == canonical_story_plan_sha256(output.story_plan)


def test_approved_output_has_no_caller_facing_constructor():
    story = _story_plan()
    with pytest.raises(TypeError):
        ApprovedStoryPlanProduction(
            story_plan=story,  # type: ignore[call-arg]
            producer_id="caller.claimed",
            producer_version="caller_v1",
            input_mode=ScriptInputMode.TOPIC,
            story_plan_sha256=canonical_story_plan_sha256(story),
        )
    with pytest.raises(TypeError, match="created only"):
        ApprovedStoryPlanProduction()


def test_approved_output_has_no_validation_or_copy_construction_api():
    for member in (
        "model_validate",
        "model_validate_json",
        "model_copy",
        "model_construct",
    ):
        assert not hasattr(ApprovedStoryPlanProduction, member)


def test_approved_output_rejects_replace_and_nested_or_detached_mutation():
    output = _TestOnlyTopicProducer().produce(_topic_input())

    with pytest.raises(TypeError):
        replace(output, producer_id="caller.claimed")
    with pytest.raises(FrozenInstanceError):
        output.producer_id = "caller.claimed"  # type: ignore[misc]
    with pytest.raises(ValidationError, match="frozen"):
        output.story_plan.topic = "mutated"  # type: ignore[misc]

    detached = output.story_plan.model_dump(mode="python")
    detached["topic"] = "detached mutation"
    assert output.story_plan.topic == "quiet courage"
    assert output.story_plan_sha256 == canonical_story_plan_sha256(output.story_plan)


def test_producer_instances_and_implementations_keep_isolated_authority():
    class _OtherProducer(ApprovedStoryPlanProducer):
        PRODUCER_ID = "test_only.other"
        PRODUCER_VERSION = "fixture_v2"
        SUPPORTED_INPUT_MODES = frozenset({ScriptInputMode.TOPIC})

        def _produce(self, normalized_input: NormalizedScriptInput) -> StoryPlan:
            del normalized_input
            return _story_plan(
                producer_id=self.producer_id,
                producer_version=self.producer_version,
            )

    first = _TestOnlyTopicProducer()
    second = _TestOnlyTopicProducer()
    other = _OtherProducer()

    assert first is not second
    assert first.producer_id == second.producer_id == "test_only.topic_storyplan"
    assert other.producer_id == "test_only.other"
    assert other.producer_version == "fixture_v2"
    assert first.__dict__ == second.__dict__ == {}


def test_producer_reuse_preserves_input_mode_without_wrapper_run_state():
    class _ReusableProducer(ApprovedStoryPlanProducer):
        PRODUCER_ID = "test_only.reusable"
        PRODUCER_VERSION = "fixture_v1"
        SUPPORTED_INPUT_MODES = frozenset(
            {
                ScriptInputMode.TOPIC,
                ScriptInputMode.NARRATION,
            }
        )

        def _produce(self, normalized_input: NormalizedScriptInput) -> StoryPlan:
            del normalized_input
            return _story_plan(
                producer_id=self.producer_id,
                producer_version=self.producer_version,
            )

    producer = _ReusableProducer()
    topic_output = producer.produce(_topic_input())
    narration_output = producer.produce(_narration_input())

    assert topic_output.input_mode is ScriptInputMode.TOPIC
    assert narration_output.input_mode is ScriptInputMode.NARRATION
    assert topic_output is not narration_output
    assert producer.__dict__ == {}


def test_unsupported_call_does_not_prevent_later_supported_call():
    producer = _TestOnlyTopicProducer()
    _TestOnlyTopicProducer.calls = 0

    with pytest.raises(StoryPlanProducerError):
        producer.produce(_narration_input())
    output = producer.produce(_topic_input())

    assert output.input_mode is ScriptInputMode.TOPIC
    assert _TestOnlyTopicProducer.calls == 1


def test_implementation_exception_propagates_unchanged_and_instance_remains_reusable():
    failure = LookupError("implementation execution failed")

    class _FlakyProducer(ApprovedStoryPlanProducer):
        PRODUCER_ID = "test_only.flaky"
        PRODUCER_VERSION = "fixture_v1"
        SUPPORTED_INPUT_MODES = frozenset({ScriptInputMode.TOPIC})
        calls: ClassVar[int] = 0

        def _produce(self, normalized_input: NormalizedScriptInput) -> StoryPlan:
            del normalized_input
            type(self).calls += 1
            if type(self).calls == 1:
                raise failure
            return _story_plan(
                producer_id=self.producer_id,
                producer_version=self.producer_version,
            )

    producer = _FlakyProducer()
    with pytest.raises(LookupError) as raised:
        producer.produce(_topic_input())

    assert raised.value is failure
    assert producer.produce(_topic_input()).producer_id == "test_only.flaky"


def test_error_payload_is_deterministic_json_safe_and_path_free():
    error = StoryPlanProducerError(
        StoryPlanProducerErrorCode.STORYPLAN_PRODUCER_OUTPUT_INVALID,
        "implementation returned invalid output",
        producer_id="test_only.error",
        input_mode=ScriptInputMode.TOPIC,
    )

    payload = error.as_dict()
    encoded = json.dumps(payload, sort_keys=True)
    assert json.loads(encoded) == payload
    assert payload == {
        "code": "STORYPLAN_PRODUCER_OUTPUT_INVALID",
        "detail": "implementation returned invalid output",
        "producer_id": "test_only.error",
        "input_mode": "TOPIC",
    }
    assert ":\\" not in encoded


def test_contract_uses_shared_hash_implementation_without_duplication():
    assert story_plan_producer.canonical_story_plan_sha256 is canonical_story_plan_sha256


def test_contract_and_test_producer_are_not_package_root_exports():
    names = (
        "ApprovedStoryPlanProducer",
        "ApprovedStoryPlanProduction",
        "StoryPlanProducerError",
        "StoryPlanProducerErrorCode",
        "_TestOnlyTopicProducer",
    )
    assert all(not hasattr(topic_production, name) for name in names)
