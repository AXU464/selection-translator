"""Normalize Chinese translation prose with the Windows character mapper."""

import ctypes
from functools import lru_cache
import logging
import os
import re

from .document import _PROTECTED

log = logging.getLogger(__name__)
_HAN = re.compile(r'[\u3400-\u9fff\uf900-\ufaff]')
# Literal destinations, URLs and filenames stay byte-for-byte unchanged.
_LITERAL = re.compile(
    r'(?<=\]\()[^\n)]*(?=\))'
    r'|(?:https?://|ftp://|www\.)[^\s<>"\[\]]+'
    r'|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}'
    r'|(?:[A-Za-z]:[\\/]|\\\\)[^\s<>"|?*]+'
    r'|(?<![\w])[^\s<>"\[\](){}`$，。；：！？]+\.[A-Za-z0-9]{1,12}(?![\w])'
)


@lru_cache(maxsize=1)
def _mapper():
    if os.name != 'nt':
        return None
    try:
        function = ctypes.WinDLL('kernel32', use_last_error=True).LCMapStringEx
        function.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_wchar_p,
                             ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
                             ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t]
        function.restype = ctypes.c_int
        return function
    except (OSError, AttributeError):
        log.warning('Chinese character normalization unavailable; translation preserved')
        return None


def _simplify(text):
    if not _HAN.search(text):
        return text
    function = _mapper()
    if function is None:
        return text
    try:
        # Win32 lengths count UTF-16 code units, including surrogate pairs.
        count = len(text.encode('utf-16-le', errors='surrogatepass')) // 2
        needed = function('zh-CN', 0x02000000, text, count, None, 0, None, None, 0)
        if not needed:
            return text
        output = ctypes.create_unicode_buffer(needed)
        written = function('zh-CN', 0x02000000, text, count, output, needed, None, None, 0)
        if not written:
            return text
        return ctypes.string_at(output, written * 2).decode('utf-16-le', errors='surrogatepass')
    except (OSError, ValueError, UnicodeError, ctypes.ArgumentError, OverflowError):
        log.warning('Chinese character normalization failed; translation preserved')
        return text


def normalize_translation(text, target):
    """Simplify only prose in Chinese output, preserving code, TeX and literals."""
    if target != 'zh' or not _HAN.search(text):
        return text

    # Merge overlapping spans before conversion. A TeX-like backslash inside a
    # Windows path must not split that path into independently converted prose.
    spans = sorted((m.start(), m.end()) for pattern in (_PROTECTED, _LITERAL)
                   for m in pattern.finditer(text))
    protected = []
    for start, end in spans:
        if protected and start <= protected[-1][1]:
            protected[-1][1] = max(protected[-1][1], end)
        else:
            protected.append([start, end])
    pieces = []
    cursor = 0
    for start, end in protected:
        pieces.extend((_simplify(text[cursor:start]), text[start:end]))
        cursor = end
    pieces.append(_simplify(text[cursor:]))
    return ''.join(pieces)
