"""Noun Project adapter with OAuth 1.0 request signing and tolerant v1/v2 parsing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from urllib.parse import parse_qsl, quote, urlparse

import httpx

from tella.object_library.models import LicenseMetadata, SourceCandidate


def _escape(value: object) -> str:
    return quote(str(value), safe="~-._")


class NounProjectAdapter:
    source = "noun_project"

    def __init__(
        self,
        key: str | None = None,
        secret: str | None = None,
        base_url: str | None = None,
        timeout: float = 20.0,
        client: httpx.Client | None = None,
    ):
        self.key = key or os.environ.get("NOUN_PROJECT_KEY", "")
        self.secret = secret or os.environ.get("NOUN_PROJECT_SECRET", "")
        self.base_url = (
            base_url or os.environ.get("NOUN_PROJECT_API_URL") or "https://api.thenounproject.com"
        ).rstrip("/")
        self.client = client or httpx.Client(timeout=timeout, follow_redirects=True)

    def _auth(self, method: str, url: str, params: dict[str, object] | None = None) -> str:
        if not self.key or not self.secret:
            raise RuntimeError("Noun Project requires NOUN_PROJECT_KEY and NOUN_PROJECT_SECRET")
        oauth = {
            "oauth_consumer_key": self.key,
            "oauth_nonce": secrets.token_hex(16),
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": str(int(time.time())),
            "oauth_version": "1.0",
        }
        parsed = urlparse(url)
        all_params = [
            *parse_qsl(parsed.query),
            *((str(k), str(v)) for k, v in (params or {}).items()),
            *oauth.items(),
        ]
        normalized = "&".join(f"{_escape(k)}={_escape(v)}" for k, v in sorted(all_params))
        base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        base = "&".join([method.upper(), _escape(base_url), _escape(normalized)])
        signature = base64.b64encode(
            hmac.new(f"{_escape(self.secret)}&".encode(), base.encode(), hashlib.sha1).digest()
        ).decode()
        oauth["oauth_signature"] = signature
        return "OAuth " + ", ".join(
            f'{_escape(k)}="{_escape(v)}"' for k, v in sorted(oauth.items())
        )

    def _get(self, url: str, params: dict[str, object] | None = None) -> httpx.Response:
        response = self.client.get(
            url, params=params, headers={"Authorization": self._auth("GET", url, params)}
        )
        response.raise_for_status()
        return response

    def search(self, keyword: str, limit: int = 32) -> list[SourceCandidate]:
        # NOUN_PROJECT_SEARCH_PATH allows API version upgrades without changing callers.
        template = os.environ.get("NOUN_PROJECT_SEARCH_PATH", "/v2/icon")
        url = self.base_url + template.format(query=quote(keyword, safe=""))
        params: dict[str, object] = {"limit": limit, "include_svg": 1, "thumbnail_size": 200}
        if "{query}" not in template:
            params["query"] = keyword
        payload = self._get(url, params).json()
        items = payload.get("icons") or payload.get("results") or payload.get("data") or []
        if isinstance(items, dict):
            items = items.get("icons") or items.get("results") or []
        candidates = []
        for wrapper in items[:limit]:
            item = wrapper.get("icon", wrapper) if isinstance(wrapper, dict) else {}
            source_id = str(item.get("id") or item.get("icon_id") or "")
            if not source_id:
                continue
            label = str(item.get("term") or item.get("title") or item.get("name") or keyword)
            creator = item.get("creator") or {}
            author = (
                str(creator.get("name") or item.get("attribution") or "")
                if isinstance(creator, dict)
                else str(creator)
            )
            download_url = str(
                item.get("icon_url")
                or item.get("svg_url")
                or item.get("download_url")
                or item.get("preview_url")
                or item.get("thumbnail_url")
                or ""
            )
            tags = item.get("tags") or []
            styles = item.get("styles") or []
            style = styles[0].get("style", "") if styles and isinstance(styles[0], dict) else ""
            candidates.append(
                SourceCandidate(
                    source="noun_project",
                    source_object_id=source_id,
                    canonical_label=label,
                    aliases=[keyword, *(str(tag) for tag in tags)],
                    download_url=download_url,
                    original_format="svg" if "svg" in download_url.lower() else "png",
                    width=item.get("width"),
                    height=item.get("height"),
                    style_family=str(style or item.get("style") or "unknown"),
                    license=LicenseMetadata(
                        name=str(
                            item.get("license_description")
                            or item.get("license")
                            or "Noun Project API license"
                        ),
                        attribution_required=bool(item.get("attribution") or author),
                        attribution_text=str(item.get("attribution") or ""),
                        author=author,
                    ),
                    raw_metadata={
                        "permalink": item.get("permalink", ""),
                        "term_id": item.get("term_id"),
                    },
                )
            )
        return candidates

    def fetch(self, candidate: SourceCandidate) -> bytes:
        if not candidate.download_url:
            raise ValueError(
                f"Noun Project candidate {candidate.source_object_id} has no downloadable URL"
            )
        parsed = urlparse(candidate.download_url)
        if parsed.netloc and parsed.netloc != urlparse(self.base_url).netloc:
            response = self.client.get(candidate.download_url)
            response.raise_for_status()
        else:
            response = self._get(candidate.download_url)
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            payload = response.json()
            encoded = payload.get("base64_encoded_file") or payload.get("data")
            if encoded:
                return base64.b64decode(encoded)
            nested_url = payload.get("download_url") or payload.get("icon_url")
            if nested_url:
                return self._get(str(nested_url)).content
            raise ValueError("Noun Project download response contained no asset")
        return response.content
