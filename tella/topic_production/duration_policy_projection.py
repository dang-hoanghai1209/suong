"""Immutable planned-duration policy projection."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, StrictInt, field_validator, model_validator

from .duration_policy import (
    BeatPacingWarning,
    DurationAssessmentStatus,
    DurationValueAuthority,
    MvpDurationTargetAssessment,
)
from .execution_models import (
    ProductionRunPlan,
    _revalidate_current_production_run_plan,
)


class DurationPolicyReport(BaseModel):
    """Validated projection of planned duration-policy records."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
    )

    schema_version: Literal[1]
    planned_duration_assessment: MvpDurationTargetAssessment
    planned_beat_pacing_warnings: tuple[BeatPacingWarning, ...]
    warning_count: StrictInt

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("DurationPolicyReport schema_version must be exact integer 1")
        return value

    @field_validator("planned_duration_assessment", mode="before")
    @classmethod
    def detach_assessment(cls, value: object) -> object:
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
        warning_ids = [warning.beat_id for warning in self.planned_beat_pacing_warnings]
        if len(warning_ids) != len(set(warning_ids)):
            raise ValueError("planned duration report contains duplicate beat IDs")
        expected_count = int(
            self.planned_duration_assessment.status
            is DurationAssessmentStatus.OUTSIDE_TARGET_WARNING
        ) + len(self.planned_beat_pacing_warnings)
        if self.warning_count != expected_count:
            raise ValueError("warning_count does not match planned duration policy records")
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
    run_plan: ProductionRunPlan,
) -> DurationPolicyReport:
    """Build one detached report from current validated run-plan authority."""

    validated = _revalidate_current_production_run_plan(run_plan)
    assessment = validated.planned_duration_assessment
    warnings = validated.planned_beat_pacing_warnings
    warning_count = int(assessment.status is DurationAssessmentStatus.OUTSIDE_TARGET_WARNING) + len(
        warnings
    )
    return DurationPolicyReport(
        schema_version=1,
        planned_duration_assessment=assessment.model_dump(mode="python"),
        planned_beat_pacing_warnings=tuple(
            warning.model_dump(mode="python") for warning in warnings
        ),
        warning_count=warning_count,
    )


__all__ = [
    "DurationPolicyReport",
    "build_duration_policy_report",
]
