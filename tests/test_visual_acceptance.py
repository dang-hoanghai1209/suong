import json
import socket
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from tella.visual_acceptance import (
    AcceptanceSuite, AcceptanceThresholds, CharacterConsistency, JobDecision,
    JobReview, RequestedActionMatch, SceneDecision, SceneReview,
    StyleComposition, UnwantedReadableText, aggregate_job,
    corrections_from_review, load_suite, report_acceptance,
)


SUITE_PATH = Path(__file__).resolve().parents[1] / "configs/acceptance/practical_life_steps_visual_v1.json"


def _scene(job, index, **overrides):
    image = job / "assets" / f"scene_{index:02d}.jpg"
    image.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 12), (index * 20, 80, 100)).save(image)
    from tella.production import file_sha256
    values = dict(
        job_id=job.name, scene_index=index, scene_role="practical_step",
        image_path=f"assets/scene_{index:02d}.jpg", image_sha256=file_sha256(image),
        plan_or_narration_sentence_hash=f"sentence-{index}",
        requested_action="place phone away", automated_qc_result={"status": "passed"},
        requested_action_match="pass", character_consistency="pass",
        unwanted_readable_text="absent", style_and_composition="pass",
        overall_scene_decision="accept", reviewer_notes="local review",
        reviewed_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    values.update(overrides)
    return SceneReview(**values)


def _review(job, **scene_override):
    return JobReview(job_id=job.name, technical_validation={
        "production_status": "completed", "audio_qc": "passed", "video_qc": "passed",
    }, scenes=[_scene(job, index, **(scene_override if index == 4 else {})) for index in range(1, 8)])


def _small_suite(case_ids=("case_one",), **threshold_overrides):
    thresholds = AcceptanceThresholds(**threshold_overrides).model_dump()
    return AcceptanceSuite.model_validate({
        "schema_version": 1, "suite_id": "test_suite", "acceptance_policy_version": 1,
        "thresholds": thresholds,
        "cases": [{
            "case_id": case_id, "topic": "Chủ đề an toàn",
            "expected_recipe": "practical_life_steps_callirrhoe_v1",
            "expected_scene_count": 7, "intended_visual_challenge": "object state",
            "important_object_states": ["phone away"], "high_risk_scenes": [4],
            "expected_request_budget": 7, "manual_review_required": True,
        } for case_id in case_ids],
    })


def _write_review(root, case_id, review):
    job = root / case_id
    if job != Path(review.scenes[0].image_path).parent:
        pass
    (job / "visual_acceptance_review.json").write_text(review.model_dump_json(indent=2), encoding="utf-8")


def test_versioned_suite_has_unique_safe_production_cases():
    suite = load_suite(SUITE_PATH)
    assert len(suite.cases) == 11
    assert len({case.case_id for case in suite.cases}) == 11
    assert all(case.expected_recipe == "practical_life_steps_callirrhoe_v1" for case in suite.cases)
    assert all(case.expected_scene_count == 7 and case.manual_review_required for case in suite.cases)


def test_empty_suite_is_rejected():
    with pytest.raises(ValueError, match="at least one"):
        AcceptanceSuite.model_validate({"schema_version": 1, "suite_id": "empty",
            "acceptance_policy_version": 1, "thresholds": {}, "cases": []})


def test_missing_reviews_and_automated_qc_only_are_incomplete(tmp_path):
    suite = _small_suite()
    report = report_acceptance(suite, tmp_path)
    assert report["status"] == "incomplete_review" and report["exit_code"] == 2
    job = tmp_path / "case_one"
    review = _review(job)
    for scene in review.scenes:
        scene.requested_action_match = RequestedActionMatch.not_reviewed
        scene.character_consistency = CharacterConsistency.not_reviewed
        scene.unwanted_readable_text = UnwantedReadableText.not_reviewed
        scene.style_and_composition = StyleComposition.not_reviewed
        scene.overall_scene_decision = SceneDecision.not_reviewed
        scene.reviewed_at_utc = ""
    (job / "visual_acceptance_review.json").write_text(review.model_dump_json())
    report = report_acceptance(suite, tmp_path)
    assert report["status"] == "incomplete_review"
    assert report["automated_qc_is_human_approval"] is False


@pytest.mark.parametrize("override", [
    {"requested_action_match": "hard_fail"},
    {"unwanted_readable_text": "present"},
    {"character_consistency": "fail"},
    {"style_and_composition": "fail"},
    {"overall_scene_decision": "regenerate"},
])
def test_hard_visual_failures_reject_job(tmp_path, override):
    job = tmp_path / "job"
    review = _review(job, **override)
    result = aggregate_job(job, review, AcceptanceThresholds())
    assert result["status"] == JobDecision.rejected


def test_soft_fail_and_warnings_are_conditional(tmp_path):
    job = tmp_path / "job"
    result = aggregate_job(job, _review(job, requested_action_match="soft_fail"), AcceptanceThresholds())
    assert result["status"] == JobDecision.conditionally_accepted
    permissive = AcceptanceThresholds(soft_fail_is_conditional=False)
    assert aggregate_job(job, _review(job, requested_action_match="soft_fail"), permissive)["status"] == JobDecision.accepted


def test_every_scene_must_be_reviewed(tmp_path):
    job = tmp_path / "job"
    review = _review(job)
    review.scenes.pop()
    result = aggregate_job(job, review, AcceptanceThresholds())
    assert result["status"] == JobDecision.incomplete_review
    assert result["missing_review_scene_indices"] == [7]


def test_stale_or_regenerated_image_requires_new_review(tmp_path):
    job = tmp_path / "job"
    review = _review(job)
    (job / review.scenes[3].image_path).write_bytes(b"regenerated")
    result = aggregate_job(job, review, AcceptanceThresholds())
    assert result["status"] == JobDecision.incomplete_review
    assert result["stale_scene_indices"] == [4]


def test_acceptance_report_pass_fail_and_atomic_output(tmp_path):
    suite = _small_suite()
    job = tmp_path / "case_one"
    review = _review(job)
    (job / "visual_acceptance_review.json").write_text(review.model_dump_json())
    output = tmp_path / "report.json"
    passed = report_acceptance(suite, tmp_path, output)
    assert passed["status"] == "accepted" and passed["exit_code"] == 0
    assert passed["command_status"] == "completed"
    assert passed["threshold_result"] == "passed"
    assert passed["human_acceptance_result"] == "accepted"
    assert passed["release_approved"] is True
    assert json.loads(output.read_text())["status"] == "accepted"
    assert not list(tmp_path.glob("*.tmp"))
    failed_review = _review(job, unwanted_readable_text="present")
    (job / "visual_acceptance_review.json").write_text(failed_review.model_dump_json())
    failed = report_acceptance(suite, tmp_path, output)
    assert failed["status"] == "rejected" and failed["exit_code"] == 1
    assert failed["release_approved"] is False
    assert "unwanted-text rate" in failed["threshold_failures"]


def test_character_failure_contributes_to_configured_threshold(tmp_path):
    suite = _small_suite(maximum_character_consistency_fail_rate=0.0)
    job = tmp_path / "case_one"
    review = _review(job, character_consistency="fail")
    (job / "visual_acceptance_review.json").write_text(review.model_dump_json())
    report = report_acceptance(suite, tmp_path)
    assert report["metrics"]["character_consistency_fail_rate"] == pytest.approx(1 / 7)
    assert "character-consistency fail rate" in report["threshold_failures"]


def test_review_json_is_deterministic_and_notes_are_safe(tmp_path):
    job = tmp_path / "job"
    review = _review(job)
    first = review.model_dump_json(indent=2)
    second = JobReview.model_validate_json(first).model_dump_json(indent=2)
    assert first == second
    with pytest.raises(ValueError, match="provider configuration"):
        _scene(job, 8, reviewer_notes="provider=alternate")


def test_correction_template_is_local_hash_bound_and_editable(tmp_path):
    job = tmp_path / "job"
    review = _review(job, overall_scene_decision="regenerate",
        regeneration_reason="action_mismatch", unwanted_readable_text="present",
        must_show_suggestions=["phone resting away"],
        must_not_show_suggestions=["phone in hand"])
    output = tmp_path / "corrections.json"
    result = corrections_from_review(review, output)
    assert len(result["corrections"]) == 1
    correction = result["corrections"][0]
    assert correction["scene_index"] == 4 and correction["forbidden_text"] is True
    assert correction["source_image_sha256"] == review.scenes[3].image_sha256
    assert result["human_edits_required_before_regeneration"] is True


def test_acceptance_operations_make_no_socket_or_provider_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("socket")))
    suite = _small_suite()
    report = report_acceptance(suite, tmp_path)
    assert report["status"] == "incomplete_review"
    assert report["command_status"] == "completed"
    assert report["threshold_result"] == "incomplete"
    assert report["release_approved"] is False


def test_conditionally_accepted_is_not_release_approved(tmp_path):
    suite = _small_suite()
    job = tmp_path / "case_one"
    review = _review(job, requested_action_match="soft_fail")
    (job / "visual_acceptance_review.json").write_text(review.model_dump_json())
    report = report_acceptance(suite, tmp_path)
    assert report["exit_code"] == 0 and report["command_status"] == "completed"
    assert report["human_acceptance_result"] == "conditionally_accepted"
    assert report["threshold_result"] == "passed"
    assert report["release_approved"] is False
