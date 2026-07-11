import json
from pathlib import Path

import pytest

import tella.cli as cli
from tella.planner.models import Scene, TellaScenePlan
from tella.recipes import (
    RecipeNotFoundError,
    apply_recipe_metadata,
    get_recipe,
    recipe_manifest,
    validate_recipe_run,
)


def _fail_call(*args, **kwargs):
    raise AssertionError("network/provider/render function must not be called")


def _basic_plan() -> TellaScenePlan:
    return TellaScenePlan(
        title="Recipe metadata",
        language="vi",
        aspect_ratio="9:16",
        media_source="ai_image",
        duration_mode="short",
        theme="cinematic",
        scenes=[
            Scene(scene_index=index, title=f"Scene {index}", voice_script="Một câu ngắn")
            for index in range(1, 4)
        ],
    )


def test_registry_lookup_returns_versioned_emotional_symbolic_recipe():
    recipe = get_recipe("emotional_symbolic_v1")

    assert recipe.recipe_version == 1
    assert recipe.status == "production"
    assert recipe.narrative_mode == "emotional_reflection"
    assert recipe.planner_id == "symbolic_emotional"
    assert recipe.visual_theme_id == "minimalist_symbolic_reel"
    assert recipe.voice_profile_id == "soft_female_vi"
    assert recipe.subtitle_style_id == "reel_minimal"
    assert recipe.transition_profile_id == "subtle_crossfade"
    assert recipe.motion_profile_id == "slow_ken_burns"
    assert recipe.scene_range == [7, 8]
    assert recipe.duration_range == [32.0, 38.0]
    assert recipe.narration_mode == "continuous"
    assert recipe.aspect_ratio == "9:16"


def test_unknown_recipe_error_is_explicit():
    with pytest.raises(RecipeNotFoundError, match="unknown recipe"):
        get_recipe("missing_recipe")


def test_scene_count_validation_rejects_below_and_above_range():
    recipe = get_recipe("emotional_symbolic_v1")

    assert "below recipe minimum 7" in " ".join(
        validate_recipe_run(recipe, scene_count=6)
    )
    assert "exceeds recipe maximum 8" in " ".join(
        validate_recipe_run(recipe, scene_count=9)
    )
    assert validate_recipe_run(recipe, scene_count=7) == []
    assert validate_recipe_run(recipe, scene_count=8) == []


def test_duration_validation_rejects_below_and_above_range():
    recipe = get_recipe("emotional_symbolic_v1")

    assert "below recipe minimum 32s" in " ".join(
        validate_recipe_run(recipe, estimated_duration_seconds=31.9)
    )
    assert "exceeds recipe maximum 38s" in " ".join(
        validate_recipe_run(recipe, estimated_duration_seconds=38.1)
    )
    assert validate_recipe_run(recipe, estimated_duration_seconds=35.0) == []


def test_aspect_and_narration_validation_are_explicit():
    recipe = get_recipe("emotional_symbolic_v1")
    errors = validate_recipe_run(
        recipe,
        aspect_ratio="16:9",
        narration_mode="per_scene",
    )

    assert "aspect ratio 16:9" in errors[0]
    assert "narration mode per_scene" in errors[1]


def test_recipe_metadata_serializes_to_plan_and_manifest():
    recipe = get_recipe("emotional_symbolic_v1")
    plan = _basic_plan()
    apply_recipe_metadata(
        plan,
        recipe,
        validation_status="failed",
        validation_errors=["scene count 3 is below recipe minimum 7"],
    )

    payload = plan.model_dump()
    assert payload["recipe_id"] == "emotional_symbolic_v1"
    assert payload["recipe_version"] == 1
    assert payload["recipe_status"] == "production"
    assert payload["visual_theme_id"] == "minimalist_symbolic_reel"
    assert payload["recipe_scene_range"] == [7, 8]
    assert payload["recipe_duration_range"] == [32.0, 38.0]
    assert payload["recipe_validation_status"] == "failed"
    assert payload["recipe_validation_errors"]

    manifest = recipe_manifest(recipe, validation_status="definition_validated")
    assert manifest["recipe_id"] == payload["recipe_id"]
    assert manifest["recipe_status"] == "production"
    assert manifest["recipe_scene_range"] == [7, 8]
    assert manifest["recipe_validation_errors"] == []


def test_plan_without_recipe_keeps_backward_compatible_defaults():
    payload = _basic_plan().model_dump()

    assert payload["recipe_id"] == ""
    assert payload["recipe_version"] == 0
    assert payload["recipe_validation_status"] == "not_selected"
    assert payload["recipe_validation_errors"] == []


def test_list_recipes_makes_no_network_or_pipeline_calls(monkeypatch, capsys):
    for name in (
        "translate_topic",
        "plan_story",
        "plan_story_from_script",
        "fetch_assets",
        "synthesize_all",
        "render",
    ):
        monkeypatch.setattr(cli, name, _fail_call)

    result = cli.main(["--list-recipes"])

    assert result == 0
    output = capsys.readouterr().out
    assert "emotional_symbolic_v1 v1 [production]" in output
    assert "theme=minimalist_symbolic_reel" in output


def test_dry_run_recipe_writes_only_local_recipe_metadata(
    monkeypatch,
    tmp_path,
):
    for name in (
        "translate_topic",
        "plan_story",
        "plan_story_from_script",
        "fetch_assets",
        "synthesize_all",
        "render",
    ):
        monkeypatch.setattr(cli, name, _fail_call)

    result = cli.main(
        [
            "--recipe",
            "emotional_symbolic_v1",
            "--dry-run-recipe",
            "--out",
            str(tmp_path),
            "--job-id",
            "recipe_dry",
        ]
    )

    assert result == 0
    files = [path.name for path in (tmp_path / "recipe_dry").iterdir()]
    assert files == ["recipe.json"]
    data = json.loads(
        (tmp_path / "recipe_dry" / "recipe.json").read_text(encoding="utf-8")
    )
    assert data["recipe_validation_status"] == "definition_validated"
    assert data["recipe_validation_errors"] == []


def test_recipe_selection_overrides_explicit_different_theme(
    monkeypatch,
    tmp_path,
    caplog,
):
    captured = {}

    async def fake_run_pipeline(**kwargs):
        captured.update(kwargs)
        return tmp_path / "plan.json"

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")

    result = cli.main(
        [
            "--recipe",
            "emotional_symbolic_v1",
            "--topic",
            "test",
            "--lang",
            "vi",
            "--theme",
            "cinematic",
            "--dry-run-plan",
            "--out",
            str(tmp_path),
        ]
    )

    assert result == 0
    assert captured["theme"] == "minimalist_symbolic_reel"
    assert captured["recipe"].recipe_id == "emotional_symbolic_v1"
    assert captured["tts_continuous"] is True
    assert "overrides requested theme cinematic" in caplog.text


def test_default_cli_path_does_not_select_recipe(monkeypatch, tmp_path):
    captured = {}

    async def fake_run_pipeline(**kwargs):
        captured.update(kwargs)
        return Path(tmp_path) / "plan.json"

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")

    result = cli.main(
        [
            "--topic",
            "test",
            "--lang",
            "vi",
            "--theme",
            "cinematic",
            "--dry-run-plan",
            "--out",
            str(tmp_path),
        ]
    )

    assert result == 0
    assert captured["theme"] == "cinematic"
    assert captured["recipe"] is None
