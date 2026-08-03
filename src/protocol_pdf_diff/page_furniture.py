"""Document-agnostic recognition of running headers and footer clusters.

The rules in this module describe layout shapes rather than publication names.
They intentionally require a page-bearing anchor near a page margin before a
neighboring revision or date line can be removed.
"""

from __future__ import annotations

import re

from .text_utils import normalize_line


_MONTH_NAME_PATTERN = (
    r"January|February|March|April|May|June|July|August|September|October|"
    r"November|December"
)
_PAGE_BEARING_HEADER_RE = re.compile(
    r"(?i)^(?=.{16,180}$)(?=.*[A-Za-z\u3400-\u9fff])"
    r"[^|\n]{6,}\|\s*(?:page\s*)?\d+"
    r"(?:\s*(?:of|/)\s*\d+)?$"
)
_REVISION_OR_EDITION_RE = re.compile(
    r"(?i)^(?:rev(?:ision)?|version|edition)\s*[.:：]?\s*"
    r"[A-Za-z0-9][A-Za-z0-9._-]*"
    r"(?:\s*[,;/]\s*(?:rev(?:ision)?|version|edition)\s*[.:：]?\s*"
    r"[A-Za-z0-9][A-Za-z0-9._-]*)?$|"
    r"^(?:修订|版本|版次)\s*[：:]?\s*[A-Za-z0-9._-]+$"
)
_PUBLICATION_DATE_RE = re.compile(
    rf"(?i)^(?:{_MONTH_NAME_PATTERN})\s+\d{{1,2}},\s+\d{{4}}$|"
    rf"^\d{{1,2}}\s+(?:{_MONTH_NAME_PATTERN})\s+\d{{4}}$|"
    r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}$|"
    r"^\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日$"
)


def looks_like_page_bearing_running_header(value: str) -> bool:
    """Return True for a title-like line ending in a pipe-delimited page number."""

    return bool(_PAGE_BEARING_HEADER_RE.fullmatch(normalize_line(value)))


def looks_like_revision_or_publication_date(value: str) -> bool:
    """Return True for a standalone revision, edition, or publication date line."""

    candidate = normalize_line(value)
    return bool(
        _REVISION_OR_EDITION_RE.fullmatch(candidate)
        or _PUBLICATION_DATE_RE.fullmatch(candidate)
    )


def running_header_cluster_line_indexes(
    raw_lines: list[str],
    *,
    margin_size: int = 4,
) -> set[int]:
    """Find title/page anchors and adjacent revision/date lines near page margins."""

    lines = [normalize_line(line) for line in raw_lines]
    non_empty_indexes = [index for index, line in enumerate(lines) if line]
    if not non_empty_indexes:
        return set()
    margin_indexes = set(non_empty_indexes[:margin_size]) | set(
        non_empty_indexes[-margin_size:]
    )

    indexes: set[int] = set()
    for index in margin_indexes:
        if not looks_like_page_bearing_running_header(lines[index]):
            continue
        cluster_neighbors: set[int] = set()
        for neighbor in range(max(0, index - 2), min(len(lines), index + 4)):
            if neighbor in margin_indexes and looks_like_revision_or_publication_date(
                lines[neighbor]
            ):
                cluster_neighbors.add(neighbor)
        if cluster_neighbors:
            indexes.add(index)
            indexes.update(cluster_neighbors)
    return indexes
