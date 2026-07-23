"""Loss-aware adapter from production briefs to validated visual contracts."""
from __future__ import annotations

from tella.visual_generation.models import SceneBrief as VisualSceneBrief

from .execution_models import VisualSceneAdapterResult
from .models import ProductionSceneBrief, SceneType


def _generic_identity_reference_role(brief: ProductionSceneBrief) -> str:
    relationship_identity_phrases = (
        "couple identity",
        "relationship identity",
        "recurring couple",
        "same couple",
    )
    identity_semantics = " ".join(
        [*brief.identity_requirements, *brief.continuity_requirements]
    ).casefold()
    if brief.scene_type is SceneType.RELATIONSHIP_VIGNETTE or any(
        phrase in identity_semantics for phrase in relationship_identity_phrases
    ):
        return "couple_identity_anchor"

    female_characters = {"recurring_woman", "woman", "female"}
    male_characters = {"supporting_person", "recurring_man", "man", "male"}
    characters = set(brief.characters)
    if len(brief.characters) == 1 and characters <= female_characters:
        return "female_identity_anchor"
    if len(brief.characters) == 1 and characters <= male_characters:
        return "male_identity_anchor"
    return "unresolved_identity_anchor"


def required_reference_roles(brief: ProductionSceneBrief) -> list[str]:
    """Resolve explicit roles first, with bounded inference for legacy briefs."""

    roles: list[str] = []
    if brief.reference_roles:
        for role in brief.reference_roles:
            roles.append(
                _generic_identity_reference_role(brief)
                if role == "identity_anchor"
                else role
            )
    elif brief.identity_requirements or brief.continuity_requirements:
        if brief.characters:
            roles.append(_generic_identity_reference_role(brief))
        roles.append("style_anchor")
    scene_role = {
        SceneType.ORGANIC_DAILY_VIGNETTE: "daily_vignette_reference",
        SceneType.EMOTIONAL_METAPHOR: "emotional_metaphor_reference",
        SceneType.SYMBOLIC_CHOICE: "symbolic_reference",
        SceneType.SELF_COMPASSION: "composition_reference",
        SceneType.JOURNEY_TRANSITION: "composition_reference",
        SceneType.CLOSURE_VIGNETTE: "composition_reference",
    }.get(brief.scene_type)
    if scene_role:
        roles.append(scene_role)
    unique = list(dict.fromkeys(roles))
    return sorted(
        unique,
        key=lambda role: (
            1
            if "identity_anchor" in role
            else 2
            if role == "style_anchor"
            else 3,
            unique.index(role),
        ),
    )


def adapt_scene_brief(brief: ProductionSceneBrief) -> VisualSceneAdapterResult:
    character_map = {
        "recurring_woman": "female",
        "woman": "female",
        "female": "female",
        "supporting_person": "male",
        "man": "male",
        "male": "male",
    }
    characters = list(
        dict.fromkeys(character_map[item] for item in brief.characters if item in character_map)
    )
    if not characters:
        characters = ["female"]
    environment = [*brief.environment, *[f"integrated object: {item}" for item in brief.objects]]
    composition = [
        *brief.composition,
        *[f"negative space: {item}" for item in brief.negative_space_requirements],
        *[f"visual hierarchy: {item}" for item in brief.visual_hierarchy],
    ]
    reference_roles = required_reference_roles(brief)
    visual = VisualSceneBrief(
        scene_id=brief.scene_id,
        scene_type=brief.scene_type.value,
        narrative_text=brief.narrative_text,
        narrative_meaning=brief.meaning,
        characters=characters,
        emotion=brief.emotional_tone,
        action=brief.action or ["one restrained readable action"],
        interaction=brief.interaction,
        environment_cues=environment,
        symbolic_elements=brief.symbols,
        composition=composition or ["one coherent vertical composition"],
        negative_constraints=brief.hard_negatives,
        reference_roles=reference_roles or ["no_reference_required"],
        natural_interaction_required=bool(brief.interaction),
    )
    preserved = {
        "topic_intent": brief.topic_intent,
        "identity_requirements": brief.identity_requirements,
        "continuity_requirements": brief.continuity_requirements,
        "objects": brief.objects,
        "negative_space_requirements": brief.negative_space_requirements,
        "visual_hierarchy": brief.visual_hierarchy,
        "reference_strategy": brief.reference_strategy.model_dump(mode="json"),
        "source_beat_id": brief.source_beat_id,
    }
    return VisualSceneAdapterResult(
        visual_scene=visual,
        preserved_semantics=preserved,
        field_mapping={
            "meaning": "narrative_meaning",
            "emotional_tone": "emotion",
            "environment+objects": "environment_cues",
            "symbols": "symbolic_elements",
            "hard_negatives": "negative_constraints",
            "negative_space+visual_hierarchy": "composition",
        },
    )
