"""Typed provider policy for production continuous narration."""
from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from typing import Literal

from tella.planner.models import TellaScenePlan
from tella.tts import gemini

GEMINI_EMOTIONAL_MODEL = "gemini-3.1-flash-tts-preview"
GEMINI_EMOTIONAL_VOICE = "Callirrhoe"
GEMINI_EMOTIONAL_STYLE = "gentle_emotional"
GEMINI_EMOTIONAL_PROFILE = "gemini_callirrhoe_vi_gentle_emotional"


@dataclass(frozen=True)
class ProductionTTSPolicy:
    policy_id: str
    mode: Literal["production_emotional", "explicit", "legacy"]
    preferred_provider: str
    actual_provider: str
    preferred_voice: str
    actual_voice: str
    voice_gender: str
    model: str
    style: str
    language: str
    fallback_allowed: bool
    fallback_used: bool
    fallback_provider: str
    fallback_reason: str
    selection_reason: str

    def metadata(self) -> dict[str, object]:
        return asdict(self)


def is_production_emotional(plan: TellaScenePlan) -> bool:
    return (
        plan.theme == "minimalist_emotional"
        and plan.requested_production_duration_seconds > 0
    )


def resolve_production_tts_policy(
    plan: TellaScenePlan,
    *,
    explicit_provider: str = "",
) -> ProductionTTSPolicy:
    """Resolve provider selection without making any provider request."""
    explicit = (explicit_provider or "").strip().lower()
    language = "vi-VN" if plan.language == "vi" else plan.language
    if explicit:
        return ProductionTTSPolicy(
            policy_id="explicit_tts_configuration_v1",
            mode="explicit",
            preferred_provider=explicit,
            actual_provider=explicit,
            preferred_voice="",
            actual_voice="",
            voice_gender="",
            model="",
            style="",
            language=plan.language,
            fallback_allowed=False,
            fallback_used=False,
            fallback_provider="",
            fallback_reason="",
            selection_reason="explicit provider configuration",
        )

    if is_production_emotional(plan):
        _name, credential = gemini.resolve_api_key_from_environment()
        if credential:
            return ProductionTTSPolicy(
                policy_id="production_emotional_callirrhoe_v1",
                mode="production_emotional",
                preferred_provider="gemini",
                actual_provider="gemini",
                preferred_voice=GEMINI_EMOTIONAL_VOICE,
                actual_voice=GEMINI_EMOTIONAL_VOICE,
                voice_gender="female",
                model=GEMINI_EMOTIONAL_MODEL,
                style=GEMINI_EMOTIONAL_STYLE,
                language=language,
                fallback_allowed=True,
                fallback_used=False,
                fallback_provider="edge",
                fallback_reason="",
                selection_reason="preferred provider configured",
            )
        return ProductionTTSPolicy(
            policy_id="production_emotional_callirrhoe_v1",
            mode="production_emotional",
            preferred_provider="gemini",
            actual_provider="edge",
            preferred_voice=GEMINI_EMOTIONAL_VOICE,
            actual_voice=(
                plan.voice_name
                or ("vi-VN-HoaiMyNeural" if plan.language == "vi" else "en-US-JennyNeural")
            ),
            voice_gender="female",
            model=GEMINI_EMOTIONAL_MODEL,
            style=GEMINI_EMOTIONAL_STYLE,
            language=language,
            fallback_allowed=True,
            fallback_used=True,
            fallback_provider="edge",
            fallback_reason="preferred_provider_unavailable",
            selection_reason="Gemini credential not configured; approved Edge fallback",
        )

    return ProductionTTSPolicy(
        policy_id="legacy_tts_selection_v1",
        mode="legacy",
        preferred_provider="edge",
        actual_provider="edge",
        preferred_voice="",
        actual_voice="",
        voice_gender="",
        model="",
        style="",
        language=plan.language,
        fallback_allowed=True,
        fallback_used=False,
        fallback_provider="edge",
        fallback_reason="",
        selection_reason="legacy provider default",
    )


def narration_planning_diagnostic(
    text: str,
    requested_duration: float,
) -> dict[str, object]:
    """Estimate only gross text/target mismatch; never claim audio precision."""
    normalized = re.sub(r"\s+", " ", text).strip()
    words = len(normalized.split()) if normalized else 0
    characters = len(normalized)
    estimated_min = round(words / 3.0, 3) if words else 0.0
    estimated_max = round(words / 2.0, 3) if words else 0.0
    requested = max(0.0, float(requested_duration))
    if requested <= 0 or words == 0:
        status = "not_evaluated"
    elif estimated_max < requested * 0.7:
        status = "likely_too_short"
    elif estimated_min > requested * 1.3:
        status = "likely_too_long"
    else:
        status = "plausible_for_target"
    return {
        "narration_text_characters": characters,
        "narration_text_words": words,
        "estimated_duration_range_seconds": [estimated_min, estimated_max],
        "estimate_basis": "2.0_to_3.0_whitespace_words_per_second",
        "estimate_is_authoritative": False,
        "requested_duration_seconds": requested,
        "planning_duration_status": status,
    }


def sanitize_tts_error(exc: BaseException) -> str:
    value = str(exc)
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        secret = (os.environ.get(name) or "").strip().strip("'\"").strip()
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return value[:500]


__all__ = [
    "GEMINI_EMOTIONAL_MODEL",
    "GEMINI_EMOTIONAL_PROFILE",
    "GEMINI_EMOTIONAL_STYLE",
    "GEMINI_EMOTIONAL_VOICE",
    "ProductionTTSPolicy",
    "is_production_emotional",
    "narration_planning_diagnostic",
    "resolve_production_tts_policy",
    "sanitize_tts_error",
]
