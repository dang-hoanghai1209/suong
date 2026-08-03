from __future__ import annotations

import pytest

from tella.planner.practical_character_continuity import (
    IdentityMode,
    aggregate_visual_decisions,
    classify_identity,
    resolve_identity_mode,
)
from tella.planner.practical_prompt_policy import (
    PRACTICAL_PROVIDER_PROMPT_MAX_BYTES,
    build_priority_prompt,
    prompt_section_byte_counts,
    validate_priority_prompt,
)
from tella.render.pipeline import _build_bg_filter
from tella.render.subtitle_layout import resolve_practical_subtitle_layout


def _box(top: float, bottom: float) -> dict[str, object]:
    return {"left": 0.08, "top": top, "right": 0.92, "bottom": bottom, "label": "protected"}


def test_dynamic_subtitle_prefers_lower_then_upper_then_middle_lower():
    assert resolve_practical_subtitle_layout([]).placement == "lower"
    assert resolve_practical_subtitle_layout([_box(0.66, 0.80)]).placement == "upper"
    decision = resolve_practical_subtitle_layout([_box(0.19, 0.35), _box(0.66, 0.81)])
    assert decision.placement == "middle_lower"


def test_dynamic_subtitle_panel_translation_and_fail_closed_are_deterministic():
    panel = resolve_practical_subtitle_layout([], busy_regions=["lower", "upper", "middle_lower"])
    assert panel.translucent_panel is True
    protected = [_box(0.20, 0.80)]
    failed = resolve_practical_subtitle_layout(protected)
    assert failed.status == "failed"
    assert failed.metadata() == resolve_practical_subtitle_layout(protected).metadata()
    translated = resolve_practical_subtitle_layout(
        [{"left": 0.08, "top": 0.60, "right": 0.92, "bottom": 0.72}],
        busy_regions=["upper", "middle_lower"], translation_safe=True,
    )
    assert translated.status == "passed"
    assert translated.image_translation_y_ratio != 0
    filter_graph = _build_bg_filter(
        is_video=False, canvas_w=768, canvas_h=1344, duration=3.0,
        ken_burns_max_scale=1.02, image_translation_y_ratio=-0.06,
    )
    assert "max((" in filter_graph and "-0.0600" in filter_graph


def test_priority_prompt_is_ordered_utf8_bounded_and_never_truncated():
    sections = {
        "hard_negatives": "No text.",
        "character_identity": "fixed recognizable character",
        "required_props": "one notebook",
        "action_setting": "write tomorrow's plan at a desk",
        "style": "flat illustration",
        "composition": "medium shot",
    }
    prompt = build_priority_prompt(sections)
    assert prompt.startswith(sections["action_setting"])
    assert validate_priority_prompt(prompt) == len(prompt.encode("utf-8"))
    assert list(prompt_section_byte_counts(sections)) == [
        "action_setting", "required_props", "character_identity",
        "composition", "style", "hard_negatives",
    ]
    oversized = {**sections, "action_setting": "đ" * PRACTICAL_PROVIDER_PROMPT_MAX_BYTES}
    with pytest.raises(ValueError, match="rejected without truncation"):
        build_priority_prompt(oversized)
    assert oversized["action_setting"].endswith("đ")


def test_approximate_identity_is_honest_and_reference_mode_fails_closed():
    base = {
        "gender_age_matches": True, "hair_color_matches": True,
        "hair_silhouette_matches": True, "top_color_matches": True,
        "body_build_matches": True, "head_present": True, "single_person": True,
        "face_shape_matches": True, "minor_face_details_match": True,
        "hands_sufficient": True, "perspective_proportions_match": True,
    }
    assert classify_identity({**base, "face_shape_matches": False})["decision"] == "soft_fail"
    result = classify_identity({**base, "hair_silhouette_matches": False})
    assert result["decision"] == "hard_fail"
    assert result["exact_pixel_identity_claimed"] is False
    with pytest.raises(RuntimeError, match="proven provider image-reference support"):
        resolve_identity_mode(IdentityMode.reference_conditioned_character,
                              provider_supports_reference_conditioning=False)


def test_semantic_failure_cannot_be_overridden_by_identity_pass():
    rows = [
        {"scene_index": index, "decision": "pass", "semantic_passed": index != 3,
         "semantic_contradiction": False}
        for index in range(1, 8)
    ]
    aggregate = aggregate_visual_decisions(rows)
    assert aggregate["passed"] is False
    assert aggregate["semantic_failure_scene_indices"] == [3]
