"""Generic topic-production prompt construction without proof-scene locks."""
from __future__ import annotations

import json

from tella.visual_generation.models import GenerationRequest, ReferenceAsset
from tella.visual_generation.prompt_builder import request_hash

from .execution_models import SceneExecutionPlan


PROMPT_PROFILE = "topic_production_v1"


def _items(values: list[str]) -> str:
    return "; ".join(values) if values else "none required"


def build_topic_production_request(scene: SceneExecutionPlan) -> GenerationRequest:
    """Map the complete production brief into one provider-neutral draft request."""
    if scene.visual_adapter.prompt_profile != PROMPT_PROFILE:
        raise ValueError("PHASE_3B1_PRODUCTION_PROMPT_PROFILE_BLOCKED")
    brief = scene.scene_brief
    sections = [
        ("PROMPT PROFILE", PROMPT_PROFILE),
        ("TOPIC INTENT", brief.topic_intent),
        ("NARRATIVE TEXT", brief.narrative_text),
        ("SCENE MEANING", brief.meaning),
        ("EMOTIONAL TONE", _items(brief.emotional_tone)),
        ("CHARACTERS", _items(brief.characters)),
        ("IDENTITY REQUIREMENTS", _items(brief.identity_requirements)),
        ("CONTINUITY REQUIREMENTS", _items(brief.continuity_requirements)),
        ("ACTION", _items(brief.action)),
        (
            "INTERACTION",
            json.dumps(brief.interaction, ensure_ascii=False, sort_keys=True),
        ),
        ("ENVIRONMENT", _items(brief.environment)),
        ("OBJECTS", _items(brief.objects)),
        ("SYMBOLS", _items(brief.symbols)),
        ("COMPOSITION", _items(brief.composition)),
        ("NEGATIVE SPACE", _items(brief.negative_space_requirements)),
        ("VISUAL HIERARCHY", _items(brief.visual_hierarchy)),
        ("REFERENCE ROLES", _items(scene.visual_adapter.visual_scene.reference_roles)),
    ]
    instruction = "\n\n".join(f"[{title}]\n{body}" for title, body in sections)
    negatives = list(
        dict.fromkeys(
            [
                *brief.hard_negatives,
                "no readable text, captions, logos, watermarks, or UI",
                "no unrelated icon collage",
                "no generated-scene chaining",
            ]
        )
    )
    references = [
        ReferenceAsset(
            role=item.roles[0],
            semantic_roles=item.roles,
            path=item.path,
            sha256=item.sha256,
            source=(
                "scene_type"
                if any("reference" in role for role in item.roles)
                else "master"
            ),
            priority=item.priority,
        )
        for item in scene.draft.references
    ]
    if not references:
        raise ValueError("required approved references are missing")
    return GenerationRequest(
        scene_id=scene.scene_id,
        candidate_index=1,
        attempt=1,
        width=scene.draft.width,
        height=scene.draft.height,
        aspect_ratio="9:16",
        instruction=instruction,
        negative_instruction="; ".join(negatives),
        references=references,
        seed=scene.draft.seed,
    )


def topic_production_request_hash(scene: SceneExecutionPlan) -> str:
    return request_hash(build_topic_production_request(scene))
