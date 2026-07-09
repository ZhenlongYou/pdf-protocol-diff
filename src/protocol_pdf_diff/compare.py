"""Section matching and difference summarization.

The comparison is designed for practical protocol review rather than formal
legal redlining. It first uses stable section numbers when possible, then falls
back to text similarity for renamed or renumbered sections. The report should be
treated as a review accelerator: important changes are surfaced with page and
section context, but final sign-off should still inspect the source PDFs.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .models import (
    DiffOptions,
    DiffResult,
    ExtractionResult,
    Section,
    SectionChange,
    SnippetPair,
    TableVisual,
)
from .pdf_extract import extract_pdf_text
from .sectioning import section_document
from .text_utils import (
    canonicalize_chinese_number_expressions,
    canonicalize_number_word_token,
    canonicalize_number_word_tokens,
    compact_inline,
    normalize_for_similarity,
    normalize_line,
    remove_draft_watermark_letter_artifacts,
)


@dataclass(frozen=True)
class _DeltaCandidate:
    """One reportable snippet before max-snippet limiting is applied."""

    kind: str
    order: int
    priority: int
    text: str = ""
    pair: SnippetPair | None = None


@dataclass(frozen=True)
class _PendingDeltaCandidate:
    """One snippet candidate before report ordering and priority are assigned."""

    kind: str
    priority_values: tuple[str, ...]
    text: str = ""
    pair: SnippetPair | None = None


def run_diff(old_pdf: str | Path, new_pdf: str | Path, options: DiffOptions) -> DiffResult:
    """Run the complete extraction, sectioning, and comparison pipeline."""

    old_extraction = extract_pdf_text(
        old_pdf,
        start_page=options.old_start_page,
        end_page=options.old_end_page,
    )
    new_extraction = extract_pdf_text(
        new_pdf,
        start_page=options.new_start_page,
        end_page=options.new_end_page,
    )
    return compare_extractions(old_extraction, new_extraction, options)


def compare_extractions(
    old_extraction: ExtractionResult,
    new_extraction: ExtractionResult,
    options: DiffOptions,
) -> DiffResult:
    """Compare two already-extracted PDFs.

    This entry point is useful for tests and future OCR integrations, where text
    may come from a different extractor but should still use the same section
    matching and report generation.
    """

    old_sections = section_document(old_extraction)
    new_sections = section_document(new_extraction)
    old_table_visuals = _table_visuals_with_text_fallbacks(old_extraction.table_visuals, old_extraction.pages)
    new_table_visuals = _table_visuals_with_text_fallbacks(new_extraction.table_visuals, new_extraction.pages)
    covered_table_unit_keys = _covered_table_visual_row_keys(  # 只隐藏已经被表格截图/结构化摘要覆盖的表格行。
        old_table_visuals,
        new_table_visuals,
    )
    changes = compare_sections(old_sections, new_sections, options, suppressed_table_unit_keys=covered_table_unit_keys)
    warnings = list(old_extraction.warnings) + list(new_extraction.warnings)
    changes, suppressed_noise_count = _suppress_global_noise_changes(changes)
    if suppressed_noise_count:
        warnings.append(
            f"已隐藏 {suppressed_noise_count} 条全局重复页眉页脚、DRAFT、版权或行号噪声差异。"
        )
    if not old_sections:
        warnings.append(f"{old_extraction.pdf_path.name}: 未识别到可比较文本段落。")
    if not new_sections:
        warnings.append(f"{new_extraction.pdf_path.name}: 未识别到可比较文本段落。")
    return DiffResult(
        old_pdf=old_extraction.pdf_path,
        new_pdf=new_extraction.pdf_path,
        old_sections=old_sections,
        new_sections=new_sections,
        changes=changes,
        warnings=warnings,
        old_total_pages=_source_page_count(old_extraction),
        new_total_pages=_source_page_count(new_extraction),
        old_selected_start_page=_selected_start_page(old_extraction),
        old_selected_end_page=_selected_end_page(old_extraction),
        new_selected_start_page=_selected_start_page(new_extraction),
        new_selected_end_page=_selected_end_page(new_extraction),
        old_table_visuals=old_table_visuals,
        new_table_visuals=new_table_visuals,
    )


def _covered_table_visual_row_keys(*table_groups: list[TableVisual]) -> set[str]:
    """Return normalized structured table rows already shown in visual summaries."""

    keys: set[str] = set()  # 保存可从下方截图+结构化摘要复核的表格行身份。
    for tables in table_groups:
        for table in tables:
            for row_text in table.row_texts:
                if _is_table_review_unit(row_text):
                    keys.add(_review_unit_key(row_text))  # 使用比较层统一 key，避免空白和大小写差异导致漏匹配。
    return keys


def _table_visuals_with_text_fallbacks(table_visuals: list[TableVisual], pages: list[PageText]) -> list[TableVisual]:
    """Add no-image structured summaries for table rows not covered by screenshots."""

    visuals = list(table_visuals)  # 保留真实截图表格，新增兜底只补未覆盖行。
    covered_keys = _covered_table_visual_row_keys(visuals)  # 已经在截图摘要里的行不重复生成兜底。
    next_table_number = max((table.table_number for table in visuals), default=0) + 1  # 兜底表号接在真实表之后。
    for page in pages:
        rows = [
            compact_inline(line)
            for line in page.text.splitlines()
            if _is_table_review_unit(line) and _review_unit_key(line) not in covered_keys
        ]  # 只收集结构化表格行，普通正文不会进入兜底摘要。
        if not rows:
            continue
        visuals.append(
            TableVisual(
                page_number=page.page_number,
                table_number=next_table_number,
                title="结构化表格文字摘要",
                bbox=(0.0, 0.0, 0.0, 0.0),
                image_data_uri="",
                row_texts=rows,
                grid_summary="未生成截图：使用结构化表格行摘要。",
            )
        )  # 没有截图时仍进入报告的表格摘要区，而不是正文差异卡片。
        covered_keys.update(_review_unit_key(row) for row in rows)
        next_table_number += 1
    return visuals


def compare_sections(
    old_sections: list[Section],
    new_sections: list[Section],
    options: DiffOptions,
    *,
    suppressed_table_unit_keys: set[str] | None = None,
) -> list[SectionChange]:
    """Match old/new sections and classify section-level changes."""

    table_unit_keys = suppressed_table_unit_keys or set()  # 表格行统一由表格摘要区承载，正文 diff 不再展示内部表格行。
    matches = _match_sections(old_sections, new_sections, options)
    changes: list[SectionChange] = []
    for old_index, new_index, similarity in matches:
        old_section = old_sections[old_index] if old_index is not None else None
        new_section = new_sections[new_index] if new_index is not None else None
        if old_section and new_section:
            if _sections_effectively_unchanged(old_section, new_section, options):
                if options.include_unchanged_sections:
                    changes.append(
                        SectionChange(
                            change_type="unchanged",
                            old_section=old_section,
                            new_section=new_section,
                            similarity=similarity,
                        )
                    )
                continue
            heading_pair = (
                SnippetPair(
                    old=f"章节标题: {old_section.heading}",
                    new=f"章节标题: {new_section.heading}",
                )
                if _section_heading_changed(old_section, new_section)
                else None
            )
            added, removed, replaced, omitted_count = _summarize_text_delta(
                old_section.body,
                new_section.body,
                max_snippets=options.max_snippets_per_section,
                leading_replacement=heading_pair,
                suppressed_table_unit_keys=table_unit_keys,
            )
            if not added and not removed and not replaced and omitted_count == 0:
                if options.include_unchanged_sections:
                    changes.append(
                        SectionChange(
                            change_type="unchanged",
                            old_section=old_section,
                            new_section=new_section,
                            similarity=similarity,
                        )
                    )
                continue
            changes.append(
                SectionChange(
                    change_type="modified",
                    old_section=old_section,
                    new_section=new_section,
                    similarity=similarity,
                    added_snippets=added,
                    removed_snippets=removed,
                    replaced_snippets=replaced,
                    omitted_snippet_count=omitted_count,
                )
            )
        elif new_section:
            added_snippets, omitted_count = _first_units(
                new_section.body,
                options.max_snippets_per_section,
                suppressed_table_unit_keys=table_unit_keys,
            )
            changes.append(
                SectionChange(
                    change_type="added",
                    old_section=None,
                    new_section=new_section,
                    similarity=0.0,
                    added_snippets=added_snippets,
                    omitted_snippet_count=omitted_count,
                )
            )
        elif old_section:
            removed_snippets, omitted_count = _first_units(
                old_section.body,
                options.max_snippets_per_section,
                suppressed_table_unit_keys=table_unit_keys,
            )
            changes.append(
                SectionChange(
                    change_type="deleted",
                    old_section=old_section,
                    new_section=None,
                    similarity=0.0,
                    removed_snippets=removed_snippets,
                    omitted_snippet_count=omitted_count,
                )
            )

    return sorted(changes, key=_change_sort_key)


def _match_sections(
    old_sections: list[Section],
    new_sections: list[Section],
    options: DiffOptions,
) -> list[tuple[int | None, int | None, float]]:
    """Pair old/new sections using exact keys first, then global best scores.

    The second pass builds all viable fallback candidates and assigns the
    strongest pairs first. That is more conservative than matching each new
    section greedily in document order, especially when protocols contain many
    repeated boilerplate clauses.
    """

    exact_old_by_key: dict[str, list[int]] = {}
    for old_index, old_section in enumerate(old_sections):
        exact_old_by_key.setdefault(old_section.identity_key, []).append(old_index)

    matched_old: set[int] = set()
    matched_new: set[int] = set()
    matches: list[tuple[int | None, int | None, float]] = []

    for new_index, new_section in enumerate(new_sections):
        exact_candidates = exact_old_by_key.get(new_section.identity_key, [])
        exact_match = next((idx for idx in exact_candidates if idx not in matched_old), None)
        if exact_match is not None:
            matched_old.add(exact_match)
            similarity = _section_similarity(
                old_sections[exact_match].comparable_text,
                new_section.comparable_text,
            )
            matches.append((exact_match, new_index, similarity))
            matched_new.add(new_index)

    fallback_candidates: list[tuple[float, int, int]] = []
    for new_index, new_section in enumerate(new_sections):
        if new_index in matched_new:
            continue
        for old_index, old_section in enumerate(old_sections):
            if old_index in matched_old:
                continue
            score = _section_match_score(old_section, new_section)
            if score >= options.min_section_match_similarity:
                fallback_candidates.append((score, old_index, new_index))

    for score, old_index, new_index in sorted(fallback_candidates, reverse=True):
        if old_index in matched_old or new_index in matched_new:
            continue
        matched_old.add(old_index)
        matched_new.add(new_index)
        similarity = _section_similarity(
            old_sections[old_index].comparable_text,
            new_sections[new_index].comparable_text,
        )
        matches.append((old_index, new_index, min(score, similarity)))

    for new_index, _new_section in enumerate(new_sections):
        if new_index not in matched_new:
            matches.append((None, new_index, 0.0))

    for old_index, _old_section in enumerate(old_sections):
        if old_index not in matched_old:
            matches.append((old_index, None, 0.0))
    return matches


def _section_match_score(old_section: Section, new_section: Section) -> float:
    """Score likely identity for renamed or renumbered sections."""

    if _both_page_fallback_sections(old_section, new_section):
        return _similarity(old_section.body, new_section.body)

    title_score = _review_similarity(old_section.title, new_section.title)
    location_score = _review_similarity(old_section.location, new_section.location)
    text_score = _section_similarity(old_section.comparable_text, new_section.comparable_text)
    return max(title_score * 0.85 + text_score * 0.15, location_score * 0.4 + text_score * 0.6)


_SECTION_MATCH_SAMPLE_CHARS = 1200  # 长章节匹配采样代表性文本，避免反复对整章做昂贵相似度计算。
_LEADING_TABLE_HEADER_FRAGMENT_RE = re.compile(
    r"(?i)^(?:unit\s+)?baud\s+rate\s+r[_\s]*baud\s+\d+(?:\s+\d+)?\s+gsym/s\s+see\s+section\s+"
)  # PDF 有时把表头和正文粘在一起，先清掉无上下文表头前缀。
_EMBEDDED_TABLE_IDENTIFIER_RESIDUE_RE = re.compile(
    r"(?i)\bfx\s+bx\s+ffe[_-]?post\s+(?=table\b)"
)  # `fx bx FFE_post Table 32-1` 是表格残片粘入正文引用，不是协议正文变化。


def _section_similarity(left: str, right: str) -> float:
    """Return a bounded similarity score for section matching and reporting."""

    left_sample = _sample_section_text(left)  # 采样保留章节开头和结尾，兼顾标题、定义和表格续行。
    right_sample = _sample_section_text(right)  # 两边使用同样采样策略，分数才可比较。
    return _similarity(left_sample, right_sample)  # 章节粗匹配走轻量相似度，重规范化留给片段级差异。


def _sample_section_text(value: str) -> str:
    """Keep representative text from long sections for cheap matching."""

    if len(value) <= _SECTION_MATCH_SAMPLE_CHARS:  # 短章节直接完整比较，避免采样丢信息。
        return value
    head_chars = int(_SECTION_MATCH_SAMPLE_CHARS * 0.4)  # 开头通常包含标题和核心定义。
    middle_chars = int(_SECTION_MATCH_SAMPLE_CHARS * 0.3)  # 中段可覆盖长章节里真正稳定的主体内容。
    tail_chars = _SECTION_MATCH_SAMPLE_CHARS - head_chars - middle_chars  # 结尾常包含跨页表格续行或总结句。
    middle_start = max(0, (len(value) // 2) - (middle_chars // 2))  # 从正文中心附近取样，避免只看头尾。
    middle_end = min(len(value), middle_start + middle_chars)  # 防止切片越界，保持总采样规模可控。
    return f"{value[:head_chars]}\n...\n{value[middle_start:middle_end]}\n...\n{value[-tail_chars:]}"


def _review_similarity(left: str, right: str) -> float:
    """Return a similarity score after display-only punctuation is normalized."""

    left_norm = _review_unit_key(left)
    right_norm = _review_unit_key(right)
    if not left_norm and not right_norm:
        return 1.0
    if not left_norm or not right_norm:
        return 0.0
    return difflib.SequenceMatcher(None, left_norm, right_norm, autojunk=False).ratio()


def _similarity(left: str, right: str) -> float:
    """Return normalized SequenceMatcher ratio for two text values."""

    left_norm = normalize_for_similarity(left)
    right_norm = normalize_for_similarity(right)
    if not left_norm and not right_norm:
        return 1.0
    if not left_norm or not right_norm:
        return 0.0
    return difflib.SequenceMatcher(None, left_norm, right_norm, autojunk=False).ratio()


def _sections_effectively_unchanged(
    old_section: Section,
    new_section: Section,
    options: DiffOptions,
) -> bool:
    """Treat a matched section as unchanged only when title and body are stable."""

    body_same = _review_unit_key(old_section.body) == _review_unit_key(new_section.body)
    return body_same and not _section_heading_changed(old_section, new_section)


def _section_heading_changed(old_section: Section, new_section: Section) -> bool:
    """Detect same-number section title changes that would otherwise be missed."""

    if _both_page_fallback_sections(old_section, new_section):
        return False
    return _review_unit_key(old_section.heading) != _review_unit_key(new_section.heading)


def _both_page_fallback_sections(old_section: Section, new_section: Section) -> bool:
    """Return True when both sections are synthetic no-heading page chunks."""

    return old_section.section_id.startswith("P") and new_section.section_id.startswith("P")


def _summarize_text_delta(
    old_text: str,
    new_text: str,
    max_snippets: int,
    leading_replacement: SnippetPair | None = None,
    *,
    suppressed_table_unit_keys: set[str] | None = None,
) -> tuple[list[str], list[str], list[SnippetPair], int]:
    """Create compact added/removed/replaced snippets for one section."""

    table_unit_keys = suppressed_table_unit_keys or set()  # 兼容旧调用方；正文区现在会隐藏全部结构化表格行。
    old_units = _paragraph_review_units(old_text, suppressed_table_unit_keys=table_unit_keys)  # 主正文区只保留段落/句子级文字。
    new_units = _paragraph_review_units(new_text, suppressed_table_unit_keys=table_unit_keys)  # 表格行交给表格摘要和截图区承载。
    old_keys = [_review_unit_key(unit) for unit in old_units]
    new_keys = [_review_unit_key(unit) for unit in new_units]
    matcher = difflib.SequenceMatcher(None, old_keys, new_keys, autojunk=False)

    candidates: list[_DeltaCandidate] = []
    candidate_order = 0

    def add_candidate(
        kind: str,
        *,
        text: str = "",
        pair: SnippetPair | None = None,
        priority_values: tuple[str, ...],
    ) -> None:
        nonlocal candidate_order
        candidates.append(
            _DeltaCandidate(
                kind=kind,
                order=candidate_order,
                priority=_substantive_priority(*priority_values),
                text=text,
                pair=pair,
            )
        )
        candidate_order += 1

    if leading_replacement:
        add_candidate(
            "replaced",
            pair=leading_replacement,
            priority_values=(leading_replacement.old, leading_replacement.new),
        )

    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert":
            for unit in new_units[new_start:new_end]:
                add_candidate(
                    "added",
                    text=_report_unit(unit),
                    priority_values=(unit,),
                )
        elif tag == "delete":
            for unit in old_units[old_start:old_end]:
                add_candidate(
                    "removed",
                    text=_report_unit(unit),
                    priority_values=(unit,),
                )
        elif tag == "replace":
            old_block_units = old_units[old_start:old_end]
            new_block_units = new_units[new_start:new_end]
            if len(old_block_units) == len(new_block_units):
                for old_unit, new_unit in zip(old_block_units, new_block_units, strict=True):
                    if _review_unit_key(old_unit) == _review_unit_key(new_unit):
                        continue
                    add_candidate(
                        "replaced",
                        pair=SnippetPair(old=_report_unit(old_unit), new=_report_unit(new_unit)),
                        priority_values=(old_unit, new_unit),
                    )
            else:
                for candidate in _unequal_replace_delta_candidates(
                    old_block_units,
                    new_block_units,
                ):
                    add_candidate(
                        candidate.kind,
                        text=candidate.text,
                        pair=candidate.pair,
                        priority_values=candidate.priority_values,
                    )

    return _materialize_delta_candidates(candidates, max_snippets)


def _paragraph_review_units(text: str, *, suppressed_table_unit_keys: set[str]) -> list[str]:
    """Return review units for paragraph cards, optionally excluding table rows."""

    units = _split_units(text)  # 先走统一切分和原始表格噪声覆盖，避免长表格块污染正文 diff。
    return [
        unit
        for unit in units
        if not _is_table_review_unit(unit)
    ]  # 结构化表格行不进正文卡片，避免和下方视觉/结构化表格摘要重复。


def _split_units(text: str) -> list[str]:
    """Split text into review-sized units.

    Sentence-like chunks work better for Chinese protocols than word-level diffs
    because Chinese text has no guaranteed spaces. If extraction returns very
    long lines, the fallback chunks by length to keep report snippets readable.
    """

    raw_units: list[str] = []
    for block in _merge_wrapped_lines(text):
        raw_units.extend(_split_line_preserving_numbers(block))

    units: list[str] = []
    for unit in raw_units:
        if len(unit) <= 720:
            units.append(unit)
            continue
        units.extend(_split_long_unit(unit))
    return _drop_orphan_review_fragments(_drop_duplicate_raw_table_units(units))


def _drop_orphan_review_fragments(units: list[str]) -> list[str]:
    """Remove single-character PDF extraction fragments before diff pairing."""

    return [
        unit
        for unit in units
        if not _looks_like_orphan_review_fragment(unit)
    ]  # `p`、`F`、`A` 等孤立残字不应和完整句子形成左右对比。


def _looks_like_orphan_review_fragment(value: str) -> bool:
    """Return True for standalone OCR/PDF residue with no readable context."""

    candidate = compact_inline(value)  # 压成单行后判断，避免换行空白影响短残片识别。
    if not candidate:
        return True  # 空片段没有审阅价值。
    if _is_table_review_unit(candidate):
        return False  # 结构化表格行即使包含单字母值，也要交给表格逻辑保留。
    if re.fullmatch(r"(?i)[a-z]", candidate):
        return True  # 单个字母多来自公式/页边残片，不能单独参与正文对比。
    if re.fullmatch(r"(?i)[a-z]\.", candidate):
        return False  # `a.` 这类列表标记已有专门合并逻辑，保守不在这里删除。
    return bool(re.fullmatch(r"(?i)(?:[a-z]\s+){1,3}[a-z]", candidate) and len(candidate) <= 7)


_RAW_TABLE_NOISE_WORDS = frozenset(
    {
        "bandwidth",
        "capacitance",
        "characteristic",
        "coefficient",
        "condition",
        "equalizer",
        "frequency",
        "impedance",
        "jitter",
        "maximum",
        "minimum",
        "notes",
        "parameter",
        "probability",
        "receiver",
        "resistance",
        "rms",
        "symbol",
        "termination",
        "transmitter",
        "units",
        "value",
        "voltage",
    }
)  # diff 层兜底使用的表格噪声词表，和抽取层保持同一语义口径。


def _drop_duplicate_raw_table_units(units: list[str]) -> list[str]:
    """Remove raw table-like text units when structured table rows are present."""

    table_units = [unit for unit in units if _is_table_review_unit(unit)]  # 只有抽取层已给出结构化表格行时才降噪。
    if not table_units:
        return units
    table_tokens = _raw_table_noise_token_index(table_units)  # 用结构化表格行建立同章节 token 覆盖范围。
    return [
        unit
        for unit in units
        if _is_table_review_unit(unit) or not _looks_like_duplicate_raw_table_unit(unit, table_tokens)
    ]  # 保留结构化行和普通正文，删除重复的原始表格块。


def _looks_like_duplicate_raw_table_unit(unit: str, table_tokens: set[str]) -> bool:
    """Return True for a noisy raw table block already covered by table rows."""

    tokens = _raw_table_noise_tokens(unit)  # 和结构化表格行做 token 覆盖判断。
    overlap = len(tokens & table_tokens)  # token 重叠越高，越可能是同一表格的原始抽取残片。
    words = set(re.findall(r"[A-Za-z]+", unit.casefold()))  # 表格词数量用于区分正文长段落和参数块。
    table_word_count = len(words & _RAW_TABLE_NOISE_WORDS)
    if len(unit) < 160:
        return _looks_like_short_duplicate_raw_table_unit(unit, overlap, table_word_count)
    number_count = len(_NUMBER_TOKEN_RE.findall(unit))  # 原始表格块通常含有大量数值。
    if number_count < 6:
        return False
    if table_word_count < 2:
        return False
    required_overlap = min(12, max(6, len(tokens) // 6))
    if overlap >= required_overlap:
        return True
    return overlap >= 4 and table_word_count >= 4 and number_count >= 10 and _has_table_block_phrase(unit)


def _looks_like_short_duplicate_raw_table_unit(unit: str, overlap: int, table_word_count: int) -> bool:
    """Return True for short raw table fragments already covered by structured rows."""

    if overlap < 3:
        return False  # 短片段必须和结构化表格有明显重叠才可删除。
    if re.search(
        r"(?i)\b(?:shall|should|must|may|can|is|are|was|were|means|describes?|represents?|indicates?|shows?|specified|measured|computed|requirements?)\b",
        unit,
    ):
        return False  # 带规范动词的短句更可能是真正文段。
    if re.match(r"(?i)^(?:the|this|that|these|those)\b", unit):
        return False  # 以冠词/指示词开头的自然句不按表格碎片删除。
    if re.search(r"(?i)\bT_[A-Z0-9_]+|\bUI(?:rms|pp)?\b|NOTES?:|(?:MIN|MAX|TYP)\s*=", unit):
        return True  # 符号、单位、NOTES、上下限列是原始表格碎片强信号。
    return overlap >= 5 and table_word_count >= 2


def _has_table_block_phrase(unit: str) -> bool:
    """Return True for phrases that rarely occur outside raw parameter tables."""

    return bool(
        re.search(
            r"(?i)\b(?:parameter values|minimum value|maximum value|step size|characteristic impedance|termination resistance)\b",
            unit,
        )
    )  # 强表格短语能兜住 pdfplumber 正文路径残留的大块表格文本。


def _raw_table_noise_token_index(table_units: list[str]) -> set[str]:
    """Build a token index from structured table units."""

    tokens: set[str] = set()  # 汇总结构化表格 token，便于判断原始块是否重复。
    for unit in table_units:
        tokens.update(_raw_table_noise_tokens(unit))
    return tokens


def _raw_table_noise_tokens(value: str) -> set[str]:
    """Tokenize table-ish text for duplicate suppression."""

    lowered = value.casefold().replace("µ", "u").replace("μ", "u")  # 单位符号归一化，减少 μ/u 差异。
    return {
        token
        for token in re.findall(r"[a-z]+[a-z0-9]*|[+-]?\d+(?:\.\d+)?", lowered)
        if len(token) > 1
    }  # 删除单字符 token，避免 a、b、R 被过度计入重叠。


def _merge_wrapped_lines(text: str) -> list[str]:
    """Merge PDF line wraps into review-sized sentences or procedure steps."""

    blocks: list[str] = []
    current = ""
    for raw_line in text.splitlines():
        line = normalize_line(raw_line)
        if not line:
            continue
        if _looks_like_orphan_review_fragment(line):
            continue  # 先丢掉独立 `p`/`F` 等残片，避免换行合并时塞进完整句。
        if _starts_new_review_block(line):
            if current:
                blocks.append(current)
            current = line
            continue
        if not current:
            current = line
        elif _ends_review_sentence(current) and not _is_standalone_list_marker(current):
            if _looks_like_decimal_continuation(current, line):
                current = _join_wrapped_line(current, line)
            else:
                blocks.append(current)
                current = line
        elif _looks_like_new_sentence_after_linebreak(current, line):
            blocks.append(current)
            current = line
        else:
            current = _join_wrapped_line(current, line)
    if current:
        blocks.append(current)
    return blocks


def _starts_new_review_block(line: str) -> bool:
    """Return True when a line starts a new list item or procedure step."""

    patterns = (
        r"^\d{1,3}[.)](?:\s+\S.*|\s*)$",
        r"^[a-zA-Z][.)](?:\s+\S.*|\s*)$",
        r"^[•●⚫-]\s+\S",
        r"^Note:\s+",
        # 表格行是提取器新增的结构化单位，需要保留为独立差异片段。
        r"^表格行:\s+",
    )
    return any(re.match(pattern, line) for pattern in patterns)


def _join_wrapped_line(left: str, right: str) -> str:
    """Join one PDF-wrapped line while repairing common hyphen breaks."""

    if left.endswith("-") and right and right[0].islower():
        return left[:-1] + right
    return f"{left} {right}"


def _looks_like_decimal_continuation(left: str, right: str) -> bool:
    """Recognize PDF line breaks inside decimal values such as ``125. 0 μs``."""

    left_tail = left.rstrip()
    right_head = right.lstrip()
    return bool(
        re.search(r"\d\.$", left_tail)
        and re.match(
            r"^\d+\s*(?:[µμ]s|us|ps|ns|ms|ui|mv|v|db|mhz|ghz|gt/s)\b",
            right_head,
            flags=re.I,
        )
    )


def _ends_review_sentence(value: str) -> bool:
    """Return True when a buffered line already looks like a complete sentence."""

    return value.rstrip().endswith((".", "。", ";", "；", "!", "！", "?", "？"))


def _is_standalone_list_marker(value: str) -> bool:
    """Keep markers such as ``a.`` or ``2.`` attached to their following text."""

    return bool(re.fullmatch(r"(?:\d{1,3}|[A-Za-z])[.)]", value.strip()))


def _looks_like_new_sentence_after_linebreak(left: str, right: str) -> bool:
    """Avoid gluing adjacent extracted sentences when punctuation is missing."""

    if _is_standalone_list_marker(left) or left.endswith("-"):
        return False
    first = right[:1]
    return bool(len(left) <= 60 and first and first.isascii() and first.isupper())


def _split_long_unit(unit: str, max_chars: int = 720) -> list[str]:
    """Split a very long unit at readable boundaries instead of mid-word."""

    chunks: list[str] = []
    remaining = unit.strip()
    while len(remaining) > max_chars:
        cut = max(
            remaining.rfind(". ", 0, max_chars),
            remaining.rfind("; ", 0, max_chars),
            remaining.rfind("。", 0, max_chars),
            remaining.rfind("；", 0, max_chars),
        )
        if cut < max_chars // 2:
            cut = remaining.rfind(" ", 0, max_chars)
        if cut < max_chars // 2:
            cut = max_chars
        chunk = remaining[: cut + 1].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[cut + 1 :].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


_NUMBER_TOKEN_RE = re.compile(
    r"[+-]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)(?:e[+-]?\d+)?",
    flags=re.I,
)
_TABLE_REVIEW_PREFIX_RE = re.compile(r"^(?:表格行|表格文字)[:：]\s*")  # 内部表格行和用户可见兜底表格文字共用识别入口。
_REVIEW_TOKEN_RE = re.compile(
    r"<=|>=|≤|≥|(?<!-)[<>](?!-)|="
    r"|[+-]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)(?:e[+-]?\d+)?"
    r"|[a-zµμ]+[a-z0-9µμ]*(?:[-_/][a-z0-9µμ]+)*|[\u4e00-\u9fff]+",
    flags=re.I,
)
_PROTECTED_NUMBER_WORD_PREFIXES = frozenset(
    {
        "appendix",
        "clause",
        "figure",
        "gen",
        "generation",
        "model",
        "part",
        "profile",
        "rev",
        "revision",
        "section",
        "table",
        "type",
    }
)
_PROTECTED_NUMBER_WORD_SUFFIXES = frozenset(
    {
        "csv",
        "dat",
        "doc",
        "docx",
        "html",
        "json",
        "pdf",
        "txt",
        "xls",
        "xlsx",
        "xml",
        "zip",
    }
)


def _review_unit_key(value: str) -> str:
    """Normalize a unit for deciding whether a visible diff is substantive.

    The key ignores case, whitespace, and sentence punctuation, but keeps
    meaningful numeric tokens intact. That prevents false equivalence between
    values such as ``1.0 ps`` and ``10 ps`` while still suppressing PDF wrapping
    and comma/period noise.
    """

    value = remove_draft_watermark_letter_artifacts(value)  # 先去掉 DRAFT 水印字母残片，再生成比较 key。
    value = _strip_leading_table_header_fragment(value)  # 去掉 UNIT/Baud Rate 等表头前缀，避免污染正文句。
    value = _strip_embedded_table_identifier_residue(value)  # 去掉句中插入的短表格标识符残片。
    normalized = normalize_for_similarity(value)
    normalized = canonicalize_chinese_number_expressions(normalized)
    normalized = normalized.replace("µ", "u").replace("μ", "u")
    normalized = normalized.replace("&", " and ")
    normalized = normalized.replace("≤", "<=").replace("≥", ">=")
    normalized = _normalize_math_symbol_artifacts(normalized)
    normalized = _normalize_embedded_number_list_spacing(normalized)
    normalized = re.sub(r"-\s*[<>]\s*", " ", normalized)
    normalized = re.sub(r"(?<=\d)\.\s+(?=\d)", ".", normalized)
    normalized = re.sub(r"\b10\s+([0-9])\b", r"10\1", normalized)
    normalized = re.sub(r"(?<=[a-z])[-‐‑](?=[a-z])", "", normalized)
    normalized = re.sub(r"\bpreset\s*([0-9]+)\b", r"p\1", normalized)
    tokens = [_canonical_review_token(token) for token in _REVIEW_TOKEN_RE.findall(normalized)]
    return " ".join(
        canonicalize_number_word_tokens(
            tokens,
            protected_previous_words=_PROTECTED_NUMBER_WORD_PREFIXES,
            protected_next_words=_PROTECTED_NUMBER_WORD_SUFFIXES,
        )
    )


def _normalize_embedded_number_list_spacing(value: str) -> str:
    """Repair OCR text such as ``Tests1,2`` before token comparison."""

    normalized = re.sub(
        r"(?i)\b(tests?|notes?)\s*(\d)(?=\s*[,.)])",
        r"\1 \2",
        value,
    )  # `Tests1,2,3` 和 `Tests 1, 2, 3` 表达相同列表，不应触发正文差异。
    return re.sub(
        r"(?i)\b(equation|figure|table|section)\s*(\d)",
        r"\1 \2",
        normalized,
    )  # 常见引用词后缺空格时只修正格式，不改变编号含义。


def _normalize_math_symbol_artifacts(value: str) -> str:
    """Normalize PDF variants of multiplication, exponents, hyphens, and spaces."""

    normalized = value.replace("−", "-").replace("–", "-").replace("—", " - ")  # 统一数学负号和破折号形态。
    normalized = re.sub(
        r"(?i)(?<![a-z])([+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*(?:x|×|\*)\s*10\s*([+-]?\d+)",
        r"\1e\2",
        normalized,
    )  # 5x10-6、5×10-6、5*10-6 都归一成 5e-6。
    normalized = re.sub(
        r"(?i)(?<=\d)\s*(?:x|×|\*)\s*(?=[a-z_])",
        " ",
        normalized,
    )  # 2xT_Vf 与 2×T_Vf 都变成 2 T_Vf，避免乘号格式噪声。
    normalized = re.sub(
        r"(?i)(?<=[a-z_])\s*(?:×|\*)\s*(?=[a-z0-9_])",
        " ",
        normalized,
    )  # fb×n 与 fb*n 的乘号在 token 比较中等价。
    normalized = re.sub(r"(?<=\d)\s+(?=[+-]\d\b)", "", normalized)  # 10 -6 这类指数空格收紧。
    return normalized


def _canonical_review_token(token: str) -> str:
    """Canonicalize token values only where protocol meaning is preserved."""

    if not _NUMBER_TOKEN_RE.fullmatch(token):
        return token
    sign = "-" if token.startswith("-") else ""
    body = token.lstrip("+-").replace(",", "")
    if body.startswith("."):
        body = f"0{body}"
    try:
        value = Decimal(body)
    except InvalidOperation:
        return token
    if value == 0:
        sign = ""
    numeric = format(value.normalize(), "f")
    if "." in numeric:
        numeric = numeric.rstrip("0").rstrip(".")
    return f"{sign}{numeric}"


def _report_unit(value: str) -> str:
    """Return a human-readable snippet without odd mid-sentence ellipses."""

    if _is_table_review_unit(value):
        return _format_fallback_table_review_unit(value)  # 未被视觉摘要覆盖的表格行用用户可读前缀展示。
    value = _strip_leading_table_header_fragment(value)  # 展示层同样去掉粘连的表头残片。
    value = _strip_embedded_table_identifier_residue(value)  # 避免报告里显示 `fx bx FFE_post` 这类粘连残片。
    readable = " ".join(_split_long_unit(value, max_chars=1200))
    if len(readable) <= 1400:
        return readable
    cut = max(
        readable.rfind(". ", 0, 1200),
        readable.rfind("; ", 0, 1200),
        readable.rfind("。", 0, 1200),
        readable.rfind("；", 0, 1200),
    )
    if cut < 600:
        cut = readable.rfind(" ", 0, 1200)
    if cut < 600:
        cut = 1200
    return readable[: cut + 1].strip() + " [片段过长，已截断；请见源 PDF 对应页]"


def _format_fallback_table_review_unit(value: str) -> str:
    """Convert an internal structured table row into a readable fallback snippet."""

    cells = _table_row_cells(value)  # 复用表格行解析，兼容内部前缀和用户可见兜底前缀。
    if cells and re.fullmatch(r"T\d+", cells[0], flags=re.I):
        cells = cells[1:]  # T1/T2 只是抽取器的页内表序号，不必暴露给用户。
    payload = " | ".join(cells) if cells else compact_inline(value)  # 保留 Header=Value，方便搜索和人工复核。
    return f"表格文字: {payload}"


def _strip_leading_table_header_fragment(value: str) -> str:
    """Drop table header residue that was glued before a normal sentence."""

    stripped = _LEADING_TABLE_HEADER_FRAGMENT_RE.sub("", compact_inline(value))  # 保留后面的正文句，不影响真实内容。
    return stripped if stripped else value  # 防止整行都是表头时被清成空字符串。


def _strip_embedded_table_identifier_residue(value: str) -> str:
    """Remove short table identifier residue glued into a normal sentence."""

    cleaned = _EMBEDDED_TABLE_IDENTIFIER_RESIDUE_RE.sub("", compact_inline(value))  # 只删除 Table 引用前的明确残片。
    return cleaned if cleaned else value  # 防止异常情况下返回空文本。


_MIN_UNEQUAL_REPLACE_PAIR_SCORE = 0.45

_REVIEW_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "shall",
        "that",
        "the",
        "then",
        "this",
        "to",
        "with",
    }
)


def _unequal_replace_delta_candidates(
    old_units: list[str],
    new_units: list[str],
) -> list[_PendingDeltaCandidate]:
    """Pair similar units inside an unequal replace block without misalignment.

    A PDF extraction block can contain two changed sentences plus one inserted
    sentence. Pairing the shortest old and shortest new sentence independently
    is compact but unsafe: it can align a preset change with a jitter change.
    This routine first matches exact normalized units, then greedily pairs
    remaining old/new units only when they share enough semantic anchors. Any
    unpaired unit is reported as an addition or deletion instead of a misleading
    side-by-side replacement.
    """

    old_keys = [_review_unit_key(unit) for unit in old_units]
    new_keys = [_review_unit_key(unit) for unit in new_units]
    matched_old: set[int] = set()
    matched_new: set[int] = set()

    for old_index, old_key in enumerate(old_keys):
        if not old_key:
            continue
        for new_index, new_key in enumerate(new_keys):
            if new_index in matched_new:
                continue
            if old_key == new_key:
                matched_old.add(old_index)
                matched_new.add(new_index)
                break

    pair_scores: list[tuple[float, float, int, int]] = []
    for old_index, old_unit in enumerate(old_units):
        if old_index in matched_old:
            continue
        for new_index, new_unit in enumerate(new_units):
            if new_index in matched_new:
                continue
            score = _unit_pair_score(old_unit, new_unit)
            if score >= _MIN_UNEQUAL_REPLACE_PAIR_SCORE:
                position_gap = abs(
                    _relative_position(old_index, len(old_units))
                    - _relative_position(new_index, len(new_units))
                )
                pair_scores.append((score, -position_gap, old_index, new_index))

    paired_indexes: list[tuple[int, int]] = []
    for _score, _position_gap, old_index, new_index in sorted(pair_scores, reverse=True):
        if old_index in matched_old or new_index in matched_new:
            continue
        matched_old.add(old_index)
        matched_new.add(new_index)
        paired_indexes.append((old_index, new_index))

    events: list[tuple[float, int, int, _PendingDeltaCandidate]] = []
    event_sequence = 0
    for old_index, new_index in paired_indexes:
        old_unit = old_units[old_index]
        new_unit = new_units[new_index]
        if _review_unit_key(old_unit) == _review_unit_key(new_unit):
            continue
        events.append(
            (
                min(old_index, new_index),
                0,
                event_sequence,
                _PendingDeltaCandidate(
                    kind="replaced",
                    pair=SnippetPair(old=_report_unit(old_unit), new=_report_unit(new_unit)),
                    priority_values=(old_unit, new_unit),
                ),
            )
        )
        event_sequence += 1

    for old_index, old_unit in enumerate(old_units):
        if old_index in matched_old:
            continue
        events.append(
            (
                old_index,
                1,
                event_sequence,
                _PendingDeltaCandidate(
                    kind="removed",
                    text=_report_unit(old_unit),
                    priority_values=(old_unit,),
                ),
            )
        )
        event_sequence += 1
    for new_index, new_unit in enumerate(new_units):
        if new_index in matched_new:
            continue
        events.append(
            (
                new_index,
                2,
                event_sequence,
                _PendingDeltaCandidate(
                    kind="added",
                    text=_report_unit(new_unit),
                    priority_values=(new_unit,),
                ),
            )
        )
        event_sequence += 1

    return [candidate for _order, _kind_order, _sequence, candidate in sorted(events)]


def _unit_pair_score(old_unit: str, new_unit: str) -> float:
    """Score whether two unequal units are safe to show as a replacement pair."""

    table_score = _table_unit_pair_score(old_unit, new_unit)  # 表格行先按参数身份配对，避免相邻行错配造成噪声。
    if table_score is not None:
        return table_score

    old_words = _meaningful_review_words(old_unit)
    new_words = _meaningful_review_words(new_unit)
    if old_words and new_words and not (old_words & new_words):
        return 0.0
    base_score = max(_review_similarity(old_unit, new_unit), _similarity(old_unit, new_unit))
    if old_words and new_words:
        overlap = len(old_words & new_words) / min(len(old_words), len(new_words))
        return base_score * (0.75 + overlap * 0.25)
    return base_score


def _table_unit_pair_score(old_unit: str, new_unit: str) -> float | None:
    """Return a table-specific pair score, or None for normal text units."""

    old_is_table = _is_table_review_unit(old_unit)  # 表格行由抽取层显式加上中文前缀。
    new_is_table = _is_table_review_unit(new_unit)  # 两侧都必须是表格行才允许表格身份配对。
    if not old_is_table and not new_is_table:
        return None
    if old_is_table != new_is_table:
        return 0.0
    old_identity = _table_row_identity(old_unit)  # 参数名/特性名是表格行的主身份，不包含 Value。
    new_identity = _table_row_identity(new_unit)  # 新旧表格同一参数应拥有相同身份键。
    if old_identity and new_identity:
        return 1.0 if old_identity == new_identity else 0.0
    return None


def _is_table_review_unit(value: str) -> bool:
    """Return True for structured table rows emitted by the extractor."""

    return bool(_TABLE_REVIEW_PREFIX_RE.match(normalize_line(value)))  # 内部行和用户可见兜底表格文字都走同一判断。


def _table_row_identity(value: str) -> str:
    """Build a stable identity key for one structured table row."""

    fields = _table_row_fields(value)  # 先解析 Header=Value 字段，便于忽略数值列。
    identity_values: list[str] = []  # 收集能代表“同一行”的参数文本。
    for key in ("parameter", "characteristic", "description", "name"):
        field_value = fields.get(key)
        if field_value:
            identity_values.append(field_value)
            break
    if fields.get("symbol"):
        identity_values.append(fields["symbol"])  # 符号能区分同名参数的不同变体。
    if identity_values:
        return _review_unit_key(" ".join(identity_values))

    for cell in _table_row_cells(value):  # 兼容未标注表头的旧片段，选第一个描述性单元格作为身份。
        if _looks_like_table_identity_cell(cell):
            return _review_unit_key(cell)
    return ""


def _table_row_fields(value: str) -> dict[str, str]:
    """Parse Header=Value parts from a structured table row."""

    fields: dict[str, str] = {}  # 小写字段名映射到原始值，保留可读文本供身份归一化。
    for cell in _table_row_cells(value):
        if "=" not in cell:
            continue
        key, raw_value = cell.split("=", 1)
        normalized_key = normalize_for_similarity(key).strip()
        normalized_value = normalize_line(raw_value)
        if normalized_key and normalized_value:
            fields[normalized_key] = normalized_value
    return fields


def _table_row_cells(value: str) -> list[str]:
    """Split one table row snippet into pipe-separated payload cells."""

    text = normalize_line(value)  # 统一空白后再切分，减少 PDF 抽取空格差异。
    text = _TABLE_REVIEW_PREFIX_RE.sub("", text).strip()  # 删除内部或可见表格前缀，只保留列内容。
    cells = [cell.strip() for cell in text.split("|") if cell.strip()]  # 表格行内部用竖线分隔列。
    if cells and re.fullmatch(r"T\d+", cells[0], flags=re.I):
        return cells[1:]  # T1/T2 只是物理表编号，不参与行身份。
    return cells


def _looks_like_table_identity_cell(value: str) -> bool:
    """Return True for an unlabeled cell that can identify a table row."""

    normalized = normalize_line(value)  # 参数名单元格通常包含字母和说明词，而不是纯数值/单位。
    if not normalized or len(normalized) <= 1:
        return False
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?(?:\s*/\s*[+-]?\d+(?:\.\d+)?)*", normalized):
        return False
    if re.fullmatch(r"(?i)(?:Ω|ohm|ff|pf|ph|mhz|ghz|ui|mv|v|db|ns/mm|1/mm|unit|units)", normalized):
        return False
    return bool(re.search(r"[A-Za-z\u4e00-\u9fff]", normalized))


def _meaningful_review_words(value: str) -> set[str]:
    """Return non-boilerplate word anchors for pairing changed units."""

    normalized = normalize_for_similarity(value).replace("µ", "u").replace("μ", "u")
    words = set(re.findall(r"[a-z]+[a-z0-9]*(?:[-_/][a-z0-9]+)*", normalized))
    return {word for word in words if word not in _REVIEW_STOP_WORDS}


def _relative_position(index: int, length: int) -> float:
    """Normalize an index within a replace block for tie-breaking."""

    if length <= 1:
        return 0.0
    return index / (length - 1)


def _materialize_delta_candidates(
    candidates: list[_DeltaCandidate],
    max_snippets: int,
) -> tuple[list[str], list[str], list[SnippetPair], int]:
    """Apply snippet limits after all meaningful differences have been scanned."""

    unique_candidates = [
        candidate
        for candidate in _dedupe_candidates(candidates)
        if not _candidate_is_global_noise(candidate)
    ]  # 先过滤表格/OCR/页眉噪声，再套 max_snippets，避免噪声抢占正文名额。
    if max_snippets <= 0:
        return [], [], [], len(unique_candidates)

    if len(unique_candidates) <= max_snippets:
        selected = unique_candidates
        omitted_count = 0
    else:
        prioritized = sorted(unique_candidates, key=lambda item: (-item.priority, item.order))
        selected = sorted(prioritized[:max_snippets], key=lambda item: item.order)
        omitted_count = len(unique_candidates) - len(selected)

    added: list[str] = []
    removed: list[str] = []
    replaced: list[SnippetPair] = []
    for candidate in selected:
        if candidate.kind == "added":
            added.append(candidate.text)
        elif candidate.kind == "removed":
            removed.append(candidate.text)
        elif candidate.kind == "replaced" and candidate.pair:
            replaced.append(candidate.pair)
    return added, removed, replaced, omitted_count


def _candidate_is_global_noise(candidate: _DeltaCandidate) -> bool:
    """Return True when a candidate should not count against visible snippets."""

    if candidate.pair:
        return _should_suppress_replaced_pair(candidate.pair)  # 替换对两侧都是噪声时整对隐藏。
    return _is_global_noise_snippet(candidate.text)  # 单侧新增/删除片段直接走全局噪声规则。


def _dedupe_candidates(candidates: list[_DeltaCandidate]) -> list[_DeltaCandidate]:
    """Remove duplicate snippets while preserving first occurrence and priority."""

    by_key: dict[tuple[str, str], _DeltaCandidate] = {}
    for candidate in candidates:
        key = _candidate_key(candidate)
        existing = by_key.get(key)
        if existing is None or candidate.priority > existing.priority:
            by_key[key] = candidate
    return sorted(by_key.values(), key=lambda item: item.order)


def _suppress_global_noise_changes(changes: list[SectionChange]) -> tuple[list[SectionChange], int]:
    """Remove report snippets that are clearly repeated layout or boilerplate noise."""

    suppressed_count = 0  # 统计被隐藏的噪声片段数量，最终写进报告警告。
    cleaned_changes: list[SectionChange] = []  # 保存仍有用户可审阅内容的章节变化。
    for change in changes:
        added = [snippet for snippet in change.added_snippets if not _is_global_noise_snippet(snippet)]
        removed = [snippet for snippet in change.removed_snippets if not _is_global_noise_snippet(snippet)]
        replaced = [
            pair
            for pair in change.replaced_snippets
            if not _should_suppress_replaced_pair(pair)
        ]
        suppressed_count += len(change.added_snippets) - len(added)
        suppressed_count += len(change.removed_snippets) - len(removed)
        suppressed_count += len(change.replaced_snippets) - len(replaced)
        if not added and not removed and not replaced and change.omitted_snippet_count == 0:
            suppressed_count += 1  # 所有可见片段都被判定为版面噪声时，整张空卡片也隐藏。
            continue
        cleaned_changes.append(
            SectionChange(
                change_type=change.change_type,
                old_section=change.old_section,
                new_section=change.new_section,
                similarity=change.similarity,
                added_snippets=added,
                removed_snippets=removed,
                replaced_snippets=replaced,
                omitted_snippet_count=change.omitted_snippet_count,
            )
        )
    return cleaned_changes, suppressed_count


def _should_suppress_replaced_pair(pair: SnippetPair) -> bool:
    """Return True when a replacement pair is only layout/image noise."""

    old_visual = _looks_like_visual_only_snippet(pair.old)  # 旧侧图片/图轴/公式碎片不能和正文或表格行并排展示。
    new_visual = _looks_like_visual_only_snippet(pair.new)  # 新侧图片/图轴/公式碎片同样需要降噪。
    old_noise = _is_global_noise_snippet(pair.old)  # 页眉页脚和水印残片也不应和视觉碎片并排展示。
    new_noise = _is_global_noise_snippet(pair.new)  # 新侧页眉页脚同理。
    old_corrupt_figure = _looks_like_corrupted_figure_reference_snippet(pair.old)  # Figure 引用被 cd/R 等残字污染时不可信。
    new_corrupt_figure = _looks_like_corrupted_figure_reference_snippet(pair.new)  # 新侧损坏 Figure 引用同理。
    old_table = _is_table_review_unit(pair.old)  # 表格行若被错误配到图片块，应交给表格截图区展示。
    new_table = _is_table_review_unit(pair.new)  # 新表格行同理，避免正文区出现“图形 vs 表格行”的错配。
    old_table_visual_noise = _table_review_unit_has_visual_noise(pair.old)  # 表格兜底里夹带页眉/公式时，不应和干净行并排展示。
    new_table_visual_noise = _table_review_unit_has_visual_noise(pair.new)  # 新侧表格兜底噪声也按同一规则隐藏。
    if old_corrupt_figure and new_corrupt_figure:
        return True
    if (old_table_visual_noise or new_table_visual_noise) and (old_table or new_table):
        return True
    if old_visual and (new_visual or new_table):
        return True
    if new_visual and (old_visual or old_table):
        return True
    if (old_visual or old_noise) and (new_visual or new_noise or new_corrupt_figure):
        return True
    if (new_visual or new_noise) and (old_visual or old_noise or old_corrupt_figure):
        return True
    return old_noise and new_noise


def _is_global_noise_snippet(value: str) -> bool:
    """Identify DRAFT, copyright, running header, and margin line-number noise."""

    candidate = compact_inline(value)  # 压成单行，便于识别跨行抽取出来的页眉页脚。
    if not candidate:
        return False
    if _table_review_unit_has_visual_noise(candidate):
        return True  # 夹带页眉/公式的结构化表格行交给表格截图区，不放在正文 diff。
    if _looks_like_fragmentary_table_or_equation_snippet(candidate):
        return True  # 表格/公式目录项不是完整句子，交给表格摘要或源 PDF 复核。
    if _looks_like_visual_only_snippet(candidate):
        return True
    if _looks_like_corrupted_figure_reference_snippet(candidate):
        return True  # 被 cd/R 等残字污染的 Figure 引用来自抽取噪声，不展示为正文差异。
    lowered = candidate.casefold()  # 大小写不影响 DRAFT/boilerplate 判断。
    if _looks_like_margin_line_number_run(candidate):
        return True
    if re.fullmatch(r"[DRAFT]\.?", candidate):
        return True  # 单独成片段的 D/R/A/F/T 通常是 DRAFT 水印残字，不是协议内容。
    if re.fullmatch(r"(?i)(?:draft\s*){1,8}", candidate):
        return True  # 单独出现或重复出现的 DRAFT 水印不是正文差异。
    noise_patterns = (
        r"\bcopyright\s+©?\s*\d{4}\s+optical\s+internetworking\s+forum\b",
        r"\bthis\s+is\s+a\s+draft\s+and\s+not\s+to\s+be\s+shared\b",
        r"\bthe\s+[“\"]?draft[”\"]?\s+watermark\s+is\s+not\s+to\s+be\s+removed\b",
        r"\boptical\s+internetworking\s+forum\s+-\s+clause\s+\d+:",
        r"\boptical\s+internetworking\s+forum\s+\(oif\)\s+\d{3,}.*\bwww\.oiforum\.com\b",
        r"\bnotice:\s+this\s+technical\s+document\s+has\s+been\s+created\s+by\s+the\s+optical\s+internetworking\s+forum\b",
        r"\bimplementation\s+agreement\s+oif-cei\b",
    )  # 这些短语在用户样本中反复出现在页眉页脚或草稿水印中。
    return any(re.search(pattern, lowered) for pattern in noise_patterns)


def _looks_like_fragmentary_table_or_equation_snippet(value: str) -> bool:
    """Return True for short table/equation row fragments, not prose sentences."""

    candidate = compact_inline(value)  # 表格碎片在报告中也是单行片段。
    if not candidate or _is_table_review_unit(candidate):
        return False  # 结构化表格兜底由专门逻辑控制，不能在这里全删。
    if len(candidate) > 140:
        return False  # 长文本更可能包含真实正文，不能按短碎片处理。
    if _has_protocol_sentence_verb(candidate):
        return False  # 带谓语的规范句应继续作为正文差异展示。
    if re.fullmatch(r"(?i)(?:[+-]?\d+(?:\.\d+)?|[+-]?\d+/\d+)(?:\s+(?:[+-]?\d+(?:\.\d+)?|[+-]?\d+/\d+)){0,8}", candidate):
        return True  # `03`、`-1 -1/3 1/3 1` 这类纯数值序列通常来自表格或公式。
    if re.fullmatch(
        r"(?i)(?:unit|units|min\.?|typ\.?|max\.?|symbol|condition|characteristic|parameter|value|notes?)"
        r"(?:\s+(?:unit|units|min\.?|typ\.?|max\.?|symbol|condition|characteristic|parameter|value|notes?)){0,5}",
        candidate,
    ):
        return True  # 表头词单独成片段没有正文审阅价值。
    if re.fullmatch(r"(?i)note\s*\d+[A-Z]?", candidate):
        return True  # `Note 2D` 多为表格/脚注编号残片。
    if re.fullmatch(r"(?i)notes?:\s*[A-Z]?", candidate):
        return True  # `NOTES:` / `NOTES: D` 是表格脚注表头残片。
    if re.search(r"(?i)(?:^|\|)\s*(?:min|max|typ|unit|units|value|symbol)\s*=", candidate):
        return True  # `| MAX=1000 | UNIT=mVppd` 是表格单元串，不是正文句。
    if re.fullmatch(r"(?i)baud\s+rate\s+r[_\s]*baud\s+\d+(?:\s+\d+)?\s+gsym/s", candidate):
        return True  # 独立 Baud Rate 表头/数据行交给表格摘要区。
    if re.fullmatch(r"(?i)se?fe\s+section|see\s+section", candidate):
        return True  # `SeFe Section` 是 `See Section` 被水印残字污染后的表头残片。
    if re.search(r"(?i)\b[A-Z]+_[A-Z0-9_]+\b", candidate) and len(candidate.split()) <= 5:
        return True  # `FFE_Post`、`fx bx FFE_Post` 这类短标识符组合不是完整正文句。
    if re.fullmatch(r"(?i)[A-Z][A-Z0-9_/-]{1,32}", candidate):
        return True  # `FFE_Post`、`UNIT` 这类孤立标识符交给表格摘要/源 PDF 复核。
    if re.fullmatch(r"(?i)(?:conversion|equation)\s*\(?\d+(?:[-–]\d+)?\)?\.?", candidate):
        return True  # `Conversion (32-6)` 是表格/公式项，不是完整句子。
    if re.fullmatch(r"(?i)(?:interference|jitter)\s+tolerance\s+table\s+\d+(?:[-–]\d+)?\.?", candidate):
        return True  # 接收端表格索引项应由表格摘要承载。
    if re.fullmatch(r"(?i)table\s+\d+(?:[-–]\d+)?\.?", candidate):
        return True  # 单独 `Table 32-10.` 没有句子上下文。
    if re.search(r"(?i)\bblock\s+error\s+ratio\b", candidate) and len(_NUMBER_TOKEN_RE.findall(candidate)) >= 2:
        return True  # BER 表格值变化应在视觉表格摘要里查看。
    return False


def _has_protocol_sentence_verb(value: str) -> bool:
    """Return True when a snippet looks like a complete protocol sentence."""

    return bool(
        re.search(
            r"(?i)\b(?:shall|should|must|may|can|is|are|was|were|be|been|being|means|defines?|describes?|"
            r"specifies?|specified|measured|computed|used|found|shown|meet|meets|differ|differs|provide|"
            r"provided|use|uses|refer|requires?|contains?)\b",
            value,
        )
    )  # 有谓语的片段通常是正文句子，即使很短也不按表格碎片删除。


def _looks_like_visual_only_snippet(value: str) -> bool:
    """Return True for standalone figure/plot snippets that should not be compared."""

    candidate = compact_inline(value)  # 图形判断使用单行文本，和 HTML 片段展示保持一致。
    if _is_table_review_unit(candidate):
        return False  # 结构化表格行是用户需要的表格对比，不能被图片规则删除。
    if _starts_with_figure_sentence_prose(candidate):
        return False  # `Figure 32-2 shows ...` 这类正文句子不能被图形噪声规则删除。
    if _looks_like_numeric_axis_tick_snippet(candidate):
        return True  # 纯数字坐标轴刻度行不参与正文差异。
    if _looks_like_orphan_symbol_fragment_snippet(candidate):
        return True  # `4.3u03 RMS03` 这类纯符号残片不是正文。
    if _looks_like_axis_only_visual_snippet(candidate):
        return True  # 无图题但只有坐标轴/曲线标签的图片碎片也应隐藏。
    if _looks_like_plot_or_formula_snippet(candidate):
        return True  # 密集坐标轴/公式块即使没有 Figure 标题，也属于图形抽取残片。
    if _starts_with_figure_caption(candidate):
        return True  # 纯图题或图形 OCR 块不属于本工具要报告的差异。
    if not re.search(r"(?i)\b(?:figure|fig\.)\s+\d+(?:[-–.]\d+)?\b|图\s*\d+", candidate):
        return False  # 普通正文没有图题锚点时不能按视觉噪声处理。
    if len(candidate) < 120:
        return False  # 短正文引用 Figure 可能是真实编号变化，需要保留。
    return _looks_like_plot_or_formula_snippet(candidate)


def _looks_like_axis_only_visual_snippet(value: str) -> bool:
    """Return True for plot-axis fragments that are not protocol prose."""

    candidate = compact_inline(value)  # 坐标轴碎片在报告中也是单行显示。
    if re.search(r"(?i)\b(?:shall|should|specified|measured|computed|requirements?)\b", candidate):
        return False  # 规范句即使引用图形，也应该继续由正文 diff 报告。
    if _looks_like_short_plot_label_snippet(candidate):
        return True  # IL min / Frequency (GHz) 等短轴标签不是正文。
    if re.match(r"(?i)^[A-Z]\s+\d+\s+\w+\s+where\b", candidate):
        return True  # `F 0 peak where ...` 是公式说明断片，不是正文段落。
    if re.search(r"(?i)\bfrequency\s+range\b", candidate) and re.search(r"(?i)\bpeak-to-peak\b|\bUI\b", candidate):
        return True  # 抽成一行的图轴/表头范围不是正文段落。
    if re.fullmatch(r"(?i)(?:amplitude|frequency|loss|jitter)(?:\s+[A-Z]){1,4}", candidate):
        return True  # `Amplitude X` 这类短轴标签是图片残片，不是正文差异。
    x_count = len(re.findall(r"\bX\b", candidate))  # 多个 X 常来自曲线/坐标标记，不是正文。
    number_count = len(re.findall(r"\d+(?:\.\d+)?", candidate))  # 坐标轴刻度会带来多个数字。
    axis_words = len(re.findall(r"(?i)\b(?:amplitude|frequency|loss|jitter|ui|ghz|db|pp)\b", candidate))
    return x_count >= 2 and number_count >= 3 and axis_words >= 2


def _looks_like_orphan_symbol_fragment_snippet(value: str) -> bool:
    """Return True for pure symbol/value fragments with no prose context."""

    candidate = compact_inline(value)
    if " " not in candidate:
        return False  # 单个符号可能是合法短差异，不能直接删除。
    symbol_piece = r"\d+(?:\.\d+)?[a-z][a-z0-9]*"
    return bool(
        re.fullmatch(
            rf"(?i){symbol_piece}(?:\s+(?:{symbol_piece}|rms\d*|drms\d*|j|\d{{1,3}})){{1,4}}",
            candidate,
        )
    )


def _looks_like_numeric_axis_tick_snippet(value: str) -> bool:
    """Return True for long chart-axis tick sequences such as ``0 5 10 ...``."""

    tokens = compact_inline(value).split()  # 坐标轴刻度通常是一串空格分隔数字。
    if len(tokens) < 8:
        return False
    return all(re.fullmatch(r"[+-]?\d+(?:\.\d+)?", token) for token in tokens)


def _looks_like_short_plot_label_snippet(value: str) -> bool:
    """Return True for compact plot labels that are not standalone prose."""

    candidate = compact_inline(value)  # 短标签必须严格匹配，避免误删正文句子。
    if re.fullmatch(r"(?i)(?:frequency|amplitude|loss|jitter)\s*\([^)]+\)", candidate):
        return True  # `Frequency (GHz)` 这类裸坐标轴标题不是正文差异。
    if re.fullmatch(r"(?i)(?:min|max|typ)", candidate):
        return True  # 图形公式拆出来的单词标签不应进入报告。
    if re.fullmatch(r"(?i)il\s+(?:min|max)(?:\s*/\s*il\s+(?:min|max))?(?:\s+f\s*[×x*]\s*\d+){0,2}", candidate):
        return True
    if re.match(r"(?i)^frequency\s*\([^)]+\)(?:\s+\(\d+(?:[-–]\d+)?\))?(?:\s+\)bd\(|\s+ssol)", candidate):
        return True
    return False


def _starts_with_figure_caption(value: str) -> bool:
    """Return True when a snippet begins with a figure caption."""

    candidate = re.sub(r"^\d{1,3}\s+(?=(?:figure|fig\.|图)\b)", "", compact_inline(value), flags=re.I)  # 去掉前置页边行号。
    match = re.match(r"(?i)^(?:figure|fig\.)\s+\d+(?:[-–.]\d+)?\b(?P<tail>.*)$", candidate)
    if match:
        return not _figure_caption_tail_starts_prose(match.group("tail"))
    chinese_match = re.match(r"^图\s*\d+(?P<tail>.*)$", candidate)
    if chinese_match:
        return not re.match(r"^\s*(?:显示|说明|描述|定义)", chinese_match.group("tail"))
    return False


def _figure_caption_tail_starts_prose(tail: str) -> bool:
    """Return True when text after ``Figure N`` is normal sentence prose."""

    cleaned_tail = tail.lstrip(" .:-–—").strip()  # 去掉图题常用分隔符后看第一个词。
    return bool(
        re.match(
            r"(?i)^(?:shows?|illustrates?|depicts?|describes?|defines?|specifies?|contains?|lists?|is|are|shall|should|must|may|can)\b",
            cleaned_tail,
        )
    )


def _looks_like_plot_or_formula_snippet(value: str) -> bool:
    """Return True for dense chart/formula text emitted from a figure region."""

    lowered = value.casefold()  # 坐标轴词大小写不重要。
    if _looks_like_il_limit_formula_snippet(value):
        return True  # 图里的 IL min/IL max 公式残片按图片噪声处理。
    if _looks_like_symbolic_formula_snippet(value):
        return True  # 独立公式字形碎片不应作为正文差异展示。
    if re.search(r"(?i)\b(?:shows?|illustrates?|depicts?|shall|should|specified|measured|computed|requirements?)\b", value):
        if not re.search(r"(?i)\b(?:il\s*min|il\s*max|frequency\s*\(|\)bd\(|ssol|insertion\s+loss)\b", value):
            return False  # 普通句首 Figure 正文不能只因有数值而被当作图片块。
    number_count = len(re.findall(r"\d+(?:\.\d+)?", value))  # 曲线坐标和公式抽取通常含大量数字。
    formula_mark_count = len(re.findall(r"[∑σ√≤≥]|(?:--+)", value))  # 特殊公式字形是强视觉块信号。
    axis_words = len(
        re.findall(
            r"\b(?:frequency|loss|db|ghz|axis|il\s*min|il\s*max|return\s+loss|insertion\s+loss)\b",
            lowered,
        )
    )  # 图形轴/曲线标签能把视觉块和普通段落分开。
    return number_count >= 8 and (formula_mark_count >= 2 or axis_words >= 2)


def _looks_like_symbolic_formula_snippet(value: str) -> bool:
    """Return True for standalone equation glyph fragments with little prose."""

    candidate = compact_inline(value)  # 报告片段已经按句/行拆分，公式残片通常很短。
    if _looks_like_formula_tail_snippet(candidate):
        return True
    if re.search(r"(?i)\b(?:parameter|characteristic|symbol|condition|value|values|units?|min|max|typ)=", candidate):
        return False  # 结构化表格字段使用等号，但不是图片公式残片。
    word_tokens = re.findall(r"[A-Za-z]{2,}", candidate)
    number_count = len(re.findall(r"\d+(?:\.\d+)?", candidate))
    if re.search(r"=", candidate) and number_count >= 2 and len(word_tokens) <= 6:
        return True  # `SNDR = ...`、`N - 1 ... = 0` 这类拆行公式没有正文语义。
    formula_mark_count = len(re.findall(r"[∑σ√≤≥]|(?:--+)", candidate))
    if formula_mark_count < 2:
        return False
    if number_count >= 2 and len(word_tokens) <= 5:
        return True
    return len(candidate) <= 180 and len(word_tokens) <= 3


def _looks_like_formula_tail_snippet(value: str) -> bool:
    """Return True for trailing equation labels such as ``6) (TBI).``."""

    candidate = compact_inline(value)
    if re.fullmatch(r"(?i)\d+\)\s+\([A-Z]{2,}\)\.?", candidate):
        return True
    if re.fullmatch(r"(?i)\d+\)\s+be\s+positive\.?", candidate):
        return True
    return bool(re.fullmatch(r"(?i)\d+\.\s+(?:[a-z]{1,3}\s+){1,4}.*\(\d+(?:[-–]\d+)?\).*", candidate))


def _looks_like_il_limit_formula_snippet(value: str) -> bool:
    """Return True for split IL-limit equations emitted from chart/figure text."""

    candidate = compact_inline(value)  # 比较层拿到的是片段文本，先压成单行再检查。
    if re.search(r"(?i)\bil\s*(?:min|max)\s*=", candidate):
        return True  # `IL min = ...` 来自图形公式，不应作为正文变化。
    has_frequency_math = bool(re.search(r"(?i)\bf\b|\bGHz\b", candidate))  # f/GHz 锚定频率公式上下文。
    has_formula_symbol = bool(re.search(r"[≤≥<>]|--+", candidate))  # 私有字体符号和分数线锚定公式残片。
    return len(candidate) <= 180 and has_frequency_math and has_formula_symbol


def _starts_with_figure_sentence_prose(value: str) -> bool:
    """Return True for real prose sentences that begin with a Figure reference."""

    candidate = re.sub(r"^\d{1,3}\s+(?=(?:figure|fig\.)\b)", "", compact_inline(value), flags=re.I)
    match = re.match(r"(?i)^(?:figure|fig\.)\s+\d+(?:[-–.]\d+)?\b(?P<tail>.*)$", candidate)
    return bool(match and _figure_caption_tail_starts_prose(match.group("tail")))


def _looks_like_corrupted_figure_reference_snippet(value: str) -> bool:
    """Return True for Figure references visibly polluted by extraction glyphs."""

    candidate = compact_inline(value)
    if not re.search(r"(?i)\bfigure\b", candidate):
        return False
    return bool(
        re.search(r"(?i)\bfigure\s+cd\s+\d|\bfigure\s+\d+\s*-\s*(?:[A-Z]\s+)?cd\b|\bfigure\b.*\bcd\b", candidate)
    )


def _table_review_unit_has_visual_noise(value: str) -> bool:
    """Return True when a structured table row contains page/header or formula residue."""

    candidate = compact_inline(value)
    if not _is_table_review_unit(candidate):
        return False
    if re.search(r"(?i)\bimplementation\s+agreement\s+oif-cei\b", candidate):
        return True
    if re.search(r"(?i)\bSNDR\s*=", candidate) and re.search(r"\bSignal\s+0\b|\(\d+(?:[-–]\d+)?\)", candidate):
        return True
    return False


def _looks_like_margin_line_number_run(value: str) -> bool:
    """Return True for extracted margin runs such as ``1 2 3 ... 49``."""

    tokens = re.findall(r"\d+", value)  # 页边行号抽取后通常是一串纯数字 token。
    if len(tokens) < 12:
        return False
    numbers = [int(token) for token in tokens if token.isdigit()]  # 转成数字后可以检查 1~49 连续段。
    if not numbers or min(numbers) < 1 or max(numbers) > 49:
        return False
    non_number_text = re.sub(r"[\d\s]+", "", value)  # 除数字和空白外仍有大量文本时不能按行号删除。
    if len(non_number_text) > 24:
        return False
    unique_numbers = sorted(set(numbers))  # 去重后检查最长连续区间。
    longest_run = 1  # 至少一个数字时连续段长度从 1 开始。
    current_run = 1  # 当前连续段长度。
    for previous, current in zip(unique_numbers, unique_numbers[1:], strict=False):
        if current == previous + 1:
            current_run += 1
        else:
            longest_run = max(longest_run, current_run)
            current_run = 1
    return max(longest_run, current_run) >= 10


def _candidate_key(candidate: _DeltaCandidate) -> tuple[str, str]:
    """Build a stable dedupe key for one candidate."""

    if candidate.pair:
        return (candidate.kind, f"{candidate.pair.old}\n---\n{candidate.pair.new}")
    return (candidate.kind, candidate.text)


def _substantive_priority(*values: str) -> int:
    """Rank protocol-risky deltas above ordinary wording changes."""

    joined = "\n".join(values)
    if _has_numeric_token(joined):
        return 3
    if _has_identifier_token(joined):
        return 2
    return 1


def _has_numeric_token(value: str) -> bool:
    """Return True when a snippet contains a number-like protocol value."""

    normalized = canonicalize_chinese_number_expressions(value)
    return bool(
        _NUMBER_TOKEN_RE.search(normalized)
        or any(
            canonicalize_number_word_token(token)
            for token in _REVIEW_TOKEN_RE.findall(normalized)
        )
    )


def _has_identifier_token(value: str) -> bool:
    """Return True for compact identifiers such as TS1/TS2, P5, or Gen6."""

    normalized = normalize_for_similarity(value)
    return bool(re.search(r"\b[a-z]+[0-9]+(?:[-_/][a-z0-9]+)*\b", normalized))


def _source_page_count(extraction: ExtractionResult) -> int:
    """Return the source PDF page count, inferring it for hand-built tests."""

    if extraction.total_pages:
        return extraction.total_pages
    if not extraction.pages:
        return 0
    return max(page.page_number for page in extraction.pages)


def _selected_start_page(extraction: ExtractionResult) -> int | None:
    """Return the first extracted source page, preserving explicit metadata."""

    if extraction.selected_start_page is not None:
        return extraction.selected_start_page
    if not extraction.pages:
        return None
    return min(page.page_number for page in extraction.pages)


def _selected_end_page(extraction: ExtractionResult) -> int | None:
    """Return the last extracted source page, preserving explicit metadata."""

    if extraction.selected_end_page is not None:
        return extraction.selected_end_page
    if not extraction.pages:
        return None
    return max(page.page_number for page in extraction.pages)


def _split_line_preserving_numbers(line: str) -> list[str]:
    """Split one line into sentence-ish units without breaking decimals.

    Protocols often use punctuation inside meaningful values: ``3.0 V``,
    ``1.2.3``, dates, firmware versions, and model names. A tiny state machine is
    clearer and safer here than a broad regular expression.
    """

    units: list[str] = []
    current: list[str] = []
    hard_endings = set("。！？；;!?")

    for index, char in enumerate(line):
        current.append(char)
        should_split = False
        if char in hard_endings:
            should_split = True
        elif char == ".":
            previous_char = line[index - 1] if index > 0 else ""
            next_char = line[index + 1] if index + 1 < len(line) else ""
            previous_text = line[:index].strip()
            if previous_char.isdigit() and next_char.isdigit():
                should_split = False
            elif previous_char.isdigit() and _next_non_space_char(line, index + 1).isdigit():
                should_split = False
            elif (
                re.fullmatch(r"\d{1,3}", previous_text)
                and next_char
                and next_char.isspace()
            ):
                should_split = False
            elif (
                re.fullmatch(r"[A-Za-z]", previous_text)
                and next_char
                and next_char.isspace()
            ):
                should_split = False
            elif next_char and not next_char.isspace():
                should_split = False
            else:
                should_split = True

        if should_split:
            unit = "".join(current).strip()
            if unit:
                units.append(unit)
            current = []

    tail = "".join(current).strip()
    if tail:
        units.append(tail)
    return units


def _next_non_space_char(value: str, start_index: int) -> str:
    """Return the next non-space character after a position, if any."""

    for char in value[start_index:]:
        if not char.isspace():
            return char
    return ""


def _first_units(
    text: str,
    max_snippets: int,
    *,
    suppressed_table_unit_keys: set[str] | None = None,
) -> tuple[list[str], int]:
    """Return leading snippets for added or deleted whole sections."""

    table_unit_keys = suppressed_table_unit_keys or set()  # 整章新增/删除也只隐藏有视觉摘要覆盖的表格行。
    review_units = _paragraph_review_units(text, suppressed_table_unit_keys=table_unit_keys)  # 未覆盖表格行继续作为文字兜底。
    units = [_report_unit(unit) for unit in review_units]  # 报告片段只做展示格式清洗，不改变比较身份。
    if max_snippets <= 0:
        return [], len(units)
    return units[:max_snippets], max(0, len(units) - max_snippets)


def _change_sort_key(change: SectionChange) -> tuple[int, int, str]:
    """Sort by new-page location first, then deleted old-page location."""

    section = change.new_section or change.old_section
    page = section.start_page if section else 0
    type_order = {"modified": 0, "added": 1, "deleted": 2, "unchanged": 3}
    return (page, type_order.get(change.change_type, 9), change.report_location)
