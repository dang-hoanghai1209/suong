"""Shared records for provider-neutral semantic objects."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


ObjectSource = Literal["iconify", "noun_project", "local"]


def deterministic_object_id(source: str, source_object_id: str) -> str:
    """Return a stable, readable ID without trusting provider path characters."""
    slug = re.sub(r"[^a-z0-9]+", "-", source_object_id.lower()).strip("-")[:52]
    digest = hashlib.sha256(f"{source}:{source_object_id}".encode()).hexdigest()[:10]
    return f"obj_{source}_{slug or 'asset'}_{digest}"


class LicenseMetadata(BaseModel):
    name: str = "unknown"
    url: str = ""
    attribution_required: bool = False
    attribution_text: str = ""
    author: str = ""


class SourceCandidate(BaseModel):
    source: ObjectSource
    source_object_id: str
    canonical_label: str
    aliases: list[str] = Field(default_factory=list)
    download_url: str = ""
    original_format: str = "svg"
    width: int | None = None
    height: int | None = None
    style_family: str = "unknown"
    license: LicenseMetadata = Field(default_factory=LicenseMetadata)
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class ObjectRecord(BaseModel):
    schema_version: int = 1
    object_id: str
    source: ObjectSource
    source_object_id: str
    canonical_label: str
    aliases: list[str] = Field(default_factory=list)
    semantic_tags: list[str] = Field(default_factory=list)
    emotional_tags: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    usage_contexts: list[str] = Field(default_factory=list)
    style_family: str = "unknown"
    license: LicenseMetadata = Field(default_factory=LicenseMetadata)
    original_format: str
    local_raw_path: str
    local_processed_path: str = ""
    preview_path: str = ""
    width: int | None = None
    height: int | None = None
    aspect_ratio: float | None = None
    color_mode: Literal["monochrome", "multicolor", "unknown"] = "unknown"
    rendering_style: Literal["outline", "filled", "mixed", "unknown"] = "unknown"
    quality_status: Literal["pending", "approved", "review", "rejected", "failed"] = "pending"
    production_eligible: bool = False
    processing_status: Literal["pending", "processed", "failed"] = "pending"
    processing_warnings: list[str] = Field(default_factory=list)
    notes: str = ""
    ingested_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    content_sha256: str = ""


class SearchResult(BaseModel):
    object: ObjectRecord
    score: float
    reasons: list[str] = Field(default_factory=list)
