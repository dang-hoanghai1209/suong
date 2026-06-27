"""Best-effort language detection for a pasted/dropped story file.

Used only in paste-script mode, where we must pick a narration voice before
the planner runs and we never translate the text. Script detection is a
fast heuristic over the 8 languages Tella supports — it does not need to be
perfect, only good enough to pick the right TTS voice. Vietnamese, Chinese,
Japanese and Korean are detected reliably by script; the Latin-script
European languages fall back to marker characters / common words, else
English.
"""
from __future__ import annotations

import re

# Vietnamese-specific letters (beyond plain ASCII + the shared Latin accents).
_VI_CHARS = set("ăâđêôơưĂÂĐÊÔƠƯ"
                "áàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ"
                "ÁÀẢÃẠẤẦẨẪẬẮẰẲẴẶÉÈẺẼẸẾỀỂỄỆÍÌỈĨỊÓÒỎÕỌỐỒỔỖỘỚỜỞỠỢÚÙỦŨỤỨỪỬỮỰÝỲỶỸỴ")

_HIRAGANA = re.compile(r"[぀-ゟ]")
_KATAKANA = re.compile(r"[゠-ヿ]")
_HANGUL = re.compile(r"[가-힣]")
_CJK = re.compile(r"[一-鿿]")

_DE_WORDS = re.compile(r"\b(der|die|das|und|nicht|ein|eine|ich|sich|mit|auch)\b", re.I)
_FR_WORDS = re.compile(r"\b(le|la|les|une?|et|est|dans|pour|avec|qui|que|pas)\b", re.I)
_ES_WORDS = re.compile(r"\b(el|la|los|las|una?|y|de|que|con|para|pero|porque)\b", re.I)


def detect_language(text: str) -> str:
    """Return an ISO-639-1 code from Tella's supported set, defaulting to 'en'."""
    if not text:
        return "en"
    sample = text[:2000]

    # CJK / kana / hangul are unambiguous by script.
    if _HIRAGANA.search(sample) or _KATAKANA.search(sample):
        return "ja"
    if _HANGUL.search(sample):
        return "ko"
    if _CJK.search(sample):
        return "zh"

    # Vietnamese: any Vietnamese-specific glyph is a strong signal.
    if any(ch in _VI_CHARS for ch in sample):
        return "vi"

    # Latin-script European languages: marker chars first, then stop words.
    lowered = sample.lower()
    if "ß" in lowered or _DE_WORDS.search(lowered):
        de = len(_DE_WORDS.findall(lowered))
        fr = len(_FR_WORDS.findall(lowered))
        es = len(_ES_WORDS.findall(lowered))
        if de >= fr and de >= es:
            return "de"
    if "ñ" in lowered or "¿" in sample or "¡" in sample:
        return "es"

    fr = len(_FR_WORDS.findall(lowered))
    es = len(_ES_WORDS.findall(lowered))
    de = len(_DE_WORDS.findall(lowered))
    best = max(fr, es, de)
    if best >= 3:
        return {"fr": fr, "es": es, "de": de}.get(
            max(("fr", "es", "de"), key=lambda k: {"fr": fr, "es": es, "de": de}[k]),
            "en",
        )

    return "en"


__all__ = ["detect_language"]
