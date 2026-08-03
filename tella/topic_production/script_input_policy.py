"""Explicit MVP planning policy for structurally valid ScriptInput ranges."""

from __future__ import annotations

from enum import StrEnum


MVP_SCENE_COUNT_RANGE = (7, 8)


class SceneCountPolicyErrorCode(StrEnum):
    INVALID_SCENE_COUNT_RANGE = "INVALID_SCENE_COUNT_RANGE"
    UNSUPPORTED_MVP_SCENE_COUNT_RANGE = "UNSUPPORTED_MVP_SCENE_COUNT_RANGE"


class SceneCountStructureError(ValueError):
    code = SceneCountPolicyErrorCode.INVALID_SCENE_COUNT_RANGE

    def __init__(self, requested_range: object):
        self.requested_range = requested_range
        super().__init__(
            f"{self.code.value}: requested range must contain two positive ordered integers"
        )


class MVPSceneCountPolicyError(ValueError):
    code = SceneCountPolicyErrorCode.UNSUPPORTED_MVP_SCENE_COUNT_RANGE

    def __init__(self, requested_range: tuple[int, int]):
        self.requested_range = requested_range
        super().__init__(
            f"{self.code.value}: requested range {requested_range} is outside "
            f"{MVP_SCENE_COUNT_RANGE}"
        )


def validate_mvp_scene_count_range(
    requested_range: tuple[int, int],
) -> tuple[int, int]:
    if (
        not isinstance(requested_range, tuple)
        or len(requested_range) != 2
        or any(type(value) is not int for value in requested_range)
    ):
        raise SceneCountStructureError(requested_range)
    minimum, maximum = requested_range
    if minimum <= 0 or maximum <= 0 or minimum > maximum:
        raise SceneCountStructureError(requested_range)
    allowed_minimum, allowed_maximum = MVP_SCENE_COUNT_RANGE
    if minimum < allowed_minimum or maximum > allowed_maximum:
        raise MVPSceneCountPolicyError(requested_range)
    return requested_range


__all__ = [
    "MVP_SCENE_COUNT_RANGE",
    "MVPSceneCountPolicyError",
    "SceneCountPolicyErrorCode",
    "SceneCountStructureError",
    "validate_mvp_scene_count_range",
]
