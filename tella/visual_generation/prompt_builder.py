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
                "Keep the woman as the primary focal subject: a medium-small, clearly readable "
                "full-body figure in the lower-middle of the vertical frame, large enough for "
                "her facial emotion and hand-on-chest pose to read, while preserving abundant "
                "empty negative space above. Directly behind her, place one small restrained "
                "irregular muted beige-cream vignette that only slightly exceeds her body "
                "silhouette—roughly one-and-a-half times her visual height, never most of the "
                "frame. It is a secondary supporting background shape, low-contrast, "
                "semi-transparent, matte, softly blended, dusty, and powdery, with an "
                "asymmetrical hand-brushed organic edge. Keep it close behind her body; it must "
                "not extend far above her head, dominate the composition, become bright white, "
                "or resemble a giant glowing cloud, light column, luminous aura, perfect circle, "
                "oval spotlight, concentric ring, target, badge, or mechanical spotlight. "
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
    elif scene.scene_id == "scene_02":
        sections.append(
            (
                "SCENE 2 QUALITY LOCK",
                "Keep the seated couple as the primary focal subject: two clearly readable, "
                "similarly simplified full-body characters in the lower-middle, male on the "
                "left and female on the right, with generous empty negative space above. Their "
                "shared bench contact, limb spacing, hand placement, body scale, and slight "
                "orientation toward one another must feel natural and anatomically coherent. "
                "Show quiet companionship and subtle shared attention, never a dramatic romance "
                "pose, embrace, or kiss. Keep the woman visually continuous with Scene 1 and "
                "render the man in exactly the same hand-drawn illustration language. Behind the "
                "couple, use one restrained asymmetrical muted beige-cream vignette, only a little "
                "wider than the seated pair, low-contrast, matte, powdery, and softly blended. It "
                "is a secondary supporting shape close to the couple, never a giant halo, bright "
                "aura, light column, circular spotlight, oval badge, or dominant scene object. "
                "Draw only the simple shared bench, one small potted plant, and one small warm "
                "lantern as subtle integrated props; do not add scenery or decorative clutter.",
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
                "no giant halo, towering cream glow, or light column",
                "no luminous aura or white-hot center",
                "no oversized cream shape occupying most of the central frame",
                "no glow extending far above the character",
                "no oval spotlight",
                "no cheerful or overly cute expression",
                "no crisp vector or glossy graphic finish",
                "no crowded motif cluster",
            ]
        )
        negatives = sorted(set(negatives))
    elif scene.scene_id == "scene_02":
        negatives.extend(
            [
                "no dramatic romance poster pose, embrace, or kiss",
                "no giant halo, bright aura, or light column",
                "no circular spotlight, oval badge, or concentric rings",
                "no dominant bench, plant, lantern, or vignette",
                "no detailed environment or decorative clutter",
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
