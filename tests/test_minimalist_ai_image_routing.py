import asyncio

from PIL import Image

from tella.media import fetch
from tella.planner.models import Scene, TellaScenePlan


def test_minimalist_ai_image_defaults_to_ai_provider_not_local_composer(monkeypatch, tmp_path):
    monkeypatch.delenv("TELLA_MINIMALIST_VISUAL_MODE", raising=False)
    monkeypatch.delenv("TELLA_MINIMALIST_USE_AI_SCENES", raising=False)
    prompts: list[str] = []

    async def fake_generate_image(prompt, out_path, *, width, height, seed=None):
        prompts.append(prompt)
        assert len(prompt) <= 2048
        Image.new("RGB", (width, height), "#e7d6c0").save(out_path)
        return out_path

    def fail_local_composer(*args, **kwargs):
        raise AssertionError("local composer must not be primary for minimalist ai_image")

    monkeypatch.setattr(fetch.ai_image, "generate_image", fake_generate_image)
    monkeypatch.setattr(fetch.sprite_composer, "compose_scene", fail_local_composer)

    plan = TellaScenePlan(
        title="Smoke",
        language="vi",
        aspect_ratio="9:16",
        media_source="ai_image",
        duration_mode="short",
        theme="minimalist_emotional",
        scenes=[
            Scene(
                scene_index=1,
                kind="scene",
                title="Quiet",
                voice_script="Cô ấy học cách thương chính mình.",
                image_prompt="warm minimalist emotional bedroom illustration",
                stock_query="quiet bedroom",
            ),
            Scene(
                scene_index=2,
                kind="scene",
                title="Window",
                voice_script="Căn phòng dịu lại dưới ánh đèn nhỏ.",
                image_prompt="warm minimalist emotional bedroom illustration",
                stock_query="quiet window",
            ),
            Scene(
                scene_index=3,
                kind="scene",
                title="Rest",
                voice_script="Cô ấy thở chậm và bình yên hơn.",
                image_prompt="warm minimalist emotional bedroom illustration",
                stock_query="quiet rest",
            ),
        ],
    )

    asyncio.run(fetch.fetch_assets(plan, tmp_path))

    for scene in plan.scenes:
        assert scene.image_source == "ai_image_provider"
        assert scene.image_provider == "cloudflare"
        assert scene.used_local_fallback is False
        assert scene.asset_status == "done"
        assert scene.asset_path == scene.image_filenames[0]
        assert (tmp_path / scene.asset_path).is_file()
    assert prompts
    joined_prompts = "\n".join(prompts).lower()
    assert "small girl" not in joined_prompts
    assert "triangular dress" not in joined_prompts
    assert "stick-like" not in joined_prompts
