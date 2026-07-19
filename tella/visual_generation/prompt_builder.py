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
    ]
    if scene.scene_id == "scene_01":
        sections.append(
            (
                "SCENE 1 QUALITY LOCK",
                "Create one soft irregular cream-colored glow behind the character with "
                "powdery, hazy, hand-brushed edges and a diffused asymmetrical transition, with a visibly lopsided cloud silhouette (one side extending farther than the other). "
                "The glow is an organic atmosphere, never a perfect circle, radially symmetric disk, concentric ring, "
                "target, badge, or mechanical spotlight. Keep the woman relatively small in "
                "the lower-middle of the vertical frame with abundant empty negative space "
                "above; the composition must breathe and remain airy rather than crowded. "
                "Her expression and posture should feel gentle, introspective, tender, calm, "
                "slightly wistful, healing, and quietly emotional—not cheerful, exaggeratedly "
                "sad, overly cute, or mannequin-neutral. Treat the ticket, cup, flower, and "
                "leaf/scribble motifs as a few restrained memory marks softly hand-drawn into "
                "the same illustration world, sharing the girl's line quality and softness; "
                "they are not UI icons, SVG symbols, stickers, or pasted assets. Use a soft "
                "matte pastel finish, subtle grain, faint chalky texture, delicate imperfect "
                "outlines, and gentle organic geometry; avoid glossy, crisp vector, hard-edged, "
                "high-contrast, or decorative rendering.",
            )
        )
    sections.extend(
        [
        (
            "REFERENCE ROLE GUIDANCE",
            "Use supplied images only as conditioning anchors; generate one complete, coherent "
            "illustration. Never composite or paste their pixels into the output. Roles: "
            + ", ".join(scene.reference_roles),
        ),
        ]
    )
    instruction = "\n\n".join(f"{title}:\n{body}" for title, body in sections if body)
    negatives = sorted(set(style.negative_constraints + scene.negative_constraints))
    if scene.scene_id == "scene_01":
        negatives.extend(
            [
                "no perfect circular halo",
                "no radially symmetric glow",
                "no concentric rings",
                "no target-like framing",
                "no badge-like spotlight",
                "no cheerful or overly cute expression",
                "no crisp vector or glossy graphic finish",
                "no crowded motif cluster",
            ]
        )
        negatives = sorted(set(negatives))
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
