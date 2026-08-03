"""Fail-closed INPUT_DECLARATION_ELIGIBILITY_ONLY identity policy.

Raw narration is not semantically analyzed. Typed caller declarations,
conservative scene-hint evidence, and later structured scene-brief evidence are
the only inputs. This module is not SCRIPT_SEMANTIC_IDENTITY_CLASSIFICATION.
"""

from __future__ import annotations

from copy import deepcopy
from enum import StrEnum
import re
from typing import Any, Self, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from .models import ProductionSceneBrief, SceneType
from .script_input import (
    CharacterRoleRequest,
    NormalizedScriptInput,
    RelationshipScope,
)


class IdentityClassificationCapability(StrEnum):
    INPUT_DECLARATION_ELIGIBILITY_ONLY = "INPUT_DECLARATION_ELIGIBILITY_ONLY"


class IdentityEligibilityStatus(StrEnum):
    SUPPORTED_RECURRING_FEMALE = "SUPPORTED_RECURRING_FEMALE"
    SUPPORTED_FEMALE_WITH_ANONYMOUS_BACKGROUND = "SUPPORTED_FEMALE_WITH_ANONYMOUS_BACKGROUND"
    UNSUPPORTED_RECURRING_MALE = "UNSUPPORTED_RECURRING_MALE"
    UNSUPPORTED_COUPLE = "UNSUPPORTED_COUPLE"
    UNSUPPORTED_MULTIPLE_RECURRING = "UNSUPPORTED_MULTIPLE_RECURRING"
    UNSUPPORTED_FAMILY_OR_CHILDREN = "UNSUPPORTED_FAMILY_OR_CHILDREN"
    UNRESOLVED_IDENTITY = "UNRESOLVED_IDENTITY"


class IdentityAuthorityErrorCode(StrEnum):
    UNSUPPORTED_IDENTITY_POLICY = "UNSUPPORTED_IDENTITY_POLICY"
    UNRESOLVED_IDENTITY_EVIDENCE = "UNRESOLVED_IDENTITY_EVIDENCE"
    INVALID_IDENTITY_DECLARATION = "INVALID_IDENTITY_DECLARATION"


class IdentityAuthorityDecision(StrEnum):
    AUTHORIZED = "AUTHORIZED"
    DENIED_POLICY = "DENIED_POLICY"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class _ValidatedFrozenIdentityModel(BaseModel):
    """Deeply immutable authority model with validated copy semantics."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
    )

    def model_copy(
        self,
        *,
        update: dict[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(mode="python")
        if deep:
            payload = deepcopy(payload)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)


# The ordering is product authority, not incidental control flow.
IDENTITY_STATUS_PRECEDENCE = (
    IdentityEligibilityStatus.UNSUPPORTED_FAMILY_OR_CHILDREN,
    IdentityEligibilityStatus.UNSUPPORTED_COUPLE,
    IdentityEligibilityStatus.UNSUPPORTED_RECURRING_MALE,
    IdentityEligibilityStatus.UNSUPPORTED_MULTIPLE_RECURRING,
    IdentityEligibilityStatus.UNRESOLVED_IDENTITY,
    IdentityEligibilityStatus.SUPPORTED_FEMALE_WITH_ANONYMOUS_BACKGROUND,
    IdentityEligibilityStatus.SUPPORTED_RECURRING_FEMALE,
)
_SUPPORTED_STATUSES = {
    IdentityEligibilityStatus.SUPPORTED_RECURRING_FEMALE,
    IdentityEligibilityStatus.SUPPORTED_FEMALE_WITH_ANONYMOUS_BACKGROUND,
}
_UNSUPPORTED_STATUSES = {
    IdentityEligibilityStatus.UNSUPPORTED_RECURRING_MALE,
    IdentityEligibilityStatus.UNSUPPORTED_COUPLE,
    IdentityEligibilityStatus.UNSUPPORTED_MULTIPLE_RECURRING,
    IdentityEligibilityStatus.UNSUPPORTED_FAMILY_OR_CHILDREN,
}


class IdentityEvidence(_ValidatedFrozenIdentityModel):
    source: str = Field(min_length=1)
    scene_id: str | None = Field(default=None, pattern=r"^scene_[0-9]{2,4}$")
    beat_id: str | None = Field(default=None, pattern=r"^beat_[0-9]{2,4}$")
    detected_roles: tuple[CharacterRoleRequest, ...] = Field(default_factory=tuple)
    recurring_character_ids: tuple[str, ...] = Field(default_factory=tuple)
    anonymous_background_people: bool = False
    abstract_or_anonymous_only: bool = False
    couple: bool = False
    family: bool = False
    recurring_children: bool = False
    unresolved_recurring_identity: bool = False
    identity_requirements: tuple[str, ...] = Field(default_factory=tuple)
    continuity_requirements: tuple[str, ...] = Field(default_factory=tuple)
    detail: str = ""


def _summarize_identity_evidence(
    evidence: tuple[IdentityEvidence, ...],
) -> tuple[
    tuple[CharacterRoleRequest, ...],
    tuple[str, ...],
    tuple[str, ...],
    bool,
    bool,
]:
    roles = tuple(dict.fromkeys(role for item in evidence for role in item.detected_roles))
    recurring_ids = tuple(
        dict.fromkeys(
            character_id.casefold()
            for item in evidence
            for character_id in item.recurring_character_ids
        )
    )
    continuity = tuple(
        dict.fromkeys(
            requirement for item in evidence for requirement in item.continuity_requirements
        )
    )
    anonymous = any(item.anonymous_background_people for item in evidence)
    unresolved = (
        not recurring_ids
        or CharacterRoleRequest.UNKNOWN in roles
        or any(
            item.unresolved_recurring_identity or item.abstract_or_anonymous_only
            for item in evidence
        )
    )
    return roles, recurring_ids, continuity, anonymous, unresolved


def _status_from_evidence(
    evidence: tuple[IdentityEvidence, ...],
) -> tuple[
    IdentityEligibilityStatus,
    tuple[CharacterRoleRequest, ...],
    tuple[str, ...],
]:
    roles, recurring_ids, continuity, anonymous, unresolved = _summarize_identity_evidence(evidence)
    candidates: set[IdentityEligibilityStatus] = set()
    if (
        any(item.family or item.recurring_children for item in evidence)
        or CharacterRoleRequest.CHILD in roles
    ):
        candidates.add(IdentityEligibilityStatus.UNSUPPORTED_FAMILY_OR_CHILDREN)
    if any(item.couple for item in evidence):
        candidates.add(IdentityEligibilityStatus.UNSUPPORTED_COUPLE)
    if CharacterRoleRequest.MALE in roles:
        candidates.add(IdentityEligibilityStatus.UNSUPPORTED_RECURRING_MALE)
    if len(recurring_ids) > 1:
        candidates.add(IdentityEligibilityStatus.UNSUPPORTED_MULTIPLE_RECURRING)
    if unresolved:
        candidates.add(IdentityEligibilityStatus.UNRESOLVED_IDENTITY)
    if not unresolved and roles == (CharacterRoleRequest.FEMALE,) and len(recurring_ids) == 1:
        candidates.add(
            IdentityEligibilityStatus.SUPPORTED_FEMALE_WITH_ANONYMOUS_BACKGROUND
            if anonymous
            else IdentityEligibilityStatus.SUPPORTED_RECURRING_FEMALE
        )
    if not candidates:
        candidates.add(IdentityEligibilityStatus.UNRESOLVED_IDENTITY)
    status = next(candidate for candidate in IDENTITY_STATUS_PRECEDENCE if candidate in candidates)
    return status, roles, continuity


class IdentityEligibilityResult(_ValidatedFrozenIdentityModel):
    capability: IdentityClassificationCapability = (
        IdentityClassificationCapability.INPUT_DECLARATION_ELIGIBILITY_ONLY
    )
    status: IdentityEligibilityStatus
    authority_decision: IdentityAuthorityDecision
    error_code: IdentityAuthorityErrorCode | None = None
    scene_beat_evidence: tuple[IdentityEvidence, ...] = Field(min_length=1)
    detected_roles: tuple[CharacterRoleRequest, ...]
    continuity_requirements: tuple[str, ...]
    required_anchor_role: str | None = None
    reasons: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_authority_matrix(self) -> "IdentityEligibilityResult":
        expected_status, expected_roles, expected_continuity = _status_from_evidence(
            self.scene_beat_evidence
        )
        if (
            self.status is not expected_status
            or self.detected_roles != expected_roles
            or self.continuity_requirements != expected_continuity
        ):
            raise PydanticCustomError(
                IdentityAuthorityErrorCode.INVALID_IDENTITY_DECLARATION.value,
                "identity eligibility status and summaries do not match evidence",
            )
        if self.status in _SUPPORTED_STATUSES:
            expected_decision = IdentityAuthorityDecision.AUTHORIZED
            expected_error = None
            expected_anchor = "female_identity_anchor"
        elif self.status in _UNSUPPORTED_STATUSES:
            expected_decision = IdentityAuthorityDecision.DENIED_POLICY
            expected_error = IdentityAuthorityErrorCode.UNSUPPORTED_IDENTITY_POLICY
            expected_anchor = None
        else:
            expected_decision = IdentityAuthorityDecision.INSUFFICIENT_EVIDENCE
            expected_error = IdentityAuthorityErrorCode.UNRESOLVED_IDENTITY_EVIDENCE
            expected_anchor = None
        if (
            self.authority_decision is not expected_decision
            or self.error_code is not expected_error
            or self.required_anchor_role != expected_anchor
        ):
            raise PydanticCustomError(
                IdentityAuthorityErrorCode.INVALID_IDENTITY_DECLARATION.value,
                "identity eligibility status, decision, error code, and anchor are inconsistent",
            )
        return self

    @property
    def supported(self) -> bool:
        return self.status in _SUPPORTED_STATUSES


class IdentityEligibilityError(ValueError):
    """Serializable pre-visual policy/evidence failure."""

    def __init__(self, result: IdentityEligibilityResult):
        if result.error_code is None:
            raise ValueError("identity eligibility failure requires a stable error code")
        self.code = result.error_code
        self.result = result
        super().__init__(f"{self.code.value}: {result.status.value}: " + "; ".join(result.reasons))

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code.value,
            "eligibility": self.result.model_dump(mode="json"),
        }


_KNOWN_POLICY_PATTERNS = {
    "family": re.compile(
        r"\b(family|same family|mother and child|father and child|parent and child|"
        r"gia đình|mẹ và con|cha và con)\b",
        re.IGNORECASE,
    ),
    "child": re.compile(
        r"\b(recurring child|same child|children|child|kid|toddler|"
        r"trẻ em|đứa trẻ|em bé|trẻ nhỏ)\b",
        re.IGNORECASE,
    ),
    "couple": re.compile(
        r"\b(couple|same couple|recurring couple|husband and wife|"
        r"boyfriend and girlfriend|vợ chồng|cặp đôi|người yêu)\b",
        re.IGNORECASE,
    ),
    "male": re.compile(
        r"\b(recurring man|recurring male|same man|same male|male identity|"
        r"nhân vật nam chính|người đàn ông lặp lại)\b",
        re.IGNORECASE,
    ),
    "female": re.compile(
        r"\b(recurring woman|recurring female|same woman|same female|female identity|"
        r"nhân vật nữ chính|người phụ nữ lặp lại)\b",
        re.IGNORECASE,
    ),
    "anonymous": re.compile(
        r"\b(anonymous background|background people|anonymous people|anonymous crowd|"
        r"non-recurring crowd|đám đông vô danh|người nền vô danh)\b",
        re.IGNORECASE,
    ),
}
_IDENTITY_BEARING_PATTERN = re.compile(
    r"\b(identity|character|recurring|same person|same woman|same man|couple|"
    r"family|child|children|anonymous|background people|nhân vật|danh tính|"
    r"lặp lại|gia đình|trẻ em|cặp đôi)\b",
    re.IGNORECASE,
)
_ANONYMOUS_CHARACTER_IDS = {
    "anonymous",
    "anonymous_person",
    "anonymous_background_person",
    "anonymous_background_people",
    "background_people",
    "crowd",
    "abstract_silhouette",
    "anonymous_silhouette",
}
_ROLE_BY_CHARACTER_ID = {
    "recurring_woman": CharacterRoleRequest.FEMALE,
    "woman": CharacterRoleRequest.FEMALE,
    "female": CharacterRoleRequest.FEMALE,
    "recurring_man": CharacterRoleRequest.MALE,
    "man": CharacterRoleRequest.MALE,
    "male": CharacterRoleRequest.MALE,
    "recurring_child": CharacterRoleRequest.CHILD,
    "child": CharacterRoleRequest.CHILD,
}


def _known_semantics(text: str) -> set[str]:
    return {name for name, pattern in _KNOWN_POLICY_PATTERNS.items() if pattern.search(text)}


def _hint_evidence(request: NormalizedScriptInput) -> tuple[IdentityEvidence, ...]:
    evidence: list[IdentityEvidence] = []
    for hint in request.scene_hints:
        matches = _known_semantics(hint.semantic_text())
        unsupported = matches & {"family", "child", "couple", "male"}
        if not unsupported:
            continue
        roles: list[CharacterRoleRequest] = []
        if unsupported & {"family", "couple", "male"}:
            roles.append(CharacterRoleRequest.MALE)
        if unsupported & {"family", "child"}:
            roles.append(CharacterRoleRequest.CHILD)
        evidence.append(
            IdentityEvidence(
                source="typed_scene_hint",
                scene_id=f"scene_{hint.target_order:02d}",
                beat_id=hint.target_beat_id,
                detected_roles=list(dict.fromkeys(roles)),
                recurring_character_ids=[
                    f"hint_unsupported_identity_scene_{hint.target_order:02d}"
                ],
                couple="couple" in matches,
                family="family" in matches,
                recurring_children=bool(matches & {"family", "child"}),
                detail="bounded unsupported identity semantics in typed scene hint",
            )
        )
    return tuple(evidence)


def identity_evidence_from_scene_briefs(
    briefs: Sequence[ProductionSceneBrief],
) -> tuple[IdentityEvidence, ...]:
    """Conservatively adapt structured brief vocabulary before reference selection."""

    evidence: list[IdentityEvidence] = []
    for brief in briefs:
        roles: list[CharacterRoleRequest] = []
        recurring_ids: list[str] = []
        anonymous = False
        unknown_character = False
        for raw_character in brief.characters:
            character = raw_character.strip().casefold()
            if character in _ANONYMOUS_CHARACTER_IDS:
                anonymous = True
                continue
            role = _ROLE_BY_CHARACTER_ID.get(character)
            if role is not None:
                roles.append(role)
            else:
                roles.append(CharacterRoleRequest.UNKNOWN)
                unknown_character = True
            if character.startswith("recurring_"):
                recurring_ids.append(character)

        requirements = [
            *brief.identity_requirements,
            *brief.continuity_requirements,
        ]
        requirements_text = " ".join(requirements).casefold()
        semantics = _known_semantics(requirements_text)
        family = "family" in semantics
        children = "child" in semantics or CharacterRoleRequest.CHILD in roles
        couple = brief.scene_type is SceneType.RELATIONSHIP_VIGNETTE or "couple" in semantics
        if "male" in semantics:
            roles.append(CharacterRoleRequest.MALE)
        if "female" in semantics:
            roles.append(CharacterRoleRequest.FEMALE)
        anonymous = anonymous or "anonymous" in semantics

        recognized_identity_semantics = bool(
            semantics & {"family", "child", "couple", "male", "female", "anonymous"}
        )
        unknown_identity_text = any(
            _IDENTITY_BEARING_PATTERN.search(requirement) and not _known_semantics(requirement)
            for requirement in requirements
        )
        unresolved = unknown_character or unknown_identity_text
        if brief.identity_requirements and not recognized_identity_semantics:
            unresolved = True
        if any(
            character.startswith("recurring_") and character not in _ROLE_BY_CHARACTER_ID
            for character in (item.strip().casefold() for item in brief.characters)
        ):
            unresolved = True

        evidence.append(
            IdentityEvidence(
                source="production_scene_brief",
                scene_id=brief.scene_id,
                beat_id=brief.source_beat_id,
                detected_roles=list(dict.fromkeys(roles)),
                recurring_character_ids=list(dict.fromkeys(recurring_ids)),
                anonymous_background_people=anonymous,
                abstract_or_anonymous_only=anonymous and not recurring_ids,
                couple=couple,
                family=family,
                recurring_children=children,
                unresolved_recurring_identity=unresolved,
                identity_requirements=list(brief.identity_requirements),
                continuity_requirements=list(brief.continuity_requirements),
                detail="structured ProductionSceneBrief identity evidence",
            )
        )
    return tuple(evidence)


_REASON_BY_STATUS = {
    IdentityEligibilityStatus.UNSUPPORTED_FAMILY_OR_CHILDREN: (
        "family or recurring-child identity is outside MVP authority"
    ),
    IdentityEligibilityStatus.UNSUPPORTED_COUPLE: (
        "couple identity is outside MVP authority even when a legacy anchor exists"
    ),
    IdentityEligibilityStatus.UNSUPPORTED_RECURRING_MALE: (
        "recurring male identity is outside MVP authority"
    ),
    IdentityEligibilityStatus.UNSUPPORTED_MULTIPLE_RECURRING: (
        "more than one recurring identity is outside MVP authority"
    ),
    IdentityEligibilityStatus.UNRESOLVED_IDENTITY: (
        "structured evidence is insufficient to authorize one recurring female"
    ),
    IdentityEligibilityStatus.SUPPORTED_FEMALE_WITH_ANONYMOUS_BACKGROUND: (
        "one recurring female and only anonymous non-recurring background people are declared"
    ),
    IdentityEligibilityStatus.SUPPORTED_RECURRING_FEMALE: (
        "exactly one recurring female is explicitly identified"
    ),
}


def classify_identity_eligibility(
    request: NormalizedScriptInput,
    *,
    semantic_evidence: Sequence[IdentityEvidence] | None = None,
) -> IdentityEligibilityResult:
    scope = request.character_scope_request
    evidence = (
        IdentityEvidence(
            source="character_scope_request",
            detected_roles=[item.role for item in scope.recurring_characters],
            recurring_character_ids=[item.character_id for item in scope.recurring_characters],
            anonymous_background_people=scope.anonymous_background_people,
            couple=scope.relationship is RelationshipScope.COUPLE,
            family=scope.relationship is RelationshipScope.FAMILY,
            unresolved_recurring_identity=(scope.relationship is RelationshipScope.UNRESOLVED),
            detail="caller-declared typed character scope",
        ),
        *(semantic_evidence or ()),
        *_hint_evidence(request),
    )
    status, roles, continuity = _status_from_evidence(evidence)
    if status in {
        IdentityEligibilityStatus.SUPPORTED_RECURRING_FEMALE,
        IdentityEligibilityStatus.SUPPORTED_FEMALE_WITH_ANONYMOUS_BACKGROUND,
    }:
        decision = IdentityAuthorityDecision.AUTHORIZED
        error_code = None
    elif status is IdentityEligibilityStatus.UNRESOLVED_IDENTITY:
        decision = IdentityAuthorityDecision.INSUFFICIENT_EVIDENCE
        error_code = IdentityAuthorityErrorCode.UNRESOLVED_IDENTITY_EVIDENCE
    else:
        decision = IdentityAuthorityDecision.DENIED_POLICY
        error_code = IdentityAuthorityErrorCode.UNSUPPORTED_IDENTITY_POLICY

    return IdentityEligibilityResult(
        status=status,
        authority_decision=decision,
        error_code=error_code,
        scene_beat_evidence=evidence,
        detected_roles=roles,
        continuity_requirements=continuity,
        required_anchor_role=(
            "female_identity_anchor" if decision is IdentityAuthorityDecision.AUTHORIZED else None
        ),
        reasons=(_REASON_BY_STATUS[status],),
    )


def require_supported_identity(
    request: NormalizedScriptInput,
    *,
    semantic_evidence: Sequence[IdentityEvidence] | None = None,
) -> IdentityEligibilityResult:
    """Contract guard only; no production call site is wired in this checkpoint."""

    result = classify_identity_eligibility(
        request,
        semantic_evidence=semantic_evidence,
    )
    if not result.supported:
        raise IdentityEligibilityError(result)
    return result


__all__ = [
    "IDENTITY_STATUS_PRECEDENCE",
    "IdentityAuthorityDecision",
    "IdentityAuthorityErrorCode",
    "IdentityClassificationCapability",
    "IdentityEligibilityError",
    "IdentityEligibilityResult",
    "IdentityEligibilityStatus",
    "IdentityEvidence",
    "classify_identity_eligibility",
    "identity_evidence_from_scene_briefs",
    "require_supported_identity",
]
