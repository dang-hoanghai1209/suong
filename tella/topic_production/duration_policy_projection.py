"""Immutable planned and measured duration-policy projection."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from .duration_policy import (
    BeatPacingWarning,
    DurationAssessmentStatus,
    DurationValueAuthority,
    MvpDurationTargetAssessment,
)
from .runtime import _revalidate_execution_state
from .runtime_models import ExecutionRunState


class DurationPolicyReport(BaseModel):
    """Validated, source-unbound projection of duration-policy records."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
    )

    schema_version: Literal[2]
    planned_duration_assessment: MvpDurationTargetAssessment
    planned_beat_pacing_warnings: tuple[BeatPacingWarning, ...]
    measured_duration_assessment: MvpDurationTargetAssessment | None
    warning_count: StrictInt = Field(ge=0)

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 2:
            raise ValueError("DurationPolicyReport schema_version must be exact integer 2")
        return value

    @field_validator(
        "planned_duration_assessment",
        "measured_duration_assessment",
        mode="before",
    )
    @classmethod
    def detach_assessment(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, MvpDurationTargetAssessment):
            return value.model_dump(mode="python")
        return deepcopy(value)

    @field_validator("planned_beat_pacing_warnings", mode="before")
    @classmethod
    def detach_warnings(cls, value: object) -> object:
        if isinstance(value, (list, tuple)):
            return tuple(
                (
                    item.model_dump(mode="python")
                    if isinstance(item, BeatPacingWarning)
                    else deepcopy(item)
                )
                for item in value
            )
        return value

    @model_validator(mode="after")
    def validate_projection(self) -> "DurationPolicyReport":
        if self.planned_duration_assessment.value_authority is not DurationValueAuthority.PLANNED:
            raise ValueError("planned duration report requires PLANNED authority")
        if (
            self.measured_duration_assessment is not None
            and self.measured_duration_assessment.value_authority
            is not DurationValueAuthority.MEASURED
        ):
            raise ValueError("measured duration report requires MEASURED authority")
        warning_ids = [warning.beat_id for warning in self.planned_beat_pacing_warnings]
        if len(warning_ids) != len(set(warning_ids)):
            raise ValueError("planned duration report contains duplicate beat IDs")
        if warning_ids != sorted(warning_ids):
            raise ValueError("planned duration report warnings are not in canonical beat ID order")
        expected_count = (
            int(
                self.planned_duration_assessment.status
                is DurationAssessmentStatus.OUTSIDE_TARGET_WARNING
            )
            + len(self.planned_beat_pacing_warnings)
            + int(
                self.measured_duration_assessment is not None
                and self.measured_duration_assessment.status
                is DurationAssessmentStatus.OUTSIDE_TARGET_WARNING
            )
        )
        if self.warning_count != expected_count:
            raise ValueError("warning_count does not match duration policy records")
        return self

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


def build_duration_policy_report(
    state: ExecutionRunState,
) -> DurationPolicyReport:
    """Build one detached report from current validated runtime authority."""

    validated = _revalidate_execution_state(state)
    planned_assessment = validated.run_plan.planned_duration_assessment
    warnings = validated.run_plan.planned_beat_pacing_warnings
    measurement = validated.processed_narration_measurement
    measured_assessment = (
        measurement.measured_duration_assessment if measurement is not None else None
    )
    warning_count = (
        int(planned_assessment.status is DurationAssessmentStatus.OUTSIDE_TARGET_WARNING)
        + len(warnings)
        + int(
            measured_assessment is not None
            and measured_assessment.status is DurationAssessmentStatus.OUTSIDE_TARGET_WARNING
        )
    )
    return DurationPolicyReport(
        schema_version=2,
        planned_duration_assessment=planned_assessment.model_dump(mode="python"),
        planned_beat_pacing_warnings=tuple(
            warning.model_dump(mode="python") for warning in warnings
        ),
        measured_duration_assessment=(
            measured_assessment.model_dump(mode="python")
            if measured_assessment is not None
            else None
        ),
        warning_count=warning_count,
    )


__all__ = [
    "DurationPolicyReport",
    "build_duration_policy_report",
]
