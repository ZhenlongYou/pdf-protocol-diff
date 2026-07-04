"""Regression tests for the protocol PDF diff workflow.

These tests use the built-in minimal PDFs so validation does not depend on any
company document. They cover the user-facing promise: old/new PDFs are accepted,
reports are produced, and chapter/section changes are classified.
"""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
import sys
from argparse import Namespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from main import resolve_inputs
from protocol_pdf_diff.compare import compare_extractions
from protocol_pdf_diff.compare import run_diff
from protocol_pdf_diff.models import DiffOptions, ExtractionResult, PageText
from protocol_pdf_diff.pdf_extract import extract_pdf_text
from protocol_pdf_diff.reporting import write_reports
from protocol_pdf_diff.sample_data import write_demo_pdfs, write_multipage_text_pdf
from protocol_pdf_diff.sectioning import section_document


class ProtocolDiffTests(unittest.TestCase):
    """End-to-end tests over generated old/new sample PDFs."""

    def test_demo_pdfs_produce_modified_and_added_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            old_pdf, new_pdf = write_demo_pdfs(Path(temp_dir) / "inputs")
            result = run_diff(old_pdf, new_pdf, DiffOptions())
            from pypdf import PdfReader

            change_types = [change.change_type for change in result.changes]
            locations = [change.report_location for change in result.changes]
            acceptance = next(
                change
                for change in result.changes
                if change.report_location == "3 Acceptance"
            )

            self.assertEqual(4, len(PdfReader(str(old_pdf)).pages))
            self.assertEqual(5, len(PdfReader(str(new_pdf)).pages))
            self.assertIn("modified", change_types)
            self.assertIn("added", change_types)
            self.assertTrue(any("1.1 Delivery" in location for location in locations))
            self.assertTrue(any("2.1 Security" in location for location in locations))
            self.assertTrue(any("2.2 Documentation" in location for location in locations))
            self.assertEqual("4", acceptance.old_section.page_range)
            self.assertEqual("5", acceptance.new_section.page_range)

    def test_extract_pdf_text_respects_inclusive_page_range(self) -> None:
        """PDF extraction should slice selected pages without renumbering them."""

        with tempfile.TemporaryDirectory() as temp_dir:
            old_pdf, _new_pdf = write_demo_pdfs(Path(temp_dir) / "inputs")
            extraction = extract_pdf_text(old_pdf, start_page=2, end_page=3)

        self.assertEqual([2, 3], [page.page_number for page in extraction.pages])
        self.assertEqual(4, extraction.total_pages)
        self.assertEqual(2, extraction.selected_start_page)
        self.assertEqual(3, extraction.selected_end_page)
        self.assertIn("1.1 Delivery", extraction.pages[0].text)
        self.assertIn("2 Technical Requirements", extraction.pages[1].text)

    def test_extract_pdf_text_rejects_invalid_page_ranges(self) -> None:
        """Invalid user-selected ranges should fail before producing reports."""

        with tempfile.TemporaryDirectory() as temp_dir:
            old_pdf, _new_pdf = write_demo_pdfs(Path(temp_dir) / "inputs")

            with self.assertRaisesRegex(ValueError, "起始页 4 不能大于终止页 2"):
                extract_pdf_text(old_pdf, start_page=4, end_page=2)
            with self.assertRaisesRegex(ValueError, "超出 PDF 总页数 4"):
                extract_pdf_text(old_pdf, start_page=99)

    def test_run_diff_uses_independent_old_and_new_page_ranges(self) -> None:
        """Old/new page windows can differ while section matching remains content-based."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf, new_pdf = write_demo_pdfs(temp_path / "inputs")
            options = DiffOptions(
                old_start_page=2,
                old_end_page=3,
                new_start_page=2,
                new_end_page=4,
            )
            result = run_diff(old_pdf, new_pdf, options)
            outputs = write_reports(result, temp_path / "reports", options)

            report_text = outputs["text"].read_text(encoding="utf-8")
            report_html = outputs["html"].read_text(encoding="utf-8")
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        locations = [change.report_location for change in result.changes]

        self.assertEqual(4, result.old_total_pages)
        self.assertEqual(5, result.new_total_pages)
        self.assertEqual((2, 3), (result.old_selected_start_page, result.old_selected_end_page))
        self.assertEqual((2, 4), (result.new_selected_start_page, result.new_selected_end_page))
        self.assertTrue(any("1.1 Delivery" in location for location in locations))
        self.assertTrue(any("2.2 Documentation" in location for location in locations))
        self.assertFalse(any("1 Scope" == location for location in locations))
        self.assertFalse(any("3 Acceptance" in location for location in locations))
        self.assertIn("- 旧选择页: 2-3", report_text)
        self.assertIn("- 新选择页: 2-4", report_text)
        self.assertIn("<dt>旧选择页</dt><dd>2-3</dd>", report_html)
        self.assertEqual({"start_page": 2, "end_page": 3, "label": "2-3", "is_full_document": False}, payload["old_selected_pages"])
        self.assertEqual({"start_page": 2, "end_page": 4, "label": "2-4", "is_full_document": False}, payload["new_selected_pages"])

    def test_reports_are_written(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf, new_pdf = write_demo_pdfs(temp_path / "inputs")
            result = run_diff(old_pdf, new_pdf, DiffOptions())
            outputs = write_reports(result, temp_path / "reports", DiffOptions())

            for key in ("markdown", "html", "text", "csv", "json"):
                self.assertTrue(outputs[key].exists(), key)
            report_html = outputs["html"].read_text(encoding="utf-8")
            report_text = outputs["text"].read_text(encoding="utf-8")
            self.assertIn("协议 PDF 差异报告", report_html)
            self.assertIn("change-card", report_html)
            self.assertIn("compare-grid", report_html)
            self.assertIn("nav-label", report_html)
            self.assertIn(">修改<", report_html)
            self.assertIn("旧/新页数", report_html)
            self.assertIn("4 / 5", report_html)
            self.assertIn("按章节编号、标题和正文相似度匹配", report_html)
            self.assertIn("仅比较 PDF 中可抽取文字", report_html)
            self.assertIn("协议 PDF 差异报告", report_text)
            self.assertIn("- 旧/新页数: 4 / 5", report_text)
            self.assertIn("按章节编号、标题和正文相似度匹配", report_text)
            self.assertIn("图片、印章、矢量图等视觉元素不比较", report_text)
            self.assertIn("Delivery", report_text)
            self.assertIn("3.0 V", report_text)
            self.assertIn("2.8 V", report_text)
            self.assertNotIn("#汇总", report_text)
            self.assertNotIn("|---", report_text)
            with outputs["csv"].open(encoding="utf-8-sig") as csv_file:
                rows = list(csv.DictReader(csv_file))
            self.assertTrue(any(row["report_location"].endswith("1.1 Delivery") for row in rows))
            self.assertTrue(any(row["change_type"] == "新增" for row in rows))
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
            self.assertIn("changes", payload)
            self.assertTrue(
                any(change["report_location"].endswith("1.1 Delivery") for change in payload["changes"])
            )
            self.assertTrue(
                any(
                    "2.8 V" in pair["new"]
                    for change in payload["changes"]
                    for pair in change["replaced_snippets"]
                )
            )

    def test_multipage_pdfs_match_sections_not_page_numbers(self) -> None:
        """A realistic page shift should not turn matching sections into page diffs."""

        old_pages = [
            [
                "ACME Protocol Specification",
                "1 Scope",
                "This agreement applies to prototype devices.",
                "Confidential - Page 1 of 3",
            ],
            [
                "ACME Protocol Specification",
                "1.1 Delivery",
                "Supplier shall deliver samples within 20 working days.",
                "Confidential - Page 2 of 3",
            ],
            [
                "ACME Protocol Specification",
                "2 Technical Requirements",
                "The operating voltage range is 3.0 V to 3.6 V.",
                "3 Acceptance",
                "Buyer shall complete acceptance within 5 working days.",
                "Confidential - Page 3 of 3",
            ],
        ]
        new_pages = [
            [
                "ACME Protocol Specification",
                "1 Scope",
                "This agreement applies to prototype devices.",
                "Confidential - Page 1 of 4",
            ],
            [
                "ACME Protocol Specification",
                "1.1 Delivery",
                "Supplier shall deliver samples within 15 working days.",
                "Supplier shall provide a delivery risk notice for delays over 2 days.",
                "Confidential - Page 2 of 4",
            ],
            [
                "ACME Protocol Specification",
                "1.2 Documentation",
                "Supplier shall provide test logs before shipment.",
                "Confidential - Page 3 of 4",
            ],
            [
                "ACME Protocol Specification",
                "2 Technical Requirements",
                "The operating voltage range is 2.8 V to 3.6 V.",
                "3 Acceptance",
                "Buyer shall complete acceptance within 5 working days.",
                "Confidential - Page 4 of 4",
            ],
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(
                temp_path / "old_shifted_protocol.pdf",
                old_pages,
                decorative_marks={3: "old"},
            )
            new_pdf = write_multipage_text_pdf(
                temp_path / "new_shifted_protocol.pdf",
                new_pages,
                decorative_marks={4: "new"},
            )
            result = run_diff(old_pdf, new_pdf, DiffOptions())
            outputs = write_reports(result, temp_path / "reports", DiffOptions())

            locations = [change.report_location for change in result.changes]
            technical = next(
                change
                for change in result.changes
                if change.report_location == "2 Technical Requirements"
            )
            all_snippets = "\n".join(
                snippet
                for change in result.changes
                for snippet in (
                    change.added_snippets
                    + change.removed_snippets
                    + [pair.old for pair in change.replaced_snippets]
                    + [pair.new for pair in change.replaced_snippets]
                )
            )
            report_html = outputs["html"].read_text(encoding="utf-8")

        self.assertIn("1 Scope / 1.1 Delivery", locations)
        self.assertIn("1 Scope / 1.2 Documentation", locations)
        self.assertIn("2 Technical Requirements", locations)
        self.assertNotIn("3 Acceptance", locations)
        self.assertEqual("3", technical.old_section.page_range)
        self.assertEqual("4", technical.new_section.page_range)
        self.assertIn("3.0 V", all_snippets)
        self.assertIn("2.8 V", all_snippets)
        self.assertNotIn("Confidential", all_snippets)
        self.assertNotIn("Page 2 of", all_snippets)
        self.assertIn("旧定位页 3 · 新定位页 4", report_html)
        self.assertIn("按章节编号、标题和正文相似度匹配", report_html)

    def test_page_fallback_matches_content_when_headings_are_missing(self) -> None:
        """When headings fail, page fallback still avoids page-number hard pairing."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_no_headings.pdf"),
            pages=[
                PageText(page_number=1, text="Overview paragraph stays unchanged."),
                PageText(page_number=2, text="Delivery obligation stays unchanged."),
                PageText(page_number=3, text="Reliability test shall use level A."),
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_no_headings.pdf"),
            pages=[
                PageText(page_number=1, text="Overview paragraph stays unchanged."),
                PageText(page_number=2, text="New warranty notice appears only in the new PDF."),
                PageText(page_number=3, text="Delivery obligation stays unchanged."),
                PageText(page_number=4, text="Reliability test shall use level B."),
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, Path(temp_dir), DiffOptions())
            report_html = outputs["html"].read_text(encoding="utf-8")
            report_text = outputs["text"].read_text(encoding="utf-8")

        page_pairs = [
            (
                change.change_type,
                change.old_section.page_range if change.old_section else "-",
                change.new_section.page_range if change.new_section else "-",
            )
            for change in result.changes
        ]
        snippets = "\n".join(
            snippet
            for change in result.changes
            for snippet in (
                change.added_snippets
                + change.removed_snippets
                + [pair.old for pair in change.replaced_snippets]
                + [pair.new for pair in change.replaced_snippets]
            )
        )

        self.assertIn(("added", "-", "2"), page_pairs)
        self.assertIn(("modified", "3", "4"), page_pairs)
        self.assertNotIn(("modified", "2", "2"), page_pairs)
        self.assertIn("level a", snippets)
        self.assertIn("level b", snippets)
        self.assertIn("未识别到稳定章节", report_html)
        self.assertIn("未识别到稳定章节", report_text)

    def test_page_fallback_rejects_low_similarity_same_page_pairs(self) -> None:
        """Synthetic page numbers must not force unrelated fallback pages to match."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_no_headings.pdf"),
            pages=[
                PageText(page_number=1, text="Stable overview paragraph."),
                PageText(page_number=2, text="Old calibration matrix alpha beta gamma."),
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_no_headings.pdf"),
            pages=[
                PageText(page_number=1, text="Stable overview paragraph."),
                PageText(page_number=2, text="New warranty disclosure for customers."),
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        page_pairs = [
            (
                change.change_type,
                change.old_section.page_range if change.old_section else "-",
                change.new_section.page_range if change.new_section else "-",
            )
            for change in result.changes
        ]

        self.assertNotIn(("modified", "2", "2"), page_pairs)
        self.assertIn(("added", "-", "2"), page_pairs)
        self.assertIn(("deleted", "2", "-"), page_pairs)

    def test_report_labels_mixed_heading_and_page_fallback_mode(self) -> None:
        """Reports should not claim pure chapter matching when one side falls back."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_with_heading.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nStable overview paragraph.")],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_without_heading.pdf"),
            pages=[PageText(page_number=1, text="Stable overview paragraph.")],
        )
        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, Path(temp_dir), DiffOptions())
            report_html = outputs["html"].read_text(encoding="utf-8")
            report_text = outputs["text"].read_text(encoding="utf-8")

        self.assertIn("至少一份 PDF 未识别到稳定章节", report_html)
        self.assertIn("至少一份 PDF 未识别到稳定章节", report_text)

    def test_visual_only_pdf_changes_do_not_create_text_diffs(self) -> None:
        """Graphic-only changes are intentionally ignored by the text diff."""

        pages = [
            [
                "ACME Protocol Specification",
                "1 Scope",
                "This agreement applies to prototype devices.",
                "Confidential - Page 1 of 1",
            ]
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(
                temp_path / "old_visual_only.pdf",
                pages,
                decorative_marks={1: "old"},
            )
            new_pdf = write_multipage_text_pdf(
                temp_path / "new_visual_only.pdf",
                pages,
                decorative_marks={1: "new"},
            )
            result = run_diff(old_pdf, new_pdf, DiffOptions())

        self.assertEqual([], result.changes)

    def test_repeated_middle_body_lines_are_not_removed_as_page_furniture(self) -> None:
        """Repeated body clauses on short pages must survive header/footer cleanup."""

        repeated_clause = "Common safety clause applies to all devices."
        extraction = ExtractionResult(
            pdf_path=Path("short_pages.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "ACME Protocol Specification\n"
                        "1 Scope\n"
                        f"{repeated_clause}\n"
                        "Specific scope sentence.\n"
                        "Confidential - Page 1 of 3"
                    ),
                ),
                PageText(
                    page_number=2,
                    text=(
                        "ACME Protocol Specification\n"
                        "1.1 Delivery\n"
                        f"{repeated_clause}\n"
                        "Supplier shall deliver samples.\n"
                        "Confidential - Page 2 of 3"
                    ),
                ),
                PageText(
                    page_number=3,
                    text=(
                        "ACME Protocol Specification\n"
                        "2 Acceptance\n"
                        f"{repeated_clause}\n"
                        "Buyer shall complete acceptance.\n"
                        "Confidential - Page 3 of 3"
                    ),
                ),
            ],
        )

        sections = section_document(extraction)
        joined_bodies = "\n".join(section.body for section in sections)

        self.assertEqual(3, joined_bodies.count(repeated_clause))
        self.assertNotIn("ACME Protocol Specification", joined_bodies)
        self.assertNotIn("Confidential", joined_bodies)

    def test_long_page_top_body_repetition_is_not_removed_as_header(self) -> None:
        """Repeated body near the top of long pages is still contract content."""

        repeated_clause = "Common safety clause applies to all devices."
        filler_lines = "\n".join(f"Body detail {index}." for index in range(1, 8))
        extraction = ExtractionResult(
            pdf_path=Path("long_pages.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        f"{repeated_clause}\n"
                        f"{filler_lines}\n"
                        "Confidential - Page 1 of 3"
                    ),
                ),
                PageText(
                    page_number=2,
                    text=(
                        "1.1 Delivery\n"
                        f"{repeated_clause}\n"
                        f"{filler_lines}\n"
                        "Confidential - Page 2 of 3"
                    ),
                ),
                PageText(
                    page_number=3,
                    text=(
                        "2 Acceptance\n"
                        f"{repeated_clause}\n"
                        f"{filler_lines}\n"
                        "Confidential - Page 3 of 3"
                    ),
                ),
            ],
        )

        sections = section_document(extraction)
        joined_bodies = "\n".join(section.body for section in sections)

        self.assertEqual(3, joined_bodies.count(repeated_clause))
        self.assertNotIn("Confidential", joined_bodies)

    def test_dynamic_footer_is_removed_even_when_not_last_extracted_line(self) -> None:
        """Dynamic page counters should be filtered near the bottom margin."""

        extraction = ExtractionResult(
            pdf_path=Path("footer_tail.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text="1 Scope\nSpecific scope sentence.\nConfidential - Page 1 of 3\nExtracted tail",
                ),
                PageText(
                    page_number=2,
                    text="1.1 Delivery\nSupplier shall deliver samples.\nConfidential - Page 2 of 3\nExtracted tail",
                ),
                PageText(
                    page_number=3,
                    text="2 Acceptance\nBuyer shall complete acceptance.\nConfidential - Page 3 of 3\nExtracted tail",
                ),
            ],
        )

        sections = section_document(extraction)
        joined_bodies = "\n".join(section.body for section in sections)

        self.assertNotIn("Confidential", joined_bodies)
        self.assertNotIn("Page 2 of 3", joined_bodies)

    def test_invalid_explicit_paths_do_not_fall_back_to_demo(self) -> None:
        args = Namespace(
            demo=False,
            old_pdf="/path/that/does/not/exist/old.pdf",
            new_pdf="/path/that/does/not/exist/new.pdf",
        )

        with self.assertRaises(FileNotFoundError):
            resolve_inputs(args)

    def test_title_only_change_is_reported(self) -> None:
        old_extraction = ExtractionResult(
            pdf_path=Path("old.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nThe requirement is unchanged.")],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new.pdf"),
            pages=[PageText(page_number=1, text="1 Applicability\nThe requirement is unchanged.")],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        self.assertEqual(["modified"], [change.change_type for change in result.changes])
        self.assertIn("章节标题: 1 Scope", result.changes[0].replaced_snippets[0].old)
        self.assertIn("章节标题: 1 Applicability", result.changes[0].replaced_snippets[0].new)

    def test_standalone_numeric_headings_are_merged_with_next_title_line(self) -> None:
        extraction = ExtractionResult(
            pdf_path=Path("chinese.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1\n"
                        "适用范围\n"
                        "本协议适用于样品阶段。\n"
                        "1.1\n"
                        "交付要求\n"
                        "供应商应在15个工作日内交付。"
                    ),
                )
            ],
        )

        sections = section_document(extraction)
        locations = [section.location for section in sections]

        self.assertIn("1 适用范围", locations[0])
        self.assertIn("1 适用范围 / 1.1 交付要求", locations[1])

    def test_chinese_chapter_and_section_headings_are_detected(self) -> None:
        extraction = ExtractionResult(
            pdf_path=Path("chapter.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "第一章 总则\n"
                        "本章说明协议范围。\n"
                        "第2节 交付要求\n"
                        "供应商应提交交付计划。"
                    ),
                )
            ],
        )

        sections = section_document(extraction)
        locations = [section.location for section in sections]

        self.assertEqual("第一章 总则", locations[0])
        self.assertEqual("第一章 总则 / 第2节 交付要求", locations[1])

    def test_html_explains_when_snippets_are_suppressed(self) -> None:
        old_extraction = ExtractionResult(
            pdf_path=Path("old.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nOld requirement.")],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nNew requirement.")],
        )
        options = DiffOptions(max_snippets_per_section=0)
        result = compare_extractions(old_extraction, new_extraction, options)

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, Path(temp_dir), options)
            report_html = outputs["html"].read_text(encoding="utf-8")

        self.assertIn("当前片段数量设置未展开具体文本", report_html)
        self.assertNotIn("仅元数据或位置发生变化", report_html)


if __name__ == "__main__":
    unittest.main()
