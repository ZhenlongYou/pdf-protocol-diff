"""Convert extracted PDF text into protocol-like sections.

The sectioner is heuristic because PDFs do not carry reliable semantic
"chapter" data. It recognizes common Chinese and numeric protocol headings such
as ``第一章``, ``第2节``, ``1``, ``1.1``, ``一、`` and ``(一)``. When no headings
are found, it falls back to page-based chunks instead of pretending it knows the
document structure.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from hashlib import sha1
from math import ceil

from .models import ExtractionResult, HeadingInfo, PageText, Section
from .text_utils import compact_inline, normalize_for_similarity, normalize_line

_CHINESE_NUM = r"零〇一二三四五六七八九十百千万两0-9\d"

_HEADING_PATTERNS: tuple[tuple[re.Pattern[str], int, str], ...] = (
    (
        re.compile(rf"^(第\s*[{_CHINESE_NUM}]+\s*章)\s*(.{{0,100}})$"),
        1,
        "chapter",
    ),
    (
        re.compile(rf"^(第\s*[{_CHINESE_NUM}]+\s*节)\s*(.{{0,100}})$"),
        2,
        "section",
    ),
    (
        re.compile(r"^(\d+(?:\.\d+){0,5})(?:[、.)．\s]+)(.{0,120})$"),
        0,
        "numeric",
    ),
    (
        re.compile(r"^([一二三四五六七八九十百千万]+[、.．])\s*(.{1,100})$"),
        2,
        "cn_list",
    ),
    (
        re.compile(r"^[(（]([一二三四五六七八九十百千万0-9\d]+)[)）]\s*(.{1,100})$"),
        3,
        "paren",
    ),
)


@dataclass
class _OpenSection:
    """Mutable buffer while pages are being sectioned."""

    heading: str
    title: str
    level: int
    heading_path: tuple[str, ...]
    number_path: tuple[str, ...]
    start_page: int
    end_page: int
    lines: list[str]


def section_document(extraction: ExtractionResult) -> list[Section]:
    """Split extracted pages into logical protocol sections.

    Repeated short lines that appear on many pages are treated as headers or
    footers and removed. This reduces false changes caused by page numbers,
    document titles, confidentiality banners, and similar furniture.
    """

    cleaned_pages = _merge_standalone_heading_lines(_remove_repeating_page_furniture(extraction.pages))
    sections: list[Section] = []
    current: _OpenSection | None = None
    heading_stack: list[HeadingInfo] = []
    saw_heading = False

    for page in cleaned_pages:
        for raw_line in page.text.splitlines():
            line = normalize_line(raw_line)
            if not line:
                continue
            heading = detect_heading(line)
            if heading:
                saw_heading = True
                if current:
                    sections.append(_close_section(current, len(sections) + 1))
                heading_stack = _updated_stack(heading_stack, heading)
                current = _OpenSection(
                    heading=heading.raw,
                    title=heading.title or heading.raw,
                    level=heading.level,
                    heading_path=tuple(item.raw for item in heading_stack),
                    number_path=tuple(item.number for item in heading_stack if item.number),
                    start_page=page.page_number,
                    end_page=page.page_number,
                    lines=[],
                )
                continue

            if current is None:
                current = _OpenSection(
                    heading="文档开头",
                    title="文档开头",
                    level=0,
                    heading_path=("文档开头",),
                    number_path=(),
                    start_page=page.page_number,
                    end_page=page.page_number,
                    lines=[],
                )
            current.end_page = page.page_number
            current.lines.append(line)

    if current:
        sections.append(_close_section(current, len(sections) + 1))

    meaningful_sections = [section for section in sections if section.body.strip()]
    if not saw_heading:
        return _page_fallback_sections(cleaned_pages)
    return meaningful_sections


def detect_heading(line: str) -> HeadingInfo | None:
    """Return heading metadata if a line looks like a protocol heading."""

    candidate = compact_inline(line)
    if not candidate or len(candidate) > 140:
        return None
    if _looks_like_table_row(candidate):
        return None

    for pattern, configured_level, kind in _HEADING_PATTERNS:
        match = pattern.match(candidate)
        if not match:
            continue
        number = match.group(1).strip()
        title = match.group(2).strip() if match.lastindex and match.lastindex >= 2 else ""
        if kind == "paren":
            number = f"({number})"
        if kind == "numeric":
            if _looks_like_year_or_decimal_value(number, title):
                continue
            level = number.count(".") + 1
        else:
            level = configured_level
        return HeadingInfo(raw=candidate, number=number, title=title, level=level)
    return None


def _remove_repeating_page_furniture(pages: list[PageText]) -> list[PageText]:
    """Remove repeated headers/footers that appear on many pages.

    Protocol PDFs often repeat document titles, confidentiality notices, and page
    counters on every page. Those lines are layout furniture rather than contract
    content, and they become especially noisy when new text shifts the page count
    from, for example, "Page 2 of 3" to "Page 2 of 4". The heuristic only learns
    from the first and last few lines of each page so repeated body clauses are
    much less likely to be removed accidentally.
    """

    if len(pages) < 2:
        return pages

    edge_counts: Counter[tuple[str, str]] = Counter()
    dynamic_counts: Counter[tuple[str, str]] = Counter()
    for page in pages:
        unique_candidates = set(_page_margin_candidates(page.text))
        for zones, line in unique_candidates:
            if 2 <= len(line) <= 100:
                for edge_position in _edge_positions(zones):
                    edge_counts[(edge_position, line)] += 1
                if _looks_like_dynamic_page_furniture(line):
                    for margin_position in _margin_positions(zones):
                        dynamic_counts[(margin_position, _furniture_fingerprint(line))] += 1

    min_repeats = max(2, ceil(len(pages) * 0.55))
    repeated_edges = {line for line, count in edge_counts.items() if count >= min_repeats}
    repeated_dynamic = {
        line for line, count in dynamic_counts.items() if count >= min_repeats
    }
    if not repeated_edges and not repeated_dynamic:
        return pages

    cleaned: list[PageText] = []
    for page in pages:
        line_zones = _page_line_zones(page.text)
        kept_lines = [
            line
            for index, line in enumerate(page.text.splitlines())
            if not _is_removed_page_furniture(
                line,
                line_zones.get(index, frozenset()),
                repeated_edges,
                repeated_dynamic,
            )
        ]
        cleaned.append(PageText(page_number=page.page_number, text="\n".join(kept_lines)))
    return cleaned


def _page_margin_candidates(text: str) -> list[tuple[frozenset[str], str]]:
    """Return position-tagged normalized lines from likely page furniture areas."""

    raw_lines = text.splitlines()
    line_zones = _page_line_zones(text)
    candidates: list[tuple[frozenset[str], str]] = []
    for index, zones in line_zones.items():
        line = normalize_line(raw_lines[index])
        if line:
            candidates.append((zones, line))
    return candidates


def _page_line_zones(text: str, margin_size: int = 3) -> dict[int, frozenset[str]]:
    """Map raw line indexes to conservative page-furniture zones.

    Exact repeated lines are removed only at the first/last non-empty line. Dynamic
    page counters use a wider top/bottom margin so lines such as `Page 2 of 4`
    are still removed when extraction appends another tail line after the footer.
    """

    indexed_lines = [
        (index, normalize_line(line))
        for index, line in enumerate(text.splitlines())
        if normalize_line(line)
    ]
    if not indexed_lines:
        return {}

    zones_by_index: dict[int, set[str]] = {}
    zones_by_index.setdefault(indexed_lines[0][0], set()).update({"top-edge", "top-margin"})
    zones_by_index.setdefault(indexed_lines[-1][0], set()).update({"bottom-edge", "bottom-margin"})

    for index, _line in indexed_lines[:margin_size]:
        zones_by_index.setdefault(index, set()).add("top-margin")
    for index, _line in indexed_lines[-margin_size:]:
        zones_by_index.setdefault(index, set()).add("bottom-margin")
    return {index: frozenset(zones) for index, zones in zones_by_index.items()}


def _is_removed_page_furniture(
    raw_line: str,
    zones: frozenset[str],
    repeated_edges: set[tuple[str, str]],
    repeated_dynamic: set[tuple[str, str]],
) -> bool:
    """Decide whether one extracted line is learned header/footer furniture."""

    if not zones:
        return False
    line = normalize_line(raw_line)
    if "top-edge" in zones and ("top", line) in repeated_edges:
        return True
    if "bottom-edge" in zones and ("bottom", line) in repeated_edges:
        return True
    if not _looks_like_dynamic_page_furniture(line):
        return False
    fingerprint = _furniture_fingerprint(line)
    return (
        ("top-margin" in zones and ("top", fingerprint) in repeated_dynamic)
        or ("bottom-margin" in zones and ("bottom", fingerprint) in repeated_dynamic)
    )


def _edge_positions(zones: frozenset[str]) -> list[str]:
    """Return exact-repeat furniture positions represented by a zone set."""

    positions: list[str] = []
    if "top-edge" in zones:
        positions.append("top")
    if "bottom-edge" in zones:
        positions.append("bottom")
    return positions


def _margin_positions(zones: frozenset[str]) -> list[str]:
    """Return dynamic-counter furniture positions represented by a zone set."""

    positions: list[str] = []
    if "top-margin" in zones:
        positions.append("top")
    if "bottom-margin" in zones:
        positions.append("bottom")
    return positions


def _looks_like_dynamic_page_furniture(line: str) -> bool:
    """Recognize page counters and other dynamic footer/header variants."""

    candidate = compact_inline(line)
    if len(candidate) > 100:
        return False
    page_patterns = (
        r"(?i)\bpage\s*\d+\s*(?:of|/|-)\s*\d+\b",
        r"(?i)\bpage\s*\d+\b",
        r"第\s*\d+\s*页(?:\s*(?:/|共)\s*\d+\s*页?)?",
        r"^\s*-?\s*\d+\s*-?\s*$",
        r"^\s*\d+\s*/\s*\d+\s*$",
    )
    return any(re.search(pattern, candidate) for pattern in page_patterns)


def _furniture_fingerprint(line: str) -> str:
    """Group dynamic furniture such as page counters across pages."""

    return re.sub(r"\d+", "#", normalize_for_similarity(line))


def _merge_standalone_heading_lines(pages: list[PageText]) -> list[PageText]:
    """Merge headings that PDF extraction split across two lines.

    Many protocols visually show ``1`` and the title beside or below it, but PDF
    extraction can return them as ``1`` followed by ``适用范围`` on the next line.
    Combining only short standalone heading markers keeps the chapter/section
    hierarchy useful without rewriting ordinary paragraphs.
    """

    merged_pages: list[PageText] = []
    for page in pages:
        raw_lines = page.text.splitlines()
        merged_lines: list[str] = []
        index = 0
        while index < len(raw_lines):
            line = normalize_line(raw_lines[index])
            if not line:
                index += 1
                continue
            next_line = _next_non_empty_line(raw_lines, index + 1)
            if next_line and _is_standalone_heading_marker(line) and _can_be_heading_title(next_line):
                merged_lines.append(f"{line} {next_line}")
                index = _index_after_next_non_empty(raw_lines, index + 1)
                continue
            merged_lines.append(line)
            index += 1
        merged_pages.append(PageText(page_number=page.page_number, text="\n".join(merged_lines)))
    return merged_pages


def _updated_stack(stack: list[HeadingInfo], heading: HeadingInfo) -> list[HeadingInfo]:
    """Apply a newly detected heading to the hierarchy stack."""

    level = max(1, heading.level)
    trimmed = [item for item in stack if item.level < level]
    trimmed.append(heading)
    return trimmed


def _close_section(open_section: _OpenSection, index: int) -> Section:
    """Freeze a mutable section buffer into an immutable Section."""

    body = "\n".join(open_section.lines).strip()
    return Section(
        section_id=f"S{index:04d}",
        heading=open_section.heading,
        title=open_section.title,
        level=open_section.level,
        heading_path=open_section.heading_path,
        number_path=open_section.number_path,
        start_page=open_section.start_page,
        end_page=open_section.end_page,
        body=body,
    )


def _page_fallback_sections(pages: list[PageText]) -> list[Section]:
    """Build page-level sections when no reliable headings were found."""

    sections: list[Section] = []
    for page in pages:
        body = normalize_for_similarity(page.text).strip()
        if not body:
            continue
        heading = f"第 {page.page_number} 页"
        sections.append(
            Section(
                section_id=f"P{page.page_number:04d}",
                heading=heading,
                title=heading,
                level=1,
                heading_path=(heading,),
                number_path=(_fallback_identity_key(body),),
                start_page=page.page_number,
                end_page=page.page_number,
                body=body,
            )
        )
    return sections


def _fallback_identity_key(body: str) -> str:
    """Create a content identity for pages without headings.

    The fallback still reports page numbers as locations, but matching should not
    use the physical page number. A stable content digest lets unchanged pages
    match across page shifts while modified pages fall through to similarity
    matching instead of being forced into same-page pairs.
    """

    digest = sha1(body.encode("utf-8")).hexdigest()
    return f"fallback-content:{digest}"


def _looks_like_table_row(line: str) -> bool:
    """Reject dense table rows that often begin with numbers."""

    separators = line.count("|") + line.count("\t")
    many_numbers = len(re.findall(r"\d+(?:\.\d+)?", line)) >= 4
    return separators >= 2 or (many_numbers and len(line) > 40)


def _looks_like_year_or_decimal_value(number: str, title: str) -> bool:
    """Avoid treating dates or plain numeric values as section headings."""

    if "." not in number and len(number) == 4 and number.startswith(("19", "20")):
        return True
    if not title:
        return True
    if len(title) <= 2 and re.fullmatch(r"[\d.%:/-]+", title):
        return True
    return False


def _is_standalone_heading_marker(line: str) -> bool:
    """Return True for bare heading markers such as ``1`` or ``第一章``."""

    numeric = re.fullmatch(r"(\d+(?:\.\d+){0,5})[.)．]?", line)
    if numeric:
        number = numeric.group(1)
        if "." not in number and len(number) == 4 and number.startswith(("19", "20")):
            return False
        return True
    return bool(re.fullmatch(rf"第\s*[{_CHINESE_NUM}]+\s*[章节]", line))


def _can_be_heading_title(line: str) -> bool:
    """Guard against merging table rows or another heading as a title."""

    title = normalize_line(line)
    if not title or len(title) > 100:
        return False
    if detect_heading(title):
        return False
    return not _looks_like_table_row(title)


def _next_non_empty_line(lines: list[str], start_index: int) -> str | None:
    """Find the next non-empty normalized line on the same page."""

    for index in range(start_index, len(lines)):
        normalized = normalize_line(lines[index])
        if normalized:
            return normalized
    return None


def _index_after_next_non_empty(lines: list[str], start_index: int) -> int:
    """Return the index immediately after the next non-empty line."""

    for index in range(start_index, len(lines)):
        if normalize_line(lines[index]):
            return index + 1
    return len(lines)
