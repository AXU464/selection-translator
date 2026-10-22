"""Conservative preparation of clipboard text, including PDF selections.

Clipboard text has no glyph coordinates. Never infer an exponent, fraction or
missing operator here; keep line order so the translator can see the damage.
The original selection is kept separately by the application.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Protect code and authored TeX before repairing typographical characters. In
# particular, NFKC would flatten Unicode superscripts and change mathematics.
_PROTECTED = re.compile(
    r"(?P<fence>`{3,}|~{3,})[^\n]*\n[\s\S]*?(?P=fence)"
    r"|(?P<ticks>`+)[^\n]*?(?P=ticks)"
    r"|\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)"
    r"|(?<![\\\w$])\$(?!\s)[^$\n]+?(?<!\s)\$(?![\w$])"
    # List bodies are prose. Protect their individual commands and mathematics,
    # while still normalizing and checking the translated item text.
    r"|\\begin\{(?P<env>(?!(?:itemize|enumerate|description)\})[^}]+)\}[\s\S]*?\\end\{(?P=env)\}"
    r"|\\[A-Za-z]+(?:\{[^{}]*\})*"
)
_LIGATURES = str.maketrans({"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi",
                           "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"})
_MATH = re.compile(r"[\u0370-\u03ff\u2070-\u209f\u2102-\u214f\u2190-\u22ff\U0001d400-\U0001d7ff²³¹ˆ]")
_MISSING = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\ufffd\ue000-\uf8ff\U000f0000-\U000ffffd\U00100000-\U0010fffd]")
_STRUCTURAL = re.compile(r"^\s*(?:[#>|`~]|[-*+]\s|\d+[.)]\s|\\)")


@dataclass(frozen=True)
class DocumentInput:
    text: str
    pdf_like: bool = False
    math_layout_risk: bool = False
    missing_glyphs: bool = False

    def ui_info(self) -> dict:
        return {"pdfLike": self.pdf_like, "mathLayoutRisk": self.math_layout_risk,
                "missingGlyphs": self.missing_glyphs}


def prepare_document(source: str) -> DocumentInput:
    """Repair only reversible typography outside code/TeX; diagnose layout loss."""
    source = source or ""
    pieces: list[str] = []
    plain: list[str] = []
    cursor = 0

    def prose(text: str) -> str:
        plain.append(text)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = text.translate(_LIGATURES)
        # A soft hyphen is an explicit discretionary break; an ordinary hyphen
        # might be a real compound word or a minus sign and must be retained.
        text = re.sub(r"(?<=[A-Za-z])\u00ad\n(?=[a-z])", "", text)
        text = text.replace("\u00ad", "")
        # Make a failed PDF font mapping visible instead of silently omitting it.
        return _MISSING.sub("�", text)

    for match in _PROTECTED.finditer(source):
        pieces.extend((prose(source[cursor:match.start()]), match.group()))
        plain.append("\n\n")  # never join evidence across protected regions
        cursor = match.end()
    pieces.append(prose(source[cursor:]))
    evidence = "".join(plain).replace("\r\n", "\n").replace("\r", "\n")
    lines = evidence.splitlines()
    wrapped = sum(
        len(a.strip()) >= 30 and len(b.strip()) >= 20
        and not _STRUCTURAL.match(a) and not _STRUCTURAL.match(b)
        for a, b in zip(lines, lines[1:])
    ) >= 2
    math = bool(_MATH.search(evidence))
    fragments = any(
        0 < len(a.strip()) <= 24 and 0 < len(b.strip()) <= 24
        and not re.search(r"[A-Za-z]{4,}", a + b)
        and bool(_MATH.search(a + b) or re.search(r"[=^_]", a + b))
        for a, b in zip(lines, lines[1:])
    )
    missing = bool(_MISSING.search(evidence))
    pdf_like = wrapped or fragments or missing or bool(re.search(r"[ﬀ-ﬆ\u00ad]", evidence))
    risk = missing or fragments or (wrapped and math)
    return DocumentInput("".join(pieces), pdf_like, risk, missing)
