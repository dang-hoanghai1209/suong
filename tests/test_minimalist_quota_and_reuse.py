import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from tella.media import fetch
from tella.media.ai_image import CloudflareAIError
from tella.planner.models import Scene, TellaScenePlan


def _plan() -> TellaScenePlan:
    return TellaScenePlan(
        title="Quota smoke",
        language="vi",
        aspect_ratio="9:16",
        media_source="ai_image",
        duration_mode="short",
        theme="minimalist_emotional",
        scenes=[
            Scene(
                scene_index=1,
                kind="scene",
                title="Một",
                voice_script="Cô ấy ngồi yên bên cửa sổ.",
                image_prompt="quiet bedroom memory",
                stock_query="quiet bedroom",
            ),
            Scene(
                scene_index=2,
                kind="scene",
                title="Hai",
                voice_script="Ánh đèn nhỏ làm căn phòng dịu lại.",
                image_prompt="warm lamp in bedroom",
                stock_query="warm lamp",
            ),
            Scene(
                scene_index=3,
                kind="scene",
                title="Ba",
                voice_script="Cô ấy học cách bình yên hơn.",
                image_prompt="small flower near bed",
                stock_query="small flower",
            ),
        ],
    )


def _quota_error() -> CloudflareAIError:
    return CloudflareAIError(
        'CF AI HTTP 429: {"message":"you have used up your daily free allocation of 10,000 neurons"}',
        error_type="quota_exhausted",
        status_code=429,
        recoverable=False,
    )


def _clear_fetch_env(monkeypatch):
    for name in (
        "TELLA_ALLOW_LOCAL_IMAGE_FALLBACK",
        "TELLA_REUSE_ASSETS",
        "TELLA_REUSE_ASSETS_MODE",
        "TELLA_ALLOW_MISMATCHED_REUSED_ASSETS",
        "TELLA_SKIP_IMAGE_GENERATION",
        "TELLA_IMAGES_FROM_JOB",
        "TELLA_REUSE_PLAN_PATH",
        "TELLA_MAX_AI_IMAGES",
        "TELLA_MINIMALIST_VISUAL_MODE",
    ):
        monkeypatch.delenv(name, raising=False)


def test_cloudflare_429_does_not_call_local_composer_by_default(monkeypatch, tmp_path):
    _clear_fetch_env(monkeypatch)

    async def fake_generate_image(*args, **kwargs):
        raise _quota_error()

    def fail_local_composer(*args, **kwargs):
        raise AssertionError("local composer must not run by default on quota")

    monkeypatch.setattr(fetch.ai_image, "generate_image", fake_generate_image)
    monkeypatch.setattr(fetch.sprite_composer, "compose_scene", fail_local_composer)
    plan = _plan()

    with pytest.raises(RuntimeError, match="Cloudflare AI quota exhausted"):
        asyncio.run(fetch.fetch_assets(plan, tmp_path))

    assert plan.ai_provider_error_type == "quota_exhausted"
    assert plan.ai_provider_recoverable is False
    assert plan.local_fallback_allowed is False
    assert plan.used_local_fallback is False
    assert any(s.asset_status == "ai_provider_quota_exhausted" for s in plan.scenes)


def test_local_composer_runs_only_when_local_fallback_allowed(monkeypatch, tmp_path):
    _clear_fetch_env(monkeypatch)
    monkeypatch.setenv("TELLA_ALLOW_LOCAL_IMAGE_FALLBACK", "1")

    async def fake_generate_image(*args, **kwargs):
        raise _quota_error()

    def fake_compose_scene(scene, out_path, width, height, state):
        Image.new("RGB", (width, height), "#e7d6c0").save(out_path)
        return SimpleNamespace(asset_hash="localhash")

    monkeypatch.setattr(fetch.ai_image, "generate_image", fake_generate_image)
    monkeypatch.setattr(fetch.sprite_composer, "compose_scene", fake_compose_scene)
    plan = _plan()

    asyncio.run(fetch.fetch_assets(plan, tmp_path))

    assert plan.local_fallback_allowed is True
    assert plan.used_local_fallback is True
    assert all(scene.used_local_fallback for scene in plan.scenes)
    assert all(scene.image_provider == "local_composer" for scene in plan.scenes)


def test_reuse_assets_does_not_call_cloudflare_when_hash_matches(monkeypatch, tmp_path):
    _clear_fetch_env(monkeypatch)
    source_job = tmp_path / "source"
    current_job = tmp_path / "current"
    source_assets = source_job / "assets"
    source_assets.mkdir(parents=True)

    source_plan = _plan()
    fetch._prepare_minimalist_image_prompts(source_plan.scenes)
    width, height = fetch._GEN_DIMS["9:16"]
    scene_records = []
    for scene in source_plan.scenes:
        prompt = fetch._minimalist_provider_prompt(scene)
        prompt_hash = fetch._asset_prompt_hash(
            prompt,
            width=width,
            height=height,
            seed=fetch._seed_for_scene(source_plan, scene),
        )
        rel = f"assets/source_scene_{scene.scene_index}.jpg"
        Image.new("RGB", (width, height), "#dac6a8").save(source_job / rel)
        scene_records.append(
            {
                "scene_index": scene.scene_index,
                "kind": "scene",
                "asset_prompt_hash": prompt_hash,
                "asset_path": rel,
                "image_source": "ai_image_provider",
                "image_provider": "cloudflare",
                "used_local_fallback": False,
                "asset_status": "done",
            }
        )
    (source_job / "plan.json").write_text(
        json.dumps({"scenes": scene_records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    async def fail_generate_image(*args, **kwargs):
        raise AssertionError("Cloudflare must not be called for matching reusable assets")

    monkeypatch.setattr(fetch.ai_image, "generate_image", fail_generate_image)
    monkeypatch.setenv("TELLA_REUSE_ASSETS", "1")
    monkeypatch.setenv("TELLA_IMAGES_FROM_JOB", str(source_job))
    plan = _plan()

    asyncio.run(fetch.fetch_assets(plan, current_job))

    assert plan.ai_images_requested == 0
    assert plan.ai_images_generated == 0
    assert plan.ai_images_reused == 3
    assert all(scene.reused_asset for scene in plan.scenes)
    assert all(scene.asset_status == "reused_asset" for scene in plan.scenes)
    assert all((current_job / scene.asset_path).is_file() for scene in plan.scenes)


def _write_source_reuse_job(source_job: Path, scene_count: int = 3) -> None:
    source_assets = source_job / "assets"
    source_assets.mkdir(parents=True)
    scene_records = []
    for idx in range(1, scene_count + 1):
        rel = f"assets/source_scene_{idx}.jpg"
        Image.new("RGB", fetch._GEN_DIMS["9:16"], "#dac6a8").save(source_job / rel)
        scene_records.append(
            {
                "scene_index": idx,
                "kind": "scene",
                "asset_prompt_hash": f"old_hash_{idx}",
                "asset_path": rel,
                "image_source": "ai_image_provider",
                "image_provider": "cloudflare",
                "used_local_fallback": False,
                "asset_status": "done",
            }
        )
    (source_job / "plan.json").write_text(
        json.dumps({"scenes": scene_records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def test_strict_reuse_rejects_mismatched_prompt_hashes(monkeypatch, tmp_path):
    _clear_fetch_env(monkeypatch)
    source_job = tmp_path / "source_mismatch"
    current_job = tmp_path / "current_strict"
    _write_source_reuse_job(source_job)

    async def fail_generate_image(*args, **kwargs):
        raise AssertionError("Cloudflare must not be called with skip-image-generation")

    monkeypatch.setattr(fetch.ai_image, "generate_image", fail_generate_image)
    monkeypatch.setenv("TELLA_REUSE_ASSETS", "1")
    monkeypatch.setenv("TELLA_SKIP_IMAGE_GENERATION", "1")
    monkeypatch.setenv("TELLA_IMAGES_FROM_JOB", str(source_job))
    plan = _plan()

    with pytest.raises(RuntimeError, match="Image generation skipped"):
        asyncio.run(fetch.fetch_assets(plan, current_job))

    assert plan.ai_images_requested == 0
    assert plan.ai_images_generated == 0
    assert plan.ai_images_reused == 0
    assert all(not scene.reused_asset for scene in plan.scenes)


def test_loose_debug_reuses_assets_by_scene_index(monkeypatch, tmp_path):
    _clear_fetch_env(monkeypatch)
    source_job = tmp_path / "source_loose"
    current_job = tmp_path / "current_loose"
    _write_source_reuse_job(source_job)

    async def fail_generate_image(*args, **kwargs):
        raise AssertionError("Cloudflare must not be called in loose debug reuse")

    monkeypatch.setattr(fetch.ai_image, "generate_image", fail_generate_image)
    monkeypatch.setenv("TELLA_REUSE_ASSETS", "1")
    monkeypatch.setenv("TELLA_REUSE_ASSETS_MODE", "loose")
    monkeypatch.setenv("TELLA_SKIP_IMAGE_GENERATION", "1")
    monkeypatch.setenv("TELLA_IMAGES_FROM_JOB", str(source_job))
    plan = _plan()

    asyncio.run(fetch.fetch_assets(plan, current_job))

    assert plan.reused_asset is True
    assert plan.reuse_mode == "loose_debug"
    assert plan.reused_from_job_id == source_job.name
    assert plan.reused_asset_prompt_hash_mismatch is True
    assert plan.ai_images_requested == 0
    assert plan.ai_images_generated == 0
    assert plan.ai_images_reused == 3
    for scene in plan.scenes:
        assert scene.reused_asset is True
        assert scene.reuse_mode == "loose_debug"
        assert scene.reused_from_job_id == source_job.name
        assert scene.reused_asset_prompt_hash_mismatch is True
        assert scene.asset_status == "reused_asset"
        assert (current_job / scene.asset_path).is_file()


def test_loose_debug_aborts_when_source_job_has_too_few_images(monkeypatch, tmp_path):
    _clear_fetch_env(monkeypatch)
    source_job = tmp_path / "source_short"
    current_job = tmp_path / "current_short"
    _write_source_reuse_job(source_job, scene_count=2)

    async def fail_generate_image(*args, **kwargs):
        raise AssertionError("Cloudflare must not be called when loose reuse validation fails")

    monkeypatch.setattr(fetch.ai_image, "generate_image", fail_generate_image)
    monkeypatch.setenv("TELLA_REUSE_ASSETS", "1")
    monkeypatch.setenv("TELLA_REUSE_ASSETS_MODE", "loose")
    monkeypatch.setenv("TELLA_SKIP_IMAGE_GENERATION", "1")
    monkeypatch.setenv("TELLA_IMAGES_FROM_JOB", str(source_job))
    plan = _plan()

    with pytest.raises(RuntimeError, match="fewer usable images"):
        asyncio.run(fetch.fetch_assets(plan, current_job))

    assert plan.ai_images_requested == 0
    assert plan.ai_images_generated == 0
