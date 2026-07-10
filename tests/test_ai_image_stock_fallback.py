import asyncio
import re
from types import SimpleNamespace

import pytest
from PIL import Image

from tella.media import fetch
from tella.media.ai_image import CloudflareAIError
from tella.planner.models import Scene, TellaScenePlan


_PROVIDER_ERROR = "cloudflare provider failed: original-error-marker"


def _clear_fetch_env(monkeypatch) -> None:
    for name in (
        "TELLA_ALLOW_LOCAL_IMAGE_FALLBACK",
        "TELLA_DISABLE_STOCK_FALLBACK",
        "TELLA_IMAGES_FROM_JOB",
        "TELLA_MAX_AI_IMAGES",
        "TELLA_MINIMALIST_VISUAL_MODE",
        "TELLA_REUSE_ASSETS",
        "TELLA_REUSE_PLAN_PATH",
        "TELLA_SKIP_IMAGE_GENERATION",
    ):
        monkeypatch.delenv(name, raising=False)


def _plan(theme: str) -> TellaScenePlan:
    scenes = []
    for index in range(1, 4):
        scene_fields = {}
        if theme == "minimalist_symbolic_reel":
            scene_fields = {
                "scene_meaning": f"quiet feeling {index}",
                "symbolic_visual": f"small paper heart {index}",
                "emotional_metaphor": f"soft metaphor {index}",
                "main_character_or_object": "small paper heart",
            }
        scenes.append(
            Scene(
                scene_index=index,
                kind="scene",
                title=f"Scene {index}",
                voice_script=f"Narration {index}.",
                image_prompt=f"hand-drawn illustration {index}",
                stock_query="quiet street",
                **scene_fields,
            )
        )
    return TellaScenePlan(
        title="Fallback routing test",
        language="en",
        aspect_ratio="9:16",
        media_source="ai_image",
        duration_mode="short",
        theme=theme,
        scenes=scenes,
    )


def _install_provider_failure(monkeypatch) -> None:
    async def fail_generate_image(*args, **kwargs):
        raise RuntimeError(_PROVIDER_ERROR)

    monkeypatch.setattr(fetch.ai_image, "generate_image", fail_generate_image)
    monkeypatch.setattr(fetch, "MAX_CONCURRENT", 1)


def test_symbolic_reel_provider_failure_does_not_call_stock_photo(
    monkeypatch, tmp_path
):
    _clear_fetch_env(monkeypatch)
    _install_provider_failure(monkeypatch)
    stock_calls = []

    async def fail_stock(*args, **kwargs):
        stock_calls.append((args, kwargs))
        raise AssertionError("Pexels must not run for a symbolic AI image failure")

    monkeypatch.setattr(fetch.stock_photo, "search_and_download", fail_stock)
    plan = _plan("minimalist_symbolic_reel")

    with pytest.raises(RuntimeError, match=re.escape(_PROVIDER_ERROR)):
        asyncio.run(fetch.fetch_assets(plan, tmp_path))

    assert stock_calls == []
    assert plan.used_local_fallback is False


def test_minimalist_emotional_provider_failure_does_not_call_stock_photo(
    monkeypatch, tmp_path
):
    _clear_fetch_env(monkeypatch)
    monkeypatch.setenv("TELLA_MINIMALIST_VISUAL_MODE", "ai_scene")
    _install_provider_failure(monkeypatch)
    stock_calls = []

    async def fail_stock(*args, **kwargs):
        stock_calls.append((args, kwargs))
        raise AssertionError("Pexels must not run for an emotional AI image failure")

    monkeypatch.setattr(fetch.stock_photo, "search_and_download", fail_stock)
    plan = _plan("minimalist_emotional")

    with pytest.raises(RuntimeError, match=re.escape(_PROVIDER_ERROR)):
        asyncio.run(fetch.fetch_assets(plan, tmp_path))

    assert stock_calls == []
    assert plan.used_local_fallback is False


def test_minimalist_error_preserves_original_provider_error(monkeypatch, tmp_path):
    _clear_fetch_env(monkeypatch)
    original_error = CloudflareAIError(
        "CF quota exhausted: original-quota-marker",
        error_type="quota_exhausted",
        status_code=429,
        recoverable=False,
    )

    async def fail_generate_image(*args, **kwargs):
        raise original_error

    async def fail_stock(*args, **kwargs):
        raise AssertionError("Pexels must not replace the provider error")

    monkeypatch.setattr(fetch.ai_image, "generate_image", fail_generate_image)
    monkeypatch.setattr(fetch.stock_photo, "search_and_download", fail_stock)
    monkeypatch.setattr(fetch, "MAX_CONCURRENT", 1)
    plan = _plan("minimalist_symbolic_reel")

    with pytest.raises(RuntimeError, match="original-quota-marker") as exc_info:
        asyncio.run(fetch.fetch_assets(plan, tmp_path))

    assert exc_info.value.__cause__ is original_error
    assert plan.ai_provider_error_message == str(original_error)


def test_symbolic_reel_explicit_local_fallback_still_works(monkeypatch, tmp_path):
    _clear_fetch_env(monkeypatch)
    monkeypatch.setenv("TELLA_ALLOW_LOCAL_IMAGE_FALLBACK", "1")
    _install_provider_failure(monkeypatch)
    stock_calls = []

    async def fail_stock(*args, **kwargs):
        stock_calls.append((args, kwargs))
        raise AssertionError("Pexels must not run when local fallback is explicit")

    def fake_compose_scene(scene, out_path, width, height, state):
        Image.new("RGB", (16, 16), "#ddd0bc").save(out_path)
        return SimpleNamespace(asset_hash=f"local-{scene.scene_index}")

    monkeypatch.setattr(fetch.stock_photo, "search_and_download", fail_stock)
    monkeypatch.setattr(fetch.sprite_composer, "compose_scene", fake_compose_scene)
    plan = _plan("minimalist_symbolic_reel")

    asyncio.run(fetch.fetch_assets(plan, tmp_path))

    assert stock_calls == []
    assert plan.local_fallback_allowed is True
    assert plan.used_local_fallback is True
    assert all(scene.used_local_fallback for scene in plan.scenes)
    assert all(scene.image_provider == "local_composer" for scene in plan.scenes)


def test_non_minimalist_ai_failure_keeps_stock_fallback(monkeypatch, tmp_path):
    _clear_fetch_env(monkeypatch)
    _install_provider_failure(monkeypatch)
    stock_calls = []

    async def fake_stock(query, out_path, *, width, height):
        stock_calls.append(query)
        Image.new("RGB", (16, 16), "#ccd2d8").save(out_path)
        return out_path

    monkeypatch.setattr(fetch.stock_photo, "search_and_download", fake_stock)
    plan = _plan("cinematic")

    asyncio.run(fetch.fetch_assets(plan, tmp_path))

    assert len(stock_calls) == len(plan.scenes)
    assert all(scene.image_source == "fallback" for scene in plan.scenes)
    assert all(scene.image_provider == "pexels" for scene in plan.scenes)
    assert all((tmp_path / scene.asset_path).is_file() for scene in plan.scenes)
