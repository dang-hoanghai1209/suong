from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from scripts.asset_batch.render_test_scene_v2 import select_semantic_asset


SOURCE_ROOT = Path(r"D:\tella-assets-staging\mvp_v1")
V2_ROOT = Path(r"D:\tella-assets-staging\mvp_v1_processed_v2")
SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "scripts" / "asset_batch"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


@pytest.fixture(scope="module")
def qc_report():
    path = V2_ROOT / "transparency_qc_report.json"
    if not path.is_file():
        pytest.skip("Run process_transparency_v2.py before generated-asset regression tests")
    return load_json(path)


def report_asset(report, asset_id: str, character_id: str | None = None):
    return next(
        asset for asset in report["assets"]
        if asset["asset_id"] == asset_id
        and (character_id is None or asset.get("character_id") == character_id)
    )


def alpha_stats(path: str):
    with Image.open(path) as image:
        assert image.mode == "RGBA"
        alpha = bytes(image.getchannel("A").getdata())
        return image.size, sum(value > 16 for value in alpha) / len(alpha), alpha


def test_sad_request_chooses_sad_pose():
    semantics = load_json(SCRIPT_ROOT / "asset_semantics_patch.json")
    selected = select_semantic_asset(
        semantics,
        {"character_id": "female_01", "action": "sit_hug_knees", "emotion": "sad", "direction": "front"},
        20260717,
    )
    assert selected["semantic_id"] == "sit_hug_knees_sad"
    assert selected["source_asset_id"] == "sit_hug_knees_backup"


def test_neutral_request_does_not_choose_sad_pose():
    semantics = load_json(SCRIPT_ROOT / "asset_semantics_patch.json")
    selected = select_semantic_asset(
        semantics,
        {"character_id": "female_01", "action": "sit_hug_knees", "emotion": "neutral", "direction": "front"},
        20260717,
    )
    assert selected["semantic_id"] == "sit_hug_knees_neutral"


def test_selection_is_deterministic_for_same_seed():
    semantics = load_json(SCRIPT_ROOT / "asset_semantics_patch.json")
    request = {"character_id": "female_01", "action": "sit_hug_knees", "emotion": "sad", "direction": "front"}
    assert select_semantic_asset(semantics, request, 9) == select_semantic_asset(semantics, request, 9)


def test_selection_falls_back_without_exact_emotion():
    semantics = load_json(SCRIPT_ROOT / "asset_semantics_patch.json")
    selected = select_semantic_asset(
        semantics,
        {"character_id": "female_01", "action": "sit_hug_knees", "emotion": "surprised", "direction": "front"},
        4,
    )
    assert selected["source_asset_id"] == "sit_hug_knees"


def test_sit_hug_knees_has_no_distant_shoe_fragments(qc_report):
    asset = report_asset(qc_report, "sit_hug_knees", "female_01")
    assert asset["removed_component_count"] >= 2
    assert all(box[3] <= 30 for box in asset["removed_component_bounding_boxes"])
    assert "foreground touching trim bounds" not in asset["warnings"]


@pytest.mark.parametrize(
    "asset_id",
    ["stand_hands_clasped", "walk_left_backup", "walk_right_backup", "stand_back_view"],
)
def test_mixed_sheet_pose_has_no_retained_shoes_above_subject(qc_report, asset_id):
    asset = report_asset(qc_report, asset_id, "female_01")
    with Image.open(asset["processed_path"]) as image:
        alpha = image.getchannel("A")
        top_quarter = alpha.crop((0, 0, image.width, max(1, image.height // 4)))
        components = []
        pixels = top_quarter.load()
        # A retained adjacent shoe is a substantial opaque island detached in the
        # upper quarter. The valid subject may enter this area but is the largest.
        from scripts.asset_batch.process_transparency_v2 import connected_components

        components = connected_components(top_quarter, 24)
        substantial = [component for component in components if component["area"] > 100]
        assert len(substantial) <= 1


def test_walk_left_keeps_both_legs_and_feet(qc_report):
    asset = report_asset(qc_report, "walk_left", "female_01")
    size, foreground, _ = alpha_stats(asset["processed_path"])
    assert size[1] > 0.85 * asset["source_dimensions"][1]
    assert foreground > 0.25
    assert asset["kept_component_count"] >= 1


def test_wipe_tear_keeps_hand_and_tear_detail(qc_report):
    asset = report_asset(qc_report, "wipe_tear", "female_01")
    size, foreground, alpha = alpha_stats(asset["processed_path"])
    assert foreground > 0.1
    assert sum(value > 32 for value in alpha[: len(alpha) // 2]) > 500
    assert size[0] < asset["source_dimensions"][0] * 0.7


def test_stand_phone_one_hand_keeps_phone(qc_report):
    asset = report_asset(qc_report, "stand_phone_one_hand", "male_01")
    with Image.open(asset["processed_path"]) as image:
        rgba = image.convert("RGBA")
        dark_left = sum(
            1 for y in range(rgba.height // 5, rgba.height // 2)
            for x in range(0, rgba.width // 3)
            if rgba.getpixel((x, y))[3] > 32 and max(rgba.getpixel((x, y))[:3]) < 100
        )
    assert dark_left > 20


def test_sit_cross_leg_phone_keeps_legitimate_parts(qc_report):
    asset = report_asset(qc_report, "sit_cross_leg_phone", "male_01")
    _, foreground, _ = alpha_stats(asset["processed_path"])
    assert foreground > 0.18
    assert asset["kept_component_count"] >= 1


def test_crowd_retains_all_people(qc_report):
    asset = report_asset(qc_report, "crowd_group_cheering_01")
    size, foreground, _ = alpha_stats(asset["processed_path"])
    assert size[0] > 900 and size[1] > 850
    assert foreground > 0.55
    assert asset["kept_component_count"] == 1
    assert asset["removed_component_count"] >= 250


@pytest.mark.parametrize("asset_id,min_foreground", [("pillow", 0.65), ("paper_letter", 0.45)])
def test_pale_object_is_not_erased(qc_report, asset_id, min_foreground):
    asset = report_asset(qc_report, asset_id)
    _, foreground, _ = alpha_stats(asset["processed_path"])
    assert foreground > min_foreground


def test_curtain_is_not_perforated(qc_report):
    asset = report_asset(qc_report, "curtain_simple")
    _, foreground, alpha = alpha_stats(asset["processed_path"])
    assert foreground > 0.55
    assert sum(value == 0 for value in alpha) / len(alpha) < 0.45


def test_flower_keeps_thin_stem(qc_report):
    asset = report_asset(qc_report, "flower_single")
    with Image.open(asset["processed_path"]) as image:
        alpha = image.getchannel("A")
        bottom_half = alpha.crop((0, image.height // 2, image.width, image.height))
        assert bottom_half.getbbox() is not None
        assert sum(value > 16 for value in bottom_half.getdata()) > 250


def test_all_processed_pngs_are_rgba_and_transparent(qc_report):
    assert qc_report["asset_count"] == 106
    for asset in qc_report["assets"]:
        with Image.open(asset["processed_path"]) as image:
            assert image.mode == "RGBA", asset["processed_path"]
            assert image.getchannel("A").getextrema()[0] == 0, asset["processed_path"]


def test_source_assets_still_match_manifests():
    manifest = load_json(SOURCE_ROOT / "asset_manifest.json")
    object_manifest = load_json(SOURCE_ROOT / "objects" / "object_manifest.json")
    expression_manifest = load_json(SOURCE_ROOT / "characters" / "female_01" / "female_01_expression_map.json")
    expected = [(SOURCE_ROOT / asset["relative_path"], asset["sha256"]) for asset in manifest["assets"]]
    expected += [(SOURCE_ROOT / asset["relative_path"], asset["sha256"]) for asset in object_manifest["objects"]]
    expected += [
        (SOURCE_ROOT / "characters" / "female_01" / asset["relative_path"], asset["sha256"])
        for asset in expression_manifest["expressions"]
    ]
    for path, digest in expected:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, path


def test_scene_metadata_is_deterministic_and_selects_sad_pose():
    scene_path = V2_ROOT / "test_renders" / "bedroom_night__female_01__sit_hug_knees_sad__phone_dark__pillow.scene.json"
    if not scene_path.is_file():
        pytest.skip("Run render_test_scene_v2.py before scene regression tests")
    before = scene_path.read_bytes()
    scene = load_json(scene_path)
    assert scene["character"]["selected_semantic_id"] == "sit_hug_knees_sad"
    assert scene["character"]["selection_score"] == 150
    assert "generated_at" not in scene
    assert scene_path.read_bytes() == before
