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
from dataclasses import dataclass, replace
from hashlib import sha1
from math import ceil

from .models import ExtractionResult, HeadingInfo, PageText, Section
from .text_utils import (
    compact_inline,
    normalize_for_similarity,
    normalize_line,
)

_CHINESE_NUM = r"零〇一二三四五六七八九十百千万两0-9\d"
_DOCUMENT_METADATA_TITLE_RE = re.compile(
    r"(?i)(?:^contents$|^目录$|\btable\s+of\s+contents\b|"
    r"\b(?:legal\s+)?notice\b|法律声明|\bcopyright\b|版权|"
    r"\bforeword\b|\bpreface\b|前言|序言|"
    r"\b(?:revision|version|change|document|release|amendment)\s+history\b|"
    r"\b(?:change|release)\s+(?:log|record)\b|"
    r"\brecord\s+of\s+(?:revisions|amendments)\b|"
    r"\bdocument\s+control\b|\bapproval\s+(?:record|history)\b|"
    r"\bdistribution\s+list\b|"
    r"修订历史|版本历史|变更历史|变更日志|修订记录|发布记录|"
    r"文件控制|文档控制|批准记录|审批记录|分发清单)"
)

_HEADING_PATTERNS: tuple[tuple[re.Pattern[str], int, str], ...] = (
    (
        re.compile(
            r"(?i)^((?:part)\s+(?:[IVXLCDM]+|\d+))"
            r"(?:(?:[\s:.-]+)(.{0,120}))?$"
        ),
        1,
        "part",
    ),
    (
        re.compile(r"^([A-Z]\.\d+(?:\.\d+){0,4})(?:[、.)．\s]+)(.{0,120})$"),
        0,
        "annex_numeric",
    ),
    (
        # 数字主章后的字母附录（如 31.A.1）必须先于纯数字规则识别，避免身份塌缩为 31。
        re.compile(r"^(\d+\.[A-Z](?:\.\d+){0,4})(?:[、.)．\s]+)(.{0,120})$"),
        0,
        "numeric_letter",
    ),
    (
        re.compile(
            r"(?i)^((?:annex|appendix)\s+[A-Z0-9]+)"
            r"(?:(?:[\s:.-]+)(.{0,120}))?$"
        ),
        1,
        "annex",
    ),
    (
        re.compile(
            rf"^(附录\s*[A-Z{_CHINESE_NUM}]+)"
            rf"(?:(?:[\s:：.-]+)(.{{0,120}}))?$",
            re.IGNORECASE,
        ),
        1,
        "annex",
    ),
    (
        re.compile(r"(?i)^((?:chapter)\s+\d+(?:\.\d+)*)(?:[\s:.-]+)(.{0,120})$"),
        1,
        "named_numeric",
    ),
    (
        re.compile(r"(?i)^((?:section|clause)\s+\d+(?:\.\d+)*)(?:[\s:.-]+)(.{0,120})$"),
        0,
        "named_numeric",
    ),
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
    page_lines: dict[int, list[str]]


def section_document(extraction: ExtractionResult) -> list[Section]:
    """Split extracted pages into logical protocol sections.

    Repeated short lines that appear on many pages are treated as headers or
    footers and removed. This reduces false changes caused by page numbers,
    document titles, confidentiality banners, and similar furniture.
    """

    cleaned_pages = _merge_standalone_heading_lines(
        _remove_repeating_page_furniture(_remove_proven_margin_noise(extraction.pages))
    )
    sections: list[Section] = []
    current: _OpenSection | None = None
    heading_stack: list[HeadingInfo] = []
    inside_contents = False
    contents_heading_stack: list[HeadingInfo] = []
    contents_heading_paths: set[tuple[str, ...]] = set()
    deep_numeric_context: tuple[str, ...] = ()
    procedure_step_numbers: list[int] = []
    prose_list_step_numbers: list[int] = []
    prose_list_item_open = False
    saw_heading = False
    opening_label = _opening_section_label(extraction)

    for page_index, page in enumerate(cleaned_pages):
        if _is_contents_page(page.text):
            inside_contents = True
        page_lines = page.text.splitlines()
        for line_index, raw_line in enumerate(page_lines):
            line = normalize_line(raw_line)
            if not line:
                continue
            heading_candidate = _line_without_ambiguous_margin_number(
                line,
                page.ambiguous_line_number_sides,
            )  # 数字仍留在正文；这里只阻止已知页边候选成为章节号或污染真实标题身份。
            heading = detect_heading(heading_candidate)
            prose_body_candidate = (
                _numbered_prose_body_candidate(heading_candidate)
                if heading is None
                else None
            )  # 技术比值等可能触发表格保护而不成为标题，但仍可作为后续列表项的结构证据。
            if heading and inside_contents:
                contents_heading = _contextualize_heading(heading, contents_heading_stack)
                candidate_contents_stack = _updated_stack(
                    contents_heading_stack,
                    contents_heading,
                )
                contents_path = tuple(
                    canonical_number_identity(item.number)
                    for item in candidate_contents_stack
                    if item.number
                )
                if contents_path in contents_heading_paths:
                    # 正文通常从目录已经列过的首个编号重新开始；同页和跨页均以
                    # 第一次完整层级路径重复作为目录结束信号，并保留真实正文标题。
                    inside_contents = False
                    contents_heading_stack.clear()
                    contents_heading_paths.clear()
                else:
                    contents_heading_stack = candidate_contents_stack
                    contents_heading_paths.add(contents_path)
                    heading = None  # 目录条目属于文档元数据，不参与技术章节匹配。
            if heading:
                heading = _contextualize_heading(heading, heading_stack)
            body_following_lines = (
                _numbered_prose_following_lines(
                    cleaned_pages,
                    page_index,
                    line_index,
                )
                if prose_body_candidate is not None
                else []
            )
            body_candidate_continues_list = bool(
                prose_body_candidate
                and _is_sequential_numbered_prose_item(
                    prose_body_candidate,
                    heading_stack,
                    tuple(prose_list_step_numbers),
                    following_context_proves_list=_following_context_proves_numbered_prose_item(
                        body_following_lines,
                        prose_body_candidate,
                        heading_stack,
                        chain_started=bool(prose_list_step_numbers),
                    ),
                    following_context_proves_chapter_body=_following_context_proves_numbered_chapter_body(
                        body_following_lines,
                        prose_body_candidate,
                        heading_stack,
                        chain_started=bool(prose_list_step_numbers),
                    ),
                )
            )
            if body_candidate_continues_list:
                assert prose_body_candidate is not None
                prose_list_step_numbers.append(int(prose_body_candidate.number))
                prose_list_item_open = not _numbered_prose_sentence_is_closed(
                    prose_body_candidate.title
                )
            elif (
                heading is None
                and prose_list_step_numbers
                and not _is_serialized_table_evidence_line(line)
            ):
                if prose_list_item_open:
                    prose_list_item_open = not _numbered_prose_sentence_is_closed(line)
                else:
                    prose_list_step_numbers.clear()
                # 仅允许未结束的列表句跨一个或多个软换行；普通正文段会立即结束编号链。
            if heading and _is_opening_range_body_integer(heading, saw_heading, opening_label):
                heading = None
            # 普通章节下也可能出现完整的 N..M 叙述句列表。连续递增且句子形态明确时
            # 留在正文；不依赖领域词，也允许页窗/抽取从列表中段开始。
            heading_following_lines = (
                _numbered_prose_following_lines(
                    cleaned_pages,
                    page_index,
                    line_index,
                )
                if heading is not None
                else []
            )
            if heading and _is_sequential_numbered_prose_item(
                heading,
                heading_stack,
                tuple(prose_list_step_numbers),
                following_context_proves_list=_following_context_proves_numbered_prose_item(
                    heading_following_lines,
                    heading,
                    heading_stack,
                    chain_started=bool(prose_list_step_numbers),
                ),
                following_context_proves_chapter_body=_following_context_proves_numbered_chapter_body(
                    heading_following_lines,
                    heading,
                    heading_stack,
                    chain_started=bool(prose_list_step_numbers),
                ),
            ):
                prose_list_step_numbers.append(int(heading.number))
                prose_list_item_open = not _numbered_prose_sentence_is_closed(
                    heading.title
                )
                heading = None
            # 已有父章节时，动词/shall 开头的整数编号更像条款列表，不应拆成新章节。
            if heading and _is_integer_list_item_under_context(
                heading,
                heading_stack,
                tuple(procedure_step_numbers),
            ):
                if _heading_stack_proves_procedure_context(heading_stack):
                    procedure_step_numbers.append(int(heading.number))
                heading = None
            if heading and _is_integer_heading_under_deep_context(heading, deep_numeric_context):
                if not _looks_like_real_integer_heading_after_deep_context(heading):
                    heading = None
            if heading and _has_inconsistent_numeric_parent(heading, heading_stack):
                heading = None  # 当前父编号与候选前缀冲突时，更像表格/引用中的旧编号。
            if heading:
                saw_heading = True
                procedure_step_numbers.clear()  # 新章节结束上一个 Procedure 的局部步骤序列。
                prose_list_step_numbers.clear()  # 真章节也结束普通叙述句的局部编号链。
                prose_list_item_open = False
                if current:
                    sections.append(_close_section(current, len(sections) + 1))
                heading_stack = _updated_stack(heading_stack, heading)
                number_path = tuple(item.number for item in heading_stack if item.number)
                if _is_deep_numeric_heading(heading):
                    deep_numeric_context = number_path
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
                    lines=(
                        [line]
                        if heading_candidate != line
                        else []
                    ),  # 结构识别可忽略疑似页边数，但原始标题行仍进入正文比较，防止真实尾数被静默吞掉。
                    page_lines=(
                        {page.page_number: [line]}
                        if heading_candidate != line
                        else {}
                    ),
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
                    page_lines={},
                )
            current.end_page = page.page_number
            current.lines.append(line)
            current.page_lines.setdefault(page.page_number, []).append(line)

    if current:
        sections.append(_close_section(current, len(sections) + 1))

    meaningful_sections = [
        section
        for section in sections
        if section.body.strip() or section.number_path
    ]  # 编号容器标题本身也是可比较事实，即使正文全部位于子条款中也不能丢弃。
    if not saw_heading:
        return _page_fallback_sections(cleaned_pages)
    return meaningful_sections


def _is_contents_page(text: str) -> bool:
    """Return True for a dedicated contents page whose numbers are not body headings."""

    visible_lines: list[str] = []
    for raw_line in text.splitlines():
        line = normalize_line(raw_line)
        if line:
            visible_lines.append(line)
    return any(
        re.fullmatch(
            r"(?i)(?:(?:table\s+of\s+contents|contents)"
            r"(?:\s*\(\s*continued\s*\))?|目\s*录(?:\s*[（(]\s*续\s*[）)])?)",
            line,
        )
        for line in visible_lines[:12]
    )


def canonical_number_identity(number: str) -> str:
    """Normalize extraction-only spacing before comparing heading numbers."""

    compact = re.sub(r"\s+", "", number).casefold()
    return re.sub(r"^(?:chapter|section|clause)", "", compact)


def _contextualize_heading(
    heading: HeadingInfo,
    stack: list[HeadingInfo],
) -> HeadingInfo:
    """Nest numeric headings beneath an active Part/Annex/Appendix container."""

    if not stack or not re.match(r"(?i)^(?:part|annex|appendix)\b", stack[0].number):
        return heading
    if not re.match(r"(?i)^(?:(?:section|clause)\s+)?\d", heading.number):
        return heading
    return HeadingInfo(
        raw=heading.raw,
        number=heading.number,
        title=heading.title,
        level=heading.level + 1,
    )


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
        title = (
            (match.group(2) or "").strip()
            if match.lastindex and match.lastindex >= 2
            else ""
        )
        if kind == "paren":
            number = f"({number})"
        # 三类点号编号都按完整结构标记计算层级，保留 31.A.1 的字母节点。
        if kind in {"numeric", "named_numeric", "annex_numeric", "numeric_letter"}:
            numeric_number = re.sub(r"(?i)^(?:chapter|section|clause)\s+", "", number)
            # 字母层级和 Annex 子条款不可能是十进制值，只对纯数字候选运行数值过滤。
            if kind not in {"annex_numeric", "numeric_letter"} and _looks_like_year_or_decimal_value(
                numeric_number,
                title,
            ):
                continue
            level = numeric_number.count(".") + 1
        else:
            level = configured_level
        if _looks_like_forbidden_heading_candidate(candidate, number, title, kind):
            if kind == "numeric_letter":
                return None  # 完整 mixed 编号已经消费；拒绝后不能再让纯 numeric 规则只接收前缀 31。
            continue
        return HeadingInfo(raw=candidate, number=number, title=title, level=level)
    return None


def _remove_proven_margin_noise(pages: list[PageText]) -> list[PageText]:
    """Remove line-number gutters and coordinate-proven running furniture.

    Layout blocks retain the raw page evidence.  Section matching, however,
    needs a readable text stream: FrameMaker exports can interleave a 1--49
    gutter with every text line and append the gutter number at line ends.
    We act only after a page itself proves that dense gutter pattern, then
    remove repeated legal/title/status clusters proven to occupy a page margin.
    """

    coordinate_furniture = _coordinate_margin_furniture_lines(pages)
    normalized_pages: list[list[str]] = []
    for page in pages:
        proven_lines = coordinate_furniture.get(page.page_number, set())
        source_lines = [normalize_line(line) for line in page.text.splitlines()]
        unique_proven_lines = _uniquely_observed_furniture_fragments(
            source_lines,
            proven_lines,
        )
        raw_lines = [
            _strip_coordinate_furniture_fragment(line, unique_proven_lines)
            for line in source_lines
        ]
        paragraph_lines: list[str] = []  # 只保留一个连续空行，避免被删除的行号把页脚推出统计窗口。
        for line in raw_lines:  # 每次迭代保留正文，或保留可证明一个段落断点的首个空行。
            if not line and (not paragraph_lines or not paragraph_lines[-1]):
                continue  # 开头及连续空行不增加新的段落证据。
            paragraph_lines.append(line)  # 非空正文和单个段落边界都进入后续章节合并。
        # 保留空行作为段落证据；后续章节循环会忽略空行，但拆行标题合并必须看见边界。
        normalized_pages.append(paragraph_lines)

    repeated_footer_lines: Counter[str] = Counter()
    for lines in normalized_pages:
        for line in set(lines[-6:]):
            if _looks_like_repeated_margin_furniture(line):
                repeated_footer_lines[line.casefold()] += 1

    if not repeated_footer_lines:
        return [
            replace(page, text="\n".join(lines))
            for page, lines in zip(pages, normalized_pages)
        ]

    return [
        replace(
            page,
            text="\n".join(
                line
                for index, line in enumerate(lines)
                if not (
                    index >= len(lines) - 6
                    and repeated_footer_lines[line.casefold()] >= 2
                    and _looks_like_repeated_margin_furniture(line)
                )
            ),
        )
        for page, lines in zip(pages, normalized_pages)
    ]


def _coordinate_margin_furniture_lines(pages: list[PageText]) -> dict[int, set[str]]:
    """Return repeated margin lines proven by native block geometry.

    PDF text streams can interleave a visually bottom-aligned footer into the
    middle of body text.  Text order therefore cannot prove where a line was
    drawn, while the existing immutable blocks can.  A line is removable only
    when it has a conservative furniture shape, lies in a geometric margin,
    and repeats in the same margin on most selected pages.
    """

    if len(pages) < 2:
        return {}
    candidates_by_page: dict[int, list[tuple[str, str, str]]] = {}
    repeat_counts: Counter[tuple[str, str]] = Counter()
    for page in pages:
        page_candidates = _page_coordinate_margin_candidates(page)
        candidates_by_page[page.page_number] = page_candidates
        page_keys = {
            (zone, fingerprint)
            for zone, fingerprint, _line in page_candidates
        }
        for zone, fingerprint in page_keys:
            repeat_counts[(zone, fingerprint)] += 1
    minimum_repeats = max(2, ceil(len(pages) * 0.55))
    repeated = {
        key for key, count in repeat_counts.items() if count >= minimum_repeats
    }
    if not repeated:
        return {}
    return {
        page_number: {
            line
            for zone, fingerprint, line in candidates
            if (zone, fingerprint) in repeated
        }
        for page_number, candidates in candidates_by_page.items()
    }


def _strip_coordinate_furniture_fragment(line: str, proven_lines: set[str]) -> str:
    """Remove only a proven footer fragment from a possibly merged body line."""

    cleaned = line
    for fragment in sorted(proven_lines, key=len, reverse=True):
        if not fragment:
            continue
        cleaned = re.sub(_coordinate_fragment_pattern(fragment), " ", cleaned, flags=re.I)
    return normalize_line(cleaned)


def _uniquely_observed_furniture_fragments(
    source_lines: list[str],
    proven_lines: set[str],
) -> set[str]:
    """Keep only fragments with one text-stream occurrence on the page.

    Geometry proves that one block is furniture.  If the same literal also
    appears in body text, the text stream cannot identify which occurrence is
    the margin block, so both are conservatively retained for review.
    """

    page_text = "\n".join(source_lines)
    return {
        fragment
        for fragment in proven_lines
        if len(re.findall(_coordinate_fragment_pattern(fragment), page_text, flags=re.I)) == 1
    }


def _coordinate_fragment_pattern(fragment: str) -> str:
    """Return a line-local pattern for one exact coordinate-proven fragment."""

    horizontal_space = r"[^\S\n]+"
    return (
        rf"(?<![\w.])(?:\d{{1,3}}{horizontal_space})?{re.escape(fragment)}"
        rf"(?:{horizontal_space}\d{{1,3}})?(?!\w)"
    )


def _page_coordinate_margin_candidates(page: PageText) -> list[tuple[str, str, str]]:
    """Collect semantically plausible furniture from one page's outer bands."""

    text_blocks = [block for block in page.blocks if block.kind.value == "text"]
    if len(text_blocks) < 2 or page.page_bbox is None:
        return []
    _left, page_top, _right, page_bottom = page.page_bbox
    vertical_span = page_bottom - page_top
    if vertical_span < 100:
        return []
    top_limit = page_top + vertical_span * 0.12
    bottom_limit = page_top + vertical_span * 0.88
    candidates: list[tuple[str, str, str]] = []
    for block in text_blocks:
        line = normalize_line(block.text)
        if not _looks_like_repeated_margin_furniture(line):
            continue
        zone = "top" if block.bbox[1] <= top_limit else "bottom" if block.bbox[3] >= bottom_limit else ""
        if not zone:
            continue
        candidates.append((zone, _margin_furniture_fingerprint(line), line))
    return candidates


def _has_dense_line_number_gutter(lines: list[str]) -> bool:
    """Recognize an ambiguous standalone-number run without deleting it."""

    values = [
        int(line)
        for line in lines
        if re.fullmatch(r"\d{1,2}", line) and 1 <= int(line) <= 99
    ]
    if len(values) < 8:
        return False
    return len(set(values)) >= 8 and max(values) - min(values) >= 7  # 这里只停止标题猜测，不删除内容，因此宁可更早进入保守路径。


def _looks_like_repeated_margin_furniture(line: str) -> bool:
    """Recognize generic title, legal, page-counter, and status furniture."""

    candidate = normalize_line(line).casefold()
    if not 2 <= len(candidate) <= 220:
        return False
    if re.match(r"^(?:copyright\b|©|\(c\)\s*\d{4})", candidate):
        return True
    if "draft" in candidate and any(
        marker in candidate
        for marker in ("watermark", "not to be shared", "not for distribution", "publication", "approval")
    ):
        return True
    if re.fullmatch(r"(?:page\s*)?\d+(?:\s*(?:of|/)\s*\d+)?", candidate):
        return True
    if re.match(r"^(?:https?://|www\.)\S+", candidate):
        return True
    if re.fullmatch(r"with\s+.{1,48}\s+approval\.?", candidate):
        return True
    return (
        bool(re.search(r"\b(?:clause|chapter|section|part)\s+\d", candidate))
        and bool(re.search(r"\s[-–—|]\s", candidate))
        and not re.search(r"\b(?:shall|must|should|required|prohibited)\b", candidate)
    )


def _margin_furniture_fingerprint(line: str) -> str:
    """Group page-bearing variants while preserving internal clause numbers."""

    fingerprint = normalize_for_similarity(line)
    fingerprint = re.sub(
        r"(?<=[-/])0+(?=\d)",
        "",
        fingerprint,
    )  # 奇偶页常把同一运行标题中的编号抽成 06.0/6.0；只在页边指纹中合并。
    fingerprint = re.sub(
        r"\bpage\s*\d+(?:\s*(?:of|/)\s*\d+)?\b",
        "page #",
        fingerprint,
        flags=re.I,
    )
    if re.match(r"^\d+\s+", fingerprint):
        return re.sub(r"^\d+\s+", "", fingerprint)
    return re.sub(r"\s+\d+\s*$", "", fingerprint)


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

    dynamic_counts: Counter[tuple[str, str]] = Counter()
    for page in pages:
        unique_candidates = set(_page_margin_candidates(page.text))
        for zones, line in unique_candidates:
            if 2 <= len(line) <= 100 and _looks_like_dynamic_page_furniture(line):
                for margin_position in _margin_positions(zones):
                    dynamic_counts[(margin_position, _furniture_fingerprint(line))] += 1

    min_repeats = max(2, ceil(len(pages) * 0.55))
    repeated_dynamic = {
        line for line, count in dynamic_counts.items() if count >= min_repeats
    }
    if not repeated_dynamic:
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
                repeated_dynamic,
            )
        ]
        cleaned.append(PageText(page_number=page.page_number, text="\n".join(kept_lines)))
    return cleaned


def _remove_single_page_furniture(pages: list[PageText]) -> list[PageText]:
    """Preserve one-page text when repetition or geometric evidence is absent."""

    return pages


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
    repeated_dynamic: set[tuple[str, str]],
) -> bool:
    """Decide whether one extracted line is learned header/footer furniture."""

    if not zones:
        return False
    line = normalize_line(raw_line)
    if not _looks_like_dynamic_page_furniture(line):
        return False
    fingerprint = _furniture_fingerprint(line)
    return (
        ("top-margin" in zones and ("top", fingerprint) in repeated_dynamic)
        or ("bottom-margin" in zones and ("bottom", fingerprint) in repeated_dynamic)
    )


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
        r"(?i)^\s*page\s*\d+\s*(?:of|/|-)\s*\d+\s*$",
        r"(?i)^\s*page\s*\d+\s*$",
        r"^\s*第\s*\d+\s*页(?:\s*(?:/|共)\s*\d+\s*页?)?\s*$",
    )
    return any(re.fullmatch(pattern, candidate) for pattern in page_patterns)


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
    document_number_lines = [
        normalize_line(line)
        for page in pages
        for line in page.text.splitlines()
    ]
    document_has_ambiguous_number_run = _has_dense_line_number_gutter(
        document_number_lines
    )  # 页脚清理可能从单页删掉少数数字；整份文本仍能证明“不要猜标题”，但不能授权删除。
    for page in pages:
        raw_lines = page.text.splitlines()
        ambiguous_number_run = document_has_ambiguous_number_run or _has_dense_line_number_gutter(
            [normalize_line(line) for line in raw_lines]
        )  # 无坐标的 1..N 既可能是行号也可能是正文列表，只用于阻止标题猜测，绝不删除。
        merged_lines: list[str] = []
        index = 0
        while index < len(raw_lines):
            line = normalize_line(raw_lines[index])
            if not line:
                index += 1
                continue
            next_index = index + 1  # 只允许与视觉上紧邻的下一行合并，空行明确终止标题候选。
            next_line = (
                normalize_line(raw_lines[next_index])
                if next_index < len(raw_lines)
                else ""
            )  # 不越过段落边界寻找标题，避免把孤立章节引用吸附到下一段。
            previous_line = (
                normalize_line(raw_lines[index - 1])
                if index > 0
                else ""
            )  # PDF 常把 `Section` 与引用编号拆行，即使视觉段落没有空行也要保留上下文。
            if (
                next_line
                and _is_standalone_heading_marker(line)
                and _can_be_heading_title(next_line)
                and not (ambiguous_number_run and bool(re.fullmatch(r"\d{1,2}", line)))
                and not _line_ends_with_reference_introducer(previous_line)
            ):
                merged_lines.append(f"{line} {next_line}")
                index = next_index + 1  # 相邻两行已经消费，继续处理其后的正文。
                continue
            merged_lines.append(line)
            index += 1
        merged_pages.append(replace(page, text="\n".join(merged_lines)))
    return merged_pages


def _line_without_ambiguous_margin_number(
    line: str,
    sides: tuple[str, ...],
) -> str:
    """Remove one coordinate-proven edge-number candidate for heading detection only."""

    candidate = line
    if "left" in sides:
        candidate = re.sub(r"^\d{1,3}\s+", "", candidate, count=1)
    if "right" in sides:
        candidate = re.sub(r"\s+\d{1,3}$", "", candidate, count=1)
    return normalize_line(candidate)  # 调用方仍把未改写的原始行保存到正文，避免证据丢失。


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
        role=_section_role(open_section),
        page_bodies=tuple(
            (page_number, "\n".join(lines).strip())
            for page_number, lines in sorted(open_section.page_lines.items())
            if any(line.strip() for line in lines)
        ),
    )


def _section_role(open_section: _OpenSection) -> str:
    """Separate document publishing metadata from technical requirements."""

    if open_section.heading == "范围起始页前序内容":
        return "technical"
    if open_section.heading == "文档开头":
        return "document_metadata"
    if _DOCUMENT_METADATA_TITLE_RE.search(open_section.title):
        return "document_metadata"
    return "technical"


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
        body = "\n".join(
            line
            for raw_line in page.text.splitlines()
            if (line := normalize_line(raw_line))
        ).strip()  # 按页回退只规整空白，必须保留技术标识符和单位的原始大小写。
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
                page_bodies=((page.page_number, body),),
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


def _looks_like_forbidden_heading_candidate(
    candidate: str,
    number: str,
    title: str,
    kind: str,
) -> bool:
    """Reject units, formulas, footnotes, phone numbers, and table values."""

    normalized_title = normalize_line(title)  # 标题部分决定数字行是不是章节。
    normalized_candidate = normalize_line(candidate)  # 全行用于识别公式和脚注形态。
    if kind == "paren" and _looks_like_phone_or_footnote(candidate, normalized_title):
        return True
    if kind == "annex" and (
        _looks_like_appendix_sentence_continuation(normalized_title)
        or _looks_like_appendix_subreference_fragment(number, normalized_title)
    ):
        return True
    numeric_kind = kind in {"numeric", "annex_numeric", "numeric_letter"}  # 混合层级沿用技术碎片防误识别规则。
    if numeric_kind and _looks_like_scope_acronym_figure_label(normalized_title):
        return True
    if numeric_kind and _looks_like_unit_only_heading(normalized_title):
        return True
    if numeric_kind and _looks_like_axis_label_heading(normalized_title):
        return True
    if numeric_kind and _looks_like_sentence_fragment_heading(number, normalized_title):
        return True
    if numeric_kind and _looks_like_symbol_fragment_heading(number, normalized_title):
        if not (
            kind == "numeric_letter"
            and bool(re.fullmatch(r"[A-Z][A-Za-z]{1,3}", normalized_title))
        ):
            return True  # mixed 附录后的 Host/Loss 等短标题有强结构证据；单字母符号仍按碎片拒绝。
    if numeric_kind and _looks_like_formula_or_table_value_heading(number, normalized_title):
        return True
    if numeric_kind and _looks_like_footnote_sentence_heading(number, normalized_title):
        return True
    if numeric_kind and _looks_like_address_heading(number, normalized_title):
        return True
    if numeric_kind and _looks_like_margin_line_heading(normalized_candidate):
        return True
    return False


def _looks_like_appendix_sentence_continuation(title: str) -> bool:
    """Reject a wrapped ``Appendix N.C.3, or ...`` reference masquerading as a heading."""

    return bool(
        re.match(
            r"(?i)^[A-Z]?\.\d+(?:\.\d+)*,\s*(?:or|and|the|a|an|if|when|with)\b",
            title,
        )
    )


def _looks_like_appendix_subreference_fragment(number: str, title: str) -> bool:
    """Reject a split ``Appendix 16.D`` cross-reference with no actual heading title."""

    return bool(re.search(r"\d$", number) and re.fullmatch(r"[A-Z]", title))


def _looks_like_scope_acronym_figure_label(title: str) -> bool:
    """Reject a chart tick followed by a scope/acronym label, not a chapter title."""

    return bool(re.fullmatch(r"Scope\s+[A-Z]{2,8}", title))


def _looks_like_phone_or_footnote(candidate: str, title: str) -> bool:
    """Return True for ``(408)309...`` and similar non-heading fragments."""

    if re.match(r"^\(\d{3,}\)\s*\d", candidate):
        return True
    if title and re.fullmatch(r"[\d\s().+-]+", title):
        return True
    prose = title.lstrip(".． ")
    if (
        len(prose) >= 30
        and prose.endswith((".", "。"))
        and re.match(r"(?i)^(?:the|a|an|this|that)\b", prose)
        and re.search(r"(?i)\b(?:is|are|shall|should|must|will|can|be)\b", prose)
    ):
        return True  # 括号编号后的完整说明句是脚注/步骤正文，不是章节标题。
    return False


def _looks_like_unit_only_heading(title: str) -> bool:
    """Return True when a numeric heading title is only a measurement unit."""

    if not title:
        return False
    unit_pattern = (
        r"(?i)^(?:ui|uipp|uirms|mv|v|db|dbc|ghz|mhz|hz|ps|ns|us|ms|"
        r"ohm|ω|ff|pf|ph|mm|ns/mm|1/mm|v2/ghz|gb/s|gsym/s|gt/s|%)$"
    )
    return bool(re.fullmatch(unit_pattern, title.strip()))


def _looks_like_axis_label_heading(title: str) -> bool:
    """Return True when a chart axis label was merged with a preceding tick value."""

    if not title:
        return False
    return bool(re.fullmatch(r"(?i)(?:frequency|amplitude|loss|jitter)\s*\([^)]+\)", title.strip()))


def _looks_like_symbol_fragment_heading(number: str, title: str) -> bool:
    """Return True for symbol/value fragments misread as numeric headings."""

    candidate = normalize_line(title)
    if "." in number and re.fullmatch(r"(?i)[a-z]{1,4}\)?", candidate):
        return True  # 带字母后缀的 dotted identifier 残片不是章节标题。
    if not number.isdigit():
        return False
    if re.fullmatch(r"(?i)ui\s+x", candidate):
        return True  # `5 UI X` 是图轴/表格值残片，不是章节标题。
    if re.fullmatch(r"(?i)\d+[a-z]{1,4}\)?", candidate):
        return True  # 被拆开的数字+字母 identifier 后缀也不是章节标题。
    symbol_piece = r"\d+(?:\.\d+)?[a-z][a-z0-9]*"
    symbol_tail = rf"{symbol_piece}(?:\s+(?:{symbol_piece}|rms\d*|drms\d*|j|\d{{1,3}})){{0,4}}"
    return bool(re.fullmatch(symbol_tail, candidate, flags=re.I))


def _looks_like_sentence_fragment_heading(number: str, title: str) -> bool:
    """Return True when a long prose sentence was misread as a dotted heading."""

    candidate = normalize_line(title)
    if "." not in number:
        return False
    if len(candidate) < 55:
        return False
    return bool(
        re.match(r"(?i)^(?:the|a|an|this|that)\b", candidate)
        and re.search(r"(?i)\b(?:is|are|shall|should|must|will|can|be)\b", candidate)
    )


def _looks_like_formula_or_table_value_heading(number: str, title: str) -> bool:
    """Return True when a numeric candidate is really a formula or table value."""

    joined = f"{number} {title}".strip()  # 部分公式被正则拆成 number/title 两段，需要拼回判断。
    if not title:
        return True
    if re.fullmatch(r"[A-Za-z]", title):
        return True  # 数字后只有一个符号字母时，更像表格值/公式残片，不是章节标题。
    if re.fullmatch(
        r"(?i)[a-z][a-z0-9_]{0,7}\s*[-—]?\s*[+-]?\d+(?:\.\d+)?\s*"
        r"(?:ui|uipp|uirms|mv|v|db|ghz|mhz|ps|ns|us|ms)",
        title,
    ):
        return True  # ``03 JH - 0.118 UI`` 是被拆开的表格符号和值，不是章节标题。
    formula_pattern = r"(?i)(?:<=|>=|[=×*≤≥])|\b\d+(?:\.\d+)?\s*x\s*\d|(?:\b10\s*[+-]?\d\b)"  # 比较符同样证明这是公式/表格值；保留 GT/s 等普通单位斜杠。
    if re.search(formula_pattern, joined) and re.search(r"\d", joined):
        return True
    if re.fullmatch(r"(?i)[+-]?\d+(?:\.\d+)?\s*(?:ui|uipp|uirms|mv|v|db|ghz|mhz|ps|ns|us|ms|ω|ohm|ff|pf|mm)", joined):
        return True
    if re.match(r"(?i)^(?:min|max|typ|value|units?|symbol|parameter)\b", title):
        return True
    if "." in number and re.search(
        r"\b[A-Z][A-Za-z0-9]*_[A-Za-z0-9_]+\s+[+-]?\d+(?:\.\d+)?(?:\s+[-—])?\s*$",
        title,
    ):
        return True  # 深层编号后的“符号 + 数值”尾部是参数表行，不是章节标题。
    return False


def _has_inconsistent_numeric_parent(
    heading: HeadingInfo,
    heading_stack: list[HeadingInfo],
) -> bool:
    """Reject a dotted candidate whose numeric prefix contradicts its active parent."""

    if "." not in heading.number or heading.level <= 1:
        return False
    expected_parent_number = heading.number.rsplit(".", 1)[0]
    parent = next(
        (item for item in reversed(heading_stack) if item.level == heading.level - 1),
        None,
    )
    if parent is None or not parent.number:
        return False  # 选定页段可能从深层条款开始，没有父上下文时不能凭空拒绝。
    parent_number = re.sub(
        r"(?i)^(?:annex|appendix)\s+",
        "",
        parent.number,
    )
    return parent_number.casefold() != expected_parent_number.casefold()


def _looks_like_address_heading(number: str, title: str) -> bool:
    """Return True for postal addresses that start with a street number."""

    if not number.isdigit() or int(number) < 100:
        return False
    return bool(
        re.search(
            r"(?i)\b(?:pkwy|parkway|blvd|boulevard|suite|suit|street|st\.?|road|rd\.?|drive|dr\.?|avenue|ave\.?|court|ct\.?)\b",
            title,
        )
    )  # Front-matter postal addresses must not become numbered technical sections.


def _looks_like_footnote_sentence_heading(number: str, title: str) -> bool:
    """Return True for numbered footnote sentences misread as headings."""

    if not number.isdigit() or "." in number:
        return False
    if re.fullmatch(r"(?i)notes?[:.]?", title):
        return True
    if not title.endswith("."):
        return False
    if re.search(r"(?i)\bis\s+(?:defined|measured|specified|described)\b", title):
        return True
    return bool(
        re.match(
            r"(?i)^(?:measured|defined|specified|see|where|for|when|values?)\b",
            title,
        )
    )  # 表格脚注常以完整句出现，不能拆成章节。


def _looks_like_margin_line_heading(candidate: str) -> bool:
    """Return True for extracted line-number runs mistaken as headings."""

    numbers = [int(value) for value in re.findall(r"\d+", candidate)]  # 页边行号通常是一串 1~49 数字。
    if len(numbers) < 10:
        return False
    if min(numbers) < 1 or max(numbers) > 49:
        return False
    non_numbers = re.sub(r"[\d\s]+", "", candidate)
    return len(non_numbers) <= 8


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
    if re.match(r"^\d+\s*[、,，]\s*\d", candidate):
        return True  # `202、234、383-387` 是编号/页码枚举，不是第 202 章。
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

    numeric_letter = re.fullmatch(
        r"(\d+\.[A-Z](?:\.\d+){0,4})[.)．]?",
        line,
    )  # 31.B 也可能和标题分落两行，必须保留完整混合编号。
    if numeric_letter:
        return True  # 字母附录标记只有与紧邻标题行组合后才会成为章节。
    numeric = re.fullmatch(r"(\d+(?:\.\d+){0,5})[.)．]?", line)
    if numeric:
        number = numeric.group(1)
        if "." not in number and len(number) == 4 and number.startswith(("19", "20")):
            return False
        return True
    return bool(re.fullmatch(rf"第\s*[{_CHINESE_NUM}]+\s*[章节]", line))


def _line_ends_with_reference_introducer(line: str) -> bool:
    """Return whether the next standalone number completes an inline reference."""

    candidate = normalize_line(line)  # 只检查编号紧邻的前一视觉行，不跨段推测语义。
    if not candidate:
        return False  # 页首或空段没有引用引导词，仍允许真实拆行标题合并。
    return bool(
        re.search(
            r"(?i)\b(?:sections?|sec\.|clauses?|chapters?|"
            r"appendix|appendices|annex|annexes|figure|fig\.|table|equation)\s*[:：]?\s*$"
            r"|(?:第|章节|条款|附录|图|表|公式)\s*[:：]?\s*$",
            candidate,
        )
    )  # 词尾引用标记是比下一句大小写更强的证据，编号应保留在正文而不是生成章节。


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

    Technical specifications often have real deep sections such as
    ``2.13.2 Overview...`` followed by numbered procedure steps like
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
    procedure_step_numbers: tuple[int, ...] = (),
) -> bool:
    """Keep numbered bullets inside a proven procedure or nested clause."""

    # 没有父章节时，整数编号仍可能是真正的顶层章节。
    if not heading_stack:
        return False
    # 只有纯整数编号才按列表项处理；32.2 这类 dotted 编号仍是章节候选。
    if not heading.number.isdigit() or "." in heading.number:
        return False
    procedure_context = _heading_stack_proves_procedure_context(heading_stack)  # Test Procedure 是比标题大小写更强的步骤证据。
    nested_context = _heading_stack_proves_nested_list_context(heading_stack)  # 普通嵌套条款仍需结合句式判断。
    # 章节标题中的动词并不自动证明列表；父路径需明确是流程，或当前已位于 32.1/31.B 等嵌套条款。
    if not (procedure_context or nested_context):
        return False
    if procedure_context:
        if not _looks_like_procedure_step_title(heading.title):
            return False  # Results 等名词性真章节仍可结束流程。
        if _is_next_top_level_integer_heading(heading, heading_stack):
            return _continues_procedure_step_chain(heading, procedure_step_numbers)
        return True  # 显式流程下的其他动词型整数行仍保守按步骤保留。
    # 非流程嵌套条款中，连续顶层编号是强结构证据；即使标题是 Configure，也应允许从 1.x 回到第 2 章。
    if _is_next_top_level_integer_heading(heading, heading_stack):
        return False
    # 短名词性标题即使在父章节后出现，也更可能是真正的顶层章节，如 ``2 Use Cases``。
    if _looks_like_real_integer_heading_after_deep_context(heading):
        return False
    # 动词或 shall 开头的标题通常是要求/步骤正文，而不是“第 6 章”。
    return _looks_like_procedure_step_title(heading.title)


def _is_sequential_numbered_prose_item(
    heading: HeadingInfo,
    heading_stack: list[HeadingInfo],
    prose_step_numbers: tuple[int, ...] = (),
    *,
    following_context_proves_list: bool = False,
    following_context_proves_chapter_body: bool = False,
) -> bool:
    """Keep a structurally proven 1..N prose list inside its numbered parent.

    This is intentionally domain-neutral.  A sentence-like integer line may
    start a list at 1, or at an out-of-sequence number when a selected page
    window begins mid-list.  The active chapter's exact next number remains a
    real heading unless an already proven list chain reaches it.
    """

    if not heading_stack or not heading.number.isdigit() or "." in heading.number:
        return False
    if not any(item.number for item in heading_stack):
        return False  # 无编号父层时，候选更可能是文档真正的第一章。
    if following_context_proves_chapter_body:
        return False  # 普通正文或表格可能属于真章节；歧义时失败可见，绝不为减少假章节而吞掉内容。
    number = int(heading.number)
    if prose_step_numbers:
        return (
            number == prose_step_numbers[-1] + 1
            and _looks_like_numbered_prose_sentence(heading.title, chain_started=True)
            and (
                following_context_proves_list
                or not _is_next_top_level_integer_heading(heading, heading_stack)
                or _prose_list_began_with_current_top_level(
                    prose_step_numbers,
                    heading_stack,
                )
            )
        )
    can_start = number == 1 or not _is_next_top_level_integer_heading(
        heading,
        heading_stack,
    )
    return can_start and _looks_like_numbered_prose_sentence(
        heading.title,
        chain_started=False,
    ) and following_context_proves_list


def _prose_list_began_with_current_top_level(
    prose_step_numbers: tuple[int, ...],
    heading_stack: list[HeadingInfo],
) -> bool:
    """Return whether a proven list restarted at its active chapter number."""

    top_level = _active_integer_chapter_heading(heading_stack)
    return bool(
        prose_step_numbers
        and top_level
        and prose_step_numbers[0] == int(top_level.number)
    )


def _following_context_proves_numbered_prose_item(
    following_lines: list[tuple[str, tuple[str, ...]]],
    heading: HeadingInfo,
    heading_stack: list[HeadingInfo],
    *,
    chain_started: bool,
) -> bool:
    """Require local structural evidence before demoting a heading to prose.

    A sentence-shaped integer line is inherently ambiguous: it can be a list
    item or a real chapter whose title happens to be a sentence.  We demote it
    only when the same page proves a consecutive next item, or when an already
    proven chain is immediately followed by an unmistakable chapter boundary.
    A duplicate of the active top-level number is also a strong local list
    signal (for example chapter 1 followed by item ``1. ...``).
    """

    if not heading.number.isdigit():
        return False
    number = int(heading.number)
    if _is_current_top_level_integer_heading(heading, heading_stack):
        return True
    current_sentence_closed = _numbered_prose_sentence_is_closed(heading.title)
    for raw_following_line, ambiguous_line_number_sides in following_lines:
        following_line = normalize_line(raw_following_line)
        if not following_line:
            continue
        if _is_serialized_table_evidence_line(following_line):
            if _is_next_top_level_integer_heading(heading, heading_stack):
                return False  # 下一主章后的表格是该章正文证据，不能跨过它寻找后续编号来吞掉真章节。
            continue  # 其他坐标表格在抽取文本中统一追加，不能伪装成列表项之间的视觉正文。
        heading_candidate = _line_without_ambiguous_margin_number(
            following_line,
            ambiguous_line_number_sides,
        )
        following_heading = detect_heading(heading_candidate)
        if following_heading is None:
            if current_sentence_closed:
                return False  # 正常正文紧随其后时，当前编号句归属一个真实章节。
            current_sentence_closed = _numbered_prose_sentence_is_closed(
                following_line
            )
            continue  # 未闭合的长列表项允许有一个或多个视觉软换行。
        if not following_heading.number.isdigit() or "." in following_heading.number:
            return False
        following_number = int(following_heading.number)
        if (
            following_number == number + 1
            and _looks_like_numbered_prose_sentence(
                following_heading.title,
                chain_started=True,
            )
        ):
            return True
        return bool(
            chain_started
            and (
                following_number == number
                or _is_next_top_level_integer_heading(
                    following_heading,
                    heading_stack,
                )
            )
        )  # 已证明列表的末项只能由紧邻的同号/下一主章边界收口。
    return False


def _following_context_proves_numbered_chapter_body(
    following_lines: list[tuple[str, tuple[str, ...]]],
    heading: HeadingInfo,
    heading_stack: list[HeadingInfo],
    *,
    chain_started: bool,
) -> bool:
    """Return whether following prose/table evidence belongs to this chapter.

    A following ordinary paragraph is ambiguous without typography, so it must
    keep the numbered candidate visible.  The sole exception is an
    out-of-sequence list item whose paragraph is followed by the active
    parent's real next chapter; that boundary proves the paragraph stayed in
    the parent rather than opening the list item as a chapter.
    """

    if not heading.number.isdigit():
        return False
    number = int(heading.number)
    current_sentence_closed = _numbered_prose_sentence_is_closed(heading.title)
    heading_started_closed = current_sentence_closed
    saw_body_evidence = False
    saw_plain_body_evidence = False
    for raw_following_line, ambiguous_line_number_sides in following_lines:
        following_line = normalize_line(raw_following_line)
        if not following_line:
            continue
        if _is_serialized_table_evidence_line(following_line):
            if (
                chain_started
                and not heading_started_closed
                and current_sentence_closed
                and not saw_plain_body_evidence
            ):
                return False  # 已证明链的换行末项后紧接父级表格，表格不属于一个新章节。
            saw_body_evidence = True
            continue
        heading_candidate = _line_without_ambiguous_margin_number(
            following_line,
            ambiguous_line_number_sides,
        )
        following_heading = detect_heading(heading_candidate)
        if following_heading is None:
            if current_sentence_closed:
                saw_body_evidence = True
                saw_plain_body_evidence = True
            else:
                current_sentence_closed = _numbered_prose_sentence_is_closed(
                    following_line
                )
            continue
        if not saw_body_evidence:
            return False
        if following_heading.number.isdigit() and "." not in following_heading.number:
            following_number = int(following_heading.number)
            if (
                following_number == number + 1
                and _looks_like_numbered_prose_sentence(
                    following_heading.title,
                    chain_started=True,
                )
            ):
                return False  # 表格/软换行之后仍出现紧邻列表项时，不能把前一项误证成章节。
            if (
                _is_next_top_level_integer_heading(following_heading, heading_stack)
                and not _is_next_top_level_integer_heading(heading, heading_stack)
            ):
                return False  # 页窗中段列表后的父级下一章证明其间普通段落仍属于父级。
        if _heading_belongs_to_active_integer_chapter(
            following_heading,
            heading_stack,
        ):
            return False  # 后续 6.1/17.1 等父级子条款证明其间总结段仍属于父级列表。
        return True
    if chain_started and not heading_started_closed and not saw_plain_body_evidence:
        # 已证明的编号链末项常因版面换行拆成两行，随后才出现父章节的表格。
        # 这种尾行只是列表句的续行；整张表不能反向把该列表项“证明”为新章节。
        return False
    return saw_body_evidence


def _numbered_prose_following_lines(
    pages: list[PageText],
    page_index: int,
    line_index: int,
) -> list[tuple[str, tuple[str, ...]]]:
    """Read forward until the next heading or a conservative hard line cap.

    The bound is semantic rather than a fixed number of pages: a sentence and
    its parent-level summary may wrap across sparse pages before a descendant
    heading proves their ownership.  The hard line cap prevents pathological
    PDFs from causing unbounded work and fails visible when no local proof is
    found.
    """

    following: list[tuple[str, tuple[str, ...]]] = []
    nonempty_count = 0
    for following_page_index in range(page_index, len(pages)):
        page = pages[following_page_index]
        page_lines = page.text.splitlines()
        start = line_index + 1 if following_page_index == page_index else 0
        for line in page_lines[start:]:
            following.append((line, page.ambiguous_line_number_sides))
            normalized = normalize_line(line)
            if not normalized:
                continue
            nonempty_count += 1
            if nonempty_count >= 256:
                return following
            if _is_serialized_table_evidence_line(normalized):
                continue
            heading_candidate = _line_without_ambiguous_margin_number(
                normalized,
                page.ambiguous_line_number_sides,
            )
            if detect_heading(heading_candidate) is not None:
                return following
    return following


def _numbered_prose_body_candidate(line: str) -> HeadingInfo | None:
    """Recover only an explicit integer sentence already forced into body text."""

    match = re.match(r"^(\d{1,3})[.)．]\s+(.{1,240})$", compact_inline(line))
    if match is None:
        return None
    return HeadingInfo(
        raw=compact_inline(line),
        number=match.group(1),
        title=match.group(2),
        level=1,
    )


def _is_serialized_table_evidence_line(line: str) -> bool:
    """Return True for the extractor's explicit coordinate-table marker."""

    return normalize_line(line).startswith("表格行:")


def _is_current_top_level_integer_heading(
    heading: HeadingInfo,
    heading_stack: list[HeadingInfo],
) -> bool:
    """Return True when a candidate repeats the active top-level number."""

    top_level = _active_integer_chapter_heading(heading_stack)
    return bool(top_level and int(heading.number) == int(top_level.number))


def _looks_like_numbered_prose_sentence(title: str, *, chain_started: bool) -> bool:
    """Recognize sentence structure without technical-domain vocabulary."""

    normalized = normalize_line(title)
    if not normalized:
        return False
    sentence_end = bool(re.search(r"[.!?;。！？；]$", normalized))
    internal_clause = bool(re.search(r"[,.!?;:，。！？；：]", normalized))
    if chain_started:
        return sentence_end or len(normalized) >= 35
    return (sentence_end and len(normalized) >= 10) or len(normalized) >= 60 or (
        len(normalized) >= 45 and internal_clause
    )


def _numbered_prose_sentence_is_closed(value: str) -> bool:
    """Return whether a list sentence has visible terminal punctuation."""

    return bool(re.search(r"[.!?;。！？；]$", normalize_line(value)))


def _is_next_top_level_integer_heading(
    heading: HeadingInfo,
    heading_stack: list[HeadingInfo],
) -> bool:
    """Return True when an integer candidate continues the active top-level sequence."""

    top_level = _active_integer_chapter_heading(heading_stack)
    if top_level is None:
        return False
    return int(heading.number) == int(top_level.number) + 1  # 1.x 后的 2 章优先于列表措辞启发式。


def _active_integer_chapter_heading(
    heading_stack: list[HeadingInfo],
) -> HeadingInfo | None:
    """Return the active integer chapter, including one nested under Part/Annex."""

    return next((item for item in heading_stack if item.number.isdigit()), None)


def _heading_belongs_to_active_integer_chapter(
    heading: HeadingInfo,
    heading_stack: list[HeadingInfo],
) -> bool:
    """Return whether a dotted heading is a descendant of the active chapter."""

    active = _active_integer_chapter_heading(heading_stack)
    return bool(active and heading.number.startswith(f"{active.number}."))


def _continues_procedure_step_chain(
    heading: HeadingInfo,
    procedure_step_numbers: tuple[int, ...],
) -> bool:
    """Require a visible 1-based step chain before hiding a next chapter number."""

    return bool(
        procedure_step_numbers
        and procedure_step_numbers[0] == 1
        and int(heading.number) == procedure_step_numbers[-1] + 1
    )  # 只有已见 1, 2... 步骤链才能压过同样合理的顶层 1→2 章节序列。


def _heading_stack_proves_nested_list_context(heading_stack: list[HeadingInfo]) -> bool:
    """Return True below a dotted or mixed-numbered subclause such as 32.1 or 31.B."""

    return any(
        item.level >= 2 and bool(re.search(r"\d[.]", canonical_number_identity(item.number)))
        for item in heading_stack
    )  # 嵌套条款下突然重启为 1..N，且句式像要求/脚注时，更可能是正文列表。


def _heading_stack_proves_procedure_context(heading_stack: list[HeadingInfo]) -> bool:
    """Return True only when an active heading explicitly names a procedure context."""

    context_titles = " ".join(
        normalize_line(item.title) for item in heading_stack if item.title
    )  # 父路径中的 Test Procedure 可覆盖其下的 Calibration 等普通子标题。
    if not context_titles:
        return False  # 只有编号而没有语义标题时，不能凭动词猜测步骤上下文。
    return bool(
        re.search(
            r"(?i)\b(?:procedures?|steps?|instructions?|methods?)\b|步骤|程序|流程|操作方法|测试方法",
            context_titles,
        )
    )  # 明示流程词是压制整数步骤标题所需的最小上下文证据。


def _is_opening_range_body_integer(
    heading: HeadingInfo,
    saw_heading: bool,
    opening_label: str,
) -> bool:
    """Keep mid-procedure range starts grouped as carry-over body text.

    When a user selects a page window that starts inside an existing technical
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

    Under deep technical sections, integer-numbered lines are often
    procedure steps than new chapters. A missed step is worse than a conservative
    parent section, so only short titles whose capitalization visibly marks a
    heading are allowed to break out. Domain vocabulary must not affect this
    decision.
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
        "reset",
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
