"""Credential-free Iconify API adapter."""

from __future__ import annotations

import re

import httpx

from tella.object_library.models import LicenseMetadata, SourceCandidate


class IconifyAdapter:
    source = "iconify"

    def __init__(
        self,
        base_url: str = "https://api.iconify.design",
        timeout: float = 20.0,
        client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=timeout, follow_redirects=True)

    def search(self, keyword: str, limit: int = 32) -> list[SourceCandidate]:
        response = self.client.get(
            f"{self.base_url}/search", params={"query": keyword, "limit": max(32, min(limit, 999))}
        )
        response.raise_for_status()
        payload = response.json()
        collections = payload.get("collections") or {}
        candidates = []
        for qualified in (payload.get("icons") or [])[:limit]:
            if ":" not in qualified:
                continue
            prefix, name = qualified.split(":", 1)
            info = collections.get(prefix) or {}
            license_info = info.get("license") or {}
            title = str(license_info.get("title") or license_info.get("spdx") or "unknown")
            candidates.append(
                SourceCandidate(
                    source="iconify",
                    source_object_id=qualified,
                    canonical_label=re.sub(r"[-_]", " ", name).strip(),
                    aliases=[keyword],
                    download_url=f"{self.base_url}/{prefix}/{name}.svg",
                    original_format="svg",
                    style_family=prefix,
                    license=LicenseMetadata(
                        name=title,
                        url=str(license_info.get("url") or ""),
                        attribution_required=title.lower()
                        not in {"mit", "apache 2.0", "public domain", "cc0"},
                        author=str(
                            info.get("author", {}).get("name", "")
                            if isinstance(info.get("author"), dict)
                            else info.get("author") or ""
                        ),
                    ),
                    raw_metadata={"collection": prefix, "collection_name": info.get("name", "")},
                )
            )
        return candidates

    def fetch(self, candidate: SourceCandidate) -> bytes:
        if candidate.source != self.source:
            raise ValueError(f"Iconify cannot fetch source {candidate.source!r}")
        response = self.client.get(candidate.download_url)
        response.raise_for_status()
        content = response.content
        if b"<svg" not in content[:500].lower():
            raise ValueError(f"Iconify returned non-SVG content for {candidate.source_object_id}")
        return content
