import json
import socket
import asyncio
import hashlib
from pathlib import Path

import pytest

from tella import cli
from tella._voice_pace import resolve_pace
from tella.atomic_write import atomic_write_json
from tella.planner.practical_life_steps import plan_practical_life_steps_from_script
from tella.production import (
    CALLIRRHOE_PRODUCTION_CONFIG,
    ProductionRun,
    ProductionStage,
    build_production_resume_attestation,
    dry_run_envelope,
    evaluate_resume,
    file_sha256,
    require_reusable_narration,
)
from tella.recipes import apply_recipe_metadata, get_recipe
from tella.visual_acceptance import canonical_script_for_input
from tella.voice_profiles import apply_voice_resolution_metadata, resolve_voice


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "configs/acceptance/scripts/phone_out_of_reach_v1.txt"


def _identity() -> dict:
    resolved = canonical_script_for_input(SCRIPT_PATH, ROOT)
    assert resolved is not None
    return resolved[0]


def _aligned_job(job: Path) -> Path:
    config = CALLIRRHOE_PRODUCTION_CONFIG
    identity, script = canonical_script_for_input(SCRIPT_PATH, ROOT)
    recipe_definition = get_recipe(config.recipe_id)
    voice_resolution = resolve_voice(
        recipe_profile_id=recipe_definition.voice_profile_id,
        narrative_mode=recipe_definition.narrative_mode,
    )
    plan = plan_practical_life_steps_from_script(
        user_script=script.canonical_narration_text,
        target_lang="vi",
        voice_pace=resolve_pace(
            theme="practical_life_steps",
            custom_edge_rate=voice_resolution.resolved_voice_rate,
        ),
        preserve_narration=True,
    )
    apply_recipe_metadata(plan, recipe_definition, validation_status="passed")
    apply_voice_resolution_metadata(plan, voice_resolution)
    cli._apply_canonical_script_identity(plan, identity)
    run = ProductionRun(job, config, script_identity=identity)
    files = {
        "plan": job / "plan.json",
        "reuse_plan": job / ".reuse_plan.json",
        "recipe": job / "recipe.json",
        "raw": job / "assets" / "narration_raw.wav",
        "normalized": job / "assets" / "narration.wav",
        "tts_metadata": job / "tts_metadata.json",
        "alignment": job / "alignment_metadata.json",
        "boundaries": job / "alignment_boundaries.json",
    }
    for path in files.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    for scene in plan.scenes:
        scene.asset_path = f"assets/scene_{scene.scene_index:02d}.jpg"
        scene.asset_status = "done"
        scene.image_provider = "cloudflare"
    plan_json = plan.model_dump_json(indent=2)
    files["plan"].write_text(plan_json, encoding="utf-8")
    files["reuse_plan"].write_text(plan_json, encoding="utf-8")
    files["recipe"].write_text('{"recipe":"callirrhoe"}', encoding="utf-8")
    files["raw"].write_bytes(b"trusted raw narration")
    files["normalized"].write_bytes(b"trusted normalized narration")
    atomic_write_json(files["tts_metadata"], {
        "provider_metadata": {
            "provider": config.provider,
            "model": config.model,
            "voice": config.voice,
            "style": config.style,
            "language": config.tts_language,
            "source_narration_text_hash": hashlib.sha256(
                " ".join(identity["canonical_script_sentences"]).encode("utf-8")
            ).hexdigest(),
            "fallback_used": False,
        }
    })
    boundaries = [1, 2, 3, 4, 5, 6]
    atomic_write_json(files["alignment"], {
        "wav_sha256": file_sha256(files["normalized"]),
        "boundaries": boundaries,
    })
    atomic_write_json(files["boundaries"], {"boundaries": boundaries})
    images = []
    for index in range(1, 8):
        image = job / "assets" / f"scene_{index:02d}.jpg"
        image.write_bytes(f"trusted image {index}".encode())
        images.append(image)
    run.record_artifact_hashes({"plan": files["plan"]}, image_artifacts=images)
    run.counts = {
        "gemini": 4, "edge": 0, "image_provider": 7,
        "retries": 0, "fallbacks": 0,
    }
    run.transport_attempts = {
        "gemini": 4, "image_provider": 7,
    }
    run.provider_results = {
        "gemini": {"successful": 1, "failed": 3},
        "image_provider": {"successful": 7, "failed": 0},
    }
    run.advance(ProductionStage.aligned, {
        "raw_narration": files["raw"],
        "normalized_narration": files["normalized"],
        "alignment": files["alignment"],
        "alignment_boundaries": files["boundaries"],
    })
    attestation = job.parent / f"{job.name}_operational_attestation.json"
    atomic_write_json(
        attestation,
        build_production_resume_attestation(job, config),
    )
    return attestation


@pytest.fixture
def no_external_access(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("provider or socket path was reached")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr("tella.tts.gemini._official_client", forbidden)
    return forbidden


def test_cli_parses_zero_tts_budget_and_required_reuse():
    args = cli.build_arg_parser().parse_args([
        "--resume", "--resume-attestation", "attestation.json",
        "--require-reused-narration", "--max-tts-requests", "0",
    ])
    assert args.max_tts_requests == 0
    assert args.require_reused_narration is True


def test_explicit_zero_is_preserved_in_runtime_envelope(tmp_path, no_external_access):
    job = tmp_path / "job"
    attestation = _aligned_job(job)
    envelope = dry_run_envelope(
        CALLIRRHOE_PRODUCTION_CONFIG,
        job,
        resume=True,
        resume_attestation_path=attestation,
        max_tts_requests=0,
        max_image_requests=0,
    )
    assert envelope["maximum_gemini_requests"] == 0
    assert envelope["maximum_gemini_sdk_attempts"] == 0
    assert envelope["current_invocation_request_limits"]["gemini"] == 0
    assert envelope["maximum_image_requests"] == 0


def test_operational_attestation_trusts_through_aligned_without_provider(
    tmp_path, no_external_access
):
    job = tmp_path / "job"
    attestation = _aligned_job(job)
    decision = evaluate_resume(
        job, CALLIRRHOE_PRODUCTION_CONFIG, _identity(), attestation
    )
    require_reusable_narration(decision)
    assert decision["operational_attestation_accepted"] is True
    assert decision["resume_stage"] == "aligned"
    assert decision["next_required_stage"] == "music_ready"
    assert decision["estimated_gemini_requests"] == 0
    assert decision["maximum_gemini_sdk_attempts"] == 0
    assert decision["estimated_image_requests"] == 0
    assert decision["reusable_image_count"] == 7

    resumed = ProductionRun(
        job, CALLIRRHOE_PRODUCTION_CONFIG, resume=True,
        max_tts_requests=0, max_image_requests=0,
    )
    assert resumed.counts["gemini"] == 4
    assert resumed.transport_attempts["gemini"] == 4
    assert resumed.invocation_counts["gemini"] == 0
    assert resumed.invocation_transport_attempts["gemini"] == 0


@pytest.mark.parametrize(
    "relative",
    [
        "assets/narration_raw.wav",
        "assets/narration.wav",
        "alignment_metadata.json",
        "alignment_boundaries.json",
        "tts_metadata.json",
    ],
)
def test_tampered_required_artifact_fails_closed(
    tmp_path, no_external_access, relative
):
    job = tmp_path / "job"
    attestation = _aligned_job(job)
    (job / relative).write_bytes(b"tampered")
    decision = evaluate_resume(
        job, CALLIRRHOE_PRODUCTION_CONFIG, _identity(), attestation
    )
    assert decision["operational_attestation_accepted"] is False
    with pytest.raises(RuntimeError, match="required narration reuse failed"):
        require_reusable_narration(decision)
    assert decision["persisted_submission_counts"]["gemini"] == 4


def test_missing_raw_or_attestation_fails_before_provider(tmp_path, no_external_access):
    job = tmp_path / "job"
    attestation = _aligned_job(job)
    (job / "assets" / "narration_raw.wav").unlink()
    missing_raw = evaluate_resume(
        job, CALLIRRHOE_PRODUCTION_CONFIG, _identity(), attestation
    )
    with pytest.raises(RuntimeError, match="required narration reuse failed"):
        require_reusable_narration(missing_raw)
    missing_attestation = evaluate_resume(
        job, CALLIRRHOE_PRODUCTION_CONFIG, _identity(), None
    )
    with pytest.raises(RuntimeError, match="attestation is required"):
        require_reusable_narration(missing_attestation)


def test_fresh_zero_budget_fails_without_incrementing_accounting(
    tmp_path, no_external_access
):
    run = ProductionRun(
        tmp_path / "zero", CALLIRRHOE_PRODUCTION_CONFIG,
        max_tts_requests=0,
    )
    with pytest.raises(RuntimeError, match="budget exhausted"):
        run.record_submission("gemini", transport_attempts=1)
    assert run.counts["gemini"] == 0
    assert run.transport_attempts["gemini"] == 0


def test_normal_fresh_budget_one_retains_submission_behavior(tmp_path):
    run = ProductionRun(
        tmp_path / "one", CALLIRRHOE_PRODUCTION_CONFIG,
        max_tts_requests=1,
    )
    run.record_submission("gemini", transport_attempts=1)
    assert run.counts["gemini"] == 1
    assert run.transport_attempts["gemini"] == 1
    assert run.invocation_counts["gemini"] == 1


def test_required_reuse_pipeline_reaches_music_without_provider_or_accounting(
    tmp_path, monkeypatch, no_external_access
):
    job = tmp_path / "job"
    attestation = _aligned_job(job)
    identity, script = canonical_script_for_input(SCRIPT_PATH, ROOT)
    recipe_definition = get_recipe(CALLIRRHOE_PRODUCTION_CONFIG.recipe_id)
    voice_resolution = resolve_voice(
        recipe_profile_id=recipe_definition.voice_profile_id,
        narrative_mode=recipe_definition.narrative_mode,
    )

    class ReachedMusic(RuntimeError):
        pass

    forbidden = no_external_access
    monkeypatch.setattr(cli, "fetch_assets", forbidden)
    monkeypatch.setattr(cli, "synthesize_all", forbidden)
    monkeypatch.setattr(cli, "apply_sentence_alignment", forbidden)
    monkeypatch.setattr(cli, "render", forbidden)
    monkeypatch.setattr(
        cli,
        "configure_music",
        lambda *args, **kwargs: (_ for _ in ()).throw(ReachedMusic("music_ready")),
    )

    with pytest.raises(ReachedMusic, match="music_ready"):
        asyncio.run(cli._run_pipeline_unlocked(
            topic="phone focus",
            target_lang="vi",
            theme="practical_life_steps",
            media_source="ai_image",
            duration_mode="short",
            aspect_ratio="9:16",
            voice_pace_name=None,
            voice_rate_custom=voice_resolution.resolved_voice_rate,
            voice_gender=None,
            out_root=tmp_path,
            job_id="job",
            user_script=script.canonical_narration_text,
            reuse_assets=True,
            skip_image_generation=True,
            reuse_assets_mode="strict",
            max_ai_images=0,
            recipe=recipe_definition,
            voice_resolution=voice_resolution,
            script_identity=identity,
            resume=True,
            resume_attestation_path=attestation,
            max_tts_requests=0,
            require_reused_narration=True,
        ))
    manifest = json.loads(
        (job / "production_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["current_invocation_submission_counts"]["gemini"] == 0
    assert manifest["current_invocation_transport_attempt_counts"]["gemini"] == 0
    assert manifest["external_submission_counts"]["gemini"] == 4
