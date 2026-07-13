import asyncio
import json
import socket
from pathlib import Path

import pytest
from dotenv import load_dotenv

from tella import cli
from tella.planner.practical_life_steps import plan_practical_life_steps_from_script
from tella.production import (
    CALLIRRHOE_PRODUCTION_CONFIG,
    ProductionRun,
    ProductionStage,
    build_legacy_image_resume_attestation,
    dry_run_envelope,
    evaluate_resume,
    file_sha256,
    image_request_accounting,
)
from tella.atomic_write import atomic_write_json
from tella.recipes import apply_recipe_metadata, get_recipe
from tella.tts import gemini
from tella.voice_profiles import resolve_voice


SCRIPT_LINES = (
    "Bạn thường mất tập trung ngay khi bắt đầu một việc quan trọng.",
    "Chỉ một thông báo nhỏ cũng có thể kéo sự chú ý của bạn khỏi công việc đang làm.",
    "Trước khi bắt đầu, hãy viết rõ một việc cần hoàn thành trong hai mươi phút.",
    "Đặt điện thoại ngoài tầm tay, ở nơi bạn không thể với tới khi vẫn ngồi tại bàn.",
    "Chuẩn bị sẵn tài liệu và dụng cụ cần dùng để không phải dừng lại tìm kiếm.",
    "Đừng vừa làm vừa kiểm tra điện thoại, vì mỗi lần chuyển chú ý sẽ khiến bạn khó tập trung trở lại.",
    "Hôm nay, hãy chọn một việc quan trọng, cất điện thoại ra xa và tập trung trọn vẹn trong hai mươi phút.",
)
SCRIPT_HASH = "041de27b2d041305751fca5c8032ba050a316b8d421386ac7d6fd8ea7984ecf9"
ROLES = (
    "hook", "context", "step_1", "step_2", "step_3",
    "common_mistake", "today_action",
)


def _identity() -> dict:
    return {
        "acceptance_suite_id": "practical_life_steps_visual_v1",
        "acceptance_suite_path": "configs/acceptance/practical_life_steps_visual_v1.json",
        "acceptance_case_id": "phone_out_of_reach",
        "expected_recipe_id": "practical_life_steps_callirrhoe_v1",
        "expected_recipe_version": 1,
        "expected_scene_count": 7,
        "expected_scene_roles": list(ROLES),
        "script_version": 1,
        "script_path": "configs/acceptance/scripts/phone_out_of_reach_v1.txt",
        "canonical_script_sha256": SCRIPT_HASH,
        "script_source": "human_reviewed",
        "script_scene_count": 7,
        "canonical_script_sentences": list(SCRIPT_LINES),
    }


def _image_job(job: Path, *, legacy_manifest: bool = False):
    identity = _identity()
    run = ProductionRun(
        job, CALLIRRHOE_PRODUCTION_CONFIG, script_identity=identity
    )
    plan = plan_practical_life_steps_from_script(
        user_script="\n".join(SCRIPT_LINES), target_lang="vi",
        preserve_narration=True,
    )
    apply_recipe_metadata(
        plan, get_recipe("practical_life_steps_callirrhoe_v1"),
        validation_status="validated",
    )
    plan.acceptance_suite_id = identity["acceptance_suite_id"]
    plan.acceptance_suite_path = identity["acceptance_suite_path"]
    plan.acceptance_case_id = identity["acceptance_case_id"]
    plan.source_script_version = identity["script_version"]
    plan.source_script_path = identity["script_path"]
    plan.source_script_scene_count = 7
    plan.canonical_script_sha256 = SCRIPT_HASH
    plan.ai_images_requested = 7
    plan.ai_images_generated = 7
    plan.image_request_budget_max = 7
    plan.image_request_budget_used_at_finish = 7
    images = []
    for scene in plan.scenes:
        image = job / "assets" / f"scene_{scene.scene_index:02d}.jpg"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(f"image-{scene.scene_index}".encode())
        images.append(image)
        scene.asset_path = image.relative_to(job).as_posix()
        scene.image_filenames = [scene.asset_path]
        scene.asset_status = "done"
        scene.asset_hash = file_sha256(image)[:16]
        scene.image_source = "ai_image_provider"
        scene.image_provider = "cloudflare"
        scene.provider_request_count_for_scene = 1
        scene.actual_cloudflare_request_count_for_scene = 1
        scene.ai_images_requested = 1
        scene.ai_images_generated = 1
        scene.image_request_budget_max = 7
        scene.image_request_budget_used_at_finish = 7
    plan_path = job / "plan.json"
    plan_path.write_text(plan.model_dump_json(), encoding="utf-8")
    run.record_image_stage(plan, plan_path)
    run.advance(ProductionStage.images_ready)
    if legacy_manifest:
        manifest = json.loads(run.manifest_path.read_text(encoding="utf-8"))
        manifest.pop("artifact_hashes", None)
        manifest.pop("image_artifacts", None)
        manifest.pop("external_submission_counts", None)
        manifest.pop("external_transport_attempt_counts", None)
        manifest.pop("provider_result_counts", None)
        run.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return run, plan, plan_path, images, identity


def _attestation(job: Path) -> Path:
    path = job.parent / f"{job.name}_resume_attestation_v1.json"
    atomic_write_json(
        path,
        build_legacy_image_resume_attestation(
            job, CALLIRRHOE_PRODUCTION_CONFIG
        ),
    )
    return path


def test_image_counts_survive_later_tts_failure_and_manifest_agrees(tmp_path):
    run, _, _, images, _ = _image_job(tmp_path)
    run.record_submission("gemini", transport_attempts=1)
    run.record_provider_result("gemini", successful=False)
    run.fail("narration_ready", RuntimeError("400 INVALID_ARGUMENT API_KEY=secret"))
    summary_text = run.summary_path.read_text(encoding="utf-8")
    summary = json.loads(summary_text)
    manifest = json.loads(run.manifest_path.read_text(encoding="utf-8"))
    expected = {
        "gemini": 1, "edge": 0, "image_provider": 7,
        "retries": 0, "fallbacks": 0,
    }
    assert summary["external_submission_counts"] == expected
    assert manifest["external_submission_counts"] == expected
    assert summary["external_transport_attempt_counts"] == {
        "gemini": 1, "image_provider": 7,
    }
    assert manifest["external_transport_attempt_counts"] == summary[
        "external_transport_attempt_counts"
    ]
    assert summary["provider_result_counts"]["image_provider"] == {
        "successful": 7, "failed": 0,
    }
    assert summary["provider_result_counts"]["gemini"] == {
        "successful": 0, "failed": 1,
    }
    assert len(manifest["image_artifacts"]) == len(images) == 7
    assert summary["last_successful_stage"] == "images_ready"
    assert summary["status"] == "provider_failure"
    assert "secret" not in summary_text


def test_failed_image_transport_is_distinct_from_success(tmp_path):
    _, plan, _, _, _ = _image_job(tmp_path)
    first = plan.scenes[0]
    first.provider_request_count_for_scene = 2
    first.actual_cloudflare_request_count_for_scene = 2
    accounting = image_request_accounting(plan)
    assert accounting == {
        "submissions": 8,
        "transport_attempts": 8,
        "successful": 7,
        "failed": 1,
    }


def test_resume_reconstructs_seven_images_and_one_future_gemini_request(
    tmp_path, monkeypatch
):
    forbidden = lambda *a, **k: pytest.fail("resume inspection used a socket")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    _, _, _, _, identity = _image_job(tmp_path, legacy_manifest=True)
    attestation = _attestation(tmp_path)
    decision = evaluate_resume(
        tmp_path, CALLIRRHOE_PRODUCTION_CONFIG, identity, attestation
    )
    assert decision["compatible"] is True
    assert decision["artifacts"]["plan"]["valid"] is True
    assert decision["artifacts"]["images"]["valid"] is True
    assert decision["reusable_image_count"] == 7
    assert decision["estimated_image_requests"] == 0
    assert decision["estimated_gemini_requests"] == 1
    assert decision["maximum_gemini_sdk_attempts"] == 1
    assert decision["application_retries"] == 0
    assert decision["fallbacks"] == 0
    assert decision["artifacts"]["raw_narration"]["valid"] is False
    envelope = dry_run_envelope(
        CALLIRRHOE_PRODUCTION_CONFIG, tmp_path,
        resume=True, script_identity=identity,
        resume_attestation_path=attestation,
    )
    assert envelope["estimated_requests_after_resume"] == {
        "gemini": 1, "images": 0,
    }
    assert envelope["maximum_gemini_sdk_attempts"] == 1
    assert envelope["edge_fallback"] == envelope["model_fallback"] == 0
    assert envelope["stock_fallback"] == envelope["local_placeholder_fallback"] == 0
    assert envelope["asr_calls"] == envelope["music_provider_calls"] == 0
    assert not (tmp_path / ".tella-job.lock").exists()


@pytest.mark.parametrize("change", ["missing", "content", "plan_hash"])
def test_legacy_image_evidence_fails_closed(tmp_path, change):
    _, plan, plan_path, images, identity = _image_job(
        tmp_path, legacy_manifest=True
    )
    if change == "missing":
        images[3].unlink()
    elif change == "content":
        images[3].write_bytes(b"changed")
    else:
        plan.scenes[3].asset_hash = "0" * 16
        plan_path.write_text(plan.model_dump_json(), encoding="utf-8")
    decision = evaluate_resume(
        tmp_path, CALLIRRHOE_PRODUCTION_CONFIG, identity
    )
    assert decision["artifacts"]["images"]["valid"] is False
    assert decision["estimated_image_requests"] == 7


def test_resume_hydrates_prior_counts_instead_of_resetting_them(tmp_path):
    run, _, _, _, identity = _image_job(tmp_path)
    run.record_submission("gemini", transport_attempts=1)
    run.record_provider_result("gemini", successful=False)
    run.fail("narration_ready", RuntimeError("400 INVALID_ARGUMENT"))
    resumed = ProductionRun(
        tmp_path, CALLIRRHOE_PRODUCTION_CONFIG,
        resume=True, script_identity=identity,
    )
    summary = json.loads(resumed.summary_path.read_text(encoding="utf-8"))
    assert summary["external_submission_counts"]["image_provider"] == 7
    assert summary["external_submission_counts"]["gemini"] == 1
    assert summary["external_transport_attempt_counts"] == {
        "gemini": 1, "image_provider": 7,
    }
    assert summary["current_invocation_submission_counts"] == {
        "gemini": 0, "edge": 0, "image_provider": 0,
        "retries": 0, "fallbacks": 0,
    }
    assert summary["current_invocation_remaining_budget"]["gemini"] == 1
    assert summary["current_invocation_remaining_budget"]["image_provider"] == 7


def test_resume_budget_is_per_invocation_while_history_is_cumulative(tmp_path):
    run, _, _, _, identity = _image_job(tmp_path)
    run.record_submission("gemini", transport_attempts=1)
    run.record_provider_result("gemini", successful=False)
    run.fail("narration_ready", RuntimeError("provider failed"))

    resumed = ProductionRun(
        tmp_path, CALLIRRHOE_PRODUCTION_CONFIG,
        resume=True, script_identity=identity,
    )
    assert resumed.counts["gemini"] == 1
    assert resumed.invocation_counts["gemini"] == 0
    assert resumed.remaining_invocation_budget()["gemini"] == 1
    assert resumed.counts["image_provider"] == 7
    assert resumed.invocation_counts["image_provider"] == 0

    resumed.record_submission("gemini", transport_attempts=1)
    resumed.record_provider_result("gemini", successful=True)
    assert resumed.counts["gemini"] == 2
    assert resumed.invocation_counts["gemini"] == 1
    assert resumed.remaining_invocation_budget()["gemini"] == 0
    with pytest.raises(RuntimeError, match="current invocation"):
        resumed.record_submission("gemini", transport_attempts=1)
    resumed.write_summary(
        "partial_failure", resumable=True, recommended="continue locally"
    )
    for path in (resumed.manifest_path, resumed.summary_path):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["external_submission_counts"]["gemini"] == 2
        assert payload["external_submission_counts"]["image_provider"] == 7
        assert payload["current_invocation_submission_counts"]["gemini"] == 1
        assert payload["current_invocation_submission_counts"]["image_provider"] == 0
        assert payload["current_invocation_remaining_budget"]["gemini"] == 0
        assert payload["current_invocation_transport_attempt_counts"]["gemini"] == 1


def test_resume_envelope_separates_history_from_new_budget(tmp_path):
    run, _, _, _, identity = _image_job(tmp_path)
    run.record_submission("gemini", transport_attempts=1)
    run.record_provider_result("gemini", successful=False)
    run.fail("narration_ready", RuntimeError("provider failed"))
    envelope = dry_run_envelope(
        CALLIRRHOE_PRODUCTION_CONFIG, tmp_path,
        resume=True, script_identity=identity,
    )
    assert envelope["cumulative_submission_counts"]["gemini"] == 1
    assert envelope["cumulative_submission_counts"]["image_provider"] == 7
    assert envelope["current_invocation_submission_counts"]["gemini"] == 0
    assert envelope["current_invocation_request_limits"]["gemini"] == 1
    assert envelope["current_invocation_remaining_budget"]["gemini"] == 1


def test_legacy_prefix_hashes_require_explicit_full_attestation(tmp_path):
    _, plan, _, _, identity = _image_job(tmp_path, legacy_manifest=True)
    assert {len(scene.asset_hash) for scene in plan.scenes} == {16}
    without = evaluate_resume(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG, identity)
    assert without["artifacts"]["images"]["valid"] is False
    attestation = _attestation(tmp_path)
    with_attestation = evaluate_resume(
        tmp_path, CALLIRRHOE_PRODUCTION_CONFIG, identity, attestation
    )
    assert with_attestation["artifacts"]["images"]["valid"] is True
    assert with_attestation["legacy_image_integrity"] == "full_sha256_attestation"
    data = json.loads(attestation.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["job_id"] == tmp_path.name
    assert len(data["images"]) == 7
    assert all(len(item["sha256"]) == 64 for item in data["images"])
    assert not any("key" in key.lower() or "token" in key.lower() for key in data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("job_id", "another-job"),
        ("recipe_fingerprint", "0" * 64),
        ("canonical_script_sha256", "0" * 64),
        ("plan_sha256", "0" * 64),
        ("source_inventory_sha256", "0" * 64),
    ],
)
def test_legacy_attestation_mismatch_fails_closed(tmp_path, field, value):
    _, _, _, _, identity = _image_job(tmp_path, legacy_manifest=True)
    attestation = _attestation(tmp_path)
    data = json.loads(attestation.read_text(encoding="utf-8"))
    data[field] = value
    atomic_write_json(attestation, data)
    decision = evaluate_resume(
        tmp_path, CALLIRRHOE_PRODUCTION_CONFIG, identity, attestation
    )
    assert decision["artifacts"]["images"]["valid"] is False
    assert decision["estimated_image_requests"] == 7


def test_resume_requires_all_seven_existing_images_before_any_provider(
    tmp_path, monkeypatch
):
    class StopBeforeProvider(RuntimeError):
        pass

    job = tmp_path / "job"
    _, _, plan_path, images, identity = _image_job(job, legacy_manifest=True)
    attestation = _attestation(job)
    original_plan = plan_path.read_bytes()
    original_images = {path: file_sha256(path) for path in images}
    forbidden = lambda *a, **k: pytest.fail("resume attempted a provider or socket")
    monkeypatch.setattr(socket, "create_connection", forbidden)

    async def inspect_fetch(plan, selected_job):
        assert selected_job == job
        assert (job / ".reuse_plan.json").read_bytes() == original_plan
        assert cli.os.environ["TELLA_IMAGES_FROM_JOB"] == str(job)
        assert cli.os.environ["TELLA_REUSE_ASSETS"] == "1"
        assert cli.os.environ["TELLA_REQUIRE_REUSED_SCENE_INDICES"] == (
            "1,2,3,4,5,6,7"
        )
        raise StopBeforeProvider("inspection stop")

    monkeypatch.setattr(cli, "fetch_assets", inspect_fetch)
    recipe = get_recipe("practical_life_steps_callirrhoe_v1")
    voice = resolve_voice(
        recipe_profile_id=recipe.voice_profile_id,
        narrative_mode=recipe.narrative_mode,
    )
    with pytest.raises(StopBeforeProvider, match="inspection stop"):
        asyncio.run(cli.run_pipeline(
            topic="phone focus", target_lang="vi",
            theme="practical_life_steps", media_source="ai_image",
            duration_mode="short", aspect_ratio="9:16",
            voice_pace_name=None, voice_rate_custom=None, voice_gender=None,
            out_root=tmp_path, job_id="job",
            user_script="\n".join(SCRIPT_LINES), max_ai_images=7,
            recipe=recipe, voice_resolution=voice,
            script_identity=identity, resume=True,
            resume_attestation_path=attestation,
        ))
    assert {path: file_sha256(path) for path in images} == original_images
    assert not (job / ".tella-job.lock").exists()


def test_process_gemini_key_wins_and_dotenv_does_not_overwrite(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("TELLA_GEMINI_PROCESS_CREDENTIAL_NAME", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", '  "process-value"  ')
    monkeypatch.setenv("GOOGLE_API_KEY", "older-google-value")
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "GEMINI_API_KEY=dotenv-value\nGOOGLE_API_KEY=dotenv-google-value\n",
        encoding="utf-8",
    )
    load_dotenv(dotenv, override=False)
    name, value = gemini.resolve_api_key_from_environment()
    assert name == "GEMINI_API_KEY"
    assert value == "process-value"
    assert gemini.credential_environment_name() == "GEMINI_API_KEY"


def test_official_tts_client_receives_explicit_selected_key(monkeypatch):
    monkeypatch.delenv("TELLA_GEMINI_PROCESS_CREDENTIAL_NAME", raising=False)
    selected = "process-selected-value"
    monkeypatch.setenv("GEMINI_API_KEY", selected)
    monkeypatch.setenv("GOOGLE_API_KEY", "older-google-value")
    captured = {}

    def fake_client(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("google.genai.Client", fake_client)
    gemini._official_client()
    assert captured["api_key"] == selected
    assert captured["http_options"].retry_options.attempts == 1


def test_google_key_is_used_only_when_gemini_is_absent(monkeypatch):
    monkeypatch.delenv("TELLA_GEMINI_PROCESS_CREDENTIAL_NAME", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "  'google-only'  ")
    assert gemini.resolve_api_key_from_environment() == (
        "GOOGLE_API_KEY", "google-only"
    )


def test_empty_normalized_credentials_are_rejected(monkeypatch):
    monkeypatch.delenv("TELLA_GEMINI_PROCESS_CREDENTIAL_NAME", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", '  ""  ')
    monkeypatch.setenv("GOOGLE_API_KEY", "  ''  ")
    assert gemini.resolve_api_key_from_environment() == ("", "")


def test_process_alias_preference_cannot_serialize_secret(monkeypatch, tmp_path):
    secret = "fresh-shell-google-secret"
    monkeypatch.setenv("GEMINI_API_KEY", "stale-dotenv-gemini")
    monkeypatch.setenv("GOOGLE_API_KEY", secret)
    monkeypatch.setenv(
        "TELLA_GEMINI_PROCESS_CREDENTIAL_NAME", "GOOGLE_API_KEY"
    )
    assert gemini.resolve_api_key_from_environment() == (
        "GOOGLE_API_KEY", secret
    )
    run = ProductionRun(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)
    run.fail("narration_ready", RuntimeError(f"Bearer {secret}"))
    serialized = (
        run.manifest_path.read_text(encoding="utf-8")
        + run.summary_path.read_text(encoding="utf-8")
    )
    assert secret not in serialized
    assert "Bearer" not in serialized
