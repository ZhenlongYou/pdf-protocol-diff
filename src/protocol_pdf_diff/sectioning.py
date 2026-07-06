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
    deep_numeric_context: tuple[str, ...] = ()
    saw_heading = False
    opening_label = _opening_section_label(extraction)

    for page in cleaned_pages:
        for raw_line in page.text.splitlines():
            line = normalize_line(raw_line)
            if not line:
                continue
            heading = detect_heading(line)
            if heading and _is_opening_range_body_integer(heading, saw_heading, opening_label):
                heading = None
            # 已有父章节时，动词/shall 开头的整数编号更像条款列表，不应拆成新章节。
            if heading and _is_integer_list_item_under_context(heading, heading_stack):
                heading = None
            if heading and _is_integer_heading_under_deep_context(heading, deep_numeric_context):
                if not _looks_like_real_integer_heading_after_deep_context(heading):
                    heading = None
            if heading:
                saw_heading = True
                if current:
                    sections.append(_close_section(current, len(sections) + 1))
                heading_stack = _updated_stack(heading_stack, heading)
                if _is_deep_numeric_heading(heading):
                    deep_numeric_context = tuple(item.number for item in heading_stack if item.number)
                else:
                    deep_numeric_context = ()
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
                    heading=opening_label,
                    title=opening_label,
                    level=0,
                    heading_path=(opening_label,),
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

    if len(pages) == 1:
        return _remove_single_page_furniture(pages)
    if len(pages) < 2:
        return pages

    edge_counts: Counter[tuple[str, str]] = Counter()
    dynamic_counts: Counter[tuple[str, str]] = Counter()
    static_furniture_counts: Counter[tuple[str, str]] = Counter()
    for page in pages:
        unique_candidates = set(_page_margin_candidates(page.text))
        for zones, line in unique_candidates:
            if 2 <= len(line) <= 100:
                for edge_position in _edge_positions(zones):
                    edge_counts[(edge_position, line)] += 1
                if _looks_like_dynamic_page_furniture(line):
                    for margin_position in _margin_positions(zones):
                        dynamic_counts[(margin_position, _furniture_fingerprint(line))] += 1
                if _looks_like_static_page_furniture(line):
                    for margin_position in _margin_positions(zones):
                        static_furniture_counts[(margin_position, line)] += 1

    min_repeats = max(2, ceil(len(pages) * 0.55))
    repeated_edges = {line for line, count in edge_counts.items() if count >= min_repeats}
    repeated_dynamic = {
        line for line, count in dynamic_counts.items() if count >= min_repeats
    }
    repeated_static = {
        line for line, count in static_furniture_counts.items() if count >= min_repeats
    }
    if not repeated_edges and not repeated_dynamic and not repeated_static:
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
                repeated_static,
            )
        ]
        cleaned.append(PageText(page_number=page.page_number, text="\n".join(kept_lines)))
    return cleaned


def _remove_single_page_furniture(pages: list[PageText]) -> list[PageText]:
    """Remove obvious margin furniture when the user selects only one page.

    Repetition-based learning cannot work on a single selected page, but PCIe
    reports still expose page counters, revision lines, and dates in the page
    margins. This path only removes lines that already match the conservative
    dynamic/static furniture recognizers and only when they are in the top or
    bottom margin.
    """

    cleaned: list[PageText] = []
    for page in pages:
        line_zones = _page_line_zones(page.text)
        kept_lines = []
        for index, line in enumerate(page.text.splitlines()):
            zones = line_zones.get(index, frozenset())
            normalized = normalize_line(line)
            if (
                zones
                and (
                    _looks_like_single_page_dynamic_furniture(normalized)
                    or _looks_like_static_page_furniture(normalized)
                )
            ):
                continue
            kept_lines.append(line)
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


def _page_line_zones(text: str, margin_size: int = 4) -> dict[int, frozenset[str]]:
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
    repeated_static: set[tuple[str, str]],
) -> bool:
    """Decide whether one extracted line is learned header/footer furniture."""

    if not zones:
        return False
    line = normalize_line(raw_line)
    if "top-edge" in zones and ("top", line) in repeated_edges:
        return True
    if "bottom-edge" in zones and ("bottom", line) in repeated_edges:
        return True
    if (
        _looks_like_static_page_furniture(line)
        and (
            ("top-margin" in zones and ("top", line) in repeated_static)
            or ("bottom-margin" in zones and ("bottom", line) in repeated_static)
        )
    ):
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
        r"\|\s*\d+\s*$",
        r"第\s*\d+\s*页(?:\s*(?:/|共)\s*\d+\s*页?)?",
        r"^\s*-?\s*\d+\s*-?\s*$",
        r"^\s*\d+\s*/\s*\d+\s*$",
    )
    return any(re.search(pattern, candidate) for pattern in page_patterns)


def _looks_like_single_page_dynamic_furniture(line: str) -> bool:
    """Single-page cleanup cannot safely remove bare numeric heading markers."""

    candidate = compact_inline(line)
    if re.fullmatch(r"-?\s*\d+\s*-?", candidate):
        return False
    return _looks_like_dynamic_page_furniture(candidate)


def _looks_like_static_page_furniture(line: str) -> bool:
    """Recognize repeated non-page-number header/footer lines.

    Some technical specifications split the running header across several
    extracted lines, for example title, revision, and date. These lines do not
    always contain a literal page counter, so they need a separate conservative
    recognizer. The caller still requires repetition in the page margin before
    removing them, which protects ordinary repeated body clauses.
    """

    candidate = compact_inline(line)
    if len(candidate) > 120:
        return False
    static_patterns = (
        # OIF 草稿 PDF 会在页边反复出现这些版权、草稿和运行标题行；它们不是协议正文差异。
        r"(?i)^draft$",
        r"(?i)^copyright\s+©?\s*\d{4}\s+optical\s+internetworking\s+forum$",
        r"(?i)^this\s+is\s+a\s+draft\s+and\s+not\s+to\s+be\s+shared\.?",
        r"(?i)^the\s+[“\"]?draft[”\"]?\s+watermark\s+is\s+not\s+to\s+be\s+removed",
        r"(?i)^optical\s+internetworking\s+forum\s+-\s+clause\s+\d+:",
        r"(?i)^implementation\s+agreement\s+oif-cei",
        r"(?i)^revision\s+\d+(?:\.\d+)*(?:,\s*version\s+\d+(?:\.\d+)*)?$",
        r"(?i)^(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2},\s+\d{4}$",
        r"(?i)^(test descriptions|revision history|table of contents)$",
    )
    return any(re.search(pattern, candidate) for pattern in static_patterns)


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
            if (
                next_line
                and _is_standalone_heading_marker(line)
                and _can_be_heading_title(next_line)
            ):
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


def _opening_section_label(extraction: ExtractionResult) -> str:
    """Name text that appears before the first detected heading.

    When the user compares a selected page range that starts mid-document, text
    before the first heading is usually carry-over content from the previous
    section rather than the beginning of the whole PDF.
    """

    start_page = extraction.selected_start_page
    if start_page is None and extraction.pages:
        start_page = min(page.page_number for page in extraction.pages)
    if start_page and start_page > 1:
        return "范围起始页前序内容"
    return "文档开头"


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

    # 形如 2.93x10-4 会被正则拆成 number=2、title=93x10-4；这里把它识别为数值。
    if _looks_like_numeric_fragment_title(title):
        return True
    if number == "0":
        return True
    if number.count(".") == 1 and (
        title[:1].islower()
        or re.match(r"(?i)^(ps|ns|us|ms|ui|mv|v|db|mhz|ghz|gt/s|hz)\b", title)
    ):
        return True
    if number.count(".") == 1 and _looks_like_decimal_table_value_title(title):
        return True
    if number.isdigit() and int(number) > 99 and title[:1].islower():
        return True
    if "." not in number and len(number) == 4 and number.startswith(("19", "20")):
        return True
    if not title:
        return True
    if len(title) <= 2 and re.fullmatch(r"[\d.%:/-]+", title):
        return True
    if re.match(r"(?i)^x\s*\d", title):
        return True
    return False


def _looks_like_numeric_fragment_title(title: str) -> bool:
    """Return True when the title part is really a numeric/table-value tail."""

    candidate = normalize_line(title)  # 数字开头可能是 400G Interfaces，也可能是 93x10-4。
    if not candidate or not candidate[:1].isdigit():
        return False
    if re.match(r"^\d+\s*[A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]+)+", candidate):
        return False  # 100G Ethernet / 400G Interfaces 是合法章节标题，不是小数尾巴。
    if re.match(r"(?i)^\d+(?:\.\d+)?\s*(?:x|×|e[+-]?\d|-|\+|/)", candidate):
        return True  # 93x10-4、2/3、2-4 这类更像数值碎片。
    if re.match(r"(?i)^\d+(?:\.\d+)?\s*(?:ps|ns|us|ms|ui|mv|v|db|mhz|ghz|gt/s|hz|ohm|mm|ff|pf|ph)\b", candidate):
        return True  # 数字后直接跟单位，通常是表格值而不是标题。
    return _looks_like_decimal_table_value_title(candidate)


def _looks_like_decimal_table_value_title(title: str) -> bool:
    """Reject table rows that begin with decimal values instead of headings."""

    candidate = normalize_line(title)  # 表格错序时，标题部分常保留参数名、单位或符号。
    if not candidate:
        return False
    unit_or_symbol = re.search(
        r"(?:Ω|—)|\b(?:ohm|mm|ff|pf|ph|ghz|mhz|gsym/s|ui|mv|v|db|ns/mm|1/mm)\b",
        candidate,
        flags=re.I,
    )  # 带单位的十进制前缀通常是表格值，不是章节号。
    table_words = re.search(
        r"(?i)\b(?:transmission|impedance|capacitance|resistance|equalizer|coefficient|frequency|voltage|bandwidth|parameter)\b",
        candidate,
    )  # 常见参数描述词进一步确认这是表格行。
    return bool(unit_or_symbol or table_words)


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
    if re.match(r"^\d", title):
        return False
    if detect_heading(title):
        return False
    return not _looks_like_table_row(title)


def _is_procedure_step_under_deep_context(
    heading: HeadingInfo,
    deep_numeric_context: tuple[str, ...],
) -> bool:
    """Recognize numbered procedure steps under their parent technical section.

    PCIe-style specifications often have real deep sections such as
    ``2.13.2 Overview...`` followed by dozens of numbered calibration steps like
    ``14. Turn all jitter...``. These steps should remain visible as diff
    units, but they should stay under the parent section path instead of being
    mistaken for top-level protocol chapters.
    """

    if not _is_integer_heading_under_deep_context(heading, deep_numeric_context):
        return False
    parent_number = deep_numeric_context[-1]
    if not _is_deep_numeric_number(parent_number):
        return False
    return _looks_like_procedure_step_title(heading.title)


def _is_integer_heading_under_deep_context(
    heading: HeadingInfo,
    deep_numeric_context: tuple[str, ...],
) -> bool:
    """Return True for integer numeric candidates under a deep section."""

    return bool(deep_numeric_context and heading.number.isdigit() and "." not in heading.number)


def _is_integer_list_item_under_context(
    heading: HeadingInfo,
    heading_stack: list[HeadingInfo],
) -> bool:
    """Keep numbered requirement bullets inside their parent section."""

    # 没有父章节时，整数编号仍可能是真正的顶层章节。
    if not heading_stack:
        return False
    # 只有纯整数编号才按列表项处理；32.2 这类 dotted 编号仍是章节候选。
    if not heading.number.isdigit() or "." in heading.number:
        return False
    # 短名词性标题即使在父章节后出现，也更可能是真正的顶层章节，如 ``2 Use Cases``。
    if _looks_like_real_integer_heading_after_deep_context(heading):
        return False
    # 动词或 shall 开头的标题通常是要求/步骤正文，而不是“第 6 章”。
    return _looks_like_procedure_step_title(heading.title)


def _is_opening_range_body_integer(
    heading: HeadingInfo,
    saw_heading: bool,
    opening_label: str,
) -> bool:
    """Keep mid-procedure range starts grouped as carry-over body text.

    When a user selects a page window that starts inside an existing PCIe-style
    procedure, the first visible lines can be ``6. Adjust ...`` or formula
    continuations such as ``6 X 62.5 ps =``. Without the parent ``2.x.x``
    heading from the previous page, those integer lines look like new top-level
    chapters. Treat only instruction-like or formula-like integer candidates as
    body until a real heading appears; noun-like headings such as
    ``3 Receiver Requirements`` still start normal sections.
    """

    if saw_heading or opening_label != "范围起始页前序内容":
        return False
    if not heading.number.isdigit() or "." in heading.number:
        return False
    return (
        _looks_like_procedure_step_title(heading.title)
        or _looks_like_wrapped_numeric_continuation(heading)
        or _looks_like_formula_fragment_heading(heading)
    )


def _looks_like_formula_fragment_heading(heading: HeadingInfo) -> bool:
    """Return True for equation fragments misread as integer headings."""

    title = normalize_line(heading.title)
    if not title:
        return False
    has_unit_or_operator = bool(
        re.search(r"(?i)\b(?:ps|ns|us|ms|ui|mv|v|mhz|ghz|gt/s)\b", title)
        or re.search(r"[=×*/+-]", title)
    )
    return has_unit_or_operator and bool(re.search(r"\d", title))


def _looks_like_wrapped_numeric_continuation(heading: HeadingInfo) -> bool:
    """Reject wrapped text fragments such as ``9 must all be calibrated``."""

    title = normalize_line(heading.title)
    return bool(title and title[:1].islower())


def _looks_like_real_integer_heading_after_deep_context(heading: HeadingInfo) -> bool:
    """Allow obvious top-level headings after a deep procedure section.

    Under PCIe-style deep sections, integer-numbered lines are much more often
    procedure steps than new chapters. A missed step is worse than a conservative
    parent section, so only short noun-like titles with common section-heading
    words are allowed to break out of the parent context.
    """

    if _looks_like_wrapped_numeric_continuation(heading):
        return False
    title = normalize_line(heading.title)
    if not title:
        return False
    if title.rstrip().endswith((".", ";", "；", "。")) and _looks_like_procedure_step_title(title):
        return False
    words = re.findall(r"[A-Za-z]+", title.casefold())
    if not words or len(words) > 8:
        return False
    heading_words = {
        "acceptance",
        "appendix",
        "architecture",
        "background",
        "calibration",
        "compliance",
        "configuration",
        "direction",
        "definitions",
        "description",
        "electrical",
        "ethernet",
        "introduction",
        "interface",
        "interfaces",
        "method",
        "overview",
        "power",
        "procedure",
        "receiver",
        "references",
        "requirements",
        "scope",
        "specification",
        "test",
        "tests",
        "transmitter",
        "use",
    }
    if set(words) & heading_words:
        return True
    return _looks_like_title_case_heading(title)


def _looks_like_title_case_heading(title: str) -> bool:
    """Return True for short title-like noun phrases without sentence punctuation."""

    if title.rstrip().endswith((".", ";", "；", "。")):
        return False
    words = re.findall(r"[A-Za-z0-9]+", title)
    if not words or len(words) > 6:
        return False
    lower_function_words = {"a", "an", "and", "for", "in", "of", "on", "the", "to", "with"}
    content_words = [word for word in words if word.casefold() not in lower_function_words]
    if not content_words:
        return False
    return all(word[:1].isupper() or word[:1].isdigit() for word in content_words)


def _is_deep_numeric_heading(heading: HeadingInfo) -> bool:
    """Return True for headings such as ``2.13.2`` that commonly own steps."""

    return _is_deep_numeric_number(heading.number)


def _is_deep_numeric_number(number: str) -> bool:
    """Return True for dotted numeric headings at depth three or deeper."""

    return number.count(".") >= 2


def _looks_like_procedure_step_title(title: str) -> bool:
    """Return True when a numeric heading is more likely a list step."""

    normalized = normalize_line(title)
    if not normalized:
        return False
    first_word_match = re.match(r"([A-Za-z]+)", normalized)
    first_word = first_word_match.group(1).casefold() if first_word_match else ""
    procedure_words = {
        "adjust",
        "analyze",
        "apply",
        "attach",
        "calibrate",
        "capture",
        "change",
        "check",
        "choose",
        "configure",
        "connect",
        "decrease",
        "determine",
        "disconnect",
        "enable",
        "enter",
        "find",
        "for",
        "have",
        "if",
        "install",
        "load",
        "measure",
        "note",
        "observe",
        "perform",
        "power",
        "prepare",
        "record",
        "remove",
        "repeat",
        "report",
        "run",
        "save",
        "select",
        "set",
        "shall",
        "transmit",
        "turn",
        "using",
        "verify",
        "wait",
    }
    if first_word in procedure_words:
        return True
    if _looks_like_instruction_sentence(normalized):
        return True
    if len(normalized) > 45:
        return True
    return bool(re.match(r"^[a-z0-9]", normalized))


def _looks_like_instruction_sentence(title: str) -> bool:
    """Return True for numbered lines that read like procedure instructions."""

    words = re.findall(r"[A-Za-z]+", title)
    if len(words) < 4:
        return False
    instruction_markers = {
        "a",
        "an",
        "after",
        "all",
        "and",
        "before",
        "from",
        "if",
        "into",
        "of",
        "the",
        "then",
        "to",
        "until",
        "using",
        "when",
        "with",
        "within",
    }
    lowered = {word.casefold() for word in words}
    return bool(lowered & instruction_markers)


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
