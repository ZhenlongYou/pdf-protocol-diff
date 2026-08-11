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
from collections import Counter
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .formula_visuals import normalized_formula_key
from .models import (
    DiffOptions,
    DiffResult,
    ExtractionResult,
    FormulaChange,
    FormulaVisual,
    PageText,
    Section,
    SectionChange,
    SnippetPair,
    TableVisual,
    VisualWatchdogAudit,
    snapshot_page_extraction_audit,
)
from .page_ocr import normalize_ocr_language
from .pdf_extract import (
    _looks_like_known_atomic_unit,
    _looks_like_missing_table_value,
    _looks_like_pure_numeric_table_entry,
    extract_pdf_text,
)
from .quality import (
    PairAssessment,
    ReliabilityState,
    assess_pair,
    build_provenance,
    provenance_inputs_are_identical,
)
from .sectioning import section_document
from .table_codec import (
    decode_table_cell,
    encode_table_field,
    split_table_cells,
    split_table_field,
)
from .text_utils import (
    TABLE_NUMBER_DASH_CLASS,
    canonicalize_chinese_number_expressions,
    canonicalize_number_word_token,
    canonicalize_number_word_tokens,
    compact_inline,
    has_measurement_context,
    identifier_boundary_signatures,
    is_english_cardinal_word,
    is_known_engineering_symbol_letter_suffix,
    mark_english_cardinal_list_commas,
    micro_identifier_signatures,
    normalize_for_similarity,
    normalize_line,
    normalize_table_number_dashes,
    reader_symbol_mapping_key,
)
from .visual_watchdog import detect_visual_review_items


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


@dataclass(frozen=True)
class _ExactTableTextMatch:
    """One unique, whole-review-unit table sequence found on the raw-text side."""

    section_start_page: int
    raw_unit_keys: tuple[str, ...]
    matched_row_count: int
    serialized_text: str
    leading_text: str


@dataclass(frozen=True)
class _SourceTableCaptionEvidence:
    """The exact source review units carrying a caption and its prose lead-in."""

    unit_keys: tuple[str, ...]
    leading_text: str


def run_diff(old_pdf: str | Path, new_pdf: str | Path, options: DiffOptions) -> DiffResult:
    """Run the complete extraction, sectioning, and comparison pipeline."""

    options = replace(
        options,
        ocr_language=normalize_ocr_language(options.ocr_language),
    )
    old_extraction = extract_pdf_text(
        old_pdf,
        start_page=options.old_start_page,
        end_page=options.old_end_page,
        ocr_language=options.ocr_language,
        layout_backend=options.layout_backend,
    )
    new_extraction = extract_pdf_text(
        new_pdf,
        start_page=options.new_start_page,
        end_page=options.new_end_page,
        ocr_language=options.ocr_language,
        layout_backend=options.layout_backend,
    )
    result = compare_extractions(old_extraction, new_extraction, options)
    if options.visual_watchdog:
        visual_review_items, visual_warnings, visual_audit = detect_visual_review_items(
            old_extraction,
            new_extraction,
            semantic_result=result,
        )
    else:
        visual_review_items = []
        visual_warnings = ["视觉漏检哨兵已关闭；本次结果不能证明没有像素层变化。"]
        visual_audit = VisualWatchdogAudit(
            enabled=False,
            attempted=False,
            backend_available=None,
            eligible_page_pair_count=0,
            checked_page_pair_count=0,
            failed_page_pair_count=0,
            ambiguous_page_count=0,
            excluded_region_count=0,
            complete=False,
            source_hashes_match=None,
        )
    provenance = (
        replace(result.provenance, visual_watchdog_audit=visual_audit)
        if result.provenance is not None
        else None
    )
    return replace(
        result,
        visual_review_items=visual_review_items,
        warnings=[*result.warnings, *visual_warnings],
        assessment=_assessment_with_visual_review(
            result.assessment,
            visual_review_count=len(visual_review_items),
            audit=visual_audit,
        ),
        provenance=provenance,
    )


def _assessment_with_visual_review(
    assessment: PairAssessment | None,
    *,
    visual_review_count: int,
    audit: VisualWatchdogAudit,
) -> PairAssessment | None:
    """Prevent all-clear when visual evidence is unexplained or was not checked."""

    if assessment is None:
        return assessment
    reasons: list[str] = []
    if visual_review_count > 0:
        reasons.append(
            f"发现 {visual_review_count} 页未解释的视觉变化；"
            "文字、表格和公式差异不足以覆盖这些源像素变化。"
        )
    if not audit.complete:
        if not audit.enabled:
            reasons.append("视觉漏检哨兵已关闭，本次未完成像素层核对。")
        else:
            reasons.append(
                "视觉漏检哨兵未完整覆盖可核对页面："
                f"已核对 {audit.checked_page_pair_count}/"
                f"{audit.eligible_page_pair_count} 对，失败 {audit.failed_page_pair_count} 对，"
                f"未安全配对页 {audit.ambiguous_page_count} 个。"
            )
    if not reasons:
        return assessment
    state = assessment.state
    headline = assessment.headline
    if state is ReliabilityState.RELIABLE:
        state = ReliabilityState.DEGRADED
        headline = (
            "需人工复核：存在语义层未解释的视觉变化"
            if visual_review_count
            else "需人工复核：视觉漏检核对未完整完成"
        )
    return replace(
        assessment,
        state=state,
        headline=headline,
        reasons=(*assessment.reasons, *reasons),
        allows_no_difference_conclusion=False,
    )


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

    old_table_visuals = _table_visuals_with_cross_page_captions(
        old_extraction.table_visuals,
        old_extraction.pages,
    )
    new_table_visuals = _table_visuals_with_cross_page_captions(
        new_extraction.table_visuals,
        new_extraction.pages,
    )
    old_reconciliation_sections = section_document(old_extraction)
    new_reconciliation_sections = section_document(new_extraction)
    old_sections = section_document(
        _extraction_without_page_bound_table_captions(
            old_extraction,
            original_tables=old_extraction.table_visuals,
            repaired_tables=old_table_visuals,
        )
    )
    new_sections = section_document(
        _extraction_without_page_bound_table_captions(
            new_extraction,
            original_tables=new_extraction.table_visuals,
            repaired_tables=new_table_visuals,
        )
    )  # 读者比较只消费同页同次数、已有视觉表证明的 caption；精确表重建仍使用未消费的原始审计单元。
    old_header_section = _running_header_section(old_extraction)
    new_header_section = _running_header_section(new_extraction)
    old_header_section, new_header_section = _ignore_trailing_header_page_only_difference(
        old_header_section,
        new_header_section,
        old_extraction,
        new_extraction,
    )
    if old_header_section is not None:
        old_sections.insert(0, old_header_section)
    if new_header_section is not None:
        new_sections.insert(0, new_header_section)
    assessment = assess_pair(old_extraction, new_extraction, old_sections, new_sections)
    provenance = build_provenance(old_extraction, new_extraction, options)
    identical_inputs = provenance_inputs_are_identical(provenance)
    old_table_visuals = _table_visuals_with_text_fallbacks(old_table_visuals, old_extraction.pages)
    new_table_visuals = _table_visuals_with_text_fallbacks(new_table_visuals, new_extraction.pages)
    (
        old_table_visuals,
        new_table_visuals,
        old_reconciled_table_unit_keys,
        new_reconciled_table_unit_keys,
    ) = _reconcile_exact_cross_side_table_text(
        old_table_visuals,
        new_table_visuals,
        old_sections=old_reconciliation_sections,
        new_sections=new_reconciliation_sections,
        old_pages=old_extraction.pages,
        new_pages=new_extraction.pages,
    )  # 一侧漏检表格时，只凭对侧完整结构化序列在原始审阅单元中的唯一精确命中补齐证据。
    old_covered_table_unit_keys = _covered_table_visual_row_keys(old_table_visuals)
    new_covered_table_unit_keys = _covered_table_visual_row_keys(new_table_visuals)
    old_covered_table_unit_keys.update(old_reconciled_table_unit_keys)
    new_covered_table_unit_keys.update(new_reconciled_table_unit_keys)
    formula_changes = (
        []
        if identical_inputs
        else _compare_formula_visuals(
            old_extraction.formula_visuals,
            new_extraction.formula_visuals,
        )
    )
    changes = (
        []
        if identical_inputs
        else compare_sections(
            old_sections,
            new_sections,
            options,
            suppressed_old_table_unit_keys=old_covered_table_unit_keys,
            suppressed_new_table_unit_keys=new_covered_table_unit_keys,
        )
    )  # 同一快照页窗不存在语义差异；不同输入仍只在各自版本隐藏已证明的表格原文单元。
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
        old_formula_visuals=list(old_extraction.formula_visuals),
        new_formula_visuals=list(new_extraction.formula_visuals),
        formula_changes=formula_changes,
        assessment=assessment,
        provenance=provenance,
        old_extraction_audit=snapshot_page_extraction_audit(old_extraction),  # 压缩为标量快照后释放旧页面/块正文的长生命周期引用。
        new_extraction_audit=snapshot_page_extraction_audit(new_extraction),  # 新版同样只保留报告审计所需字段，不改变比较正文结果。
    )


def _running_header_section(extraction: ExtractionResult) -> Section | None:
    """Build one auditable comparison unit from coordinate-proven headers."""

    page_observations: list[tuple[int, str]] = []
    for page in extraction.pages:
        seen_on_page: set[str] = set()
        for value in page.running_header_texts:
            compact = compact_inline(value)
            if not compact or compact in seen_on_page:
                continue
            seen_on_page.add(compact)
            page_observations.append((page.page_number, compact))
    if not page_observations:
        return None
    header_page_count = len({page_number for page_number, _text in page_observations})
    occurrence_counts = Counter(text for _page_number, text in page_observations)
    emitted_full_page_values: set[str] = set()
    observed: list[tuple[int, str]] = []
    for page_number, text in page_observations:
        if occurrence_counts[text] == header_page_count:
            if text in emitted_full_page_values:
                continue
            emitted_full_page_values.add(text)
        observed.append((page_number, text))
    # 每个页眉页都相同的固定家具只保留一次，避免页数变化制造差异；只在同一
    # 文档存在部分页采用不同精确值时保留逐页次数，从而仍能发现 ALPHA/alpha
    # 这类技术标识符的分布变化。
    start_page = min(page_number for page_number, _text in observed)
    end_page = max(
        page.page_number
        for page in extraction.pages
        if page.running_header_texts
    )
    heading = "运行页眉（坐标证据）"
    return Section(
        section_id="running-header-evidence",
        heading=heading,
        title=heading,
        level=1,
        heading_path=(heading,),
        number_path=(),
        start_page=start_page,
        end_page=end_page,
        body="\n".join(text for _page_number, text in observed),
        role="technical",
        page_bodies=tuple((page_number, text) for page_number, text in observed),
    )


def _ignore_trailing_header_page_only_difference(
    old_section: Section | None,
    new_section: Section | None,
    old_extraction: ExtractionResult,
    new_extraction: ExtractionResult,
) -> tuple[Section | None, Section | None]:
    """Ignore header observations that exist only on trailing unmatched pages.

    This is authorized only when one selected page set is a strict trailing
    extension of the other, every overlapping page has the same exact
    coordinate-proven observations, and every extra-page observation repeats
    a value proven on at least two common pages.  A new exact value therefore
    remains an auditable technical change even when it first appears on a tail
    page.
    """

    if old_section is None or new_section is None:
        return old_section, new_section
    old_page_numbers = {page.page_number for page in old_extraction.pages}
    new_page_numbers = {page.page_number for page in new_extraction.pages}
    common_page_numbers = old_page_numbers & new_page_numbers
    old_only_pages = old_page_numbers - new_page_numbers
    new_only_pages = new_page_numbers - old_page_numbers
    if not common_page_numbers or bool(old_only_pages) == bool(new_only_pages):
        return old_section, new_section  # 页窗相同或两侧都不对齐时，不存在单侧尾页证明。
    if old_only_pages and min(old_only_pages) <= max(new_page_numbers):
        return old_section, new_section
    if new_only_pages and min(new_only_pages) <= max(old_page_numbers):
        return old_section, new_section
    old_observations = _running_header_observations_by_page(old_extraction)
    new_observations = _running_header_observations_by_page(new_extraction)
    if any(
        old_observations.get(page_number, ())
        != new_observations.get(page_number, ())
        for page_number in common_page_numbers
    ):
        return old_section, new_section
    common_value_counts = Counter(
        value
        for page_number in common_page_numbers
        for value in old_observations.get(page_number, ())
    )
    stable_common_values = {
        value
        for value, count in common_value_counts.items()
        if count >= 2
    }
    extra_extraction_observations = (
        old_observations if old_only_pages else new_observations
    )
    extra_page_numbers = old_only_pages or new_only_pages
    if any(
        value not in stable_common_values
        for page_number in extra_page_numbers
        for value in extra_extraction_observations.get(page_number, ())
    ):
        return old_section, new_section  # 尾页出现新的精确技术页眉值时仍必须失败可见。
    shared_values = tuple(
        text
        for page_number, text in old_section.page_bodies
        if page_number in common_page_numbers
    )
    if not shared_values:
        return old_section, new_section
    shared_body = "\n".join(shared_values)
    return (
        replace(old_section, body=shared_body),
        replace(new_section, body=shared_body),
    )


def _running_header_observations_by_page(
    extraction: ExtractionResult,
) -> dict[int, tuple[str, ...]]:
    """Return exact normalized header observations for every selected page."""

    observations: dict[int, tuple[str, ...]] = {}
    for page in extraction.pages:
        seen: set[str] = set()
        values: list[str] = []
        for value in page.running_header_texts:
            compact = compact_inline(value)
            if compact and compact not in seen:
                seen.add(compact)
                values.append(compact)
        observations[page.page_number] = tuple(values)
    return observations


def _compare_formula_visuals(
    old_formulas: list[FormulaVisual],
    new_formulas: list[FormulaVisual],
) -> list[FormulaChange]:
    """Pair displayed formulas by geometry-aware semantics, then report deltas."""

    changes: list[FormulaChange] = []
    used_old: set[int] = set()
    used_new: set[int] = set()
    old_by_key: dict[str, list[int]] = {}
    new_by_key: dict[str, list[int]] = {}
    for index, formula in enumerate(old_formulas):
        old_by_key.setdefault(normalized_formula_key(formula.semantic_text), []).append(index)
    for index, formula in enumerate(new_formulas):
        new_by_key.setdefault(normalized_formula_key(formula.semantic_text), []).append(index)

    for key in sorted(set(old_by_key) & set(new_by_key)):
        for old_index, new_index in zip(old_by_key[key], new_by_key[key], strict=False):
            old_formula = old_formulas[old_index]
            new_formula = new_formulas[new_index]
            used_old.add(old_index)
            used_new.add(new_index)
            visual_similarity = _formula_visual_similarity(old_formula, new_formula)
            if old_formula.formula_number != new_formula.formula_number:
                changes.append(
                    FormulaChange(
                        change_type="modified",
                        old_formula=old_formula,
                        new_formula=new_formula,
                        similarity=1.0,
                        visual_similarity=visual_similarity,
                        reason="公式主体文字一致，公式编号发生顺延或调整。",
                    )
                )
            elif visual_similarity < 0.78:
                changes.append(
                    FormulaChange(
                        change_type="review",
                        old_formula=old_formula,
                        new_formula=new_formula,
                        similarity=1.0,
                        visual_similarity=visual_similarity,
                        reason="可抽取公式文字一致，但源截图结构差异较大，需回到源 PDF 核对根号、分式或矢量符号。",
                    )
                )

    fuzzy_candidates: list[tuple[float, float, int, int]] = []
    for old_index, old_formula in enumerate(old_formulas):
        if old_index in used_old:
            continue
        old_key = normalized_formula_key(old_formula.semantic_text)
        for new_index, new_formula in enumerate(new_formulas):
            if new_index in used_new:
                continue
            new_key = normalized_formula_key(new_formula.semantic_text)
            similarity = difflib.SequenceMatcher(None, old_key, new_key).ratio()
            score = similarity + (
                0.08 if old_formula.formula_number == new_formula.formula_number else 0.0
            )
            if similarity >= 0.55 and score >= 0.72:
                fuzzy_candidates.append((score, similarity, old_index, new_index))
    for _score, similarity, old_index, new_index in sorted(
        fuzzy_candidates,
        key=lambda item: (-item[0], item[2], item[3]),
    ):
        if old_index in used_old or new_index in used_new:
            continue
        old_formula = old_formulas[old_index]
        new_formula = new_formulas[new_index]
        used_old.add(old_index)
        used_new.add(new_index)
        changes.append(
            FormulaChange(
                change_type="modified",
                old_formula=old_formula,
                new_formula=new_formula,
                similarity=similarity,
                visual_similarity=_formula_visual_similarity(old_formula, new_formula),
                reason="公式可抽取文字或坐标已证明的上下标发生变化。",
            )
        )

    for old_index, old_formula in enumerate(old_formulas):
        if old_index not in used_old:
            changes.append(
                FormulaChange(
                    change_type="deleted",
                    old_formula=old_formula,
                    new_formula=None,
                    similarity=0.0,
                    visual_similarity=0.0,
                    reason="旧版显示公式在新版中没有可靠配对。",
                )
            )
    for new_index, new_formula in enumerate(new_formulas):
        if new_index not in used_new:
            changes.append(
                FormulaChange(
                    change_type="added",
                    old_formula=None,
                    new_formula=new_formula,
                    similarity=0.0,
                    visual_similarity=0.0,
                    reason="新版出现未能与旧版可靠配对的显示公式。",
                )
            )
    return sorted(changes, key=_formula_change_sort_key)


def _formula_visual_similarity(old: FormulaVisual, new: FormulaVisual) -> float:
    """Convert two 64-bit dHashes into a bounded review-only similarity."""

    if not old.image_dhash or not new.image_dhash:
        return 1.0  # 截图缺失已有抽取警告，不再伪造视觉变化。
    try:
        distance = (int(old.image_dhash, 16) ^ int(new.image_dhash, 16)).bit_count()
    except ValueError:
        return 1.0
    return max(0.0, 1.0 - distance / 64.0)


def _formula_change_sort_key(change: FormulaChange) -> tuple[int, str, int]:
    """Order formula findings by visible source location and side."""

    formula = change.new_formula or change.old_formula
    assert formula is not None
    side_rank = 0 if change.old_formula is not None else 1
    return formula.page_number, formula.formula_number, side_rank


def _covered_table_visual_row_keys(*table_groups: list[TableVisual]) -> set[str]:
    """Return normalized structured table rows already shown in visual summaries."""

    keys: set[str] = set()  # 保存可从下方截图+结构化摘要复核的表格行身份。
    for tables in table_groups:
        for table in tables:
            for row_text in table.row_texts:
                if _is_table_review_unit(row_text):
                    keys.add(_review_unit_key(row_text))  # 使用比较层统一 key，避免空白和大小写差异导致漏匹配。
    return keys  # 表题和 `Table N.` 不能按全文字符串全局隐藏；远端同文正文仍可能发生真实增删。


def _extraction_without_page_bound_table_captions(
    extraction: ExtractionResult,
    *,
    original_tables: list[TableVisual],
    repaired_tables: list[TableVisual],
) -> ExtractionResult:
    """Remove only caption occurrences proven by a visual on the same source page."""

    source_pages = {page.page_number: page for page in extraction.pages}
    caption_options: dict[int, list[tuple[str, str, tuple[int, ...]]]] = {}
    for original, repaired in zip(original_tables, repaired_tables, strict=True):
        if not repaired.title:
            continue
        caption_page = repaired.page_number
        if not original.title and repaired.is_continuation:
            caption_page -= 1  # 跨页修复的 caption 来自上一页最后一行。
        table_number = _table_caption_number(repaired.title)
        source_line_indexes = (
            _cross_page_caption_source_line_indexes(
                source_pages.get(caption_page),
                repaired.title,
            )
            if not original.title and repaired.is_continuation
            else ()
        )
        caption_options.setdefault(caption_page, []).append(
            (
                _review_unit_key(repaired.title),
                _review_unit_key(f"Table {table_number}.") if table_number else "",
                source_line_indexes,
            )
        )

    pages: list[PageText] = []
    for page in extraction.pages:
        source_keys = [_review_unit_key(line) for line in page.text.splitlines()]
        available = Counter(source_keys)
        remaining: Counter[str] = Counter()
        consumed_line_indexes: set[int] = set()
        for full_key, short_key, source_line_indexes in caption_options.get(page.page_number, []):
            if source_line_indexes:
                consumed_line_indexes.update(source_line_indexes)
                continue
            selected_key = full_key if available[full_key] > 0 else short_key
            if selected_key and available[selected_key] > 0:
                available[selected_key] -= 1
                remaining[selected_key] += 1
        # 完整 title 与 `Table N.` 是同一视觉表 caption 的替代表达，只能消费其中一个 occurrence。
        kept_lines: list[str] = []
        for line_index, line in enumerate(page.text.splitlines()):
            if line_index in consumed_line_indexes:
                continue
            key = _review_unit_key(line)
            if remaining[key] > 0:
                remaining[key] -= 1
                continue
            kept_lines.append(line)
        pages.append(replace(page, text="\n".join(kept_lines)))
    return replace(extraction, pages=pages)


def _cross_page_caption_source_line_indexes(
    source_page: PageText | None,
    title: str,
) -> tuple[int, ...]:
    """Locate a reconstructed caption and its proven trailing page furniture."""

    if (
        source_page is None
        or not source_page.text
        or _review_unit_key(_last_table_caption_line(source_page))
        != _review_unit_key(title)
    ):
        return ()
    raw_lines = source_page.text.splitlines()
    observed = [
        (index, compact_inline(line))
        for index, line in enumerate(raw_lines)
        if compact_inline(line)
    ]
    content_end = len(observed)
    while (
        content_end > max(0, len(observed) - 8)
        and _cross_page_caption_trailing_line_is_furniture(observed[content_end - 1][1])
    ):
        content_end -= 1
    if content_end <= 0:
        return ()
    for start in range(content_end - 1, max(-1, content_end - 3), -1):
        first = _caption_without_embedded_line_number(
            observed[start][1],
            [line for _index, line in observed[start + 1 :]],
            source_page=source_page,
        )
        joined = compact_inline(
            " ".join([first, *(line for _index, line in observed[start + 1 : content_end])])
        )
        if _review_unit_key(joined) == _review_unit_key(title):
            # The caption recognizer has already proved every remaining tail line
            # to be a bare line number or running footer.  Consume that evidence
            # from the reader comparison together with the caption so revision-
            # dependent page numbering cannot surface as a technical change.
            # The original extraction and JSON audit snapshot remain untouched.
            return tuple(index for index, _line in observed[start:])
    return ()


def _table_visuals_with_cross_page_captions(
    table_visuals: list[TableVisual],
    pages: list[PageText],
) -> list[TableVisual]:
    """Attach a bottom-of-page caption to a proven next-page continuation table."""

    page_by_number = {page.page_number: page for page in pages}
    repaired: list[TableVisual] = []
    for table in table_visuals:
        if table.title or not table.is_continuation or not _table_starts_near_page_top(table):
            repaired.append(table)
            continue
        previous_page = page_by_number.get(table.page_number - 1)
        caption = _last_table_caption_line(previous_page) if previous_page else ""
        repaired.append(replace(table, title=caption) if caption else table)
    return repaired


def _table_starts_near_page_top(table: TableVisual) -> bool:
    """Require page geometry before inheriting a caption across a page break."""

    if table.page_bbox is None:
        return False
    _left, page_top, _right, page_bottom = table.page_bbox
    page_height = page_bottom - page_top
    return page_height > 0 and (table.bbox[1] - page_top) / page_height <= 0.22


def _last_table_caption_line(source: str | PageText) -> str:
    """Return a page-bottom English table caption, never intervening prose."""

    source_page = source if isinstance(source, PageText) else None
    text = source.text if source_page is not None else source
    lines = [compact_inline(line) for line in text.splitlines() if compact_inline(line)]
    if not lines:
        return ""
    caption_pattern = re.compile(
        rf"(?i)table\s+\d+(?:\s*{TABLE_NUMBER_DASH_CLASS}\s*\d+)?\s*[.:]\s*.+"
    )
    # 先从页尾剥离至多七条形态明确的行号/运行页脚。遇到任何普通正文即停止，禁止
    # 为了寻找更早的 Table 字样而跨越一句真实要求。
    content_end = len(lines)
    while (
        content_end > max(0, len(lines) - 8)
        and _cross_page_caption_trailing_line_is_furniture(lines[content_end - 1])
    ):
        content_end -= 1
    if content_end <= 0:
        return ""
    last_content_index = content_end - 1
    direct = _caption_without_embedded_line_number(
        lines[last_content_index],
        lines[last_content_index + 1 :],
        source_page=source_page,
    )
    if caption_pattern.fullmatch(direct):
        return direct

    # 标题偶尔在最后一个 title-case 短语前换行。只拼接紧邻页尾的 1--2 条标题式
    # continuation；普通小写句子、URL 或规范性动词都不具备该证据。
    for start_index in range(last_content_index - 1, max(-1, last_content_index - 3), -1):
        if start_index < 0:
            break
        continuation = lines[start_index + 1 : content_end]
        prefix = _caption_without_embedded_line_number(
            lines[start_index],
            lines[start_index + 1 :],
            source_page=source_page,
        )
        running_prefix = prefix
        continuation_is_caption = bool(continuation)
        for line in continuation:
            if not _cross_page_caption_continuation_is_title_phrase(
                line,
                prefix=running_prefix,
            ):
                continuation_is_caption = False
                break
            running_prefix = compact_inline(f"{running_prefix} {line}")
        if not continuation_is_caption:
            continue
        joined = compact_inline(" ".join((prefix, *continuation)))
        if caption_pattern.fullmatch(joined):
            return joined
    return ""


def _caption_without_embedded_line_number(
    candidate: str,
    following_lines: list[str],
    *,
    source_page: PageText | None = None,
) -> str:
    """Strip a glued line number only with consecutive and coordinate proof."""

    embedded = re.search(r"\s+(\d{1,3})$", candidate)
    next_number = next(
        (int(line) for line in following_lines if re.fullmatch(r"\d{1,3}", line)),
        None,
    )
    if (
        embedded is not None
        and next_number is not None
        and next_number == int(embedded.group(1)) + 1
        and _page_proves_glued_gutter_line_number(source_page, candidate)
    ):
        return candidate[: embedded.start()].rstrip()
    return candidate


def _page_proves_glued_gutter_line_number(
    page: PageText | None,
    candidate: str,
) -> bool:
    """Bind a trailing number to an aligned gutter sequence using block geometry."""

    if page is None or page.page_bbox is None or not page.blocks:
        return False
    match = re.search(r"\s+(\d{1,3})$", compact_inline(candidate))
    if match is None:
        return False
    expected_next = str(int(match.group(1)) + 1)
    page_left, _page_top, page_right, _page_bottom = page.page_bbox
    page_width = page_right - page_left
    if page_width <= 0:
        return False
    ordered_blocks = sorted(page.blocks, key=lambda block: block.reading_order)
    for index, block in enumerate(ordered_blocks):
        if compact_inline(block.text) != compact_inline(candidate):
            continue
        for following in ordered_blocks[index + 1 : index + 5]:
            if compact_inline(following.text) != expected_next:
                continue
            close_vertically = 0 <= following.bbox[1] - block.bbox[1] <= 48.0
            right_gutter = bool(
                "right" in page.ambiguous_line_number_sides
                and block.bbox[2] >= page_left + page_width * 0.88
                and following.bbox[2] >= page_left + page_width * 0.88
                and abs(block.bbox[2] - following.bbox[2]) <= 3.0
            )
            left_gutter = bool(
                "left" in page.ambiguous_line_number_sides
                and block.bbox[0] <= page_left + page_width * 0.12
                and following.bbox[0] <= page_left + page_width * 0.12
                and abs(block.bbox[0] - following.bbox[0]) <= 3.0
            )
            if close_vertically and (right_gutter or left_gutter):
                return True
    return False


def _cross_page_caption_continuation_is_title_phrase(
    value: str,
    *,
    prefix: str = "",
) -> bool:
    """Accept only a short title-case phrase as a wrapped caption suffix."""

    candidate = compact_inline(value)
    if not candidate or len(candidate) > 120 or re.search(r"[.!?;:]$", candidate):
        return False
    if re.search(
        r"(?i)\b(?:shall|must|should|is|are|was|were|has|have|does|did|"
        r"requires?|specifies?|discusses?|continues?)\b",
        candidate,
    ):
        return False
    if re.fullmatch(r"(?i)\(?continued\)?", candidate):
        return True
    connectors = {
        "a", "an", "and", "at", "by", "for", "from", "in", "of", "on",
        "or", "the", "to", "versus", "vs", "with",
    }
    words = re.findall(r"[A-Za-z][A-Za-z0-9_./+()-]*", candidate)
    substantive = [word for word in words if word.casefold() not in connectors]
    title_case_phrase = bool(
        substantive
        and all(
            word[:1].isupper()
            or word.isupper()
            or bool(re.search(r"\d", word))
            for word in substantive
        )
    )
    if title_case_phrase:
        return True
    begins_with_connector = bool(words and words[0].casefold() in connectors)
    prefix_ends_with_connector = bool(
        re.search(
            r"(?i)\b(?:and|at|by|for|from|in|of|on|or|to|versus|vs|with)\s*$",
            compact_inline(prefix),
        )
    )
    return bool(
        substantive
        and len(words) <= 12
        and (begins_with_connector or prefix_ends_with_connector)
    )


def _cross_page_caption_trailing_line_is_furniture(value: str) -> bool:
    """Recognize only line-number or running-footer text below a caption."""

    candidate = compact_inline(value).casefold()
    if re.fullmatch(r"\d{1,3}", candidate):
        return True
    if not 2 <= len(candidate) <= 220:
        return False
    if re.match(r"^(?:copyright\b|©|\(c\)\s*\d{4})", candidate):
        return True
    if re.fullmatch(r"(?:https?://|www\.)\S+", candidate):
        return True
    if "draft" in candidate and any(
        marker in candidate
        for marker in (
            "watermark",
            "not to be shared",
            "not for distribution",
            "publication",
            "approval",
        )
    ):
        return True
    return (
        bool(re.search(r"\b(?:clause|chapter|section|part)\s+\d", candidate))
        and bool(re.search(r"\s[-–—|]\s", candidate))
        and bool(re.search(r"\b(?:forum|standard|specification|interface|agreement|draft)\b", candidate))
        and bool(re.search(r"\d{1,4}$", candidate))
        and not re.match(r"^(?:see|refer|as\s+specified)\b", candidate)
        and not re.search(r"\b(?:shall|must|should|required|prohibited)\b", candidate)
    )


def _table_caption_number(value: str) -> str:
    """Return the normalized number from a caption that begins with `Table`."""

    match = _STRICT_TABLE_REFERENCE_RE.match(compact_inline(value))
    if match is None:
        return ""
    return _normalized_table_reference_number(match.group("number"))


_STRICT_TABLE_REFERENCE_RE = re.compile(
    rf"(?i)\btable\s+(?P<number>\d+(?:\s*{TABLE_NUMBER_DASH_CLASS}\s*\d+)?)"
    rf"(?!\w|\s*{TABLE_NUMBER_DASH_CLASS}|\s*/|\s*\.(?=\S))"
)


def _normalized_table_reference_number(value: str) -> str:
    """Normalize one number accepted by the strict table-reference grammar."""

    return normalize_table_number_dashes(compact_inline(value))


def _table_title_anchor(value: str) -> str:
    """Return the stable leading caption token even when the title wraps."""

    title = compact_inline(value)
    numbered = _STRICT_TABLE_REFERENCE_RE.match(title)
    if numbered is not None:
        punctuation = re.match(r"\s*[.:]", title[numbered.end() :])
        if punctuation is not None:
            return compact_inline(title[: numbered.end() + punctuation.end()])
    title_units = _split_units(value)
    return compact_inline(title_units[0] if title_units else value)


def _table_visuals_with_text_fallbacks(table_visuals: list[TableVisual], pages: list[PageText]) -> list[TableVisual]:
    """Add no-image structured summaries for table rows not covered by screenshots."""

    visuals = list(table_visuals)  # 保留真实截图表格，新增兜底只补未覆盖行。
    covered_by_page: dict[int, Counter[str]] = {}
    for table in visuals:
        page_counter = covered_by_page.setdefault(table.page_number, Counter())
        page_counter.update(
            _review_unit_key(row)
            for row in table.row_texts
            if _is_table_review_unit(row)
        )  # 截图覆盖只在同一页按出现次数消费；同文行出现在别页仍须生成独立证据。
    next_table_number = max((table.table_number for table in visuals), default=0) + 1  # 兜底表号接在真实表之后。
    for page in pages:
        page_counter = covered_by_page.get(page.page_number, Counter())
        rows: list[str] = []
        for line in page.text.splitlines():
            if not _is_table_review_unit(line):
                continue
            key = _review_unit_key(line)
            if page_counter[key] > 0:
                page_counter[key] -= 1
                continue
            rows.append(compact_inline(line))
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
        next_table_number += 1
    return visuals


def _reconcile_exact_cross_side_table_text(
    old_tables: list[TableVisual],
    new_tables: list[TableVisual],
    *,
    old_sections: list[Section],
    new_sections: list[Section],
    old_pages: list[PageText],
    new_pages: list[PageText],
) -> tuple[list[TableVisual], list[TableVisual], set[str], set[str]]:
    """Rebuild a table missed on one side only from unique exact raw text.

    The structured side is treated as a serialization recipe, never as a fuzzy
    template.  A candidate is accepted only when its title occurs on exactly
    one visual on that side, is absent from the other visual side, and the
    title plus a whole-row sequence equals exactly one contiguous span of raw
    review units on the other side after whitespace compaction.
    """

    original_old_tables = list(old_tables)  # 两个方向都只看抽取器原始结果，禁止刚合成的证据反向自证。
    original_new_tables = list(new_tables)
    old_title_counts = Counter(
        key
        for table in original_old_tables
        if (key := _reconcilable_table_title_key(table))
    )
    new_title_counts = Counter(
        key
        for table in original_new_tables
        if (key := _reconcilable_table_title_key(table))
    )
    old_candidates = [
        table
        for table in original_old_tables
        if (key := _reconcilable_table_title_key(table))
        and old_title_counts[key] == 1
        and new_title_counts.get(key, 0) == 0
    ]
    new_candidates = [
        table
        for table in original_new_tables
        if (key := _reconcilable_table_title_key(table))
        and new_title_counts[key] == 1
        and old_title_counts.get(key, 0) == 0
    ]

    (
        synthesized_old,
        old_target_suppressed_keys,
        new_source_suppressed_keys,
        matched_new_source_titles,
    ) = _synthesize_exact_text_backed_tables(
        source_tables=new_candidates,
        source_sections=new_sections,
        target_tables=original_old_tables,
        target_sections=old_sections,
        target_pages=old_pages,
    )
    (
        synthesized_new,
        new_target_suppressed_keys,
        old_source_suppressed_keys,
        matched_old_source_titles,
    ) = _synthesize_exact_text_backed_tables(
        source_tables=old_candidates,
        source_sections=old_sections,
        target_tables=original_new_tables,
        target_sections=new_sections,
        target_pages=new_pages,
    )
    reconciled_old_tables = [
        _table_with_stable_reconciled_row_identity(table)
        if _reconcilable_table_title_key(table) in matched_old_source_titles
        else table
        for table in original_old_tables
    ]
    reconciled_new_tables = [
        _table_with_stable_reconciled_row_identity(table)
        if _reconcilable_table_title_key(table) in matched_new_source_titles
        else table
        for table in original_new_tables
    ]
    return (
        [*reconciled_old_tables, *synthesized_old],
        [*reconciled_new_tables, *synthesized_new],
        old_target_suppressed_keys | old_source_suppressed_keys,
        new_target_suppressed_keys | new_source_suppressed_keys,
    )


def _reconcilable_table_title_key(table: TableVisual) -> str:
    """Return a conservative key for a named, structured source table."""

    title = compact_inline(table.title)
    if (
        not title
        or title == "结构化表格文字摘要"
        or not table.row_texts
        or table.is_continuation
    ):
        return ""
    return title.casefold()  # 大小写只用于判断两侧是否已经都有该表；实际序列匹配仍逐字符保留大小写。


def _synthesize_exact_text_backed_tables(
    *,
    source_tables: list[TableVisual],
    source_sections: list[Section],
    target_tables: list[TableVisual],
    target_sections: list[Section],
    target_pages: list[PageText],
) -> tuple[list[TableVisual], set[str], set[str], set[str]]:
    """Materialize uniquely matched source tables on the raw-text target side."""

    synthesized: list[TableVisual] = []
    target_suppressed_keys: set[str] = set()
    source_suppressed_keys: set[str] = set()
    matched_source_titles: set[str] = set()
    for source_table in source_tables:
        match = _longest_unique_table_text_match(source_table, target_sections)
        if match is None:
            continue
        source_caption = _source_table_caption_evidence(source_table, source_sections)
        if source_caption is None:
            if compact_inline(match.leading_text):
                continue  # 来源侧表题已被坐标级表格替换时，只接受目标侧从表题开始的精确序列；目标引导语仍必须留在正文。
            source_unit_keys: tuple[str, ...] = ()
        else:
            if compact_inline(source_caption.leading_text) != compact_inline(
                match.leading_text
            ):
                continue  # 表题与引导语共处一单元时，只有两侧引导语逐字符相同才可整单元抑制；真实 prose 修订必须保留。
            source_unit_keys = source_caption.unit_keys
        unique_target_keys = _unique_review_unit_keys_in_sections(
            match.raw_unit_keys,
            target_sections,
        )
        unique_source_keys = _unique_review_unit_keys_in_sections(
            source_unit_keys,
            source_sections,
        )  # 完整表序列的唯一命中仍可重建；只有在本侧唯一的单元才获得正文抑制权。
        matched_rows = source_table.row_texts[: match.matched_row_count]
        report_rows = _rows_with_stable_reconciled_identity(matched_rows)
        serialized = match.serialized_text
        if not serialized:
            continue  # 防御性检查：匹配使用的序列若不能再次序列化，绝不创建不完整证据。
        page_number = _exact_table_text_page_number(
            serialized,
            target_pages,
            fallback=match.section_start_page,
        )
        page = next(
            (candidate for candidate in target_pages if candidate.page_number == page_number),
            None,
        )
        existing_on_page = [
            table.table_number
            for table in (*target_tables, *synthesized)
            if table.page_number == page_number
        ]
        synthesized.append(
            TableVisual(
                page_number=page_number,
                table_number=max(existing_on_page, default=0) + 1,
                title=source_table.title,
                bbox=(0.0, 0.0, 0.0, 0.0),
                image_data_uri="",
                row_texts=report_rows,
                grid_summary="未生成截图：由本侧原始文字与对侧结构化表格逐值精确重建。",
                ocr_status="text_backed_exact_match",
                page_bbox=page.page_bbox if page is not None else None,
            )
        )
        target_suppressed_keys.update(unique_target_keys)  # 重复 raw 单元保留为正文，避免吞掉别处真实删除。
        source_suppressed_keys.update(unique_source_keys)  # 重复 caption/reference 同样不做字符串级全局隐藏。
        matched_source_titles.add(_reconcilable_table_title_key(source_table))
    return (
        synthesized,
        target_suppressed_keys,
        source_suppressed_keys,
        matched_source_titles,
    )


def _unique_review_unit_keys_in_sections(
    unit_keys: tuple[str, ...],
    sections: list[Section],
) -> set[str]:
    """Return only keys that identify one review unit on one document side."""

    counts = Counter(
        key
        for section in sections
        for unit in _split_units(section.body)
        if (key := _review_unit_key(unit))
    )
    return {key for key in unit_keys if counts[key] == 1}


def _table_with_stable_reconciled_row_identity(table: TableVisual) -> TableVisual:
    """Add a report-only primary identity to matched revision-history rows."""

    rows = _rows_with_stable_reconciled_identity(table.row_texts)
    return replace(table, row_texts=rows) if rows != table.row_texts else table


def _rows_with_stable_reconciled_identity(rows: list[str]) -> list[str]:
    """Use Revision as Parameter when metadata rows otherwise key on long prose."""

    return [_row_with_stable_revision_identity(row) for row in rows]


def _row_with_stable_revision_identity(row: str) -> str:
    """Expose the exact revision token as row identity without dropping any field."""

    fields = _parsed_table_row_fields(row)
    if fields is None:
        return row
    by_label = {compact_inline(label).casefold(): value for label, value in fields}
    if (
        not {"revision", "date", "description"}.issubset(by_label)
        or "parameter" in by_label
        or not by_label["revision"]
    ):
        return row
    normalized = normalize_line(row)
    prefix_match = re.match(
        r"^((?:表格行|表格文字)[:：]\s*(?:T\d+\s*\|\s*)?)",
        normalized,
        flags=re.I,
    )
    if prefix_match is None:
        return row
    remainder = normalized[prefix_match.end() :]
    return (
        f"{prefix_match.group(1)}"
        f"{encode_table_field('Parameter', by_label['revision'])} | "
        f"{encode_table_field('Details', by_label['description'])} | {remainder}"
    )  # Revision 作为稳定身份；Details 让原 Description 在身份列改变后仍进入可见值摘要，原字段继续完整保留。


def _longest_unique_table_text_match(
    table: TableVisual,
    target_sections: list[Section],
) -> _ExactTableTextMatch | None:
    """Return the longest whole-row prefix with one exact raw-unit occurrence."""

    minimum_row_count = _minimum_table_data_prefix_length(table.row_texts)
    if minimum_row_count > len(table.row_texts):
        return None
    for row_count in range(len(table.row_texts), minimum_row_count - 1, -1):
        serializations = _table_serialization_candidates(
            table.title,
            table.row_texts[:row_count],
        )
        if not serializations:
            return None  # 任一行存在无标签单元格时，无法证明“等号后值序列”，整表失败关闭。
        for serialized in serializations:
            match = _unique_exact_review_unit_span(
                serialized,
                target_sections,
                title=table.title,
            )
            if match is not None:
                return _ExactTableTextMatch(
                    section_start_page=match.section_start_page,
                    raw_unit_keys=match.raw_unit_keys,
                    matched_row_count=row_count,
                    serialized_text=serialized,
                    leading_text=match.leading_text,
                )
    return None


def _minimum_table_data_prefix_length(rows: list[str]) -> int:
    """Require a prefix to contain at least one complete physical data row."""

    if not rows:
        return 1
    first_cells = _parsed_table_row_fields(rows[0])
    if first_cells is None:
        return len(rows) + 1
    labels = [compact_inline(label).casefold() for label, _value in first_cells]
    values = [compact_inline(value).casefold() for _label, value in first_cells]
    generic_schema = bool(labels) and all(
        re.fullmatch(r"(?:column|col)\s*\d+", label)
        for label in labels
    )
    repeated_labels = bool(labels) and all(
        _review_unit_key(label) == _review_unit_key(value)
        for label, value in zip(labels, values)
    )
    first_row_is_header = generic_schema or repeated_labels
    if first_row_is_header:
        return 2 if len(rows) >= 2 else len(rows) + 1
    return 1


def _atomic_exact_table_values(rows: list[str]) -> list[str] | None:
    """Return values whose whitespace tokens cannot conceal a cell-boundary move."""

    values: list[str] = []
    for row in rows:
        fields = _parsed_table_row_fields(row)
        if fields is None:
            return None
        for _label, value in fields:
            atomic_value = compact_inline(value)
            if not atomic_value or re.search(r"\s", atomic_value):
                return None
            values.append(atomic_value)
    return values or None


def _table_serialization_candidates(title: str, rows: list[str]) -> list[str]:
    """Return only serializations whose visible schema or record grammar is retained."""

    title_text = compact_inline(title)
    if not title_text or not rows:
        return []
    first_fields = _parsed_table_row_fields(rows[0])
    if first_fields is None:
        return []
    labels = [label for label, _value in first_fields]
    normalized_labels = [compact_inline(label).casefold() for label in labels]
    semantic_labels = bool(labels) and all(labels) and not all(
        re.fullmatch(r"(?:column|col)\s*\d+", label)
        for label in normalized_labels
    )
    transposed_multiline = _serialize_transposed_multiline_table_rows(title, rows)
    atomic_values = _atomic_exact_table_values(rows)
    with_visible_header = (
        compact_inline(" ".join([title_text, *labels, *atomic_values]))
        if semantic_labels and atomic_values is not None
        else ""
    )
    generated_header_numeric_records = _serialize_generated_header_numeric_records(
        title_text,
        rows,
    )
    revision_history = _serialize_revision_history_records(title_text, rows)
    return list(
        dict.fromkeys(
            candidate
            for candidate in (
                with_visible_header,
                transposed_multiline,
                generated_header_numeric_records,
                revision_history,
            )
            if candidate
        )
    )


def _serialize_generated_header_numeric_records(title: str, rows: list[str]) -> str:
    """Serialize a generated-column table only when its visible header and records prove shape.

    Flat PDF text cannot generally prove cell boundaries.  This path is limited
    to a visible first header row followed by at least two records whose suffix
    columns are complete numeric facts, placeholders, or known unit tokens.
    The free-text first column must be a categorical list or one stable numbered
    series, which excludes arbitrary multiword cell-boundary reassignment.
    """

    parsed_rows = [_parsed_table_row_fields(row) for row in rows]
    if len(parsed_rows) < 2 or any(fields is None for fields in parsed_rows):
        return ""
    concrete_rows = [fields for fields in parsed_rows if fields is not None]
    labels = [compact_inline(label) for label, _value in concrete_rows[0]]
    if len(labels) < 3 or not all(
        re.fullmatch(r"(?:column|col)\s*\d+", label, flags=re.I)
        for label in labels
    ):
        return ""
    if any(
        [compact_inline(label) for label, _value in fields] != labels
        for fields in concrete_rows[1:]
    ):
        return ""
    header_values = [compact_inline(value) for _label, value in concrete_rows[0]]
    header_hits = sum(
        _looks_like_embedded_table_header_value(value)
        for value in header_values
    )
    descriptive_header = all(
        bool(re.search(r"[A-Za-z]", value))
        and not _is_complete_atomic_numeric_or_placeholder(value)
        and not _looks_like_known_atomic_unit(value)
        for value in header_values
    )
    if header_hits < 2 and not descriptive_header:
        return ""
    data_rows = concrete_rows[1:]
    descriptors = [compact_inline(fields[0][1]) for fields in data_rows]
    if not _categorical_descriptor_series_is_proven(descriptors):
        return ""
    suffix_columns = [
        [compact_inline(fields[column_index][1]) for fields in data_rows]
        for column_index in range(1, len(labels))
    ]
    if not suffix_columns or not all(
        all(
            _is_complete_atomic_numeric_or_placeholder(value)
            or _looks_like_known_atomic_unit(value)
            for value in column
        )
        for column in suffix_columns
    ):
        return ""
    if not any(
        all(
            _is_complete_atomic_numeric_or_placeholder(value)
            for value in column
        )
        for column in suffix_columns
    ):
        return ""  # 只有单位列仍不能证明记录边界。
    values = [value for fields in concrete_rows for _label, value in fields]
    return compact_inline(" ".join([title, *values]))


def _categorical_descriptor_series_is_proven(values: list[str]) -> bool:
    """Accept plain categories or one repeated label followed by numeric indexes."""

    if not values or any(not value for value in values):
        return False
    if len(values) == 1:
        return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_.()+\-/]{1,31}", values[0]))
    if all(not re.search(r"\d", value) for value in values):
        return True
    numbered: list[tuple[str, int]] = []
    unnumbered = 0
    for value in values:
        match = re.fullmatch(r"(.+?)[\s_-]+(\d+)", value)
        if match is None:
            unnumbered += 1
            continue
        numbered.append((compact_inline(match.group(1)).casefold(), int(match.group(2))))
    return bool(
        len(numbered) >= 2
        and unnumbered <= 1
        and len({prefix for prefix, _index in numbered}) == 1
        and len({index for _prefix, index in numbered}) == len(numbered)
    )


def _serialize_revision_history_records(title: str, rows: list[str]) -> str:
    """Serialize an explicitly headed Revision/Date/Description history table."""

    parsed_rows = [_parsed_table_row_fields(row) for row in rows]
    if not parsed_rows or any(fields is None for fields in parsed_rows):
        return ""
    concrete_rows = [fields for fields in parsed_rows if fields is not None]
    labels = [compact_inline(label) for label, _value in concrete_rows[0]]
    normalized_labels = [label.casefold() for label in labels]
    if normalized_labels != ["revision", "date", "description"]:
        return ""
    if any(
        [compact_inline(label).casefold() for label, _value in fields]
        != normalized_labels
        for fields in concrete_rows[1:]
    ):
        return ""
    values: list[str] = []
    for fields in concrete_rows:
        revision, date, description = [compact_inline(value) for _label, value in fields]
        if not (
            re.search(r"\d+(?:\.\d+){1,}", revision)
            and (
                re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)
                or re.fullmatch(
                    r"\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9},?\s+\d{4}",
                    date,
                    flags=re.I,
                )
            )
            and description
        ):
            return ""
        values.extend((revision, date, description))
    return compact_inline(" ".join([title, *labels, *values]))


def _serialize_transposed_multiline_table_rows(
    title: str,
    rows: list[str],
) -> str:
    """Serialize a physical row whose cells contain aligned logical rows.

    Some PDFs omit one horizontal grid line, so pdfplumber returns the header
    and first data row inside every cell of one physical row.  The reversible
    table codec retains those newlines.  Only equal multi-line counts across
    *all* cells authorize a row-wise transpose; literal slashes and ragged
    soft wraps therefore cannot enter this exact-reconciliation candidate.
    """

    parts = [compact_inline(title)]
    saw_transposed_row = False
    fixed_atomic_data_columns: set[int] = set()
    for row_index, row in enumerate(rows):
        fields = _parsed_table_row_fields_preserving_lines(row)
        if fields is None:
            return ""
        line_groups = [lines for _label, lines in fields]
        multi_counts = {len(lines) for lines in line_groups if len(lines) > 1}
        if not multi_counts:
            if saw_transposed_row:
                fixed_atomic_data_columns = {
                    column_index
                    for column_index in fixed_atomic_data_columns
                    if column_index < len(line_groups)
                    and _is_complete_atomic_numeric_or_placeholder(
                        line_groups[column_index][0]
                    )
                }
                if not fixed_atomic_data_columns:
                    return ""  # 首行证明的同一数值列必须在全部后续物理行继续成立。
            parts.extend(lines[0] if lines else "" for lines in line_groups)
            continue
        if len(multi_counts) != 1:
            return ""
        row_count = next(iter(multi_counts))
        if row_count <= 1 or any(len(lines) != row_count for lines in line_groups):
            return ""  # 一列软换行或各列行数不齐时失败关闭。
        proven_columns = _embedded_header_atomic_data_column_indexes(line_groups)
        if saw_transposed_row or row_index != 0 or not proven_columns:
            return ""  # 只有首个物理行能由表头形态+逐行数值证据授权转置。
        saw_transposed_row = True
        fixed_atomic_data_columns = proven_columns
        for line_index in range(row_count):
            parts.extend(lines[line_index] for lines in line_groups)
    return compact_inline(" ".join(parts)) if saw_transposed_row else ""


def _multiline_row_proves_embedded_header_records(
    line_groups: list[tuple[str, ...]],
) -> bool:
    """Require a schema row plus one fixed atomic numeric/placeholder column."""

    return bool(_embedded_header_atomic_data_column_indexes(line_groups))


def _embedded_header_atomic_data_column_indexes(
    line_groups: list[tuple[str, ...]],
) -> set[int]:
    """Return fixed columns whose complete records are numeric or placeholders."""

    if len(line_groups) < 3:
        return set()  # 两列等长 prose 软换行太容易偶然成立。
    header_values = [lines[0] for lines in line_groups]
    header_hits = sum(_looks_like_embedded_table_header_value(value) for value in header_values)
    if header_hits < 2:
        return set()
    return {
        column_index
        for column_index, lines in enumerate(line_groups)
        if all(
            _is_complete_atomic_numeric_or_placeholder(value)
            for value in lines[1:]
        )
    }  # 数字散落在不同 prose 列不构成记录；同一固定列必须逐条提供完整原子值。


def _is_complete_atomic_numeric_or_placeholder(value: str) -> bool:
    """Accept one complete numeric fact or standard missing-value marker."""

    candidate = compact_inline(value)
    if not candidate:
        return False
    return bool(
        _looks_like_pure_numeric_table_entry(candidate)
        or _looks_like_missing_table_value(candidate)
    )


def _looks_like_embedded_table_header_value(value: str) -> bool:
    """Recognize compact schema labels without treating ordinary title words as headers."""

    candidate = compact_inline(value)
    folded = candidate.casefold().rstrip(".")
    if folded in {
        "parameter",
        "parameters",
        "characteristic",
        "characteristics",
        "symbol",
        "symbols",
        "condition",
        "conditions",
        "value",
        "values",
        "unit",
        "units",
        "min",
        "minimum",
        "typ",
        "typical",
        "max",
        "maximum",
        "revision",
        "date",
        "description",
        "label",
    }:
        return True
    return bool(
        re.fullmatch(r"[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+", candidate)
        or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*\s*\([+\-−]?(?:\d+|min|max|typ)\)", candidate, flags=re.I)
    )


def _parsed_table_row_fields_preserving_lines(
    row: str,
) -> list[tuple[str, tuple[str, ...]]] | None:
    """Decode labeled cells while retaining codec-proven physical newlines."""

    cells = _raw_table_row_cells(row)
    if not cells:
        return None
    fields: list[tuple[str, tuple[str, ...]]] = []
    for cell in cells:
        field = split_table_field(cell)
        if field is None:
            return None
        label, value = field
        lines = tuple(
            compact_inline(line)
            for line in value.splitlines()
            if compact_inline(line)
        )
        fields.append((compact_inline(label), lines or ("",)))
    return fields if any(any(lines) for _label, lines in fields) else None


def _parsed_table_row_fields(row: str) -> list[tuple[str, str]] | None:
    """Decode one structured row only when every physical cell is labeled."""

    cells = _raw_table_row_cells(row)
    if not cells:
        return None
    fields: list[tuple[str, str]] = []
    for cell in cells:
        field = split_table_field(cell)
        if field is None:
            return None
        label, value = field
        fields.append((compact_inline(label), compact_inline(value)))
    if not any(value for _label, value in fields):
        return None
    return fields


def _unique_exact_review_unit_span(
    serialized: str,
    sections: list[Section],
    *,
    title: str,
) -> _ExactTableTextMatch | None:
    """Find one exact sequence ending at a raw-unit boundary.

    A PDF may merge an unchanged lead-in sentence with the caption, so the
    first raw unit may contain text before the title.  Text after the matched
    row sequence is never allowed in the last unit; that boundary rule keeps a
    later changed row visible instead of suppressing it with the prefix.
    """

    target = compact_inline(serialized)
    title_anchor = _table_title_anchor(title)
    if not target or not title_anchor:
        return None
    found_by_occurrence: dict[tuple[int, int], _ExactTableTextMatch] = {}
    for section_index, section in enumerate(sections):
        units = [unit for unit in _split_units(section.body) if compact_inline(unit)]
        normalized_units = [compact_inline(unit) for unit in units]
        for start_index, first_unit in enumerate(normalized_units):
            if title_anchor not in first_unit:
                continue
            candidate = ""
            for end_index in range(start_index, len(units)):
                candidate = compact_inline(
                    f"{candidate} {normalized_units[end_index]}"
                )
                if not _raw_unit_span_has_exact_table_suffix(candidate, target):
                    continue
                match = _ExactTableTextMatch(
                    section_start_page=section.start_page,
                    raw_unit_keys=tuple(
                        _review_unit_key(unit)
                        for unit in units[start_index : end_index + 1]
                        if _review_unit_key(unit)
                    ),
                    matched_row_count=0,
                    serialized_text=target,
                    leading_text=compact_inline(candidate[: -len(target)]),
                )
                occurrence_key = (section_index, end_index)
                previous = found_by_occurrence.get(occurrence_key)
                if previous is None or len(match.raw_unit_keys) < len(previous.raw_unit_keys):
                    found_by_occurrence[occurrence_key] = match  # 同一结尾的重叠起点是同一次命中，保留最短完整单元跨度。
                break
    if len(found_by_occurrence) != 1:
        return None  # 不同位置出现两次即失去唯一性，禁止猜测对应哪张表。
    return next(iter(found_by_occurrence.values()))


def _raw_unit_span_has_exact_table_suffix(candidate: str, target: str) -> bool:
    """Allow only an unchanged lead-in before an otherwise exact table sequence."""

    if candidate == target:
        return candidate.count(target) == 1
    if not candidate.endswith(target):
        return False
    boundary_index = len(candidate) - len(target)
    return bool(
        candidate.count(target) == 1
        and boundary_index > 0
        and candidate[boundary_index - 1].isspace()
    )


def _source_table_caption_evidence(
    table: TableVisual,
    sections: list[Section],
) -> _SourceTableCaptionEvidence | None:
    """Locate the unique caption span immediately preceding this source table."""

    title = compact_inline(table.title)
    title_anchor = _table_title_anchor(table.title)
    if not title or not title_anchor:
        return None
    adjacent_matches: dict[tuple[int, int], _SourceTableCaptionEvidence] = {}
    all_matches: dict[tuple[int, int], _SourceTableCaptionEvidence] = {}
    for section_index, section in enumerate(sections):
        if not section.start_page <= table.page_number <= section.end_page:
            continue
        section_units = _split_units(section.body)
        table_row_keys = {
            _review_unit_key(row)
            for row in table.row_texts
            if _review_unit_key(row)
        }
        first_row_index = next(
            (
                index
                for index, unit in enumerate(section_units)
                if _review_unit_key(unit) in table_row_keys
            ),
            len(section_units),
        )
        normalized_units = [compact_inline(unit) for unit in section_units]
        for start_index, first_unit in enumerate(normalized_units):
            if title_anchor not in first_unit:
                continue
            candidate = ""
            for end_index in range(start_index, len(section_units)):
                candidate = compact_inline(f"{candidate} {normalized_units[end_index]}")
                if not _raw_unit_span_has_exact_table_suffix(candidate, title):
                    continue
                unit_keys = tuple(
                    _review_unit_key(unit)
                    for unit in section_units[start_index : end_index + 1]
                    if _review_unit_key(unit)
                )
                if not unit_keys:
                    continue
                evidence = _SourceTableCaptionEvidence(
                    unit_keys=unit_keys,
                    leading_text=compact_inline(candidate[: -len(title)]),
                )
                occurrence_key = (section_index, end_index)
                previous = all_matches.get(occurrence_key)
                if previous is None or len(unit_keys) < len(previous.unit_keys):
                    all_matches[occurrence_key] = evidence  # 同一物理 caption 的重叠起点只保留最短整单元跨度。
                if end_index + 1 == first_row_index:
                    previous_adjacent = adjacent_matches.get(occurrence_key)
                    if previous_adjacent is None or len(unit_keys) < len(previous_adjacent.unit_keys):
                        adjacent_matches[occurrence_key] = evidence  # 紧邻第一条结构化行是最强来源证据。
    if len(adjacent_matches) == 1:
        return next(iter(adjacent_matches.values()))
    if adjacent_matches or len(all_matches) != 1:
        return None
    return next(iter(all_matches.values()))  # 行被章节边界或另一张表隔开时，仅接受完整标题的单次末尾对齐命中。


def _exact_table_text_page_number(
    serialized: str,
    pages: list[PageText],
    *,
    fallback: int,
) -> int:
    """Use a unique raw page occurrence for location, otherwise keep section start."""

    target = compact_inline(serialized)
    matching_pages = [
        page.page_number
        for page in pages
        if target and target in compact_inline(page.text)
    ]
    return matching_pages[0] if len(matching_pages) == 1 else fallback


def compare_sections(
    old_sections: list[Section],
    new_sections: list[Section],
    options: DiffOptions,
    *,
    suppressed_table_unit_keys: set[str] | None = None,
    suppressed_old_table_unit_keys: set[str] | None = None,
    suppressed_new_table_unit_keys: set[str] | None = None,
) -> list[SectionChange]:
    """Match old/new sections and classify section-level changes."""

    common_table_unit_keys = suppressed_table_unit_keys or set()
    old_table_unit_keys = common_table_unit_keys | (
        suppressed_old_table_unit_keys or set()
    )
    new_table_unit_keys = common_table_unit_keys | (
        suppressed_new_table_unit_keys or set()
    )  # 旧兼容参数仍可双侧使用；精确重建路径必须传入侧别集合。
    matches = _match_sections(old_sections, new_sections, options)
    changes: list[SectionChange] = []
    for old_index, new_index, similarity, match_basis in matches:
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
                            match_basis=match_basis,
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
            (
                added,
                removed,
                replaced,
                omitted_count,
                audit_added,
                audit_removed,
                audit_replaced,
            ) = _summarize_text_delta(
                old_section.body,
                new_section.body,
                max_snippets=options.max_snippets_per_section,
                leading_replacement=heading_pair,
                suppressed_old_table_unit_keys=old_table_unit_keys,
                suppressed_new_table_unit_keys=new_table_unit_keys,
            )
            if not added and not removed and not replaced and omitted_count == 0:
                if options.include_unchanged_sections:
                    changes.append(
                        SectionChange(
                            change_type="unchanged",
                            old_section=old_section,
                            new_section=new_section,
                            similarity=similarity,
                            match_basis=match_basis,
                        )
                    )
                continue
            changes.append(
                SectionChange(
                    change_type=(
                        "review"
                        if _delta_is_unverified_pua_mapping_only(
                            added,
                            removed,
                            replaced,
                            omitted_count,
                        )
                        else "modified"
                    ),
                    old_section=old_section,
                    new_section=new_section,
                    similarity=similarity,
                    match_basis=match_basis,
                    added_snippets=added,
                    removed_snippets=removed,
                    replaced_snippets=replaced,
                    omitted_snippet_count=omitted_count,
                    audit_added_snippets=audit_added,
                    audit_removed_snippets=audit_removed,
                    audit_replaced_snippets=audit_replaced,
                )
            )
        elif new_section:
            added_snippets, omitted_count, audit_added = _first_units(
                new_section.body,
                options.max_snippets_per_section,
                suppressed_table_unit_keys=new_table_unit_keys,
            )
            changes.append(
                SectionChange(
                    change_type="added",
                    old_section=None,
                    new_section=new_section,
                    similarity=0.0,
                    added_snippets=added_snippets,
                    omitted_snippet_count=omitted_count,
                    audit_added_snippets=audit_added,
                )
            )
        elif old_section:
            removed_snippets, omitted_count, audit_removed = _first_units(
                old_section.body,
                options.max_snippets_per_section,
                suppressed_table_unit_keys=old_table_unit_keys,
            )
            changes.append(
                SectionChange(
                    change_type="deleted",
                    old_section=old_section,
                    new_section=None,
                    similarity=0.0,
                    removed_snippets=removed_snippets,
                    omitted_snippet_count=omitted_count,
                    audit_removed_snippets=audit_removed,
                )
            )

    return sorted(changes, key=_change_sort_key)


def _match_sections(
    old_sections: list[Section],
    new_sections: list[Section],
    options: DiffOptions,
) -> list[tuple[int | None, int | None, float, str]]:
    """Pair old/new sections using exact keys first, then global best scores.

    The second pass builds all viable fallback candidates and assigns the
    strongest pairs first. That is more conservative than matching each new
    section greedily in document order, especially when protocols contain many
    repeated boilerplate clauses.
    """

    exact_old_by_key: dict[str, list[int]] = {}
    for old_index, old_section in enumerate(old_sections):
        exact_old_by_key.setdefault(old_section.identity_key, []).append(old_index)
    exact_new_by_key: dict[str, list[int]] = {}
    for new_index, new_section in enumerate(new_sections):
        exact_new_by_key.setdefault(new_section.identity_key, []).append(new_index)

    matched_old: set[int] = set()
    matched_new: set[int] = set()
    rejected_exact_pairs: set[tuple[int, int]] = set()
    matches: list[tuple[int | None, int | None, float, str]] = []

    exact_candidates: list[tuple[float, int, int]] = []
    for identity_key, old_indexes in exact_old_by_key.items():
        for old_index in old_indexes:
            for new_index in exact_new_by_key.get(identity_key, []):
                old_section = old_sections[old_index]
                new_section = new_sections[new_index]
                body_similarity = _exact_identity_similarity(
                    old_section.body,
                    new_section.body,
                )
                if body_similarity is None:
                    rejected_exact_pairs.add((old_index, new_index))
                    continue
                if (
                    old_section.body.strip()
                    and new_section.body.strip()
                    and body_similarity < options.min_section_match_similarity
                ):
                    continue  # 同号同题也不能让长标题淹没两段完全无关的实际正文。
                similarity = max(
                    _section_similarity(
                        old_section.comparable_text,
                        new_section.comparable_text,
                    ),
                    body_similarity,
                )
                if similarity >= options.min_section_match_similarity:
                    exact_candidates.append((similarity, old_index, new_index))
                # 相同编号并不保证是同一条款：插入新条款会占用旧编号并整体后移。
                # 即使标题未变，也必须由实际可比文本达到用户阈值；证据不足时保守显示新增/删除。

    for similarity, old_index, new_index in sorted(
        exact_candidates,
        key=lambda item: (-item[0], abs(item[1] - item[2]), item[2], item[1]),
    ):
        if old_index in matched_old or new_index in matched_new:
            continue
        matched_old.add(old_index)
        matched_new.add(new_index)
        matches.append((old_index, new_index, similarity, "similarity_exact"))

    fallback_candidates: list[tuple[float, int, int]] = []
    for new_index, new_section in enumerate(new_sections):
        if new_index in matched_new:
            continue
        for old_index, old_section in enumerate(old_sections):
            if old_index in matched_old:
                continue
            if (old_index, new_index) in rejected_exact_pairs:
                continue
            body_similarity = _section_similarity(
                old_section.body,
                new_section.body,
            )  # fallback 必须先由正文达到用户配置门槛，标题和位置不能单独证明同一章节。
            if body_similarity < options.min_section_match_similarity:
                continue  # 正文证据不足时保守保留新增/删除，避免把完全重写的同名章节强配。
            score = _section_match_score(old_section, new_section)
            if score < options.min_section_match_similarity:
                continue
            comparable_similarity = _section_similarity(
                old_section.comparable_text,
                new_section.comparable_text,
            )  # 标题/位置组合分只能排序候选，不能替代用户声明的实际可比文本门槛。
            if comparable_similarity >= options.min_section_match_similarity:
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
        matches.append(
            (old_index, new_index, min(score, similarity), "similarity_fallback")
        )

    ordinary_matches = tuple(matches)  # 后置结构救援只能引用首轮普通配对，禁止候选互相循环自证。
    for old_index, new_index, match_basis in _structural_identity_rescue_pairs(
        old_sections,
        new_sections,
        exact_old_by_key,
        exact_new_by_key,
        matched_old,
        matched_new,
        {
            (old_index, new_index)
            for old_index, new_index, _similarity_value, _match_basis in matches
            if old_index is not None and new_index is not None
        },
        options.min_section_match_similarity,
    ):
        matched_old.add(old_index)
        matched_new.add(new_index)
        matches.append(
            (
                old_index,
                new_index,
                _section_similarity(
                    old_sections[old_index].comparable_text,
                    new_sections[new_index].comparable_text,
                ),
                match_basis,
            )
        )  # 保留实际全文分数；结构证据只授权配对，不伪造高相似度。

    for old_index, new_index, match_basis in _shifted_section_rescue_pairs(
        old_sections,
        new_sections,
        ordinary_matches,
        matched_old,
        matched_new,
        options.min_section_match_similarity,
    ):
        matched_old.add(old_index)
        matched_new.add(new_index)
        matches.append(
            (
                old_index,
                new_index,
                _section_similarity(
                    old_sections[old_index].comparable_text,
                    new_sections[new_index].comparable_text,
                ),
                match_basis,
            )
        )  # 编号后移只改变配对授权；报告继续显示实际全文相似度。

    for new_index, _new_section in enumerate(new_sections):
        if new_index not in matched_new:
            matches.append((None, new_index, 0.0, "unmatched"))

    for old_index, _old_section in enumerate(old_sections):
        if old_index not in matched_old:
            matches.append((old_index, None, 0.0, "unmatched"))
    return matches


_SECTION_IDENTITY_ANCHOR_MIN_CHARS = 80
_SECTION_IDENTITY_NEAR_ANCHOR_MIN_SCORE = 0.85
_STRUCTURAL_IDENTITY_ANCHOR_SCORE = 0.90
_STRUCTURAL_IDENTITY_BRACKET_SCORE = 0.90
_STRUCTURAL_NUMBER_SHIFT_SCORE = 0.90
_SECTION_SHIFT_PROSE_MIN_CHARS = 50
_SECTION_IDENTITY_PROSE_VERB_RE = re.compile(
    r"(?i)\b(?:shall|should|must|may|can|is|are|was|were|be|being|been|"
    r"accept|calculate|comply|complies|connect|define|defined|follow|include|measure|meet|"
    r"obtain|perform|preserve|provide(?:s|d|ing)?|remain|require|specify|transmit|use)\b"
)
_SECTION_IDENTITY_ACRONYM_RE = re.compile(
    r"(?<![A-Za-z0-9_])[A-Z]{2,}[A-Za-z0-9_-]*(?![A-Za-z0-9_])"
)
_SECTION_IDENTITY_TITLE_STOP_WORDS = frozenset(
    {
        "appendix",
        "characteristics",
        "clause",
        "general",
        "interface",
        "requirement",
        "requirements",
        "section",
        "specification",
        "specifications",
    }
)


def _structural_identity_rescue_pairs(
    old_sections: list[Section],
    new_sections: list[Section],
    old_by_key: dict[str, list[int]],
    new_by_key: dict[str, list[int]],
    matched_old: set[int],
    matched_new: set[int],
    matched_pairs: set[tuple[int, int]],
    minimum_similarity: float,
) -> list[tuple[int, int, str]]:
    """Rescue unique exact clauses when independent prose anchors prove identity.

    This pass runs only after ordinary exact and fallback matching. It therefore
    cannot steal a renumbered body match, and it never relies on number/title
    alone: prose equality after structured rows are removed, same-parent unique
    title-linked technical sentences with a second independent prose fact, or
    two already matched adjacent clauses must support the pair. Structural
    support is also compared with the user's configured match floor.
    """

    if minimum_similarity >= 1.0:
        return []  # “最低相似度=1”要求全文完全一致，结构证据不能绕过该公开配置。

    old_anchor_counts = Counter(
        (section.number_path[:-1], section.level, anchor)
        for section in old_sections
        for anchor in _section_identity_anchor_keys(section.body, section.title)
    )
    new_anchor_counts = Counter(
        (section.number_path[:-1], section.level, anchor)
        for section in new_sections
        for anchor in _section_identity_anchor_keys(section.body, section.title)
    )
    candidates: list[tuple[int, int, float, str]] = []
    for identity_key, old_indexes in old_by_key.items():
        new_indexes = new_by_key.get(identity_key, [])
        if len(old_indexes) != 1 or len(new_indexes) != 1:
            continue  # 重复编号下的候选不唯一，不作结构性猜测。
        old_index = old_indexes[0]
        new_index = new_indexes[0]
        if old_index in matched_old or new_index in matched_new:
            continue
        old_section = old_sections[old_index]
        new_section = new_sections[new_index]
        if not old_section.number_path or old_section.number_path != new_section.number_path:
            continue  # 无编号页级块和仅标题候选不具备足够的结构证据。
        if old_section.level != new_section.level:
            continue
        old_title_key = _review_unit_key(old_section.title)
        new_title_key = _review_unit_key(new_section.title)
        if not old_title_key or old_title_key != new_title_key:
            continue  # 标题也变化时仍交给全文 fallback，避免插入章节占号。
        prose_score, prose_basis = _structural_identity_prose_support(
            old_section,
            new_section,
            old_anchor_counts,
            new_anchor_counts,
        )
        candidates.append((old_index, new_index, prose_score, prose_basis))

    # 先固化自身已有正文证据的配对，再让相邻边界使用这些独立证据。
    # 只做这一层扩展，不让 bracket 结果继续链式传播，避免一串同号同题章节互相自证。
    rescued: list[tuple[int, int, str]] = [
        (old_index, new_index, prose_basis)
        for old_index, new_index, prose_score, prose_basis in candidates
        if prose_basis and prose_score >= minimum_similarity
    ]
    independently_matched_pairs = matched_pairs | {
        (old_index, new_index) for old_index, new_index, _basis in rescued
    }
    independently_matched_old = matched_old | {
        old_index for old_index, _new_index, _basis in rescued
    }
    independently_matched_new = matched_new | {
        new_index for _old_index, new_index, _basis in rescued
    }
    for old_index, new_index, _prose_score, prose_basis in candidates:
        if prose_basis and (old_index, new_index, prose_basis) in rescued:
            continue
        if old_index in independently_matched_old or new_index in independently_matched_new:
            continue
        bracket_supported = _matched_adjacent_brackets_support(
            old_sections,
            new_sections,
            old_index,
            new_index,
            independently_matched_pairs,
        )
        if bracket_supported and _STRUCTURAL_IDENTITY_BRACKET_SCORE >= minimum_similarity:
            rescued.append((old_index, new_index, "structural_adjacent_brackets"))
    return sorted(rescued, key=lambda item: (item[1], item[0]))


def _structural_identity_prose_support(
    old_section: Section,
    new_section: Section,
    old_anchor_counts: Counter[tuple[tuple[str, ...], int, str]],
    new_anchor_counts: Counter[tuple[tuple[str, ...], int, str]],
) -> tuple[float, str]:
    """Return structural support score and audit basis for exact prose/anchors."""

    old_prose_keys = _section_identity_prose_keys(old_section.body)
    new_prose_keys = _section_identity_prose_keys(new_section.body)
    if old_prose_keys and old_prose_keys == new_prose_keys:
        return 1.0, "structural_prose_identity"  # 去掉结构化表格后正文完全相同，身份证据为满分。

    shared_anchors = set(
        _section_identity_anchor_keys(old_section.body, old_section.title)
    ) & set(
        _section_identity_anchor_keys(new_section.body, new_section.title)
    )
    old_context = (old_section.number_path[:-1], old_section.level)
    new_context = (new_section.number_path[:-1], new_section.level)
    unique_anchors = {
        anchor
        for anchor in shared_anchors
        if old_anchor_counts[(*old_context, anchor)] == 1
        and new_anchor_counts[(*new_context, anchor)] == 1
    }
    if len(unique_anchors) >= 2:
        return _STRUCTURAL_IDENTITY_ANCHOR_SCORE, "structural_unique_anchor"
    if unique_anchors and _has_independent_near_identity_unit(
        old_section.body,
        new_section.body,
        excluded_keys=unique_anchors,
        title=old_section.title,
    ):
        return _STRUCTURAL_IDENTITY_ANCHOR_SCORE, "structural_unique_anchor"
    return 0.0, ""


def _section_identity_prose_keys(body: str) -> tuple[str, ...]:
    """Return non-table review-unit keys used only as identity evidence."""

    return tuple(
        key
        for unit in _paragraph_review_units(body, suppressed_table_unit_keys=set())
        if (key := _review_unit_key(unit))
    )


def _section_identity_anchor_keys(body: str, title: str) -> tuple[str, ...]:
    """Return conservative complete-sentence anchors from one section body."""

    anchors: list[str] = []
    for unit in _paragraph_review_units(body, suppressed_table_unit_keys=set()):
        compact = compact_inline(unit)
        if len(compact) < _SECTION_IDENTITY_ANCHOR_MIN_CHARS:
            continue
        if not _ends_review_sentence(compact):
            continue
        if not _SECTION_IDENTITY_PROSE_VERB_RE.search(compact):
            continue
        if not _has_section_identity_title_link(compact, title):
            continue
        key = _review_unit_key(compact)
        if key:
            anchors.append(key)
    return tuple(anchors)


def _has_section_identity_title_link(anchor: str, title: str) -> bool:
    """Require a meaningful word or acronym shared with the exact section title."""

    anchor_acronyms = set(_SECTION_IDENTITY_ACRONYM_RE.findall(anchor))
    anchor_words = set(re.findall(r"[a-z]{4,}", anchor.casefold()))
    title_words = set(re.findall(r"[a-z]{4,}", title.casefold()))
    title_words.difference_update(_SECTION_IDENTITY_TITLE_STOP_WORDS)
    title_acronyms = set(_SECTION_IDENTITY_ACRONYM_RE.findall(title))
    if anchor_words & title_words or anchor_acronyms & title_acronyms:
        return True
    return any(
        min(len(anchor_word), len(title_word)) >= 6
        and (
            anchor_word.startswith(title_word)
            or title_word.startswith(anchor_word)
        )
        for anchor_word in anchor_words
        for title_word in title_words
    )  # transmit/transmitter 等同根技术词可建立标题关联，短词前缀仍被拒绝。


def _has_independent_near_identity_unit(
    old_body: str,
    new_body: str,
    *,
    excluded_keys: set[str],
    title: str,
) -> bool:
    """Require a mutual-unique second long prose fact tied to the same title."""

    old_units = _paragraph_review_units(old_body, suppressed_table_unit_keys=set())
    new_units = _paragraph_review_units(new_body, suppressed_table_unit_keys=set())
    candidate_edges: list[tuple[int, int]] = []
    for old_index, old_unit in enumerate(old_units):
        old_key = _review_unit_key(old_unit)
        if old_key in excluded_keys or len(compact_inline(old_unit)) < _SECTION_IDENTITY_ANCHOR_MIN_CHARS:
            continue
        if not _has_section_identity_title_link(old_unit, title):
            continue
        old_words = _meaningful_review_words(old_unit)
        for new_index, new_unit in enumerate(new_units):
            new_key = _review_unit_key(new_unit)
            if new_key in excluded_keys or len(compact_inline(new_unit)) < _SECTION_IDENTITY_ANCHOR_MIN_CHARS:
                continue
            if not _has_section_identity_title_link(new_unit, title):
                continue
            shared_words = old_words & _meaningful_review_words(new_unit)
            if len(shared_words) < 6:
                continue
            if _unit_pair_score(old_unit, new_unit) >= _SECTION_IDENTITY_NEAR_ANCHOR_MIN_SCORE:
                candidate_edges.append((old_index, new_index))
    old_degrees = Counter(old_index for old_index, _new_index in candidate_edges)
    new_degrees = Counter(new_index for _old_index, new_index in candidate_edges)
    return any(
        old_degrees[old_index] == 1 and new_degrees[new_index] == 1
        for old_index, new_index in candidate_edges
    )


def _shifted_section_rescue_pairs(
    old_sections: list[Section],
    new_sections: list[Section],
    ordinary_matches: tuple[tuple[int | None, int | None, float, str], ...],
    matched_old: set[int],
    matched_new: set[int],
    minimum_similarity: float,
) -> list[tuple[int, int, str]]:
    """Pair table-heavy clauses only when an ordinary sibling run proves renumbering.

    Titles and offsets are never sufficient alone.  The parent pair and shift
    seeds come from the frozen ordinary-match pass, title uniqueness is measured
    over every original direct child, and each rescued clause supplies its own
    prose evidence.  This prevents an inserted same-title clause from borrowing
    confidence from the clauses it is trying to rescue.
    """

    if minimum_similarity >= 1.0:
        return []  # 映射父章节等结构证据也不能越过用户要求的全文完全一致。

    ordinary_pairs = {
        (old_index, new_index)
        for old_index, new_index, _similarity_value, match_basis in ordinary_matches
        if old_index is not None
        and new_index is not None
        and match_basis in {"similarity_exact", "similarity_fallback"}
    }
    old_parent_title_counts = Counter(
        (section.number_path[:-1], section.level, _review_unit_key(section.title))
        for section in old_sections
        if section.number_path and _review_unit_key(section.title)
    )
    new_parent_title_counts = Counter(
        (section.number_path[:-1], section.level, _review_unit_key(section.title))
        for section in new_sections
        if section.number_path and _review_unit_key(section.title)
    )
    trusted_parent_path_pairs: set[tuple[tuple[str, ...], tuple[str, ...]]] = {
        ((), ())
    }  # 根域是递归信任链的唯一无条件基点。
    renumbered_parent_candidates: list[
        tuple[tuple[str, ...], tuple[str, ...], int, str]
    ] = []
    for old_index, new_index in ordinary_pairs:
        old_section = old_sections[old_index]
        new_section = new_sections[new_index]
        old_path = old_section.number_path
        new_path = new_section.number_path
        if not old_path or not new_path or old_section.level != new_section.level:
            continue
        if old_path == new_path:
            trusted_parent_path_pairs.add((old_path, new_path))
            continue  # 同一路径的普通配对本身可信，标题注记修订不能阻断子章编号偏移证据。
        title_key = _review_unit_key(old_section.title)
        if title_key != _review_unit_key(new_section.title):
            continue
        renumbered_parent_candidates.append(
            (old_path, new_path, old_section.level, title_key)
        )
    pending = sorted(
        renumbered_parent_candidates,
        key=lambda item: (len(item[0]), item[0], item[1]),
    )
    while pending:
        progressed = False
        next_pending: list[tuple[tuple[str, ...], tuple[str, ...], int, str]] = []
        for old_path, new_path, level, title_key in pending:
            if (
                (old_path[:-1], new_path[:-1]) in trusted_parent_path_pairs
                and title_key
                and old_parent_title_counts[(old_path[:-1], level, title_key)] == 1
                and new_parent_title_counts[(new_path[:-1], level, title_key)] == 1
            ):
                trusted_parent_path_pairs.add((old_path, new_path))
                progressed = True
            else:
                next_pending.append((old_path, new_path, level, title_key))
        if not progressed:
            break
        pending = next_pending
    # 改号父章节必须沿祖先路径逐层可信且在原始父域标题双侧唯一，重复空容器不能按顺序自证。
    old_parent_targets: dict[tuple[str, ...], set[tuple[str, ...]]] = {}
    new_parent_sources: dict[tuple[str, ...], set[tuple[str, ...]]] = {}
    for old_path, new_path in trusted_parent_path_pairs:
        old_parent_targets.setdefault(old_path, set()).add(new_path)
        new_parent_sources.setdefault(new_path, set()).add(old_path)

    old_title_counts = Counter(
        (section.number_path[:-1], section.level, _review_unit_key(section.title))
        for section in old_sections
        if _direct_child_ordinal(section) is not None
    )
    new_title_counts = Counter(
        (section.number_path[:-1], section.level, _review_unit_key(section.title))
        for section in new_sections
        if _direct_child_ordinal(section) is not None
    )
    old_ordinal_counts = Counter(
        (section.number_path[:-1], section.level, ordinal)
        for section in old_sections
        if (ordinal := _direct_child_ordinal(section)) is not None
    )
    new_ordinal_counts = Counter(
        (section.number_path[:-1], section.level, ordinal)
        for section in new_sections
        if (ordinal := _direct_child_ordinal(section)) is not None
    )
    old_prose_counts = _shift_prose_unit_counts(old_sections)
    new_prose_counts = _shift_prose_unit_counts(new_sections)

    seed_groups: dict[
        tuple[tuple[str, ...], tuple[str, ...], int, int],
        list[tuple[int, int, int, int]],
    ] = {}
    ordinary_shift_pairs_by_group: dict[
        tuple[tuple[str, ...], tuple[str, ...], int, int],
        set[tuple[int, int]],
    ] = {}
    for old_index, new_index in ordinary_pairs:
        old_section = old_sections[old_index]
        new_section = new_sections[new_index]
        old_ordinal = _direct_child_ordinal(old_section)
        new_ordinal = _direct_child_ordinal(new_section)
        if old_ordinal is None or new_ordinal is None:
            continue
        if old_section.level != new_section.level:
            continue
        if _review_unit_key(old_section.title) != _review_unit_key(new_section.title):
            continue
        offset = new_ordinal - old_ordinal
        if offset == 0:
            continue
        parent_pair = (old_section.number_path[:-1], new_section.number_path[:-1])
        if parent_pair not in trusted_parent_path_pairs:
            continue
        key = (*parent_pair, old_section.level, offset)
        ordinary_shift_pairs_by_group.setdefault(key, set()).add(
            (old_index, new_index)
        )  # 空容器可作为已配的相邻边界，但不能独自证明整个编号偏移区间。
        if not old_section.body.strip() or not new_section.body.strip():
            continue
        if _section_similarity(old_section.body, new_section.body) < minimum_similarity:
            continue
        seed_groups.setdefault(key, []).append(
            (old_index, new_index, old_ordinal, new_ordinal)
        )

    supported_seed_groups: dict[
        tuple[tuple[str, ...], tuple[str, ...], int, int],
        set[tuple[int, int]],
    ] = {}
    offsets_by_domain: dict[
        tuple[tuple[str, ...], tuple[str, ...], int],
        set[int],
    ] = {}
    for key, group in seed_groups.items():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda item: item[2])
        if [item[3] for item in ordered] != sorted(item[3] for item in ordered):
            continue  # 交叉重排不能充当统一编号后移证据。
        domain = key[:3]
        offsets_by_domain.setdefault(domain, set()).add(key[3])
        supported_seed_groups[key] = {
            (old_index, new_index)
            for old_index, new_index, _old_ordinal, _new_ordinal in group
        }
    supported_seed_groups = {
        key: pairs
        for key, pairs in supported_seed_groups.items()
        if len(offsets_by_domain.get(key[:3], set())) == 1
    }  # 同一父域出现多个可竞争偏移时保持新增/删除，不作全局外推。

    new_children_by_key: dict[
        tuple[tuple[str, ...], int, str],
        list[int],
    ] = {}
    for new_index, section in enumerate(new_sections):
        if _direct_child_ordinal(section) is None:
            continue
        new_children_by_key.setdefault(
            (section.number_path[:-1], section.level, _review_unit_key(section.title)),
            [],
        ).append(new_index)

    rescued: list[tuple[int, int, str]] = []
    local_matched_old = set(matched_old)
    local_matched_new = set(matched_new)
    for old_index, old_section in enumerate(old_sections):
        if old_index in local_matched_old:
            continue
        old_ordinal = _direct_child_ordinal(old_section)
        title_key = _review_unit_key(old_section.title)
        old_parent = old_section.number_path[:-1]
        if old_ordinal is None or not title_key or not old_parent:
            continue
        parent_targets = old_parent_targets.get(old_parent, set())
        if len(parent_targets) != 1:
            continue
        new_parent = next(iter(parent_targets))
        if len(new_parent_sources.get(new_parent, set())) != 1:
            continue
        candidate_indexes = new_children_by_key.get(
            (new_parent, old_section.level, title_key),
            [],
        )
        if len(candidate_indexes) != 1:
            continue
        new_index = candidate_indexes[0]
        if new_index in local_matched_new:
            continue
        new_section = new_sections[new_index]
        new_ordinal = _direct_child_ordinal(new_section)
        if new_ordinal is None:
            continue
        if old_title_counts[(old_parent, old_section.level, title_key)] != 1:
            continue
        if new_title_counts[(new_parent, new_section.level, title_key)] != 1:
            continue
        if old_ordinal_counts[(old_parent, old_section.level, old_ordinal)] != 1:
            continue
        if new_ordinal_counts[(new_parent, new_section.level, new_ordinal)] != 1:
            continue

        match_basis = ""
        body_similarity = _section_similarity(old_section.body, new_section.body)
        if (
            old_parent != new_parent
            and old_ordinal == new_ordinal
        ):
            if body_similarity >= minimum_similarity:
                match_basis = "structural_mapped_parent_body"
            elif (
                _STRUCTURAL_NUMBER_SHIFT_SCORE >= minimum_similarity
                and _mapped_parent_boundary_child_has_independent_support(
                    old_sections,
                    new_sections,
                    old_section,
                    new_section,
                    old_ordinal,
                    new_ordinal,
                    ordinary_pairs,
                )
            ):
                match_basis = "structural_mapped_parent_boundary"
        else:
            offset = new_ordinal - old_ordinal
            seed_key = (old_parent, new_parent, old_section.level, offset)
            seed_pairs = supported_seed_groups.get(seed_key, set())
            bracket_pairs = ordinary_shift_pairs_by_group.get(seed_key, set())
            if (
                offset != 0
                and seed_pairs
                and _STRUCTURAL_NUMBER_SHIFT_SCORE >= minimum_similarity
            ):
                shared_units, has_title_link = _shared_unique_shift_prose_units(
                    old_section,
                    new_section,
                    old_prose_counts,
                    new_prose_counts,
                )
                if has_title_link and len(shared_units) >= 2:
                    match_basis = "structural_shift_run_body"
                elif (
                    has_title_link
                    and len(shared_units) == 1
                    and _ordinary_shift_brackets_candidate(
                        old_sections,
                        new_sections,
                        old_section,
                        new_section,
                        old_ordinal,
                        new_ordinal,
                        bracket_pairs,
                    )
                ):
                    match_basis = "structural_shift_bracketed_sentence"
        if not match_basis:
            continue
        rescued.append((old_index, new_index, match_basis))
        local_matched_old.add(old_index)
        local_matched_new.add(new_index)
    return sorted(rescued, key=lambda item: (item[1], item[0]))


def _mapped_parent_boundary_child_has_independent_support(
    old_sections: list[Section],
    new_sections: list[Section],
    old_section: Section,
    new_section: Section,
    old_ordinal: int,
    new_ordinal: int,
    ordinary_pairs: set[tuple[int, int]],
) -> bool:
    """Prove a noisy first/last child using one sibling and one direct descendant."""

    old_parent = old_section.number_path[:-1]
    new_parent = new_section.number_path[:-1]
    old_sibling_ordinals = {
        ordinal
        for section in old_sections
        if section.number_path[:-1] == old_parent
        and section.level == old_section.level
        and (ordinal := _direct_child_ordinal(section)) is not None
    }
    new_sibling_ordinals = {
        ordinal
        for section in new_sections
        if section.number_path[:-1] == new_parent
        and section.level == new_section.level
        and (ordinal := _direct_child_ordinal(section)) is not None
    }
    if not old_sibling_ordinals or not new_sibling_ordinals:
        return False
    same_boundary = (
        old_ordinal == min(old_sibling_ordinals)
        and new_ordinal == min(new_sibling_ordinals)
    ) or (
        old_ordinal == max(old_sibling_ordinals)
        and new_ordinal == max(new_sibling_ordinals)
    )
    if not same_boundary:
        return False  # 中间子章缺一侧邻居时保持未配，不能借边界规则放宽。

    sibling_supported = False
    descendant_supported = False
    for old_index, new_index in ordinary_pairs:
        old_candidate = old_sections[old_index]
        new_candidate = new_sections[new_index]
        if (
            old_candidate.number_path[:-1] == old_parent
            and new_candidate.number_path[:-1] == new_parent
            and old_candidate.level == old_section.level
            and new_candidate.level == new_section.level
        ):
            old_candidate_ordinal = _direct_child_ordinal(old_candidate)
            new_candidate_ordinal = _direct_child_ordinal(new_candidate)
            if (
                old_candidate_ordinal is not None
                and new_candidate_ordinal is not None
                and abs(old_candidate_ordinal - old_ordinal) == 1
                and new_candidate_ordinal - new_ordinal
                == old_candidate_ordinal - old_ordinal
                and _review_unit_key(old_candidate.title)
                == _review_unit_key(new_candidate.title)
            ):
                sibling_supported = True
        if (
            old_candidate.number_path[:-1] == old_section.number_path
            and new_candidate.number_path[:-1] == new_section.number_path
            and old_candidate.level == old_section.level + 1
            and new_candidate.level == new_section.level + 1
            and _review_unit_key(old_candidate.title)
            == _review_unit_key(new_candidate.title)
        ):
            descendant_supported = True
        if sibling_supported and descendant_supported:
            return True
    return False


def _direct_child_ordinal(section: Section) -> int | None:
    """Return a numeric ordinal only when the leaf directly extends its parent."""

    if len(section.number_path) < 2:
        return None
    parent_number = section.number_path[-2]
    child_number = section.number_path[-1]
    if not re.fullmatch(r"\d+(?:\.\d+)*", parent_number):
        return None
    if not re.fullmatch(r"\d+(?:\.\d+)*", child_number):
        return None
    parent_parts = tuple(int(part) for part in parent_number.split("."))
    child_parts = tuple(int(part) for part in child_number.split("."))
    if child_parts[:-1] != parent_parts:
        return None
    return child_parts[-1]


def _shift_prose_units(section: Section) -> tuple[tuple[str, str], ...]:
    """Return complete substantive prose units; captions and table rows are excluded."""

    units: list[tuple[str, str]] = []
    for unit in _paragraph_review_units(section.body, suppressed_table_unit_keys=set()):
        compact = compact_inline(unit)
        if len(compact) < _SECTION_SHIFT_PROSE_MIN_CHARS:
            continue
        if not _ends_review_sentence(compact):
            continue
        if not _SECTION_IDENTITY_PROSE_VERB_RE.search(compact):
            continue
        if len(_meaningful_review_words(compact)) < 4:
            continue
        key = _review_unit_key(compact)
        if key:
            units.append((key, compact))
    return tuple(units)


def _shift_prose_unit_counts(
    sections: list[Section],
) -> Counter[tuple[tuple[str, ...], int, str]]:
    """Count prose anchors in the original parent domain before any rescue."""

    return Counter(
        (section.number_path[:-1], section.level, key)
        for section in sections
        for key, _unit in _shift_prose_units(section)
    )


def _shared_unique_shift_prose_units(
    old_section: Section,
    new_section: Section,
    old_counts: Counter[tuple[tuple[str, ...], int, str]],
    new_counts: Counter[tuple[tuple[str, ...], int, str]],
) -> tuple[tuple[str, ...], bool]:
    """Return exact substantive sentences unique within both original parents."""

    old_units = dict(_shift_prose_units(old_section))
    new_units = dict(_shift_prose_units(new_section))
    shared: list[str] = []
    has_title_link = False
    for key in old_units.keys() & new_units.keys():
        old_count_key = (old_section.number_path[:-1], old_section.level, key)
        new_count_key = (new_section.number_path[:-1], new_section.level, key)
        if old_counts[old_count_key] != 1 or new_counts[new_count_key] != 1:
            continue
        shared.append(key)
        if _has_section_identity_title_link(old_units[key], old_section.title):
            has_title_link = True
    return tuple(sorted(shared)), has_title_link


def _ordinary_shift_brackets_candidate(
    old_sections: list[Section],
    new_sections: list[Section],
    old_section: Section,
    new_section: Section,
    old_ordinal: int,
    new_ordinal: int,
    seed_pairs: set[tuple[int, int]],
) -> bool:
    """Require ordinary same-offset siblings immediately before and after a weak target."""

    old_neighbors: dict[int, list[int]] = {}
    new_neighbors: dict[int, list[int]] = {}
    for index, section in enumerate(old_sections):
        if section.number_path[:-1] == old_section.number_path[:-1] and section.level == old_section.level:
            ordinal = _direct_child_ordinal(section)
            if ordinal is not None:
                old_neighbors.setdefault(ordinal, []).append(index)
    for index, section in enumerate(new_sections):
        if section.number_path[:-1] == new_section.number_path[:-1] and section.level == new_section.level:
            ordinal = _direct_child_ordinal(section)
            if ordinal is not None:
                new_neighbors.setdefault(ordinal, []).append(index)
    for direction in (-1, 1):
        old_indexes = old_neighbors.get(old_ordinal + direction, [])
        new_indexes = new_neighbors.get(new_ordinal + direction, [])
        if len(old_indexes) != 1 or len(new_indexes) != 1:
            return False
        if (old_indexes[0], new_indexes[0]) not in seed_pairs:
            return False
    return True


def _matched_adjacent_brackets_support(
    old_sections: list[Section],
    new_sections: list[Section],
    old_index: int,
    new_index: int,
    matched_pairs: set[tuple[int, int]],
) -> bool:
    """Require two already matched direct neighbors around one rescue candidate."""

    candidate_old = old_sections[old_index]
    candidate_new = new_sections[new_index]
    if not candidate_old.body.strip() or not candidate_new.body.strip():
        return False
    for offset in (-1, 1):
        neighbor_old_index = old_index + offset
        neighbor_new_index = new_index + offset
        if not (0 <= neighbor_old_index < len(old_sections)):
            return False
        if not (0 <= neighbor_new_index < len(new_sections)):
            return False
        if (neighbor_old_index, neighbor_new_index) not in matched_pairs:
            return False  # 必须是显式相互配对，不能只凭两个索引各自已使用。
        old_neighbor = old_sections[neighbor_old_index]
        new_neighbor = new_sections[neighbor_new_index]
        if not old_neighbor.number_path or old_neighbor.number_path != new_neighbor.number_path:
            return False
        if old_neighbor.level != new_neighbor.level:
            return False
        old_title_key = _review_unit_key(old_neighbor.title)
        new_title_key = _review_unit_key(new_neighbor.title)
        if not old_title_key or old_title_key != new_title_key:
            return False
        if not old_neighbor.body.strip() or not new_neighbor.body.strip():
            return False
    return True


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


def _exact_identity_similarity(left: str, right: str) -> float | None:
    """Score same-identity sections without penalizing proven spelling equivalence."""

    left_sample = _sample_section_text(left)
    right_sample = _sample_section_text(right)
    raw_score = _similarity(left_sample, right_sample)
    left_units = _paragraph_review_units(
        left,
        suppressed_table_unit_keys=set(),
    )
    right_units = _paragraph_review_units(
        right,
        suppressed_table_unit_keys=set(),
    )
    if not left_units and not right_units:
        return raw_score
    if not left_units or not right_units:
        return None  # 空容器不能仅凭占用相同编号抢配另一条有正文的章节。
    left_review_key = _review_unit_key(left)
    right_review_key = _review_unit_key(right)
    if left_review_key and left_review_key == right_review_key:
        return 1.0  # 整段语义键完全相等时，句末标点造成的分句差异不应破坏章节身份。
    if len(left_units) != len(right_units):
        if not _review_units_have_complete_skeleton_matching(left_units, right_units):
            return None
        return max(
            raw_score,
            _review_similarity(left_sample, right_sample),
            0.90,
        )
    if len(left_units) == 1:
        left_field = _assignment_field_key(left_units[0])
        right_field = _assignment_field_key(right_units[0])
        if left_field and left_field == right_field:
            return max(raw_score, 0.90)  # 稳定字段名可以证明 Mode/State 的值槽修改。
        if (
            _review_unit_key(left_units[0]) != _review_unit_key(right_units[0])
            and (
                _unit_has_unproven_label_syntax(left_units[0])
                or _unit_has_unproven_label_syntax(right_units[0])
            )
        ):
            return None  # 单句冒号标签无法在无 schema provenance 时证明字段身份。
        return raw_score  # 无局部结构证据时仍按原始相似度，不让长标题强行配对 ALPHA/OMEGA。
    matched_unit_count = _review_unit_skeleton_match_count(left_units, right_units)
    if matched_unit_count < len(left_units):
        stable_ratio = matched_unit_count / len(left_units)
        if matched_unit_count < 2 or stable_ratio < 0.75:
            return None  # 少量通用句不能替互异正文自证章节身份。
        return max(raw_score, _review_similarity(left_sample, right_sample))
        # 大多数独立片段稳定时保留同章，剩余互异片段仍按增删显示且不伪造 0.90 分。
    return max(
        raw_score,
        _review_similarity(left_sample, right_sample),
        0.90,
    )  # 每个对应句均已通过局部骨架门，技术枚举长度不应再否决章节身份。


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


def _review_units_share_sentence_skeleton(left: str, right: str) -> bool:
    """Require local lexical continuity without treating a technical edit as disjoint prose."""

    if _review_unit_key(left) == _review_unit_key(right):
        return True
    if _unit_has_unproven_label_syntax(left) or _unit_has_unproven_label_syntax(right):
        return False  # 无 schema provenance 时，冒号标签不猜测字段身份。
    if max(_review_similarity(left, right), _similarity(left, right)) >= 0.90:
        return True  # 短数值、状态词或大小写技术标识符变化仍是同一句。
    left_field = _assignment_field_key(left)
    right_field = _assignment_field_key(right)
    if left_field and left_field == right_field:
        return True  # 显式 `=` 左值稳定时，值槽的不透明技术枚举仍是同一条修改。
    left_words = _meaningful_review_words(left)
    right_words = _meaningful_review_words(right)
    if not left_words or not right_words:
        return False
    shared_words = left_words & right_words
    return len(shared_words) >= 2 and len(shared_words) / min(
        len(left_words), len(right_words)
    ) >= 0.60  # 要求大部分实质词不变，拒绝只共享 defines/requirements 类套话的互异正文。


def _review_units_have_complete_skeleton_matching(
    left_units: list[str],
    right_units: list[str],
) -> bool:
    """Prove every unit on the shorter side has one distinct semantic peer."""

    return _review_unit_skeleton_match_count(left_units, right_units) == min(
        len(left_units), len(right_units)
    )


def _review_unit_skeleton_match_count(
    left_units: list[str],
    right_units: list[str],
) -> int:
    """Return the maximum number of one-to-one semantic unit matches."""

    shorter, longer = (
        (left_units, right_units)
        if len(left_units) <= len(right_units)
        else (right_units, left_units)
    )
    candidates: list[list[int]] = []
    for short_unit in shorter:
        short_key = _review_unit_key(short_unit)
        indexes = [
            index
            for index, long_unit in enumerate(longer)
            if _review_units_share_sentence_skeleton(short_unit, long_unit)
        ]
        indexes.sort(
            key=lambda index: _review_unit_key(longer[index]) == short_key,
            reverse=True,
        )  # 先占用完全相等的同句，再用骨架配对真实字段/数值修改。
        candidates.append(indexes)

    matched_short_by_long: dict[int, int] = {}

    def assign(short_index: int, visited_long: set[int]) -> bool:
        for long_index in candidates[short_index]:
            if long_index in visited_long:
                continue
            visited_long.add(long_index)
            previous_short = matched_short_by_long.get(long_index)
            if previous_short is None or assign(previous_short, visited_long):
                matched_short_by_long[long_index] = short_index
                return True
        return False

    matched_count = 0
    for short_index in range(len(shorter)):
        if assign(short_index, set()):
            matched_count += 1
    return matched_count


def _assignment_field_key(value: str) -> str:
    """Return a stable left-hand field only for explicit equals assignments."""

    compact = compact_inline(value).strip(".?!！？。 ")
    if compact.count("=") != 1:
        return ""
    operator_index = compact.index("=")
    field = compact[:operator_index].rstrip()
    assigned_value = compact[operator_index + 1 :].lstrip()
    if not field or not assigned_value:
        return ""
    field_token = r"\w+(?:[./-]\w+)*(?:\[[\w.-]+\])?"
    if len(field) > 80 or re.fullmatch(
        rf"{field_token}(?:\s+{field_token})*",
        field,
    ) is None:
        return ""  # 只让紧凑字段名充当身份证据；条件/匹配运算式保守保持可见。
    if re.match(
        r'''(?:[\w"'([{]|[+\-−±]?\s*(?:\d+(?:\.\d*)?|\.\d+))''',
        assigned_value,
    ) is None:
        return ""
    field_key = normalize_for_similarity(field)
    if not field_key:
        return ""
    if not _meaningful_review_words(field) and not re.search(r"[\u4e00-\u9fff]", field):
        return ""
    return field_key


def _unit_has_unproven_label_syntax(value: str) -> bool:
    """Return True for changed label-like prose that lacks schema provenance."""

    return bool(re.search(r"[:：]", value))


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
    """Treat only conservatively normalized, exactly equal sections as unchanged."""

    body_same = _review_unit_key(old_section.body) == _review_unit_key(new_section.body)
    if _section_heading_changed(old_section, new_section):
        return False  # 标题变化始终需要展示，不能被长正文的高相似度掩盖。
    return body_same  # 相似度不能授权猜测复数、动词变化或其他语义等价。


def _delta_is_unverified_pua_mapping_only(
    added: list[str],
    removed: list[str],
    replaced: list[SnippetPair],
    omitted_count: int,
) -> bool:
    """Classify only fully visible PUA/lookalike replacements as review items."""

    if added or removed or omitted_count or not replaced:
        return False
    return all(
        pair.old != pair.new
        and bool(re.search(r"[\ue000-\uf8ff]", pair.old + pair.new))
        and reader_symbol_mapping_key(pair.old)
        == reader_symbol_mapping_key(pair.new)
        for pair in replaced
    )  # 任一其他正文变化或被截断片段都保持 modified，避免把真实修订降级成编码复核。


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
    suppressed_old_table_unit_keys: set[str] | None = None,
    suppressed_new_table_unit_keys: set[str] | None = None,
) -> tuple[
    list[str],
    list[str],
    list[SnippetPair],
    int,
    list[str],
    list[str],
    list[SnippetPair],
]:
    """Create compact added/removed/replaced snippets for one section."""

    old_units = _paragraph_review_units(
        old_text,
        suppressed_table_unit_keys=suppressed_old_table_unit_keys or set(),
    )
    new_units = _paragraph_review_units(
        new_text,
        suppressed_table_unit_keys=suppressed_new_table_unit_keys or set(),
    )  # 表格证据按版本分别承载，不能用另一侧字符串集合过滤本侧正文。
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

    reordered_pair, reordered_old_indexes, reordered_new_indexes = (
        _common_unit_occurrence_evidence(old_units, new_units)
    )
    if reordered_pair is not None:
        add_candidate(
            "replaced",
            pair=reordered_pair,
            priority_values=(reordered_pair.old, reordered_pair.new),
        )  # 句子搬移是可见语义变化，不能让 SequenceMatcher 只剩标点噪声。

    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert":
            for _index, text, source_units in _coherent_delta_unit_groups(
                [
                    (index, new_units[index])
                    for index in range(new_start, new_end)
                    if index not in reordered_new_indexes
                ]
            ):
                add_candidate(
                    "added",
                    text=text,
                    priority_values=source_units,
                )
        elif tag == "delete":
            for _index, text, source_units in _coherent_delta_unit_groups(
                [
                    (index, old_units[index])
                    for index in range(old_start, old_end)
                    if index not in reordered_old_indexes
                ]
            ):
                add_candidate(
                    "removed",
                    text=text,
                    priority_values=source_units,
                )
        elif tag == "replace":
            old_block_units = [
                old_units[index]
                for index in range(old_start, old_end)
                if index not in reordered_old_indexes
            ]
            new_block_units = [
                new_units[index]
                for index in range(new_start, new_end)
                if index not in reordered_new_indexes
            ]
            for candidate in _unequal_replace_delta_candidates(
                old_block_units,
                new_block_units,
            ):
                add_candidate(
                    candidate.kind,
                    text=candidate.text,
                    pair=candidate.pair,
                    priority_values=candidate.priority_values,
                )  # 等长和不等长块统一经过语义锚点与最低分门槛，禁止按位置强配。

    return _materialize_delta_candidates(candidates, max_snippets)


def _common_unit_occurrence_evidence(
    old_units: list[str],
    new_units: list[str],
) -> tuple[SnippetPair | None, set[int], set[int]]:
    """Separate exact common occurrences, true extras, and proven reordering."""

    old_keys = [_review_unit_key(unit) for unit in old_units]
    new_keys = [_review_unit_key(unit) for unit in new_units]
    old_counts = Counter(key for key in old_keys if key)
    new_counts = Counter(key for key in new_keys if key)
    common_counts = old_counts & new_counts
    if not common_counts:
        return None, set(), set()

    old_length = len(old_keys)
    new_length = len(new_keys)
    lcs_lengths = [
        [0] * (new_length + 1)
        for _old_index in range(old_length + 1)
    ]
    for old_index in range(old_length - 1, -1, -1):
        for new_index in range(new_length - 1, -1, -1):
            if old_keys[old_index] and old_keys[old_index] == new_keys[new_index]:
                lcs_lengths[old_index][new_index] = (
                    1 + lcs_lengths[old_index + 1][new_index + 1]
                )
            else:
                lcs_lengths[old_index][new_index] = max(
                    lcs_lengths[old_index + 1][new_index],
                    lcs_lengths[old_index][new_index + 1],
                )

    matched_old_indexes: set[int] = set()
    matched_new_indexes: set[int] = set()
    matched_counts: Counter[str] = Counter()
    old_index = 0
    new_index = 0
    while old_index < old_length and new_index < new_length:
        if (
            old_keys[old_index]
            and old_keys[old_index] == new_keys[new_index]
            and lcs_lengths[old_index][new_index]
            == 1 + lcs_lengths[old_index + 1][new_index + 1]
        ):
            matched_old_indexes.add(old_index)
            matched_new_indexes.add(new_index)
            matched_counts[old_keys[old_index]] += 1
            old_index += 1
            new_index += 1
        elif lcs_lengths[old_index + 1][new_index] >= lcs_lengths[old_index][new_index + 1]:
            old_index += 1
        else:
            new_index += 1

    moved_counts = common_counts - matched_counts

    def select_moved_indexes(
        keys: list[str],
        matched_indexes: set[int],
    ) -> set[int]:
        remaining = moved_counts.copy()
        selected: set[int] = set()
        for index, key in enumerate(keys):
            if index in matched_indexes or remaining.get(key, 0) <= 0:
                continue
            selected.add(index)
            remaining[key] -= 1
        return selected

    moved_old_indexes = select_moved_indexes(old_keys, matched_old_indexes)
    moved_new_indexes = select_moved_indexes(new_keys, matched_new_indexes)
    common_old_indexes = matched_old_indexes | moved_old_indexes
    common_new_indexes = matched_new_indexes | moved_new_indexes
    common_old_order = [old_keys[index] for index in sorted(common_old_indexes)]
    common_new_order = [new_keys[index] for index in sorted(common_new_indexes)]
    if common_old_order == common_new_order:
        return None, common_old_indexes, common_new_indexes
    return (
        SnippetPair(
            old="\n".join(
                _report_unit(old_units[index]) for index in sorted(common_old_indexes)
            ),
            new="\n".join(
                _report_unit(new_units[index]) for index in sorted(common_new_indexes)
            ),
        ),
        common_old_indexes,
        common_new_indexes,
    )


def _coherent_delta_unit_groups(
    indexed_units: list[tuple[int, str]],
) -> list[tuple[int, str, tuple[str, ...]]]:
    """Join prose only when source indexes prove one uninterrupted diff run."""

    groups: list[tuple[int, str, tuple[str, ...]]] = []
    prose_buffer: list[tuple[int, str]] = []

    def flush_prose() -> None:
        if not prose_buffer:
            return
        groups.append(
            (
                prose_buffer[0][0],
                " ".join(_report_unit(unit) for _index, unit in prose_buffer),
                tuple(unit for _index, unit in prose_buffer),
            )
        )
        prose_buffer.clear()

    previous_index: int | None = None
    for index, unit in indexed_units:
        if previous_index is not None and index != previous_index + 1:
            flush_prose()
        if _delta_unit_is_joinable_prose(unit):
            prose_buffer.append((index, unit))
        else:
            flush_prose()
            groups.append((index, _report_unit(unit), (unit,)))
        previous_index = index
    flush_prose()
    return groups


def _delta_unit_is_joinable_prose(value: str) -> bool:
    """Recognize sentence prose without joining numbered procedures or captions."""

    compact = compact_inline(value)
    if re.match(r"^(?:[-•●]|[A-Za-z][.)]|\d{1,3}[.)])\s+", compact):
        return False
    if re.match(r"(?i)^table\s+\d", compact):
        return False
    if not re.search(r"[.!?。！？]$", compact):
        return False
    latin_words = re.findall(r"[A-Za-z][A-Za-z0-9'-]*", compact)
    cjk_characters = re.findall(r"[\u4e00-\u9fff]", compact)
    return len(latin_words) >= 5 or len(cjk_characters) >= 12


def _paragraph_review_units(text: str, *, suppressed_table_unit_keys: set[str]) -> list[str]:
    """Return review units for paragraph cards, optionally excluding table rows."""

    prose_text = "\n".join(
        raw_line
        for raw_line in text.splitlines()
        if not _is_table_review_unit(raw_line)
        and _review_unit_key(raw_line) not in suppressed_table_unit_keys
    )  # 必须先按原始结构化行整体过滤；否则 NOTES 单元格内的句号会先拆掉前缀，再冒充正文。
    units = _split_units(prose_text)  # 表格由表格证据区承载，正文卡片只切分剩余文本。
    return [
        unit
        for unit in units
        if not _is_table_review_unit(unit)
        and _review_unit_key(unit) not in suppressed_table_unit_keys
    ]  # 结构化表格行及已由跨侧精确重建覆盖的原始整单元都不再进入正文卡片。


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
    return units  # 原始文本和结构化表格是两份可核查证据；没有坐标级同一性证明时不猜测删除。


def _merge_wrapped_lines(text: str) -> list[str]:
    """Merge PDF line wraps into review-sized sentences or procedure steps."""

    blocks: list[str] = []
    current = ""
    normalized_lines = [normalize_line(raw_line) for raw_line in text.splitlines()]
    for line in normalized_lines:
        if not line:
            continue
        cross_reference_join = _join_wrapped_cross_reference(current, line)
        if cross_reference_join is not None:
            current = cross_reference_join
            continue
        if _starts_new_review_block(line):
            if current:
                blocks.append(current)
            current = line
            continue
        if not current:
            current = line
        elif _ends_review_sentence(current) and not _is_standalone_list_marker(current):
            blocks.append(current)
            current = line  # 纯文本换行不能证明 `1.\n0 V` 是小数，也可能是两句技术要求。
        elif _looks_like_new_sentence_after_linebreak(current, line):
            blocks.append(current)
            current = line
        else:
            current = _join_wrapped_line(current, line)
    if current:
        blocks.append(current)
    return blocks


def _join_wrapped_cross_reference(left: str, right: str) -> str | None:
    """Rejoin a numbered reference split immediately after its hyphen."""

    if not left:
        return None
    plain_reference = re.search(
        rf"(?i)\b(?:table|figure|equation|section)\s+\d+\s*{TABLE_NUMBER_DASH_CLASS}\s*$",
        left,
    )
    parenthesized_reference = re.search(
        rf"(?i)\b(?:equation|figure|table|section)\s+\(\d+\s*{TABLE_NUMBER_DASH_CLASS}\s*$",
        left,
    )
    if parenthesized_reference is not None:
        match = re.fullmatch(r"(\d+)\)(?:\s+(.*))?", right)
        if match is None:
            return None
        joined = f"{left.rstrip()}{match.group(1)})"
        return f"{joined} {match.group(2)}" if match.group(2) else joined
    if plain_reference is None:
        return None
    match = re.fullmatch(r"(\d+)\.(?:\s+(.*))?", right)
    if match is None:
        return None
    joined = f"{left.rstrip()}{match.group(1)}."
    return f"{joined} {match.group(2)}" if match.group(2) else joined


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
    """Join adjacent extracted lines without guessing away visible glyphs."""

    return f"{left} {right}"


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
    r"<=>|<->|->|<-|=>|<=|>=|!=|==|≤|≥|≠"
    r"|[+-]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)(?:e[+-]?\d+)?"
    r"|(?:[^\W\d_]|_)\w*(?:[-/]\w+)*"
    r"|[^\w\s.,;:?\"“”'‘’，。；：！？、]",
    flags=re.I,
)
_REVIEW_NUMBER_PHRASE_BOUNDARY_SENTINEL = "number-phrase-boundary-sentinel"
_CASE_BEARING_TOKEN_RE = re.compile(
    r"(?<!\w)(?:[^\W\d_]|_)\w*(?:[-/]\w+)*(?!\w)"
)
_SEMANTIC_OPERATOR_RE = re.compile(
    r"[\u2061-\u2064]"
    r"|(?<=[A-Za-z0-9)\]])\s+([+\-−*/×÷])\s+(?=[A-Za-z0-9(\[])"
    r"|(?<=[A-Za-z0-9)\]])([+*×÷−])(?=[A-Za-z0-9(\[])"
)  # 保留明确的公式运算符；ASCII 连字符仅在两侧有空格时视作减号，避免误伤词内连字符。
_SUPERSCRIPT_TRANSLATION = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾", "0123456789+-=()")
_SUBSCRIPT_TRANSLATION = str.maketrans("₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎", "0123456789+-=()")
_SUPERSCRIPT_CHARS = "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ"
_SUBSCRIPT_CHARS = "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ"
_PROTECTED_NUMBER_WORD_PREFIXES = frozenset(
    {
        "appendix",
        "build",
        "clause",
        "code",
        "codes",
        "figure",
        "gen",
        "generation",
        "id",
        "identifier",
        "model",
        "models",
        "part",
        "profile",
        "profiles",
        "rev",
        "revision",
        "serial",
        "section",
        "table",
        "type",
        "version",
        "versions",
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

    The key ignores ordinary prose case, whitespace, and sentence punctuation,
    but keeps meaningful numeric tokens and technical-token case intact. That
    prevents false equivalence between values such as ``1.0 ps`` and ``10 ps``
    or identifiers such as ``MODE_FAST`` and ``mode_fast``.
    """

    normalized_list_value = _normalize_known_body_compact_symbol_spacing(
        _normalize_leading_list_marker(value)
    )  # Z c/Zc 等有上下文证明的排版空格可统一；无字体 provenance 的 PUA 必须保留差异。
    case_signature_source = _normalize_math_symbol_artifacts(
        _normalize_embedded_number_list_spacing(normalized_list_value)
    )
    case_signature_source = _normalize_directional_symbols(case_signature_source)
    case_signature_source = re.sub(
        r"(?<=\d)(?![eE][+-]?\d)(?=(?:[^\W\d_]|_))",
        " ",
        case_signature_source,
    )  # `5V` 与 `5 V` 的单位大小写证据必须一致。
    case_signatures = _technical_case_signatures(case_signature_source)
    operator_signatures = _semantic_operator_signatures(case_signature_source)
    ampersand_signatures = _semantic_ampersand_signatures(normalized_list_value)
    modifier_signatures = _measurement_modifier_signatures(normalized_list_value)
    punctuation_signature_source = _normalize_embedded_number_list_spacing(
        normalized_list_value
    )
    punctuation_signature_source = mark_english_cardinal_list_commas(
        punctuation_signature_source
    )
    punctuation_signature_source = re.sub(
        r"(?<=\d)(?![eE][+-]?\d)(?=(?:[^\W\d_]|_))",
        " ",
        punctuation_signature_source,
    )
    punctuation_signatures = _contextual_punctuation_signatures(
        punctuation_signature_source
    )
    script_signatures = _super_subscript_signatures(normalized_list_value)
    lexical_number_signatures = _lexical_number_signatures(normalized_list_value)
    identifier_number_signatures = _identifier_number_signatures(normalized_list_value)
    identifier_literal_signatures = _identifier_literal_signatures(normalized_list_value)
    dotted_identifier_signatures = _dotted_identifier_signatures(normalized_list_value)
    path_identifier_signatures = _path_identifier_signatures(normalized_list_value)
    joined_identifier_signatures = identifier_boundary_signatures(normalized_list_value)
    micro_literal_signatures = micro_identifier_signatures(normalized_list_value)
    normalized = normalize_for_similarity(normalized_list_value)
    normalized = canonicalize_chinese_number_expressions(normalized)
    normalized = normalized.replace("µ", "u").replace("μ", "u")
    normalized = normalized.replace("&", " and ")
    normalized = normalized.replace("≤", "<=").replace("≥", ">=")
    normalized = _normalize_math_symbol_artifacts(normalized)
    normalized = _normalize_directional_symbols(normalized)
    normalized = _normalize_embedded_number_list_spacing(normalized)
    normalized = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=\d)", "", normalized)
    normalized = re.sub(r"(?<=\d)\s+(?=[\u4e00-\u9fff])", "", normalized)
    token_source = mark_english_cardinal_list_commas(normalized)
    token_source = re.sub(
        r"(?<!\d)[.,;:?!，。；：！？]|[.,;:?!，。；：！？](?!\d)",
        f" {_REVIEW_NUMBER_PHRASE_BOUNDARY_SENTINEL} ",
        token_source,
    )
    tokens = [
        _canonical_review_token(token) for token in _REVIEW_TOKEN_RE.findall(token_source)
    ]
    canonical_tokens = canonicalize_number_word_tokens(
        tokens,
        protected_previous_words=_PROTECTED_NUMBER_WORD_PREFIXES,
        protected_next_words=_PROTECTED_NUMBER_WORD_SUFFIXES,
    )
    canonical_tokens = [
        token
        for token in canonical_tokens
        if token != _REVIEW_NUMBER_PHRASE_BOUNDARY_SENTINEL
    ]
    return " ".join(
        [
            *canonical_tokens,
            *(f"case:{token}" for token in case_signatures),
            *(f"operator:{operator}" for operator in operator_signatures),
            *(f"operator:{operator}" for operator in ampersand_signatures),
            *(f"modifier:{modifier}" for modifier in modifier_signatures),
            *(f"punctuation:{punctuation}" for punctuation in punctuation_signatures),
            *(f"script:{script}" for script in script_signatures),
            *(f"lexical-number:{number}" for number in lexical_number_signatures),
            *(f"identifier-number:{number}" for number in identifier_number_signatures),
            *(f"identifier-literal:{literal}" for literal in identifier_literal_signatures),
            *(f"dotted-identifier:{literal}" for literal in dotted_identifier_signatures),
            *(f"path-identifier:{literal}" for literal in path_identifier_signatures),
            *(f"identifier-boundary:{literal}" for literal in joined_identifier_signatures),
            *(f"micro-identifier:{literal}" for literal in micro_literal_signatures),
        ]
    )


def _normalize_known_body_compact_symbol_spacing(value: str) -> str:
    """Join only verified split engineering symbols in ordinary body text."""

    pattern = re.compile(
        r"(?<![A-Za-z0-9_])([A-Za-z])\s+([a-z]+)([0-9_.+-]{0,2})(?![A-Za-z0-9_])"
    )

    def replace_match(match: re.Match[str]) -> str:
        base, suffix, qualifier = match.groups()
        if not is_known_engineering_symbol_letter_suffix(base, suffix):
            return match.group(0)
        local_prefix = re.split(
            r"[,;:.!?\n=+*/<>≤≥]",
            value[: match.start()],
        )[-1][-80:]
        local_words = re.findall(
            r"[A-Za-z]+|[\u3400-\u4dbf\u4e00-\u9fff]+",
            local_prefix,
        )
        nearest_quantity_name = local_words[-1] if local_words else ""
        algebraic_context = re.search(
            r"(?i)\b(?:expression|equation|denote|variables?|let)\b",
            local_prefix,
        )
        local_suffix = value[match.end() : match.end() + 100]
        separated_symbol_context = re.search(
            r"(?i)^\s*(?:"
            r"denotes?\s+(?:two|separate)\b|"
            r"(?:is|are)\s+not\s+(?:one|a(?:\s+single)?)\s+symbol\b|"
            r"(?:should\s+)?remain(?:s)?\s+(?:spaced|separated)\b|"
            r"means?\b.*\bmultiplied\s+by\b|"
            r"represents?\b.*\btimes\b|"
            r"and\s+\w+\s+(?:is|are)\s+separate\s+variables?\b"
            r")",
            local_suffix,
        )
        if (
            algebraic_context
            or not has_measurement_context(nearest_quantity_name)
            or separated_symbol_context
        ):
            return match.group(0)  # 量名必须在同一局部短语中先于符号；后文的 voltage/resistance 不能全句授权。
        return f"{base}{suffix}{qualifier}"

    return pattern.sub(replace_match, value)


def _technical_case_signatures(value: str) -> list[str]:
    """Preserve case only where token shape indicates technical semantics."""

    signatures: list[str] = []
    normalized_value = normalize_line(value)
    for token_index, match in enumerate(_CASE_BEARING_TOKEN_RE.finditer(normalized_value)):
        token = match.group(0)
        letters = [character for character in token if character.isalpha()]
        has_cased_letter = any(
            character.lower() != character.upper() for character in letters
        )
        if not has_cased_letter:
            continue  # 汉字等无大小写文字不应因夹带数字而被当成技术大小写。
        has_letter_and_digit = bool(letters) and any(character.isdigit() for character in token)
        has_internal_upper = any(character.isupper() for character in token[1:])
        is_all_upper = len(letters) >= 2 and all(character.isupper() for character in letters)
        is_single_letter = len(token) == 1 and len(letters) == 1
        has_non_ascii_case = any(
            ord(character) > 127 and character.lower() != character.upper()
            for character in letters
        )
        prefix = normalized_value[: match.start()]
        is_measurement_unit = bool(
            re.search(r"\d(?:\.\d+)?\s*$", prefix)
            and any(character.isupper() for character in letters)
        )
        is_short_mixed_case = (
            len(letters) == 2
            and any(character.isupper() for character in letters)
            and any(character.islower() for character in letters)
            and match.start() > 0
        )
        is_non_initial_titlecase = (
            len(letters) >= 2
            and token[0].isupper()
            and all(character.islower() for character in letters[1:])
            and not _token_starts_sentence(normalized_value, match)
        )
        has_explicit_technical_context = _token_has_explicit_technical_context(
            normalized_value,
            match,
        )
        if token.islower() and is_english_cardinal_word(token):
            has_explicit_technical_context = False
        if (
            "_" in token
            or has_letter_and_digit
            or has_internal_upper
            or is_all_upper
            or is_measurement_unit
            or is_short_mixed_case
            or is_non_initial_titlecase
            or has_explicit_technical_context
            or (is_single_letter and _single_letter_case_is_technical(normalized_value, match))
            or (has_non_ascii_case and any(character.isupper() for character in letters))
        ):
            signatures.append(f"{token_index}:{token}")
    return signatures


def _token_has_explicit_technical_context(value: str, match: re.Match[str]) -> bool:
    """Recognize assignment operands and function names without a word allowlist."""

    before = value[: match.start()].rstrip()
    after = value[match.end() :].lstrip()
    if before.endswith(("=", "(", "[", "{", "$", "@", "#")) or after.startswith(
        ("=", "(", ":")
    ):
        return True
    if before.endswith(("<", "</")) and after.startswith((">", "/>")):
        return True  # XML/HTML element names are case-sensitive in several real formats.
    if before.endswith("/") and after.startswith("/"):
        return True  # A visibly slash-delimited token is syntax, not ordinary prose case.
    line_before = before[before.rfind("\n") + 1 :]
    line_after = after[: after.find("\n") if "\n" in after else len(after)]
    if (
        re.fullmatch(r"[^:\n]{1,80}:\s*", line_before)
        and re.fullmatch(r"[\s.,;!?…)}\]]*", line_after)
    ):
        return True  # Generic short ``label: value`` records preserve the value literally.
    if "=" in line_before or _inside_unclosed_group(line_before):
        return True  # 赋值表达式和调用/列表内部的后续 operand 继承技术上下文。
    return bool(
        re.search(
            r"(?i)\b(?:build|enum|filename|id|identifier|mode|model|option|profile|setting|state|value|variable|version)\s*$",
            before,
        )
    )


def _token_starts_sentence(value: str, match: re.Match[str]) -> bool:
    """Return whether ordinary sentence capitalization can explain this token."""

    before = value[: match.start()].rstrip()
    return not before or before.endswith((".", "!", "?", "。", "！", "？"))


def _inside_unclosed_group(value: str) -> bool:
    """Return True when the text ends inside ``()``, ``[]``, or ``{}``."""

    stack: list[str] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    for character in value:
        if character in "([{":
            stack.append(character)
        elif character in pairs and stack and stack[-1] == pairs[character]:
            stack.pop()
    return bool(stack)


def _single_letter_case_is_technical(value: str, match: re.Match[str]) -> bool:
    """Use visible local context to distinguish variables/units from prose articles."""

    compact = normalize_line(value)
    if re.fullmatch(r"[^\W\d_]", compact):
        return True  # 独立单字母行可以是状态、枚举或变量。
    if match.group(0).casefold() not in {"a", "i"}:
        return True  # 英文中除冠词 A/代词 I 外，独立字母默认是变量、单位或状态。
    before = value[: match.start()].rstrip()
    after = value[match.end() :].lstrip()
    if re.search(r"\d\s*$", before):
        return True  # 数值后的 V/v 等单字母通常是单位。
    if re.search(
        r"(?i)\b(?:variable|state|mode|symbol|unit|enum|class|grade|level|channel|port|pin|node)\s*$",
        before,
    ):
        return True
    if before.endswith(("=", "(", "[", "{", "+", "-", "*", "/", "×", "÷")):
        return True
    if after.startswith(("=", ")", "]", "}", "+", "-", "*", "/", "×", "÷", "<", ">")):
        return True
    return False


def _normalize_leading_list_marker(value: str) -> str:
    """Ignore only position-proven bullet/list-marker presentation changes."""

    normalized = re.sub(r"^\s*[•●⚫]\s*", "", value)
    normalized = re.sub(r"^\s*(\d{1,3}|[A-Za-z])[.)]\s+", r"\1 ", normalized)
    return normalized


def _measurement_modifier_signatures(value: str) -> list[str]:
    """Preserve ASCII prime/foot/inch glyphs when their context is technical."""

    signatures: list[str] = []
    pattern = re.compile(r"(?P<base>\w+)\s*(?P<glyph>['\"])(?!\w)", flags=re.UNICODE)
    for match in pattern.finditer(value):
        base = match.group("base")
        glyph = match.group("glyph")
        if base.isdigit():
            kind = "foot" if glyph == "'" else "inch"
        elif len(base) == 1 or _has_identifier_token(base):
            kind = "prime" if glyph == "'" else "double-prime"
        else:
            continue  # 普通英文复数所有格等不应被猜成公式 prime。
        position = _semantic_token_position(value, match.start())
        signatures.append(f"{position}:{base.casefold()}:{kind}")
    return signatures


def _contextual_punctuation_signatures(value: str) -> list[str]:
    """Preserve punctuation only where neighboring syntax makes it structural."""

    signatures: list[str] = []
    punctuation_run_ranges: set[int] = set()
    for match in re.finditer(r"[.,;:?!]{2,}", value):
        position = _punctuation_token_position(value, match.start())
        signatures.append(f"{position}:run:{match.group(0)}")
        punctuation_run_ranges.update(range(match.start(), match.end()))
    for index, character in enumerate(value):
        if character not in ".:,;?":
            continue
        if index in punctuation_run_ranges:
            continue
        left_index = index - 1
        while left_index >= 0 and value[left_index].isspace():
            left_index -= 1
        right_index = index + 1
        while right_index < len(value) and value[right_index].isspace():
            right_index += 1
        left = value[left_index].casefold() if left_index >= 0 else ""
        right = value[right_index].casefold() if right_index < len(value) else ""
        if (
            character == "."
            and index > 0
            and index + 1 < len(value)
            and value[index - 1].isdigit()
            and value[index + 1].isdigit()
        ):
            continue  # 小数点由数字 token 自身保真，不再绑定原始 token 位置。
        if character == "," and any(
            match.start() < index < match.end() and "," in match.group(0)
            for match in _NUMBER_TOKEN_RE.finditer(value)
        ):
            continue  # 合法千分位逗号由完整数字 token 归一，不能误当列表分隔符。
        if character in ".:,;" and left and right and (left.isalnum() or left == "_") and (right.isalnum() or right == "_"):
            kind = {".": "dot", ":": "colon", ",": "comma", ";": "semicolon"}[character]
            position = _punctuation_token_position(value, index)
            signatures.append(f"{position}:{kind}")
        elif character == "?" and left and (left.isalnum() or left == "_"):
            position = _punctuation_token_position(value, index)
            signatures.append(f"{position}:question:{left}")
    stack: list[str] = []
    matching = {")": "(", "]": "[", "}": "{"}
    for index, character in enumerate(value):
        if character in "([{":
            stack.append(character)
            continue
        if character in matching:
            if stack and stack[-1] == matching[character]:
                stack.pop()
            continue
        if character not in ",;:" or not stack:
            continue
        left = value[index - 1].casefold() if index > 0 else ""
        right = value[index + 1].casefold() if index + 1 < len(value) else ""
        position = _punctuation_token_position(value, index)
        signatures.append(f"{position}:group-{ord(character)}:{left}:{right}")
    for match in re.finditer(
        r"([\"“'‘])(?P<literal>[^\"”'’\n]+)([\"”'’])",
        value,
        flags=re.UNICODE,
    ):
        position = _punctuation_token_position(value, match.start())
        signatures.append(
            f"{position}:quoted:{normalize_line(match.group('literal'))}"
        )
    for match in re.finditer(r"`(?P<literal>[^`\n]+)`", value):
        position = _punctuation_token_position(value, match.start())
        signatures.append(
            f"{position}:backtick:{normalize_line(match.group('literal'))}"
        )
    return signatures


def _super_subscript_signatures(value: str) -> list[str]:
    """Capture compatibility glyph semantics before NFKC flattens them."""

    signatures: list[tuple[int, str]] = []
    for kind, characters, translation in (
        ("sup", _SUPERSCRIPT_CHARS, _SUPERSCRIPT_TRANSLATION),
        ("sub", _SUBSCRIPT_CHARS, _SUBSCRIPT_TRANSLATION),
    ):
        pattern = re.compile(rf"(?P<base>\w)?(?P<script>[{re.escape(characters)}]+)")
        for match in pattern.finditer(value):
            base = (match.group("base") or "").casefold()
            script = match.group("script").translate(translation)
            position = _semantic_token_position(value, match.start())
            signatures.append((match.start(), f"{position}:{base}:{kind}:{script}"))
    return [signature for _position, signature in sorted(signatures)]


def _lexical_number_signatures(value: str) -> list[str]:
    """Keep leading-zero number spelling used by models, profiles, and codes."""

    signatures: list[str] = []
    for number_index, match in enumerate(_NUMBER_TOKEN_RE.finditer(value)):
        token = match.group(0).lstrip("+-")
        if re.fullmatch(r"\d+", token) and len(token) > 1 and token.startswith("0"):
            signatures.append(f"{number_index}:{token}")
    return signatures


def _identifier_number_signatures(value: str) -> list[str]:
    """Preserve numeric spelling when a visible label makes it an identifier."""

    labels = (
        "build|code|codes|id|identifier|model|models|part|profile|profiles|"
        "rev|revision|serial|version|versions"
    )
    number = r"[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?(?:[eE][+\-]?\d+)?"
    signatures: list[str] = []
    for match in re.finditer(
        rf"(?i)\b(?P<label>{labels})\b\s*(?:[:=#]\s*)?(?P<number>{number})(?!\w)",
        value,
    ):
        raw_number = match.group("number").replace("−", "-").casefold()
        position = _semantic_token_position(value, match.start("number"))
        signatures.append(f"{position}:{match.group('label').casefold()}:{raw_number}")
    return signatures


def _identifier_literal_signatures(value: str) -> list[str]:
    """Capture a complete labeled version/build/file literal before token splitting."""

    labels = (
        "build|code|codes|filename|id|identifier|model|models|part|profile|profiles|"
        "rev|revision|serial|version|versions"
    )
    signatures: list[str] = []
    for match in re.finditer(
        rf"(?i)\b(?P<label>{labels})\b\s*(?:[:=#]\s*)?(?P<literal>[^\s,;]+)",
        value,
    ):
        literal = match.group("literal").rstrip(".)]}。")
        if not literal or not any(character.isdigit() for character in literal):
            continue
        position = _semantic_token_position(value, match.start("literal"))
        signatures.append(f"{position}:{match.group('label').casefold()}:{literal}")
    return signatures


def _dotted_identifier_signatures(value: str) -> list[str]:
    """Preserve complete dotted identifiers instead of normalizing each number."""

    signatures: list[str] = []
    for match in re.finditer(
        r"(?<![\w.])(?P<literal>[A-Za-z0-9_+\-]+(?:\.[A-Za-z0-9_+\-]+)+)(?![\w.])",
        value,
    ):
        literal = match.group("literal")
        segments = literal.split(".")
        has_identifier_shape = (
            any(character.isalpha() for character in segments[0])
            or len(segments) >= 3
            or any(segment.isalpha() for segment in segments[1:])
        )
        if not has_identifier_shape:
            continue  # 普通 `1.00`/`62.5ps` 测量值仍可按数值比较；版本号和文件名保留原貌。
        position = _semantic_token_position(value, match.start())
        signatures.append(f"{position}:{literal}")
    return signatures


def _path_identifier_signatures(value: str) -> list[str]:
    """Preserve case and components in POSIX, URL, Windows, and registry paths."""

    signatures: list[str] = []
    pattern = re.compile(
        r"(?<!\w)(?:[A-Za-z][A-Za-z0-9+.-]*://[^\s,;]+|/?[A-Za-z0-9_.+\-]+(?:/[A-Za-z0-9_.+\-]+)+)(?!\w)"
    )
    for match in pattern.finditer(value):
        literal = match.group(0).rstrip(".)]}。")
        position = _semantic_token_position(value, match.start())
        signatures.append(f"{position}:{literal}")
    windows_patterns = (
        re.compile(
            r"(?<!\w)[A-Za-z]:\\(?:[^\\\s,;]+\\)*[^\\\s,;]+"
        ),
        re.compile(
            r"(?<!\\)\\\\[^\\\s,;]+\\[^\\\s,;]+(?:\\[^\\\s,;]+)*"
        ),
        re.compile(
            r"(?i)\b(?:HKLM|HKCU|HKCR|HKU|HKCC|HKEY_LOCAL_MACHINE|"
            r"HKEY_CURRENT_USER|HKEY_CLASSES_ROOT|HKEY_USERS|"
            r"HKEY_CURRENT_CONFIG)(?:\\[^\\\s,;]+)+"
        ),
    )
    occupied_ranges = {
        (match.start(), match.end())
        for windows_pattern in windows_patterns
        for match in windows_pattern.finditer(value)
    }
    for start, end in sorted(occupied_ranges):
        literal = value[start:end].rstrip(".)]}。")
        position = _semantic_token_position(value, start)
        signatures.append(f"{position}:{literal}")
    for match in re.finditer(
        r"(?i)\b(?:file|path|url)\s+(?P<path>/[A-Za-z0-9_.+\-]+)(?![\w/])",
        value,
    ):
        literal = match.group("path").rstrip(".)]}。")
        position = _semantic_token_position(value, match.start("path"))
        signatures.append(f"{position}:{literal}")
    return signatures


def _semantic_token_position(value: str, character_index: int) -> int:
    """Return a whitespace-insensitive word/number occurrence position."""

    return sum(1 for _ in re.finditer(r"\w+", value[:character_index], flags=re.UNICODE))


def _punctuation_token_position(value: str, character_index: int) -> int:
    """Return a number-spelling-invariant position for structural punctuation."""

    prefix_tokens = [
        _canonical_review_token(token)
        for token in _REVIEW_TOKEN_RE.findall(value[:character_index])
    ]
    return len(
        canonicalize_number_word_tokens(
            prefix_tokens,
            protected_previous_words=_PROTECTED_NUMBER_WORD_PREFIXES,
            protected_next_words=_PROTECTED_NUMBER_WORD_SUFFIXES,
        )
    )


def _normalize_directional_symbols(value: str) -> str:
    """Canonicalize unambiguous arrow glyph variants while retaining direction."""

    normalized = re.sub(r"<\s*=\s*>", " ⇔ ", value)
    normalized = re.sub(r"<\s*-\s*>", " ↔ ", normalized)
    normalized = re.sub(r"-\s*>", " → ", normalized)
    normalized = re.sub(
        r"(?<=\s)<-(?=\s+(?:[^\W\d_]|_)\w*)",
        " ← ",
        normalized,
    )  # `< -5`/`<-5` 是“小于负数”的常见写法，没有充分证据时不能猜成左箭头。
    normalized = re.sub(r"=\s*>", " ⇒ ", normalized)
    normalized = normalized.replace("⟶", "→").replace("⟵", "←")
    normalized = normalized.replace("⇒", "⇒").replace("⇐", "⇐")
    return normalized


def _semantic_operator_signatures(value: str) -> list[str]:
    """Return canonical signatures for operators with explicit formula context."""

    canonical = {
        "+": "plus",
        "-": "minus",
        "−": "minus",
        "*": "multiply",
        "×": "multiply",
        "/": "divide",
        "÷": "divide",
        "\u2061": "function-application",
        "\u2062": "multiply",
        "\u2063": "separator",
        "\u2064": "plus",
    }
    signatures: list[str] = []
    for match in _SEMANTIC_OPERATOR_RE.finditer(value):
        operator = next(
            (group for group in match.groups() if group),
            match.group(0).strip(),
        )
        if (
            operator in {"+", "-", "−"}
            and match.start() >= 2
            and value[match.start() - 1] in {"e", "E"}
            and value[match.start() - 2].isdigit()
        ):
            continue  # 科学计数法指数符号属于数字 token，不是加减运算符。
        if operator in canonical:
            position = _semantic_token_position(value, match.start())
            signatures.append(f"{position}:{canonical[operator]}")
    return signatures


def _semantic_ampersand_signatures(value: str) -> list[str]:
    """Preserve ``&`` where an assignment makes operator intent explicit."""

    signatures: list[str] = []
    for match in re.finditer(r"&", value):
        line_start = value.rfind("\n", 0, match.start()) + 1
        prefix = value[line_start : match.start()]
        if "=" not in prefix and not re.search(r"(?i)\b(?:expression|formula)\b", prefix):
            continue  # 标题/自然语言里的 `A & B` 仍可与 `A and B` 视为展示等价。
        position = _semantic_token_position(value, match.start())
        signatures.append(f"{position}:ampersand")
    return signatures


def _normalize_embedded_number_list_spacing(value: str) -> str:
    """Repair OCR text such as ``Tests1,2`` before token comparison."""

    normalized = re.sub(
        r"(?i)\b(tests?|notes?)\s*(\d(?:\s*,\s*\d)+)",
        lambda match: (
            f"{match.group(1)} "
            + " ".join(re.findall(r"\d+", match.group(2)))
        ),
        value,
    )  # 明确由 Tests/Notes 引入的编号串只统一抽取空格；普通 `1,2` 仍保留逗号语义。
    return re.sub(
        r"(?i)\b(equation|figure|table|section)\s*(\d)",
        r"\1 \2",
        normalized,
    )  # 常见引用词后缺空格时只修正格式，不改变编号含义。


def _normalize_math_symbol_artifacts(value: str) -> str:
    """Normalize PDF variants of multiplication, exponents, hyphens, and spaces."""

    hexadecimal_literals: list[str] = []

    def protect_hexadecimal(match: re.Match[str]) -> str:
        hexadecimal_literals.append(match.group(0))
        return f"\ue000{len(hexadecimal_literals) - 1}\ue001"

    normalized = re.sub(
        r"(?i)(?<![\w.])0x[0-9a-f]+(?!\w)",
        protect_hexadecimal,
        value,
    )  # 0x10/0XCAFE 是协议地址或掩码，绝不能把 x 猜成乘号。
    normalized = normalized.replace("−", "-").replace("–", "-").replace("—", " - ")  # 统一数学负号和破折号形态。
    normalized = re.sub(
        r"(?i)(?<![a-z])([+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*(?:x|×|\*)\s*10\s*\^\s*([+-]?\s*\d+)",
        lambda match: (
            f"{match.group(1)}e"
            f"{match.group(2).replace(' ', '').removeprefix('+')}"
        ),
        normalized,
    )  # `5×10^6`/`5×10^-6` 有显式指数证据，可以安全归一。
    normalized = re.sub(
        r"(?<=\d)\s*(?:x|X|×|\*)\s*(?=\d)",
        " × ",
        normalized,
    )  # 数字两侧的 x/X/×/* 明确是乘法；只统一符号，不猜测指数。
    normalized = re.sub(
        r"(?i)(?<=\d)\s*(?:x|×|\*)\s*(?=[a-z_])",
        " × ",
        normalized,
    )  # 2xT_Vf 与 2×T_Vf 都变成 2 T_Vf，避免乘号格式噪声。
    normalized = re.sub(
        r"(?i)(?<=[a-z_])\s*(?:×|\*)\s*(?=[a-z0-9_])",
        " × ",
        normalized,
    )  # fb×n 与 fb*n 的乘号在 token 比较中等价。
    for index, literal in enumerate(hexadecimal_literals):
        normalized = normalized.replace(f"\ue000{index}\ue001", literal)
    return normalized


def _canonical_review_token(token: str) -> str:
    """Canonicalize token values only where protocol meaning is preserved."""

    if not _NUMBER_TOKEN_RE.fullmatch(token):
        return token
    sign = "-" if token.startswith("-") else "+" if token.startswith("+") else ""
    body = token.lstrip("+-").replace(",", "")
    if body.startswith("."):
        body = f"0{body}"
    try:
        value = Decimal(body)
    except InvalidOperation:
        return token
    # ``normalize()`` obeys Decimal's process-wide precision and can round two
    # long observed integers into the same key.  Fixed formatting preserves
    # the exact coefficient while still expanding an exponent spelling.
    numeric = format(value, "f")
    if "." in numeric:
        numeric = numeric.rstrip("0").rstrip(".")
    return f"{sign}{numeric}"


def _report_unit(value: str) -> str:
    """Return a human-readable snippet without odd mid-sentence ellipses."""

    if _is_table_review_unit(value):
        return _format_fallback_table_review_unit(value)  # 未被视觉摘要覆盖的表格行用用户可读前缀展示。
    value = _normalize_extracted_arrow_spacing(value)  # 修复 `- >` 这类 PDF 抽取箭头断裂，提升报告可读性。
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


def _normalize_extracted_arrow_spacing(value: str) -> str:
    """Repair arrow spacing artifacts in user-facing snippets."""

    repaired = re.sub(r"\s*-\s*>\s*", "->", value)  # 把 `- >`、` -> ` 统一成紧凑箭头。
    return repaired  # 保留 PDF 原有 Unicode 箭头，只修复被拆开的 ASCII 箭头。


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
    is compact but unsafe: it can align two unrelated technical changes.
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

    for old_index, text, source_units in _coherent_delta_unit_groups(
        [
            (old_index, old_unit)
            for old_index, old_unit in enumerate(old_units)
            if old_index not in matched_old
        ]
    ):
        events.append(
            (
                old_index,
                1,
                event_sequence,
                _PendingDeltaCandidate(
                    kind="removed",
                    text=text,
                    priority_values=source_units,
                ),
            )
        )
        event_sequence += 1
    for new_index, text, source_units in _coherent_delta_unit_groups(
        [
            (new_index, new_unit)
            for new_index, new_unit in enumerate(new_units)
            if new_index not in matched_new
        ]
    ):
        events.append(
            (
                new_index,
                2,
                event_sequence,
                _PendingDeltaCandidate(
                    kind="added",
                    text=text,
                    priority_values=source_units,
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
    for cell in _raw_table_row_cells(value):
        field = split_table_field(cell)
        if field is None:
            continue
        key, raw_value = field
        normalized_key = normalize_for_similarity(key).strip()
        normalized_value = normalize_line(raw_value)
        if normalized_key and normalized_value:
            fields[normalized_key] = normalized_value
    return fields


def _table_row_cells(value: str) -> list[str]:
    """Return decoded, human-readable cells from one structured row."""

    cells: list[str] = []
    for cell in _raw_table_row_cells(value):
        field = split_table_field(cell)
        if field is None:
            cells.append(decode_table_cell(cell))
        else:
            cells.append(f"{field[0]}={field[1]}")
    return cells


def _raw_table_row_cells(value: str) -> list[str]:
    """Split only structural `` | `` separators, retaining escaped contents."""

    text = normalize_line(value)  # 统一空白后再切分，减少 PDF 抽取空格差异。
    text = _TABLE_REVIEW_PREFIX_RE.sub("", text).strip()  # 删除内部或可见表格前缀，只保留列内容。
    cells = [cell for cell in split_table_cells(text) if cell]
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
) -> tuple[
    list[str],
    list[str],
    list[SnippetPair],
    int,
    list[str],
    list[str],
    list[SnippetPair],
]:
    """Apply snippet limits after all meaningful differences have been scanned."""

    unique_candidates = _dedupe_candidates(candidates)  # 没有布局证据时不根据文本形状删除任何差异。
    audit_added, audit_removed, audit_replaced = _candidate_payloads(unique_candidates)
    if max_snippets <= 0:
        return (
            [],
            [],
            [],
            len(unique_candidates),
            audit_added,
            audit_removed,
            audit_replaced,
        )

    if len(unique_candidates) <= max_snippets:
        selected = unique_candidates
        omitted_count = 0
    else:
        prioritized = sorted(unique_candidates, key=lambda item: (-item.priority, item.order))
        selected = sorted(prioritized[:max_snippets], key=lambda item: item.order)
        omitted_count = len(unique_candidates) - len(selected)

    added, removed, replaced = _candidate_payloads(selected)
    return (
        added,
        removed,
        replaced,
        omitted_count,
        audit_added,
        audit_removed,
        audit_replaced,
    )


def _candidate_payloads(
    candidates: list[_DeltaCandidate],
) -> tuple[list[str], list[str], list[SnippetPair]]:
    """Split ordered candidates into the three public snippet collections."""

    added: list[str] = []
    removed: list[str] = []
    replaced: list[SnippetPair] = []
    for candidate in candidates:
        if candidate.kind == "added":
            added.append(candidate.text)
        elif candidate.kind == "removed":
            removed.append(candidate.text)
        elif candidate.kind == "replaced" and candidate.pair:
            replaced.append(candidate.pair)
    return added, removed, replaced


def _dedupe_candidates(candidates: list[_DeltaCandidate]) -> list[_DeltaCandidate]:
    """Preserve every source occurrence in the auditable comparison model."""

    # Equal text at two positions is still two document changes.  Reader-only
    # rendering may collapse redundant cards, but JSON/CSV and snippet limits
    # must count each observed occurrence instead of silently under-reporting it.
    return sorted(candidates, key=lambda item: item.order)


def _standalone_table_reference_is_covered(
    candidate: _DeltaCandidate,
    candidates: list[_DeltaCandidate],
) -> bool:
    """Hide a context-free table number already explained by a full sentence pair."""

    if candidate.kind != "replaced" or candidate.pair is None:
        return False
    old_reference = _standalone_table_reference_number(candidate.pair.old)
    new_reference = _standalone_table_reference_number(candidate.pair.new)
    if not old_reference or not new_reference:
        return False
    for other in candidates:
        if other is candidate or other.kind != "replaced" or other.pair is None:
            continue
        if (
            _text_contains_table_reference(other.pair.old, old_reference)
            and _text_contains_table_reference(other.pair.new, new_reference)
            and len(compact_inline(other.pair.old)) > len(compact_inline(candidate.pair.old))
            and len(compact_inline(other.pair.new)) > len(compact_inline(candidate.pair.new))
        ):
            return True
    return False


def _standalone_table_reference_number(value: str) -> str:
    """Return a normalized table number only when the whole unit is that reference."""

    candidate = compact_inline(value)
    match = _STRICT_TABLE_REFERENCE_RE.match(candidate)
    if match is None:
        return ""
    if not re.fullmatch(r"\s*[.:]?", candidate[match.end() :]):
        return ""
    return _normalized_table_reference_number(match.group("number"))


def _text_contains_table_reference(value: str, number: str) -> bool:
    """Return True when a longer unit cites the exact table number."""

    target = _normalized_table_reference_number(number)
    references = {
        _normalized_table_reference_number(match.group("number"))
        for match in _STRICT_TABLE_REFERENCE_RE.finditer(compact_inline(value))
    }
    return target in references  # 不支持的字母、点号或更深复合编号整体失败关闭，绝不截成短号。


def _suppress_global_noise_changes(changes: list[SectionChange]) -> tuple[list[SectionChange], int]:
    """Keep all deltas; layout noise must be removed only where layout proves it."""

    return changes, 0


def _is_global_noise_snippet(value: str) -> bool:
    """Compatibility hook: text alone never proves that a visible fact is noise."""

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


def _candidate_key(candidate: _DeltaCandidate) -> tuple[str, str]:
    """Build a stable dedupe key for one candidate."""

    if candidate.pair:
        return (candidate.kind, f"{candidate.pair.old}\n---\n{candidate.pair.new}")
    return (candidate.kind, candidate.text)


def _substantive_priority(*values: str) -> int:
    """Rank protocol-risky deltas above ordinary wording changes."""

    joined = "\n".join(values)
    if _has_numeric_token(joined):
        return 4
    if _has_identifier_token(joined):
        return 3
    if _has_protocol_sentence_verb(joined):
        return 2  # 片段上限优先展示数值/标识符，再展示普通完整句；所有候选仍计入省略数。
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

    if re.fullmatch(
        rf"(?i)table\s+\d+(?:\s*{TABLE_NUMBER_DASH_CLASS}\s*\d+)?\s*[.:]\s*\S.+",
        compact_inline(line),
    ):
        return [compact_inline(line)]  # 独立完整表题必须作为一个可读/可审计单位，不能拆成 `Table N.` + 孤立标题。

    units: list[str] = []
    current: list[str] = []
    hard_endings = set("。！？；;!?")

    for index, char in enumerate(line):
        current.append(char)
        should_split = False
        if char in {"―", "—"} and re.match(r"\s*If\b", line[index + 1 :], flags=re.I):
            current.pop()  # 长破折号只是把列表说明粘在同一行，拆句时不放进上一条。
            should_split = True
        elif char in hard_endings:
            should_split = True
        elif char == ".":
            previous_char = line[index - 1] if index > 0 else ""
            next_char = line[index + 1] if index + 1 < len(line) else ""
            previous_text = line[:index].strip()
            if previous_char.isdigit() and next_char.isdigit():
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

def _first_units(
    text: str,
    max_snippets: int,
    *,
    suppressed_table_unit_keys: set[str] | None = None,
) -> tuple[list[str], int, list[str]]:
    """Return leading snippets for added or deleted whole sections."""

    table_unit_keys = suppressed_table_unit_keys or set()  # 整章新增/删除也只隐藏有视觉摘要覆盖的表格行。
    review_units = _paragraph_review_units(text, suppressed_table_unit_keys=table_unit_keys)  # 未覆盖表格行继续作为文字兜底。
    grouped_units = _coherent_delta_unit_groups(list(enumerate(review_units)))
    units = [text for _index, text, _source_units in grouped_units]
    if max_snippets <= 0:
        return [], len(units), units
    return units[:max_snippets], max(0, len(units) - max_snippets), units


def _change_sort_key(change: SectionChange) -> tuple[int, int, str]:
    """Sort by new-page location first, then deleted old-page location."""

    section = change.new_section or change.old_section
    page = section.start_page if section else 0
    type_order = {"modified": 0, "added": 1, "deleted": 2, "unchanged": 3}
    return (page, type_order.get(change.change_type, 9), change.report_location)
