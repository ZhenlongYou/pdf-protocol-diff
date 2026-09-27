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
    alphabetic words, not between numbers/list entries. Letter case is always
    retained because ordinary-looking words may be case-sensitive identifiers,
    enum values or command names. This is spelling normalization, not semantic
    paraphrase matching.
    """
    def compact(value: str) -> str:
        value = without_email_addresses(value)
        # A written negative exponent keeps its sign under hyphen/minus glyph variants.
        value = re.sub(r'(?<=\d)([eE])[‐‑−](?=\d)', r'\1-', value)
        # Quantity spacing carries no value or unit change. Keep unit spelling
        # and case, numeric signs, and line/list boundaries intact.
        if not re.search(r'["`]|https?://|\b(?:identifier|literal|regex|path)\b', context+' '+value, re.I):
            value = re.sub(r'(?<=\d)[ \t]+(?=(?:GHz|MHz|kHz|Hz|mV|mA|mW|dB|ns|ps|mm|cm|V|A|W|s|m)\b)', '', value)
        if not _LITERAL_CONTEXT.search(context+' '+value) and not re.search(r'https?://|www\.', value):
            value = re.sub(r'(?<=[A-Za-z])[‐‑](?=[A-Za-z])', '-', value)
        if cell_wrap:
            value = re.sub(r"(?<=[A-Za-z])\s*(?:↵|\n)\s*(?=[A-Za-z])", " ", value)
        # Preserve unproven cell boundaries instead of flattening numeric lists.
        if cell_wrap:
            value = value.replace('\n', ' ↵ ')
        return ' '.join(value.split())

    return compact(old) == compact(new)
