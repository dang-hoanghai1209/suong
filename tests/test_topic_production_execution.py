"""Phase 2 tests for offline topic-to-visual execution planning."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from tella.topic_production import (
    DeterministicTopicPlanner,
    ApprovedReferenceValidationError,
    ExecutionMode,
    PlannerMode,
    ProductionSceneStatus,
    ReferenceDecisionStatus,
    SceneComplexity,
    SceneType,
    adapt_scene_brief,
    build_fixture_preview_run,
    build_production_run_plan,
    build_scene_briefs,
    deterministic_scene_seed,
    load_reference_catalog,
    resolve_references,
)
from tella.topic_production.cli import main
from tella.visual_generation.providers.cloudflare_flux import DEV_MODEL, KLEIN_4B_MODEL
from tella.visual_generation.references import REFERENCE_FILES
from tella.visual_generation.references import sha256_file
import tella.topic_production.reference_planning as reference_planning


TOPIC_A = "Ở một mình không có nghĩa là cô đơn."
TOPIC_B = "Học cách buông bỏ một người không còn yêu mình."


def _reference_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "approved-references"
    root.mkdir()
    for definition in reference_planning.APPROVED_REFERENCE_DEFINITIONS:
        (root / definition.filename).write_bytes(
            f"approved fixture: {definition.filename}".encode()
        )
    definitions = tuple(
        replace(
            definition,
            expected_sha256=sha256_file(root / definition.filename),
        )
        for definition in reference_planning.APPROVED_REFERENCE_DEFINITIONS
    )
    monkeypatch.setattr(reference_planning, "APPROVED_REFERENCE_DEFINITIONS", definitions)
    return root


@pytest.mark.parametrize("scene_count", [7, 8])
def test_fixture_run_plan_is_deterministic_and_clearly_non_production(
    scene_count: int,
) -> None:
    first = build_fixture_preview_run(topic=TOPIC_A, scene_count=scene_count, job_id="stable")
    second = build_fixture_preview_run(topic=TOPIC_A, scene_count=scene_count, job_id="stable")

    assert first == second
    assert first.plan_label == "OFFLINE_FIXTURE_PREVIEW"
    assert first.story_plan.planner_metadata.planner_mode is PlannerMode.FIXTURE
    assert first.story_plan.planner_metadata.production_eligible is False
    assert first.external_calls == 0
    assert len(first.scene_execution_plans) == scene_count


def test_live_mode_rejects_fixture_planner() -> None:
    story = DeterministicTopicPlanner().plan(topic=TOPIC_A)

    with pytest.raises(ValueError, match="production-eligible planner"):
        build_production_run_plan(
            job_id="must-fail",
            story_plan=story,
            scene_briefs=build_scene_briefs(story),
            reference_catalog=load_reference_catalog(None),
            execution_mode=ExecutionMode.LIVE_PRODUCTION,
        )


def test_typed_production_capability_can_enter_live_planning_without_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = DeterministicTopicPlanner().plan(topic=TOPIC_A)
    metadata = fixture.planner_metadata.model_copy(
        update={"planner_mode": PlannerMode.PRODUCTION, "production_eligible": True}
    )
    production_story = fixture.model_copy(update={"planner_metadata": metadata})

    run = build_production_run_plan(
        job_id="typed-production-contract",
        story_plan=production_story,
        scene_briefs=build_scene_briefs(production_story),
        reference_catalog=load_reference_catalog(_reference_root(tmp_path, monkeypatch)),
        execution_mode=ExecutionMode.LIVE_PRODUCTION,
    )

    assert run.plan_label == "PRODUCTION_RUN_PLAN"
    assert run.external_calls == 0


def test_scene_one_through_eight_seeds_extend_legacy_sequence() -> None:
    assert [deterministic_scene_seed(index) for index in range(1, 9)] == [
        10101,
        10202,
        10303,
        10404,
        10505,
        10606,
        10707,
        10808,
    ]
    with pytest.raises(ValueError):
        deterministic_scene_seed(9)


def test_different_topics_materially_change_execution_plans() -> None:
    first = build_fixture_preview_run(topic=TOPIC_A, job_id="comparison")
    second = build_fixture_preview_run(topic=TOPIC_B, job_id="comparison")

    assert first.planning_hash != second.planning_hash
    assert [scene.scene_brief.meaning for scene in first.scene_execution_plans] != [
        scene.scene_brief.meaning for scene in second.scene_execution_plans
    ]
    assert [scene.scene_brief.scene_type for scene in first.scene_execution_plans] != [
        scene.scene_brief.scene_type for scene in second.scene_execution_plans
    ]


def test_every_brief_maps_to_one_unaccepted_draft_pending_execution() -> None:
    run = build_fixture_preview_run(topic=TOPIC_A)

    assert [scene.scene_id for scene in run.scene_execution_plans] == [
        brief.scene_id for brief in run.manifest.scene_briefs
    ]
    assert all(
        scene.initial_status is ProductionSceneStatus.DRAFT_PENDING
        for scene in run.scene_execution_plans
    )
    assert all(scene.acceptance_policy.automatic_acceptance is False for scene in run.scene_execution_plans)
    assert all(item.accepted_candidate is None for item in run.manifest.scenes)


def test_tiers_resolve_from_validated_visual_configuration() -> None:
    run = build_fixture_preview_run(topic=TOPIC_A)

    for scene in run.scene_execution_plans:
        assert (scene.draft.provider, scene.draft.model) == (
            "cloudflare-flux",
            KLEIN_4B_MODEL,
        )
        assert (scene.draft.steps, scene.draft.timeout_seconds) == (4, 120.0)
        assert (scene.draft.width, scene.draft.height) == (576, 1024)
        assert (scene.acceptance.model, scene.acceptance.steps) == (DEV_MODEL, 25)
        assert scene.acceptance.timeout_seconds == 300.0
        assert scene.acceptance.promotion_requires_explicit_qc
        assert scene.acceptance.seed_policy == "reuse_stable_scene_seed"
        assert scene.acceptance.references == scene.draft.references
        assert scene.acceptance.reference_decisions == scene.draft.reference_decisions
        assert scene.draft.accepted_scene_chaining is False
        assert scene.acceptance.accepted_scene_chaining is False
        assert scene.draft.provider_request_hash is None
        assert scene.acceptance.provider_request_hash is None


def test_catalog_contains_only_physical_approved_static_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = load_reference_catalog(None)
    catalog = load_reference_catalog(_reference_root(tmp_path, monkeypatch))

    assert empty.references == []
    assert set(empty.unavailable_roles) == set(REFERENCE_FILES)
    assert len(catalog.references) == 4
    assert all(Path(item.path).is_file() for item in catalog.references)
    assert all(len(item.sha256) == 64 for item in catalog.references)
    assert catalog.generated_assets_authoritative is False
    style = next(item for item in catalog.references if item.reference_id == "scene_01_style_anchor")
    assert style.roles == ["female_identity_anchor", "style_anchor"]


def test_catalog_validates_actual_sha_and_configured_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _reference_root(tmp_path, monkeypatch)
    catalog = load_reference_catalog(root)

    for reference in catalog.references:
        path = Path(reference.path)
        assert path.parent == root.resolve()
        assert reference.sha256 == sha256_file(path)
        assert reference.metadata["validated_sha256"] is True


def test_changed_bytes_under_approved_filename_fail_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _reference_root(tmp_path, monkeypatch)
    (root / "scene_01_style_anchor.png").write_bytes(b"changed after approval")

    with pytest.raises(ApprovedReferenceValidationError, match="hash mismatch"):
        load_reference_catalog(root)


def test_missing_required_identity_and_style_are_typed_blocking_decisions() -> None:
    brief = build_scene_briefs(DeterministicTopicPlanner().plan(topic=TOPIC_A))[0]

    references, decisions = resolve_references(brief, load_reference_catalog(None))

    assert references == []
    assert {item.status for item in decisions} == {
        ReferenceDecisionStatus.REFERENCE_BLOCKED_REQUIRED_IDENTITY,
        ReferenceDecisionStatus.REFERENCE_BLOCKED_REQUIRED_STYLE,
    }


def test_live_production_stops_when_required_identity_is_missing() -> None:
    fixture = DeterministicTopicPlanner().plan(topic=TOPIC_A)
    metadata = fixture.planner_metadata.model_copy(
        update={"planner_mode": PlannerMode.PRODUCTION, "production_eligible": True}
    )
    production_story = fixture.model_copy(update={"planner_metadata": metadata})

    with pytest.raises(ValueError, match="REFERENCE_BLOCKED_REQUIRED_IDENTITY"):
        build_production_run_plan(
            job_id="blocked-reference",
            story_plan=production_story,
            scene_briefs=build_scene_briefs(production_story),
            reference_catalog=load_reference_catalog(None),
            execution_mode=ExecutionMode.LIVE_PRODUCTION,
        )


def test_identity_then_style_then_composition_priority_is_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    story = DeterministicTopicPlanner().plan(topic=TOPIC_A)
    metaphor = next(
        brief
        for brief in build_scene_briefs(story)
        if brief.scene_type is SceneType.EMOTIONAL_METAPHOR
    )
    references, decisions = resolve_references(
        metaphor, load_reference_catalog(_reference_root(tmp_path, monkeypatch))
    )

    assert [decision.priority for decision in decisions] == sorted(
        decision.priority for decision in decisions
    )
    assert decisions[0].role == "female_identity_anchor"
    assert [decision.role for decision in decisions][-1] == "emotional_metaphor_reference"
    assert all(decision.status is ReferenceDecisionStatus.SELECTED for decision in decisions)
    assert references


def test_style_anchor_is_stable_and_duplicate_physical_asset_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    story = DeterministicTopicPlanner().plan(topic=TOPIC_A)
    briefs = build_scene_briefs(story)
    catalog = load_reference_catalog(_reference_root(tmp_path, monkeypatch))
    resolutions = [resolve_references(brief, catalog) for brief in briefs]
    style_ids = [
        next(decision.reference_id for decision in decisions if decision.role == "style_anchor")
        for _, decisions in resolutions
    ]

    assert len(set(style_ids)) == 1
    for references, _ in resolutions:
        assert len({reference.sha256 for reference in references}) == len(references)


def test_optional_missing_composition_reference_is_explicit_and_does_not_crash() -> None:
    story = DeterministicTopicPlanner().plan(topic=TOPIC_A)
    symbolic = build_scene_briefs(story)[0].model_copy(
        update={"scene_type": SceneType.SYMBOLIC_CHOICE}
    )

    references, decisions = resolve_references(symbolic, load_reference_catalog(None))
    symbolic_decision = next(item for item in decisions if item.role == "symbolic_reference")

    assert references == []
    assert symbolic_decision.status is ReferenceDecisionStatus.NO_COMPOSITION_REFERENCE_AVAILABLE
    assert symbolic_decision.path is None
    assert symbolic_decision.sha256 is None


def test_identity_style_resolve_while_optional_symbolic_composition_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = build_scene_briefs(DeterministicTopicPlanner().plan(topic=TOPIC_A))[0]
    symbolic = source.model_copy(update={"scene_type": SceneType.SYMBOLIC_CHOICE})

    references, decisions = resolve_references(
        symbolic, load_reference_catalog(_reference_root(tmp_path, monkeypatch))
    )

    assert [item.reference_id for item in references] == ["scene_01_style_anchor"]
    assert [item.status for item in decisions[:2]] == [
        ReferenceDecisionStatus.SELECTED,
        ReferenceDecisionStatus.SELECTED,
    ]
    assert decisions[-1].status is ReferenceDecisionStatus.NO_COMPOSITION_REFERENCE_AVAILABLE


def test_scene_types_resolve_only_semantically_compatible_approved_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = build_scene_briefs(DeterministicTopicPlanner().plan(topic=TOPIC_A))[0]
    catalog = load_reference_catalog(_reference_root(tmp_path, monkeypatch))
    cases = {
        SceneType.EMOTIONAL_METAPHOR: "scene_04_emotional_metaphor",
        SceneType.ORGANIC_DAILY_VIGNETTE: "scene_03_daily_vignette",
        SceneType.RELATIONSHIP_VIGNETTE: "scene_02_couple_anchor",
    }
    for scene_type, expected_id in cases.items():
        update = {"scene_type": scene_type}
        if scene_type is SceneType.RELATIONSHIP_VIGNETTE:
            update["characters"] = ["recurring_woman", "supporting_person"]
        brief = source.model_copy(update=update)
        references, _ = resolve_references(brief, catalog)
        assert expected_id in {item.reference_id for item in references}
        if scene_type is SceneType.RELATIONSHIP_VIGNETTE:
            assert references[0].reference_id == "scene_02_couple_anchor"

    solo_refs, _ = resolve_references(source, catalog)
    assert "scene_02_couple_anchor" not in {item.reference_id for item in solo_refs}


def test_scene_number_never_drives_reference_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = build_scene_briefs(DeterministicTopicPlanner().plan(topic=TOPIC_A))[0]
    scene_seven_metaphor = source.model_copy(
        update={"scene_id": "scene_07", "order": 7, "scene_type": SceneType.EMOTIONAL_METAPHOR}
    )
    references, _ = resolve_references(
        scene_seven_metaphor,
        load_reference_catalog(_reference_root(tmp_path, monkeypatch)),
    )

    assert "scene_04_emotional_metaphor" in {item.reference_id for item in references}
    assert "scene_02_couple_anchor" not in {item.reference_id for item in references}


def test_journey_uses_identity_style_but_not_unrelated_composition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = build_scene_briefs(DeterministicTopicPlanner().plan(topic=TOPIC_A))[0]
    journey = source.model_copy(update={"scene_type": SceneType.JOURNEY_TRANSITION})
    references, decisions = resolve_references(
        journey, load_reference_catalog(_reference_root(tmp_path, monkeypatch))
    )

    assert [item.reference_id for item in references] == ["scene_01_style_anchor"]
    assert decisions[-1].role == "composition_reference"
    assert decisions[-1].status is ReferenceDecisionStatus.NO_COMPOSITION_REFERENCE_AVAILABLE


def test_relationship_and_metaphor_types_drive_reference_roles() -> None:
    story = DeterministicTopicPlanner().plan(topic=TOPIC_A)
    source = build_scene_briefs(story)[0]
    relationship = source.model_copy(
        update={
            "scene_type": SceneType.RELATIONSHIP_VIGNETTE,
            "characters": ["recurring_woman", "supporting_person"],
        }
    )
    metaphor = source.model_copy(update={"scene_type": SceneType.EMOTIONAL_METAPHOR})

    relationship_roles = adapt_scene_brief(relationship).visual_scene.reference_roles
    metaphor_roles = adapt_scene_brief(metaphor).visual_scene.reference_roles

    assert relationship_roles[0] == "couple_identity_anchor"
    assert "emotional_metaphor_reference" in metaphor_roles


def test_simple_transition_uses_fewer_references_than_relationship(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    story = DeterministicTopicPlanner().plan(topic=TOPIC_A)
    source = build_scene_briefs(story)[0]
    simple = source.model_copy(
        update={"scene_type": SceneType.JOURNEY_TRANSITION, "complexity": SceneComplexity.SIMPLE}
    )
    relationship = source.model_copy(
        update={
            "scene_type": SceneType.RELATIONSHIP_VIGNETTE,
            "characters": ["recurring_woman", "supporting_person"],
        }
    )
    catalog = load_reference_catalog(_reference_root(tmp_path, monkeypatch))

    simple_refs, _ = resolve_references(simple, catalog)
    relationship_refs, _ = resolve_references(relationship, catalog)

    assert len(simple_refs) < len(relationship_refs)


def test_complex_and_closure_scenes_receive_high_acceptance_priority() -> None:
    run = build_fixture_preview_run(topic=TOPIC_A)

    for scene in run.scene_execution_plans:
        if scene.scene_brief.complexity is SceneComplexity.COMPLEX or (
            scene.scene_brief.scene_type is SceneType.CLOSURE_VIGNETTE
        ):
            assert scene.acceptance_policy.priority.value == "high"
            assert scene.acceptance_policy.dev_acceptance_recommended
        assert scene.acceptance_policy.automatic_acceptance is False


def test_adapter_preserves_all_critical_semantic_fields() -> None:
    brief = build_scene_briefs(DeterministicTopicPlanner().plan(topic=TOPIC_A))[0]
    adapted = adapt_scene_brief(brief)
    visual = adapted.visual_scene

    assert visual.narrative_text == brief.narrative_text
    assert visual.narrative_meaning == brief.meaning
    assert visual.action == brief.action
    assert visual.interaction == brief.interaction
    assert all(item in visual.environment_cues for item in brief.environment)
    assert all(f"integrated object: {item}" in visual.environment_cues for item in brief.objects)
    assert all(item in visual.symbolic_elements for item in brief.symbols)
    assert all(item in visual.composition for item in brief.composition)
    assert visual.negative_constraints == brief.hard_negatives
    assert adapted.preserved_semantics["identity_requirements"] == brief.identity_requirements
    assert adapted.preserved_semantics["continuity_requirements"] == brief.continuity_requirements
    assert adapted.preserved_semantics["source_beat_id"] == brief.source_beat_id
    assert adapted.prompt_profile == "topic_production_v1"


def test_pre_generation_manifest_is_serializable_traceable_and_fail_closed() -> None:
    run = build_fixture_preview_run(topic=TOPIC_A)
    serialized = json.dumps(run.model_dump(mode="json"), sort_keys=True)

    assert serialized
    assert run.manifest.render_ready is False
    assert len(run.manifest.blocked_reasons) == 8
    assert all(scene.status is ProductionSceneStatus.DRAFT_PENDING for scene in run.manifest.scenes)
    assert len(run.manifest.metadata["execution_plans"]) == 8
    assert all(
        item["provider_request_hash"] is None
        for item in run.manifest.metadata["execution_plans"]
    )
    assert all(len(item["logical_visual_request_hash"]) == 64 for item in run.manifest.metadata["execution_plans"])
    assert run.manifest.metadata["external_calls"] == 0


def test_plan_production_cli_emits_compact_offline_fixture_preview(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["plan-production", "--topic", TOPIC_A, "--scene-count", "8"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["plan_label"] == "OFFLINE_FIXTURE_PREVIEW"
    assert payload["planner_mode"] == "fixture"
    assert payload["production_eligible"] is False
    assert len(payload["scenes"]) == 8
    assert {scene["initial_state"] for scene in payload["scenes"]} == {"DRAFT_PENDING"}
    assert payload["render_readiness"] is False
    assert payload["external_calls"] == 0


def test_plan_production_cli_resolves_configured_reference_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _reference_root(tmp_path, monkeypatch)
    assert main(
        [
            "plan-production",
            "--topic",
            TOPIC_B,
            "--scene-count",
            "8",
            "--reference-root",
            str(root),
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert all(scene["references"] for scene in payload["scenes"])
    assert all(
        "scene_01_style_anchor" in {item["id"] for item in scene["references"]}
        for scene in payload["scenes"]
        if scene["type"] != "relationship_vignette"
    )
    assert payload["external_calls"] == 0
