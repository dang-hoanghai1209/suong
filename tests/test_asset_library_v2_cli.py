from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import tella.cli as cli
from tella.asset_library.production_mvp import BASE_SEED, build_seven_scene_plan, scene_seed
from tella.media.fetch import fetch_assets
from tella.planner.models import TellaScenePlan

ROOT = Path(r"D:\tella-assets-staging\mvp_v1_processed_v2")
SEMANTICS = Path(r"D:\tella-production-resolver\scripts\asset_batch\asset_semantics_patch.json")


def _pipeline_kwargs(tmp_path: Path) -> dict:
    return {
        "topic": "emotional healing",
        "target_lang": "vi",
        "theme": "minimalist_emotional",
        "media_source": "ai_image",
        "duration_mode": "short",
        "aspect_ratio": "9:16",
        "voice_pace_name": None,
        "voice_rate_custom": None,
        "voice_gender": "female",
        "out_root": tmp_path,
        "job_id": "cli-routing",
        "dry_run_plan": True,
    }


def test_enabled_cli_job_routes_to_deterministic_seven_scene_plan(monkeypatch, tmp_path):
    monkeypatch.setenv("TELLA_ASSET_LIBRARY_V2", "1")

    async def planner_must_not_run(*args, **kwargs):
        raise AssertionError("Gemini planner must not run in Asset-library V2 mode")

    async def translator_must_not_run(*args, **kwargs):
        raise AssertionError("Gemini translation must not run in Asset-library V2 mode")

    monkeypatch.setattr(cli, "plan_story", planner_must_not_run)
    monkeypatch.setattr(cli, "translate_topic", translator_must_not_run)

    plan_path = asyncio.run(cli._run_pipeline_unlocked(**_pipeline_kwargs(tmp_path)))
    plan = TellaScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    assert len(plan.scenes) == 7
    assert [scene.asset_library_request["seed"] for scene in plan.scenes] == [
        scene_seed(BASE_SEED, index) for index in range(1, 8)
    ]
    assert all(scene.asset_library_request["base_seed"] == BASE_SEED for scene in plan.scenes)


def test_cli_main_v2_bypasses_gemini_credential_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("TELLA_ASSET_LIBRARY_V2", "1")
    captured = {}

    async def fake_run_pipeline(**kwargs):
        captured.update(kwargs)
        return tmp_path / "plan.json"

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)
    result = cli.main([
        "--topic", "healing",
        "--lang", "vi",
        "--theme", "minimalist_emotional",
        "--dry-run-plan",
        "--out", str(tmp_path),
    ])
    assert result == 0
    assert captured["topic"] == "healing"


def test_disabled_cli_job_preserves_legacy_planner_path(monkeypatch, tmp_path):
    monkeypatch.delenv("TELLA_ASSET_LIBRARY_V2", raising=False)
    calls = []

    async def translate(*args, **kwargs):
        calls.append("translate")
        return SimpleNamespace(
            translated_topic="translated healing",
            source_language_detected="vi",
            target_language="vi",
            needs_translation=False,
        )

    async def planner(*args, **kwargs):
        calls.append("planner")
        return build_seven_scene_plan(enabled=False)

    monkeypatch.setattr(cli, "translate_topic", translate)
    monkeypatch.setattr(cli, "plan_story", planner)
    plan_path = asyncio.run(cli._run_pipeline_unlocked(**_pipeline_kwargs(tmp_path)))
    assert plan_path.is_file()
    assert calls == ["translate", "planner"]


def test_v2_fetch_never_calls_external_ai_image_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("TELLA_ASSET_LIBRARY_V2", "1")
    monkeypatch.setenv("TELLA_ASSET_LIBRARY_ROOT", str(ROOT))
    monkeypatch.setenv("TELLA_ASSET_LIBRARY_SEMANTICS_PATH", str(SEMANTICS))
    from tella.media import fetch as fetch_module

    async def external_provider_called(*args, **kwargs):
        raise AssertionError("external AI image provider was called")

    monkeypatch.setattr(fetch_module.ai_image, "generate_image", external_provider_called)
    plan = build_seven_scene_plan(enabled=True)
    asyncio.run(fetch_assets(plan, tmp_path))
    assert len(list((tmp_path / "assets").glob("scene_*_asset_library.png"))) == 7


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        ("TELLA_ASSET_LIBRARY_ROOT", "missing-root", "Asset registry not found"),
        ("TELLA_ASSET_LIBRARY_SEMANTICS_PATH", "missing-semantics.json", "Semantic overlay not found"),
    ],
)
def test_v2_missing_dependencies_fail_clearly(monkeypatch, tmp_path, variable, value, message):
    monkeypatch.setenv("TELLA_ASSET_LIBRARY_V2", "1")
    monkeypatch.setenv("TELLA_ASSET_LIBRARY_ROOT", str(ROOT))
    monkeypatch.setenv("TELLA_ASSET_LIBRARY_SEMANTICS_PATH", str(SEMANTICS))
    monkeypatch.setenv(variable, str(tmp_path / value))
    with pytest.raises(FileNotFoundError, match=message):
        asyncio.run(fetch_assets(build_seven_scene_plan(enabled=True), tmp_path / "job"))
