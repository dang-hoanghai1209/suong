"""Offline topic planner and StoryPlan-to-SceneBrief adapter.

The fixture planner is intentionally deterministic and provider-free.  It is
an executable contract test, not a claim of model-level semantic reasoning.
Future planners can implement :class:`TopicStoryPlanner` and return the same
validated models.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Protocol

from .models import (
    AcceptancePriority,
    PlannerMetadata,
    ProductionSceneBrief,
    ReferenceStrategy,
    SceneComplexity,
    SceneType,
    SemanticBeat,
    StoryPlan,
    TopicFidelityReport,
)
from .timing import allocate_durations

_FIXED_DEMO_MARKERS = {
    "four_scene_proof_v1",
    "fixed_demo",
    "scene_01_style_anchor",
    "daily-life self-company can become quietly content and nourishing",
}
_STOPWORDS = {
    "a", "an", "and", "are", "be", "for", "in", "is", "of", "the", "to",
    "có", "của", "không", "là", "một", "người", "những", "và", "với",
}

_STAGES = (
    ("recognize", "nhận ra điều đang thật sự chạm vào mình", "quiet awareness", "pause and notice"),
    ("tension", "nhìn thẳng vào phần khó khăn thay vì né tránh", "tender ache", "move inward"),
    ("name", "gọi tên nhu cầu và cảm xúc cốt lõi", "honest sadness", "clarify meaning"),
    ("daily", "đưa chủ đề vào một khoảnh khắc đời thường cụ thể", "grounded calm", "make it tangible"),
    ("choice", "thể hiện một lựa chọn nhỏ nhưng có chủ ý", "gentle resolve", "turn toward agency"),
    ("transition", "cho thấy sự dịch chuyển mà không phủ nhận nỗi buồn", "soft release", "open forward motion"),
    ("compassion", "đối xử với bản thân bằng sự dịu dàng", "self compassion", "settle and integrate"),
    ("closure", "khép lại bằng một ý nghĩa chữa lành gắn với chủ đề", "quiet hope", "resolve without certainty"),
)
_TYPE_POOLS: dict[str, tuple[SceneType, ...]] = {
    "recognize": (SceneType.SOLO_EMOTIONAL_VIGNETTE, SceneType.EMOTIONAL_METAPHOR),
    "tension": (SceneType.RELATIONSHIP_VIGNETTE, SceneType.EMOTIONAL_METAPHOR),
    "name": (SceneType.SOLO_EMOTIONAL_VIGNETTE, SceneType.SYMBOLIC_CHOICE),
    "daily": (SceneType.ORGANIC_DAILY_VIGNETTE, SceneType.SELF_COMPASSION),
    "choice": (SceneType.SYMBOLIC_CHOICE, SceneType.SELF_COMPASSION),
    "transition": (SceneType.JOURNEY_TRANSITION, SceneType.ORGANIC_DAILY_VIGNETTE),
    "compassion": (SceneType.SELF_COMPASSION, SceneType.SOLO_EMOTIONAL_VIGNETTE),
    "closure": (SceneType.CLOSURE_VIGNETTE,),
}


class TopicStoryPlanner(Protocol):
    def plan(
        self,
        *,
        topic: str,
        language: str,
        scene_count: int,
        target_duration_seconds: float,
    ) -> StoryPlan: ...


class TopicFidelityEvaluator(Protocol):
    """Extension point for future semantic/model QC without coupling Phase 1 to a provider."""

    def evaluate(
        self,
        *,
        plan: StoryPlan,
        briefs: list[ProductionSceneBrief] | None = None,
    ) -> TopicFidelityReport: ...


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).strip().split())


def topic_concepts(topic: str) -> list[str]:
    tokens = re.findall(r"[^\W_]+", _normalize(topic).casefold(), flags=re.UNICODE)
    concepts = [token for token in tokens if len(token) > 1 and token not in _STOPWORDS]
    return list(dict.fromkeys(concepts)) or tokens[:1]


def _selected_stages(scene_count: int) -> list[tuple[str, str, str, str]]:
    if scene_count == 8:
        return list(_STAGES)
    if scene_count == 7:
        return [_STAGES[index] for index in (0, 1, 2, 3, 4, 6, 7)]
    raise ValueError("scene_count must be 7 or 8")


def _scene_type(stage_id: str, digest: bytes, order: int) -> SceneType:
    options = _TYPE_POOLS[stage_id]
    return options[digest[(order - 1) % len(digest)] % len(options)]


def _narration(topic: str, purpose: str, order: int, language: str) -> str:
    if language == "vi":
        openings = (
            "Với", "Khi nghĩ về", "Có lúc", "Rồi ta học cách",
            "Trong một khoảnh khắc nhỏ,", "Dần dần,", "Ta có thể", "Cuối cùng,",
        )
        return f"{openings[order - 1]} {topic}, ta {purpose}."
    return f"Beat {order} explores {topic}: {purpose}."


class DeterministicTopicPlanner:
    """Provider-free fixture planner used for contracts, previews, and tests."""

    def plan(
        self,
        *,
        topic: str,
        language: str = "vi",
        scene_count: int = 8,
        target_duration_seconds: float = 35.0,
    ) -> StoryPlan:
        normalized = _normalize(topic)
        if not normalized:
            raise ValueError("topic is empty")
        concepts = topic_concepts(normalized)
        digest = hashlib.sha256(normalized.casefold().encode("utf-8")).digest()
        durations = allocate_durations(scene_count, target_duration_seconds)
        beats: list[SemanticBeat] = []
        arc: list[str] = []
        for order, (stage_id, purpose, emotion, transition) in enumerate(
            _selected_stages(scene_count), start=1
        ):
            focus = concepts[(order - 1) % len(concepts)]
            scene_type = _scene_type(stage_id, digest, order)
            semantic_purpose = f"{purpose}; giữ trọng tâm '{focus}' của chủ đề '{normalized}'"
            beats.append(
                SemanticBeat(
                    beat_id=f"beat_{order:02d}",
                    order=order,
                    narration_segment=_narration(normalized, purpose, order, language),
                    semantic_purpose=semantic_purpose,
                    emotional_state=emotion,
                    transition_intent=transition,
                    visual_intent=(
                        f"{scene_type.value}: một hình ảnh chính thể hiện '{focus}' "
                        f"trong ngữ cảnh của '{normalized}'"
                    ),
                    duration_seconds=durations[order - 1],
                )
            )
            arc.append(emotion)
        intent = (
            f"Dẫn người xem qua một chuyển động cảm xúc dịu và cụ thể về '{normalized}', "
            f"giữ các khái niệm trọng tâm: {', '.join(concepts)}."
        )
        key = digest.hex()[:16]
        return StoryPlan(
            topic=normalized,
            language=language,
            target_duration_seconds=target_duration_seconds,
            requested_scene_count=scene_count,
            narration_text=" ".join(beat.narration_segment for beat in beats),
            emotional_arc=arc,
            topic_intent=intent,
            semantic_beats=beats,
            planner_metadata=PlannerMetadata(
                normalized_topic=normalized,
                topic_concepts=concepts,
                deterministic_key=key,
            ),
        )


def _brief_shape(scene_type: SceneType) -> dict[str, object]:
    symbolic = scene_type in {SceneType.EMOTIONAL_METAPHOR, SceneType.SYMBOLIC_CHOICE}
    relationship = scene_type is SceneType.RELATIONSHIP_VIGNETTE
    complex_scene = symbolic or relationship
    return {
        "characters": ["recurring_woman", "supporting_person"] if relationship else ["recurring_woman"],
        "action": ["one readable emotionally grounded action"],
        "interaction": {"primary": "characters, action, and objects form one coherent relationship"},
        "environment": ["minimal warm dark editorial environment"],
        "objects": ["one topic-specific integrated object"],
        "symbols": ["one restrained topic-specific metaphor"] if symbolic else [],
        "composition": ["lower-middle coherent cluster", "one dominant visual idea"],
        "complexity": SceneComplexity.COMPLEX if complex_scene else SceneComplexity.MODERATE,
        "acceptance_priority": (
            AcceptancePriority.HIGH if complex_scene else AcceptancePriority.STANDARD
        ),
    }


def build_scene_briefs(plan: StoryPlan) -> list[ProductionSceneBrief]:
    briefs: list[ProductionSceneBrief] = []
    digest = bytes.fromhex(plan.planner_metadata.deterministic_key)
    for beat in plan.semantic_beats:
        scene_type = _scene_type(_selected_stages(plan.requested_scene_count)[beat.order - 1][0], digest, beat.order)
        shape = _brief_shape(scene_type)
        briefs.append(
            ProductionSceneBrief(
                scene_id=f"scene_{beat.order:02d}",
                order=beat.order,
                scene_type=scene_type,
                narrative_text=beat.narration_segment,
                meaning=beat.semantic_purpose,
                emotional_tone=[beat.emotional_state],
                topic_intent=plan.topic_intent,
                characters=shape["characters"],
                identity_requirements=["preserve recurring character archetype when present"],
                continuity_requirements=["same muted handmade editorial universe"],
                action=shape["action"],
                interaction=shape["interaction"],
                environment=shape["environment"],
                objects=shape["objects"],
                symbols=shape["symbols"],
                composition=shape["composition"],
                negative_space_requirements=["substantial quiet negative space"],
                visual_hierarchy=["topic meaning", "subject/action", "supporting details"],
                reference_roles=["style_anchor", "identity_anchor"],
                reference_strategy=ReferenceStrategy(
                    strategy="approved_static_topic_and_identity_anchors",
                    notes="No generated-scene chaining in the initial production contract.",
                ),
                hard_negatives=["no unrelated generic solitude scene", "no icon collage"],
                complexity=shape["complexity"],
                acceptance_priority=shape["acceptance_priority"],
                source_beat_id=beat.beat_id,
                duration_seconds=beat.duration_seconds,
            )
        )
    return briefs


def validate_topic_fidelity(
    plan: StoryPlan, briefs: list[ProductionSceneBrief] | None = None
) -> TopicFidelityReport:
    corpus = " ".join(
        [plan.topic_intent]
        + [
            f"{beat.narration_segment} {beat.semantic_purpose} {beat.visual_intent}"
            for beat in plan.semantic_beats
        ]
    ).casefold()
    concepts = plan.planner_metadata.topic_concepts
    concept_propagation = bool(concepts) and all(concept.casefold() in corpus for concept in concepts)
    beat_semantics_present = all(
        beat.semantic_purpose.strip() and beat.visual_intent.strip() for beat in plan.semantic_beats
    )
    topic_retained = plan.planner_metadata.normalized_topic == _normalize(plan.topic)
    no_demo_leakage = not any(marker in corpus for marker in _FIXED_DEMO_MARKERS)
    traceable = True
    meanings_derive = True
    intent_retained = True
    if briefs is not None:
        by_id = {beat.beat_id: beat for beat in plan.semantic_beats}
        traceable = len(briefs) == len(by_id) and all(
            brief.source_beat_id in by_id for brief in briefs
        )
        meanings_derive = traceable and all(
            brief.meaning == by_id[brief.source_beat_id].semantic_purpose for brief in briefs
        )
        intent_retained = all(brief.topic_intent == plan.topic_intent for brief in briefs)
    signals = {
        "topic_retained": topic_retained,
        "topic_concepts_propagated": concept_propagation,
        "every_beat_has_semantic_purpose_and_visual_intent": beat_semantics_present,
        "no_fixed_demo_marker_leakage": no_demo_leakage,
        "scene_traceability_complete": traceable,
        "scene_meanings_derive_from_beats": meanings_derive,
        "topic_intent_retained_in_scenes": intent_retained,
    }
    issues = [name for name, passed in signals.items() if not passed]
    return TopicFidelityReport(passed=not issues, signals=signals, issues=issues)
