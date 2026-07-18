from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from tella.asset_library.production_mvp import (
    BASE_SEED,
    OUTPUT_DIR,
    build_seven_scene_plan,
    scene_seed,
)
from tella.asset_library.semantic_resolver import AssetLibraryRequest, select_semantic_asset
from tella.asset_library.semantic_resolver import build_production_scene_request
from tella.media.fetch import fetch_assets

V2_ROOT = Path(r"D:\tella-assets-staging\mvp_v1_processed_v2")
SEMANTICS_PATH = Path(r"D:\tella-production-resolver\scripts\asset_batch\asset_semantics_patch.json")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_seven_scene_plan_is_real_planner_plan_and_flag_scoped():
    disabled = build_seven_scene_plan(enabled=False)
    assert len(disabled.scenes) == 7
    assert all(not scene.asset_library_request for scene in disabled.scenes)

    enabled = build_seven_scene_plan(base_seed=BASE_SEED, enabled=True)
    assert len(enabled.scenes) == 7
    assert all(scene.character_id == "female_01" for scene in enabled.scenes)
    assert [scene.asset_library_request["seed"] for scene in enabled.scenes] == [
        scene_seed(BASE_SEED, index) for index in range(1, 8)
    ]
    assert len({scene.asset_library_request["seed"] for scene in enabled.scenes}) == 7
    assert all(scene.asset_library_request["base_seed"] == BASE_SEED for scene in enabled.scenes)


def test_seven_scene_requests_resolve_real_backgrounds_objects_and_eligible_characters():
    plan = build_seven_scene_plan(base_seed=BASE_SEED, enabled=True)
    for scene in plan.scenes:
        selected = select_semantic_asset(
            SEMANTICS_PATH,
            V2_ROOT,
            build_production_scene_request(scene),
        )
        assert selected.production_eligible is True
        assert Path(selected.background_path).is_file()
        assert all(Path(path).is_file() for path in selected.object_paths.values())
        assert selected.selected_source_asset_id not in {
            "stand_front_backup", "stand_wave", "stand_hands_clasped",
            "walk_left_backup", "walk_right_backup",
        }


def test_asset_library_fetch_renders_exactly_seven_images_without_ai_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("TELLA_ASSET_LIBRARY_V2", "1")
    monkeypatch.setenv("TELLA_ASSET_LIBRARY_ROOT", str(V2_ROOT))
    monkeypatch.setenv("TELLA_ASSET_LIBRARY_SEMANTICS_PATH", str(SEMANTICS_PATH))
    from tella.media import fetch as fetch_module

    async def fail_external_image(*args, **kwargs):
        raise AssertionError("external AI image provider must not be called")

    monkeypatch.setattr(fetch_module.ai_image, "generate_image", fail_external_image)
    plan = build_seven_scene_plan(base_seed=BASE_SEED, enabled=True)
    asyncio.run(fetch_assets(plan, tmp_path))

    images = [tmp_path / scene.image_filenames[0] for scene in plan.scenes]
    assert len(images) == 7
    assert all(path.is_file() for path in images)
    hashes = [_sha256(path) for path in images]
    assert all(left != right for left, right in zip(hashes, hashes[1:]))
    for path in images:
        with Image.open(path) as image:
            assert image.size == (1080, 1920)
            assert image.mode == "RGB"
    metadata = json.loads((tmp_path / "asset_library_scene_metadata.json").read_text(encoding="utf-8"))
    assert len(metadata) == 7
    assert all(item["character"]["production_eligible"] for item in metadata)
    assert all(
        placement["width"] > 0 and placement["height"] > 0
        for item in metadata
        for placement in [obj["placement"] for obj in item["objects"]]
    )


def test_same_seven_scene_plan_is_byte_deterministic(tmp_path, monkeypatch):
    monkeypatch.setenv("TELLA_ASSET_LIBRARY_V2", "1")
    monkeypatch.setenv("TELLA_ASSET_LIBRARY_ROOT", str(V2_ROOT))
    monkeypatch.setenv("TELLA_ASSET_LIBRARY_SEMANTICS_PATH", str(SEMANTICS_PATH))
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first = build_seven_scene_plan(base_seed=BASE_SEED, enabled=True)
    second = build_seven_scene_plan(base_seed=BASE_SEED, enabled=True)
    asyncio.run(fetch_assets(first, first_dir))
    asyncio.run(fetch_assets(second, second_dir))
    first_hashes = [_sha256(first_dir / scene.image_filenames[0]) for scene in first.scenes]
    second_hashes = [_sha256(second_dir / scene.image_filenames[0]) for scene in second.scenes]
    assert first_hashes == second_hashes
    first_metadata = json.loads((first_dir / "asset_library_scene_metadata.json").read_text(encoding="utf-8"))
    second_metadata = json.loads((second_dir / "asset_library_scene_metadata.json").read_text(encoding="utf-8"))
    for left, right in zip(first_metadata, second_metadata):
        left["output"] = Path(left["output"]).name
        right["output"] = Path(right["output"]).name
    assert first_metadata == second_metadata


def test_optional_object_failure_is_a_structured_warning():
    request = AssetLibraryRequest(
        character_id="female_01",
        action="sit_hug_knees",
        emotion="sad",
        direction="front",
        location="bedroom",
        time_of_day="night",
        objects=["optional_missing_prop"],
        optional_objects=["optional_missing_prop"],
        composition_preset="bedroom_floor_sitting",
        seed=BASE_SEED,
    )
    selected = select_semantic_asset(SEMANTICS_PATH, V2_ROOT, request)
    assert selected.object_paths == {}
    assert selected.object_warnings == [{
        "code": "optional_object_missing",
        "object_id": "optional_missing_prop",
        "message": "Object asset not found: optional_missing_prop",
    }]


def test_generated_acceptance_video_and_job_metadata_are_valid():
    video = OUTPUT_DIR / "video.mp4"
    metadata_path = OUTPUT_DIR / "asset_library_video_metadata.json"
    if not video.is_file() or not metadata_path.is_file():
        pytest.skip("run render_asset_library_v2_7_scene.py for video acceptance")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    properties = metadata["final_video_properties"]
    assert metadata["scene_count"] == 7
    assert len(metadata["scenes"]) == 7
    assert properties["width"] == 1080
    assert properties["height"] == 1920
    assert 30.0 <= properties["duration"] <= 35.0
    assert properties["video_codec"] == "h264"
    assert properties["audio_codec"] == "aac"
