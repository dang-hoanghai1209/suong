"""Network-free IllustratedSceneRequest to Cloudflare preview mapping."""
from __future__ import annotations

from pydantic import ValidationError
import pytest

from tella.topic_production.cloudflare_illustrated_adapter import (
    build_cloudflare_illustrated_preview,
)
from tella.topic_production.execution import (
    build_fixture_preview_run,
    build_production_run_plan,
)
from tella.topic_production.execution_models import (
    ApprovedReference,
    LocalCompositionRequest,
    ReferenceCatalog,
    VolumeLocalSceneInput,
)
from tella.topic_production.illustrated_request import (
    IllustratedReferenceAuthority,
    IllustratedReferenceBinding,
    IllustratedSceneRequest,
    build_illustrated_scene_request,
)
from tella.topic_production.planner import (
    DeterministicTopicPlanner,
    build_scene_briefs,
)
from tella.topic_production.strategy import (
    ProductionStrategyConfig,
    SceneDataSensitivity,
    VisualExecutionMode,
)
from tella.visual_generation.prompt_builder import request_hash
from tella.visual_generation.providers.cloudflare_flux import (
    KLEIN_4B_MODEL,
    cloudflare_prompt,
    provider_request_hash,
)


def _reference(
    reference_id: str,
    roles: list[str],
    sha256: str,
    *,
    path_root: str = "C:/approved-private",
    sensitivity: SceneDataSensitivity = SceneDataSensitivity.PRIVATE,
) -> ApprovedReference:
    return ApprovedReference(
        reference_id=reference_id,
        path=f"{path_root}/{reference_id}.png",
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


def _brief():
    story = DeterministicTopicPlanner().plan(topic="Healing through a quiet evening.")
    return build_scene_briefs(story)[0]


def _request(
    *,
    style: ApprovedReference,
    identity: ApprovedReference,
    sensitivity: SceneDataSensitivity = SceneDataSensitivity.PRIVATE,
) -> IllustratedSceneRequest:
    brief = _brief()
    return IllustratedSceneRequest(
        scene_id=brief.scene_id,
        semantic_scene=brief,
        sensitivity=sensitivity,
        required_reference_roles=["female_identity_anchor", "style_anchor"],
        style_anchors=[_binding(style, "style_anchor")],
        character_identity_anchors=[
            _binding(identity, "female_identity_anchor")
        ],
    )


def _separate_request(
    *,
    path_root: str = "C:/approved-private",
) -> IllustratedSceneRequest:
    return _request(
        style=_reference(
            "style_master",
            ["style_anchor"],
            "a" * 64,
            path_root=path_root,
        ),
        identity=_reference(
            "female_master",
            ["female_identity_anchor"],
            "b" * 64,
            path_root=path_root,
        ),
    )


def test_private_female_request_maps_to_deterministic_klein_preview() -> None:
    request = _separate_request()

    first = build_cloudflare_illustrated_preview(request)
    second = build_cloudflare_illustrated_preview(request)

    assert first == second
    assert first.blocked is False
    assert first.model == KLEIN_4B_MODEL
    assert (first.width, first.height, first.steps) == (576, 1024, 4)
    assert first.seed == 10101
    assert first.provider_request is not None
    assert first.provider_request.seed == first.seed
    assert first.provider_request_hash
    assert first.privacy_authorized_for_cloudflare is True
    assert first.external_calls == first.provider_reaching_calls == 0
    assert first.transport_authorized is False


def test_reference_order_is_identity_then_style() -> None:
    preview = build_cloudflare_illustrated_preview(_separate_request())

    assert [binding.authority for binding in preview.semantic_bindings] == [
        IllustratedReferenceAuthority.CHARACTER_IDENTITY,
        IllustratedReferenceAuthority.STYLE,
    ]
    assert [
        upload.authorities for upload in preview.reference_uploads
    ] == [
        [IllustratedReferenceAuthority.CHARACTER_IDENTITY],
        [IllustratedReferenceAuthority.STYLE],
    ]
    assert preview.provider_request is not None
    assert [reference.semantic_roles for reference in preview.provider_request.references] == [
        ["female_identity_anchor"],
        ["style_anchor"],
    ]


def test_dual_role_semantics_survive_one_physical_upload() -> None:
    dual = _reference(
        "explicit_dual_master",
        ["style_anchor", "female_identity_anchor"],
        "c" * 64,
    )
    preview = build_cloudflare_illustrated_preview(
        _request(style=dual, identity=dual)
    )

    assert len(preview.semantic_bindings) == 2
    assert len(preview.reference_uploads) == 1
    upload = preview.reference_uploads[0]
    assert upload.declared_roles == [
        "female_identity_anchor",
        "style_anchor",
    ]
    assert upload.authorities == [
        IllustratedReferenceAuthority.CHARACTER_IDENTITY,
        IllustratedReferenceAuthority.STYLE,
    ]
    assert len(upload.bindings) == 2
    assert preview.provider_request is not None
    assert len(preview.provider_request.references) == 1
    assert preview.provider_request.references[0].semantic_roles == upload.declared_roles


def test_reference_free_public_safe_request_is_text_only() -> None:
    brief = _brief().model_copy(
        update={
            "characters": [],
            "identity_requirements": [],
            "continuity_requirements": [],
            "reference_roles": [],
        }
    )
    request = IllustratedSceneRequest(
        scene_id=brief.scene_id,
        semantic_scene=brief,
        sensitivity=SceneDataSensitivity.PUBLIC_SAFE,
    )

    preview = build_cloudflare_illustrated_preview(request)

    assert preview.blocked is False
    assert preview.semantic_bindings == []
    assert preview.reference_uploads == []
    assert preview.provider_request is not None
    assert preview.provider_request.references == []
    assert "generic text brief only" in preview.prompt
    assert "no_reference_required" not in preview.prompt
    assert preview.external_calls == preview.provider_reaching_calls == 0


def test_local_only_request_returns_blocked_zero_call_decision() -> None:
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

    preview = build_cloudflare_illustrated_preview(request)

    assert preview.blocked is True
    assert preview.provider_request is None
    assert preview.provider_request_hash is None
    assert preview.prompt == ""
    assert preview.reference_uploads == []
    assert preview.transport_authorized is False
    assert preview.privacy_authorized_for_cloudflare is False
    assert preview.external_calls == preview.provider_reaching_calls == 0


def test_missing_male_anchor_cannot_be_bypassed_by_cloudflare_adapter() -> None:
    fixture = build_fixture_preview_run(topic="male identity must fail closed", scene_count=7)
    briefs = [scene.scene_brief for scene in fixture.scene_execution_plans]
    briefs[0] = briefs[0].model_copy(
        update={
            "characters": ["man"],
            "reference_roles": ["style_anchor", "identity_anchor"],
        }
    )
    female_style = _reference(
        "female_style_master",
        ["style_anchor", "female_identity_anchor"],
        "c" * 64,
    )
    run = build_production_run_plan(
        job_id="male-anchor-block",
        story_plan=fixture.story_plan,
        scene_briefs=briefs,
        reference_catalog=ReferenceCatalog(references=[female_style]),
        production_strategy=ProductionStrategyConfig.volume(
            visual_mode=VisualExecutionMode.ILLUSTRATED_SCENE
        ),
        volume_local_inputs={
            "scene_01": VolumeLocalSceneInput(
                sensitivity=SceneDataSensitivity.PRIVATE,
                request=LocalCompositionRequest(
                    character_id="male_01",
                    action="stand",
                    emotion="calm",
                    location="room",
                    time_of_day="day",
                    composition_preset="generic_vertical",
                    seed=10101,
                ),
            )
        },
    )

    with pytest.raises(ValidationError, match="male_identity_anchor"):
        build_illustrated_scene_request(run, scene_id="scene_01")


def test_authoritative_inputs_change_prompt_or_provider_identity() -> None:
    baseline_request = _separate_request()
    baseline = build_cloudflare_illustrated_preview(baseline_request)
    changed_semantics = baseline_request.model_copy(
        update={
            "semantic_scene": baseline_request.semantic_scene.model_copy(
                update={"meaning": "A different authoritative scene meaning."}
            )
        },
        deep=True,
    )
    changed_style = _request(
        style=baseline_request.style_anchors[0].reference.model_copy(
            update={"sha256": "d" * 64}
        ),
        identity=baseline_request.character_identity_anchors[0].reference,
    )
    changed_identity = _request(
        style=baseline_request.style_anchors[0].reference,
        identity=baseline_request.character_identity_anchors[0].reference.model_copy(
            update={"sha256": "e" * 64}
        ),
    )

    semantic_preview = build_cloudflare_illustrated_preview(changed_semantics)
    style_preview = build_cloudflare_illustrated_preview(changed_style)
    identity_preview = build_cloudflare_illustrated_preview(changed_identity)

    assert semantic_preview.prompt != baseline.prompt
    assert semantic_preview.provider_request_hash != baseline.provider_request_hash
    assert style_preview.provider_request_hash != baseline.provider_request_hash
    assert identity_preview.provider_request_hash != baseline.provider_request_hash


def test_local_root_does_not_change_logical_or_provider_identity() -> None:
    first = build_cloudflare_illustrated_preview(
        _separate_request(path_root="C:/approved-private")
    )
    moved = build_cloudflare_illustrated_preview(
        _separate_request(path_root="D:/moved-approved-private")
    )

    assert moved.illustrated_request_hash == first.illustrated_request_hash
    assert moved.provider_request_hash == first.provider_request_hash
    assert moved.prompt == first.prompt


def test_prompt_is_complete_scene_oriented_and_not_compositor_driven() -> None:
    preview = build_cloudflare_illustrated_preview(_separate_request())
    prompt = preview.prompt.casefold()

    assert "one cohesive finished illustration" in prompt
    assert "integrate the character, environment, objects, and symbols" in prompt
    assert "assembled collage" in prompt
    assert "soft hand-drawn emotional editorial illustration" in prompt
    assert "no pasted character" in prompt
    assert "no realism" in prompt
    assert "no anime" in prompt
    assert "no 3d" in prompt
    assert "overlay prop" not in prompt
    assert "layer png" not in prompt
    assert "placement coordinates" not in prompt
    assert "composition_preset" not in prompt


def test_prompt_keeps_style_identity_and_scene_authorities_distinct() -> None:
    preview = build_cloudflare_illustrated_preview(_separate_request())
    prompt = preview.prompt.casefold()

    assert "character identity only" in prompt
    assert "not scene composition" in prompt
    assert "visual style only" in prompt
    assert "not character identity" in prompt
    assert "[story beat]" in prompt
    assert "[action]" in prompt
    assert "[environment]" in prompt


def test_shared_prompt_preserves_legacy_reference_guidance() -> None:
    illustrated = build_cloudflare_illustrated_preview(_separate_request())
    assert illustrated.provider_request is not None
    legacy_request = illustrated.provider_request.model_copy(
        update={"reference_authority_contract": None}
    )

    prompt = cloudflare_prompt(legacy_request)

    assert "Use image 0 as guidance for the female character archetype" in prompt
    assert "short dark bob" in prompt
    assert "dusty-pink long dress" in prompt
    assert "warm dark brown visual world" in prompt
    assert "cream halo or vignette" in prompt
    assert "thin imperfect outlines" in prompt
    assert "muted palette" in prompt
    assert "Use each supplied reference" not in prompt


def test_authority_aware_prompt_uses_mapping_without_image_zero_assumption() -> None:
    preview = build_cloudflare_illustrated_preview(_separate_request())

    assert "Use each supplied reference only according to its explicitly declared" in (
        preview.prompt
    )
    assert "Use image 0 as guidance for the female character archetype" not in (
        preview.prompt
    )
    assert "Reference image 0: character identity only" in preview.prompt
    assert "Reference image 1: visual style only" in preview.prompt


def test_provider_hash_keeps_legacy_default_and_accepts_upstream_identity() -> None:
    preview = build_cloudflare_illustrated_preview(_separate_request())
    assert preview.provider_request is not None
    request = preview.provider_request.model_copy(
        update={"reference_authority_contract": None}
    )
    prompt = cloudflare_prompt(request)
    kwargs = {
        "request": request,
        "prompt": prompt,
        "model": preview.model,
        "width": preview.width,
        "height": preview.height,
        "steps": preview.steps,
    }

    legacy_default = provider_request_hash(**kwargs)
    explicit_legacy_identity = provider_request_hash(
        **kwargs,
        logical_request_hash=request_hash(request),
    )
    illustrated_identity = provider_request_hash(
        **kwargs,
        logical_request_hash="f" * 64,
    )

    assert legacy_default == explicit_legacy_identity
    assert illustrated_identity != legacy_default
    assert request_hash(request) == request_hash(preview.provider_request)


def test_preview_requires_no_generated_scene_chaining_or_transport() -> None:
    request = _separate_request()
    preview = build_cloudflare_illustrated_preview(request)

    assert request.accepted_scene_chaining is False
    assert all(
        binding.reference.metadata.get("source") != "accepted_scene"
        for binding in preview.semantic_bindings
    )
    assert preview.transport_authorized is False
    assert preview.external_calls == 0
    assert preview.provider_reaching_calls == 0
