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
