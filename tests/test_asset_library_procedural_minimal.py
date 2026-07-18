from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image

from tella.asset_library.background_renderer import render_procedural_background, resolve_background_mode
from tella.asset_library.production_mvp import BASE_SEED, build_seven_scene_plan
from tella.asset_library.semantic_resolver import (
    build_production_scene_request,
    compose_asset_library_scene,
    select_semantic_asset,
)

ROOT = Path(r"D:\tella-assets-staging\mvp_v1_processed_v2")
SEMANTICS = Path(r"D:\tella-production-resolver\scripts\asset_batch\asset_semantics_patch.json")
TRUNCATED = {
    "stand_front_backup",
    "stand_wave",
    "stand_hands_clasped",
    "walk_left_backup",
    "walk_right_backup",
}


def _first_request():
    return build_production_scene_request(build_seven_scene_plan(enabled=True).scenes[0])


def _resolution(request):
    return select_semantic_asset(SEMANTICS, ROOT, request)


def test_procedural_mode_does_not_load_scenic_background(monkeypatch):
    monkeypatch.setenv("TELLA_ASSET_BACKGROUND_MODE", "procedural_minimal")
    import tella.asset_library.semantic_resolver as resolver

    monkeypatch.setattr(
        resolver,
        "_resolve_background",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("scenic background loaded")),
    )
    resolution = _resolution(_first_request())
    assert resolution.background_path == ""


def test_procedural_background_is_deterministic_and_profile_specific():
    first, metadata = render_procedural_background("dark_brown", BASE_SEED)
    second, _ = render_procedural_background("dark_brown", BASE_SEED)
    other, other_metadata = render_procedural_background("warm_beige", BASE_SEED)
    assert first.size == second.size == other.size == (1080, 1920)
    assert hashlib.sha256(first.tobytes()).digest() == hashlib.sha256(second.tobytes()).digest()
    assert hashlib.sha256(first.tobytes()).digest() != hashlib.sha256(other.tobytes()).digest()
    assert metadata["mode"] == "procedural_minimal"
    assert metadata["profile"] == "dark_brown"
    assert other_metadata["base_color"] != metadata["base_color"]


def test_background_mode_defaults_to_scenic_and_rejects_unknown(monkeypatch):
    monkeypatch.delenv("TELLA_ASSET_BACKGROUND_MODE", raising=False)
    assert resolve_background_mode() == "scenic_asset"
    with pytest.raises(ValueError, match="Unsupported TELLA_ASSET_BACKGROUND_MODE"):
        resolve_background_mode("not-a-mode")


def test_scenic_mode_still_resolves_existing_background(monkeypatch):
    monkeypatch.setenv("TELLA_ASSET_BACKGROUND_MODE", "scenic_asset")
    resolution = _resolution(_first_request())
    assert Path(resolution.background_path).is_file()


def test_procedural_composition_has_grounding_metadata_and_valid_layers(monkeypatch, tmp_path):
    monkeypatch.setenv("TELLA_ASSET_BACKGROUND_MODE", "procedural_minimal")
    metadata = compose_asset_library_scene(
        build_seven_scene_plan(enabled=True).scenes[0],
        tmp_path / "scene.png",
        ROOT,
        SEMANTICS,
    )
    assert metadata["background"]["mode"] == "procedural_minimal"
    assert metadata["background"]["profile"] == "dark_brown"
    assert metadata["layout_template"] == "floor_sit_center"
    assert metadata["character"]["production_eligible"] is True
    placement = metadata["character"]["placement"]
    assert placement["ground_x"] == placement["ground_anchor"]["x"]
    assert placement["ground_y"] == placement["ground_anchor"]["y"]
    assert metadata["character"]["contact_shadow"]["opacity"] <= 40
    assert metadata["layer_order"][:2] == ["procedural_background", "background_effects"]
    assert metadata["layer_order"][-1] == "global_harmonization"
    assert all(item["placement"]["width"] > 0 and item["placement"]["height"] > 0 for item in metadata["objects"])
    assert len(metadata["objects"]) <= 2
    assert Path(tmp_path / "scene.png").is_file()


def test_floor_phone_is_flattened_and_shadowed(monkeypatch, tmp_path):
    monkeypatch.setenv("TELLA_ASSET_BACKGROUND_MODE", "procedural_minimal")
    plan = build_seven_scene_plan(enabled=True)
    scene = plan.scenes[0]
    metadata = compose_asset_library_scene(scene, tmp_path / "scene.png", ROOT, SEMANTICS)
    phone = next(item for item in metadata["objects"] if item["asset_id"] == "phone_dark")
    assert phone["placement"]["floor_perspective_applied"] is True
    assert phone["placement"]["scale_y"] == pytest.approx(0.66)
    assert 8 <= phone["placement"]["rotation_degrees"] <= 15
    assert phone["shadow"]["width"] > 0


def test_same_scene_composition_hash_is_deterministic(monkeypatch, tmp_path):
    monkeypatch.setenv("TELLA_ASSET_BACKGROUND_MODE", "procedural_minimal")
    plan = build_seven_scene_plan(enabled=True)
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    compose_asset_library_scene(plan.scenes[4], first, ROOT, SEMANTICS)
    compose_asset_library_scene(plan.scenes[4], second, ROOT, SEMANTICS)
    assert hashlib.sha256(first.read_bytes()).hexdigest() == hashlib.sha256(second.read_bytes()).hexdigest()


def test_all_benchmark_selections_remain_eligible_and_truncated_assets_excluded(monkeypatch):
    monkeypatch.setenv("TELLA_ASSET_BACKGROUND_MODE", "procedural_minimal")
    plan = build_seven_scene_plan(enabled=True)
    selected = [_resolution(build_production_scene_request(scene)) for scene in plan.scenes]
    assert all(item.production_eligible for item in selected)
    assert not any(item.selected_source_asset_id in TRUNCATED for item in selected)


def test_minimal_scene_png_is_1080x1920(monkeypatch, tmp_path):
    monkeypatch.setenv("TELLA_ASSET_BACKGROUND_MODE", "procedural_minimal")
    output = tmp_path / "scene.png"
    compose_asset_library_scene(build_seven_scene_plan(enabled=True).scenes[6], output, ROOT, SEMANTICS)
    with Image.open(output) as image:
        assert image.size == (1080, 1920)
