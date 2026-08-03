"""Visual QC contracts, thresholds, and non-visual structural checks."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from .models import QCDecision, SceneBrief, VisualQCResult

ACCEPTANCE_THRESHOLDS = {
    "style_coherence": 8.0,
    "character_identity": 7.5,
    "scene_meaning": 8.0,
    "composition": 8.0,
    "natural_interaction": 8.0,
    "anatomy": 7.5,
    "visual_appeal": 8.0,
}


def validate_candidate_structure(path: Path, *, width: int, height: int) -> list[str]:
    failures: list[str] = []
    if not path.is_file() or path.stat().st_size == 0:
        return ["candidate image is missing or empty"]
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            if image.size != (width, height):
                failures.append(
                    f"candidate dimensions {image.size} do not match {(width, height)}"
                )
    except (OSError, SyntaxError) as exc:
        failures.append(f"candidate is not a readable image: {exc}")
    return failures


def scores_meet_acceptance(result: VisualQCResult, scene: SceneBrief) -> bool:
    values = result.model_dump()
    for field, threshold in ACCEPTANCE_THRESHOLDS.items():
        if field == "natural_interaction" and not scene.natural_interaction_required:
            continue
        if float(values[field]) < threshold:
            return False
    return result.minimum_score >= 7.0 and result.decision is QCDecision.PASS


def human_review_template() -> dict[str, object]:
    return {
        "score_source": "human_review",
        "style_coherence": None,
        "character_identity": None,
        "scene_meaning": None,
        "composition": None,
        "natural_interaction": None,
        "anatomy": None,
        "visual_appeal": None,
        "notes": "",
        "decision": None,
        "reviewer": "",
        "human_review_required": True,
    }


def unavailable_visual_qc() -> VisualQCResult:
    """Fail closed; structural checks are not image-understanding scores."""
    return VisualQCResult(
        style_coherence=0,
        character_identity=0,
        scene_meaning=0,
        composition=0,
        natural_interaction=0,
        anatomy=0,
        visual_appeal=0,
        score_source="heuristic",
        decision=QCDecision.REGENERATE,
        notes="No vision-model or human-review QC adapter supplied; no visual PASS claimed.",
    )
