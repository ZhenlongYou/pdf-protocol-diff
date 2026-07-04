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

    def test_pcie_style_numbered_steps_stay_inside_deep_section(self) -> None:
        """Procedure steps under sections like 2.11.2 should not become sections."""

        extraction = ExtractionResult(
            pdf_path=Path("pcie_steps.pdf"),
            pages=[
                PageText(
                    page_number=36,
                    text=(
                        "Test Descriptions\n"
                        "PCI Express Architecture PHY Test Specification | 36\n"
                        "Revision 4.0, Version 1.2\n"
                        "August 18, 2021\n"
                        "2.11.2 Overview of Calibration Steps at 16.0 GT/s\n"
                        "For this calibration a real time oscilloscope is used.\n"
                        "1. Connect the end of the cables to the RX SMPs.\n"
                        "2.\n"
                        "128 bits of a 1010 clock pattern at 16.0 GT/s.\n"
                        "14. Turn all jitter and noise sources off.\n"
                        "6 X 62.5 ps =\n"
                        "125.0 us) and adjust it to the target range.\n"
                    ),
                ),
                PageText(
                    page_number=37,
                    text=(
                        "Test Descriptions\n"
                        "PCI Express Architecture PHY Test Specification | 37\n"
                        "Revision 4.0, Version 1.2\n"
                        "August 18, 2021\n"
                        "16. Capture 2.0 million unit-intervals of data.\n"
                        "17. Analyze the waveform using SigTest.\n"
                    ),
                ),
            ],
        )

        sections = section_document(extraction)
        locations = [section.location for section in sections]
        body = "\n".join(section.body for section in sections)

        self.assertEqual(1, len(sections))
        self.assertIn("2.11.2 Overview of Calibration Steps at 16.0 GT/s", locations)
        self.assertNotIn(
            "2.11.2 Overview of Calibration Steps at 16.0 GT/s / 6 X 62.5 ps =",
            locations,
        )
        self.assertIn("1. Connect the end of the cables to the RX SMPs.", body)
        self.assertIn("128 bits of a 1010 clock pattern", body)
        self.assertIn("14. Turn all jitter and noise sources off.", body)
        self.assertIn("16. Capture 2.0 million unit-intervals of data.", body)
        self.assertNotIn("PCI Express Architecture PHY Test Specification", body)
        self.assertNotIn("Revision 4.0", body)
        self.assertNotIn("August 18, 2021", body)

    def test_deep_section_keeps_unlisted_procedure_verbs(self) -> None:
        """Deep PCIe-like sections should not drop numbered steps by verb list."""

        extraction = ExtractionResult(
            pdf_path=Path("unlisted_steps.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "2.13.2 Overview of Calibration Steps at 16.0 GT/s\n"
                        "Intro text.\n"
                        "1. Ensure CTLE enabled.\n"
                        "2. Allow settling.\n"
                        "3. Calibrate the source jitter to the required limit.\n"
                        "4. Observe the recovered clock output.\n"
                        "5. Use the saved template for analysis."
                    ),
                )
            ],
        )

        sections = section_document(extraction)
        body = "\n".join(section.body for section in sections)

        self.assertEqual(1, len(sections))
        self.assertIn("1. Ensure CTLE enabled.", body)
        self.assertIn("2. Allow settling.", body)
        self.assertIn("3. Calibrate the source jitter", body)
        self.assertIn("4. Observe the recovered clock", body)
        self.assertIn("5. Use the saved template", body)

    def test_real_integer_heading_after_deep_steps_is_preserved(self) -> None:
        """A top-level chapter after deep procedure steps should not be swallowed."""

        extraction = ExtractionResult(
            pdf_path=Path("deep_then_top.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "2.13.2 Overview of Calibration Steps at 16.0 GT/s\n"
                        "Intro text.\n"
                        "1. Calibrate the source jitter to the required limit.\n"
                        "2. Observe the recovered clock output.\n"
                        "3 Receiver Requirements\n"
                        "Receiver requirements text."
                    ),
                )
            ],
        )

        sections = section_document(extraction)
        locations = [section.location for section in sections]

        self.assertIn("2.13.2 Overview of Calibration Steps at 16.0 GT/s", locations)
        self.assertIn("3 Receiver Requirements", locations)
        self.assertIn("1. Calibrate the source jitter", sections[0].body)
        self.assertNotIn("3 Receiver Requirements", sections[0].body)

    def test_top_level_numbered_headings_after_subsections_are_preserved(self) -> None:
        """A real top-level heading after a dotted subsection must remain a section."""

        extraction = ExtractionResult(
            pdf_path=Path("top_level_after_subsection.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Scope text.\n"
                        "1.1 Delivery\n"
                        "Delivery text.\n"
                        "2 Acceptance\n"
                        "Acceptance text."
                    ),
                )
            ],
        )

        sections = section_document(extraction)
        locations = [section.location for section in sections]

        self.assertIn("1 Scope", locations)
        self.assertIn("1 Scope / 1.1 Delivery", locations)
        self.assertIn("2 Acceptance", locations)

    def test_single_selected_pcie_page_removes_obvious_margin_furniture(self) -> None:
        """Single-page windows still need conservative header/footer cleanup."""

        extraction = ExtractionResult(
            pdf_path=Path("single_pcie_page.pdf"),
            pages=[
                PageText(
                    page_number=36,
                    text=(
                        "Test Descriptions\n"
                        "PCI Express Architecture PHY Test Specification | 36\n"
                        "Revision 4.0, Version 1.2\n"
                        "August 18, 2021\n"
                        "2.11.2 Overview of Calibration Steps at 16.0 GT/s\n"
                        "For this calibration a real time oscilloscope is used.\n"
                        "1. Connect the end of the cables to the RX SMPs.\n"
                    ),
                )
            ],
            selected_start_page=36,
            selected_end_page=36,
        )

        sections = section_document(extraction)
        joined_bodies = "\n".join(section.body for section in sections)

        self.assertIn("2.11.2 Overview of Calibration Steps at 16.0 GT/s", sections[0].location)
        self.assertIn("1. Connect the end of the cables", joined_bodies)
        self.assertNotIn("Test Descriptions", joined_bodies)
        self.assertNotIn("PCI Express Architecture PHY Test Specification", joined_bodies)
        self.assertNotIn("Revision 4.0", joined_bodies)
        self.assertNotIn("August 18, 2021", joined_bodies)

    def test_opening_selected_range_keeps_procedure_steps_as_body(self) -> None:
        """A page window starting mid-procedure should not create fake chapters."""

        extraction = ExtractionResult(
            pdf_path=Path("range_starts_mid_procedure.pdf"),
            pages=[
                PageText(
                    page_number=37,
                    text=(
                        "Test Descriptions\n"
                        "PCI Express Architecture PHY Test Specification | 37\n"
                        "Revision 4.0, Version 1.2\n"
                        "August 18, 2021\n"
                        "6. Adjust the TX equalization preset to the target value.\n"
                        "6 X 62.5 ps = 375 ps\n"
                        "7. Capture 2.0 million unit-intervals of data.\n"
                        "3 Receiver Requirements\n"
                        "Receiver requirements text."
                    ),
                )
            ],
            selected_start_page=37,
            selected_end_page=37,
        )

        sections = section_document(extraction)
        locations = [section.location for section in sections]

        self.assertEqual(2, len(sections))
        self.assertEqual("范围起始页前序内容", sections[0].location)
        self.assertIn("3 Receiver Requirements", locations)
        self.assertIn("6. Adjust the TX equalization", sections[0].body)
        self.assertIn("6 X 62.5 ps = 375 ps", sections[0].body)
        self.assertIn("7. Capture 2.0 million", sections[0].body)
        self.assertNotIn("6. Adjust the TX equalization", locations)
        self.assertNotIn("7. Capture 2.0 million", locations)

    def test_cosmetic_case_spacing_and_punctuation_diffs_are_suppressed(self) -> None:
        """Formatting-only extraction differences should not clutter reports."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_cosmetic.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Enable 100 MHz Sj and set the value to 0.0ps, then save the waveform."
                    ),
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_cosmetic.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Enable 100 MHz SJ and set the value to 0.0 ps then save the waveform"
                    ),
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        self.assertEqual([], result.changes)

    def test_numeric_punctuation_changes_are_not_suppressed(self) -> None:
        """Numeric punctuation and tolerance signs can carry protocol meaning."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_numeric.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Set the residual jitter limit to 1.0 ps.\n"
                        "Apply the voltage tolerance of +0/-2 mV."
                    ),
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_numeric.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Set the residual jitter limit to 10 ps.\n"
                        "Apply the voltage tolerance of 0/2 mV."
                    ),
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        snippets = "\n".join(
            pair.old + "\n" + pair.new
            for change in result.changes
            for pair in change.replaced_snippets
        )

        self.assertEqual(1, len(result.changes))
        self.assertIn("1.0 ps", snippets)
        self.assertIn("10 ps", snippets)
        self.assertIn("+0/-2 mV", snippets)
        self.assertIn("0/2 mV", snippets)

    def test_comparison_operator_changes_are_not_suppressed(self) -> None:
        """Inequality operators are protocol content, not display punctuation."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_operator.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nEye height must be <= 15 mV.")],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_operator.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nEye height must be >= 15 mV.")],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        snippets = "\n".join(
            pair.old + "\n" + pair.new
            for change in result.changes
            for pair in change.replaced_snippets
        )

        self.assertEqual(1, len(result.changes))
        self.assertIn("<= 15 mV", snippets)
        self.assertIn(">= 15 mV", snippets)

    def test_arrow_symbol_differences_are_suppressed(self) -> None:
        """Connection arrows are layout noise unless nearby tokens also change."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_arrows.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nGenerator→Cable→Scope.")],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_arrows.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nGenerator- >Cable->Scope.")],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        self.assertEqual([], result.changes)

    def test_pdf_numeric_extraction_artifacts_are_suppressed(self) -> None:
        """PDF spacing around units, decimals, and exponents should not be noise."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_numeric_artifact.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text="1 Scope\nCapture 2.0 X 106 X 62.5ps = 125.0μs.",
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_numeric_artifact.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text="1 Scope\nCapture 2.0 X 10 6 X 62.5 ps = 125. 0 μs.",
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        self.assertEqual([], result.changes)

    def test_decimal_value_split_across_pdf_lines_stays_one_unit(self) -> None:
        """A line break inside a decimal value should not create a lone fragment."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_decimal_wrap.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text="1 Scope\nCapture 2.0 million unit-intervals (125.0 μs).",
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_decimal_wrap.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text="1 Scope\nCapture 2.0 million unit-intervals (125.\n0 μs).",
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        self.assertEqual([], result.changes)

    def test_snippet_limit_scans_all_differences_and_reports_omissions(self) -> None:
        """Later substantive changes should not disappear when snippets are capped."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_many.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Formatting text uses commas, only.\n"
                        "Unchanged anchor one.\n"
                        "The jitter limit is 1.0 ps.\n"
                        "Unchanged anchor two.\n"
                        "The preset mode is P5."
                    ),
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_many.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Formatting text uses commas only\n"
                        "Unchanged anchor one.\n"
                        "The jitter limit is 10 ps.\n"
                        "Unchanged anchor two.\n"
                        "The preset mode is P6."
                    ),
                )
            ],
        )

        result = compare_extractions(
            old_extraction,
            new_extraction,
            DiffOptions(max_snippets_per_section=1),
        )

        self.assertEqual(1, len(result.changes))
        self.assertEqual(1, result.changes[0].omitted_snippet_count)
        shown = "\n".join(
            pair.old + "\n" + pair.new for pair in result.changes[0].replaced_snippets
        )
        self.assertIn("1.0 ps", shown)
        self.assertIn("10 ps", shown)
        self.assertNotIn("Formatting text uses commas", shown)

    def test_unequal_replace_block_pairs_related_units(self) -> None:
        """Unequal replace blocks should not cross-pair unrelated changed sentences."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_unequal_block.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "The jitter limit is 1.0 ps.\n"
                        "The preset mode is P5."
                    ),
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_unequal_block.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "The jitter limit is 10 ps.\n"
                        "Supplier shall provide waveform logs.\n"
                        "The preset mode is P6."
                    ),
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        pairs = result.changes[0].replaced_snippets

        self.assertEqual(1, len(result.changes))
        self.assertEqual(2, len(pairs))
        self.assertTrue(any("jitter limit" in pair.old and "jitter limit" in pair.new for pair in pairs))
        self.assertTrue(any("preset mode" in pair.old and "preset mode" in pair.new for pair in pairs))
        self.assertFalse(any("preset mode" in pair.old and "jitter limit" in pair.new for pair in pairs))
        self.assertIn("Supplier shall provide waveform logs.", result.changes[0].added_snippets)

        swapped_new_extraction = ExtractionResult(
            pdf_path=Path("new_unequal_block_swapped.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "The preset mode is P6.\n"
                        "The jitter limit is 10 ps.\n"
                        "Supplier shall provide waveform logs."
                    ),
                )
            ],
        )

        swapped_result = compare_extractions(old_extraction, swapped_new_extraction, DiffOptions())
        swapped_pairs = swapped_result.changes[0].replaced_snippets

        self.assertTrue(
            any("jitter limit" in pair.old and "jitter limit" in pair.new for pair in swapped_pairs)
        )
        self.assertTrue(
            any("preset mode" in pair.old and "preset mode" in pair.new for pair in swapped_pairs)
        )
        self.assertFalse(
            any("preset mode" in pair.old and "jitter limit" in pair.new for pair in swapped_pairs)
        )

    def test_heading_change_counts_against_snippet_limit(self) -> None:
        """A title snippet should not silently hide a body change."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_heading_limit.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nSet jitter limit to 1.0 ps.")],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_heading_limit.pdf"),
            pages=[PageText(page_number=1, text="1 Applicability\nSet jitter limit to 10 ps.")],
        )

        result = compare_extractions(
            old_extraction,
            new_extraction,
            DiffOptions(max_snippets_per_section=1),
        )

        self.assertEqual(1, len(result.changes))
        self.assertEqual(1, result.changes[0].omitted_snippet_count)
        self.assertEqual(1, len(result.changes[0].replaced_snippets))

    def test_wrapped_sentence_snippets_are_reported_as_complete_units(self) -> None:
        """Line-wrapped PDF text should produce readable sentence-level snippets."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_wrapped.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "The supplier shall provide the calibration report before shipment and include\n"
                        "the original waveform files for audit."
                    ),
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_wrapped.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "The supplier shall provide the calibration report before shipment and include\n"
                        "the original waveform files plus SigTest logs for audit."
                    ),
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        self.assertEqual(1, len(result.changes))
        pair = result.changes[0].replaced_snippets[0]
        self.assertIn("include the original waveform files for audit.", pair.old)
        self.assertIn("include the original waveform files plus SigTest logs for audit.", pair.new)
        self.assertNotIn("…", pair.old)
        self.assertNotIn("…", pair.new)

    def test_wrapped_lettered_list_marker_stays_with_sentence(self) -> None:
        """List markers split by PDF extraction should not become lone snippets."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_list.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text="1 Scope\na.\nSet transmitter amplitude to 720 mV.",
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_list.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text="1 Scope\na.\nSet transmitter amplitude to 800 mV.",
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        pair = result.changes[0].replaced_snippets[0]

        self.assertIn("a. Set transmitter amplitude to 720 mV.", pair.old)
        self.assertIn("a. Set transmitter amplitude to 800 mV.", pair.new)
        self.assertNotEqual("a.", pair.old)
        self.assertNotEqual("a.", pair.new)

    def test_html_inline_highlight_deemphasizes_case_noise(self) -> None:
        """HTML should highlight substantive token changes, not case-only noise."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_highlight.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text="1 Scope\nIf the computed Rj is valid, repeat steps 9 through 11.",
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_highlight.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text="1 Scope\nIf the computed RJ is valid, repeat steps 10 through 11.",
                )
            ],
        )
        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, Path(temp_dir), DiffOptions())
            report_html = outputs["html"].read_text(encoding="utf-8")

        self.assertNotIn('<mark class="del">Rj</mark>', report_html)
        self.assertNotIn('<mark class="ins">RJ</mark>', report_html)
        self.assertIn('<mark class="del">9</mark>', report_html)
        self.assertIn('<mark class="ins">10</mark>', report_html)

    def test_opening_range_location_is_human_readable(self) -> None:
        """Selected-range pre-heading text should not expose internal labels."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_range.pdf"),
            pages=[PageText(page_number=10, text="1 Scope\nCommon requirement.")],
            selected_start_page=10,
            selected_end_page=10,
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_range.pdf"),
            pages=[
                PageText(
                    page_number=20,
                    text="New preface requirement.\n1 Scope\nCommon requirement.",
                )
            ],
            selected_start_page=20,
            selected_end_page=20,
        )
        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, Path(temp_dir), DiffOptions())
            report_md = outputs["markdown"].read_text(encoding="utf-8")

        self.assertIn("新增: 新选择范围第 20 页的章节前内容", report_md)
        self.assertIn("新位置: 新选择范围第 20 页的章节前内容", report_md)
        self.assertNotIn("范围起始页前序内容", report_md)

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

    def test_html_explains_when_snippets_are_omitted_by_limit(self) -> None:
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

        self.assertIn("另有 1 条差异片段未展示", report_html)
        self.assertNotIn("仅元数据或位置发生变化", report_html)


if __name__ == "__main__":
    unittest.main()
