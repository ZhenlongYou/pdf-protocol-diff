"""Regression tests for the protocol PDF diff workflow.

These tests use the built-in minimal PDFs so validation does not depend on any
company document. They cover the user-facing promise: old/new PDFs are accepted,
reports are produced, and chapter/section changes are classified.
"""

from __future__ import annotations

import csv
import json
import os
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
from protocol_pdf_diff.desktop_gui import (
    ProtocolDiffDesktopApp,
    collect_widget_texts,  # 用于确认桌面界面真的渲染了关键按钮和页码标签。
    default_demo_dir,  # 用于确认 demo 输入文件也写到用户可写目录。
    default_output_dir,  # 用于确认打包版默认输出到用户可写的文档目录。
    parse_optional_page,
    parse_positive_float,
    parse_positive_int,
    run_smoke_test,
)
from protocol_pdf_diff.models import DiffOptions, ExtractionResult, PageText
from protocol_pdf_diff.pdf_extract import extract_pdf_text
from protocol_pdf_diff.reporting import write_reports
from protocol_pdf_diff.sample_data import write_demo_pdfs, write_multipage_text_pdf
from protocol_pdf_diff.sectioning import section_document


class ProtocolDiffTests(unittest.TestCase):
    """End-to-end tests over generated old/new sample PDFs."""

    def test_desktop_gui_input_parsers_validate_user_fields(self) -> None:
        """GUI page and numeric fields should fail early with readable errors."""

        self.assertIsNone(parse_optional_page("", "旧协议起始页"))
        self.assertEqual(36, parse_optional_page("36", "旧协议起始页"))
        self.assertEqual(0.72, parse_positive_float("0.72", "章节匹配阈值"))
        self.assertEqual(20, parse_positive_int("20", "最大片段数"))

        with self.assertRaisesRegex(ValueError, "旧协议起始页 必须是正整数"):
            parse_optional_page("abc", "旧协议起始页")
        with self.assertRaisesRegex(ValueError, "旧协议起始页 必须大于等于 1"):
            parse_optional_page("0", "旧协议起始页")
        with self.assertRaisesRegex(ValueError, "章节匹配阈值 必须大于 0"):
            parse_positive_float("0", "章节匹配阈值")
        with self.assertRaisesRegex(ValueError, "最大片段数 不能小于 0"):
            parse_positive_int("-1", "最大片段数")

    @unittest.skipUnless(
        sys.platform == "darwin" or os.name == "nt" or os.environ.get("DISPLAY"),
        "Tk smoke test needs a desktop session",
    )
    def test_desktop_gui_smoke_test_builds_widgets(self) -> None:
        """The desktop UI should instantiate cleanly for packaged startup checks."""

        run_smoke_test()

    @unittest.skipUnless(
        sys.platform == "darwin" or os.name == "nt" or os.environ.get("DISPLAY"),
        "Tk form test needs a desktop session",
    )
    def test_desktop_gui_collects_valid_form_config(self) -> None:
        """Form values should map cleanly into the shared DiffOptions model."""

        import tkinter as tk

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf, new_pdf = write_demo_pdfs(temp_path / "inputs")
            root = tk.Tk()
            root.withdraw()
            try:
                app = ProtocolDiffDesktopApp(root)
                app.old_pdf_var.set(str(old_pdf))
                app.new_pdf_var.set(str(new_pdf))
                app.output_dir_var.set(str(temp_path / "reports"))
                app.old_start_var.set("2")
                app.old_end_var.set("3")
                app.new_start_var.set("2")
                app.new_end_var.set("4")
                app.min_similarity_var.set("0.8")
                app.unchanged_similarity_var.set("0.99")
                app.max_snippets_var.set("12")
                app.include_unchanged_var.set(True)
                widget_texts = collect_widget_texts(root)  # 收集当前窗口所有可见控件文案。
                page_entry_facts = {
                    label: (entry.winfo_class(), entry.winfo_manager())
                    for label, entry in app.page_entry_widgets.items()
                }  # 在销毁窗口前记录页码输入框的类型和布局状态。
                browse_button_facts = [
                    (button.cget("text"), button.cget("command"))
                    for button in app.file_browse_buttons
                ]  # 在销毁窗口前记录“选择”按钮文案和回调绑定。

                config = app.collect_config()
            finally:
                root.destroy()

        self.assertEqual(old_pdf, config.old_pdf)
        self.assertEqual(new_pdf, config.new_pdf)
        self.assertEqual(2, config.options.old_start_page)
        self.assertEqual(3, config.options.old_end_page)
        self.assertEqual(2, config.options.new_start_page)
        self.assertEqual(4, config.options.new_end_page)
        self.assertEqual(0.8, config.options.min_section_match_similarity)
        self.assertEqual(0.99, config.options.unchanged_similarity)
        self.assertEqual(12, config.options.max_snippets_per_section)
        self.assertTrue(config.options.include_unchanged_sections)
        self.assertEqual(str(default_output_dir()), str(Path.home() / "Documents" / "ProtocolPdfDiffReports"))  # 默认输出目录不能落到 app 包内部。
        self.assertEqual(default_output_dir() / "_demo_inputs", default_demo_dir())  # Demo PDF 也应落在用户可写区域。
        self.assertIn("旧协议起始页", widget_texts)  # 旧 PDF 起始页输入标签必须存在。
        self.assertIn("旧协议终止页", widget_texts)  # 旧 PDF 终止页输入标签必须存在。
        self.assertIn("新协议起始页", widget_texts)  # 新 PDF 起始页输入标签必须存在。
        self.assertIn("新协议终止页", widget_texts)  # 新 PDF 终止页输入标签必须存在。
        self.assertIn("开始比较 / 生成报告", widget_texts)  # 主运行按钮必须存在。
        self.assertEqual(4, len(page_entry_facts))  # 四个页码输入框必须真实创建。
        self.assertEqual(3, len(browse_button_facts))  # 旧 PDF、新 PDF、输出目录三行都必须有“选择”按钮。
        for label, (widget_class, layout_manager) in page_entry_facts.items():
            self.assertEqual("TEntry", widget_class, f"{label} 应该是可输入控件")  # 防止只剩标签没有输入框。
            self.assertEqual("grid", layout_manager, f"{label} 应该已加入布局")  # 防止控件存在但不可见。
        for button_text, button_command in browse_button_facts:
            self.assertEqual("选择", button_text)  # 三个浏览按钮都应显示相同入口文案。
            self.assertTrue(button_command)  # 浏览按钮必须绑定文件/目录选择回调。

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

    def test_reports_preserve_different_old_and_new_source_start_pages(self) -> None:
        """Reports should show real source pages when selected windows start apart."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_window.pdf"),
            pages=[
                PageText(page_number=36, text="2.13 Calibration\nCapture 7 waveforms."),
                PageText(page_number=37, text="2.14 Acceptance\nStable requirement."),
            ],
            total_pages=80,
            selected_start_page=36,
            selected_end_page=37,
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_window.pdf"),
            pages=[
                PageText(page_number=78, text="2.13 Calibration\nCapture 8 waveforms."),
                PageText(page_number=79, text="2.14 Acceptance\nStable requirement."),
            ],
            total_pages=188,
            selected_start_page=78,
            selected_end_page=79,
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, Path(temp_dir), DiffOptions())
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
            report_html = outputs["html"].read_text(encoding="utf-8")

        self.assertEqual({"start_page": 36, "end_page": 37, "label": "36-37", "is_full_document": False}, payload["old_selected_pages"])
        self.assertEqual({"start_page": 78, "end_page": 79, "label": "78-79", "is_full_document": False}, payload["new_selected_pages"])
        self.assertIn("<dt>旧选择页</dt><dd>36-37</dd>", report_html)
        self.assertIn("<dt>新选择页</dt><dd>78-79</dd>", report_html)

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

    def test_deleted_section_is_reported_with_old_location(self) -> None:
        """Whole-section removals should stay visible instead of becoming vague text loss."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_deleted_section.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Common requirement stays unchanged.\n"
                        "1.1 Delivery\n"
                        "Supplier shall deliver samples.\n"
                        "1.2 Warranty\n"
                        "Supplier shall provide a one-year warranty."
                    ),
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_deleted_section.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Common requirement stays unchanged.\n"
                        "1.1 Delivery\n"
                        "Supplier shall deliver samples."
                    ),
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        self.assertEqual(1, len(result.changes))
        change = result.changes[0]
        self.assertEqual("deleted", change.change_type)
        self.assertEqual("1 Scope / 1.2 Warranty", change.report_location)
        self.assertIn("one-year warranty", "\n".join(change.removed_snippets))

    def test_heading_renumbering_is_reported_when_body_is_same(self) -> None:
        """A moved or renumbered clause title is still a reviewable protocol change."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_renumbered_heading.pdf"),
            pages=[PageText(page_number=1, text="2.1 Security\nSupplier shall encrypt logs.")],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_renumbered_heading.pdf"),
            pages=[PageText(page_number=1, text="2.2 Security\nSupplier shall encrypt logs.")],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        self.assertEqual(1, len(result.changes))
        self.assertEqual("modified", result.changes[0].change_type)
        self.assertEqual(
            [("章节标题: 2.1 Security", "章节标题: 2.2 Security")],
            [(pair.old, pair.new) for pair in result.changes[0].replaced_snippets],
        )

    def test_table_row_value_changes_are_reported(self) -> None:
        """Dense table-like rows should not be discarded when numeric limits change."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_table_value.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Lane | Max jitter | Max voltage\n"
                        "Lane 0 | 0.30 UI | 800 mV\n"
                        "Lane 1 | 0.32 UI | 800 mV"
                    ),
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_table_value.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Lane | Max jitter | Max voltage\n"
                        "Lane 0 | 0.28 UI | 800 mV\n"
                        "Lane 1 | 0.32 UI | 760 mV"
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
        self.assertIn("0.30 UI", snippets)
        self.assertIn("0.28 UI", snippets)
        self.assertIn("800 mV", snippets)
        self.assertIn("760 mV", snippets)

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

    def test_numeric_thousands_separator_noise_is_suppressed(self) -> None:
        """Valid thousands separators should compare equal to plain digits."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_thousands.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nCollect 1,000 samples.")],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_thousands.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nCollect 1000 samples.")],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        self.assertEqual([], result.changes)

    def test_numeric_thousands_separator_does_not_hide_value_changes(self) -> None:
        """Comma normalization must not hide real numeric changes."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_thousands_change.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nCollect 1,000 samples.")],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_thousands_change.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nCollect 1001 samples.")],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        snippets = "\n".join(
            pair.old + "\n" + pair.new
            for change in result.changes
            for pair in change.replaced_snippets
        )

        self.assertEqual(1, len(result.changes))
        self.assertIn("1,000", snippets)
        self.assertIn("1001", snippets)

    def test_cardinal_number_words_and_digits_are_semantically_equal(self) -> None:
        """Spelled-out counts such as seven should compare equal to digits."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_number_words.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Capture seven waveforms and save them.\n"
                        "Repeat the measurement twenty-one times.\n"
                        "Retry after twenty one idle intervals.\n"
                        "Collect one hundred and five samples."
                    ),
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_number_words.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Capture 7 waveforms and save them.\n"
                        "Repeat the measurement 21 times.\n"
                        "Retry after 21 idle intervals.\n"
                        "Collect 105 samples."
                    ),
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        self.assertEqual([], result.changes)

    def test_pcie_capture_number_word_only_noise_is_suppressed(self) -> None:
        """The real PCIe seven/7 sentence shape should not change on count spelling alone."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_pcie_capture_count.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "2.13.2 Overview of Calibration Steps at 16.0 GT/s\n"
                        "39. Capture seven 2.0 million unit-interval waveforms "
                        "(2.0 X 106 X 62.5 ps = 125.0 μs) with a real time oscilloscope "
                        "and save to separate files."
                    ),
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_pcie_capture_count.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "2.13.2 Overview of Calibration Steps at 16.0 GT/s\n"
                        "39. Capture 7, 2.0 million unit-interval waveforms "
                        "(2.0 X 106 X 62.5 ps = 125.0 μs) with a real time oscilloscope "
                        "and save to separate files."
                    ),
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        self.assertEqual([], result.changes)

    def test_pcie_capture_real_wording_change_survives_number_word_noise(self) -> None:
        """Mixed PCIe sentence changes should highlight wording, not seven/7."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_pcie_capture_wording.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "2.13.2 Overview of Calibration Steps at 16.0 GT/s\n"
                        "39. Capture seven 2.0 million unit-interval waveforms "
                        "with a real time oscilloscope and save to separate files."
                    ),
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_pcie_capture_wording.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "2.13.2 Overview of Calibration Steps at 16.0 GT/s\n"
                        "39. Capture 7, 2.0 million unit-interval waveforms "
                        "with a real time oscilloscope and save them to separate files."
                    ),
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, Path(temp_dir), DiffOptions())
            report_html = outputs["html"].read_text(encoding="utf-8")

        self.assertEqual(1, len(result.changes))
        self.assertIn("save to separate files", result.changes[0].replaced_snippets[0].old)
        self.assertIn("save them to separate files", result.changes[0].replaced_snippets[0].new)
        self.assertNotIn('<mark class="del">seven</mark>', report_html)
        self.assertNotIn('<mark class="ins">7</mark>', report_html)
        self.assertIn('<mark class="ins">them</mark>', report_html)

    def test_identifier_like_number_words_and_digits_are_not_collapsed(self) -> None:
        """Number words in model, generation, section, and file contexts stay visible."""

        cases = [
            ("PCIe Gen seven mode is enabled.", "PCIe Gen 7 mode is enabled."),
            ("Use Model seven for calibration.", "Use Model 7 for calibration."),
            ("See Section seven for details.", "See Section 7 for details."),
            ("Review Table seven before testing.", "Review Table 7 before testing."),
            ("Open report seven.pdf.", "Open report 7.pdf."),
            ("Load profile-seven.csv.", "Load profile-7.csv."),
        ]
        for old_text, new_text in cases:
            with self.subTest(old=old_text, new=new_text):
                old_extraction = ExtractionResult(
                    pdf_path=Path("old_identifier_number_word.pdf"),
                    pages=[PageText(page_number=1, text=f"1 Scope\n{old_text}")],
                )
                new_extraction = ExtractionResult(
                    pdf_path=Path("new_identifier_number_word.pdf"),
                    pages=[PageText(page_number=1, text=f"1 Scope\n{new_text}")],
                )

                result = compare_extractions(old_extraction, new_extraction, DiffOptions())

                self.assertEqual(1, len(result.changes))

    def test_different_cardinal_number_words_are_still_reported(self) -> None:
        """Semantic numeric changes must not disappear just because both are words."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_number_word_change.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nCapture seven waveforms.")],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_number_word_change.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nCapture eight waveforms.")],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        snippets = "\n".join(
            pair.old + "\n" + pair.new
            for change in result.changes
            for pair in change.replaced_snippets
        )

        self.assertEqual(1, len(result.changes))
        self.assertIn("seven", snippets)
        self.assertIn("eight", snippets)

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

    def test_hyphenated_pdf_line_wrap_is_suppressed(self) -> None:
        """PDF hyphen line wraps should not create false word-level changes."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_hyphen_wrap.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text="1 Scope\nThe transmitter shall enter recovery after timeout.",
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_hyphen_wrap.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text="1 Scope\nThe trans-\nmitter shall enter recovery after timeout.",
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        self.assertEqual([], result.changes)

    def test_chinese_spacing_and_sentence_punctuation_are_suppressed(self) -> None:
        """Chinese text should ignore harmless spacing and terminal punctuation."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_chinese_spacing.pdf"),
            pages=[PageText(page_number=1, text="1 范围\n供应商应在7个工作日内交付。")],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_chinese_spacing.pdf"),
            pages=[PageText(page_number=1, text="1 范围\n供应商应在 7 个工作日内交付")],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        self.assertEqual([], result.changes)

    def test_chinese_count_words_and_digits_are_semantically_equal(self) -> None:
        """Chinese count words such as 七个 should compare equal to Arabic digits."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_chinese_count.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 范围\n"
                        "供应商应捕获七个波形并保存。\n"
                        "设备应收集一百零五个样本。"
                    ),
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_chinese_count.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 范围\n"
                        "供应商应捕获 7 个波形并保存\n"
                        "设备应收集 105 个样本"
                    ),
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        self.assertEqual([], result.changes)

    def test_chinese_count_words_before_ascii_units_are_semantically_equal(self) -> None:
        """Chinese counts before standalone ASCII units should compare equal."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_chinese_ascii_unit_count.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nCollect 七 samples before analysis.")],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_chinese_ascii_unit_count.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nCollect 7 samples before analysis.")],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        self.assertEqual([], result.changes)

    def test_chinese_number_inside_identifier_is_not_collapsed(self) -> None:
        """Chinese numerals in identifier-like words should remain visible."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_chinese_identifier.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nUse 七sampleRate for logging.")],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_chinese_identifier.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nUse 7sampleRate for logging.")],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, Path(temp_dir), DiffOptions())
            report_html = outputs["html"].read_text(encoding="utf-8")

        self.assertEqual(1, len(result.changes))
        self.assertIn('<mark class="del">七</mark>', report_html)
        self.assertIn('<mark class="ins">7</mark>', report_html)

    def test_bare_chinese_number_identifier_change_is_highlighted(self) -> None:
        """Bare Chinese numbers should not silently compare equal to digits."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_bare_chinese_number.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\n方案 一 可用。")],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_bare_chinese_number.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\n方案 1 可用。")],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, Path(temp_dir), DiffOptions())
            report_html = outputs["html"].read_text(encoding="utf-8")

        self.assertEqual(1, len(result.changes))
        self.assertIn('<mark class="del">一</mark>', report_html)
        self.assertIn('<mark class="ins">1</mark>', report_html)

    def test_chinese_count_noise_does_not_hide_real_wording_change(self) -> None:
        """Chinese seven/7 noise should not hide a real sentence edit."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_chinese_count_wording.pdf"),
            pages=[PageText(page_number=1, text="1 范围\n供应商应捕获七个波形并保存。")],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_chinese_count_wording.pdf"),
            pages=[PageText(page_number=1, text="1 范围\n供应商应捕获 7 个波形并立即保存。")],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, Path(temp_dir), DiffOptions())
            report_html = outputs["html"].read_text(encoding="utf-8")

        self.assertEqual(1, len(result.changes))
        self.assertIn("立即保存", report_html)
        self.assertNotIn('<mark class="del">七</mark>', report_html)
        self.assertNotIn('<mark class="ins">7</mark>', report_html)

    def test_different_chinese_count_words_are_still_reported(self) -> None:
        """Chinese count values must not disappear when the numeric meaning changes."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_chinese_count_change.pdf"),
            pages=[PageText(page_number=1, text="1 范围\n供应商应捕获七个波形。")],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_chinese_count_change.pdf"),
            pages=[PageText(page_number=1, text="1 范围\n供应商应捕获八个波形。")],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        snippets = "\n".join(
            pair.old + "\n" + pair.new
            for change in result.changes
            for pair in change.replaced_snippets
        )

        self.assertEqual(1, len(result.changes))
        self.assertIn("七个波形", snippets)
        self.assertIn("八个波形", snippets)

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

    def test_html_inline_highlight_deemphasizes_number_word_noise(self) -> None:
        """HTML should not highlight seven/7 when another token really changed."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_number_word_highlight.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text="1 Scope\nCapture seven waveforms and save them.",
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_number_word_highlight.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text="1 Scope\nCapture 7 waveforms and archive them.",
                )
            ],
        )
        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, Path(temp_dir), DiffOptions())
            report_html = outputs["html"].read_text(encoding="utf-8")

        self.assertNotIn('<mark class="del">seven</mark>', report_html)
        self.assertNotIn('<mark class="ins">7</mark>', report_html)
        self.assertIn('<mark class="del">save</mark>', report_html)
        self.assertIn('<mark class="ins">archive</mark>', report_html)

    def test_html_inline_highlight_keeps_identifier_number_words(self) -> None:
        """Protected identifier contexts should still highlight Gen seven/Gen 7."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_identifier_highlight.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text="1 Scope\nPCIe Gen seven mode is enabled.",
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_identifier_highlight.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text="1 Scope\nPCIe Gen 7 mode is disabled.",
                )
            ],
        )
        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, Path(temp_dir), DiffOptions())
            report_html = outputs["html"].read_text(encoding="utf-8")

        self.assertIn('<mark class="del">seven</mark>', report_html)
        self.assertIn('<mark class="ins">7</mark>', report_html)
        self.assertIn('<mark class="del">enabled</mark>', report_html)
        self.assertIn('<mark class="ins">disabled</mark>', report_html)

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
