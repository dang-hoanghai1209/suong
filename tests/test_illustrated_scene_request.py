"""Provider-neutral illustrated request and anchor authority contracts."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from tella.topic_production.execution import (
    build_fixture_preview_run,
    build_production_run_plan,
)
from tella.topic_production.execution_models import (
    ApprovedReference,
    LocalCompositionRequest,
    ReferenceCatalog,
    ReferenceDecisionStatus,
    VolumeLocalSceneInput,
)
from tella.topic_production.illustrated_request import (
    IllustratedReferenceAuthority,
    IllustratedReferenceBinding,
    IllustratedSceneRequest,
    build_illustrated_scene_request,
    illustrated_scene_request_hash,
)
from tella.topic_production.planner import (
    DeterministicTopicPlanner,
    build_scene_briefs,
)
from tella.topic_production.models import SceneType
from tella.topic_production.reference_planning import resolve_references
from tella.topic_production.strategy import (
    ProductionStrategyConfig,
    SceneDataSensitivity,
    VisualExecutionMode,
)
from tella.topic_production.visual_adapter import required_reference_roles


def _reference(
    reference_id: str,
    roles: list[str],
    sha256: str,
    *,
    sensitivity: SceneDataSensitivity = SceneDataSensitivity.PRIVATE,
    path: str | None = None,
) -> ApprovedReference:
    return ApprovedReference(
        reference_id=reference_id,
        path=path or f"C:/approved-private/{reference_id}.png",
        sha256=sha256,
        roles=roles,
        sensitivity=sensitivity,
        identity_scope=(
            "recurring_female"
            if any("identity_anchor" in role for role in roles)
            else None
        ),
        style_scope="soft_editorial_v1" if "style_anchor" in roles else None,
        priority=1,
    )


def _binding(
    reference: ApprovedReference,
    role: str,
) -> IllustratedReferenceBinding:
    authority = (
        IllustratedReferenceAuthority.STYLE
        if role == "style_anchor"
        else IllustratedReferenceAuthority.CHARACTER_IDENTITY
        if "identity_anchor" in role
        else IllustratedReferenceAuthority.COMPOSITION
    )
    return IllustratedReferenceBinding(
        authority=authority,
        declared_role=role,
        reference=reference,
    )


def _brief(index: int = 0):
    story = DeterministicTopicPlanner().plan(topic="Stable anchors, changing scenes.")
    return story, build_scene_briefs(story)[index]


def _request(
    *,
    style: ApprovedReference,
    identity: ApprovedReference,
    sensitivity: SceneDataSensitivity = SceneDataSensitivity.PRIVATE,
    scene_index: int = 0,
) -> IllustratedSceneRequest:
    _, brief = _brief(scene_index)
    identity_roles = [
        role for role in required_reference_roles(brief) if "identity_anchor" in role
    ]
    assert len(identity_roles) == 1
    identity_role = identity_roles[0]
    return IllustratedSceneRequest(
        scene_id=brief.scene_id,
        semantic_scene=brief,
        sensitivity=sensitivity,
        required_reference_roles=["style_anchor", identity_role],
        style_anchors=[_binding(style, "style_anchor")],
        character_identity_anchors=[_binding(identity, identity_role)],
    )


def _illustrated_plan(
    catalog: ReferenceCatalog,
    *,
    sensitivity: SceneDataSensitivity = SceneDataSensitivity.PRIVATE,
):
    fixture = build_fixture_preview_run(topic="Illustrated request integration", scene_count=7)
    return build_production_run_plan(
        job_id="illustrated-request-contract",
        story_plan=fixture.story_plan,
        scene_briefs=[scene.scene_brief for scene in fixture.scene_execution_plans],
        reference_catalog=catalog,
        production_strategy=ProductionStrategyConfig.volume(
            visual_mode=VisualExecutionMode.ILLUSTRATED_SCENE
        ),
        volume_local_inputs={
            "scene_01": VolumeLocalSceneInput(
                sensitivity=sensitivity,
                request=LocalCompositionRequest(
                    character_id="female_01",
                    action="sit_hug_knees",
                    emotion="sad",
                    location="bedroom",
                    time_of_day="night",
                    composition_preset="bedroom_floor_sitting",
                    seed=10101,
                ),
            )
        },
    )


def test_request_separates_style_identity_and_current_scene_semantics() -> None:
    style = _reference("style_master", ["style_anchor"], "a" * 64)
    identity = _reference(
        "female_master",
        ["female_identity_anchor"],
        "b" * 64,
    )
    request = _request(style=style, identity=identity)

    assert request.style_anchors[0].authority is IllustratedReferenceAuthority.STYLE
    assert (
        request.character_identity_anchors[0].authority
        is IllustratedReferenceAuthority.CHARACTER_IDENTITY
    )
    assert request.style_anchors[0].reference.reference_id == "style_master"
    assert request.character_identity_anchors[0].reference.reference_id == "female_master"
    assert request.semantic_scene.meaning
    assert request.semantic_scene.action
    assert request.semantic_scene.environment
    assert request.semantic_scene.composition


def test_same_file_is_not_implicitly_granted_another_authority() -> None:
    style_only = _reference("style_only", ["style_anchor"], "a" * 64)

    with pytest.raises(ValidationError, match="explicitly declared"):
        IllustratedReferenceBinding(
            authority=IllustratedReferenceAuthority.CHARACTER_IDENTITY,
            declared_role="female_identity_anchor",
            reference=style_only,
        )


def test_explicit_dual_role_reference_can_bind_each_declared_authority() -> None:
    dual = _reference(
        "explicit_dual_master",
        ["style_anchor", "female_identity_anchor"],
        "c" * 64,
    )
    request = _request(style=dual, identity=dual)

    assert request.style_anchors[0].declared_role == "style_anchor"
    assert (
        request.character_identity_anchors[0].declared_role
        == "female_identity_anchor"
    )
    assert request.style_anchors[0].reference.sha256 == (
        request.character_identity_anchors[0].reference.sha256
    )


def test_selected_decisions_preserve_explicit_roles_for_one_physical_reference() -> None:
    dual = _reference(
        "explicit_dual_master",
        ["style_anchor", "female_identity_anchor"],
        "c" * 64,
    )
    request = build_illustrated_scene_request(
        _illustrated_plan(ReferenceCatalog(references=[dual])),
        scene_id="scene_01",
    )

    assert len(request.style_anchors) == 1
    assert len(request.character_identity_anchors) == 1
    assert request.style_anchors[0].declared_role == "style_anchor"
    assert (
        request.character_identity_anchors[0].declared_role
        == "female_identity_anchor"
    )


def test_style_identity_and_scene_changes_each_change_request_hash() -> None:
    style = _reference("style_master", ["style_anchor"], "a" * 64)
    identity = _reference(
        "female_master",
        ["female_identity_anchor"],
        "b" * 64,
    )
    baseline = _request(style=style, identity=identity)
    changed_style = _request(
        style=style.model_copy(update={"sha256": "d" * 64}),
        identity=identity,
    )
    changed_identity = _request(
        style=style,
        identity=identity.model_copy(update={"sha256": "e" * 64}),
    )
    changed_semantics = baseline.model_copy(
        update={
            "semantic_scene": baseline.semantic_scene.model_copy(
                update={"meaning": "A materially different story beat."}
            )
        },
        deep=True,
    )

    baseline_hash = illustrated_scene_request_hash(baseline)
    assert illustrated_scene_request_hash(changed_style) != baseline_hash
    assert illustrated_scene_request_hash(changed_identity) != baseline_hash
    assert illustrated_scene_request_hash(changed_semantics) != baseline_hash


def test_request_hash_uses_reference_content_identity_not_local_path() -> None:
    style = _reference("style_master", ["style_anchor"], "a" * 64)
    identity = _reference(
        "female_master",
        ["female_identity_anchor"],
        "b" * 64,
    )
    baseline = _request(style=style, identity=identity)
    moved = _request(
        style=style.model_copy(update={"path": "D:/other-private-root/style.png"}),
        identity=identity.model_copy(
            update={"path": "D:/other-private-root/identity.png"}
        ),
    )

    assert illustrated_scene_request_hash(moved) == illustrated_scene_request_hash(
        baseline
    )


def test_stable_master_anchors_are_reusable_across_scene_requests() -> None:
    style = _reference("style_master", ["style_anchor"], "a" * 64)
    identity = _reference(
        "female_master",
        ["female_identity_anchor"],
        "b" * 64,
    )
    first = _request(style=style, identity=identity, scene_index=0)
    second = _request(style=style, identity=identity, scene_index=1)

    assert first.style_anchors == second.style_anchors
    assert first.character_identity_anchors == second.character_identity_anchors
    assert first.semantic_scene != second.semantic_scene
    assert illustrated_scene_request_hash(first) != illustrated_scene_request_hash(
        second
    )


def test_generated_scene_chaining_is_disabled_and_not_required() -> None:
    request = _request(
        style=_reference("style_master", ["style_anchor"], "a" * 64),
        identity=_reference(
            "female_master",
            ["female_identity_anchor"],
            "b" * 64,
        ),
    )

    assert request.accepted_scene_chaining is False
    assert all(
        binding.reference.metadata.get("source") != "accepted_scene"
        for binding in [
            *request.style_anchors,
            *request.character_identity_anchors,
            *request.composition_references,
        ]
    )
    with pytest.raises(ValidationError):
        IllustratedSceneRequest.model_validate(
            {**request.model_dump(mode="json"), "accepted_scene_chaining": True}
        )


def test_public_safe_request_rejects_private_reference_provenance() -> None:
    private_style = _reference("style_master", ["style_anchor"], "a" * 64)
    public_identity = _reference(
        "female_master",
        ["female_identity_anchor"],
        "b" * 64,
        sensitivity=SceneDataSensitivity.PUBLIC_SAFE,
    )

    with pytest.raises(ValidationError, match="cannot carry private/local references"):
        _request(
            style=private_style,
            identity=public_identity,
            sensitivity=SceneDataSensitivity.PUBLIC_SAFE,
        )


def test_public_safe_and_private_requests_preserve_reference_privacy() -> None:
    public_style = _reference(
        "public_style",
        ["style_anchor"],
        "a" * 64,
        sensitivity=SceneDataSensitivity.PUBLIC_SAFE,
    )
    public_identity = _reference(
        "public_identity",
        ["female_identity_anchor"],
        "b" * 64,
        sensitivity=SceneDataSensitivity.PUBLIC_SAFE,
    )
    public_request = _request(
        style=public_style,
        identity=public_identity,
        sensitivity=SceneDataSensitivity.PUBLIC_SAFE,
    )
    private_request = _request(
        style=_reference("private_style", ["style_anchor"], "c" * 64),
        identity=_reference(
            "private_identity",
            ["female_identity_anchor"],
            "d" * 64,
        ),
    )

    assert all(
        binding.reference.sensitivity is SceneDataSensitivity.PUBLIC_SAFE
        for binding in [
            *public_request.style_anchors,
            *public_request.character_identity_anchors,
        ]
    )
    assert private_request.style_anchors[0].reference.path.startswith(
        "C:/approved-private/"
    )
    assert (
        private_request.character_identity_anchors[0].reference.sha256 == "d" * 64
    )


def test_local_only_request_is_explicitly_non_externalizable() -> None:
    local_style = _reference(
        "local_style",
        ["style_anchor"],
        "a" * 64,
        sensitivity=SceneDataSensitivity.LOCAL_ONLY,
    )
    local_identity = _reference(
        "local_identity",
        ["female_identity_anchor"],
        "b" * 64,
        sensitivity=SceneDataSensitivity.LOCAL_ONLY,
    )
    request = _request(
        style=local_style,
        identity=local_identity,
        sensitivity=SceneDataSensitivity.LOCAL_ONLY,
    )

    assert request.externalizable is False


def test_private_request_cannot_externalize_local_only_reference() -> None:
    local_style = _reference(
        "local_style",
        ["style_anchor"],
        "a" * 64,
        sensitivity=SceneDataSensitivity.LOCAL_ONLY,
    )
    private_identity = _reference(
        "private_identity",
        ["female_identity_anchor"],
        "b" * 64,
    )

    with pytest.raises(ValidationError, match="cannot carry LOCAL_ONLY references"):
        _request(style=local_style, identity=private_identity)


def test_request_construction_does_not_mutate_story_or_scene_semantics() -> None:
    dual = _reference(
        "explicit_dual_master",
        ["style_anchor", "female_identity_anchor"],
        "c" * 64,
    )
    run = _illustrated_plan(ReferenceCatalog(references=[dual]))
    story_before = run.story_plan.model_dump(mode="json")
    brief_before = run.scene_execution_plans[0].scene_brief.model_dump(mode="json")

    build_illustrated_scene_request(run, scene_id="scene_01")

    assert run.story_plan.model_dump(mode="json") == story_before
    assert (
        run.scene_execution_plans[0].scene_brief.model_dump(mode="json")
        == brief_before
    )


def test_explicit_reference_roles_override_legacy_identity_inference() -> None:
    _, source = _brief()
    style_only = source.model_copy(update={"reference_roles": ["style_anchor"]})

    roles = required_reference_roles(style_only)

    assert "style_anchor" in roles
    assert not any("identity_anchor" in role for role in roles)


def test_generic_identity_role_resolves_from_female_character_semantics() -> None:
    _, source = _brief()
    female = source.model_copy(
        update={
            "characters": ["female"],
            "reference_roles": ["style_anchor", "identity_anchor"],
        }
    )

    roles = required_reference_roles(female)

    assert "female_identity_anchor" in roles
    assert "male_identity_anchor" not in roles


@pytest.mark.parametrize("character", ["man", "male"])
def test_generic_identity_role_resolves_from_male_character_semantics(
    character: str,
) -> None:
    _, source = _brief()
    male = source.model_copy(
        update={
            "characters": [character],
            "reference_roles": ["style_anchor", "identity_anchor"],
        }
    )

    roles = required_reference_roles(male)

    assert "male_identity_anchor" in roles
    assert "female_identity_anchor" not in roles


def test_missing_male_anchor_blocks_without_female_substitution() -> None:
    _, source = _brief()
    male = source.model_copy(
        update={
            "characters": ["man"],
            "reference_roles": ["style_anchor", "identity_anchor"],
        }
    )
    female_style = _reference(
        "female_style_master",
        ["style_anchor", "female_identity_anchor"],
        "a" * 64,
    )

    references, decisions = resolve_references(
        male,
        ReferenceCatalog(references=[female_style]),
    )

    male_decision = next(
        decision for decision in decisions if decision.role == "male_identity_anchor"
    )
    assert (
        male_decision.status
        is ReferenceDecisionStatus.REFERENCE_BLOCKED_REQUIRED_IDENTITY
    )
    assert not any(
        decision.role == "female_identity_anchor"
        and decision.status is ReferenceDecisionStatus.SELECTED
        for decision in decisions
    )
    assert references == [female_style]


def test_relationship_scene_still_resolves_couple_identity() -> None:
    _, source = _brief()
    relationship = source.model_copy(
        update={
            "scene_type": SceneType.RELATIONSHIP_VIGNETTE,
            "characters": ["woman", "man"],
            "reference_roles": ["style_anchor", "identity_anchor"],
        }
    )

    roles = required_reference_roles(relationship)

    assert "couple_identity_anchor" in roles
    assert "female_identity_anchor" not in roles
    assert "male_identity_anchor" not in roles


def test_ambiguous_multiple_characters_do_not_invent_couple_identity() -> None:
    _, source = _brief()
    ambiguous = source.model_copy(
        update={
            "characters": ["woman", "man"],
            "reference_roles": ["style_anchor", "identity_anchor"],
        }
    )

    roles = required_reference_roles(ambiguous)
    _, decisions = resolve_references(ambiguous, ReferenceCatalog())

    assert "couple_identity_anchor" not in roles
    assert "unresolved_identity_anchor" in roles
    unresolved = next(
        decision for decision in decisions if decision.role == "unresolved_identity_anchor"
    )
    assert (
        unresolved.status
        is ReferenceDecisionStatus.REFERENCE_BLOCKED_REQUIRED_IDENTITY
    )


def test_existing_female_production_fixture_role_is_unchanged() -> None:
    _, brief = _brief()

    roles = required_reference_roles(brief)

    assert roles[0] == "female_identity_anchor"


def test_reference_free_public_safe_request_does_not_leak_legacy_sentinel() -> None:
    fixture = build_fixture_preview_run(topic="public safe no references", scene_count=7)
    briefs = [scene.scene_brief for scene in fixture.scene_execution_plans]
    briefs[0] = briefs[0].model_copy(
        update={
            "scene_type": SceneType.SOLO_EMOTIONAL_VIGNETTE,
            "characters": [],
            "identity_requirements": [],
            "continuity_requirements": [],
            "reference_roles": [],
        }
    )
    run = build_production_run_plan(
        job_id="public-safe-reference-free",
        story_plan=fixture.story_plan,
        scene_briefs=briefs,
        reference_catalog=ReferenceCatalog(),
        production_strategy=ProductionStrategyConfig.volume(
            visual_mode=VisualExecutionMode.ILLUSTRATED_SCENE
        ),
        volume_local_inputs={
            "scene_01": VolumeLocalSceneInput(
                sensitivity=SceneDataSensitivity.PUBLIC_SAFE,
                request=LocalCompositionRequest(
                    character_id="anonymous",
                    action="pause",
                    emotion="calm",
                    location="generic_room",
                    time_of_day="day",
                    composition_preset="generic_vertical",
                    seed=10101,
                ),
            )
        },
    )

    request = build_illustrated_scene_request(run, scene_id="scene_01")

    assert run.scene_execution_plans[0].visual_adapter.visual_scene.reference_roles == [
        "no_reference_required"
    ]
    assert request.required_reference_roles == []
    assert request.style_anchors == []
    assert request.character_identity_anchors == []
    assert request.composition_references == []


def test_local_compositor_plan_cannot_build_illustrated_request() -> None:
    fixture = build_fixture_preview_run(topic="local mode remains isolated", scene_count=7)
    run = build_production_run_plan(
        job_id="local-mode-isolation",
        story_plan=fixture.story_plan,
        scene_briefs=[scene.scene_brief for scene in fixture.scene_execution_plans],
        reference_catalog=ReferenceCatalog(),
        production_strategy=ProductionStrategyConfig.volume(),
    )

    with pytest.raises(ValueError, match="requires ILLUSTRATED_SCENE mode"):
        build_illustrated_scene_request(run, scene_id="scene_01")
