"""Source adapter contract."""

from __future__ import annotations

from typing import Protocol

from tella.object_library.models import SourceCandidate


class ObjectSourceAdapter(Protocol):
    source: str

    def search(self, keyword: str, limit: int = 32) -> list[SourceCandidate]: ...
    def fetch(self, candidate: SourceCandidate) -> bytes: ...
