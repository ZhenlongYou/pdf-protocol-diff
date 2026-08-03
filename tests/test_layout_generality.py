"""Generality regressions for PDF reading-order capability checks."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from protocol_pdf_diff.compare import compare_extractions
from protocol_pdf_diff.models import DiffOptions, ExtractionResult, PageText, TableVisual
from protocol_pdf_diff.pdf_extract import _content_x_bounds_without_line_gutters, extract_pdf_text


_TECHNICAL_LINE = (
    "The receiver shall preserve every normative voltage timing calibration "
    "requirement for all supported operating modes."
)


class _CoordinatePage:
    """Small pdfplumber-compatible page for public extraction-path tests."""

    width = 600
    height = 800
    lines: list[object] = []
    rects: list[object] = []

    def __init__(self, words: list[dict[str, object]], text: str) -> None:
        self._words = words
        self._text = text

    def crop(self, _bbox: object) -> "_CoordinatePage":
        return self

    def filter(self, _predicate: object) -> "_CoordinatePage":
        return self

    def extract_words(self, **_kwargs: object) -> list[dict[str, object]]:
        return self._words

    def extract_text(self, **_kwargs: object) -> str:
        return self._text


class _OnePagePdf:
    def __init__(self, page: _CoordinatePage) -> None:
        self.pages = [page]

    def close(self) -> None:
        pass


class _PageSequencePdf:
    def __init__(self, pages: list[_CoordinatePage]) -> None:
        self.pages = pages

    def close(self) -> None:
        pass


class _BrokenCoordinatePage(_CoordinatePage):
    def extract_words(self, **_kwargs: object) -> list[dict[str, object]]:
        raise RuntimeError("coordinate words unavailable")


def _word(text: str, x0: float, x1: float, top: float) -> dict[str, object]:
    return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": top + 12}


def _horizontal_rules(tops: list[float]) -> list[dict[str, object]]:
    return [
        {"x0": 40, "x1": 560, "top": top, "bottom": top}
        for top in tops
    ]


def _extract_page(page: _CoordinatePage):
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "layout.pdf"
        path.write_bytes(b"%PDF-1.4\n%%EOF\n")
        with mock.patch("pdfplumber.open", return_value=_OnePagePdf(page)):
            return extract_pdf_text(path)


def _extract_pages(pages: list[_CoordinatePage]):
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "layout.pdf"
        path.write_bytes(b"%PDF-1.4\n%%EOF\n")
        with mock.patch("pdfplumber.open", return_value=_PageSequencePdf(pages)):
            return extract_pdf_text(path)


class LayoutGeneralityTests(unittest.TestCase):
    def test_numeric_table_index_column_is_never_cropped_as_a_gutter(self) -> None:
        """A ruled 1..N channel/index column is table data, not proven line numbering."""

        class RuledTablePage:
            width = 600
            height = 800
            bbox = (0, 0, 600, 800)
            lines = [
                {"x0": 20, "x1": 580, "top": 80 + row * 30, "bottom": 80 + row * 30}
                for row in range(17)
            ]

            def extract_words(self, **_kwargs):
                return [
                    {
                        "text": str(row),
                        "x0": 40,
                        "x1": 52,
                        "top": 90 + row * 30,
                        "bottom": 102 + row * 30,
                    }
                    for row in range(1, 17)
                ]

        self.assertEqual(
            (0.0, 600.0),
            _content_x_bounds_without_line_gutters(RuledTablePage()),
        )

    def test_screenshot_only_table_regions_prevent_reliable_no_difference(self) -> None:
        """A table screenshot without comparable rows is unknown, not unchanged."""

        body = "\n".join([_TECHNICAL_LINE] * 8)

        def extraction(name: str, image: str) -> ExtractionResult:
            return ExtractionResult(
                pdf_path=Path(name),
                pages=[PageText(page_number=1, text=f"1 Scope\n{body}")],
                total_pages=1,
                selected_start_page=1,
                selected_end_page=1,
                table_visuals=[
                    TableVisual(
                        page_number=1,
                        table_number=1,
                        title="Table 1 Opaque results",
                        bbox=(0.0, 0.0, 100.0, 100.0),
                        image_data_uri=image,
                        row_texts=[],
                        grid_summary="visible grid",
                    )
                ],
            )

        result = compare_extractions(
            extraction("old.pdf", "data:image/png;base64,OLD"),
            extraction("new.pdf", "data:image/png;base64,NEW"),
            DiffOptions(),
        )

        self.assertEqual("degraded", result.assessment.state.value)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)
        self.assertTrue(any("表格" in reason for reason in result.assessment.reasons))

    def test_layout_risk_on_a_structured_table_page_is_technical_risk(self) -> None:
        """A table on a metadata-looking page cannot hide reading-order uncertainty."""

        body = " ".join([_TECHNICAL_LINE] * 20)
        table_row = "表格行: T1 | Parameter=Receiver limit | Value=5 | Units=V"
        extraction = ExtractionResult(
            pdf_path=Path("table-layout-risk.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=f"Revision History\nTable 1 Receiver Limits\n{table_row}",
                    layout_risk=True,
                ),
                PageText(page_number=2, text=f"1 Scope\n{body}"),
            ],
            total_pages=2,
            selected_start_page=1,
            selected_end_page=2,
            table_visuals=[
                TableVisual(
                    page_number=1,
                    table_number=1,
                    title="Table 1 Receiver Limits",
                    bbox=(20.0, 100.0, 580.0, 300.0),
                    image_data_uri="data:image/png;base64,TABLE",
                    row_texts=[table_row],
                    grid_summary="ruled table",
                )
            ],
        )

        result = compare_extractions(extraction, extraction, DiffOptions())

        self.assertEqual("degraded", result.assessment.state.value)
        self.assertIn(1, result.assessment.old_document.technical_layout_risk_pages)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_dense_two_character_fragmentation_cannot_be_reliable(self) -> None:
        """Hundreds of two-character lines are extraction fragments, not dense prose."""

        extraction = ExtractionResult(
            pdf_path=Path("two-character-fragments.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\n" + "\n".join(["ab"] * 300))],
            total_pages=1,
            selected_start_page=1,
            selected_end_page=1,
        )

        result = compare_extractions(extraction, extraction, DiffOptions())

        self.assertEqual("degraded", result.assessment.state.value)
        self.assertIn(1, result.assessment.old_document.fragmented_text_pages)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_repeated_edge_text_cannot_inflate_reliability_volume(self) -> None:
        """Preserved headers/footers remain comparable but do not count repeatedly as body depth."""

        header = "Protocol Specification Traceable Header " * 4
        footer = "Copyright and distribution footer retained for review " * 4
        extraction = ExtractionResult(
            pdf_path=Path("edge-volume.pdf"),
            pages=[
                PageText(page_number=1, text=f"{header}\n1 Scope\nTiny\n{footer}"),
                PageText(page_number=2, text=f"{header}\n2 Terms\nx\n{footer}"),
                PageText(page_number=3, text=f"{header}\ny\n{footer}"),
            ],
            total_pages=3,
            selected_start_page=1,
            selected_end_page=3,
        )

        result = compare_extractions(extraction, extraction, DiffOptions())

        self.assertEqual("degraded", result.assessment.state.value)
        self.assertLess(result.assessment.old_document.technical_character_count, 500)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_partial_window_carryover_is_not_a_stable_numbered_section(self) -> None:
        """Unnumbered carry-over text remains comparable but cannot prove structure."""

        carryover = "\n".join([_TECHNICAL_LINE] * 8)
        extraction = ExtractionResult(
            pdf_path=Path("partial.pdf"),
            pages=[
                PageText(
                    page_number=50,
                    text=f"{carryover}\n1 Document Control\nRevision history metadata.",
                )
            ],
            total_pages=100,
            selected_start_page=50,
            selected_end_page=50,
        )

        result = compare_extractions(extraction, extraction, DiffOptions())

        self.assertEqual("degraded", result.assessment.state.value)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)
        self.assertEqual(0, result.assessment.old_document.stable_section_count)

    def test_mixed_prefixed_numbering_restart_degrades_structure_confidence(self) -> None:
        """Section 1/2 followed by 1/2 is a numbering restart, not four unique paths."""

        paragraph = " ".join([_TECHNICAL_LINE] * 4)
        text = "\n".join(
            [
                "Section 1 First requirements",
                paragraph,
                "Section 2 Second requirements",
                paragraph,
                "1 Restarted requirements",
                paragraph,
                "2 More restarted requirements",
                paragraph,
            ]
        )
        extraction = ExtractionResult(
            pdf_path=Path("restarted.pdf"),
            pages=[PageText(page_number=1, text=text)],
            total_pages=1,
            selected_start_page=1,
            selected_end_page=1,
        )

        result = compare_extractions(extraction, extraction, DiffOptions())

        self.assertEqual("degraded", result.assessment.state.value)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)
        self.assertGreaterEqual(
            result.assessment.old_document.duplicate_number_path_count,
            2,
        )

    def test_declared_page_window_must_match_extracted_page_sequence(self) -> None:
        """One extracted page cannot stand in for a declared 100-page window."""

        body = " ".join([_TECHNICAL_LINE] * 20)
        extraction = ExtractionResult(
            pdf_path=Path("incomplete-window.pdf"),
            pages=[PageText(page_number=1, text=f"1 Scope\n{body}")],
            total_pages=100,
            selected_start_page=1,
            selected_end_page=100,
        )

        result = compare_extractions(extraction, extraction, DiffOptions())

        self.assertEqual("degraded", result.assessment.state.value)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)
        self.assertFalse(result.assessment.old_document.page_window_consistent)

    def test_huge_invalid_page_metadata_degrades_without_expanding_ranges(self) -> None:
        """External/OCR metadata may be corrupt and must never trigger huge allocations."""

        extraction = ExtractionResult(
            pdf_path=Path("huge-window.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nA stable requirement remains visible.")],
            total_pages=10**9,
            selected_start_page=1,
            selected_end_page=10**9,
        )

        result = compare_extractions(extraction, extraction, DiffOptions())

        self.assertFalse(result.assessment.old_document.page_window_consistent)
        self.assertNotEqual("reliable", result.assessment.state.value)
        self.assertTrue(any("页码范围" in reason for reason in result.assessment.reasons))

    def test_partial_page_metadata_and_nonpositive_pages_degrade(self) -> None:
        """Each supplied range edge and every one-based page number must be valid."""

        body = " ".join([_TECHNICAL_LINE] * 20)
        cases = (
            (1, 100, 50, None),
            (1, 100, None, 100),
            (0, 0, None, None),
            (-1, 0, None, None),
        )
        for page_number, total_pages, start_page, end_page in cases:
            with self.subTest(
                page_number=page_number,
                start_page=start_page,
                end_page=end_page,
            ):
                extraction = ExtractionResult(
                    pdf_path=Path("invalid-window.pdf"),
                    pages=[PageText(page_number=page_number, text=f"1 Scope\n{body}")],
                    total_pages=total_pages,
                    selected_start_page=start_page,
                    selected_end_page=end_page,
                )
                result = compare_extractions(extraction, extraction, DiffOptions())

                self.assertEqual("degraded", result.assessment.state.value)
                self.assertFalse(result.assessment.old_document.page_window_consistent)

    def test_short_two_column_text_cannot_be_reported_as_reliable(self) -> None:
        """Two substantial column rows are enough to make reading order unsafe."""

        words = [
            _word("LeftRequirementAlpha", 60, 220, 120),
            _word("RightRequirementAlpha", 380, 550, 120),
            _word("LeftRequirementBeta", 60, 220, 160),
            _word("RightRequirementBeta", 380, 550, 160),
        ]
        page = _CoordinatePage(words, "1 Scope\n" + "\n".join([_TECHNICAL_LINE] * 8))

        extraction = _extract_page(page)
        result = compare_extractions(extraction, extraction, DiffOptions())

        self.assertTrue(extraction.pages[0].layout_risk)
        self.assertEqual("degraded", result.assessment.state.value)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_title_and_footer_do_not_hide_two_strong_column_rows(self) -> None:
        """Full-width furniture must not change a short two-column decision."""

        words = [
            _word("Document technical requirements", 60, 550, 40),
            _word("Publication subtitle information", 60, 550, 70),
            _word("LeftRequirementAlpha", 60, 220, 120),
            _word("RightRequirementAlpha", 380, 550, 120),
            _word("LeftRequirementBeta", 60, 220, 160),
            _word("RightRequirementBeta", 380, 550, 160),
            _word("Publication footer information", 60, 550, 730),
        ]
        page = _CoordinatePage(words, "1 Scope\n" + "\n".join([_TECHNICAL_LINE] * 8))

        extraction = _extract_page(page)

        self.assertTrue(extraction.pages[0].layout_risk)

    def test_coordinate_analysis_failure_warns_and_degrades_the_pair(self) -> None:
        """An unavailable layout probe is unknown evidence, not proof of linear text."""

        page = _BrokenCoordinatePage(
            [],
            "1 Scope\n" + "\n".join([_TECHNICAL_LINE] * 8),
        )

        extraction = _extract_page(page)
        result = compare_extractions(extraction, extraction, DiffOptions())

        self.assertTrue(
            any("阅读顺序" in warning and "坐标" in warning for warning in extraction.warnings),
            extraction.warnings,
        )
        self.assertEqual("degraded", result.assessment.state.value)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_dense_text_without_coordinate_words_warns_and_degrades_the_pair(self) -> None:
        """Dense text needs coordinate coverage before reading order can be trusted."""

        page = _CoordinatePage(
            [],
            "1 Scope\n" + "\n".join([_TECHNICAL_LINE] * 8),
        )

        extraction = _extract_page(page)
        result = compare_extractions(extraction, extraction, DiffOptions())

        self.assertTrue(
            any("阅读顺序" in warning and "坐标" in warning for warning in extraction.warnings),
            extraction.warnings,
        )
        self.assertEqual("degraded", result.assessment.state.value)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_dense_text_with_only_header_footer_coordinates_is_degraded(self) -> None:
        """Two sparse coordinate lines do not cover a dense extracted body."""

        page = _CoordinatePage(
            [
                _word("Publication Header", 60, 220, 40),
                _word("Page Footer", 60, 180, 730),
            ],
            "1 Scope\n" + "\n".join([_TECHNICAL_LINE] * 8),
        )

        extraction = _extract_page(page)
        result = compare_extractions(extraction, extraction, DiffOptions())

        self.assertTrue(
            any("阅读顺序坐标检查证据不足" in warning for warning in extraction.warnings),
            extraction.warnings,
        )
        self.assertEqual("degraded", result.assessment.state.value)

    def test_short_text_without_coordinate_words_does_not_add_layout_noise(self) -> None:
        """A genuinely short page is handled by the text-volume gate instead."""

        extraction = _extract_page(_CoordinatePage([], "Cover title"))

        self.assertFalse(
            any("阅读顺序坐标检查证据不足" in warning for warning in extraction.warnings),
            extraction.warnings,
        )

    def test_coordinate_gap_is_detected_when_reliable_text_is_split_across_pages(self) -> None:
        """Two medium pages must not evade a whole-document reliability gate."""

        page_body = "\n".join([_TECHNICAL_LINE] * 3)
        extraction = _extract_pages(
            [
                _CoordinatePage([], f"1 Scope\n{page_body}"),
                _CoordinatePage([], f"2 Requirements\n{page_body}"),
            ]
        )
        coordinate_warnings = [
            warning
            for warning in extraction.warnings
            if "阅读顺序坐标检查证据不足" in warning
        ]

        self.assertEqual(2, len(coordinate_warnings), extraction.warnings)

    def test_short_staggered_columns_cannot_be_reported_as_reliable(self) -> None:
        """Offset baselines must not let a short two-column page escape the gate."""

        words = [
            _word("Document technical requirements", 60, 550, 30),
            _word("Publication subtitle information", 60, 550, 60),
            _word("LeftRequirementAlpha", 60, 220, 120),
            _word("RightRequirementAlpha", 380, 550, 130),
            _word("LeftRequirementBeta", 60, 220, 150),
            _word("RightRequirementBeta", 380, 550, 160),
            _word("Publication footer information", 60, 550, 730),
        ]
        page = _CoordinatePage(words, "1 Scope\n" + "\n".join([_TECHNICAL_LINE] * 8))

        extraction = _extract_page(page)
        result = compare_extractions(extraction, extraction, DiffOptions())

        self.assertTrue(extraction.pages[0].layout_risk)
        self.assertEqual("degraded", result.assessment.state.value)

    def test_many_short_aligned_column_lines_cannot_escape_layout_risk(self) -> None:
        """Repeated short labels form column evidence when they span most of a page."""

        words: list[dict[str, object]] = []
        for row in range(35):
            top = 70 + row * 17
            words.extend(
                [
                    _word(f"Limit L{row + 1:02d}", 60, 160, top),
                    _word(f"Value R{row + 1:02d}", 390, 500, top),
                ]
            )
        page = _CoordinatePage(words, "1 Scope\n" + "\n".join([_TECHNICAL_LINE] * 8))

        extraction = _extract_page(page)
        result = compare_extractions(extraction, extraction, DiffOptions())

        self.assertTrue(extraction.pages[0].layout_risk)
        self.assertEqual("degraded", result.assessment.state.value)

    def test_many_short_staggered_column_lines_cannot_escape_layout_risk(self) -> None:
        """Short left/right labels with offset baselines still prove two columns."""

        words = [
            _word(f"Limit L{row + 1:02d}", 60, 160, 70 + row * 32)
            for row in range(18)
        ]
        words.extend(
            _word(f"Value R{row + 1:02d}", 390, 500, 86 + row * 32)
            for row in range(17)
        )
        page = _CoordinatePage(words, "1 Scope\n" + "\n".join([_TECHNICAL_LINE] * 8))

        extraction = _extract_page(page)
        result = compare_extractions(extraction, extraction, DiffOptions())

        self.assertTrue(extraction.pages[0].layout_risk)
        self.assertEqual("degraded", result.assessment.state.value)

    def test_narrow_left_sidebar_columns_are_detected(self) -> None:
        """A 30/70 split is still a non-linear two-column page."""

        words: list[dict[str, object]] = []
        for row in range(8):
            top = 90 + row * 35
            words.extend(
                [
                    _word(f"SidebarRequirement{row:02d}", 50, 150, top),
                    _word(f"MainRequirementText{row:02d}", 210, 550, top),
                ]
            )
        extraction = _extract_page(
            _CoordinatePage(words, "1 Scope\n" + "\n".join([_TECHNICAL_LINE] * 8))
        )

        self.assertTrue(extraction.pages[0].layout_risk)

    def test_narrow_right_sidebar_columns_are_detected(self) -> None:
        """A 65/35 split is still a non-linear two-column page."""

        words: list[dict[str, object]] = []
        for row in range(8):
            top = 90 + row * 35
            words.extend(
                [
                    _word(f"MainRequirementText{row:02d}", 60, 390, top),
                    _word(f"SidebarRequirement{row:02d}", 450, 550, top),
                ]
            )
        extraction = _extract_page(
            _CoordinatePage(words, "1 Scope\n" + "\n".join([_TECHNICAL_LINE] * 8))
        )

        self.assertTrue(extraction.pages[0].layout_risk)

    def test_many_short_aligned_grid_rows_are_not_guessed_to_be_prose_columns(self) -> None:
        """Visible table grid evidence outweighs aligned short-label repetition."""

        words: list[dict[str, object]] = []
        for row in range(35):
            top = 70 + row * 17
            words.extend(
                [
                    _word(f"Limit L{row + 1:02d}", 60, 160, top),
                    _word(f"Value R{row + 1:02d}", 390, 500, top),
                ]
            )
        page = _CoordinatePage(words, "\n".join([_TECHNICAL_LINE] * 8))
        page.lines = _horizontal_rules([60 + row * 17 for row in range(37)])

        extraction = _extract_page(page)

        self.assertFalse(extraction.pages[0].layout_risk)

    def test_unrelated_footer_rules_do_not_hide_real_aligned_columns(self) -> None:
        """A local ruled block elsewhere on the page is not table evidence for prose."""

        words: list[dict[str, object]] = []
        for row in range(12):
            top = 80 + row * 28
            words.extend(
                [
                    _word(f"LeftRequirement{row:02d}", 60, 220, top),
                    _word(f"RightRequirement{row:02d}", 380, 550, top),
                ]
            )
        page = _CoordinatePage(words, "1 Scope\n" + "\n".join([_TECHNICAL_LINE] * 8))
        page.lines = _horizontal_rules([620 + row * 8 for row in range(12)])

        extraction = _extract_page(page)

        self.assertTrue(extraction.pages[0].layout_risk)

    def test_small_embedded_grid_does_not_hide_surrounding_prose_columns(self) -> None:
        """A local table inside a column region cannot suppress the whole page."""

        words: list[dict[str, object]] = []
        for row in range(12):
            top = 80 + row * 28
            words.extend(
                [
                    _word(f"LeftRequirement{row:02d}", 60, 220, top),
                    _word(f"RightRequirement{row:02d}", 380, 550, top),
                ]
            )
        page = _CoordinatePage(words, "1 Scope\n" + "\n".join([_TECHNICAL_LINE] * 8))
        page.lines = _horizontal_rules([200, 210, 220, 230])

        extraction = _extract_page(page)

        self.assertTrue(extraction.pages[0].layout_risk)

    def test_disconnected_small_grids_do_not_merge_into_one_large_table(self) -> None:
        """Separated ruled clusters cannot jointly suppress prose between them."""

        words: list[dict[str, object]] = []
        for row in range(12):
            top = 80 + row * 28
            words.extend(
                [
                    _word(f"LeftRequirement{row:02d}", 60, 220, top),
                    _word(f"RightRequirement{row:02d}", 380, 550, top),
                ]
            )
        page = _CoordinatePage(words, "1 Scope\n" + "\n".join([_TECHNICAL_LINE] * 8))
        page.lines = _horizontal_rules([90, 100, 350, 360])

        extraction = _extract_page(page)

        self.assertTrue(extraction.pages[0].layout_risk)

    def test_offset_grid_cells_are_not_guessed_to_be_staggered_prose(self) -> None:
        """Small baseline offsets inside a ruled table do not prove prose columns."""

        words = [
            _word(f"LeftRequirement{row:02d}", 60, 220, 100 + row * 30)
            for row in range(8)
        ]
        words.extend(
            _word(f"RightRequirement{row:02d}", 380, 550, 108 + row * 30)
            for row in range(8)
        )
        page = _CoordinatePage(words, "\n".join([_TECHNICAL_LINE] * 8))
        page.lines = _horizontal_rules([90 + row * 30 for row in range(10)])

        extraction = _extract_page(page)

        self.assertFalse(extraction.pages[0].layout_risk)

    def test_full_multipage_document_needs_more_than_one_heading_hit(self) -> None:
        """One accidental heading on a complete document is not stable structure."""

        page_body = "\n".join([_TECHNICAL_LINE] * 5)
        extraction = ExtractionResult(
            pdf_path=Path("full-seven-page-document.pdf"),
            pages=[
                PageText(page_number=1, text=f"1 Overview\n{page_body}"),
                *[
                    PageText(page_number=page_number, text=page_body)
                    for page_number in range(2, 8)
                ],
            ],
            total_pages=7,
            selected_start_page=1,
            selected_end_page=7,
        )

        result = compare_extractions(extraction, extraction, DiffOptions())

        self.assertEqual(1, result.assessment.old_document.stable_section_count)
        self.assertEqual("degraded", result.assessment.state.value)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_explicit_multipage_clause_window_can_remain_reliable(self) -> None:
        """A proven partial window may intentionally span one long numbered clause."""

        page_body = "\n".join([_TECHNICAL_LINE] * 5)
        extraction = ExtractionResult(
            pdf_path=Path("selected-long-clause.pdf"),
            pages=[
                PageText(page_number=36, text=f"8.3 Receiver requirements\n{page_body}"),
                PageText(page_number=37, text=page_body),
                PageText(page_number=38, text=page_body),
            ],
            total_pages=120,
            selected_start_page=36,
            selected_end_page=38,
        )

        result = compare_extractions(extraction, extraction, DiffOptions())

        self.assertTrue(result.assessment.old_document.explicit_partial_window)
        self.assertEqual("reliable", result.assessment.state.value)
        self.assertTrue(result.assessment.allows_no_difference_conclusion)

    def test_nearly_full_window_cannot_use_partial_selection_as_an_exemption(self) -> None:
        """Skipping only a cover page does not make one heading stable structure."""

        page_body = "\n".join([_TECHNICAL_LINE] * 5)
        extraction = ExtractionResult(
            pdf_path=Path("cover-skipped-document.pdf"),
            pages=[
                PageText(page_number=2, text=f"1 Overview\n{page_body}"),
                *[
                    PageText(page_number=page_number, text=page_body)
                    for page_number in range(3, 9)
                ],
            ],
            total_pages=8,
            selected_start_page=2,
            selected_end_page=8,
        )

        result = compare_extractions(extraction, extraction, DiffOptions())

        self.assertTrue(result.assessment.old_document.explicit_partial_window)
        self.assertEqual("degraded", result.assessment.state.value)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)


if __name__ == "__main__":
    unittest.main()
