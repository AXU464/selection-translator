"""Local character screening for the app's Chinese/English translation rules.

Any Latin letter makes the selection English, including Chinese/English mixtures.
This is a deterministic bilingual rule, not a general-purpose language detector.
"""

from __future__ import annotations

import unicodedata

SAME_LANGUAGE_MESSAGE = "选中语言类型与目标一致"
LOCAL_MODEL_LABEL = "本地检测"


def source_language(text: str) -> str | None:
    """Return en for Latin/mixed text, zh for Han-only text, else unknown.

    Digits, punctuation and whitespace are neutral. Unicode names cover both
    simplified/traditional Han (including extensions) and full-width Latin.
    """
    has_chinese = False
    for char in text:
        name = unicodedata.name(char, "")
        if char.isalpha() and "LATIN" in name:
            return "en"
        if name.startswith(("CJK UNIFIED IDEOGRAPH-", "CJK COMPATIBILITY IDEOGRAPH-")):
            has_chinese = True
    return "zh" if has_chinese else None


def matches_target_language(text: str, target: str) -> bool:
    return source_language(text) == ("en" if target == "en" else "zh")
