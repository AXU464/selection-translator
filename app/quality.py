"""Conservative checks for untranslated ordinary English in Chinese prose.

Only flag common prose vocabulary, never rewrite mathematical content or trigger
an automatic paid request. Proper names and unfamiliar technical terms are left
alone because Latin text is often intentional in scientific translations.
"""
import re

from .chinese import _LITERAL
from .document import _PROTECTED

_PROSE_WORDS = set('''
interchangeable interchangeably respectively otherwise therefore however whereas
although furthermore moreover nevertheless subsequently specifically similarly
approximately particularly corresponding previously aforementioned following
consequently sufficiently essentially inherently theoretically experimentally
simultaneously independently respectively according including excluding
without within between through before after instead whether unless
compute computes computed computing calculate calculates calculated calculation
consider considers considered considering assume assumes assumed assuming
provide provides provided providing preserve preserves preserved preserving
achieve achieves achieved achieving determine determines determined determining
obtain obtains obtained obtaining indicate indicates indicated indicating
denote denotes denoted denoting represent represents represented representing
guarantee guarantees guaranteed guaranteeing satisfy satisfies satisfied satisfying
because example probability respectively original respectively
'''.split())


def untranslated_words(source, translation, target):
    if target != 'zh' or not re.search(r'[\u3400-\u9fff]', translation):
        return []
    masked = _PROTECTED.sub(' ', translation)
    masked = _LITERAL.sub(' ', masked)
    words = []
    for word in re.findall(r'\b[A-Za-z]+\b', masked):
        if word.islower() and word in _PROSE_WORDS and word not in words:
            words.append(word)
    return words[:8]


def retry_review(source, translation, target):
    words = untranslated_words(source, translation, target)
    if not words:
        return ''
    return ('The previous Chinese prose still contains these ordinary English words: '
            + ', '.join(words) + '. Translate them in context. Keep proper names, code, '
            'identifiers, URLs and mathematical notation unchanged. Return the full translation.')
