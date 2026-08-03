import hashlib
import json
import socket
from pathlib import Path

import pytest

from tella.atomic_write import atomic_write_json
from tella.production import (
    CALLIRRHOE_PRODUCTION_CONFIG,
    ProductionRun,
    file_sha256,
    repair_completed_job_metadata,
    validate_completed_job_integrity,
)


def _artifact_paths(job: Path) -> dict[str, Path]:
    return {
        "raw_narration": job / "assets/narration_raw.wav",
        "normalized_narration": job / "assets/narration.wav",
        "alignment": job / "alignment_metadata.json",
        "alignment_boundaries": job / "alignment_boundaries.json",
        "tts_metadata": job / "tts_metadata.json",
        "recipe": job / "recipe.json",
        "music_metadata": job / "music_metadata.json",
        "audio_qc": job / "audio_qc.json",
        "prepared_music": job / "_render/music_prepared.wav",
        "silent_video": job / "_render/silent_video.mp4",
        "final_video": job / "video.mp4",
        "video_qc": job / "video_qc.json",
    }


def _complete_job(job: Path, *, resume: bool) -> tuple[ProductionRun, dict[str, Path]]:
    if resume:
        original = ProductionRun(job, CALLIRRHOE_PRODUCTION_CONFIG)
        original.counts.update({"gemini": 4, "image_provider": 7})
        original.transport_attempts.update({"gemini": 4, "image_provider": 7})
        original.provider_results["gemini"] = {"successful": 1, "failed": 3}
        original.provider_results["image_provider"] = {"successful": 7, "failed": 0}
        original.write_summary(
            "partial_failure", resumable=True, recommended="resume locally"
        )
    run = ProductionRun(
        job,
        CALLIRRHOE_PRODUCTION_CONFIG,
        resume=resume,
        max_tts_requests=0 if resume else 1,
        max_image_requests=0 if resume else 7,
    )
    paths = _artifact_paths(job)
    for key, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if key == "audio_qc":
            atomic_write_json(path, {"status": "passed"})
        elif key == "video_qc":
            atomic_write_json(path, {"status": "passed"})
        elif key == "music_metadata":
            atomic_write_json(path, {
                "selected_track": CALLIRRHOE_PRODUCTION_CONFIG.music_track,
                "music_profile_id": CALLIRRHOE_PRODUCTION_CONFIG.music_profile,
                "qc_result": "passed",
            })
        else:
            path.write_bytes(f"stable-{key}".encode())
    atomic_write_json(paths["alignment"], {
        "wav_sha256": file_sha256(paths["normalized_narration"]),
    })
    plan_path = job / "plan.json"
    images = []
    for index in range(1, 8):
        image = job / "assets" / f"scene_{index:02d}.jpg"
        image.write_bytes(f"image-{index}".encode())
        images.append(image)
    run.finalize_completed(
        plan_path=plan_path,
        plan_data={"final_plan": True, "duration": 34.84},
        artifacts=paths,
        image_artifacts=images,
        qc_results={"audio": "passed", "video": "passed"},
    )
    return run, paths


@pytest.mark.parametrize("resume", [False, True])
def test_finalization_records_stable_plan_and_completion_origin(tmp_path, resume):
    job = tmp_path / "job"
    _, _ = _complete_job(job, resume=resume)
    manifest = json.loads((job / "production_manifest.json").read_text())
    summary = json.loads((job / "production_summary.json").read_text())
    actual = file_sha256(job / "plan.json")
    assert manifest["artifact_hashes"]["plan"] == actual
    assert manifest["completion_state"] == summary["status"] == "completed"
    assert manifest["resume_requested"] is summary["resume_requested"] is resume
    assert manifest["completed_from_resume"] is summary["completed_from_resume"] is resume
    assert summary["current_stage"] == summary["last_successful_stage"] == "completed"


def test_no_artifact_changes_after_final_hashes_are_recorded(tmp_path):
    job = tmp_path / "job"
    _, paths = _complete_job(job, resume=True)
    manifest = json.loads((job / "production_manifest.json").read_text())
    for key, path in {"plan": job / "plan.json", **paths}.items():
        assert manifest["artifact_hashes"][key] == file_sha256(path)
    assert validate_completed_job_integrity(
        job, CALLIRRHOE_PRODUCTION_CONFIG
    )["valid"] is True


def test_stale_plan_hash_fails_completed_validation(tmp_path):
    job = tmp_path / "job"
    _complete_job(job, resume=False)
    (job / "plan.json").write_text('{"mutated":true}')
    result = validate_completed_job_integrity(job, CALLIRRHOE_PRODUCTION_CONFIG)
    assert result["valid"] is False
    assert "completed artifact hash mismatch: plan" in result["errors"]


def test_zero_request_resumed_finalization_preserves_cumulative_accounting(tmp_path):
    job = tmp_path / "job"
    run, _ = _complete_job(job, resume=True)
    manifest = json.loads(run.manifest_path.read_text())
    assert manifest["current_invocation_submission_counts"]["gemini"] == 0
    assert manifest["current_invocation_transport_attempt_counts"]["gemini"] == 0
    assert manifest["current_invocation_submission_counts"]["image_provider"] == 0
    assert manifest["external_submission_counts"]["gemini"] == 4
    assert manifest["external_transport_attempt_counts"]["gemini"] == 4
    assert manifest["external_submission_counts"]["image_provider"] == 7
    assert manifest["external_transport_attempt_counts"]["image_provider"] == 7


def test_metadata_only_repair_is_local_and_requires_full_validation(
    tmp_path, monkeypatch
):
    job = tmp_path / "job"
    _complete_job(job, resume=True)
    manifest_path = job / "production_manifest.json"
    summary_path = job / "production_summary.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifact_hashes"]["plan"] = "0" * 64
    manifest["resume_requested"] = False
    manifest["completed_from_resume"] = False
    atomic_write_json(manifest_path, manifest)
    summary = json.loads(summary_path.read_text())
    summary["resume_requested"] = False
    summary["completed_from_resume"] = False
    atomic_write_json(summary_path, summary)

    def forbidden(*args, **kwargs):
        pytest.fail("metadata repair attempted provider, socket, or media access")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr("tella.tts.gemini._official_client", forbidden)
    before = {
        key: file_sha256(path)
        for key, path in _artifact_paths(job).items()
    }
    result = repair_completed_job_metadata(
        job, CALLIRRHOE_PRODUCTION_CONFIG, completed_from_resume=True
    )
    assert result["valid"] is True
    assert result["completed_from_resume"] is True
    assert before == {
        key: file_sha256(path)
        for key, path in _artifact_paths(job).items()
    }

    (job / "video.mp4").unlink()
    with pytest.raises(RuntimeError, match="completed artifacts missing"):
        repair_completed_job_metadata(
            job, CALLIRRHOE_PRODUCTION_CONFIG, completed_from_resume=True
        )
