"""Script-input identity, authority, policy, and filesystem adapter tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import tella.topic_production as topic_production
from tella.topic_production.identity_eligibility import (
    IdentityAuthorityDecision,
    IdentityAuthorityErrorCode,
    IdentityClassificationCapability,
    IdentityEligibilityError,
    IdentityEligibilityResult,
    IdentityEligibilityStatus,
    IdentityEvidence,
    classify_identity_eligibility,
    identity_evidence_from_scene_briefs,
    require_supported_identity,
)
from tella.topic_production.models import (
    AcceptancePriority,
    ProductionSceneBrief,
    ReferenceStrategy,
    SceneComplexity,
    SceneType,
)
from tella.topic_production.script_input import (
    CharacterRoleRequest,
    CharacterScopeRequest,
    HintedNarrationScriptInput,
    NarrationAuthority,
    NarrationScriptInput,
    NormalizedScriptInput,
    RecurringCharacterRequest,
    RelationshipScope,
    SceneHint,
    ScriptInputMode,
    SourceContentAuthority,
    TopicScriptInput,
    normalize_script_input,
    parse_script_input,
)
from tella.topic_production.script_input_io import (
    ScriptInputFileError,
    ScriptInputFileErrorCode,
    narration_input_from_file,
)
from tella.topic_production.script_input_policy import (
    MVPSceneCountPolicyError,
    SceneCountPolicyErrorCode,
    SceneCountStructureError,
    validate_mvp_scene_count_range,
)


EXACT_NARRATION = "Cô ấy bình tâm.\r\nDòng thứ hai  \n"


def _character(
    character_id: str,
    role: CharacterRoleRequest,
) -> RecurringCharacterRequest:
    return RecurringCharacterRequest(character_id=character_id, role=role)


def _scope(
    *characters: RecurringCharacterRequest,
    anonymous: bool = False,
    relationship: RelationshipScope = RelationshipScope.NONE,
) -> CharacterScopeRequest:
    return CharacterScopeRequest(
        recurring_characters=list(characters),
        anonymous_background_people=anonymous,
        relationship=relationship,
    )


def _normalized(
    scope: CharacterScopeRequest | None = None,
    *,
    narration: str = EXACT_NARRATION,
    hints: list[SceneHint] | None = None,
) -> NormalizedScriptInput:
    common = {
        "narration": narration,
        "language": "vi",
        "character_scope_request": scope or CharacterScopeRequest(),
    }
    request = (
        HintedNarrationScriptInput(scene_hints=hints, **common)
        if hints is not None
        else NarrationScriptInput(**common)
    )
    return normalize_script_input(request)


def _brief(
    *,
    characters: list[str],
    identity: list[str] | None = None,
    continuity: list[str] | None = None,
    scene_type: SceneType = SceneType.SOLO_EMOTIONAL_VIGNETTE,
) -> ProductionSceneBrief:
    return ProductionSceneBrief(
        scene_id="scene_01",
        order=1,
        scene_type=scene_type,
        narrative_text="A bounded visual beat.",
        meaning="identity evidence fixture",
        emotional_tone=["calm"],
        topic_intent="preserve the declared identity policy",
        characters=characters,
        identity_requirements=identity or [],
        continuity_requirements=continuity or [],
        action=["stand calmly"],
        interaction={},
        environment=["quiet room"],
        objects=[],
        symbols=[],
        composition=["single focal subject"],
        negative_space_requirements=[],
        visual_hierarchy=["subject"],
        reference_roles=[],
        reference_strategy=ReferenceStrategy(
            strategy="fixture",
            accepted_scene_chaining=False,
        ),
        hard_negatives=[],
        complexity=SceneComplexity.SIMPLE,
        acceptance_priority=AcceptancePriority.STANDARD,
        source_beat_id="beat_01",
        duration_seconds=4.0,
    )


def test_modes_and_exact_text_authority() -> None:
    topic = normalize_script_input(TopicScriptInput(topic="Trust again", language="EN"))
    narration = _normalized()

    assert topic.input_mode is ScriptInputMode.TOPIC
    assert topic.narration_authority is NarrationAuthority.STORY_PLAN
    assert topic.narration_generation_permitted
    assert narration.source_content == EXACT_NARRATION
    assert narration.text_authority is SourceContentAuthority.DECODED_TEXT_CONTENT
    assert narration.narration_authority is NarrationAuthority.EXACT_SOURCE
    assert not narration.narration_generation_permitted


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "TOPIC", "topic": "x", "narration": "y", "language": "en"},
        {"mode": "NARRATION", "narration": "x", "topic": "y", "language": "en"},
        {
            "mode": "NARRATION",
            "narration": "x",
            "language": "en",
            "scene_hints": [{"target_scene_order": 1, "action": "walk"}],
        },
    ],
)
def test_discriminated_modes_reject_ambiguous_fields(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        parse_script_input(payload)


@pytest.mark.parametrize(
    "text",
    [
        "Tiếng Việt nguyên vẹn: bình yên.",
        "\ufeffBOM remains content",
        "line one\r\nline two  \n",
    ],
)
def test_decoded_text_forms_are_preserved_exactly(text: str) -> None:
    normalized = _normalized(narration=text)

    assert normalized.source_content == text
    assert normalized.source_sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_unicode_normalization_is_not_applied() -> None:
    nfc = _normalized(narration="Café")
    nfd = _normalized(narration="Cafe\u0301")

    assert nfc.source_content != nfd.source_content
    assert nfc.source_sha256 != nfd.source_sha256


def test_hashes_cannot_be_forged_by_construction_or_validation() -> None:
    normalized = _normalized()
    payload = normalized.model_dump(mode="python")

    for field in ("source_sha256", "logical_input_hash"):
        forged = dict(payload)
        forged[field] = "0" * 64
        with pytest.raises(ValidationError, match=field):
            NormalizedScriptInput.model_validate(forged)


def test_model_copy_recomputes_hashes_and_rejects_explicit_stale_hashes() -> None:
    original = _normalized(_scope(_character("heroine", CharacterRoleRequest.FEMALE)))
    content_update = original.model_copy(update={"source_content": "Changed"})
    semantic_update = original.model_copy(update={"language": "en"})

    assert content_update.source_sha256 != original.source_sha256
    assert content_update.logical_input_hash != original.logical_input_hash
    assert semantic_update.source_sha256 == original.source_sha256
    assert semantic_update.logical_input_hash != original.logical_input_hash
    with pytest.raises(ValidationError, match="source_sha256"):
        original.model_copy(
            update={"source_content": "Changed", "source_sha256": original.source_sha256}
        )


def test_serialization_reconstruction_preserves_effective_identity() -> None:
    original = _normalized(
        _scope(_character("heroine", CharacterRoleRequest.FEMALE)),
        hints=[SceneHint(target_scene_order=2, objects=["cup", "book"])],
    )
    reconstructed = NormalizedScriptInput.model_validate(original.model_dump(mode="python"))

    assert reconstructed == original
    assert reconstructed.source_sha256 == original.source_sha256
    assert reconstructed.logical_input_hash == original.logical_input_hash


def test_json_key_order_does_not_change_logical_identity() -> None:
    first = {
        "mode": "NARRATION",
        "narration": EXACT_NARRATION,
        "language": "vi",
        "character_scope_request": {
            "anonymous_background_people": False,
            "recurring_characters": [{"character_id": "heroine", "role": "female"}],
        },
    }
    second = {
        "character_scope_request": {
            "recurring_characters": [{"role": "female", "character_id": "heroine"}],
            "anonymous_background_people": False,
        },
        "language": "vi",
        "narration": EXACT_NARRATION,
        "mode": "NARRATION",
    }

    assert (
        normalize_script_input(first).logical_input_hash
        == normalize_script_input(second).logical_input_hash
    )


def test_hint_target_order_is_canonical_but_item_priority_is_semantic() -> None:
    one = SceneHint(target_scene_order=1, objects=["cup", "book"], symbols=["sun", "leaf"])
    two = SceneHint(target_scene_order=2, action="walk")
    reordered_targets = _normalized(hints=[two, one])
    canonical_targets = _normalized(hints=[one, two])
    reordered_items = _normalized(
        hints=[
            SceneHint(
                target_scene_order=1,
                objects=["book", "cup"],
                symbols=["leaf", "sun"],
            ),
            two,
        ]
    )

    assert [hint.target_order for hint in reordered_targets.scene_hints] == [1, 2]
    assert reordered_targets.logical_input_hash == canonical_targets.logical_input_hash
    assert reordered_items.logical_input_hash != canonical_targets.logical_input_hash


def test_character_declaration_order_is_not_semantic() -> None:
    first = _normalized(
        _scope(
            _character("Zed", CharacterRoleRequest.MALE),
            _character("Ana", CharacterRoleRequest.FEMALE),
        )
    )
    second = _normalized(
        _scope(
            _character("Ana", CharacterRoleRequest.FEMALE),
            _character("Zed", CharacterRoleRequest.MALE),
        )
    )

    assert first.character_scope_request == second.character_scope_request
    assert first.logical_input_hash == second.logical_input_hash


def test_duplicate_targets_characters_and_hint_items_fail() -> None:
    with pytest.raises(ValidationError, match="targets must be unique"):
        HintedNarrationScriptInput(
            narration="x",
            language="en",
            scene_hints=[
                SceneHint(target_beat_id="beat_02", action="sit"),
                SceneHint(target_scene_order=2, pose="stand"),
            ],
        )
    with pytest.raises(ValidationError) as caught:
        _scope(
            _character("Heroine", CharacterRoleRequest.FEMALE),
            _character("heroine", CharacterRoleRequest.MALE),
        )
    assert caught.value.errors()[0]["type"] == "INVALID_IDENTITY_DECLARATION"
    with pytest.raises(ValidationError) as duplicate:
        _scope(
            _character("Heroine", CharacterRoleRequest.FEMALE),
            _character("heroine", CharacterRoleRequest.FEMALE),
        )
    assert duplicate.value.errors()[0]["type"] == "INVALID_IDENTITY_DECLARATION"
    with pytest.raises(ValidationError, match="unique"):
        SceneHint(target_scene_order=1, objects=[" cup ", "CUP"])


@pytest.mark.parametrize(
    "payload",
    [
        {"target_scene_order": 1},
        {"target_scene_order": 1, "objects": [" "]},
        {"target_scene_order": 1, "action": "x" * 301},
        {"target_scene_order": 1, "objects": [str(index) for index in range(9)]},
        {"target_scene_order": 1, "symbols": ["x" * 121]},
        {"target_scene_order": 1, "action": "walk", "provider": "forbidden"},
        {"target_scene_order": 1, "action": "walk\u0000"},
        {"action": "walk"},
        {"target_scene_order": 1, "target_beat_id": "beat_01", "action": "walk"},
        {"target_beat_id": "beat_x", "action": "walk"},
        {"target_scene_order": 0, "action": "walk"},
    ],
)
def test_scene_hint_is_bounded_provider_neutral_guidance(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        SceneHint.model_validate(payload)


def test_input_local_target_errors_do_not_claim_storyplan_existence() -> None:
    with pytest.raises(ValidationError) as caught:
        HintedNarrationScriptInput(
            narration="x",
            language="en",
            requested_scene_count_range=(3, 5),
            scene_hints=[SceneHint(target_scene_order=6, action="walk")],
        )

    message = str(caught.value)
    assert "input-local requested scene maximum" in message
    assert "does not exist" not in message
    assert "StoryPlan" not in message


def test_scene_range_structure_and_mvp_policy_are_separate() -> None:
    parsed = NarrationScriptInput(
        narration="x",
        language="en",
        requested_scene_count_range=(3, 5),
    )

    assert parsed.requested_scene_count_range == (3, 5)
    assert validate_mvp_scene_count_range((7, 7)) == (7, 7)
    assert validate_mvp_scene_count_range((7, 8)) == (7, 8)
    assert validate_mvp_scene_count_range((8, 8)) == (8, 8)
    with pytest.raises(MVPSceneCountPolicyError) as caught:
        validate_mvp_scene_count_range((3, 5))
    assert caught.value.code == "UNSUPPORTED_MVP_SCENE_COUNT_RANGE"
    with pytest.raises(ValidationError):
        NarrationScriptInput(
            narration="x",
            language="en",
            requested_scene_count_range=(5, 3),
        )


def test_inline_and_file_decoded_content_have_path_free_identity(tmp_path: Path) -> None:
    first = tmp_path / "one.txt"
    second = tmp_path / "nested" / "two.txt"
    second.parent.mkdir()
    first.write_bytes(EXACT_NARRATION.encode("utf-8"))
    second.write_bytes(EXACT_NARRATION.encode("utf-8"))
    records = [
        _normalized(),
        normalize_script_input(narration_input_from_file(first, language="vi")),
        normalize_script_input(narration_input_from_file(second, language="vi")),
    ]

    assert len({record.source_sha256 for record in records}) == 1
    assert len({record.logical_input_hash for record in records}) == 1
    assert all(str(tmp_path) not in record.model_dump_json() for record in records)


def test_file_adapter_preserves_utf8_bom_and_crlf(tmp_path: Path) -> None:
    path = tmp_path / "script.txt"
    content = "\ufeffMột\r\nHai  \n"
    path.write_bytes(content.encode("utf-8"))

    assert narration_input_from_file(path, language="vi").narration == content


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ("missing", ScriptInputFileErrorCode.MISSING_FILE),
        ("invalid", ScriptInputFileErrorCode.INVALID_UTF8),
        ("oversized", ScriptInputFileErrorCode.OVERSIZED_FILE),
    ],
)
def test_file_adapter_typed_failures(
    tmp_path: Path,
    setup: str,
    expected: ScriptInputFileErrorCode,
) -> None:
    path = tmp_path / "script.txt"
    if setup == "invalid":
        path.write_bytes(b"\xff")
    elif setup == "oversized":
        path.write_bytes(b"1234")

    with pytest.raises(ScriptInputFileError) as caught:
        narration_input_from_file(path, language="en", max_bytes=3)
    assert caught.value.code is expected
    assert str(tmp_path) not in str(caught.value.as_dict())


def test_file_adapter_rejects_symlinks_without_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "link.txt"
    monkeypatch.setattr(Path, "is_symlink", lambda _self: True)

    with pytest.raises(ScriptInputFileError) as caught:
        narration_input_from_file(path, language="en")
    assert caught.value.code is ScriptInputFileErrorCode.SYMLINK_NOT_ALLOWED


def test_file_adapter_reports_unreadable_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "script.txt"
    path.write_text("valid", encoding="utf-8")

    def deny_read(_self: Path) -> bytes:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_bytes", deny_read)
    with pytest.raises(ScriptInputFileError) as caught:
        narration_input_from_file(path, language="en")
    assert caught.value.code is ScriptInputFileErrorCode.UNREADABLE_FILE


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        (
            _scope(_character("heroine", CharacterRoleRequest.FEMALE)),
            IdentityEligibilityStatus.SUPPORTED_RECURRING_FEMALE,
        ),
        (
            _scope(
                _character("heroine", CharacterRoleRequest.FEMALE),
                anonymous=True,
            ),
            IdentityEligibilityStatus.SUPPORTED_FEMALE_WITH_ANONYMOUS_BACKGROUND,
        ),
        (
            _scope(_character("hero", CharacterRoleRequest.MALE)),
            IdentityEligibilityStatus.UNSUPPORTED_RECURRING_MALE,
        ),
        (
            _scope(
                _character("heroine", CharacterRoleRequest.FEMALE),
                _character("hero", CharacterRoleRequest.MALE),
            ),
            IdentityEligibilityStatus.UNSUPPORTED_RECURRING_MALE,
        ),
        (
            _scope(
                _character("one", CharacterRoleRequest.FEMALE),
                _character("two", CharacterRoleRequest.FEMALE),
            ),
            IdentityEligibilityStatus.UNSUPPORTED_MULTIPLE_RECURRING,
        ),
        (
            _scope(
                _character("heroine", CharacterRoleRequest.FEMALE),
                relationship=RelationshipScope.FAMILY,
            ),
            IdentityEligibilityStatus.UNSUPPORTED_FAMILY_OR_CHILDREN,
        ),
        (
            _scope(_character("child", CharacterRoleRequest.CHILD)),
            IdentityEligibilityStatus.UNSUPPORTED_FAMILY_OR_CHILDREN,
        ),
        (CharacterScopeRequest(), IdentityEligibilityStatus.UNRESOLVED_IDENTITY),
        (
            _scope(_character("unknown", CharacterRoleRequest.UNKNOWN)),
            IdentityEligibilityStatus.UNRESOLVED_IDENTITY,
        ),
    ],
)
def test_identity_policy_matrix(
    scope: CharacterScopeRequest,
    expected: IdentityEligibilityStatus,
) -> None:
    result = classify_identity_eligibility(_normalized(scope))

    assert result.status is expected
    assert result.capability is IdentityClassificationCapability.INPUT_DECLARATION_ELIGIBILITY_ONLY
    assert result.supported is expected.name.startswith("SUPPORTED_")


@pytest.mark.parametrize(
    ("brief", "expected"),
    [
        (
            _brief(
                characters=["recurring_woman"],
                identity=["same family and recurring children"],
            ),
            IdentityEligibilityStatus.UNSUPPORTED_FAMILY_OR_CHILDREN,
        ),
        (
            _brief(
                characters=["recurring_woman"],
                continuity=["preserve the same family across scenes"],
            ),
            IdentityEligibilityStatus.UNSUPPORTED_FAMILY_OR_CHILDREN,
        ),
        (
            _brief(
                characters=["recurring_woman", "recurring_child"],
                identity=["preserve recurring female identity"],
            ),
            IdentityEligibilityStatus.UNSUPPORTED_FAMILY_OR_CHILDREN,
        ),
        (
            _brief(
                characters=["recurring_woman", "anonymous_background_people"],
                identity=["preserve recurring female identity"],
            ),
            IdentityEligibilityStatus.SUPPORTED_FEMALE_WITH_ANONYMOUS_BACKGROUND,
        ),
        (
            _brief(characters=["abstract_silhouette"]),
            IdentityEligibilityStatus.UNRESOLVED_IDENTITY,
        ),
        (
            _brief(
                characters=["recurring_guide"],
                identity=["preserve recurring character identity"],
            ),
            IdentityEligibilityStatus.UNRESOLVED_IDENTITY,
        ),
        (
            _brief(
                characters=["recurring_woman"],
                identity=["preserve recurring female identity"],
                continuity=["preserve recurring guide identity"],
            ),
            IdentityEligibilityStatus.UNRESOLVED_IDENTITY,
        ),
        (_brief(characters=[]), IdentityEligibilityStatus.UNRESOLVED_IDENTITY),
    ],
)
def test_scene_brief_evidence_fails_closed(
    brief: ProductionSceneBrief,
    expected: IdentityEligibilityStatus,
) -> None:
    evidence = identity_evidence_from_scene_briefs([brief])
    result = classify_identity_eligibility(
        _normalized(),
        semantic_evidence=evidence,
    )

    assert evidence[0].scene_id == "scene_01"
    assert evidence[0].beat_id == "beat_01"
    assert result.status is expected


def test_explicit_precedence_for_conflicting_evidence() -> None:
    request = _normalized(_scope(_character("heroine", CharacterRoleRequest.FEMALE)))
    result = classify_identity_eligibility(
        request,
        semantic_evidence=[
            IdentityEvidence(
                source="fixture",
                detected_roles=[
                    CharacterRoleRequest.MALE,
                    CharacterRoleRequest.CHILD,
                ],
                recurring_character_ids=["hero", "child"],
                couple=True,
                family=True,
                recurring_children=True,
            )
        ],
    )
    unknown = classify_identity_eligibility(
        request,
        semantic_evidence=[
            IdentityEvidence(
                source="fixture",
                detected_roles=[CharacterRoleRequest.UNKNOWN],
                recurring_character_ids=["heroine"],
                unresolved_recurring_identity=True,
            )
        ],
    )

    assert result.status is IdentityEligibilityStatus.UNSUPPORTED_FAMILY_OR_CHILDREN
    assert unknown.status is IdentityEligibilityStatus.UNRESOLVED_IDENTITY


def test_relationship_scene_and_legacy_anchor_cannot_enable_couple() -> None:
    evidence = identity_evidence_from_scene_briefs(
        [
            _brief(
                characters=["recurring_woman"],
                identity=["preserve recurring female identity"],
                scene_type=SceneType.RELATIONSHIP_VIGNETTE,
            )
        ]
    )
    result = classify_identity_eligibility(
        _normalized(),
        semantic_evidence=evidence,
    )

    assert result.status is IdentityEligibilityStatus.UNSUPPORTED_COUPLE
    assert result.required_anchor_role is None


def test_raw_narration_is_not_semantically_classified() -> None:
    result = classify_identity_eligibility(
        _normalized(narration="Anh ấy gặp người yêu và gia đình.")
    )

    assert result.status is IdentityEligibilityStatus.UNRESOLVED_IDENTITY
    assert result.capability is IdentityClassificationCapability.INPUT_DECLARATION_ELIGIBILITY_ONLY


def test_identity_error_is_stable_structured_and_path_free() -> None:
    request = _normalized(_scope(_character("hero", CharacterRoleRequest.MALE)))

    with pytest.raises(IdentityEligibilityError) as caught:
        require_supported_identity(request)
    payload = caught.value.as_dict()

    assert caught.value.code is IdentityAuthorityErrorCode.UNSUPPORTED_IDENTITY_POLICY
    assert payload["code"] == "UNSUPPORTED_IDENTITY_POLICY"
    eligibility = payload["eligibility"]
    assert eligibility["status"] == "UNSUPPORTED_RECURRING_MALE"
    assert eligibility["authority_decision"] == IdentityAuthorityDecision.DENIED_POLICY
    assert eligibility["scene_beat_evidence"]
    assert eligibility["detected_roles"] == ["male"]
    assert ":\\" not in str(payload)


def test_unresolved_error_code_is_distinct_from_policy_denial() -> None:
    result = classify_identity_eligibility(_normalized())

    assert result.error_code is IdentityAuthorityErrorCode.UNRESOLVED_IDENTITY_EVIDENCE
    assert result.authority_decision is IdentityAuthorityDecision.INSUFFICIENT_EVIDENCE


def test_guard_blocks_all_future_external_boundaries() -> None:
    calls = {"reference": 0, "provider": 0, "renderer": 0}

    def guarded() -> None:
        require_supported_identity(_normalized())
        calls["reference"] += 1
        calls["provider"] += 1
        calls["renderer"] += 1

    with pytest.raises(IdentityEligibilityError):
        guarded()
    assert calls == {"reference": 0, "provider": 0, "renderer": 0}


def test_package_root_exports_only_durable_checkpoint_contracts() -> None:
    assert "ScriptInput" in topic_production.__all__
    assert "IdentityEligibilityResult" in topic_production.__all__
    assert "narration_input_from_file" not in topic_production.__all__
    assert "parse_script_input" not in topic_production.__all__
    assert "identity_evidence_from_scene_briefs" not in topic_production.__all__
    assert "IdentityEvidence" not in topic_production.__all__


def test_script_input_collections_defensively_break_caller_aliases() -> None:
    object_items = ["cup"]
    symbol_items = ["leaf"]
    hint = SceneHint(
        target_scene_order=1,
        objects=object_items,
        symbols=symbol_items,
    )
    caller_hints = [hint]
    caller_characters = [_character("heroine", CharacterRoleRequest.FEMALE)]
    scope = CharacterScopeRequest(recurring_characters=caller_characters)
    request = HintedNarrationScriptInput(
        narration=EXACT_NARRATION,
        language="vi",
        scene_hints=caller_hints,
        character_scope_request=scope,
    )
    normalized = normalize_script_input(request)
    original_hash = normalized.logical_input_hash

    object_items.append("book")
    symbol_items.append("sun")
    caller_hints.append(SceneHint(target_scene_order=2, action="walk"))
    caller_characters.append(_character("partner", CharacterRoleRequest.MALE))

    assert hint.objects == ("cup",)
    assert hint.symbols == ("leaf",)
    assert request.scene_hints == (hint,)
    assert scope.recurring_characters == (_character("heroine", CharacterRoleRequest.FEMALE),)
    assert normalized.logical_input_hash == original_hash


def test_script_input_nested_semantics_are_immutable() -> None:
    normalized = _normalized(
        _scope(_character("heroine", CharacterRoleRequest.FEMALE)),
        hints=[SceneHint(target_scene_order=1, objects=["cup"], symbols=["leaf"])],
    )

    with pytest.raises(AttributeError):
        normalized.scene_hints.append(SceneHint(target_scene_order=2, action="walk"))
    with pytest.raises(AttributeError):
        normalized.scene_hints[0].objects.append("book")
    with pytest.raises(AttributeError):
        normalized.scene_hints[0].symbols.extend(("sun",))
    with pytest.raises(AttributeError):
        normalized.character_scope_request.recurring_characters.append(
            _character("partner", CharacterRoleRequest.MALE)
        )


def test_serialized_payload_is_detached_and_tampering_is_revalidated() -> None:
    normalized = _normalized(
        _scope(_character("heroine", CharacterRoleRequest.FEMALE)),
        hints=[SceneHint(target_scene_order=1, objects=["cup"])],
    )
    payload = normalized.model_dump(mode="json")
    original_hash = normalized.logical_input_hash

    assert isinstance(payload["scene_hints"], list)
    assert isinstance(payload["scene_hints"][0]["objects"], list)
    payload["scene_hints"][0]["objects"].append("book")

    assert normalized.scene_hints[0].objects == ("cup",)
    assert normalized.logical_input_hash == original_hash
    with pytest.raises(ValidationError, match="logical_input_hash"):
        NormalizedScriptInput.model_validate(payload)


def test_json_array_round_trip_restores_immutable_semantics() -> None:
    normalized = _normalized(
        _scope(_character("heroine", CharacterRoleRequest.FEMALE)),
        hints=[SceneHint(target_scene_order=1, objects=["cup"], symbols=["leaf"])],
    )
    reconstructed = NormalizedScriptInput.model_validate_json(normalized.model_dump_json())

    assert reconstructed == normalized
    assert reconstructed.scene_hints[0].objects == ("cup",)
    assert reconstructed.scene_hints[0].symbols == ("leaf",)
    assert reconstructed.character_scope_request.recurring_characters[0].character_id == ("heroine")


def test_validated_model_copy_updates_recompute_logical_identity() -> None:
    original = _normalized(
        _scope(_character("heroine", CharacterRoleRequest.FEMALE)),
        hints=[SceneHint(target_scene_order=1, objects=["cup"])],
    )
    hints_update = original.model_copy(
        update={"scene_hints": [SceneHint(target_scene_order=1, objects=["cup", "book"])]}
    )
    scope_update = original.model_copy(
        update={
            "character_scope_request": CharacterScopeRequest(
                recurring_characters=[_character("second_heroine", CharacterRoleRequest.FEMALE)]
            )
        }
    )

    assert hints_update.scene_hints[0].objects == ("cup", "book")
    assert hints_update.logical_input_hash != original.logical_input_hash
    assert scope_update.logical_input_hash != original.logical_input_hash
    assert hints_update.source_sha256 == original.source_sha256
    assert scope_update.source_sha256 == original.source_sha256


def test_identity_evidence_and_result_break_caller_aliases() -> None:
    caller_roles = [CharacterRoleRequest.FEMALE]
    caller_ids = ["heroine"]
    caller_identity = ["preserve recurring female identity"]
    caller_continuity = ["same wardrobe"]
    evidence = IdentityEvidence(
        source="fixture",
        detected_roles=caller_roles,
        recurring_character_ids=caller_ids,
        identity_requirements=caller_identity,
        continuity_requirements=caller_continuity,
    )
    caller_evidence = [evidence]
    result = classify_identity_eligibility(
        _normalized(),
        semantic_evidence=caller_evidence,
    )

    caller_roles.append(CharacterRoleRequest.MALE)
    caller_ids.append("partner")
    caller_identity.append("preserve recurring male identity")
    caller_continuity.append("same family")
    caller_evidence.append(IdentityEvidence(source="later"))

    assert evidence.detected_roles == (CharacterRoleRequest.FEMALE,)
    assert evidence.recurring_character_ids == ("heroine",)
    assert evidence.identity_requirements == ("preserve recurring female identity",)
    assert evidence.continuity_requirements == ("same wardrobe",)
    assert result.status is IdentityEligibilityStatus.SUPPORTED_RECURRING_FEMALE
    assert len(result.scene_beat_evidence) == 2

    caller_reasons = list(result.reasons)
    reconstructed_payload = result.model_dump(mode="python")
    reconstructed_payload["reasons"] = caller_reasons
    reconstructed = IdentityEligibilityResult.model_validate(reconstructed_payload)
    caller_reasons.append("mutated")
    assert reconstructed.reasons == result.reasons


def test_authorized_identity_result_is_deeply_immutable() -> None:
    result = classify_identity_eligibility(
        _normalized(_scope(_character("heroine", CharacterRoleRequest.FEMALE)))
    )
    evidence = result.scene_beat_evidence[0]

    with pytest.raises(AttributeError):
        result.scene_beat_evidence.append(
            IdentityEvidence(
                source="child",
                detected_roles=[CharacterRoleRequest.CHILD],
                family=True,
            )
        )
    with pytest.raises(AttributeError):
        result.detected_roles.append(CharacterRoleRequest.MALE)
    with pytest.raises(TypeError):
        result.detected_roles[0] = CharacterRoleRequest.MALE
    with pytest.raises(AttributeError):
        result.continuity_requirements.append("same family")
    with pytest.raises(AttributeError):
        result.reasons.append("mutated")
    with pytest.raises(AttributeError):
        evidence.detected_roles.append(CharacterRoleRequest.CHILD)
    with pytest.raises(ValidationError):
        result.authority_decision = IdentityAuthorityDecision.DENIED_POLICY

    serialized = result.model_dump(mode="json")
    serialized["detected_roles"].append("male")
    serialized["scene_beat_evidence"][0]["recurring_character_ids"].append("partner")
    assert result.detected_roles == (CharacterRoleRequest.FEMALE,)
    assert result.scene_beat_evidence[0].recurring_character_ids == ("heroine",)


def test_blocked_identity_result_is_deeply_immutable() -> None:
    result = classify_identity_eligibility(
        _normalized(_scope(_character("hero", CharacterRoleRequest.MALE)))
    )

    with pytest.raises(AttributeError):
        result.detected_roles.clear()
    with pytest.raises(AttributeError):
        result.scene_beat_evidence[0].recurring_character_ids.append("heroine")
    assert not result.supported


def _result_payload(
    status: IdentityEligibilityStatus,
) -> dict[str, object]:
    scope_by_status = {
        IdentityEligibilityStatus.UNSUPPORTED_RECURRING_MALE: _scope(
            _character("hero", CharacterRoleRequest.MALE)
        ),
        IdentityEligibilityStatus.UNSUPPORTED_COUPLE: _scope(
            _character("heroine", CharacterRoleRequest.FEMALE),
            relationship=RelationshipScope.COUPLE,
        ),
        IdentityEligibilityStatus.UNSUPPORTED_FAMILY_OR_CHILDREN: _scope(
            _character("heroine", CharacterRoleRequest.FEMALE),
            relationship=RelationshipScope.FAMILY,
        ),
        IdentityEligibilityStatus.UNRESOLVED_IDENTITY: CharacterScopeRequest(),
        IdentityEligibilityStatus.SUPPORTED_RECURRING_FEMALE: _scope(
            _character("heroine", CharacterRoleRequest.FEMALE)
        ),
    }
    return classify_identity_eligibility(_normalized(scope_by_status[status])).model_dump(
        mode="python"
    )


@pytest.mark.parametrize(
    ("status", "updates"),
    [
        (
            IdentityEligibilityStatus.UNSUPPORTED_RECURRING_MALE,
            {"authority_decision": IdentityAuthorityDecision.AUTHORIZED},
        ),
        (
            IdentityEligibilityStatus.UNSUPPORTED_COUPLE,
            {"authority_decision": IdentityAuthorityDecision.AUTHORIZED},
        ),
        (
            IdentityEligibilityStatus.UNSUPPORTED_FAMILY_OR_CHILDREN,
            {"authority_decision": IdentityAuthorityDecision.AUTHORIZED},
        ),
        (
            IdentityEligibilityStatus.UNRESOLVED_IDENTITY,
            {"authority_decision": IdentityAuthorityDecision.AUTHORIZED},
        ),
        (
            IdentityEligibilityStatus.UNSUPPORTED_RECURRING_MALE,
            {"error_code": None},
        ),
        (
            IdentityEligibilityStatus.SUPPORTED_RECURRING_FEMALE,
            {
                "error_code": IdentityAuthorityErrorCode.UNSUPPORTED_IDENTITY_POLICY,
            },
        ),
        (
            IdentityEligibilityStatus.SUPPORTED_RECURRING_FEMALE,
            {"authority_decision": IdentityAuthorityDecision.DENIED_POLICY},
        ),
        (
            IdentityEligibilityStatus.UNSUPPORTED_RECURRING_MALE,
            {"required_anchor_role": "female_identity_anchor"},
        ),
        (
            IdentityEligibilityStatus.UNRESOLVED_IDENTITY,
            {"required_anchor_role": "female_identity_anchor"},
        ),
        (
            IdentityEligibilityStatus.SUPPORTED_RECURRING_FEMALE,
            {"required_anchor_role": "male_identity_anchor"},
        ),
    ],
)
def test_contradictory_identity_result_construction_fails(
    status: IdentityEligibilityStatus,
    updates: dict[str, object],
) -> None:
    payload = _result_payload(status)
    payload.update(updates)

    with pytest.raises(ValidationError) as caught:
        IdentityEligibilityResult.model_validate(payload)
    assert caught.value.errors()[0]["type"] == "INVALID_IDENTITY_DECLARATION"


def test_direct_identity_result_constructor_rejects_authorized_unsupported_male() -> None:
    payload = _result_payload(IdentityEligibilityStatus.UNSUPPORTED_RECURRING_MALE)
    payload.update(
        {
            "authority_decision": IdentityAuthorityDecision.AUTHORIZED,
            "error_code": None,
            "required_anchor_role": "female_identity_anchor",
        }
    )

    with pytest.raises(ValidationError) as caught:
        IdentityEligibilityResult(**payload)
    assert caught.value.errors()[0]["type"] == "INVALID_IDENTITY_DECLARATION"


def test_contradictory_identity_json_and_model_copy_fail() -> None:
    valid = classify_identity_eligibility(
        _normalized(_scope(_character("hero", CharacterRoleRequest.MALE)))
    )
    payload = valid.model_dump(mode="json")
    payload["authority_decision"] = IdentityAuthorityDecision.AUTHORIZED

    with pytest.raises(ValidationError):
        IdentityEligibilityResult.model_validate_json(json.dumps(payload))
    with pytest.raises(ValidationError):
        valid.model_copy(update={"authority_decision": IdentityAuthorityDecision.AUTHORIZED})


def test_supported_result_requires_matching_female_evidence() -> None:
    valid = classify_identity_eligibility(
        _normalized(_scope(_character("heroine", CharacterRoleRequest.FEMALE)))
    )

    with pytest.raises(ValidationError):
        valid.model_copy(update={"detected_roles": [CharacterRoleRequest.MALE]})


@pytest.mark.parametrize(
    "unsupported_evidence",
    [
        pytest.param(
            IdentityEvidence(
                source="nested_male",
                detected_roles=[CharacterRoleRequest.MALE],
                recurring_character_ids=["recurring_man"],
            ),
            id="recurring-male",
        ),
        pytest.param(
            IdentityEvidence(
                source="nested_family_child",
                detected_roles=[CharacterRoleRequest.CHILD],
                recurring_character_ids=["recurring_child"],
                family=True,
                recurring_children=True,
            ),
            id="family-child",
        ),
        pytest.param(
            IdentityEvidence(
                source="nested_second_female",
                detected_roles=[CharacterRoleRequest.FEMALE],
                recurring_character_ids=["second_recurring_woman"],
            ),
            id="multiple-recurring-identities",
        ),
    ],
)
def test_supported_result_rejects_nested_unsupported_identity_evidence(
    unsupported_evidence: IdentityEvidence,
) -> None:
    valid = classify_identity_eligibility(
        _normalized(_scope(_character("heroine", CharacterRoleRequest.FEMALE)))
    )

    with pytest.raises(ValidationError) as caught:
        valid.model_copy(
            update={
                "scene_beat_evidence": [
                    *valid.scene_beat_evidence,
                    unsupported_evidence,
                ]
            }
        )
    assert caught.value.errors()[0]["type"] == "INVALID_IDENTITY_DECLARATION"


def test_identity_result_rejects_continuity_summary_mismatch_with_evidence() -> None:
    evidence = IdentityEvidence(
        source="continuity_fixture",
        detected_roles=[CharacterRoleRequest.FEMALE],
        recurring_character_ids=["heroine"],
        continuity_requirements=["same coat"],
    )
    valid = classify_identity_eligibility(
        _normalized(),
        semantic_evidence=[evidence],
    )
    assert valid.continuity_requirements == ("same coat",)

    with pytest.raises(ValidationError) as caught:
        valid.model_copy(update={"continuity_requirements": []})
    assert caught.value.errors()[0]["type"] == "INVALID_IDENTITY_DECLARATION"


def test_identity_error_dictionary_has_deterministic_json_round_trip() -> None:
    evidence = IdentityEvidence(
        source="error_fixture",
        scene_id="scene_01",
        beat_id="beat_01",
        detected_roles=[CharacterRoleRequest.MALE],
        recurring_character_ids=["recurring_man"],
        continuity_requirements=["same coat"],
    )
    result = classify_identity_eligibility(
        _normalized(),
        semantic_evidence=[evidence],
    )
    error = IdentityEligibilityError(result)

    payload = error.as_dict()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    decoded = json.loads(encoded)
    structured_evidence = decoded["eligibility"]["scene_beat_evidence"]

    assert decoded == payload
    assert decoded["code"] == "UNSUPPORTED_IDENTITY_POLICY"
    assert decoded["eligibility"]["status"] == "UNSUPPORTED_RECURRING_MALE"
    assert decoded["eligibility"]["authority_decision"] == "DENIED_POLICY"
    assert any(item["scene_id"] == "scene_01" for item in structured_evidence)
    assert any(item["beat_id"] == "beat_01" for item in structured_evidence)
    assert decoded["eligibility"]["detected_roles"] == ["male"]
    assert decoded["eligibility"]["continuity_requirements"] == ["same coat"]
    assert decoded["eligibility"]["reasons"] == list(result.reasons)
    assert isinstance(decoded["eligibility"]["scene_beat_evidence"], list)
    assert isinstance(decoded["eligibility"]["detected_roles"][0], str)
    assert str(Path.cwd()) not in encoded
    assert "secret" not in encoded.casefold()


@pytest.mark.parametrize(
    "requested_range",
    [
        (0, 8),
        (-1, 8),
        (8, 0),
        (8, 7),
        (9, 8),
        (7,),
        ("7", 8),
        (True, 8),
        (7.0, 8),
        [7, 8],
        None,
    ],
)
def test_mvp_policy_rejects_structurally_invalid_ranges(
    requested_range: object,
) -> None:
    with pytest.raises(SceneCountStructureError) as caught:
        validate_mvp_scene_count_range(requested_range)  # type: ignore[arg-type]
    assert caught.value.code is SceneCountPolicyErrorCode.INVALID_SCENE_COUNT_RANGE


@pytest.mark.parametrize("requested_range", [(6, 6), (6, 8), (7, 9), (9, 9)])
def test_mvp_policy_separately_rejects_supported_structure_outside_policy(
    requested_range: tuple[int, int],
) -> None:
    with pytest.raises(MVPSceneCountPolicyError) as caught:
        validate_mvp_scene_count_range(requested_range)
    assert caught.value.code is SceneCountPolicyErrorCode.UNSUPPORTED_MVP_SCENE_COUNT_RANGE
