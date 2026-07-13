import json
import os
import shutil
import socket
import sys
import unicodedata
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts import release_preflight
from tella import cli
from tella._voice_pace import resolve_pace
from tella.acceptance_script import (
    CanonicalScriptReference,
    canonicalize_script_bytes,
    load_canonical_script,
)
from tella.planner.practical_life_steps import (
    plan_practical_life_steps_from_script,
    plan_practical_life_steps_from_topic,
)
from tella.production import CALLIRRHOE_PRODUCTION_CONFIG, ProductionRun
from tella.recipes import apply_recipe_metadata, get_recipe
from tella.visual_acceptance import (
    DEFAULT_ACCEPTANCE_SUITE_PATH,
    EXPECTED_SCRIPT_ROLE_IDENTITIES,
    canonical_script_for_case,
    canonical_script_for_input,
    initialize_review,
    load_review,
    load_suite,
    validate_job_script_identity,
    validate_review,
)
from tella.voice_profiles import apply_voice_resolution_metadata, resolve_voice


ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = ROOT / DEFAULT_ACCEPTANCE_SUITE_PATH
SCRIPT_PATH = ROOT / "configs/acceptance/scripts/phone_out_of_reach_v1.txt"
SCRIPT_HASH = "041de27b2d041305751fca5c8032ba050a316b8d421386ac7d6fd8ea7984ecf9"
PHONE_TOPIC = (
    "Đặt điện thoại ngoài tầm tay trong hai mươi phút để tập trung làm một "
    "việc quan trọng."
)


def _script():
    suite = load_suite(SUITE_PATH)
    return canonical_script_for_case(suite, "phone_out_of_reach", ROOT)[1]


def _identity():
    resolved = canonical_script_for_input(SCRIPT_PATH, ROOT)
    assert resolved is not None
    return resolved


def _plan():
    identity, script = _identity()
    recipe = get_recipe("practical_life_steps_callirrhoe_v1")
    resolution = resolve_voice(
        recipe_profile_id=recipe.voice_profile_id,
        narrative_mode=recipe.narrative_mode,
    )
    plan = plan_practical_life_steps_from_script(
        user_script=script.canonical_narration_text,
        target_lang="vi",
        voice_pace=resolve_pace(
            theme="practical_life_steps",
            custom_edge_rate=resolution.resolved_voice_rate,
        ),
        preserve_narration=True,
    )
    apply_recipe_metadata(plan, recipe, validation_status="passed")
    apply_voice_resolution_metadata(plan, resolution)
    cli._apply_canonical_script_identity(plan, identity)
    return plan, identity, script


def _job(tmp_path: Path):
    job = tmp_path / "job"
    plan, identity, script = _plan()
    run = ProductionRun(
        job, CALLIRRHOE_PRODUCTION_CONFIG, script_identity=identity
    )
    assets = job / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    for scene in plan.scenes:
        image = assets / f"scene_{scene.scene_index:02d}.jpg"
        image.write_bytes(f"image-{scene.scene_index}".encode())
        scene.asset_path = f"assets/{image.name}"
        scene.asset_status = "done"
    (job / "plan.json").write_text(
        plan.model_dump_json(indent=2), encoding="utf-8"
    )
    return job, plan, identity, script


def test_lf_crlf_and_nfc_equivalents_have_one_canonical_hash():
    raw = SCRIPT_PATH.read_bytes()
    sentences, canonical, digest = canonicalize_script_bytes(raw)
    crlf = canonical.replace("\n", "\r\n").encode("utf-8")
    nfd = unicodedata.normalize("NFD", canonical).encode("utf-8")
    assert canonicalize_script_bytes(crlf)[2] == digest == SCRIPT_HASH
    assert canonicalize_script_bytes(nfd)[2] == digest
    assert len(sentences) == 7 and canonical.endswith("\n")


@pytest.mark.parametrize(
    "content",
    [
        b"one\n\ntwo\nthree\nfour\nfive\nsix\nseven\n",
        b"one\ntwo\nthree\nfour\nfive\nsix\n",
        b"one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\n",
        b" one\ntwo\nthree\nfour\nfive\nsix\nseven\n",
        b"one \ntwo\nthree\nfour\nfive\nsix\nseven\n",
    ],
)
def test_malformed_line_structure_fails(content):
    with pytest.raises(ValueError):
        canonicalize_script_bytes(content)


def test_invalid_utf8_and_unsupported_version_fail():
    with pytest.raises(ValueError, match="UTF-8"):
        canonicalize_script_bytes(b"\xff\xfe")
    with pytest.raises(ValidationError, match="unsupported canonical script version"):
        CanonicalScriptReference(
            script_version=2,
            script_path="configs/script.txt",
            canonical_script_sha256="0" * 64,
            script_source="human_reviewed",
        )


def test_hash_mismatch_path_traversal_and_symlink_fail(tmp_path):
    repo = tmp_path / "repo"
    target = repo / "configs" / "script.txt"
    target.parent.mkdir(parents=True)
    target.write_bytes(SCRIPT_PATH.read_bytes())
    with pytest.raises(ValidationError, match="escape"):
        CanonicalScriptReference(
            script_version=1,
            script_path="../script.txt",
            canonical_script_sha256=SCRIPT_HASH,
            script_source="human_reviewed",
        )
    wrong = CanonicalScriptReference(
        script_version=1,
        script_path="configs/script.txt",
        canonical_script_sha256="0" * 64,
        script_source="human_reviewed",
    )
    with pytest.raises(ValueError, match="SHA256"):
        load_canonical_script(wrong, repo)
    link = repo / "configs" / "linked.txt"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    linked = wrong.model_copy(update={
        "script_path": "configs/linked.txt",
        "canonical_script_sha256": SCRIPT_HASH,
    })
    with pytest.raises(ValueError, match="symlink or junction"):
        load_canonical_script(linked, repo)


def test_script_text_is_data_not_executed_configuration(monkeypatch):
    monkeypatch.delenv("CANONICAL_SCRIPT_EXECUTED", raising=False)
    lines = ["CANONICAL_SCRIPT_EXECUTED=1"] + [f"safe sentence {i}." for i in range(2, 8)]
    sentences, _, _ = canonicalize_script_bytes(("\n".join(lines) + "\n").encode())
    assert sentences[0] == "CANONICAL_SCRIPT_EXECUTED=1"
    assert "CANONICAL_SCRIPT_EXECUTED" not in os.environ


def test_suite_links_only_phone_case_and_preserves_unique_cases():
    suite = load_suite(SUITE_PATH)
    assert len(suite.cases) == len({case.case_id for case in suite.cases}) == 10
    linked = [case for case in suite.cases if case.canonical_script is not None]
    assert [case.case_id for case in linked] == ["phone_out_of_reach"]
    case = linked[0]
    assert case.expected_recipe == "practical_life_steps_callirrhoe_v1"
    assert case.expected_scene_count == 7
    assert case.canonical_script.canonical_script_sha256 == SCRIPT_HASH


def test_canonical_planner_preserves_sentences_roles_and_phone_state(monkeypatch):
    forbidden = lambda *a, **k: pytest.fail("provider or socket called")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    plan, _, script = _plan()
    scenes = plan.scenes
    roles = [
        f"step_{scene.step_number}" if scene.scene_role == "practical_step" else scene.scene_role
        for scene in scenes
    ]
    assert len(scenes) == 7
    assert roles == list(EXPECTED_SCRIPT_ROLE_IDENTITIES)
    assert [scene.voice_script for scene in scenes] == list(script.sentences)
    assert plan.voice_edge_rate == plan.resolved_voice_rate == "+0%"
    assert "placing" in scenes[3].visual_action
    assert "phone" in scenes[3].visual_action
    assert "beyond arm's reach" in scenes[3].visual_action
    assert "wordless unbranded" in scenes[3].image_prompt
    assert "checking" not in scenes[5].visual_action.casefold()
    with pytest.raises(ValueError, match="topic-only advice generation"):
        plan_practical_life_steps_from_topic(topic=PHONE_TOPIC, target_lang="vi")


def test_exact_job_script_identity_passes_and_image_change_does_not_invalidate(tmp_path):
    job, _, _, _ = _job(tmp_path)
    assert validate_job_script_identity(job)["valid"] is True
    (job / "assets" / "scene_04.jpg").write_bytes(b"regenerated image")
    assert validate_job_script_identity(job)["valid"] is True


@pytest.mark.parametrize("mutation", ["changed", "reordered", "missing", "stale_hash"])
def test_script_identity_mismatch_fails_closed(tmp_path, mutation):
    job, plan, _, _ = _job(tmp_path)
    if mutation == "changed":
        plan.scenes[0].voice_script += " Thay đổi."
    elif mutation == "reordered":
        plan.scenes[0].voice_script, plan.scenes[1].voice_script = (
            plan.scenes[1].voice_script, plan.scenes[0].voice_script,
        )
    elif mutation == "missing":
        plan.acceptance_case_id = ""
    else:
        plan.canonical_script_sha256 = "0" * 64
    (job / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    result = validate_job_script_identity(job)
    assert result["valid"] is False and result["errors"]


def test_automated_qc_cannot_override_script_mismatch(tmp_path):
    job, plan, _, _ = _job(tmp_path)
    review_path = tmp_path / "review.json"
    initialize_review(job, review_path)
    plan.scenes[0].voice_script += " Thay đổi."
    (job / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    review = load_review(review_path)
    review.technical_validation["production_status"] = "completed"
    result = validate_review(job, review)
    assert result["valid"] is False
    assert result["canonical_script_identity"]["valid"] is False


def _copy_acceptance_config(repo: Path):
    (repo / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='0'\n")
    suite = repo / DEFAULT_ACCEPTANCE_SUITE_PATH
    script = repo / "configs/acceptance/scripts/phone_out_of_reach_v1.txt"
    suite.parent.mkdir(parents=True, exist_ok=True)
    script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SUITE_PATH, suite)
    shutil.copyfile(SCRIPT_PATH, script)
    return suite, script


def test_release_preflight_script_check_passes_without_credentials_or_sockets(monkeypatch):
    forbidden = lambda *a, **k: pytest.fail("provider or socket called")
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "CF_AI_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    checks = release_preflight.runtime_contract_checks(ROOT)
    result = {item.name: item.result for item in checks}
    assert result["canonical_acceptance_script"] == "PASS"


@pytest.mark.parametrize("mode", ["modified", "missing"])
def test_release_preflight_fails_closed_for_script_change(tmp_path, mode):
    repo = tmp_path / "repo"
    repo.mkdir()
    _, script = _copy_acceptance_config(repo)
    if mode == "modified":
        script.write_text("changed\n", encoding="utf-8")
    else:
        script.unlink()
    checks = release_preflight.runtime_contract_checks(repo)
    result = {item.name: item.result for item in checks}
    assert result["canonical_acceptance_script"] == "FAIL"


def test_canonical_production_dry_run_records_identity_and_no_media(
    tmp_path, monkeypatch
):
    class LegacyConsole:
        encoding = "cp1252"

        def write(self, value):
            value.encode(self.encoding)
            return len(value)

        def flush(self):
            return None

    forbidden = lambda *a, **k: pytest.fail("provider, socket, media, or render called")
    monkeypatch.setattr(cli, "run_pipeline", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(sys, "stdout", LegacyConsole())
    result = cli.main([
        "--recipe", "practical_life_steps_callirrhoe_v1",
        "--topic", PHONE_TOPIC,
        "--script-file", str(SCRIPT_PATH),
        "--lang", "vi",
        "--out", str(tmp_path),
        "--job-id", "script_validation",
        "--max-ai-images", "7",
        "--max-tts-requests", "1",
        "--no-tts-retry",
        "--production-dry-run",
    ])
    assert result == 0
    job = tmp_path / "script_validation"
    assert {path.name for path in job.iterdir()} == {
        "production_manifest.json", "production_summary.json",
        "recipe.json", "request_envelope.json",
    }
    envelope = json.loads((job / "request_envelope.json").read_text(encoding="utf-8"))
    identity = envelope["canonical_script_identity"]
    assert identity["canonical_script_sha256"] == SCRIPT_HASH
    assert identity["canonical_script_sentences"] == list(_script().sentences)
    assert identity["script_version"] == 1
    assert envelope["effective_voice_rate"] == "+0%"
    assert envelope["maximum_gemini_requests"] == 1
    assert envelope["maximum_image_requests"] == 7
    assert envelope["external_calls_performed"] == 0
    assert envelope["render_operations_performed"] == 0
    for reserved in (
        ROOT / "out/acceptance/practical_life_steps_visual_v1/phone_focus_dryrun_02",
        ROOT / "out/acceptance/practical_life_steps_visual_v1/phone_focus_source_02",
        ROOT / "out/acceptance/practical_life_steps_visual_v1/reviews/phone_focus_source_02_review_v1.json",
    ):
        assert not reserved.exists()
