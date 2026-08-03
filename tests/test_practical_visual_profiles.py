from __future__ import annotations

import json
from pathlib import Path

import pytest

from tella.planner.practical_character_continuity import (
    canonical_character_fingerprint,
    canonical_character_payload,
    canonical_identity_prompt,
)
from tella.planner.practical_life_steps import plan_practical_life_steps_from_script
from tella.planner.practical_visual_profiles import (
    PracticalVisualProfile,
    load_practical_visual_profile,
)


SCRIPT = (
    Path(__file__).resolve().parents[1] / "script_practical_life_steps_test.txt"
).read_text(encoding="utf-8")
ROLES = (
    "hook", "context", "practical_step", "practical_step", "practical_step",
    "common_mistake", "today_action",
)


def _payload(profile_id: str = "synthetic_visual_profile_v1") -> dict:
    scenes = []
    for index, role in enumerate(ROLES, start=1):
        scenes.append({
            "scene_index": index,
            "scene_role": role,
            "setting": f"generic setting {index}",
            "primary_action": f"generic action {index}",
            "primary_prop": f"generic prop {index}",
            "secondary_props": [f"secondary prop {index}"],
            "body_pose": f"generic pose {index}",
            "character_placement": f"generic placement {index}",
            "camera_framing": f"generic framing {index}",
            "composition_family": f"generic composition {index}",
            "emotional_state": "calm",
            "semantic_hard_negatives": ["No unrelated object."],
        })
    return {
        "schema_version": 1,
        "profile_id": profile_id,
        "identity_mode": "approximate_character_continuity",
        "identity_continuity_strategy": "explicit_profile",
        "identity_acceptance_standard": "recognizable character",
        "character_fingerprint": canonical_character_fingerprint(),
        "canonical_character_spec": canonical_character_payload(),
        "character_identity_prompt": canonical_identity_prompt(),
        "identity_invariants": ["stable character"],
        "forbidden_identity_changes": ["duplicated character"],
        "cast_archetype": "generic_adult",
        "style_instruction": "Wordless flat illustration.",
        "global_hard_negatives": ["No text."],
        "subtitle_layout_policy_id": "practical_dynamic_v1",
        "scenes": scenes,
    }


def test_default_planning_does_not_apply_an_explicit_profile():
    plan = plan_practical_life_steps_from_script(
        user_script=SCRIPT, target_lang="vi", preserve_narration=True
    )
    assert plan.character_fingerprint == ""
    assert plan.identity_continuity_strategy == ""
    assert all(scene.composition_family == "" for scene in plan.scenes)


def test_same_planner_consumes_a_different_explicit_profile():
    profile = PracticalVisualProfile.model_validate(_payload())
    plan = plan_practical_life_steps_from_script(
        user_script=SCRIPT,
        target_lang="vi",
        preserve_narration=True,
        visual_profile=profile,
    )
    assert plan.character_fingerprint == profile.character_fingerprint
    assert [scene.composition_family for scene in plan.scenes] == [
        f"generic composition {index}" for index in range(1, 8)
    ]
    assert all("generic action" in scene.provider_prompt_variant for scene in plan.scenes)


def test_profile_loader_fails_missing_unknown_malformed_and_role_mismatch(tmp_path):
    with pytest.raises(FileNotFoundError, match="visual profile is missing"):
        load_practical_visual_profile(tmp_path / "missing.json")

    path = tmp_path / "profile.json"
    path.write_text(json.dumps(_payload()), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown visual profile ID"):
        load_practical_visual_profile(path, expected_profile_id="unknown_profile_v1")
    with pytest.raises(ValueError, match="scene-role order mismatch"):
        load_practical_visual_profile(path, expected_scene_roles=tuple(reversed(ROLES)))

    malformed = _payload()
    malformed["scenes"][1]["scene_index"] = 1
    path.write_text(json.dumps(malformed), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate scene indices"):
        load_practical_visual_profile(path)

    missing_index = _payload()
    del missing_index["scenes"][1]
    path.write_text(json.dumps(missing_index), encoding="utf-8")
    with pytest.raises(ValueError, match="ordered and contiguous"):
        load_practical_visual_profile(path)

    malformed_scene = _payload()
    del malformed_scene["scenes"][0]["setting"]
    path.write_text(json.dumps(malformed_scene), encoding="utf-8")
    with pytest.raises(ValueError, match="setting"):
        load_practical_visual_profile(path)


def test_production_planner_sources_have_no_benchmark_coupling():
    root = Path(__file__).resolve().parents[1]
    production_files = tuple((root / "tella/planner").glob("*.py"))
    forbidden = (
        "prepare" + "_tomorrow",
        "prepare" + "-tomorrow",
        "prepare" + "_tomorrow_source_",
    )
    for path in production_files:
        text = path.read_text(encoding="utf-8").lower()
        assert not any(token in text for token in forbidden), path

    harness_name = "prepare" + "_tomorrow_visual_benchmark"
    for path in (root / "tella").rglob("*.py"):
        assert harness_name not in path.read_text(encoding="utf-8"), path
