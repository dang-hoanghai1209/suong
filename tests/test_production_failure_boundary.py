import asyncio
import json

import pytest

from tella import cli, production
from tella.production import (
    CALLIRRHOE_PRODUCTION_CONFIG,
    ProductionRun,
    ProductionStage,
)
from tella.recipes import get_recipe


@pytest.mark.parametrize(
    ("exc", "expected_status", "expected_category"),
    [
        (ValueError("invalid local planner configuration"), "validation_failure", "validation_failure"),
        (RuntimeError("400 INVALID_ARGUMENT"), "provider_failure", "invalid_request"),
        (RuntimeError("429 RESOURCE_EXHAUSTED"), "quota_failure", "quota_or_rate_limit"),
        (RuntimeError("ffmpeg render failed"), "render_failure", "render_failure"),
        (RuntimeError("audio QC failed"), "qc_failure", "qc_failure"),
        (RuntimeError("video QC failed"), "qc_failure", "qc_failure"),
        (KeyboardInterrupt(), "interrupted", "interrupted"),
        (SystemExit(2), "interrupted", "interrupted"),
    ],
)
def test_outer_failure_boundary_preserves_status_counts_artifacts_and_unlocks(
    tmp_path, monkeypatch, exc, expected_status, expected_category
):
    job = tmp_path / expected_status
    recipe = get_recipe("practical_life_steps_callirrhoe_v1")

    async def fail_after_initialized_run(**kwargs):
        run = ProductionRun(job, CALLIRRHOE_PRODUCTION_CONFIG)
        plan = job / "plan.json"
        plan.write_text("{}", encoding="utf-8")
        run.counts.update({"gemini": 1, "image_provider": 2})
        run.advance(ProductionStage.planned, {"plan": plan})
        raise exc

    monkeypatch.setattr(cli, "_run_pipeline_unlocked", fail_after_initialized_run)
    expected_exception = type(exc)
    with pytest.raises(expected_exception):
        asyncio.run(cli.run_pipeline(
            topic="local test",
            out_root=tmp_path,
            job_id=job.name,
            recipe=recipe,
        ))

    summary = json.loads((job / "production_summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == expected_status
    assert summary["error_category"] == expected_category
    assert summary["failed_stage"] == "images_ready"
    assert summary["last_successful_stage"] == "planned"
    assert summary["external_submission_counts"]["gemini"] == 1
    assert summary["external_submission_counts"]["image_provider"] == 2
    assert summary["preserved_artifact_paths"]["plan"] == str(job / "plan.json")
    assert not (job / ".tella-job.lock").exists()


def test_outer_boundary_does_not_overwrite_specific_inner_failure(tmp_path, monkeypatch):
    job = tmp_path / "specific"
    recipe = get_recipe("practical_life_steps_callirrhoe_v1")
    summary_writes = 0
    original_atomic_write = production.atomic_write_json

    def count_summary_writes(path, payload):
        nonlocal summary_writes
        if path.name == "production_summary.json":
            summary_writes += 1
        return original_atomic_write(path, payload)

    async def inner_failure(**kwargs):
        run = ProductionRun(job, CALLIRRHOE_PRODUCTION_CONFIG)
        plan = job / "plan.json"
        plan.write_text("{}", encoding="utf-8")
        run.advance(ProductionStage.planned, {"plan": plan})
        run.counts["gemini"] = 1
        exc = RuntimeError("429 RESOURCE_EXHAUSTED")
        run.fail("narration_ready", exc)
        raise exc

    monkeypatch.setattr(production, "atomic_write_json", count_summary_writes)
    monkeypatch.setattr(cli, "_run_pipeline_unlocked", inner_failure)
    with pytest.raises(RuntimeError, match="429"):
        asyncio.run(cli.run_pipeline(
            topic="local test", out_root=tmp_path, job_id=job.name, recipe=recipe
        ))

    summary = json.loads((job / "production_summary.json").read_text(encoding="utf-8"))
    assert summary_writes == 3
    assert summary["status"] == "quota_failure"
    assert summary["failed_stage"] == "narration_ready"
    assert summary["external_submission_counts"]["gemini"] == 1
    assert summary["preserved_artifact_paths"]["plan"] == str(job / "plan.json")
    assert not (job / ".tella-job.lock").exists()


@pytest.mark.parametrize(
    "name",
    ["GEMINI_API_KEY", "GOOGLE_API_KEY", "CF_AI_TOKEN", "AUTHORIZATION"],
)
def test_known_credential_values_are_redacted(tmp_path, monkeypatch, name):
    secret = f"secret-value-for-{name.lower()}"
    monkeypatch.setenv(name, secret)
    run = ProductionRun(tmp_path / name.lower(), CALLIRRHOE_PRODUCTION_CONFIG)
    run.fail("planned", ValueError(f"invalid configuration {secret}"))
    summary_text = run.summary_path.read_text(encoding="utf-8")
    assert secret not in summary_text


@pytest.mark.parametrize(
    "message",
    [
        "SHA256 74E2E29C42C05E872A89C8C6CE3B726A9E9A90247E2BF7573261857BA63B57C8 mismatch",
        "job phone_focus_source_02 is invalid",
        "recipe practical_life_steps_callirrhoe_v1 is invalid",
        r"safe path D:\tella\out\job is missing",
    ],
)
def test_safe_failure_details_are_not_over_redacted(tmp_path, message):
    run = ProductionRun(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)
    run.fail("planned", ValueError(message))
    summary = json.loads(run.summary_path.read_text(encoding="utf-8"))
    assert summary["safe_error_message"] == message
