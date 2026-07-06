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
)
from .pdf_extract import extract_pdf_text
from .sectioning import section_document
from .text_utils import (
    canonicalize_chinese_number_expressions,
    canonicalize_number_word_token,
    canonicalize_number_word_tokens,
    normalize_for_similarity,
    normalize_line,
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
    changes = compare_sections(old_sections, new_sections, options)
    warnings = list(old_extraction.warnings) + list(new_extraction.warnings)
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
    )


def compare_sections(
    old_sections: list[Section],
    new_sections: list[Section],
    options: DiffOptions,
) -> list[SectionChange]:
    """Match old/new sections and classify section-level changes."""

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
) -> tuple[list[str], list[str], list[SnippetPair], int]:
    """Create compact added/removed/replaced snippets for one section."""

    old_units = _split_units(old_text)
    new_units = _split_units(new_text)
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
    return _drop_duplicate_raw_table_units(units)


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
        "maximum",
        "minimum",
        "parameter",
        "receiver",
        "resistance",
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

    if len(unit) < 160:  # 短文本在 diff 层不兜底删除，短行已在抽取层处理。
        return False
    number_count = len(_NUMBER_TOKEN_RE.findall(unit))  # 原始表格块通常含有大量数值。
    if number_count < 6:
        return False
    words = set(re.findall(r"[A-Za-z]+", unit.casefold()))  # 表格词数量用于区分正文长段落和参数块。
    table_word_count = len(words & _RAW_TABLE_NOISE_WORDS)
    if table_word_count < 2:
        return False
    tokens = _raw_table_noise_tokens(unit)  # 和结构化表格行做 token 覆盖判断。
    overlap = len(tokens & table_tokens)
    required_overlap = min(12, max(6, len(tokens) // 6))
    if overlap >= required_overlap:
        return True
    return overlap >= 4 and table_word_count >= 4 and number_count >= 10 and _has_table_block_phrase(unit)


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
    r"[+-]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"
)
_REVIEW_TOKEN_RE = re.compile(
    r"<=|>=|≤|≥|(?<!-)[<>](?!-)|="
    r"|[+-]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"
    r"|[a-zµμ]+[a-z0-9µμ]*(?:[-_/][a-z0-9µμ]+)*|[\u4e00-\u9fff]+"
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

    normalized = normalize_for_similarity(value)
    normalized = canonicalize_chinese_number_expressions(normalized)
    normalized = normalized.replace("µ", "u").replace("μ", "u")
    normalized = normalized.replace("&", " and ")
    normalized = normalized.replace("≤", "<=").replace("≥", ">=")
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

    return normalize_line(value).startswith("表格行:")  # 前缀是内部稳定契约，不暴露新的外部 API。


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
    if text.startswith("表格行:"):
        text = text[len("表格行:") :].strip()
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

    unique_candidates = _dedupe_candidates(candidates)
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


def _dedupe_candidates(candidates: list[_DeltaCandidate]) -> list[_DeltaCandidate]:
    """Remove duplicate snippets while preserving first occurrence and priority."""

    by_key: dict[tuple[str, str], _DeltaCandidate] = {}
    for candidate in candidates:
        key = _candidate_key(candidate)
        existing = by_key.get(key)
        if existing is None or candidate.priority > existing.priority:
            by_key[key] = candidate
    return sorted(by_key.values(), key=lambda item: item.order)


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


def _first_units(text: str, max_snippets: int) -> tuple[list[str], int]:
    """Return leading snippets for added or deleted whole sections."""

    units = [_report_unit(unit) for unit in _split_units(text)]
    if max_snippets <= 0:
        return [], len(units)
    return units[:max_snippets], max(0, len(units) - max_snippets)


def _change_sort_key(change: SectionChange) -> tuple[int, int, str]:
    """Sort by new-page location first, then deleted old-page location."""

    section = change.new_section or change.old_section
    page = section.start_page if section else 0
    type_order = {"modified": 0, "added": 1, "deleted": 2, "unchanged": 3}
    return (page, type_order.get(change.change_type, 9), change.report_location)
