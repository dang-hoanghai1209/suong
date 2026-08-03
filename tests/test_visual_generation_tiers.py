from __future__ import annotations

from pathlib import Path

from tella.visual_generation.cli import main
from tella.visual_generation.providers.cloudflare_flux import (
    DEV_MODEL,
    KLEIN_4B_MODEL,
)
from tella.visual_generation.tiers import VisualQualityTier, resolve_visual_tier

ROOT = Path(__file__).parents[1]
PLAN = ROOT / "configs" / "visual_quality" / "four_scene_proof_v1.json"
STYLE = ROOT / "configs" / "visual_quality" / "soft_emotional_reference_v1.json"


def _command(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "render-proof",
        "--plan",
        str(PLAN),
        "--style",
        str(STYLE),
        "--reference-root",
        str(tmp_path),
        "--out",
        str(tmp_path / "out"),
        "--job-id",
        "tier-test",
        "--scene",
        "scene_01",
        "--dry-run",
        *extra,
    ]


def test_tier_to_model_steps_and_timeout_resolution():
    draft = resolve_visual_tier(VisualQualityTier.DRAFT)
    acceptance = resolve_visual_tier(VisualQualityTier.ACCEPTANCE)

    assert (draft.provider, draft.model, draft.steps, draft.timeout_seconds) == (
        "cloudflare-flux",
        KLEIN_4B_MODEL,
        4,
        120.0,
    )
    assert (acceptance.provider, acceptance.model, acceptance.steps) == (
        "cloudflare-flux",
        DEV_MODEL,
        25,
    )
    assert acceptance.timeout_seconds == 300.0


def test_tier_rejects_provider_model_and_draft_step_mismatches():
    for overrides in (
        {"provider": "gemini"},
        {"model": DEV_MODEL},
        {"steps": 25},
    ):
        try:
            resolve_visual_tier("draft", **overrides)
        except ValueError as exc:
            assert "tier draft" in str(exc)
        else:
            raise AssertionError(f"draft tier accepted incoherent overrides: {overrides}")


def test_cli_dry_run_passes_resolved_draft_configuration(monkeypatch, tmp_path, capsys):
    captured = {}

    async def fake_render(**kwargs):
        captured.update(kwargs)
        return {"external_calls_made": 0}

    monkeypatch.setattr("tella.visual_generation.cli.render_proof", fake_render)
    assert main(_command(tmp_path, "--tier", "draft", "--seed", "27183")) == 0

    provider = captured["provider"]
    assert provider.model == KLEIN_4B_MODEL
    assert provider.steps == 4
    assert provider.timeout_seconds == 120.0
    assert captured["tier"] == "draft"
    assert captured["intended_usage_class"] == "draft"
    assert captured["chain_accepted_scenes"] is False
    output = capsys.readouterr().out
    assert '"selected_tier": "draft"' in output
    assert '"reference_count": {' in output


def test_cli_dry_run_passes_resolved_acceptance_configuration(monkeypatch, tmp_path):
    captured = {}

    async def fake_render(**kwargs):
        captured.update(kwargs)
        return {"external_calls_made": 0}

    monkeypatch.setattr("tella.visual_generation.cli.render_proof", fake_render)
    assert main(_command(tmp_path, "--tier", "acceptance")) == 0

    provider = captured["provider"]
    assert provider.model == DEV_MODEL
    assert provider.steps == 25
    assert provider.timeout_seconds == 300.0
    assert captured["tier"] == "acceptance"


def test_cli_invalid_tier_model_combination_fails_without_render(monkeypatch, tmp_path):
    called = False

    async def fake_render(**_kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr("tella.visual_generation.cli.render_proof", fake_render)
    result = main(_command(tmp_path, "--tier", "draft", "--model", DEV_MODEL))
    assert result == 2
    assert called is False


def test_cli_legacy_explicit_cloudflare_model_still_works(monkeypatch, tmp_path):
    captured = {}

    async def fake_render(**kwargs):
        captured.update(kwargs)
        return {"external_calls_made": 0}

    monkeypatch.setattr("tella.visual_generation.cli.render_proof", fake_render)
    assert main(
        _command(
            tmp_path,
            "--provider",
            "cloudflare-flux",
            "--model",
            KLEIN_4B_MODEL,
            "--steps",
            "4",
        )
    ) == 0
    assert captured["provider"].model == KLEIN_4B_MODEL
    assert captured["tier"] is None
