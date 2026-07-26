"""Mechanical helpers for exact StoryPlan narration coverage migration."""

from __future__ import annotations

from collections.abc import Sequence

from .models import NarrationSourceSpan, SemanticBeat


def assign_fixture_source_spans(
    segments: Sequence[str],
) -> tuple[str, tuple[str, ...], tuple[NarrationSourceSpan, ...]]:
    """Join existing ordered fixture text and assign separators to prior beats.

    This helper does not discover boundaries or infer semantics. It only maps
    caller-supplied authoritative segments into one exact single-space-joined
    narration using the repository's deterministic fixture migration rule.
    """

    if not segments or any(not segment.strip() for segment in segments):
        raise ValueError("fixture source segments must contain non-whitespace text")
    narration_text = " ".join(segments)
    authority_segments = tuple(
        segment + (" " if index < len(segments) - 1 else "")
        for index, segment in enumerate(segments)
    )
    spans: list[NarrationSourceSpan] = []
    cursor = 0
    for segment in authority_segments:
        end = cursor + len(segment)
        spans.append(NarrationSourceSpan(start=cursor, end=end))
        cursor = end
    if "".join(authority_segments) != narration_text:
        raise ValueError("fixture source-span assignment did not preserve narration")
    return narration_text, authority_segments, tuple(spans)


def semantic_beat_display_text(beat: SemanticBeat) -> str:
    """Return boundary-whitespace-normalized text for non-authority consumers."""

    return beat.narration_segment.strip()


__all__: list[str] = []
