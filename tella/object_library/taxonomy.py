"""Small, composition-oriented taxonomy for minimalist emotional scenes."""

from __future__ import annotations

import re


GROUPS: dict[str, dict[str, list[str]]] = {
    "communication": {
        "terms": ["phone", "message", "letter", "mail", "chat"],
        "moods": ["waiting", "loneliness", "overthinking"],
        "contexts": ["bedroom", "cafe", "daily_solitude"],
    },
    "self_care": {
        "terms": ["tea", "cup", "tissue", "journal", "bath", "medicine"],
        "moods": ["healing", "quiet_comfort"],
        "contexts": ["bedroom", "home"],
    },
    "memory": {
        "terms": ["photo", "photograph", "letter", "keepsake", "calendar"],
        "moods": ["sadness", "reflection", "acceptance"],
        "contexts": ["bedroom", "daily_solitude"],
    },
    "comfort": {
        "terms": ["pillow", "blanket", "lamp", "candle", "mug", "book"],
        "moods": ["healing", "quiet_comfort", "loneliness"],
        "contexts": ["bedroom", "home", "cafe"],
    },
    "room_prop": {
        "terms": ["chair", "table", "window", "clock", "plant", "bed", "lamp"],
        "moods": ["daily_solitude", "waiting"],
        "contexts": ["bedroom", "home"],
    },
    "outdoor_prop": {
        "terms": ["tree", "flower", "bench", "rain", "leaf", "moon"],
        "moods": ["reflection", "healing", "acceptance"],
        "contexts": ["park", "outdoor"],
    },
    "cafe_prop": {
        "terms": ["coffee", "cup", "mug", "table", "menu"],
        "moods": ["waiting", "daily_solitude"],
        "contexts": ["cafe"],
    },
    "travel_waiting": {
        "terms": ["bus", "train", "ticket", "suitcase", "clock", "bench"],
        "moods": ["waiting", "loneliness", "reflection"],
        "contexts": ["station", "bus_stop", "travel"],
    },
    "emotional_symbol": {
        "terms": ["heart", "cloud", "rain", "seedling", "sunrise", "path", "star", "knot"],
        "moods": ["sadness", "overthinking", "healing", "acceptance"],
        "contexts": ["symbolic", "minimal"],
    },
}

SYNONYMS = {
    "cellphone": ["phone", "message"],
    "mobile": ["phone"],
    "sofa": ["couch", "comfort"],
    "coffee": ["cup", "mug", "cafe"],
    "envelope": ["letter", "mail", "memory"],
    "sprout": ["seedling", "growth", "healing"],
    "watch": ["clock", "waiting", "time"],
}


def tokens(value: str) -> set[str]:
    return {item for item in re.split(r"[^a-z0-9]+", value.lower()) if item}


def enrich(label: str, aliases: list[str] | None = None) -> dict[str, list[str]]:
    words = tokens(" ".join([label, *(aliases or [])]))
    semantic = set(words)
    for word in list(words):
        semantic.update(SYNONYMS.get(word, []))
    categories, moods, contexts = set(), set(), set()
    for group, definition in GROUPS.items():
        if semantic.intersection(definition["terms"]):
            categories.add(group)
            moods.update(definition["moods"])
            contexts.update(definition["contexts"])
    return {
        "semantic_tags": sorted(semantic),
        "emotional_tags": sorted(moods),
        "categories": sorted(categories),
        "usage_contexts": sorted(contexts),
    }
