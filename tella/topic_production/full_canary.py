"""Authoritative visual-only scene design for the first full Klein 4B canary."""

from __future__ import annotations

from dataclasses import dataclass

from .models import (
    AcceptancePriority,
    ProductionSceneBrief,
    ReferenceStrategy,
    SceneComplexity,
    SceneType,
    StoryPlan,
)
from .story_plan_identity import canonical_story_plan_sha256

FULL_CANARY_STORY_ID = "supplied_eligible_acceptance_story:82d876cb5ef79698"
FULL_CANARY_STORY_PLAN_SHA256 = "5316d0fc18b9936039f104712219e248ddf7eb76c2b0c14ceb3b3a311441a66a"
_FULL_CANARY_BEAT_IDS = tuple(f"beat_{order:02d}" for order in range(1, 9))


@dataclass(frozen=True)
class _VisualSpec:
    scene_type: SceneType
    action: tuple[str, ...]
    environment: tuple[str, ...]
    objects: tuple[str, ...]
    symbols: tuple[str, ...]
    composition: tuple[str, ...]
    negative_space: tuple[str, ...]
    visual_hierarchy: tuple[str, ...]
    hard_negatives: tuple[str, ...] = ()
    complexity: SceneComplexity = SceneComplexity.MODERATE
    acceptance_priority: AcceptancePriority = AcceptancePriority.STANDARD


_VISUAL_SPECS = (
    _VisualSpec(
        scene_type=SceneType.SOLO_EMOTIONAL_VIGNETTE,
        action=("standing still with lowered shoulders beneath a small umbrella",),
        environment=(
            "quiet rain-dark bus stop at dusk",
            "wet pavement reflecting one restrained warm light",
        ),
        objects=("small closed bag held loosely at her side", "simple umbrella"),
        symbols=("thin rain lines carrying the feeling of accumulated effort",),
        composition=(
            "wide full-body view with the woman small in the lower-left third",
            "bus shelter edge creates one asymmetrical vertical frame",
        ),
        negative_space=("large dark rainy field above and to the right",),
        visual_hierarchy=("exhausted woman", "rain atmosphere", "small warm reflection"),
    ),
    _VisualSpec(
        scene_type=SceneType.ORGANIC_DAILY_VIGNETTE,
        action=("sitting on the edge of an unmade bed while setting a dark phone face-down",),
        environment=(
            "quiet early-morning bedroom",
            "soft bedside light and rumpled blanket",
        ),
        objects=("dark phone", "bedside glass of water", "folded blanket"),
        symbols=("small unlit bedside clock suggesting neglected rest",),
        composition=(
            "medium-high three-quarter view rather than a frontal standing pose",
            "bed forms a low diagonal supporting the gesture",
        ),
        negative_space=("calm wall and dim window space above the bed",),
        visual_hierarchy=("phone-setting gesture", "tired posture", "resting environment"),
    ),
    _VisualSpec(
        scene_type=SceneType.JOURNEY_TRANSITION,
        action=("pausing halfway up a quiet stair landing with one hand resting on the rail",),
        environment=(
            "minimal apartment stairwell",
            "one soft cream doorway glow behind the landing",
        ),
        objects=("simple stair rail", "small everyday shoulder bag"),
        symbols=("stairs continue upward but she deliberately stops",),
        composition=(
            "side-profile full-body view on a rising diagonal",
            "woman placed near center-left with visible space ahead",
        ),
        negative_space=("open upper landing and dark wall remain uncluttered",),
        visual_hierarchy=("intentional pause", "ascending path", "cream doorway glow"),
        complexity=SceneComplexity.COMPLEX,
        acceptance_priority=AcceptancePriority.HIGH,
    ),
    _VisualSpec(
        scene_type=SceneType.SELF_COMPASSION,
        action=(
            "sitting cross-legged on a floor cushion with eyes gently lowered",
            "one hand on her chest and one hand relaxed on her knee while breathing",
        ),
        environment=(
            "window alcove with a thin curtain",
            "quiet indoor evening light",
        ),
        objects=("round floor cushion", "small untouched cup of warm tea"),
        symbols=("one faint expanding breath line near her chest",),
        composition=(
            "intimate medium shot with a clear triangular seated silhouette",
            "window glow offset behind one shoulder rather than centered",
        ),
        negative_space=("soft curtain and dark wall create breathing room",),
        visual_hierarchy=("breathing gesture", "stable seated silhouette", "quiet tea"),
        acceptance_priority=AcceptancePriority.HIGH,
    ),
    _VisualSpec(
        scene_type=SceneType.SOLO_EMOTIONAL_VIGNETTE,
        action=("gently wiping one tear with a tissue while leaning beside a washbasin",),
        environment=(
            "small quiet bathroom with no visible mirror reflection",
            "matte tiled wall reduced to a few hand-drawn lines",
        ),
        objects=("single tissue", "plain washbasin", "folded hand towel"),
        symbols=("one tiny falling water mark echoed below the tear",),
        composition=(
            "close three-quarter portrait focused on hand-to-face interaction",
            "washbasin curve anchors the lower-right edge",
        ),
        negative_space=("plain tiled wall remains open around her head and hand",),
        visual_hierarchy=("tear-wiping hand", "soft face", "restrained bathroom cues"),
        hard_negatives=("no duplicate woman or reflected second face",),
        acceptance_priority=AcceptancePriority.HIGH,
    ),
    _VisualSpec(
        scene_type=SceneType.SELF_COMPASSION,
        action=(
            "reclining slightly against a park bench with shoulders released",
            "face tilted toward the air during one unhurried breath",
        ),
        environment=(
            "quiet small garden after rain",
            "simple park bench beneath sparse leaves",
        ),
        objects=("wooden bench", "closed umbrella resting safely beside her"),
        symbols=("two light leaves drifting apart above the bench",),
        composition=(
            "wide horizontal scene cluster within the vertical canvas",
            "woman occupies the lower-right third with garden depth to the left",
        ),
        negative_space=("open muted sky fills the upper half",),
        visual_hierarchy=("released posture", "open air", "small drifting leaves"),
    ),
    _VisualSpec(
        scene_type=SceneType.JOURNEY_TRANSITION,
        action=("walking slowly along a warm garden path with one hand near her chest",),
        environment=("simple path between low plants and a cream-lit doorway in the distance",),
        objects=("small cloth bag moving naturally with her step",),
        symbols=("a few new leaves appear along the path without becoming icons",),
        composition=(
            "full-body walking profile with visible forward motion",
            "curving path leads from lower-left toward upper-right",
        ),
        negative_space=("dark taupe garden field surrounds the narrow path",),
        visual_hierarchy=("gentle forward step", "curving path", "distant warm doorway"),
        complexity=SceneComplexity.COMPLEX,
        acceptance_priority=AcceptancePriority.HIGH,
    ),
    _VisualSpec(
        scene_type=SceneType.CLOSURE_VIGNETTE,
        action=(
            "standing at an open balcony doorway holding a warm cup with both hands",
            "looking into the first quiet light without urgency",
        ),
        environment=(
            "minimal home balcony at dawn",
            "soft curtain and one small potted plant",
        ),
        objects=("warm cup", "small potted plant", "light curtain"),
        symbols=("restrained cream sunrise arc suggesting patience rather than triumph",),
        composition=(
            "rear three-quarter medium-wide view",
            "doorway frames her on the right while dawn opens on the left",
        ),
        negative_space=("broad quiet dawn field closes the series",),
        visual_hierarchy=("calm recurring woman", "open dawn", "warm cup"),
        complexity=SceneComplexity.COMPLEX,
        acceptance_priority=AcceptancePriority.CONTINUITY_CRITICAL,
    ),
)


def build_full_canary_scene_briefs(plan: StoryPlan) -> list[ProductionSceneBrief]:
    """Re-author only visuals while preserving the supplied story byte-for-byte."""

    if plan.requested_scene_count != 8 or len(plan.semantic_beats) != 8:
        raise ValueError("full Klein canary visual design requires exactly eight beats")
    if plan.language != "vi":
        raise ValueError("full Klein canary visual design requires Vietnamese narration")
    story_id = f"{plan.planner_metadata.planner_id}:{plan.planner_metadata.deterministic_key}"
    if story_id != FULL_CANARY_STORY_ID:
        raise ValueError("full Klein canary visual design is locked to one StoryPlan")
    if canonical_story_plan_sha256(plan) != FULL_CANARY_STORY_PLAN_SHA256:
        raise ValueError("full Klein canary StoryPlan SHA-256 does not match")
    if tuple(beat.beat_id for beat in plan.semantic_beats) != _FULL_CANARY_BEAT_IDS:
        raise ValueError("full Klein canary beat IDs do not match")

    identity_requirements = [
        "preserve the approved recurring female identity from CHARACTER_IDENTITY authority"
    ]
    continuity_requirements = [
        "same recurring woman across all eight scenes",
        "same muted handmade editorial universe",
    ]
    briefs = []
    for beat, spec in zip(plan.semantic_beats, _VISUAL_SPECS, strict=True):
        briefs.append(
            ProductionSceneBrief(
                scene_id=f"scene_{beat.order:02d}",
                order=beat.order,
                scene_type=spec.scene_type,
                narrative_text=beat.narration_segment,
                meaning=beat.semantic_purpose,
                emotional_tone=list(dict.fromkeys([beat.emotional_state, "gentle"])),
                topic_intent=plan.topic_intent,
                characters=["recurring_woman"],
                identity_requirements=identity_requirements,
                continuity_requirements=continuity_requirements,
                action=list(spec.action),
                interaction={
                    "primary": (
                        "the recurring woman, action, environment, and props form "
                        "one anatomically coherent complete illustration"
                    )
                },
                environment=list(spec.environment),
                objects=list(spec.objects),
                symbols=list(spec.symbols),
                composition=list(spec.composition),
                negative_space_requirements=list(spec.negative_space),
                visual_hierarchy=list(spec.visual_hierarchy),
                reference_roles=["female_identity_anchor", "style_anchor"],
                reference_strategy=ReferenceStrategy(
                    strategy="approved_static_topic_and_identity_anchors",
                    accepted_scene_chaining=False,
                    notes=(
                        "Fixed approved female/style anchor only; no generated-scene "
                        "chaining in the controlled full canary."
                    ),
                ),
                hard_negatives=[
                    "no recurring male or couple",
                    "no extra detailed person",
                    "no icon collage or pasted asset",
                    "no top title or readable text",
                    *spec.hard_negatives,
                ],
                complexity=spec.complexity,
                acceptance_priority=spec.acceptance_priority,
                source_beat_id=beat.beat_id,
                duration_seconds=beat.duration_seconds,
            )
        )
    return briefs


__all__ = [
    "FULL_CANARY_STORY_ID",
    "FULL_CANARY_STORY_PLAN_SHA256",
    "build_full_canary_scene_briefs",
]
