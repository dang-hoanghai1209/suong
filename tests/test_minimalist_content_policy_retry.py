import asyncio
import re

import pytest
from PIL import Image

from tella.media import fetch
from tella.media.ai_image import CloudflareAIError, classify_cloudflare_error
from tella.planner.models import Scene, SceneQCResult, TellaScenePlan


def _clear_fetch_env(monkeypatch):
    for name in (
        "TELLA_ALLOW_LOCAL_IMAGE_FALLBACK",
        "TELLA_REUSE_ASSETS",
        "TELLA_SKIP_IMAGE_GENERATION",
        "TELLA_IMAGES_FROM_JOB",
        "TELLA_REUSE_PLAN_PATH",
        "TELLA_MAX_AI_IMAGES",
        "TELLA_MINIMALIST_VISUAL_MODE",
    ):
        monkeypatch.delenv(name, raising=False)


def _content_policy_error() -> CloudflareAIError:
    return CloudflareAIError(
        'CF AI HTTP 400: {"errors":[{"code":3030,"message":"Input prompt contains NSFW content"}]}',
        error_type="content_policy_blocked",
        status_code=400,
        recoverable=True,
    )


def _bakery_plan(script: str = "Co ay cam hop banh nho trong tay.") -> TellaScenePlan:
    return TellaScenePlan(
        title="Bakery retry",
        language="vi",
        aspect_ratio="9:16",
        media_source="ai_image",
        duration_mode="short",
        theme="minimalist_emotional",
        scenes=[
            Scene(
                scene_index=1,
                kind="scene",
                title="Scene 1",
                voice_script=script,
                image_prompt="warm bedroom, cute young woman with tiny mouth, full body visible",
                stock_query="bakery",
            ),
            Scene(
                scene_index=2,
                kind="scene",
                title="Scene 2",
                voice_script="Co ay binh yen hon.",
                image_prompt="quiet emotional moment",
                stock_query="quiet",
            ),
            Scene(
                scene_index=3,
                kind="scene",
                title="Scene 3",
                voice_script="Co ay mim cuoi nhe.",
                image_prompt="quiet emotional moment",
                stock_query="quiet",
            ),
        ],
    )


def test_cloudflare_3030_classifies_as_content_policy_blocked():
    error_type, recoverable = classify_cloudflare_error(
        400,
        '{"errors":[{"code":3030,"message":"Input prompt contains NSFW content"}]}',
    )

    assert error_type == "content_policy_blocked"
    assert recoverable is True


def test_content_policy_safe_prompt_removes_risky_words():
    scene = Scene(
        scene_index=1,
        kind="scene",
        title="Counter",
        voice_script="Co ay chon mot chiec banh o quay.",
        image_prompt="cute young woman, tiny mouth, full body, bedroom",
        stock_query="bakery",
        scene_setting="bakery_counter",
        scene_action="choosing_cake",
    )

    prompt = fetch._cloudflare_safe_minimalist_prompt(scene).lower()

    for risky in ("young", "cute", "tiny", "mouth", "body", "bedroom"):
        assert re.search(rf"\b{re.escape(risky)}\b", prompt) is None
    assert "adult woman" in prompt
    assert "fully clothed" in prompt
    assert "wholesome everyday scene" in prompt
    assert "bakery display counter" in prompt
    assert "cakes and pastries" in prompt


def test_symbolic_provider_prompt_is_positive_only_and_keeps_scene_semantics():
    scene = Scene(
        scene_index=1,
        kind="scene",
        title="Burden",
        voice_script="A quiet emotional burden.",
        image_prompt=(
            "adult age band, no child, no medical mask, no ghost, no monster, "
            "no blob creature"
        ),
        stock_query="symbolic burden",
        symbolic_visual=(
            "one clearly drawn adult carrying a large cracked stone on their "
            "shoulders, visible facial features, no black silhouette"
        ),
        main_character_or_object="adult carrying a heavy cracked stone",
        cast_archetype="adult_woman_or_man",
    )

    prompt = fetch._cloudflare_safe_symbolic_prompt(scene).lower()

    for risky in (
        "child",
        "medical",
        "mask",
        "ghost",
        "monster",
        "blob",
        "silhouette",
        "mouth",
        "body-part",
    ):
        assert re.search(rf"\b{re.escape(risky)}\b", prompt) is None
    assert "adult carrying a large cracked stone" in prompt
    assert "adult carrying a heavy cracked stone" in prompt
    assert "moderately dark warm taupe" in prompt
    assert "readable symbolic action" in prompt


def test_symbolic_fetch_sends_provider_safe_prompt_and_keeps_full_plan_prompt(
    monkeypatch,
    tmp_path,
):
    _clear_fetch_env(monkeypatch)
    calls: list[str] = []
    full_prompt = (
        "moderately dark symbolic illustration, adult age band, no child, "
        "no medical mask, no ghost, no monster, no blob creature"
    )
    scene = Scene(
        scene_index=1,
        kind="scene",
        title="Burden",
        voice_script="A quiet emotional burden.",
        image_prompt=full_prompt,
        stock_query="symbolic burden",
        scene_meaning="The weight of unspoken sorrow",
        symbolic_visual=(
            "one clearly drawn adult carrying a large cracked stone on their "
            "shoulders, visible facial features, no black silhouette"
        ),
        emotional_metaphor="Emotional baggage",
        main_character_or_object="adult carrying a heavy cracked stone",
        cast_archetype="adult_woman_or_man",
        visual_mode="symbolic_listicle",
    )
    plan = TellaScenePlan(
        title="Symbolic safe provider prompt",
        language="en",
        aspect_ratio="9:16",
        media_source="ai_image",
        duration_mode="short",
        theme="minimalist_symbolic_reel",
        scenes=[
            scene,
            scene.model_copy(
                update={"scene_index": 2, "title": "Burden two"}
            ),
            scene.model_copy(
                update={"scene_index": 3, "title": "Burden three"}
            ),
        ],
    )
    plan.scenes = plan.scenes[:1]

    async def fake_generate_image(prompt, out_path, *, width, height, seed=None):
        calls.append(prompt)
        Image.new("RGB", (width, height), "#504845").save(out_path)
        return out_path

    def fake_evaluate(scene, image_path, visual_bible, expected):
        return SceneQCResult(
            scene_index=scene.scene_index,
            passed=True,
            final_passed=True,
            model_passed=True,
            model_qc_passed=True,
            basic_qc_passed=True,
            symbolic_qc_passed=True,
            symbolic_qc_final_status="passed",
            image_path=str(image_path),
        )

    monkeypatch.setattr(fetch.ai_image, "generate_image", fake_generate_image)
    monkeypatch.setattr(fetch, "evaluate_scene_image", fake_evaluate)
    monkeypatch.setattr(fetch, "save_qc_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(fetch, "max_attempts", lambda: 1)
    monkeypatch.setattr(fetch, "MAX_CONCURRENT", 1)

    asyncio.run(fetch.fetch_assets(plan, tmp_path))

    assert len(calls) == 1
    provider_prompt = calls[0].lower()
    assert scene.image_prompt == full_prompt
    assert scene.prompt_used == calls[0]
    assert scene.sanitized_prompt_used == calls[0]
    assert scene.original_prompt_summary
    assert scene.sanitized_prompt_summary
    assert "adult carrying a large cracked stone" in provider_prompt
    for risky in ("child", "medical", "mask", "ghost", "monster", "blob", "silhouette"):
        assert re.search(rf"\b{risky}\b", provider_prompt) is None


def test_bakery_safe_retry_prompts_include_scene_specific_terms():
    cases = [
        ("street_sidewalk", "walking_outside", ("adult woman", "fully clothed", "sidewalk")),
        ("bakery_exterior", "noticing_bakery", ("bakery storefront", "warm lights")),
        ("bakery_entrance", "entering_shop", ("bakery doorway", "shop interior")),
        ("bakery_counter", "choosing_cake", ("display counter", "cakes and pastries")),
        ("bakery_interior", "holding_cake", ("paper bakery bag", "pastry box")),
        ("exit_street", "leaving_shop", ("walking out of the bakery", "paper bakery bag")),
    ]

    for setting, action, expected_terms in cases:
        scene = Scene(
            scene_index=1,
            kind="scene",
            title="Scene",
            voice_script="Bakery scene.",
            image_prompt="ignored risky prompt with young cute tiny mouth body bedroom",
            stock_query="bakery",
            scene_setting=setting,
            scene_action=action,
        )
        prompt = fetch._cloudflare_safe_minimalist_prompt(scene).lower()
        assert "adult woman" in prompt
        assert "fully clothed" in prompt
        assert "hand-drawn cartoon illustration" in prompt
        assert "no text" in prompt
        assert "no watermark" in prompt
        for term in expected_terms:
            assert term in prompt


def test_content_policy_retry_success_does_not_use_local_fallback(monkeypatch, tmp_path):
    _clear_fetch_env(monkeypatch)
    calls: list[str] = []

    async def fake_generate_image(prompt, out_path, *, width, height, seed=None):
        calls.append(prompt)
        if len(calls) == 1:
            raise _content_policy_error()
        Image.new("RGB", (width, height), "#dcc8aa").save(out_path)
        return out_path

    def fail_local_composer(*args, **kwargs):
        raise AssertionError("local composer must not run after safe retry succeeds")

    monkeypatch.setattr(fetch.ai_image, "generate_image", fake_generate_image)
    monkeypatch.setattr(fetch.sprite_composer, "compose_scene", fail_local_composer)
    monkeypatch.setattr(fetch, "MAX_CONCURRENT", 1)
    plan = _bakery_plan()
    plan.scenes = plan.scenes[:1]

    asyncio.run(fetch.fetch_assets(plan, tmp_path))

    first = plan.scenes[0]
    assert first.nsfw_retry_attempted is True
    assert first.nsfw_retry_succeeded is True
    assert first.ai_provider_error_type == "content_policy_blocked"
    assert first.ai_provider_recoverable is True
    assert first.content_policy_blocked_count == 1
    assert first.used_local_fallback is False
    assert first.asset_status == "sanitized_retry"
    assert "adult woman" in first.sanitized_prompt_used.lower()
    assert "body" not in first.sanitized_prompt_used.lower()
    assert plan.used_local_fallback is False


def test_content_policy_retry_failure_aborts_without_local_fallback(monkeypatch, tmp_path):
    _clear_fetch_env(monkeypatch)

    async def fake_generate_image(*args, **kwargs):
        raise _content_policy_error()

    def fail_local_composer(*args, **kwargs):
        raise AssertionError("local composer must not run when fallback is disabled")

    monkeypatch.setattr(fetch.ai_image, "generate_image", fake_generate_image)
    monkeypatch.setattr(fetch.sprite_composer, "compose_scene", fail_local_composer)
    monkeypatch.setattr(fetch, "MAX_CONCURRENT", 1)
    plan = _bakery_plan()
    plan.scenes = plan.scenes[:1]

    with pytest.raises(RuntimeError, match="content policy blocked"):
        asyncio.run(fetch.fetch_assets(plan, tmp_path))

    first = plan.scenes[0]
    assert first.nsfw_retry_attempted is True
    assert first.nsfw_retry_succeeded is False
    assert first.original_prompt_hash
    assert first.sanitized_prompt_hash
    assert first.original_prompt_summary
    assert first.sanitized_prompt_summary
    assert first.content_policy_blocked_count == 2
    assert plan.content_policy_blocked_count == 2
