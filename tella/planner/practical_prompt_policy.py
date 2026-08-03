"""Fail-closed prompt assembly for the practical visual benchmark."""
from __future__ import annotations

from collections import OrderedDict
from typing import Mapping

PRACTICAL_PROVIDER_PROMPT_MAX_BYTES = 1850
PROMPT_SECTION_ORDER = (
    "action_setting", "required_props", "character_identity",
    "composition", "style", "hard_negatives",
)


def prompt_section_byte_counts(sections: Mapping[str, str]) -> dict[str, int]:
    return {
        name: len(str(sections.get(name, "")).strip().encode("utf-8"))
        for name in PROMPT_SECTION_ORDER
    }


def build_priority_prompt(
    sections: Mapping[str, str], *,
    maximum_bytes: int = PRACTICAL_PROVIDER_PROMPT_MAX_BYTES,
) -> str:
    """Join validated sections in semantic-priority order without truncation."""
    unknown = set(sections) - set(PROMPT_SECTION_ORDER)
    if unknown:
        raise ValueError(f"unsupported prompt sections: {sorted(unknown)}")
    required = ("action_setting", "required_props", "character_identity")
    missing = [name for name in required if not str(sections.get(name, "")).strip()]
    if missing:
        raise ValueError(f"required prompt sections are empty: {missing}")
    ordered = OrderedDict(
        (name, " ".join(str(sections.get(name, "")).split()))
        for name in PROMPT_SECTION_ORDER if str(sections.get(name, "")).strip()
    )
    prompt = " ".join(ordered.values())
    size = len(prompt.encode("utf-8"))
    if size > maximum_bytes:
        raise ValueError(
            f"practical provider prompt is {size} UTF-8 bytes; maximum is {maximum_bytes}; "
            "prompt was rejected without truncation"
        )
    return prompt


def validate_priority_prompt(prompt: str) -> int:
    size = len((prompt or "").encode("utf-8"))
    if not (prompt or "").strip():
        raise ValueError("practical provider prompt is empty")
    if size > PRACTICAL_PROVIDER_PROMPT_MAX_BYTES:
        raise ValueError(
            f"practical provider prompt is {size} UTF-8 bytes; maximum is "
            f"{PRACTICAL_PROVIDER_PROMPT_MAX_BYTES}"
        )
    return size


__all__ = [
    "PRACTICAL_PROVIDER_PROMPT_MAX_BYTES", "PROMPT_SECTION_ORDER",
    "build_priority_prompt", "prompt_section_byte_counts", "validate_priority_prompt",
]
