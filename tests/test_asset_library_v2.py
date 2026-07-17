from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tella.asset_library.semantic_resolver import (
    AssetLibraryRequest,
    _safe_candidate_pool,
    select_semantic_asset,
)
from tella.media.fetch import fetch_assets
from tella.planner.models import Scene, TellaScenePlan

V2_ROOT = Path(r"D:\tella-assets-staging\mvp_v1_processed_v2")
SEMANTICS_PATH = Path(r"D:\tella-production-resolver\scripts\asset_batch\asset_semantics_patch.json")
SOURCE_TRUNCATED_ASSETS = (
    ("stand_front_backup", "front"),
    ("stand_wave", "front"),
    ("stand_hands_clasped", "front"),
    ("walk_left_backup", "left"),
    ("walk_right_backup", "right"),
)


def test_production_resolver_loads_registry_and_semantics():
    request = AssetLibraryRequest(
        character_id="female_01",
        action="sit_hug_knees",
        emotion="sad",
        direction="front",
        location="bedroom",
        time_of_day="night",
        objects=["pillow", "phone_dark"],
        composition_preset="bedroom_floor_sitting",
        seed=12345,
    )
    selected = select_semantic_asset(SEMANTICS_PATH, V2_ROOT, request)
    assert selected.selected_semantic_id == "sit_hug_knees_sad"
    assert selected.selected_source_asset_id == "sit_hug_knees_backup"
    assert selected.selection_score == 150
    assert selected.selection_reasons
    assert selected.score_breakdown == {
        "exact_action": 100,
        "exact_emotion": 50,
        "related_emotion": 0,
        "exact_direction": 5,
        "enabled": 0,
        "canonical": 0,
        "core_tier": 0,
        "backup_tier": -5,
        "total": 150,
    }
    assert selected.production_eligible is True
    assert selected.quality_status == "approved"


@pytest.mark.parametrize(("asset_id", "direction"), SOURCE_TRUNCATED_ASSETS)
def test_production_resolver_excludes_ineligible_candidate_before_fallback_ranking(
    asset_id, direction
):
    semantics = json.loads(SEMANTICS_PATH.read_text(encoding="utf-8"))
    excluded = next(
        candidate for candidate in semantics["assets"]
        if candidate["source_asset_id"] == asset_id
    )
    assert excluded["production_eligible"] is False
    assert excluded["quality_status"] == "source_truncated"
    request = AssetLibraryRequest(
        character_id="female_01",
        action=asset_id,
        emotion="neutral",
        direction=direction,
        location="bedroom",
        time_of_day="night",
        objects=[],
        composition_preset="bedroom_floor_sitting",
        seed=7,
    )
    candidate_pool = _safe_candidate_pool(semantics, request)
    assert excluded not in candidate_pool
    assert all(candidate.get("production_eligible") is not False for candidate in candidate_pool)
    selected = select_semantic_asset(SEMANTICS_PATH, V2_ROOT, request)
    assert selected.selected_source_asset_id != asset_id
    assert selected.production_eligible is True
    assert selected.fallback_reason == "no_production_eligible_exact_action"


def test_background_and_objects_resolve_to_processed_v2_paths():
    request = AssetLibraryRequest(
        character_id="female_01",
        action="sit_hug_knees",
        emotion="sad",
        direction="front",
        location="bedroom",
        time_of_day="night",
        objects=["pillow", "phone_dark"],
        composition_preset="bedroom_floor_sitting",
        seed=12345,
    )
    selected = select_semantic_asset(SEMANTICS_PATH, V2_ROOT, request)
    assert selected.background_path.endswith("bedroom_night_01.png")
    assert selected.object_paths["pillow"].endswith("01_pillow.png")
    assert selected.object_paths["phone_dark"].endswith("01_phone_dark.png")
    assert selected.character_processed_path.endswith("sit_hug_knees_backup.png")


def test_asset_library_mode_writes_scene_metadata_and_png(tmp_path, monkeypatch):
    monkeypatch.setenv("TELLA_ASSET_LIBRARY_V2", "1")
    monkeypatch.setenv("TELLA_ASSET_LIBRARY_ROOT", str(V2_ROOT))
    job_dir = tmp_path / "job"
    job_dir.mkdir(parents=True, exist_ok=True)
    scene = Scene(kind="scene", scene_index=1, title="Test scene", voice_script="Test scene voice")
    scene.image_prompt = "female_01 sitting on floor"
    scene.scene_action = "sit_hug_knees"
    scene.emotion_tag = "sad"
    scene.scene_setting = "bedroom"
    request_payload = {
        "character_id": "female_01",
        "action": "sit_hug_knees",
        "emotion": "sad",
        "direction": "front",
        "location": "bedroom",
        "time_of_day": "night",
        "objects": ["pillow", "phone_dark"],
        "composition_preset": "bedroom_floor_sitting",
        "seed": 12345,
    }
    scene.asset_library_request = request_payload
    scenes = [
        Scene(kind="cover", scene_index=0, title="Cover", voice_script="Cover voice"),
        scene,
        Scene(kind="outro", scene_index=2, title="Outro", voice_script="Outro voice"),
    ]
    plan = TellaScenePlan(title="Asset library test", scenes=scenes)
    plan.theme = "practical_life_steps"
    asyncio.run(fetch_assets(plan, job_dir))
    assert scene.image_filenames
    assert scene.asset_status == "asset_library"
    metadata_path = job_dir / "asset_library_scene_metadata.json"
    assert metadata_path.is_file()
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert len(metadata) == 1
    first_scene_metadata = metadata[0]
    character = first_scene_metadata["character"]
    assert character["selected_semantic_id"] == "sit_hug_knees_sad"
    assert character["selected_asset_id"] == "sit_hug_knees_backup"
    assert character["selection_score"] == 150
    assert character["selected_tier"] == "backup"
    assert character["enabled"] is False
    assert character["canonical"] is False
    assert character["production_eligible"] is True
    assert character["quality_status"] == "approved"
    assert character["fallback_reason"] == ""
    assert character["score_breakdown"]["total"] == 150
    assert first_scene_metadata["canvas"]["width"] == 1080
    assert first_scene_metadata["canvas"]["height"] == 1920
    objects = {item["asset_id"]: item["placement"] for item in first_scene_metadata["objects"]}
    assert objects["pillow"]["width"] > 0
    assert objects["pillow"]["height"] > 0
    assert objects["pillow"]["rotation_degrees"] == 0
    assert objects["phone_dark"]["width"] > 0
    assert objects["phone_dark"]["height"] > 0
    assert objects["phone_dark"]["rotation_degrees"] == 10
    scene_png = (job_dir / scene.image_filenames[0]).resolve()
    assert scene_png.is_file()


def test_mode_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TELLA_ASSET_LIBRARY_V2", raising=False)
    from tella.media.fetch import _asset_library_mode_enabled

    assert _asset_library_mode_enabled(None) is False
