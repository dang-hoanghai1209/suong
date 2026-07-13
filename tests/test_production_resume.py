import json
import wave
from pathlib import Path

import pytest

from tella.production import (
    CALLIRRHOE_PRODUCTION_CONFIG,
    LocalTTSCache,
    ProductionRun,
    ProductionStage,
    ProductionSummaryStatus,
    evaluate_resume,
    file_sha256,
    production_fingerprint,
    classify_error,
    tts_cache_key,
)


def _cache_key(**overrides):
    values = {
        "provider": "gemini", "model": "model-a", "voice": "Callirrhoe",
        "style": "natural", "language": "vi-VN",
        "canonical_narration_sha256": "text", "serialized_provider_input_sha256": "prompt",
        "request_format_version": "v1", "voice_registry_version": 1,
    }
    values.update(overrides)
    return tts_cache_key(**values)


def test_tts_cache_key_invalidates_every_request_identity_dimension():
    original = _cache_key()
    for field, changed in {
        "model": "model-b", "voice": "Leda", "style": "smile",
        "language": "en-US", "canonical_narration_sha256": "changed",
        "serialized_provider_input_sha256": "changed-prompt",
        "request_format_version": "v2", "voice_registry_version": 2,
    }.items():
        assert _cache_key(**{field: changed}) != original


def test_valid_local_cache_hit_reuses_raw_audio_without_credentials(tmp_path):
    raw = tmp_path / "raw.wav"
    with wave.open(str(raw), "wb") as wav:
        wav.setparams((1, 2, 24000, 240, "NONE", ""))
        wav.writeframes(b"\0\0" * 240)
    cache = LocalTTSCache(tmp_path / "cache")
    cache.store("abc", raw, {"voice": "Callirrhoe", "api_key": "secret"})
    copied = tmp_path / "copied.wav"
    assert cache.lookup("abc", copied)
    assert file_sha256(copied) == file_sha256(raw)
    metadata = (tmp_path / "cache" / "abc.json").read_text()
    assert "secret" not in metadata and "api_key" not in metadata
    evaluation = cache.evaluate("abc")
    assert evaluation["cache_hit"] is True
    assert evaluation["estimated_gemini_requests"] == 0
    assert evaluation["raw_audio_path"].endswith("abc.wav")
    assert cache.evaluate(_cache_key(voice="Leda"))["estimated_gemini_requests"] == 1
    assert cache.evaluate(_cache_key(model="model-b"))["estimated_gemini_requests"] == 1
    assert cache.evaluate(_cache_key(canonical_narration_sha256="changed"))["estimated_gemini_requests"] == 1


def test_resume_requires_recipe_and_artifact_hashes(tmp_path):
    job = tmp_path / "job"
    run = ProductionRun(job, CALLIRRHOE_PRODUCTION_CONFIG)
    plan = job / "plan.json"
    plan.write_text("{}")
    run.record_artifact_hashes({"plan": plan})
    decision = evaluate_resume(job, CALLIRRHOE_PRODUCTION_CONFIG)
    assert decision["compatible"] and decision["artifacts"]["plan"]["valid"]
    plan.write_text('{"stale":true}')
    assert not evaluate_resume(job, CALLIRRHOE_PRODUCTION_CONFIG)["artifacts"]["plan"]["valid"]


def test_new_production_manifest_serializes_canonical_neutral_rate(tmp_path):
    run = ProductionRun(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)
    manifest = json.loads(run.manifest_path.read_text(encoding="utf-8"))
    assert manifest["recipe"]["voice_rate"] == "+0%"
    assert manifest["recipe_fingerprint"] == production_fingerprint(
        CALLIRRHOE_PRODUCTION_CONFIG
    )
    assert evaluate_resume(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)[
        "fingerprint_compatibility"
    ] == "canonical"


def test_incompatible_recipe_fingerprint_stops_resume(tmp_path):
    run = ProductionRun(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)
    manifest = json.loads(run.manifest_path.read_text())
    manifest["recipe_fingerprint"] = "different-version"
    run.manifest_path.write_text(json.dumps(manifest))
    decision = evaluate_resume(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)
    assert not decision["compatible"]
    assert "recipe fingerprint mismatch" in decision["reasons"]


def _completed_legacy_neutral_job(job: Path) -> tuple[dict[str, Path], list[Path]]:
    run = ProductionRun(job, CALLIRRHOE_PRODUCTION_CONFIG)
    paths = {
        "plan": job / "plan.json",
        "raw_narration": job / "assets" / "narration_raw.wav",
        "normalized_narration": job / "assets" / "narration.wav",
        "alignment": job / "alignment_metadata.json",
        "mixed_audio": job / "assets" / "final_mixed_audio.m4a",
        "silent_video": job / "_render" / "silent_video.mp4",
        "final_video": job / "video.mp4",
    }
    for index, path in enumerate(paths.values()):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"legacy-artifact-{index}".encode())
    images = []
    for index in range(7):
        path = job / "assets" / f"scene_{index + 1}.jpg"
        path.write_bytes(f"legacy-image-{index}".encode())
        images.append(path)
    run.record_artifact_hashes(
        paths,
        image_artifacts=images,
        qc_results={"audio": "passed", "video": "passed"},
    )
    manifest = json.loads(run.manifest_path.read_text(encoding="utf-8"))
    manifest["recipe"]["voice_rate"] = "0%"
    manifest["recipe_fingerprint"] = (
        "575946730cd92f8fa0ab0367d4114ea65ecd38cdb97c4d4597b2c0c670d403d4"
    )
    run.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return paths, images


def test_legacy_neutral_rate_manifest_reuses_all_valid_completed_artifacts(tmp_path):
    _completed_legacy_neutral_job(tmp_path)
    decision = evaluate_resume(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)
    assert decision["compatible"] is True
    assert decision["fingerprint_compatibility"] == "legacy_neutral_rate_v1"
    assert decision["estimated_gemini_requests"] == 0
    assert decision["estimated_image_requests"] == 0
    assert decision["render_required"] is False
    assert decision["artifacts"]["raw_narration"]["valid"] is True
    assert decision["artifacts"]["final_video"]["valid"] is True


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("model", "different-model"),
        ("voice", "Leda"),
        ("music_track", "different-track"),
        ("track_offset_seconds", 9.0),
        ("max_image_requests", 6),
    ],
)
def test_legacy_neutral_fingerprint_does_not_mask_other_recipe_changes(
    tmp_path, field, changed
):
    _completed_legacy_neutral_job(tmp_path)
    changed_config = CALLIRRHOE_PRODUCTION_CONFIG.model_copy(update={field: changed})
    decision = evaluate_resume(tmp_path, changed_config)
    assert decision["compatible"] is False
    assert decision["fingerprint_compatibility"] == "manifest recipe configuration mismatch"


def test_legacy_compatibility_still_requires_narration_and_image_hashes(tmp_path):
    paths, images = _completed_legacy_neutral_job(tmp_path)
    paths["raw_narration"].write_bytes(b"changed narration")
    decision = evaluate_resume(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)
    assert decision["compatible"] is True
    assert decision["estimated_gemini_requests"] == 1
    assert decision["render_required"] is True
    paths, images = _completed_legacy_neutral_job(tmp_path / "images")
    images[3].write_bytes(b"changed image")
    image_decision = evaluate_resume(tmp_path / "images", CALLIRRHOE_PRODUCTION_CONFIG)
    assert image_decision["compatible"] is True
    assert image_decision["estimated_image_requests"] == 7
    assert image_decision["render_required"] is True


def test_invalid_image_hash_prevents_reuse(tmp_path):
    run = ProductionRun(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)
    images = []
    for index in range(7):
        path = tmp_path / f"scene_{index + 1}.jpg"
        path.write_bytes(bytes([index]))
        images.append(path)
    run.record_artifact_hashes({}, image_artifacts=images)
    images[3].write_bytes(b"changed")
    decision = evaluate_resume(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)
    assert not decision["artifacts"]["images"]["valid"]
    assert decision["estimated_image_requests"] == 7
    assert decision["artifacts"]["final_video"]["reason"] == "image stage invalid"


def test_valid_seven_images_need_zero_image_requests(tmp_path):
    run = ProductionRun(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)
    images = []
    for index in range(7):
        path = tmp_path / f"scene_{index + 1}.jpg"
        path.write_bytes(bytes([index]))
        images.append(path)
    run.record_artifact_hashes({}, image_artifacts=images)
    decision = evaluate_resume(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)
    assert decision["artifacts"]["images"]["valid"]
    assert decision["estimated_image_requests"] == 0


def test_completed_job_reuse_requires_hashes_and_both_qc_passes(tmp_path):
    run = ProductionRun(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)
    paths = {
        "plan": tmp_path / "plan.json",
        "raw_narration": tmp_path / "assets" / "narration_raw.wav",
        "normalized_narration": tmp_path / "assets" / "narration.wav",
        "alignment": tmp_path / "alignment_metadata.json",
        "mixed_audio": tmp_path / "assets" / "final_mixed_audio.m4a",
        "silent_video": tmp_path / "_render" / "silent_video.mp4",
        "final_video": tmp_path / "video.mp4",
    }
    for index, path in enumerate(paths.values()):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"artifact-{index}".encode())
    images = []
    for index in range(7):
        path = tmp_path / "assets" / f"scene_{index + 1}.jpg"
        path.write_bytes(bytes([index]))
        images.append(path)
    run.record_artifact_hashes(
        paths, image_artifacts=images,
        qc_results={"audio": "passed", "video": "passed"},
    )
    decision = evaluate_resume(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)
    assert decision["artifacts"]["final_video"]["valid"]
    assert decision["estimated_gemini_requests"] == 0
    assert decision["estimated_image_requests"] == 0
    assert decision["render_required"] is False
    manifest = json.loads(run.manifest_path.read_text())
    manifest["qc_results"]["audio"] = "failed"
    run.manifest_path.write_text(json.dumps(manifest))
    invalid = evaluate_resume(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)
    assert not invalid["artifacts"]["final_video"]["valid"]
    assert invalid["render_required"] is True


def test_completed_and_quota_failure_summaries_are_always_written(tmp_path):
    completed = ProductionRun(tmp_path / "ok", CALLIRRHOE_PRODUCTION_CONFIG)
    completed.advance(ProductionStage.completed)
    assert json.loads(completed.summary_path.read_text())["status"] == "completed"
    failed = ProductionRun(tmp_path / "failed", CALLIRRHOE_PRODUCTION_CONFIG)
    upstream = failed.job_dir / "plan.json"
    upstream.write_text("{}")
    failed.advance(ProductionStage.images_ready, {"plan": upstream})
    failed.counts["gemini"] = 1
    failed.fail("narration_ready", RuntimeError("429 RESOURCE_EXHAUSTED API_KEY=secret"))
    summary = json.loads(failed.summary_path.read_text())
    assert summary["status"] == "quota_failure"
    assert summary["external_submission_counts"]["gemini"] == 1
    assert summary["last_successful_stage"] == "images_ready"
    assert summary["resumable"] and upstream.is_file()
    assert "secret" not in summary["safe_error_message"]
    assert summary["failed_stage"] == "narration_ready"
    assert summary["external_submission_counts"]["retries"] == 0
    assert summary["external_submission_counts"]["fallbacks"] == 0


@pytest.mark.parametrize(("exc", "expected"), [
    (FileNotFoundError("missing"), "validation_failure"),
    (RuntimeError("400 INVALID_ARGUMENT"), "provider_failure"),
    (RuntimeError("429 RESOURCE_EXHAUSTED"), "quota_failure"),
    (RuntimeError("ffmpeg render failed"), "render_failure"),
    (RuntimeError("audio QC failed"), "qc_failure"),
    (KeyboardInterrupt(), "interrupted"),
])
def test_failure_summary_statuses_and_secret_redaction(tmp_path, exc, expected):
    run = ProductionRun(tmp_path / expected, CALLIRRHOE_PRODUCTION_CONFIG)
    run.fail("audit_stage", exc)
    summary_text = run.summary_path.read_text()
    summary = json.loads(summary_text)
    assert summary["status"] == expected
    assert summary["failed_stage"] == "audit_stage"
    assert "GEMINI_API_KEY" not in summary_text
    assert "Authorization" not in summary_text


def test_manifest_envelope_and_summary_never_serialize_secrets(tmp_path):
    run = ProductionRun(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)
    run.fail("TTS", RuntimeError("Authorization: Bearer secret GEMINI_API_KEY=secret"))
    combined = run.manifest_path.read_text() + run.summary_path.read_text()
    assert "Bearer secret" not in combined
    assert "GEMINI_API_KEY" not in combined


def test_partial_failure_preserves_valid_artifacts_and_distinguishes_issues(tmp_path, monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        pytest.fail("partial-failure serialization attempted external work")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    run = ProductionRun(tmp_path, CALLIRRHOE_PRODUCTION_CONFIG)
    plan = tmp_path / "plan.json"
    image = tmp_path / "assets" / "scene_1.jpg"
    image.parent.mkdir(parents=True)
    plan.write_text("{}")
    image.write_bytes(b"valid-image")
    run.advance(ProductionStage.images_ready, {"plan": plan, "image_1": image})
    run.record_artifact_issue(
        "narration_raw", tmp_path / "assets" / "narration_raw.wav",
        status="failed", reason="unexpected local pipeline failure",
    )
    run.record_artifact_issue(
        "alignment", tmp_path / "alignment_metadata.json",
        status="missing", reason="not reached",
    )
    run.counts.update({"gemini": 1, "retries": 0, "fallbacks": 0})
    run.fail("TTS", RuntimeError("unexpected failure Authorization: Bearer secret"))
    summary_text = run.summary_path.read_text()
    summary = json.loads(summary_text)
    assert summary["status"] == ProductionSummaryStatus.partial_failure.value
    assert summary["current_stage"] == "failed"
    assert summary["last_successful_stage"] == "images_ready"
    assert summary["failed_stage"] == "TTS"
    assert summary["error_category"] == "unexpected_failure"
    assert summary["preserved_artifact_paths"] == {
        "plan": str(plan), "image_1": str(image),
    }
    issues = summary["invalid_or_missing_artifact_paths"]
    assert issues["narration_raw"]["status"] == "failed"
    assert issues["alignment"]["status"] == "missing"
    assert summary["resumable"] is True
    assert summary["recommended_resume_action"].startswith("resume from TTS")
    assert summary["external_submission_counts"]["gemini"] == 1
    assert summary["external_submission_counts"]["retries"] == 0
    assert summary["external_submission_counts"]["fallbacks"] == 0
    assert "Bearer secret" not in summary_text
    assert "Authorization" not in summary_text
