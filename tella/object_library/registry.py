"""Atomic manifests, semantic index, and deterministic candidate ranking."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from tella.atomic_write import atomic_write_json
from tella.object_library.models import ObjectRecord, SearchResult
from tella.object_library.storage import ObjectStore
from tella.object_library.taxonomy import tokens


SOURCE_PRIORITY = {"iconify": 20.0, "noun_project": 10.0, "local": 5.0}


def build_registry(store: ObjectStore) -> dict[str, object]:
    records = sorted(store.load_records(), key=lambda item: item.object_id)
    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "object_count": len(records),
        "objects": [item.model_dump(mode="json") for item in records],
    }
    manifests = store.root / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    atomic_write_json(manifests / "object_manifest.json", manifest)
    for source in ("iconify", "noun_project", "local"):
        subset = [item.model_dump(mode="json") for item in records if item.source == source]
        atomic_write_json(
            manifests / f"{source}.json",
            {"schema_version": 1, "source": source, "object_count": len(subset), "objects": subset},
        )
    inverted: dict[str, list[str]] = {}
    for record in records:
        fields = [
            record.canonical_label,
            *record.aliases,
            *record.semantic_tags,
            *record.emotional_tags,
            *record.categories,
            *record.usage_contexts,
        ]
        for token in tokens(" ".join(fields)):
            inverted.setdefault(token, []).append(record.object_id)
    atomic_write_json(
        manifests / "semantic_index.json",
        {
            "schema_version": 1,
            "terms": {key: sorted(set(value)) for key, value in sorted(inverted.items())},
        },
    )
    return manifest


class ObjectRegistry:
    def __init__(self, records: list[ObjectRecord]):
        self.records = records

    @classmethod
    def from_root(cls, root: str | Path) -> "ObjectRegistry":
        path = Path(root) / "manifests" / "object_manifest.json"
        if not path.is_file():
            return cls(ObjectStore(root).load_records())
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls([ObjectRecord.model_validate(item) for item in payload.get("objects", [])])

    def search(
        self,
        query: str,
        *,
        moods: list[str] | None = None,
        contexts: list[str] | None = None,
        categories: list[str] | None = None,
        source: str | None = None,
        style_family: str | None = None,
        production_only: bool = True,
        limit: int = 20,
    ) -> list[SearchResult]:
        query_tokens = tokens(query)
        requested_moods, requested_contexts, requested_categories = (
            set(moods or []),
            set(contexts or []),
            set(categories or []),
        )
        ranked = []
        for record in self.records:
            if production_only and not record.production_eligible:
                continue
            if source and record.source != source:
                continue
            if style_family and record.style_family != style_family:
                continue
            if requested_categories and not requested_categories.intersection(record.categories):
                continue
            label_tokens = tokens(record.canonical_label)
            semantic = set(record.semantic_tags) | set(record.aliases) | label_tokens
            overlap = query_tokens.intersection(semantic)
            if query_tokens and not overlap:
                continue
            score, reasons = SOURCE_PRIORITY.get(record.source, 0.0), [f"source:{record.source}"]
            exact = query.strip().lower() == record.canonical_label.lower()
            if exact:
                score += 100
                reasons.append("exact_label")
            score += 25 * len(overlap)
            if overlap:
                reasons.append("semantic_match:" + ",".join(sorted(overlap)))
            mood_overlap = requested_moods.intersection(record.emotional_tags)
            context_overlap = requested_contexts.intersection(record.usage_contexts)
            score += 18 * len(mood_overlap) + 12 * len(context_overlap)
            if mood_overlap:
                reasons.append("mood_match:" + ",".join(sorted(mood_overlap)))
            if context_overlap:
                reasons.append("context_match:" + ",".join(sorted(context_overlap)))
            if record.quality_status == "approved":
                score += 15
                reasons.append("approved")
            ranked.append(SearchResult(object=record, score=score, reasons=reasons))
        return sorted(ranked, key=lambda item: (-item.score, item.object.object_id))[:limit]
