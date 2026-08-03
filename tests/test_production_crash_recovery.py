import hashlib
import json
import socket
from pathlib import Path

import pytest

import tella.atomic_write as atomic
from tella.media.fetch import _CloudflareRequestBudget
from tella.planner.practical_life_steps import plan_practical_life_steps_from_script
from tella.production import (
    CALLIRRHOE_PRODUCTION_CONFIG,
    ProductionRun,
    evaluate_resume,
)


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "configs"
    / "acceptance"
    / "scripts"
    / "phone_out_of_reach_v1.txt"
).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("crash recovery test attempted network access")

    monkeypatch.setattr(socket, "create_connection", forbidden)


def _plan(job: Path):
    plan = plan_practical_life_steps_from_script(
        user_script=SCRIPT, target_lang="vi", preserve_narration=True
    )
    plan.recipe_id = CALLIRRHOE_PRODUCTION_CONFIG.recipe_id
    plan.recipe_version = CALLIRRHOE_PRODUCTION_CONFIG.recipe_version
    atomic.atomic_write_json(job / "plan.json", plan.model_dump(mode="json"))
    return plan


@pytest.mark.asyncio
async def test_request_acquisition_is_persisted_before_transport_or_image(tmp_path):
    run = ProductionRun(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)
    plan = _plan(tmp_path)
    scene = plan.scenes[0]
    budget = _CloudflareRequestBudget(plan, plan.scenes, 7, tmp_path)

    await budget.acquire(scene, "local test prompt", "initial")
    persisted = json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))
    assert persisted["ai_images_requested"] == 1
    assert persisted["scenes"][0]["provider_request_count_for_scene"] == 1
    assert persisted["scenes"][0]["actual_cloudflare_request_count_for_scene"] == 1
    assert not (tmp_path / "assets" / "scene_01.jpg").exists()

    resumed = ProductionRun(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG, resume=True)
    assert resumed.counts["image_provider"] == 1
    assert resumed.transport_attempts["image_provider"] == 1
    assert resumed.invocation_counts["image_provider"] == 0
    decision = evaluate_resume(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)
    assert decision["artifacts"]["images"]["valid"] is False
    assert decision["estimated_image_requests"] == 7
    assert run.counts["image_provider"] == 0


def test_success_counter_without_file_is_never_reusable(tmp_path):
    run = ProductionRun(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)
    plan = _plan(tmp_path)
    scene = plan.scenes[0]
    scene.provider_request_count_for_scene = 1
    scene.actual_cloudflare_request_count_for_scene = 1
    scene.ai_images_generated = 1
    scene.asset_status = "done"
    scene.image_provider = "cloudflare"
    scene.asset_path = "assets/scene_01.jpg"
    scene.asset_hash = "0" * 16
    atomic.atomic_write_json(tmp_path / "plan.json", plan.model_dump(mode="json"))
    run.record_image_stage(plan, tmp_path / "plan.json")

    decision = evaluate_resume(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)
    assert decision["artifacts"]["images"]["valid"] is False
    assert json.loads(run.manifest_path.read_text(encoding="utf-8"))["image_artifacts"] == []


def test_image_written_before_plan_or_manifest_update_is_not_reusable(tmp_path):
    run = ProductionRun(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)
    _plan(tmp_path)
    image = tmp_path / "assets" / "scene_01.jpg"
    atomic.atomic_write_bytes(image, b"complete-but-unrecorded-image")

    manifest = json.loads(run.manifest_path.read_text(encoding="utf-8"))
    assert not manifest.get("image_artifacts")
    decision = evaluate_resume(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)
    assert decision["artifacts"]["images"]["valid"] is False
    assert decision["estimated_image_requests"] == 7


def test_atomic_image_replace_failure_preserves_previous_file(tmp_path, monkeypatch):
    destination = tmp_path / "scene.jpg"
    destination.write_bytes(b"previous-valid-image")
    monkeypatch.setattr(
        atomic.os,
        "replace",
        lambda *args: (_ for _ in ()).throw(OSError("simulated crash")),
    )
    with pytest.raises(OSError, match="simulated crash"):
        atomic.atomic_write_bytes(destination, b"partial-new-image")
    assert destination.read_bytes() == b"previous-valid-image"
    assert not list(tmp_path.glob(".scene.jpg.*.tmp"))


def test_newer_plan_or_summary_counter_wins_without_double_count(tmp_path):
    run = ProductionRun(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)
    plan = _plan(tmp_path)
    scene = plan.scenes[0]
    scene.provider_request_count_for_scene = 1
    scene.actual_cloudflare_request_count_for_scene = 1
    atomic.atomic_write_json(tmp_path / "plan.json", plan.model_dump(mode="json"))

    summary = json.loads(run.summary_path.read_text(encoding="utf-8"))
    summary["external_submission_counts"]["gemini"] = 2
    summary["external_transport_attempt_counts"]["gemini"] = 2
    atomic.atomic_write_json(run.summary_path, summary)
    manifest = json.loads(run.manifest_path.read_text(encoding="utf-8"))
    manifest["external_submission_counts"]["gemini"] = 1
    manifest["external_transport_attempt_counts"]["gemini"] = 1
    atomic.atomic_write_json(run.manifest_path, manifest)

    resumed = ProductionRun(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG, resume=True)
    assert resumed.counts["gemini"] == 2
    assert resumed.transport_attempts["gemini"] == 2
    assert resumed.counts["image_provider"] == 1
    assert resumed.invocation_counts["gemini"] == 0
    assert resumed.invocation_counts["image_provider"] == 0


def test_summary_persisted_before_stage_transition_keeps_counts_not_stage(tmp_path):
    run = ProductionRun(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)
    plan = _plan(tmp_path)
    scene = plan.scenes[0]
    image = tmp_path / "assets" / "scene_01.jpg"
    atomic.atomic_write_bytes(image, b"valid-image")
    scene.asset_path = image.relative_to(tmp_path).as_posix()
    scene.asset_hash = hashlib.sha256(image.read_bytes()).hexdigest()[:16]
    scene.asset_status = "done"
    scene.image_provider = "cloudflare"
    scene.provider_request_count_for_scene = 1
    scene.actual_cloudflare_request_count_for_scene = 1
    scene.ai_images_generated = 1
    atomic.atomic_write_json(tmp_path / "plan.json", plan.model_dump(mode="json"))
    run.record_image_stage(plan, tmp_path / "plan.json")
    run.write_summary(
        "partial_failure", resumable=True, recommended="resume images stage"
    )

    summary = json.loads(run.summary_path.read_text(encoding="utf-8"))
    assert summary["last_successful_stage"] == ""
    assert summary["current_stage"] == "initialized"
    assert summary["external_submission_counts"]["image_provider"] == 1
    resumed = ProductionRun(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG, resume=True)
    assert resumed.counts["image_provider"] == 1
    assert resumed.invocation_counts["image_provider"] == 0


def test_malformed_legacy_accounting_does_not_grant_reuse(tmp_path):
    run = ProductionRun(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)
    manifest = json.loads(run.manifest_path.read_text(encoding="utf-8"))
    manifest["external_submission_counts"] = {
        "gemini": "not-a-number", "image_provider": {"bad": True}
    }
    atomic.atomic_write_json(run.manifest_path, manifest)
    resumed = ProductionRun(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG, resume=True)
    assert resumed.counts["gemini"] == 0
    assert resumed.counts["image_provider"] == 0
    assert evaluate_resume(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)[
        "artifacts"
    ]["images"]["valid"] is False
