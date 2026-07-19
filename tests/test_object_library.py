from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
from PIL import Image, ImageDraw

from tella.object_library.models import (
    LicenseMetadata,
    ObjectRecord,
    SourceCandidate,
    deterministic_object_id,
)
from tella.object_library.processor import normalize_png, normalize_svg, process_record
from tella.object_library.registry import ObjectRegistry, build_registry
from tella.object_library.service import ObjectIngestionService
from tella.object_library.sources.iconify import IconifyAdapter
from tella.object_library.sources.noun_project import NounProjectAdapter
from tella.object_library.storage import ObjectStore


def _png(*, opaque: bool = False) -> bytes:
    image = Image.new("RGBA", (100, 80), "white" if opaque else (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((25, 10, 75, 70), fill=(40, 30, 20, 255))
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


class FakeAdapter:
    def __init__(self, source: str, count: int = 2):
        self.source = source
        self.count = count

    def search(self, keyword: str, limit: int = 32) -> list[SourceCandidate]:
        return [
            SourceCandidate(
                source=self.source,
                source_object_id=f"{keyword}-{index}",
                canonical_label=keyword,
                aliases=[keyword],
                original_format="png",
                license=LicenseMetadata(name="MIT", url="https://opensource.org/license/mit"),
            )
            for index in range(min(limit, self.count))
        ]

    def fetch(self, candidate: SourceCandidate) -> bytes:
        return _png()


def _record(source: str, source_id: str, root: Path, *, eligible: bool = True) -> ObjectRecord:
    path = root / f"{source_id}.png"
    path.write_bytes(_png())
    return ObjectRecord(
        object_id=deterministic_object_id(source, source_id),
        source=source,
        source_object_id=source_id,
        canonical_label="phone",
        semantic_tags=["phone", "message"],
        emotional_tags=["waiting"],
        categories=["communication"],
        usage_contexts=["bedroom"],
        style_family="rounded",
        license=LicenseMetadata(name="test"),
        original_format="png",
        local_raw_path=str(path),
        local_processed_path=str(path),
        preview_path=str(path),
        width=100,
        height=80,
        aspect_ratio=1.25,
        quality_status="approved" if eligible else "review",
        production_eligible=eligible,
        processing_status="processed",
    )


def test_deterministic_ids_are_stable_and_source_scoped():
    assert deterministic_object_id("iconify", "mdi:phone") == deterministic_object_id(
        "iconify", "mdi:phone"
    )
    assert deterministic_object_id("iconify", "mdi:phone") != deterministic_object_id(
        "noun_project", "mdi:phone"
    )


def test_iconify_adapter_normalizes_search_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["query"] == "phone"
        return httpx.Response(
            200,
            json={
                "icons": ["mdi:phone-outline"],
                "collections": {
                    "mdi": {
                        "name": "Material Design Icons",
                        "license": {"title": "Apache 2.0", "url": "https://example/license"},
                    }
                },
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    candidate = IconifyAdapter(client=client).search("phone", 1)[0]
    assert candidate.source == "iconify"
    assert candidate.source_object_id == "mdi:phone-outline"
    assert candidate.canonical_label == "phone outline"
    assert candidate.license.name == "Apache 2.0"


def test_noun_project_adapter_normalizes_and_requires_credentials():
    missing = NounProjectAdapter(key="", secret="", client=httpx.Client())
    try:
        missing.search("letter", 1)
        raise AssertionError("missing credentials were accepted")
    except RuntimeError as exc:
        assert "NOUN_PROJECT_KEY" in str(exc)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"].startswith("OAuth ")
        return httpx.Response(
            200,
            json={
                "icons": [
                    {
                        "id": 42,
                        "term": "paper letter",
                        "icon_url": "https://api.test/icon/42.svg",
                        "attribution": "Letter by Example",
                    }
                ]
            },
        )

    adapter = NounProjectAdapter(
        key="key",
        secret="secret",
        base_url="https://api.test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    candidate = adapter.search("letter", 1)[0]
    assert candidate.source_object_id == "42"
    assert candidate.license.attribution_required is True


def test_source_preference_fills_from_iconify_before_noun(tmp_path):
    service = ObjectIngestionService(
        ObjectStore(tmp_path), [FakeAdapter("iconify", 2), FakeAdapter("noun_project", 3)]
    )
    candidates = service.search("phone", 3)
    assert [item.source for item in candidates] == ["iconify", "iconify", "noun_project"]


def test_png_normalization_trims_padding_and_rejects_opaque_for_production(tmp_path):
    processed, preview, metadata = normalize_png(_png())
    with Image.open(io.BytesIO(processed)) as image:
        assert image.mode == "RGBA"
        assert max(image.size) <= 1024
        assert image.getchannel("A").getextrema()[0] == 0
    with Image.open(io.BytesIO(preview)) as image:
        assert max(image.size) <= 256
    assert metadata["warnings"] == []

    store = ObjectStore(tmp_path / "store")
    store.initialize()
    record = _record("local", "opaque", tmp_path)
    (tmp_path / "opaque.png").write_bytes(_png(opaque=True))
    record.local_raw_path = str(tmp_path / "opaque.png")
    record.local_processed_path = ""
    record.processing_status = "pending"
    result = process_record(record, store)
    assert result.quality_status == "review"
    assert result.production_eligible is False


def test_svg_qc_rejects_script_and_accepts_clean_vector():
    clean = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 12"><path fill="currentColor" d="M0 0h24v12H0z"/></svg>'
    normalized, metadata = normalize_svg(clean)
    assert b'viewBox="0 0 24 12"' in normalized
    assert b"#F0E6D8" in normalized
    assert metadata["width"] == 24
    try:
        normalize_svg(b'<svg viewBox="0 0 10 10"><script>alert(1)</script></svg>')
        raise AssertionError("unsafe SVG was accepted")
    except ValueError as exc:
        assert "unsafe SVG" in str(exc)


def test_registry_manifest_filters_and_iconify_ranking(tmp_path):
    store = ObjectStore(tmp_path / "library")
    store.initialize()
    noun = _record("noun_project", "42", tmp_path)
    iconify = _record("iconify", "mdi:phone", tmp_path)
    rejected = _record("iconify", "broken", tmp_path, eligible=False)
    for record in (noun, iconify, rejected):
        store.save_record(record)
    manifest = build_registry(store)
    assert manifest["object_count"] == 3
    assert (store.root / "manifests" / "semantic_index.json").is_file()
    payload = json.loads((store.root / "manifests" / "iconify.json").read_text(encoding="utf-8"))
    assert payload["object_count"] == 2
    results = ObjectRegistry.from_root(store.root).search(
        "phone", moods=["waiting"], contexts=["bedroom"]
    )
    assert [item.object.source for item in results] == ["iconify", "noun_project"]
    assert all(item.object.production_eligible for item in results)


def test_offline_ingest_process_registry_lookup_end_to_end(tmp_path):
    store = ObjectStore(tmp_path / "library")
    service = ObjectIngestionService(store, [FakeAdapter("iconify", 1)])
    records = service.ingest_keyword("coffee", count=1, sources=["iconify"])
    assert records[0].processing_status == "processed"
    assert records[0].production_eligible is True
    assert Path(records[0].local_raw_path).is_file()
    assert Path(records[0].local_processed_path).is_file()
    result = ObjectRegistry.from_root(store.root).search(
        "coffee", moods=["waiting"], contexts=["cafe"]
    )[0]
    assert "cafe_prop" in result.object.categories
    assert result.score > 0


def test_ingestion_reuses_search_and_asset_cache(tmp_path):
    store = ObjectStore(tmp_path / "library")
    service = ObjectIngestionService(store, [FakeAdapter("iconify", 1)])
    service.ingest_keyword("phone", count=1, sources=["iconify"])
    assert service.cache_stats == {
        "search_hits": 0,
        "search_misses": 1,
        "asset_hits": 0,
        "asset_misses": 1,
    }
    second = ObjectIngestionService(store, [FakeAdapter("iconify", 1)])
    second.ingest_keyword("phone", count=1, sources=["iconify"])
    assert second.cache_stats == {
        "search_hits": 1,
        "search_misses": 0,
        "asset_hits": 1,
        "asset_misses": 0,
    }
