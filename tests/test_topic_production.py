"""Contract tests for the offline topic-aware production foundation."""
from __future__ import annotations

import json

import pytest

from tella.topic_production import (
    AcceptancePriority,
    CandidateArtifact,
    DeterministicTopicPlanner,
    DualTierPolicy,
    GenerationTier,
    ProductionSceneStatus,
    SceneComplexity,
    build_initial_manifest,
    build_scene_briefs,
    evaluate_render_readiness,
    refresh_manifest_readiness,
    validate_topic_fidelity,
)
from tella.topic_production.cli import main
from tella.topic_production.models import SceneQCRecord
from tella.topic_production.state import (
    accept_candidate,
    initialize_scenes,
    record_draft_candidate,
    tier_decision_for_brief,
)
from tella.topic_production.timing import allocate_durations


TOPIC = "học cách tin tưởng lại sau một mùa khó khăn"


def _plan(scene_count: int = 8, duration: float = 35.0):
    return DeterministicTopicPlanner().plan(
        topic=TOPIC,
        scene_count=scene_count,
        target_duration_seconds=duration,
    )


def _candidate(scene_id: str, tier: GenerationTier) -> CandidateArtifact:
    return CandidateArtifact(
        candidate_id=f"{scene_id}-{tier.value}",
        tier=tier,
        provider="offline-test-provider",
        model=f"fixture-{tier.value}",
        seed=17,
        path=f"artifacts/{scene_id}/{tier.value}.png",
        sha256="a" * 64,
        request_hash="b" * 64,
        reference_hashes=["c" * 64],
    )


def _qc(tier: GenerationTier, passed: bool = True) -> SceneQCRecord:
    return SceneQCRecord(
        tier=tier,
        passed=passed,
        reviewer="deterministic-test-qc",
        reasons=[] if passed else ["composition failed"],
        scores={"semantic_fidelity": 1.0 if passed else 0.0},
    )


@pytest.mark.parametrize(("scene_count", "duration"), [(7, 32.0), (8, 38.0)])
def test_valid_story_plans_have_ordered_unique_semantic_beats(
    scene_count: int, duration: float
) -> None:
    plan = _plan(scene_count, duration)

    assert len(plan.semantic_beats) == scene_count
    assert [beat.order for beat in plan.semantic_beats] == list(range(1, scene_count + 1))
    assert [beat.beat_id for beat in plan.semantic_beats] == [
        f"beat_{order:02d}" for order in range(1, scene_count + 1)
    ]
    assert len({beat.beat_id for beat in plan.semantic_beats}) == scene_count
    assert plan.narration_text == " ".join(
        beat.narration_segment for beat in plan.semantic_beats
    )
    assert sum(beat.duration_seconds for beat in plan.semantic_beats) == pytest.approx(duration)
    assert all(3.0 <= beat.duration_seconds <= 5.0 for beat in plan.semantic_beats)


def test_planner_is_deterministic_for_the_same_inputs() -> None:
    first = _plan()
    second = _plan()

    assert first == second
    assert first.planner_metadata.external_calls == 0


def test_different_topics_materially_change_the_plan() -> None:
    first = _plan()
    second = DeterministicTopicPlanner().plan(
        topic="tìm lại niềm vui trong những việc nhỏ mỗi ngày",
        scene_count=8,
        target_duration_seconds=35.0,
    )

    assert first.planner_metadata.deterministic_key != second.planner_metadata.deterministic_key
    assert first.topic_intent != second.topic_intent
    assert [beat.narration_segment for beat in first.semantic_beats] != [
        beat.narration_segment for beat in second.semantic_beats
    ]
    assert [beat.visual_intent for beat in first.semantic_beats] != [
        beat.visual_intent for beat in second.semantic_beats
    ]


def test_each_beat_maps_one_to_one_to_a_structured_scene_brief() -> None:
    plan = _plan()
    briefs = build_scene_briefs(plan)

    assert len(briefs) == len(plan.semantic_beats)
    for beat, brief in zip(plan.semantic_beats, briefs, strict=True):
        assert brief.order == beat.order
        assert brief.source_beat_id == beat.beat_id
        assert brief.narrative_text == beat.narration_segment
        assert brief.meaning == beat.semantic_purpose
        assert brief.topic_intent == plan.topic_intent
        assert brief.duration_seconds == beat.duration_seconds
        assert brief.composition and brief.visual_hierarchy and brief.hard_negatives
        assert brief.reference_strategy.accepted_scene_chaining is False


def test_topic_fidelity_passes_and_fixed_demo_content_does_not_leak() -> None:
    plan = _plan()
    briefs = build_scene_briefs(plan)
    report = validate_topic_fidelity(plan, briefs)
    serialized = json.dumps(
        {"plan": plan.model_dump(mode="json"), "briefs": [b.model_dump(mode="json") for b in briefs]},
        ensure_ascii=False,
    ).casefold()

    assert report.passed
    assert all(report.signals.values())
    assert "four_scene_proof_v1" not in serialized
    assert "scene_01_style_anchor" not in serialized
    assert "daily-life self-company can become quietly content" not in serialized


def test_topic_fidelity_reports_structural_semantic_failure() -> None:
    plan = _plan()
    metadata = plan.planner_metadata.model_copy(
        update={"topic_concepts": ["concept-that-is-absent"]}
    )
    inconsistent = plan.model_copy(update={"planner_metadata": metadata})

    report = validate_topic_fidelity(inconsistent, build_scene_briefs(inconsistent))

    assert not report.passed
    assert not report.signals["topic_concepts_propagated"]
    assert "topic_concepts_propagated" in report.issues


@pytest.mark.parametrize(("scene_count", "duration"), [(7, 35.0), (8, 35.0)])
def test_manifest_timings_are_positive_contiguous_and_total_target(
    scene_count: int, duration: float
) -> None:
    plan = _plan(scene_count, duration)
    manifest = build_initial_manifest(
        job_id="offline-contract", plan=plan, briefs=build_scene_briefs(plan)
    )

    assert manifest.timings[0].start_seconds == 0
    assert manifest.timings[-1].end_seconds == duration
    assert [timing.order for timing in manifest.timings] == list(range(1, scene_count + 1))
    assert all(timing.duration_seconds > 0 for timing in manifest.timings)
    assert all(
        left.end_seconds == right.start_seconds
        for left, right in zip(manifest.timings, manifest.timings[1:], strict=False)
    )


@pytest.mark.parametrize(
    ("scene_count", "duration"),
    [(6, 35.0), (9, 35.0), (7, 31.9), (8, 38.1), (7, 38.0)],
)
def test_incompatible_timing_requests_fail_closed(scene_count: int, duration: float) -> None:
    with pytest.raises(ValueError):
        allocate_durations(scene_count, duration)


def test_initial_manifest_is_traceable_blocked_and_provider_free() -> None:
    plan = _plan()
    briefs = build_scene_briefs(plan)
    manifest = build_initial_manifest(job_id="offline-contract", plan=plan, briefs=briefs)

    assert manifest.topic == TOPIC
    assert [scene.brief.source_beat_id for scene in manifest.scenes] == [
        beat.beat_id for beat in plan.semantic_beats
    ]
    assert all(scene.status is ProductionSceneStatus.DRAFT_PENDING for scene in manifest.scenes)
    assert not manifest.render_ready
    assert set(manifest.blocked_reasons) == {brief.scene_id for brief in briefs}
    assert manifest.metadata["external_calls"] == 0
    assert manifest.narration_path is None
    assert manifest.subtitle_path is None
    assert manifest.video_path is None


def test_manifest_rejects_out_of_order_or_incomplete_brief_mapping() -> None:
    plan = _plan()
    briefs = build_scene_briefs(plan)

    with pytest.raises(ValueError, match="one-to-one"):
        build_initial_manifest(job_id="bad", plan=plan, briefs=briefs[:-1])
    with pytest.raises(ValueError, match="one-to-one"):
        build_initial_manifest(job_id="bad", plan=plan, briefs=list(reversed(briefs)))


def test_recording_a_draft_never_automatically_accepts_it() -> None:
    scene = initialize_scenes(build_scene_briefs(_plan())[:1], DualTierPolicy())[0]
    candidate = _candidate(scene.brief.scene_id, GenerationTier.DRAFT)

    updated = record_draft_candidate(scene, candidate)

    assert updated.status is ProductionSceneStatus.DRAFT_GENERATED
    assert updated.draft_candidate == candidate
    assert updated.accepted_candidate is None
    assert updated.accepted_source_tier is None


def test_complex_scene_requires_acceptance_tier_and_explicit_qc() -> None:
    brief = build_scene_briefs(_plan())[0].model_copy(
        update={"complexity": SceneComplexity.COMPLEX}
    )
    scene = initialize_scenes([brief], DualTierPolicy())[0]
    draft = _candidate(brief.scene_id, GenerationTier.DRAFT)

    assert scene.tier_decision.dev_acceptance_recommended
    assert not scene.tier_decision.draft_only_acceptance_allowed_after_explicit_qc
    with pytest.raises(ValueError, match="acceptance-tier"):
        accept_candidate(scene, draft, _qc(GenerationTier.DRAFT))
    with pytest.raises(ValueError, match="passing explicit QC"):
        accept_candidate(
            scene,
            _candidate(brief.scene_id, GenerationTier.ACCEPTANCE),
            _qc(GenerationTier.ACCEPTANCE, passed=False),
        )


def test_simple_scene_can_use_draft_only_after_explicit_qc() -> None:
    brief = build_scene_briefs(_plan())[0].model_copy(
        update={
            "complexity": SceneComplexity.SIMPLE,
            "acceptance_priority": AcceptancePriority.STANDARD,
        }
    )
    decision = tier_decision_for_brief(brief, DualTierPolicy())
    scene = initialize_scenes([brief], DualTierPolicy())[0]
    draft = _candidate(brief.scene_id, GenerationTier.DRAFT)

    assert decision.draft_only_acceptance_allowed_after_explicit_qc
    accepted = accept_candidate(scene, draft, _qc(GenerationTier.DRAFT))
    assert accepted.status is ProductionSceneStatus.ACCEPTED
    assert accepted.accepted_source_tier is GenerationTier.DRAFT
    assert accepted.qc_records[-1].passed


def test_seven_of_eight_accepted_scenes_still_block_render() -> None:
    plan = _plan()
    scenes = initialize_scenes(build_scene_briefs(plan), DualTierPolicy())
    for index in range(7):
        artifact = _candidate(scenes[index].brief.scene_id, GenerationTier.ACCEPTANCE)
        scenes[index] = accept_candidate(scenes[index], artifact, _qc(GenerationTier.ACCEPTANCE))

    readiness = evaluate_render_readiness(scenes)

    assert not readiness.ready
    assert readiness.unresolved_scene_ids == ["scene_08"]
    assert "not ACCEPTED" in " ".join(readiness.reasons["scene_08"])
    assert "artifact is missing" in " ".join(readiness.reasons["scene_08"])


def test_all_eight_accepted_artifacts_allow_render_and_preserve_tier_source() -> None:
    plan = _plan()
    manifest = build_initial_manifest(
        job_id="ready", plan=plan, briefs=build_scene_briefs(plan)
    )
    for index, scene in enumerate(manifest.scenes):
        artifact = _candidate(scene.brief.scene_id, GenerationTier.ACCEPTANCE)
        manifest.scenes[index] = accept_candidate(
            scene, artifact, _qc(GenerationTier.ACCEPTANCE)
        )

    refreshed = refresh_manifest_readiness(manifest)

    assert refreshed.render_ready
    assert refreshed.blocked_reasons == {}
    assert all(
        scene.accepted_candidate is not None
        and scene.accepted_source_tier is GenerationTier.ACCEPTANCE
        for scene in refreshed.scenes
    )


def test_accepted_status_without_artifact_record_remains_blocked() -> None:
    scene = initialize_scenes(build_scene_briefs(_plan())[:1], DualTierPolicy())[0]
    inconsistent = scene.model_copy(
        update={
            "status": ProductionSceneStatus.ACCEPTED,
            "accepted_source_tier": GenerationTier.ACCEPTANCE,
        }
    )

    readiness = evaluate_render_readiness([inconsistent])

    assert not readiness.ready
    assert readiness.reasons[scene.brief.scene_id] == [
        "accepted candidate artifact is missing"
    ]


def test_preview_cli_is_offline_and_emits_contract_json(capsys: pytest.CaptureFixture[str]) -> None:
    result = main(
        [
            "--topic",
            TOPIC,
            "--scene-count",
            "7",
            "--target-duration",
            "35",
            "--job-id",
            "cli-offline",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert len(payload["story_plan"]["semantic_beats"]) == 7
    assert len(payload["scene_briefs"]) == 7
    assert set(payload["initial_states"].values()) == {"DRAFT_PENDING"}
    assert payload["topic_fidelity"]["passed"]
    assert payload["render_readiness"] is False
    assert payload["external_calls"] == 0
