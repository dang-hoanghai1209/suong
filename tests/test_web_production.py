from __future__ import annotations

import asyncio
import http.client
from http import HTTPStatus
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from PIL import Image
from pydantic import ValidationError

from tella.media import fetch
from tella.planner.models import Scene, TellaScenePlan
from tella.tts import synth_all
from tella.tts.providers import GeminiTTSProvider
from tella.web import ProductionWebServer
from tella.web_contract import MAX_WEB_REQUEST_BYTES, WebJobStatus, WebRenderRequest
from tella import web_jobs as web_jobs_module
from tella.web_jobs import JobManager, sanitize_public_text, web_readiness
from tella.voice_profiles import get_voice_profile

_ORIGINAL_CREATE_CONNECTION = socket.create_connection
_WEB_PROFILE = get_voice_profile("gemini_callirrhoe_vi_gentle_emotional")
_WEB_TTS_MODEL = "gemini-2.5-flash-preview-tts"


def _request(
    *,
    content: str = "A patient gardener",
    input_mode: str = "topic",
) -> WebRenderRequest:
    return WebRenderRequest(
        input_mode=input_mode,
        content=content,
        language="en",
        theme="cinematic",
        media_source="ai_image",
        duration_mode="short",
        aspect_ratio="9:16",
        no_music=True,
    )


def test_web_narrator_profile_is_semantic_and_pins_gemini_2_5() -> None:
    assert _WEB_PROFILE.profile_id == "gemini_callirrhoe_vi_gentle_emotional"
    assert _WEB_PROFILE.model == _WEB_TTS_MODEL
    assert _WEB_PROFILE.voice == "Callirrhoe"
    assert _WEB_PROFILE.style == "gentle_emotional"
    assert _WEB_PROFILE.language == "vi-VN"
    assert _WEB_PROFILE.automatic_edge_fallback_enabled is False
    assert _WEB_PROFILE.automatic_model_fallback_enabled is False
    assert web_jobs_module._WEB_ENVIRONMENT["TELLA_TTS_MODEL"] == _WEB_TTS_MODEL


def _configure_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-readiness")
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("CF_ACCOUNT_ID", "test-cloudflare-account")
    monkeypatch.setenv("CF_AI_TOKEN", "test-cloudflare-token")
    monkeypatch.delenv("CF_ACCOUNTS", raising=False)
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("PEXELS_API_KEYS", raising=False)
    monkeypatch.setattr(
        web_jobs_module.shutil,
        "which",
        lambda executable: f"C:/tools/{executable}.exe",
    )


def _wait(manager: JobManager, job_id: str, timeout: float = 5) -> object:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.get(job_id)
        if job.status in {
            WebJobStatus.SUCCEEDED,
            WebJobStatus.FAILED,
            WebJobStatus.CANCELLED,
        }:
            return job
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def _fake_probe(path: Path) -> dict[str, object]:
    assert path.read_bytes() == b"fake mp4 for API boundary"
    return {"duration_seconds": 4.25, "video_streams": 1, "audio_streams": 1}


def _write_fake_tts_metadata(job_dir: Path) -> None:
    (job_dir / "tts_metadata.json").write_text(
        json.dumps(
            {
                "requested_provider": "gemini",
                "tts_provider": "gemini",
                "tts_model": _WEB_PROFILE.model,
                "tts_voice": _WEB_PROFILE.voice,
                "tts_style": _WEB_PROFILE.style,
                "tts_language": "en",
                "fallback_used": False,
                "tts_continuous": True,
                "authorization_header": "test-only-sensitive-value",
                "raw_upstream_url": "https://provider.invalid/private",
            }
        ),
        encoding="utf-8",
    )


def test_request_is_strict_and_forbids_browser_provider_authority() -> None:
    with pytest.raises(ValidationError):
        _request(content=" leading")
    with pytest.raises(ValidationError):
        WebRenderRequest.model_validate(
            {**_request().model_dump(mode="json"), "schema_version": 1.0},
            strict=True,
        )
    for media_source in ("stock_photo", "stock_video"):
        with pytest.raises(ValidationError):
            WebRenderRequest.model_validate(
                {**_request().model_dump(mode="json"), "media_source": media_source},
                strict=True,
            )
    for field, value in {
        "provider": "google",
        "TELLA_DISABLE_STOCK_FALLBACK": "0",
        "TELLA_RENDER_MOTION_PROFILE": "slow_ken_burns",
        "stock_fallback": True,
        "motion_profile": "slow_ken_burns",
        "ffmpeg_expression": "zoom+0.01",
        "api_key": "browser-secret",
        "upstream_url": "https://provider.invalid",
        "model": "browser-model",
        "voice": "Callirrhoe",
        "voice_pace": "slow",
        "voice_gender": "female",
    }.items():
        with pytest.raises(ValidationError):
            WebRenderRequest.model_validate(
                {**_request().model_dump(mode="json"), field: value},
                strict=True,
            )


def test_vietnamese_script_no_music_request_persists_without_default_replacement(
    tmp_path: Path,
) -> None:
    payload = _request(
        content="Mỗi ngày là một cơ hội mới.",
        input_mode="script",
    ).model_dump(mode="json")
    payload.update(language="vi", no_music=True)
    request = WebRenderRequest.model_validate(payload, strict=True)
    manager = JobManager(tmp_path, autostart=False)

    created = manager.create(request)
    persisted = json.loads((tmp_path / created.job_id / "job.json").read_text(encoding="utf-8"))

    assert created.request.input_mode == "script"
    assert created.request.language == "vi"
    assert created.request.no_music is True
    assert persisted["request"] == request.model_dump(mode="json")
    assert persisted["request"]["language"] == "vi"
    assert persisted["request"]["no_music"] is True
    manager.close()


@pytest.mark.parametrize("language", ["vi", "en", "es", "fr", "de", "ja", "ko", "zh", "pt", "it"])
def test_backend_owned_narrator_preserves_supported_request_languages(language: str) -> None:
    payload = _request().model_dump(mode="json")
    payload["language"] = language
    assert WebRenderRequest.model_validate(payload, strict=True).language == language


@pytest.mark.parametrize("original", [None, "", "0", "false"])
def test_web_stock_fallback_authority_overrides_and_exactly_restores_inherited_state(
    original: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if original is None:
        monkeypatch.delenv("TELLA_DISABLE_STOCK_FALLBACK", raising=False)
    else:
        monkeypatch.setenv("TELLA_DISABLE_STOCK_FALLBACK", original)
    manager = JobManager(tmp_path, runner=lambda **_kwargs: None, media_probe=_fake_probe)

    with manager._strict_gemini_environment():
        assert os.environ["TELLA_DISABLE_STOCK_FALLBACK"] == "1"

    manager.close()
    assert os.environ.get("TELLA_DISABLE_STOCK_FALLBACK") == original


def test_web_model_authority_overrides_and_restores_hostile_3_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_model = "gemini-3.1-flash-tts-preview"
    monkeypatch.setenv("TELLA_TTS_MODEL", legacy_model)
    manager = JobManager(tmp_path, runner=lambda **_kwargs: None, media_probe=_fake_probe)

    with manager._strict_gemini_environment():
        assert os.environ["TELLA_TTS_MODEL"] == _WEB_TTS_MODEL
        assert os.environ["TELLA_TTS_VOICE"] == "Callirrhoe"
        assert os.environ["TELLA_TTS_PROVIDER"] == "gemini"

    manager.close()
    assert os.environ["TELLA_TTS_MODEL"] == legacy_model


def _classic_web_image_plan() -> TellaScenePlan:
    return TellaScenePlan(
        title="Cloudflare-only web route",
        language="en",
        aspect_ratio="9:16",
        media_source="ai_image",
        duration_mode="short",
        theme="cinematic",
        scenes=[
            Scene(
                scene_index=index,
                kind="scene",
                title=f"Scene {index}",
                voice_script=f"Narration {index}.",
                image_prompt=f"Public illustration {index}",
                stock_query="forbidden stock query",
            )
            for index in range(1, 4)
        ],
    )


def test_web_cloudflare_failure_is_terminal_without_fallback_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = {"cloudflare": 0, "pexels": 0, "local": 0}
    observed_plan: TellaScenePlan | None = None

    async def fail_cloudflare(*_args: object, **_kwargs: object) -> None:
        calls["cloudflare"] += 1
        raise RuntimeError("controlled Cloudflare failure")

    async def forbidden_pexels(*_args: object, **_kwargs: object) -> None:
        calls["pexels"] += 1
        raise AssertionError("Pexels must not run in the production web profile")

    def forbidden_local(*_args: object, **_kwargs: object) -> None:
        calls["local"] += 1
        raise AssertionError("local fallback must not run in the web profile")

    async def runner(**kwargs: object) -> Path:
        nonlocal observed_plan
        observed_plan = _classic_web_image_plan()
        await fetch.fetch_assets(
            observed_plan,
            Path(kwargs["out_root"]) / str(kwargs["job_id"]),
        )
        raise AssertionError("Cloudflare failure must terminate web execution")

    monkeypatch.setattr(fetch.ai_image, "generate_image", fail_cloudflare)
    monkeypatch.setattr(fetch.stock_photo, "search_and_download", forbidden_pexels)
    monkeypatch.setattr(fetch.sprite_composer, "compose_scene", forbidden_local)
    monkeypatch.setattr(fetch, "MAX_CONCURRENT", 1)
    caplog.set_level(logging.WARNING, logger="tella.media.fetch")
    manager = JobManager(tmp_path, runner=runner, media_probe=_fake_probe)
    result = _wait(manager, manager.create(_request()).job_id)
    manager.close()

    assert result.status is WebJobStatus.FAILED
    assert result.error is not None
    assert result.error.code == "PIPELINE_FAILED"
    assert calls == {"cloudflare": 3, "pexels": 0, "local": 0}
    assert observed_plan is not None
    assert observed_plan.used_local_fallback is False
    assert observed_plan.reused_asset is False
    assert all(not scene.image_filenames for scene in observed_plan.scenes)
    assert "stock fallback is disabled" in caplog.text
    assert "fallback to Pexels" not in caplog.text
    assert result.error.message == "Production pipeline failed. Review the sanitized job logs."


def test_web_cloudflare_success_keeps_generated_still_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"cloudflare": 0, "pexels": 0, "local": 0}
    observed_plan: TellaScenePlan | None = None

    async def succeed_cloudflare(
        _prompt: str,
        out_path: Path,
        *,
        width: int,
        height: int,
        seed: int | None = None,
    ) -> Path:
        del seed
        calls["cloudflare"] += 1
        Image.new("RGB", (width, height), "#315b71").save(out_path)
        return out_path

    async def forbidden_pexels(*_args: object, **_kwargs: object) -> None:
        calls["pexels"] += 1
        raise AssertionError("Pexels must not run after Cloudflare success")

    def forbidden_local(*_args: object, **_kwargs: object) -> None:
        calls["local"] += 1
        raise AssertionError("local fallback must not run after Cloudflare success")

    async def runner(**kwargs: object) -> Path:
        nonlocal observed_plan
        job_dir = Path(kwargs["out_root"]) / str(kwargs["job_id"])
        observed_plan = _classic_web_image_plan()
        await fetch.fetch_assets(observed_plan, job_dir)
        output = job_dir / "video.mp4"
        output.write_bytes(b"fake mp4 for API boundary")
        _write_fake_tts_metadata(job_dir)
        return output

    monkeypatch.setattr(fetch.ai_image, "generate_image", succeed_cloudflare)
    monkeypatch.setattr(fetch.stock_photo, "search_and_download", forbidden_pexels)
    monkeypatch.setattr(fetch.sprite_composer, "compose_scene", forbidden_local)
    monkeypatch.setattr(fetch, "MAX_CONCURRENT", 1)
    manager = JobManager(tmp_path, runner=runner, media_probe=_fake_probe)
    result = _wait(manager, manager.create(_request()).job_id)
    manager.close()

    assert result.status is WebJobStatus.SUCCEEDED
    assert calls == {"cloudflare": 3, "pexels": 0, "local": 0}
    assert observed_plan is not None
    assert all(scene.asset_status == "done" for scene in observed_plan.scenes)
    assert all(scene.image_filenames for scene in observed_plan.scenes)


def test_stock_fallback_default_remains_enabled_outside_web_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TELLA_DISABLE_STOCK_FALLBACK", raising=False)

    assert fetch._stock_fallback_disabled() is False


def test_job_manager_runs_serially_persists_logs_and_enforces_gemini(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    active = 0
    maximum_active = 0
    seen: list[dict[str, object]] = []
    lock = threading.Lock()

    async def runner(**kwargs: object) -> Path:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        try:
            assert {name: os.environ.get(name) for name in expected} == expected
            assert os.environ["TELLA_TTS_PROVIDER"] == "gemini"
            assert os.environ["TELLA_STRICT_TTS_PROVIDER"] == "1"
            assert os.environ["TELLA_TTS_MODEL"] == _WEB_PROFILE.model
            assert os.environ["TELLA_TTS_VOICE"] == _WEB_PROFILE.voice
            assert os.environ["TELLA_TTS_STYLE"] == _WEB_PROFILE.style
            assert os.environ["TELLA_DISABLE_STOCK_FALLBACK"] == "1"
            assert kwargs["google_tts_api_key"] == ""
            assert kwargs["google_tts_voice"] == ""
            assert kwargs["voice_pace_name"] is None
            assert kwargs["voice_gender"] is None
            assert kwargs["dry_run_plan"] is False
            assert kwargs["allow_local_image_fallback"] is False
            seen.append(kwargs)
            logging.getLogger("tella.test").info("step 4/6 synthesizing narration")
            await asyncio.sleep(0.03)
            job_dir = Path(kwargs["out_root"]) / str(kwargs["job_id"])
            (job_dir / "video.mp4").write_bytes(b"fake mp4 for API boundary")
            (job_dir / "plan.json").write_text(
                '{"title":"Safe title","scenes":[{},{}]}',
                encoding="utf-8",
            )
            _write_fake_tts_metadata(job_dir)
            return job_dir / "video.mp4"
        finally:
            with lock:
                active -= 1

    original = {
        name: f"hostile-{index}" for index, name in enumerate(web_jobs_module._WEB_ENVIRONMENT)
    }
    expected = dict(web_jobs_module._WEB_ENVIRONMENT)
    for name, value in original.items():
        monkeypatch.setenv(name, value)
    manager = JobManager(tmp_path, runner=runner, media_probe=_fake_probe)
    first = manager.create(_request(content="First"))
    second = manager.create(_request(content="Second", input_mode="script"))
    first_done = _wait(manager, first.job_id)
    second_done = _wait(manager, second.job_id)
    manager.close()

    assert maximum_active == 1
    assert len(seen) == 2
    assert seen[0]["topic"] == "First"
    assert seen[0]["user_script"] is None
    assert seen[1]["topic"] == "exact script"
    assert seen[1]["user_script"] == "Second"
    assert first_done.status is WebJobStatus.SUCCEEDED
    assert second_done.status is WebJobStatus.SUCCEEDED
    assert first_done.progress == 100
    assert first_done.request == _request(content="First")
    assert first_done.output_bytes == len(b"fake mp4 for API boundary")
    assert any("step 4/6" in line for line in first_done.logs)
    persisted = json.loads((tmp_path / first.job_id / "job.json").read_text("utf-8"))
    assert persisted["output_path"] == "video.mp4"
    assert persisted["probe"]["audio_streams"] == 1
    assert first_done.plan_metadata == {"title": "Safe title", "scene_count": 2}
    assert first_done.tts_metadata == {
        "provider": "gemini",
        "narrator_profile": "gemini_callirrhoe_vi_gentle_emotional",
        "language": "en",
        "fallback_used": False,
        "continuous_narration": True,
    }
    assert "test-only-sensitive-value" not in json.dumps(persisted)
    assert "provider.invalid" not in json.dumps(persisted)
    assert {name: os.environ.get(name) for name in original} == original
    assert all(seen_value is not None for seen_value in original.values())


def test_failed_pipeline_never_exposes_artifact_and_restores_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original: dict[str, str | None] = {}
    for index, name in enumerate(web_jobs_module._WEB_ENVIRONMENT):
        value = f"prior-failure-{index}" if index % 2 else None
        original[name] = value
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    expected = dict(web_jobs_module._WEB_ENVIRONMENT)
    original["TELLA_DISABLE_STOCK_FALLBACK"] = "0"
    monkeypatch.setenv("TELLA_DISABLE_STOCK_FALLBACK", "0")

    async def runner(**kwargs: object) -> Path:
        assert {name: os.environ.get(name) for name in expected} == expected
        raise RuntimeError("provider failed with sensitive implementation detail")

    manager = JobManager(tmp_path, runner=runner, media_probe=_fake_probe)
    created = manager.create(_request())
    result = _wait(manager, created.job_id)
    assert result.status is WebJobStatus.FAILED
    assert result.progress < 100
    assert result.output_available is False
    assert result.error is not None
    assert result.error.code == "PIPELINE_FAILED"
    with pytest.raises(KeyError):
        manager.artifact_path(created.job_id)
    manager.close()
    assert {name: os.environ.get(name) for name in original} == original


def test_non_gemini_or_fallback_metadata_cannot_authorize_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def runner(**kwargs: object) -> Path:
        output = Path(kwargs["out_root"]) / str(kwargs["job_id"]) / "video.mp4"
        output.write_bytes(b"fake mp4 for API boundary")
        _write_fake_tts_metadata(output.parent)
        metadata_path = output.parent / "tts_metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["tts_provider"] = "edge"
        metadata["fallback_used"] = True
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        return output

    monkeypatch.setenv("TELLA_SKIP_IMAGE_GENERATION", "hostile-original")
    monkeypatch.delenv("TELLA_TTS_CACHE_ENABLED", raising=False)
    monkeypatch.delenv("TELLA_DISABLE_STOCK_FALLBACK", raising=False)
    monkeypatch.delenv("TELLA_RENDER_MOTION_PROFILE", raising=False)
    manager = JobManager(tmp_path, runner=runner, media_probe=_fake_probe)
    result = _wait(manager, manager.create(_request()).job_id)
    manager.close()

    assert result.status is WebJobStatus.FAILED
    assert result.error is not None
    assert result.error.code == "WEB_TTS_AUTHORITY_INVALID"
    assert result.output_available is False
    assert os.environ["TELLA_SKIP_IMAGE_GENERATION"] == "hostile-original"
    assert "TELLA_TTS_CACHE_ENABLED" not in os.environ
    assert "TELLA_DISABLE_STOCK_FALLBACK" not in os.environ
    assert "TELLA_RENDER_MOTION_PROFILE" not in os.environ


def test_stale_3_1_metadata_cannot_authorize_new_2_5_web_success(
    tmp_path: Path,
) -> None:
    async def runner(**kwargs: object) -> Path:
        output = Path(kwargs["out_root"]) / str(kwargs["job_id"]) / "video.mp4"
        output.write_bytes(b"fake mp4 for API boundary")
        _write_fake_tts_metadata(output.parent)
        metadata_path = output.parent / "tts_metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["tts_model"] = "gemini-3.1-flash-tts-preview"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        return output

    manager = JobManager(tmp_path, runner=runner, media_probe=_fake_probe)
    result = _wait(manager, manager.create(_request()).job_id)
    manager.close()

    assert result.status is WebJobStatus.FAILED
    assert result.error is not None
    assert result.error.code == "WEB_TTS_AUTHORITY_INVALID"
    assert result.output_available is False


def test_web_gemini_failure_never_calls_google_or_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"gemini": 0, "google": 0, "edge": 0}

    async def fail_gemini(*args: object, **kwargs: object) -> object:
        calls["gemini"] += 1
        raise RuntimeError("bounded Gemini failure")

    async def forbidden_google(*args: object, **kwargs: object) -> bool:
        calls["google"] += 1
        return False

    async def forbidden_edge(*args: object, **kwargs: object) -> object:
        calls["edge"] += 1
        raise AssertionError("Edge must not be called")

    monkeypatch.setattr(GeminiTTSProvider, "synthesize", fail_gemini)
    monkeypatch.setattr(synth_all.google, "synth_google", forbidden_google)
    monkeypatch.setattr(synth_all.EdgeTTSProvider, "synthesize", forbidden_edge)

    async def runner(**kwargs: object) -> Path:
        plan = TellaScenePlan(
            title="Web Gemini boundary",
            language=str(kwargs["target_lang"]),
            theme=str(kwargs["theme"]),
            scenes=[
                Scene(scene_index=index, voice_script=f"Narration scene {index}.")
                for index in range(1, 4)
            ],
        )
        await synth_all.synthesize_all(
            plan,
            Path(kwargs["out_root"]) / str(kwargs["job_id"]),
            google_tts_api_key=str(kwargs["google_tts_api_key"]),
            google_tts_voice=str(kwargs["google_tts_voice"]),
        )
        raise AssertionError("Gemini failure must stop the pipeline")

    manager = JobManager(tmp_path, runner=runner, media_probe=_fake_probe)
    result = _wait(manager, manager.create(_request()).job_id)
    manager.close()

    assert result.status is WebJobStatus.FAILED
    assert result.error is not None
    assert result.error.code == "GEMINI_TTS_FAILED"
    assert calls == {"gemini": 1, "google": 0, "edge": 0}


def test_restart_marks_inflight_job_failed(tmp_path: Path) -> None:
    job_id = "web-0123456789abcdef01234567"
    directory = tmp_path / job_id
    directory.mkdir()
    payload = {
        "schema_version": 1,
        "job_id": job_id,
        "status": "running",
        "phase": "rendering",
        "progress": 85,
        "created_at": "2026-01-01T00:00:00Z",
        "started_at": "2026-01-01T00:00:01Z",
        "finished_at": None,
        "logs": [],
        "error": None,
        "output_path": None,
        "probe": None,
        "request": _request().model_dump(mode="json"),
    }
    (directory / "job.json").write_text(json.dumps(payload), encoding="utf-8")
    manager = JobManager(tmp_path, media_probe=_fake_probe, autostart=False)
    recovered = manager.get(job_id)
    assert recovered.status is WebJobStatus.FAILED
    assert recovered.error is not None
    assert recovered.error.code == "SERVER_RESTART_INTERRUPTED_JOB"


def test_restart_sanitizes_untrusted_persisted_logs_and_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = "restart-secret-72014"
    monkeypatch.setenv("GEMINI_API_KEY", sentinel)
    job_id = "web-1123456789abcdef01234567"
    directory = tmp_path / job_id
    directory.mkdir()
    unsafe = (
        f"Authorization: Bearer {sentinel} "
        r"C:\private\restart\failure.txt HTTP 500: private response"
    )
    payload = {
        "schema_version": 1,
        "job_id": job_id,
        "status": "failed",
        "phase": "failed",
        "progress": 42,
        "created_at": "2026-01-01T00:00:00Z",
        "started_at": "2026-01-01T00:00:01Z",
        "finished_at": "2026-01-01T00:00:02Z",
        "logs": [unsafe],
        "error": {"code": unsafe, "message": unsafe},
        "output_path": None,
        "probe": None,
        "request": _request().model_dump(mode="json"),
    }
    (directory / "job.json").write_text(json.dumps(payload), encoding="utf-8")

    recovered = JobManager(tmp_path, media_probe=_fake_probe, autostart=False).get(job_id)
    serialized = json.dumps(recovered.model_dump(mode="json"))
    assert sentinel not in serialized
    assert "private response" not in serialized
    assert r"C:\private\restart" not in serialized
    assert recovered.error is not None
    assert recovered.error.code == "PIPELINE_FAILED"


def _http(
    base: str,
    path: str,
    *,
    method: str = "GET",
    payload: object | None = None,
) -> tuple[int, dict[str, object] | bytes, dict[str, str]]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        base + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        response = urlopen(request, timeout=5)
    except HTTPError as exc:
        response = exc
    content = response.read()
    headers = {key.lower(): value for key, value in response.headers.items()}
    if "application/json" in headers.get("content-type", ""):
        return response.status, json.loads(content), headers
    return response.status, content, headers


def test_api_create_status_preview_download_and_security(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A repository script imported by an earlier full-suite test replaces this
    # process-global function without restoring it. Retain the genuine loopback
    # connector captured during collection so this API contract is order-safe.
    monkeypatch.setattr(socket, "create_connection", _ORIGINAL_CREATE_CONNECTION)
    _configure_ready(monkeypatch)

    async def runner(**kwargs: object) -> Path:
        logging.info("step 6/6 rendering video")
        output = Path(kwargs["out_root"]) / str(kwargs["job_id"]) / "video.mp4"
        output.write_bytes(b"fake mp4 for API boundary")
        _write_fake_tts_metadata(output.parent)
        return output

    manager = JobManager(tmp_path / "jobs", runner=runner, media_probe=_fake_probe)
    server = ProductionWebServer(("127.0.0.1", 0), manager)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        status, health, _ = _http(base, "/api/health?media_source=stock_photo")
        assert status == HTTPStatus.UNPROCESSABLE_ENTITY
        assert isinstance(health, dict)
        assert health["error"]["code"] == "INVALID_MEDIA_SOURCE"

        status, root, headers = _http(base, "/")
        assert status == HTTPStatus.OK
        assert isinstance(root, bytes)
        assert b'type="module" src="/app.js"' in root
        assert b'id="media_source"' not in root
        assert b"stock_photo" not in root
        assert b"stock_video" not in root
        assert b'id="job-history"' in root
        assert b'id="job-progress"' in root
        assert "text/html" in headers["content-type"]
        for static_path in ("/styles.css", "/app.js", "/ui_state.js"):
            static_status, body, static_headers = _http(base, static_path)
            assert static_status == HTTPStatus.OK
            assert isinstance(body, bytes)
            expected_type = "text/css" if static_path.endswith(".css") else "javascript"
            assert expected_type in static_headers["content-type"]

        status, created, _ = _http(
            base,
            "/api/jobs",
            method="POST",
            payload=_request().model_dump(mode="json"),
        )
        assert status == HTTPStatus.ACCEPTED
        assert isinstance(created, dict)
        job_id = str(created["job_id"])
        _wait(manager, job_id)

        status, job, _ = _http(base, f"/api/jobs/{job_id}")
        assert status == HTTPStatus.OK
        assert isinstance(job, dict)
        assert job["status"] == "succeeded"
        assert job["request"]["media_source"] == "ai_image"
        assert job["output_bytes"] == len(b"fake mp4 for API boundary")
        status, preview, headers = _http(base, f"/api/jobs/{job_id}/preview")
        assert status == HTTPStatus.OK
        assert preview == b"fake mp4 for API boundary"
        assert headers["content-type"] == "video/mp4"
        range_response = urlopen(
            Request(
                base + f"/api/jobs/{job_id}/preview",
                headers={"Range": "bytes=5-9"},
            ),
            timeout=5,
        )
        assert range_response.status == HTTPStatus.PARTIAL_CONTENT
        assert range_response.headers["Content-Range"] == "bytes 5-9/25"
        assert range_response.read() == b"mp4 f"
        status, download, headers = _http(base, f"/api/jobs/{job_id}/download")
        assert status == HTTPStatus.OK
        assert download == preview
        assert headers["content-disposition"].startswith("attachment;")

        status, _, _ = _http(base, "/.env")
        assert status == HTTPStatus.NOT_FOUND
        status, _, _ = _http(base, "/api/jobs/../../.env")
        assert status == HTTPStatus.NOT_FOUND
        status, error, _ = _http(base, "/api/jobs", method="POST", payload={"bad": True})
        assert status == HTTPStatus.UNPROCESSABLE_ENTITY
        assert isinstance(error, dict)
        assert error["error"]["code"] == "INVALID_RENDER_REQUEST"
        stock_payload = _request().model_dump(mode="json")
        stock_payload["media_source"] = "stock_video"
        status, error, _ = _http(base, "/api/jobs", method="POST", payload=stock_payload)
        assert status == HTTPStatus.UNPROCESSABLE_ENTITY
        assert isinstance(error, dict)
        assert error["error"]["code"] == "INVALID_RENDER_REQUEST"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_api_history_cancel_and_retry_create_distinct_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(socket, "create_connection", _ORIGINAL_CREATE_CONNECTION)
    _configure_ready(monkeypatch)
    manager = JobManager(tmp_path / "jobs", autostart=False)
    server = ProductionWebServer(("127.0.0.1", 0), manager)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    payload = _request(content="Retry-safe exact content", input_mode="script").model_dump(
        mode="json"
    )
    try:
        status, first, _ = _http(base, "/api/jobs", method="POST", payload=payload)
        assert status == HTTPStatus.ACCEPTED
        assert isinstance(first, dict)
        first_id = str(first["job_id"])

        status, history, _ = _http(base, "/api/jobs")
        assert status == HTTPStatus.OK
        assert isinstance(history, dict)
        assert history["jobs"][0]["request"] == payload

        status, cancelled, _ = _http(base, f"/api/jobs/{first_id}/cancel", method="POST")
        assert status == HTTPStatus.OK
        assert isinstance(cancelled, dict)
        assert cancelled["status"] == "cancelled"

        status, retried, _ = _http(base, "/api/jobs", method="POST", payload=payload)
        assert status == HTTPStatus.ACCEPTED
        assert isinstance(retried, dict)
        assert retried["job_id"] != first_id
        assert manager.get(first_id).status is WebJobStatus.CANCELLED
        assert manager.get(str(retried["job_id"])).status is WebJobStatus.QUEUED
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_health_is_truthful_and_contains_no_secret_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "secret-gemini-value")
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_TTS_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_TTS_VOICE", raising=False)
    monkeypatch.setenv("CF_ACCOUNT_ID", "secret-cloudflare-account")
    monkeypatch.setenv("CF_AI_TOKEN", "secret-cloudflare-token")
    monkeypatch.setattr(web_jobs_module.shutil, "which", lambda name: f"tool-{name}")
    result = web_readiness(tmp_path)
    serialized = json.dumps(result)
    assert result["checks"]["gemini_planning"]["ready"] is True
    assert result["checks"]["gemini_narration"]["ready"] is True
    assert result["checks"]["image_route"]["ready"] is True
    assert "google_tts" not in result["checks"]
    assert "google_voice" not in result["checks"]
    assert result["tts_provider"] == "gemini"
    assert result["narrator_profile"] == "gemini_callirrhoe_vi_gentle_emotional"
    assert "secret-gemini-value" not in serialized
    assert "secret-cloudflare" not in serialized

    monkeypatch.delenv("GEMINI_API_KEY")
    missing = web_readiness(tmp_path)
    assert missing["checks"]["gemini_planning"]["ready"] is False
    assert missing["checks"]["gemini_narration"]["ready"] is False


def test_health_fails_closed_when_registered_narrator_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_ready(monkeypatch)

    def invalid_voice(*_args: object, **_kwargs: object) -> object:
        raise ValueError("controlled invalid narrator profile")

    monkeypatch.setattr(web_jobs_module, "resolve_voice", invalid_voice)
    result = web_readiness(tmp_path)

    assert result["ready"] is False
    assert result["checks"]["gemini_planning"]["ready"] is True
    assert result["checks"]["gemini_narration"]["ready"] is False


def test_image_only_readiness_uses_cloudflare_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_ready(monkeypatch)
    ai_result = web_readiness(tmp_path, _request())
    assert ai_result["ready"] is True
    assert ai_result["media_source"] == "ai_image"
    with pytest.raises(ValueError, match="unsupported media source"):
        web_readiness(tmp_path, media_source="stock_photo")


def test_direct_post_rejects_unready_without_creating_or_executing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(socket, "create_connection", _ORIGINAL_CREATE_CONNECTION)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("CF_ACCOUNTS", raising=False)
    monkeypatch.delenv("CF_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("CF_AI_TOKEN", raising=False)
    monkeypatch.setattr(web_jobs_module.shutil, "which", lambda name: f"tool-{name}")
    calls = {"pipeline": 0, "probe": 0, "subprocess": 0}

    async def runner(**kwargs: object) -> Path:
        calls["pipeline"] += 1
        raise AssertionError("pipeline must not run")

    def probe(path: Path) -> dict[str, object]:
        calls["probe"] += 1
        raise AssertionError("ffprobe seam must not run")

    def subprocess_run(*args: object, **kwargs: object) -> object:
        calls["subprocess"] += 1
        raise AssertionError("external executable must not run")

    monkeypatch.setattr(web_jobs_module.subprocess, "run", subprocess_run)

    jobs_root = tmp_path / "jobs"
    manager = JobManager(jobs_root, runner=runner, media_probe=probe)
    server = ProductionWebServer(("127.0.0.1", 0), manager)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload, _ = _http(
            f"http://127.0.0.1:{server.server_port}",
            "/api/jobs",
            method="POST",
            payload=_request().model_dump(mode="json"),
        )
        assert status == HTTPStatus.SERVICE_UNAVAILABLE
        assert isinstance(payload, dict)
        assert payload["error"] == {
            "code": "PRODUCTION_NOT_READY",
            "message": "Required local production capabilities are not ready.",
        }
        assert payload["readiness"]["ready"] is False
        assert manager.list() == ()
        assert list(jobs_root.iterdir()) == []
        assert calls == {"pipeline": 0, "probe": 0, "subprocess": 0}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_sanitizer_redacts_secrets_paths_headers_queries_and_provider_bodies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinels = {
        "GEMINI_API_KEY": "gemini-sentinel-40291",
        "GOOGLE_TTS_API_KEY": "google-sentinel-19382",
        "CF_AI_TOKEN": "cloudflare-sentinel-59320",
        "POLLINATIONS_API_KEY": "pollinations-sentinel-84021",
        "PEXELS_API_KEY": "pexels-sentinel-62013",
        "GOOGLE_APPLICATION_CREDENTIALS": r"C:\private\google-credentials.json",
    }
    for name, value in sentinels.items():
        monkeypatch.setenv(name, value)
    source = (
        "Safe narration. Authorization: Bearer header-secret-501 "
        "https://provider.invalid/api?key=query-secret-502&token=query-secret-503 "
        "https://user-secret:password-secret@provider.invalid/private "
        r"C:\private\trace\failure.txt /var/private/trace/failure.txt "
        "HTTP 500: private provider response body"
    )
    sanitized = sanitize_public_text(source)
    assert sanitized.startswith("Safe narration.")
    assert all(value not in sanitized for value in sentinels.values())
    assert all(
        marker not in sanitized
        for marker in (
            "header-secret-501",
            "query-secret-502",
            "query-secret-503",
            "user-secret",
            "password-secret",
            "private provider response body",
            r"C:\private\trace",
            "/var/private/trace",
        )
    )

    async def runner(**kwargs: object) -> Path:
        logging.info(source)
        raise RuntimeError(source)

    root = tmp_path / "jobs"
    manager = JobManager(root, runner=runner, media_probe=_fake_probe)
    result = _wait(manager, manager.create(_request()).job_id)
    assert manager.close()
    persisted_path = root / result.job_id / "job.json"
    persisted = persisted_path.read_text(encoding="utf-8")
    assert all(value not in persisted for value in sentinels.values())
    assert "private provider response body" not in persisted
    assert result.error is not None
    assert result.error.message == "Production pipeline failed. Review the sanitized job logs."

    reloaded = JobManager(root, media_probe=_fake_probe, autostart=False).get(result.job_id)
    serialized = json.dumps(reloaded.model_dump(mode="json"))
    assert all(value not in serialized for value in sentinels.values())
    assert "private provider response body" not in serialized


@pytest.mark.parametrize(
    ("raw_length", "expected_status"),
    [
        (str(MAX_WEB_REQUEST_BYTES + 1), HTTPStatus.REQUEST_ENTITY_TOO_LARGE),
        ("invalid", HTTPStatus.BAD_REQUEST),
        ("-1", HTTPStatus.BAD_REQUEST),
        (None, HTTPStatus.LENGTH_REQUIRED),
    ],
)
def test_invalid_content_lengths_close_connection_and_next_connection_is_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw_length: str | None,
    expected_status: HTTPStatus,
) -> None:
    monkeypatch.setattr(socket, "create_connection", _ORIGINAL_CREATE_CONNECTION)
    manager = JobManager(tmp_path / "jobs")
    server = ProductionWebServer(("127.0.0.1", 0), manager)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.putrequest("POST", "/api/jobs")
        connection.putheader("Content-Type", "application/json")
        if raw_length is not None:
            connection.putheader("Content-Length", raw_length)
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == expected_status
        assert response.getheader("Connection") == "close"
        error = json.loads(response.read())
        assert error["error"]["code"] in {
            "INVALID_CONTENT_LENGTH",
            "CONTENT_LENGTH_REQUIRED",
            "REQUEST_SIZE_INVALID",
        }
        connection.close()

        status, health, _ = _http(f"http://127.0.0.1:{server.server_port}", "/api/health")
        assert status == HTTPStatus.OK
        assert isinstance(health, dict)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_malformed_json_and_unknown_route_preserve_server_availability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(socket, "create_connection", _ORIGINAL_CREATE_CONNECTION)
    manager = JobManager(tmp_path / "jobs")
    server = ProductionWebServer(("127.0.0.1", 0), manager)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        request = Request(
            base + "/api/jobs",
            data=b"{not-json",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(HTTPError) as caught:
            urlopen(request, timeout=5)
        assert caught.value.code == HTTPStatus.BAD_REQUEST
        assert json.loads(caught.value.read())["error"]["code"] == "INVALID_JSON"

        status, payload, _ = _http(base, "/api/not-a-route")
        assert status == HTTPStatus.NOT_FOUND
        assert isinstance(payload, dict)
        assert payload["error"]["code"] == "NOT_FOUND"
        assert _http(base, "/api/health")[0] == HTTPStatus.OK
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_shutdown_cancels_queued_jobs_and_rejects_new_submissions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELLA_DISABLE_STOCK_FALLBACK", "queued-original")
    manager = JobManager(tmp_path, autostart=False)
    first = manager.create(_request(content="First queued"))
    second = manager.create(_request(content="Second queued"))

    assert manager.close(timeout=0) is True
    assert manager.get(first.job_id).status is WebJobStatus.CANCELLED
    assert manager.get(second.job_id).status is WebJobStatus.CANCELLED
    first_error = manager.get(first.job_id).error
    assert first_error is not None
    assert first_error.code == "SERVER_SHUTDOWN_CANCELLED_JOB"
    with pytest.raises(RuntimeError, match="JOB_MANAGER_CLOSING"):
        manager.create(_request(content="Rejected"))
    assert manager.close(timeout=0) is True
    assert os.environ["TELLA_DISABLE_STOCK_FALLBACK"] == "queued-original"


def test_shutdown_without_jobs_and_server_incomplete_signal_are_truthful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = JobManager(tmp_path / "empty")
    assert empty.close(timeout=5) is True
    assert empty.close(timeout=0) is True

    manager = JobManager(tmp_path / "server")
    real_close = manager.close
    server = ProductionWebServer(("127.0.0.1", 0), manager)
    monkeypatch.setattr(manager, "close", lambda: False)
    with pytest.raises(RuntimeError, match="JOB_MANAGER_SHUTDOWN_INCOMPLETE"):
        server.server_close()
    assert real_close(timeout=5) is True


def test_shutdown_timeout_retains_worker_then_completes_after_release(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    async def runner(**kwargs: object) -> Path:
        started.set()
        await asyncio.to_thread(release.wait)
        output = Path(kwargs["out_root"]) / str(kwargs["job_id"]) / "video.mp4"
        output.write_bytes(b"fake mp4 for API boundary")
        _write_fake_tts_metadata(output.parent)
        return output

    manager = JobManager(tmp_path, runner=runner, media_probe=_fake_probe)
    job_id = manager.create(_request()).job_id
    assert started.wait(timeout=5)

    assert manager.close(timeout=0.01) is False
    assert manager._worker is not None
    assert manager._worker.is_alive()
    with pytest.raises(RuntimeError, match="JOB_MANAGER_CLOSING"):
        manager.create(_request(content="Rejected while closing"))

    release.set()
    assert manager.close(timeout=5) is True
    assert manager._worker is None
    assert manager.get(job_id).status is WebJobStatus.SUCCEEDED
    assert manager.close(timeout=0) is True


_REAL_E2E_READY = (
    os.environ.get("TELLA_WEB_REAL_E2E") == "1"
    and bool(
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GEMINI_API_KEYS")
        or os.environ.get("GOOGLE_API_KEY")
    )
    and shutil.which("ffmpeg") is not None
    and shutil.which("ffprobe") is not None
)


@pytest.mark.skipif(
    not _REAL_E2E_READY,
    reason="real web E2E requires TELLA_WEB_REAL_E2E=1, Gemini credentials, and tools",
)
def test_real_web_render_e2e(tmp_path: Path) -> None:
    manager = JobManager(tmp_path / "real-web-jobs")
    created = manager.create(
        WebRenderRequest(
            input_mode="topic",
            content="A tiny seed learns that patient growth is still progress.",
            language="en",
            theme="minimalist_symbolic_reel",
            media_source="ai_image",
            duration_mode="short",
            aspect_ratio="9:16",
            no_music=True,
        )
    )
    result = _wait(manager, created.job_id, timeout=20 * 60)
    try:
        assert result.status is WebJobStatus.SUCCEEDED, result.error
        assert result.audio_streams >= 1
        assert result.video_streams >= 1
        assert manager.artifact_path(created.job_id).stat().st_size > 0
    finally:
        manager.close()
