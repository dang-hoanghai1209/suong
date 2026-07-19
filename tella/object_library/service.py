"""Provider orchestration and ingest workflow."""

from __future__ import annotations

import hashlib
import json

from tella.object_library.models import ObjectRecord, SourceCandidate, deterministic_object_id
from tella.object_library.processor import process_record
from tella.object_library.registry import build_registry
from tella.object_library.sources.base import ObjectSourceAdapter
from tella.object_library.storage import ObjectStore
from tella.object_library.taxonomy import enrich


class ObjectIngestionService:
    def __init__(self, store: ObjectStore, adapters: list[ObjectSourceAdapter]):
        self.store = store
        self.adapters = {adapter.source: adapter for adapter in adapters}
        self.store.initialize()
        self.cache_stats = {
            "search_hits": 0,
            "search_misses": 0,
            "asset_hits": 0,
            "asset_misses": 0,
        }

    def _cache_key(self, value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _search_cache_path(self, source: str, keyword: str, limit: int):
        key = self._cache_key(f"{keyword.lower()}:{limit}")
        return self.store.root / "cache" / source / "search" / f"{key}.json"

    def _asset_cache_path(self, candidate: SourceCandidate):
        extension = candidate.original_format.lower().lstrip(".") or "bin"
        key = self._cache_key(candidate.source_object_id)
        return self.store.root / "cache" / candidate.source / "assets" / f"{key}.{extension}"

    def search(
        self, keyword: str, limit: int = 32, sources: list[str] | None = None
    ) -> list[SourceCandidate]:
        ordered = sources or ["iconify", "noun_project"]
        candidates = []
        errors = []
        for source in ordered:
            adapter = self.adapters.get(source)
            if adapter is None:
                continue
            try:
                requested = max(1, limit - len(candidates))
                cache_path = self._search_cache_path(source, keyword, requested)
                if cache_path.is_file():
                    found = [
                        SourceCandidate.model_validate(item)
                        for item in json.loads(cache_path.read_text(encoding="utf-8"))
                    ]
                    self.cache_stats["search_hits"] += 1
                else:
                    found = adapter.search(keyword, limit=requested)
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(
                        json.dumps(
                            [item.model_dump(mode="json") for item in found],
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                    self.cache_stats["search_misses"] += 1
            except Exception as exc:
                errors.append(f"{source}: {exc}")
                continue
            candidates.extend(found)
            if len(candidates) >= limit:
                break
        if not candidates and errors:
            raise RuntimeError("; ".join(errors))
        return candidates[:limit]

    def ingest_candidate(
        self, candidate: SourceCandidate, *, process: bool = True, notes: str = ""
    ) -> ObjectRecord:
        adapter = self.adapters.get(candidate.source)
        if adapter is None:
            raise ValueError(f"No adapter configured for {candidate.source}")
        cache_path = self._asset_cache_path(candidate)
        if cache_path.is_file():
            content = cache_path.read_bytes()
            self.cache_stats["asset_hits"] += 1
        else:
            content = adapter.fetch(candidate)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(content)
            self.cache_stats["asset_misses"] += 1
        taxonomy = enrich(candidate.canonical_label, candidate.aliases)
        record = ObjectRecord(
            object_id=deterministic_object_id(candidate.source, candidate.source_object_id),
            source=candidate.source,
            source_object_id=candidate.source_object_id,
            canonical_label=candidate.canonical_label,
            aliases=sorted(set(candidate.aliases)),
            style_family=candidate.style_family,
            license=candidate.license,
            original_format=candidate.original_format.lower(),
            local_raw_path="",
            notes=notes,
            content_sha256=hashlib.sha256(content).hexdigest(),
            **taxonomy,
        )
        raw_path = self.store.write_raw(record, content)
        record.local_raw_path = str(raw_path.resolve())
        self.store.save_record(record)
        if process:
            record = process_record(record, self.store)
        return record

    def ingest_keyword(
        self,
        keyword: str,
        *,
        count: int = 10,
        sources: list[str] | None = None,
        process: bool = True,
    ) -> list[ObjectRecord]:
        candidates = self.search(keyword, limit=count, sources=sources)
        records = [self.ingest_candidate(candidate, process=process) for candidate in candidates]
        build_registry(self.store)
        return records

    def process_pending(self) -> list[ObjectRecord]:
        records = []
        for record in self.store.load_records():
            if record.processing_status != "processed":
                records.append(process_record(record, self.store))
        build_registry(self.store)
        return records
