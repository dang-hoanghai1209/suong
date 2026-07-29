from __future__ import annotations

from copy import deepcopy
import hashlib
import http.client
import json
import struct
import threading

import pytest
from pydantic import ValidationError

from tella.topic_production.narration_stage import (
    NarrationAudioArtifactV1,
    NarrationStageAccessV1,
    NarrationStageStore,
)
from tella.topic_production.plan_only_application import dispatch_plan_only_request
from tella.tts.providers import TTSProvider, TTSResult
from tests.test_plan_only_execution_enablement import _approved_application, _create
from tests.test_plan_only_local_host import _http_request, _json_request, _running_host


def _wav_bytes(*, payload_size: int = 128) -> bytes:
    payload = b"\0" * payload_size
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(payload))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 24000, 48000, 2, 16)
        + b"data"
        + struct.pack("<I", len(payload))
        + payload
    )


class FakeTTSProvider(TTSProvider):
    provider_name = "gemini"

    def __init__(self, *, content: bytes | None = None, failure: Exception | None = None):
        self.content = _wav_bytes() if content is None else content
        self.failure = failure
        self.calls: list[dict[str, object]] = []

    async def synthesize(
        self,
        text,
        out_path,
        *,
        voice,
        language,
        speed,
        codec,
        sample_rate,
        metadata=None,
    ):
        self.calls.append(
            {
                "text": text,
                "out_path": out_path,
                "voice": voice,
                "language": language,
                "speed": speed,
                "codec": codec,
                "sample_rate": sample_rate,
                "metadata": deepcopy(metadata),
            }
        )
        if self.failure is not None:
            raise self.failure
        out_path.write_bytes(self.content)
        return TTSResult(
            audio_path=out_path,
            provider="gemini",
            voice=voice,
            language=language,
            metadata={"codec": "wav"},
        )


class BlockingTTSProvider(FakeTTSProvider):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    async def synthesize(self, *args, **kwargs):
        self.started.set()
        assert self.release.wait(timeout=5)
        return await super().synthesize(*args, **kwargs)


def _sealed_application(tmp_path, *, provider=None, duration=35.0, configured=True):
    application, visual, run_id, scenes, visuals, compositions, timeline, report = (
        _approved_application(tmp_path)
    )
    tts = provider or FakeTTSProvider()
    application._narration_stage = NarrationStageStore(
        provider=tts,
        artifact_root=tmp_path / "audio",
        duration_probe=lambda _: duration,
        provider_configured=configured,
    )
    package = _create(application, run_id, report)["package"]
    return application, tts, visual, run_id, package, timeline


def _approve(application, run_id, package):
    return application.approve_narration_stage(
        run_id,
        {
            "schema_version": 1,
            "execution_package_id": package["package_id"],
            "execution_package_revision_id": package["package_revision_id"],
            "package_source_authority_sha256": package["package_source_authority_sha256"],
            "explicit_external_provider_acknowledgement": True,
            "explicit_confirmation": True,
            "note": "Approve one narration audio request.",
        },
    )


def _generate(application, run_id, package, approval):
    return application.generate_narration_audio(
        run_id,
        {
            "schema_version": 1,
            "execution_package_id": package["package_id"],
            "execution_package_revision_id": package["package_revision_id"],
            "package_source_authority_sha256": package["package_source_authority_sha256"],
            "approval_id": approval["approval_id"],
            "approval_revision_id": approval["approval_revision_id"],
            "explicit_confirmation": True,
        },
    )


def test_access_binds_exact_package_narration_and_false_renderer_locks(tmp_path) -> None:
    application, _, _, run_id, package, _ = _sealed_application(tmp_path)
    access = application.get_narration_stage_access(run_id)
    source = access["narration_source"]
    assert access["narration_stage_review_capability"] is True
    assert access["narration_stage_approval_capability"] is True
    assert access["narration_generation_capability"] is False
    assert source["narration_source_sha256"] == package["authority"]["narration_source_sha256"]
    assert (
        hashlib.sha256(source["narration_text"].encode()).hexdigest()
        == source["narration_source_sha256"]
    )
    assert source["editable"] is False
    assert access["provider_configuration"]["voice_id"] == "Callirrhoe"
    assert access["provider_configuration"]["language"] == "vi-VN"
    for key in (
        "full_render_enabled",
        "timeline_execution_authority",
        "renderer_execution_authority",
        "render_authority",
        "video_render_authority",
        "subtitle_generation_capability",
        "media_muxing_capability",
        "output_creation_capability",
        "execution_job_creation_capability",
        "final_media_capability",
    ):
        assert access[key] is False
    NarrationStageAccessV1.model_validate_json(json.dumps(access))


def test_unconfigured_provider_is_inspectable_but_cannot_generate(tmp_path) -> None:
    application, _, _, run_id, package, _ = _sealed_application(tmp_path, configured=False)
    access = application.get_narration_stage_access(run_id)
    assert access["provider_configuration"]["provider_configured"] is False
    assert "TTS_PROVIDER_NOT_CONFIGURED" in access["blocker_codes"]
    approval = _approve(application, run_id, package)["approval"]
    result = _generate(application, run_id, package, approval)
    assert result["error"]["code"] == "TTS_PROVIDER_NOT_CONFIGURED"
    assert not (tmp_path / "audio").exists()


def test_approval_and_generation_are_explicit_bound_and_duplicate_safe(tmp_path) -> None:
    application, tts, visual, run_id, package, timeline = _sealed_application(tmp_path)
    assert tts.calls == []
    approved = _approve(application, run_id, package)
    approval = approved["approval"]
    assert approval["purpose"] == "NARRATION_TTS_ARTIFACT_GENERATION"
    assert approval["execution_package_revision_id"] == package["package_revision_id"]
    assert (
        approval["accepted_timeline_revision_id"]
        == package["authority"]["accepted_timeline_revision_id"]
    )
    assert tts.calls == []

    result = _generate(application, run_id, package, approval)
    artifact = result["artifact"]
    assert result["error"] is None
    assert len(tts.calls) == 1
    assert tts.calls[0]["text"] == application.get_run(run_id)["story_plan"]["narration_text"]
    assert tts.calls[0]["voice"] == "Callirrhoe"
    assert artifact["audio_sha256"] == hashlib.sha256(tts.content).hexdigest()
    assert artifact["byte_length"] == len(tts.content)
    assert artifact["measured_duration_ms"] == 35000
    assert artifact["tts_request_sha256"]
    assert (
        artifact["duration_comparison"]["accepted_timeline_duration_ms"]
        == package["authority"]["effective_timeline_duration_ms"]
    )
    assert artifact["duration_comparison"]["duration_alignment_status"] in {
        "WITHIN_POLICY",
        "OUTSIDE_POLICY",
    }
    assert _generate(application, run_id, package, approval)["error"]["code"] == (
        "TTS_REQUEST_ALREADY_COMPLETED"
    )
    assert len(tts.calls) == 1
    assert visual.calls
    assert timeline


@pytest.mark.parametrize(
    ("operation", "extra"),
    [
        ("approve", {"narration_text": "browser supplied"}),
        ("approve", {"provider_url": "https://example.invalid"}),
        ("approve", {"api_key": "secret"}),
        ("generate", {"output_path": "outside.wav"}),
        ("generate", {"model_id": "caller-model"}),
    ],
)
def test_browser_cannot_submit_source_credentials_provider_or_path(
    tmp_path, operation, extra
) -> None:
    application, _, _, run_id, package, _ = _sealed_application(tmp_path)
    approval_payload = {
        "schema_version": 1,
        "execution_package_id": package["package_id"],
        "execution_package_revision_id": package["package_revision_id"],
        "package_source_authority_sha256": package["package_source_authority_sha256"],
        "explicit_external_provider_acknowledgement": True,
        "explicit_confirmation": True,
        "note": None,
    }
    if operation == "approve":
        result = application.approve_narration_stage(run_id, {**approval_payload, **extra})
        assert result["error"]["code"] == "INVALID_NARRATION_STAGE_APPROVAL"
    else:
        approval = application.approve_narration_stage(run_id, approval_payload)["approval"]
        generation = {
            "schema_version": 1,
            "execution_package_id": package["package_id"],
            "execution_package_revision_id": package["package_revision_id"],
            "package_source_authority_sha256": package["package_source_authority_sha256"],
            "approval_id": approval["approval_id"],
            "approval_revision_id": approval["approval_revision_id"],
            "explicit_confirmation": True,
            **extra,
        }
        assert (
            application.generate_narration_audio(run_id, generation)["error"]["code"]
            == "INVALID_NARRATION_GENERATION_REQUEST"
        )


def test_concurrent_generation_is_rejected_without_second_provider_call(tmp_path) -> None:
    provider = BlockingTTSProvider()
    application, _, _, run_id, package, _ = _sealed_application(tmp_path, provider=provider)
    approval = _approve(application, run_id, package)["approval"]
    results: list[dict[str, object]] = []
    worker = threading.Thread(
        target=lambda: results.append(_generate(application, run_id, package, approval))
    )
    worker.start()
    assert provider.started.wait(timeout=5)
    concurrent = _generate(application, run_id, package, approval)
    assert concurrent["error"]["code"] == "TTS_GENERATION_ALREADY_IN_PROGRESS"
    provider.release.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert results[0]["error"] is None
    assert len(provider.calls) == 1


def test_regeneration_request_requires_new_approval_and_request_identity(tmp_path) -> None:
    application, provider, _, run_id, package, _ = _sealed_application(tmp_path)
    first_approval = _approve(application, run_id, package)["approval"]
    first = _generate(application, run_id, package, first_approval)["artifact"]
    requested = application.review_narration_audio(
        run_id,
        first["artifact_id"],
        "request-regeneration",
        {
            "schema_version": 1,
            "artifact_revision_id": first["artifact_revision_id"],
            "audio_sha256": first["audio_sha256"],
            "explicit_confirmation": True,
            "reason": "Please generate one new audio artifact.",
        },
    )
    assert requested["error"] is None
    assert application.get_narration_stage_access(run_id)["approval"]["current"] is False
    second_approval = _approve(application, run_id, package)["approval"]
    second = _generate(application, run_id, package, second_approval)["artifact"]
    assert second["tts_request_sha256"] != first["tts_request_sha256"]
    assert second["artifact_id"] != first["artifact_id"]
    assert len(provider.calls) == 2


@pytest.mark.parametrize(
    ("content", "duration", "code"),
    [
        (b"", 35.0, "AUDIO_ARTIFACT_EMPTY"),
        (b"<html>provider error</html>", 35.0, "AUDIO_SIGNATURE_INVALID"),
        (_wav_bytes(), 0.0, "AUDIO_MEASUREMENT_FAILED"),
        (_wav_bytes(), float("nan"), "AUDIO_MEASUREMENT_FAILED"),
    ],
)
def test_invalid_audio_creates_failed_attempt_no_artifact_and_removes_partial(
    tmp_path, content, duration, code
) -> None:
    provider = FakeTTSProvider(content=content)
    application, _, _, run_id, package, _ = _sealed_application(
        tmp_path, provider=provider, duration=duration
    )
    approval = _approve(application, run_id, package)["approval"]
    result = _generate(application, run_id, package, approval)
    assert result["error"]["code"] == code
    assert result["attempt"]["status"] == "FAILED"
    assert result["artifact"] is None
    assert list((tmp_path / "audio").glob("*.tmp.wav")) == []
    assert list((tmp_path / "audio").glob("narration-audio-artifact-*.wav")) == []


def test_provider_failure_is_sanitized_and_retry_requires_new_action(tmp_path) -> None:
    secret = "GEMINI_API_KEY=secret Authorization: Bearer secret"
    provider = FakeTTSProvider(failure=RuntimeError(f"429 {secret}"))
    application, _, _, run_id, package, _ = _sealed_application(tmp_path, provider=provider)
    approval = _approve(application, run_id, package)["approval"]
    result = _generate(application, run_id, package, approval)
    assert result["error"]["code"] == "TTS_PROVIDER_RATE_LIMITED"
    assert result["error"]["retryable"] is True
    assert secret not in str(result)
    assert len(provider.calls) == 1
    second = _generate(application, run_id, package, approval)
    assert second["error"]["code"] == "TTS_PROVIDER_RATE_LIMITED"
    assert len(provider.calls) == 2


def test_duration_outside_policy_blocks_acceptance_but_remains_playable(tmp_path) -> None:
    application, _, _, run_id, package, _ = _sealed_application(tmp_path, duration=100.0)
    approval = _approve(application, run_id, package)["approval"]
    artifact = _generate(application, run_id, package, approval)["artifact"]
    assert artifact["duration_comparison"]["timeline_realignment_required"] is True
    result = application.review_narration_audio(
        run_id,
        artifact["artifact_id"],
        "accept",
        {
            "schema_version": 1,
            "artifact_revision_id": artifact["artifact_revision_id"],
            "audio_sha256": artifact["audio_sha256"],
            "explicit_confirmation": True,
            "reason": None,
        },
    )
    assert result["error"]["code"] == "NARRATION_DURATION_OUT_OF_POLICY"
    assert application.get_narration_audio_bytes(run_id, artifact["artifact_id"])[0]


def test_accept_reject_and_regeneration_authority_are_detached(tmp_path) -> None:
    application, tts, _, run_id, package, _ = _sealed_application(tmp_path)
    approval = _approve(application, run_id, package)["approval"]
    artifact = _generate(application, run_id, package, approval)["artifact"]
    artifact["provider_id"] = "forged"
    current = application.get_narration_audio_artifact(run_id, artifact["artifact_id"])
    assert current["provider_id"] == "gemini"
    request = {
        "schema_version": 1,
        "artifact_revision_id": current["artifact_revision_id"],
        "audio_sha256": current["audio_sha256"],
        "explicit_confirmation": True,
        "reason": None,
    }
    accepted = application.review_narration_audio(
        run_id, current["artifact_id"], "accept", request
    )["artifact"]
    assert accepted["status"] == "ACCEPTED_FOR_RENDERER_STAGE_REVIEW"
    assert accepted["eligible_for_renderer_stage_review"] is True
    assert accepted["renderer_execution_authority"] is False
    NarrationAudioArtifactV1.model_validate(accepted)
    assert len(tts.calls) == 1


def test_upstream_change_supersedes_approval_and_artifact(tmp_path) -> None:
    application, _, _, run_id, package, timeline = _sealed_application(tmp_path)
    approval = _approve(application, run_id, package)["approval"]
    _generate(application, run_id, package, approval)
    segment = timeline["segments"][0]
    application.mutate_timeline_segment(
        run_id,
        segment["segment_id"],
        "save",
        {
            "schema_version": 1,
            "base_collection_revision_id": timeline["collection_revision_id"],
            "base_segment_revision_id": segment["segment_revision_id"],
            "changes": {"note": "Change package-bound timeline authority."},
        },
    )
    access = application.get_narration_stage_access(run_id)
    assert "EXECUTION_PACKAGE_STALE" in access["blocker_codes"]
    assert access["approval"]["current"] is False
    assert access["artifact"]["status"] == "SUPERSEDED"
    assert access["eligible_for_renderer_stage_review"] is False


def test_routes_serve_opaque_audio_with_exact_headers_and_preserve_win1(tmp_path) -> None:
    application, tts, _, run_id, package, timeline = _sealed_application(tmp_path)
    prefix = f"/api/v1/plan-only/runs/{run_id}/narration-stage"
    assert (
        dispatch_plan_only_request(application, method="GET", path=f"{prefix}/access").status_code
        == 200
    )
    with _running_host(tmp_path, application=application) as address:
        status, approved, _ = _json_request(
            address,
            "POST",
            f"{prefix}/approve",
            {
                "schema_version": 1,
                "execution_package_id": package["package_id"],
                "execution_package_revision_id": package["package_revision_id"],
                "package_source_authority_sha256": package["package_source_authority_sha256"],
                "explicit_external_provider_acknowledgement": True,
                "explicit_confirmation": True,
                "note": None,
            },
        )
        assert status == 200
        approval = approved["approval"]
        status, generated, _ = _json_request(
            address,
            "POST",
            f"{prefix}/generate",
            {
                "schema_version": 1,
                "execution_package_id": package["package_id"],
                "execution_package_revision_id": package["package_revision_id"],
                "package_source_authority_sha256": package["package_source_authority_sha256"],
                "approval_id": approval["approval_id"],
                "approval_revision_id": approval["approval_revision_id"],
                "explicit_confirmation": True,
            },
        )
        artifact = generated["artifact"]
        assert status == 200
        connection = http.client.HTTPConnection(*address, timeout=2)
        connection.request("GET", f"{prefix}/artifacts/{artifact['artifact_id']}/audio")
        response = connection.getresponse()
        content = response.read()
        headers = dict(response.getheaders())
        connection.close()
        assert response.status == 200
        assert content == tts.content
        assert headers["Content-Type"] == "audio/wav"
        assert headers["Content-Length"] == str(len(tts.content))
        assert headers["Cache-Control"] == "no-store"
        assert str(tmp_path).encode() not in content
        status, duplicate, _ = _json_request(
            address,
            "POST",
            f"{prefix}/generate",
            {
                "schema_version": 1,
                "execution_package_id": package["package_id"],
                "execution_package_revision_id": package["package_revision_id"],
                "package_source_authority_sha256": package["package_source_authority_sha256"],
                "approval_id": approval["approval_id"],
                "approval_revision_id": approval["approval_revision_id"],
                "explicit_confirmation": True,
            },
        )
        assert status == 200
        assert duplicate["error"]["code"] == "TTS_REQUEST_ALREADY_COMPLETED"
        status, accepted, _ = _json_request(
            address,
            "POST",
            f"{prefix}/artifacts/{artifact['artifact_id']}/accept",
            {
                "schema_version": 1,
                "artifact_revision_id": artifact["artifact_revision_id"],
                "audio_sha256": artifact["audio_sha256"],
                "explicit_confirmation": True,
                "reason": None,
            },
        )
        assert status == 200
        assert accepted["artifact"]["eligible_for_renderer_stage_review"] is True
        assert accepted["artifact"]["renderer_execution_authority"] is False
        assert accepted["artifact"]["final_media_capability"] is False
        status, rejected, _ = _http_request(
            address,
            "PUT",
            f"{prefix}/access",
            body=b"{}",
            headers={"Content-Type": "application/json", "Content-Length": "2"},
        )
        assert status == 405
        assert rejected["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert len(tts.calls) == 1
    segment = timeline["segments"][0]
    application.mutate_timeline_segment(
        run_id,
        segment["segment_id"],
        "save",
        {
            "schema_version": 1,
            "base_collection_revision_id": timeline["collection_revision_id"],
            "base_segment_revision_id": segment["segment_revision_id"],
            "changes": {"note": "Supersede after runtime acceptance."},
        },
    )
    stale = application.get_narration_stage_access(run_id)
    assert stale["artifact"]["status"] == "SUPERSEDED"
    assert stale["eligible_for_renderer_stage_review"] is False
    history = application.get_narration_stage_history(run_id)
    assert history["artifacts"][0]["status"] == "GENERATED_FOR_REVIEW"
    assert history["artifacts"][-1]["status"] == "SUPERSEDED"
    assert list((tmp_path / "audio").glob("*.tmp.wav")) == []


def test_public_models_reject_forged_capabilities_and_caller_aliases(tmp_path) -> None:
    application, _, _, run_id, _, _ = _sealed_application(tmp_path)
    access = application.get_narration_stage_access(run_id)
    forged = deepcopy(access)
    forged["renderer_execution_authority"] = True
    with pytest.raises(ValidationError):
        NarrationStageAccessV1.model_validate(forged)
    access["blocker_codes"].append("forged")
    assert "forged" not in application.get_narration_stage_access(run_id)["blocker_codes"]
