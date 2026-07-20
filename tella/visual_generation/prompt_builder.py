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
                "similarly simplified full-body characters in the lower-middle, with generous "
                "empty negative space above. From the viewer's perspective, place the young man "
                "on the LEFT side of the bench and the young woman on the RIGHT side. Maintain "
                "this left-to-right order exactly: MAN -> WOMAN. Do not mirror, reverse, or swap "
                "their positions; the male must remain viewer-left and the female viewer-right. "
                "Their "
                "shared bench contact, limb spacing, hand placement, body scale, and slight "
                "orientation toward one another must feel natural and anatomically coherent. "
                "Show quiet companionship and subtle shared attention, never a dramatic romance "
                "pose, embrace, or kiss. Keep the woman visually continuous with Scene 1 and "
                "render the man in exactly the same hand-drawn illustration language. Behind the "
                "couple, place one small-to-moderate restrained irregular muted beige-cream "
                "emotional vignette, only large enough to softly support the two-person cluster. "
                "Keep it close behind the couple, low-contrast, semi-transparent, matte, softly "
                "blended, dusty, and powdery, with an asymmetrical hand-brushed organic edge. It "
                "is secondary to the couple and must not extend dramatically above their heads, "
                "fill most of the central frame, become bright white, form a giant oval or "
                "symmetrical ellipse, resemble a white luminous disk or circular/oval spotlight, "
                "or dominate the couple. Preserve this visual hierarchy: first the COUPLE, then "
                "the restrained emotional vignette, then the bench, then the small plant and "
                "lantern. "
                "Draw only the simple shared bench, one small potted plant, and one small warm "
                "lantern as subtle integrated props; do not add scenery or decorative clutter.",
            )
        )
    elif scene.scene_id == "scene_03":
        sections.append(
            (
                "SCENE 3 QUALITY LOCK",
                "Preserve the quiet daily-life meaning and keep the recurring woman actively "
                "eating alone as the primary focal subject in the lower-middle of the vertical "
                "frame, with substantial empty negative space above. Preserve her rounded dark "
                "bob, simple cream face, muted coral or dusty-pink clothing, coherent utensil-to-"
                "hand alignment, the meal directly in front of her, the cup logically placed, "
                "and natural table, chair, and body contact. Keep the small flower, restrained "
                "plant, and warm lamp as subtle integrated supporting cues. Loosely behind only "
                "the woman and small table, place one restrained irregular beige-cream "
                "atmospheric patch. Keep this patch open, uneven, asymmetrical, softly feathered, "
                "powdery, matte, semi-transparent, low-contrast, and hand-brushed, with edges "
                "that softly fade and dissolve naturally into the dark brown background. It must "
                "not form a closed geometric shape or an outlined enclosure, and it must not "
                "read as an oval, ellipse, closed bubble, enclosure, badge, spotlight, white "
                "disk, outlined backdrop, or hard-edged geometric shape. Make the patch smaller "
                "and visually quieter than the woman, meal, and eating action. Preserve this "
                "visual hierarchy exactly: first the WOMAN ACTIVELY EATING, then the MEAL AND "
                "TABLE ACTION, then the subtle CHAIR, CUP, AND SMALL FLOWER, then the restrained "
                "PLANT AND WARM LAMP, and last the CREAM ATMOSPHERIC PATCH.",
            )
        )
    elif scene.scene_id == "scene_04":
        sections.append(
            (
                "SCENE 4 QUALITY LOCK",
                "Keep the established dark brown or deep taupe outer background and place one "
                "quiet lower emotional cluster in the lower-middle of the vertical frame, with "
                "large calm negative space above. Keep the recurring woman as the primary emotional "
                "subject in the lower-right or lower-center-right, with her short rounded dark bob, "
                "simple cream face, muted dusty-pink or coral clothing, curled sitting pose, and "
                "arms clearly hugging her knees. Make the central idea immediately read as someone "
                "missing: place one faint incomplete empty human outline beside her on the LEFT as "
                "an absence, not another character. The absence must be hollow or nearly transparent, "
                "outline-based or softly partial, with parts of the outline fading into the dark "
                "background. Give it no face, skin, clothing, body detail, detailed arm, or detailed "
                "hand; never render it as a solid gray person, ghost character, realistic second "
                "person, or fully rendered human body. Keep the woman-and-absence spatial relationship "
                "primary, followed by one restrained taupe-cream support patch directly behind the "
                "emotional cluster only. The support patch must be irregular, soft, matte, "
                "semi-transparent, feathered, secondary, and naturally integrated; it must not fill "
                "the page, become a light overall background, or form a giant enclosure. Include "
                "exactly one small loose dark scribbled cloud total, close to the emotional cluster, "
                "understated and secondary; never add a second cloud, a large competing cloud collage, "
                "a white or light cloud near the top, a dramatic storm cloud, or a dominant top-center "
                "object. Include one clearly visible tiny muted hand-drawn broken-heart doodle, lightly "
                "integrated near the cluster; never make it a large bright heart icon, emoji, sticker, "
                "or UI symbol. Keep only a few falling marks or tears, a few leaves, and one restrained "
                "cream botanical doodle on the right. These marks remain subordinate so the image reads "
                "as one quiet emotional illustration rather than a collage of sadness icons. Preserve "
                "the established warm, muted, hand-drawn Tella visual world and dark-background grammar.",
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
                "no swapped positions, mirrored couple layout, or woman-left/man-right arrangement",
                "no giant halo, bright aura, or light column",
                "no giant symmetrical oval spotlight or white luminous disk",
                "no circular spotlight, oval badge, symmetrical ellipse, or concentric rings",
                "no dominant bench, plant, lantern, or vignette",
                "no detailed environment or decorative clutter",
            ]
        )
        negatives = sorted(set(negatives))
    elif scene.scene_id == "scene_03":
        negatives.extend(
            [
                "no oval, ellipse, closed bubble, enclosure, or badge",
                "no spotlight, white disk, or outlined backdrop",
                "no closed geometric vignette or hard-edged cream shape",
                "no visible hard outline around the atmospheric patch",
                "no dominant, oversized, bright, or high-contrast cream patch",
                "no cream patch competing with the woman, meal, or eating action",
            ]
        )
        negatives = sorted(set(negatives))
    elif scene.scene_id == "scene_04":
        negatives.extend(
            [
                "no solid gray person, ghost character, or realistic second person",
                "no detailed face, clothing, skin, body, arm, or hand on the absence",
                "no fully rendered human body or filled-in silhouette",
                "no large isolated top-center cloud or dramatic storm cloud",
                "no dominant cloud competing with the woman and absence",
                "no second cloud or competing cloud collage",
                "no white or light cloud near the top",
                "no full beige page or full light background",
                "no giant cream enclosure or page-filling vignette",
                "no large bright heart icon, emoji, sticker, or UI symbol",
                "no icon collage or competing sadness symbols",
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
