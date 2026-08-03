from pathlib import Path

from tella.acceptance_script import canonicalize_script_bytes
from tella.cli import build_arg_parser
from tella.planner.practical_life_steps import plan_practical_life_steps_from_script
from tella.planner.practical_character_continuity import (
    aggregate_identity_decisions,
    canonical_character_fingerprint,
    classify_identity,
    generated_text_is_hard_failure,
    validate_symbol_only_overlay,
)
from scripts.benchmarks.prepare_tomorrow_visual_benchmark import (
    benchmark_execution_envelope,
    validate_benchmark,
)
from tella.media.image_provider import CloudflareImageProvider
from tella.visual_acceptance import (
    canonical_script_for_case,
    load_suite,
    visual_profile_for_case,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "configs/acceptance/scripts/prepare_tomorrow_night_before_v1.txt"
SUITE = ROOT / "configs/acceptance/practical_life_steps_visual_v1.json"
EXPECTED_HASH = "19ebb34b1f054b1379a9a1007305ee8ccabae4f10dad8107b43ef1a18730de12"


def _plan():
    sentences, canonical, digest = canonicalize_script_bytes(SCRIPT.read_bytes())
    assert digest == EXPECTED_HASH
    suite = load_suite(SUITE, repository_root=ROOT)
    profile = visual_profile_for_case(
        suite, "prepare_tomorrow_night_before", ROOT
    )
    plan = plan_practical_life_steps_from_script(
        user_script=canonical.removesuffix("\n"),
        target_lang="vi",
        preserve_narration=True,
        visual_profile=profile,
    )
    return sentences, plan


def test_new_canonical_script_is_registered_and_exact():
    suite = load_suite(SUITE)
    case, script = canonical_script_for_case(
        suite, "prepare_tomorrow_night_before", ROOT
    )
    assert case.expected_recipe == "practical_life_steps_callirrhoe_v1"
    assert case.expected_request_budget == 17
    assert script.canonical_script_sha256 == EXPECTED_HASH
    assert script.scene_count == 7
    assert case.visual_profile is not None
    assert case.visual_profile.profile_id == "prepare_tomorrow_visual_scenario_v1"


def test_script_text_never_selects_the_benchmark_profile_implicitly():
    _, canonical, _ = canonicalize_script_bytes(SCRIPT.read_bytes())
    exact_without_profile = plan_practical_life_steps_from_script(
        user_script=canonical.removesuffix("\n"),
        target_lang="vi",
        preserve_narration=True,
    )
    assert exact_without_profile.character_fingerprint == ""
    assert all(scene.composition_family == "" for scene in exact_without_profile.scenes)

    unrelated = (
        ROOT / "script_practical_life_steps_test.txt"
    ).read_text(encoding="utf-8").splitlines()
    benchmark_lines = canonical.removesuffix("\n").splitlines()
    unrelated[0] = benchmark_lines[0]
    similar_without_profile = plan_practical_life_steps_from_script(
        user_script="\n".join(unrelated),
        target_lang="vi",
        preserve_narration=True,
    )
    assert similar_without_profile.character_fingerprint == ""
    assert all(scene.composition_family == "" for scene in similar_without_profile.scenes)


def test_planner_records_seven_explicit_diverse_compositions():
    sentences, plan = _plan()
    scenes = [scene for scene in plan.scenes if scene.kind == "scene"]
    assert [scene.voice_script for scene in scenes] == list(sentences)
    assert [scene.scene_role for scene in scenes] == [
        "hook", "context", "practical_step", "practical_step",
        "practical_step", "common_mistake", "today_action",
    ]
    assert len({scene.composition_family for scene in scenes}) == 7
    assert len({scene.body_pose for scene in scenes}) == 7
    assert len({scene.camera_framing for scene in scenes}) == 7
    assert all(scene.character_count == 1 for scene in scenes)
    assert all(scene.scene_setting and scene.scene_action for scene in scenes)
    assert all(scene.character_placement and scene.primary_prop for scene in scenes)
    assert all(scene.secondary_props and scene.emotional_state for scene in scenes)


def test_prompts_lock_identity_and_fit_pre_submission_limit():
    _, plan = _plan()
    prompts = [scene.provider_prompt_variant for scene in plan.scenes]
    assert len(set(prompts)) == 7
    assert all(len(prompt.encode("utf-8")) <= 1850 for prompt in prompts)
    fingerprint = canonical_character_fingerprint()
    assert all(fingerprint in prompt for prompt in prompts)
    assert all("teal" in prompt and "round-collar" in prompt for prompt in prompts)
    assert all("young adult male" in prompt for prompt in prompts)
    assert plan.subtitle_layout_policy_id == "practical_dynamic_v1"
    assert all("lower 22%" not in prompt.lower() for prompt in prompts)
    assert all("extra person" in prompt for prompt in prompts)


def test_character_spec_and_fingerprint_are_immutable_across_seven_pose_diverse_scenes():
    _, plan = _plan()
    scenes = [scene for scene in plan.scenes if scene.kind == "scene"]
    fingerprint = canonical_character_fingerprint()
    assert plan.character_fingerprint == fingerprint
    assert plan.identity_continuity_strategy == "text_fingerprint_plus_seed_and_strict_qc"
    assert plan.identity_acceptance_standard.startswith("same recognizable designed character")
    assert {scene.character_fingerprint for scene in scenes} == {fingerprint}
    assert all(scene.identity_invariants and scene.forbidden_identity_changes for scene in scenes)
    assert len({scene.permitted_pose_variation for scene in scenes}) == 7


def test_cloudflare_provider_is_seeded_text_only_without_reference_conditioning():
    provider = CloudflareImageProvider()
    assert provider.supports_seed() is True
    assert provider.supports_reference_conditioning() is False


def test_scene_three_is_symbol_only_and_generated_marks_are_hard_failures():
    _, plan = _plan()
    scene = plan.scenes[2]
    prompt = scene.provider_prompt_variant.lower()
    assert "empty boxes and three colored circles" in prompt
    assert "no letters, numbers, labels, readable text, pseudo-writing" in prompt
    assert "phone" in prompt
    for key in ("readable_text", "pseudo_text", "labels", "digits"):
        assert generated_text_is_hard_failure({key: True}) is True
    assert generated_text_is_hard_failure({}) is False


def test_generic_symbol_overlay_is_safe_only_outside_character_and_without_text():
    safe = {
        "intersects_character": False, "contains_text": False,
        "contains_digits": False, "task_specific_raster_repair": False,
        "generic_reusable": True,
        "shapes": ["empty_box", "empty_box", "colored_circle"],
    }
    assert validate_symbol_only_overlay(safe)["passed"] is True
    assert validate_symbol_only_overlay({**safe, "intersects_character": True})["passed"] is False
    assert validate_symbol_only_overlay({**safe, "contains_text": True})["passed"] is False


def test_scene_six_is_policy_safe_simple_and_leaves_subtitles_to_renderer():
    _, plan = _plan()
    scene = plan.scenes[5]
    prompt = scene.provider_prompt_variant.lower()
    assert scene.subtitle_safe_lower_fraction == 0.0
    assert "one overfilled open bag" in prompt
    assert "three recognizable unneeded items" in prompt
    assert scene.subtitle_layout_policy_id == "practical_dynamic_v1"
    assert "lower 22% empty lane" not in prompt
    assert "many supplies" not in prompt
    assert "stuff" not in prompt
    assert "random background clutter" in prompt


def test_identity_hard_and_soft_policy_is_consistent_including_scene_seven():
    base = {
        "gender_age_matches": True, "hair_color_matches": True,
        "hair_silhouette_matches": True, "top_color_matches": True,
        "face_shape_matches": True, "body_build_matches": True,
        "head_present": True, "single_person": True,
        "minor_face_details_match": True, "hands_sufficient": True,
        "perspective_proportions_match": True,
    }
    passed = classify_identity(base)
    soft = classify_identity({**base, "minor_face_details_match": False})
    soft_face = classify_identity({**base, "face_shape_matches": False})
    hard = classify_identity({**base, "hair_silhouette_matches": False})
    assert passed["decision"] == "pass"
    assert soft["decision"] == "soft_fail"
    assert soft_face["decision"] == "soft_fail"
    assert hard["decision"] == "hard_fail"
    results = [{"scene_index": i, **passed} for i in range(1, 7)] + [
        {"scene_index": 7, **hard}
    ]
    aggregate = aggregate_identity_decisions(results)
    assert aggregate["passed"] is False
    assert aggregate["hard_failure_scene_indices"] == [7]
    assert aggregate["exact_pixel_identity_claimed"] is False


def test_bounded_fresh_rerun_accounting_stops_before_narration_and_render():
    envelope = benchmark_execution_envelope()
    assert envelope["source_job_id"] == "prepare_tomorrow_source_02"
    assert envelope["fresh_candidate_count_per_scene"] == 1
    assert envelope["maximum_targeted_candidates_per_failed_scene"] == 2
    assert envelope["maximum_cloudflare_submissions"] == 17
    assert envelope["maximum_transport_attempts_per_submission"] == 1
    assert envelope["automatic_provider_retries"] == 0
    assert envelope["fallbacks"] == 0
    assert envelope["gemini_submissions"] == 0
    assert envelope["narration_generation"] == 0
    assert envelope["music_processing"] == 0
    assert envelope["render_operations"] == 0
    assert envelope["stop_after_images"] is True


def test_benchmark_harness_validation_is_explicit_and_provider_free():
    result = validate_benchmark(ROOT)
    assert result["status"] == "validated_no_execution"
    assert result["visual_profile_id"] == "prepare_tomorrow_visual_scenario_v1"
    assert result["identity_mode"] == "approximate_character_continuity"
    assert result["actual_provider_calls"] == 0


def test_stop_after_images_is_an_explicit_cli_checkpoint():
    args = build_arg_parser().parse_args([
        "--recipe", "practical_life_steps_callirrhoe_v1",
        "--script-file", str(SCRIPT),
        "--lang", "vi",
        "--stop-after-images",
        "--regenerate-scene-indices", "3,5",
    ])
    assert args.stop_after_images is True
    assert args.regenerate_scene_indices == [3, 5]
