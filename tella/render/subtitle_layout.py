"""Scoped deterministic subtitle placement for practical vertical reels."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Mapping

PRACTICAL_DYNAMIC_SUBTITLE_POLICY = "practical_dynamic_v1"


@dataclass(frozen=True)
class NormalizedBox:
    left: float
    top: float
    right: float
    bottom: float
    label: str = "important"

    def overlaps(self, other: "NormalizedBox") -> bool:
        return not (
            self.right <= other.left or self.left >= other.right
            or self.bottom <= other.top or self.top >= other.bottom
        )


@dataclass(frozen=True)
class SubtitleLayoutDecision:
    status: str
    policy_id: str
    placement: str
    caption_center_y_ratio: float
    image_translation_y_ratio: float
    translucent_panel: bool
    protected_overlap: bool
    reason: str

    def metadata(self) -> dict[str, object]:
        return asdict(self)


_PLACEMENTS = (
    ("lower", NormalizedBox(0.10, 0.66, 0.90, 0.80, "subtitle"), 0.73),
    ("upper", NormalizedBox(0.10, 0.20, 0.90, 0.34, "subtitle"), 0.27),
    ("middle_lower", NormalizedBox(0.10, 0.51, 0.90, 0.65, "subtitle"), 0.58),
)


def _box(value: Mapping[str, object]) -> NormalizedBox:
    return NormalizedBox(
        float(value["left"]), float(value["top"]),
        float(value["right"]), float(value["bottom"]),
        str(value.get("label", "important")),
    )


def resolve_practical_subtitle_layout(
    protected_regions: Iterable[Mapping[str, object]], *,
    busy_regions: Iterable[str] = (), translation_safe: bool = False,
) -> SubtitleLayoutDecision:
    protected = tuple(_box(item) for item in protected_regions)
    busy = set(busy_regions)
    clear_busy_candidate: tuple[str, NormalizedBox, float] | None = None
    for name, region, center in _PLACEMENTS:
        if any(region.overlaps(item) for item in protected):
            continue
        if name not in busy:
            return SubtitleLayoutDecision(
                "passed", PRACTICAL_DYNAMIC_SUBTITLE_POLICY, name, center,
                0.0, False, False, f"{name} region preserves protected content",
            )
        clear_busy_candidate = clear_busy_candidate or (name, region, center)
    if translation_safe:
        for shift in (-0.06, 0.06):
            shifted = tuple(
                NormalizedBox(item.left, item.top + shift, item.right, item.bottom + shift, item.label)
                for item in protected
            )
            if all(item.top >= 0.08 and item.bottom <= 0.92 for item in shifted):
                name, region, center = _PLACEMENTS[0]
                if not any(region.overlaps(item) for item in shifted):
                    return SubtitleLayoutDecision(
                        "passed", PRACTICAL_DYNAMIC_SUBTITLE_POLICY,
                        "lower_after_translation", center, shift, False, False,
                        "deterministic vertical translation clears lower subtitle region",
                    )
    if clear_busy_candidate is not None:
        name, _, center = clear_busy_candidate
        return SubtitleLayoutDecision(
            "passed", PRACTICAL_DYNAMIC_SUBTITLE_POLICY, name, center,
            0.0, True, False,
            "region avoids protected content; translucent panel handles busy background",
        )
    return SubtitleLayoutDecision(
        "failed", PRACTICAL_DYNAMIC_SUBTITLE_POLICY, "none", 0.0,
        0.0, False, True,
        "no supported layout preserves all protected faces, hands, props, and actions",
    )


__all__ = [
    "NormalizedBox", "PRACTICAL_DYNAMIC_SUBTITLE_POLICY",
    "SubtitleLayoutDecision", "resolve_practical_subtitle_layout",
]
