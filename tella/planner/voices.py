"""Multi-language Edge TTS voice resolution.

Tella supports 8 target languages. Each language gets one male + one
female Edge TTS voice. The Google Cloud TTS adapter (Phase 4) wraps the
same locale mapping but picks Chirp 3 HD voices instead — see
``tella/tts/google.py``.

Pick a voice by ``(language, gender)``:

    >>> edge_voice_for("vi", "female")
    'vi-VN-HoaiMyNeural'
    >>> edge_voice_for("ja", "male")
    'ja-JP-KeitaNeural'
"""
from __future__ import annotations

# Edge TTS uses BCP-47 locale tags (e.g. "vi-VN") for voice names but
# Tella keeps its public surface on ISO-639-1 ("vi") — one mapping here.
_EDGE_LOCALE: dict[str, str] = {
    "vi": "vi-VN",
    "en": "en-US",
    "ja": "ja-JP",
    "ko": "ko-KR",
    "zh": "zh-CN",
    "de": "de-DE",
    "fr": "fr-FR",
    "es": "es-ES",
}

# (language, gender) → Edge voice name. Voices picked for clarity + warmth;
# avoiding the youngest-sounding options which over-emote on parable content.
_EDGE_VOICE_BY_GENDER: dict[str, dict[str, str]] = {
    "vi": {"male": "vi-VN-NamMinhNeural",   "female": "vi-VN-HoaiMyNeural"},
    "en": {"male": "en-US-GuyNeural",       "female": "en-US-JennyNeural"},
    "ja": {"male": "ja-JP-KeitaNeural",     "female": "ja-JP-NanamiNeural"},
    "ko": {"male": "ko-KR-InJoonNeural",    "female": "ko-KR-SunHiNeural"},
    "zh": {"male": "zh-CN-YunxiNeural",     "female": "zh-CN-XiaoxiaoNeural"},
    "de": {"male": "de-DE-ConradNeural",    "female": "de-DE-KatjaNeural"},
    "fr": {"male": "fr-FR-HenriNeural",     "female": "fr-FR-DeniseNeural"},
    "es": {"male": "es-ES-AlvaroNeural",    "female": "es-ES-ElviraNeural"},
}


def locale_for(language: str) -> str:
    """Return the BCP-47 locale tag for an ISO-639-1 ``language`` code."""
    return _EDGE_LOCALE.get(language.lower(), "en-US")


def edge_voice_for(language: str, gender: str) -> str:
    """Pick an Edge TTS voice name for ``(language, gender)``.

    Unknown language → English fallback.
    Unknown gender   → male voice.
    """
    by_lang = _EDGE_VOICE_BY_GENDER.get(language.lower(), _EDGE_VOICE_BY_GENDER["en"])
    return by_lang.get(gender.lower(), by_lang["male"])


__all__ = [
    "edge_voice_for",
    "locale_for",
]
