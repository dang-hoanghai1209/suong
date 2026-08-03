from __future__ import annotations

import asyncio
from pathlib import Path

from PIL import Image
import pytest

from tella.media import fetch
from tella.media.ai_image import CloudflareAIError
from tella.planner.models import Scene, TellaScenePlan
from tella.topic_production.strategy import SceneDataSensitivity
from tella import web_image_fallback as fallback


def _plan(
    *,
    source_text: str = "A patient gardener shares a quiet lesson",
    aspect_ratio: str = "9:16",
) -> TellaScenePlan:
    plan = TellaScenePlan(
        title="Public editorial scenes",
        language="en",
        aspect_ratio=aspect_ratio,
        media_source="ai_image",
        duration_mode="short",
        theme="cinematic",
        scenes=[
            Scene(
                scene_index=index,
                voice_script=f"A calm public lesson number {index}.",
                image_prompt=f"Anonymous adult woman in a garden scene {index}",
                scene_meaning="Patience supports steady progress",
                visual_action="watering a small plant",
                visual_environment="quiet public garden",
            )
            for index in range(1, 4)
        ],
    )
    fallback.apply_web_scene_sensitivity_authority(
        plan,
        source_text=source_text,
        policy_id=fallback.WEB_SENSITIVITY_POLICY_ID,
    )
    return plan


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        fallback.WEB_SENSITIVITY_POLICY_ENV,
        fallback.WEB_SENSITIVITY_POLICY_ID,
    )
    monkeypatch.setenv(fallback.WEB_POLLINATIONS_FALLBACK_ENV, "1")
    monkeypatch.setenv("POLLINATIONS_API_KEY", "test-only-pollinations-key")
    monkeypatch.setenv("TELLA_DISABLE_STOCK_FALLBACK", "1")
    monkeypatch.setenv("TELLA_ALLOW_LOCAL_IMAGE_FALLBACK", "0")
    monkeypatch.setenv("TELLA_AI_IMAGE_SEQUENTIAL", "1")


def _cloudflare_failure(error_type: str) -> CloudflareAIError:
    return CloudflareAIError(
        "sanitized controlled Cloudflare failure",
        error_type=error_type,
        recoverable=False,
    )


@pytest.mark.parametrize(
    "error_type",
    ["rate_limited", "quota_exhausted", "provider_unavailable", "timeout"],
)
def test_public_safe_eligible_failure_calls_pollinations_once_per_scene(
    error_type: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    calls = {"cloudflare": 0, "pollinations": 0}

    async def fail_cloudflare(*_args: object, **_kwargs: object) -> None:
        calls["cloudflare"] += 1
        raise _cloudflare_failure(error_type)

    async def pollinations(**kwargs: object) -> dict[str, object]:
        calls["pollinations"] += 1
        output = Path(kwargs["output_path"])
        width, height = int(kwargs["width"]), int(kwargs["height"])
        Image.new("RGB", (width, height), "#315b71").save(output)
        return {
            "provider": "pollinations",
            "request_sha256": "a" * 64,
            "width": width,
            "height": height,
            "bytes": output.stat().st_size,
            "sha256": "b" * 64,
            "mime_type": "image/jpeg",
        }

    monkeypatch.setattr(fetch.ai_image, "generate_image", fail_cloudflare)
    monkeypatch.setattr(fetch, "generate_web_pollinations_fallback", pollinations)
    plan = _plan()

    asyncio.run(fetch.fetch_assets(plan, tmp_path))

    assert calls == {"cloudflare": 3, "pollinations": 3}
    assert plan.pollinations_fallback_used is True
    assert plan.pollinations_fallback_attempt_count == 3
    assert plan.resolved_image_providers == ["pollinations"]
    assert all(scene.data_sensitivity == "public_safe" for scene in plan.scenes)
    assert all(scene.image_fallback_used for scene in plan.scenes)
    assert all(scene.resolved_image_provider == "pollinations" for scene in plan.scenes)
    assert all(scene.generated_image_width == 768 for scene in plan.scenes)
    assert all(scene.generated_image_height == 1344 for scene in plan.scenes)


def test_cloudflare_success_never_calls_pollinations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    calls = {"cloudflare": 0, "pollinations": 0}

    async def cloudflare(_prompt: str, output: Path, **kwargs: object) -> Path:
        calls["cloudflare"] += 1
        Image.new("RGB", (int(kwargs["width"]), int(kwargs["height"]))).save(output)
        return output

    async def forbidden_pollinations(**_kwargs: object) -> dict[str, object]:
        calls["pollinations"] += 1
        raise AssertionError("Pollinations must not run after Cloudflare success")

    monkeypatch.setattr(fetch.ai_image, "generate_image", cloudflare)
    monkeypatch.setattr(fetch, "generate_web_pollinations_fallback", forbidden_pollinations)
    plan = _plan()

    asyncio.run(fetch.fetch_assets(plan, tmp_path))

    assert calls == {"cloudflare": 3, "pollinations": 0}
    assert plan.pollinations_fallback_used is False
    assert all(scene.resolved_image_provider == "cloudflare" for scene in plan.scenes)


@pytest.mark.parametrize(
    ("aspect_ratio", "expected_size"),
    [("9:16", (768, 1344)), ("16:9", (1344, 768))],
)
def test_web_fallback_preserves_exact_supported_geometry(
    aspect_ratio: str,
    expected_size: tuple[int, int],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    observed: list[tuple[int, int]] = []

    async def fail_cloudflare(*_args: object, **_kwargs: object) -> None:
        raise _cloudflare_failure("provider_unavailable")

    async def pollinations(**kwargs: object) -> dict[str, object]:
        width, height = int(kwargs["width"]), int(kwargs["height"])
        observed.append((width, height))
        output = Path(kwargs["output_path"])
        Image.new("RGB", (width, height)).save(output)
        return {
            "provider": "pollinations",
            "request_sha256": "a" * 64,
            "width": width,
            "height": height,
            "bytes": output.stat().st_size,
            "sha256": "b" * 64,
            "mime_type": "image/jpeg",
        }

    monkeypatch.setattr(fetch.ai_image, "generate_image", fail_cloudflare)
    monkeypatch.setattr(fetch, "generate_web_pollinations_fallback", pollinations)

    asyncio.run(fetch.fetch_assets(_plan(aspect_ratio=aspect_ratio), tmp_path))

    assert observed == [expected_size] * 3


@pytest.mark.parametrize(
    ("configuration", "reason"),
    [
        ({"POLLINATIONS_API_KEY": ""}, "missing_credential"),
        ({fallback.WEB_POLLINATIONS_FALLBACK_ENV: "0"}, "disabled"),
    ],
)
def test_missing_fallback_readiness_never_calls_pollinations(
    configuration: dict[str, str],
    reason: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    for name, value in configuration.items():
        monkeypatch.setenv(name, value)
    calls = {"cloudflare": 0, "pollinations": 0}

    async def fail_cloudflare(*_args: object, **_kwargs: object) -> None:
        calls["cloudflare"] += 1
        raise _cloudflare_failure("provider_unavailable")

    async def forbidden_pollinations(**_kwargs: object) -> dict[str, object]:
        calls["pollinations"] += 1
        raise AssertionError("unready Pollinations fallback must not run")

    monkeypatch.setattr(fetch.ai_image, "generate_image", fail_cloudflare)
    monkeypatch.setattr(fetch, "generate_web_pollinations_fallback", forbidden_pollinations)
    plan = _plan()

    with pytest.raises(CloudflareAIError):
        asyncio.run(fetch.fetch_assets(plan, tmp_path))

    assert calls == {"cloudflare": 1, "pollinations": 0}
    assert plan.scenes[0].image_fallback_eligible is False
    assert fallback.pollinations_web_readiness(width=768, height=1344)["reason"] == reason


def test_missing_execution_policy_fails_before_any_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    monkeypatch.setenv(fallback.WEB_SENSITIVITY_POLICY_ENV, "")
    calls = {"cloudflare": 0, "pollinations": 0}

    async def forbidden_cloudflare(*_args: object, **_kwargs: object) -> None:
        calls["cloudflare"] += 1

    async def forbidden_pollinations(**_kwargs: object) -> dict[str, object]:
        calls["pollinations"] += 1
        return {}

    monkeypatch.setattr(fetch.ai_image, "generate_image", forbidden_cloudflare)
    monkeypatch.setattr(fetch, "generate_web_pollinations_fallback", forbidden_pollinations)

    with pytest.raises(ValueError, match="policy is missing"):
        asyncio.run(fetch.fetch_assets(_plan(), tmp_path))

    assert calls == {"cloudflare": 0, "pollinations": 0}
    assert fallback.pollinations_web_readiness(width=768, height=1344)["reason"] == (
        "missing_sensitivity_authority"
    )


@pytest.mark.parametrize(
    ("source_text", "expected_sensitivity"),
    [
        ("My name is Alice and my address is private", "private"),
        ("Contact me at person@example.com", "private"),
    ],
)
def test_private_authority_never_calls_pollinations(
    source_text: str,
    expected_sensitivity: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    calls = {"cloudflare": 0, "pollinations": 0}

    async def fail_cloudflare(*_args: object, **_kwargs: object) -> None:
        calls["cloudflare"] += 1
        raise _cloudflare_failure("provider_unavailable")

    async def forbidden_pollinations(**_kwargs: object) -> dict[str, object]:
        calls["pollinations"] += 1
        raise AssertionError("PRIVATE scenes must never call Pollinations")

    monkeypatch.setattr(fetch.ai_image, "generate_image", fail_cloudflare)
    monkeypatch.setattr(fetch, "generate_web_pollinations_fallback", forbidden_pollinations)
    plan = _plan(source_text=source_text)

    with pytest.raises(CloudflareAIError):
        asyncio.run(fetch.fetch_assets(plan, tmp_path))

    assert calls == {"cloudflare": 1, "pollinations": 0}
    assert all(scene.data_sensitivity == expected_sensitivity for scene in plan.scenes)


@pytest.mark.parametrize(
    "error_type",
    [
        "content_policy_blocked",
        "auth_error",
        "permission_error",
        "invalid_request",
        "provider_http_error",
        "provider_failed",
        "quality_insufficient",
        "unknown",
    ],
)
def test_ineligible_cloudflare_failures_never_call_pollinations(
    error_type: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    calls = {"cloudflare": 0, "pollinations": 0}

    async def fail_cloudflare(*_args: object, **_kwargs: object) -> None:
        calls["cloudflare"] += 1
        raise _cloudflare_failure(error_type)

    async def forbidden_pollinations(**_kwargs: object) -> dict[str, object]:
        calls["pollinations"] += 1
        raise AssertionError("ineligible failure must not call Pollinations")

    monkeypatch.setattr(fetch.ai_image, "generate_image", fail_cloudflare)
    monkeypatch.setattr(fetch, "generate_web_pollinations_fallback", forbidden_pollinations)

    with pytest.raises(CloudflareAIError):
        asyncio.run(fetch.fetch_assets(_plan(), tmp_path))

    assert calls == {"cloudflare": 1, "pollinations": 0}


def test_missing_or_stale_authority_fails_before_any_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    calls = {"cloudflare": 0, "pollinations": 0}

    async def forbidden_cloudflare(*_args: object, **_kwargs: object) -> None:
        calls["cloudflare"] += 1

    async def forbidden_pollinations(**_kwargs: object) -> dict[str, object]:
        calls["pollinations"] += 1
        return {}

    monkeypatch.setattr(fetch.ai_image, "generate_image", forbidden_cloudflare)
    monkeypatch.setattr(fetch, "generate_web_pollinations_fallback", forbidden_pollinations)
    missing = _plan()
    missing.scenes[0].data_sensitivity = ""
    stale = _plan()
    stale.scenes[0].image_prompt = "mutated after planning"

    for plan in (missing, stale):
        with pytest.raises(ValueError, match="sensitivity"):
            asyncio.run(fetch.fetch_assets(plan, tmp_path / plan.scenes[0].image_prompt[:4]))

    assert calls == {"cloudflare": 0, "pollinations": 0}


def test_local_only_authority_blocks_all_external_providers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    plan = _plan()
    for scene in plan.scenes:
        sensitivity = SceneDataSensitivity.LOCAL_ONLY
        scene.data_sensitivity = sensitivity.value
        scene.data_sensitivity_authority_sha256 = fallback._canonical_sha256(
            fallback._scene_authority_material(
                scene,
                source_sha256=plan.web_sensitivity_source_sha256,
                sensitivity=sensitivity,
            )
        )
    plan.web_sensitivity_authority_sha256 = fallback._plan_authority_sha256(plan)
    calls = {"cloudflare": 0, "pollinations": 0}

    async def forbidden_cloudflare(*_args: object, **_kwargs: object) -> None:
        calls["cloudflare"] += 1

    async def forbidden_pollinations(**_kwargs: object) -> dict[str, object]:
        calls["pollinations"] += 1
        return {}

    monkeypatch.setattr(fetch.ai_image, "generate_image", forbidden_cloudflare)
    monkeypatch.setattr(fetch, "generate_web_pollinations_fallback", forbidden_pollinations)

    with pytest.raises(RuntimeError, match="LOCAL_ONLY"):
        asyncio.run(fetch.fetch_assets(plan, tmp_path))

    assert calls == {"cloudflare": 0, "pollinations": 0}


def test_pollinations_failure_never_calls_other_fallbacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    calls = {"pollinations": 0, "pexels": 0, "local": 0}

    async def fail_cloudflare(*_args: object, **_kwargs: object) -> None:
        raise _cloudflare_failure("rate_limited")

    async def fail_pollinations(**_kwargs: object) -> dict[str, object]:
        calls["pollinations"] += 1
        raise RuntimeError("sanitized Pollinations provider unavailable")

    async def forbidden_pexels(*_args: object, **_kwargs: object) -> None:
        calls["pexels"] += 1

    def forbidden_local(*_args: object, **_kwargs: object) -> object:
        calls["local"] += 1
        raise AssertionError("local fallback must not run")

    monkeypatch.setattr(fetch.ai_image, "generate_image", fail_cloudflare)
    monkeypatch.setattr(fetch, "generate_web_pollinations_fallback", fail_pollinations)
    monkeypatch.setattr(fetch.stock_photo, "search_and_download", forbidden_pexels)
    monkeypatch.setattr(fetch.sprite_composer, "compose_scene", forbidden_local)

    with pytest.raises(RuntimeError, match="Pollinations"):
        asyncio.run(fetch.fetch_assets(_plan(), tmp_path))

    assert calls == {"pollinations": 1, "pexels": 0, "local": 0}


def test_concurrent_duplicate_generation_does_not_duplicate_pollinations_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    plan = _plan()
    plan.scenes = plan.scenes[:1]
    plan.web_sensitivity_authority_sha256 = fallback._plan_authority_sha256(plan)
    calls = 0

    async def fail_cloudflare(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(0)
        raise _cloudflare_failure("timeout")

    async def slow_pollinations(**kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        output = Path(kwargs["output_path"])
        Image.new("RGB", (int(kwargs["width"]), int(kwargs["height"]))).save(output)
        return {
            "provider": "pollinations",
            "request_sha256": "a" * 64,
            "width": int(kwargs["width"]),
            "height": int(kwargs["height"]),
            "bytes": output.stat().st_size,
            "sha256": "b" * 64,
            "mime_type": "image/jpeg",
        }

    monkeypatch.setattr(fetch.ai_image, "generate_image", fail_cloudflare)
    monkeypatch.setattr(fetch, "generate_web_pollinations_fallback", slow_pollinations)

    async def run_twice() -> list[object]:
        return await asyncio.gather(
            fetch.fetch_assets(plan, tmp_path),
            fetch.fetch_assets(plan, tmp_path),
            return_exceptions=True,
        )

    outcomes = asyncio.run(run_twice())

    assert calls == 1
    assert any(isinstance(outcome, RuntimeError) for outcome in outcomes)
