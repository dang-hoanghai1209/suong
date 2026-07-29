from __future__ import annotations

from copy import deepcopy
import http.client
import json
from pathlib import Path
import subprocess
import threading
import time

import pytest
from pydantic import ValidationError

from tella.topic_production.renderer_stage import (
    LocalAuthorizedRenderer,
    RenderArtifactV1,
    RenderPackageV1,
    RendererStageStore,
    probe_mp4_artifact,
)
from tests.test_plan_only_local_host import _http_request, _json_request, _running_host
from tests.test_plan_only_narration_stage import _approve, _generate, _sealed_application


def _mp4_bytes() -> bytes:
    return b"\x00\x00\x00\x18ftypisom" + b"\0" * 256


def _probe(*, duration_ms: int = 35_000):
    return {
        "duration_ms": duration_ms,
        "width": 1080,
        "height": 1920,
        "video_codec": "h264",
        "audio_codec": "aac",
        "video_stream_count": 1,
        "audio_stream_count": 1,
    }


class FakeRenderer:
    def __init__(
        self,
        *,
        content: bytes | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.content = _mp4_bytes() if content is None else content
        self.failure = failure
        self.calls: list[dict[str, object]] = []

    def __call__(
        self,
        bridge,
        *,
        job_dir,
        narration_artifact_path,
        cancellation_requested,
    ):
        snapshot = bridge.require_builder_authorization()
        self.calls.append(
            {
                "bridge": snapshot,
                "job_dir": job_dir,
                "narration_artifact_path": narration_artifact_path,
                "cancellation_requested": cancellation_requested,
            }
        )
        if self.failure is not None:
            raise self.failure
        output = job_dir / "video.mp4"
        output.write_bytes(self.content)
        return output


class BlockingRenderer(FakeRenderer):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, *args, **kwargs):
        self.started.set()
        assert self.release.wait(timeout=5)
        return super().__call__(*args, **kwargs)


def _renderer_ready_application(
    tmp_path: Path,
    *,
    renderer: FakeRenderer | None = None,
    probe=None,
    configured: bool = True,
):
    application, tts, visual, run_id, package, timeline = _sealed_application(tmp_path)
    narration_approval = _approve(application, run_id, package)["approval"]
    narration_artifact = _generate(application, run_id, package, narration_approval)["artifact"]
    accepted = application.review_narration_audio(
        run_id,
        narration_artifact["artifact_id"],
        "accept",
        {
            "schema_version": 1,
            "artifact_revision_id": narration_artifact["artifact_revision_id"],
            "audio_sha256": narration_artifact["audio_sha256"],
            "explicit_confirmation": True,
        },
    )["artifact"]
    application._renderer_stage.close()
    fake = renderer or FakeRenderer()
    application._renderer_stage = RendererStageStore(
        renderer=fake,
        artifact_root=tmp_path / "renderer",
        probe=probe or (lambda _: _probe()),
        renderer_configured=configured,
    )
    return application, fake, tts, visual, run_id, package, timeline, accepted


def _approve_renderer(application, run_id):
    access = application.get_renderer_stage_access(run_id)
    authority = access["input_authority"]
    return application.approve_renderer_stage(
        run_id,
        {
            "schema_version": 1,
            "execution_package_id": authority["execution_package_id"],
            "execution_package_revision_id": authority["execution_package_revision_id"],
            "package_source_authority_sha256": authority["package_source_authority_sha256"],
            "narration_artifact_id": authority["narration_artifact_id"],
            "narration_artifact_revision_id": authority["narration_artifact_revision_id"],
            "narration_audio_sha256": authority["narration_audio_sha256"],
            "explicit_local_resource_acknowledgement": True,
            "explicit_confirmation": True,
            "note": "Approve one bounded local MP4 render.",
        },
    )["approval"]


def _create_render_package(application, run_id, approval):
    return application.create_render_package(
        run_id,
        {
            "schema_version": 1,
            "approval_id": approval["approval_id"],
            "approval_revision_id": approval["approval_revision_id"],
            "explicit_confirmation": True,
        },
    )["render_package"]


def _start(application, run_id, package):
    return application.start_render_job(
        run_id,
        {
            "schema_version": 1,
            "render_package_id": package["render_package_id"],
            "render_package_revision_id": package["render_package_revision_id"],
            "render_package_sha256": package["render_package_sha256"],
            "explicit_confirmation": True,
        },
    )["job"]


def _wait(application, run_id, job_id, *, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = application.get_render_job(run_id, job_id)
        if job["status"] not in {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}:
            return job
        time.sleep(0.01)
    raise AssertionError("render job did not finish")


def test_locked_access_and_configuration_are_fail_closed(tmp_path) -> None:
    application, _, _, _, run_id, _, _, _ = _renderer_ready_application(tmp_path, configured=False)
    access = application.get_renderer_stage_access(run_id)
    assert access["renderer_configuration"]["renderer_configured"] is False
    assert "RENDERER_NOT_CONFIGURED" in access["blocker_codes"]
    assert access["renderer_stage_approval_capability"] is False
    assert access["full_render_enabled"] is False
    assert access["final_media_capability"] is False
    application.close()


def test_approval_and_render_package_bind_complete_authority(tmp_path) -> None:
    application, _, _, _, run_id, package, timeline, accepted = _renderer_ready_application(
        tmp_path
    )
    access = application.get_renderer_stage_access(run_id)
    authority = access["input_authority"]
    assert authority["execution_package_id"] == package["package_id"]
    assert authority["narration_artifact_id"] == accepted["artifact_id"]
    assert authority["accepted_timeline_revision_id"] == timeline["collection_revision_id"]
    assert len(authority["scene_ids"]) == len(authority["visual_artifact_sha256s"]) == 8
    approval = _approve_renderer(application, run_id)
    assert approval["purpose"] == "BOUNDED_MP4_RENDER_EXECUTION"
    assert approval["renderer_configuration"]["shell_allowed"] is False
    render_package = _create_render_package(application, run_id, approval)
    assert RenderPackageV1.model_validate_json(json.dumps(render_package))
    tampered = deepcopy(render_package)
    tampered["input_authority"]["narration_audio_sha256"] = "0" * 64
    with pytest.raises(ValidationError):
        RenderPackageV1.model_validate_json(json.dumps(tampered))
    assert "path" not in json.dumps(render_package).lower()
    application.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("command", "ffmpeg -i arbitrary"),
        ("input_path", "C:/secret.wav"),
        ("output_path", "C:/secret.mp4"),
        ("filename", "chosen.mp4"),
        ("filter_graph", "movie=http://example.invalid"),
        ("provider_url", "https://example.invalid"),
        ("api_key", "secret"),
    ],
)
def test_browser_render_requests_reject_operational_fields(tmp_path, field, value) -> None:
    application, _, _, _, run_id, _, _, _ = _renderer_ready_application(tmp_path)
    authority = application.get_renderer_stage_access(run_id)["input_authority"]
    payload = {
        "schema_version": 1,
        "execution_package_id": authority["execution_package_id"],
        "execution_package_revision_id": authority["execution_package_revision_id"],
        "package_source_authority_sha256": authority["package_source_authority_sha256"],
        "narration_artifact_id": authority["narration_artifact_id"],
        "narration_artifact_revision_id": authority["narration_artifact_revision_id"],
        "narration_audio_sha256": authority["narration_audio_sha256"],
        "explicit_local_resource_acknowledgement": True,
        "explicit_confirmation": True,
        field: value,
    }
    assert application.approve_renderer_stage(run_id, payload)["error"]["code"] == (
        "RENDERER_STAGE_APPROVAL_REQUIRED"
    )
    application.close()


def test_one_render_registers_valid_opaque_artifact_and_duplicate_is_blocked(
    tmp_path,
) -> None:
    application, renderer, _, visual, run_id, _, _, _ = _renderer_ready_application(tmp_path)
    approval = _approve_renderer(application, run_id)
    package = _create_render_package(application, run_id, approval)
    job = _start(application, run_id, package)
    duplicate = application.start_render_job(
        run_id,
        {
            "schema_version": 1,
            "render_package_id": package["render_package_id"],
            "render_package_revision_id": package["render_package_revision_id"],
            "render_package_sha256": package["render_package_sha256"],
            "explicit_confirmation": True,
        },
    )
    assert duplicate["error"]["code"] == "RENDER_ALREADY_IN_PROGRESS"
    completed = _wait(application, run_id, job["job_id"])
    assert completed["status"] == "SUCCEEDED_FOR_REVIEW"
    assert len(renderer.calls) == 1
    artifact = application.get_renderer_stage_access(run_id)["artifact"]
    assert RenderArtifactV1.model_validate_json(json.dumps(artifact))
    assert artifact["mime_type"] == "video/mp4"
    assert artifact["byte_length"] == len(_mp4_bytes())
    assert artifact["metadata"] == {"schema_version": 1, **_probe()}
    assert application.get_render_artifact_bytes(run_id, artifact["artifact_id"])[0] == _mp4_bytes()
    assert len(visual.calls) == 8
    review = application.review_render_artifact(
        run_id,
        artifact["artifact_id"],
        "accept-for-qc",
        {
            "schema_version": 1,
            "artifact_revision_id": artifact["artifact_revision_id"],
            "mp4_sha256": artifact["mp4_sha256"],
            "explicit_confirmation": True,
        },
    )["artifact"]
    assert review["render_artifact_accepted_for_qc"] is True
    assert review["eligible_for_final_media_qc_review"] is True
    assert review["final_media_capability"] is False
    application.close()


def test_cancellation_is_scoped_to_current_known_job(tmp_path) -> None:
    renderer = BlockingRenderer()
    application, _, _, _, run_id, _, _, _ = _renderer_ready_application(tmp_path, renderer=renderer)
    package = _create_render_package(application, run_id, _approve_renderer(application, run_id))
    job = _start(application, run_id, package)
    assert renderer.started.wait(timeout=5)
    cancelled = application.cancel_render_job(
        run_id,
        job["job_id"],
        {
            "schema_version": 1,
            "job_attempt_id": job["job_attempt_id"],
            "explicit_confirmation": True,
            "reason": "Stop this bounded render.",
        },
    )["job"]
    assert cancelled["status"] == "CANCEL_REQUESTED"
    renderer.release.set()
    completed = _wait(application, run_id, job["job_id"])
    assert completed["status"] == "CANCELLED"
    assert application.get_renderer_stage_access(run_id)["artifact"] is None
    assert not list((tmp_path / "renderer").glob("*.mp4"))
    application.close()


@pytest.mark.parametrize(
    "content,probe,failure_code",
    [
        (b"", _probe(), "MP4_ARTIFACT_EMPTY"),
        (b"not-an-mp4", _probe(), "MP4_SIGNATURE_INVALID"),
        (_mp4_bytes(), _probe(duration_ms=20_000), "MP4_DURATION_OUT_OF_POLICY"),
        (
            _mp4_bytes(),
            {**_probe(), "width": 720},
            "MP4_STREAM_POLICY_FAILED",
        ),
    ],
)
def test_invalid_renderer_outputs_fail_safely(tmp_path, content, probe, failure_code) -> None:
    application, _, _, _, run_id, _, _, _ = _renderer_ready_application(
        tmp_path,
        renderer=FakeRenderer(content=content),
        probe=lambda _: probe,
    )
    package = _create_render_package(application, run_id, _approve_renderer(application, run_id))
    job = _start(application, run_id, package)
    completed = _wait(application, run_id, job["job_id"])
    assert completed["status"] == "FAILED"
    assert completed["failure_code"] == failure_code
    assert application.get_renderer_stage_access(run_id)["artifact"] is None
    assert not list((tmp_path / "renderer").rglob("*.tmp"))
    application.close()


def test_renderer_failure_is_sanitized(tmp_path) -> None:
    application, _, _, _, run_id, _, _, _ = _renderer_ready_application(
        tmp_path,
        renderer=FakeRenderer(failure=RuntimeError("C:/secret provider-token")),
    )
    package = _create_render_package(application, run_id, _approve_renderer(application, run_id))
    job = _start(application, run_id, package)
    completed = _wait(application, run_id, job["job_id"])
    assert completed["failure_code"] == "RENDERER_FAILED"
    assert "secret" not in json.dumps(completed).lower()
    application.close()


def test_renderer_timeout_is_typed_and_leaves_no_partial_artifact(tmp_path) -> None:
    application, _, _, _, run_id, _, _, _ = _renderer_ready_application(
        tmp_path,
        renderer=FakeRenderer(failure=TimeoutError()),
    )
    package = _create_render_package(application, run_id, _approve_renderer(application, run_id))
    job = _start(application, run_id, package)
    completed = _wait(application, run_id, job["job_id"])
    assert completed["status"] == "FAILED"
    assert completed["failure_code"] == "RENDERER_TIMEOUT"
    assert application.get_renderer_stage_access(run_id)["artifact"] is None
    assert not list((tmp_path / "renderer").rglob("*.tmp"))
    application.close()


def test_local_renderer_transcodes_accepted_wav_to_fixed_aac_profile(monkeypatch, tmp_path) -> None:
    observed = {}

    async def fake_render(plan, job_dir, **kwargs):
        observed.update(kwargs)
        observed["narration_audio_filename"] = plan.narration_audio_filename
        output = job_dir / "video.mp4"
        output.write_bytes(_mp4_bytes())
        return output

    monkeypatch.setattr("tella.topic_production.renderer_stage.render", fake_render)
    application, _, _, _, run_id, _, _, _ = _renderer_ready_application(
        tmp_path,
        renderer=LocalAuthorizedRenderer(),
    )
    package = _create_render_package(application, run_id, _approve_renderer(application, run_id))
    job = _start(application, run_id, package)
    assert _wait(application, run_id, job["job_id"])["status"] == "SUCCEEDED_FOR_REVIEW"
    assert observed["preserve_timing"] is True
    assert "existing_mixed_audio" not in observed
    assert Path(observed["narration_audio_filename"]).suffix == ".wav"
    application.close()


def test_rerender_request_reopens_exact_package_and_upstream_change_supersedes(
    tmp_path,
) -> None:
    application, renderer, _, _, run_id, _, timeline, _ = _renderer_ready_application(tmp_path)
    package = _create_render_package(application, run_id, _approve_renderer(application, run_id))
    first_job = _start(application, run_id, package)
    first_completed = _wait(application, run_id, first_job["job_id"])
    artifact = application.get_render_artifact(run_id, first_completed["artifact_id"])
    rerender = application.review_render_artifact(
        run_id,
        artifact["artifact_id"],
        "request-rerender",
        {
            "schema_version": 1,
            "artifact_revision_id": artifact["artifact_revision_id"],
            "mp4_sha256": artifact["mp4_sha256"],
            "explicit_confirmation": True,
            "reason": "Bounded correction requested.",
        },
    )
    assert rerender["artifact"]["status"] == "RERENDER_REQUESTED"
    assert (
        application.get_renderer_stage_access(run_id)["bounded_render_execution_capability"] is True
    )
    second_job = _start(application, run_id, package)
    assert _wait(application, run_id, second_job["job_id"])["status"] == ("SUCCEEDED_FOR_REVIEW")
    assert len(renderer.calls) == 2
    segment = timeline["segments"][0]
    application.mutate_timeline_segment(
        run_id,
        segment["segment_id"],
        "save",
        {
            "schema_version": 1,
            "base_collection_revision_id": timeline["collection_revision_id"],
            "base_segment_revision_id": segment["segment_revision_id"],
            "changes": {"note": "Supersede renderer authority."},
        },
    )
    stale = application.get_renderer_stage_access(run_id)
    assert "ACCEPTED_NARRATION_ARTIFACT_REQUIRED" in stale["blocker_codes"]
    assert stale["approval"]["current"] is False
    assert stale["render_package"]["current"] is False
    assert stale["artifact"]["status"] == "SUPERSEDED"
    application.close()


def test_probe_uses_exact_argument_list_shell_false_and_strict_streams(
    monkeypatch, tmp_path
) -> None:
    artifact = tmp_path / "fixture.mp4"
    artifact.write_bytes(_mp4_bytes())
    observed = {}

    def fake_run(command, **kwargs):
        observed.update(command=command, kwargs=kwargs)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "streams": [
                        {
                            "codec_type": "video",
                            "codec_name": "h264",
                            "width": 1080,
                            "height": 1920,
                        },
                        {"codec_type": "audio", "codec_name": "aac"},
                    ],
                    "format": {"duration": "35.0"},
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert probe_mp4_artifact(artifact, ffprobe_binary="strict-ffprobe") == _probe()
    assert observed["command"][0] == "strict-ffprobe"
    assert observed["command"][-1] == str(artifact)
    assert observed["kwargs"]["shell"] is False
    assert observed["kwargs"]["timeout"] == 30


def test_loopback_routes_serve_opaque_mp4_and_preserve_win1(tmp_path) -> None:
    application, renderer, _, _, run_id, _, _, _ = _renderer_ready_application(tmp_path)
    with _running_host(tmp_path, application=application) as address:
        prefix = f"/api/v1/plan-only/runs/{run_id}/renderer-stage"
        status, access, _ = _http_request(address, "GET", f"{prefix}/access")
        assert status == 200
        authority = access["input_authority"]
        status, approved, _ = _json_request(
            address,
            "POST",
            f"{prefix}/approve",
            {
                "schema_version": 1,
                "execution_package_id": authority["execution_package_id"],
                "execution_package_revision_id": authority["execution_package_revision_id"],
                "package_source_authority_sha256": authority["package_source_authority_sha256"],
                "narration_artifact_id": authority["narration_artifact_id"],
                "narration_artifact_revision_id": authority["narration_artifact_revision_id"],
                "narration_audio_sha256": authority["narration_audio_sha256"],
                "explicit_local_resource_acknowledgement": True,
                "explicit_confirmation": True,
            },
        )
        assert status == 200
        approval = approved["approval"]
        status, packaged, _ = _json_request(
            address,
            "POST",
            f"{prefix}/create-package",
            {
                "schema_version": 1,
                "approval_id": approval["approval_id"],
                "approval_revision_id": approval["approval_revision_id"],
                "explicit_confirmation": True,
            },
        )
        render_package = packaged["render_package"]
        status, started, _ = _json_request(
            address,
            "POST",
            f"{prefix}/jobs",
            {
                "schema_version": 1,
                "render_package_id": render_package["render_package_id"],
                "render_package_revision_id": render_package["render_package_revision_id"],
                "render_package_sha256": render_package["render_package_sha256"],
                "explicit_confirmation": True,
            },
        )
        assert status == 200
        job_id = started["job"]["job_id"]
        for _ in range(100):
            status, job, _ = _http_request(
                address,
                "GET",
                f"{prefix}/jobs/{job_id}",
            )
            if job["status"] == "SUCCEEDED_FOR_REVIEW":
                break
            time.sleep(0.01)
        artifact_id = job["artifact_id"]
        connection = http.client.HTTPConnection(*address, timeout=2)
        connection.request("GET", f"{prefix}/artifacts/{artifact_id}/video")
        response = connection.getresponse()
        content = response.read()
        headers = dict(response.getheaders())
        connection.close()
        assert response.status == 200
        assert headers["Content-Type"] == "video/mp4"
        assert headers["Content-Length"] == str(len(_mp4_bytes()))
        assert headers["Cache-Control"] == "no-store"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert content == _mp4_bytes()
        connection = http.client.HTTPConnection(*address, timeout=5)
        connection.request(
            "PATCH",
            f"{prefix}/access",
            body=b'{"unread":true}',
            headers={"Content-Type": "application/json", "Content-Length": "15"},
        )
        assert connection.getresponse().status == 405
        connection.close()
    assert len(renderer.calls) == 1


def test_process_restart_does_not_rebind_orphan_mp4(tmp_path) -> None:
    first, _, _, _, run_id, _, _, _ = _renderer_ready_application(tmp_path)
    package = _create_render_package(first, run_id, _approve_renderer(first, run_id))
    job = _start(first, run_id, package)
    completed = _wait(first, run_id, job["job_id"])
    artifact = first.get_render_artifact(run_id, completed["artifact_id"])
    assert artifact is not None
    first.close()
    fresh = RendererStageStore(
        renderer=FakeRenderer(),
        artifact_root=tmp_path / "renderer",
        probe=lambda _: _probe(),
        renderer_configured=True,
    )
    assert fresh.artifact(run_id, artifact["artifact_id"]) is None
    assert fresh.video(run_id, artifact["artifact_id"]) is None
    fresh.close()
