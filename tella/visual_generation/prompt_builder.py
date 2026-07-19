"""Deterministic provider-neutral visual instruction construction."""
from __future__ import annotations

import hashlib
import json

from .models import GenerationRequest, ReferencePack, SceneBrief, StyleBible


def build_instruction(scene: SceneBrief, style: StyleBible) -> tuple[str, str]:
    character_locks = {
        character: style.character_archetypes[character].identity_locks
        for character in scene.characters
    }
    sections = [
        ("GLOBAL STYLE LOCK", _items(style.drawing + style.palette + style.background)),
        ("CHARACTER IDENTITY LOCK", _mapping(character_locks)),
        ("CURRENT SCENE MEANING", scene.narrative_meaning),
        ("EMOTION", _items(scene.emotion)),
        ("ACTION", _items(scene.action)),
        ("INTERACTION", _mapping(scene.interaction)),
        ("ENVIRONMENT CUES", _items(scene.environment_cues)),
        ("SYMBOLIC ELEMENTS", _items(scene.symbolic_elements)),
        ("COMPOSITION", _items(style.composition + scene.composition + style.lighting)),
        (
            "REFERENCE ROLE GUIDANCE",
            "Use supplied images only as conditioning anchors; generate one complete, coherent "
            "illustration. Never composite or paste their pixels into the output. Roles: "
            + ", ".join(scene.reference_roles),
        ),
    ]
    instruction = "\n\n".join(f"{title}:\n{body}" for title, body in sections if body)
    negatives = sorted(set(style.negative_constraints + scene.negative_constraints))
    return instruction, "; ".join(negatives)


def build_generation_request(
    scene: SceneBrief,
    style: StyleBible,
    references: ReferencePack,
    *,
    candidate_index: int,
    attempt: int,
    seed: int | None,
) -> GenerationRequest:
    instruction, negative = build_instruction(scene, style)
    return GenerationRequest(
        scene_id=scene.scene_id,
        candidate_index=candidate_index,
        attempt=attempt,
        width=style.canvas.width,
        height=style.canvas.height,
        aspect_ratio=style.canvas.aspect_ratio,
        instruction=instruction,
        negative_instruction=negative,
        references=references.references,
        seed=seed,
    )


def request_hash(request: GenerationRequest) -> str:
    payload = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def instruction_hash(request: GenerationRequest) -> str:
    return hashlib.sha256(request.instruction.encode("utf-8")).hexdigest()


def _items(values: list[str]) -> str:
    return "; ".join(values)


def _mapping(values: dict[str, object]) -> str:
    return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
