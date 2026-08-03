"""Pure product-target assessment for structurally valid topic durations."""

from __future__ import annotations

from copy import deepcopy
from enum import StrEnum
import math
from typing import Any, Iterable, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, model_validator


MVP_DURATION_POLICY_ID = "mvp_emotional_duration_32_38_v1"
MVP_DURATION_TARGET_MIN_SECONDS = 32.0
MVP_DURATION_TARGET_MAX_SECONDS = 38.0
BEAT_PACING_TARGET_MIN_SECONDS = 3.0
BEAT_PACING_TARGET_MAX_SECONDS = 5.0


class DurationAssessmentStatus(StrEnum):
    IN_TARGET = "IN_TARGET"
    OUTSIDE_TARGET_WARNING = "OUTSIDE_TARGET_WARNING"


class DurationValueAuthority(StrEnum):
    PLANNED = "PLANNED"
    ESTIMATED = "ESTIMATED"
    MEASURED = "MEASURED"


class DurationAssessmentReasonCode(StrEnum):
    DURATION_IN_TARGET = "DURATION_IN_TARGET"
    OUTSIDE_DURATION_TARGET_WARNING = "OUTSIDE_DURATION_TARGET_WARNING"


class BeatPacingWarningCode(StrEnum):
    BEAT_DURATION_BELOW_PACING_TARGET_WARNING = "BEAT_DURATION_BELOW_PACING_TARGET_WARNING"
    BEAT_DURATION_ABOVE_PACING_TARGET_WARNING = "BEAT_DURATION_ABOVE_PACING_TARGET_WARNING"


class _ValidatedFrozenPolicyModel(BaseModel):
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


class MvpDurationTargetAssessment(_ValidatedFrozenPolicyModel):
    status: DurationAssessmentStatus
    actual_duration_seconds: StrictFloat = Field(gt=0, allow_inf_nan=False)
    target_min_seconds: Literal[32.0] = MVP_DURATION_TARGET_MIN_SECONDS
    target_max_seconds: Literal[38.0] = MVP_DURATION_TARGET_MAX_SECONDS
    value_authority: DurationValueAuthority
    policy_id: Literal["mvp_emotional_duration_32_38_v1"] = MVP_DURATION_POLICY_ID
    reason_code: DurationAssessmentReasonCode

    @model_validator(mode="after")
    def validate_derived_result(self) -> "MvpDurationTargetAssessment":
        in_target = (
            self.target_min_seconds <= self.actual_duration_seconds <= self.target_max_seconds
        )
        expected_status = (
            DurationAssessmentStatus.IN_TARGET
            if in_target
            else DurationAssessmentStatus.OUTSIDE_TARGET_WARNING
        )
        expected_reason = (
            DurationAssessmentReasonCode.DURATION_IN_TARGET
            if in_target
            else DurationAssessmentReasonCode.OUTSIDE_DURATION_TARGET_WARNING
        )
        if self.status is not expected_status or self.reason_code is not expected_reason:
            raise ValueError("duration assessment status and reason must match its value")
        return self


class BeatPacingWarning(_ValidatedFrozenPolicyModel):
    beat_id: str = Field(pattern=r"^beat_[0-9]{2}$")
    actual_duration_seconds: StrictFloat = Field(gt=0, allow_inf_nan=False)
    target_min_seconds: Literal[3.0] = BEAT_PACING_TARGET_MIN_SECONDS
    target_max_seconds: Literal[5.0] = BEAT_PACING_TARGET_MAX_SECONDS
    warning_code: BeatPacingWarningCode

    @model_validator(mode="after")
    def validate_warning(self) -> "BeatPacingWarning":
        if self.actual_duration_seconds < self.target_min_seconds:
            expected = BeatPacingWarningCode.BEAT_DURATION_BELOW_PACING_TARGET_WARNING
        elif self.actual_duration_seconds > self.target_max_seconds:
            expected = BeatPacingWarningCode.BEAT_DURATION_ABOVE_PACING_TARGET_WARNING
        else:
            raise ValueError("in-target beat duration cannot produce a pacing warning")
        if self.warning_code is not expected:
            raise ValueError("beat pacing warning code must match its duration")
        return self


def _require_strict_finite_positive_duration(value: float) -> float:
    if type(value) is not float or not math.isfinite(value) or value <= 0:
        raise ValueError("duration must be a strict finite positive float")
    return value


def assess_mvp_duration_target(
    actual_duration_seconds: float,
    *,
    value_authority: DurationValueAuthority,
) -> MvpDurationTargetAssessment:
    actual = _require_strict_finite_positive_duration(actual_duration_seconds)
    in_target = MVP_DURATION_TARGET_MIN_SECONDS <= actual <= MVP_DURATION_TARGET_MAX_SECONDS
    return MvpDurationTargetAssessment(
        status=(
            DurationAssessmentStatus.IN_TARGET
            if in_target
            else DurationAssessmentStatus.OUTSIDE_TARGET_WARNING
        ),
        actual_duration_seconds=actual,
        value_authority=value_authority,
        reason_code=(
            DurationAssessmentReasonCode.DURATION_IN_TARGET
            if in_target
            else DurationAssessmentReasonCode.OUTSIDE_DURATION_TARGET_WARNING
        ),
    )


def assess_beat_duration_pacing(
    *,
    beat_id: str,
    actual_duration_seconds: float,
) -> BeatPacingWarning | None:
    actual = _require_strict_finite_positive_duration(actual_duration_seconds)
    if actual < BEAT_PACING_TARGET_MIN_SECONDS:
        code = BeatPacingWarningCode.BEAT_DURATION_BELOW_PACING_TARGET_WARNING
    elif actual > BEAT_PACING_TARGET_MAX_SECONDS:
        code = BeatPacingWarningCode.BEAT_DURATION_ABOVE_PACING_TARGET_WARNING
    else:
        return None
    return BeatPacingWarning(
        beat_id=beat_id,
        actual_duration_seconds=actual,
        warning_code=code,
    )


def _derive_planned_duration_policy(
    *,
    target_duration_seconds: float,
    beats: Iterable[tuple[str, int, float]],
) -> tuple[MvpDurationTargetAssessment, tuple[BeatPacingWarning, ...]]:
    """Derive immutable policy records from validated planned duration values."""

    ordered_beats = sorted(beats, key=lambda item: item[1])
    beat_ids = [beat_id for beat_id, _, _ in ordered_beats]
    if len(beat_ids) != len(set(beat_ids)):
        raise ValueError("planned duration policy requires unique beat IDs")
    assessment = assess_mvp_duration_target(
        target_duration_seconds,
        value_authority=DurationValueAuthority.PLANNED,
    )
    warnings = tuple(
        warning
        for beat_id, _, duration_seconds in ordered_beats
        if (
            warning := assess_beat_duration_pacing(
                beat_id=beat_id,
                actual_duration_seconds=duration_seconds,
            )
        )
        is not None
    )
    return assessment, warnings


__all__ = [
    "BEAT_PACING_TARGET_MAX_SECONDS",
    "BEAT_PACING_TARGET_MIN_SECONDS",
    "MVP_DURATION_POLICY_ID",
    "MVP_DURATION_TARGET_MAX_SECONDS",
    "MVP_DURATION_TARGET_MIN_SECONDS",
    "BeatPacingWarning",
    "BeatPacingWarningCode",
    "DurationAssessmentReasonCode",
    "DurationAssessmentStatus",
    "DurationValueAuthority",
    "MvpDurationTargetAssessment",
    "assess_beat_duration_pacing",
    "assess_mvp_duration_target",
]
