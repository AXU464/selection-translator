"""Conservative local alignment of complete translation units.

Containment alone cannot align arbitrary bilingual substrings. Reuse only whole
paragraphs, list items or sentences whose ordered structure agrees on both sides.
Never trim translated words by a source character/word-count ratio.
"""
from collections import Counter
from dataclasses import dataclass
import re

from .document import _PROTECTED

MAX_TEXT = 200_000
_WORDS = re.compile(r'[A-Za-z]+|\d+(?:\.\d+)?')
_LIST = re.compile(r'^\s*(?:([-*+])\s+|(\d+)[.)]\s+)')
_HEADING = re.compile(r'^\s*(#{1,6})\s+')
_ABBREVIATION = re.compile(r'\b(?:Mr|Mrs|Ms|Dr|Prof|Fig|Eq|Sec|St|vs|etc|al|e\.g|i\.e)\.$', re.I)


def normalize(text):
    # Do not case-fold or apply NFKC: either could change identifiers or math.
    # Whitespace inside authored code/TeX stays significant, especially Python
    # indentation. Only prose whitespace may vary between selections.
    pieces = []
    cursor = 0
    for match in _PROTECTED.finditer(text):
        pieces.extend((re.sub(r'\s+', ' ', text[cursor:match.start()]), match.group()))
        cursor = match.end()
    pieces.append(re.sub(r'\s+', ' ', text[cursor:]))
    return ''.join(pieces).strip()


@dataclass(frozen=True)
class Unit:
    start: int
    end: int
    text: str
    kind: str = 'prose'


def _unit(text, start, end, kind='prose'):
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return Unit(start, end, text[start:end], kind)


def paragraphs(text):
    protected = [(m.start(), m.end()) for m in _PROTECTED.finditer(text)]
    cuts = [(0, 0)] + [(m.start(), m.end()) for m in re.finditer(r'\n\s*\n', text)
                       if not any(a <= m.start() < b for a, b in protected)] + [(len(text), len(text))]
    result = []
    for (_, start), (end, _) in zip(cuts, cuts[1:]):
        part = _unit(text, start, end)
        if not part.text:
            continue
        lines = part.text.splitlines(keepends=True)
        if len(lines) > 1 and all(_LIST.match(line) for line in lines if line.strip()):
            offset = part.start
            for line in lines:
                item = _unit(text, offset, offset + len(line), 'list')
                if item.text:
                    result.append(item)
                offset += len(line)
        else:
            heading = _HEADING.match(part.text)
            kind = 'heading' + str(len(heading[1])) if heading else 'list' if _LIST.match(part.text) else 'prose'
            # Tables, code blocks and environments require richer alignment.
            if re.search(r'^\s*(?:```|~~~|\||\\begin\{|\$\$|\\\[)', part.text, re.M):
                kind = 'opaque'
            result.append(Unit(part.start, part.end, part.text, kind))
    return result


def sentences(text):
    masked = list(text)
    for match in _PROTECTED.finditer(text):
        masked[match.start():match.end()] = ' ' * (match.end() - match.start())
    masked = ''.join(masked)
    # PDF ellipses in sets often arrive as ". . ." and are not sentence ends.
    masked = re.sub(r'\.(?:\s*\.){1,}', lambda m:' '*len(m.group()), masked)
    result = []
    start = 0
    # Chinese punctuation is a boundary even before a Latin name (。PS-TAHES).
    endings = r'[。！？]+["”’\)\]]*|[.!?]+["”’\)\]]*(?=\s|$|[A-Z\u3400-\u9fff]|\d{1,2}\s+[A-Z])'
    for match in re.finditer(endings, masked):
        end = match.end()
        if match.group().startswith('.'):
            if _ABBREVIATION.search(masked[:end]) or re.search(r'(?<![\w-])[A-Z]\.$', masked[:end]):
                continue
        result.append(_unit(text, start, end))
        start = end
    remainder = _unit(text, start, len(text))
    if remainder.text:
        result.append(remainder)
    return result


def _anchors(text):
    # PDF indices and their reconstructed TeX use different letter counts.
    # Compare ordinary preserved terms, not single letters from commands/indices.
    prose = _PROTECTED.sub(' ', text)
    words = Counter(re.findall(r'(?<![A-Za-z])[A-Za-z]{3,}(?![A-Za-z])', prose))
    literals = Counter('literal:'+normalize(m.group()) for m in _PROTECTED.finditer(text))
    return words + literals


def _aligned(source, target):
    if not source or len(source) != len(target) or len(source) > 120:
        return False
    if any(a.kind != b.kind or a.kind == 'opaque' for a, b in zip(source, target)):
        return False
    # Preserved terms, references and numeric anchors must stay in the same unit.
    # This rejects detectable reordering even when paragraph/sentence counts agree.
    left = _anchors(' '.join(x.text for x in source))
    right = _anchors(' '.join(x.text for x in target))
    shared = set(left) & set(right)
    for a, b in zip(source, target):
        aa, bb = _anchors(a.text), _anchors(b.text)
        if any(aa[key] != bb[key] for key in shared):
            return False
        if Counter(re.findall(r'\d+(?:\.\d+)?', a.text)) != Counter(re.findall(r'\d+(?:\.\d+)?', b.text)):
            return False
    return True


def _extract(query, source, target, target_text):
    if not _aligned(source, target):
        return None
    matches = []
    for first in range(len(source)):
        for last in range(first, len(source)):
            candidate = normalize(' '.join(x.text for x in source[first:last + 1]))
            if len(candidate) > len(query):
                break
            if candidate == query:
                matches.append(target_text[target[first].start:target[last].end].strip())
    # Repeated source units with different translations are ambiguous.
    return matches[0] if matches and len(set(matches)) == 1 else None


def extract(source, translation, selection):
    if not source or not translation or max(len(source), len(translation), len(selection)) > MAX_TEXT:
        return None
    source = source.replace('\r\n', '\n').replace('\r', '\n')
    translation = translation.replace('\r\n', '\n').replace('\r', '\n')
    query, full = normalize(selection), normalize(source)
    if not query or query not in full or source.strip() == selection.strip():
        return None
    if query == full:
        return translation.strip()
    aa, bb = paragraphs(source), paragraphs(translation)
    result = _extract(query, aa, bb, translation)
    if result:
        return result
    if not _aligned(aa, bb):
        return None
    # Descend only within already aligned paragraphs. Mid-sentence selections
    # and sentence merges/splits deliberately fall through to normal translation.
    matches = []
    for a, b in zip(aa, bb):
        if a.kind not in ('prose', 'list') or query not in normalize(a.text):
            continue
        sa, sb = sentences(a.text), sentences(b.text)
        if len(sa) < 2:
            continue
        value = _extract(query, sa, sb, b.text)
        if value:
            matches.append(value)
    return matches[0] if matches and len(set(matches)) == 1 else None


def find_reuse(selection, candidates, cancel=None):
    best = None
    for row in candidates:
        if cancel is not None and cancel.is_set():
            return None
        source, translation = row.get('source', ''), row.get('text', '')
        value = extract(source, translation, selection)
        if not value:
            continue
        # Prefer the smallest covering context. Within it, the provider supplies
        # newest history before cache so user-corrected history takes precedence.
        rank = len(normalize(source))
        if best is None or rank < best[0]:
            best = (rank, value, row.get('model', ''))
    return {'text':best[1], 'model':best[2]} if best else None
