"""Text normalization utilities shared by sectioning and diffing.

PDF extraction often inserts arbitrary line breaks and spacing. These helpers
apply conservative cleanup only: they avoid aggressive rewriting so the final
report still resembles the source protocol text a reviewer will see on screen.
"""

from __future__ import annotations

import re
import unicodedata

_WHITESPACE_RE = re.compile(r"[ \t\u00a0]+")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")
_NUMBER_WORD_UNITS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_NUMBER_WORD_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}


def normalize_line(line: str) -> str:
    """Normalize one extracted line without destroying protocol numbering."""

    normalized = unicodedata.normalize("NFKC", line)
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    return normalized.strip()


def normalize_for_similarity(text: str) -> str:
    """Normalize text for similarity scoring.

    This function deliberately keeps punctuation and digits because protocol
    deltas often hide in numbers, dates, voltage/current limits, or option names.
    """

    lines = [normalize_line(line) for line in text.splitlines()]
    compact = "\n".join(line for line in lines if line)
    compact = _MULTI_BLANK_RE.sub("\n\n", compact)
    return compact.casefold()


def compact_inline(text: str) -> str:
    """Make a short single-line snippet for reports and CSV fields."""

    return _WHITESPACE_RE.sub(" ", " ".join(text.split())).strip()


def truncate(text: str, max_chars: int = 260) -> str:
    """Trim a report snippet while preserving the most useful prefix."""

    value = compact_inline(text)
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "…"


def canonicalize_number_word_token(token: str) -> str | None:
    """Return the digit string for one compact English cardinal number token.

    This intentionally covers only compact protocol-review cases such as
    ``seven``/``7`` or ``twenty-one``/``21``. Context-sensitive callers should
    use ``canonicalize_number_word_tokens`` so identifiers like ``Gen seven``
    or ``report seven.pdf`` can stay protected.
    """

    normalized = token.casefold().replace("-", "").replace("‐", "").replace("‑", "")
    value = _compact_number_word_value(normalized)
    return str(value) if value is not None else None


def parse_number_word_phrase(tokens: list[str], start_index: int) -> tuple[str, int] | None:
    """Parse a short English cardinal phrase from a token stream.

    Returns ``(canonical_digit_string, consumed_token_count)``. The parser is
    deliberately small: it covers common protocol counts from zero through 999,
    including ``twenty one`` and ``one hundred and five``, while ignoring
    ordinals and domain-specific identifiers.
    """

    if start_index >= len(tokens):
        return None
    normalized = [_normalize_number_word_token(token) for token in tokens]
    first = normalized[start_index]

    if first in _NUMBER_WORD_UNITS and _NUMBER_WORD_UNITS[first] > 0:
        next_index = start_index + 1
        if next_index < len(normalized) and normalized[next_index] == "hundred":
            value = _NUMBER_WORD_UNITS[first] * 100
            consumed = 2
            tail_index = start_index + consumed
            if tail_index < len(normalized) and normalized[tail_index] == "and":
                tail_index += 1
                consumed += 1
            tail = _parse_under_hundred(normalized, tail_index)
            if tail:
                tail_value, tail_consumed = tail
                value += tail_value
                consumed += tail_consumed
            return str(value), consumed

    under_hundred = _parse_under_hundred(normalized, start_index)
    if under_hundred:
        value, consumed = under_hundred
        return str(value), consumed
    compact_value = _compact_number_word_value(first)
    if compact_value is not None:
        return str(compact_value), 1
    return None


def canonicalize_number_word_tokens(
    tokens: list[str],
    *,
    protected_previous_words: frozenset[str] = frozenset(),
    protected_next_words: frozenset[str] = frozenset(),
) -> list[str]:
    """Canonicalize English cardinal phrases inside a token stream.

    ``protected_previous_words`` and ``protected_next_words`` let callers avoid
    rewriting identifier-like contexts. For example, ``seven waveforms`` can
    compare equal to ``7 waveforms``, while ``Gen seven`` and
    ``report seven.pdf`` remain visible differences.
    """

    canonical: list[str] = []
    index = 0
    normalized_tokens = [_normalize_number_word_token(token) for token in tokens]
    while index < len(tokens):
        parsed = parse_number_word_phrase(tokens, index)
        if parsed:
            value, consumed = parsed
            previous_word = normalized_tokens[index - 1] if index > 0 else ""
            next_index = index + consumed
            next_word = normalized_tokens[next_index] if next_index < len(tokens) else ""
            if previous_word in protected_previous_words or next_word in protected_next_words:
                canonical.extend(tokens[index : index + consumed])
            else:
                canonical.append(value)
            index += consumed
            continue
        canonical.append(tokens[index])
        index += 1
    return canonical


def _normalize_number_word_token(token: str) -> str:
    """Normalize a candidate number-word token for parsing only."""

    return token.casefold().replace("-", "").replace("‐", "").replace("‑", "")


def _compact_number_word_value(normalized: str) -> int | None:
    """Return a value for a compact cardinal token, if it is one."""

    if normalized in _NUMBER_WORD_UNITS:
        return _NUMBER_WORD_UNITS[normalized]
    if normalized in _NUMBER_WORD_TENS:
        return _NUMBER_WORD_TENS[normalized]
    for tens_word, tens_value in _NUMBER_WORD_TENS.items():
        if not normalized.startswith(tens_word):
            continue
        unit_word = normalized[len(tens_word) :]
        unit_value = _NUMBER_WORD_UNITS.get(unit_word)
        if unit_value and unit_value < 10:
            return tens_value + unit_value
    for unit_word, unit_value in _NUMBER_WORD_UNITS.items():
        if unit_value <= 0:
            continue
        prefix = f"{unit_word}hundred"
        if not normalized.startswith(prefix):
            continue
        tail = normalized[len(prefix) :]
        if not tail:
            return unit_value * 100
        if tail.startswith("and"):
            tail = tail[3:]
        tail_value = _compact_number_word_value(tail)
        if tail_value is not None and tail_value < 100:
            return unit_value * 100 + tail_value
    return None


def _parse_under_hundred(tokens: list[str], start_index: int) -> tuple[int, int] | None:
    """Parse a cardinal value below 100 from normalized tokens."""

    if start_index >= len(tokens):
        return None
    token = tokens[start_index]
    if token in _NUMBER_WORD_TENS and start_index + 1 < len(tokens):
        next_value = _NUMBER_WORD_UNITS.get(tokens[start_index + 1])
        if next_value and next_value < 10:
            return _NUMBER_WORD_TENS[token] + next_value, 2
    compact_value = _compact_number_word_value(token)
    if compact_value is not None and compact_value < 100:
        return compact_value, 1
    return None
