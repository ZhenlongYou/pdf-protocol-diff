"""Narrow presentation equivalence for the content-focused reader report.

Keep source text intact. These predicates remove display-only differences,
never infer paraphrases or compare bags of words across different conditions.
"""
from __future__ import annotations

import re

_LITERAL_CONTEXT = re.compile(
    r"(?i)\b(?:symbol|units?|id|identifier|register|state|mode|enum|pin|signal|"
    r"regex|regexp|pattern|path|address|opcode|code|literal|variable)\b|"
    r"[\"`]|\b\w+_\w+\b"
)
_WORD = re.compile(r"[A-Za-z]+|[^A-Za-z\s]+")
_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}(?![\w-])")


def without_email_addresses(value: str) -> str:
    """User explicitly excludes email addresses in every document region.

    Remove only the address span; the surrounding requirement remains intact.
    """
    return _EMAIL.sub('', value)


def neutral_email_text(value: str) -> str:
    """Keep sentence structure while making excluded addresses unhighlightable."""
    if _EMAIL.search(value) is None:
        return value
    remainder = without_email_addresses(value).strip()
    if not remainder or re.fullmatch(r"(?i)(?:e-?mail|邮箱|电子邮件)\s*[:：]?", remainder):
        return ''
    return _EMAIL.sub('[邮箱]', value)


def cosmetic_content_equal(old: str, new: str, *, cell_wrap: bool = False, context: str = '') -> bool:
    """Compare cosmetic text without changing technical-token spelling.

    Whitespace width is irrelevant. A cell soft wrap is ignored only between
    alphabetic words, not between numbers/list entries. Uppercase emphasis is
    ignored for ordinary-shaped long words;
    short acronyms, camel-case units, literals and named technical fields retain
    case. This is spelling normalization, not semantic paraphrase matching.
    """
    def compact(value: str) -> str:
        value = without_email_addresses(value)
        if cell_wrap:
            value = re.sub(r"(?<=[A-Za-z])\s*(?:↵|\n)\s*(?=[A-Za-z])", " ", value)
        # Preserve unproven cell boundaries instead of flattening numeric lists.
        if cell_wrap:
            value = value.replace('\n', ' ↵ ')
        return ' '.join(value.split())

    left, right = compact(old), compact(new)
    if left == right:
        return True
    if left.casefold() != right.casefold() or _LITERAL_CONTEXT.search(context + ' ' + left + ' ' + right):
        return False
    old_tokens, new_tokens = list(_WORD.finditer(left)), list(_WORD.finditer(right))
    if len(old_tokens) != len(new_tokens):
        return False
    changed = [i for i, (a, b) in enumerate(zip(old_tokens, new_tokens)) if a[0] != b[0]]
    if not changed:
        return False
    for i in changed:
        a, b = old_tokens[i][0], new_tokens[i][0]
        if not (a.isalpha() and len(a) >= 4 and len(b) >= 4):
            return False
        if not (a.isupper() or a.islower() or a.istitle()) or not (b.isupper() or b.islower() or b.istitle()):
            return False
    return True
