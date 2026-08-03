"""Conservative reliability assessment for extracted protocol documents.

This layer only evaluates facts produced by extraction and sectioning.  It
never removes or rewrites pages, sections, warnings, or comparison findings.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
import hashlib
import os
from pathlib import Path
import re

from . import __version__
from .models import DiffOptions, ExtractionResult, PageParserRoute, Section
from .page_ocr import (
    OCR_MAXIMUM_RENDER_PIXELS,
    OCR_MINIMUM_IMAGE_COVERAGE,
    OCR_MINIMUM_NATIVE_TEXT_VERTICAL_BAND_COVERAGE,
    OCR_MINIMUM_RESULT_CHARACTERS,
    OCR_NATIVE_CHARACTER_LIMIT,
    OCR_NATIVE_TEXT_VERTICAL_BAND_COUNT,
    OCR_PAGE_TIMEOUT_SECONDS,
    OCR_RENDER_RESOLUTION,
)
from .sectioning import canonical_number_identity
from .text_utils import normalize_line


SUPPORTED_PROFILE = (
    "原生可选文本、以线性阅读顺序为主、具有稳定编号章节的协议或规范 PDF"
)
MIN_RELIABLE_CHARACTERS = 500
MAX_RELIABLE_EMPTY_PAGE_RATIO = 0.20
MIN_PAGES_FOR_DENSITY_CHECK = 3
MIN_PAGES_FOR_STRUCTURE_COVERAGE_CHECK = 3
MIN_STABLE_SECTIONS_FOR_FULL_DOCUMENT = 2
MAX_STRUCTURE_EXEMPT_PARTIAL_WINDOW_RATIO = 0.50
MIN_AVERAGE_TECHNICAL_CHARACTERS_PER_PAGE = 200
MIN_LINES_FOR_FRAGMENTATION_CHECK = 40
MAX_AVERAGE_CHARACTERS_PER_FRAGMENTED_LINE = 4.0
MIN_SINGLE_CHARACTER_LINE_RATIO = 0.50


class ReliabilityState(str, Enum):
    """String-backed states used by reports and machine-readable output."""

    RELIABLE = "reliable"
    DEGRADED = "degraded"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class DocumentQualityMetrics:
    """Observable extraction/sectioning facts for one selected document window."""

    selected_page_count: int
    non_empty_page_count: int
    character_count: int
    stable_section_count: int
    fallback_section_count: int
    extraction_warnings: tuple[str, ...]
    layout_risk_pages: tuple[int, ...]
    empty_page_ratio: float
    average_characters_per_non_empty_page: float = 0.0
    technical_layout_risk_pages: tuple[int, ...] = ()
    fragmented_text_pages: tuple[int, ...] = ()
    technical_character_count: int = 0
    technical_page_count: int = 0
    average_technical_characters_per_page: float = 0.0
    duplicate_number_path_count: int = 0
    unstructured_technical_section_count: int = 0
    uncomparable_table_visual_count: int = 0
    ambiguous_table_context_page_count: int = 0
    page_window_consistent: bool = True
    explicit_partial_window: bool = False
    limited_partial_window: bool = False
    ocr_pages: tuple[int, ...] = ()
    image_dominant_pages: tuple[int, ...] = ()  # 图像主导事实独立于 layout_risk，支持替代后端直接交付页面证据。
    parser_route_pages: tuple[tuple[str, tuple[int, ...]], ...] = ()  # 页面路由按稳定枚举顺序分组，供 JSON 审计而非比较逻辑使用。


@dataclass(frozen=True)
class PairAssessment:
    """Reliability judgment for an old/new comparison pair."""

    state: ReliabilityState
    headline: str
    reasons: tuple[str, ...]
    supported_profile: str
    allows_no_difference_conclusion: bool
    old_document: DocumentQualityMetrics
    new_document: DocumentQualityMetrics


@dataclass(frozen=True)
class InputProvenance:
    """Reproducibility facts for one source and its selected page window."""

    path: Path
    sha256: str | None
    selected_start_page: int | None
    selected_end_page: int | None


@dataclass(frozen=True)
class EffectiveThresholds:
    """Values that materially control comparison and quality decisions."""

    min_section_match_similarity: float
    max_snippets_per_section: int
    min_reliable_characters: int
    max_reliable_empty_page_ratio: float
    min_pages_for_density_check: int = MIN_PAGES_FOR_DENSITY_CHECK
    min_average_technical_characters_per_page: int = (
        MIN_AVERAGE_TECHNICAL_CHARACTERS_PER_PAGE
    )
    min_pages_for_structure_coverage_check: int = (
        MIN_PAGES_FOR_STRUCTURE_COVERAGE_CHECK
    )
    min_stable_sections_for_full_document: int = (
        MIN_STABLE_SECTIONS_FOR_FULL_DOCUMENT
    )
    max_structure_exempt_partial_window_ratio: float = (
        MAX_STRUCTURE_EXEMPT_PARTIAL_WINDOW_RATIO
    )
    min_lines_for_fragmentation_check: int = MIN_LINES_FOR_FRAGMENTATION_CHECK
    max_average_characters_per_fragmented_line: float = (
        MAX_AVERAGE_CHARACTERS_PER_FRAGMENTED_LINE
    )
    min_single_character_line_ratio: float = MIN_SINGLE_CHARACTER_LINE_RATIO
    ocr_language: str | None = None
    layout_backend: str = "native"
    ocr_native_character_limit: int = OCR_NATIVE_CHARACTER_LIMIT
    ocr_minimum_image_coverage: float = OCR_MINIMUM_IMAGE_COVERAGE
    ocr_native_text_vertical_band_count: int = OCR_NATIVE_TEXT_VERTICAL_BAND_COUNT
    ocr_minimum_native_text_vertical_band_coverage: float = (
        OCR_MINIMUM_NATIVE_TEXT_VERTICAL_BAND_COVERAGE
    )
    ocr_minimum_result_characters: int = OCR_MINIMUM_RESULT_CHARACTERS
    ocr_render_resolution: int = OCR_RENDER_RESOLUTION
    ocr_page_timeout_seconds: int = OCR_PAGE_TIMEOUT_SECONDS
    ocr_maximum_render_pixels: int = OCR_MAXIMUM_RENDER_PIXELS


@dataclass(frozen=True)
class DiffProvenance:
    """Machine-readable facts needed to reproduce one comparison."""

    package_version: str
    build_commit: str | None
    supported_profile: str
    old_input: InputProvenance
    new_input: InputProvenance
    effective_thresholds: EffectiveThresholds


def assess_pair(
    old_extraction: ExtractionResult,
    new_extraction: ExtractionResult,
    old_sections: list[Section],
    new_sections: list[Section],
) -> PairAssessment:
    """Assess a pair without changing any extracted or compared facts."""

    old_metrics = _document_metrics(old_extraction, old_sections)
    new_metrics = _document_metrics(new_extraction, new_sections)
    documents = (("旧协议", old_metrics), ("新协议", new_metrics))

    indeterminate_reasons: list[str] = []
    for label, metrics in documents:
        if metrics.character_count == 0:
            indeterminate_reasons.append(f"{label}没有可比较文字。")
        if (
            metrics.stable_section_count
            + metrics.fallback_section_count
            + metrics.unstructured_technical_section_count
            == 0
        ):
            indeterminate_reasons.append(f"{label}没有可比较章节。")
    if indeterminate_reasons:
        return PairAssessment(
            state=ReliabilityState.INDETERMINATE,
            headline="无法判断：缺少可比较文字或章节",
            reasons=tuple(indeterminate_reasons),
            supported_profile=SUPPORTED_PROFILE,
            allows_no_difference_conclusion=False,
            old_document=old_metrics,
            new_document=new_metrics,
        )

    degraded_reasons: list[str] = []
    for label, metrics in documents:
        if metrics.technical_character_count < MIN_RELIABLE_CHARACTERS:
            degraded_reasons.append(
                f"{label}只有 {metrics.technical_character_count} 个清洗后技术正文字符，"
                f"低于可靠阈值 {MIN_RELIABLE_CHARACTERS}。"
            )
        if metrics.extraction_warnings:
            degraded_reasons.append(f"{label}有 {len(metrics.extraction_warnings)} 条抽取警告。")
        if metrics.fallback_section_count:
            degraded_reasons.append(
                f"{label}有 {metrics.fallback_section_count} 个按页回退文本块，未识别到稳定章节。"
            )
        if metrics.unstructured_technical_section_count:
            degraded_reasons.append(
                f"{label}有 {metrics.unstructured_technical_section_count} 个无编号结构身份的技术文本段；"
                "内容仍参与比较，但不能据此证明章节识别可靠。"
            )
        if metrics.uncomparable_table_visual_count:
            degraded_reasons.append(
                f"{label}有 {metrics.uncomparable_table_visual_count} 个仅有截图、没有结构化可比较行的表格区域；"
                "本工具不做图像像素差异，不能据此得出无差异结论。"
            )
        if metrics.ambiguous_table_context_page_count:
            degraded_reasons.append(
                f"{label}有 {metrics.ambiguous_table_context_page_count} 页同时包含多个技术章节和多个无稳定编号表格；"
                "缺少章节纵向坐标，不能可靠证明表格归属。"
            )
        if not metrics.page_window_consistent:
            degraded_reasons.append(
                f"{label}实际抽取页序列与声明的页码范围不一致；"
                "缺页、重复页、乱序或越界都会使完整性结论失效。"
            )
        if (
            metrics.selected_page_count >= MIN_PAGES_FOR_STRUCTURE_COVERAGE_CHECK
            and 0 < metrics.stable_section_count < MIN_STABLE_SECTIONS_FOR_FULL_DOCUMENT
            and not metrics.limited_partial_window
        ):
            degraded_reasons.append(
                f"{label}完整选择了 {metrics.selected_page_count} 页，但只识别到 "
                f"{metrics.stable_section_count} 个稳定技术章节；"
                "单个偶然编号不足以证明整份多页文档结构识别可靠。"
            )
        if metrics.empty_page_ratio > MAX_RELIABLE_EMPTY_PAGE_RATIO:
            degraded_reasons.append(
                f"{label}空白页比例为 {metrics.empty_page_ratio:.0%}，"
                f"高于可靠阈值 {MAX_RELIABLE_EMPTY_PAGE_RATIO:.0%}。"
            )
        if (
            metrics.technical_page_count >= MIN_PAGES_FOR_DENSITY_CHECK
            and metrics.average_technical_characters_per_page
            < MIN_AVERAGE_TECHNICAL_CHARACTERS_PER_PAGE
        ):
            degraded_reasons.append(
                f"{label}平均每个技术正文页只有 "
                f"{metrics.average_technical_characters_per_page:.0f} 个清洗后字符，"
                f"低于可靠阈值 {MIN_AVERAGE_TECHNICAL_CHARACTERS_PER_PAGE}。"
            )
        if metrics.technical_layout_risk_pages:
            pages = "、".join(str(page) for page in metrics.technical_layout_risk_pages)
            degraded_reasons.append(f"{label}存在非线性阅读顺序风险页：{pages}。")
        if metrics.ocr_pages:
            pages = "、".join(str(page) for page in metrics.ocr_pages)
            degraded_reasons.append(
                f"{label}第 {pages} 页使用了整页 OCR；文字可用于定位差异，"
                "但不能据此自动确认两份 PDF 一致。"
            )
        if metrics.image_dominant_pages:
            pages = "、".join(str(page) for page in metrics.image_dominant_pages)
            degraded_reasons.append(
                f"{label}第 {pages} 页以大面积栅格图像为主；"
                "即使存在可搜索文字层，也不能据此自动确认两份 PDF 一致。"
            )
        if metrics.fragmented_text_pages:
            pages = "、".join(str(page) for page in metrics.fragmented_text_pages)
            degraded_reasons.append(
                f"{label}存在文字被拆成单字/短碎片的页面：{pages}；"
                "这类幻灯片或异常字体抽取结果不能视为连续正文。"
            )
        if metrics.duplicate_number_path_count:
            degraded_reasons.append(
                f"{label}有 {metrics.duplicate_number_path_count} 个重复章节编号路径；"
                "可能包含多个子文档或编号重启，章节配对需要人工复核。"
            )
    if degraded_reasons:
        return PairAssessment(
            state=ReliabilityState.DEGRADED,
            headline="需人工复核：识别结果存在可靠性风险",
            reasons=tuple(degraded_reasons),
            supported_profile=SUPPORTED_PROFILE,
            allows_no_difference_conclusion=False,
            old_document=old_metrics,
            new_document=new_metrics,
        )

    # One stable section is sufficient: an explicitly selected page window may
    # intentionally isolate a single numbered protocol clause.  Character
    # volume and every extraction/layout risk still have to pass the gates above.
    return PairAssessment(
        state=ReliabilityState.RELIABLE,
        headline="可靠：两份文档均符合当前支持范围",
        reasons=("文字量、稳定章节和抽取信号均通过可靠性检查。",),
        supported_profile=SUPPORTED_PROFILE,
        allows_no_difference_conclusion=True,
        old_document=old_metrics,
        new_document=new_metrics,
    )


def build_provenance(
    old_extraction: ExtractionResult,
    new_extraction: ExtractionResult,
    options: DiffOptions,
) -> DiffProvenance:
    """Capture reproducibility metadata without invoking git or another process."""

    return DiffProvenance(
        package_version=__version__,
        build_commit=os.environ.get("PROTOCOL_PDF_DIFF_BUILD_COMMIT") or None,
        supported_profile=SUPPORTED_PROFILE,
        old_input=_input_provenance(old_extraction),
        new_input=_input_provenance(new_extraction),
        effective_thresholds=EffectiveThresholds(
            min_section_match_similarity=options.min_section_match_similarity,
            max_snippets_per_section=options.max_snippets_per_section,
            layout_backend=options.layout_backend,
            min_reliable_characters=MIN_RELIABLE_CHARACTERS,
            max_reliable_empty_page_ratio=MAX_RELIABLE_EMPTY_PAGE_RATIO,
            min_pages_for_density_check=MIN_PAGES_FOR_DENSITY_CHECK,
            min_average_technical_characters_per_page=(
                MIN_AVERAGE_TECHNICAL_CHARACTERS_PER_PAGE
            ),
            min_pages_for_structure_coverage_check=(
                MIN_PAGES_FOR_STRUCTURE_COVERAGE_CHECK
            ),
            min_stable_sections_for_full_document=(
                MIN_STABLE_SECTIONS_FOR_FULL_DOCUMENT
            ),
            max_structure_exempt_partial_window_ratio=(
                MAX_STRUCTURE_EXEMPT_PARTIAL_WINDOW_RATIO
            ),
            min_lines_for_fragmentation_check=MIN_LINES_FOR_FRAGMENTATION_CHECK,
            max_average_characters_per_fragmented_line=(
                MAX_AVERAGE_CHARACTERS_PER_FRAGMENTED_LINE
            ),
            min_single_character_line_ratio=MIN_SINGLE_CHARACTER_LINE_RATIO,
            ocr_language=options.ocr_language,
            ocr_native_character_limit=OCR_NATIVE_CHARACTER_LIMIT,
            ocr_minimum_image_coverage=OCR_MINIMUM_IMAGE_COVERAGE,
            ocr_native_text_vertical_band_count=OCR_NATIVE_TEXT_VERTICAL_BAND_COUNT,
            ocr_minimum_native_text_vertical_band_coverage=(
                OCR_MINIMUM_NATIVE_TEXT_VERTICAL_BAND_COVERAGE
            ),
            ocr_minimum_result_characters=OCR_MINIMUM_RESULT_CHARACTERS,
            ocr_render_resolution=OCR_RENDER_RESOLUTION,
            ocr_page_timeout_seconds=OCR_PAGE_TIMEOUT_SECONDS,
            ocr_maximum_render_pixels=OCR_MAXIMUM_RENDER_PIXELS,
        ),
    )


def _document_metrics(
    extraction: ExtractionResult,
    sections: list[Section],
) -> DocumentQualityMetrics:
    """Collect quality facts while preserving the extraction unchanged."""

    selected_page_count = len(extraction.pages)
    non_empty_page_count = sum(bool(page.text.strip()) for page in extraction.pages)
    character_count = sum(len(page.text.strip()) for page in extraction.pages)
    fallback_section_count = sum(section.section_id.startswith("P") for section in sections)
    stable_section_count = sum(
        not section.section_id.startswith("P")
        and section.role == "technical"
        and bool(section.number_path)
        for section in sections
    )
    unstructured_technical_section_count = sum(
        not section.section_id.startswith("P")
        and section.role == "technical"
        and not section.number_path
        for section in sections
    )
    uncomparable_table_visual_count = sum(
        not any(row.strip() for row in table.row_texts)
        for table in extraction.table_visuals
    )
    ambiguous_table_context_page_count = _ambiguous_table_context_page_count(
        extraction,
        sections,
    )
    page_window_consistent = _page_window_is_consistent(extraction)
    layout_risk_pages = tuple(
        page.page_number for page in extraction.pages if page.layout_risk
    )
    technical_sections = [section for section in sections if section.role == "technical"]
    observed_page_numbers = {page.page_number for page in extraction.pages}
    technical_pages = {
        page_number
        for page_number in observed_page_numbers
        if any(
            section.start_page <= page_number <= section.end_page
            for section in technical_sections
        )
    }  # 只统计实际抽取到的页，禁止展开不可信的巨大章节范围。
    raw_technical_character_count = sum(
        len(f"{section.heading}\n{section.body}".strip())
        for section in technical_sections
    )
    repeated_edge_character_count = _repeated_edge_characters_in_sections(
        extraction,
        technical_sections,
    )
    technical_character_count = max(
        0,
        raw_technical_character_count - repeated_edge_character_count,
    )  # 重复边缘文字仍参与 diff，但不能重复灌水把低信息文档抬成 reliable。
    technical_page_count = len(technical_pages)
    average_technical_characters_per_page = (
        technical_character_count / technical_page_count
        if technical_page_count
        else 0.0
    )
    normalized_number_paths = [
        tuple(canonical_number_identity(number) for number in section.number_path)
        for section in technical_sections
        if section.number_path
    ]
    duplicate_number_path_count = sum(
        count - 1 for count in Counter(normalized_number_paths).values() if count > 1
    )
    table_visual_pages = {table.page_number for table in extraction.table_visuals}
    technical_layout_risk_pages = tuple(
        page_number
        for page_number in layout_risk_pages
        if (
            not technical_pages
            or page_number in technical_pages
            or page_number in table_visual_pages
        )
    )
    empty_page_ratio = (
        (selected_page_count - non_empty_page_count) / selected_page_count
        if selected_page_count
        else 1.0
    )
    average_characters_per_non_empty_page = (
        character_count / non_empty_page_count if non_empty_page_count else 0.0
    )
    fragmented_text_pages = tuple(
        page.page_number
        for page in extraction.pages
        if _has_extreme_line_fragmentation(page.text)
    )
    ocr_pages = tuple(page.page_number for page in extraction.pages if page.ocr_used)
    image_dominant_pages = tuple(
        page.page_number
        for page in extraction.pages
        if page.image_dominant
    )  # 直接消费页面事实，禁止仅依赖 pdf_extract 把图像页并入 layout_risk 的实现细节。
    parser_route_pages = tuple(
        (
            route.value,
            tuple(
                page.page_number
                for page in extraction.pages
                if page.parser_route == route
            ),
        )
        for route in PageParserRoute
        if any(page.parser_route == route for page in extraction.pages)
    )  # 枚举顺序稳定地按路由聚合所有选中页；每个 PageText 只能进入自身的一个互斥分组。
    return DocumentQualityMetrics(
        selected_page_count=selected_page_count,
        non_empty_page_count=non_empty_page_count,
        character_count=character_count,
        stable_section_count=stable_section_count,
        fallback_section_count=fallback_section_count,
        extraction_warnings=tuple(extraction.warnings),
        layout_risk_pages=layout_risk_pages,
        empty_page_ratio=empty_page_ratio,
        average_characters_per_non_empty_page=average_characters_per_non_empty_page,
        technical_layout_risk_pages=technical_layout_risk_pages,
        fragmented_text_pages=fragmented_text_pages,
        technical_character_count=technical_character_count,
        technical_page_count=technical_page_count,
        average_technical_characters_per_page=average_technical_characters_per_page,
        duplicate_number_path_count=duplicate_number_path_count,
        unstructured_technical_section_count=unstructured_technical_section_count,
        uncomparable_table_visual_count=uncomparable_table_visual_count,
        ambiguous_table_context_page_count=ambiguous_table_context_page_count,
        page_window_consistent=page_window_consistent,
        explicit_partial_window=_is_explicit_partial_window(extraction),
        limited_partial_window=_is_limited_partial_window(extraction),
        ocr_pages=ocr_pages,
        image_dominant_pages=image_dominant_pages,
        parser_route_pages=parser_route_pages,
    )


def _ambiguous_table_context_page_count(
    extraction: ExtractionResult,
    sections: list[Section],
) -> int:
    """Count pages where page-only context cannot assign unnumbered tables."""

    technical_sections = [section for section in sections if section.role == "technical"]
    ambiguous_pages = 0
    for page in extraction.pages:
        page_sections = [
            section
            for section in technical_sections
            if section.start_page <= page.page_number <= section.end_page
        ]
        if len(page_sections) < 2:
            continue
        unnumbered_tables = [
            table
            for table in extraction.table_visuals
            if table.page_number == page.page_number
            and not re.search(
                r"(?i)\btable\s+(?:[A-Z]+[-.]?)?\d+(?:[-.]\d+)*\b|表\s*\d+",
                normalize_line(table.title),
            )
        ]
        if len(unnumbered_tables) >= 2:
            ambiguous_pages += 1
    return ambiguous_pages


def _page_window_is_consistent(extraction: ExtractionResult) -> bool:
    """Validate extracted page order against inclusive selection metadata."""

    page_numbers = [page.page_number for page in extraction.pages]
    if not page_numbers:
        return True  # 空提取会由 indeterminate 门禁单独处理。
    if page_numbers[0] < 1:
        return False  # PageText 使用 one-based 页码，0/负数不能进入可靠结论。
    if any(
        current != previous + 1
        for previous, current in zip(page_numbers, page_numbers[1:], strict=False)
    ):
        return False  # 同时覆盖重复、乱序和中间断页。

    start_page = extraction.selected_start_page
    end_page = extraction.selected_end_page
    if start_page is not None and page_numbers[0] != start_page:
        return False
    if end_page is not None and page_numbers[-1] != end_page:
        return False
    if start_page is not None and end_page is not None:
        if start_page < 1 or end_page < start_page:
            return False
        if len(page_numbers) != end_page - start_page + 1:
            return False
    if extraction.total_pages:
        if extraction.total_pages < 1 or page_numbers[-1] > extraction.total_pages:
            return False
        if end_page is not None and end_page > extraction.total_pages:
            return False
    return True


def _repeated_edge_characters_in_sections(
    extraction: ExtractionResult,
    technical_sections: list[Section],
) -> int:
    """Count exact repeated page-edge text for confidence-volume discounting only."""

    if len(extraction.pages) < 3 or not technical_sections:
        return 0
    page_presence: Counter[str] = Counter()
    for page in extraction.pages:
        lines = [normalize_line(line) for line in page.text.splitlines()]
        lines = [line for line in lines if line]
        edge_lines = set(lines[:2]) | set(lines[-2:])
        page_presence.update(edge_lines)
    required_pages = max(3, (len(extraction.pages) * 3 + 3) // 4)
    repeated = {
        line for line, page_count in page_presence.items() if page_count >= required_pages
    }
    if not repeated:
        return 0
    technical_line_counts: Counter[str] = Counter()
    for section in technical_sections:
        technical_line_counts.update(
            line
            for raw_line in f"{section.heading}\n{section.body}".splitlines()
            if (line := normalize_line(raw_line))
        )
    return sum(
        len(line) * min(technical_line_counts[line], page_presence[line])
        for line in repeated
    )


def _is_explicit_partial_window(extraction: ExtractionResult) -> bool:
    """Return True only when selection metadata proves that pages were omitted."""

    start_page = extraction.selected_start_page
    end_page = extraction.selected_end_page
    if start_page is None or end_page is None:
        return False
    if start_page > 1:
        return True
    return extraction.total_pages > 0 and end_page < extraction.total_pages


def _is_limited_partial_window(extraction: ExtractionResult) -> bool:
    """Return True when an explicit selection covers at most half of the PDF."""

    if not _is_explicit_partial_window(extraction) or extraction.total_pages <= 0:
        return False
    selected_page_count = len(extraction.pages)
    return (
        selected_page_count / extraction.total_pages
        <= MAX_STRUCTURE_EXEMPT_PARTIAL_WINDOW_RATIO
    )


def _has_extreme_line_fragmentation(text: str) -> bool:
    """Detect extraction that emits hundreds of isolated glyph-sized lines."""

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < MIN_LINES_FOR_FRAGMENTATION_CHECK:
        return False
    average_length = sum(len(line) for line in lines) / len(lines)
    single_character_ratio = sum(len(line) == 1 for line in lines) / len(lines)
    very_short_line_ratio = sum(len(line) <= 4 for line in lines) / len(lines)
    return (
        average_length <= MAX_AVERAGE_CHARACTERS_PER_FRAGMENTED_LINE
        and (
            single_character_ratio >= MIN_SINGLE_CHARACTER_LINE_RATIO
            or very_short_line_ratio >= 0.80
        )
    )


def _input_provenance(extraction: ExtractionResult) -> InputProvenance:
    """Build one source record; absent synthetic paths intentionally hash to null."""

    start_page = extraction.selected_start_page
    end_page = extraction.selected_end_page
    if extraction.pages:
        start_page = start_page if start_page is not None else min(page.page_number for page in extraction.pages)
        end_page = end_page if end_page is not None else max(page.page_number for page in extraction.pages)
    return InputProvenance(
        path=extraction.pdf_path,
        sha256=_sha256_if_file(extraction.pdf_path),
        selected_start_page=start_page,
        selected_end_page=end_page,
    )


def _sha256_if_file(path: Path) -> str | None:
    """Hash an existing regular file using bounded reads; synthetic paths return null."""

    try:
        if not path.is_file():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None
