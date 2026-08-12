"""Regression tests for the protocol PDF diff workflow.

These tests use the built-in minimal PDFs so validation does not depend on any
company document. They cover the user-facing promise: old/new PDFs are accepted,
reports are produced, and chapter/section changes are classified.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
import tempfile
import unittest
from argparse import Namespace
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import protocol_pdf_diff.pdf_extract as pdf_extract_module
from main import resolve_inputs
from protocol_pdf_diff import __version__
from protocol_pdf_diff import compare as compare_module
from protocol_pdf_diff import reporting as reporting_module
from protocol_pdf_diff.compare import (
    _is_global_noise_snippet,  # 直接覆盖报告层短碎片过滤规则。
    _merge_wrapped_lines,  # 验证 PCIe 页眉簇过滤不会吞掉正文修订历史。
    _split_line_preserving_numbers,  # 验证列表破折号粘连会拆成独立审阅句。
    compare_extractions,
    run_diff,
)
from protocol_pdf_diff.desktop_gui import (
    DesktopRunSuccess,
    ProtocolDiffDesktopApp,
    collect_widget_texts,  # 用于确认桌面界面真的渲染了关键按钮和页码标签。
    default_output_dir,  # 用于确认打包版默认输出到用户可写的文档目录。
    parse_optional_page,
    parse_positive_float,
    parse_positive_int,
    run_smoke_test,
)
from protocol_pdf_diff.models import (
    DiffOptions,
    DiffResult,
    ExtractionResult,
    PageText,
    Section,
    SectionChange,
    SnippetPair,
    TableVisual,
    VisualWatchdogAudit,
)
from protocol_pdf_diff.pdf_extract import (
    MissingDependencyError,  # 验证缺少 pdfplumber 时直接失败，而不是静默退回 pypdf。
    _clean_extracted_page_text,  # 验证抽取层先过滤页边行号、DRAFT 和版权页脚。
    _clean_table_cell,  # 验证表格数学符号残片在进入结构化事实前被修正。
    _combine_text_and_table_lines,  # 验证结构化表格行覆盖原始长表格文本后的降噪行为。
    _expanded_rows_preserve_source_alignment,  # 多列列表未展开时仍须保留 bbox 原始行序。
    _expanded_rows_preserve_source_cells,  # bbox 替换授权必须逐物理行、逐列证明无内容丢失。
    _filtered_layout_page,  # 验证页面级证据不足时不会误删孤立旋转字母。
    _geometry_compound_script_lines,  # 复合上下标候选必须双向唯一，否则整格回退。
    _keep_non_watermark_object,  # 直接验证水印过滤谓词，防止大标题被误删。
    _looks_like_figure_caption,  # 附录字母编号 Figure 也必须获得图形上下文。
    _looks_like_table_caption,  # 附录字母编号 Table 仍是真实表题。
    _should_skip_detected_table,  # 验证 Figure/空伪表格不会进入表格截图和正文 diff。
    _table_bbox_belongs_to_captioned_figure,  # 图中小线框只有坐标和图题共同证明时才跳过。
    _table_lines_from_rows,  # 直接验证 pdfplumber 表格行格式化，覆盖无需真实 PDF 的边界场景。
    _table_lines_from_rows_with_coverage,  # 结构化行覆盖不足时必须保留 bbox 原始比较文本。
    _table_row_bbox_matches_raw_cells,  # 行级替换必须精确覆盖实际会被删除的字符。
    _table_row_replacement_flags,  # 整表不安全时，只替换逐行完整结构化的数据行。
    extract_pdf_text,
)
from protocol_pdf_diff.reporting import (
    _inline_diff_html,
    _paired_table_visuals,
    _reader_pair_difference_hint,
    _reader_snippet_collapse_kind,
    _reader_snippet_text,
    write_reports,
)
from protocol_pdf_diff.sample_data import write_demo_pdfs, write_multipage_text_pdf
from protocol_pdf_diff.sectioning import detect_heading, section_document
from protocol_pdf_diff.venv_bootstrap import (  # 验证 GUI/命令行入口会优先使用项目本地 .venv。
    BOOTSTRAP_ATTEMPT_ENV,
    project_venv_python,
    project_venv_root,
    reexec_into_project_venv,
    should_reexec_into_project_venv,
)
from protocol_pdf_diff.visual_watchdog import detect_visual_review_items

OIF_OLD_SAMPLE = Path("/Users/mac/Downloads/oif2024.058.11.pdf")  # 真实回归样本旧版路径；文件不存在时测试会跳过，避免影响 CI。
OIF_NEW_SAMPLE = Path("/Users/mac/Downloads/oif2024.058.13.pdf")  # 真实回归样本新版路径；用于验证用户反馈的 OIF 表格差异。


class _VisibleHTMLTextParser(HTMLParser):
    """Collect first-view text while excluding scripts and closed detail bodies."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_tag_depth = 0
        self.closed_details_depth = 0
        self.summary_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.casefold()
        if normalized in {"style", "script"}:
            self.hidden_tag_depth += 1
        elif normalized == "details" and not any(
            key.casefold() == "open" for key, _value in attrs
        ):
            self.closed_details_depth += 1
        elif normalized == "summary":
            self.summary_depth += 1

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in {"style", "script"} and self.hidden_tag_depth:
            self.hidden_tag_depth -= 1
        elif normalized == "summary" and self.summary_depth:
            self.summary_depth -= 1
        elif normalized == "details" and self.closed_details_depth:
            self.closed_details_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.hidden_tag_depth:
            return
        if self.closed_details_depth and not self.summary_depth:
            return
        self.parts.append(data)


def _visible_html_text(value: str) -> str:
    """Return text visible before a reader expands any closed details."""

    parser = _VisibleHTMLTextParser()
    parser.feed(value)
    parser.close()
    return "".join(parser.parts)


class _FakeLayoutPage:
    """Minimal pdfplumber page used to exercise coordinate-based risk detection."""

    width = 600
    height = 800
    lines: list[object] = []
    rects: list[object] = []

    def __init__(self, words: list[dict[str, object]], text: str) -> None:
        self._words = words
        self._text = text

    def crop(self, _bbox: object) -> "_FakeLayoutPage":
        return self

    def filter(self, _predicate: object) -> "_FakeLayoutPage":
        return self

    def extract_text(self, **_kwargs: object) -> str:
        return self._text

    def extract_words(self, **_kwargs: object) -> list[dict[str, object]]:
        return self._words


class _FakePdf:
    """One-page pdfplumber document wrapper for extraction tests."""

    def __init__(self, page: _FakeLayoutPage) -> None:
        self.pages = [page]

    def close(self) -> None:
        pass


def _extract_fake_layout_page(page: _FakeLayoutPage) -> PageText:
    """Run a fake coordinate page through the normal pdfplumber entry point."""

    with tempfile.TemporaryDirectory() as temp_dir:
        pdf_path = Path(temp_dir) / "layout.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
        with mock.patch("pdfplumber.open", return_value=_FakePdf(page)):
            return extract_pdf_text(pdf_path).pages[0]


class ProtocolDiffTests(unittest.TestCase):
    """End-to-end tests over generated old/new sample PDFs."""

    def test_empty_extraction_is_indeterminate(self) -> None:
        """No comparable text or sections must never support an equality claim."""

        empty_old = ExtractionResult(pdf_path=Path("empty_old.pdf"), pages=[])
        empty_new = ExtractionResult(pdf_path=Path("empty_new.pdf"), pages=[])

        result = compare_extractions(empty_old, empty_new, DiffOptions())

        self.assertEqual("indeterminate", result.assessment.state)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_page_fallback_comparison_is_degraded(self) -> None:
        """Long text still needs manual review when stable sections are unavailable."""

        paragraph = (
            "The receiver shall preserve every compliance requirement, timing limit, "
            "measurement condition, and normative exception during interoperability review. "
        ) * 12
        old_extraction = ExtractionResult(
            pdf_path=Path("old_fallback.pdf"),
            pages=[PageText(page_number=1, text=paragraph)],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_fallback.pdf"),
            pages=[PageText(page_number=1, text=paragraph)],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        self.assertEqual("degraded", result.assessment.state)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_numbered_protocol_comparison_is_reliable(self) -> None:
        """Adequately populated numbered protocol text can support a reliable result."""

        scope_body = (
            "This specification defines normative receiver behavior, electrical limits, "
            "measurement conditions, and interoperability requirements for compliant devices. "
        ) * 8
        requirement_body = (
            "The receiver shall meet the stated voltage, timing, and calibration requirements "
            "for every supported operating mode and declared data rate. "
        ) * 8
        protocol_text = (
            f"1 Scope\n{scope_body}\n"
            f"2 Normative requirements\n{requirement_body}"
        )
        old_extraction = ExtractionResult(
            pdf_path=Path("old_numbered_protocol.pdf"),
            pages=[PageText(page_number=1, text=protocol_text)],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_numbered_protocol.pdf"),
            pages=[PageText(page_number=1, text=protocol_text)],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        self.assertEqual("reliable", result.assessment.state)
        self.assertTrue(result.assessment.allows_no_difference_conclusion)

    def test_single_populated_numbered_section_can_be_reliable(self) -> None:
        """A selected page window may intentionally contain one complete protocol clause."""

        body = (
            "The receiver shall satisfy every normative electrical timing calibration and "
            "interoperability requirement for all declared operating modes and data rates. "
        ) * 12
        extraction = ExtractionResult(
            pdf_path=Path("single_numbered_clause.pdf"),
            pages=[PageText(page_number=36, text=f"8 Receiver compliance\n{body}")],
            total_pages=120,
            selected_start_page=36,
            selected_end_page=36,
        )

        result = compare_extractions(extraction, extraction, DiffOptions())

        self.assertEqual(1, result.assessment.old_document.stable_section_count)
        self.assertEqual("reliable", result.assessment.state)
        self.assertTrue(result.assessment.allows_no_difference_conclusion)

    def test_indeterminate_report_never_claims_no_difference(self) -> None:
        """An empty/scanned self-comparison must state that equality is unknowable."""

        extraction = ExtractionResult(
            pdf_path=Path("scanned.pdf"),
            pages=[PageText(page_number=1, text="")],
            warnings=["scanned.pdf 第 1 页未提取到可比较文字。"],
            total_pages=1,
            selected_start_page=1,
            selected_end_page=1,
        )
        result = compare_extractions(extraction, extraction, DiffOptions())

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, Path(temp_dir), DiffOptions())
            report_html = outputs["html"].read_text(encoding="utf-8")

        self.assertIn("无法判断是否存在差异", report_html)
        self.assertNotIn("未发现章节级差异", report_html)

    def test_reader_reports_hide_extraction_warnings_but_json_keeps_audit_facts(self) -> None:
        """Internal extraction diagnostics should not crowd the human review report."""

        old_warning = "OLD_INTERNAL_AUDIT_SENTINEL: printed line-number grid filtered."
        new_warning = "NEW_INTERNAL_AUDIT_SENTINEL: table coordinate repair applied."
        body = (
            "The receiver shall preserve every normative timing and voltage requirement. "
            * 12
        )
        old_extraction = ExtractionResult(
            pdf_path=Path("old-layout-risk.pdf"),
            pages=[PageText(page_number=1, text=f"1 Scope\n{body}", layout_risk=True)],
            warnings=[old_warning],
            total_pages=1,
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new-layout-risk.pdf"),
            pages=[PageText(page_number=1, text=f"1 Scope\n{body}", layout_risk=True)],
            warnings=[new_warning],
            total_pages=1,
        )
        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, Path(temp_dir), DiffOptions())
            reader_outputs = {
                kind: outputs[kind].read_text(encoding="utf-8")
                for kind in ("html", "markdown", "text")
            }
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        for kind, rendered in reader_outputs.items():
            with self.subTest(kind=kind):
                self.assertNotIn("抽取警告", rendered)
                self.assertNotIn(old_warning, rendered)
                self.assertNotIn(new_warning, rendered)
                self.assertNotRegex(rendered, r"[旧新]协议有\s+\d+\s+条抽取警告")
                self.assertIn("需人工复核", rendered)
                self.assertIn("非线性阅读顺序风险页", rendered)
        self.assertEqual([old_warning, new_warning], payload["warnings"])
        self.assertEqual("degraded", payload["assessment"]["state"])
        self.assertEqual(
            [old_warning],
            payload["assessment"]["old_document"]["extraction_warnings"],
        )
        self.assertEqual(
            [new_warning],
            payload["assessment"]["new_document"]["extraction_warnings"],
        )
        self.assertIn(
            "旧协议有 1 条抽取警告。",
            payload["assessment"]["reasons"],
        )
        self.assertIn(
            "新协议有 1 条抽取警告。",
            payload["assessment"]["reasons"],
        )

    def test_warning_only_degraded_reader_keeps_trust_gate_without_diagnostics(self) -> None:
        """Hidden warning details must not turn a risky result into an all-clear."""

        body = (
            "The receiver shall preserve every normative timing and voltage requirement. "
            * 12
        )

        def extraction(name: str, warning: str) -> ExtractionResult:
            return ExtractionResult(
                pdf_path=Path(name),
                pages=[PageText(page_number=1, text=f"1 Scope\n{body}")],
                warnings=[warning],
                total_pages=1,
            )

        result = compare_extractions(
            extraction("old-warning-only.pdf", "OLD_PRIVATE_WARNING"),
            extraction("new-warning-only.pdf", "NEW_PRIVATE_WARNING"),
            DiffOptions(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            reader_text = "\n".join(
                outputs[kind].read_text(encoding="utf-8")
                for kind in ("html", "markdown", "text")
            )
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        self.assertIn("需人工复核", reader_text)
        self.assertNotIn("OLD_PRIVATE_WARNING", reader_text)
        self.assertNotIn("NEW_PRIVATE_WARNING", reader_text)
        self.assertNotIn("抽取警告", reader_text)
        self.assertEqual("degraded", payload["assessment"]["state"])
        self.assertFalse(payload["assessment"]["allows_no_difference_conclusion"])
        self.assertIn("旧协议有 1 条抽取警告。", payload["assessment"]["reasons"])
        self.assertIn("新协议有 1 条抽取警告。", payload["assessment"]["reasons"])

    def test_reader_reports_fold_linearized_layout_text_but_json_keeps_raw_evidence(self) -> None:
        """Dense table text must not become an unreadable wall in reader reports."""

        shared_body = (
            "The receiver shall preserve the normative pattern requirements and timing limits. "
            * 8
        )
        linearized_table = (
            "QPRBS13-CEI Pattern Symbols Used for Jitter Measurement Index of Index Index "
            "of Gray Coded PAM4 Label Description First Transition Last Threshold Level Symbols "
            "Symbol Begins Ends Symbol Reference REF for symbol 3333333 1 7 index R03 0 to 3 "
            "rise 10000 330 1830 1834 1835 1837 (V +V)/2 -1 1 F30 3 to 0 fall 233333 001 "
            "1269 1273 1274 1276 R12 1 to 2 rise 011111 2222221 3638 3644 3645 3651."
        )
        old_extraction = ExtractionResult(
            pdf_path=Path("old-pattern-table.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=f"1 Pattern requirements\n{shared_body}\n{linearized_table}",
                )
            ],
            total_pages=1,
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new-pattern-table.pdf"),
            pages=[PageText(page_number=1, text=f"1 Pattern requirements\n{shared_body}")],
            total_pages=1,
        )
        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            html = outputs["html"].read_text(encoding="utf-8")
            markdown = outputs["markdown"].read_text(encoding="utf-8")
            text = outputs["text"].read_text(encoding="utf-8")
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        self.assertNotIn('<details class="snippet-detail snippet-detail-layout">', html)
        self.assertIn("疑似表格或公式的版面文字已折叠", html)
        self.assertNotIn(linearized_table, html)
        self.assertIn('href="protocol_diff_data.json"', html)
        self.assertNotIn(linearized_table, markdown)
        self.assertNotIn(linearized_table, text)
        self.assertIn("疑似表格或公式的版面文字已折叠", markdown)
        self.assertIn("疑似表格或公式的版面文字已折叠", text)
        self.assertNotIn("可展开原始文字", markdown)
        self.assertNotIn("可展开原始文字", text)
        self.assertIn("完整文字见 JSON/CSV", markdown)
        self.assertIn("完整文字见 JSON/CSV", text)
        self.assertIn("QPRBS13-CEI", markdown)  # 折叠摘要仍需给出可区分的短锚点。
        self.assertIn("QPRBS13-CEI", text)
        self.assertNotIn("3644 3645 3651", markdown)  # 摘要不应重建长表格文字墙。
        self.assertNotIn("3644 3645 3651", text)
        self.assertEqual(
            [linearized_table],
            payload["changes"][0]["removed_snippets"],
        )  # 机器审计面必须无损，读者层折叠不能篡改事实模型。

    def test_reader_reports_fold_layout_text_in_added_and_replaced_paths(self) -> None:
        """Added and paired layout debris use the same collapsed reader treatment."""

        shared_body = (
            "The receiver shall preserve every normative timing and voltage requirement. "
            * 8
        )
        added_layout = (
            "Parameter Symbol Value Units Conditions Index Transition Threshold Level Label "
            "Description Reference First Last 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 "
            "17 18 19 20 21 22 23 24 100 200 300 400 500 600 700 800."
        )
        old_layout = (
            "Parameter Symbol Value Units Conditions Index Transition Threshold Level Label "
            "Description Reference First Last 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 "
            "17 18 19 20 21 22 23 24 100 200 300 400 500 600 700 810."
        )
        new_layout = old_layout.replace("810", "910")
        old_text = (
            f"1 Added layout evidence\n{shared_body}\n"
            f"2 Replaced layout evidence\n{shared_body}\n{old_layout}"
        )
        new_text = (
            f"1 Added layout evidence\n{shared_body}\n{added_layout}\n"
            f"2 Replaced layout evidence\n{shared_body}\n{new_layout}"
        )
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-layout-paths.pdf"),
                pages=[PageText(page_number=1, text=old_text)],
                total_pages=1,
            ),
            ExtractionResult(
                pdf_path=Path("new-layout-paths.pdf"),
                pages=[PageText(page_number=1, text=new_text)],
                total_pages=1,
            ),
            DiffOptions(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            html = outputs["html"].read_text(encoding="utf-8")
            markdown = outputs["markdown"].read_text(encoding="utf-8")
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        self.assertEqual(0, html.count('<details class="snippet-detail snippet-detail-layout">'))
        for raw_layout in (added_layout, old_layout, new_layout):
            self.assertNotIn(raw_layout, html)
            self.assertNotIn(raw_layout, markdown)
        self.assertGreaterEqual(markdown.count("疑似表格或公式的版面文字已折叠"), 3)
        for reader in (html, markdown):
            self.assertIn("差异锚点", reader)
            self.assertIn("810", reader)
            self.assertIn("910", reader)
        changes_by_number = {
            change["report_location"].split(maxsplit=1)[0]: change
            for change in payload["changes"]
        }
        self.assertEqual([added_layout], changes_by_number["1"]["added_snippets"])
        self.assertEqual(
            [{"old": old_layout, "new": new_layout}],
            changes_by_number["2"]["replaced_snippets"],
        )

    def test_reader_reports_fold_short_private_use_formula_fragments(self) -> None:
        """Several Symbol-font glyphs are layout debris even below the table length floor."""

        shared_body = (
            "The receiver shall preserve every normative timing and voltage requirement. "
            * 8
        )
        formula_fragment = (
            "RLcd = (0 + 1) / , x = 0.618 and Zc = 46.25 ohm."
        )
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-symbol-formula.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=f"1 Formula requirements\n{shared_body}\n{formula_fragment}",
                    )
                ],
                total_pages=1,
            ),
            ExtractionResult(
                pdf_path=Path("new-symbol-formula.pdf"),
                pages=[PageText(page_number=1, text=f"1 Formula requirements\n{shared_body}")],
                total_pages=1,
            ),
            DiffOptions(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            html = outputs["html"].read_text(encoding="utf-8")
            markdown = outputs["markdown"].read_text(encoding="utf-8")
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        self.assertNotIn('<details class="snippet-detail snippet-detail-layout">', html)
        self.assertNotIn(formula_fragment, html)
        self.assertIn('href="protocol_diff_data.json"', html)
        self.assertNotIn(formula_fragment, markdown)
        self.assertEqual([formula_fragment], payload["changes"][0]["removed_snippets"])

    def test_reader_reports_fold_long_private_glyph_formula_with_explanatory_verbs(self) -> None:
        """A dense long Symbol-font formula stays folded even when its tail contains prose verbs."""

        shared_body = (
            "The receiver shall preserve every normative timing and voltage requirement. "
            * 8
        )
        formula_fragment = (
            "SNDR = 10log10 (P1 + P2 + P3 + P4) / (N1 + N2 + "
            "N3 + N4), where k is selected from 1 2 3 4 5 6 7 8 9 10 11 12 "
            "and M should be calculated for 13 14 15 16 17 18 19 20 before the final "
            "frequency-domain measurement is reported."
        )
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-long-symbol-formula.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=f"1 Formula requirements\n{shared_body}\n{formula_fragment}",
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new-long-symbol-formula.pdf"),
                pages=[PageText(page_number=1, text=f"1 Formula requirements\n{shared_body}")],
            ),
            DiffOptions(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            html = outputs["html"].read_text(encoding="utf-8")
            markdown = outputs["markdown"].read_text(encoding="utf-8")
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        self.assertNotIn('<details class="snippet-detail snippet-detail-layout">', html)
        self.assertNotIn(formula_fragment, html)
        self.assertIn('href="protocol_diff_data.json"', html)
        self.assertNotIn(formula_fragment, markdown)
        self.assertEqual([formula_fragment], payload["changes"][0]["removed_snippets"])

    def test_reader_downgrades_exact_layout_token_reorder_but_audit_stays_modified(self) -> None:
        """Pure formula reading-order drift is review-only in reader formats."""

        old_formula = (
            "Parameter Symbol Value Units Conditions Index Transition Threshold Level Label "
            "Description Reference First Last 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 "
            "17 18 19 20 21 22 23 24 100 200 300 400 500 600 700 800"
        )
        new_formula = (
            "Value Units Parameter Symbol Conditions Index Transition Threshold Level Label "
            "Description Reference First Last 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 "
            "17 18 19 20 21 22 23 24 100 200 300 400 500 600 700 800"
        )
        old_section = Section(
            "old-1",
            "1 Formula",
            "Formula",
            1,
            ("1 Formula",),
            ("1",),
            1,
            1,
            old_formula,
        )
        new_section = Section(
            "new-1",
            "1 Formula",
            "Formula",
            1,
            ("1 Formula",),
            ("1",),
            1,
            1,
            new_formula,
        )
        change = SectionChange(
            "modified",
            old_section,
            new_section,
            0.99,
            replaced_snippets=[SnippetPair(old_formula, new_formula)],
        )
        result = DiffResult(
            Path("old-layout.pdf"),
            Path("new-layout.pdf"),
            [old_section],
            [new_section],
            [change],
            [],
            old_total_pages=1,
            new_total_pages=1,
        )

        self.assertTrue(
            reporting_module._reader_pair_is_layout_token_reorder(
                old_formula,
                new_formula,
            )
        )
        self.assertFalse(
            reporting_module._reader_pair_is_layout_token_reorder(
                "The receiver shall enable mode A before mode B.",
                "The receiver shall enable mode B before mode A.",
            )
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            html = outputs["html"].read_text(encoding="utf-8")
            markdown = outputs["markdown"].read_text(encoding="utf-8")
            text_report = outputs["text"].read_text(encoding="utf-8")
            changes_csv = outputs["csv"].read_text(encoding="utf-8-sig")
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        self.assertIn("正文字符复核项", html)
        self.assertIn("仅线性提取顺序不同", html)
        self.assertNotIn("旧“（无）”", html)
        self.assertIn("仅线性提取顺序不同", markdown)
        self.assertIn("仅线性提取顺序不同", text_report)
        self.assertIn("修改", changes_csv)
        self.assertIn("Parameter Symbol Value Units", changes_csv)
        self.assertIn("Value Units Parameter Symbol", changes_csv)
        self.assertEqual("modified", payload["changes"][0]["change_type"])

    def test_reader_reports_fold_mixed_table_axis_and_spaced_letter_layout(self) -> None:
        """A short explanatory tail cannot expose a preceding table, graph axis, or broken vertical label."""

        table_wall = (
            "Unit Conditions AC Common Mode Peak-to-Peak Voltage (Vcm) Referred to host ground "
            "Low-Frequency, VCM LF - 32 mV See Note 1 Full-Band, VCM FB - 85 mV Common Mode "
            "to Differential Mode Equation Conversion - dB See Note 2 (31-1) (SDC22) -2 dB "
            "0.25 ≤ f ≤ 4 -1.6 - 0.1*f dB 4 < f ≤ 30 Common Mode Return Loss (SCC22) - "
            "-8.5 + 0.13*f dB 30 < f ≤ min(0.8*f, 43 GHz) where f is in GHz, see Note 2 "
            "Transmitter Output Waveform absolute value of step size for all taps 0.005 0.025 "
            "value at minimum state for c(-3) - -0.06 value at maximum state for c(-2) 0.12 "
            "value at minimum state for c(-1) -0.34 value at minimum state for c(0) 0.5 "
            "value at minimum state for c(1) -0.2 Table 31-2."
        )
        graph_axis = (
            "Recommended minimum SDD21 of the end-to-end channel (for f = 106.25 GHz) "
            "0 -10 -20 -30 -40 -50 -60 -70 -80 0 10 20 30 40 50 60 70 80 90 100 "
            "freq, GHz (31-9) In addition it is recommended that the channel have an FOM less "
            "than or equal to 0.25 dB and a return loss greater than or equal to 10 dB."
        )
        spaced_vertical_label = (
            "characteristic impedance z p (3) 1.3 ohm 30 Transmission line 3 length Z c (3) "
            "100 mm 31 T T r r a a n n s s m m i i s s s s i i o o n n l l i i n n e e "
            "4 4 c le h n a g r t a h cteristic impedance z Z p c (4) 1 7."
        )

        for kind, snippet in (
            ("table", table_wall),
            ("axis", graph_axis),
            ("spaced_letters", spaced_vertical_label),
        ):
            with self.subTest(kind=kind):
                self.assertEqual("layout", _reader_snippet_collapse_kind(snippet))

        axis_reader_text = _reader_snippet_text(graph_axis)
        self.assertIn("可读正文", axis_reader_text)
        self.assertIn("FOM less than or equal to 0.25 dB", axis_reader_text)
        self.assertIn("return loss greater than or equal to 10 dB", axis_reader_text)
        axis_html = reporting_module._render_collapsible_snippet_html(
            graph_axis,
            reporting_module._escape(graph_axis),
        )
        self.assertIn("snippet-visible-tail", axis_html)
        self.assertIn("0.25 dB", axis_html)

    def test_reader_layout_detection_covers_figure_axes_and_nonsequential_letters(self) -> None:
        """Long visual runs stay folded without confusing monotonic lane labels."""

        figure_axis = (
            "Figure 31-8 measured response "
            + " ".join(str(value) for value in range(60))
            + " This is the measured curve used for the graphical review of the channel response."
        )
        vertical_label = (
            "T r a n s m i s s i o n l i n e c h a r a c t e r i s t i c "
            "T r a n s m i s s i o n l i n e c h a r a c t e r i s t i c "
            "1 2 3 4 5 6 7 8"
        )

        self.assertEqual("layout", _reader_snippet_collapse_kind(figure_axis))
        self.assertIn("This is the measured curve", _reader_snippet_text(figure_axis))
        self.assertEqual("layout", _reader_snippet_collapse_kind(vertical_label))

    def test_reader_folds_pattern_table_wall_with_normative_tail(self) -> None:
        """A final EOJ sentence stays visible without disabling folding of the table prefix."""

        header = (
            "Label Description Gray Coded PAM4 Symbols Index First Symbol Index Transition "
            "Begins Index Transition Ends Index Last Symbol Threshold Level Reference "
        )
        numeric_rows = " ".join(
            f"R03 {index} 3 10000 330 1830 1834 1835 1837 1 3 7"
            for index in range(1, 18)
        )
        tail = (
            "It is acceptable to meet the EOJ requirement with either QPRBS13 or "
            "QPRBS9 test pattern."
        )
        second_tail = (
            "The receiver shall record this requirement for every supported "
            "operating mode."
        )

        for prose_tail in (tail, f"{tail} {second_tail}"):
            with self.subTest(sentence_count=prose_tail.count(".") ):
                snippet = f"QPRBS13 CEI Pattern {header}{numeric_rows}. {prose_tail}"
                self.assertEqual("layout", _reader_snippet_collapse_kind(snippet))
                reader = _reader_snippet_text(snippet)
                self.assertIn("已折叠", reader)
                self.assertIn(prose_tail, reader)
                self.assertNotIn(numeric_rows, reader)

    def test_reader_keeps_dense_header_vocabulary_in_normative_clause_expanded(self) -> None:
        """A clear shall-clause is not a table merely because it lists schema fields and profiles."""

        clause = (
            "For profiles 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 "
            "22 23 24 25, the Parameter, Symbol, Value, Units, Conditions, Index, Transition, "
            "Threshold, Level, Label, Description, and Reference shall be recorded and verified "
            "for every receiver configuration before the implementation is released."
        )

        self.assertIsNone(_reader_snippet_collapse_kind(clause))
        self.assertEqual(clause, _reader_snippet_text(clause))

    def test_reader_keeps_normative_insertion_loss_axis_sentence_expanded(self) -> None:
        """Frequency terms and a long numeric run do not override a sentence-level shall."""

        body = (
            "The transmitter shall meet insertion loss at frequency values "
            + " ".join(str(value) for value in range(1, 31))
            + " GHz, and shall retain every measured requirement and review record "
            "for all supported operating modes and channels."
        )

        for prefix in ("", "1. ", "Clause31 "):
            with self.subTest(prefix=prefix):
                clause = f"{prefix}{body}"
                self.assertGreaterEqual(len(clause), 250)
                self.assertIsNone(_reader_snippet_collapse_kind(clause))
                self.assertEqual(clause, _reader_snippet_text(clause))

    def test_reader_folds_very_long_table_cells_but_audit_keeps_full_values(self) -> None:
        """NOTES rows are bounded in HTML/MD/TXT while JSON/CSV facts stay lossless."""

        old_value = "OLD NOTES " + ("receiver requirement and measurement condition 0.118 UI; " * 24)
        new_value = old_value.replace("0.118 UI", "0.135 UI", 1) + (
            "additional implementation guidance; " * 12
        )
        item = "NOTES " + ("full explanatory condition and reference; " * 18)
        row_change = reporting_module.TableRowChange(
            item=item,
            old_value=old_value,
            new_value=new_value,
            change_type="实质变化",
        )
        old_table = TableVisual(
            page_number=1,
            table_number=1,
            title="Table 1-1. Receiver Notes",
            bbox=(0.0, 0.0, 1.0, 1.0),
            image_data_uri="",
            row_texts=[],
            grid_summary="",
        )
        new_table = TableVisual(
            page_number=1,
            table_number=1,
            title="Table 1-1. Receiver Notes",
            bbox=(0.0, 0.0, 1.0, 1.0),
            image_data_uri="",
            row_texts=[],
            grid_summary="",
        )
        table_change = reporting_module.TableChange(
            change_type="modified",
            old_tables=(old_table,),
            new_tables=(new_table,),
            similarity=0.9,
            caption_changed=False,
            row_changes=(row_change,),
        )
        shared = "The receiver shall preserve every normative requirement. " * 10
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-long-notes.pdf"),
                pages=[PageText(page_number=1, text=f"1 Scope\n{shared}")],
            ),
            ExtractionResult(
                pdf_path=Path("new-long-notes.pdf"),
                pages=[PageText(page_number=1, text=f"1 Scope\n{shared}")],
            ),
            DiffOptions(),
        )

        html = reporting_module._render_table_row_change(row_change)
        markdown = reporting_module._render_markdown(
            result,
            DiffOptions(),
            [table_change],
        )
        html_summary = html.split("</summary>", 1)[0]
        json_fact = reporting_module._table_change_to_dict(table_change)
        csv_fact = reporting_module._rows_for_table_csv([table_change])[0]

        self.assertIn('details class="table-cell-detail"', html)
        self.assertIn("超长单元格已折叠", html_summary)
        self.assertNotIn(old_value, html_summary)
        self.assertNotIn(new_value, html_summary)
        self.assertIn("超长单元格已折叠", markdown)
        self.assertNotIn(old_value, markdown)
        self.assertNotIn(new_value, markdown)
        self.assertEqual(old_value, json_fact["row_changes"][0]["old_value"])
        self.assertEqual(new_value, json_fact["row_changes"][0]["new_value"])
        self.assertEqual(old_value, csv_fact["old_value"])
        self.assertEqual(new_value, csv_fact["new_value"])

    def test_reader_difference_hint_prioritizes_numeric_limit_and_reports_omissions(self) -> None:
        """Three earlier word insertions cannot crowd a later engineering limit out of the summary."""

        old = (
            "Parameter A shared-one B shared-two C shared-three limit 0.118 UI "
            "1 2 3 4 5 6 7 8 9 10"
        )
        new = (
            "Parameter insertA A shared-one insertB B shared-two insertC C shared-three "
            "limit 0.135 UI 1 2 3 4 5 6 7 8 9 10"
        )

        hint = _reader_pair_difference_hint(old, new)

        self.assertIn("0.118", hint)
        self.assertIn("0.135", hint)
        self.assertIn("共 4 处", hint)
        self.assertIn("显示 3 处", hint)

    def test_reader_reports_fold_short_dense_equation_layout(self) -> None:
        """A compact graph equation with broken glyphs is still unreadable layout evidence."""

        equation = (
            "SDD21 = 0.041 – 0.002*f + 0.013*f/2 – 0.004*f/3 + 0.009*f/4 "
            "– 0.007*f/5 + 0.003*f/6 – 0.001*f/7 for 1 2 3 4 5 6 7 8 9 10 "
            "Figure 31-8."
        )

        self.assertEqual("layout", _reader_snippet_collapse_kind(equation))

    def test_folded_layout_pair_keeps_equation_number_change_visible(self) -> None:
        """Folding a graph wall must not hide the actual 31-9 to 31-10 change."""

        old_axis = (
            "Recommended minimum SDD21 of the end-to-end channel (for f = 106.25 GHz) "
            "0 -10 -20 -30 -40 -50 -60 -70 -80 0 10 20 30 40 50 60 70 80 90 100 "
            "freq, GHz (31-9) In addition it is recommended that the channel have an FOM less "
            "than or equal to 0.25 dB and a return loss greater than or equal to 10 dB."
        )
        new_axis = old_axis.replace("(31-9)", "(31-10)")
        shared_body = "The receiver shall preserve the declared operating conditions. " * 10
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-axis-number.pdf"),
                pages=[PageText(page_number=1, text=f"1 Axis\n{shared_body}\n{old_axis}")],
            ),
            ExtractionResult(
                pdf_path=Path("new-axis-number.pdf"),
                pages=[PageText(page_number=1, text=f"1 Axis\n{shared_body}\n{new_axis}")],
            ),
            DiffOptions(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            readers = [
                outputs[kind].read_text(encoding="utf-8")
                for kind in ("html", "markdown", "text")
            ]

        for reader in readers:
            self.assertIn("差异锚点", reader)
            self.assertIn("31-9", reader)
            self.assertIn("31-10", reader)

    def test_folded_axis_pair_keeps_readable_requirement_tail_visible(self) -> None:
        """A graph wall may fold, but its trailing FOM/return-loss requirement stays readable."""

        old_axis = (
            "Recommended minimum SDD21 of the end-to-end channel (for f = 106.25 GHz) "
            "0 -10 -20 -30 -40 -50 -60 -70 -80 0 10 20 30 40 50 60 70 80 90 100 "
            "freq, GHz (31-9) In addition it is recommended that the channel have an FOM less "
            "than or equal to 0.25 dB and a return loss greater than or equal to 10 dB."
        )
        new_axis = old_axis.replace("0.25 dB", "0.30 dB")
        shared_body = "The channel shall preserve all other declared requirements. " * 10
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-axis-tail.pdf"),
                pages=[PageText(page_number=1, text=f"1 Axis\n{shared_body}\n{old_axis}")],
            ),
            ExtractionResult(
                pdf_path=Path("new-axis-tail.pdf"),
                pages=[PageText(page_number=1, text=f"1 Axis\n{shared_body}\n{new_axis}")],
            ),
            DiffOptions(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            html = outputs["html"].read_text(encoding="utf-8")
            markdown = outputs["markdown"].read_text(encoding="utf-8")
            text = outputs["text"].read_text(encoding="utf-8")

        self.assertIn("snippet-visible-tail", html)
        for reader in (html, markdown, text):
            self.assertIn("FOM less than or equal to 0.25 dB", reader)
            self.assertIn("FOM less than or equal to 0.30 dB", reader)
            self.assertIn("return loss greater than or equal to 10 dB", reader)
        self.assertNotIn(old_axis, markdown)
        self.assertNotIn(new_axis, markdown)

    def test_reader_reports_keep_normative_numeric_and_letter_sequences_expanded(self) -> None:
        """Enumerated modes and lanes with prose verbs are not graph axes or vertical labels."""

        numeric_clause = (
            "The receiver shall test modes 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 "
            "17 18 19 20 21 22 23 24 and shall preserve every recorded result, "
            "calibration setting, review decision, and traceable approval for release."
        )
        lane_clause = (
            "The implementation must verify lanes A B C D E F G H I J K L M N O P Q R S T "
            "U V for profiles 1 2 3 4 5 6 and must retain the receiver configuration, "
            "measured limits, exception log, and final approval for every lane."
        )

        self.assertIsNone(_reader_snippet_collapse_kind(numeric_clause))
        self.assertIsNone(_reader_snippet_collapse_kind(lane_clause))

    def test_reader_reports_keep_single_private_use_symbol_parameter_expanded(self) -> None:
        """One meaningful Symbol-font parameter is not enough to hide a concise limit edit."""

        shared_body = (
            "The receiver shall preserve every normative timing and voltage requirement. "
            * 8
        )
        old_limit = "Transmission line parameter  shall be 6.141×10-3 ns/mm."
        new_limit = "Transmission line parameter  shall be 6.200×10-3 ns/mm."
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-symbol-parameter.pdf"),
                pages=[PageText(page_number=1, text=f"1 Channel model\n{shared_body}\n{old_limit}")],
                total_pages=1,
            ),
            ExtractionResult(
                pdf_path=Path("new-symbol-parameter.pdf"),
                pages=[PageText(page_number=1, text=f"1 Channel model\n{shared_body}\n{new_limit}")],
                total_pages=1,
            ),
            DiffOptions(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            html = outputs["html"].read_text(encoding="utf-8")
            markdown = outputs["markdown"].read_text(encoding="utf-8")
            text_report = outputs["text"].read_text(encoding="utf-8")
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        self.assertNotIn('<details class="snippet-detail', html)
        for rendered in (html, markdown, text_report):
            self.assertIn("τ", rendered)
            self.assertNotIn("\uf074", rendered)
            self.assertIn("6.141", rendered)
            self.assertIn("6.200", rendered)
        for rendered in (markdown, text_report):
            self.assertIn("6.141×10-3", rendered)
            self.assertIn("6.200×10-3", rendered)
        self.assertIn("\uf074", json.dumps(payload, ensure_ascii=False))

    def test_reader_reports_keep_header_terms_in_normative_prose_expanded(self) -> None:
        """Table-like vocabulary alone cannot hide a readable normative limit change."""

        shared_body = (
            "The receiver shall preserve every normative timing and voltage requirement. "
            * 8
        )
        old_clause = (
            "For each declared receiver mode, the parameter symbol, value, units, conditions, "
            "and threshold shall be verified, profiles 1, 2, 3, 4, 5, 6, 7, 8, 9, and 10 "
            "require the JH4u limit "
            "to be 0.118 UI while the reference clock remains unchanged and the documented "
            "calibration procedure is preserved."
        )
        new_clause = old_clause.replace("0.118 UI", "0.135 UI")
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-readable-header-terms.pdf"),
                pages=[PageText(page_number=1, text=f"1 Jitter review\n{shared_body}\n{old_clause}")],
            ),
            ExtractionResult(
                pdf_path=Path("new-readable-header-terms.pdf"),
                pages=[PageText(page_number=1, text=f"1 Jitter review\n{shared_body}\n{new_clause}")],
            ),
            DiffOptions(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            html = outputs["html"].read_text(encoding="utf-8")
            markdown = outputs["markdown"].read_text(encoding="utf-8")
            text = outputs["text"].read_text(encoding="utf-8")

        self.assertNotIn('<details class="snippet-detail', html)
        for reader in (markdown, text):
            self.assertIn("0.118 UI", reader)
            self.assertIn("0.135 UI", reader)

    def test_reader_reports_keep_readable_private_glyph_formula_limit_expanded(self) -> None:
        """A compact readable equation with a normative verb keeps its changed values visible."""

        shared_body = (
            "The receiver shall preserve every normative timing and voltage requirement. "
            * 8
        )
        old_formula = (
            "RLcd = (0 + 1) / , x = 0.618 and Zc shall change from "
            "46.25 ohm to 46.25 ohm."
        )
        new_formula = old_formula.replace("to 46.25 ohm", "to 47.50 ohm")
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-readable-formula.pdf"),
                pages=[PageText(page_number=1, text=f"1 Return loss\n{shared_body}\n{old_formula}")],
            ),
            ExtractionResult(
                pdf_path=Path("new-readable-formula.pdf"),
                pages=[PageText(page_number=1, text=f"1 Return loss\n{shared_body}\n{new_formula}")],
            ),
            DiffOptions(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            html = outputs["html"].read_text(encoding="utf-8")
            markdown = outputs["markdown"].read_text(encoding="utf-8")
            text = outputs["text"].read_text(encoding="utf-8")

        self.assertNotIn('<details class="snippet-detail', html)
        for reader in (markdown, text):
            self.assertIn("46.25 ohm", reader)
            self.assertIn("47.50 ohm", reader)

    def test_reader_reports_keep_number_dense_chinese_requirement_expanded(self) -> None:
        """Chinese normative verbs also protect readable numeric prose from folding."""

        shared_body = "接收机的其余校准流程和工作模式在本次修订中保持不变。" * 18
        old_clause = (
            "接收机应依次验证模式 1 2 3 4 5 6 7 8 9 10 11 12，并且必须将 JH4u "
            "限值设为 0.118 UI，本要求适用于所有声明的数据速率和测试条件，实施方应记录"
            "每个模式的测量结果、判定依据、设备配置以及复核结论，任何单一模式均不得省略，"
            "复核人员还应确认校准状态、环境条件、参考时钟来源、接收机配置、测试夹具版本和最终批准记录"
            "均保持完整并可追溯。"
        )
        new_clause = old_clause.replace("0.118 UI", "0.135 UI")
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-chinese-requirement.pdf"),
                pages=[PageText(page_number=1, text=f"1 抖动要求\n{shared_body}\n{old_clause}")],
            ),
            ExtractionResult(
                pdf_path=Path("new-chinese-requirement.pdf"),
                pages=[PageText(page_number=1, text=f"1 抖动要求\n{shared_body}\n{new_clause}")],
            ),
            DiffOptions(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            html = outputs["html"].read_text(encoding="utf-8")
            markdown = outputs["markdown"].read_text(encoding="utf-8")

        self.assertNotIn('<details class="snippet-detail', html)
        self.assertIn("0.118 UI", markdown)
        self.assertIn("0.135 UI", markdown)

    def test_reader_reports_keep_short_numeric_identifier_changes_expanded(self) -> None:
        """A concise protocol limit stays immediately visible instead of being over-folded."""

        shared_body = (
            "The receiver shall preserve every normative timing and voltage requirement. "
            * 8
        )
        old_extraction = ExtractionResult(
            pdf_path=Path("old-jitter-limit.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        f"1 Jitter requirements\n{shared_body}\n"
                        "The JH4u limit shall be 0.118 UI."
                    ),
                )
            ],
            total_pages=1,
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new-jitter-limit.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        f"1 Jitter requirements\n{shared_body}\n"
                        "The JH4u limit shall be 0.135 UI."
                    ),
                )
            ],
            total_pages=1,
        )
        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            html = outputs["html"].read_text(encoding="utf-8")
            markdown = outputs["markdown"].read_text(encoding="utf-8")

        self.assertNotIn('<details class="snippet-detail', html)
        self.assertIn("JH4u", html)
        self.assertIn("0.118", markdown)
        self.assertIn("0.135", markdown)

    def test_reader_reports_keep_long_prose_limit_changes_expanded(self) -> None:
        """Length alone must not hide a real limit change inside normative prose."""

        repeated_context = (
            "the implementation shall preserve the normative receiver behavior, calibration "
            "conditions, interoperability requirements, exception handling, and traceable review "
        ) * 3
        old_requirement = (
            f"For every declared operating mode, {repeated_context}and complete final review "
            "within 15 days."
        )
        new_requirement = old_requirement.replace("15 days", "20 days")
        old_extraction = ExtractionResult(
            pdf_path=Path("old-long-requirement.pdf"),
            pages=[PageText(page_number=1, text=f"1 Review requirement\n{old_requirement}")],
            total_pages=1,
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new-long-requirement.pdf"),
            pages=[PageText(page_number=1, text=f"1 Review requirement\n{new_requirement}")],
            total_pages=1,
        )
        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            html = outputs["html"].read_text(encoding="utf-8")
            markdown = outputs["markdown"].read_text(encoding="utf-8")

        self.assertNotIn('<details class="snippet-detail', html)
        self.assertIn("15", markdown)
        self.assertIn("20", markdown)
        self.assertIn("complete final review", markdown)

    def test_reader_hides_numbered_reference_prose_but_audit_keeps_it(self) -> None:
        """长正文中的纯 Section 引用列表变化也只保留在审计层。"""

        shared_body = (
            "The review process and receiver scope remain unchanged for this revision. "
            * 8
        )
        old_clause = (
            "Before release, the reviewer shall verify Sections 31.1, 31.2, 31.3, 31.4, "
            "31.5, 31.6, 31.7, and 31.8, document the result for every referenced requirement, "
            "and obtain approval from the responsible receiver engineer."
        )
        new_clause = old_clause.replace("31.8", "31.9")
        old_extraction = ExtractionResult(
            pdf_path=Path("old-numbered-prose.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=f"1 Review requirements\n{shared_body}\n{old_clause}",
                )
            ],
            total_pages=1,
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new-numbered-prose.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=f"1 Review requirements\n{shared_body}\n{new_clause}",
                )
            ],
            total_pages=1,
        )
        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            html = outputs["html"].read_text(encoding="utf-8")
            markdown = outputs["markdown"].read_text(encoding="utf-8")
            text = outputs["text"].read_text(encoding="utf-8")
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        # 引用清单即使位于完整规范句中，也不再进入三种读者报告的技术差异区。
        for reader in (markdown, text):
            self.assertNotIn("31.8", reader)
            self.assertNotIn("31.9", reader)
        self.assertNotIn("31.8", _visible_html_text(html))
        self.assertNotIn("31.9", _visible_html_text(html))
        # JSON 仍输出完整规范句，证明长引用列表没有从原始比较事实中丢失。
        self.assertEqual(
            [{"old": old_clause, "new": new_clause}],
            payload["changes"][0]["replaced_snippets"],
        )

    def test_reports_include_assessment_and_reproducibility_provenance(self) -> None:
        """Every report should expose trust state and JSON should reproduce the run."""

        body = (
            "The receiver shall meet every normative voltage, timing, calibration, and "
            "interoperability requirement for each declared data rate. "
        ) * 10
        options = DiffOptions(
            min_section_match_similarity=0.81,
            unchanged_similarity=0.991,
            max_snippets_per_section=7,
            old_start_page=2,
            old_end_page=3,
            new_start_page=5,
            new_end_page=6,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_path = temp_path / "old.pdf"
            new_path = temp_path / "new.pdf"
            old_bytes = b"old reproducible source"
            new_bytes = b"new reproducible source"
            old_path.write_bytes(old_bytes)
            new_path.write_bytes(new_bytes)
            old_extraction = ExtractionResult(
                pdf_path=old_path,
                pages=[
                    PageText(page_number=2, text=f"1 Scope\n{body}"),
                    PageText(page_number=3, text=f"2 Requirements\n{body}"),
                ],
                total_pages=10,
                selected_start_page=2,
                selected_end_page=3,
                source_sha256=hashlib.sha256(old_bytes).hexdigest(),
            )
            new_extraction = ExtractionResult(
                pdf_path=new_path,
                pages=[
                    PageText(page_number=5, text=f"1 Scope\n{body}"),
                    PageText(page_number=6, text=f"2 Requirements\n{body}"),
                ],
                total_pages=12,
                selected_start_page=5,
                selected_end_page=6,
                source_sha256=hashlib.sha256(new_bytes).hexdigest(),
            )
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("PROTOCOL_PDF_DIFF_BUILD_COMMIT", None)
                result = compare_extractions(old_extraction, new_extraction, options)
            outputs = write_reports(result, temp_path / "reports", options)
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
            report_html = outputs["html"].read_text(encoding="utf-8")
            report_md = outputs["markdown"].read_text(encoding="utf-8")
            report_txt = outputs["text"].read_text(encoding="utf-8")

        self.assertIn("识别可信度：可靠", report_html)
        self.assertIn("## 识别可信度", report_md)
        self.assertIn("识别可信度", report_txt)
        self.assertEqual("reliable", payload["assessment"]["state"])
        provenance = payload["provenance"]
        self.assertEqual(__version__, provenance["package_version"])
        self.assertIsNone(provenance["build_commit"])
        self.assertIn("稳定编号章节", provenance["supported_profile"])
        self.assertEqual(hashlib.sha256(old_bytes).hexdigest(), provenance["inputs"]["old"]["sha256"])
        self.assertEqual(hashlib.sha256(new_bytes).hexdigest(), provenance["inputs"]["new"]["sha256"])
        self.assertEqual(
            {"start_page": 2, "end_page": 3},
            provenance["inputs"]["old"]["selected_page_window"],
        )
        self.assertEqual(
            {"start_page": 5, "end_page": 6},
            provenance["inputs"]["new"]["selected_page_window"],
        )
        self.assertEqual(0.81, provenance["effective_thresholds"]["min_section_match_similarity"])
        self.assertNotIn("unchanged_similarity", provenance["effective_thresholds"])
        self.assertEqual(7, provenance["effective_thresholds"]["max_snippets_per_section"])
        self.assertEqual(
            8,
            provenance["effective_thresholds"][
                "ocr_native_text_vertical_band_count"
            ],
        )
        self.assertEqual(
            0.5,
            provenance["effective_thresholds"][
                "ocr_minimum_native_text_vertical_band_coverage"
            ],
        )
        self.assertEqual(
            40,
            provenance["effective_thresholds"][
                "quality_min_lines_for_fragmentation_check"
            ],
        )
        self.assertEqual(
            4.0,
            provenance["effective_thresholds"][
                "quality_max_average_characters_per_fragmented_line"
            ],
        )
        self.assertEqual(
            0.5,
            provenance["effective_thresholds"][
                "quality_min_single_character_line_ratio"
            ],
        )

    def test_provenance_hash_is_bound_to_the_pdf_snapshot_actually_parsed(self) -> None:
        """Replacing a path after extraction must not rewrite the reported input digest."""

        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "source.pdf"
            write_multipage_text_pdf(
                pdf_path,
                [["1 Scope", "The receiver shall preserve the parsed source bytes."]],
            )
            parsed_bytes = pdf_path.read_bytes()
            extraction = extract_pdf_text(pdf_path)
            parsed_digest = hashlib.sha256(parsed_bytes).hexdigest()
            pdf_path.write_bytes(b"a different file now occupies the same path")

            result = compare_extractions(extraction, extraction, DiffOptions())

        self.assertEqual(parsed_digest, extraction.source_sha256)
        self.assertIsNotNone(result.provenance)
        assert result.provenance is not None
        self.assertEqual(parsed_digest, result.provenance.old_input.sha256)
        self.assertEqual(parsed_digest, result.provenance.new_input.sha256)

    def test_visual_watchdog_pixels_cannot_come_from_bytes_after_extraction_snapshot(self) -> None:
        """Replacing a path after extraction must fail closed instead of forging visual evidence."""

        body = "The receiver shall preserve every calibrated operating requirement. " * 5
        pages = [[f"{index} Requirement {index}", body] for index in range(1, 8)]
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(temp_path / "old_snapshot.pdf", pages)
            new_pdf = write_multipage_text_pdf(temp_path / "new_snapshot.pdf", pages)
            extraction_calls = 0

            def extract_then_replace_path(*args: object, **kwargs: object) -> ExtractionResult:
                nonlocal extraction_calls
                extraction = extract_pdf_text(*args, **kwargs)
                extraction_calls += 1
                if extraction_calls == 2:
                    write_multipage_text_pdf(
                        old_pdf,
                        pages,
                        decorative_marks={4: "new"},
                    )
                return extraction

            with mock.patch(
                "protocol_pdf_diff.compare.extract_pdf_text",
                side_effect=extract_then_replace_path,
            ):
                result = run_diff(old_pdf, new_pdf, DiffOptions())

        self.assertEqual([], result.changes)
        self.assertEqual([], result.visual_review_items)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)
        self.assertFalse(result.provenance.visual_watchdog_audit.source_hashes_match)

    def test_each_quality_risk_degrades_without_discarding_sections(self) -> None:
        """Low text, warnings, empty pages, and layout risk are review signals only."""

        long_body = (
            "The transmitter shall preserve normative voltage timing calibration and "
            "interoperability behavior for every declared operating mode. "
        ) * 10
        variants = {
            "low_text": ExtractionResult(
                pdf_path=Path("low_text.pdf"),
                pages=[PageText(page_number=1, text="1 Scope\nShort requirement text.")],
            ),
            "warning": ExtractionResult(
                pdf_path=Path("warning.pdf"),
                pages=[PageText(page_number=1, text=f"1 Scope\n{long_body}")],
                warnings=["Page 1 text extraction was incomplete."],
            ),
            "empty_page_ratio": ExtractionResult(
                pdf_path=Path("empty_page_ratio.pdf"),
                pages=[
                    PageText(page_number=1, text=f"1 Scope\n{long_body}"),
                    PageText(page_number=2, text=""),
                    PageText(page_number=3, text=f"2 Requirements\n{long_body}"),
                    PageText(page_number=4, text=f"3 Compliance\n{long_body}"),
                ],
            ),
            "layout_risk": ExtractionResult(
                pdf_path=Path("layout_risk.pdf"),
                pages=[PageText(page_number=1, text=f"1 Scope\n{long_body}", layout_risk=True)],
            ),
        }

        for risk, extraction in variants.items():
            with self.subTest(risk=risk):
                expected_sections = section_document(extraction)
                result = compare_extractions(extraction, extraction, DiffOptions())
                self.assertEqual("degraded", result.assessment.state)
                self.assertEqual(expected_sections, result.old_sections)
                self.assertEqual(extraction.warnings, list(result.assessment.old_document.extraction_warnings))

    def test_extreme_glyph_fragmentation_degrades_slide_like_extraction(self) -> None:
        """Thousands of one-glyph lines are text-extraction failure, not reliable prose."""

        fragmented_text = "1 Signal processing\n" + "\n".join("x" for _ in range(700))
        extraction = ExtractionResult(
            pdf_path=Path("fragmented_slides.pdf"),
            pages=[PageText(page_number=1, text=fragmented_text)],
        )

        result = compare_extractions(extraction, extraction, DiffOptions())

        self.assertEqual((1,), result.assessment.old_document.fragmented_text_pages)
        self.assertEqual("degraded", result.assessment.state)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_degraded_empty_report_does_not_confirm_consistency(self) -> None:
        """No detected fallback changes must still be presented as inconclusive."""

        paragraph = (
            "The receiver shall preserve every compliance requirement and measurement "
            "condition during interoperability review. "
        ) * 12
        extraction = ExtractionResult(
            pdf_path=Path("fallback.pdf"),
            pages=[PageText(page_number=1, text=paragraph)],
        )
        result = compare_extractions(extraction, extraction, DiffOptions())

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, Path(temp_dir), DiffOptions())
            report_html = outputs["html"].read_text(encoding="utf-8")
            report_md = outputs["markdown"].read_text(encoding="utf-8")

        self.assertIn("未检出差异，但不能据此确认一致", report_html)
        self.assertIn("未检出差异，但不能据此确认一致", report_md)
        self.assertNotIn("未发现章节级差异", report_html)

    def test_entry_points_bootstrap_project_venv_before_heavy_imports(self) -> None:
        """GUI and PyCharm entry points should enter .venv before PDF/Tk imports."""

        with tempfile.TemporaryDirectory() as temp_dir:  # 临时项目根目录用于模拟不同解释器前缀。
            project_root = Path(temp_dir)  # 把临时目录当作一个最小项目根目录。
            expected_python = project_venv_python(project_root)  # 计算当前平台应该使用的 .venv Python。
            expected_python.parent.mkdir(parents=True)  # 创建 bin 或 Scripts 目录，模拟已初始化 .venv。
            expected_python.write_text("# fake python\n", encoding="utf-8")  # 写一个占位文件，让存在性判断通过。
            outside_prefix = project_root / "external-python"  # 模拟 PyCharm/Anaconda 传入的错误解释器前缀。
            outside_prefix.mkdir()  # 创建错误前缀目录，保证 resolve 后路径稳定。
            active_venv_prefix = project_root / ".venv"  # 模拟已经位于项目 .venv 的正确前缀。

            self.assertTrue(
                should_reexec_into_project_venv(project_root, outside_prefix)
            )  # 错误解释器必须触发重启，否则会再次缺 pdfplumber。
            self.assertFalse(
                should_reexec_into_project_venv(project_root, active_venv_prefix)
            )  # 已经在项目 .venv 时不能重复重启，避免无限循环。
            with mock.patch.dict(
                os.environ,
                {BOOTSTRAP_ATTEMPT_ENV: str(project_venv_root(project_root).resolve())},
            ):  # 模拟上一轮已经尝试过进入同一个 .venv。
                self.assertFalse(
                    should_reexec_into_project_venv(project_root, outside_prefix)
                )  # 损坏 .venv 导致 sys.prefix 仍不对时，也不能无限重复 exec。
            with mock.patch.dict(os.environ, {}, clear=False):  # 保留系统环境，只临时检查 exec 参数。
                os.environ.pop(BOOTSTRAP_ATTEMPT_ENV, None)  # 确保测试从“未尝试重启”的状态开始。
                with mock.patch.object(sys, "argv", ["gui_app.py", "--smoke-test"]):  # 模拟用户启动 GUI 自测。
                    with mock.patch("os.execv", side_effect=RuntimeError("exec-called")) as execv_mock:
                        with self.assertRaisesRegex(RuntimeError, "exec-called"):
                            reexec_into_project_venv(project_root, project_root / "gui_app.py")
                execv_mock.assert_called_once()  # 确认确实会替换进程，而不是继续用错误解释器。
                exec_path, exec_argv = execv_mock.call_args.args  # 读取 execv 的目标和参数。
                self.assertEqual(str(expected_python), exec_path)  # exec 目标必须是项目 .venv 的 Python。
                self.assertEqual(str(expected_python), exec_argv[0])  # argv[0] 也应指向 .venv Python。
                self.assertEqual(str(project_root / "gui_app.py"), exec_argv[1])  # 保留原入口脚本路径。
                self.assertEqual("--smoke-test", exec_argv[2])  # 保留用户传入的命令行参数。
                self.assertEqual(
                    str(project_venv_root(project_root).resolve()),
                    os.environ[BOOTSTRAP_ATTEMPT_ENV],
                )  # exec 前设置一次性保护标记，防止坏 .venv 循环。

        gui_source = (PROJECT_ROOT / "gui_app.py").read_text(encoding="utf-8")  # 读取 GUI 入口源码检查启动顺序。
        main_source = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")  # 读取 PyCharm 入口源码检查启动顺序。
        build_source = (PROJECT_ROOT / "build_desktop.py").read_text(encoding="utf-8")  # 读取打包入口源码检查解释器顺序。
        self.assertLess(
            gui_source.index("reexec_into_project_venv("),
            gui_source.index("from protocol_pdf_diff.desktop_gui"),
        )  # GUI 必须先切 .venv，再导入 Tkinter 相关模块。
        self.assertLess(
            main_source.index("reexec_into_project_venv("),
            main_source.index("from protocol_pdf_diff.compare"),
        )  # 命令行入口必须先切 .venv，再导入 pdfplumber 相关链路。
        self.assertLess(
            build_source.index("reexec_into_project_venv("),
            build_source.index("import PyInstaller.__main__"),
        )  # 打包入口必须先切 .venv，再导入 PyInstaller 和运行依赖检查。

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

    def test_desktop_success_copy_exposes_reliability_state(self) -> None:
        """The desktop summary must not flatten risky runs into plain completion."""

        long_body = (
            "The receiver shall meet every normative voltage timing calibration and "
            "interoperability requirement for each declared operating mode. "
        ) * 10
        cases = [
            (
                ExtractionResult(
                    pdf_path=Path("reliable.pdf"),
                    pages=[PageText(page_number=1, text=f"1 Scope\n{long_body}")],
                ),
                "识别可靠",
                "可靠比较完成",
            ),
            (
                ExtractionResult(
                    pdf_path=Path("degraded.pdf"),
                    pages=[PageText(page_number=1, text=f"1 Scope\n{long_body}")],
                    warnings=["INTERNAL_ONLY_WARNING"],
                ),
                "需人工复核",
                "请人工复核",
            ),
            (
                ExtractionResult(
                    pdf_path=Path("indeterminate.pdf"),
                    pages=[PageText(page_number=1, text="")],
                ),
                "无法判断",
                "无法可靠识别",
            ),
        ]

        for extraction, expected_summary, expected_status in cases:
            with self.subTest(expected_summary=expected_summary):
                result = compare_extractions(extraction, extraction, DiffOptions())
                app = object.__new__(ProtocolDiffDesktopApp)
                app.summary_var = mock.Mock()
                app.report_path_var = mock.Mock()
                app.status_var = mock.Mock()
                app.open_html_button = mock.Mock()
                app.open_dir_button = mock.Mock()

                app._handle_success(
                    DesktopRunSuccess(
                        result=result,
                        outputs={"html": Path("/tmp/protocol_diff_report.html")},
                    )
                )

                summary_text = app.summary_var.set.call_args.args[0]
                status_text = app.status_var.set.call_args.args[0]
                self.assertIn(expected_summary, summary_text)
                self.assertIn(expected_status, status_text)
                self.assertNotIn("警告", summary_text)

    def test_desktop_summary_includes_reported_table_change_count(self) -> None:
        """A table-only change must not look like an overall zero-change run."""

        body = (
            "The receiver shall preserve every normative voltage timing calibration "
            "requirement for all supported operating modes. "
        ) * 10

        def extraction(name: str, value: str) -> ExtractionResult:
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
                        title="Table 1 Operating limits",
                        bbox=(0.0, 0.0, 100.0, 100.0),
                        image_data_uri="",
                        row_texts=(
                            f"表格行: T1 | Parameter=Voltage | Symbol=VDD | Max={value} | Units=V",
                        ),
                        grid_summary="structured rows",
                    )
                ],
            )

        result = compare_extractions(
            extraction("old.pdf", "1.1"),
            extraction("new.pdf", "1.2"),
            DiffOptions(),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            app = object.__new__(ProtocolDiffDesktopApp)
            app.summary_var = mock.Mock()
            app.report_path_var = mock.Mock()
            app.status_var = mock.Mock()
            app.open_html_button = mock.Mock()
            app.open_dir_button = mock.Mock()

            app._handle_success(DesktopRunSuccess(result=result, outputs=outputs))

        summary_text = app.summary_var.set.call_args.args[0]
        self.assertIn("章节修改 0", summary_text)
        self.assertIn("表格变化 1", summary_text)

    def test_extraction_requires_pdfplumber_without_pypdf_fallback(self) -> None:
        """Missing pdfplumber should fail clearly instead of using pypdf silently."""

        with tempfile.TemporaryDirectory() as temp_dir:  # 临时目录隔离依赖失败测试文件。
            pdf_path = Path(temp_dir) / "placeholder.pdf"  # 只需要存在即可，底层提取函数会被 mock。
            pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")  # 写入极小 PDF 占位内容，触发路径存在检查。
            with mock.patch(
                "protocol_pdf_diff.pdf_extract._extract_pdf_text_with_pdfplumber",
                side_effect=ModuleNotFoundError("pdfplumber"),
            ):  # 模拟打包环境漏装 pdfplumber 的真实故障。
                with self.assertRaises(MissingDependencyError):  # 入口必须抛出明确依赖错误。
                    extract_pdf_text(pdf_path)

        self.assertFalse(
            hasattr(pdf_extract_module, "_extract_pdf_text_with_pypdf")
        )  # 源码中不再保留可被误接回去的 pypdf 提取 fallback。

    def test_extraction_preserves_unproven_content_and_removes_margin_number_runs(self) -> None:
        """Text-only cleanup must not guess that status, copyright, or title lines are furniture."""

        line_numbers = " ".join(str(value) for value in range(1, 50))  # 模拟 PDF 页边 1~49 行号整段抽取。
        raw_text = "\n".join(
            [
                "DRAFT",  # 草稿水印单独成行时必须删除。
                "D",  # 竖排 DRAFT 第 1 行。
                "R",  # 竖排 DRAFT 第 2 行。
                "A",  # 竖排 DRAFT 第 3 行。
                "F",  # 竖排 DRAFT 第 4 行。
                "T",  # 竖排 DRAFT 第 5 行。
                *[str(value) for value in range(1, 50)],  # 页边行号逐行抽取时也必须删除。
                line_numbers,  # 页边行号整行必须删除。
                f"{line_numbers} Receiver calibration shall remain.",  # 行号拼入正文时只删除行号。
                "Copyright © 2024 Optical Internetworking Forum",  # 页脚版权行必须删除。
                "Optical Internetworking Forum - Clause 3: Electrical",  # OIF 运行页眉必须删除。
                "Receiver calibration shall remain.",  # 普通正文必须保留。
            ]
        )
        cleaned = _clean_extracted_page_text(raw_text)  # 直接验证抽取层清洗函数，避免依赖真实 PDF。

        self.assertIn("DRAFT", cleaned)  # 没有坐标证据时，DRAFT 也可能是正文状态。
        self.assertIn("Copyright", cleaned)  # 单页版权声明不能仅凭词面被静默删除。
        self.assertIn("Optical Internetworking Forum - Clause", cleaned)  # 标准名称不能触发出版方特判。
        self.assertIn("\nD\nR\nA\nF\nT\n", f"\n{cleaned}\n")  # 无坐标证据时，竖排文本也可能是状态或测试向量。
        self.assertIn(line_numbers, cleaned)  # 纯数字序列也可能是通道/向量，文本层不猜测删除。
        self.assertIn("Receiver calibration shall remain.", cleaned)  # 正文句子仍应保留。

    def test_pdfplumber_extraction_marks_stable_two_column_page_as_layout_risk(self) -> None:
        """Several aligned rows on both sides of a central gutter need manual review."""

        words: list[dict[str, object]] = []
        for row, top in enumerate((100, 125, 150, 175, 200), start=1):
            words.extend(
                [
                    {"text": f"Left-requirement-{row}", "x0": 60, "x1": 240, "top": top, "bottom": top + 10},
                    {"text": f"Right-requirement-{row}", "x0": 360, "x1": 550, "top": top, "bottom": top + 10},
                ]
            )
        page = _FakeLayoutPage(
            words,
            "\n".join(f"Left requirement {row} Right requirement {row}" for row in range(1, 6)),
        )

        self.assertTrue(_extract_fake_layout_page(page).layout_risk)

    def test_pdfplumber_extraction_marks_staggered_line_grid_columns_as_layout_risk(self) -> None:
        """Offset column baselines in a line-heavy form must still require review."""

        words: list[dict[str, object]] = []
        for row, top in enumerate((100, 140, 180, 220), start=1):
            words.append(
                {
                    "text": f"Left-form-requirement-{row}",
                    "x0": 60,
                    "x1": 240,
                    "top": top,
                    "bottom": top + 10,
                }
            )
        for row, top in enumerate((120, 160, 200, 240), start=1):
            words.append(
                {
                    "text": f"Right-form-requirement-{row}",
                    "x0": 360,
                    "x1": 550,
                    "top": top,
                    "bottom": top + 10,
                }
            )
        dense_body = " ".join(
            f"Requirement {index} shall remain reviewable."
            for index in range(1, 24)
        )
        page = _FakeLayoutPage(words, f"1 Scope\n{dense_body}")
        page.lines = [{} for _ in range(12)]

        extracted_page = _extract_fake_layout_page(page)
        extraction = ExtractionResult(
            pdf_path=Path("staggered-form.pdf"),
            pages=[extracted_page],
        )
        result = compare_extractions(extraction, extraction, DiffOptions())

        self.assertTrue(extracted_page.layout_risk)
        self.assertEqual("degraded", result.assessment.state)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_pdfplumber_extraction_keeps_full_width_prose_linear(self) -> None:
        """Ordinary full-width prose crossing the page centre is not a layout risk."""

        words: list[dict[str, object]] = []
        for row, top in enumerate((100, 125, 150, 175, 200), start=1):
            words.extend(
                [
                    {"text": f"Requirement-{row}", "x0": 60, "x1": 205, "top": top, "bottom": top + 10},
                    {"text": "continues-across-centre", "x0": 215, "x1": 385, "top": top, "bottom": top + 10},
                    {"text": "without-a-column-break", "x0": 395, "x1": 550, "top": top, "bottom": top + 10},
                ]
            )
        page = _FakeLayoutPage(words, "\n".join(f"Full width requirement line {row}" for row in range(1, 6)))

        self.assertFalse(_extract_fake_layout_page(page).layout_risk)

    def test_pdfplumber_extraction_does_not_flag_short_table_cells_as_columns(self) -> None:
        """Aligned short table cells on both halves are not enough to infer two-column prose."""

        words: list[dict[str, object]] = []
        for row, top in enumerate((100, 125, 150, 175, 200), start=1):
            words.extend(
                [
                    {"text": f"P{row}", "x0": 60, "x1": 85, "top": top, "bottom": top + 10},
                    {"text": f"{row}.0", "x0": 180, "x1": 215, "top": top, "bottom": top + 10},
                    {"text": f"V{row}", "x0": 365, "x1": 390, "top": top, "bottom": top + 10},
                    {"text": "UI", "x0": 500, "x1": 520, "top": top, "bottom": top + 10},
                ]
            )
        page = _FakeLayoutPage(words, "\n".join(f"P{row} {row}.0 V{row} UI" for row in range(1, 6)))

        self.assertFalse(_extract_fake_layout_page(page).layout_risk)

    def test_heading_detection_rejects_ct_postal_address(self) -> None:
        """A street number followed by the Ct suffix is an address, not a section."""

        self.assertIsNone(detect_heading("5177 Brandin Ct"))

    def test_heading_detection_rejects_parenthesized_footnotes_and_table_rows(self) -> None:
        """Numbered prose footnotes and explicit structured rows stay in body text."""

        self.assertIsNone(detect_heading("(1). The arithmetic mean is computed over all samples."))
        self.assertIsNone(detect_heading("(1) The arithmetic mean is computed over all samples."))
        self.assertIsNone(detect_heading("表格行: T1 | Parameter=RJ | Value=0.10 UI"))

    def test_repeated_deep_number_path_is_preserved_as_ambiguous_evidence(self) -> None:
        """An unproven duplicate remains visible and lowers comparison confidence."""

        extraction = ExtractionResult(
            pdf_path=Path("repeated-deep-number.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "32 Link requirements\n"
                        "32.3 Transmitter requirements\n"
                        "32.3.1 Equalization requirements\n"
                        "The transmitter shall meet the primary limit.\n"
                        "32.3.1 Equalization row copied from a table cell\n"
                        "The copied row value shall remain visible.\n"
                        "32.3.2 Jitter requirements\n"
                        "The transmitter shall meet the jitter limit."
                    ),
                )
            ],
            total_pages=1,
        )

        result = compare_extractions(extraction, extraction, DiffOptions())
        sections = result.old_sections
        matching = [section for section in sections if section.number_path == ("32", "32.3", "32.3.1")]

        self.assertEqual(2, len(matching))
        self.assertIn("The copied row value shall remain visible.", matching[1].body)
        self.assertEqual("degraded", result.assessment.state)

    def test_prior_sibling_number_restart_is_preserved_and_degraded(self) -> None:
        """A prior sibling restart stays visible instead of being silently discarded."""

        extraction = ExtractionResult(
            pdf_path=Path("prior-sibling-table-reference.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "32.3 Receiver requirements\n"
                        "32.3.1 Equalization\nOriginal equalization requirement.\n"
                        "32.3.2 Calibration\nOriginal calibration requirement.\n"
                        "32.3.1 Equalization row copied from a table cell\n"
                        "The copied row value shall remain part of calibration."
                    ),
                )
            ],
        )

        result = compare_extractions(extraction, extraction, DiffOptions())
        technical_sections = [section for section in result.old_sections if section.role == "technical"]

        self.assertEqual(
            [("32.3",), ("32.3", "32.3.1"), ("32.3", "32.3.2"), ("32.3", "32.3.1")],
            [section.number_path for section in technical_sections],
        )
        self.assertIn(
            "The copied row value shall remain part of calibration.",
            technical_sections[-1].body,
        )
        self.assertEqual("degraded", result.assessment.state)

    def test_contents_entries_do_not_consume_same_numbered_body_sections(self) -> None:
        """TOC numbers may repeat in the body without suppressing real sections."""

        extraction = ExtractionResult(
            pdf_path=Path("contents-and-body.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text="Contents\n1 Introduction\n2 Scope",
                ),
                PageText(
                    page_number=2,
                    text=(
                        "1 Introduction\n"
                        "The real introduction defines the implementation agreement.\n"
                        "2 Scope\n"
                        "The real scope defines every normative receiver requirement."
                    ),
                ),
            ],
            total_pages=2,
        )

        sections = section_document(extraction)
        technical_sections = [
            section for section in sections if section.role == "technical"
        ]

        self.assertEqual(
            [("1",), ("2",)],
            [section.number_path for section in technical_sections],
        )
        self.assertIn("real introduction", technical_sections[0].body)
        self.assertIn("real scope", technical_sections[1].body)

    def test_contents_and_real_body_can_share_one_page(self) -> None:
        """A short TOC must stop when its numbering restarts into real prose."""

        old = ExtractionResult(
            pdf_path=Path("old-same-page-contents.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "Contents\n1 Introduction\n2 Limits\n"
                        "1 Introduction\nThe operating voltage shall be 10 V.\n"
                        "2 Limits\nThe receiver shall preserve every stated limit."
                    ),
                )
            ],
        )
        new = ExtractionResult(
            pdf_path=Path("new-same-page-contents.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "Contents\n1 Introduction\n2 Limits\n"
                        "1 Introduction\nThe operating voltage shall be 12 V.\n"
                        "2 Limits\nThe receiver shall preserve every stated limit."
                    ),
                )
            ],
        )

        result = compare_extractions(old, new, DiffOptions())
        technical_sections = [
            section for section in result.old_sections if section.role == "technical"
        ]

        self.assertEqual(
            [("1",), ("2",)],
            [section.number_path for section in technical_sections],
        )
        self.assertEqual(1, sum(change.role == "technical" for change in result.changes))
        self.assertEqual(0, sum(change.role == "document_metadata" for change in result.changes))

    def test_multipage_contents_continuation_does_not_poison_body_numbers(self) -> None:
        """TOC continuation pages without their own title stay outside body section history."""

        extraction = ExtractionResult(
            pdf_path=Path("multipage-contents.pdf"),
            pages=[
                PageText(page_number=1, text="Contents\n1 Introduction\n2 Scope"),
                PageText(page_number=2, text="3 Requirements\n4 Compliance"),
                PageText(
                    page_number=3,
                    text=(
                        "1 Introduction\nActual introduction text.\n"
                        "2 Scope\nActual scope text.\n"
                        "3 Requirements\nActual requirement text.\n"
                        "4 Compliance\nActual compliance text."
                    ),
                ),
            ],
        )

        sections = section_document(extraction)
        technical_sections = [
            section for section in sections if section.role == "technical"
        ]

        self.assertEqual(
            [("1",), ("2",), ("3",), ("4",)],
            [section.number_path for section in technical_sections],
        )

    def test_spaced_chinese_contents_title_does_not_consume_body_sections(self) -> None:
        """Chinese contents titles split by PDF spacing must still be metadata."""

        extraction = ExtractionResult(
            pdf_path=Path("spaced-chinese-contents.pdf"),
            pages=[
                PageText(page_number=1, text="目 录\n1 总则\n2 要求"),
                PageText(
                    page_number=2,
                    text=(
                        "1 总则\n真实总则规定接收机的适用范围。\n"
                        "2 要求\n真实要求规定接收机必须满足全部电气限制。"
                    ),
                ),
            ],
        )

        technical_sections = [
            section for section in section_document(extraction) if section.role == "technical"
        ]

        self.assertEqual(
            [("1",), ("2",)],
            [section.number_path for section in technical_sections],
        )
        self.assertIn("真实总则", technical_sections[0].body)
        self.assertIn("真实要求", technical_sections[1].body)

    def test_contents_number_spacing_does_not_prevent_body_restart(self) -> None:
        """TOC and body chapter numbers may differ only by extracted whitespace."""

        extraction = ExtractionResult(
            pdf_path=Path("spaced-chapter-number.pdf"),
            pages=[
                PageText(page_number=1, text="目 录\n第 1 章 总则\n第 2 章 要求"),
                PageText(
                    page_number=2,
                    text=(
                        "第1章 总则\n真实总则正文。\n"
                        "第2章 要求\n真实要求正文。"
                    ),
                ),
            ],
        )

        technical_sections = [
            section for section in section_document(extraction) if section.role == "technical"
        ]

        self.assertEqual(2, len(technical_sections))
        self.assertEqual(("第1章",), technical_sections[0].number_path)
        self.assertEqual(("第2章",), technical_sections[1].number_path)

    def test_deep_table_value_and_inconsistent_parent_do_not_open_sections(self) -> None:
        """Table parameters with section-like numbers must remain body evidence."""

        self.assertIsNone(
            detect_heading("32.3.1.3 Level Separation Mismatch Ratio T_RLM 0.95 -")
        )
        extraction = ExtractionResult(
            pdf_path=Path("inconsistent-parent.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "32 Link requirements\n"
                        "32.3 Transmitter requirements\n"
                        "32.3.2 Receiver interference requirements\n"
                        "The receiver shall meet the primary limit.\n"
                        "32.3.1.3 Differential to Common Mode Input Equation\n"
                        "R_SCD11 shall be recorded as table evidence."
                    ),
                )
            ],
        )

        sections = section_document(extraction)

        self.assertFalse(
            any(section.heading.startswith("32.3.1.3") for section in sections)
        )
        receiver_section = next(
            section for section in sections if section.heading.startswith("32.3.2")
        )
        self.assertIn("32.3.1.3 Differential to Common Mode Input Equation", receiver_section.body)

    def test_note_boxes_allow_empty_companion_cells_but_keep_real_single_column_tables(self) -> None:
        """Split Note boxes are skipped without broad deletion of one-column tables."""

        split_note_rows = [
            "表格行: T1 | Column 1= | Column 2= | Column 3=Note: Use a calibrated adapter. | Column 4=",
            "表格行: T1 | Column 1= | Column 2= | Column 3=Include the fixture in the measurement. | Column 4=",
        ]
        title_led_note_rows = [
            "表格行: T1 | Column 1=The limit applies at the reference plane. | Column 2=",
        ]
        real_single_column_rows = [
            "表格行: T1 | Value=Calibration method A",
            "表格行: T1 | Value=Adapter type SMP",
        ]
        sparse_real_table_rows = [
            "表格行: T1 | Column 1=Note: required for mode A | Column 2=",
            "表格行: T1 | Column 1= | Column 2=20 mV",
        ]

        self.assertTrue(_should_skip_detected_table("", split_note_rows))
        self.assertTrue(_should_skip_detected_table("Notes", title_led_note_rows))
        self.assertFalse(_should_skip_detected_table("", real_single_column_rows))
        self.assertFalse(_should_skip_detected_table("", sparse_real_table_rows))

    def test_captioned_figure_grid_requires_intervening_label_geometry(self) -> None:
        """A distant Figure caption can suppress only a label-filled diagram grid."""

        def word(text: str, top: float, x0: float = 72.0) -> dict[str, object]:
            return {
                "text": text,
                "x0": x0,
                "x1": x0 + max(8.0, len(text) * 5.0),
                "top": top,
                "bottom": top + 10.0,
            }

        figure_words = [
            word("Figure 4-2. Receiver test setup", 80.0),
            word("Generator", 110.0),
            word("Stressed signal", 130.0),
            word("Reference", 150.0),
            word("CRU", 170.0),
        ]
        figure_rows = ["表格行: T1 | Column 1=Reference CRU | Column 2=DFE"]

        self.assertTrue(
            _table_bbox_belongs_to_captioned_figure(
                (300.0, 190.0, 390.0, 225.0),
                figure_rows,
                figure_words,
            )
        )
        prose_after_figure = [
            *figure_words[:2],
            word("The receiver shall preserve every calibrated 20 mV limit.", 135.0),
            word("Parameter", 165.0),
        ]
        self.assertFalse(
            _table_bbox_belongs_to_captioned_figure(
                (300.0, 190.0, 390.0, 225.0),
                ["表格行: T1 | Parameter=Mode A | Value=20 mV"],
                prose_after_figure,
            )
        )
        self.assertFalse(
            _table_bbox_belongs_to_captioned_figure(
                (300.0, 190.0, 390.0, 225.0),
                figure_rows,
                figure_words,
                title="Table 4-1. Receiver limits",
            )
        )
        table_after_figure = [
            word("Figure 4-2. Receiver test setup", 20.0),
            word("Table 4-1. Receiver limits", 80.0),
            word("Parameter", 110.0),
            word("Minimum", 130.0),
            word("Maximum", 150.0),
        ]
        self.assertFalse(
            _table_bbox_belongs_to_captioned_figure(
                (300.0, 190.0, 390.0, 225.0),
                [
                    "表格行: T1 | Parameter=Mode A | Minimum=10 mV | Maximum=20 mV",
                    "表格行: T1 | Parameter=Mode B | Minimum=12 mV | Maximum=24 mV",
                ],
                table_after_figure,
                title="",
            )
        )
        self.assertTrue(
            _table_bbox_belongs_to_captioned_figure(
                (300.0, 190.0, 390.0, 225.0),
                [
                    "表格行: T1 | Column 1=See Table 31-8 | Column 2=MCB",
                    "表格行: T1 | Column 1=Channel | Column 2=Reference",
                ],
                figure_words,
                title="",
            )
        )
        self.assertTrue(
            _table_bbox_belongs_to_captioned_figure(
                (300.0, 190.0, 390.0, 225.0),
                [
                    "表格行: T1 | Column 1=MCB | Column 2=Reference",
                    "表格行: T1 | Column 1=Table 31-8 | Column 2=Lookup",
                ],
                figure_words,
                title="",
            )
        )
        self.assertFalse(
            _table_bbox_belongs_to_captioned_figure(
                (300.0, 190.0, 390.0, 225.0),
                [
                    "表格行: T1 | Column 1=Table | Column 2=4-1. Receiver limits",
                    "表格行: T1 | Parameter=Mode A | Value=20 mV",
                ],
                figure_words,
                title="",
            )
        )
        self.assertFalse(
            _table_bbox_belongs_to_captioned_figure(
                (300.0, 190.0, 390.0, 225.0),
                [
                    "表格行: T1 | Column 1=Table 4-1. Receiver limits | Column 2=",
                    "表格行: T1 | Parameter=Mode A | Value=20 mV",
                ],
                figure_words,
                title="",
            )
        )

    def test_annex_letter_captions_distinguish_figure_grids_from_real_tables(self) -> None:
        """Annex A-2/A-1 captions use the same geometry safeguards as numeric captions."""

        def word(text: str, top: float, x0: float = 72.0) -> dict[str, object]:
            return {
                "text": text,
                "x0": x0,
                "x1": x0 + max(8.0, len(text) * 5.0),
                "top": top,
                "bottom": top + 10.0,
            }

        figure_words = [
            word("Figure A-2. Receiver test setup", 80.0),
            word("Generator", 110.0),
            word("Stressed signal", 130.0),
            word("Reference", 150.0),
            word("CRU", 170.0),
        ]
        bbox = (300.0, 190.0, 390.0, 225.0)

        self.assertTrue(_looks_like_figure_caption("Figure A-2. Receiver test setup"))
        self.assertTrue(_looks_like_table_caption("Table A-1. Receiver limits"))
        self.assertFalse(_looks_like_figure_caption("Figure for reference."))
        self.assertFalse(_looks_like_table_caption("Use the table for receiver calibration."))
        self.assertFalse(_looks_like_table_caption("See Table A-1"))
        self.assertFalse(_looks_like_table_caption("Refer to Table A-1"))
        self.assertFalse(_looks_like_table_caption("Use Table A-1 for calibration."))
        self.assertTrue(
            _table_bbox_belongs_to_captioned_figure(
                bbox,
                ["表格行: T1 | Column 1=MCB | Column 2=Reference"],
                figure_words,
            )
        )
        self.assertFalse(
            _table_bbox_belongs_to_captioned_figure(
                bbox,
                [
                    "表格行: T1 | Column 1=Table | Column 2=A-1. Receiver limits",
                    "表格行: T1 | Parameter=Mode A | Value=20 mV",
                ],
                figure_words,
            )
        )
        figure_with_table_reference = [
            figure_words[0],
            word("See Table A-1", 105.0),
            word("Generator", 125.0),
            word("MCB", 145.0),
            word("HCB", 165.0),
        ]
        self.assertTrue(
            _table_bbox_belongs_to_captioned_figure(
                bbox,
                ["表格行: T1 | Column 1=MCB | Column 2=Reference"],
                figure_with_table_reference,
            )
        )

    def test_public_extraction_keeps_annex_letter_captioned_grid_tables(self) -> None:
        """The page pre-gate must route A-1/AA.2 tables through real extraction."""

        import pymupdf

        for caption in ("Table A-1. Receiver limits", "Table AA.2. Receiver limits"):
            with self.subTest(caption=caption), tempfile.TemporaryDirectory() as temp_dir:
                pdf_path = Path(temp_dir) / "annex-grid-table.pdf"
                document = pymupdf.open()
                page = document.new_page(width=612, height=792)
                page.insert_text((72, 90), caption, fontsize=11)
                for y_position in (120, 160, 200):
                    page.draw_line((72, y_position), (320, y_position))
                for x_position in (72, 200, 320):
                    page.draw_line((x_position, 120), (x_position, 200))
                page.insert_text((82, 145), "Parameter", fontsize=10)
                page.insert_text((210, 145), "Value", fontsize=10)
                page.insert_text((82, 185), "Swing", fontsize=10)
                page.insert_text((210, 185), "12 mV", fontsize=10)
                document.save(pdf_path)
                document.close()

                extraction = extract_pdf_text(pdf_path)

                self.assertTrue(extraction.table_visuals)
                self.assertTrue(
                    any(caption.split(".", 1)[0] in table.title for table in extraction.table_visuals)
                )

    def test_repeated_standalone_number_keeps_both_observed_titles(self) -> None:
        """Text shape alone cannot prove that a repeated number is page furniture."""

        extraction = ExtractionResult(
            pdf_path=Path("running-header.pdf"),
            pages=[
                PageText(page_number=1, text="32.3\nReceiver calibration shall remain visible on page one."),
                PageText(page_number=2, text="32.3\nContinuation text shall remain visible on page two."),
            ],
            total_pages=2,
        )

        sections = section_document(extraction)

        self.assertEqual(2, len(sections))
        self.assertEqual([("32.3",), ("32.3",)], [section.number_path for section in sections])
        headings = "\n".join(section.heading for section in sections)
        self.assertIn("Receiver calibration shall remain visible", headings)
        self.assertIn("Continuation text shall remain visible", headings)

    def test_heading_detection_rejects_units_formulas_and_footnotes(self) -> None:
        """Unit values, formulas, and footnotes should not become section headings."""

        self.assertIsNone(detect_heading("1 UI"))  # 单位值不是章节。
        self.assertIsNone(detect_heading("5 UIpp"))  # 抖动单位值不是章节。
        self.assertIsNone(detect_heading("6 X 62.5 ps ="))  # 公式片段不是章节。
        self.assertIsNone(detect_heading("(408)309-9299"))  # 电话/脚注形态不是章节。
        self.assertIsNone(detect_heading("39221 Paseo Padre Pkwy, Suit J"))  # 邮寄地址不是章节。
        self.assertIsNone(detect_heading("15 R"))  # 数字加单字母符号不是章节。
        self.assertIsNone(detect_heading("1. Measured as described in Section 32.3.1.7."))  # 表格脚注不是章节。
        self.assertIsNone(detect_heading("03 NOTES:"))  # 表格 NOTES 标记不是章节。
        self.assertIsNone(detect_heading("5. T_RLM is defined in Appendix 16.C.4.3."))  # 符号定义脚注不是章节。
        self.assertIsNone(detect_heading("1 2 3 4 5 6 7 8 9 10 11 12"))  # 页边行号串不是章节。
        self.assertIsNone(detect_heading("35 Frequency (GHz)"))  # 图轴标签被前一刻度合并后也不能成为章节。
        self.assertIsNone(detect_heading("4.3d 4.3d"))  # 符号/数值残片不是章节标题。
        self.assertIsNone(detect_heading("5 UI X"))  # 图轴/表格值残片不是章节标题。
        self.assertIsNone(detect_heading("4.3u03 RMS03"))  # 抖动符号残片不是章节标题。
        self.assertIsNone(detect_heading("4.3u DRMS 03"))  # 抖动符号和下标残片不是章节标题。
        self.assertIsNone(detect_heading("802.3dj)"))  # 标准名残片不是章节标题。
        self.assertIsNone(
            detect_heading("32.3.1. The test transmitter is constrained such that for any transmitter equalizer setting")
        )  # 长正文句子不能被当作章节路径。
        self.assertIsNotNone(detect_heading("2 400G Interfaces"))  # 合法协议章节仍应识别。
        self.assertIsNotNone(
            detect_heading("2.13.2 Overview of Calibration Steps at 16.0 GT/s")
        )  # 带 GT/s 单位的深层章节标题仍应识别。
        self.assertIsNotNone(detect_heading("1 The Protocol Architecture."))  # 合法 The 开头标题不能被脚注规则误删。
        cleaned_heading = detect_heading("32.1 RequirRements")  # 没有版面证据时，混合大小写属于源文字。
        self.assertIsNotNone(cleaned_heading)  # 保留原文后仍应识别为合法章节。
        self.assertEqual("32.1 RequirRements", cleaned_heading.raw)  # 通用核心不能猜测并改写字母。

    def test_symbol_normalization_suppresses_multiply_and_exponent_noise(self) -> None:
        """Equivalent x/×/* and exponent renderings should not create diffs."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old.pdf"),  # 手工构造旧 PDF 抽取结果，专注比较层行为。
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "The limit is 5x10-6 and the timing window is 2xT_Vf. "
                        "The clock relation is fb*n."
                    ),
                )
            ],
            total_pages=1,
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new.pdf"),  # 手工构造新 PDF 抽取结果，内容仅变换符号形态。
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "The limit is 5×10-6 and the timing window is 2×T_Vf. "
                        "The clock relation is fb×n."
                    ),
                )
            ],
            total_pages=1,
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())  # 执行完整 section/diff 流程。

        self.assertEqual([], result.changes)  # 纯符号渲染差异不应出现在报告。

    def test_unproven_watermark_letters_are_preserved_as_text_diffs(self) -> None:
        """Without layout evidence, opaque letters and mixed-case words are content."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old.pdf"),  # 手工构造旧正文，避免真实 PDF 干扰。
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "A compliant receiver shall deliver the specified raw BER. "
                        "The single-ended transmitter output voltage shall remain stable."
                    ),
                )
            ],
            total_pages=1,
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new.pdf"),  # 新正文只包含 DRAFT 水印字母残片。
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "F\n"
                        "A T compliant receiver shall deliver the specified raw BER. "
                        "The single-ended trRansmitter output voltage shall remain stable."
                    ),
                )
            ],
            total_pages=1,
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())  # 执行完整比较流程。

        self.assertTrue(result.changes)  # 内容层不能猜测 F/T/R 是水印并静默删除。

        old_identifier = ExtractionResult(
            pdf_path=Path("old_identifier.pdf"),  # 旧侧使用合法训练标识符。
            pages=[PageText(page_number=1, text="1 Scope\nlaneTraining shall remain visible.")],
            total_pages=1,
        )
        new_identifier = ExtractionResult(
            pdf_path=Path("new_identifier.pdf"),  # 新侧真实删除 T，应当作为正文变化报告。
            pages=[PageText(page_number=1, text="1 Scope\nlaneraining shall remain visible.")],
            total_pages=1,
        )
        identifier_result = compare_extractions(old_identifier, new_identifier, DiffOptions())
        self.assertEqual(1, len(identifier_result.changes))  # 真实标识符变化不能被水印清理隐藏。

    def test_unproven_contact_notice_is_preserved(self) -> None:
        """A contact notice is content until page position or repetition proves furniture."""

        shared_context = (
            "Each implementation shall retain the same validated receiver behavior. " * 8
        )  # 真实匹配阈值现只接受文本证据；足量共同正文让本测试继续聚焦“联系信息不能被删除”。
        old_extraction = ExtractionResult(
            pdf_path=Path("old.pdf"),  # 旧侧只有真实正文。
            pages=[PageText(page_number=1, text=f"1 Scope\n{shared_context}\nCore requirement remains.")],
            total_pages=1,
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new.pdf"),  # 新侧额外混入 OIF 前言联系信息。
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        f"{shared_context}\n"
                        "Core requirement remains.\n"
                        "Optical Internetworking Forum (OIF) 39221 Paseo Padre Pkwy, "
                        "Suit J Fremont CA 94538 USA +1.510.392.4903 / "
                        "info@oiforum.com www.oiforum.com Notice: This Technical Document "
                        "has been created by the Optical Internetworking Forum (OIF)."
                    ),
                )
            ],
            total_pages=1,
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        self.assertEqual(1, len(result.changes))
        self.assertIn("info@oiforum.com", "\n".join(result.changes[0].added_snippets))

    def test_table_visual_section_keeps_long_text_diffs(self) -> None:
        """Table screenshots should supplement, not replace, paragraph diffs."""

        old_table = TableVisual(
            page_number=2,  # 旧 PDF 表格定位页。
            table_number=1,  # 单页内第一张表。
            title="Table 3-1 Receiver parameters",  # 表题用于视觉表格配对。
            bbox=(10.0, 20.0, 300.0, 160.0),  # 伪 bbox 足够报告渲染定位文本。
            image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",  # 极小内嵌图占位。
            row_texts=["表格行: T1 | Parameter=Input jitter | Value=0.30 UI"],  # 旧表结构化行。
            grid_summary="OpenCV 网格检测: 横线 4 条，竖线 3 条",  # 旧表视觉证据摘要。
            ocr_status="OCR 未启用：未发现 tesseract；使用截图和 pdfplumber 表格行。",  # 状态应短且可读。
        )
        new_table = TableVisual(
            page_number=2,  # 新 PDF 表格定位页。
            table_number=1,  # 单页内第一张表。
            title="Table 3-1 Receiver parameters",  # 同表题应被配成一组。
            bbox=(10.0, 20.0, 300.0, 160.0),  # 新表 bbox 与旧表对应。
            image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",  # 极小内嵌图占位。
            row_texts=["表格行: T1 | Parameter=Input jitter | Value=0.28 UI"],  # 新表结构化行。
            grid_summary="OpenCV 网格检测: 横线 4 条，竖线 3 条",  # 新表视觉证据摘要。
            ocr_status="OCR 未启用：未发现 tesseract；使用截图和 pdfplumber 表格行。",  # 状态应短且可读。
        )
        old_extraction = ExtractionResult(
            pdf_path=Path("old.pdf"),  # 旧侧包含正文和表格视觉结果。
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Receiver calibration\n"
                        "The receiver calibration chapter describes 10 operating windows, "
                        "long settling behavior, and measurement setup details for review."
                    ),
                )
            ],
            total_pages=2,
            table_visuals=[old_table],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new.pdf"),  # 新侧包含正文和表格视觉结果。
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Receiver calibration\n"
                        "The receiver calibration chapter describes 12 operating windows, "
                        "long settling behavior, and measurement setup details for review."
                    ),
                )
            ],
            total_pages=2,
            table_visuals=[new_table],
        )
        result = compare_extractions(old_extraction, new_extraction, DiffOptions())  # 正文 diff 和表格视觉都进入结果。

        with tempfile.TemporaryDirectory() as temp_dir:  # 报告输出写入临时目录，避免污染项目结果。
            paths = write_reports(result, temp_dir, DiffOptions())  # 生成 HTML 以验证用户最终看到的页面。
            html = paths["html"].read_text(encoding="utf-8")  # 读取完整 HTML 文本做断言。
            html_text = _visible_html_text(html)

        self.assertIn("operating windows", html)  # 大段正文句子必须仍在报告里。
        self.assertIn('class="del">10</mark>', html)  # 旧正文数值必须被保留并高亮。
        self.assertIn('class="ins">12</mark>', html)  # 新正文数值必须被保留并高亮。
        self.assertIn("表格补充证据（变化与复核）", html)  # 表格截图和结构化变化/复核区也必须存在。
        self.assertIn("<th>项目</th><th>旧版</th><th>新版</th><th>类型</th>", html)  # 表格摘要应像人工审查表。
        self.assertIn("Input jitter", html_text)  # 项目列应显示参数名，而不是内部“表格行”。
        self.assertIn("0.30 UI", html_text)  # 旧版列保留旧值；mark 标签不改变读者看到的连续文字。
        self.assertIn("0.28 UI", html_text)  # 新版列保留新值。
        self.assertIn("实质变化", html_text)  # 类型列应给出可读变化标签。
        self.assertNotIn("OpenCV", html_text)  # 用户报告不显示内部图像库名称。
        self.assertNotIn("pdfplumber", html_text)  # 用户报告不显示内部文本库名称。
        self.assertNotIn("OCR 未启用", html_text)  # 缺少 OCR 引擎不应作为表格比较正文展示。
        self.assertNotIn("bbox", html_text)  # 用户报告不显示内部坐标。
        self.assertNotIn("为什么", html_text)  # 报告不应包含实现自述式文案。
        self.assertNotIn("不接入", html_text)  # 报告不应解释内部集成取舍。

    def test_table_visual_rows_do_not_repeat_in_main_text_cards(self) -> None:
        """Structured table rows should be shown in table visuals, not paragraph cards."""

        old_table = TableVisual(
            page_number=2,  # 旧 PDF 表格定位页。
            table_number=1,  # 单页内第一张表。
            title="Table 3-1 Receiver parameters",  # 同名表题用于新旧表格配对。
            bbox=(10.0, 20.0, 300.0, 160.0),  # 伪 bbox 只用于报告定位文本。
            image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",  # 极小内嵌图占位。
            row_texts=["表格行: T1 | Parameter=Input jitter | Value=0.30 UI"],  # 表格摘要仍使用结构化行。
            grid_summary="网格检测: 横线 4 条，竖线 3 条",  # 内部摘要不会显示给最终用户。
        )
        new_table = TableVisual(
            page_number=2,  # 新 PDF 表格定位页。
            table_number=1,  # 单页内第一张表。
            title="Table 3-1 Receiver parameters",  # 和旧版表题一致，应配成一组。
            bbox=(10.0, 20.0, 300.0, 160.0),  # 新表 bbox 与旧表对应。
            image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",  # 极小内嵌图占位。
            row_texts=["表格行: T1 | Parameter=Input jitter | Value=0.28 UI"],  # 新表值应进入视觉表格摘要。
            grid_summary="网格检测: 横线 4 条，竖线 3 条",  # 内部摘要不会显示给最终用户。
        )
        old_extraction = ExtractionResult(
            pdf_path=Path("old.pdf"),  # 旧侧同时含正文和结构化表格行。
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Receiver calibration\n"
                        "The receiver calibration chapter describes 10 operating windows for review.\n"
                        "表格行: T1 | Parameter=Input jitter | Value=0.30 UI"
                    ),
                )
            ],
            table_visuals=[old_table],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new.pdf"),  # 新侧同时含正文和结构化表格行。
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Receiver calibration\n"
                        "The receiver calibration chapter describes 12 operating windows for review.\n"
                        "表格行: T1 | Parameter=Input jitter | Value=0.28 UI"
                    ),
                )
            ],
            table_visuals=[new_table],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())  # 有 TableVisual 时，正文卡片隐藏内部表格行。
        visible_snippets = "\n".join(
            snippet
            for change in result.changes
            for snippet in (
                change.added_snippets
                + change.removed_snippets
                + [pair.old for pair in change.replaced_snippets]
                + [pair.new for pair in change.replaced_snippets]
            )
        )  # 汇总正文差异卡片中真正会展示的片段。

        with tempfile.TemporaryDirectory() as temp_dir:  # 报告输出写入临时目录，避免污染项目目录。
            paths = write_reports(result, temp_dir, DiffOptions())  # 生成 HTML，验证最终用户看到的页面。
            html = paths["html"].read_text(encoding="utf-8")  # 读取完整 HTML 以检查正文区和表格区。
            html_text = _visible_html_text(html)

        self.assertIn("operating windows", visible_snippets)  # 正文段落变化仍然保留。
        self.assertNotIn("表格行:", visible_snippets)  # 主正文差异卡片不再重复内部表格行。
        self.assertNotIn("表格行:", html)  # HTML 页面也不应直接暴露内部表格行格式。
        self.assertIn("Input jitter", html_text)  # 表格视觉摘要仍展示项目名。
        self.assertIn("0.30 UI", html_text)  # 表格视觉摘要仍展示旧值。
        self.assertIn("0.28 UI", html_text)  # 表格视觉摘要仍展示新值。

    def test_exact_raw_table_sequence_rebuilds_missing_visual_and_surfaces_structure_review(self) -> None:
        """Exact content may fold duplicate raw text, but unknown table shape stays visible."""

        shared = (
            "The receiver shall preserve the validated calibration behavior in every operating mode. "
            * 8
        )
        rows = [
            "表格行: T1 | Column 1=Parameter | Column 2=Maximum | Column 3=Unit",
            "表格行: T1 | Column 1=JH4u | Column 2=0.118 | Column 3=UI",
        ]
        old_extraction = ExtractionResult(
            pdf_path=Path("old_raw_table.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        f"1 Receiver limits\n{shared}\n"
                        "Table 1-1.\nSignal Limits\n"
                        "Parameter Maximum Unit JH4u 0.118 UI"
                    ),
                )
            ],
            total_pages=1,
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_structured_table.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        f"1 Receiver limits\n{shared}\n"
                        "Table 1-1.\nSignal Limits\n"
                        + "\n".join(rows)
                    ),
                )
            ],
            table_visuals=[
                TableVisual(
                    page_number=1,
                    table_number=1,
                    title="Table 1-1. Signal Limits",
                    bbox=(10.0, 20.0, 300.0, 160.0),
                    image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
                    row_texts=rows,
                    grid_summary="structured rows",
                )
            ],
            total_pages=1,
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        rebuilt = [
            table
            for table in result.old_table_visuals
            if table.title == "Table 1-1. Signal Limits"
        ]
        table_changes = reporting_module._build_table_changes(result)
        visible_text = "\n".join(
            [
                snippet
                for change in result.changes
                for snippet in (*change.added_snippets, *change.removed_snippets)
            ]
            + [
                text
                for change in result.changes
                for pair in change.replaced_snippets
                for text in (pair.old, pair.new)
            ]
        )

        self.assertEqual(1, len(rebuilt))
        self.assertEqual("", rebuilt[0].image_data_uri)
        self.assertEqual(rows, rebuilt[0].row_texts)
        self.assertEqual("text_backed_exact_match", rebuilt[0].ocr_status)
        matched = [
            change
            for change in table_changes
            if "Table 1-1. Signal Limits"
            in " ".join(table.title for table in (*change.old_tables, *change.new_tables))
        ]
        self.assertEqual(1, len(matched))
        self.assertEqual(
            ["需人工复核"],
            [row.change_type for row in matched[0].row_changes],
        )
        self.assertNotIn("Table 1-1", visible_text)
        self.assertNotIn("JH4u 0.118 UI", visible_text)

    def test_exact_table_reconciliation_does_not_suppress_repeated_unit(self) -> None:
        """A repeated raw unit may be a real deletion elsewhere and must remain visible."""

        shared_limits = (
            "The receiver shall preserve the validated calibration behavior in every operating mode. "
            * 8
        )
        shared_audit = (
            "The audit trail shall retain every independently reviewed receiver decision. "
            * 8
        )
        repeated_unit = "Signal Limits Parameter Maximum Unit JH4u 0.118 UI"
        rows = [
            "表格行: T1 | Column 1=Parameter | Column 2=Maximum | Column 3=Unit",
            "表格行: T1 | Column 1=JH4u | Column 2=0.118 | Column 3=UI",
        ]
        old_extraction = ExtractionResult(
            pdf_path=Path("old_repeated_raw_unit.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        f"1 Receiver limits\n{shared_limits}\n"
                        f"Table 1-1.\n{repeated_unit}\n"
                        f"2 Audit trail\n{shared_audit}\n{repeated_unit}"
                    ),
                )
            ],
            total_pages=1,
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_repeated_raw_unit.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        f"1 Receiver limits\n{shared_limits}\nTable 1-1.\nSignal Limits\n"
                        + "\n".join(rows)
                        + f"\n2 Audit trail\n{shared_audit}"
                    ),
                )
            ],
            table_visuals=[
                TableVisual(
                    page_number=1,
                    table_number=1,
                    title="Table 1-1. Signal Limits",
                    bbox=(10.0, 20.0, 300.0, 160.0),
                    image_data_uri="",
                    row_texts=rows,
                    grid_summary="structured rows",
                )
            ],
            total_pages=1,
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        visible_removed = "\n".join(
            snippet
            for change in result.changes
            for snippet in change.removed_snippets
        )

        self.assertTrue(
            any(
                table.ocr_status == "text_backed_exact_match"
                for table in result.old_table_visuals
            )
        )
        self.assertIn(repeated_unit, visible_removed)

    def test_exact_raw_table_reconciliation_rejects_changed_value(self) -> None:
        """One changed numeric cell must block reconstruction and remain reportable."""

        shared = (
            "The receiver shall preserve the validated calibration behavior in every operating mode. "
            * 8
        )
        new_rows = [
            "表格行: T1 | Column 1=Parameter | Column 2=Maximum | Column 3=Unit",
            "表格行: T1 | Column 1=JH4u | Column 2=0.135 | Column 3=UI",
        ]
        old_extraction = ExtractionResult(
            pdf_path=Path("old_changed_raw_table.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        f"1 Receiver limits\n{shared}\n"
                        "Table 1-1.\nSignal Limits\n"
                        "Parameter Maximum Unit JH4u 0.118 UI"
                    ),
                )
            ],
            total_pages=1,
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_changed_structured_table.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        f"1 Receiver limits\n{shared}\n"
                        "Table 1-1.\nSignal Limits\n"
                        + "\n".join(new_rows)
                    ),
                )
            ],
            table_visuals=[
                TableVisual(
                    page_number=1,
                    table_number=1,
                    title="Table 1-1. Signal Limits",
                    bbox=(10.0, 20.0, 300.0, 160.0),
                    image_data_uri="",
                    row_texts=new_rows,
                    grid_summary="structured rows",
                )
            ],
            total_pages=1,
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        table_changes = reporting_module._build_table_changes(result)
        visible_text = "\n".join(
            [
                snippet
                for change in result.changes
                for snippet in (*change.added_snippets, *change.removed_snippets)
            ]
            + [
                text
                for change in result.changes
                for pair in change.replaced_snippets
                for text in (pair.old, pair.new)
            ]
        )

        self.assertFalse(
            any(table.ocr_status == "text_backed_exact_match" for table in result.old_table_visuals)
        )
        self.assertEqual(1, len(table_changes))
        self.assertEqual("added", table_changes[0].change_type)
        self.assertIn("0.135", table_changes[0].row_changes[-1].new_value)
        self.assertIn("0.118", visible_text)

    def test_revision_history_uses_longest_exact_row_prefix(self) -> None:
        """An appended revision should reuse the exact old prefix and report only the new row."""

        shared = (
            "The publication history remains auditable and tied to the released implementation agreement. "
            * 8
        )
        revision_rows = [
            "表格行: T1 | Revision=OIF 2024.532.04 | Date=9th March 2026 | Description=Baseline document.",
            "表格行: T1 | Revision=OIF 2024.532.05 | Date=29th June 2026 | Description=Updated comment resolution.",
        ]
        old_extraction = ExtractionResult(
            pdf_path=Path("old_revision_raw.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        f"1 Document history\n{shared}\n"
                        "The history is detailed in the table below: Revision Date Description "
                        "OIF 2024.532.04 9th March 2026 Baseline document."
                    ),
                )
            ],
            total_pages=1,
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_revision_structured.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        f"1 Document history\n{shared}\n"
                        "The history is detailed in the table below:\n"
                        + "\n".join(revision_rows)
                    ),
                )
            ],
            table_visuals=[
                TableVisual(
                    page_number=1,
                    table_number=1,
                    title="in the table below:",
                    bbox=(10.0, 20.0, 300.0, 160.0),
                    image_data_uri="",
                    row_texts=revision_rows,
                    grid_summary="structured rows",
                    row_alignment_reliable=True,
                )
            ],
            total_pages=1,
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        rebuilt = [
            table
            for table in result.old_table_visuals
            if table.title == "in the table below:"
        ]
        table_changes = reporting_module._build_table_changes(result)
        visible_text = "\n".join(
            [
                snippet
                for change in result.changes
                for snippet in (*change.added_snippets, *change.removed_snippets)
            ]
            + [
                text
                for change in result.changes
                for pair in change.replaced_snippets
                for text in (pair.old, pair.new)
            ]
        )

        self.assertEqual(1, len(rebuilt))
        self.assertEqual(1, len(rebuilt[0].row_texts))
        self.assertIn("OIF 2024.532.04", rebuilt[0].row_texts[0])
        self.assertEqual(1, len(table_changes))
        self.assertEqual("modified", table_changes[0].change_type)
        self.assertEqual(2, len(table_changes[0].row_changes))
        added_row = next(
            row for row in table_changes[0].row_changes
            if row.change_type == "新表新增行"
        )
        review_row = next(
            row for row in table_changes[0].row_changes
            if row.change_type == "需人工复核"
        )
        self.assertIn("OIF 2024.532.05", added_row.item)
        self.assertNotIn("OIF 2024.532.04", added_row.item)
        self.assertIn("Updated comment resolution.", added_row.new_value)
        self.assertEqual("表格结构复核", review_row.item)
        self.assertIn("行列边界未验证", review_row.old_value)
        self.assertNotIn("Baseline document", visible_text)
        self.assertNotIn("table below", visible_text)

    def test_reconciliation_rejects_changed_caption_lead_in(self) -> None:
        """Exact table rows must not hide prose changed in the same caption unit."""

        shared = (
            "The publication history remains auditable and tied to the released implementation agreement. "
            * 8
        )
        revision_rows = [
            "表格行: T1 | Revision=OIF 2024.532.04 | Date=9th March 2026 | Description=Baseline document.",
            "表格行: T1 | Revision=OIF 2024.532.05 | Date=29th June 2026 | Description=Updated comment resolution.",
        ]
        old_extraction = ExtractionResult(
            pdf_path=Path("old_revision_policy.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        f"1 Document history\n{shared}\n"
                        "The LEGACY release policy is detailed in the table below: "
                        "Revision Date Description OIF 2024.532.04 9th March 2026 Baseline document."
                    ),
                )
            ],
            total_pages=1,
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_revision_policy.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        f"1 Document history\n{shared}\n"
                        "The NEW release policy is detailed in the table below:\n"
                        + "\n".join(revision_rows)
                    ),
                )
            ],
            table_visuals=[
                TableVisual(
                    page_number=1,
                    table_number=1,
                    title="in the table below:",
                    bbox=(10.0, 20.0, 300.0, 160.0),
                    image_data_uri="",
                    row_texts=revision_rows,
                    grid_summary="structured rows",
                )
            ],
            total_pages=1,
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        visible_text = "\n".join(
            [
                snippet
                for change in result.changes
                for snippet in (*change.added_snippets, *change.removed_snippets)
            ]
            + [
                text
                for change in result.changes
                for pair in change.replaced_snippets
                for text in (pair.old, pair.new)
            ]
        )

        self.assertFalse(
            any(table.ocr_status == "text_backed_exact_match" for table in result.old_table_visuals)
        )
        self.assertIn("LEGACY release policy", visible_text)
        self.assertIn("NEW release policy", visible_text)

    def test_uncovered_table_rows_stay_out_of_paragraph_diff(self) -> None:
        """Structured table rows should not crowd paragraph cards even if unmatched."""

        old_table = TableVisual(
            page_number=2,  # 旧 PDF 中有视觉证据的一张表。
            table_number=1,  # 单页内第一张表。
            title="Table 3-1 Receiver parameters",  # 表题用于视觉摘要配对。
            bbox=(10.0, 20.0, 300.0, 160.0),  # 伪 bbox 只用于构造测试对象。
            image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",  # 极小内嵌图占位。
            row_texts=["表格行: T1 | Parameter=Covered jitter | Value=0.30 UI"],  # 这行已由视觉摘要覆盖。
            grid_summary="网格检测: 横线 4 条，竖线 3 条",  # 测试不依赖内部摘要内容。
        )
        new_table = TableVisual(
            page_number=2,  # 新 PDF 中对应的视觉证据。
            table_number=1,  # 单页内第一张表。
            title="Table 3-1 Receiver parameters",  # 和旧版表题一致。
            bbox=(10.0, 20.0, 300.0, 160.0),  # 新表 bbox 与旧表对应。
            image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",  # 极小内嵌图占位。
            row_texts=["表格行: T1 | Parameter=Covered jitter | Value=0.28 UI"],  # 这行已由视觉摘要覆盖。
            grid_summary="网格检测: 横线 4 条，竖线 3 条",  # 测试不依赖内部摘要内容。
        )
        old_extraction = ExtractionResult(
            pdf_path=Path("old.pdf"),  # 旧侧同时包含有视觉覆盖和无视觉覆盖的表格行。
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Receiver calibration\n"
                        "表格行: T1 | Parameter=Covered jitter | Value=0.30 UI\n"
                        "表格行: T2 | Parameter=No visual fallback | Value=1.0 UI"
                    ),
                )
            ],
            table_visuals=[old_table],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new.pdf"),  # 新侧无视觉覆盖的表格行仍代表真实差异。
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Receiver calibration\n"
                        "表格行: T1 | Parameter=Covered jitter | Value=0.28 UI\n"
                        "表格行: T2 | Parameter=No visual fallback | Value=1.2 UI"
                    ),
                )
            ],
            table_visuals=[new_table],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())  # 运行完整比较，覆盖文档级混合场景。
        visible_snippets = "\n".join(
            snippet
            for change in result.changes
            for snippet in (
                change.added_snippets
                + change.removed_snippets
                + [pair.old for pair in change.replaced_snippets]
                + [pair.new for pair in change.replaced_snippets]
            )
        )  # 汇总正文差异卡片中实际可见的片段。

        self.assertNotIn("Covered jitter", visible_snippets)  # 已由视觉摘要覆盖的表格行不重复进入正文区。
        self.assertNotIn("No visual fallback", visible_snippets)  # 未覆盖表格行也不再作为正文兜底刷屏。
        self.assertNotIn("1.0 UI", visible_snippets)  # 表格数值不应混入段落差异卡片。
        self.assertNotIn("1.2 UI", visible_snippets)  # 新表格数值同样留给表格区或源 PDF 复核。
        table_summary_text = "\n".join(
            row
            for table in result.old_table_visuals + result.new_table_visuals
            for row in table.row_texts
        )  # 汇总截图表格和无截图兜底表格摘要。
        self.assertIn("No visual fallback", table_summary_text)  # 未覆盖表格行必须进入表格摘要区，不能静默丢失。
        self.assertIn("1.0 UI", table_summary_text)  # 旧表格值仍可在结构化摘要中复核。
        self.assertIn("1.2 UI", table_summary_text)  # 新表格值也仍可在结构化摘要中复核。

    def test_figure_table_candidates_are_skipped_before_text_and_visual_diff(self) -> None:
        """Figure/axis detections from pdfplumber should not be treated as tables."""

        figure_rows = ["表格行: T1 | IL min / IL max"]  # 模拟 Figure 32-2 曲线被误检成一行表格。
        real_rows = ["表格行: T1 | Parameter=R0 | Value=46.25 | Units=Ω"]  # 模拟真实参数表结构化行。
        note_box_rows = [
            "表格行: T1 | Value=Note: Adapters such as DC blocks are part of the generator",
            "表格行: T1 | Value=and are not included in the VNA measurement.",
        ]  # 模拟 PCIe Note 文本框被 pdfplumber 误检成无表题单列表格。
        titled_note_box_rows = [
            "表格行: T1 | Value=Note: If most waveforms are outliers, check the generator.",
        ]  # 带上下文标题的 Note 框也不应进入表格比较。
        single_column_real_rows = [
            "表格行: T1 | Value=Calibration method A",
            "表格行: T1 | Value=Adapter type SMP",
        ]  # 没有明确 Note 前缀的单列表格仍可能是真实表格，不能按关键词删除。

        self.assertTrue(
            _should_skip_detected_table("Figure 32-2.Channel Insertion Loss Limit for 112 Gsym/s", figure_rows)
        )  # 图题候选必须整块跳过，不能进入正文或截图。
        self.assertTrue(_should_skip_detected_table("", figure_rows))  # 无 bbox/无图题时，图轴表格行也必须跳过。
        self.assertTrue(_should_skip_detected_table("X", []))  # 坐标轴单字母空候选必须跳过。
        self.assertTrue(_should_skip_detected_table("", note_box_rows))  # 无表题单列 Note 框不应进入表格对比。
        self.assertTrue(_should_skip_detected_table("Calibration note", titled_note_box_rows))  # 非 Table 标题的 Note 框也应跳过。
        self.assertFalse(_should_skip_detected_table("Table 32-1. COM Parameter Values", []))  # 有明确表题时保留截图。
        self.assertFalse(_should_skip_detected_table("", real_rows))  # 缺表题但有结构化表格行时保守保留。
        self.assertFalse(_should_skip_detected_table("", single_column_real_rows))  # 单列真实表不能被关键词粗暴删除。

    def test_table_visual_summary_matches_human_readable_symbol_change_style(self) -> None:
        """Small table summaries should show item/old/new/type like the visual reference."""

        old_table = TableVisual(
            page_number=9,  # 旧版截图页码模拟用户截图里的旧 PDF 页。
            table_number=1,  # 该页第一张表。
            title="Table 32-4. Transmitter Output Jitter Specification",  # 表题用于成组配对。
            bbox=(193.0, 1433.0, 1121.0, 255.0),  # bbox 只用于报告标题和测试对象完整性。
            image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",  # 极小图片占位。
            row_texts=[
                (
                    "表格行: T1 | Characteristic=Uncorrelated Jitter | Symbol=T_J4.3u03 | "
                    "Condition= | Min= | Typ= | Max=0.121 | Unit=UI"
                ),  # 第一行旧版符号与新版不同，数值相同。
                (
                    "表格行: T1 | Characteristic=Uncorrelated jitter RMS | Symbol=T_JRMS03 | "
                    "Condition=See Note 1 | Min= | Typ= | Max=0.023 | Unit=UIrms"
                ),  # 第二行旧版符号与新版不同，数值相同。
                (
                    "表格行: T1 | Characteristic=Even-Odd Jitter | Symbol=T_EOJ03 | "
                    "Condition= | Min= | Typ= | Max=0.025 | Unit=UIpp"
                ),  # 第三行保持不变，小表格应一起展示。
            ],
            grid_summary="OpenCV 网格检测: 横线 6 条，竖线 7 条",  # 网格摘要应转换成用户可读表格证据。
            row_alignment_reliable=True,  # 夹具中的每个元素已明确是一条完整逻辑行，允许确认行级变化。
        )
        new_table = TableVisual(
            page_number=11,  # 新版截图页码模拟用户截图里的新 PDF 页。
            table_number=1,  # 该页第一张表。
            title="Table 32-4. Transmitter Output Jitter Specification",  # 同名表题应配成一组。
            bbox=(174.0, 1426.0, 1121.0, 254.0),  # bbox 只用于报告标题和测试对象完整性。
            image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",  # 极小图片占位。
            row_texts=[
                (
                    "表格行: T1 | Characteristic=Uncorrelated Jitter | Symbol=T_JH4.3u | "
                    "Condition= | Min= | Typ= | Max=0.121 | Unit=UI"
                ),  # 第一行新版符号变化。
                (
                    "表格行: T1 | Characteristic=Uncorrelated jitter RMS | Symbol=T_JHRMS | "
                    "Condition=See Note 1 | Min= | Typ= | Max=0.023 | Unit=UIrms"
                ),  # 第二行新版符号变化。
                (
                    "表格行: T1 | Characteristic=Even-Odd Jitter | Symbol=T_EOJ03 | "
                    "Condition= | Min= | Typ= | Max=0.025 | Unit=UIpp"
                ),  # 第三行未变化。
            ],
            grid_summary="OpenCV 网格检测: 横线 6 条，竖线 7 条",  # 网格摘要应转换成用户可读表格证据。
            row_alignment_reliable=True,  # 新表夹具同样给出已证明的一行一记录边界。
        )
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_jitter.pdf"),  # 旧版只保留稳定正文，方便聚焦表格视觉摘要。
                pages=[PageText(page_number=1, text="1 Scope\nStable requirement.")],
                table_visuals=[old_table],
            ),
            ExtractionResult(
                pdf_path=Path("new_jitter.pdf"),  # 新版正文不变，表格摘要仍应展示符号变化。
                pages=[PageText(page_number=1, text="1 Scope\nStable requirement.")],
                table_visuals=[new_table],
            ),
            DiffOptions(),
        )  # 报告层应从 TableVisual 直接生成截图和结构化摘要。

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, temp_dir, DiffOptions())  # 输出 HTML 并检查用户最终看到的表格摘要。
            html = paths["html"].read_text(encoding="utf-8")  # 读取完整 HTML，避免只测内部函数。
            html_text = _visible_html_text(html)

        self.assertIn("<th>项目</th><th>旧版</th><th>新版</th><th>类型</th>", html)  # 摘要表头应和视觉参考一致。
        self.assertIn("Uncorrelated Jitter symbol", html_text)  # 项目列应标出符号变化。
        self.assertIn("T_J4.3u03 | 0.121 UI", html_text)  # 旧版值应紧凑显示符号、数值和单位。
        self.assertIn("T_JH4.3u | 0.121 UI", html_text)  # 新版值应紧凑显示符号、数值和单位。
        self.assertIn("Uncorrelated jitter RMS symbol", html_text)  # 第二个符号变化也应有独立项目。
        self.assertIn("T_JRMS03 | 0.023 UIrms", html_text)  # 旧版 RMS 值应可直接对照。
        self.assertIn("T_JHRMS | 0.023 UIrms", html_text)  # 新版 RMS 值应可直接对照。
        self.assertNotIn("Even-Odd Jitter", html_text)  # 未变化行不再占用变化报告篇幅。
        self.assertNotIn("无变化", html_text)  # 表格卡只承载需要复核的行。
        self.assertGreaterEqual(html_text.count("实质/符号变化"), 2)  # 两个符号变化都应被明确标记。

    def test_table_visual_summary_preserves_unproven_symbol_changes(self) -> None:
        """Checkbox and repeated-operator edits stay visible without layout proof."""

        old_table = TableVisual(
            page_number=1,  # 旧表页码只用于报告定位。
            table_number=1,  # 旧表内部序号不应进入行级比较 key。
            title="Table 1 Calibration notes",  # 同名表会被报告层配对。
            bbox=(0.0, 0.0, 100.0, 100.0),  # 测试不依赖真实截图尺寸。
            image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",  # 极小图片占位。
            row_texts=[
                "表格行: T1 | Value=❑ SJ – 5 to 10 ps PP @ TP1",  # 旧侧带复选框和长横线。
                "表格行: T1 | Value=Note: Adapters such as DC blocks, pickoff T’s, etc. that are connected",  # 旧侧带逗号。
                "表格行: T1 | Characteristic=Peak-to-peak jitter | Condition=See Note 2 | MAX=80",
                "表格行: T1 | Parameter=Block Error Ratio | Value=3.2e-13",
            ],
            grid_summary="OpenCV 网格检测: 横线 2 条，竖线 2 条",  # 报告层会把内部摘要转为用户可读文案。
        )
        new_table = TableVisual(
            page_number=1,  # 新表页码与旧表一致，聚焦行文本差异。
            table_number=2,  # 新侧 T2 行号不同，但只是抽取器内部序号。
            title="Table 1 Calibration notes",  # 同名表应与旧表配对。
            bbox=(0.0, 0.0, 100.0, 100.0),  # 测试不依赖真实截图尺寸。
            image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",  # 极小图片占位。
            row_texts=[
                "表格行: T2 | Value=SJ - 5 to 10 ps PP @ TP1",  # 新侧缺复选框，但内容相同。
                "表格行: T2 | Value=Note: Adapters such as DC blocks, pickoff T’s etc. that are connected",  # 新侧少一个逗号。
                "表格行: T2 | Characteristic=Peak-to- / peak jitter | Condition=See Note2 | MAX=80",
                "表格行: T2 | Parameter=Block Error Ratio | Value=3.2×x10–13",
            ],
            grid_summary="OpenCV 网格检测: 横线 2 条，竖线 2 条",  # 报告层会隐藏 OpenCV 内部名称。
        )
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_table_noise.pdf"),  # 旧版正文不变，测试只关注表格摘要。
                pages=[PageText(page_number=1, text="1 Scope\nStable requirement.")],
                table_visuals=[old_table],
            ),
            ExtractionResult(
                pdf_path=Path("new_table_noise.pdf"),  # 新版正文不变，测试只关注表格摘要。
                pages=[PageText(page_number=1, text="1 Scope\nStable requirement.")],
                table_visuals=[new_table],
            ),
            DiffOptions(),
        )  # 完整结果能覆盖表格配对和 HTML 摘要渲染链路。

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, temp_dir, DiffOptions())  # 输出 HTML，验证用户实际看到的状态标签。
            html = paths["html"].read_text(encoding="utf-8")  # 读取完整 HTML，避免只测内部 key。
            html_text = _visible_html_text(html)

        self.assertIn('id="table-changes"', html)  # 未证明是抽取噪声的符号变化必须进入报告。
        self.assertIn("Table 1 Calibration notes", html_text)
        self.assertIn("❑", html_text)  # 复选框可能表示状态，不能按装饰符无条件删除。
        self.assertIn("3.2×x10", html_text)  # 重复乘号也可能是真实公式编辑，只能原样保留。

    def test_table_visual_summary_pairs_rows_by_identity_after_insertions(self) -> None:
        """Inserted table rows should not offset-pair unrelated changed parameters."""

        old_table = TableVisual(
            page_number=1,  # 旧表页码只用于报告定位。
            table_number=1,  # 单页第一张表。
            title="Table 1 Electrical values",  # 同名表应配成一组。
            bbox=(0.0, 0.0, 100.0, 100.0),  # 测试不依赖真实截图尺寸。
            image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",  # 极小图片占位。
            row_texts=[
                "表格行: T1 | Parameter=Reference resistance | Symbol=R0 | Value=50 | Units=Ω",
                "表格行: T1 | Parameter=Eye height | Symbol=EH | Min=< 15 | Units=mV",
            ],  # 旧表包含一个普通数值和一个单独 `<` 限值。
            grid_summary="OpenCV 网格检测: 横线 4 条，竖线 4 条",  # 报告层会隐藏内部库名。
            row_alignment_reliable=True,  # 手工行列表已按逻辑记录切分，不触发行归属不确定性降级。
        )
        new_table = TableVisual(
            page_number=1,  # 新表页码只用于报告定位。
            table_number=1,  # 单页第一张表。
            title="Table 1 Electrical values",  # 同名表应配成一组。
            bbox=(0.0, 0.0, 100.0, 100.0),  # 测试不依赖真实截图尺寸。
            image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",  # 极小图片占位。
            row_texts=[
                "表格行: T1 | Parameter=New impedance | Symbol=Zp | Value=40 | Units=Ω",
                "表格行: T1 | Parameter=Reference resistance | Symbol=R0 | Value=46.25 | Units=Ω",
                "表格行: T1 | Parameter=Eye height | Symbol=EH | Min=> 15 | Units=mV",
            ],  # 新表在前面插入一行，同一参数仍应按身份配对。
            grid_summary="OpenCV 网格检测: 横线 4 条，竖线 4 条",  # 报告层会隐藏内部库名。
            row_alignment_reliable=True,  # 插入前后的三条记录均有明确独立边界。
        )
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_table_insert.pdf"),  # 旧版正文不变，测试只关注表格摘要。
                pages=[PageText(page_number=1, text="1 Scope\nStable requirement.")],
                table_visuals=[old_table],
            ),
            ExtractionResult(
                pdf_path=Path("new_table_insert.pdf"),  # 新版正文不变，测试只关注表格摘要。
                pages=[PageText(page_number=1, text="1 Scope\nStable requirement.")],
                table_visuals=[new_table],
            ),
            DiffOptions(),
        )  # 完整结果覆盖表格配组、摘要配对和 HTML 渲染。

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, temp_dir, DiffOptions())  # 输出 HTML，验证用户最终看到的表格行摘要。
            html = paths["html"].read_text(encoding="utf-8")  # 读取完整 HTML 做断言。
            html_text = _visible_html_text(html)

        self.assertIn("New impedance", html_text)  # 插入行应显示为新增行，而不是和旧行错配。
        self.assertIn("新表新增行", html_text)  # 插入行类型应明确。
        self.assertIn("Reference resistance", html_text)  # 原有参数仍应被配到同一行。
        self.assertIn("50 Ω", html_text)  # 旧值保留。
        self.assertIn("46.25 Ω", html_text)  # 新值保留。
        self.assertIn("Eye height", html_text)  # `<` 到 `>` 的限值符号变化不能被吞掉。
        self.assertIn("< 15 mV", html_text)  # 旧侧单独小于号必须进入显示值。
        self.assertIn("> 15 mV", html_text)  # 新侧单独大于号必须进入显示值。
        self.assertGreaterEqual(html_text.count("实质变化"), 2)  # 数值变化和限值方向变化都应判为实质变化。

    def test_table_changes_share_html_json_csv_and_navigation(self) -> None:
        """Every report format should consume the same materialized table facts."""

        old_table = TableVisual(
            page_number=1,
            table_number=1,
            title="Table 1 Receiver limits",
            bbox=(0.0, 0.0, 100.0, 100.0),
            image_data_uri="",
            row_texts=[
                "表格行: T1 | Parameter=Reference resistance | Condition=See Note 1 | Value=50 | Units=Ω"
            ],
            grid_summary="",
        )
        new_table = TableVisual(
            page_number=2,
            table_number=1,
            title="Table 2 Receiver limits",
            bbox=(0.0, 0.0, 100.0, 100.0),
            image_data_uri="",
            row_texts=[
                "表格行: T1 | Parameter=Reference resistance | Condition=See Note 2 | Value=46.25 | Units=Ω"
            ],
            grid_summary="",
        )
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_table_facts.pdf"),
                pages=[PageText(page_number=1, text="1 Scope\nStable requirement.")],
                total_pages=1,
                table_visuals=[old_table],
            ),
            ExtractionResult(
                pdf_path=Path("new_table_facts.pdf"),
                pages=[PageText(page_number=1, text="1 Scope\nStable requirement.")],
                total_pages=2,
                table_visuals=[new_table],
            ),
            DiffOptions(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, temp_dir, DiffOptions())
            html = paths["html"].read_text(encoding="utf-8")
            payload = json.loads(paths["json"].read_text(encoding="utf-8"))
            markdown = paths["markdown"].read_text(encoding="utf-8")
            table_csv = paths["table_csv"].read_text(encoding="utf-8-sig")

        self.assertIn('href="#table-change-1"', html)
        self.assertIn("表格行变化", html)
        # 表格截图和行级事实是审阅入口，必须排在技术正文之前，避免读者先穿过长正文墙。
        self.assertLess(html.index('id="table-changes"'), html.index('id="text-changes"'))
        self.assertIn("Table 1 Receiver limits", html)
        self.assertIn("Table 2 Receiver limits", html)
        # 报告必须直接说明编号中性与数值严格性的边界，防止读者误解为全局数字归一化。
        self.assertIn("数值、限值和单位仍严格比较", html)
        self.assertIn("表格补充证据（变化与复核）", markdown)
        self.assertIn("数值、限值和单位仍严格比较", markdown)
        # Markdown/TXT 与 HTML 使用同一信息层级，复制到文档后仍要先看到表格证据。
        self.assertLess(
            markdown.index("## 表格补充证据（变化与复核）"),
            markdown.index("## 技术正文变化与复核"),
        )
        self.assertIn("46.25 Ω", payload["table_changes"][0]["row_changes"][0]["new_value"])
        self.assertIn("46.25 Ω", table_csv)
        self.assertIn("Condition=See Note 1", payload["table_changes"][0]["row_changes"][0]["old_value"])
        self.assertIn("Condition=See Note 2", payload["table_changes"][0]["row_changes"][0]["new_value"])

    def test_reader_hides_proven_condition_reference_shift_but_audit_keeps_it(self) -> None:
        """相同表值只因显式条款引用顺延时，不应占用读者表格证据区。"""

        # 同题同正文条款同时模拟真实报告中的章节顺延，但表格判断只依赖完整格值。
        old_section_text = (
            "31.3.13 Output Jitter\n"
            "Output jitter shall be measured with the declared reference receiver."
        )
        new_section_text = (
            "31.3.14 Output Jitter\n"
            "Output jitter shall be measured with the declared reference receiver."
        )
        # 表格参数和值完全一致，唯一变化是 Condition 中引用了顺延后的条款号。
        old_table = TableVisual(
            page_number=1,
            table_number=1,
            title="Table 31-10. Receiver limits",
            bbox=(0.0, 0.0, 100.0, 100.0),
            image_data_uri="",
            row_texts=[
                "表格行: T1 | Parameter=Residual jitter | Condition=See 31.3.13 | Value=0.023 | Units=UI"
            ],
            grid_summary="structured rows",
        )
        new_table = TableVisual(
            **{
                **old_table.__dict__,
                "row_texts": [
                    "表格行: T1 | Parameter=Residual jitter | Condition=See 31.3.14 | Value=0.023 | Units=UI"
                ],
            }
        )
        # 真实比较和报告入口共同验证定位中和只发生在读者报告中。
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_condition_reference.pdf"),
                pages=[PageText(page_number=1, text=old_section_text)],
                table_visuals=[old_table],
            ),
            ExtractionResult(
                pdf_path=Path("new_condition_reference.pdf"),
                pages=[PageText(page_number=1, text=new_section_text)],
                table_visuals=[new_table],
            ),
            DiffOptions(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, temp_dir, DiffOptions())
            html_text = _visible_html_text(paths["html"].read_text(encoding="utf-8"))
            markdown = paths["markdown"].read_text(encoding="utf-8")
            payload = json.loads(paths["json"].read_text(encoding="utf-8"))
            table_csv = paths["table_csv"].read_text(encoding="utf-8-sig")

        # 读者报告不显示纯引用编号顺延形成的表格卡。
        for rendered in (html_text, markdown):
            self.assertNotIn("Condition=See 31.3.13", rendered)
            self.assertNotIn("Condition=See 31.3.14", rendered)
        # JSON/CSV 保留条件引用的旧值和新值，确保过滤可审计、可回放。
        row_payload = payload["table_changes"][0]["row_changes"][0]
        self.assertIn("Condition=See 31.3.13", row_payload["old_value"])
        self.assertIn("Condition=See 31.3.14", row_payload["new_value"])
        self.assertIn("Condition=See 31.3.13", table_csv)
        self.assertIn("Condition=See 31.3.14", table_csv)

    def test_reader_keeps_table_value_change_when_condition_reference_also_shifts(self) -> None:
        """Condition 引用顺延不能遮住同一行的 UI 数值变化。"""

        # 同题条款模拟 31.3.13 → 31.3.14，工程值则单独从 0.023 改为 0.025。
        old_section_text = (
            "31.3.13 Output Jitter\n"
            "Output jitter shall be measured with the declared reference receiver."
        )
        new_section_text = (
            "31.3.14 Output Jitter\n"
            "Output jitter shall be measured with the declared reference receiver."
        )
        old_table = TableVisual(
            page_number=1,
            table_number=1,
            title="Table 31-10. Receiver limits",
            bbox=(0.0, 0.0, 100.0, 100.0),
            image_data_uri="",
            row_texts=[
                "表格行: T1 | Parameter=Residual jitter | Condition=See 31.3.13 | Value=0.023 | Units=UI"
            ],
            grid_summary="structured rows",
        )
        # 新表除引用顺延外还把技术值改为 0.025 UI，这一字符差异必须阻止整行隐藏。
        new_table = TableVisual(
            **{
                **old_table.__dict__,
                "row_texts": [
                    "表格行: T1 | Parameter=Residual jitter | Condition=See 31.3.14 | Value=0.025 | Units=UI"
                ],
            }
        )
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_condition_value.pdf"),
                pages=[PageText(page_number=1, text=old_section_text)],
                table_visuals=[old_table],
            ),
            ExtractionResult(
                pdf_path=Path("new_condition_value.pdf"),
                pages=[PageText(page_number=1, text=new_section_text)],
                table_visuals=[new_table],
            ),
            DiffOptions(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, temp_dir, DiffOptions())
            html_text = _visible_html_text(paths["html"].read_text(encoding="utf-8"))
            markdown = paths["markdown"].read_text(encoding="utf-8")

        # 两种人读格式都展示旧、新完整数值，证明定位中和没有扩大到工程数字。
        for rendered in (html_text, markdown):
            self.assertIn("0.023 UI", rendered)
            self.assertIn("0.025 UI", rendered)
            self.assertIn("Residual jitter", rendered)

    def test_reader_hides_single_table_caption_renumber_but_audit_keeps_it(self) -> None:
        """单张表除表号外完全一致时，读者报告隐藏、审计数据保留。"""

        old_table = TableVisual(
            page_number=1,
            table_number=1,
            title="Table 1 Receiver limits",
            bbox=(0.0, 0.0, 100.0, 100.0),
            image_data_uri="",
            row_texts=["表格行: T1 | Parameter=Reference resistance | Value=50 | Units=Ω"],
            grid_summary="",
        )
        new_table = TableVisual(
            page_number=2,
            table_number=1,
            title="Table 2 Receiver limits",
            bbox=(0.0, 0.0, 100.0, 100.0),
            image_data_uri="",
            row_texts=["表格行: T1 | Parameter=Reference resistance | Value=50 | Units=Ω"],
            grid_summary="",
        )
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_title.pdf"),
                pages=[PageText(page_number=1, text="1 Scope\nStable requirement.")],
                table_visuals=[old_table],
            ),
            ExtractionResult(
                pdf_path=Path("new_title.pdf"),
                pages=[PageText(page_number=1, text="1 Scope\nStable requirement.")],
                table_visuals=[new_table],
            ),
            DiffOptions(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, temp_dir, DiffOptions())
            html_text = _visible_html_text(paths["html"].read_text(encoding="utf-8"))
            markdown = paths["markdown"].read_text(encoding="utf-8")
            payload = json.loads(paths["json"].read_text(encoding="utf-8"))

        # 人读格式不把纯表号变化列为差异，JSON 仍提供原表题供审计回放。
        for rendered in (html_text, markdown):
            self.assertNotIn("Table 1 Receiver limits", rendered)
            self.assertNotIn("Table 2 Receiver limits", rendered)
        self.assertEqual(1, len(payload["table_changes"]))
        self.assertTrue(payload["table_changes"][0]["caption_changed"])
        self.assertEqual(0, payload["table_changes"][0]["row_change_count"])

        # 表号顺延若同时把表题单位从 mV 改成 MV，就不再是 caption-only 定位变化。
        case_result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_title_unit.pdf"),
                pages=[PageText(page_number=1, text="1 Scope\nStable requirement.")],
                table_visuals=[
                    TableVisual(**{**old_table.__dict__, "title": "Table 1 mV receiver limits"})
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new_title_unit.pdf"),
                pages=[PageText(page_number=1, text="1 Scope\nStable requirement.")],
                table_visuals=[
                    TableVisual(**{**new_table.__dict__, "title": "Table 2 MV receiver limits"})
                ],
            ),
            DiffOptions(),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            case_paths = write_reports(case_result, temp_dir, DiffOptions())
            case_html = _visible_html_text(
                case_paths["html"].read_text(encoding="utf-8")
            )
            case_markdown = case_paths["markdown"].read_text(encoding="utf-8")

        # 两种读者格式都保留大小写敏感单位，证明表题门禁也采用精确比较。
        for rendered in (case_html, case_markdown):
            self.assertIn("Table 1 mV receiver limits", rendered)
            self.assertIn("Table 2 MV receiver limits", rendered)

    def test_reader_hides_each_pure_table_caption_renumber(self) -> None:
        """多张描述性表题分别只改表号时，都不再列作读者差异。"""

        # 两张不同表题都只改 31-x 的末级表号，正文和表格行保持完全一致。
        old_tables = [
            TableVisual(
                page_number=1,
                table_number=1,
                title="Table 31-9. Receiver electrical operating limits",
                bbox=(0.0, 0.0, 100.0, 100.0),
                image_data_uri="",
                row_texts=["表格行: T1 | Parameter=Voltage | Value=1 | Units=V"],
                grid_summary="structured rows",
            ),
            TableVisual(
                page_number=2,
                table_number=1,
                title="Table 31-10. Output jitter measurement limits",
                bbox=(0.0, 0.0, 100.0, 100.0),
                image_data_uri="",
                row_texts=["表格行: T1 | Parameter=Jitter | Value=0.023 | Units=UI"],
                grid_summary="structured rows",
            ),
        ]
        new_tables = [
            TableVisual(
                **{
                    **old_tables[0].__dict__,
                    "title": "Table 31-10. Receiver electrical operating limits",
                }
            ),
            TableVisual(
                **{
                    **old_tables[1].__dict__,
                    "title": "Table 31-11. Output jitter measurement limits",
                }
            ),
        ]
        # 正文保持不变，报告中的任何表格变化都只能来自已证明的表号顺延。
        stable_page = PageText(
            page_number=1,
            text="31.3 Receiver Requirements\nReceiver requirements remain unchanged.",
        )
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_table_shift.pdf"),
                pages=[stable_page],
                total_pages=2,
                table_visuals=old_tables,
            ),
            ExtractionResult(
                pdf_path=Path("new_table_shift.pdf"),
                pages=[stable_page],
                total_pages=2,
                table_visuals=new_tables,
            ),
            DiffOptions(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, temp_dir, DiffOptions())
            html_text = _visible_html_text(paths["html"].read_text(encoding="utf-8"))
            markdown = paths["markdown"].read_text(encoding="utf-8")
            payload = json.loads(paths["json"].read_text(encoding="utf-8"))

        # 人读格式不再把统一表号偏移列为变化，机器审计仍保留两张 caption_changed 记录。
        for rendered in (html_text, markdown):
            self.assertNotIn("Receiver electrical operating limits", rendered)
            self.assertNotIn("Output jitter measurement limits", rendered)
        self.assertEqual(2, len(payload["table_changes"]))
        self.assertTrue(all(change["caption_changed"] for change in payload["table_changes"]))

    def test_wrapped_table_rows_remain_physical_without_geometry_evidence(self) -> None:
        """Text shape alone cannot prove that two physical table rows are one row."""

        rows = [
            ["Characteristic", "Symbol", "Condition", "MIN.", "TYP.", "MAX.", "UNIT"],  # 七列表头模拟 OIF jitter 表。
            [
                "Uncorrelated Jitter (time interval from 0.0025% to",
                "T_J",
                "See Note1",
                "",
                "",
                "0.121",
                "UI",
            ],  # 第一物理行带主符号和值。
            [
                "99.9975% of the probability distribution)",
                "4.3u03",
                "",
                "",
                "",
                "",
                "",
            ],  # 第二物理行只是上一行描述和符号的续写。
            ["Even-Odd Jitter", "T_EOJ03", "", "", "", "0.025", "UIpp"],  # 后续真实独立行不能被吞并。
        ]

        lines = _table_lines_from_rows(rows, 2)  # 走真实表格行格式化和续行合并路径。

        self.assertEqual(3, len(lines))  # 没有 rowspan/bbox 证据时，三条物理行必须全部保留。
        self.assertIn("Characteristic=Uncorrelated Jitter (time interval from 0.0025% to", lines[0])
        self.assertIn("Symbol=T_J", lines[0])
        self.assertIn("MAX=0.121", lines[0])  # 上一行数值不能丢失。
        self.assertIn("Characteristic=99.9975% of the probability distribution)", lines[1])
        self.assertIn("Symbol=4.3u03", lines[1])  # 看似后缀的内容仍可能是独立事实。
        self.assertIn("Symbol=T_EOJ03", lines[2])  # 后续独立行应保持独立。

    def test_partial_structured_coverage_does_not_hide_unrepresented_raw_table_text(self) -> None:
        """One structured row cannot justify deleting a larger opaque raw table block."""

        old_raw = (
            "T_J 0.121 UI 99.9975% of the probability distribution) 4.3u03 "
            "Uncorrelated jitter RMS (standard deviation of See Note 1 T_J 0.023 UIrms "
            "the probability distribution) RMS03 Even-Odd Jitter T_EOJ 0.025 UIpp 03 NOTES:"
        )  # 模拟 pdfplumber 正文路径抽出的低质量表格整块。
        new_raw = "T_JH 0.121 UI 99.9975% of the probability distribution) 4.3u"  # 模拟新版短表格碎片。
        old_structured = (
            "表格行: T2 | Characteristic=Uncorrelated Jitter (time interval from 0.0025% to / "
            "99.9975% of the probability distribution) | Symbol=T_J4.3u03 | Condition=See Note1 | MAX=0.121 | UNIT=UI"
        )  # 结构化行是应该展示的表格差异来源。
        new_structured = old_structured.replace("T_J4.3u03", "T_JH4.3u")  # 新版符号变化。
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_raw_table.pdf"),
                pages=[PageText(page_number=1, text=f"1 Scope\n{old_raw}\n{old_structured}")],
            ),
            ExtractionResult(
                pdf_path=Path("new_raw_table.pdf"),
                pages=[PageText(page_number=1, text=f"1 Scope\n{new_raw}\n{new_structured}")],
            ),
            DiffOptions(),
        )  # 比较层应删除原始表格碎片，只保留结构化表格行。

        snippets = "\n".join(
            "\n".join(change.added_snippets + change.removed_snippets)
            + "\n"
            + "\n".join(pair.old + "\n" + pair.new for pair in change.replaced_snippets)
            for change in result.changes
        )  # 收集所有用户可见片段。

        self.assertIn("T_J 0.121 UI 99.9975%", snippets)  # 旧块还包含未被结构化行覆盖的其它记录。
        self.assertIn("T_JH 0.121 UI 99.9975%", snippets)  # 纯文字输入没有行级 bbox，保守保留。
        self.assertNotIn("Symbol=T_J4.3u03", snippets)  # 结构化表格行也不再重复进入正文区。
        self.assertNotIn("Symbol=T_JH4.3u", snippets)  # 表格符号变化交给表格摘要展示。

        prose_result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_table_symbol_prose.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Scope\n"
                            "The value T_JH4.3u is 0.121 UI in this example.\n"
                            f"{new_structured}"
                        ),
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new_table_symbol_prose.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Scope\n"
                            "The value T_JH4.3u is 0.122 UI in this example.\n"
                            f"{new_structured}"
                        ),
                    )
                ],
            ),
            DiffOptions(),
        )  # 正常英文句子不能因包含表格符号和单位而被短碎片规则隐藏。
        prose_snippets = "\n".join(
            f"{pair.old}\n{pair.new}"
            for change in prose_result.changes
            for pair in change.replaced_snippets
        )
        self.assertIn("0.121 UI", prose_snippets)
        self.assertIn("0.122 UI", prose_snippets)

    def test_figure_formula_and_axis_text_are_preserved_without_layout_evidence(self) -> None:
        """Selectable text cannot be discarded merely because its words resemble a visual region."""

        figure_block = (  # 模拟当前报告中 Figure 32-2 被抽成的坐标轴和公式长块。
            "Figure 32-2.Channel Insertion Loss Limit for 112 Gsym/s "
            "0 5 10 15 20 25 30 35 0 10 20 30 40 50 60 70 80 90 "
            "Frequency (GHz) IL min IL max f  112 f  112 IL = 0.9837 + 1.393"
        )
        cleaned = _clean_extracted_page_text(
            "\n".join(
                [
                    "The common-mode return loss limit is shown in Figure 32-3.",  # 普通正文引用必须保留。
                    figure_block,  # 独立图形块必须删除。
                    "Amplitude X 0.05 UI X X X pp fb/ 2656000 fb/ 26560 10f",  # 无 Figure 标题的图轴碎片也要删除。
                ]
            )
        )  # 抽取层先清掉图片块，减少后续章节 diff 噪声。

        self.assertIn("shown in Figure 32-3", cleaned)  # 正文引用 Figure 仍然存在。
        self.assertIn("Figure 32-2.Channel", cleaned)  # 纯文字入口没有 bbox，不能断言该行来自图片。
        self.assertIn("Amplitude X", cleaned)  # 轴标签也可能是正文、表单字段或可比较标注。

        figure_prose = _clean_extracted_page_text(
            "Figure 32-2 shows the insertion loss limit for 112 Gsym/s with IL min, "
            "0 5 10 15 20 25 GHz reference points."
        )  # 句首 Figure 正文即使包含插损/频率数字，也不能被图形过滤误删。
        self.assertIn("Figure 32-2 shows the insertion loss limit", figure_prose)

        split_chart_text = _clean_extracted_page_text(
            "\n".join(
                [
                    "25",  # 曲线坐标刻度可能被单独抽成多行。
                    "30",  # 曲线坐标刻度可能被单独抽成多行。
                    "35",  # 曲线坐标刻度可能被单独抽成多行。
                    "Frequency (GHz)",  # 裸坐标轴标题不能被后续分节器当成章节。
                    "IL min = ------  f – 1 1GHz  f  68GHz",  # 图形公式残片必须删除。
                    "The channel must comply with the normative specification in Section 32.2.4.2.",  # 正文仍保留。
                ]
            )
        )  # 覆盖真实 OIF 报告里图轴和 IL 公式拆成多行的情况。
        self.assertIn("Frequency (GHz)", split_chart_text)
        self.assertIn("IL min =", split_chart_text)
        self.assertIn("normative specification", split_chart_text)  # 同页普通段落仍应保留。

        old_extraction = ExtractionResult(
            pdf_path=Path("old.pdf"),  # 旧侧只包含一个图片块变化。
            pages=[PageText(page_number=1, text="1 Scope\n" + figure_block)],
            total_pages=1,
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new.pdf"),  # 新侧图片块数字变化，但图片不需要比较。
            pages=[PageText(page_number=1, text="1 Scope\n" + figure_block.replace("0.9837", "1.0000"))],
            total_pages=1,
        )
        figure_only_result = compare_extractions(old_extraction, new_extraction, DiffOptions())  # 比较层也要兜底隐藏。

        self.assertTrue(figure_only_result.changes)  # 没有布局证据时，公式数值变化必须可见。

        axis_label_result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_axis.pdf"),  # 旧侧没有图轴标签残片。
                pages=[PageText(page_number=1, text="1 Scope\nStable requirement.")],
                total_pages=1,
            ),
            ExtractionResult(
                pdf_path=Path("new_axis.pdf"),  # 新侧只有图片坐标轴标签残片。
                pages=[PageText(page_number=1, text="1 Scope\nStable requirement.\nAmplitude X")],
                total_pages=1,
            ),
            DiffOptions(),
        )  # 比较层必须隐藏短轴标签新增。

        self.assertTrue(axis_label_result.changes)

        formula_label_result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_formula_axis.pdf"),  # 旧侧只有图中 IL 公式残片。
                pages=[
                    PageText(
                        page_number=1,
                        text="1 Scope\nIL min = ------  f – 1 1GHz  f  68GHz",
                    )
                ],
                total_pages=1,
            ),
            ExtractionResult(
                pdf_path=Path("new_formula_axis.pdf"),  # 新侧只有正文稳定内容。
                pages=[PageText(page_number=1, text="1 Scope")],
                total_pages=1,
            ),
            DiffOptions(),
        )  # 比较层兜底隐藏已经进入输入的图片公式残片。

        self.assertTrue(formula_label_result.changes)

        corrupt_figure_result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_corrupt_figure.pdf"),  # 旧侧只有公式尾巴和损坏 Figure 引用。
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Scope\n"
                            "6) (TBI).\n"
                            "The differential to common-mode return loss limit RL (f) is shown in Figure cd 32-5."
                        ),
                    )
                ],
                total_pages=1,
            ),
            ExtractionResult(
                pdf_path=Path("new_corrupt_figure.pdf"),  # 新侧只有损坏 Figure 引用和公式残片。
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Scope\n"
                            "The differential to common-mode return loss limit RL (f) is shown in Figure 32- R cd\n"
                            "5. cd cd  b  (32-8) b b"
                        ),
                    )
                ],
                total_pages=1,
            ),
            DiffOptions(),
        )  # 损坏 Figure 引用配公式尾巴时属于图形/抽取噪声，不应展示。
        corrupt_snippets = "\n".join(
            "\n".join(change.added_snippets + change.removed_snippets)
            + "\n"
            + "\n".join(pair.old + "\n" + pair.new for pair in change.replaced_snippets)
            for change in corrupt_figure_result.changes
        )
        self.assertIn("Figure 32- R cd", corrupt_snippets)
        self.assertIn("Figure cd 32-5", corrupt_snippets)
        self.assertTrue(
            any("6) (TBI)" in section.location for section in corrupt_figure_result.old_sections)
        )  # 即使启发式把短编号行识别成标题，文字仍保留在报告定位中。

        orphan_symbol_result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_symbol_noise.pdf"),  # 旧侧只有表格/图形残片。
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Scope\n"
                            "4.3u03 RMS03\n"
                            "Frequency Range peak-to-peak (UI) b b b b b CRU 5 UI X pp\n"
                            "SNDR = 10log  (32-5) 10 2 2 e n\n"
                            "N - 1 T p 2 Signal 0 i = 0"
                        ),
                    )
                ],
                total_pages=1,
            ),
            ExtractionResult(
                pdf_path=Path("new_symbol_noise.pdf"),  # 新侧没有这些残片。
                pages=[PageText(page_number=1, text="1 Scope")],
                total_pages=1,
            ),
            DiffOptions(),
        )  # 不透明符号可能是方程或变量，纯文字输入不足以证明它们是视觉噪声。
        self.assertTrue(orphan_symbol_result.changes)

        figure_prose_result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_figure_prose.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Scope\n"
                            "Figure 32-2 shows the insertion loss limit for 112 Gsym/s "
                            "with IL min, 0 5 10 15 20 25 GHz reference points."
                        ),
                    )
                ],
                total_pages=1,
            ),
            ExtractionResult(
                pdf_path=Path("new_figure_prose.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Scope\n"
                            "Figure 32-3 shows the insertion loss limit for 112 Gsym/s "
                            "with IL min, 0 5 10 15 20 25 GHz reference points."
                        ),
                    )
                ],
                total_pages=1,
            ),
            DiffOptions(),
        )  # 比较层同样不能把句首 Figure 正文当图片公式删除。
        figure_prose_snippets = "\n".join(
            f"{pair.old}\n{pair.new}"
            for change in figure_prose_result.changes
            for pair in change.replaced_snippets
        )
        self.assertIn("Figure 32-2", figure_prose_snippets)
        self.assertIn("Figure 32-3", figure_prose_snippets)

        reference_old = ExtractionResult(
            pdf_path=Path("old_ref.pdf"),  # 旧侧包含真实正文 Figure 引用。
            pages=[PageText(page_number=1, text="1 Scope\nThe return loss is shown in Figure 32-5.")],
            total_pages=1,
        )
        reference_new = ExtractionResult(
            pdf_path=Path("new_ref.pdf"),  # 新侧正文引用编号变化，应继续报告。
            pages=[PageText(page_number=1, text="1 Scope\nThe return loss is shown in Figure 32-6.")],
            total_pages=1,
        )
        reference_result = compare_extractions(reference_old, reference_new, DiffOptions())  # 普通正文变化仍走原 diff。
        reference_snippets = "\n".join(
            f"{pair.old}\n{pair.new}"
            for change in reference_result.changes
            for pair in change.replaced_snippets
        )  # 收集替换片段，确认 Figure 引用没有被误删。

        self.assertIn("Figure 32-5", reference_snippets)  # 旧 Figure 引用应可见。
        self.assertIn("Figure 32-6", reference_snippets)  # 新 Figure 引用应可见。

        sentence_initial_result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_figure_sentence.pdf"),  # 旧侧句首以 Figure 开始，但这是正文句子。
                pages=[PageText(page_number=1, text="1 Scope\nFigure 32-2 shows the minimum attenuation limit.")],
                total_pages=1,
            ),
            ExtractionResult(
                pdf_path=Path("new_figure_sentence.pdf"),  # 新侧只改 Figure 引用编号，必须报告。
                pages=[PageText(page_number=1, text="1 Scope\nFigure 32-3 shows the minimum attenuation limit.")],
                total_pages=1,
            ),
            DiffOptions(),
        )  # 句首 Figure 正文不能被当作图题整句删除。
        sentence_snippets = "\n".join(
            f"{pair.old}\n{pair.new}"
            for change in sentence_initial_result.changes
            for pair in change.replaced_snippets
        )  # 收集句首 Figure 引用差异。

        self.assertIn("Figure 32-2", sentence_snippets)  # 旧句首 Figure 正文应保留。
        self.assertIn("Figure 32-3", sentence_snippets)  # 新句首 Figure 正文应保留。

    def test_same_named_multipage_tables_pair_by_document_order(self) -> None:
        """Same table captions should pair like the visual report's cross-page groups."""

        old_tables = [
            TableVisual(
                page_number=4,  # 旧版 Table 32-1 第一页。
                table_number=1,  # 单页第一个表。
                title="Table 32-1. COM Parameter Values",  # 同名跨页表题。
                bbox=(0.0, 0.0, 100.0, 100.0),  # 测试只需要稳定 bbox。
                image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",  # 极小图片占位。
                row_texts=["表格行: T1 | Parameter=Class A | Value=old"],  # 第一页行内容和新版可能差异很大。
                grid_summary="OpenCV 网格检测: 横线 4 条，竖线 3 条",  # 网格摘要用于报告渲染。
            ),
            TableVisual(
                page_number=5,  # 旧版 Table 32-1 第二页。
                table_number=1,  # 单页第一个表。
                title="Table 32-1. COM Parameter Values",  # 同一表题应继续按顺序配对。
                bbox=(0.0, 0.0, 100.0, 100.0),  # 测试只需要稳定 bbox。
                image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",  # 极小图片占位。
                row_texts=["表格行: T1 | Parameter=Class B | Value=old"],  # 第二页代表另一段表格。
                grid_summary="OpenCV 网格检测: 横线 4 条，竖线 3 条",  # 网格摘要用于报告渲染。
            ),
        ]
        new_tables = [
            TableVisual(
                page_number=6,  # 新版 Table 32-1 第一页，页码因前文插入而后移。
                table_number=1,  # 单页第一个表。
                title="Table 32-1. COM Parameter Values",  # 同名表题提供强配对锚点。
                bbox=(0.0, 0.0, 100.0, 100.0),  # 测试只需要稳定 bbox。
                image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",  # 极小图片占位。
                row_texts=["表格行: T1 | Parameter=New intro row | Value=new"],  # 行文本不同也不应导致错配。
                grid_summary="OpenCV 网格检测: 横线 4 条，竖线 3 条",  # 网格摘要用于报告渲染。
            ),
            TableVisual(
                page_number=7,  # 新版 Table 32-1 第二页。
                table_number=1,  # 单页第一个表。
                title="Table 32-1. COM Parameter Values",  # 同名表题提供强配对锚点。
                bbox=(0.0, 0.0, 100.0, 100.0),  # 测试只需要稳定 bbox。
                image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",  # 极小图片占位。
                row_texts=["表格行: T1 | Parameter=Class B | Value=new"],  # 第二页和旧第二页对齐。
                grid_summary="OpenCV 网格检测: 横线 4 条，竖线 3 条",  # 网格摘要用于报告渲染。
            ),
            TableVisual(
                page_number=8,  # 新版多出来的续页。
                table_number=1,  # 单页第一个表。
                title="Table 32-1. COM Parameter Values",  # 同名表题但没有旧侧对应页。
                bbox=(0.0, 0.0, 100.0, 100.0),  # 测试只需要稳定 bbox。
                image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",  # 极小图片占位。
                row_texts=["表格行: T1 | Parameter=Tail | Value=new"],  # 新增续页应保持单侧展示。
                grid_summary="OpenCV 网格检测: 横线 4 条，竖线 3 条",  # 网格摘要用于报告渲染。
            ),
        ]

        groups = _paired_table_visuals(old_tables, new_tables)  # 直接测试报告层的表格配组策略。

        self.assertEqual([4, 5], [table.page_number for table in groups[0].old_tables])  # 旧版同名跨页表应合为一组。
        self.assertEqual([6, 7, 8], [table.page_number for table in groups[0].new_tables])  # 新版新增续页也应并入同组。
        self.assertEqual(1, len(groups))  # 同名跨页表不应再拆成多个单页卡片。

    def test_captioned_table_groups_absorb_untitled_continuation_pages(self) -> None:
        """A repeated boundary-row identity can prove an untitled continuation."""

        old_tables = [
            TableVisual(
                page_number=4,
                table_number=1,
                title="Table 32-1. COM Parameter Values",
                bbox=(0.0, 0.0, 100.0, 100.0),
                image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
                row_texts=["表格行: T1 | Parameter=Boundary | Value=old head"],
                grid_summary="OpenCV 网格检测: 横线 4 条，竖线 3 条",
            ),
            TableVisual(
                page_number=5,
                table_number=1,
                title="",
                bbox=(0.0, 0.0, 100.0, 100.0),
                image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
                row_texts=["表格行: T1 | Parameter=Boundary | Value=old tail"],
                grid_summary="OpenCV 网格检测: 横线 4 条，竖线 3 条",
                is_continuation=True,
            ),
        ]  # 旧版续页首行与上一页末行共享参数身份，提供跨页断行的正证据。
        new_tables = [
            TableVisual(
                page_number=6,
                table_number=1,
                title="Table 32-1. COM Parameter Values",
                bbox=(0.0, 0.0, 100.0, 100.0),
                image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
                row_texts=["表格行: T1 | Parameter=Boundary | Value=new head"],
                grid_summary="OpenCV 网格检测: 横线 4 条，竖线 3 条",
            ),
            TableVisual(
                page_number=7,
                table_number=1,
                title="",
                bbox=(0.0, 0.0, 100.0, 100.0),
                image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
                row_texts=["表格行: T1 | Parameter=Boundary | Value=new tail"],
                grid_summary="OpenCV 网格检测: 横线 4 条，竖线 3 条",
                is_continuation=True,
            ),
        ]  # 新版使用同一边界行证据，避免单靠 ``is_continuation`` 猜测。

        groups = _paired_table_visuals(old_tables, new_tables)

        self.assertEqual(1, len(groups))  # 有标题首页和无标题续页应合成一个逻辑表格组。
        self.assertEqual([4, 5], [table.page_number for table in groups[0].old_tables])
        self.assertEqual([6, 7], [table.page_number for table in groups[0].new_tables])

    def test_table_visual_summary_reports_omissions_and_avoids_bad_pairing(self) -> None:
        """Visual table summaries should avoid false pairs and visible truncation."""

        old_rows = [f"表格行: T1 | Parameter=P{index} | Value={index}" for index in range(28)]  # 构造 28 行旧表变化。
        new_rows = [f"表格行: T1 | Parameter=P{index} | Value={index + 1}" for index in range(28)]  # 构造 28 行新表变化。
        old_table = TableVisual(
            page_number=1,
            table_number=1,
            title="Table 1 Matching rows",
            bbox=(0.0, 0.0, 100.0, 100.0),
            image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
            row_texts=old_rows,
            grid_summary="OpenCV 网格检测: 横线 4 条，竖线 3 条",
        )
        new_table = TableVisual(
            page_number=1,
            table_number=1,
            title="Table 1 Matching rows",
            bbox=(0.0, 0.0, 100.0, 100.0),
            image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
            row_texts=new_rows,
            grid_summary="OpenCV 网格检测: 横线 4 条，竖线 3 条",
        )
        old_unmatched = TableVisual(
            page_number=2,
            table_number=1,
            title="Table A Old only",
            bbox=(0.0, 0.0, 100.0, 100.0),
            image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
            row_texts=["表格行: T1 | Parameter=Old only | Value=1"],
            grid_summary="OpenCV 网格检测: 横线 2 条，竖线 2 条",
        )
        new_unmatched = TableVisual(
            page_number=2,
            table_number=1,
            title="Completely different inserted table",
            bbox=(0.0, 0.0, 100.0, 100.0),
            image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
            row_texts=["表格行: T1 | Parameter=Inserted | Value=2"],
            grid_summary="OpenCV 网格检测: 横线 2 条，竖线 2 条",
        )
        old_math = TableVisual(
            page_number=3,
            table_number=1,
            title="Table math notation",
            bbox=(0.0, 0.0, 100.0, 100.0),
            image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
            row_texts=["表格行: T1 | Parameter=Clock relation | Value=fb*n"],
            grid_summary="OpenCV 网格检测: 横线 2 条，竖线 2 条",
        )
        new_math = TableVisual(
            page_number=3,
            table_number=1,
            title="Table math notation",
            bbox=(0.0, 0.0, 100.0, 100.0),
            image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
            row_texts=["表格行: T1 | Parameter=Clock relation | Value=fb×n"],
            grid_summary="OpenCV 网格检测: 横线 2 条，竖线 2 条",
        )
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old.pdf"),
                pages=[PageText(page_number=1, text="1 Scope\nNo text change.")],
                total_pages=3,
                table_visuals=[old_table, old_unmatched, old_math],
            ),
            ExtractionResult(
                pdf_path=Path("new.pdf"),
                pages=[PageText(page_number=1, text="1 Scope\nNo text change.")],
                total_pages=3,
                table_visuals=[new_table, new_unmatched, new_math],
            ),
            DiffOptions(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, temp_dir, DiffOptions())
            html = paths["html"].read_text(encoding="utf-8")
            payload = json.loads(paths["json"].read_text(encoding="utf-8"))

        self.assertIn("另有 8 行表格变化未展示", html)  # 28 行变化展示 20 行时必须说明遗漏数量。
        self.assertGreaterEqual(html.count("无对应表格截图"), 2)  # 不相似的新旧表不能按顺序硬凑成一组。
        self.assertNotIn("Table math notation", html)  # fb*n 与 fb×n 等价，整张未变化表不进入报告。
        self.assertTrue(
            all(
                change["pair_similarity"] is None
                for change in payload["table_changes"]
                if not change["old_pages"] or not change["new_pages"]
            )
        )  # 单侧表格的配对分数不适用，JSON 应和 HTML/CSV 一样留空。

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

        import tkinter as tk  # Tk 用于创建真实 GUI 控件，验证页码框可编辑。

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)  # 临时目录隔离测试 PDF 和报告输出。
            old_pdf, new_pdf = write_demo_pdfs(temp_path / "inputs")  # 生成可抽取文本的示例 PDF。
            root = tk.Tk()  # 创建真实 Tk 窗口，便于测试输入控件行为。
            root.geometry("980x700+0+0")  # 固定窗口大小，贴近用户打开 GUI 的首屏尺寸。
            try:
                app = ProtocolDiffDesktopApp(root)  # 构建完整桌面应用。
                root.update()  # 让窗口完成布局和焦点初始化。
                app.old_pdf_var.set(str(old_pdf))  # 填入旧 PDF 路径。
                app.new_pdf_var.set(str(new_pdf))  # 填入新 PDF 路径。
                app.output_dir_var.set(str(temp_path / "reports"))  # 填入报告输出目录。
                app.min_similarity_var.set("0.8")  # 调整章节匹配阈值，验证高级参数仍能读取。
                app.max_snippets_var.set("12")  # 调整片段数量，验证数字输入仍能读取。
                app.include_unchanged_var.set(True)  # 打开未变化章节选项。
                widget_texts = collect_widget_texts(root)  # 收集当前窗口所有可见控件文案。

                page_values = {  # 这些值模拟用户逐个点击页码框并键盘输入。
                    "旧协议起始页": "2",
                    "旧协议终止页": "3",
                    "新协议起始页": "2",
                    "新协议终止页": "4",
                }
                for label, value in page_values.items():
                    entry = app.page_entry_widgets[label]  # 取到真实页码输入框，而不是直接写 StringVar。
                    entry.focus_force()  # 模拟用户点击该输入框获得焦点。
                    root.update()  # 处理焦点事件，确保后续键盘事件送到该控件。
                    entry.delete(0, tk.END)  # 清空旧值，模拟重新输入。
                    entry.insert(0, value)  # 直接走 Tk 文本插入接口，避免 Windows runner 不派发合成字符按键。
                    root.update()  # 处理控件状态和布局，让输入框文本完成更新。

                page_entry_facts = {
                    label: (entry.winfo_class(), entry.winfo_manager(), entry.get())
                    for label, entry in app.page_entry_widgets.items()
                }  # 在销毁窗口前记录页码输入框的类型、布局状态和实际输入结果。
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
        self.assertFalse(hasattr(app, "unchanged_similarity_var"))  # 非技术界面不再暴露会误导用户的无效阈值。
        self.assertEqual(12, config.options.max_snippets_per_section)
        self.assertIsNone(config.options.ocr_language)  # 图形界面不应暴露只在扫描页才生效的语言代码。
        self.assertTrue(config.options.include_unchanged_sections)
        self.assertEqual(str(default_output_dir()), str(Path.home() / "Documents" / "ProtocolPdfDiffReports"))  # 默认输出目录不能落到 app 包内部。
        self.assertIn("旧协议起始页", widget_texts)  # 旧 PDF 起始页输入标签必须存在。
        self.assertIn("旧协议终止页", widget_texts)  # 旧 PDF 终止页输入标签必须存在。
        self.assertIn("新协议起始页", widget_texts)  # 新 PDF 起始页输入标签必须存在。
        self.assertIn("新协议终止页", widget_texts)  # 新 PDF 终止页输入标签必须存在。
        self.assertIn("开始比较", widget_texts)  # 主运行按钮必须存在且文案保持简洁。
        self.assertNotIn("填入 Demo 文件", widget_texts)  # 用户界面不保留示例输入入口。
        self.assertNotIn("OCR 语言", widget_texts)
        self.assertNotIn("扫描页 OCR", widget_texts)
        self.assertEqual(4, len(page_entry_facts))  # 四个页码输入框必须真实创建。
        self.assertEqual(3, len(browse_button_facts))  # 旧 PDF、新 PDF、输出目录三行都必须有“选择”按钮。
        expected_page_values = {
            "旧协议起始页": "2",
            "旧协议终止页": "3",
            "新协议起始页": "2",
            "新协议终止页": "4",
        }  # 期望输入框通过键盘事件得到的最终值。
        for label, (widget_class, layout_manager, typed_value) in page_entry_facts.items():
            self.assertEqual("Entry", widget_class, f"{label} 应该是原生可输入控件")  # 防止只剩标签没有输入框。
            self.assertEqual("grid", layout_manager, f"{label} 应该已加入布局")  # 防止控件存在但不可见。
            self.assertEqual(expected_page_values[label], typed_value, f"{label} 应该能接收键盘输入")  # 防止无法输入页码的回归。
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
            self.assertEqual("reliable", result.assessment.state)
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

    @unittest.skipUnless(
        OIF_OLD_SAMPLE.exists() and OIF_NEW_SAMPLE.exists(),
        "OIF regression PDFs are local user samples",
    )
    def test_oif_table_changes_move_from_text_cards_to_visual_summary(self) -> None:
        """The OIF COM table should expose row changes in the visual table summary."""

        result = run_diff(  # 运行完整 PDF 比较链路，复现用户反馈的真实表格场景。
            OIF_OLD_SAMPLE,
            OIF_NEW_SAMPLE,
            DiffOptions(max_snippets_per_section=80),
        )
        snippet_text = "\n".join(  # 汇总所有正文片段，便于断言结构化表格行不再重复展示。
            "\n".join(change.added_snippets + change.removed_snippets)
            + "\n".join(f"{pair.old}\n{pair.new}" for pair in change.replaced_snippets)
            for change in result.changes
        )
        with tempfile.TemporaryDirectory() as temp_dir:  # 报告写到临时目录，验证最终 HTML 展示而非内部对象。
            paths = write_reports(result, temp_dir, DiffOptions(max_snippets_per_section=80))  # 生成完整报告，覆盖视觉表格区。
            html = paths["html"].read_text(encoding="utf-8")  # 读取 HTML 断言用户实际看到的内容。
            payload = json.loads(paths["json"].read_text(encoding="utf-8"))  # JSON 必须保留和 HTML 同一批表格变化。
            table_csv = paths["table_csv"].read_text(encoding="utf-8-sig")  # CSV 用于确认表格事实没有只存在于 HTML。

        self.assertNotIn("表格行:", snippet_text)  # 正文卡片不应再重复内部结构化表格行。
        self.assertNotIn("表格行:", html)  # HTML 报告不应暴露内部表格行格式。
        self.assertIn("表格补充证据（变化与复核）", html)  # 表格事实应进入统一导航和视觉证据区。
        self.assertIn("Single-ended reference resistance", html)  # 用户关心的参数名仍必须可搜索定位。
        self.assertIn("46.25", html)  # 新版值必须在表格摘要或截图区可见。
        self.assertIn("50", html)  # 旧版值必须在表格摘要或截图区可见。
        self.assertIn("Np = 53", snippet_text)  # 已知真实正文数值变化必须继续保留。
        self.assertIn("Np = 60", snippet_text)
        combined_report = html + json.dumps(payload["table_changes"], ensure_ascii=False) + table_csv
        for false_positive in (
            "foAr",
            "vaTlues",
            "specifDications",
            "canA",
            "Afmin",
            "| T mVppd",
        ):
            self.assertNotIn(false_positive, combined_report)  # 真实 DRAFT 水印和列错位残片不得再次进入报告。
        self.assertIn("3.2×x10", combined_report)  # 无布局证据时，畸形运算符仍是可核查原文而非可删除噪声。
        self.assertNotIn("OIF 2024.058.13 30th June 2026", snippet_text)
        self.assertNotIn("Updated based on comment resolution spreadsheet oif2026.245.01.", snippet_text)
        self.assertIn("OIF 2024.058.13", table_csv)  # 修订历史只由表格事实系统承载一次。
        self.assertIn("Updated based on comment resolution spreadsheet oif2026.245.01.", table_csv)
        technical_table_count = sum(
            change["role"] == "technical" for change in payload["table_changes"]
        )  # 读者 HTML 只导航技术表，出版/修订表继续留在 JSON 与 table CSV 审计面。
        self.assertEqual(
            technical_table_count,
            html.count('href="#table-change-'),
        )
        self.assertTrue(
            all(
                len(change["old_titles"]) == len(set(change["old_titles"]))
                and len(change["new_titles"]) == len(set(change["new_titles"]))
                for change in payload["table_changes"]
            )
        )  # 跨页表格的重复表题不能让 JSON 和 HTML/CSV 口径分裂。
        self.assertTrue(result.changes)  # 用户样本只做端到端回归，不能反向定义通用配对阈值。

    def test_table_rows_scan_headers_and_expand_continuation_cells(self) -> None:
        """Table extraction should recover headers and split multi-row cells."""

        header_delayed_rows = [  # 模拟 pdfplumber 把表题放在表头之前的情况。
            ["Table 1 - Electrical parameters"],
            ["NOTE 1: Values are measured at package edge."],
            ["NOTE 2: Reserved rows are omitted."],
            ["Class B operating point"],
            ["Unless otherwise specified"],
            ["All values are reference values"],
            ["Parameter", "Symbol", "Value", "Units"],
            ["Single-ended reference resistance", "R\n0", "46.25", "Ω"],
        ]
        delayed_lines = _table_lines_from_rows(header_delayed_rows, table_number=1)  # 表头不在第一行也应被扫描到。

        continuation_rows = [  # 模拟 OIF 续页：没有表头，多个参数被塞进同一个物理表格行。
            [
                "Device package model interface parameters\n"
                "Single-ended reference resistance\n"
                "Single-ended termination resistance",
                "R\n0\nR\nd",
                "50\n46.25",
                "Ω\nΩ",
            ]
        ]
        continuation_lines = _table_lines_from_rows(continuation_rows, table_number=1)  # 缺表头时应按 4 列参数表补默认列名。

        self.assertIn(
            "表格行: T1 | Parameter=Single-ended reference resistance | Symbol=R0 | Value=46.25 | Units=Ω",
            delayed_lines,
        )  # 延迟表头路径必须输出 Header=Value，而不是裸列文本。
        self.assertEqual(1, len(continuation_lines))
        continuation = continuation_lines[0]
        self.assertIn(r"Device package model interface parameters\nSingle-ended reference resistance", continuation)
        self.assertIn(r"Column 2=R\n0\nR\nd", continuation)
        self.assertIn(r"Column 3=50\n46.25", continuation)
        self.assertIn(r"Column 4=Ω\nΩ", continuation)

    def test_parameter_cell_rejoins_subscripts_before_aligned_row_expansion(self) -> None:
        """A grouped parameter cell should recover JRMS, EOJ03, and JH4u rows."""

        rows = [
            ["Parameter", "Min.", "Max.", "Unit", "Conditions"],
            [
                "Output Jitter\nJ\nRMS\nEOJ\n03\nJH\n4u",
                "-\n-\n-",
                "0.023\n0.025\n0.118",
                "UI\nUI\nUI",
                "See 31.3.13",
            ],
        ]

        lines = _table_lines_from_rows(rows, table_number=1)

        self.assertEqual(3, len(lines))
        self.assertIn("Parameter=Output Jitter\\nJRMS", lines[0])
        self.assertIn("Min=- | Max=0.023 | Unit=UI", lines[0])
        self.assertIn("Parameter=EOJ03 | Min=- | Max=0.025 | Unit=UI", lines[1])
        self.assertIn("Parameter=JH4u | Min=- | Max=0.118 | Unit=UI", lines[2])
        self.assertTrue(all("Conditions=See 31.3.13" in line for line in lines))

    def test_geometry_rejoins_subscripts_before_multiline_row_decision(self) -> None:
        """Visual subscript evidence must not become a second logical table row."""

        rows = [
            ["Characteristic", "Symbol", "Condition", "MIN.", "TYP.", "MAX.", "UNIT"],
            [
                "Uncorrelated jitter RMS (standard deviation of\n"
                "the probability distribution)",
                "T_J\nRMS03",
                "See Note1",
                "",
                "",
                "0.023",
                "UI\nRMS",
            ],
            ["Even-Odd Jitter", "T_EOJ\n03", "", "", "", "0.025", "UIpp"],
        ]

        def word(text: str, x0: float, x1: float, top: float, size: float) -> dict[str, object]:
            return {
                "text": text,
                "x0": x0,
                "x1": x1,
                "top": top,
                "bottom": top + size,
                "size": size,
            }

        cell_word_rows = [
            [[] for _cell in rows[0]],
            [
                [],
                [word("T_J", 100.0, 113.0, 20.0, 8.0), word("RMS03", 113.1, 134.0, 23.2, 6.4)],
                [],
                [],
                [],
                [],
                [word("UI", 200.0, 208.0, 20.0, 8.0), word("RMS", 208.1, 222.0, 23.2, 6.4)],
            ],
            [
                [],
                [word("T_EOJ", 100.0, 124.0, 40.0, 8.0), word("03", 124.1, 131.0, 43.2, 6.4)],
                [],
                [],
                [],
                [],
                [],
            ],
        ]

        lines = _table_lines_from_rows(
            rows,
            table_number=1,
            cell_word_rows=cell_word_rows,
        )

        self.assertEqual(2, len(lines))
        self.assertIn(
            "Characteristic=Uncorrelated jitter RMS (standard deviation of\\n"
            "the probability distribution)",
            lines[0],
        )
        self.assertIn("Symbol=T_JRMS03", lines[0])
        self.assertIn("MAX=0.023 | UNIT=UIRMS", lines[0])
        self.assertIn("Symbol=T_EOJ03", lines[1])
        self.assertNotIn("Symbol=T_J |", "\n".join(lines))
        self.assertNotIn("Symbol=RMS03", "\n".join(lines))

    def test_geometry_reorders_subscript_before_parenthesized_qualifier(self) -> None:
        """A symbol's lowered suffix must precede its raised qualifier."""

        rows = [
            ["Parameter", "Symbol", "Value", "Units"],
            [
                "Device termination model\n"
                "Single-ended device capacitance for stage 1\n"
                "Single-ended device capacitance for stage 2\n"
                "Single-ended device capacitance for stage 3",
                "C (1)\nd\nC (2)\nd\nC (3)\nd",
                "40\n90\n110",
                "fF\nfF\nfF",
            ],
            ["Normalized DFE coefficient maximum limit", "b (1)\nDFE_max", "0.85", "—"],
            ["Normalized FFE main tap coefficient minimum limit", "b (0)\nmin", "0.7", "—"],
        ]

        def word(text: str, x0: float, x1: float, top: float, height: float) -> dict[str, object]:
            return {
                "text": text,
                "x0": x0,
                "x1": x1,
                "top": top,
                "bottom": top + height,
            }

        descriptor_words = [
            [word("Device termination model", 10, 90, 10, 9)],
            [word("Single-ended device capacitance for stage 1", 10, 180, 21, 9)],
            [word("Single-ended device capacitance for stage 2", 10, 180, 32, 9)],
            [word("Single-ended device capacitance for stage 3", 10, 180, 43, 9)],
        ]
        symbol_words = [
            word("C", 200, 206.5, 21, 9),
            word("d", 206.5, 210.5, 24.7, 7.2),
            word("(1)", 210.5, 218.7, 19.2, 6.7),
            word("C", 200, 206.5, 32, 9),
            word("d", 206.5, 210.5, 35.7, 7.2),
            word("(2)", 210.5, 218.7, 30.2, 6.7),
            word("C", 200, 206.5, 43, 9),
            word("d", 206.5, 210.5, 46.7, 7.2),
            word("(3)", 210.5, 218.7, 41.2, 6.7),
        ]
        value_words = [
            word("40", 240, 249, 21, 9),
            word("90", 240, 249, 32, 9),
            word("110", 240, 253, 43, 9),
        ]
        unit_words = [
            word("fF", 270, 278, 21, 9),
            word("fF", 270, 278, 32, 9),
            word("fF", 270, 278, 43, 9),
        ]
        cell_word_rows = [
            [[], [], [], []],
            [
                [item for line in descriptor_words for item in line],
                symbol_words,
                value_words,
                unit_words,
            ],
            [
                [],
                [
                    word("b", 200, 205, 70, 9),
                    word("DFE_max", 205, 230, 74.4, 5.5),
                    word("(1)", 230.1, 241, 70, 9),
                ],
                [],
                [],
            ],
            [
                [],
                [
                    word("b", 200, 205, 90, 9),
                    word("min", 205, 214, 94.4, 5.5),
                    word("(0)", 214.1, 225, 90, 9),
                ],
                [],
                [],
            ],
        ]

        lines = _table_lines_from_rows(
            rows,
            table_number=1,
            cell_word_rows=cell_word_rows,
        )

        self.assertIn(
            "Parameter=Device termination model — Single-ended device capacitance for stage 1 | "
            "Symbol=Cd(1) | Value=40 | Units=fF",
            "\n".join(lines),
        )
        self.assertIn("Parameter=Single-ended device capacitance for stage 2 | Symbol=Cd(2) | Value=90", "\n".join(lines))
        self.assertIn("Parameter=Single-ended device capacitance for stage 3 | Symbol=Cd(3) | Value=110", "\n".join(lines))
        self.assertIn("Symbol=bDFE_max(1) | Value=0.85", "\n".join(lines))
        self.assertIn("Symbol=bmin(0) | Value=0.7", "\n".join(lines))

    def test_geometry_rebuilds_fragmented_qualifiers_without_orphan_tokens(self) -> None:
        """Split parentheses must stay attached to the same physical symbol record."""

        rows = [
            ["Parameter", "Symbol", "Value", "Units"],
            [
                "Line length\nTermination time constant\nCharacteristic impedance",
                "z (1)\np\n\uf074(h)\nZ (h)\nc",
                "45\n0.1\n92.5",
                "mm\nns\nΩ",
            ],
        ]

        def word(text: str, x0: float, x1: float, top: float, height: float) -> dict[str, object]:
            return {
                "text": text,
                "x0": x0,
                "x1": x1,
                "top": top,
                "bottom": top + height,
            }

        cell_word_rows = [
            [[], [], [], []],
            [
                [],
                [
                    word("(", 208.0, 210.0, 18.0, 6.0),
                    word("1", 210.0, 213.0, 18.0, 6.0),
                    word(")", 213.0, 215.0, 18.0, 6.0),
                    word("z", 200.0, 204.0, 20.0, 8.0),
                    word("p", 204.0, 208.0, 23.2, 6.4),
                    word("\uf074(", 200.0, 206.0, 40.0, 9.0),
                    word("h", 206.0, 210.0, 40.0, 6.8),
                    word(")", 210.0, 212.0, 40.0, 6.8),
                    word("(h)", 208.0, 216.0, 58.0, 6.5),
                    word("Z", 200.0, 204.8, 60.0, 8.0),
                    word("c", 204.8, 208.0, 63.2, 6.4),
                ],
                [],
                [],
            ],
        ]

        lines = _table_lines_from_rows(
            rows,
            table_number=1,
            cell_word_rows=cell_word_rows,
        )

        rendered = "\n".join(lines)
        self.assertIn("Symbol=zp(1) | Value=45", rendered)
        self.assertIn("Symbol=\uf074(h) | Value=0.1", rendered)
        self.assertIn("Symbol=Zc(h) | Value=92.5", rendered)
        self.assertNotIn("Symbol=(", rendered)
        self.assertNotIn(" / ( / ", rendered)

    def test_compound_script_rejects_suffix_shared_by_two_bases(self) -> None:
        """A suffix/qualifier candidate graph must be one-to-one in both directions."""

        def word(text: str, x0: float, x1: float, top: float, height: float) -> dict[str, object]:
            return {
                "text": text,
                "x0": x0,
                "x1": x1,
                "top": top,
                "bottom": top + height,
            }

        ambiguous_words = [
            word("A", 100.0, 108.0, 10.0, 8.0),
            word("B", 100.0, 108.0, 10.2, 8.0),
            word("x", 108.1, 114.0, 13.2, 6.4),
            word("(1)", 114.1, 122.0, 9.0, 6.4),
        ]

        self.assertIsNone(
            _geometry_compound_script_lines(
                ["A", "x", "(1)", "B"],
                ambiguous_words,
            )
        )

    def test_headerless_four_column_parameter_continuation_repairs_symbol_geometry(self) -> None:
        """A long, schema-proven continuation may recover Parameter/Symbol labels."""

        rows = [
            ["Reference resistance", "R\n0", "50", "Ω"],
            ["Signaling rate maximum", "f\nb", "116", "GHz"],
            ["Number of signal levels", "L", "4", "—"],
            ["Equalizer tap length", "N\nb", "1", "UI"],
            ["Transmitter output voltage", "A\nv", "0.385", "V"],
            ["Noise ratio minimum", "SNR\nTX", "33.5", "dB"],
            ["Floating tap position", "N\nts", "9", "UI"],
            ["Normalized DFE coefficient maximum limit", "b (1)\nmax", "0.85", "—"],
        ]

        def word(text: str, x0: float, x1: float, top: float, height: float) -> dict[str, object]:
            return {
                "text": text,
                "x0": x0,
                "x1": x1,
                "top": top,
                "bottom": top + height,
            }

        cell_word_rows = [
            [[], [], [], []]
            for _row in rows
        ]
        cell_word_rows[-1][1] = [
            word("(1)", 118.1, 126.0, 9.0, 6.4),
            word("b", 100.0, 105.0, 10.0, 8.0),
            word("max", 105.1, 118.0, 13.2, 6.4),
        ]

        lines = _table_lines_from_rows(
            rows,
            table_number=1,
            cell_word_rows=cell_word_rows,
        )

        self.assertTrue(all("Parameter=" in line and "Symbol=" in line for line in lines))
        self.assertTrue(
            any(
                "Parameter=Normalized DFE coefficient maximum limit | "
                "Symbol=bmax(1) | Value=0.85 | Units=—" in line
                for line in lines
            )
        )

    def test_headerless_min_max_table_does_not_infer_a_numeric_symbol_column(self) -> None:
        """Four columns alone cannot relabel Characteristic/Min/Max/Unit as Parameter/Symbol."""

        rows = [
            [f"Receiver characteristic limit {index}", f"{index / 10:.1f}", f"{index / 10 + 0.8:.1f}", "V"]
            for index in range(1, 9)
        ]

        lines = _table_lines_from_rows(rows, table_number=1)

        self.assertTrue(all("Column 1=" in line and "Column 2=" in line for line in lines))
        self.assertTrue(all("Parameter=" not in line and "Symbol=" not in line for line in lines))

    def test_headerless_scientific_min_max_table_does_not_infer_a_symbol_column(self) -> None:
        """Scientific notation markers e/E/x/X are numeric syntax, not symbol evidence."""

        minimums = [
            "1e-6",
            "2E-6",
            "3x10-6",
            "4X10^-6",
            "5 × 10^-6",
            "< 6e-6",
            "7e-6 to 8e-6",
            "9e-6 ± 1e-7",
            "1e-6-2e-6",
        ]
        rows = [
            [f"Receiver characteristic limit {index}", minimum, f"{index + 1}e-6", "V"]
            for index, minimum in enumerate(minimums, start=1)
        ]

        lines = _table_lines_from_rows(rows, table_number=1)

        self.assertTrue(all("Column 1=" in line and "Column 2=" in line for line in lines))
        self.assertTrue(all("Parameter=" not in line and "Symbol=" not in line for line in lines))

    def test_headerless_placeholder_min_max_table_does_not_infer_a_symbol_column(self) -> None:
        """Missing-value words in a Min column are not evidence of parameter symbols."""

        placeholders = [
            "N/A",
            "N.A.",
            "N/R",
            "TBD",
            "TBC",
            "None",
            "Not Specified",
            "Not Applicable",
        ]
        rows = [
            [f"Receiver characteristic limit {index}", placeholder, f"{index / 10 + 0.8:.1f}", "V"]
            for index, placeholder in enumerate(placeholders, start=1)
        ]

        lines = _table_lines_from_rows(rows, table_number=1)

        self.assertTrue(all("Column 1=" in line and "Column 2=" in line for line in lines))
        self.assertTrue(all("Parameter=" not in line and "Symbol=" not in line for line in lines))

    def test_geometry_subscript_evidence_is_bound_to_its_physical_occurrence(self) -> None:
        """One proven pair must not merge a later equal-text pair or RX/TX rows."""

        def word(text: str, x0: float, x1: float, top: float, size: float) -> dict[str, object]:
            return {
                "text": text,
                "x0": x0,
                "x1": x1,
                "top": top,
                "bottom": top + size,
                "size": size,
            }

        repeated_rows = [
            ["Parameter", "Symbol", "Value"],
            ["First\nSecond\nThird", "R\nLM\nR\nLM", "1\n2\n3"],
        ]
        repeated_geometry = [
            [[], [], []],
            [
                [],
                [
                    word("R", 100.0, 108.0, 10.0, 8.0),
                    word("LM", 108.1, 120.0, 13.2, 6.4),
                    word("R", 100.0, 108.0, 30.0, 8.0),
                    word("LM", 100.0, 116.0, 40.0, 8.0),
                ],
                [],
            ],
        ]

        repeated_lines = _table_lines_from_rows(
            repeated_rows,
            table_number=1,
            cell_word_rows=repeated_geometry,
        )

        self.assertEqual(1, len(repeated_lines))
        self.assertIn(r"Parameter=First\nSecond\nThird", repeated_lines[0])
        self.assertIn("Symbol=RLM / R / LM", repeated_lines[0])
        self.assertIn(r"Value=1\n2\n3", repeated_lines[0])
        self.assertNotIn("RLM / RLM", repeated_lines[0])

        separate_rows = [
            ["Parameter", "Symbol", "Value"],
            ["Receiver\nTransmitter", "RX\nTX", "1\n2"],
        ]
        separate_geometry = [
            [[], [], []],
            [
                [],
                [
                    word("RX", 100.0, 112.0, 10.0, 8.0),
                    word("TX", 100.0, 112.0, 20.0, 8.0),
                ],
                [],
            ],
        ]

        separate_lines = _table_lines_from_rows(
            separate_rows,
            table_number=1,
            cell_word_rows=separate_geometry,
        )

        self.assertEqual(1, len(separate_lines))
        self.assertIn(r"Parameter=Receiver\nTransmitter", separate_lines[0])
        self.assertIn("Symbol=RX / TX", separate_lines[0])
        self.assertIn(r"Value=1\n2", separate_lines[0])
        self.assertNotIn("RXTX", "\n".join(separate_lines))

    @unittest.skipUnless(
        Path("/Users/mac/Documents/文件对比工具/oif2024.532.04.pdf").is_file()
        and Path("/Users/mac/Documents/文件对比工具/oif2024.532.05.pdf").is_file(),
        "本地 OIF 532 双版本样本不存在",
    )
    def test_real_532_jitter_table_uses_structured_rows_without_raw_fragment_leak(self) -> None:
        """A fully covered real table should not leak split raw text into reader cards."""

        old_extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.532.04.pdf",
            start_page=11,
            end_page=11,
        )
        new_extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.532.05.pdf",
            start_page=9,
            end_page=9,
        )
        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        old_rows = "\n".join(
            row
            for table in old_extraction.table_visuals
            for row in table.row_texts
        )
        new_rows = "\n".join(
            row
            for table in new_extraction.table_visuals
            for row in table.row_texts
        )

        for rows, condition in ((old_rows, "31.3.13"), (new_rows, "31.3.14")):
            with self.subTest(condition=condition):
                self.assertIn("Parameter=Output Jitter\\nJRMS | Min=- | Max=0.023 | Unit=UI", rows)
                self.assertIn("Parameter=EOJ03 | Min=- | Max=0.025 | Unit=UI", rows)
                self.assertIn("Parameter=JH4u | Min=- | Max=0.118 | Unit=UI", rows)
                self.assertIn(f"Conditions=See {condition}", rows)

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            html = outputs["html"].read_text(encoding="utf-8")
            markdown = outputs["markdown"].read_text(encoding="utf-8")
            text_report = outputs["text"].read_text(encoding="utf-8")

        for kind, rendered in {
            "html": html,
            "markdown": markdown,
            "text": text_report,
        }.items():
            with self.subTest(kind=kind):
                self.assertNotIn("JH - 0.118 UI 4u", rendered)
                self.assertNotIn("EOJ - 0.025 UI 03", rendered)
                self.assertIn("JH4u", rendered)
                self.assertIn("EOJ03", rendered)

    @unittest.skipUnless(
        Path("/Users/mac/Documents/文件对比工具/oif2024.532.04.pdf").is_file()
        and Path("/Users/mac/Documents/文件对比工具/oif2024.532.05.pdf").is_file(),
        "本地 OIF 532 双版本样本不存在",
    )
    def test_real_532_emulated_host_channel_column_merge_has_no_row_change(self) -> None:
        """A detector-side column merge must not turn one unchanged table into five edits."""

        old_extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.532.04.pdf",
            start_page=18,
            end_page=18,
        )
        new_extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.532.05.pdf",
            start_page=17,
            end_page=17,
        )
        old_tables = tuple(
            table
            for table in old_extraction.table_visuals
            if "Emulated Host Channels" in table.title
        )
        new_tables = tuple(
            table
            for table in new_extraction.table_visuals
            if "Emulated Host Channels" in table.title
        )

        self.assertTrue(old_tables)
        self.assertTrue(new_tables)
        old_rows = [row for table in old_tables for row in table.row_texts]
        new_rows = [row for table in new_tables for row in table.row_texts]
        self.assertEqual(5, len(old_rows))
        self.assertEqual(5, len(new_rows))
        self.assertEqual(old_rows, new_rows)
        for expected in (
            "Column 1=Short | Column 2=near-end | Column 3=0 | Column 4=0",
            "Column 1=Short | Column 2=far-end | Column 3=12.0 | Column 4=240",
            "Column 1=Long | Column 2=near-end | Column 3=8.0 | Column 4=160",
            "Column 1=Long | Column 2=far-end | Column 3=20.0 | Column 4=400",
        ):
            self.assertTrue(any(expected in row for row in old_rows), expected)
        self.assertEqual([], reporting_module._table_row_changes(old_tables, new_tables))

    @unittest.skipUnless(
        Path("/Users/mac/Documents/文件对比工具/oif2024.532.04.pdf").is_file()
        and Path("/Users/mac/Documents/文件对比工具/oif2024.532.05.pdf").is_file(),
        "本地 OIF 532 双版本样本不存在",
    )
    def test_real_532_shifted_sections_and_split_table_remain_single_changes(self) -> None:
        """The real insertion shift must not become five delete/add pairs or split one table."""

        old_extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.532.04.pdf",
        )
        new_extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.532.05.pdf",
        )
        for extraction in (old_extraction, new_extraction):
            self.assertFalse(
                any(
                    not table.title
                    and len(table.row_texts) <= 2
                    and any(
                        marker in " ".join(table.row_texts)
                        for marker in ("Reference\\nCRU", "Column 1=HCB", "mulated Host\\nChannel")
                    )
                    for table in extraction.table_visuals
                ),
                "captioned figure labels must not reappear as table screenshots",
            )
        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        expected_bases = {
            "Steady-State Voltage and Linear Fit Pulse Peak Ratio": {"similarity_fallback"},
            "Transmit Equalization": {"structural_shift_run_body"},
            "Signal-to-Noise-and-Distortion Ratio (SNDR)": {"structural_shift_run_body"},
            "Interference Tolerance": {"structural_shift_bracketed_sentence"},
            "Test Procedure": {"structural_mapped_parent_body"},
            # Exact revision-table extraction strengthens the mapped parent;
            # direct similarity, mapped-parent body, or mapped-parent boundary
            # evidence is safe when the card remains one modified pair.
            "Host and Module output parameters": {
                "similarity_fallback",
                "structural_mapped_parent_body",
                "structural_mapped_parent_boundary",
            },
        }
        for title, expected_basis in expected_bases.items():
            with self.subTest(title=title):
                title_changes = [
                    change
                    for change in result.changes
                    if (change.old_section and change.old_section.title == title)
                    or (change.new_section and change.new_section.title == title)
                ]
                self.assertEqual(1, len(title_changes))
                self.assertEqual("modified", title_changes[0].change_type)
                self.assertIn(title_changes[0].match_basis, expected_basis)

        output_jitter_change = next(
            change
            for change in result.changes
            if change.old_section
            and change.new_section
            and change.old_section.title == "Output Jitter"
            and change.new_section.title == "Output Jitter"
        )
        self.assertEqual([], output_jitter_change.added_snippets)
        self.assertEqual([], output_jitter_change.removed_snippets)
        self.assertEqual(
            [
                (
                    "章节标题: 31.3.13 Output Jitter",
                    "章节标题: 31.3.14 Output Jitter",
                )
            ],
            [
                (pair.old, pair.new)
                for pair in output_jitter_change.replaced_snippets
            ],
        )

        common_mode_changes = [
            change
            for change in result.changes
            if (change.old_section and change.old_section.title == "Common-Mode Return Loss")
            or (change.new_section and change.new_section.title == "Common-Mode Return Loss")
        ]
        self.assertEqual(1, len(common_mode_changes))
        self.assertEqual("added", common_mode_changes[0].change_type)
        self.assertIsNone(common_mode_changes[0].old_section)
        self.assertTrue(common_mode_changes[0].new_section.heading.startswith("31.3.9 "))
        self.assertIn("SCC22", common_mode_changes[0].new_section.body)

        steady_change = next(
            change
            for change in result.changes
            if change.old_section
            and change.new_section
            and change.old_section.title
            == "Steady-State Voltage and Linear Fit Pulse Peak Ratio"
            and change.new_section.title
            == "Steady-State Voltage and Linear Fit Pulse Peak Ratio"
        )
        self.assertTrue(steady_change.old_section.heading.startswith("31.3.9 "))
        self.assertTrue(steady_change.new_section.heading.startswith("31.3.10 "))
        self.assertGreaterEqual(steady_change.similarity, 0.85)
        self.assertNotIn("SCC22", steady_change.old_section.body)
        self.assertNotIn("SCC22", steady_change.new_section.body)
        shared_vpeak_definition = "vpeak is defined as the maximum value of p(k)."
        self.assertIn(shared_vpeak_definition, steady_change.old_section.body)
        self.assertIn(shared_vpeak_definition, steady_change.new_section.body)
        steady_visible_delta = "\n".join(
            [*steady_change.added_snippets, *steady_change.removed_snippets]
            + [
                text
                for pair in steady_change.replaced_snippets
                for text in (pair.old, pair.new)
            ]
        )
        self.assertNotIn("SCC22", steady_visible_delta)
        self.assertNotIn(shared_vpeak_definition, steady_visible_delta)

        all_table_changes = reporting_module._build_table_changes(result)
        appendix_com_change = next(
            change
            for change in result.changes
            if change.old_section
            and change.new_section
            and change.old_section.title == "Appendix - Channel Operating Margin (COM)"
            and change.new_section.title == "Appendix - Channel Operating Margin (COM)"
        )
        self.assertTrue(appendix_com_change.replaced_snippets)
        self.assertTrue(
            reporting_module._reader_change_is_coordinate_proven_table_body_duplicate(
                appendix_com_change,
                all_table_changes,
            )
        )  # 原始正文墙留在审计层，读者层由同页同表的逐字符覆盖证明去重。
        table_changes = [
            change
            for change in all_table_changes
            if any(
                "Table 31-10." in table.title
                for table in (*change.old_tables, *change.new_tables)
            )
        ]
        self.assertEqual(1, len(table_changes))
        self.assertEqual("modified", table_changes[0].change_type)
        self.assertEqual(1, len(table_changes[0].old_tables))
        self.assertEqual(2, len(table_changes[0].new_tables))
        self.assertGreaterEqual(table_changes[0].similarity, 0.65)

        text_backed_cross_side_captions = (
            "Table 31-8. Coefficient Initial Conditions",
            "Table 31-9. SNDR Limits",
            "Table 31-13. Expected Reach of Different Material Classes",
        )
        for caption in text_backed_cross_side_captions:
            with self.subTest(caption=caption):
                matched = [
                    change
                    for change in all_table_changes
                    if any(
                        caption in table.title
                        for table in (*change.old_tables, *change.new_tables)
                    )
                ]
                self.assertEqual(1, len(matched))
                self.assertEqual("review", matched[0].change_type)
                self.assertEqual(
                    ["需人工复核"],
                    [row.change_type for row in matched[0].row_changes],
                )  # 扁平文字可折叠重复原文，但未知物理行列结构不得静默判等。

        visible_section_text = "\n".join(
            [
                snippet
                for change in result.changes
                for snippet in (*change.added_snippets, *change.removed_snippets)
            ]
            + [
                text
                for change in result.changes
                for pair in change.replaced_snippets
                for text in (pair.old, pair.new)
            ]
        )
        for leaked_raw_table in (
            "Coefficient Initial Conditions ic_req",
            "SNDR Limits ic_req",
            "Material Class Attenuation per mm",
        ):
            with self.subTest(leaked_raw_table=leaked_raw_table):
                self.assertNotIn(leaked_raw_table, visible_section_text)

        revision_changes = [
            change
            for change in all_table_changes
            if any(
                table.title == "in the table below:"
                for table in (*change.old_tables, *change.new_tables)
            )
        ]
        self.assertEqual(1, len(revision_changes))
        self.assertEqual("modified", revision_changes[0].change_type)
        self.assertEqual(1, len(revision_changes[0].row_changes))
        revision_added = next(
            row for row in revision_changes[0].row_changes
            if row.change_type == "新表新增行"
        )
        self.assertIn("comment resolution spreadsheet", revision_added.item)
        self.assertIn("OIF 2024.532.05", revision_added.new_value)
        self.assertNotIn("OIF 2024.532.04", revision_added.new_value)
        self.assertFalse(
            any(row.change_type == "需人工复核" for row in revision_changes[0].row_changes)
        )

    @unittest.skipUnless(
        Path("/Users/mac/Documents/文件对比工具/oif2024.532.05.pdf").is_file(),
        "本地 OIF 532 新版样本不存在",
    )
    def test_real_532_page_window_keeps_table_31_10_continuation(self) -> None:
        """Selecting only pages 15–16 must still retain the split table as one run."""

        extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.532.05.pdf",
            start_page=15,
            end_page=16,
        )
        result = compare_extractions(extraction, extraction, DiffOptions())
        runs = reporting_module._table_visual_indexes_by_caption_key(
            result.old_table_visuals,
            result.old_sections,
        )
        matching_runs = [
            indexes
            for indexes in runs.values()
            if any(
                "Table 31-10." in result.old_table_visuals[index].title
                for index in indexes
            )
        ]

        self.assertEqual(1, len(matching_runs))
        self.assertEqual(
            [15, 16],
            [
                result.old_table_visuals[index].page_number
                for index in matching_runs[0]
            ],
        )

    @unittest.skipUnless(
        Path("/Users/mac/Documents/文件对比工具/oif2023.235.12.pdf").is_file()
        and Path("/Users/mac/Documents/文件对比工具/oif2024.532.04.pdf").is_file()
        and Path("/Users/mac/Documents/文件对比工具/oif2024.532.05.pdf").is_file()
        and Path("/Users/mac/Documents/文件对比工具/oif2024.058.13.pdf").is_file(),
        "本地 OIF 235/532/058 回归样本不存在",
    )
    def test_real_protocol_symbol_scripts_preserve_alignment_and_filtered_watermarks(self) -> None:
        """Real Symbol cells must follow geometry without reintroducing DRAFT letters."""

        sample_pages = {
            "235": (
                "/Users/mac/Documents/文件对比工具/oif2023.235.12.pdf",
                8,
            ),
            "532": (
                "/Users/mac/Documents/文件对比工具/oif2024.532.04.pdf",
                32,
            ),
            "532_new": (
                "/Users/mac/Documents/文件对比工具/oif2024.532.05.pdf",
                31,
            ),
            "058": (
                "/Users/mac/Documents/文件对比工具/oif2024.058.13.pdf",
                10,
            ),
        }
        extracted_rows: dict[str, str] = {}
        extractions: dict[str, ExtractionResult] = {}
        for label, (pdf_path, page_number) in sample_pages.items():
            extraction = extract_pdf_text(
                pdf_path,
                start_page=page_number,
                end_page=page_number,
            )
            extractions[label] = extraction
            extracted_rows[label] = "\n".join(
                row
                for table in extraction.table_visuals
                for row in table.row_texts
            )

        self.assertIn("Symbol=bDFE_max(1) | Value=0.85 | Units=—", extracted_rows["235"])
        self.assertIn("Symbol=bDFE_min(1) | Value=0 | Units=—", extracted_rows["235"])
        self.assertIn("Symbol=bmin(0) | Value=0.7 | Units=—", extracted_rows["235"])
        self.assertNotIn("Symbol=b (1) / DFE", extracted_rows["235"])

        for sample in ("532", "532_new"):
            for expected in (
                "Symbol=Cd(1) | Value=40 | Units=fF",
                "Symbol=Cd(2) | Value=90 | Units=fF",
                "Symbol=Cd(3) | Value=110 | Units=fF",
                "Symbol=Ls(1) | Value=130 | Units=pH",
                "Symbol=Ls(2) | Value=140 | Units=pH",
                "Symbol=Ls(3) | Value=150 | Units=pH",
                "Symbol=Rd(t) | Value=46.25 | Units=Ω",
                "Symbol=Rd(r) | Value=46.25 | Units=Ω",
            ):
                with self.subTest(sample=sample, expected=expected):
                    self.assertIn(expected, extracted_rows[sample])
            self.assertNotIn("Symbol=C (1) / d", extracted_rows[sample])
            self.assertNotIn("Symbol=R (t) / d", extracted_rows[sample])

        expected_device_symbols = (
            "Symbol=\uf0670 / \uf0611 / \uf0612 / \uf074 / zp(1) / Zc(1) / "
            "zp(2) / Zc(2) / zp(3) / Zc(3) / zp(4) / Zc(4) / Cp"
        )
        expected_partial_symbols = (
            "Symbol=C0 / \uf0670(h) / \uf0611(h) / \uf0612(h) / \uf074(h) / "
            "Zc(h) / zp(2) / C1"
        )
        for sample in ("532", "532_new"):
            with self.subTest(sample=sample, row="device"):
                self.assertIn(expected_device_symbols, extracted_rows[sample])
            with self.subTest(sample=sample, row="partial"):
                self.assertIn(expected_partial_symbols, extracted_rows[sample])
            self.assertNotIn(" / ( / 1 / ) / ", extracted_rows[sample])
            self.assertNotIn(" / h / ) / ", extracted_rows[sample])

        for parameter in ("Device package model", "Partial host channel model"):
            old_row = next(
                row
                for table in extractions["532"].table_visuals
                for row in table.row_texts
                if f"Parameter={parameter}" in row
            )
            new_row = next(
                row
                for table in extractions["532_new"].table_visuals
                for row in table.row_texts
                if f"Parameter={parameter}" in row
            )
            self.assertEqual(old_row, new_row)  # 同一模型不能因 PDF tokenization 差异制造伪修订。

        model_result = compare_extractions(
            extractions["532"],
            extractions["532_new"],
            DiffOptions(),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(model_result, temp_dir, DiffOptions())
            for kind in ("html", "markdown", "text"):
                rendered = outputs[kind].read_text(encoding="utf-8")
                with self.subTest(kind=kind):
                    self.assertNotIn(" / ( / 1 / ) / ", rendered)
                    self.assertNotIn(" / h / ) / ", rendered)
                    self.assertNotIn(" / \uf074(", rendered)

        self.assertIn("Symbol=\uf072x | Value=0.618 | Units=-", extracted_rows["058"])
        self.assertNotIn("Symbol=F", extracted_rows["058"])

    @unittest.skipUnless(
        Path("/Users/mac/Documents/文件对比工具/oif2023.235.12.pdf").is_file()
        and Path("/Users/mac/Documents/文件对比工具/oif2023.235.13.pdf").is_file(),
        "本地 OIF 235 双版本样本不存在",
    )
    def test_real_235_reader_reports_do_not_leak_cross_row_subscript_fragments(self) -> None:
        """Cross-border suffix glyphs must be replaced by the proven structured row."""

        old_extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2023.235.12.pdf",
            start_page=8,
            end_page=8,
        )
        new_extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2023.235.13.pdf",
            start_page=8,
            end_page=8,
        )
        for extraction in (old_extraction, new_extraction):
            page_text = extraction.pages[0].text
            self.assertNotIn("Normalized DFE coefficient maximum limit b (1) 0.85", page_text)
            self.assertNotIn("Normalized FFE main tap coefficient minimum limit b (0) 0.7", page_text)
            structured_rows = "\n".join(
                row
                for table in extraction.table_visuals
                for row in table.row_texts
            )
            self.assertIn("Symbol=bDFE_max(1) | Value=0.85", structured_rows)
            self.assertIn("Symbol=bmin(0) | Value=0.7", structured_rows)

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            rendered_reports = {
                kind: outputs[kind].read_text(encoding="utf-8")
                for kind in ("html", "markdown", "text")
            }
        for kind, rendered in rendered_reports.items():
            with self.subTest(kind=kind):
                self.assertNotIn("Normalized DFE coefficient maximum limit b (1) 0.85", rendered)
                self.assertNotIn("Normalized FFE main tap coefficient minimum limit b (0) 0.7", rendered)

    @unittest.skipUnless(
        Path("/Users/mac/Documents/文件对比工具/oif2023.235.12.pdf").is_file()
        and Path("/Users/mac/Documents/文件对比工具/oif2023.235.13.pdf").is_file(),
        "本地 OIF 235 双版本样本不存在",
    )
    def test_real_235_peak_to_peak_header_wrap_has_no_row_change(self) -> None:
        """A line-end hyphen in the same header must not become a table-row edit."""

        old_extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2023.235.12.pdf",
            start_page=25,
            end_page=25,
        )
        new_extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2023.235.13.pdf",
            start_page=23,
            end_page=23,
        )
        old_table = next(
            table
            for table in old_extraction.table_visuals
            if "Receiver Jitter Tolerance Parameters" in table.title
        )
        new_table = next(
            table
            for table in new_extraction.table_visuals
            if "Receiver Jitter Tolerance Parameters" in table.title
        )

        self.assertEqual(4, len(old_table.row_texts))
        self.assertEqual(4, len(new_table.row_texts))
        for header in (old_table.row_texts[0], new_table.row_texts[0]):
            self.assertIn("Sinusoidal jitter", header)
            self.assertIn("peak-to-peak", header.replace("\\n", ""))
        self.assertEqual(
            [],
            reporting_module._table_row_changes((old_table,), (new_table,)),
        )

    @unittest.skipUnless(
        Path("/Users/mac/Documents/文件对比工具/oif2023.235.12.pdf").is_file()
        and Path("/Users/mac/Documents/文件对比工具/oif2023.235.13.pdf").is_file(),
        "本地 OIF 235 双版本样本不存在",
    )
    def test_real_235_mlsd_note_reports_only_readable_ieee_addition(self) -> None:
        """The spanning MLSD note is one readable sentence edit, never empty fields."""

        old_extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2023.235.12.pdf",
            start_page=9,
            end_page=9,
        )
        new_extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2023.235.13.pdf",
            start_page=9,
            end_page=9,
        )
        old_table = next(
            table for table in old_extraction.table_visuals if "Table 33-1" in table.title
        )
        new_table = next(
            table for table in new_extraction.table_visuals if "Table 33-1" in table.title
        )
        self.assertEqual(3, len(old_table.row_texts))
        self.assertEqual(3, len(new_table.row_texts))
        table_rows = reporting_module._table_row_changes((old_table,), (new_table,))
        self.assertEqual(1, len(table_rows))
        self.assertEqual("表格说明", table_rows[0].item)
        self.assertNotIn("IEEE 802.3dj", table_rows[0].old_value)
        self.assertIn("IEEE 802.3dj", table_rows[0].new_value)

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            rendered_reports = {
                kind: outputs[kind].read_text(encoding="utf-8")
                for kind in ("html", "markdown", "text")
            }
        for kind, rendered in rendered_reports.items():
            with self.subTest(kind=kind):
                visible = re.sub(r"<[^>]+>", "", rendered) if kind == "html" else rendered
                self.assertIn("maximum likelihood sequence detection", visible)
                self.assertIn("IEEE 802.3dj", visible)
                self.assertNotIn("<empty>", rendered)
                self.assertNotIn("Symbol=（空白）", rendered)

    @unittest.skipUnless(
        Path("/Users/mac/Documents/文件对比工具/oif2024.058.11.pdf").is_file()
        and Path("/Users/mac/Documents/文件对比工具/oif2024.058.13.pdf").is_file(),
        "本地 OIF 058 双版本样本不存在",
    )
    def test_real_058_table_renumbering_has_one_contextual_sentence(self) -> None:
        """Table 32-9/10 renumbering stays inside its complete explanatory sentence."""

        old_extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.058.11.pdf",
            start_page=18,
            end_page=18,
        )
        new_extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.058.13.pdf",
            start_page=18,
            end_page=18,
        )
        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        pairs = [pair for change in result.changes for pair in change.replaced_snippets]
        contextual = [
            pair for pair in pairs if "Further receiver electrical requirements" in pair.old
        ]
        self.assertEqual(1, len(contextual))
        self.assertIn("Table 32-9", contextual[0].old)
        self.assertIn("Table 32-10", contextual[0].old)
        self.assertIn("Table 32-7", contextual[0].new)
        self.assertIn("Table 32-8", contextual[0].new)
        self.assertFalse(
            any(
                pair.old.strip() in {"Table 32-9.", "Table 32-10."}
                or pair.new.strip() in {"Table 32-7.", "Table 32-8."}
                for pair in pairs
            )
        )
        raw_single_side = [
            snippet
            for change in result.changes
            for snippet in (*change.added_snippets, *change.removed_snippets)
        ]
        self.assertIn("9 10 Table 32-7.", raw_single_side)

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            rendered_reports = {
                kind: outputs[kind].read_text(encoding="utf-8")
                for kind in ("html", "markdown", "text")
            }
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
        self.assertTrue(
            any("9 10 Table 32-7." in change["added_snippets"] for change in payload["changes"])
        )
        for kind, rendered in rendered_reports.items():
            with self.subTest(kind=kind):
                self.assertNotIn("9 10 Table 32-7.", rendered)
        self.assertEqual(
            2,
            rendered_reports["html"].count("Further receiver electrical requirements"),
        )

    @unittest.skipUnless(
        Path("/Users/mac/Documents/文件对比工具/oif2024.058.11.pdf").is_file()
        and Path("/Users/mac/Documents/文件对比工具/oif2024.058.13.pdf").is_file(),
        "本地 OIF 058 双版本样本不存在",
    )
    def test_real_058_class_b_aggregate_pairs_with_expanded_rows(self) -> None:
        """One old aggregate row must pair with its fourteen expanded counterparts."""

        old_extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.058.11.pdf",
            start_page=4,
            end_page=6,
        )
        new_extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.058.13.pdf",
            start_page=6,
            end_page=9,
        )
        old_tables = tuple(
            table
            for table in old_extraction.table_visuals
            if "Table 32-1" in table.title
        )
        new_tables = tuple(
            table
            for table in new_extraction.table_visuals
            if "Table 32-1" in table.title
        )

        changes = reporting_module._table_row_changes(old_tables, new_tables)
        material = [change for change in changes if change.change_type != "需人工复核"]

        old_rows = [row for table in old_tables for row in table.row_texts]
        new_rows = [row for table in new_tables for row in table.row_texts]
        self.assertEqual(75, len(old_rows))
        self.assertEqual(75, len(new_rows))
        for rows in (old_rows, new_rows):
            tau_rows = [row for row in rows if "6.141" in row]
            self.assertEqual(2, len(tau_rows))
            self.assertTrue(all("Symbol=\uf074" in row for row in tau_rows))
            self.assertTrue(
                all(
                    "Symbol=τ" in reporting_module._reader_table_inline_text(row)
                    for row in tau_rows
                )
            )
        self.assertFalse(
            any(
                "6.141" in f"{change.item} {change.old_value} {change.new_value}"
                for change in changes
            )
        )

        self.assertEqual(2, len(material))
        self.assertEqual(
            {
                "Single-ended reference resistance",
                "表格项目名称",
            },
            {change.item for change in material},
        )
        self.assertTrue(
            any(
                change.item == "Single-ended reference resistance"
                and "50 Ω" in change.old_value
                and "46.25 Ω" in change.new_value
                for change in material
            )
        )
        self.assertTrue(
            any(
                "FFE maximum span" in change.old_value
                and "FFE maximum post-tap span" in change.new_value
                for change in material
            )
        )
        self.assertFalse(any(change.change_type == "新表新增行" for change in changes))
        self.assertTrue(
            any(
                change.change_type == "需人工复核"
                and change.item == "Transmission line parameter"
                and "Units=（空白）" in change.old_value
                for change in changes
            )
        )

    @unittest.skipUnless(
        Path("/Users/mac/Documents/文件对比工具/oif2023.235.12.pdf").is_file()
        and Path("/Users/mac/Documents/文件对比工具/oif2023.235.13.pdf").is_file(),
        "本地 OIF 235 双版本样本不存在",
    )
    def test_real_235_long_jitter_deletion_is_two_coherent_blocks(self) -> None:
        """Deleted explanatory prose is grouped by source continuity, not table fragments."""

        result = compare_extractions(
            extract_pdf_text("/Users/mac/Documents/文件对比工具/oif2023.235.12.pdf"),
            extract_pdf_text("/Users/mac/Documents/文件对比工具/oif2023.235.13.pdf"),
            DiffOptions(),
        )
        change = next(
            item
            for item in result.changes
            if item.old_section
            and item.old_section.title == "Transmitter output jitter"
            and any("short-term correlated jitter" in text for text in item.removed_snippets)
        )

        self.assertEqual(2, len(change.removed_snippets))
        main_block, final_block = change.removed_snippets
        self.assertTrue(main_block.startswith("J3u , JRMS , and EOJ are defined"))
        for anchor in (
            "The 12 transitions represent",
            "Table 33-7",
            "Table 33-8",
            "The threshold used to define each transition",
        ):
            self.assertIn(anchor, main_block)
        self.assertEqual(
            "It is acceptable to meet the EOJ requirement with either QPRBS13 or QPRBS9 test pattern.",
            final_block,
        )
        deleted_text = "\n".join(change.removed_snippets)
        for fragment in (
            "\nTable 33-7.",
            "QPRBS13-CEI Pattern Symbols Used for Jitter Measurement",
            "QPRBS9-CEI Pattern Symbols Used for Jitter/EOJ Measurement",
        ):
            self.assertNotIn(fragment, deleted_text)

    @unittest.skipUnless(
        Path("/Users/mac/Documents/文件对比工具/oif2024.532.04.pdf").is_file()
        and Path("/Users/mac/Documents/文件对比工具/oif2024.532.05.pdf").is_file(),
        "本地 OIF 532 双版本样本不存在",
    )
    def test_real_532_digit_spacing_is_review_while_true_value_change_remains(self) -> None:
        """The old `4 0` glyph spacing is uncertain; `True 1` to `1` is real."""

        old_extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.532.04.pdf",
            start_page=13,
            end_page=13,
        )
        new_extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.532.05.pdf",
            start_page=11,
            end_page=11,
        )
        old_tables = tuple(
            table for table in old_extraction.table_visuals if "Table 31-7" in table.title
        )
        new_tables = tuple(
            table for table in new_extraction.table_visuals if "Table 31-7" in table.title
        )

        changes = reporting_module._table_row_changes(old_tables, new_tables)
        by_item = {change.item: change for change in changes}

        self.assertEqual(
            "需人工复核",
            by_item["Equalizer length associated with reflection signal"].change_type,
        )
        self.assertNotEqual(
            "需人工复核",
            by_item["Tukey window flag"].change_type,
        )

    @unittest.skipUnless(
        Path("/Users/mac/Documents/文件对比工具/oif2024.058.11.pdf").is_file()
        and Path("/Users/mac/Documents/文件对比工具/oif2024.058.13.pdf").is_file(),
        "本地 OIF 058 双版本样本不存在",
    )
    def test_real_058_table_32_4_keeps_three_data_rows_and_two_changes(self) -> None:
        """Wrapped cells in Table 32-4 must remain physical rows, not extra changes."""

        old_extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.058.11.pdf",
            start_page=9,
            end_page=9,
        )
        new_extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.058.13.pdf",
            start_page=11,
            end_page=11,
        )

        def table_32_4(extraction: ExtractionResult) -> TableVisual:
            return next(
                table
                for table in extraction.table_visuals
                if table.title == "Table 32-4. Transmitter Output Jitter Specification"
            )

        old_table = table_32_4(old_extraction)
        new_table = table_32_4(new_extraction)
        for table, expected_symbols in (
            (old_table, ("T_J4.3u03", "T_JRMS03", "T_EOJ03")),
            (new_table, ("T_JH4.3u", "T_JHRMS", "T_EOJ03")),
        ):
            self.assertEqual(4, len(table.row_texts))
            data_rows = [
                row for row in table.row_texts if "Characteristic=NOTES:" not in row
            ]
            note_rows = [
                row for row in table.row_texts if "Characteristic=NOTES:" in row
            ]
            self.assertEqual(3, len(data_rows))
            self.assertEqual(1, len(note_rows))
            self.assertIn(
                "Characteristic=NOTES:\\n1.Measured as described in Section 32.3.1.7.",
                note_rows[0],
            )
            self.assertEqual(
                list(expected_symbols),
                [row.split(" | Symbol=", 1)[1].split(" | ", 1)[0] for row in data_rows],
            )
            self.assertIn(
                "Characteristic=Uncorrelated Jitter (time interval from 0.0025% "
                "to\\n99.9975% of the probability distribution)",
                data_rows[0],
            )
            self.assertIn(
                "Characteristic=Uncorrelated jitter RMS (standard deviation "
                "of\\nthe probability distribution)",
                data_rows[1],
            )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, temp_dir, DiffOptions())
            html = paths["html"].read_text(encoding="utf-8")
            payload = json.loads(paths["json"].read_text(encoding="utf-8"))

        table_change = next(
            change
            for change in payload["table_changes"]
            if "Table 32-4. Transmitter Output Jitter Specification"
            in change["old_titles"] + change["new_titles"]
        )
        row_changes = table_change["row_changes"]
        self.assertEqual(2, table_change["row_change_count"])
        self.assertEqual(2, len(row_changes))
        self.assertEqual(
            ["Symbol=T_J4.3u03", "Symbol=T_JRMS03"],
            [change["old_value"].split(" | ", 1)[0] for change in row_changes],
        )
        self.assertEqual(
            ["Symbol=T_JH4.3u", "Symbol=T_JHRMS"],
            [change["new_value"].split(" | ", 1)[0] for change in row_changes],
        )
        self.assertEqual(
            ["实质/符号变化", "实质/符号变化"],
            [change["change_type"] for change in row_changes],
        )
        self.assertIn("表格修改 · 2 行", html)
        self.assertNotIn(">99.9975% of the probability distribution) symbol<", html)
        self.assertNotIn(">the probability distribution) symbol<", html)

    @unittest.skipUnless(
        Path("/Users/mac/Documents/文件对比工具/oif2024.058.11.pdf").is_file()
        and Path("/Users/mac/Documents/文件对比工具/oif2024.058.13.pdf").is_file(),
        "本地 OIF 058 双版本样本不存在",
    )
    def test_real_058_headerless_continuation_does_not_leak_split_symbols(self) -> None:
        """A schema-proven continuation table must align with the later explicit header."""

        old_extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.058.11.pdf",
        )
        new_extraction = extract_pdf_text(
            "/Users/mac/Documents/文件对比工具/oif2024.058.13.pdf",
        )
        old_page_17 = next(
            page for page in old_extraction.pages if page.page_number == 17
        ).text
        old_page_22 = next(
            page for page in old_extraction.pages if page.page_number == 22
        ).text
        self.assertIn("32.3.1.7.1 J4.3u and JRMS Jitter", old_page_17)
        self.assertIn("set S0i = {ti (1) - Tavgi", old_page_17)
        self.assertIn("probability distribution fJ (t)", old_page_17)
        self.assertIn("J4.3u is defined as the time interval", old_page_17)
        self.assertIn("JRMS is defined as the standard deviation of fJ (t)", old_page_17)
        self.assertIn(
            "measured for J4.3u03 and JRMS03 include the effects",
            old_page_22,
        )
        for extraction in (old_extraction, new_extraction):
            rows = "\n".join(
                row
                for table in extraction.table_visuals
                for row in table.row_texts
            )
            self.assertIn("Symbol=bmax(1) | Value=0.85", rows)
            self.assertIn("Symbol=bmin(1) | Value=0", rows)
            self.assertIn("Symbol=bmin(0) | Value=0.7", rows)
            self.assertNotIn("Column 2=b (1)", rows)
            self.assertNotIn("Column 2=b (0)", rows)

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        changes_by_number: dict[str, list[str]] = {}
        for change in result.changes:
            numbers = {
                section.number_path[-1]
                for section in (change.old_section, change.new_section)
                if section is not None and section.number_path
            }
            for number in numbers:
                changes_by_number.setdefault(number, []).append(change.change_type)
        for number in ("32.3.1.7", "32.3.2.1"):
            with self.subTest(number=number):
                self.assertEqual(["modified"], changes_by_number.get(number))
        for table_only_number in ("32.3.2.2", "32.3.2.7"):
            self.assertNotIn(
                table_only_number,
                changes_by_number,
            )  # 稳定正文+表格变化只由表格证据区承载。
        glued_caption = (
            "Table 32-7. QPRBS13-CEI Pattern Symbols Used for Jitter Measurement "
            "It is acceptable to meet the EOJ requirement with either QPRBS13 or "
            "QPRBS9 test pattern."
        )
        readable_sentence = (
            "It is acceptable to meet the EOJ requirement with either QPRBS13 or "
            "QPRBS9 test pattern."
        )
        raw_snippets = "\n".join(
            snippet
            for change in result.changes
            for snippet in (*change.added_snippets, *change.removed_snippets)
        )
        self.assertNotIn(glued_caption, raw_snippets)
        self.assertIn(readable_sentence, raw_snippets)
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
            json_snippets = "\n".join(
                snippet
                for change in payload["changes"]
                for snippet in (*change["added_snippets"], *change["removed_snippets"])
            )
            self.assertNotIn(glued_caption, json_snippets)
            self.assertIn(readable_sentence, json_snippets)
            self.assertTrue(
                any(
                    table["title"]
                    == "Table 32-7. QPRBS13-CEI Pattern Symbols Used for Jitter Measurement"
                    for table in (*payload["old_table_visuals"], *payload["new_table_visuals"])
                )
            )  # 审计版保留续表标题及结构化表格证据，但不把标题伪装成被删除的正文。
            for kind in ("html", "markdown", "text"):
                rendered = outputs[kind].read_text(encoding="utf-8")
                with self.subTest(kind=kind):
                    self.assertNotIn("b (1)", rendered)
                    self.assertNotIn("b (0)", rendered)
                    self.assertNotIn("Column 2=b (1)", rendered)
                    self.assertNotIn("Column 2=b (0)", rendered)
                    self.assertNotIn("J and J Jitter", rendered)
                    self.assertNotIn(">4.3u RMS<", rendered)
                    self.assertNotIn("i i i i i", rendered)
                    self.assertNotIn(">RMS J<", rendered)
                    self.assertNotIn(glued_caption, rendered)
                    self.assertIn(readable_sentence, rendered)

    def test_headerless_table_expansion_uses_neutral_columns_and_preserves_ambiguity(self) -> None:
        """Headerless tables may expand only on strong shape evidence; ambiguous cells stay intact."""

        rows = [  # 模拟 OIF COM 表中组标题占据参数列第一行的抽取形态。
            [
                "Device package model: Class B (Note 1)\n"
                "Single-ended PKG capacitance\n"
                "Transmission line 2 characteristic impedance",
                "C\np\nZ\nc2",
                "40\n87.5",
                "fF\nΩ",
            ]
        ]

        lines = _table_lines_from_rows(rows, table_number=1)  # 缺表头时走 4 列默认参数表和多行拆分。

        rows_with_stray_value = [  # 模拟新版 OIF 中 Value 列多抽出一个孤立 T 的错位形态。
            [
                "D\n"
                "Device package model: Class B (Note 1)\n"
                "Transmission line length, Tx Test 1,2\n"
                "Single-ended PKG capacitance at pkg-to-board IF\n"
                "Transmission line characteristic impedance\n"
                "Transmission line 2 characteristic impedance",
                "z\np\nC\np\nZ\nc\nZ\nc2",
                "44,45\n40\n87.5\nT\n95",
                "mm\nfF\nΩ\nΩ",
            ]
        ]
        lines_with_stray_value = _table_lines_from_rows(rows_with_stray_value, table_number=1)  # T 不应把后续值整体错位。

        self.assertEqual(1, len(lines))
        self.assertIn(r"Column 1=Device package model: Class B (Note 1)\nSingle-ended PKG capacitance", lines[0])
        self.assertIn(r"Column 2=C\np\nZ\nc2", lines[0])
        self.assertIn(r"Column 3=40\n87.5", lines[0])
        self.assertIn(r"Column 4=fF\nΩ", lines[0])
        self.assertEqual(1, len(lines_with_stray_value))  # 各列长度冲突时不能猜测删除或重新对齐。
        opaque_row = lines_with_stray_value[0]
        for token in (
            r"Column 1=D\nDevice package model",
            r"Column 2=z\np\nC",
            r"44,45\n40\n87.5\nT\n95",
            r"mm\nfF\nΩ\nΩ",
        ):
            self.assertIn(token, opaque_row)  # 包括单字母 D/T 在内的所有不透明值都必须可复核。

    def test_ambiguous_group_like_table_text_is_preserved_in_one_row(self) -> None:
        """A familiar noun phrase cannot be silently discarded as a group label."""

        rows = [
            [
                "Device die model\n"
                "Single-ended device capacitance1\n"
                "Single-device series inductance1",
                "C\nd1\nL\ns1",
                "40\n130",
                "fF\npH",
            ]
        ]  # 真实 OIF 表把组标题和两条参数放在同一个物理单元格中。

        lines = _table_lines_from_rows(rows, table_number=1)

        self.assertEqual(1, len(lines))
        self.assertIn(r"Column 1=Device die model\nSingle-ended device capacitance1", lines[0])
        self.assertIn(r"Column 2=C\nd1\nL\ns1", lines[0])
        self.assertIn(r"Column 3=40\n130", lines[0])
        self.assertIn(r"Column 4=fF\npH", lines[0])

    def test_single_letter_table_values_are_preserved(self) -> None:
        """A/B/C style table values should not be treated as extraction noise."""

        old_lines = _table_lines_from_rows(
            [
                ["Parameter", "Symbol", "Value", "Units"],
                ["Preset 1\nPreset 2\nPreset 3", "P1\nP2\nP3", "10\nA\n20", "UI\nUI\nUI"],
            ],
            table_number=1,
        )  # 旧表格中间行的 Value=A 是合法等级值。
        new_lines = _table_lines_from_rows(
            [
                ["Parameter", "Symbol", "Value", "Units"],
                ["Preset 1\nPreset 2\nPreset 3", "P1\nP2\nP3", "10\nB\n20", "UI\nUI\nUI"],
            ],
            table_number=1,
        )  # 新表格只把等级值从 A 改成 B。
        old_extraction = ExtractionResult(
            pdf_path=Path("old_letter_value.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\n" + "\n".join(old_lines))],
        )  # 旧版使用结构化表格行作为章节正文。
        new_extraction = ExtractionResult(
            pdf_path=Path("new_letter_value.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\n" + "\n".join(new_lines))],
        )  # 新版同一参数同一符号只改 Value。

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())  # 走完整 diff，防止 A/B 被清洗成相同。
        snippets = "\n".join(
            pair.old + "\n" + pair.new
            for change in result.changes
            for pair in change.replaced_snippets
        )  # 收集替换片段。

        self.assertIn("Symbol=P2", "\n".join(old_lines + new_lines))  # P1/P2/P3 不应被误合并为 P1P2。
        self.assertIn("Value=A", "\n".join(old_lines))  # 表格行生成阶段仍保留旧合法单字母值。
        self.assertIn("Value=B", "\n".join(new_lines))  # 表格行生成阶段仍保留新合法单字母值。
        self.assertNotIn("Value=A", snippets)  # 正文卡片不再把表格行作为段落差异展示。
        self.assertNotIn("Value=B", snippets)  # 新表格值也留给表格摘要/源 PDF 复核。

    def test_extra_single_letter_table_values_are_not_silently_dropped(self) -> None:
        """A/B value changes should survive even when the value column has an extra item."""

        old_lines = _table_lines_from_rows(
            [
                ["Parameter", "Symbol", "Value", "Units"],
                ["Preset 1\nPreset 2\nPreset 3", "P1\nP2\nP3", "10\nA\n20\n30", "UI\nUI\nUI"],
            ],
            table_number=1,
        )  # Value 列多一项时，A 仍可能是合法等级值，不能被泛化删除。
        new_lines = _table_lines_from_rows(
            [
                ["Parameter", "Symbol", "Value", "Units"],
                ["Preset 1\nPreset 2\nPreset 3", "P1\nP2\nP3", "10\nB\n20\n30", "UI\nUI\nUI"],
            ],
            table_number=1,
        )  # 新版只把 A 改成 B，额外 30 不应掩盖该变化。
        old_extraction = ExtractionResult(
            pdf_path=Path("old_extra_letter_value.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\n" + "\n".join(old_lines))],
        )  # 旧版结构化行。
        new_extraction = ExtractionResult(
            pdf_path=Path("new_extra_letter_value.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\n" + "\n".join(new_lines))],
        )  # 新版结构化行。

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())  # 走完整比较，验证不会 false negative。
        snippets = "\n".join(
            pair.old + "\n" + pair.new
            for change in result.changes
            for pair in change.replaced_snippets
        )  # 收集替换片段。

        self.assertIn(r"Value=10\nA\n20\n30", "\n".join(old_lines))  # 条数冲突时保留全部旧值及真实行边界。
        self.assertIn(r"Value=10\nB\n20\n30", "\n".join(new_lines))  # 新值 B 同样保持可复核。
        self.assertNotIn("Value=A", snippets)  # 正文差异卡片不再展示纯表格行。
        self.assertNotIn("Value=B", snippets)  # 新值 B 也不应刷进正文区。

    def test_raw_table_text_is_preserved_without_coordinate_level_identity(self) -> None:
        """Structured extraction alone cannot authorize deleting raw page evidence."""

        raw_short_table_row = "Single-ended reference resistance R 0 50 Ω"  # 模拟正文抽取保留下来的单条原始表格行。
        raw_table_line = (  # 模拟 pdfplumber 正文抽取把整张参数表压成一个超长自然语言行。
            "Transmission line parameter a2 2.93x10-4 ns/mm "
            "Single-ended reference resistance R 0 50 Ω "
            "Single-ended termination resistance R d 46.25 Ω "
            "Receiver 3 dB bandwidth f b 0.55 GHz "
            "Transmitter equalizer coefficient c(0) 0.54 maximum value 0.16 step size 0.02"
        )
        text = "\n".join(  # 普通正文和重复表格噪声混在同一页抽取文本中。
            [
                "1 Scope",
                "This ordinary paragraph should stay visible.",
                raw_short_table_row,
                raw_table_line,
            ]
        )
        table_lines = [  # 结构化表格行已经覆盖了 raw_table_line 里的关键参数和值。
            "表格行: T1 | Parameter=Single-ended reference resistance | Symbol=R0 | Value=50 | Units=Ω",
            "表格行: T1 | Parameter=Single-ended termination resistance | Symbol=Rd | Value=46.25 | Units=Ω",
            "表格行: T1 | Parameter=Receiver 3 dB bandwidth | Symbol=fb | Value=0.55 | Units=GHz",
        ]

        combined = _combine_text_and_table_lines(text, table_lines)  # 合并两路证据，不根据 token 重合猜测删除。

        self.assertIn("This ordinary paragraph should stay visible.", combined)  # 普通正文不应被降噪误删。
        self.assertIn(raw_short_table_row, combined.splitlines())  # 没有坐标级同一性证据时，短行也必须保留。
        self.assertIn(raw_table_line, combined)  # 还含未结构化记录的大块不能整块删除。
        self.assertIn("Parameter=Single-ended reference resistance", combined)  # 结构化表格行必须保留用于后续 diff。

    def test_table_bbox_replacement_requires_row_local_lossless_coverage(self) -> None:
        """Schema text or another row cannot mask omitted source-cell content."""

        lines, fully_represented = _table_lines_from_rows_with_coverage(
            [
                ["Table 1 operating limits"],
                ["Parameter", "Value"],
                ["x", "1"],
                ["y", "2"],
            ],
            table_number=1,
        )

        self.assertTrue(lines)
        self.assertFalse(fully_represented)  # 表头前标题未进入结构化行，bbox 原文必须保留。
        self.assertFalse(
            _expanded_rows_preserve_source_cells(
                [["x"], ["1"], ["rate"], ["alue"]],
                [["x", "1", "", ""]],
            )
        )  # Parameter/Value 等合成标签不能替缺失的 rate/alue 提供虚假覆盖。
        self.assertTrue(
            _expanded_rows_preserve_source_cells(
                [["x"], ["1"], ["rate"], ["alue"]],
                [["x", "1", "rate", "alue"]],
            )
        )
        self.assertFalse(
            _expanded_rows_preserve_source_alignment(
                [["first", "second"], ["A", "B"], ["1", "2", "3"]],
                [["first\\nsecond", "A / B", "1\\n2\\n3"]],
            )
        )  # 多列列表仍聚成一行时，字符虽齐全但行间对应关系未被结构化证明。
        self.assertEqual(
            (False, True, False),
            _table_row_replacement_flags(
                [
                    ["Parameter", "Symbol", "Value"],
                    ["Output jitter\nEven-odd jitter", "JH4u\nEOJ03", "0.118\n0.025"],
                    ["Ambiguous A\nAmbiguous B", "X\nY", "1\n2\n3"],
                ],
                None,
            ),
        )  # 表头保留；对齐的多记录行可逐行替换；计数冲突行继续保留原始比较文本。

    def test_table_row_replacement_counts_the_exact_center_filtered_glyphs(self) -> None:
        """Cell words cannot authorize deletion of extra text inside the row box."""

        class RowProofPage:
            def __init__(self, characters: list[dict[str, object]], crop_text: str) -> None:
                self.chars = characters
                self._crop_text = crop_text

            def crop(self, _bbox: object) -> "RowProofPage":
                return self

            def extract_text(self, **_kwargs: object) -> str:
                return self._crop_text

        def character(text: str, top: float, bottom: float) -> dict[str, object]:
            return {"text": text, "x0": 2.0, "x1": 4.0, "top": top, "bottom": bottom}

        bbox = (0.0, 0.0, 10.0, 10.0)
        cell_words = [[{"text": "A"}]]
        crossing_page = RowProofPage(
            [character("A", 2.0, 8.0), character("X", -4.0, 2.0)],
            "AX",
        )
        extra_inside_page = RowProofPage(
            [character("A", 2.0, 8.0), character("X", 3.0, 9.0)],
            "AX",
        )

        self.assertTrue(
            _table_row_bbox_matches_raw_cells(
                crossing_page,
                bbox,
                ["A"],
                cell_word_row=cell_words,
            )
        )  # 越界字形中心在相邻行，实际不会被本行中心过滤删除。
        self.assertFalse(
            _table_row_bbox_matches_raw_cells(
                extra_inside_page,
                bbox,
                ["A"],
                cell_word_row=cell_words,
            )
        )  # bbox 中心内的额外 X 会被删除，必须撤销行级替换。

    def test_hybrid_revision_prose_is_not_deleted_with_duplicated_row_tail(self) -> None:
        """A sentence plus table-row tail must stay visible when safe substring removal is unavailable."""

        raw_first_row = (
            "The history of this document is detailed in the table below: Revision Date Description "
            "OIF 2024.058.12 30th March 2026 Updated based on comment resolution spreadsheet oif2026107.01."
        )
        raw_second_row = (
            "OIF 2024.058.13 30th June 2026 "
            "Updated based on comment resolution spreadsheet oif2026.245.01."
        )
        table_lines = [
            (
                "表格行: T1 | Revision=OIF 2024.058.12 | Date=30th March 2026 | "
                "Description=Updated based on comment resolution spreadsheet oif2026107.01."
            ),
            (
                "表格行: T1 | Revision=OIF 2024.058.13 | Date=30th June 2026 | "
                "Description=Updated based on comment resolution spreadsheet oif2026.245.01."
            ),
        ]

        combined = _combine_text_and_table_lines(
            "\n".join(["1 Scope", "The technical requirement remains visible.", raw_first_row, raw_second_row]),
            table_lines,
        )

        self.assertIn("The technical requirement remains visible.", combined)
        self.assertIn(raw_first_row, combined)  # 首句是正文说明，不能因后半段和表格重叠而整行删除。
        self.assertIn(raw_second_row, combined)
        self.assertEqual(2, combined.count("表格行:"))

    def test_table_cell_preserves_unproven_duplicate_multiplication_glyphs(self) -> None:
        """Text shape alone cannot prove that a repeated multiplication glyph is noise."""

        self.assertEqual("3.2×x10–13", _clean_table_cell("3.2×x10–13"))

    def test_large_non_draft_text_is_not_filtered_as_watermark(self) -> None:
        """Watermark filtering should not delete legitimate large headings."""

        self.assertTrue(
            _keep_non_watermark_object({"object_type": "char", "size": 72, "text": "D", "upright": True})
        )  # 大号普通标题字符应保留，避免封面/章节标题丢失。
        self.assertFalse(
            _keep_non_watermark_object({"object_type": "char", "size": 72, "text": "D", "upright": False})
        )  # 旋转的大号 DRAFT 字符仍应被过滤，避免水印污染正文和表格。
        self.assertTrue(
            _keep_non_watermark_object({"object_type": "line", "size": 72, "text": "D"})
        )  # 非字符对象必须保留，表格线条需要参与 pdfplumber 表格识别。

    def test_large_rotated_draft_text_is_filtered_when_upright_flag_is_true(self) -> None:
        """pdfplumber may mark a visibly rotated DRAFT glyph as upright."""

        watermark = {
            "object_type": "char",
            "size": 175.34,
            "text": "D",
            "upright": True,
            "matrix": (101.82, 101.82, -101.82, 101.82, 171.53, 186.69),
        }  # 来自 oif2024.058.13.pdf 的实际字符属性，矩阵表明字符旋转约 45°。

        self.assertFalse(_keep_non_watermark_object(watermark))

    def test_isolated_large_rotated_letter_is_preserved_without_full_watermark_cluster(self) -> None:
        """A lone rotated letter may be real content and is not watermark evidence."""

        isolated_letter = {
            "object_type": "char",
            "size": 90,
            "text": "T",
            "x0": 240,
            "x1": 300,
            "top": 260,
            "bottom": 350,
            "upright": False,
        }
        page = mock.Mock()
        page.width = 600
        page.height = 800
        page.chars = [isolated_letter]
        page.extract_words.return_value = []
        page.crop.return_value = page

        filtered_page = _filtered_layout_page(page)

        self.assertIs(page, filtered_page)
        page.filter.assert_not_called()

    def test_full_spatial_draft_cluster_enables_watermark_filtering(self) -> None:
        """Filtering needs a complete, large, rotated, spatially coherent word."""

        cluster = [
            {
                "object_type": "char",
                "size": 90,
                "text": letter,
                "x0": 90 + index * 72,
                "x1": 150 + index * 72,
                "top": 500 - index * 72,
                "bottom": 590 - index * 72,
                "upright": False,
            }
            for index, letter in enumerate("DRAFT")
        ]
        page = mock.Mock()
        page.width = 600
        page.height = 800
        page.chars = cluster
        page.extract_words.return_value = []
        page.crop.return_value = page
        filtered = object()
        page.filter.return_value = filtered

        filtered_page = _filtered_layout_page(page)

        self.assertIs(filtered, filtered_page)
        predicate = page.filter.call_args.args[0]
        self.assertTrue(all(not predicate(character) for character in cluster))

    def test_verified_oif_bottom_footer_is_filtered_by_coordinates(self) -> None:
        """A repeated-looking OIF footer may be removed only with bottom-margin proof."""

        page = mock.Mock()
        page.width = 612
        page.height = 792
        page.chars = []
        page.crop.return_value = page
        page.extract_words.return_value = [
            {"text": "The", "x0": 72, "x1": 88, "top": 680, "bottom": 691},
            {"text": "requirement", "x0": 91, "x1": 150, "top": 680, "bottom": 691},
            {"text": "www.oiforum.com", "x0": 72, "x1": 158, "top": 733, "bottom": 744},
            {"text": "Copyright", "x0": 72, "x1": 121, "top": 749, "bottom": 760},
            {"text": "draft", "x0": 126, "x1": 151, "top": 760, "bottom": 771},
        ]
        filtered = object()
        page.filter.return_value = filtered

        filtered_page = _filtered_layout_page(page)

        self.assertIs(filtered, filtered_page)
        predicate = page.filter.call_args.args[0]
        self.assertTrue(predicate({"text": "T", "x0": 73, "x1": 78, "top": 681, "bottom": 690}))
        self.assertFalse(predicate({"text": "w", "x0": 73, "x1": 78, "top": 734, "bottom": 743}))
        self.assertFalse(predicate({"text": "d", "x0": 127, "x1": 132, "top": 761, "bottom": 770}))

    def test_oif_clause_without_second_footer_marker_is_preserved(self) -> None:
        """A bottom clause phrase alone is insufficient proof to delete normal content."""

        page = mock.Mock()
        page.width = 612
        page.height = 792
        page.chars = []
        page.crop.return_value = page
        page.extract_words.return_value = [
            {"text": "Requirement", "x0": 72, "x1": 132, "top": 680, "bottom": 691},
            {"text": "Optical", "x0": 72, "x1": 110, "top": 733, "bottom": 744},
            {"text": "Internetworking", "x0": 113, "x1": 186, "top": 733, "bottom": 744},
            {"text": "Forum", "x0": 189, "x1": 220, "top": 733, "bottom": 744},
            {"text": "-", "x0": 223, "x1": 227, "top": 733, "bottom": 744},
            {"text": "Clause", "x0": 230, "x1": 264, "top": 733, "bottom": 744},
            {"text": "31:", "x0": 267, "x1": 282, "top": 733, "bottom": 744},
            {"text": "Implementation", "x0": 72, "x1": 150, "top": 749, "bottom": 760},
            {"text": "Agreement", "x0": 153, "x1": 206, "top": 749, "bottom": 760},
        ]
        filtered = object()
        page.filter.return_value = filtered

        filtered_page = _filtered_layout_page(page)

        self.assertIs(page, filtered_page)
        page.filter.assert_not_called()

    def test_oif_footer_filter_preserves_bottom_body_starting_with_optical(self) -> None:
        """A normal bottom sentence must not widen an OIF footer box by one word."""

        page = mock.Mock()
        page.width = 612
        page.height = 792
        page.chars = []
        page.crop.return_value = page
        page.extract_words.return_value = [
            {"text": "Optical", "x0": 72, "x1": 110, "top": 680, "bottom": 691},
            {"text": "draft", "x0": 113, "x1": 140, "top": 680, "bottom": 691},
            {"text": "requirements", "x0": 143, "x1": 210, "top": 680, "bottom": 691},
            {"text": "apply.", "x0": 213, "x1": 244, "top": 680, "bottom": 691},
            {"text": "www.oiforum.com", "x0": 72, "x1": 158, "top": 733, "bottom": 744},
            {"text": "Copyright", "x0": 72, "x1": 121, "top": 749, "bottom": 760},
            {"text": "draft", "x0": 126, "x1": 151, "top": 760, "bottom": 771},
        ]
        filtered = object()
        page.filter.return_value = filtered

        _filtered_layout_page(page)

        predicate = page.filter.call_args.args[0]
        self.assertTrue(predicate({"text": "O", "x0": 73, "x1": 78, "top": 681, "bottom": 690}))
        self.assertFalse(predicate({"text": "w", "x0": 73, "x1": 78, "top": 734, "bottom": 743}))

    def test_single_page_header_shape_is_not_deleted_without_cross_page_proof(self) -> None:
        """A title-looking top line needs document-wide evidence before separation."""

        page = mock.Mock()
        page.width = 612
        page.height = 792
        page.chars = []
        page.crop.return_value = page
        page.extract_words.return_value = [
            {"text": "The", "x0": 72, "x1": 88, "top": 680, "bottom": 691},
            {"text": "requirement", "x0": 91, "x1": 150, "top": 680, "bottom": 691},
            {"text": "Implementation", "x0": 72, "x1": 150, "top": 30, "bottom": 41},
            {"text": "Agreement", "x0": 153, "x1": 206, "top": 30, "bottom": 41},
            {"text": "OIF-CEI-06", "x0": 209, "x1": 260, "top": 30, "bottom": 41},
            {"text": "Common", "x0": 263, "x1": 303, "top": 30, "bottom": 41},
            {"text": "Electrical", "x0": 306, "x1": 359, "top": 30, "bottom": 41},
            {"text": "I/O", "x0": 362, "x1": 379, "top": 30, "bottom": 41},
        ]
        filtered_page = _filtered_layout_page(page)

        self.assertIs(page, filtered_page)
        page.filter.assert_not_called()

    def test_reports_are_written(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf, new_pdf = write_demo_pdfs(temp_path / "inputs")
            result = run_diff(old_pdf, new_pdf, DiffOptions())
            outputs = write_reports(result, temp_path / "reports", DiffOptions())

            for key in ("markdown", "html", "text", "csv", "table_csv", "json"):
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
            self.assertIn("主要比较 PDF 中可抽取文字", report_html)
            self.assertIn("协议 PDF 差异报告", report_text)
            self.assertIn("- 旧/新页数: 4 / 5", report_text)
            self.assertIn("按章节编号、标题和正文相似度匹配", report_text)
            self.assertIn("表格会额外提供截图辅助复核", report_text)
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
        self.assertIn("3 Acceptance", locations)  # 页脚形文本的页数变化在无坐标证据时保守可见。
        self.assertEqual("3", technical.old_section.page_range)
        self.assertEqual("4", technical.new_section.page_range)
        self.assertIn("3.0 V", all_snippets)
        self.assertIn("2.8 V", all_snippets)
        self.assertIn("Confidential", all_snippets)  # 没有行坐标证据时，页脚形文字保守保留。
        self.assertIn("Page 2 of", all_snippets)
        self.assertIn("旧定位页 3 · 新定位页 4", report_html)
        self.assertIn("按章节编号、标题和正文相似度匹配", report_html)

    def test_long_renamed_sections_can_match_by_middle_content(self) -> None:
        """Long sections should not depend only on their head and tail text."""

        shared_middle = "\n".join(
            f"Shared compliance matrix row {index} keeps the same calibration rule and review anchor."
            for index in range(1, 24)
        )  # 中段主体内容保持一致，模拟长章节中间的大段稳定要求。
        old_head = "\n".join(f"Old introductory context {index} differs materially." for index in range(1, 12))
        new_head = "\n".join(f"New introductory context {index} differs materially." for index in range(1, 12))
        old_tail = "\n".join(f"Old closing note {index} differs materially." for index in range(1, 12))
        new_tail = "\n".join(f"New closing note {index} differs materially." for index in range(1, 12))
        old_extraction = ExtractionResult(
            pdf_path=Path("old_long_middle.pdf"),
            pages=[PageText(page_number=1, text=f"2 Calibration Procedure\n{old_head}\n{shared_middle}\n{old_tail}")],
        )  # 旧章节编号和标题略不同，头尾也不同。
        new_extraction = ExtractionResult(
            pdf_path=Path("new_long_middle.pdf"),
            pages=[PageText(page_number=1, text=f"3 Calibration Process\n{new_head}\n{shared_middle}\n{new_tail}")],
        )  # 新章节应通过标题相似度和中段共同内容匹配为 modified。

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())  # 使用默认阈值验证真实匹配行为。
        change_types = [change.change_type for change in result.changes]  # 收集类型，避免新增+删除错配。

        self.assertIn("modified", change_types)  # 中段采样应帮助长章节匹配成修改。
        self.assertNotIn("added", change_types)  # 不应把新版章节当作孤立新增。
        self.assertNotIn("deleted", change_types)  # 不应把旧版章节当作孤立删除。

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
        self.assertIn("level A", snippets)
        self.assertIn("level B", snippets)
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

    def test_reader_reports_hide_proven_heading_renumber_but_json_keeps_audit(self) -> None:
        """纯标题编号顺延不占读者报告，但机器审计必须保留原始编号。"""

        # 相同标题和正文提供条款身份，唯一变化只有结构编号 2.1 → 2.2。
        old_extraction = ExtractionResult(
            pdf_path=Path("old_reader_renumber.pdf"),
            pages=[PageText(page_number=1, text="2.1 Security\nSupplier shall encrypt logs.")],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_reader_renumber.pdf"),
            pages=[PageText(page_number=1, text="2.2 Security\nSupplier shall encrypt logs.")],
        )
        # 通过公开比较与报告入口验证读者层和审计层的边界。
        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, temp_dir, DiffOptions())
            html = paths["html"].read_text(encoding="utf-8")
            markdown = paths["markdown"].read_text(encoding="utf-8")
            text_report = paths["text"].read_text(encoding="utf-8")
            payload = json.loads(paths["json"].read_text(encoding="utf-8"))

        # 三种人读格式都不应把已证明的定位编号顺延列作技术差异。
        for rendered in (html, markdown, text_report):
            self.assertNotIn("章节标题: 2.1 Security", rendered)
            self.assertNotIn("章节标题: 2.2 Security", rendered)
        # JSON 保留原始替换事实，便于审计和后续规则回放。
        self.assertEqual(1, len(payload["changes"]))
        self.assertEqual(
            "章节标题: 2.1 Security",
            payload["changes"][0]["replaced_snippets"][0]["old"],
        )
        self.assertEqual(
            "章节标题: 2.2 Security",
            payload["changes"][0]["replaced_snippets"][0]["new"],
        )

    def test_reader_heading_renumber_never_hides_technical_value_or_term_changes(self) -> None:
        """编号中性只作用于标题定位，UI 数值和技术术语仍严格比较。"""

        # 同一条款同时包含编号顺延、数值变化和技术术语变化，防止宽泛数字归一化误删后两者。
        old_extraction = ExtractionResult(
            pdf_path=Path("old_mixed_renumber.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "31.3.10 Transmit Equalization\n"
                        "CMIT-LT shall limit residual jitter to 0.023 UI for every lane."
                    ),
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_mixed_renumber.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "31.3.11 Transmit Equalization\n"
                        "CMIS-LT shall limit residual jitter to 0.025 UI for every lane."
                    ),
                )
            ],
        )
        # 公开报告入口同时验证人读差异和机器审计，没有调用编号过滤私有 helper。
        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, temp_dir, DiffOptions())
            html = paths["html"].read_text(encoding="utf-8")
            # 高亮标签会拆开连续源码字符串，数值 oracle 应检查浏览器可见文本。
            html_text = _visible_html_text(html)
            markdown = paths["markdown"].read_text(encoding="utf-8")
            payload = json.loads(paths["json"].read_text(encoding="utf-8"))

        # 标题编号替换不再作为差异片段展示，但卡片定位仍可保留真实章节号供回查源 PDF。
        for rendered in (html_text, markdown):
            self.assertNotIn("章节标题: 31.3.10 Transmit Equalization", rendered)
            self.assertNotIn("章节标题: 31.3.11 Transmit Equalization", rendered)
            self.assertIn("CMIT-LT", rendered)
            self.assertIn("CMIS-LT", rendered)
            self.assertIn("0.023 UI", rendered)
            self.assertIn("0.025 UI", rendered)
        # 审计层仍含标题编号和技术事实，证明读者过滤没有改变后续数值比较输入。
        audit_pairs = payload["changes"][0]["replaced_snippets"]
        self.assertTrue(any("章节标题: 31.3.10" in pair["old"] for pair in audit_pairs))
        self.assertTrue(any("0.023 UI" in pair["old"] for pair in audit_pairs))
        self.assertTrue(any("0.025 UI" in pair["new"] for pair in audit_pairs))

        # 标题中的技术标识符大小写同样属于真实文字变化，不能因章节号顺延被中和。
        title_case_result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_heading_case.pdf"),
                pages=[PageText(page_number=1, text="2.1 CMIT-LT Mode\nStable requirement.")],
            ),
            ExtractionResult(
                pdf_path=Path("new_heading_case.pdf"),
                pages=[PageText(page_number=1, text="2.2 cmit-lt Mode\nStable requirement.")],
            ),
            DiffOptions(),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            title_case_paths = write_reports(title_case_result, temp_dir, DiffOptions())
            title_case_html = _visible_html_text(
                title_case_paths["html"].read_text(encoding="utf-8")
            )
            title_case_markdown = title_case_paths["markdown"].read_text(encoding="utf-8")

        # HTML 与 Markdown 都显示旧、新标识符，标题编号过滤因此保持大小写敏感。
        for rendered in (title_case_html, title_case_markdown):
            self.assertIn("CMIT-LT", rendered)
            self.assertIn("cmit-lt", rendered)

    def test_reader_hides_pure_section_and_table_reference_shifts(self) -> None:
        """正文整句只改 Section/Table 定位编号时，读者隐藏而审计保留。"""

        # 同一句同时顺延条款号和表号，句中没有其它技术值变化。
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_locator_refs.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text="1 Scope\nSee Section 31.3.10 and Table 31-5 for the calibration method.",
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new_locator_refs.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text="1 Scope\nSee Section 31.3.11 and Table 31-6 for the calibration method.",
                    )
                ],
            ),
            DiffOptions(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, temp_dir, DiffOptions())
            html_text = _visible_html_text(paths["html"].read_text(encoding="utf-8"))
            markdown = paths["markdown"].read_text(encoding="utf-8")
            payload = json.loads(paths["json"].read_text(encoding="utf-8"))

        # 三种显式定位词均被窄范围中和，原始旧、新句仍在 JSON 中可追溯。
        for rendered in (html_text, markdown):
            self.assertNotIn("Section 31.3.10", rendered)
            self.assertNotIn("Section 31.3.11", rendered)
            self.assertNotIn("Table 31-5", rendered)
            self.assertNotIn("Table 31-6", rendered)
        audit_pair = payload["changes"][0]["replaced_snippets"][0]
        self.assertIn("Section 31.3.10", audit_pair["old"])
        self.assertIn("Table 31-6", audit_pair["new"])

    def test_reader_hides_changed_reference_lists_and_plural_section_ranges(self) -> None:
        """引用列表增删与复数 Section 范围端点变化不得占用读者报告。"""

        # 复刻用户截图：第一句增加一个表格引用，第二句只顺延引用范围的末端。
        old_body = (
            "Each module-to-host lane shall meet the specifications of Table 31-4 and Table 31-5. "
            "Definitions and methodologies can be found in Sections 31.3.4 to 31.3.18."
        )
        # 新版正文的技术语义未改，只扩展了引用表列表和 Section 引用范围。
        new_body = (
            "Each module-to-host lane shall meet the specifications of Table 31-4, Table 31-5 and Table 31-6. "
            "Definitions and methodologies can be found in Sections 31.3.4 to 31.3.19."
        )
        # 从公开比较入口生成真实 SectionChange，避免只测试私有正则辅助函数。
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_reference_lists.pdf"),
                pages=[PageText(page_number=1, text=f"1 Scope\n{old_body}")],
            ),
            ExtractionResult(
                pdf_path=Path("new_reference_lists.pdf"),
                pages=[PageText(page_number=1, text=f"1 Scope\n{new_body}")],
            ),
            DiffOptions(),
        )

        # 公开写报告入口必须同时满足读者降噪和 JSON 无损审计两个契约。
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, temp_dir, DiffOptions())
            html_text = _visible_html_text(paths["html"].read_text(encoding="utf-8"))
            markdown = paths["markdown"].read_text(encoding="utf-8")
            payload = json.loads(paths["json"].read_text(encoding="utf-8"))

        # HTML 与 Markdown 都不再把纯引用来源变化显示成技术差异。
        for rendered in (html_text, markdown):
            self.assertNotIn("Table 31-4 and Table 31-5", rendered)
            self.assertNotIn("Table 31-4, Table 31-5 and Table 31-6", rendered)
            self.assertNotIn("Sections 31.3.4 to 31.3.18", rendered)
            self.assertNotIn("Sections 31.3.4 to 31.3.19", rendered)
        # JSON 仍保留截图中的四个原始引用事实，供机器审计和后续复查。
        audit_pairs = payload["changes"][0]["replaced_snippets"]
        audit_text = json.dumps(audit_pairs, ensure_ascii=False)
        self.assertIn("Table 31-4 and Table 31-5", audit_text)
        self.assertIn("Table 31-4, Table 31-5 and Table 31-6", audit_text)
        self.assertIn("Sections 31.3.4 to 31.3.18", audit_text)
        self.assertIn("Sections 31.3.4 to 31.3.19", audit_text)

    def test_reader_hides_formula_reference_list_with_empty_extracted_locator(self) -> None:
        """公式出处中一个空括号是抽取缺号，不应变成读者技术差异。"""

        old_sentence = (
            "The reference mated MCB-HCB loss is given in Equation (31-5) "
            "and Equation ()."
        )
        new_sentence = (
            "The reference mated MCB-HCB loss is given in Equation (31-6)."
        )
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_empty_equation_reference.pdf"),
                pages=[PageText(page_number=1, text=f"1 Scope\n{old_sentence}")],
            ),
            ExtractionResult(
                pdf_path=Path("new_empty_equation_reference.pdf"),
                pages=[PageText(page_number=1, text=f"1 Scope\n{new_sentence}")],
            ),
            DiffOptions(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, temp_dir, DiffOptions())
            rendered_reports = (
                _visible_html_text(paths["html"].read_text(encoding="utf-8")),
                paths["markdown"].read_text(encoding="utf-8"),
                paths["text"].read_text(encoding="utf-8"),
            )
            audit = paths["json"].read_text(encoding="utf-8")

        for rendered in rendered_reports:
            self.assertNotIn(old_sentence, rendered)
            self.assertNotIn(new_sentence, rendered)
        self.assertIn(old_sentence, audit)
        self.assertIn(new_sentence, audit)

    def test_reader_pairs_added_removed_reference_list_sentences(self) -> None:
        """长引用列表被 diff 拆成新增和删除时，读者层仍应将两句视为一致。"""

        # 大幅扩展列表会让底层 SequenceMatcher 拆成 added/removed，而不是 replaced pair。
        old_sentence = "Use Tables 1 and 2."
        new_sentence = "Use Tables 1, 2, 3, 4, 5, 6, 7, 8, 9 and 10."
        # 通过公开比较入口保留真实拆分行为，确保报告过滤覆盖这一用户可见路径。
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_long_reference_list.pdf"),
                pages=[PageText(page_number=1, text=f"1 Scope\n{old_sentence}")],
            ),
            ExtractionResult(
                pdf_path=Path("new_long_reference_list.pdf"),
                pages=[PageText(page_number=1, text=f"1 Scope\n{new_sentence}")],
            ),
            DiffOptions(),
        )
        # 先锁定底层确实产生独立新增/删除卡，避免测试偶然退化为已覆盖的 replaced pair。
        raw_removed = [
            snippet
            for change in result.changes
            for snippet in change.removed_snippets
        ]
        raw_added = [
            snippet
            for change in result.changes
            for snippet in change.added_snippets
        ]
        self.assertEqual([old_sentence], raw_removed)
        self.assertEqual([new_sentence], raw_added)
        self.assertFalse(
            any(change.replaced_snippets for change in result.changes)
        )

        # 写报告时只在读者副本中重新配对；原始 JSON 仍输出新增和删除事实。
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, temp_dir, DiffOptions())
            html_text = _visible_html_text(paths["html"].read_text(encoding="utf-8"))
            markdown = paths["markdown"].read_text(encoding="utf-8")
            payload = json.loads(paths["json"].read_text(encoding="utf-8"))

        # 两种读者格式均不得显示纯引用清单扩展，避免长列表逃过降噪规则。
        for rendered in (html_text, markdown):
            self.assertNotIn(old_sentence, rendered)
            self.assertNotIn(new_sentence, rendered)
        # JSON 的 removed/added 字段继续保持底层审计事实，供后续追溯与算法分析。
        audit_removed = [
            snippet
            for change in payload["changes"]
            for snippet in change["removed_snippets"]
        ]
        audit_added = [
            snippet
            for change in payload["changes"]
            for snippet in change["added_snippets"]
        ]
        self.assertEqual([old_sentence], audit_removed)
        self.assertEqual([new_sentence], audit_added)

    def test_reader_cross_card_reference_pair_keeps_value_change(self) -> None:
        """跨卡片引用列表配对不得隐藏同句中的工程限值变化。"""

        # 列表大幅扩展仍会触发 added/deleted 拆分，但 33.5→34.0 dB 必须阻止读者消除。
        old_sentence = "Use Tables 1 and 2 with a 33.5 dB insertion-loss limit."
        new_sentence = (
            "Use Tables 1, 2, 3, 4, 5, 6, 7, 8, 9 and 10 with a 34.0 dB "
            "insertion-loss limit."
        )
        # 使用公开比较入口复现跨卡片形态，同时保留完整技术句作为最终 oracle。
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_cross_card_value.pdf"),
                pages=[PageText(page_number=1, text=f"1 Scope\n{old_sentence}")],
            ),
            ExtractionResult(
                pdf_path=Path("new_cross_card_value.pdf"),
                pages=[PageText(page_number=1, text=f"1 Scope\n{new_sentence}")],
            ),
            DiffOptions(),
        )

        # HTML 与 Markdown 都必须显示旧、新 dB 值，证明跨卡片 key 仍对正文值精确比较。
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, temp_dir, DiffOptions())
            rendered_reports = (
                _visible_html_text(paths["html"].read_text(encoding="utf-8")),
                paths["markdown"].read_text(encoding="utf-8"),
            )

        # 任一读者格式缺少旧值或新值，都说明引用清单降噪越过了数值边界。
        for rendered in rendered_reports:
            self.assertIn("33.5 dB", rendered)
            self.assertIn("34.0 dB", rendered)

    def test_reader_keeps_case_sensitive_unit_and_identifier_with_locator_shift(self) -> None:
        """定位编号顺延不能吞掉 mV/MV 或技术标识符的大小写变化。"""

        # 图引用列表增加的同时伴随单位数量级和标识符大小写变化，必须保留完整旧、新句。
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_case_sensitive_refs.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text="1 Scope\nFigure 31-5 shows CMIT-LT at a 1 mV limit.",
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new_case_sensitive_refs.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text="1 Scope\nFigures 31-6 and 31-7 show cmit-lt at a 1 MV limit.",
                    )
                ],
            ),
            DiffOptions(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, temp_dir, DiffOptions())
            html_text = _visible_html_text(paths["html"].read_text(encoding="utf-8"))
            markdown = paths["markdown"].read_text(encoding="utf-8")

        # 两种读者格式都必须保留大小写敏感事实，证明编号中和后的比较仍为精确比较。
        for rendered in (html_text, markdown):
            self.assertIn("CMIT-LT", rendered)
            self.assertIn("cmit-lt", rendered)
            self.assertIn("1 mV", rendered)
            self.assertIn("1 MV", rendered)

    def test_reader_keeps_bare_engineering_values_after_locator_phrases(self) -> None:
        """单数或复数定位短语后的裸工程值都必须继续严格比较。"""

        # 同时覆盖审查发现的复数 Figures/Conditions 误吞 dB、mV、UI 反例。
        cases = [
            (
                "Condition 5 and 33.5 dB of insertion loss apply.",
                "Condition 6 and 34.0 dB of insertion loss apply.",
                "33.5 dB",
                "34.0 dB",
            ),
            (
                "Figures 5 and 33.5 dB of insertion loss apply.",
                "Figures 6 and 34.0 dB of insertion loss apply.",
                "33.5 dB",
                "34.0 dB",
            ),
            (
                "Figures 31-5 and 1 mV of residual voltage apply.",
                "Figures 31-6 and 2 mV of residual voltage apply.",
                "1 mV",
                "2 mV",
            ),
            (
                "Conditions 5 and 0.023 UI of residual jitter apply.",
                "Conditions 6 and 0.025 UI of residual jitter apply.",
                "0.023 UI",
                "0.025 UI",
            ),
            (
                "Sections 31.3 and 53.125 GBd apply.",
                "Sections 31.4 and 56.25 GBd apply.",
                "53.125 GBd",
                "56.25 GBd",
            ),
            (
                "Figures 5 and 45 degrees of phase rotation apply.",
                "Figures 6 and 46 degrees of phase rotation apply.",
                "45 degrees",
                "46 degrees",
            ),
            (
                "Figures 5 and 45 deg of phase rotation apply.",
                "Figures 6 and 46 deg of phase rotation apply.",
                "45 deg",
                "46 deg",
            ),
            (
                "Figures 5 and 1 mVrms of residual voltage apply.",
                "Figures 6 and 2 mVrms of residual voltage apply.",
                "1 mVrms",
                "2 mVrms",
            ),
            (
                "Figures 5 and 1 mVpp of residual voltage apply.",
                "Figures 6 and 2 mVpp of residual voltage apply.",
                "1 mVpp",
                "2 mVpp",
            ),
            (
                "Figures 5 and 1:1000 ratio apply.",
                "Figures 6 and 2:1000 ratio apply.",
                "1:1000 ratio",
                "2:1000 ratio",
            ),
            (
                "Figures 5 and 1 BER target apply.",
                "Figures 6 and 2 BER target apply.",
                "1 BER",
                "2 BER",
            ),
            (
                "Figures 5 and 4 lanes are required.",
                "Figures 6 and 8 lanes are required.",
                "4 lanes",
                "8 lanes",
            ),
            (
                "Figures 5 and 2 taps are enabled.",
                "Figures 6 and 3 taps are enabled.",
                "2 taps",
                "3 taps",
            ),
            (
                "Figures 5 and 1,000 lanes are required.",
                "Figures 6 and 2,000 lanes are required.",
                "1,000 lanes",
                "2,000 lanes",
            ),
            (
                "Figures 5 and 1 to 4 lanes are required.",
                "Figures 6 and 2 to 4 lanes are required.",
                "1 to 4 lanes",
                "2 to 4 lanes",
            ),
            (
                "Figures 5 and 1 or 2 taps are enabled.",
                "Figures 6 and 3 or 2 taps are enabled.",
                "1 or 2 taps",
                "3 or 2 taps",
            ),
            (
                "Figures 5 and 1 and 1/2 UI of jitter apply.",
                "Figures 6 and 2 and 1/2 UI of jitter apply.",
                "1 and 1/2 UI",
                "2 and 1/2 UI",
            ),
            (
                "Sections 31.3 and 53.125 to 56.25 GBd operation apply.",
                "Sections 31.4 and 54.0 to 56.25 GBd operation apply.",
                "53.125 to 56.25 GBd",
                "54.0 to 56.25 GBd",
            ),
            (
                "Sections 31.3 and 53.125 to 56.25 apply.",
                "Sections 31.4 and 54.0 to 56.25 apply.",
                "53.125 to 56.25",
                "54.0 to 56.25",
            ),
            (
                "Figures 5 and 1 in trace length apply.",
                "Figures 6 and 2 in trace length apply.",
                "1 in",
                "2 in",
            ),
            (
                "Figures 5 and 1 by 2 lane arrays apply.",
                "Figures 6 and 3 by 2 lane arrays apply.",
                "1 by 2",
                "3 by 2",
            ),
            (
                "Figures 5 and 1 at 56 GBd apply.",
                "Figures 6 and 2 at 56 GBd apply.",
                "1 at 56 GBd",
                "2 at 56 GBd",
            ),
            (
                "Figures 5 and 1 with ±0.1 tolerance apply.",
                "Figures 6 and 2 with ±0.1 tolerance apply.",
                "1 with ±0.1",
                "2 with ±0.1",
            ),
            (
                "The limit (Figures 5 and 1) lanes are required.",
                "The limit (Figures 6 and 2) lanes are required.",
                "1) lanes",
                "2) lanes",
            ),
            (
                "The limit [Figures 5 and 1] lanes are required.",
                "The limit [Figures 6 and 2] lanes are required.",
                "1] lanes",
                "2] lanes",
            ),
            (
                "The limit (Sections 31.3 and 31.4) GBd is required.",
                "The limit (Sections 31.5 and 31.6) GBd is required.",
                "31.3 and 31.4) GBd",
                "31.5 and 31.6) GBd",
            ),
            (
                "The limit [Figures 31-3 and 31-4] lanes are required.",
                "The limit [Figures 31-5 and 31-6] lanes are required.",
                "31-3 and 31-4] lanes",
                "31-5 and 31-6] lanes",
            ),
        ]
        # 每个反例都从公开比较与写报告入口执行，避免私有正则单测形成假绿。
        for old_sentence, new_sentence, old_value, new_value in cases:
            with self.subTest(old_sentence=old_sentence):
                result = compare_extractions(
                    ExtractionResult(
                        pdf_path=Path("old_locator_and_value.pdf"),
                        pages=[PageText(page_number=1, text=f"1 Scope\n{old_sentence}")],
                    ),
                    ExtractionResult(
                        pdf_path=Path("new_locator_and_value.pdf"),
                        pages=[PageText(page_number=1, text=f"1 Scope\n{new_sentence}")],
                    ),
                    DiffOptions(),
                )

                # HTML 与 Markdown 必须同时暴露旧、新工程值，证明引用中和没有越界。
                with tempfile.TemporaryDirectory() as temp_dir:
                    paths = write_reports(result, temp_dir, DiffOptions())
                    rendered_reports = (
                        _visible_html_text(paths["html"].read_text(encoding="utf-8")),
                        paths["markdown"].read_text(encoding="utf-8"),
                    )

                # 每种读者格式都必须保留两个值；任一格式隐藏都视为回归。
                for rendered in rendered_reports:
                    self.assertIn(old_value, rendered)
                    self.assertIn(new_value, rendered)

    def test_reader_hides_pure_figure_number_shifts_in_each_context(self) -> None:
        """每个整句只改 Figure 编号时，都在读者报告中视为一致。"""

        # 两个不同技术条款各自只改变 Figure 编号，句中其它技术文字完全一致。
        section_specs = [
            (
                "31.3.10",
                "31.3.11",
                "Jitter Mask",
                "Figure 31-3 shows the receiver jitter mask for every supported lane.",
                "Figure 31-4 shows the receiver jitter mask for every supported lane.",
            ),
            (
                "31.3.11",
                "31.3.12",
                "Output Calibration",
                "Figure 31-4 illustrates the output jitter calibration method.",
                "Figure 31-5 illustrates the output jitter calibration method.",
            ),
        ]
        old_sections: list[Section] = []
        new_sections: list[Section] = []
        changes: list[SectionChange] = []
        # 直接构造公开报告模型，隔离验证读者编号策略而不借助抽取器猜测配对。
        for index, (old_number, new_number, title, old_body, new_body) in enumerate(
            section_specs,
            start=1,
        ):
            old_section = Section(
                section_id=f"old-{index}",
                heading=f"{old_number} {title}",
                title=title,
                level=3,
                heading_path=(f"{old_number} {title}",),
                number_path=(old_number,),
                start_page=index,
                end_page=index,
                body=old_body,
            )
            new_section = Section(
                section_id=f"new-{index}",
                heading=f"{new_number} {title}",
                title=title,
                level=3,
                heading_path=(f"{new_number} {title}",),
                number_path=(new_number,),
                start_page=index,
                end_page=index,
                body=new_body,
            )
            old_sections.append(old_section)
            new_sections.append(new_section)
            changes.append(
                SectionChange(
                    change_type="modified",
                    old_section=old_section,
                    new_section=new_section,
                    similarity=0.98,
                    replaced_snippets=[
                        SnippetPair(
                            f"章节标题: {old_section.heading}",
                            f"章节标题: {new_section.heading}",
                        ),
                        SnippetPair(old_body, new_body),
                    ],
                )
            )
        result = DiffResult(
            old_pdf=Path("old_figure_shift.pdf"),
            new_pdf=Path("new_figure_shift.pdf"),
            old_sections=old_sections,
            new_sections=new_sections,
            changes=changes,
            warnings=[],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, temp_dir, DiffOptions())
            html_text = _visible_html_text(paths["html"].read_text(encoding="utf-8"))
            markdown = paths["markdown"].read_text(encoding="utf-8")
            payload = json.loads(paths["json"].read_text(encoding="utf-8"))

        # 读者报告不再列出纯 Figure 顺延，JSON 仍保存两个原始引用替换。
        for rendered in (html_text, markdown):
            self.assertNotIn("Figure 31-3 shows", rendered)
            self.assertNotIn("Figure 31-5 illustrates", rendered)
        self.assertEqual(2, len(payload["changes"]))
        self.assertTrue(
            any(
                "Figure 31-3" in pair["old"] and "Figure 31-4" in pair["new"]
                for change in payload["changes"]
                for pair in change["replaced_snippets"]
            )
        )

    def test_reader_hides_single_pure_figure_shift_but_keeps_value_change(self) -> None:
        """单句纯图号变化可隐藏，同句工程值变化必须保留。"""

        # 单个句子除图号外完全一致，符合用户要求的编号中性阅读规则。
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_single_figure.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text="1 Scope\nThe return loss is shown in Figure 32-5.",
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new_single_figure.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text="1 Scope\nThe return loss is shown in Figure 32-6.",
                    )
                ],
            ),
            DiffOptions(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, temp_dir, DiffOptions())
            html_text = _visible_html_text(paths["html"].read_text(encoding="utf-8"))
            markdown = paths["markdown"].read_text(encoding="utf-8")
            payload = json.loads(paths["json"].read_text(encoding="utf-8"))

        # 两种读者格式隐藏纯图号替换，机器审计仍保留旧、新原句。
        for rendered in (html_text, markdown):
            self.assertNotIn("Figure 32-5", rendered)
            self.assertNotIn("Figure 32-6", rendered)
        self.assertIn("Figure 32-5", payload["changes"][0]["replaced_snippets"][0]["old"])
        self.assertIn("Figure 32-6", payload["changes"][0]["replaced_snippets"][0]["new"])

        # 同句把限值从 33.5 dB 改为 34.0 dB，定位编号中和后文本仍不相等。
        changed_result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_figure_value.pdf"),
                pages=[PageText(page_number=1, text="1 Scope\nFigure 32-5 shows a 33.5 dB limit.")],
            ),
            ExtractionResult(
                pdf_path=Path("new_figure_value.pdf"),
                pages=[PageText(page_number=1, text="1 Scope\nFigure 32-6 shows a 34.0 dB limit.")],
            ),
            DiffOptions(),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            changed_paths = write_reports(changed_result, temp_dir, DiffOptions())
            changed_html = _visible_html_text(
                changed_paths["html"].read_text(encoding="utf-8")
            )
            changed_markdown = changed_paths["markdown"].read_text(encoding="utf-8")

        # 数值变化使整句继续展示，旧值、新值和图号均可见。
        for rendered in (changed_html, changed_markdown):
            self.assertIn("Figure 32-5", rendered)
            self.assertIn("Figure 32-6", rendered)
            self.assertIn("33.5 dB", rendered)
            self.assertIn("34.0 dB", rendered)

    def test_renumbered_same_heading_with_disjoint_body_is_not_forced_into_modified(self) -> None:
        """A fallback title match cannot override a body score below the configured floor."""

        old_body = "alpha beta gamma " * 43  # 旧正文使用与新版不相交的技术词汇，稳定复现低相似度候选。
        new_body = "voltage current impedance " * 43  # 新正文长度相近，排除仅由长度差导致的拒配。
        old_extraction = ExtractionResult(  # 两侧标题相同但编号不同，强制候选进入 fallback 标题评分。
            pdf_path=Path("old-disjoint-scope.pdf"),
            pages=[PageText(page_number=1, text=f"1 Scope\n{old_body}")],
        )
        new_extraction = ExtractionResult(  # 标题相同但正文无关，形成标题权重曾经强配的反例。
            pdf_path=Path("new-disjoint-scope.pdf"),
            pages=[PageText(page_number=1, text=f"2 Scope\n{new_body}")],
        )

        result = compare_extractions(  # 使用默认 0.72 章节门槛验证公开比较结果。
            old_extraction,
            new_extraction,
            DiffOptions(),
        )
        change_types = [change.change_type for change in result.changes]  # 类型直接反映是否发生危险强配。

        self.assertNotIn("modified", change_types)  # 约 0.24 的正文不能被标题权重抬升为普通修改。
        self.assertEqual(1, change_types.count("added"))  # 无可靠配对时新版章节保守显示为新增。
        self.assertEqual(1, change_types.count("deleted"))  # 无可靠配对时旧版章节保守显示为删除。

    def test_exact_identity_title_does_not_bypass_the_configured_similarity_threshold(self) -> None:
        """A shared number/title cannot replace the requested comparable-text evidence."""

        old_body = "alpha beta gamma " * 43  # 长且互不相交的正文排除“短章整体改写”造成的偶然词面重合。
        new_body = "voltage current impedance " * 43  # 同长度反例确保结果由匹配证据而非长度差驱动。
        old_extraction = ExtractionResult(
            pdf_path=Path("old-exact-disjoint-scope.pdf"),
            pages=[PageText(page_number=1, text=f"1 Scope\n{old_body}")],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new-exact-disjoint-scope.pdf"),
            pages=[PageText(page_number=1, text=f"1 Scope\n{new_body}")],
        )

        for threshold in (0.72, 0.90, 1.0):  # 覆盖默认值、旧固定结构分数边界和最严格边界。
            with self.subTest(threshold=threshold):
                options = (
                    DiffOptions()
                    if threshold == 0.72
                    else DiffOptions(min_section_match_similarity=threshold)
                )  # 默认构造也必须与显式阈值遵守相同的真实相似度语义。
                result = compare_extractions(old_extraction, new_extraction, options)
                change_types = [change.change_type for change in result.changes]

                self.assertNotIn("modified", change_types)  # 实际可比文本远低于 0.72，任何配置都不能由固定 0.90 抬升。
                self.assertEqual(1, change_types.count("added"))  # 无可靠配对时新版章节保守显示为新增。
                self.assertEqual(1, change_types.count("deleted"))  # 旧版同号同题章节也应独立显示为删除。

    def test_long_exact_title_cannot_hide_disjoint_short_bodies(self) -> None:
        """Exact structural identity still needs evidence from two present bodies."""

        title = (
            "Operational Requirements for Deterministic Receiver Calibration "
            "and Compliance Validation"
        )  # 长且完全相同的标题会在整章分数中淹没极短正文。
        old_extraction = ExtractionResult(
            pdf_path=Path("old-long-title-short-body.pdf"),
            pages=[PageText(page_number=1, text=f"1 {title}\nALPHA")],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new-long-title-short-body.pdf"),
            pages=[PageText(page_number=1, text=f"1 {title}\nOMEGA")],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        change_types = [change.change_type for change in result.changes]

        self.assertNotIn("modified", change_types)  # ALPHA/OMEGA 无正文身份证据，不能被长标题强配。
        self.assertEqual(1, change_types.count("added"))
        self.assertEqual(1, change_types.count("deleted"))

    def test_exact_identity_keeps_a_supported_short_body_edit_as_modified(self) -> None:
        """A small edit in a short sentence still has enough independent body evidence."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old-short-supported-edit.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text="1 Output Requirement\nReceiver output shall remain at 5 V.",
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new-short-supported-edit.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text="1 Output Requirement\nReceiver output shall remain at 6 V.",
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        self.assertEqual(1, len(result.changes))
        self.assertEqual("modified", result.changes[0].change_type)  # 正文高度相似时仍应展示精确数值修改。

    def test_unique_exact_section_uses_shared_sentence_anchor_after_large_rewrite(self) -> None:
        """A unique stable clause sentence keeps a heavily rewritten section paired."""

        shared_anchor = (
            "Jitter measurements shall use the recovered reference clock while every other "
            "physical lane transmits an asynchronous QPRBS31-CEI compliant test pattern."
        )
        old_near_fact = (
            "Every output-jitter transmitter shall operate across the declared baud-rate range "
            "with a reference-clock tolerance of 50 ppm."
        )
        new_near_fact = old_near_fact.replace("50 ppm", "100 ppm")
        old_body = " ".join(
            [shared_anchor, old_near_fact]
            + [
                f"Legacy transition method alpha{i} beta{i} gamma{i} defines the old measurement."
                for i in range(12)
            ]
        )
        new_body = " ".join(
            [shared_anchor, new_near_fact]
            + [
                f"Revised IEEE procedure voltage{i} current{i} impedance{i} defines the new measurement."
                for i in range(12)
            ]
        )
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-large-rewrite.pdf"),
                pages=[PageText(page_number=1, text=f"1 Transmitter output jitter\n{old_body}")],
            ),
            ExtractionResult(
                pdf_path=Path("new-large-rewrite.pdf"),
                pages=[PageText(page_number=1, text=f"1 Transmitter output jitter\n{new_body}")],
            ),
            DiffOptions(),
        )

        self.assertEqual(1, len(result.changes))
        self.assertEqual("modified", result.changes[0].change_type)
        self.assertEqual("structural_unique_anchor", result.changes[0].match_basis)
        self.assertIsNotNone(result.changes[0].old_section)
        self.assertIsNotNone(result.changes[0].new_section)
        rendered_snippets = "\n".join(
            result.changes[0].added_snippets + result.changes[0].removed_snippets
        )
        self.assertNotIn(shared_anchor, rendered_snippets)  # 稳定锚点是配对证据，不是差异。
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            html = outputs["html"].read_text(encoding="utf-8")
            markdown = outputs["markdown"].read_text(encoding="utf-8")
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
            csv_text = outputs["csv"].read_text(encoding="utf-8-sig")
        for reader in (html, markdown):
            self.assertIn("同父同层唯一的标题技术锚点+第二正文证据", reader)
            self.assertIn("相似度仍为全文实际值", reader)
        self.assertEqual(
            "structural_unique_anchor",
            payload["changes"][0]["match_basis"],
        )
        self.assertIn("match_basis", csv_text.splitlines()[0])
        self.assertIn("structural_unique_anchor", csv_text)

    def test_generic_shared_sentence_does_not_rescue_disjoint_exact_sections(self) -> None:
        """One generic compliance sentence is not an identity proof for disjoint bodies."""

        generic_anchor = (
            "The device shall comply with all applicable IEEE 802.3 requirements described in "
            "Section 3.2.8 for every declared operating mode."
        )
        old_body = " ".join(
            [generic_anchor]
            + [
                f"Legacy alpha{i} beta{i} gamma{i} behavior defines the old implementation."
                for i in range(12)
            ]
        )
        new_body = " ".join(
            [generic_anchor]
            + [
                f"Revised voltage{i} current{i} impedance{i} procedure defines the new implementation."
                for i in range(12)
            ]
        )
        for threshold in (0.72, 0.99, 1.0):
            with self.subTest(threshold=threshold):
                result = compare_extractions(
                    ExtractionResult(
                        pdf_path=Path("old-generic-anchor.pdf"),
                        pages=[PageText(page_number=1, text=f"1 General\n{old_body}")],
                    ),
                    ExtractionResult(
                        pdf_path=Path("new-generic-anchor.pdf"),
                        pages=[PageText(page_number=1, text=f"1 General\n{new_body}")],
                    ),
                    DiffOptions(min_section_match_similarity=threshold),
                )
                change_types = [change.change_type for change in result.changes]

                self.assertNotIn("modified", change_types)
                self.assertEqual(1, change_types.count("added"))
                self.assertEqual(1, change_types.count("deleted"))

    def test_one_of_two_generic_units_cannot_rescue_disjoint_exact_sections(self) -> None:
        """A matching boilerplate sentence cannot override a disjoint technical unit."""

        shared = "The device shall comply with all applicable IEEE 802.3 requirements."
        old_body = (
            f"{shared}\n"
            "Legacy alpha beta gamma behavior defines the old implementation."
        )
        new_body = (
            f"{shared}\n"
            "Revised voltage current impedance procedure defines the new implementation."
        )

        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-two-unit-generic.pdf"),
                pages=[PageText(page_number=1, text=f"1 General\n{old_body}")],
            ),
            ExtractionResult(
                pdf_path=Path("new-two-unit-generic.pdf"),
                pages=[PageText(page_number=1, text=f"1 General\n{new_body}")],
            ),
            DiffOptions(),
        )

        change_types = [change.change_type for change in result.changes]
        self.assertNotIn("modified", change_types)
        self.assertEqual(1, change_types.count("added"))
        self.assertEqual(1, change_types.count("deleted"))

    def test_equivalent_count_and_identifier_case_change_remain_one_section(self) -> None:
        """A real technical-token edit must not expose an equivalent count spelling."""

        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-mixed-count-id.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Queue Processing\n"
                            "Capture one million packets.\n"
                            "Profile CMIS-LT is selected.\n"
                            "The limit is 20 mV."
                        ),
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new-mixed-count-id.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Queue Processing\n"
                            "Capture 1,000,000 packets.\n"
                            "Profile cmis-lt is selected.\n"
                            "The limit is 21 mV."
                        ),
                    )
                ],
            ),
            DiffOptions(),
        )

        self.assertEqual(1, len(result.changes))
        self.assertEqual("modified", result.changes[0].change_type)
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            html = outputs["html"].read_text(encoding="utf-8")
            markdown = outputs["markdown"].read_text(encoding="utf-8")
            text_report = outputs["text"].read_text(encoding="utf-8")
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
            csv_text = outputs["csv"].read_text(encoding="utf-8-sig")
        for reader in (markdown, text_report):
            self.assertIn("CMIS-LT", reader)
            self.assertIn("cmis-lt", reader)
            self.assertIn("20 mV", reader)
            self.assertIn("21 mV", reader)
            self.assertNotIn("one million packets", reader)
            self.assertNotIn("1,000,000 packets", reader)
        for visible_token in ("CMIS-LT", "cmis-lt", "20", "21", "mV"):
            self.assertIn(visible_token, html)
        self.assertNotIn("one million packets", html)
        self.assertNotIn("1,000,000 packets", html)
        self.assertEqual(1, len(payload["changes"]))
        self.assertIn("CMIS-LT", csv_text)
        self.assertIn("cmis-lt", csv_text)

    def test_equivalent_count_and_long_field_value_change_remain_one_section(self) -> None:
        """A stable unknown field identifies an opaque technical value replacement."""

        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-mixed-count-mode.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Queue Processing\n"
                            "Capture one million packets.\n"
                            "Mode = PAM4."
                        ),
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new-mixed-count-mode.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Queue Processing\n"
                            "Capture 1,000,000 packets.\n"
                            "Mode = LEGACY_128B130B_FLIT_DISABLED.\n"
                            "The limit is 20 mV."
                        ),
                    )
                ],
            ),
            DiffOptions(),
        )

        self.assertEqual(1, len(result.changes))
        self.assertEqual("modified", result.changes[0].change_type)
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            readers = [
                outputs["html"].read_text(encoding="utf-8"),
                outputs["markdown"].read_text(encoding="utf-8"),
                outputs["text"].read_text(encoding="utf-8"),
            ]
        for reader in readers:
            self.assertIn("PAM4", reader)
            self.assertIn("LEGACY_128B130B_FLIT_DISABLED", reader)
            self.assertIn("The limit is 20 mV.", reader)
            self.assertNotIn("one million packets", reader)
            self.assertNotIn("1,000,000 packets", reader)

    def test_reordered_technical_sentences_remain_visible(self) -> None:
        """Moving unchanged technical sentences must remain visible in every reader."""

        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-technical-order.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text="1 Modes\nPAM4 mode applies.\nNRZ mode applies.",
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new-technical-order.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text="1 Modes\nNRZ mode applies\nPAM4 mode applies",
                    )
                ],
            ),
            DiffOptions(),
        )

        self.assertEqual(1, len(result.changes))
        self.assertEqual("modified", result.changes[0].change_type)
        self.assertFalse(result.changes[0].added_snippets)
        self.assertFalse(result.changes[0].removed_snippets)
        self.assertTrue(
            any(
                "PAM4" in pair.old
                and "NRZ" in pair.old
                and "PAM4" in pair.new
                and "NRZ" in pair.new
                for pair in result.changes[0].replaced_snippets
            )
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            readers = [
                outputs["html"].read_text(encoding="utf-8"),
                outputs["markdown"].read_text(encoding="utf-8"),
                outputs["text"].read_text(encoding="utf-8"),
            ]
        for reader in readers:
            self.assertIn("PAM4", reader)
            self.assertIn("NRZ", reader)

    def test_reordered_repeated_technical_blocks_remain_visible_once(self) -> None:
        """Repeated A,A,B,B blocks still provide one compact order-change fact."""

        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-repeated-order.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Modes\nPAM4 mode applies.\nPAM4 mode applies.\n"
                            "NRZ mode applies.\nNRZ mode applies."
                        ),
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new-repeated-order.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Modes\nNRZ mode applies\nNRZ mode applies\n"
                            "PAM4 mode applies\nPAM4 mode applies"
                        ),
                    )
                ],
            ),
            DiffOptions(),
        )

        self.assertEqual(1, len(result.changes))
        change = result.changes[0]
        self.assertEqual("modified", change.change_type)
        self.assertEqual(1, len(change.replaced_snippets))
        self.assertFalse(change.added_snippets)
        self.assertFalse(change.removed_snippets)
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            readers = [
                outputs["html"].read_text(encoding="utf-8"),
                outputs["markdown"].read_text(encoding="utf-8"),
                outputs["text"].read_text(encoding="utf-8"),
            ]
        for reader in readers:
            self.assertIn("PAM4", reader)
            self.assertIn("NRZ", reader)

    def test_repeated_count_change_is_not_fabricated_as_reordering(self) -> None:
        """Deleting one duplicate remains a deletion when occurrence identity is ambiguous."""

        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-repeated-count.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Modes\nPAM4 mode applies.\nNRZ mode applies.\n"
                            "PAM4 mode applies."
                        ),
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new-repeated-count.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text="1 Modes\nNRZ mode applies.\nPAM4 mode applies.",
                    )
                ],
            ),
            DiffOptions(),
        )

        self.assertEqual(1, len(result.changes))
        change = result.changes[0]
        self.assertEqual("modified", change.change_type)
        self.assertFalse(change.replaced_snippets)
        self.assertFalse(change.added_snippets)
        self.assertTrue(any("PAM4" in snippet for snippet in change.removed_snippets))
        self.assertTrue(any("PAM4" in snippet for snippet in change.audit_removed_snippets))

    def test_interleaved_reordering_has_no_duplicate_add_delete_facts(self) -> None:
        """Every inversion in an interleaved repeated sequence is covered once."""

        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-interleaved-order.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Rates\nAlpha rate applies.\nAlpha rate applies.\n"
                            "Bravo rate applies.\nCharlie rate applies."
                        ),
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new-interleaved-order.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Rates\nBravo rate applies.\nAlpha rate applies.\n"
                            "Charlie rate applies.\nAlpha rate applies."
                        ),
                    )
                ],
            ),
            DiffOptions(),
        )

        self.assertEqual(1, len(result.changes))
        change = result.changes[0]
        self.assertEqual("modified", change.change_type)
        self.assertEqual(1, len(change.replaced_snippets))
        self.assertFalse(change.added_snippets)
        self.assertFalse(change.removed_snippets)
        self.assertFalse(change.audit_added_snippets)
        self.assertFalse(change.audit_removed_snippets)
        for token in ("Alpha", "Bravo"):
            self.assertIn(token, change.replaced_snippets[0].old)
            self.assertIn(token, change.replaced_snippets[0].new)
        self.assertNotIn("Charlie", change.replaced_snippets[0].old)
        self.assertNotIn("Charlie", change.replaced_snippets[0].new)

    def test_reordering_with_an_extra_duplicate_reports_both_facts(self) -> None:
        """Multiset excess is reported separately from the common units' reordering."""

        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-order-plus-extra.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text="1 Modes\nPAM4 mode applies.\nNRZ mode applies.",
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new-order-plus-extra.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Modes\nNRZ mode applies.\nPAM4 mode applies.\n"
                            "PAM4 mode applies."
                        ),
                    )
                ],
            ),
            DiffOptions(),
        )

        self.assertEqual(1, len(result.changes))
        change = result.changes[0]
        self.assertEqual("modified", change.change_type)
        self.assertEqual(1, len(change.replaced_snippets))
        self.assertEqual(["PAM4 mode applies."], change.added_snippets)
        self.assertFalse(change.removed_snippets)
        self.assertEqual(["PAM4 mode applies."], change.audit_added_snippets)

    def test_moved_assignment_edit_pairs_across_common_unit(self) -> None:
        """A moved stable field remains one replacement plus the true extra sentence."""

        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-moved-assignment.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text="1 Modes\nMode = PAM4.\nCapture path remains stable.",
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new-moved-assignment.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Modes\nCapture path remains stable.\nMode = NRZ.\n"
                            "The limit is 20 mV."
                        ),
                    )
                ],
            ),
            DiffOptions(),
        )

        self.assertEqual(1, len(result.changes))
        change = result.changes[0]
        self.assertEqual("modified", change.change_type)
        self.assertEqual(1, len(change.replaced_snippets))
        self.assertIn("PAM4", change.replaced_snippets[0].old)
        self.assertIn("NRZ", change.replaced_snippets[0].new)
        self.assertEqual(["The limit is 20 mV."], change.added_snippets)
        self.assertFalse(change.removed_snippets)

    def test_global_residual_pairing_respects_assignment_field_identity(self) -> None:
        """Shared values cannot cross-pair two moved explicit assignment fields."""

        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-cross-field.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Settings\nMode = ALPHA_ALPHA_ALPHA.\n"
                            "Capture path remains stable.\nState = BETA."
                        ),
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new-cross-field.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Settings\nState = ALPHA_ALPHA_ALPHA.\n"
                            "Capture path remains stable.\nMode = BETA."
                        ),
                    )
                ],
            ),
            DiffOptions(),
        )

        self.assertEqual(1, len(result.changes))
        pairs = result.changes[0].replaced_snippets
        self.assertEqual(2, len(pairs))
        self.assertTrue(
            any(
                pair.old.startswith("Mode =") and pair.new.startswith("Mode =")
                for pair in pairs
            )
        )
        self.assertTrue(
            any(
                pair.old.startswith("State =") and pair.new.startswith("State =")
                for pair in pairs
            )
        )
        self.assertFalse(
            any(
                pair.old.startswith("Mode =") and pair.new.startswith("State =")
                for pair in pairs
            )
        )
        self.assertFalse(
            any(
                pair.old.startswith("State =") and pair.new.startswith("Mode =")
                for pair in pairs
            )
        )

    def test_local_swap_with_peripheral_duplicates_has_no_false_add_delete(self) -> None:
        """All common occurrences are removed from opcode noise after a local swap."""

        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-local-swap.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Modes\nAlpha mode applies.\nBravo mode applies.\n"
                            "Charlie mode applies.\nAlpha mode applies.\nBravo mode applies."
                        ),
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new-local-swap.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Modes\nAlpha mode applies.\nCharlie mode applies.\n"
                            "Bravo mode applies.\nAlpha mode applies.\nBravo mode applies."
                        ),
                    )
                ],
            ),
            DiffOptions(max_snippets_per_section=1),
        )

        self.assertEqual(1, len(result.changes))
        change = result.changes[0]
        self.assertEqual("modified", change.change_type)
        self.assertEqual(1, len(change.replaced_snippets))
        self.assertFalse(change.added_snippets)
        self.assertFalse(change.removed_snippets)
        self.assertFalse(change.audit_added_snippets)
        self.assertFalse(change.audit_removed_snippets)
        self.assertEqual(0, change.omitted_snippet_count)

    def test_large_local_reorder_keeps_evidence_bounded(self) -> None:
        """One local swap must not copy a whole large section into one snippet."""

        old_units = [f"Technical mode {index} remains stable." for index in range(500)]
        new_units = list(old_units)
        new_units[249], new_units[250] = new_units[250], new_units[249]

        pair, covered_old, covered_new = compare_module._common_unit_occurrence_evidence(
            old_units,
            new_units,
        )

        self.assertIsNotNone(pair)
        assert pair is not None
        self.assertEqual(500, len(covered_old))
        self.assertEqual(500, len(covered_new))
        self.assertEqual(1, pair.old.count("\n"))
        self.assertEqual(1, pair.new.count("\n"))
        self.assertLess(len(pair.old) + len(pair.new), 500)

        old_text = "1 Modes\n" + "\n".join(old_units)
        new_text = "1 Modes\n" + "\n".join(new_units)
        with mock.patch.object(
            compare_module,
            "_review_units_share_sentence_skeleton",
            wraps=compare_module._review_units_share_sentence_skeleton,
        ) as skeleton_match:
            result = compare_extractions(
                ExtractionResult(
                    pdf_path=Path("old-large-order.pdf"),
                    pages=[PageText(page_number=1, text=old_text)],
                ),
                ExtractionResult(
                    pdf_path=Path("new-large-order.pdf"),
                    pages=[PageText(page_number=1, text=new_text)],
                ),
                DiffOptions(max_snippets_per_section=1),
            )

        self.assertEqual(1, len(result.changes))
        self.assertEqual("modified", result.changes[0].change_type)
        self.assertLess(skeleton_match.call_count, 20)
        self.assertLess(
            sum(
                len(pair.old) + len(pair.new)
                for pair in result.changes[0].replaced_snippets
            ),
            500,
        )

    def test_large_remote_assignment_reorder_keeps_exact_field_identity(self) -> None:
        """Stable explicit fields must survive bounded matching at any distance."""

        first_words = ("Alpha", "Bravo", "Charlie", "Delta", "Echo")
        second_words = ("Red", "Blue", "Green", "White", "Black")
        third_words = ("Circle", "Square", "Triangle", "Diamond", "Hexagon")
        fields = [
            f"{first} {second} {third}"
            for first in first_words
            for second in second_words
            for third in third_words
        ]
        old_units = [f"{field} = LEGACY." for field in fields]
        shifted_fields = fields[62:] + fields[:62]
        new_units = [f"{field} = RECOVERY." for field in shifted_fields]

        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-large-fields.pdf"),
                pages=[
                    PageText(page_number=1, text="1 Settings\n" + "\n".join(old_units))
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new-large-fields.pdf"),
                pages=[
                    PageText(page_number=1, text="1 Settings\n" + "\n".join(new_units))
                ],
            ),
            DiffOptions(max_snippets_per_section=2),
        )

        self.assertEqual(1, len(result.changes))
        change = result.changes[0]
        self.assertEqual("modified", change.change_type)
        self.assertEqual(125, len(change.audit_replaced_snippets))
        self.assertFalse(change.audit_added_snippets)
        self.assertFalse(change.audit_removed_snippets)
        self.assertTrue(
            all(
                pair.old.partition("=")[0].strip()
                == pair.new.partition("=")[0].strip()
                for pair in change.audit_replaced_snippets
            )
        )

    def test_large_residual_pairing_uses_bounded_candidates(self) -> None:
        """A long rewritten section must not score every old/new unit pair."""

        old_units = [f"Channel {index} mode uses legacy path." for index in range(100)]
        new_units = [f"Channel {index} mode uses revised route." for index in range(100)]
        with mock.patch.object(
            compare_module,
            "_unit_pair_score",
            wraps=compare_module._unit_pair_score,
        ) as pair_score:
            result = compare_extractions(
                ExtractionResult(
                    pdf_path=Path("old-large-residual.pdf"),
                    pages=[
                        PageText(
                            page_number=1,
                            text="1 Settings\n" + "\n".join(old_units),
                        )
                    ],
                ),
                ExtractionResult(
                    pdf_path=Path("new-large-residual.pdf"),
                    pages=[
                        PageText(
                            page_number=1,
                            text="1 Settings\n" + "\n".join(new_units),
                        )
                    ],
                ),
                DiffOptions(max_snippets_per_section=2),
            )

        self.assertEqual(1, len(result.changes))
        self.assertEqual("modified", result.changes[0].change_type)
        self.assertEqual(100, len(result.changes[0].audit_replaced_snippets))
        self.assertLess(pair_score.call_count, 1000)

    def test_free_text_numeric_lanes_fail_closed_across_threshold(self) -> None:
        """Whitespace-only numeric labels stay visible without guessed attribution."""

        for count in (32, 33):
            old_units = [
                f"Lane {index} voltage limit is 20 mV." for index in range(count)
            ]
            shift = count // 2
            shifted_indexes = list(range(count))[shift:] + list(range(count))[:shift]
            new_units = [
                f"Lane {index} voltage limit is 21 mV." for index in shifted_indexes
            ]
            with self.subTest(count=count):
                result = compare_extractions(
                    ExtractionResult(
                        pdf_path=Path(f"old-lanes-{count}.pdf"),
                        pages=[
                            PageText(
                                page_number=1,
                                text="1 Lane Limits\n" + "\n".join(old_units),
                            )
                        ],
                    ),
                    ExtractionResult(
                        pdf_path=Path(f"new-lanes-{count}.pdf"),
                        pages=[
                            PageText(
                                page_number=1,
                                text="1 Lane Limits\n" + "\n".join(new_units),
                            )
                        ],
                    ),
                    DiffOptions(max_snippets_per_section=count),
                )

                self.assertEqual(1, len(result.changes))
                change = result.changes[0]
                self.assertEqual("modified", change.change_type)
                self.assertFalse(change.audit_replaced_snippets)
                self.assertTrue(change.audit_added_snippets)
                self.assertTrue(change.audit_removed_snippets)

    def test_large_free_text_fail_closed_report_is_bounded(self) -> None:
        """Conservative record evidence keeps all text without creating giant cards."""

        count = 120
        old_units = [
            f"Lane {index} voltage limit is 20 mV." for index in range(count)
        ]
        shifted_indexes = list(range(count))[60:] + list(range(count))[:60]
        new_units = [
            f"Lane {index} voltage limit is 21 mV." for index in shifted_indexes
        ]
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-large-free-text.pdf"),
                pages=[PageText(page_number=1, text="1 Lanes\n" + "\n".join(old_units))],
            ),
            ExtractionResult(
                pdf_path=Path("new-large-free-text.pdf"),
                pages=[PageText(page_number=1, text="1 Lanes\n" + "\n".join(new_units))],
            ),
            DiffOptions(max_snippets_per_section=1),
        )

        self.assertEqual(1, len(result.changes))
        change = result.changes[0]
        self.assertEqual("modified", change.change_type)
        self.assertFalse(change.audit_replaced_snippets)
        self.assertGreater(len(change.audit_added_snippets), 1)
        self.assertGreater(len(change.audit_removed_snippets), 1)
        self.assertTrue(
            all(
                len(snippet) <= 6000
                for snippet in (
                    *change.audit_added_snippets,
                    *change.audit_removed_snippets,
                )
            )
        )
        audited = " ".join(
            (*change.audit_added_snippets, *change.audit_removed_snippets)
        )
        self.assertIn("Lane 0 voltage limit", audited)
        self.assertIn("Lane 119 voltage limit", audited)
        self.assertGreater(change.omitted_snippet_count, 0)

    def test_cjk_free_text_numeric_labels_remain_unattributed(self) -> None:
        """CJK label-number text remains visible without guessed record identity."""

        count = 100
        old_units = [
            f"接收机通道{index}的电压限制为20毫伏。" for index in range(count)
        ]
        shifted_indexes = list(range(count))[50:] + list(range(count))[:50]
        new_units = [
            f"接收机通道{index}的电压限制为21毫伏。"
            for index in shifted_indexes
        ]
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-cjk-channels.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text="1 接收机限制\n" + "\n".join(old_units),
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new-cjk-channels.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text="1 接收机限制\n" + "\n".join(new_units),
                    )
                ],
            ),
            DiffOptions(max_snippets_per_section=count),
        )

        self.assertEqual(1, len(result.changes))
        change = result.changes[0]
        self.assertEqual("modified", change.change_type)
        self.assertFalse(change.audit_replaced_snippets)
        self.assertTrue(change.audit_added_snippets)
        self.assertTrue(change.audit_removed_snippets)

    def test_cjk_number_word_records_remain_unattributed(self) -> None:
        """Chinese-number labels use the same conservative policy as digits."""

        chinese_numbers = (
            "零", "一", "二", "三", "四", "五", "六", "七", "八", "九",
            "十", "十一", "十二", "十三", "十四", "十五", "十六", "十七",
            "十八", "十九", "二十", "二十一", "二十二", "二十三", "二十四",
            "二十五", "二十六", "二十七", "二十八", "二十九", "三十",
            "三十一", "三十二",
        )
        for separator in ("", " ", "   ", "\N{NO-BREAK SPACE}"):
            for count in (32, 33):
                old_units = [
                    f"接收机通道{separator}{number}的电压限制为二十毫伏。"
                    for number in chinese_numbers[:count]
                ]
                shift = count // 2
                shifted_numbers = (
                    list(chinese_numbers[:count])[shift:]
                    + list(chinese_numbers[:count])[:shift]
                )
                new_units = [
                    f"接收机通道{separator}{number}的电压限制为二十一毫伏。"
                    for number in shifted_numbers
                ]
                with self.subTest(count=count, separator=repr(separator)):
                    result = compare_extractions(
                        ExtractionResult(
                            pdf_path=Path(f"old-cjk-words-{count}.pdf"),
                            pages=[
                                PageText(
                                    page_number=1,
                                    text="1 接收机限制\n" + "\n".join(old_units),
                                )
                            ],
                        ),
                        ExtractionResult(
                            pdf_path=Path(f"new-cjk-words-{count}.pdf"),
                            pages=[
                                PageText(
                                    page_number=1,
                                    text="1 接收机限制\n" + "\n".join(new_units),
                                )
                            ],
                        ),
                        DiffOptions(max_snippets_per_section=count),
                    )

                    self.assertEqual(1, len(result.changes))
                    change = result.changes[0]
                    self.assertEqual("modified", change.change_type)
                    self.assertFalse(change.audit_replaced_snippets)
                    self.assertTrue(change.audit_added_snippets)
                    self.assertTrue(change.audit_removed_snippets)

    def test_cjk_numeric_value_collision_remains_unattributed(self) -> None:
        """A value equal to another CJK label cannot create a guessed pairing."""

        chinese_numbers = (
            "零", "一", "二", "三", "四", "五", "六", "七", "八", "九",
            "十", "十一", "十二", "十三", "十四", "十五", "十六", "十七",
            "十八", "十九", "二十", "二十一", "二十二", "二十三", "二十四",
            "二十五", "二十六", "二十七", "二十八", "二十九", "三十",
            "三十一", "三十二",
        )
        next_numbers = chinese_numbers[1:] + ("零",)
        old_units = [
            f"接收机通道{number}的电压限制为{number}毫伏。"
            for number in chinese_numbers
        ]
        shifted_indexes = list(range(33))[16:] + list(range(33))[:16]
        new_units = [
            f"接收机通道{chinese_numbers[index]}的电压限制为{next_numbers[index]}毫伏。"
            for index in shifted_indexes
        ]
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-cjk-id-value-collision.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text="1 接收机限制\n" + "\n".join(old_units),
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new-cjk-id-value-collision.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text="1 接收机限制\n" + "\n".join(new_units),
                    )
                ],
            ),
            DiffOptions(max_snippets_per_section=33),
        )

        self.assertEqual(1, len(result.changes))
        change = result.changes[0]
        self.assertEqual("modified", change.change_type)
        self.assertFalse(change.audit_replaced_snippets)
        self.assertTrue(change.audit_added_snippets)
        self.assertTrue(change.audit_removed_snippets)

    def test_cjk_table_reference_and_record_number_remain_unattributed(self) -> None:
        """Channel, locator, and value numbers cannot guess a free-text primary key."""

        count = 8
        old_units = [
            f"接收机通道{index}应符合表31的要求，电压限制为20毫伏。"
            for index in range(count)
        ]
        shifted_indexes = list(range(count))[4:] + list(range(count))[:4]
        new_units = [
            f"接收机通道{index}应符合表32的要求，电压限制为21毫伏。"
            for index in shifted_indexes
        ]
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-cjk-table-reference.pdf"),
                pages=[PageText(page_number=1, text="1 通道\n" + "\n".join(old_units))],
            ),
            ExtractionResult(
                pdf_path=Path("new-cjk-table-reference.pdf"),
                pages=[PageText(page_number=1, text="1 通道\n" + "\n".join(new_units))],
            ),
            DiffOptions(max_snippets_per_section=count),
        )

        self.assertEqual(1, len(result.changes))
        change = result.changes[0]
        self.assertEqual("modified", change.change_type)
        self.assertFalse(change.audit_replaced_snippets)
        self.assertTrue(change.audit_added_snippets)
        self.assertTrue(change.audit_removed_snippets)

    def test_repeated_cjk_label_cannot_borrow_count_as_composite_identity(self) -> None:
        """A repeated channel cannot use configuration/count text to fake uniqueness."""

        for count_phrase in ("配置", "包含", "使用", "报告", "具有", "需要"):
            old_units = [
                f"通道1{count_phrase}2个模式供接收器甲使用，限制为20毫伏。",
                f"通道1{count_phrase}3个模式供接收器乙使用，限制为30毫伏。",
            ]
            new_units = [
                f"通道1{count_phrase}3个模式供接收器甲使用，限制为21毫伏。",
                f"通道1{count_phrase}2个模式供接收器乙使用，限制为31毫伏。",
            ]
            with self.subTest(count_phrase=count_phrase):
                result = compare_extractions(
                    ExtractionResult(
                        pdf_path=Path(f"old-repeated-cjk-{count_phrase}.pdf"),
                        pages=[PageText(page_number=1, text="1 配置\n" + "\n".join(old_units))],
                    ),
                    ExtractionResult(
                        pdf_path=Path(f"new-repeated-cjk-{count_phrase}.pdf"),
                        pages=[PageText(page_number=1, text="1 配置\n" + "\n".join(new_units))],
                    ),
                    DiffOptions(max_snippets_per_section=4),
                )

                self.assertTrue(result.changes)
                self.assertTrue(
                    all(not change.audit_replaced_snippets for change in result.changes)
                )
                evidence = " ".join(
                    snippet
                    for change in result.changes
                    for snippet in (
                        *change.audit_added_snippets,
                        *change.audit_removed_snippets,
                    )
                )
                self.assertIn("接收器甲", evidence)
                self.assertIn("接收器乙", evidence)

    def test_semicolon_split_free_text_reorder_keeps_every_value_fact(self) -> None:
        """A moved record split at a semicolon cannot lose its value half."""

        count = 8
        old_units = [
            f"Count {index} equipment for receiver RX{index}; "
            f"limit {20 + index} mV."
            for index in range(count)
        ]
        shifted_indexes = list(range(count))[4:] + list(range(count))[:4]
        new_units = [
            f"Count {index} equipment for receiver RX{index}; "
            f"limit {21 + index} mV."
            for index in shifted_indexes
        ]
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-semicolon-records.pdf"),
                pages=[PageText(page_number=1, text="1 Limits\n" + "\n".join(old_units))],
            ),
            ExtractionResult(
                pdf_path=Path("new-semicolon-records.pdf"),
                pages=[PageText(page_number=1, text="1 Limits\n" + "\n".join(new_units))],
            ),
            DiffOptions(max_snippets_per_section=100),
        )

        self.assertEqual(1, len(result.changes))
        change = result.changes[0]
        self.assertEqual("modified", change.change_type)
        self.assertFalse(change.audit_replaced_snippets)
        evidence = "\n".join(
            (*change.audit_added_snippets, *change.audit_removed_snippets)
        )
        for index in range(count):
            self.assertIn(f"limit {20 + index} mV", evidence)
            self.assertIn(f"limit {21 + index} mV", evidence)

    def test_narrative_count_cannot_prove_section_identity(self) -> None:
        """A shared prose verb plus count remains candidate evidence, not identity."""

        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-narrative-count.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Procedure\n"
                            "The procedure uses 2 examples for optical calibration."
                        ),
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new-narrative-count.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Procedure\n"
                            "The receiver uses 2 examples for copper link training."
                        ),
                    )
                ],
            ),
            DiffOptions(),
        )

        self.assertEqual(["added", "deleted"], sorted(change.change_type for change in result.changes))

    def test_narrative_count_cannot_force_residual_replacement(self) -> None:
        """Stable surrounding units do not turn a generic count into a field key."""

        stable_units = (
            "The implementation preserves every declared timing boundary.",
            "The receiver records each completed transaction for review.",
            "The device reports faults through the standard interface.",
        )
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-residual-narrative-count.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Procedure\n"
                            + "\n".join(stable_units)
                            + "\nThe procedure uses 2 examples for optical calibration."
                        ),
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new-residual-narrative-count.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Procedure\n"
                            + "\n".join(stable_units)
                            + "\nThe receiver uses 2 examples for copper link training."
                        ),
                    )
                ],
            ),
            DiffOptions(max_snippets_per_section=4),
        )

        self.assertEqual(1, len(result.changes))
        change = result.changes[0]
        self.assertEqual("modified", change.change_type)
        self.assertFalse(change.audit_replaced_snippets)
        self.assertEqual(1, len(change.audit_added_snippets))
        self.assertEqual(1, len(change.audit_removed_snippets))

    def test_free_text_metadata_cannot_prove_record_identity(self) -> None:
        """Version/Lane/value candidates cannot choose a free-text primary key."""

        for count in (32, 33):
            old_units = [
                f"Version 1 Lane {index} has a voltage limit of {index} mV."
                for index in range(count)
            ]
            shift = count // 2
            shifted_indexes = list(range(count))[shift:] + list(range(count))[:shift]
            new_units = [
                f"Version 1 Lane {index} has a voltage limit of {(index + 1) % count} mV."
                for index in shifted_indexes
            ]
            with self.subTest(count=count):
                result = compare_extractions(
                    ExtractionResult(
                        pdf_path=Path(f"old-version-lanes-{count}.pdf"),
                        pages=[
                            PageText(
                                page_number=1,
                                text="1 Lane Limits\n" + "\n".join(old_units),
                            )
                        ],
                    ),
                    ExtractionResult(
                        pdf_path=Path(f"new-version-lanes-{count}.pdf"),
                        pages=[
                            PageText(
                                page_number=1,
                                text="1 Lane Limits\n" + "\n".join(new_units),
                            )
                        ],
                    ),
                    DiffOptions(max_snippets_per_section=count),
                )

                self.assertEqual(1, len(result.changes))
                change = result.changes[0]
                self.assertEqual("modified", change.change_type)
                self.assertFalse(change.audit_replaced_snippets)
                self.assertTrue(change.audit_added_snippets)
                self.assertTrue(change.audit_removed_snippets)

    def test_free_text_unique_values_cannot_prove_record_identity(self) -> None:
        """Unique Lane/profile candidates still lack structured-key provenance."""

        count = 33
        old_units = [
            f"Version 1 Lane {index} uses profile {1000 + index} for receiver calibration."
            for index in range(count)
        ]
        shifted_indexes = list(range(count))[16:] + list(range(count))[:16]
        new_units = [
            f"Version 1 Lane {index} uses profile {1001 + index} for receiver calibration."
            for index in shifted_indexes
        ]
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-profile-lanes.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text="1 Lane Profiles\n" + "\n".join(old_units),
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new-profile-lanes.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text="1 Lane Profiles\n" + "\n".join(new_units),
                    )
                ],
            ),
            DiffOptions(max_snippets_per_section=count),
        )

        self.assertEqual(1, len(result.changes))
        change = result.changes[0]
        self.assertEqual("modified", change.change_type)
        self.assertFalse(change.audit_replaced_snippets)
        self.assertTrue(change.audit_added_snippets)
        self.assertTrue(change.audit_removed_snippets)

    def test_free_text_numeric_fields_do_not_guess_a_primary_record_key(self) -> None:
        """Whitespace-only English fields remain candidates, never strong record identity."""

        count = 33
        old_units = [
            f"Version 1 Lane {index} uses profile {1000 + index} for receiver calibration."
            for index in range(count)
        ]
        shifted_indexes = list(range(count))[16:] + list(range(count))[:16]
        new_units = [
            f"Version 1 Lane {index} uses profile {1000 + ((index + 1) % count)} for receiver calibration."
            for index in shifted_indexes
        ]
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-competing-identities.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text="1 Lane Profiles\n" + "\n".join(old_units),
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new-competing-identities.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text="1 Lane Profiles\n" + "\n".join(new_units),
                    )
                ],
            ),
            DiffOptions(max_snippets_per_section=count * 2),
        )

        self.assertTrue(result.changes)
        self.assertEqual(["modified"], [change.change_type for change in result.changes])
        self.assertTrue(
            all(not change.audit_replaced_snippets for change in result.changes)
        )
        self.assertTrue(result.changes[0].audit_added_snippets)
        self.assertTrue(result.changes[0].audit_removed_snippets)

    def test_free_text_record_addition_does_not_enable_guessed_identity(self) -> None:
        """A new free-text record remains visible without guessed Lane pairings."""

        for count in (32, 33):
            old_units = [
                f"Lane {index} has voltage limit {20 + index} mV."
                for index in range(count)
            ]
            shifted_indexes = list(range(count))[count // 2 :] + list(range(count))[: count // 2]
            new_units = [
                f"Lane {index} has voltage limit {21 + index} mV."
                for index in shifted_indexes
            ] + [f"Lane {count} has voltage limit {21 + count} mV."]
            with self.subTest(count=count):
                result = compare_extractions(
                    ExtractionResult(
                        pdf_path=Path(f"old-lane-extra-{count}.pdf"),
                        pages=[
                            PageText(
                                page_number=1,
                                text="1 Lane Limits\n" + "\n".join(old_units),
                            )
                        ],
                    ),
                    ExtractionResult(
                        pdf_path=Path(f"new-lane-extra-{count}.pdf"),
                        pages=[
                            PageText(
                                page_number=1,
                                text="1 Lane Limits\n" + "\n".join(new_units),
                            )
                        ],
                    ),
                    DiffOptions(max_snippets_per_section=count + 1),
                )

                self.assertEqual(1, len(result.changes))
                change = result.changes[0]
                self.assertEqual("modified", change.change_type)
                self.assertFalse(change.audit_replaced_snippets)
                self.assertTrue(change.audit_added_snippets)
                self.assertTrue(change.audit_removed_snippets)

                reverse = compare_extractions(
                    ExtractionResult(
                        pdf_path=Path(f"old-lane-remove-{count}.pdf"),
                        pages=[
                            PageText(
                                page_number=1,
                                text="1 Lane Limits\n" + "\n".join(new_units),
                            )
                        ],
                    ),
                    ExtractionResult(
                        pdf_path=Path(f"new-lane-remove-{count}.pdf"),
                        pages=[
                            PageText(
                                page_number=1,
                                text="1 Lane Limits\n" + "\n".join(old_units),
                            )
                        ],
                    ),
                    DiffOptions(max_snippets_per_section=count + 1),
                )
                self.assertEqual(1, len(reverse.changes))
                reverse_change = reverse.changes[0]
                self.assertEqual("modified", reverse_change.change_type)
                self.assertFalse(reverse_change.audit_replaced_snippets)
                self.assertTrue(reverse_change.audit_added_snippets)
                self.assertTrue(reverse_change.audit_removed_snippets)

    def test_bounded_matching_does_not_lock_duplicate_partial_identities(self) -> None:
        """Duplicate identifiers remain unproven when one side has an extra occurrence."""

        old_units = [
            "Lane 1 voltage limit is 20 mV.",
            "Lane 1 voltage limit is 21 mV.",
            "Lane 2 voltage limit is 22 mV.",
            "Lane 3 voltage limit is 23 mV.",
        ]
        new_units = [
            "Lane 3 voltage limit is 24 mV.",
            "Lane 2 voltage limit is 23 mV.",
            "Lane 1 voltage limit is 25 mV.",
            "Lane 1 voltage limit is 26 mV.",
            "Lane 1 voltage limit is 27 mV.",
        ]
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-duplicate-partial-id.pdf"),
                pages=[PageText(page_number=1, text="1 Profiles\n" + "\n".join(old_units))],
            ),
            ExtractionResult(
                pdf_path=Path("new-duplicate-partial-id.pdf"),
                pages=[PageText(page_number=1, text="1 Profiles\n" + "\n".join(new_units))],
            ),
            DiffOptions(max_snippets_per_section=8),
        )

        self.assertTrue(result.changes)
        self.assertTrue(
            all(
                not pair.old.startswith("Lane 1 ")
                or pair.new.startswith("Lane 1 ")
                for change in result.changes
                for pair in (change.audit_replaced_snippets or ())
            )
        )  # 重复 Lane 1 无唯一 occurrence 证明，至少不能跨 Lane 强归因。

    def test_free_text_two_sided_overlap_remains_unattributed(self) -> None:
        """A sliding free-text window exposes facts without guessed Lane pairings."""

        count = 33
        old_units = [
            f"Lane {index} has voltage limit {20 + index} mV."
            for index in range(count)
        ]
        shared = list(range(1, count))
        shifted_shared = shared[16:] + shared[:16]
        new_units = [
            f"Lane {index} has voltage limit {21 + index} mV."
            for index in shifted_shared
        ] + [f"Lane {count} has voltage limit {21 + count} mV."]
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-lane-window.pdf"),
                pages=[PageText(page_number=1, text="1 Lanes\n" + "\n".join(old_units))],
            ),
            ExtractionResult(
                pdf_path=Path("new-lane-window.pdf"),
                pages=[PageText(page_number=1, text="1 Lanes\n" + "\n".join(new_units))],
            ),
            DiffOptions(max_snippets_per_section=count + 1),
        )

        self.assertEqual(1, len(result.changes))
        change = result.changes[0]
        self.assertEqual("modified", change.change_type)
        self.assertFalse(change.audit_replaced_snippets)
        self.assertTrue(change.audit_added_snippets)
        self.assertTrue(change.audit_removed_snippets)

    def test_free_text_composite_numeric_fields_remain_unattributed(self) -> None:
        """Port+Lane text without structure is not promoted to a composite key."""

        records = [(port, lane) for port in range(6) for lane in range(6)]
        shifted = records[18:] + records[:18]
        old_units = [
            f"Port {port} Lane {lane} has voltage limit {20 + index} mV."
            for index, (port, lane) in enumerate(records)
        ]
        new_units = [
            f"Port {port} Lane {lane} has voltage limit {21 + records.index((port, lane))} mV."
            for port, lane in shifted
        ]
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-port-lane-grid.pdf"),
                pages=[PageText(page_number=1, text="1 Grid\n" + "\n".join(old_units))],
            ),
            ExtractionResult(
                pdf_path=Path("new-port-lane-grid.pdf"),
                pages=[PageText(page_number=1, text="1 Grid\n" + "\n".join(new_units))],
            ),
            DiffOptions(max_snippets_per_section=len(records)),
        )

        self.assertEqual(1, len(result.changes))
        change = result.changes[0]
        self.assertEqual("modified", change.change_type)
        self.assertFalse(change.audit_replaced_snippets)
        self.assertTrue(change.audit_added_snippets)
        self.assertTrue(change.audit_removed_snippets)

    def test_free_text_five_field_candidates_fail_closed_within_section(self) -> None:
        """Many free-text fields stay in one chapter without fuzzy attribution."""

        records = [
            (rack, shelf, port, lane, slot)
            for rack in range(2)
            for shelf in range(2)
            for port in range(2)
            for lane in range(2)
            for slot in range(2)
        ]
        shifted = records[16:] + records[:16]
        old_units = [
            (
                f"Rack {rack} Shelf {shelf} Port {port} Lane {lane} Slot {slot} "
                f"has voltage limit {20 + index} mV."
            )
            for index, (rack, shelf, port, lane, slot) in enumerate(records)
        ]
        new_units = [
            (
                f"Rack {rack} Shelf {shelf} Port {port} Lane {lane} Slot {slot} "
                f"has voltage limit {21 + records.index((rack, shelf, port, lane, slot))} mV."
            )
            for rack, shelf, port, lane, slot in shifted
        ]
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-five-field-key.pdf"),
                pages=[PageText(page_number=1, text="1 Grid\n" + "\n".join(old_units))],
            ),
            ExtractionResult(
                pdf_path=Path("new-five-field-key.pdf"),
                pages=[PageText(page_number=1, text="1 Grid\n" + "\n".join(new_units))],
            ),
            DiffOptions(max_snippets_per_section=len(records) * 2),
        )

        self.assertEqual(1, len(result.changes))
        change = result.changes[0]
        self.assertEqual("modified", change.change_type)
        self.assertFalse(change.audit_replaced_snippets)
        self.assertTrue(change.audit_added_snippets)
        self.assertTrue(change.audit_removed_snippets)

    def test_ordinary_count_verbs_cannot_become_record_identity(self) -> None:
        """Unique shifted counts after prose verbs cannot override an alphanumeric record id."""

        count = 8
        old_units = [
            f"Receiver RX{index} uses {index + 1} lanes and includes {100 + index} warnings."
            for index in range(count)
        ]
        shifted_indexes = list(range(count))[4:] + list(range(count))[:4]
        new_units = [
            f"Receiver RX{index} uses {index + 2} lanes and includes {101 + index} warnings."
            for index in shifted_indexes
        ]
        for verbs in (("uses", "includes"), ("count", "add"), ("show", "record")):
            verb_old_units = [
                re.sub(r"\buses\b", verbs[0], unit).replace("includes", verbs[1])
                for unit in old_units
            ]
            verb_new_units = [
                re.sub(r"\buses\b", verbs[0], unit).replace("includes", verbs[1])
                for unit in new_units
            ]
            for style in ("lower", "title", "upper"):
                def styled(unit: str) -> str:
                    if style == "lower":
                        return unit
                    if style == "title":
                        return unit.title()
                    return unit.upper()

                with self.subTest(verbs=verbs, style=style):
                    result = compare_extractions(
                        ExtractionResult(
                            pdf_path=Path(f"old-shifted-count-values-{style}.pdf"),
                            pages=[
                                PageText(
                                    page_number=1,
                                    text="1 Receivers\n"
                                    + "\n".join(map(styled, verb_old_units)),
                                )
                            ],
                        ),
                        ExtractionResult(
                            pdf_path=Path(f"new-shifted-count-values-{style}.pdf"),
                            pages=[
                                PageText(
                                    page_number=1,
                                    text="1 Receivers\n"
                                    + "\n".join(map(styled, verb_new_units)),
                                )
                            ],
                        ),
                        DiffOptions(max_snippets_per_section=count),
                    )

                    self.assertEqual(1, len(result.changes))
                    self.assertEqual("modified", result.changes[0].change_type)
                    self.assertTrue(
                        all(not change.audit_replaced_snippets for change in result.changes)
                    )
                    self.assertTrue(result.changes[0].audit_added_snippets)
                    self.assertTrue(result.changes[0].audit_removed_snippets)

    def test_multiple_ordinary_count_verbs_remain_a_normal_numeric_edit(self) -> None:
        """Several prose count slots do not trigger composite-key fail-closed mode."""

        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-prose-count-slots.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Status\n"
                            "The device reports 2 warnings and records 4 failures.\n"
                            "The device reports 3 warnings and records 5 failures."
                        ),
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new-prose-count-slots.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Status\n"
                            "The device reports 6 warnings and records 8 failures.\n"
                            "The device reports 7 warnings and records 9 failures."
                        ),
                    )
                ],
            ),
            DiffOptions(max_snippets_per_section=4),
        )

        self.assertEqual(1, len(result.changes))
        self.assertEqual("modified", result.changes[0].change_type)

    def test_shared_count_order_cannot_rescue_disjoint_sections(self) -> None:
        """A reordered ordinary count is not enough to prove chapter identity."""

        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-disjoint-count-order.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 General\n"
                            "The procedure uses 2 examples for optical calibration.\n"
                            "The link uses 3 examples for legacy timing."
                        ),
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new-disjoint-count-order.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 General\n"
                            "The receiver uses 3 examples for copper training.\n"
                            "The transmitter uses 2 examples for packet routing."
                        ),
                    )
                ],
            ),
            DiffOptions(max_snippets_per_section=4),
        )

        self.assertEqual(
            ["added", "deleted"],
            sorted(change.change_type for change in result.changes),
        )
        self.assertTrue(
            all(not change.audit_replaced_snippets for change in result.changes)
        )

    def test_comparison_operator_cannot_prove_assignment_identity(self) -> None:
        """Comparison operators must not bypass the disjoint-unit hard gate."""

        for old_condition, new_condition in (
            (
                "If mode == LEGACY, follow the optical calibration path.",
                "If mode == RECOVERY, use the copper training method.",
            ),
            (
                "If margin >= 20 mV, follow the optical calibration path.",
                "If margin >= 4 UI, use the copper training method.",
            ),
            (
                "If margin <= 20 mV, follow the optical calibration path.",
                "If margin <= 4 UI, use the copper training method.",
            ),
            (
                "If mode ~= LEGACY, follow the optical calibration path.",
                "If mode ~= RECOVERY, use the copper training method.",
            ),
            (
                "If mode =~ LEGACY, follow the optical calibration path.",
                "If mode =~ RECOVERY, use the copper training method.",
            ),
            (
                "If mode != LEGACY, follow the optical calibration path.",
                "If mode != RECOVERY, use the copper training method.",
            ),
            (
                "If margin += 20 mV, follow the optical calibration path.",
                "If margin += 4 UI, use the copper training method.",
            ),
            (
                "If mode := LEGACY, follow the optical calibration path.",
                "If mode := RECOVERY, use the copper training method.",
            ),
            (
                "If margin -= 20 mV, follow the optical calibration path.",
                "If margin -= 4 UI, use the copper training method.",
            ),
            (
                "If ratio /= 2, follow the optical calibration path.",
                "If ratio /= 4, use the copper training method.",
            ),
            (
                "If mode .= LEGACY, follow the optical calibration path.",
                "If mode .= RECOVERY, use the copper training method.",
            ),
        ):
            with self.subTest(old_condition=old_condition):
                result = compare_extractions(
                    ExtractionResult(
                        pdf_path=Path("old-comparison-operator.pdf"),
                        pages=[
                            PageText(
                                page_number=1,
                                text=f"1 General\nCapture one million packets.\n{old_condition}",
                            )
                        ],
                    ),
                    ExtractionResult(
                        pdf_path=Path("new-comparison-operator.pdf"),
                        pages=[
                            PageText(
                                page_number=1,
                                text=f"1 General\nCapture 1,000,000 packets.\n{new_condition}",
                            )
                        ],
                    ),
                    DiffOptions(),
                )

                change_types = [change.change_type for change in result.changes]
                self.assertNotIn("modified", change_types)
                self.assertEqual(1, change_types.count("added"))
                self.assertEqual(1, change_types.count("deleted"))

    def test_unproven_copula_subject_cannot_rescue_disjoint_exact_sections(self) -> None:
        """Natural-language copula subjects are not stable assignment fields."""

        old_tail = "a legacy optical calibration path."
        new_tail = "a revised copper training method."
        for subject, copula in (
            ("There", "is"),
            ("Here", "is"),
            ("They", "are"),
            ("These", "are"),
            ("The former", "is"),
            ("The first", "is"),
            ("The following", "is"),
            ("Elsewhere", "is"),
            ("This requirement", "is"),
            ("The section", "is"),
            ("The following requirement", "is"),
            ("Such a requirement", "is"),
            ("That item", "is"),
            ("The result", "is"),
            ("This statement", "is"),
            ("The above item", "is"),
            ("Our requirement", "is"),
            ("The former process", "is"),
            ("The previous method", "is"),
            ("Both alternatives", "are"),
            ("What follows", "is"),
            ("The method above", "is"),
            ("Mode", "is"),
            ("Current requirement", "is"),
            ("Present requirement", "is"),
            ("Selected requirement", "is"),
            ("Applicable requirement", "is"),
            ("Existing process", "is"),
            ("Proposed method", "is"),
            ("Specified method", "is"),
            ("Defined value", "is"),
            ("Named section", "is"),
        ):
            with self.subTest(subject=subject):
                result = compare_extractions(
                    ExtractionResult(
                        pdf_path=Path("old-referential-subject.pdf"),
                        pages=[
                            PageText(
                                page_number=1,
                                text=(
                                    "1 General\nCapture one million packets.\n"
                                    f"{subject} {copula} {old_tail}"
                                ),
                            )
                        ],
                    ),
                    ExtractionResult(
                        pdf_path=Path("new-referential-subject.pdf"),
                        pages=[
                            PageText(
                                page_number=1,
                                text=(
                                    "1 General\nCapture 1,000,000 packets.\n"
                                    f"{subject} {copula} {new_tail}"
                                ),
                            )
                        ],
                    ),
                    DiffOptions(),
                )

                change_types = [change.change_type for change in result.changes]
                self.assertNotIn("modified", change_types)
                self.assertEqual(1, change_types.count("added"))
                self.assertEqual(1, change_types.count("deleted"))

    def test_single_short_field_value_change_does_not_depend_on_raw_similarity(self) -> None:
        """A proven field/value slot remains one modified single-unit section."""

        for old_body, new_body in (
            ("RX_STATE=IDLE.", "RX_STATE=RECOVERY."),
            ("rx-state = idle.", "rx-state = recovery."),
            ("lane[0] = idle.", "lane[0] = recovery."),
            (
                "Threshold = .5 UI.",
                "Threshold = .123456789012345678901234567890 UI.",
            ),
            (
                "Threshold = -.5 UI.",
                "Threshold = +.125 UI.",
            ),
        ):
            with self.subTest(old_body=old_body, new_body=new_body):
                result = compare_extractions(
                    ExtractionResult(
                        pdf_path=Path("old-single-field.pdf"),
                        pages=[PageText(page_number=1, text=f"1 Operating Mode\n{old_body}")],
                    ),
                    ExtractionResult(
                        pdf_path=Path("new-single-field.pdf"),
                        pages=[PageText(page_number=1, text=f"1 Operating Mode\n{new_body}")],
                    ),
                    DiffOptions(),
                )

                self.assertEqual(1, len(result.changes))
                self.assertEqual("modified", result.changes[0].change_type)
                self.assertEqual(old_body, result.changes[0].replaced_snippets[0].old)
                self.assertEqual(new_body, result.changes[0].replaced_snippets[0].new)

    def test_unproven_colon_label_cannot_rescue_disjoint_exact_sections(self) -> None:
        """A natural-language colon label is not a technical field identity proof."""

        shared = "Capture one million packets."
        for label in (
            "Note",
            "Notes",
            "Note 1",
            "Note-1",
            "Warning 2",
            "Warning-2",
            "Example",
            "Example-1",
            "Requirement",
            "Requirement-id",
            "Caution",
            "Caution-warning",
            "Important",
            "Tip",
            "Observation",
            "Summary",
            "Summary.note",
            "Background_info",
            "See-also",
            "Context",
            "Rationale",
            "State",
        ):
            with self.subTest(label=label):
                result = compare_extractions(
                    ExtractionResult(
                        pdf_path=Path("old-discourse-label.pdf"),
                        pages=[
                            PageText(
                                page_number=1,
                                text=f"1 General\n{shared}\n{label}: PAM4.",
                            )
                        ],
                    ),
                    ExtractionResult(
                        pdf_path=Path("new-discourse-label.pdf"),
                        pages=[
                            PageText(
                                page_number=1,
                                text=f"1 General\nCapture 1,000,000 packets.\n{label}: NRZ.",
                            )
                        ],
                    ),
                    DiffOptions(),
                )

                change_types = [change.change_type for change in result.changes]
                self.assertNotIn("modified", change_types)
                self.assertEqual(1, change_types.count("added"))
                self.assertEqual(1, change_types.count("deleted"))

    def test_exact_section_allows_an_ordered_sentence_addition(self) -> None:
        """A preserved original sentence plus one new fact is one modified section."""

        shared = (
            "The receiver shall support PAM4 operation across every declared lane "
            "and operating mode."
        )
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-ordered-addition.pdf"),
                pages=[PageText(page_number=1, text=f"1 General\n{shared}")],
            ),
            ExtractionResult(
                pdf_path=Path("new-ordered-addition.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=f"1 General\n{shared}\nThe limit is 0.025 UI.",
                    )
                ],
            ),
            DiffOptions(),
        )

        self.assertEqual(1, len(result.changes))
        self.assertEqual("modified", result.changes[0].change_type)
        self.assertEqual(["The limit is 0.025 UI."], result.changes[0].added_snippets)
        self.assertEqual([], result.changes[0].removed_snippets)
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, Path(temp_dir), DiffOptions())
            for suffix in (".md", ".txt"):
                report = next(path for path in paths.values() if path.suffix == suffix)
                reader = report.read_text(encoding="utf-8-sig")
                self.assertIn("The limit is 0.025 UI.", reader)
                self.assertNotIn(shared, reader)

    def test_structural_anchor_rescue_respects_a_stricter_configured_threshold(self) -> None:
        """A technical anchor may help at the default floor but cannot ignore a 0.99 request."""

        technical_anchor = (
            "Jitter measurements shall use the recovered CRU while every other physical lane "
            "transmits the asynchronous QPRBS31-CEI test pattern."
        )
        old_near_fact = (
            "Every output-jitter transmitter shall operate across the declared baud-rate range "
            "with a reference-clock tolerance of 50 ppm."
        )
        new_near_fact = old_near_fact.replace("50 ppm", "100 ppm")
        old_body = " ".join(
            [technical_anchor, old_near_fact]
            + [f"Legacy alpha{i} beta{i} gamma{i} method applies." for i in range(12)]
        )
        new_body = " ".join(
            [technical_anchor, new_near_fact]
            + [f"Revised voltage{i} current{i} impedance{i} procedure applies." for i in range(12)]
        )
        default_result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-default-anchor.pdf"),
                pages=[PageText(page_number=1, text=f"1 Output jitter\n{old_body}")],
            ),
            ExtractionResult(
                pdf_path=Path("new-default-anchor.pdf"),
                pages=[PageText(page_number=1, text=f"1 Output jitter\n{new_body}")],
            ),
            DiffOptions(),
        )
        self.assertEqual(["modified"], [change.change_type for change in default_result.changes])
        self.assertEqual("structural_unique_anchor", default_result.changes[0].match_basis)

        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-strict-anchor.pdf"),
                pages=[PageText(page_number=1, text=f"1 Output jitter\n{old_body}")],
            ),
            ExtractionResult(
                pdf_path=Path("new-strict-anchor.pdf"),
                pages=[PageText(page_number=1, text=f"1 Output jitter\n{new_body}")],
            ),
            DiffOptions(min_section_match_similarity=0.99),
        )
        change_types = [change.change_type for change in result.changes]

        self.assertNotIn("modified", change_types)
        self.assertEqual(1, change_types.count("added"))
        self.assertEqual(1, change_types.count("deleted"))

    def test_unique_exact_section_ignores_added_table_rows_when_prose_is_unchanged(self) -> None:
        """Structured rows cannot split one unchanged prose clause into red/green cards."""

        stable_prose = "Refer to Section 3.2.8."
        added_rows = "\n".join(
            f"表格行: T1 | Parameter=Lane {index} interference tolerance | Value={index} | Units=dB"
            for index in range(1, 14)
        )
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-table-rows.pdf"),
                pages=[PageText(page_number=1, text=f"1 Input Lane-to-Lane Skew\n{stable_prose}")],
            ),
            ExtractionResult(
                pdf_path=Path("new-table-rows.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=f"1 Input Lane-to-Lane Skew\n{stable_prose}\n{added_rows}",
                    )
                ],
            ),
            DiffOptions(),
        )

        self.assertEqual([], result.changes)  # 表格另由结构化表格区承载，正文不应重复红绿。

    def test_unique_exact_section_can_use_two_matched_sibling_brackets(self) -> None:
        """Two stable adjacent siblings can identify one fully rewritten middle clause."""

        old_middle = " ".join(
            f"Legacy alpha{i} beta{i} gamma{i} method defines the old behavior."
            for i in range(12)
        )
        new_middle = " ".join(
            f"Revised voltage{i} current{i} impedance{i} procedure defines the new behavior."
            for i in range(12)
        )
        old_text = (
            "1 Receiver requirements\n"
            "1.1 Stable before\nThe receiver shall preserve the first stable boundary.\n"
            f"1.2 Rewritten method\n{old_middle}\n"
            "1.3 Stable after\nThe receiver shall preserve the second stable boundary."
        )
        new_text = old_text.replace(old_middle, new_middle)
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-bracketed-rewrite.pdf"),
                pages=[PageText(page_number=1, text=old_text)],
            ),
            ExtractionResult(
                pdf_path=Path("new-bracketed-rewrite.pdf"),
                pages=[PageText(page_number=1, text=new_text)],
            ),
            DiffOptions(),
        )

        middle_changes = [
            change
            for change in result.changes
            if any(
                section is not None
                and section.number_path
                and section.number_path[-1] == "1.2"
                for section in (change.old_section, change.new_section)
            )
        ]
        self.assertEqual(1, len(middle_changes))
        self.assertEqual("modified", middle_changes[0].change_type)
        self.assertEqual(
            "structural_adjacent_brackets",
            middle_changes[0].match_basis,
        )

    def test_bracket_rescue_can_use_an_independently_proven_table_sibling(self) -> None:
        """A prose-proven table sibling may close one bracket without a rescue chain."""

        old_middle = " ".join(
            f"Legacy alpha{i} beta{i} gamma{i} method defines the old behavior."
            for i in range(12)
        )
        new_middle = " ".join(
            f"Revised voltage{i} current{i} impedance{i} procedure defines the new behavior."
            for i in range(12)
        )
        stable_after = "The receiver shall preserve the independently proven final boundary."
        old_rows = "\n".join(
            f"表格行: T1 | Parameter=Legacy {index} | Value={index} mV"
            for index in range(14)
        )
        new_rows = "\n".join(
            f"表格行: T1 | Parameter=Revised {index} | Value={index + 20} mV"
            for index in range(14)
        )
        old_text = (
            "1 Receiver requirements\n"
            "1.1 Stable before\nThe receiver shall preserve the first stable boundary.\n"
            f"1.2 Rewritten method\n{old_middle}\n"
            f"1.3 Stable after\n{stable_after}\n{old_rows}"
        )
        new_text = (
            "1 Receiver requirements\n"
            "1.1 Stable before\nThe receiver shall preserve the first stable boundary.\n"
            f"1.2 Rewritten method\n{new_middle}\n"
            f"1.3 Stable after\n{stable_after}\n{new_rows}"
        )

        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-independent-bracket.pdf"),
                pages=[PageText(page_number=1, text=old_text)],
            ),
            ExtractionResult(
                pdf_path=Path("new-independent-bracket.pdf"),
                pages=[PageText(page_number=1, text=new_text)],
            ),
            DiffOptions(),
        )

        middle_changes = [
            change
            for change in result.changes
            if any(
                section is not None
                and section.number_path
                and section.number_path[-1] == "1.2"
                for section in (change.old_section, change.new_section)
            )
        ]
        self.assertEqual(1, len(middle_changes))
        self.assertEqual("modified", middle_changes[0].change_type)
        self.assertEqual("structural_adjacent_brackets", middle_changes[0].match_basis)

    def test_duplicate_exact_paths_do_not_use_shared_anchor_rescue(self) -> None:
        """Repeated numbering remains ambiguous even when both copies share one sentence."""

        shared_anchor = (
            "The receiver shall use the common recovered clock for every declared operating mode."
        )
        old_first = " ".join(
            f"Legacy alpha{i} beta{i} gamma{i} behavior applies only to the first copy."
            for i in range(10)
        )
        old_second = " ".join(
            f"Legacy delta{i} epsilon{i} zeta{i} behavior applies only to the second copy."
            for i in range(10)
        )
        new_first = " ".join(
            f"Revised voltage{i} current{i} impedance{i} behavior applies only to the first copy."
            for i in range(10)
        )
        new_second = " ".join(
            f"Revised loss{i} delay{i} bandwidth{i} behavior applies only to the second copy."
            for i in range(10)
        )
        old_text = "\n".join(
            (
                "1 Shared clause",
                shared_anchor,
                old_first,
                "1 Shared clause",
                shared_anchor,
                old_second,
            )
        )
        new_text = "\n".join(
            (
                "1 Shared clause",
                shared_anchor,
                new_first,
                "1 Shared clause",
                shared_anchor,
                new_second,
            )
        )
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-duplicate-path.pdf"),
                pages=[PageText(page_number=1, text=old_text)],
            ),
            ExtractionResult(
                pdf_path=Path("new-duplicate-path.pdf"),
                pages=[PageText(page_number=1, text=new_text)],
            ),
            DiffOptions(min_section_match_similarity=0.99),
        )
        change_types = [change.change_type for change in result.changes]

        self.assertNotIn("modified", change_types)
        self.assertEqual(2, change_types.count("added"))
        self.assertEqual(2, change_types.count("deleted"))

    def test_exact_identity_preserves_an_empty_container_section(self) -> None:
        """An empty parent has no contradictory body and may match by exact structure."""

        text = (
            "1 Receiver Requirements\n"
            "1.1 Output Limit\n"
            "Receiver output shall remain at 5 V."
        )
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-empty-container.pdf"),
                pages=[PageText(page_number=1, text=text)],
            ),
            ExtractionResult(
                pdf_path=Path("new-empty-container.pdf"),
                pages=[PageText(page_number=1, text=text)],
            ),
            DiffOptions(include_unchanged_sections=True),
        )

        parent_changes = [
            change
            for change in result.changes
            if change.report_location == "1 Receiver Requirements"
        ]
        self.assertEqual(1, len(parent_changes))
        self.assertEqual("unchanged", parent_changes[0].change_type)  # 空正文容器仍依靠编号和标题稳定配对。

    def test_empty_exact_clause_cannot_steal_a_shifted_populated_clause(self) -> None:
        """An empty old clause must not consume a populated clause that shifted forward."""

        old_text = (
            "1 Receiver\n"
            "1.1 Output Limit\n"
            "1.2 Output Limit\n"
            "The limit applies.\n"
            "1.3 Stable Tail\n"
            "The receiver shall preserve timing."
        )
        new_text = (
            "1 Receiver\n"
            "1.1 Output Limit\n"
            "The limit applies.\n"
            "1.2 Stable Tail\n"
            "The receiver shall preserve timing."
        )
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-empty-shift.pdf"),
                pages=[PageText(page_number=1, text=old_text)],
            ),
            ExtractionResult(
                pdf_path=Path("new-empty-shift.pdf"),
                pages=[PageText(page_number=1, text=new_text)],
            ),
            DiffOptions(),
        )

        output_limit_changes = [
            change
            for change in result.changes
            if (change.old_section and change.old_section.title == "Output Limit")
            or (change.new_section and change.new_section.title == "Output Limit")
        ]
        self.assertEqual(2, len(output_limit_changes))
        shifted = next(
            change for change in output_limit_changes if change.change_type == "modified"
        )
        deleted = next(
            change for change in output_limit_changes if change.change_type == "deleted"
        )
        self.assertEqual("1 Receiver / 1.2 Output Limit", shifted.old_section.location)
        self.assertEqual("1 Receiver / 1.1 Output Limit", shifted.new_section.location)
        self.assertEqual("The limit applies.", shifted.old_section.body)
        self.assertEqual("The limit applies.", shifted.new_section.body)
        self.assertEqual("1 Receiver / 1.1 Output Limit", deleted.old_section.location)
        self.assertEqual("", deleted.old_section.body)

    def test_empty_repeated_title_does_not_hide_a_shifted_technical_clause(self) -> None:
        """A long repeated title cannot override empty-versus-populated body evidence."""

        title = "Receiver Calibration and Compliance Validation"
        old_text = (
            "1 Receiver\n"
            f"1.1 {title}\n"
            "The limit shall remain 20 mV.\n"
            f"1.2 {title}\n"
            "1.3 Stable Tail\n"
            "The receiver shall preserve timing."
        )
        new_text = (
            "1 Receiver\n"
            f"1.1 {title}\n"
            "The threshold shall remain 34.0 dB.\n"
            f"1.2 {title}\n"
            "The limit shall remain 20 mV.\n"
            "1.3 Stable Tail\n"
            "The receiver shall preserve timing."
        )
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-empty-repeated-title.pdf"),
                pages=[PageText(page_number=1, text=old_text)],
            ),
            ExtractionResult(
                pdf_path=Path("new-empty-repeated-title.pdf"),
                pages=[PageText(page_number=1, text=new_text)],
            ),
            DiffOptions(),
        )

        moved_limit = next(
            change
            for change in result.changes
            if change.old_section
            and change.old_section.body == "The limit shall remain 20 mV."
        )
        new_threshold = next(
            change
            for change in result.changes
            if change.new_section
            and change.new_section.body == "The threshold shall remain 34.0 dB."
        )
        empty_old = next(
            change
            for change in result.changes
            if change.change_type == "deleted"
            and change.old_section
            and change.old_section.body == ""
        )
        self.assertEqual(
            "1 Receiver / 1.2 Receiver Calibration and Compliance Validation",
            moved_limit.new_section.location,
        )
        self.assertEqual("similarity_fallback", moved_limit.match_basis)
        self.assertEqual("added", new_threshold.change_type)
        self.assertIn("34.0 dB", new_threshold.new_section.body)
        self.assertEqual(
            "1 Receiver / 1.2 Receiver Calibration and Compliance Validation",
            empty_old.old_section.location,
        )

    def test_inserted_section_does_not_force_same_number_mismatch(self) -> None:
        """A new clause may occupy an old number and shift the original content."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_inserted_section.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Scope requirement.\n"
                        "1.1 Transmit Equalization\n"
                        "Transmit coefficients shall be calibrated.\n"
                        "1.2 Output Jitter\n"
                        "Jitter shall remain within the stated limit."
                    ),
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_inserted_section.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Scope requirement.\n"
                        "1.1 Common-Mode Return Loss\n"
                        "Return loss shall meet the stated limit.\n"
                        "1.2 Transmit Equalization\n"
                        "Transmit coefficients shall be calibrated.\n"
                        "1.3 Output Jitter\n"
                        "Jitter shall remain within the stated limit."
                    ),
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        pairs = {
            (change.old_section.title if change.old_section else None,
             change.new_section.title if change.new_section else None): change.change_type
            for change in result.changes
        }
        self.assertEqual("added", pairs[(None, "Common-Mode Return Loss")])
        self.assertEqual(
            "modified",
            pairs[("Transmit Equalization", "Transmit Equalization")],
        )
        self.assertEqual("modified", pairs[("Output Jitter", "Output Jitter")])
        self.assertNotIn(("Transmit Equalization", "Common-Mode Return Loss"), pairs)
        self.assertNotIn(("Output Jitter", "Transmit Equalization"), pairs)

    def test_shifted_low_similarity_sections_use_proven_sibling_offset_and_prose(self) -> None:
        """A proven insertion shift plus independent prose keeps table-heavy clauses paired."""

        transmit_prose = (
            "Transmit equalization shall preserve the declared coefficient training sequence.\n"
            "The transmitter shall record every accepted coefficient request before applying it."
        )
        interference_prose = (
            "Interference tolerance test requirements are specified by the declared receiver profile."
        )
        old_transmit_rows = "\n".join(
            f"表格行: T1 | Parameter=Legacy alpha{i} beta{i} gamma{i} | Value={i}"
            for i in range(18)
        )
        new_transmit_rows = "\n".join(
            f"表格行: T1 | Parameter=Revised voltage{i} current{i} impedance{i} | Value={i + 100}"
            for i in range(18)
        )
        old_interference_rows = "\n".join(
            f"表格行: T2 | Parameter=Legacy noise profile alpha{i} | Value={i}"
            for i in range(14)
        )
        new_interference_rows = "\n".join(
            f"表格行: T2 | Parameter=Revised aggressor profile omega{i} | Value={i + 200}"
            for i in range(14)
        )
        old_text = "\n".join(
            (
                "1 Receiver Requirements",
                "1.1 Transmit Equalization",
                transmit_prose,
                old_transmit_rows,
                "1.2 Clock Recovery",
                "Clock recovery shall preserve the recovered phase across every supported rate.",
                "1.3 Interference Tolerance",
                interference_prose,
                old_interference_rows,
                "1.4 Receiver Output",
                "Receiver output shall preserve the declared voltage across every supported rate.",
            )
        )
        new_text = "\n".join(
            (
                "1 Receiver Requirements",
                "1.1 Common-Mode Return Loss",
                "Common-mode return loss shall meet the newly declared limit.",
                "1.2 Transmit Equalization",
                transmit_prose,
                new_transmit_rows,
                "1.3 Clock Recovery",
                "Clock recovery shall preserve the recovered phase across every supported rate.",
                "1.4 Interference Tolerance",
                interference_prose,
                new_interference_rows,
                "1.5 Receiver Output",
                "Receiver output shall preserve the declared voltage across every supported rate.",
            )
        )

        options = DiffOptions(min_section_match_similarity=0.90)
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-shifted-table-clauses.pdf"),
                pages=[PageText(page_number=1, text=old_text)],
            ),
            ExtractionResult(
                pdf_path=Path("new-shifted-table-clauses.pdf"),
                pages=[PageText(page_number=1, text=new_text)],
            ),
            options,
        )
        by_titles = {
            (
                change.old_section.title if change.old_section else None,
                change.new_section.title if change.new_section else None,
            ): change
            for change in result.changes
        }

        self.assertEqual("added", by_titles[(None, "Common-Mode Return Loss")].change_type)
        expected_bases = {
            "Transmit Equalization": "structural_shift_run_body",
            "Interference Tolerance": "structural_shift_bracketed_sentence",
        }
        for title, expected_basis in expected_bases.items():
            with self.subTest(title=title):
                change = by_titles[(title, title)]
                self.assertEqual("modified", change.change_type)
                self.assertEqual(expected_basis, change.match_basis)
                self.assertLess(change.similarity, options.min_section_match_similarity)
        self.assertEqual(
            "similarity_fallback",
            by_titles[("Clock Recovery", "Clock Recovery")].match_basis,
        )
        self.assertEqual(
            "similarity_fallback",
            by_titles[("Receiver Output", "Receiver Output")].match_basis,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, options)
            html = outputs["html"].read_text(encoding="utf-8")
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
        # 结构配对依据留在 JSON；纯编号标题被读者过滤后不再额外显示“编号偏移”噪声。
        self.assertNotIn("一致编号偏移", html)
        self.assertEqual(
            2,
            sum(
                change["match_basis"] in set(expected_bases.values())
                for change in payload["changes"]
            ),
        )

        strict_result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-strict-shifted-table-clauses.pdf"),
                pages=[PageText(page_number=1, text=old_text)],
            ),
            ExtractionResult(
                pdf_path=Path("new-strict-shifted-table-clauses.pdf"),
                pages=[PageText(page_number=1, text=new_text)],
            ),
            DiffOptions(min_section_match_similarity=0.99),
        )
        self.assertFalse(
            any(
                change.match_basis.startswith("structural_shift_")
                for change in strict_result.changes
            )
        )  # 用户把门槛提高到 .99 时，固定 .90 的结构证据不得越权强配。

    def test_shifted_run_accepts_inflected_predicate_as_independent_prose(self) -> None:
        """Real technical prose using ``provides`` must still prove a shifted clause."""

        shared_prose = "\n".join(
            (
                "CMIS-LT provides transmit equalization coefficient controls for the declared host interface.",
                "Coefficient initial conditions are retained for every supported training state.",
            )
        )
        old_rows = "\n".join(
            f"表格行: T1 | Parameter=Legacy coefficient alpha{index} | Value={index}"
            for index in range(18)
        )
        new_rows = "\n".join(
            f"表格行: T1 | Parameter=Revised coefficient omega{index} | Value={index + 100}"
            for index in range(18)
        )
        old_text = "\n".join(
            (
                "1 Receiver Requirements",
                "1.1 Transmit Equalization",
                shared_prose,
                old_rows,
                "1.2 Clock Recovery",
                "Clock recovery shall preserve the recovered phase across every supported rate.",
                "1.3 Receiver Output",
                "Receiver output shall preserve the declared voltage across every supported rate.",
            )
        )
        new_text = "\n".join(
            (
                "1 Receiver Requirements",
                "1.1 Newly Inserted Limit",
                "The inserted limit shall define a separate electrical requirement.",
                "1.2 Transmit Equalization",
                shared_prose,
                new_rows,
                "1.3 Clock Recovery",
                "Clock recovery shall preserve the recovered phase across every supported rate.",
                "1.4 Receiver Output",
                "Receiver output shall preserve the declared voltage across every supported rate.",
            )
        )

        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-inflected-shift.pdf"),
                pages=[PageText(page_number=1, text=old_text)],
            ),
            ExtractionResult(
                pdf_path=Path("new-inflected-shift.pdf"),
                pages=[PageText(page_number=1, text=new_text)],
            ),
            DiffOptions(min_section_match_similarity=0.90),
        )
        transmit_changes = [
            change
            for change in result.changes
            if (change.old_section and change.old_section.title == "Transmit Equalization")
            or (change.new_section and change.new_section.title == "Transmit Equalization")
        ]

        self.assertEqual(1, len(transmit_changes))
        self.assertEqual("modified", transmit_changes[0].change_type)
        self.assertEqual("structural_shift_run_body", transmit_changes[0].match_basis)

    def test_shift_prose_units_recognize_provides_as_a_complete_predicate(self) -> None:
        """Third-person technical requirements must not disappear from identity evidence."""

        section = Section(
            "S1",
            "1.2 Transmit Equalization",
            "Transmit Equalization",
            2,
            ("1.2 Transmit Equalization",),
            ("1", "1.2"),
            1,
            1,
            "CMIS-LT provides functionality through transmitter output coefficient control.",
        )

        units = compare_module._shift_prose_units(section)

        self.assertEqual(1, len(units))
        self.assertIn("CMIS-LT provides", units[0][1])

    def test_shifted_weak_clause_can_be_bracketed_by_an_empty_ordinary_container(self) -> None:
        """An empty adjacent container may bracket, but must not prove the shift run itself."""

        weak_sentence = (
            "Interference tolerance test requirements are specified by the declared receiver profile."
        )
        old_rows = "\n".join(
            f"表格行: T2 | Parameter=Legacy aggressor alpha{index} | Value={index}"
            for index in range(15)
        )
        new_rows = "\n".join(
            f"表格行: T2 | Parameter=Revised aggressor omega{index} | Value={index + 100}"
            for index in range(15)
        )
        old_text = "\n".join(
            (
                "1 Receiver Requirements",
                "1.1 Stable Before",
                "Stable before shall preserve the declared phase across every supported rate.",
                "1.2 Interference Tolerance",
                weak_sentence,
                old_rows,
                "1.3 Jitter Tolerance",
                "1.3.1 Test Procedure",
                "The test procedure shall apply the declared calibration sequence.",
                "1.4 Stable After",
                "Stable after shall preserve the declared voltage across every supported rate.",
                "1.5 Stable Tail",
                "Stable tail shall preserve the declared timing across every supported rate.",
            )
        )
        new_text = "\n".join(
            (
                "1 Receiver Requirements",
                "1.1 Newly Inserted Limit",
                "The inserted limit shall define a separate electrical requirement.",
                "1.2 Stable Before",
                "Stable before shall preserve the declared phase across every supported rate.",
                "1.3 Interference Tolerance",
                weak_sentence,
                new_rows,
                "1.4 Jitter Tolerance",
                "1.4.1 Test Procedure",
                "The test procedure shall apply the declared calibration sequence.",
                "1.5 Stable After",
                "Stable after shall preserve the declared voltage across every supported rate.",
                "1.6 Stable Tail",
                "Stable tail shall preserve the declared timing across every supported rate.",
            )
        )

        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-empty-bracket-shift.pdf"),
                pages=[PageText(page_number=1, text=old_text)],
            ),
            ExtractionResult(
                pdf_path=Path("new-empty-bracket-shift.pdf"),
                pages=[PageText(page_number=1, text=new_text)],
            ),
            DiffOptions(min_section_match_similarity=0.90),
        )
        interference_changes = [
            change
            for change in result.changes
            if (change.old_section and change.old_section.title == "Interference Tolerance")
            or (change.new_section and change.new_section.title == "Interference Tolerance")
        ]

        self.assertEqual(1, len(interference_changes))
        self.assertEqual("modified", interference_changes[0].change_type)
        self.assertEqual(
            "structural_shift_bracketed_sentence",
            interference_changes[0].match_basis,
        )

    def test_proven_number_shift_does_not_pair_disjoint_same_title_bodies(self) -> None:
        """Consistent sibling offsets cannot turn a deleted and added same-title clause into one edit."""

        shared_generic = (
            "Every implementation shall follow the applicable requirements in this document.\n"
            "The supplier shall preserve complete records for every declared configuration."
        )
        old_text = "\n".join(
            (
                "1 Receiver Requirements",
                "1.1 Operating Mode",
                shared_generic,
                "Legacy alpha beta gamma behavior defines the retired optical implementation. " * 20,
                "1.2 Clock Recovery",
                "Clock recovery shall preserve the recovered phase across every supported rate.",
                "1.3 Receiver Output",
                "Receiver output shall preserve the declared voltage across every supported rate.",
            )
        )
        new_text = "\n".join(
            (
                "1 Receiver Requirements",
                "1.1 Newly Inserted Limit",
                "The inserted limit shall define a separate electrical requirement.",
                "1.2 Operating Mode",
                shared_generic,
                "Revised voltage current impedance procedure defines a new copper implementation. " * 20,
                "1.3 Clock Recovery",
                "Clock recovery shall preserve the recovered phase across every supported rate.",
                "1.4 Receiver Output",
                "Receiver output shall preserve the declared voltage across every supported rate.",
            )
        )

        for threshold in (0.72, 0.99):
            with self.subTest(threshold=threshold):
                result = compare_extractions(
                    ExtractionResult(
                        pdf_path=Path("old-disjoint-shifted-title.pdf"),
                        pages=[PageText(page_number=1, text=old_text)],
                    ),
                    ExtractionResult(
                        pdf_path=Path("new-disjoint-shifted-title.pdf"),
                        pages=[PageText(page_number=1, text=new_text)],
                    ),
                    DiffOptions(min_section_match_similarity=threshold),
                )
                operating_changes = [
                    change
                    for change in result.changes
                    if (change.old_section and change.old_section.title == "Operating Mode")
                    or (change.new_section and change.new_section.title == "Operating Mode")
                ]

                self.assertEqual(
                    ["added", "deleted"],
                    sorted(change.change_type for change in operating_changes),
                )
                self.assertTrue(
                    all(
                        not change.match_basis.startswith("structural_shift_")
                        for change in result.changes
                    )
                )

    def test_shift_rescue_uses_title_uniqueness_from_the_original_parent_domain(self) -> None:
        """Matching one duplicate first must not make the remaining duplicate look unique."""

        shared_prose = "\n".join(
            (
                "Operating mode control shall preserve the declared receiver state during training.",
                "The operating mode shall record every accepted transition before activation.",
            )
        )
        old_rows = "\n".join(
            f"表格行: T1 | Parameter=Legacy mode alpha{index} | Value={index}"
            for index in range(16)
        )
        new_rows = "\n".join(
            f"表格行: T1 | Parameter=Revised mode omega{index} | Value={index + 100}"
            for index in range(16)
        )
        old_text = "\n".join(
            (
                "1 Receiver Requirements",
                "1.1 Operating Mode",
                "The first operating mode shall preserve a stable training sequence.",
                "1.2 Operating Mode",
                shared_prose,
                old_rows,
                "1.3 Clock Recovery",
                "Clock recovery shall preserve the recovered phase across every supported rate.",
                "1.4 Receiver Output",
                "Receiver output shall preserve the declared voltage across every supported rate.",
            )
        )
        new_text = "\n".join(
            (
                "1 Receiver Requirements",
                "1.1 Newly Inserted Limit",
                "The inserted limit shall define a separate electrical requirement.",
                "1.2 Operating Mode",
                "The first operating mode shall preserve a stable training sequence.",
                "1.3 Operating Mode",
                shared_prose,
                new_rows,
                "1.4 Clock Recovery",
                "Clock recovery shall preserve the recovered phase across every supported rate.",
                "1.5 Receiver Output",
                "Receiver output shall preserve the declared voltage across every supported rate.",
            )
        )

        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-duplicate-title-shift.pdf"),
                pages=[PageText(page_number=1, text=old_text)],
            ),
            ExtractionResult(
                pdf_path=Path("new-duplicate-title-shift.pdf"),
                pages=[PageText(page_number=1, text=new_text)],
            ),
            DiffOptions(min_section_match_similarity=0.90),
        )
        operating_changes = [
            change
            for change in result.changes
            if (change.old_section and change.old_section.title == "Operating Mode")
            or (change.new_section and change.new_section.title == "Operating Mode")
        ]

        self.assertEqual(3, len(operating_changes))
        self.assertEqual(
            ["added", "deleted", "modified"],
            sorted(change.change_type for change in operating_changes),
        )
        self.assertFalse(
            any(
                change.match_basis.startswith("structural_shift_")
                for change in operating_changes
            )
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

    def test_table_rows_stay_out_of_paragraph_diff_inside_insertions(self) -> None:
        """Inserted table rows should not be rendered as paragraph snippets."""

        old_extraction = ExtractionResult(  # 构造旧版表格片段，第一行是后续要比较的参数。
            pdf_path=Path("old_table_identity.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "表格行: T1 | Parameter=Reference resistance | Symbol=R0 | Value=50 | Units=Ω\n"
                        "表格行: T1 | Parameter=Termination resistance | Symbol=Rd | Value=46.25 | Units=Ω"
                    ),
                )
            ],
        )
        new_extraction = ExtractionResult(  # 构造新版表格片段，在变更行前插入一条新参数，验证配对不被打乱。
            pdf_path=Path("new_table_identity.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "表格行: T1 | Parameter=New impedance | Symbol=Zp | Value=40 | Units=Ω\n"
                        "表格行: T1 | Parameter=Reference resistance | Symbol=R0 | Value=46.25 | Units=Ω\n"
                        "表格行: T1 | Parameter=Termination resistance | Symbol=Rd | Value=46.25 | Units=Ω"
                    ),
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())  # 走完整 section+diff 流程，不直接调私有配对函数。
        replaced_pairs = [  # 收集替换对，确认同一参数行被配在一起。
            (pair.old, pair.new)
            for change in result.changes
            for pair in change.replaced_snippets
        ]
        added_snippets = [  # 收集新增片段，确认插入的新参数没有被错配成替换。
            snippet
            for change in result.changes
            for snippet in change.added_snippets
        ]

        self.assertFalse(replaced_pairs, replaced_pairs)  # 表格行替换不再作为正文替换对展示。
        self.assertFalse(added_snippets, added_snippets)  # 新插入表格行也不再作为正文新增片段展示。

    def test_partial_structured_row_does_not_suppress_larger_raw_table_block(self) -> None:
        """A single structured row cannot prove that every token in a larger block is duplicated."""

        old_raw_block = (  # 模拟 section body 中残留的旧版原始表格块。
            "COM Parameter Values Device package model Class B Transmission line parameter a2 "
            "2.93x10-4 ns1/2/mm Single-ended reference resistance R 0 50 Ω "
            "Single-ended termination resistance R d 46.25 Ω Receiver 3 dB bandwidth f b 0.55 GHz "
            "Transmitter equalizer coefficient c(0) 0.54 Minimum value 0 Maximum value 0.16 Step size 0.02"
        )
        new_raw_block = (  # 模拟新版同一表格块，文本顺序和符号碎片略有不同。
            "COM Parameter Values Device package model Class B Transmission line parameter a2 "
            "2.93×10-4 ns/mm Single-ended reference resistance R 46.25 Ω 0 "
            "Single-ended termination resistance R 46.25 Ω d Receiver 3 dB bandwidth f b 0.55 GHz "
            "Transmitter equalizer coefficient c(0) 0.54 Minimum value 0 Maximum value 0.16 Step size 0.02"
        )
        old_extraction = ExtractionResult(  # 旧版同时含有残留原始块和结构化表格行。
            pdf_path=Path("old_raw_table_block.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        f"{old_raw_block}\n"
                        "表格行: T1 | Parameter=Single-ended reference resistance | Symbol=R0 | Value=50 | Units=Ω"
                    ),
                )
            ],
        )
        new_extraction = ExtractionResult(  # 新版结构化行表达真正需要审阅的值变化。
            pdf_path=Path("new_raw_table_block.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        f"{new_raw_block}\n"
                        "表格行: T1 | Parameter=Single-ended reference resistance | Symbol=R0 | Value=46.25 | Units=Ω"
                    ),
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())  # 走真实比较路径，验证片段输出。
        snippets = "\n".join(  # 汇总所有可见 diff 片段。
            snippet
            for change in result.changes
            for snippet in (
                change.added_snippets
                + change.removed_snippets
                + [pair.old for pair in change.replaced_snippets]
                + [pair.new for pair in change.replaced_snippets]
            )
        )

        self.assertIn("COM Parameter Values Device package model", snippets)  # 其余未结构化记录仍需可审阅。
        self.assertNotIn("Parameter=Single-ended reference resistance", snippets)  # 结构化表格行不再作为正文差异出现。
        self.assertNotIn("Value=50", snippets)  # 旧表格值不刷进正文。
        self.assertNotIn("Value=46.25", snippets)  # 新表格值不刷进正文。

    def test_table_noise_suppression_keeps_nonduplicate_prose(self) -> None:
        """Table-like prose must not be dropped without structured-row overlap."""

        old_prose = (  # 这段长正文故意包含表格词和多个数字，但内容不是结构化表格行的重复。
            "The interoperability parameter values shall be reviewed across 10 operating windows, "
            "with minimum value 1, maximum value 9, condition 3, voltage 800 mV, frequency 26 GHz, "
            "and receiver observation 4 before transmitter observation 5. The procedure remains normative."
        )
        new_prose = old_prose.replace("10 operating windows", "12 operating windows")  # 只改一个真实正文数值。
        shared_table = "表格行: T1 | Parameter=Reference resistance | Symbol=R0 | Value=50 | Units=Ω"  # 同章节存在表格行，但 token 不覆盖正文。
        old_extraction = ExtractionResult(
            pdf_path=Path("old_table_like_prose.pdf"),
            pages=[PageText(page_number=1, text=f"1 Scope\n{old_prose}\n{shared_table}")],
        )  # 旧版正文和稳定表格行。
        new_extraction = ExtractionResult(
            pdf_path=Path("new_table_like_prose.pdf"),
            pages=[PageText(page_number=1, text=f"1 Scope\n{new_prose}\n{shared_table}")],
        )  # 新版正文有真实数值变化。

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())  # 走完整 diff，验证正文不会被降噪隐藏。
        snippets = "\n".join(
            pair.old + "\n" + pair.new
            for change in result.changes
            for pair in change.replaced_snippets
        )  # 收集替换片段，确认真实正文变化还在。

        self.assertIn("10 operating windows", snippets)  # 旧正文数值必须可见。
        self.assertIn("12 operating windows", snippets)  # 新正文数值必须可见。

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

    def test_visual_only_pdf_changes_become_review_evidence_without_fake_text_diffs(self) -> None:
        """Graphic-only changes must be visible without pretending they are text edits."""

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
            outputs = write_reports(result, temp_path / "reports", DiffOptions())
            report_html = outputs["html"].read_text(encoding="utf-8")
            report_json = json.loads(outputs["json"].read_text(encoding="utf-8"))
            app = object.__new__(ProtocolDiffDesktopApp)
            app.summary_var = mock.Mock()
            app.report_path_var = mock.Mock()
            app.status_var = mock.Mock()
            app.open_html_button = mock.Mock()
            app.open_dir_button = mock.Mock()
            app._handle_success(DesktopRunSuccess(result=result, outputs=outputs))
            desktop_summary = app.summary_var.set.call_args.args[0]

        self.assertEqual([], result.changes)
        self.assertEqual(1, len(result.visual_review_items))
        self.assertEqual(1, result.visual_review_items[0].old_page_number)
        self.assertEqual(1, result.visual_review_items[0].new_page_number)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)
        self.assertTrue(
            any("未解释的视觉变化" in reason for reason in result.assessment.reasons)
        )
        self.assertIn("视觉漏检核对", report_html)
        self.assertIn("图形变化未被文字、表格或公式差异覆盖", report_html)
        self.assertEqual(1, len(report_json["visual_review_items"]))
        self.assertIn("视觉待核对 1", desktop_summary)

    def test_visual_watchdog_aligns_exact_text_pages_after_an_inserted_page(self) -> None:
        """A page insertion must not make the visual watchdog compare unrelated pages."""

        old_pages = [
            ["1 Scope", "Common scope text."],
            ["2 Diagram", "The diagram below defines the supported path."],
        ]
        new_pages = [
            ["Cover note", "This page was inserted in the new revision."],
            *old_pages,
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(
                temp_path / "old_shifted_visual.pdf",
                old_pages,
                decorative_marks={2: "old"},
            )
            new_pdf = write_multipage_text_pdf(
                temp_path / "new_shifted_visual.pdf",
                new_pages,
                decorative_marks={3: "new"},
            )

            result = run_diff(old_pdf, new_pdf, DiffOptions())

        self.assertEqual(1, len(result.visual_review_items))
        visual = result.visual_review_items[0]
        self.assertEqual(2, visual.old_page_number)
        self.assertEqual(3, visual.new_page_number)
        self.assertEqual("unique-exact-text", visual.alignment_method)

    def test_visual_watchdog_does_not_force_ambiguous_duplicate_page_alignment(self) -> None:
        """Repeated page text plus insertion must not fabricate modified-page evidence."""

        repeated_page = ["1 Repeated diagram", "The repeated diagram template is unchanged."]
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(
                temp_path / "old_repeated_pages.pdf",
                [repeated_page, repeated_page],
                decorative_marks={1: "old", 2: "new"},
            )
            new_pdf = write_multipage_text_pdf(
                temp_path / "new_repeated_pages.pdf",
                [repeated_page, repeated_page, repeated_page],
                decorative_marks={2: "old", 3: "new"},
            )

            result = run_diff(old_pdf, new_pdf, DiffOptions())

        self.assertEqual([], result.visual_review_items)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)
        self.assertGreater(
            result.provenance.visual_watchdog_audit.ambiguous_page_count,
            0,
        )

    def test_visual_watchdog_excludes_reader_visible_change_from_unchanged_scope(self) -> None:
        """A normal redline page is already covered and must not degrade every report."""

        pages = [
            [
                f"{index} Requirement {index}",
                *[
                    f"The receiver requirement {index}.{line} shall preserve calibrated "
                    "voltage timing interoperability behavior for every declared mode."
                    for line in range(1, 7)
                ],
            ]
            for index in range(1, 8)
        ]
        changed_pages = [list(page) for page in pages]
        changed_pages[3][-1] = (
            "The receiver requirement 4.6 shall preserve revised voltage timing "
            "interoperability behavior for every declared mode."
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(temp_path / "old_unpaired.pdf", pages)
            new_pdf = write_multipage_text_pdf(
                temp_path / "new_unpaired.pdf",
                changed_pages,
            )

            result = run_diff(old_pdf, new_pdf, DiffOptions())

        audit = result.provenance.visual_watchdog_audit
        self.assertEqual(6, audit.eligible_page_pair_count)
        self.assertEqual(6, audit.checked_page_pair_count)
        self.assertEqual(0, audit.ambiguous_page_count)
        self.assertTrue(audit.complete)
        self.assertEqual("reliable", result.assessment.state)

    def test_visual_watchdog_counts_unpaired_reader_suppressed_page_as_incomplete(self) -> None:
        """Metadata hidden from reader cards cannot make eligible=0 look complete."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_metadata.pdf"),
            pages=[PageText(1, "1 Revision History\nCopyright 2025 Protocol Working Group")],
            total_pages=1,
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_metadata.pdf"),
            pages=[PageText(1, "1 Revision History\nCopyright 2026 Protocol Working Group")],
            total_pages=1,
        )
        semantic_result = compare_extractions(
            old_extraction,
            new_extraction,
            DiffOptions(),
        )

        items, warnings, audit = detect_visual_review_items(
            old_extraction,
            new_extraction,
            semantic_result=semantic_result,
        )

        self.assertEqual([], items)
        self.assertEqual(0, audit.eligible_page_pair_count)
        self.assertEqual(2, audit.ambiguous_page_count)
        self.assertFalse(audit.complete)
        self.assertTrue(
            any("无法用完全一致或读者等价文字安全配对" in warning for warning in warnings)
        )

    def test_multi_page_section_card_cannot_cover_suppressed_unmatched_page(self) -> None:
        """A page-1 value card cannot certify hidden page-2 locator pixels."""

        common = (
            "The receiver shall preserve calibrated voltage timing "
            "interoperability behavior for every declared mode."
        )
        old_pages = [
            [
                "1 Link Requirements",
                "The calibrated limit shall be 10 mV.",
                common,
                common,
                common,
                common,
            ],
            [
                "See Table 5 for limits.",
                "See Figure 4 for mask.",
                common,
                common,
                common,
                common,
            ],
            *[
                [f"{index} Requirement {index}", *[common for _ in range(6)]]
                for index in range(2, 7)
            ],
        ]
        new_pages = [list(page) for page in old_pages]
        new_pages[0][1] = "The calibrated limit shall be 12 mV."
        new_pages[1][0] = "See Figure 5 for mask."
        new_pages[1][1] = "See Table 6 for limits."
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(root / "old.pdf", old_pages)
            new_pdf = write_multipage_text_pdf(
                root / "new.pdf",
                new_pages,
                decorative_marks={2: "small-new"},
            )

            result = run_diff(old_pdf, new_pdf, DiffOptions())
            outputs = write_reports(result, root / "reports", DiffOptions())
            readers = {
                "html": _visible_html_text(
                    outputs["html"].read_text(encoding="utf-8")
                ),
                "markdown": outputs["markdown"].read_text(encoding="utf-8"),
                "text": outputs["text"].read_text(encoding="utf-8"),
            }

        for reader in readers.values():
            self.assertIn("10 mV", reader)
            self.assertIn("12 mV", reader)
            for locator in (
                "See Table 5",
                "See Table 6",
                "See Figure 4",
                "See Figure 5",
            ):
                self.assertNotIn(locator, reader)
        audit = result.provenance.visual_watchdog_audit
        self.assertEqual(5, audit.eligible_page_pair_count)
        self.assertEqual(5, audit.checked_page_pair_count)
        self.assertEqual(2, audit.ambiguous_page_count)
        self.assertFalse(audit.complete)
        self.assertEqual([], result.visual_review_items)
        self.assertEqual("degraded", result.assessment.state.value)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_visual_watchdog_pairs_unique_pages_even_when_page_order_is_swapped(self) -> None:
        """A crossed unique page identity must not fall outside an LCS silently."""

        pages = [
            [
                f"{index} Requirement {index}",
                *[
                    f"Unique requirement {index}.{line} preserves calibrated voltage timing "
                    "interoperability behavior for every declared operating mode."
                    for line in range(1, 7)
                ],
            ]
            for index in range(1, 8)
        ]
        swapped_pages = [pages[1], pages[0], *pages[2:]]
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(
                temp_path / "old_swapped_unique.pdf",
                pages,
                decorative_marks={2: "old"},
            )
            new_pdf = write_multipage_text_pdf(
                temp_path / "new_swapped_unique.pdf",
                swapped_pages,
                decorative_marks={1: "new"},
            )

            result = run_diff(old_pdf, new_pdf, DiffOptions())

        matching = [
            item
            for item in result.visual_review_items
            if item.old_page_number == 2 and item.new_page_number == 1
        ]
        self.assertEqual(1, len(matching))
        self.assertEqual("unique-exact-text", matching[0].alignment_method)
        self.assertTrue(result.provenance.visual_watchdog_audit.complete)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_visual_watchdog_flags_same_page_graphics_when_both_text_layers_are_empty(self) -> None:
        """Image-only pages still need a visual review signal when semantic parsing is empty."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(
                temp_path / "old_image_only.pdf",
                [[]],
                decorative_marks={1: "old"},
            )
            new_pdf = write_multipage_text_pdf(
                temp_path / "new_image_only.pdf",
                [[]],
                decorative_marks={1: "new"},
            )

            result = run_diff(old_pdf, new_pdf, DiffOptions())

        self.assertEqual(1, len(result.visual_review_items))
        self.assertEqual("same-page-empty-text", result.visual_review_items[0].alignment_method)
        self.assertEqual("indeterminate", result.assessment.state.value)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_visual_watchdog_flags_one_small_material_vector_symbol(self) -> None:
        """A connected local symbol must not disappear behind a whole-page ratio gate."""

        body = "The receiver shall preserve every calibrated operating requirement. " * 5
        pages = [[f"{index} Requirement {index}", body] for index in range(1, 8)]
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(temp_path / "old_small_symbol.pdf", pages)
            new_pdf = write_multipage_text_pdf(
                temp_path / "new_small_symbol.pdf",
                pages,
                decorative_marks={4: "small-new"},
            )

            result = run_diff(old_pdf, new_pdf, DiffOptions())

        self.assertEqual(1, len(result.visual_review_items))
        self.assertEqual(4, result.visual_review_items[0].old_page_number)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_visual_watchdog_ignores_only_coordinate_proven_running_footer_pixels(self) -> None:
        """Filtered revision footers must not return as eight duplicate visual alerts."""

        pages = [
            [
                f"{index} Requirement {index}",
                *[
                    f"The receiver requirement {index}.{line} shall preserve calibrated "
                    "voltage timing interoperability behavior for every declared operating mode."
                    for line in range(1, 7)
                ],
            ]
            for index in range(1, 9)
        ]
        old_footers = {
            index: [
                "Copyright AAAAAAAAAA Protocol Working Group",
                "www.example.test/revision/AAAAAAAAA",
            ]
            for index in range(1, 9)
        }
        new_footers = {
            index: [
                "Copyright BBBBBBBBBB Protocol Working Group",
                "www.example.test/revision/BBBBBBBBB",
            ]
            for index in range(1, 9)
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(
                temp_path / "old_dynamic_footer.pdf",
                pages,
                footer_lines=old_footers,
            )
            new_pdf = write_multipage_text_pdf(
                temp_path / "new_dynamic_footer.pdf",
                pages,
                footer_lines=new_footers,
            )

            result = run_diff(old_pdf, new_pdf, DiffOptions())
            outputs = write_reports(result, temp_path / "reports", DiffOptions())
            html_size = outputs["html"].stat().st_size

        self.assertEqual([], result.changes)
        self.assertEqual([], result.visual_review_items)
        self.assertTrue(result.assessment.allows_no_difference_conclusion)
        self.assertTrue(result.provenance.visual_watchdog_audit.complete)
        self.assertLess(html_size, 250_000)

    def test_visual_watchdog_keeps_small_graphic_below_proven_footer_words(self) -> None:
        """Footer proof may mask its words, never the entire bottom page band."""

        pages = [
            [
                f"{index} Requirement {index}",
                *[
                    f"The receiver requirement {index}.{line} shall preserve calibrated "
                    "voltage timing interoperability behavior for every declared mode."
                    for line in range(1, 7)
                ],
            ]
            for index in range(1, 9)
        ]
        footers = {
            index: [
                "Copyright Protocol Working Group",
                "www.example.test/revision/stable",
            ]
            for index in range(1, 9)
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(
                temp_path / "old_footer_graphic.pdf",
                pages,
                footer_lines=footers,
            )
            new_pdf = write_multipage_text_pdf(
                temp_path / "new_footer_graphic.pdf",
                pages,
                footer_lines=footers,
                decorative_marks={4: "footer-small"},
            )

            result = run_diff(old_pdf, new_pdf, DiffOptions())

        self.assertEqual(1, len(result.visual_review_items))
        self.assertEqual(4, result.visual_review_items[0].new_page_number)
        self.assertTrue(result.provenance.visual_watchdog_audit.complete)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_visual_watchdog_filters_dynamic_third_line_in_proven_footer_cluster(self) -> None:
        """A tight word mask still includes adjacent continuation lines of one footer."""

        pages = [
            [
                f"{index} Requirement {index}",
                *[
                    f"The receiver requirement {index}.{line} shall preserve calibrated "
                    "voltage timing interoperability behavior for every declared mode."
                    for line in range(1, 7)
                ],
            ]
            for index in range(1, 9)
        ]
        old_footers = {
            index: [
                "Copyright Protocol Working Group",
                "www.example.test/revision/stable",
                "Draft revision AAAAAAAAA",
            ]
            for index in range(1, 9)
        }
        new_footers = {
            index: [
                "Copyright Protocol Working Group",
                "www.example.test/revision/stable",
                "Draft revision BBBBBBBBB",
            ]
            for index in range(1, 9)
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(
                temp_path / "old_three_line_footer.pdf",
                pages,
                footer_lines=old_footers,
            )
            new_pdf = write_multipage_text_pdf(
                temp_path / "new_three_line_footer.pdf",
                pages,
                footer_lines=new_footers,
            )

            result = run_diff(old_pdf, new_pdf, DiffOptions())

        self.assertEqual([], result.changes)
        self.assertEqual([], result.visual_review_items)
        self.assertTrue(result.provenance.visual_watchdog_audit.complete)
        self.assertTrue(result.assessment.allows_no_difference_conclusion)

    def test_repeated_running_header_identifier_change_remains_auditable(self) -> None:
        """Per-document repetition cannot prove that a changed header is disposable."""

        pages = [
            [
                f"{index} Requirement {index}",
                *[
                    f"The calibrated receiver requirement {index}.{line} shall preserve "
                    "voltage timing interoperability behavior for every operating mode."
                    for line in range(1, 7)
                ],
            ]
            for index in range(1, 9)
        ]
        old_headers = {
            index: ["Implementation Agreement Protocol ALPHA"]
            for index in range(1, 9)
        }
        new_headers = {
            index: ["Implementation Agreement Protocol BETA"]
            for index in range(1, 9)
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(
                temp_path / "old_dynamic_header.pdf",
                pages,
                header_lines=old_headers,
            )
            new_pdf = write_multipage_text_pdf(
                temp_path / "new_dynamic_header.pdf",
                pages,
                header_lines=new_headers,
            )

            result = run_diff(old_pdf, new_pdf, DiffOptions())

        changed_text = "\n".join(
            [
                *(
                    snippet
                    for change in result.changes
                    for snippet in change.added_snippets + change.removed_snippets
                ),
                *(
                    snippet
                    for change in result.changes
                    for pair in change.replaced_snippets
                    for snippet in (pair.old, pair.new)
                ),
            ]
        )
        self.assertIn("ALPHA", changed_text)
        self.assertIn("BETA", changed_text)
        header_changes = [
            change
            for change in result.changes
            if change.report_location == "运行页眉（坐标证据）"
        ]
        self.assertEqual(1, len(header_changes))
        with tempfile.TemporaryDirectory() as report_dir:
            outputs = write_reports(result, report_dir, DiffOptions())
            for surface in ("html", "markdown", "text", "json", "csv"):
                rendered = outputs[surface].read_text(encoding="utf-8")
                with self.subTest(surface=surface):
                    self.assertIn("ALPHA", rendered)
                    self.assertIn("BETA", rendered)

    def test_running_header_identifier_case_change_preserves_each_observed_form(self) -> None:
        """Header grouping may ignore case, but comparison must preserve exact IDs."""

        pages = [
            [
                f"{index} Requirement {index}",
                *[
                    f"The calibrated receiver requirement {index}.{line} shall preserve "
                    "voltage timing interoperability behavior for every operating mode."
                    for line in range(1, 7)
                ],
            ]
            for index in range(1, 9)
        ]
        old_headers = {
            index: ["Consortium Protocol ID ALPHA" if index == 1 else "Consortium Protocol ID alpha"]
            for index in range(1, 9)
        }
        new_headers = {
            index: ["Consortium Protocol ID alpha" if index == 8 else "Consortium Protocol ID ALPHA"]
            for index in range(1, 9)
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(
                temp_path / "old_header_case.pdf",
                pages,
                header_lines=old_headers,
            )
            new_pdf = write_multipage_text_pdf(
                temp_path / "new_header_case.pdf",
                pages,
                header_lines=new_headers,
            )

            result = run_diff(old_pdf, new_pdf, DiffOptions())
            outputs = write_reports(result, temp_path / "report", DiffOptions())

            self.assertTrue(
                any(
                    change.report_location == "运行页眉（坐标证据）"
                    for change in result.changes
                )
            )
            for surface in ("html", "markdown", "text", "json", "csv"):
                rendered = outputs[surface].read_text(encoding="utf-8")
                with self.subTest(surface=surface):
                    self.assertIn("ALPHA", rendered)
                    self.assertIn("alpha", rendered)

    def test_recurring_header_count_tracks_pages_without_becoming_a_change(self) -> None:
        """One stable header is furniture even when the revisions have different page counts."""

        def extraction(name: str, page_count: int) -> ExtractionResult:
            return ExtractionResult(
                pdf_path=Path(name),
                pages=[
                    PageText(
                        page_number=index,
                        text=(
                            "1 Scope\n"
                            "The calibrated receiver shall preserve the declared voltage "
                            "and timing behavior for every supported operating mode."
                        ),
                        running_header_texts=("Consortium Protocol ID STABLE",),
                    )
                    for index in range(1, page_count + 1)
                ],
            )

        result = compare_extractions(
            extraction("old-stable-header.pdf", 8),
            extraction("new-stable-header.pdf", 7),
            DiffOptions(),
        )

        self.assertFalse(
            any(
                change.report_location == "运行页眉（坐标证据）"
                for change in result.changes
            )
        )

    def test_mixed_header_prefix_truncation_is_only_a_page_count_change(self) -> None:
        """Removing one trailing page does not alter identical overlapping headers."""

        def extraction(name: str, values: list[str]) -> ExtractionResult:
            return ExtractionResult(
                pdf_path=Path(name),
                pages=[
                    PageText(
                        page_number=index,
                        text=(
                            "1 Scope\n"
                            "The calibrated receiver shall preserve the declared voltage "
                            "and timing behavior for every supported operating mode."
                        ),
                        running_header_texts=(value,),
                    )
                    for index, value in enumerate(values, start=1)
                ],
            )

        result = compare_extractions(
            extraction("old-mixed-header.pdf", ["ID ALPHA", *(["ID alpha"] * 7)]),
            extraction("new-mixed-header.pdf", ["ID ALPHA", *(["ID alpha"] * 6)]),
            DiffOptions(),
        )

        self.assertFalse(
            any(
                change.report_location == "运行页眉（坐标证据）"
                for change in result.changes
            )
        )

    def test_same_page_added_header_is_not_trailing_page_furniture(self) -> None:
        """A header newly observed on a shared page remains visible and auditable."""

        def extraction(name: str, page_three_header: tuple[str, ...]) -> ExtractionResult:
            headers = {
                1: ("Consortium Protocol ALPHA",),
                2: ("Consortium Protocol alpha",),
                3: page_three_header,
            }
            return ExtractionResult(
                pdf_path=Path(name),
                pages=[
                    PageText(
                        page_number=index,
                        text=(
                            "1 Scope\n"
                            "The calibrated receiver shall preserve the declared voltage "
                            "and timing behavior for every supported operating mode."
                        ),
                        running_header_texts=headers[index],
                    )
                    for index in range(1, 4)
                ],
            )

        result = compare_extractions(
            extraction("old-shared-page-header.pdf", ()),
            extraction("new-shared-page-header.pdf", ("Consortium Protocol BETA",)),
            DiffOptions(),
        )

        header_changes = [
            change
            for change in result.changes
            if change.report_location == "运行页眉（坐标证据）"
        ]
        self.assertEqual(1, len(header_changes))
        with tempfile.TemporaryDirectory() as report_dir:
            outputs = write_reports(result, report_dir, DiffOptions())
            for surface in ("html", "markdown", "text", "json", "csv"):
                rendered = outputs[surface].read_text(encoding="utf-8")
                with self.subTest(surface=surface):
                    self.assertIn("BETA", rendered)

    def test_trailing_page_subset_of_stable_headers_is_page_count_furniture(self) -> None:
        """Each repeated exact header value remains stable on a subset tail page."""

        def extraction(name: str, headers: list[tuple[str, ...]]) -> ExtractionResult:
            return ExtractionResult(
                pdf_path=Path(name),
                pages=[
                    PageText(
                        page_number=index,
                        text=(
                            "1 Scope\n"
                            "The calibrated receiver shall preserve timing behavior."
                        ),
                        running_header_texts=values,
                    )
                    for index, values in enumerate(headers, start=1)
                ],
            )

        common = ("Protocol ALPHA", "Confidential")
        result = compare_extractions(
            extraction("old-two-headers.pdf", [common, common]),
            extraction("new-header-subset.pdf", [common, common, ("Protocol ALPHA",)]),
            DiffOptions(),
        )

        self.assertFalse(
            any(
                change.report_location == "运行页眉（坐标证据）"
                for change in result.changes
            )
        )

    def test_trailing_page_new_header_value_is_not_page_count_furniture(self) -> None:
        """A new technical header on an added tail page remains visible everywhere."""

        def extraction(name: str, headers: list[str]) -> ExtractionResult:
            return ExtractionResult(
                pdf_path=Path(name),
                pages=[
                    PageText(
                        page_number=index,
                        text=(
                            "1 Scope\n"
                            "The calibrated receiver shall preserve the declared voltage "
                            "and timing behavior for every supported operating mode."
                        ),
                        running_header_texts=(header,),
                    )
                    for index, header in enumerate(headers, start=1)
                ],
            )

        result = compare_extractions(
            extraction(
                "old-tail-header.pdf",
                ["Consortium Protocol ALPHA", "Consortium Protocol ALPHA"],
            ),
            extraction(
                "new-tail-header.pdf",
                [
                    "Consortium Protocol ALPHA",
                    "Consortium Protocol ALPHA",
                    "Consortium Protocol BETA",
                ],
            ),
            DiffOptions(),
        )

        header_changes = [
            change
            for change in result.changes
            if change.report_location == "运行页眉（坐标证据）"
        ]
        self.assertTrue(header_changes)
        with tempfile.TemporaryDirectory() as report_dir:
            outputs = write_reports(result, report_dir, DiffOptions())
            for surface in ("html", "markdown", "text", "json", "csv"):
                rendered = outputs[surface].read_text(encoding="utf-8")
                with self.subTest(surface=surface):
                    self.assertIn("BETA", rendered)

    def test_distinct_top_protocol_titles_are_not_authorized_as_running_headers(self) -> None:
        """Per-page technical titles cannot borrow the repeated-header deletion rule."""

        pages = [
            [
                f"{index} Requirement {index}",
                "The calibrated receiver requirement shall preserve timing behavior.",
            ]
            for index in range(1, 9)
        ]
        headers = {
            index: [f"OIF Implementation Agreement Protocol Mode {index}"]
            for index in range(1, 9)
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = write_multipage_text_pdf(
                Path(temp_dir) / "distinct_protocol_titles.pdf",
                pages,
                header_lines=headers,
            )

            extraction = extract_pdf_text(pdf_path)

        for index, page in enumerate(extraction.pages, start=1):
            self.assertIn(
                f"OIF Implementation Agreement Protocol Mode {index}",
                page.text,
            )

    def test_visual_watchdog_keeps_small_graphic_beside_proven_header_words(self) -> None:
        """Header proof may mask its words, never a full-width top band."""

        pages = [
            [
                f"{index} Requirement {index}",
                *[
                    f"The receiver requirement {index}.{line} shall preserve calibrated "
                    "voltage timing interoperability behavior for every declared mode."
                    for line in range(1, 7)
                ],
            ]
            for index in range(1, 9)
        ]
        headers = {
            index: ["OIF Implementation Agreement Protocol"]
            for index in range(1, 9)
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(
                temp_path / "old_header_graphic.pdf",
                pages,
                header_lines=headers,
            )
            new_pdf = write_multipage_text_pdf(
                temp_path / "new_header_graphic.pdf",
                pages,
                header_lines=headers,
                decorative_marks={4: "header-small"},
            )

            result = run_diff(old_pdf, new_pdf, DiffOptions())

        self.assertEqual(1, len(result.visual_review_items))
        self.assertEqual(4, result.visual_review_items[0].new_page_number)
        self.assertTrue(result.provenance.visual_watchdog_audit.complete)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_visual_watchdog_fails_closed_for_graphic_inside_locator_line(self) -> None:
        """A locator line bbox cannot hide a same-line material vector change."""

        old_pages: list[list[str]] = []
        new_pages: list[list[str]] = []
        for index in range(1, 9):
            stable_lines = [
                f"{index} Requirement {index}",
                *[
                    f"The receiver requirement {index}.{line} shall preserve calibrated "
                    "voltage timing interoperability behavior for every declared mode."
                    for line in range(1, 7)
                ],
            ]
            old_lines = list(stable_lines)
            new_lines = list(stable_lines)
            if index == 4:
                old_lines.append("Definitions are in Sections 31.3.4 to 31.3.18.")
                new_lines.append("Definitions are in Sections 31.3.4 to 31.3.19.")
            old_pages.append(old_lines)
            new_pages.append(new_lines)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(
                temp_path / "old_reference_and_vector.pdf",
                old_pages,
            )
            new_pdf = write_multipage_text_pdf(
                temp_path / "new_reference_and_vector.pdf",
                new_pages,
                decorative_marks={4: "small-inline"},
            )
            result = run_diff(old_pdf, new_pdf, DiffOptions())
            outputs = write_reports(result, temp_path / "reports", DiffOptions())
            reader_texts = {
                key: outputs[key].read_text(encoding="utf-8")
                for key in ("html", "markdown", "text")
            }

        audit = result.provenance.visual_watchdog_audit
        self.assertEqual([], result.visual_review_items)
        self.assertFalse(audit.complete)
        self.assertEqual(1, audit.failed_page_pair_count)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)
        self.assertTrue(any("逐字符编号坐标" in warning for warning in result.warnings))
        for reader in reader_texts.values():
            self.assertNotIn("31.3.18", reader)
            self.assertNotIn("31.3.19", reader)

    def test_visual_watchdog_fails_closed_for_repeated_reference_lines(self) -> None:
        """Repeated locator lines cannot be certified from whole-line boxes."""

        old_pages: list[list[str]] = []
        new_pages: list[list[str]] = []
        for index in range(1, 9):
            stable_lines = [
                f"{index} Requirement {index}",
                *[
                    f"The receiver requirement {index}.{line} shall preserve calibrated "
                    "voltage timing interoperability behavior for every declared mode."
                    for line in range(1, 7)
                ],
            ]
            if index == 4:
                old_lines = [
                    *stable_lines,
                    "Definitions are in Sections 31.3.4 to 31.3.18.",
                    "Definitions are in Sections 31.3.4 to 31.3.18.",
                ]
                new_lines = [
                    *stable_lines,
                    "Definitions are in Sections 31.3.4 to 31.3.19.",
                    "Definitions are in Sections 31.3.4 to 31.3.19.",
                ]
            else:
                old_lines = list(stable_lines)
                new_lines = list(stable_lines)
            old_pages.append(old_lines)
            new_pages.append(new_lines)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(temp_path / "old_repeated_refs.pdf", old_pages)
            new_pdf = write_multipage_text_pdf(temp_path / "new_repeated_refs.pdf", new_pages)

            result = run_diff(old_pdf, new_pdf, DiffOptions())

        self.assertEqual([], result.visual_review_items)
        self.assertFalse(result.provenance.visual_watchdog_audit.complete)
        self.assertEqual(1, result.provenance.visual_watchdog_audit.failed_page_pair_count)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_visual_watchdog_marks_reflowed_page_incomplete_without_false_card(self) -> None:
        """Direct pixel subtraction must not turn a moved page into a visual change."""

        old_pages: list[list[str]] = []
        new_pages: list[list[str]] = []
        for index in range(1, 9):
            stable_lines = [
                f"{index} Requirement {index}",
                *[
                    f"The receiver requirement {index}.{line} shall preserve calibrated "
                    "voltage timing interoperability behavior for every declared mode."
                    for line in range(1, 7)
                ],
            ]
            old_lines = list(stable_lines)
            new_lines = list(stable_lines)
            if index == 4:
                old_lines.append("Definitions are in Sections 31.3.4 to 31.3.18.")
                new_lines.append("Definitions are in Sections 31.3.4 to 31.3.19.")
            old_pages.append(old_lines)
            new_pages.append(new_lines)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(
                temp_path / "old_reflowed_reference.pdf",
                old_pages,
            )
            new_pdf = write_multipage_text_pdf(
                temp_path / "new_reflowed_reference.pdf",
                new_pages,
                body_origins={4: (92.0, 718.0)},
            )

            result = run_diff(old_pdf, new_pdf, DiffOptions())

        audit = result.provenance.visual_watchdog_audit
        self.assertEqual([], result.visual_review_items)
        self.assertFalse(audit.complete)
        self.assertEqual(1, audit.failed_page_pair_count)
        self.assertTrue(any("文字版式" in warning for warning in result.warnings))
        self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_visual_watchdog_fails_closed_without_locator_line_coordinates(self) -> None:
        """Reader-equivalent OCR text without line boxes cannot certify a pixel all-clear."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(
                temp_path / "old_unboxed_reference.pdf",
                [["1 Limits", "See Sections 31.3.4 to 31.3.18."]],
            )
            new_pdf = write_multipage_text_pdf(
                temp_path / "new_unboxed_reference.pdf",
                [["1 Limits", "See Sections 31.3.4 to 31.3.19."]],
            )
            old_extraction = ExtractionResult(
                pdf_path=old_pdf,
                pages=[
                    PageText(
                        page_number=1,
                        text="1 Limits\nSee Sections 31.3.4 to 31.3.18.",
                        ocr_used=True,
                    )
                ],
                source_sha256=hashlib.sha256(old_pdf.read_bytes()).hexdigest(),
            )
            new_extraction = ExtractionResult(
                pdf_path=new_pdf,
                pages=[
                    PageText(
                        page_number=1,
                        text="1 Limits\nSee Sections 31.3.4 to 31.3.19.",
                        ocr_used=True,
                    )
                ],
                source_sha256=hashlib.sha256(new_pdf.read_bytes()).hexdigest(),
            )
            with mock.patch(
                "protocol_pdf_diff.compare.extract_pdf_text",
                side_effect=[old_extraction, new_extraction],
            ):
                result = run_diff(old_pdf, new_pdf, DiffOptions())

        audit = result.provenance.visual_watchdog_audit
        self.assertFalse(audit.complete)
        self.assertEqual(1, audit.failed_page_pair_count)
        self.assertEqual([], result.visual_review_items)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_visual_watchdog_failure_blocks_reliable_all_clear(self) -> None:
        """A failed default watchdog cannot be represented as a completed clean check."""

        pages = [
            [
                f"{index} Requirement {index}",
                *[
                    f"The receiver requirement {index}.{line} shall preserve calibrated "
                    "voltage timing interoperability behavior for every declared operating mode."
                    for line in range(1, 7)
                ],
            ]
            for index in range(1, 8)
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(temp_path / "old_failed_watchdog.pdf", pages)
            new_pdf = write_multipage_text_pdf(temp_path / "new_failed_watchdog.pdf", pages)
            with mock.patch(
                "protocol_pdf_diff.compare.detect_visual_review_items",
                return_value=(
                    [],
                    ["watchdog failed"],
                    VisualWatchdogAudit(
                        enabled=True,
                        attempted=True,
                        backend_available=True,
                        eligible_page_pair_count=7,
                        checked_page_pair_count=0,
                        failed_page_pair_count=7,
                        ambiguous_page_count=0,
                        excluded_region_count=0,
                        complete=False,
                        source_hashes_match=True,
                    ),
                ),
            ):
                result = run_diff(old_pdf, new_pdf, DiffOptions())

        self.assertEqual("degraded", result.assessment.state.value)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)
        self.assertIn("watchdog failed", result.warnings)

    def test_visual_watchdog_can_be_explicitly_disabled_for_performance_diagnosis(self) -> None:
        """The opt-out suppresses only visual review work, not ordinary semantic comparison."""

        pages = [["1 Scope", "The receiver shall support 53.125 GBd operation."]]
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(
                temp_path / "old_visual_disabled.pdf",
                pages,
                decorative_marks={1: "old"},
            )
            new_pdf = write_multipage_text_pdf(
                temp_path / "new_visual_disabled.pdf",
                pages,
                decorative_marks={1: "new"},
            )

            result = run_diff(
                old_pdf,
                new_pdf,
                DiffOptions(visual_watchdog=False),
            )

        self.assertEqual([], result.changes)
        self.assertEqual([], result.visual_review_items)
        self.assertFalse(result.provenance.effective_thresholds.visual_watchdog)

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
        self.assertIn("ACME Protocol Specification", joined_bodies)
        self.assertIn("Confidential", joined_bodies)

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
        self.assertIn("Confidential", joined_bodies)

    def test_dynamic_footer_shaped_text_is_preserved_without_coordinates(self) -> None:
        """Near-bottom text still needs coordinate evidence before deletion."""

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

        self.assertIn("Confidential", joined_bodies)
        self.assertIn("Page 2 of 3", joined_bodies)

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

        self.assertEqual(2, len(sections))  # 页首元数据无布局证据时作为范围前序保留。
        self.assertIn("2.11.2 Overview of Calibration Steps at 16.0 GT/s", locations)
        self.assertNotIn(
            "2.11.2 Overview of Calibration Steps at 16.0 GT/s / 6 X 62.5 ps =",
            locations,
        )
        self.assertIn("1. Connect the end of the cables to the RX SMPs.", body)
        self.assertIn("128 bits of a 1010 clock pattern", body)
        self.assertIn("14. Turn all jitter and noise sources off.", body)
        self.assertIn("16. Capture 2.0 million unit-intervals of data.", body)
        self.assertIn("PCI Express Architecture PHY Test Specification", body)
        self.assertIn("Revision 4.0", body)
        self.assertIn("August 18, 2021", body)

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

    def test_procedure_word_integer_headings_are_preserved_when_title_like(self) -> None:
        """Short title-like headings should not be swallowed as numbered steps."""

        extraction = ExtractionResult(  # 这些标题首词也可能是动词，但在这里是章节名。
            pdf_path=Path("procedure_word_headings.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Scope overview text.\n"
                        "2 Power\n"
                        "Power chapter text.\n"
                        "3 Transmit Direction\n"
                        "Direction chapter text.\n"
                        "4 Shall Requirements\n"
                        "Requirement chapter text."
                    ),
                )
            ],
        )

        sections = section_document(extraction)  # 走章节器，确认短标题不会被步骤启发式吞掉。
        locations = [section.location for section in sections]  # 收集章节定位。

        self.assertIn("2 Power", locations)  # Power 可以是章节名。
        self.assertIn("3 Transmit Direction", locations)  # Transmit Direction 可以是章节名。
        self.assertIn("4 Shall Requirements", locations)  # Shall Requirements 可以是章节名。
        self.assertNotIn("2 Power", sections[0].body)  # 第二章不能进入第一章正文。

    def test_sentence_case_integer_heading_survives_without_procedure_context(self) -> None:
        """An imperative first word alone cannot prove that a numbered line is a procedure step."""

        extraction = ExtractionResult(  # Overview 不提供步骤上下文，后续 Configure 应按章节处理。
            pdf_path=Path("sentence-case-configure-heading.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Overview\n"
                        "This chapter introduces the receiver architecture.\n"
                        "2 Configure the receiver\n"
                        "The receiver configuration shall preserve every declared limit."
                    ),
                )
            ],
        )

        sections = section_document(extraction)  # 使用完整章节器观察最终报告结构。
        locations = [section.location for section in sections]  # 章节位置是用户可见的独立判据。

        self.assertEqual(  # 两个整数标题都应保持顶层身份，不能把第二章吞进第一章正文。
            ["1 Overview", "2 Configure the receiver"],
            locations,
        )
        self.assertNotIn("2 Configure the receiver", sections[0].body)  # 第一章正文不得包含第二章标题。

    def test_sequential_numbered_sentences_stay_in_body_without_domain_keywords(self) -> None:
        """A proven 1..N sentence chain is a prose list even under a generic parent."""

        extraction = ExtractionResult(
            pdf_path=Path("generic-numbered-prose-list.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Overview\n"
                        "The document describes a general data exchange.\n"
                        "1. The requester creates a message and records its identifier.\n"
                        "2. The processing layer validates the message before forwarding it.\n"
                        "3. The receiver returns a response to the original requester.\n"
                        "2 Architecture\n"
                        "The architecture chapter defines the participating components."
                    ),
                )
            ],
        )

        sections = section_document(extraction)
        locations = [section.location for section in sections]

        self.assertEqual(["1 Overview", "2 Architecture"], locations)
        self.assertIn("1. The requester creates a message", sections[0].body)
        self.assertIn("2. The processing layer validates the message", sections[0].body)
        self.assertIn("3. The receiver returns a response", sections[0].body)

    def test_body_only_first_item_can_seed_a_numbered_prose_chain(self) -> None:
        """A technical ratio in item 1 must not make item 2 a fake chapter."""

        extraction = ExtractionResult(
            pdf_path=Path("technical-numbered-prose-list.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "17 Encoding and adaptation\n"
                        "The revision introduces two different improvements:\n"
                        "1. 128b/130b 解决编码效率问题，把编码开销从 20% 降到约 1.5%。"
                    ),
                ),
                PageText(
                    page_number=2,
                    text=(
                        "2. Link equalization adapts the transmitter and receiver to the channel.\n"
                        "These improvements solve different engineering constraints.\n"
                        "18 Verification\n"
                        "The verification chapter defines the test evidence."
                    ),
                )
            ],
        )

        sections = section_document(extraction)

        self.assertEqual(
            ["17 Encoding and adaptation", "18 Verification"],
            [section.location for section in sections],
        )
        self.assertIn("1. 128b/130b 解决编码效率问题", sections[0].body)
        self.assertIn("2. Link equalization adapts", sections[0].body)

    def test_serialized_table_evidence_does_not_break_a_cross_page_prose_list(self) -> None:
        """Out-of-order table evidence cannot turn adjacent list items into chapters."""

        extraction = ExtractionResult(
            pdf_path=Path("table-evidence-after-numbered-item.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "17 Encoding and adaptation\n"
                        "The revision introduces two different improvements:\n"
                        "1. Encoding efficiency reduces the transport overhead.\n"
                        "表格行: T1 | Dimension=Encoding | Value=128b/130b"
                    ),
                ),
                PageText(
                    page_number=2,
                    text=(
                        "2. Link equalization adapts the transmitter and receiver to the channel.\n"
                        "These improvements solve different engineering constraints.\n"
                        "18 Verification\n"
                        "The verification chapter defines the test evidence."
                    ),
                ),
            ],
        )

        sections = section_document(extraction)

        self.assertEqual(
            ["17 Encoding and adaptation", "18 Verification"],
            [section.location for section in sections],
        )
        self.assertIn("1. Encoding efficiency", sections[0].body)
        self.assertIn("2. Link equalization adapts", sections[0].body)

    def test_ordinary_body_paragraph_ends_numbered_prose_chain(self) -> None:
        """A distant sentence-like integer heading must not inherit an earlier list item."""

        extraction = ExtractionResult(
            pdf_path=Path("numbered-list-then-real-chapter.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Overview\n"
                        "1. The requester creates a message and records its identifier.\n"
                        "This paragraph explains the completed request independently.\n"
                        "2. Configure the receiver for reliable operation.\n"
                        "The configuration chapter preserves every declared limit."
                    ),
                )
            ],
        )

        sections = section_document(extraction)

        self.assertEqual(
            ["1 Overview", "2. Configure the receiver for reliable operation."],
            [section.location for section in sections],
        )

    def test_wrapped_numbered_prose_item_keeps_immediately_continuing_chain(self) -> None:
        """One soft-wrapped list sentence may continue before the next numbered item."""

        extraction = ExtractionResult(
            pdf_path=Path("wrapped-numbered-prose-list.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Overview\n"
                        "1. The requester creates a message and records its identifier.\n"
                        "2. The processing layer validates the message: forwarding continues\n"
                        "after every declared condition has been checked.\n"
                        "3. The receiver returns a response to the original requester.\n"
                        "2 Architecture\n"
                        "The architecture chapter defines the participating components."
                    ),
                )
            ],
        )

        sections = section_document(extraction)

        self.assertEqual(
            ["1 Overview", "2 Architecture"],
            [section.location for section in sections],
        )
        self.assertIn("after every declared condition has been checked.", sections[0].body)
        self.assertIn("3. The receiver returns a response", sections[0].body)

    def test_mid_list_numbered_sentences_stay_under_active_chapter(self) -> None:
        """A selected page may begin midway through a prose list rather than at item 1."""

        extraction = ExtractionResult(
            pdf_path=Path("mid-list-numbered-prose.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "6 Data flow\n"
                        "The preceding page contains the first three numbered steps.\n"
                        "4. The link layer records the message identifier before forwarding\n"
                        "after every declared flow-control condition has been checked.\n"
                        "5. The physical layer encodes the payload and sends it to the peer.\n"
                        "6. The peer validates the payload and returns an acknowledgement.\n"
                        "7. The requester associates the response with the original message.\n"
                        "7 Transaction rules\n"
                        "The transaction chapter defines request and completion handling."
                    ),
                )
            ],
        )

        sections = section_document(extraction)

        self.assertEqual(
            ["6 Data flow", "7 Transaction rules"],
            [section.location for section in sections],
        )
        for number in range(4, 8):
            self.assertIn(f"{number}.", sections[0].body)

    def test_real_next_chapter_ends_a_mid_list_chain(self) -> None:
        """A proven list cannot swallow a real next chapter with its own body."""

        extraction = ExtractionResult(
            pdf_path=Path("mid-list-before-real-next-chapter.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "6 Data flow\n"
                        "4. The link layer records the message identifier before forwarding.\n"
                        "5. The physical layer encodes the payload and sends it to the peer.\n"
                        "6. The peer validates the payload and returns an acknowledgement.\n"
                        "7. Link equalization requirements for PCI Express devices.\n"
                        "The equalization chapter defines independent device requirements."
                    ),
                )
            ],
        )

        sections = section_document(extraction)

        self.assertEqual(
            [
                "6 Data flow",
                "7. Link equalization requirements for PCI Express devices.",
            ],
            [section.location for section in sections],
        )
        self.assertIn("6. The peer validates", sections[0].body)

    def test_mid_list_chain_can_be_proven_across_one_page_boundary(self) -> None:
        """A page break between consecutive items does not create fake chapters."""

        extraction = ExtractionResult(
            pdf_path=Path("cross-page-mid-list.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "6 Data flow\n"
                        "The preceding page contains the first three numbered steps.\n"
                        "4. The link layer records the message identifier before forwarding."
                    ),
                ),
                PageText(
                    page_number=2,
                    text=(
                        "5. The physical layer encodes the payload and sends it to the peer.\n"
                        "6. The peer validates the payload and returns an acknowledgement.\n"
                        "7 Transaction rules\n"
                        "The transaction chapter defines independent request handling."
                    ),
                ),
            ],
        )

        sections = section_document(extraction)

        self.assertEqual(
            ["6 Data flow", "7 Transaction rules"],
            [section.location for section in sections],
        )
        for number in range(4, 7):
            self.assertIn(f"{number}.", sections[0].body)

    def test_numbered_prose_chain_final_item_stays_in_body_at_eof(self) -> None:
        """The last item inherits a proven chain when the document ends there."""

        extraction = ExtractionResult(
            pdf_path=Path("numbered-prose-chain-at-eof.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Overview\n"
                        "1. The requester creates a message and records its identifier.\n"
                        "2. The processing layer validates the message before forwarding it.\n"
                        "3. The receiver returns a response to the original requester."
                    ),
                )
            ],
        )

        sections = section_document(extraction)

        self.assertEqual(["1 Overview"], [section.location for section in sections])
        self.assertIn("3. The receiver returns", sections[0].body)

    def test_ambiguous_terminal_numbered_sentence_before_prose_stays_visible(self) -> None:
        """Plain following prose may be chapter body, so the numbered line stays visible."""

        extraction = ExtractionResult(
            pdf_path=Path("numbered-prose-chain-before-summary.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Overview\n"
                        "1. The requester creates a message and records its identifier.\n"
                        "2. The processing layer validates the message before forwarding it.\n"
                        "3. The receiver returns a response to the original requester.\n"
                        "This summary closes the complete request sequence."
                    ),
                )
            ],
        )

        sections = section_document(extraction)

        self.assertEqual(
            [
                "1 Overview",
                "3. The receiver returns a response to the original requester.",
            ],
            [section.location for section in sections],
        )
        self.assertIn("This summary closes", sections[1].body)

    def test_two_item_numbered_prose_chain_closes_at_eof(self) -> None:
        """A two-item chain under chapter 1 must not turn item 2 into a chapter."""

        extraction = ExtractionResult(
            pdf_path=Path("two-item-chain-at-eof.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Overview\n"
                        "1. The requester creates a message and records its identifier.\n"
                        "2. The receiver returns a response to the requester."
                    ),
                )
            ],
        )

        sections = section_document(extraction)

        self.assertEqual(["1 Overview"], [section.location for section in sections])
        self.assertIn("2. The receiver returns", sections[0].body)

    def test_two_item_chain_before_plain_prose_fails_visible_as_a_chapter(self) -> None:
        """Without typography, following prose may be chapter body and must stay visible."""

        extraction = ExtractionResult(
            pdf_path=Path("two-item-chain-before-summary.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Overview\n"
                        "1. The requester creates a message and records its identifier.\n"
                        "2. The receiver returns a response to the requester.\n"
                        "This summary closes the request flow."
                    ),
                )
            ],
        )

        sections = section_document(extraction)

        self.assertEqual(
            ["1 Overview", "2. The receiver returns a response to the requester."],
            [section.location for section in sections],
        )
        self.assertIn("This summary closes", sections[1].body)

    def test_explicit_two_item_intro_keeps_both_items_in_parent(self) -> None:
        """An exact cardinality lead-in resolves the otherwise ambiguous final item."""

        for tail in (
            "These two observations complete the request flow.",
            "表格行: T1 | Observation=Second | Evidence=Trace",
        ):
            with self.subTest(tail=tail):
                extraction = ExtractionResult(
                    pdf_path=Path("explicit-two-item-list.pdf"),
                    pages=[
                        PageText(
                            page_number=1,
                            text=(
                                "1 Overview\n"
                                "This section introduces exactly two implementation observations:\n"
                                "1. The requester records each message before forwarding it.\n"
                                "2. The receiver returns a response to the requester.\n"
                                f"{tail}"
                            ),
                        )
                    ],
                )

                sections = section_document(extraction)

                self.assertEqual(["1 Overview"], [section.location for section in sections])
                self.assertIn("2. The receiver returns", sections[0].body)

    def test_approved_counted_head_noun_allows_postmodifier(self) -> None:
        """Postmodifiers remain valid only after an adjacent approved list head noun."""

        for intro in (
            "The following two steps for calibration:",
            "This section records exactly two items of evidence:",
            "The following two observations in this test:",
        ):
            with self.subTest(intro=intro):
                extraction = ExtractionResult(
                    pdf_path=Path("counted-list-postmodifier.pdf"),
                    pages=[
                        PageText(
                            page_number=1,
                            text=(
                                "1 Overview\n"
                                f"{intro}\n"
                                "1. The requester records each message before forwarding it.\n"
                                "2. The receiver returns a response to the requester.\n"
                                "These two entries complete the evidence."
                            ),
                        )
                    ],
                )

                self.assertEqual(
                    ["1 Overview"],
                    [section.location for section in section_document(extraction)],
                )

    def test_wrapped_explicit_two_item_intro_keeps_both_items_in_parent(self) -> None:
        """A PDF soft line break does not destroy explicit list cardinality evidence."""

        for intro in (
            "This section introduces exactly two implementation\nobservations:",
            "This section introduces exactly\ntwo\nimplementation\nobservations:",
        ):
            with self.subTest(intro=intro):
                extraction = ExtractionResult(
                    pdf_path=Path("wrapped-explicit-two-item-list.pdf"),
                    pages=[
                        PageText(
                            page_number=1,
                            text=(
                                "1 Overview\n"
                                f"{intro}\n"
                                "1. The requester records each message before forwarding it.\n"
                                "2. The receiver returns a response to the requester.\n"
                                "These two observations complete the request flow."
                            ),
                        )
                    ],
                )

                sections = section_document(extraction)

                self.assertEqual(["1 Overview"], [section.location for section in sections])
                self.assertIn("2. The receiver returns", sections[0].body)

    def test_structural_container_cardinality_never_authorizes_prose_list_demotion(
        self,
    ) -> None:
        """Named chapters/sections/appendices stay structural despite a count lead-in."""

        for intro in (
            "This document contains exactly two chapters:",
            "The following two sections:",
            "The following two appendices:",
            "The following two chapters define the requirements:",
            "The following two chapters contain the steps:",
            "以下两个章节：",
        ):
            with self.subTest(intro=intro):
                extraction = ExtractionResult(
                    pdf_path=Path("explicit-structural-containers.pdf"),
                    pages=[
                        PageText(
                            page_number=1,
                            text=(
                                "1 Overview\n"
                                f"{intro}\n"
                                "1. General requirements.\n"
                                "2. Security requirements.\n"
                                "This summary applies to both structural containers."
                            ),
                        )
                    ],
                )

                sections = section_document(extraction)

                self.assertEqual(
                    [
                        "1 Overview",
                        "1. General requirements.",
                        "2. Security requirements.",
                    ],
                    [section.location for section in sections],
                )

    def test_unknown_counted_noun_does_not_authorize_prose_list_demotion(self) -> None:
        """A counted technical noun alone cannot hide body-backed real chapters."""

        extraction = ExtractionResult(
            pdf_path=Path("unknown-counted-noun.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Overview\n"
                        "This process contains exactly two cycles of test operations:\n"
                        "1. General requirements.\n"
                        "2. Security requirements.\n"
                        "The security chapter defines independent behavior."
                    ),
                )
            ],
        )

        self.assertEqual(
            [
                "1 Overview",
                "1. General requirements.",
                "2. Security requirements.",
            ],
            [section.location for section in section_document(extraction)],
        )

    def test_real_next_chapter_after_same_numbered_item_is_not_demoted(self) -> None:
        """A chapter-1 item 1 cannot make a body-backed chapter 2 disappear."""

        extraction = ExtractionResult(
            pdf_path=Path("same-numbered-item-before-real-chapter.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Overview\n"
                        "1. The requester records each message before forwarding it.\n"
                        "2. Security architecture and operational requirements for independent devices.\n"
                        "The security chapter defines a separate architecture.\n"
                        "3 Verification\n"
                        "The verification chapter defines independent evidence."
                    ),
                )
            ],
        )

        self.assertEqual(
            [
                "1 Overview",
                "2. Security architecture and operational requirements for independent devices.",
                "3 Verification",
            ],
            [section.location for section in section_document(extraction)],
        )

    def test_bare_part_heading_resets_same_numbered_chapter_identity(self) -> None:
        """A bare Part boundary makes its chapter 1 a new structural section."""

        extraction = ExtractionResult(
            pdf_path=Path("bare-part-numbering-restart.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Existing requirements\n"
                        "The first part defines the existing requirements.\n"
                        "Part II\n"
                        "1. Deployment architecture and operational requirements.\n"
                        "The deployment chapter belongs to the second part."
                    ),
                )
            ],
        )

        self.assertEqual(
            [
                "1 Existing requirements",
                "Part II",
                "Part II / 1. Deployment architecture and operational requirements.",
            ],
            [section.location for section in section_document(extraction)],
        )

    def test_bare_part_and_annex_markers_accept_terminal_punctuation(self) -> None:
        """A delimiter without a title remains a structural Part/Annex boundary."""

        for line, expected in (
            ("Part II.", "Part II."),
            ("Part II:", "Part II:"),
            ("Annex B:", "Annex B:"),
            ("Appendix C.", "Appendix C."),
        ):
            with self.subTest(line=line):
                heading = detect_heading(line)
                self.assertIsNotNone(heading)
                assert heading is not None
                self.assertEqual(expected, heading.raw)

    def test_appendix_and_annex_prose_is_not_a_bare_marker(self) -> None:
        """Ordinary prose after Appendix/Annex cannot become a level-1 heading."""

        for line in ("Appendix body.", "Appendix requirements.", "Annex material."):
            with self.subTest(line=line):
                self.assertIsNone(detect_heading(line))

    def test_named_appendix_reference_sentence_is_not_a_heading(self) -> None:
        """A named appendix used as a sentence subject remains ordinary prose."""

        for line in (
            "Appendix A describes the calibration method.",
            "Appendix A describes the calibration",
            "Appendix A presents the calibration method",
            "Appendix B details the receiver limits",
            "Appendix C sets out the calibration procedure",
            "Appendix A describes and defines the calibration method.",
            "Annex B contains and explains the receiver limits.",
            "Part II states and lists the normative requirements.",
            "Appendix C provides and documents the calibration evidence.",
            "Appendix A describes, defines, and documents the calibration method.",
            "Annex B contains and clearly explains the receiver limits.",
            "Annex B contains and very clearly explains the receiver limits.",
            "Appendix A shall define the calibration method.",
            "APPENDIX A DESCRIBES THE CALIBRATION METHOD.",
            "Appendix A explains.",
            "APPENDIX A SUMMARIZES.",
            "Appendix A explains and summarizes.",
            "APPENDIX A EXPLAINS AND SUMMARIZES.",
            "Appendix A outlines the calibration method.",
            "Appendix A clearly explains the receiver limits.",
            "Appendix A explains: the receiver limit is 20 mV.",
            "Part II describes the transmitter—receiver interface.",
            "APPENDIX A STATES TWO REQUIREMENTS.",
            "ANNEX B LISTS ALL REQUIREMENTS.",
            "APPENDIX C COVERS THREE METHODS.",
            "APPENDIX A STATES RECEIVER REQUIREMENTS.",
            "APPENDIX A STATES REQUIREMENTS CLEARLY.",
            "APPENDIX A REPORTS ON TWO FAILURES.",
            "APPENDIX A REPORTS ON SEVERAL FAILURES.",
            "APPENDIX A REPORTS ABOUT MORE THAN TWO FAILURES.",
            "Annex B contains normative requirements.",
            "Annex B remains normative for receiver testing.",
            "Annex B remains normative for receiver",
            "Part II defines the receiver architecture.",
            "Part II does not apply to legacy devices.",
            "Part II applies to legacy devices",
            "Appendix C may be used for calibration.",
            "Annex D can provide supplemental limits.",
            "ANNEX B REMAINS NORMATIVE.",
            "ANNEX C STATES THE RECEIVER REQUIREMENTS.",
            "PART II APPLIES TO RECEIVERS.",
            "APPENDIX A OF THIS DOCUMENT.",
            "附录 A 描述了校准方法。",
            "附录 A 描述校准方法",
            "附录 A 描述了校准",
            "附录 B 用于说明校准流程。",
            "附录 B 应当规定接收机限值。",
            "附录 C 仍然适用于旧设备。",
            "附录 D 中的要求适用于旧设备。",
            "附录 B 中的要求适用",
            "附录 B 中规定接收机限值",
            "附录 A 描述和定义校准方法。",
            "附录 A 描述和定义校准方法",
            "附录 A 描述、定义并记录校准方法",
            "附录 B 说明及规定接收机限值。",
            "附录 B 说明及明确规定接收机限值。",
            "附录 B 说明及全面规定接收机限值。",
            "附录 C 描述并准确地定义校准方法",
            "附录 D 说明并清晰地规定接收机限值",
            "附录 E 描述、 定义并 记录校准方法",
            "附录 C 说明并充分定义校准方法。",
            "附录 D 说明并进一步明确规定接收机限值。",
            "附录 E 说明、要求并规定接收机限值。",
            "附录 A 说明：接收机限值为20mV。",
            "附录 A 阐述校准方法。",
            "附录 A 明确规定接收机限值。",
            "附录 A 说 明并规定接收机限值。",
            "附录 C 介绍与展示校准证据。",
        ):
            with self.subTest(line=line):
                self.assertIsNone(detect_heading(line))

    def test_chinese_named_container_noun_titles_remain_structural(self) -> None:
        """Common Chinese characters inside noun phrases are not sentence proof."""

        for line in (
            "附录 A 接收机的校准方法",
            "附录 B 过渡响应",
            "附录 C 附着损耗",
            "附录 D 已完成的测试",
            "附录 E 经过校准的接收机要求",
        ):
            with self.subTest(line=line):
                heading = detect_heading(line)
                self.assertIsNotNone(heading)
                self.assertRegex(heading.number, r"^附录\s+[A-E]$")

    def test_chinese_named_container_titles_keep_their_technical_bodies(self) -> None:
        """A real Annex title must own its values instead of leaking into the prior chapter."""

        extraction = ExtractionResult(
            pdf_path=Path("chinese-annex-titles.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 General requirements\n"
                        "The receiver requirements apply.\n"
                        "附录 A 接收机的校准方法\n"
                        "校准容差为 0.025 UI。\n"
                        "附录 B 过渡响应\n"
                        "响应限值为 34.0 dB。"
                    ),
                )
            ],
        )

        sections = section_document(extraction)

        self.assertEqual(
            ["1 General requirements", "附录 A 接收机的校准方法", "附录 B 过渡响应"],
            [section.location for section in sections],
        )
        self.assertIn("0.025 UI", sections[1].body)
        self.assertIn("34.0 dB", sections[2].body)

    def test_chinese_predicate_shaped_enumeration_title_keeps_technical_body(
        self,
    ) -> None:
        """A noun enumeration after a verb-shaped word remains an Annex title."""

        extraction = ExtractionResult(
            pdf_path=Path("chinese-enumeration-annex-title.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 范围\n"
                        "附录 A 说明、要求和示例\n"
                        "校准容差为 0.025 UI。\n"
                        "2 验证\n"
                        "验证限值为 34.0 dB。"
                    ),
                )
            ],
        )

        sections = section_document(extraction)

        self.assertIn("附录 A 说明、要求和示例", [section.location for section in sections])
        self.assertTrue(
            any("0.025 UI" in section.body for section in sections if "附录 A" in section.location)
        )

    def test_terminal_period_preserves_ambiguous_container_noun_title(self) -> None:
        """Adding normal title punctuation cannot demote a noun title to prose."""

        extraction = ExtractionResult(
            pdf_path=Path("period-terminated-container-title.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Appendix A States the Receiver Supports.\n"
                        "The transition tolerance is 0.025 UI.\n"
                        "1 Child requirements\n"
                        "The child threshold is 34.0 dB."
                    ),
                )
            ],
        )

        sections = section_document(extraction)

        self.assertEqual(
            [
                "1 Scope",
                "Appendix A States the Receiver Supports.",
                "Appendix A States the Receiver Supports. / 1 Child requirements",
            ],
            [section.location for section in sections],
        )
        self.assertIn("0.025 UI", sections[1].body)

    def test_participial_and_delimited_container_titles_keep_their_bodies(
        self,
    ) -> None:
        """Ambiguous participles and internal title delimiters fail visible."""

        for title in (
            "APPENDIX A STATES SUPPORTED BY THE RECEIVER.",
            "APPENDIX A STATES THE PROTOCOL RECEIVER SUPPORTS.",
            "附录 A 说明：要求和示例",
            "附录 A 说明：要求和示例。",
            "附录 B 描述—定义和缩写",
            "附录 B 描述—定义和缩写！",
            "附录 A 说明 、 要求 和 示例",
            "附录 B 描述 、 定义 、 缩写",
            "附录 A 说明及适用要求范围。",
            "附录 C 说明和接收机定义要求。",
            "APPENDIX A SYSTEMS AND INTERFACES.",
            "ANNEX B REQUIREMENTS AND EXAMPLES.",
            "Appendix A systems and interfaces.",
            "Annex B processes and procedures.",
            "Appendix C analysis methods.",
            "APPENDIX A REPORTS REQUIRED FOR CALIBRATION.",
            "APPENDIX A REPORTS THE RECEIVER GENERATES.",
            "APPENDIX A NOTES ON CALIBRATION.",
            "ANNEX B REPORTS ABOUT RECEIVER TESTING.",
            "APPENDIX C RECORDS FROM CALIBRATION.",
            "APPENDIX A NOTES ON 2.5-GT/S CALIBRATION.",
            "APPENDIX B REPORTS ON 25% MARGIN ANALYSIS.",
            "APPENDIX C NOTES ON 1,000-LANE CALIBRATION.",
            "APPENDIX D REPORTS ON 25% MARGIN ANALYSIS.",
            "APPENDIX E NOTES ON 2.5 GT/S CALIBRATION.",
            "APPENDIX F REPORTS ON 34.0 dB LIMITS.",
            "APPENDIX G REPORTS ON 56.25 GBd LIMITS.",
            "APPENDIX H NOTES ON 120 mVrms CALIBRATION.",
            "APPENDIX I REPORTS ON 3.5 mVpp MARGIN.",
            "APPENDIX J NOTES ON 20 degrees PHASE.",
            "APPENDIX K NOTES ON 2.5 Tbaud CALIBRATION.",
            "APPENDIX L NOTES ON 20 DEGREES.",
            "APPENDIX M REPORTS ON 120 mVrms.",
            "APPENDIX N NOTES ON 50 OHMS.",
            "APPENDIX O REPORTS ON 25 GBPS.",
            "APPENDIX P NOTES ON TWO mVrms.",
            "APPENDIX AA REPORTS ON AT LEAST PAIRS OF ERRORS.",
            "APPENDIX AB REPORTS ON ONE MILLION THOUSAND FAILURES.",
            "APPENDIX AC REPORTS ON ONE HUNDRED HUNDRED FAILURES.",
            "APPENDIX AD REPORTS ON ONE AND AND TWO FAILURES.",
            "APPENDIX AE REPORTS ON TWENTY THIRTY FAILURES.",
            "APPENDIX AF REPORTS ON ONE AND FAILURES.",
            "APPENDIX AG REPORTS ON ONE HUNDRED ZERO FAILURES.",
            "APPENDIX AH REPORTS ON ONE HUNDRED AND ZERO FAILURES.",
            "APPENDIX AI REPORTS ON ONE BILLION ZERO MILLION EVENTS.",
            "APPENDIX AJ REPORTS ON ONE THOUSAND AND ZERO CASES.",
            "APPENDIX AK REPORTS ON A MILLIONS OF PACKETS.",
            "APPENDIX AL REPORTS ON A PAIRS OF ERRORS.",
            "APPENDIX AM REPORTS ON A HUNDREDS OF TESTS.",
            "APPENDIX AN REPORTS ON AT LEAST NEITHER FAILURE.",
            "APPENDIX AO REPORTS ON MORE THAN ALL FAILURES.",
            "APPENDIX AP REPORTS ON EACH PAIRS OF ERRORS.",
            "APPENDIX AQ REPORTS ON BOTH PAIR OF ERRORS.",
            "APPENDIX AR REPORTS ON EACH FAILURES.",
            "APPENDIX AS REPORTS ON BOTH FAILURE.",
            "APPENDIX AT REPORTS ON ONE FAILURES.",
            "APPENDIX AU REPORTS ON TWO FAILURE.",
            "APPENDIX AV REPORTS ON A FAILURES.",
            "APPENDIX AW REPORTS ON ALL FAILURE.",
            "APPENDIX AX REPORTS ON A COUPLE OF FAILURE.",
            "APPENDIX AY REPORTS ON ONE PAIR OF ERROR.",
            "APPENDIX AZ REPORTS ON APPROXIMATELY MORE FAILURES.",
            "APPENDIX BA REPORTS ON AN FAILURE.",
            "APPENDIX BB REPORTS ON A ERROR.",
            "APPENDIX BC REPORTS ON APPROXIMATELY BOTH FAILURES.",
            "APPENDIX BD REPORTS ON AN PAIR OF ERRORS.",
            "APPENDIX BE REPORTS ON APPROXIMATELY EACH FAILURE.",
            "APPENDIX BF REPORTS ON ZERO OR ONE FAILURES.",
            "APPENDIX BG REPORTS ON MORE THAN ONE TO TWO FAILURES.",
            "APPENDIX BH REPORTS ON UP TO ONE TO TWO FAILURES.",
            "APPENDIX BI REPORTS ON EXACTLY ONE TO TWO FAILURES.",
            "APPENDIX BJ REPORTS ON OVER BETWEEN ONE AND TWO FAILURES.",
            "APPENDIX BK REPORTS ON UNDER FROM ONE TO TWO FAILURES.",
            "APPENDIX BL REPORTS ON ONE MILLION THOUSAND TO TWO MILLION PACKETS.",
            "APPENDIX BM REPORTS ON ONE HUNDRED HUNDRED TO TWO HUNDRED FAILURES.",
            "APPENDIX BN REPORTS ON ZERO TO ONE FAILURES.",
            "APPENDIX BO REPORTS ON ONE OR OR TWO FAILURES.",
            "APPENDIX BP REPORTS ON ONE, OR OR TWO FAILURES.",
            "APPENDIX BQ REPORTS ON ONE OR TWO, THREE FAILURES.",
            "APPENDIX BR REPORTS ON ONE DOZEN HUNDRED FAILURES.",
            "APPENDIX BS REPORTS ON SEVERAL DOZEN HUNDRED THOUSAND FAILURES.",
            "APPENDIX BT REPORTS ON HUNDREDS OF THOUSANDS PACKETS.",
            "APPENDIX BU REPORTS ON HUNDREDS OF THOUSANDS OF TO MILLIONS OF PACKETS.",
            "ANNEX B RECORDS SUPPORTED BY THE RECEIVER.",
            "APPENDIX C NOTES APPLICABLE TO TEST FIXTURES.",
            "PART II MAPS USED FOR VERIFICATION.",
            "附录 A 说明：适用要求和示例。",
            "附录 B 描述—适用要求范围！",
            "附录 A 说明：行为要求和示例。",
            "附录 B 描述—用于校准的设备。",
            "附录 C 说明：需要说明的事项。",
            "附录 D 说明：适用于 PAM4 的要求。",
            "附录 E 说明：是非判断规则。",
            "附录 A 说明：接收机必须满足的要求。",
            "附录 B 描述—接收机需要支持的模式。",
            "附录 C 说明：接收机具有的功能。",
            "附录 D 描述：设备可以使用的校准方法。",
            "附录 E 说明：模块不得超过的限值。",
            "附录 F 说明：接收机必须满足的 34.0 dB 要求。",
            "附录 G 说明：PAM4 接收机必须满足的要求。",
            "附录 H 说明：接收机必须始终满足的要求。",
            "附录 I 描述—接收机需要额外支持的模式。",
            "附录 J 说明：设备可以独立使用的校准方法。",
            "附录 K 说明：模块不得擅自超过的限值。",
            "附录 L 说明：接收机必须满足协议要求的条件。",
            "附录 M 描述—接收机需要支持 PAM4 模式的要求。",
            "附录 N 说明：设备可以使用测试软件的方法。",
            "附录 O 说明：模块不得超过协议限值的规则。",
            "附录 P 说明：接收机必须在高温下满足的要求。",
            "附录 Q 说明：接收机必须满足所有规定的要求。",
            "附录 R 描述—接收机需要支持协议所列的模式。",
            "附录 S 说明：设备可以使用规范推荐的校准方法。",
            "附录 T 说明：接收机必须满足的行为要求。",
            "附录 U 说明：接收机必须满足的是非判定规则。",
            "附录 V 说明：设备可以采用的需要说明事项。",
            "附录 W 说明：模块不得违反的用于校准要求。",
        ):
            with self.subTest(title=title):
                extraction = ExtractionResult(
                    pdf_path=Path("ambiguous-container-title.pdf"),
                    pages=[
                        PageText(
                            page_number=1,
                            text=(
                                "1 Scope\n"
                                f"{title}\n"
                                "The calibrated tolerance is 0.025 UI."
                            ),
                        )
                    ],
                )

                sections = section_document(extraction)

                self.assertIn(title, [section.location for section in sections])
                self.assertTrue(
                    any(
                        "0.025 UI" in section.body
                        for section in sections
                        if section.location == title
                    )
                )

    def test_coordinated_container_predicates_do_not_capture_later_sections(
        self,
    ) -> None:
        """Predicate chains with commas, adverbs, or soft-wrap punctuation stay prose."""

        for reference in (
            "Appendix A describes, defines, and documents the calibration method.",
            "Annex B contains and clearly explains the receiver limits.",
            "Annex B contains and very clearly explains the receiver limits.",
            "Appendix A explains.",
            "APPENDIX A SUMMARIZES.",
            "Appendix A explains and summarizes.",
            "APPENDIX A EXPLAINS AND SUMMARIZES.",
            "Appendix A outlines the calibration method.",
            "Appendix A clearly explains the receiver limits.",
            "Appendix A explains: the receiver limit is 20 mV.",
            "Part II describes the transmitter—receiver interface.",
            "APPENDIX A STATES TWO REQUIREMENTS.",
            "ANNEX B LISTS ALL REQUIREMENTS.",
            "APPENDIX C COVERS THREE METHODS.",
            "APPENDIX A STATES RECEIVER REQUIREMENTS.",
            "APPENDIX A STATES REQUIREMENTS CLEARLY.",
            "APPENDIX A REPORTS ON TWO FAILURES.",
            "APPENDIX A REPORTS ON SEVERAL FAILURES.",
            "APPENDIX A REPORTS ABOUT MORE THAN TWO FAILURES.",
            "APPENDIX A REPORTS ON 25% OF TESTS.",
            "ANNEX B REPORTS ON 1,000 FAILURES.",
            "APPENDIX A REPORTS ON ZERO FAILURES.",
            "APPENDIX B REPORTS ON ELEVEN FAILURES.",
            "APPENDIX C NOTES ON TWELVE TESTS.",
            "APPENDIX D REPORTS ABOUT TWENTY CASES.",
            "APPENDIX E REPORTS ON MORE THAN ELEVEN FAILURES.",
            "APPENDIX F REPORTS ON A DOZEN FAILURES.",
            "APPENDIX G REPORTS ON A WARNING.",
            "APPENDIX H REPORTS ON TWO WARNINGS.",
            "APPENDIX I REPORTS ON TWO DOZEN FAILURES.",
            "APPENDIX J REPORTS ON THREE DOZEN WARNINGS.",
            "APPENDIX K REPORTS ON A HUNDRED TESTS.",
            "APPENDIX L REPORTS ON ONE THOUSAND CASES.",
            "APPENDIX M REPORTS ON A COUPLE OF FAILURES.",
            "APPENDIX N REPORTS ON MORE THAN A DOZEN FAILURES.",
            "APPENDIX O REPORTS ON AT LEAST A DOZEN WARNINGS.",
            "APPENDIX P REPORTS ON 2 DOZEN FAILURES.",
            "APPENDIX Q REPORTS ON 1 MILLION PACKETS.",
            "APPENDIX R REPORTS ON MILLIONS OF PACKETS.",
            "APPENDIX S REPORTS ON SEVERAL DOZEN WARNINGS.",
            "APPENDIX T REPORTS ON PAIRS OF ERRORS.",
            "APPENDIX U REPORTS ON TWO THOUSAND FIVE HUNDRED FAILURES.",
            "APPENDIX V REPORTS ON ONE THOUSAND ONE CASES.",
            "APPENDIX W REPORTS ON ONE MILLION TWO HUNDRED THOUSAND EVENTS.",
            "APPENDIX X REPORTS ON 1.5 MILLION PACKETS.",
            "APPENDIX Y REPORTS ON HALF A DOZEN FAILURES.",
            "APPENDIX Z REPORTS ON A FEW DOZEN WARNINGS.",
            "ANNEX A REPORTS ON TWO PAIRS OF ERRORS.",
            "ANNEX B REPORTS ON SEVERAL MILLIONS OF PACKETS.",
            "ANNEX C REPORTS ON TENS OF FAILURES.",
            "ANNEX D REPORTS ON MORE THAN A FEW FAILURES.",
            "ANNEX E REPORTS ON AT LEAST SEVERAL WARNINGS.",
            "ANNEX F REPORTS ON AT LEAST ANOTHER WARNING.",
            "ANNEX G REPORTS ON ONE AND A HALF MILLION FAILURES.",
            "ANNEX H REPORTS ON TWO AND A HALF DOZEN FAILURES.",
            "ANNEX I REPORTS ON 25 PERCENT OF TESTS.",
            "ANNEX J REPORTS ON TWENTY-FIVE PERCENT OF TESTS.",
            "ANNEX K REPORTS ON APPROXIMATELY ONE HUNDRED FAILURES.",
            "ANNEX L REPORTS ON OVER ONE HUNDRED FAILURES.",
            "ANNEX M REPORTS ON OVER A FEW FAILURES.",
            "ANNEX N REPORTS ON APPROXIMATELY A FEW WARNINGS.",
            "ANNEX O REPORTS ABOUT A FEW ERRORS.",
            "ANNEX P REPORTS ON NEARLY ALL TESTS.",
            "ANNEX Q REPORTS ON ALMOST EVERY WARNING.",
            "ANNEX R REPORTS ON OVER HALF OF TESTS.",
            "ANNEX S REPORTS ON APPROXIMATELY HALF OF EVENTS.",
            "ANNEX T REPORTS ON MORE THAN HALF OF FAILURES.",
            "ANNEX U REPORTS ON NO MORE THAN ONE HUNDRED FAILURES.",
            "ANNEX V REPORTS ON ONE THOUSAND PAIRS OF ERRORS.",
            "ANNEX W REPORTS ON TWO HUNDRED PAIRS OF ERRORS.",
            "ANNEX X REPORTS ON BOTH PAIRS OF ERRORS.",
            "ANNEX Y REPORTS ON ALL PAIRS OF ERRORS.",
            "ANNEX Z REPORTS ON NO PAIRS OF ERRORS.",
            "PART I REPORTS ON ONE PAIR OF ERRORS.",
            "PART II REPORTS ON EACH PAIR OF ERRORS.",
            "PART III REPORTS ON EXACTLY ONE FAILURE.",
            "PART IV REPORTS ON EXACTLY TWO FAILURES.",
            "PART V REPORTS ON ONLY ONE FAILURE.",
            "PART VI REPORTS ON UP TO TWO FAILURES.",
            "PART VII REPORTS ON ONE ANALYSIS.",
            "PART VIII REPORTS ON TWO ANALYSES.",
            "PART IX REPORTS ON ONE CRITERION.",
            "PART X REPORTS ON TWO CRITERIA.",
            "PART XI REPORTS ON ONE INDEX.",
            "PART XII REPORTS ON TWO INDICES.",
            "PART XIII REPORTS ON ONLY SOME FAILURES.",
            "PART XIV REPORTS ON ONLY SEVERAL FAILURES.",
            "PART XV REPORTS ON ONLY MANY FAILURES.",
            "PART XVI REPORTS ON ONE OR MORE FAILURES.",
            "PART XVII REPORTS ON ONE OR TWO FAILURES.",
            "PART XVIII REPORTS ON ONE TO TWO FAILURES.",
            "PART XIX REPORTS ON BETWEEN ONE AND TWO FAILURES.",
            "PART XX REPORTS ON ZERO OR ONE FAILURE.",
            "PART XXI REPORTS ON ONE OR TWO OR THREE FAILURES.",
            "PART XXII REPORTS ON 1 MILLION TO 2 MILLION PACKETS.",
            "PART XXIII REPORTS ON 1.5 MILLION TO 2.5 MILLION PACKETS.",
            "PART XXIV REPORTS ON ONE DOZEN TO TWO DOZEN FAILURES.",
            "PART XXV REPORTS ON HALF A MILLION TO ONE MILLION PACKETS.",
            "PART XXVI REPORTS ON ONE, TWO, OR THREE FAILURES.",
            "PART XXVII REPORTS ON ZERO TO ONE FAILURE.",
            "PART XXVIII REPORTS ON FROM ZERO TO ONE FAILURE.",
            "PART XXIX REPORTS ON BETWEEN ZERO AND ONE FAILURE.",
            "PART XXX REPORTS ON ONE AND TWO FAILURES.",
            "PART XXXI REPORTS ON ONE, TWO, AND THREE FAILURES.",
            "PART XXXII REPORTS ON A FEW DOZEN TO SEVERAL DOZEN FAILURES.",
            "PART XXXIII REPORTS ON ONE MILLION AND TWO MILLION PACKETS.",
            "PART XXXIV REPORTS ON ONE HUNDRED AND TWO HUNDRED FAILURES.",
            "PART XXXV REPORTS ON ONE HUNDRED AND FIVE OR TWO HUNDRED AND SIX FAILURES.",
            "PART XXXVI REPORTS ON SEVERAL HUNDRED THOUSAND PACKETS.",
            "PART XXXVII REPORTS ON A FEW HUNDRED THOUSAND PACKETS.",
            "PART XXXVIII REPORTS ON SEVERAL HUNDRED THOUSAND TO ONE MILLION PACKETS.",
            "PART XXXIX REPORTS ON HUNDREDS OF THOUSANDS TO MILLIONS OF PACKETS.",
            "PART XL REPORTS ON EXACTLY ONE OR TWO FAILURES.",
            "PART XLI REPORTS ON ONE AND TWO HUNDRED FAILURES.",
            "PART XLII REPORTS ON MILLIONS OR BILLIONS OF PACKETS.",
            "PART XLIII REPORTS ON MILLIONS AND BILLIONS OF PACKETS.",
            "附录 A 说明：接收机的限值为 20 mV。",
            "附录 B 描述—接收机的模式是 PAM4。",
            "附录 C 说明：模块的参数必须保持稳定。",
            "附录 D 描述：设备的功能可以独立启用。",
            "附录 E 说明：接收机的值为 20 mV。",
            "附录 A 描述和定义校准方法",
            "附录 A 描述、定义并记录校准方法",
            "附录 B 说明及明确规定接收机限值。",
            "附录 B 说明及全面规定接收机限值。",
            "附录 C 描述并准确地定义校准方法",
            "附录 D 说明并清晰地规定接收机限值",
            "附录 E 描述、 定义并 记录校准方法",
            "附录 C 说明并充分定义校准方法。",
            "附录 D 说明并进一步明确规定接收机限值。",
            "附录 E 说明、要求并规定接收机限值。",
            "附录 A 说明：接收机限值为20mV。",
            "附录 A 阐述校准方法。",
            "附录 A 明确规定接收机限值。",
            "附录 A 说 明并规定接收机限值。",
        ):
            with self.subTest(reference=reference):
                extraction = ExtractionResult(
                    pdf_path=Path("coordinated-container-reference.pdf"),
                    pages=[
                        PageText(
                            page_number=1,
                            text=(
                                "1 Scope\n"
                                f"{reference}\n"
                                "2 Verification\n"
                                "The verified threshold is 34.0 dB."
                            ),
                        )
                    ],
                )

                sections = section_document(extraction)

                self.assertEqual(
                    ["1 Scope", "2 Verification"],
                    [section.location for section in sections],
                )
                self.assertIn(reference, sections[0].body)

    def test_complete_uppercase_container_reference_sentences_stay_prose(
        self,
    ) -> None:
        """A closed all-caps predicate is prose even when its complement is short."""

        for line in (
            "ANNEX A APPLIES.",
            "ANNEX B REMAINS.",
            "APPENDIX C STATES REQUIREMENTS.",
            "ANNEX D LISTS REQUIREMENTS.",
        ):
            with self.subTest(line=line):
                self.assertIsNone(detect_heading(line))

    def test_wrapped_container_reference_sentence_does_not_capture_later_sections(
        self,
    ) -> None:
        """A PDF soft wrap cannot turn a container subject into a new parent."""

        extraction = ExtractionResult(
            pdf_path=Path("wrapped-container-reference.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Appendix A describes the calibration\n"
                        "method used for all receivers.\n"
                        "2 Verification\n"
                        "Evidence remains visible."
                    ),
                )
            ],
        )

        sections = section_document(extraction)

        self.assertEqual(
            ["1 Scope", "2 Verification"],
            [section.location for section in sections],
        )
        self.assertIn("Appendix A describes the calibration", sections[0].body)

    def test_standalone_container_identifier_and_wrapped_predicate_stay_prose(
        self,
    ) -> None:
        """A container subject split immediately after its ID is still one sentence."""

        for identifier, continuation in (
            ("Appendix A", "remains normative for receiver testing."),
            ("APPENDIX A", "OF THIS DOCUMENT."),
            ("附录 B", "中规定接收机限值。"),
        ):
            with self.subTest(identifier=identifier, continuation=continuation):
                extraction = ExtractionResult(
                    pdf_path=Path("wrapped-container-id-reference.pdf"),
                    pages=[
                        PageText(
                            page_number=1,
                            text=(
                                "1 Scope\n"
                                f"{identifier}\n"
                                f"{continuation}\n"
                                "2 Verification\n"
                                "Evidence remains visible."
                            ),
                        )
                    ],
                )

                sections = section_document(extraction)

                self.assertEqual(
                    ["1 Scope", "2 Verification"],
                    [section.location for section in sections],
                )
                self.assertIn(identifier, sections[0].body)
                self.assertIn(continuation, sections[0].body)

    def test_standalone_container_identifier_before_noun_title_remains_structural(
        self,
    ) -> None:
        """The wrapped-line guard must not demote a real container plus noun title."""

        extraction = ExtractionResult(
            pdf_path=Path("wrapped-container-id-title.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Appendix A\n"
                        "States and Transitions\n"
                        "The transition tolerance is 0.025 UI.\n"
                        "1 State definitions\n"
                        "The state threshold is 34.0 dB."
                    ),
                )
            ],
        )

        sections = section_document(extraction)

        self.assertEqual(
            ["1 Scope", "Appendix A", "Appendix A / 1 State definitions"],
            [section.location for section in sections],
        )
        self.assertIn("States and Transitions", sections[1].body)
        self.assertIn("0.025 UI", sections[1].body)

    def test_page_break_after_container_identifier_preserves_sentence_or_title(
        self,
    ) -> None:
        """The immediately adjacent next page can finish a subject or start a noun title."""

        sentence_extraction = ExtractionResult(
            pdf_path=Path("page-break-container-sentence.pdf"),
            pages=[
                PageText(page_number=1, text="1 Scope\nAppendix A"),
                PageText(
                    page_number=2,
                    text=(
                        "remains normative for receiver testing.\n"
                        "2 Verification\n"
                        "Evidence remains visible."
                    ),
                ),
            ],
        )
        title_extraction = ExtractionResult(
            pdf_path=Path("page-break-container-title.pdf"),
            pages=[
                PageText(page_number=1, text="1 Scope\nAppendix A"),
                PageText(
                    page_number=2,
                    text=(
                        "States and Transitions\n"
                        "The transition tolerance is 0.025 UI.\n"
                        "1 State definitions\n"
                        "The state threshold is 34.0 dB."
                    ),
                ),
            ],
        )

        self.assertEqual(
            ["1 Scope", "2 Verification"],
            [section.location for section in section_document(sentence_extraction)],
        )
        self.assertEqual(
            ["1 Scope", "Appendix A", "Appendix A / 1 State definitions"],
            [section.location for section in section_document(title_extraction)],
        )

    def test_nonconsecutive_page_break_does_not_join_container_sentence(
        self,
    ) -> None:
        """Missing physical pages make a cross-page sentence join unprovable."""

        extraction = ExtractionResult(
            pdf_path=Path("nonconsecutive-page-container.pdf"),
            pages=[
                PageText(page_number=1, text="1 Scope\nAppendix A"),
                PageText(
                    page_number=3,
                    text=(
                        "remains normative for receiver testing.\n"
                        "2 Verification\n"
                        "Evidence remains visible."
                    ),
                ),
            ],
        )

        sections = section_document(extraction)

        self.assertIn("Appendix A", [section.location for section in sections])

    def test_container_delimiter_and_blank_paragraph_block_wrapped_sentence_guard(
        self,
    ) -> None:
        """A strong title delimiter or blank paragraph is never joined to a predicate."""

        for identifier, continuation in (
            ("Appendix A:", "Applies to Receivers"),
            ("Annex B.", "Remains normative for receiver testing"),
            ("Part II:", "Sets out the calibration procedure"),
            ("附录 B：", "中规定接收机限值"),
        ):
            with self.subTest(identifier=identifier):
                extraction = ExtractionResult(
                    pdf_path=Path("delimited-container-title.pdf"),
                    pages=[
                        PageText(
                            page_number=1,
                            text=(
                                "1 Scope\n"
                                f"{identifier}\n"
                                f"{continuation}\n"
                                "1 Child requirements\n"
                                "The child threshold is 0.025 UI."
                            ),
                        )
                    ],
                )
                sections = section_document(extraction)
                self.assertEqual(identifier, sections[1].location)
                self.assertEqual(
                    f"{identifier} / 1 Child requirements",
                    sections[2].location,
                )

        blank_extraction = ExtractionResult(
            pdf_path=Path("blank-separated-container-title.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Appendix A\n\n"
                        "Applies to Receivers\n"
                        "1 Child requirements\n"
                        "The child threshold is 34.0 dB."
                    ),
                )
            ],
        )
        blank_sections = section_document(blank_extraction)
        self.assertEqual("Appendix A", blank_sections[1].location)
        self.assertEqual(
            "Appendix A / 1 Child requirements",
            blank_sections[2].location,
        )

    def test_part_and_appendix_titles_accept_compact_unicode_dashes(self) -> None:
        """En/em dashes are valid title separators even without surrounding spaces."""

        for line in (
            "Part II–Architecture",
            "Annex B—Normative requirements",
            "Appendix C–Calibration data",
            "Appendix E Receiver Calibration Reference Requirements.",
            "Annex A normative references",
            "Appendix A calibration data",
            "Appendix A States and Transitions",
            "Annex B Lists of Tables",
            "Appendix C Covers and Enclosures",
            "Appendix E Provides and Services",
            "Part II States and Lists of Tables",
            "APPENDIX A STATES AND TRANSITIONS",
            "Appendix A States the Receiver Supports",
            "Appendix A States the Receiver Supports.",
            "APPENDIX A STATES THE RECEIVER SUPPORTS.",
            "APPENDIX A STATES SUPPORTED BY THE RECEIVER.",
            "APPENDIX A STATES THE PROTOCOL RECEIVER SUPPORTS.",
            "APPENDIX B LISTS SUPPORTED FEATURES.",
            "ANNEX C STATES REQUIRED FOR CALIBRATION.",
            "Annex B Lists the Receiver Supports",
            "Annex B Lists the Receiver Supports.",
            "Appendix C Covers for Test Fixtures",
            "Appendix D Details for Calibration",
            "Appendix B Describes, Defines, and Documents.",
            "APPENDIX A SYSTEMS AND INTERFACES.",
            "ANNEX B REQUIREMENTS AND EXAMPLES.",
            "PART II METHODS AND RESULTS.",
            "Appendix A systems and interfaces.",
            "Annex B requirements and examples.",
            "Annex B processes and procedures.",
            "Appendix C analysis methods.",
            "APPENDIX A INTERFACES AND PROTOCOLS.",
            "APPENDIX A REPORTS REQUIRED FOR CALIBRATION.",
            "APPENDIX A REPORTS THE RECEIVER GENERATES.",
            "APPENDIX A NOTES ON CALIBRATION METHODS.",
            "ANNEX B REPORTS ABOUT RECEIVER TESTING.",
            "APPENDIX C RECORDS FROM CALIBRATION.",
            "APPENDIX A NOTES ON 2.5-GT/S CALIBRATION.",
            "APPENDIX B REPORTS ON 25% MARGIN ANALYSIS.",
            "APPENDIX C NOTES ON 1,000-LANE CALIBRATION.",
            "APPENDIX D REPORTS ON 25% MARGIN ANALYSIS.",
            "APPENDIX E NOTES ON 2.5 GT/S CALIBRATION.",
            "APPENDIX F REPORTS ON 34.0 dB LIMITS.",
            "APPENDIX G REPORTS ON 56.25 GBd LIMITS.",
            "APPENDIX H NOTES ON 120 mVrms CALIBRATION.",
            "APPENDIX I REPORTS ON 3.5 mVpp MARGIN.",
            "APPENDIX J NOTES ON 20 degrees PHASE.",
            "APPENDIX K NOTES ON 2.5 Tbaud CALIBRATION.",
            "APPENDIX L NOTES ON 20 DEGREES.",
            "APPENDIX M REPORTS ON 120 mVrms.",
            "APPENDIX N NOTES ON 50 OHMS.",
            "APPENDIX O REPORTS ON 25 GBPS.",
            "APPENDIX P NOTES ON TWO mVrms.",
            "APPENDIX AA REPORTS ON AT LEAST PAIRS OF ERRORS.",
            "APPENDIX AB REPORTS ON ONE MILLION THOUSAND FAILURES.",
            "APPENDIX AC REPORTS ON ONE HUNDRED HUNDRED FAILURES.",
            "APPENDIX AD REPORTS ON ONE AND AND TWO FAILURES.",
            "APPENDIX AE REPORTS ON TWENTY THIRTY FAILURES.",
            "APPENDIX AF REPORTS ON ONE AND FAILURES.",
            "APPENDIX AG REPORTS ON ONE HUNDRED ZERO FAILURES.",
            "APPENDIX AH REPORTS ON ONE HUNDRED AND ZERO FAILURES.",
            "APPENDIX AI REPORTS ON ONE BILLION ZERO MILLION EVENTS.",
            "APPENDIX AJ REPORTS ON ONE THOUSAND AND ZERO CASES.",
            "ANNEX B RECORDS SUPPORTED BY THE RECEIVER.",
            "APPENDIX C NOTES APPLICABLE TO TEST FIXTURES.",
            "PART II MAPS USED FOR VERIFICATION.",
            "附录 A—校准数据",
            "附录 A 说明",
            "附录 B 规定",
            "附录 C 校准数据的说明",
            "附录 D 说明与规定",
            "附录 E 描述与分析",
            "附录 A 说明、要求和示例",
            "附录 B 描述、定义、缩写",
            "附录 A 说明：要求和示例",
            "附录 A 说明：要求和示例。",
            "附录 A 说明：适用要求和示例。",
            "附录 A 说明：行为要求和示例。",
            "附录 B 描述—定义和缩写",
            "附录 B 描述—定义和缩写！",
            "附录 B 描述—适用要求范围！",
            "附录 B 描述—用于校准的设备。",
            "附录 C 说明：需要说明的事项。",
            "附录 D 说明：适用于 PAM4 的要求。",
            "附录 E 说明：是非判断规则。",
            "附录 A 说明：接收机必须满足的要求。",
            "附录 B 描述—接收机需要支持的模式。",
            "附录 C 说明：接收机具有的功能。",
            "附录 D 描述：设备可以使用的校准方法。",
            "附录 E 说明：模块不得超过的限值。",
            "附录 F 说明：接收机必须满足的 34.0 dB 要求。",
            "附录 G 说明：PAM4 接收机必须满足的要求。",
            "附录 H 说明：接收机必须始终满足的要求。",
            "附录 I 描述—接收机需要额外支持的模式。",
            "附录 J 说明：设备可以独立使用的校准方法。",
            "附录 K 说明：模块不得擅自超过的限值。",
            "附录 L 说明：接收机必须满足协议要求的条件。",
            "附录 M 描述—接收机需要支持 PAM4 模式的要求。",
            "附录 N 说明：设备可以使用测试软件的方法。",
            "附录 O 说明：模块不得超过协议限值的规则。",
            "附录 P 说明：接收机必须在高温下满足的要求。",
            "附录 Q 说明：接收机必须满足所有规定的要求。",
            "附录 R 描述—接收机需要支持协议所列的模式。",
            "附录 S 说明：设备可以使用规范推荐的校准方法。",
            "附录 T 说明：接收机必须满足的行为要求。",
            "附录 U 说明：接收机必须满足的是非判定规则。",
            "附录 V 说明：设备可以采用的需要说明事项。",
            "附录 W 说明：模块不得违反的用于校准要求。",
            "附录 A 说明 、 要求 和 示例",
            "附录 B 描述 、 定义 、 缩写",
            "附录 A 说明及适用要求范围。",
        ):
            with self.subTest(line=line):
                self.assertIsNotNone(detect_heading(line))

    def test_english_named_container_noun_titles_keep_their_technical_bodies(self) -> None:
        """A noun title beginning with a verb-shaped word still owns its body."""

        extraction = ExtractionResult(
            pdf_path=Path("english-annex-noun-titles.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 General requirements\n"
                        "The receiver requirements apply.\n"
                        "Appendix A States and Transitions\n"
                        "The transition tolerance is 0.025 UI.\n"
                        "1 State definitions\n"
                        "The state threshold is 34.0 dB."
                    ),
                )
            ],
        )

        sections = section_document(extraction)

        self.assertEqual(
            [
                "1 General requirements",
                "Appendix A States and Transitions",
                "Appendix A States and Transitions / 1 State definitions",
            ],
            [section.location for section in sections],
        )
        self.assertIn("0.025 UI", sections[1].body)
        self.assertIn("34.0 dB", sections[2].body)

    def test_chinese_appendix_heading_ends_a_wrapped_numbered_list(self) -> None:
        """A Chinese appendix is a structural boundary, not list-item body."""

        extraction = ExtractionResult(
            pdf_path=Path("chinese-appendix-after-list.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "24 Summary\n"
                        "1. The first observation describes the architecture.\n"
                        "2. The second observation describes the protocol evolution.\n"
                        "3. The final observation maps every symptom to its layer and\n"
                        "the evidence required to isolate it.\n"
                        "附录 A. Reference sources\n"
                        "The appendix records the supporting material."
                    ),
                )
            ],
        )

        self.assertEqual(
            ["24 Summary", "附录 A. Reference sources"],
            [section.location for section in section_document(extraction)],
        )

    def test_chinese_appendix_contextualizes_its_numeric_children(self) -> None:
        """Numeric headings following a Chinese appendix stay under that container."""

        extraction = ExtractionResult(
            pdf_path=Path("chinese-appendix-child.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "附录 A. Reference sources\n"
                        "1 General requirements\n"
                        "The appendix requirement preserves its local numbering."
                    ),
                )
            ],
        )

        self.assertEqual(
            [
                "附录 A. Reference sources",
                "附录 A. Reference sources / 1 General requirements",
            ],
            [section.location for section in section_document(extraction)],
        )

    def test_part_nested_real_chapter_after_item_one_is_not_demoted(self) -> None:
        """Part contextualization must still identify its active numeric chapter."""

        extraction = ExtractionResult(
            pdf_path=Path("part-nested-real-chapter.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "Part II\n"
                        "1 Existing requirements\n"
                        "1. The requester records each message before forwarding it.\n"
                        "2. Receiver limits are mandatory for independent devices.\n"
                        "表格行: T1 | Parameter=Mode A | Value=20 mV\n"
                        "3 Verification\n"
                        "The verification chapter defines independent evidence."
                    ),
                )
            ],
        )

        self.assertEqual(
            [
                "Part II",
                "Part II / 1 Existing requirements",
                "Part II / 2. Receiver limits are mandatory for independent devices.",
                "Part II / 3 Verification",
            ],
            [section.location for section in section_document(extraction)],
        )

    def test_wrapped_mid_list_item_can_reach_the_third_page(self) -> None:
        """One soft-wrapped item may close on page 2 before item 5 on page 3."""

        extraction = ExtractionResult(
            pdf_path=Path("three-page-wrapped-mid-list.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "6 Data flow\n"
                        "4. The link layer records the identifier and preserves every declared"
                    ),
                ),
                PageText(
                    page_number=2,
                    text="condition before forwarding the request.",
                ),
                PageText(
                    page_number=3,
                    text=(
                        "5. The receiver validates the request and returns a response.\n"
                        "7 Transaction rules\n"
                        "The transaction chapter defines independent request handling."
                    ),
                ),
            ],
        )

        sections = section_document(extraction)

        self.assertEqual(
            ["6 Data flow", "7 Transaction rules"],
            [section.location for section in sections],
        )
        self.assertIn("4. The link layer", sections[0].body)
        self.assertIn("5. The receiver validates", sections[0].body)

    def test_wrapped_mid_list_item_can_reach_the_fourth_page(self) -> None:
        """Soft wrapping follows content closure rather than a fixed page count."""

        extraction = ExtractionResult(
            pdf_path=Path("four-page-wrapped-mid-list.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "6 Data flow\n"
                        "4. The link layer records the identifier and preserves every declared"
                    ),
                ),
                PageText(page_number=2, text="condition while the request crosses a"),
                PageText(page_number=3, text="page boundary before forwarding the request."),
                PageText(
                    page_number=4,
                    text=(
                        "5. The receiver validates the request and returns a response.\n"
                        "7 Transaction rules\n"
                        "The transaction chapter defines independent request handling."
                    ),
                ),
            ],
        )

        sections = section_document(extraction)

        self.assertEqual(
            ["6 Data flow", "7 Transaction rules"],
            [section.location for section in sections],
        )
        self.assertIn("4. The link layer", sections[0].body)
        self.assertIn("5. The receiver validates", sections[0].body)

    def test_table_only_real_chapter_ends_a_mid_list_chain(self) -> None:
        """A real next chapter may contain only serialized table evidence."""

        extraction = ExtractionResult(
            pdf_path=Path("table-only-real-chapter.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "6 Data flow\n"
                        "4. The link layer records the message identifier before forwarding.\n"
                        "5. The physical layer encodes the payload and sends it to the peer.\n"
                        "6. The peer validates the payload and returns an acknowledgement.\n"
                        "7. Receiver limits and operating conditions.\n"
                        "表格行: T1 | Parameter=Mode A | Value=20 mV"
                    ),
                ),
                PageText(
                    page_number=2,
                    text=(
                        "8 Verification requirements\n"
                        "The verification chapter defines independent evidence."
                    ),
                ),
            ],
        )

        sections = section_document(extraction)

        self.assertEqual(
            [
                "6 Data flow",
                "7. Receiver limits and operating conditions.",
                "8 Verification requirements",
            ],
            [section.location for section in sections],
        )
        self.assertIn("Parameter=Mode A", sections[1].body)

    def test_real_chapter_after_list_past_parent_number_keeps_plain_body(self) -> None:
        """A proven 4..7 list cannot consume a body-backed chapter 8."""

        extraction = ExtractionResult(
            pdf_path=Path("real-chapter-after-long-mid-list.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "6 Data flow\n"
                        "4. The link layer records each message before forwarding it.\n"
                        "5. The physical layer encodes each payload before transmission.\n"
                        "6. The peer validates each payload before acknowledging it.\n"
                        "7. The receiver records each acknowledgement before completion.\n"
                        "8. Receiver limits are mandatory for independent devices.\n"
                        "The receiver chapter defines independent device behavior."
                    ),
                )
            ],
        )

        self.assertEqual(
            [
                "6 Data flow",
                "8. Receiver limits are mandatory for independent devices.",
            ],
            [section.location for section in section_document(extraction)],
        )

    def test_real_chapter_after_list_past_parent_number_keeps_table_body(self) -> None:
        """A table-backed chapter after a long list stays visible before chapter 9."""

        extraction = ExtractionResult(
            pdf_path=Path("table-chapter-after-long-mid-list.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "6 Data flow\n"
                        "4. The link layer records each message before forwarding it.\n"
                        "5. The physical layer encodes each payload before transmission.\n"
                        "6. The peer validates each payload before acknowledging it.\n"
                        "7. The receiver records each acknowledgement before completion.\n"
                        "8. Receiver limits are mandatory for independent devices.\n"
                        "表格行: T1 | Parameter=Mode A | Value=20 mV"
                    ),
                ),
                PageText(
                    page_number=2,
                    text=(
                        "9 Verification\n"
                        "The verification chapter defines independent evidence."
                    ),
                ),
            ],
        )

        sections = section_document(extraction)

        self.assertEqual(
            [
                "6 Data flow",
                "8. Receiver limits are mandatory for independent devices.",
                "9 Verification",
            ],
            [section.location for section in sections],
        )
        self.assertIn("Parameter=Mode A", sections[1].body)

    def test_mid_list_summary_before_parent_descendant_stays_in_parent(self) -> None:
        """A later parent subclause proves that summary prose did not open item 8."""

        extraction = ExtractionResult(
            pdf_path=Path("mid-list-summary-before-descendant.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "6 Data flow\n"
                        "4. The link layer records each message before forwarding it.\n"
                        "5. The physical layer encodes each payload before transmission.\n"
                        "6. The peer validates each payload before acknowledging it.\n"
                        "7. The receiver returns a completion after processing the request.\n"
                        "8. The requester matches the completion to the original request.\n"
                        "This paragraph summarizes the complete request flow.\n"
                        "The next subclause maps the flow to product behavior.\n"
                        "6.1 Product behavior mapping\n"
                        "The mapping remains part of the data-flow chapter."
                    ),
                )
            ],
        )

        self.assertEqual(
            ["6 Data flow", "6 Data flow / 6.1 Product behavior mapping"],
            [section.location for section in section_document(extraction)],
        )

    def test_short_list_summary_before_parent_descendant_stays_in_parent(self) -> None:
        """A 1..2 summary plus table remains prose when chapter 17.1 follows."""

        extraction = ExtractionResult(
            pdf_path=Path("short-list-summary-before-descendant.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "17 Encoding and adaptation\n"
                        "1. The encoder improves transport efficiency for every payload.\n"
                        "2. The equalizer adapts the transmitter and receiver to the channel.\n"
                        "These mechanisms solve different engineering constraints.\n"
                        "表格行: T1 | Dimension=Encoding | Value=128b/130b\n"
                        "17.1 Product impact\n"
                        "The product impact remains part of chapter 17."
                    ),
                )
            ],
        )

        self.assertEqual(
            ["17 Encoding and adaptation", "17 Encoding and adaptation / 17.1 Product impact"],
            [section.location for section in section_document(extraction)],
        )

    def test_nonconsecutive_real_chapter_with_sentence_title_remains_visible(self) -> None:
        """A legal chapter-number gap is structural when no local list chain follows."""

        extraction = ExtractionResult(
            pdf_path=Path("nonconsecutive-real-chapter.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "17 Overview\n"
                        "The overview chapter preserves the current operating model.\n"
                        "19. Receiver requirements are mandatory for independent devices.\n"
                        "The receiver chapter defines the independent device behavior."
                    ),
                )
            ],
        )

        self.assertEqual(
            [
                "17 Overview",
                "19. Receiver requirements are mandatory for independent devices.",
            ],
            [section.location for section in section_document(extraction)],
        )

    def test_long_wrapped_mid_list_item_can_start_without_terminal_punctuation(self) -> None:
        """A long out-of-sequence item can begin a list before its wrapped tail."""

        extraction = ExtractionResult(
            pdf_path=Path("wrapped-mid-list-start.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "24 Summary\n"
                        "2. Product evolution changes encoding, training, signaling, and error protection\n"
                        "across each supported generation.\n"
                        "3. Field debugging maps every symptom to a layer and an observable fact.\n"
                        "25 References\n"
                        "The references chapter lists the source material."
                    ),
                )
            ],
        )

        sections = section_document(extraction)

        self.assertEqual(
            ["24 Summary", "25 References"],
            [section.location for section in sections],
        )
        self.assertIn("2. Product evolution", sections[0].body)
        self.assertIn("3. Field debugging", sections[0].body)

    def test_wrapped_final_list_item_before_parent_table_stays_in_parent(self) -> None:
        """A parent table cannot turn a wrapped final list item into a chapter."""

        extraction = ExtractionResult(
            pdf_path=Path("wrapped-list-final-before-parent-table.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "24 Summary\n"
                        "If the reader remembers only three observations:\n"
                        "18. The first observation describes the complete architecture.\n"
                        "19. The second observation describes the protocol evolution.\n"
                        "20. The final observation maps every symptom to its layer and\n"
                        "the evidence required to isolate it.\n"
                        "表格行: T1 | Scenario=Link failure | Evidence=Trace and measurement"
                    ),
                )
            ],
        )

        sections = section_document(extraction)

        self.assertEqual(["24 Summary"], [section.location for section in sections])
        self.assertIn("20. The final observation", sections[0].body)
        self.assertIn("Evidence=Trace", sections[0].body)

    def test_numeric_reference_enumeration_does_not_become_a_section(self) -> None:
        """A list of source fragments is body text, not a high-number chapter."""

        extraction = ExtractionResult(
            pdf_path=Path("numeric-reference-enumeration.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "24 Summary\n"
                        "The implementation notes use source fragments below.\n"
                        "202、234、383-387 等片段\n"
                        "The appendix material remains traceable to the source document."
                    ),
                )
            ],
        )

        sections = section_document(extraction)

        self.assertEqual(["24 Summary"], [section.location for section in sections])
        self.assertIn("202、234、383-387 等片段", sections[0].body)

    def test_out_of_sequence_single_sentence_remains_a_visible_section(self) -> None:
        """One isolated integer sentence lacks enough evidence to become a prose list."""

        extraction = ExtractionResult(
            pdf_path=Path("single-mid-list-sentence.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "17 Signaling evolution\n"
                        "2. Link equalization selects compensation for the active channel.\n"
                        "18 Receiver validation\n"
                        "The receiver chapter defines the validation procedure."
                    ),
                )
            ],
        )

        sections = section_document(extraction)

        self.assertEqual(
            [
                "17 Signaling evolution",
                "2. Link equalization selects compensation for the active channel.",
                "18 Receiver validation",
            ],
            [section.location for section in sections],
        )

    def test_skipped_chapter_number_is_not_reclassified_as_a_prose_list(self) -> None:
        """A reserved chapter gap does not prove that the later chapter is a list item."""

        extraction = ExtractionResult(
            pdf_path=Path("reserved-chapter-gap.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "6 Current requirements\n"
                        "The current chapter preserves the active requirements.\n"
                        "8. Security architecture and operational requirements.\n"
                        "The security chapter defines a separate architecture."
                    ),
                )
            ],
        )

        self.assertEqual(
            [
                "6 Current requirements",
                "8. Security architecture and operational requirements.",
            ],
            [section.location for section in section_document(extraction)],
        )

    def test_new_numbering_part_restart_is_not_reclassified_as_a_prose_list(self) -> None:
        """An isolated chapter 1 restart remains structural without a local list chain."""

        extraction = ExtractionResult(
            pdf_path=Path("new-part-numbering-restart.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "6 Existing requirements\n"
                        "The previous part ends with the existing requirements.\n"
                        "1. Deployment architecture and operational requirements.\n"
                        "The new part introduces a separate deployment architecture."
                    ),
                )
            ],
        )

        self.assertEqual(
            [
                "6 Existing requirements",
                "1. Deployment architecture and operational requirements.",
            ],
            [section.location for section in section_document(extraction)],
        )

    def test_integer_requirement_bullets_stay_under_numbered_parent(self) -> None:
        """OIF requirement bullets must not replace the active clause hierarchy."""

        extraction = ExtractionResult(  # 复现 32.1 后接整数要求列表、再进入 32.2 的真实排版。
            pdf_path=Path("oif-requirement-list.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "32 CEI-224G-MR-PAM4 Medium Reach Interface\n"
                        "32.1 Requirements\n"
                        "1. Support serial baud rates from 72 Gsym/s to 116 Gsym/s.\n"
                        "3. Capable of driving up to 500 mm of PCB and one connector.\n"
                        "4. Shall support AC-coupled operation.\n"
                        "5. Shall allow multiple lanes.\n"
                        "6. Shall support hot plug.\n"
                        "32.2 General Requirements\n"
                        "32.2.1 Data Patterns\n"
                        "The transmitter shall use the declared pattern."
                    ),
                )
            ],
        )

        sections = section_document(extraction)  # 走完整上下文栈，验证列表不会把 32 章父路径替换为 6。
        locations = [section.location for section in sections]  # 用户报告中的定位必须保持 32.1/32.2 层级。
        requirements = next(section for section in sections if section.number_path[-1:] == ("32.1",))

        self.assertIn("6. Shall support hot plug.", requirements.body)  # 第 6 条仍是可比较正文。
        self.assertFalse(any(location.startswith("6. Shall support") for location in locations))  # 列表项不得成为父章节。
        self.assertIn(  # 后续真实章节必须恢复到 32.2，而不是挂到伪造的第 6 章下。
            "32 CEI-224G-MR-PAM4 Medium Reach Interface / 32.2 General Requirements / 32.2.1 Data Patterns",
            locations,
        )

    def test_title_case_steps_stay_inside_explicit_procedure_context(self) -> None:
        """Consecutive title-case steps under Test Procedure are body, not chapters."""

        extraction = ExtractionResult(  # 标题式步骤没有句点，必须依靠父级流程语义和连续编号判定。
            pdf_path=Path("title-case-procedure-steps.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "32 Interface\n"
                        "32.1 Test Procedure\n"
                        "1 Reset Device\n"
                        "2 Measure Output\n"
                        "32.2 Results\n"
                        "The measured output shall satisfy the declared limit."
                    ),
                )
            ],
        )

        sections = section_document(extraction)  # 走完整上下文栈，验证步骤与后续同级章节的交互。
        locations = [section.location for section in sections]  # 用户可见位置直接暴露伪顶层章节。
        procedure = next(  # 用结构编号定位父节，不依赖列表在结果中的物理索引。
            section for section in sections if section.number_path[-1:] == ("32.1",)
        )

        self.assertIn("1 Reset Device", procedure.body)  # 第一步保留为可比较正文。
        self.assertIn("2 Measure Output", procedure.body)  # 连续第二步也不能替换顶层上下文。
        self.assertFalse(any(location.startswith("1 Reset Device") for location in locations))  # 步骤不得成为第 1 章。
        self.assertFalse(any(location.startswith("2 Measure Output") for location in locations))  # 步骤不得成为第 2 章。
        self.assertIn(  # 步骤结束后的真实同级章节必须恢复到 32.2。
            "32 Interface / 32.2 Results",
            locations,
        )

    def test_top_level_test_procedure_keeps_its_second_numbered_step(self) -> None:
        """A top-level Procedure parent must outrank the apparent 1 -> 2 chapter sequence."""

        extraction = ExtractionResult(
            pdf_path=Path("top-level-procedure.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Test Procedure\n"
                        "1 Reset Device\n"
                        "2 Measure Output\n"
                        "3 Record the measured limit.\n"
                        "2 Results\n"
                        "The receiver result shall be reported."
                    ),
                )
            ],
        )

        sections = section_document(extraction)
        locations = [section.location for section in sections]
        procedure = next(section for section in sections if section.location == "1 Test Procedure")

        self.assertIn("1 Reset Device", procedure.body)
        self.assertIn("2 Measure Output", procedure.body)  # 旧逻辑把它误当成紧随第 1 章的第 2 章。
        self.assertIn("3 Record the measured limit.", procedure.body)
        self.assertIn("2 Results", locations)  # 名词性标题仍应结束流程并恢复真正的顶层章节。
        self.assertNotIn("2 Measure Output", locations)

    def test_top_level_procedure_does_not_consume_next_chapter_without_step_chain(self) -> None:
        """A procedure title alone cannot turn the next top-level chapter into a step."""

        extraction = ExtractionResult(
            pdf_path=Path("procedure-followed-by-power-management.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Test Procedure\n"
                        "The setup requirements are defined below.\n"
                        "2 Power Management\n"
                        "The receiver shall support the declared power state.\n"
                        "3 Results\n"
                        "The measured result shall be recorded."
                    ),
                )
            ],
        )

        sections = section_document(extraction)  # 公开章节器是 PDF 报告最终使用的真实入口。
        locations = [section.location for section in sections]

        self.assertEqual(
            ["1 Test Procedure", "2 Power Management", "3 Results"],
            locations,
        )  # 没有先出现第 1 步时，紧随的第 2 章应优先按顶层连续结构解释。
        self.assertNotIn("2 Power Management", sections[0].body)  # 真实章节标题不得被吞入 Procedure 正文。

    def test_integer_table_fragments_and_notes_stay_inside_mixed_appendix(self) -> None:
        """Numeric table rows and notes must not become parents of a following 31.C appendix."""

        extraction = ExtractionResult(  # 复现 31.B COM 表尾与下一页 31.C 标题之间的真实文字顺序。
            pdf_path=Path("oif-mixed-appendix-notes.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "31 CEI-224G-VSR-PAM4 Very Short Reach Interface\n"
                        "31.B Appendix - Channel Operating Margin (COM)\n"
                        "1 z1 b\n"
                        "2 z2 b\n"
                        "1 ≤ j ≤ d and d + 2 ≤ j ≤ N w (j) 0.7 —\n"
                        "NOTES:\n"
                        "1. C includes die bump, ESD and additional parasitic effects.\n"
                        "2. Not all combinations are allowed, See Table 31-13.\n"
                        "3. f is called f_HP_PZ in Appendix 31.D.3."
                    ),
                ),
                PageText(
                    page_number=2,
                    text=(
                        "31.C Appendix - Informative Host Transmitter output Electrical\n"
                        "Recommendations\n"
                        "31.C.1 Host Transmitter output test point\n"
                        "The output shall be measured at TP0a."
                    ),
                ),
            ],
        )

        sections = section_document(extraction)  # 真实混合编号路径应保持 31.B、31.C、31.C.1 三层事实。
        locations = [section.location for section in sections]  # 报告位置直接暴露表格/脚注是否污染章节栈。
        appendix_b = next(section for section in sections if section.number_path[-1:] == ("31.B",))

        self.assertIn("1 z1 b", appendix_b.body)  # 表格残片保留为正文证据，而不是被静默删除。
        self.assertIn("3. f is called f_HP_PZ", appendix_b.body)  # 脚注也应完整留在可审阅正文中。
        self.assertFalse(any(location.startswith(("1 z1 b", "2 z2 b", "3. f is called")) for location in locations))
        self.assertIn(  # 下一页真实附录必须回到 31.C 路径，而不是挂在脚注 3 下。
            "31 CEI-224G-VSR-PAM4 Very Short Reach Interface / 31.C Appendix - Informative Host Transmitter output Electrical / 31.C.1 Host Transmitter output test point",
            locations,
        )

    def test_use_cases_integer_heading_is_not_swallowed_as_step(self) -> None:
        """Noun-like top-level headings such as 2 Use Cases should remain sections."""

        extraction = ExtractionResult(  # 构造真实协议常见结构：Scope 后接 Use Cases 顶层章节。
            pdf_path=Path("use_cases_heading.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Scope overview text.\n"
                        "2 Use Cases\n"
                        "Use case overview text."
                    ),
                )
            ],
        )

        sections = section_document(extraction)  # 走章节器，验证整数标题不被列表项启发式吞掉。
        locations = [section.location for section in sections]  # 收集章节位置，便于断言层级。

        self.assertIn("1 Scope", locations)  # 第一章仍应正常存在。
        self.assertIn("2 Use Cases", locations)  # Use Cases 必须作为顶层章节出现。
        self.assertNotIn("2 Use Cases", sections[0].body)  # 不能把第二章标题并进第一章正文。

    def test_digit_leading_numeric_headings_are_detected(self) -> None:
        """Headings such as 2 400G Interfaces should not be mistaken for decimals."""

        heading_400g = detect_heading("2 400G Interfaces")  # 数字开头标题是高速协议常见章节名。
        heading_100g = detect_heading("2 100G Ethernet")  # Ethernet 章节同样可能以速率开头。
        table_value = detect_heading("2.93x10-4 ns/mm")  # 表格数值仍不应被当成章节。

        self.assertIsNotNone(heading_400g)  # 400G Interfaces 应识别为 heading。
        self.assertEqual("400G Interfaces", heading_400g.title)  # 标题文本应完整保留。
        self.assertIsNotNone(heading_100g)  # 100G Ethernet 应识别为 heading。
        self.assertIsNone(table_value)  # 小数/科学计数法表格值仍应过滤。

    def test_jitter_table_value_is_not_promoted_to_a_heading(self) -> None:
        """A split jitter-table value must remain table evidence, never a chapter."""

        # OIF 2024.532 的真实抽取残片：符号 T_JH4u 被拆成 ``03 JH``。
        # 如果它成为标题，会污染随后所有章节路径并触发连锁错配。
        self.assertIsNone(detect_heading("03 JH - 0.118 UI"))

    def test_appendix_sentence_continuation_is_not_promoted_to_a_heading(self) -> None:
        """A wrapped Appendix reference must remain prose, not create a false parent section."""

        self.assertIsNone(
            detect_heading(
                "Appendix 16.C.3.2, or a valid CEI signal, or transmitting the same pattern with a slightly"
            )
        )

    def test_appendix_subreference_fragment_is_not_promoted_to_a_heading(self) -> None:
        """A split ``See Appendix 16.D`` reference must not capture later sections."""

        self.assertIsNone(detect_heading("Appendix 16.D"))

    def test_scope_acronym_figure_label_is_not_promoted_to_a_heading(self) -> None:
        """A chart tick plus scope label must not become a top-level section."""

        self.assertIsNone(detect_heading("10 Scope CTLE"))

    def test_line_number_gutter_and_repeated_oif_footer_stay_out_of_sections(self) -> None:
        """Text-only number runs stay conservative while repeated OIF footers are removed."""

        page_lines = (
            "1\n"
            "1 Scope\n"
            "The interface shall support the defined reach.\n"
            "The limit holds. Optical Internetworking Forum - Clause 31: Example Interface www.oiforum.com This is a draft and not to be shared.\n"
            "5\n6\n7\n8\n9\n10\n11\n12\n13\n"
            "Optical Internetworking Forum - Clause 31: Example Interface 1\n"
            "www.oiforum.com\n"
            "This is a draft and not to be shared.\n"
            "with OIF BoD approval."
        )
        extraction = ExtractionResult(
            pdf_path=Path("oif_line_gutter.pdf"),
            pages=[
                PageText(page_number=1, text=page_lines),
                PageText(
                    page_number=2,
                    text=page_lines.replace("1 Scope", "The requirement continues."),
                ),
            ],
        )

        sections = section_document(extraction)

        locations = [section.location for section in sections]
        self.assertIn("1 Scope", locations)
        self.assertFalse(any(location.startswith("1 The requirement") for location in locations))  # 无坐标数字不得吸附正文造出伪章节。
        body = "\n".join(section.body for section in sections)
        self.assertIn("oiforum.com", body)  # 粘在正文中的页脚形文字缺少坐标证据，必须保留而非猜删。
        self.assertNotIn("with OIF BoD approval", body)  # 真正位于文本尾且跨页重复的完整页脚簇仍可移除。
        self.assertIn("The interface shall support the defined reach.", body)

    def test_text_only_number_runs_do_not_delete_trailing_requirement_values(self) -> None:
        """Without coordinates, a dense numeric list cannot prove every trailing number is a gutter."""

        extraction = ExtractionResult(
            pdf_path=Path("numbered_requirements.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n"
                        "The maximum channels are 12\n"
                        "Voltage shall be 3"
                    ),
                )
            ],
        )

        sections = section_document(extraction)

        body = "\n".join(section.body for section in sections)
        self.assertIn("The maximum channels are 12", body)
        self.assertIn("Voltage shall be 3", body)

    def test_ambiguous_margin_numbers_do_not_create_or_rename_sections(self) -> None:
        """Coordinate-proven edge candidates affect heading guesses, never stored text."""

        extraction = ExtractionResult(
            pdf_path=Path("ambiguous-margin-numbers.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 32 Interface Requirements\n"
                        "2 32.1 General Requirements\n"
                        "15 may extend the physical length of the connection.\n"
                        "16 signal paths shall preserve 100 ohm impedance."
                    ),
                    layout_risk=True,
                    ambiguous_line_number_sides=("left",),
                ),
                PageText(
                    page_number=2,
                    text=(
                        "32.2 Receiver Requirements 1\n"
                        "The receiver shall preserve the limit. 2"
                    ),
                    layout_risk=True,
                    ambiguous_line_number_sides=("right",),
                ),
            ],
        )

        sections = section_document(extraction)
        locations = [section.location for section in sections]
        combined_body = "\n".join(section.body for section in sections)

        self.assertIn("32 Interface Requirements / 32.1 General Requirements", locations)
        self.assertIn("32 Interface Requirements / 32.2 Receiver Requirements", locations)
        self.assertFalse(any(location.startswith(("15 may", "16 signal")) for location in locations))
        self.assertNotIn("32.2 Receiver Requirements 1", locations)  # 右侧候选不能污染真实标题身份。
        self.assertIn("15 may extend the physical length", combined_body)  # 原始数字和正文仍完整可比较。
        self.assertIn("16 signal paths shall preserve 100 ohm", combined_body)

    def test_ambiguous_right_margin_never_hides_a_real_heading_tail_change(self) -> None:
        """Page-level side evidence cannot authorize deleting a semantic title suffix."""

        shared_body = "The receiver shall preserve the same stable configuration. " * 8
        old = ExtractionResult(
            pdf_path=Path("old-heading-tail.pdf"),
            pages=[
                PageText(
                    1,
                    f"32.1 Transmitter Lane Configuration 1\n{shared_body}",
                    layout_risk=True,
                    ambiguous_line_number_sides=("right",),
                )
            ],
        )
        new = ExtractionResult(
            pdf_path=Path("new-heading-tail.pdf"),
            pages=[
                PageText(
                    1,
                    f"32.1 Transmitter Lane Configuration 2\n{shared_body}",
                    layout_risk=True,
                    ambiguous_line_number_sides=("right",),
                )
            ],
        )

        result = compare_extractions(old, new, DiffOptions())

        self.assertEqual(1, len(result.changes))
        self.assertEqual("modified", result.changes[0].change_type)
        visible = "\n".join(
            [
                *result.changes[0].removed_snippets,
                *result.changes[0].added_snippets,
                *(pair.old for pair in result.changes[0].replaced_snippets),
                *(pair.new for pair in result.changes[0].replaced_snippets),
            ]
        )
        self.assertIn("Configuration 1", visible)
        self.assertIn("Configuration 2", visible)

    def test_single_selected_page_preserves_unproven_metadata_cluster(self) -> None:
        """A single page cannot prove that title, revision, and date are furniture."""

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

        technical = next(section for section in sections if section.number_path == ("2.11.2",))
        self.assertIn("2.11.2 Overview of Calibration Steps at 16.0 GT/s", technical.location)
        self.assertIn("1. Connect the end of the cables", joined_bodies)
        self.assertIn("Test Descriptions", joined_bodies)  # 无页边坐标或跨页重复证据，不能删除普通短标题。
        self.assertIn("PCI Express Architecture PHY Test Specification", joined_bodies)
        self.assertIn("Revision 4.0", joined_bodies)
        self.assertIn("August 18, 2021", joined_bodies)

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

    def test_technical_case_changes_survive_cosmetic_normalization(self) -> None:
        """Unit spacing and terminal punctuation normalize; technical case stays visible."""

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

        self.assertEqual(1, len(result.changes))
        pair = result.changes[0].replaced_snippets[0]
        self.assertIn("Sj", pair.old)
        self.assertIn("SJ", pair.new)

        cosmetic_only = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old_spacing.pdf"),
                pages=[PageText(page_number=1, text="1 Scope\nEnable 100 MHz SJ and set 0.0ps, then save.")],
            ),
            ExtractionResult(
                pdf_path=Path("new_spacing.pdf"),
                pages=[PageText(page_number=1, text="1 Scope\nEnable 100 MHz SJ and set 0.0 ps, then save")],
            ),
            DiffOptions(),
        )
        self.assertEqual([], cosmetic_only.changes)

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

    def test_explicit_exponent_and_unit_glyph_variants_are_suppressed(self) -> None:
        """Only explicitly marked exponent and unit-rendering variants compare equal."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_numeric_artifact.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text="1 Scope\nCapture 2.0 X 10^+6 X 62.5ps = 125.0μs.",
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_numeric_artifact.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text="1 Scope\nCapture 2.0 × 10^6 × 62.5 ps = 125.0 μs.",
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
                        "Collect one hundred and five samples.\n"
                        "Collect a hundred packets.\n"
                        "Collect a million packets.\n"
                        "Collect a hundred thousand packets.\n"
                        "Collect a thousand million packets.\n"
                        "Collect one hundred thousand five hundred packets.\n"
                        "Collect one hundred million five hundred thousand packets.\n"
                        "Collect one hundred thousand and five hundred packets, then archive one million samples.\n"
                        "Count one hundred thousand and five hundred; one million packets follow.\n"
                        "Count one hundred thousand and five hundred: one million packets follow.\n"
                        "Count one hundred thousand and five hundred. One million packets follow.\n"
                        "Capture one and a half million packets.\n"
                        "Compare one million and two million packets.\n"
                        "Record one hundred and two hundred failures.\n"
                        "Count one, two, or three failures.\n"
                        "Count one hundred, two hundred, or three hundred failures.\n"
                        "Count one million, two million, or three million packets.\n"
                        "Count one and a half million, two million, or three million packets.\n"
                        "Count one hundred and a half million packets.\n"
                        "Count one thousand million packets.\n"
                        "Count one million billion packets.\n"
                        "Count one thousand million, two thousand million, or three thousand million packets.\n"
                        "Count one and a half thousand million packets.\n"
                        "Count one and a half thousand million, two and a half thousand million, or three and a half thousand million packets.\n"
                        "Count half a thousand million packets.\n"
                        "Count half a million, one million, or two million packets.\n"
                        "Span one million to two million packets.\n"
                        "Span one thousand million to two thousand million packets.\n"
                        "Span one and a half thousand million to two and a half thousand million packets.\n"
                        "Exercise one million and two million tests.\n"
                        "Check one hundred and two hundred requirements."
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
                        "Collect 105 samples.\n"
                        "Collect 100 packets.\n"
                        "Collect 1,000,000 packets.\n"
                        "Collect 100,000 packets.\n"
                        "Collect 1,000,000,000 packets.\n"
                        "Collect 100,500 packets.\n"
                        "Collect 100,500,000 packets.\n"
                        "Collect 100,500 packets, then archive 1,000,000 samples.\n"
                        "Count 100,500; 1,000,000 packets follow.\n"
                        "Count 100,500: 1,000,000 packets follow.\n"
                        "Count 100,500. 1,000,000 packets follow.\n"
                        "Capture 1.5 million packets.\n"
                        "Compare 1,000,000 and 2,000,000 packets.\n"
                        "Record 100 and 200 failures.\n"
                        "Count 1, 2, or 3 failures.\n"
                        "Count 100, 200, or 300 failures.\n"
                        "Count 1,000,000, 2,000,000, or 3,000,000 packets.\n"
                        "Count 1.5 million, 2 million, or 3 million packets.\n"
                        "Count 100.5 million packets.\n"
                        "Count 1 thousand million packets.\n"
                        "Count 1 million billion packets.\n"
                        "Count 1 thousand million, 2 thousand million, or 3 thousand million packets.\n"
                        "Count 1.5 thousand million packets.\n"
                        "Count 1.5 thousand million, 2.5 thousand million, or 3.5 thousand million packets.\n"
                        "Count 500,000,000 packets.\n"
                        "Count 500,000, 1,000,000, or 2,000,000 packets.\n"
                        "Span 1,000,000 to 2,000,000 packets.\n"
                        "Span 1 thousand million to 2 thousand million packets.\n"
                        "Span 1.5 thousand million to 2.5 thousand million packets.\n"
                        "Exercise 1,000,000 and 2,000,000 tests.\n"
                        "Check 100 and 200 requirements."
                    ),
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        self.assertEqual([], result.changes)

    def test_invalid_repeated_or_descending_scale_chain_stays_visible(self) -> None:
        """A valid prefix must not authorize an invalid trailing count scale."""

        for old_count, new_count in (
            ("one million thousand packets", "1 million thousand packets"),
            ("one million million packets", "1 million million packets"),
            (
                "one and a half million thousand packets",
                "1.5 million thousand packets",
            ),
            (
                "one million and a half million packets",
                "1,000,000,500,000 packets",
            ),
        ):
            with self.subTest(old_count=old_count):
                old_extraction = ExtractionResult(
                    pdf_path=Path("old_invalid_scale_chain.pdf"),
                    pages=[PageText(page_number=1, text=f"1 Scope\nCount {old_count}.")],
                )
                new_extraction = ExtractionResult(
                    pdf_path=Path("new_invalid_scale_chain.pdf"),
                    pages=[PageText(page_number=1, text=f"1 Scope\nCount {new_count}.")],
                )

                result = compare_extractions(
                    old_extraction,
                    new_extraction,
                    DiffOptions(),
                )

                self.assertTrue(result.changes)

    def test_equivalent_count_spelling_does_not_break_a_changed_section_match(self) -> None:
        """One real value change must not expose an adjacent equivalent count line."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_count_context.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Collect one hundred thousand and five hundred packets, "
                        "then archive one million samples.\n"
                        "The limit is 120 mVrms."
                    ),
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_count_context.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Collect 100,500 packets, then archive 1,000,000 samples.\n"
                        "The limit is 121 mVrms."
                    ),
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        self.assertEqual(1, len(result.changes))
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, Path(temp_dir), DiffOptions())
            for suffix in (".html", ".md", ".txt", ".csv"):
                report = next(path for path in paths.values() if path.suffix == suffix)
                reader = report.read_text(encoding="utf-8-sig")
                if suffix == ".html":
                    self.assertIn(">120</mark> mVrms", reader)
                    self.assertIn(">121</mark> mVrms", reader)
                else:
                    self.assertIn("120 mVrms", reader)
                    self.assertIn("121 mVrms", reader)
                self.assertNotIn("one hundred thousand and five hundred", reader)

    def test_large_scaled_integer_changes_do_not_round_to_one_key(self) -> None:
        """Scaled counts beyond Decimal's default precision must remain distinct."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_large_count.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Count 12345678901234567890123456781 million packets."
                    ),
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_large_count.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Count 12345678901234567890123456782 million packets."
                    ),
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        self.assertTrue(result.changes)

    def test_number_word_change_with_an_added_comma_remains_visible(self) -> None:
        """Count spelling may normalize, but an observed numeric separator remains evidence."""

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

        self.assertTrue(result.changes)

    def test_zero_scaled_count_change_remains_visible_in_reader_reports(self) -> None:
        """Explicit zero is never treated as an omitted one before a scale."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_zero_scaled_count.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nCapture zero million packets.")],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_one_scaled_count.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nCapture one million packets.")],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        self.assertTrue(result.changes)
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, Path(temp_dir), DiffOptions())
            for key in ("html", "markdown", "text", "csv"):
                surface = paths[key].read_text(encoding="utf-8")
                with self.subTest(surface=key):
                    self.assertIn("zero million", surface)
                    self.assertIn("one million", surface)

    def test_large_half_count_change_remains_visible_in_reader_reports(self) -> None:
        """Half-unit cardinal keys retain exact precision above six digits."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_large_half_count.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text="1 Scope\nCapture one million and a half cycles.",
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_large_half_count.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text="1 Scope\nCapture one million one and a half cycles.",
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        self.assertTrue(result.changes)
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, Path(temp_dir), DiffOptions())
            for key in ("html", "markdown", "text", "csv"):
                surface = paths[key].read_text(encoding="utf-8")
                with self.subTest(surface=key):
                    self.assertIn("one million and a half", surface)
                    self.assertIn("one million one and a half", surface)

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

    def test_decimal_like_line_break_is_preserved_without_layout_evidence(self) -> None:
        """Text alone cannot prove that a sentence break is a split decimal."""

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

        self.assertTrue(result.changes)

    def test_hyphenated_pdf_line_wrap_is_preserved_without_layout_evidence(self) -> None:
        """Plain extracted text cannot prove that a visible line-end hyphen is soft."""

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

        self.assertTrue(result.changes)

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

    def test_semantic_semicolon_is_not_lost_when_sentence_boundaries_change(self) -> None:
        """A command separator remains visible even when it changes unit splitting."""

        shared = (
            "The implementation shall retain traceable requirements and review records. "
        ) * 12
        old_extraction = ExtractionResult(
            pdf_path=Path("old-semicolon-boundary.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=f"1 Scope\n{shared}\nPerform reset; continue.",
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new-semicolon-boundary.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=f"1 Scope\n{shared}\nPerform reset continue.",
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        self.assertEqual(1, len(result.changes))
        self.assertEqual("modified", result.changes[0].change_type)
        evidence = "\n".join(
            [
                *result.changes[0].audit_added_snippets,
                *result.changes[0].audit_removed_snippets,
                *(
                    f"{pair.old}\n{pair.new}"
                    for pair in result.changes[0].audit_replaced_snippets
                ),
            ]
        )
        self.assertIn("reset;", evidence)

    def test_unicode_adjacent_comparison_operator_changes_remain_visible(self) -> None:
        """CJK text directly touching a comparison operator remains semantic."""

        for old_body, new_body in (
            ("模式!=关闭", "模式=关闭"),
            ("参数!=20", "参数=20"),
            ("模式!=“关闭”", "模式=“关闭”"),
            ('"mode"!="off"', '"mode"="off"'),
            ("模式! =关闭", "模式=关闭"),
            ("模式!\n=关闭", "模式\n=关闭"),
            ("模式！=关闭", "模式=关闭"),
            ("模式！＝关闭", "模式＝关闭"),
            ("模式==关闭", "模式=关闭"),
            ("模式= =关闭", "模式=关闭"),
            ("模式＝＝关闭", "模式＝关闭"),
            ("参数<=20", "参数=20"),
            ("参数< =20", "参数=20"),
            ("参数＜＝20", "参数＝20"),
            ("参数>=20", "参数=20"),
            ("参数>\n=20", "参数\n=20"),
            ("参数＞=20", "参数=20"),
            ("模式≠关闭", "模式=关闭"),
        ):
            with self.subTest(old_body=old_body):
                result = compare_extractions(
                    ExtractionResult(
                        pdf_path=Path("old-unicode-operator.pdf"),
                        pages=[PageText(page_number=1, text=f"1 配置\n{old_body}")],
                    ),
                    ExtractionResult(
                        pdf_path=Path("new-unicode-operator.pdf"),
                        pages=[PageText(page_number=1, text=f"1 配置\n{new_body}")],
                    ),
                    DiffOptions(),
                )

                self.assertTrue(result.changes)

    def test_comparison_operator_width_and_pdf_spacing_are_presentation_only(self) -> None:
        """Equivalent comparison operators survive width and extraction spacing."""

        for old_body, new_body in (
            ("模式！＝关闭", "模式!=关闭"),
            ("模式！ =关闭", "模式!=关闭"),
            ("模式＝＝关闭", "模式==关闭"),
            ("参数＜＝20", "参数<=20"),
            ("参数＞\n＝20", "参数>=20"),
        ):
            with self.subTest(old_body=old_body):
                result = compare_extractions(
                    ExtractionResult(
                        pdf_path=Path("old-operator-width.pdf"),
                        pages=[PageText(page_number=1, text=f"1 配置\n{old_body}")],
                    ),
                    ExtractionResult(
                        pdf_path=Path("new-operator-width.pdf"),
                        pages=[PageText(page_number=1, text=f"1 配置\n{new_body}")],
                    ),
                    DiffOptions(),
                )

                self.assertFalse(result.changes)

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
        self.assertEqual(2, result.changes[0].omitted_snippet_count)  # 内部逗号变化也作为可观察差异计入省略数。
        shown = "\n".join(
            pair.old + "\n" + pair.new for pair in result.changes[0].replaced_snippets
        )
        self.assertIn("1.0 ps", shown)
        self.assertIn("10 ps", shown)
        self.assertNotIn("Formatting text uses commas", shown)

    def test_unequal_replace_block_pairs_related_units(self) -> None:
        """Unequal replace blocks should not cross-pair unrelated changed sentences."""

        shared_context = (
            "The receiver validation sequence remains unchanged for every supported lane. " * 4
        )  # 章节配对必须先过真实文本门槛，避免本片段级测试依赖同号同题捷径。
        old_extraction = ExtractionResult(
            pdf_path=Path("old_unequal_block.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        f"{shared_context}\n"
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
                        f"{shared_context}\n"
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
                        f"{shared_context}\n"
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

    def test_equal_length_replace_block_does_not_pair_unrelated_units(self) -> None:
        """Equal-size blocks need the same semantic pairing gate as unequal blocks."""

        shared_context = (
            "The compliance setup and traceable review procedure remain unchanged. " * 8
        )  # 用真实共同正文证明章节身份；两条互不相关的变化仍单独检验片段配对门。
        old_extraction = ExtractionResult(
            pdf_path=Path("old_equal_block.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        f"{shared_context}\n"
                        "Alpha receivers shall retain the original calibration policy.\n"
                        "Beta transmitters shall report the original measurement."
                    ),
                )
            ],
            total_pages=1,
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_equal_block.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        f"{shared_context}\n"
                        "Gamma encoders must use a completely separate framing rule.\n"
                        "Delta decoders must publish a different training sequence."
                    ),
                )
            ],
            total_pages=1,
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        change = result.changes[0]

        self.assertFalse(change.replaced_snippets)  # 没有共同语义锚点的句子不能伪装成左右替换。
        removed_text = "\n".join(change.removed_snippets)
        added_text = "\n".join(change.added_snippets)
        self.assertIn("Alpha receivers", removed_text)
        self.assertIn("Beta transmitters", removed_text)
        self.assertIn("Gamma encoders", added_text)
        self.assertIn("Delta decoders", added_text)

    def test_legacy_unchanged_threshold_does_not_hide_word_inflections(self) -> None:
        """The compatibility option is ignored because word inflections can alter requirements."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_spelling.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "The receiver supports calibration and measurement reporting for every compliant implementation."
                    ),
                )
            ],
            total_pages=1,
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_spelling.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "The receiver support calibration and measurement reporting for every compliant implementation."
                    ),
                )
            ],
            total_pages=1,
        )

        relaxed = compare_extractions(
            old_extraction,
            new_extraction,
            DiffOptions(unchanged_similarity=0.95),
        )
        strict = compare_extractions(
            old_extraction,
            new_extraction,
            DiffOptions(unchanged_similarity=0.9999),
        )

        self.assertEqual(1, len(relaxed.changes))
        self.assertEqual(1, len(strict.changes))

    def test_unchanged_threshold_never_hides_numeric_change(self) -> None:
        """Even a relaxed unchanged threshold must preserve protocol values."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_numeric.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nUse Np = 53 samples for the calculation.")],
            total_pages=1,
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_numeric.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nUse Np = 60 samples for the calculation.")],
            total_pages=1,
        )

        result = compare_extractions(
            old_extraction,
            new_extraction,
            DiffOptions(unchanged_similarity=0.5),
        )

        self.assertEqual(1, len(result.changes))
        self.assertIn("53", result.changes[0].replaced_snippets[0].old)
        self.assertIn("60", result.changes[0].replaced_snippets[0].new)

    def test_unchanged_threshold_never_hides_negation_prefixes(self) -> None:
        """Character-similar words with a negating prefix are semantic changes."""

        for old_word, new_word in (
            ("supported", "unsupported"),
            ("available", "unavailable"),
            ("allowed", "disallowed"),
            ("capable", "incapable"),
        ):
            with self.subTest(old_word=old_word, new_word=new_word):
                old_extraction = ExtractionResult(
                    pdf_path=Path(f"old_{old_word}.pdf"),
                    pages=[
                        PageText(
                            page_number=1,
                            text=(
                                "1 Scope\n"
                                f"The receiver is {old_word} in every operating mode described by this implementation."
                            ),
                        )
                    ],
                    total_pages=1,
                )
                new_extraction = ExtractionResult(
                    pdf_path=Path(f"new_{new_word}.pdf"),
                    pages=[
                        PageText(
                            page_number=1,
                            text=(
                                "1 Scope\n"
                                f"The receiver is {new_word} in every operating mode described by this implementation."
                            ),
                        )
                    ],
                    total_pages=1,
                )

                result = compare_extractions(old_extraction, new_extraction, DiffOptions())

                self.assertEqual(1, len(result.changes), (old_word, new_word))

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

    def test_orphan_pdf_fragments_do_not_pair_with_complete_inserted_sentence(self) -> None:
        """Single-character extraction residue should not replace a full sentence."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_orphan_fragment.pdf"),  # 旧 PDF 模拟 OIF 样本里的 `p` 孤立残片。
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Tests1,2,3,4 differ in the value of the device package model transmission line length z .\n"
                        "p\n"
                        "An informative package model overview can be found in IEEE Std 802.3dj [2] Clause 178A."
                    ),
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_orphan_fragment.pdf"),  # 新 PDF 多了一句完整的 channel compliance 说明。
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Tests 1, 2, 3, 4 differ in the value of the device package model transmission line length z .\n"
                        "For channel compliance testing, the device package model for the class of\n"
                        "p\n"
                        "transmitter package "
                        "claimed by the transmitter vendor should be used.\n"
                        "An informative package model overview can be found in IEEE Std 802.3dj [2] Clause 178A."
                    ),
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())  # 走完整比较，验证真实报告片段形态。
        change = result.changes[0]  # 该合成样本只有一个章节变化。
        pair_text = "\n".join(f"{pair.old}\n{pair.new}" for pair in change.replaced_snippets)  # 汇总左右替换对。
        added_text = "\n".join(change.added_snippets)  # 汇总新增片段。

        self.assertNotIn("\np\n", f"\n{pair_text}\n")  # 孤立 `p` 不能再显示成旧协议侧片段。
        self.assertNotIn("Tests1", pair_text)  # `Tests1,2` 和 `Tests 1, 2` 只是 OCR 空格差异。
        self.assertIn("For channel compliance testing", added_text)  # 新增句子应作为完整句展示。
        self.assertIn("class of p transmitter package", added_text)  # 没有几何证据时，p 可能是变量或合法词，必须保留。
        self.assertIn("should be used.", added_text)  # 新增片段不能被截成半句。

    def test_unproven_table_and_equation_fragments_remain_visible(self) -> None:
        """Short technical text stays visible unless structured or layout evidence proves duplication."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_fragmentary_table_refs.pdf"),  # 旧侧模拟从表格抽出的短行。
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Conversion (32-6)\n"
                        "Interference Tolerance Table 32-10\n"
                        "Jitter Tolerance Table 32-11\n"
                        "Table 32-10.\n"
                        "Block Error Ratio, Note 3 3.2e-13 3.2e-13\n"
                        "The receiver shall use BERadded=1e-4 for tolerance testing."
                    ),
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_fragmentary_table_refs.pdf"),  # 新侧短表格项改变，但正文句也有真实变化。
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Conversion (32-8)\n"
                        "Interference Tolerance Table 32-8\n"
                        "Jitter Tolerance Table 32-9\n"
                        "Table 32-8.\n"
                        "Block Error Ratio, Note 3 3.2×10–13 3.2×10–13\n"
                        "The receiver shall use BER =1e-4 for tolerance testing."
                    ),
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())  # 运行真实全局噪声过滤。
        snippets = "\n".join(
            snippet
            for change in result.changes
            for snippet in (
                change.added_snippets
                + change.removed_snippets
                + [pair.old for pair in change.replaced_snippets]
                + [pair.new for pair in change.replaced_snippets]
            )
        )  # 汇总最终会出现在正文卡片中的片段。

        self.assertIn("Conversion (32-6)", snippets)
        self.assertIn("Interference Tolerance Table 32-10", snippets)
        self.assertIn("Jitter Tolerance Table 32-11", snippets)
        for opaque_text in (
            "03", "-1 -1/3 1/3 1", "UNIT", "Note 2D", "FFE_Post", "fx bx FFE_Post",
            "| MAX=1000 | UNIT=mVppd", "Baud Rate R_Baud 72 116 Gsym/s", "SeFe Section", "NOTES: D",
        ):
            self.assertFalse(_is_global_noise_snippet(opaque_text), opaque_text)
        self.assertIn("The receiver shall use BERadded=1e-4", snippets)  # 真正文句仍保留。
        self.assertIn("The receiver shall use BER =1e-4", snippets)  # 新正文句也必须完整可见。

    def test_unproven_running_metadata_is_preserved_in_text_cards(self) -> None:
        """Different single-page metadata clusters remain observable without layout evidence."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_pcie_headers.pdf"),  # 旧侧模拟 PCIe 页眉页脚混入正文抽取结果。
            pages=[
                PageText(
                    page_number=36,
                    text=(
                        "1 Scope\n"
                        "PCI Express Architecture PHY Test Specification | 36\n"
                        "Revision 4.0, Version 1.2\n"
                        "August 18, 2021\n"
                        "The transmitter shall repeat steps 16 and 17 using the computed Sj value."
                    ),
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_pcie_headers.pdf"),  # 新侧页码、版本和日期都不同，但不是正文变化。
            pages=[
                PageText(
                    page_number=78,
                    text=(
                        "1 Scope\n"
                        "PCI Express Architecture PHY Test Specification | 78\n"
                        "Revision 6.0\n"
                        "April 22, 2026\n"
                        "The transmitter shall repeat steps 19 and 20 using the computed SJ value."
                    ),
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())  # 运行完整片段生成和全局噪声过滤。
        snippets = "\n".join(
            snippet
            for change in result.changes
            for snippet in (
                change.added_snippets
                + change.removed_snippets
                + [pair.old for pair in change.replaced_snippets]
                + [pair.new for pair in change.replaced_snippets]
            )
        )  # 汇总用户最终会在正文差异卡中看到的文字。

        self.assertIn("PCI Express Architecture PHY Test Specification", snippets)
        self.assertIn("Revision 4.0, Version 1.2", snippets)
        self.assertIn("Revision 6.0", snippets)
        self.assertIn("August 18, 2021", snippets)
        self.assertIn("April 22, 2026", snippets)
        self.assertIn("steps 16 and 17", snippets)  # 真正的步骤号变化仍要保留。
        self.assertIn("steps 19 and 20", snippets)  # 新侧步骤号变化同样保留。
        self.assertFalse(
            _is_global_noise_snippet("PCI Express Architecture PHY Test Specification | 78")
        )  # 脱离页边和相邻版本/日期语境后，单独一行不足以证明是页面附属信息。
        self.assertFalse(_is_global_noise_snippet("Revision 6.0"))  # 没有 running header 语境时可能是正文修订历史。
        self.assertFalse(_is_global_noise_snippet("April 22, 2026"))  # 没有 running header 语境时可能是正文日期。
        self.assertFalse(
            _is_global_noise_snippet("The 16 GT/s CLB does not support revision 4.0 of the Add-in Card test.")
        )  # 正文里的 revision 引用不能被误删。

    def test_standalone_revision_and_date_lines_can_remain_body_diffs(self) -> None:
        """Revision/date lines should only be suppressed when tied to PCIe headers."""

        body_units = _merge_wrapped_lines("Revision 6.0\nApril 22, 2026")  # 没有页眉语境时按正文保留。
        footer_units = _merge_wrapped_lines(
            "PCI Express Architecture PHY Test Specification | 78\nRevision 6.0\nApril 22, 2026"
        )  # 与 PCIe running header 相邻时按页脚删除。
        cleaned_body = _clean_extracted_page_text("Revision 6.0\nApril 22, 2026")  # 抽取层也不能单独删正文修订历史。
        cleaned_footer = _clean_extracted_page_text(
            "PCI Express Architecture PHY Test Specification | 78\nRevision 6.0\nApril 22, 2026"
        )  # 抽取层应删除完整页眉簇。
        old_revision = ExtractionResult(
            pdf_path=Path("old_revision_history.pdf"),  # 旧侧模拟正文修订历史行。
            pages=[
                PageText(
                    page_number=1,
                    text="1 Scope\nCurrent revision details follow.\nRevision 5.0\nMarch 1, 2025",
                )
            ],
        )
        new_revision = ExtractionResult(
            pdf_path=Path("new_revision_history.pdf"),  # 新侧模拟正文修订历史行更新。
            pages=[
                PageText(
                    page_number=1,
                    text="1 Scope\nCurrent revision details follow.\nRevision 6.0\nApril 22, 2026",
                )
            ],
        )
        result = compare_extractions(old_revision, new_revision, DiffOptions())  # 覆盖 section_document 到 compare 的最终路径。
        snippets = "\n".join(
            snippet
            for change in result.changes
            for snippet in (
                change.added_snippets
                + change.removed_snippets
                + [pair.old for pair in change.replaced_snippets]
                + [pair.new for pair in change.replaced_snippets]
            )
        )  # 汇总报告最终可见片段，确认 sectioning 没有提前删除。

        self.assertIn("Revision 6.0", "\n".join(body_units))  # 比较层保留独立修订正文。
        self.assertIn("April 22, 2026", "\n".join(body_units))  # 比较层保留独立日期正文。
        self.assertIn("Revision 6.0", "\n".join(footer_units))
        self.assertIn("April 22, 2026", "\n".join(footer_units))
        self.assertIn("Revision 6.0", cleaned_body)  # 抽取层保留独立修订正文。
        self.assertIn("April 22, 2026", cleaned_body)  # 抽取层保留独立日期正文。
        self.assertIn("PCI Express Architecture PHY Test Specification", cleaned_footer)
        self.assertIn("Revision 6.0", cleaned_footer)
        self.assertIn("Revision 5.0", snippets)  # 最终报告路径保留旧修订正文。
        self.assertIn("Revision 6.0", snippets)  # 最终报告路径保留新修订正文。
        self.assertIn("March 1, 2025", snippets)  # 最终报告路径保留旧日期正文。
        self.assertIn("April 22, 2026", snippets)  # 最终报告路径保留新日期正文。

    def test_embedded_running_header_like_text_is_preserved_without_layout_evidence(self) -> None:
        """A header-shaped phrase glued into prose cannot be removed safely from text alone."""

        shared_context = (
            "The calibration setup shall retain the same traceable measurement path. " * 6
        )  # 共同正文让章节以实际分数配对，测试仍只约束嵌入文本不能被降噪吞掉。
        old_extraction = ExtractionResult(
            pdf_path=Path("old_embedded_pcie_header.pdf"),  # 旧侧是没有页脚污染的干净步骤句。
            pages=[
                PageText(
                    page_number=37,
                    text=(
                        "1 Scope\n"
                        f"{shared_context}\n"
                        "Use Generator -> SMP Cable -> Variable ISI channel -> Oscilloscope "
                        "and follow Appendix C for calibration."
                    ),
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_embedded_pcie_header.pdf"),  # 新侧模拟页眉页脚被插进同一行中间。
            pages=[
                PageText(
                    page_number=80,
                    text=(
                        "1 Scope\n"
                        f"{shared_context}\n"
                        "Use Generator -> SMP Cable- PCI Express Architecture PHY Test Specification | 80 "
                        "Revision 6.0 April 22, 2026 >Variable ISI channel -> Oscilloscope "
                        "and follow Appendix A for calibration."
                    ),
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())  # 嵌入噪声应在报告片段前被剔除。
        snippets = "\n".join(
            snippet
            for change in result.changes
            for pair in change.replaced_snippets
            for snippet in (pair.old, pair.new)
        )  # 只汇总替换片段，验证句中页眉清理不会影响真实 Appendix 差异。

        self.assertIn("PCI Express Architecture PHY Test Specification", snippets)
        self.assertIn("Revision 6.0", snippets)
        self.assertIn("April 22, 2026", snippets)
        self.assertIn("Appendix C", snippets)  # 旧侧真实附录引用仍保留。
        self.assertIn("Appendix A", snippets)  # 新侧真实附录引用仍保留。

    def test_inline_highlight_keeps_display_normalization_without_domain_aliases(self) -> None:
        """Display-only variants stay quiet while named terminology remains visible."""

        old_html, new_html = _inline_diff_html(
            "computed SJ through Generator→SMP Adaptor at 30dB before step 16 and P5 at E-12.",
            "computed SJ through Generator->SMP Adapter at 30 dB before step 19 and Preset 5 at 1e-12.",
        )  # 业务术语/标识符差异需可见，科学计数法仍只做显示归一化。
        display_old_html, display_new_html = _inline_diff_html(
            "computed SJ through Generator→SMP at 30dB and E-12.",
            "computed SJ through Generator->SMP at 30 dB and 1e-12.",
        )
        amp_old_html, amp_new_html = _inline_diff_html(
            "Eye Width & Extrapolated Eye Height",
            "Eye Width and Extrapolated Eye Height",
        )  # & 与 and 是同义连接符，不应在报告里制造高亮。

        self.assertNotIn("<mark", display_old_html)
        self.assertNotIn("<mark", display_new_html)
        self.assertIn('<mark class="del">Adaptor</mark>', old_html)
        self.assertIn('<mark class="ins">Adapter</mark>', new_html)
        self.assertIn('<mark class="del">P5</mark>', old_html)
        self.assertIn('<mark class="ins">Preset</mark>', new_html)
        self.assertIn('<mark class="ins">5</mark>', new_html)
        self.assertNotIn("<mark", old_html.rsplit(" at ", 1)[1])
        self.assertNotIn("<mark", new_html.rsplit(" at ", 1)[1])
        self.assertNotIn("<mark", amp_old_html)  # 旧侧 & 不应被标成删除。
        self.assertNotIn("<mark", amp_new_html)  # 新侧 and 不应被标成新增。
        self.assertIn('<mark class="del">16</mark>', old_html)  # 旧侧真实步骤号变化仍需标红。
        self.assertIn('<mark class="ins">19</mark>', new_html)  # 新侧真实步骤号变化仍需标绿。

    def test_inline_highlight_preserves_technical_identifier_case(self) -> None:
        """A reported identifier-case change must also be visible inside the card."""

        old_html, new_html = _inline_diff_html(
            "Select MODE_FAST before calibration.",
            "Select mode_fast before calibration.",
        )
        prose_old_html, prose_new_html = _inline_diff_html(
            "Scope remains unchanged.",
            "scope remains unchanged.",
        )

        self.assertIn('<mark class="del">MODE_FAST</mark>', old_html)
        self.assertIn('<mark class="ins">mode_fast</mark>', new_html)
        self.assertNotIn("<mark", prose_old_html)
        self.assertNotIn("<mark", prose_new_html)

    def test_inline_highlight_preserves_math_operator_changes(self) -> None:
        """Formula operators reported by the core must be highlighted in HTML."""

        old_html, new_html = _inline_diff_html(
            "The output shall use A + B.",
            "The output shall use A - B.",
        )

        self.assertIn('<mark class="del">+</mark>', old_html)
        self.assertIn('<mark class="ins">-</mark>', new_html)

    def test_report_classifies_arbitrary_wording_changes_without_term_lists(self) -> None:
        """Report categories should describe generic wording changes, not known samples."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_generic_term.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nThe module shall use the cobalt connector.")],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_generic_term.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nThe module shall use the amber connector.")],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, Path(temp_dir), DiffOptions())
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        self.assertIn("术语/文字", payload["changes"][0]["change_nature"])

    def test_dash_joined_list_clauses_split_into_review_units(self) -> None:
        """A dash-glued list sentence should split before the next If clause."""

        units = _split_line_preserving_numbers(
            "a) All waveform outliers are removed from the average ― If the Eye Width is less than 1.0 ps."
        )  # PCIe 列表有时把 a) 说明和后续 If 子句粘在一行。

        self.assertEqual("a) All waveform outliers are removed from the average", units[0])  # 第一条列表说明独立成句。
        self.assertEqual("If the Eye Width is less than 1.0 ps.", units[1])  # 后续 If 子句可与新版 b. 正确配对。

    def test_leading_table_like_prefix_is_preserved_without_structure_evidence(self) -> None:
        """A table-shaped prefix remains reviewable when no table boundary proves duplication."""

        shared_context = (
            "The receiver compliance method and measurement conditions remain unchanged. " * 4
        )  # 以共同正文满足章节门槛，使本测试继续覆盖表形前缀的片段保留语义。
        old_extraction = ExtractionResult(
            pdf_path=Path("old_header_prefix.pdf"),  # 旧侧模拟表头残片粘到 ERL 句前。
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        f"{shared_context}\n"
                        "UNIT Baud Rate R_Baud 72 116 Gsym/s See Section "
                        "Effective return loss (ERL) 11.3 dB"
                    ),
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_header_prefix.pdf"),  # 新侧只有真正的 ERL 句。
            pages=[
                PageText(
                    page_number=1,
                    text=f"1 Scope\n{shared_context}\nEffective return loss (ERL) 11.3 dB",
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())  # 表头前缀应被 key 归一化吃掉。

        self.assertEqual(1, len(result.changes))
        self.assertIn("R_Baud", result.changes[0].replaced_snippets[0].old)

    def test_embedded_identifier_text_is_preserved_without_structure_evidence(self) -> None:
        """Opaque identifiers may be real content and cannot be normalized away by sample shape."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_embedded_table_id.pdf"),  # 旧侧是干净的正文引用。
            pages=[PageText(page_number=1, text="1 Scope\nN is set to the value of N in Table 32-1.")],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_embedded_table_id.pdf"),  # 新侧模拟 `fx bx FFE_post` 被插进 Table 引用前。
            pages=[
                PageText(
                    page_number=1,
                    text="1 Scope\nN is set to the value of N in fx bx FFE_post Table 32-1.",
                )
            ],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())  # 嵌入残片应在 key 层归一化。

        self.assertEqual(1, len(result.changes))
        self.assertIn("fx bx FFE_post", result.changes[0].replaced_snippets[0].new)

    def test_protocol_numeric_reference_wins_a_one_snippet_limit(self) -> None:
        """A strict snippet limit prioritizes observable numeric protocol changes."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_noise_limit.pdf"),  # 旧侧前两个变化都是表格/公式短碎片。
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Conversion (32-6)\n"
                        "Table 32-10.\n"
                        "The receiver shall enable BLER tolerance testing."
                    ),
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_noise_limit.pdf"),  # 新侧真正需要展示的是 shall 句子。
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "Conversion (32-8)\n"
                        "Table 32-8.\n"
                        "The receiver shall disable BLER tolerance testing."
                    ),
                )
            ],
        )

        result = compare_extractions(
            old_extraction,
            new_extraction,
            DiffOptions(max_snippets_per_section=1),
        )  # max_snippets 很小时也要先过滤噪声再选片段。
        snippets = "\n".join(
            pair.old + "\n" + pair.new
            for change in result.changes
            for pair in change.replaced_snippets
        )  # 汇总最终可见的替换片段。

        self.assertIn("Conversion (32-6)", snippets)
        self.assertIn("Conversion (32-8)", snippets)
        self.assertNotIn("receiver shall enable", snippets)  # 其余变化由省略计数明确提示，不伪装成无变化。
        self.assertGreater(sum(change.omitted_snippet_count for change in result.changes), 0)

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

        self.assertIn('<mark class="del">Rj</mark>', report_html)
        self.assertIn('<mark class="ins">RJ</mark>', report_html)
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

    def test_html_inline_highlight_deemphasizes_coordinated_number_words(self) -> None:
        """Every endpoint inherits the final observed count-noun context."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_coordinated_number_highlight.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\nCapture one million and two million packets, "
                        "then save them. The receiver records this result for "
                        "the compliance review."
                    ),
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_coordinated_number_highlight.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\nCapture 1,000,000 and 2,000,000 packets, "
                        "then archive them. The receiver records this result for "
                        "the compliance review."
                    ),
                )
            ],
        )
        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, Path(temp_dir), DiffOptions())
            report_html = outputs["html"].read_text(encoding="utf-8")

        self.assertNotIn('<mark class="del">one million</mark>', report_html)
        self.assertNotIn('<mark class="del">two million</mark>', report_html)
        self.assertNotIn('<mark class="ins">1,000,000</mark>', report_html)
        self.assertNotIn('<mark class="ins">2,000,000</mark>', report_html)
        self.assertIn('<mark class="del">save</mark>', report_html)
        self.assertIn('<mark class="ins">archive</mark>', report_html)

    def test_html_inline_highlight_deemphasizes_range_and_comma_list_counts(
        self,
    ) -> None:
        """Range and comma-list endpoints inherit one final count noun safely."""

        cases = (
            (
                "one million to two million packets",
                "1,000,000 to 2,000,000 packets",
                ("one million", "two million"),
                ("1,000,000", "2,000,000"),
            ),
            (
                "one, two, or three failures",
                "1, 2, or 3 failures",
                ("one", "two", "three"),
                ("1", "2", "3"),
            ),
            (
                "one hundred, two hundred, or three hundred failures",
                "100, 200, or 300 failures",
                ("one hundred", "two hundred", "three hundred"),
                ("100", "200", "300"),
            ),
            (
                "one and a half million, two million, or three million packets",
                "1.5 million, 2 million, or 3 million packets",
                ("one and a half", "two", "three"),
                ("1.5", "2", "3"),
            ),
            (
                "half a million, one million, or two million packets",
                "500,000, 1,000,000, or 2,000,000 packets",
                ("half a", "one", "two"),
                ("500,000", "1,000,000", "2,000,000"),
            ),
            (
                "one thousand million, two thousand million, or three thousand million packets",
                "1 thousand million, 2 thousand million, or 3 thousand million packets",
                (
                    "one thousand million",
                    "two thousand million",
                    "three thousand million",
                ),
                ("1 thousand million", "2 thousand million", "3 thousand million"),
            ),
        )
        for old_count, new_count, old_tokens, new_tokens in cases:
            with self.subTest(old_count=old_count):
                old_extraction = ExtractionResult(
                    pdf_path=Path("old_structured_count_highlight.pdf"),
                    pages=[
                        PageText(
                            page_number=1,
                            text=(
                                f"1 Scope\nCapture {old_count}, then save them for "
                                "the compliance review. The receiver retains every "
                                "accepted result."
                            ),
                        )
                    ],
                )
                new_extraction = ExtractionResult(
                    pdf_path=Path("new_structured_count_highlight.pdf"),
                    pages=[
                        PageText(
                            page_number=1,
                            text=(
                                f"1 Scope\nCapture {new_count}, then archive them for "
                                "the compliance review. The receiver retains every "
                                "accepted result."
                            ),
                        )
                    ],
                )
                result = compare_extractions(
                    old_extraction,
                    new_extraction,
                    DiffOptions(),
                )

                with tempfile.TemporaryDirectory() as temp_dir:
                    outputs = write_reports(result, Path(temp_dir), DiffOptions())
                    report_html = outputs["html"].read_text(encoding="utf-8")

                for token in old_tokens:
                    self.assertNotIn(
                        f'<mark class="del">{token}</mark>',
                        report_html,
                    )
                for token in new_tokens:
                    self.assertNotIn(
                        f'<mark class="ins">{token}</mark>',
                        report_html,
                    )
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

    def test_standalone_cross_reference_does_not_merge_across_paragraph_break(self) -> None:
        """A wrapped Section reference must stay prose while an adjacent split heading still merges."""

        extraction = ExtractionResult(  # 真实 PDF 文本层没有空行，只能由前一行的 Section 引用语义判定。
            pdf_path=Path("wrapped-section-reference.pdf"),
            pages=[
                PageText(
                    page_number=29,
                    text=(
                        "31.A Host Compliance Board\n"
                        "The recommended channel is described in Section\n"
                        "31.1.\n"
                        "The first recommended channel includes a host compliance board.\n"
                        "31.B\n"
                        "Module Compliance Board\n"
                        "The module board shall meet the stated requirements."
                    ),
                )
            ],
            selected_start_page=29,
            selected_end_page=29,
        )

        sections = section_document(extraction)  # 走完整章节化路径，覆盖清洗与拆行标题合并的交互。
        locations = [section.location for section in sections]  # 报告位置直接暴露是否制造了伪章节。
        host_section = next(  # 通过独立章节身份定位正文，不依赖内部合并辅助函数。
            section for section in sections if section.number_path == ("31.A",)
        )

        self.assertFalse(  # 跨段的 31.1 引用不能吸附下一段并成为标题。
            any("31.1. The first recommended channel" in location for location in locations)
        )
        self.assertIn("31.1.", host_section.body)  # 原始引用编号必须作为可审阅正文保留。
        self.assertIn(  # 引用后的首段仍属于 31.A 正文，不能被错误切成独立章节。
            "The first recommended channel includes a host compliance board.",
            host_section.body,
        )
        self.assertIn("31.B Module Compliance Board", locations)  # 无空行的真实拆行标题仍应合并。

    def test_parenthesized_equation_reference_rejoins_after_line_wrap(self) -> None:
        """A source line wrap inside Equation (32-6) must not create a fake edit."""

        merged = _merge_wrapped_lines(
            "The receiver shall meet Equation (32-\n"
            "6) (TBI). The return-loss limit remains applicable."
        )

        self.assertEqual(
            [
                "The receiver shall meet Equation (32-6) (TBI). "
                "The return-loss limit remains applicable.",
            ],
            merged,
        )
        self.assertIsNone(
            compare_module._join_wrapped_cross_reference(
                "The numbered procedure step is (32-",
                "6) and continues here.",
            )
        )

    def test_punctuated_and_abbreviated_cross_references_do_not_create_headings(self) -> None:
        """Section punctuation and Sec. abbreviations still introduce wrapped references."""

        for introducer in ("Section:", "Section：", "Sec."):  # 三种常见文档导出形式具有相同引用语义。
            with self.subTest(introducer=introducer):
                extraction = ExtractionResult(  # 无空行场景要求语义守卫阻止 31.1 吸附下一句。
                    pdf_path=Path("punctuated-wrapped-reference.pdf"),
                    pages=[
                        PageText(
                            page_number=29,
                            text=(
                                "31.A Host Compliance Board\n"
                                f"The channel is described in {introducer}\n"
                                "31.1.\n"
                                "The first recommended channel includes a host board.\n"
                                "31.B Module Compliance Board\n"
                                "The module board shall meet the stated requirements."
                            ),
                        )
                    ],
                    selected_start_page=29,
                    selected_end_page=29,
                )

                sections = section_document(extraction)  # 走真实章节合并入口观察最终位置与正文。
                locations = [section.location for section in sections]  # 伪标题会直接暴露在报告位置中。
                host_section = next(  # 完整 mixed 编号用于稳定定位引用所属正文。
                    section for section in sections if section.number_path == ("31.A",)
                )

                self.assertFalse(  # 三种引导词之后都不能制造 31.1 伪章节。
                    any("31.1. The first recommended channel" in location for location in locations)
                )
                self.assertIn("31.1.", host_section.body)  # 引用编号本身必须保留供人工审阅。
                self.assertIn(  # 引用后的正文仍属于 31.A。
                    "The first recommended channel includes a host board.",
                    host_section.body,
                )

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


class DocumentRoleAndTechnicalQualityTests(unittest.TestCase):
    """Document metadata must not inflate technical comparison confidence."""

    @staticmethod
    def _extraction(name: str, page_texts: list[str], *, start_page: int = 1) -> ExtractionResult:
        pages = [
            PageText(page_number=start_page + index, text=text)
            for index, text in enumerate(page_texts)
        ]
        return ExtractionResult(
            pdf_path=Path(name),
            pages=pages,
            total_pages=start_page + len(pages) - 1,
            selected_start_page=start_page,
            selected_end_page=start_page + len(pages) - 1,
        )

    def test_section_role_defaults_to_technical_for_compatibility(self) -> None:
        section = Section(
            section_id="1-scope",
            heading="1 Scope",
            title="Scope",
            level=1,
            heading_path=("1 Scope",),
            number_path=("1",),
            start_page=1,
            end_page=1,
            body="The transmitter shall meet the electrical limits.",
        )

        self.assertEqual("technical", section.role)

    def test_opening_and_revision_sections_are_document_metadata(self) -> None:
        opening = self._extraction(
            "opening.pdf",
            [
                "Protocol Specification\nRevision 2.0\nCopyright 2026 Example Organization\n"
                "1 Scope\nThe transmitter shall meet the electrical limits."
            ],
        )
        revision = self._extraction(
            "revision.pdf",
            [
                "Revision History\nRevision | Date | Description\n"
                "2.0 | 2026-07-01 | Updated publication text.\n"
                "1 Scope\nThe receiver shall meet the electrical limits."
            ],
        )

        opening_sections = section_document(opening)
        revision_sections = section_document(revision)

        self.assertEqual("document_metadata", opening_sections[0].role)
        self.assertEqual("document_metadata", revision_sections[0].role)
        self.assertEqual("technical", opening_sections[-1].role)
        self.assertEqual("technical", revision_sections[-1].role)

    def test_selected_range_opening_content_remains_technical(self) -> None:
        extraction = self._extraction(
            "selected-range.pdf",
            ["The receiver shall tolerate at least 25 dB insertion loss at Nyquist."],
            start_page=20,
        )

        sections = section_document(extraction)

        self.assertEqual(1, len(sections))
        self.assertEqual("technical", sections[0].role)

    def test_metadata_only_pair_cannot_be_reliable(self) -> None:
        old_history = " ".join(
            f"Revision {index}.0 was published on 2025-01-{index:02d} with editorial updates."
            for index in range(1, 12)
        )
        new_history = " ".join(
            f"Revision {index}.1 was published on 2026-01-{index:02d} with editorial updates."
            for index in range(1, 12)
        )
        old = self._extraction(
            "old-metadata.pdf",
            [
                "1 Revision History\n"
                f"Copyright 2025 Example Organization. {old_history}"
            ],
        )
        new = self._extraction(
            "new-metadata.pdf",
            [
                "1 Revision History\n"
                f"Copyright 2026 Example Organization. {new_history}"
            ],
        )

        result = compare_extractions(old, new, DiffOptions())

        self.assertIsNotNone(result.assessment)
        self.assertNotEqual("reliable", result.assessment.state)

    def test_metadata_volume_cannot_make_tiny_technical_body_reliable(self) -> None:
        metadata = " ".join(
            f"Copyright notice {index} preserves publication and distribution terms."
            for index in range(1, 16)
        )
        extraction = self._extraction(
            "metadata-heavy.pdf",
            [f"Revision History\n{metadata}\n1 Scope\nx"],
        )

        result = compare_extractions(extraction, extraction, DiffOptions())

        self.assertGreater(result.assessment.old_document.character_count, 500)
        self.assertLess(result.assessment.old_document.technical_character_count, 500)
        self.assertEqual("degraded", result.assessment.state)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_repeated_page_furniture_cannot_inflate_technical_density(self) -> None:
        header = "Protocol Specification Confidential Header " * 2
        footer = "Copyright 2026 Example Organization Footer " * 2
        extraction = self._extraction(
            "furniture-heavy.pdf",
            [
                f"{header}\n1 Scope\nTiny.\n{footer}",
                f"{header}\nx\n{footer}",
                f"{header}\ny\n{footer}",
            ],
        )

        result = compare_extractions(extraction, extraction, DiffOptions())

        self.assertGreater(result.assessment.old_document.character_count, 500)
        self.assertLess(result.assessment.old_document.technical_character_count, 500)
        self.assertEqual("degraded", result.assessment.state)

    def test_sparse_multi_page_pair_is_degraded_by_average_text_density(self) -> None:
        page_texts = [
            (
                f"1.{index} Interface Topic {index}\n"
                "The transmitter and receiver exchange a short status summary. "
                "The stated limit applies at the referenced test point."
            )
            for index in range(1, 6)
        ]
        old = self._extraction("old-slides.pdf", page_texts)
        new = self._extraction("new-slides.pdf", page_texts)

        result = compare_extractions(old, new, DiffOptions())

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, Path(temp_dir), DiffOptions())
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        self.assertEqual("degraded", result.assessment.state)
        self.assertLess(
            result.assessment.old_document.average_technical_characters_per_page,
            200,
        )
        self.assertEqual(
            3,
            payload["provenance"]["effective_thresholds"]["quality_min_pages_for_density_check"],
        )
        self.assertIn(
            "average_characters_per_non_empty_page",
            payload["assessment"]["old_document"],
        )
        self.assertIn(
            "technical_character_count",
            payload["assessment"]["old_document"],
        )
        self.assertIn(
            "average_technical_characters_per_page",
            payload["assessment"]["old_document"],
        )
        self.assertEqual(
            200,
            payload["provenance"]["effective_thresholds"][
                "quality_min_average_technical_characters_per_page"
            ],
        )

    def test_dense_single_page_clause_can_still_be_reliable(self) -> None:
        body = " ".join(
            f"Requirement {index}: the receiver shall satisfy the specified electrical limit."
            for index in range(1, 12)
        )
        old = self._extraction("old-clause.pdf", [f"1 Scope\n{body}"])
        new = self._extraction("new-clause.pdf", [f"1 Scope\n{body}"])

        result = compare_extractions(old, new, DiffOptions())

        self.assertEqual("reliable", result.assessment.state)

    def test_layout_risk_on_metadata_page_does_not_degrade_linear_technical_body(self) -> None:
        dense_body = " ".join(
            f"Requirement {index}: the receiver shall satisfy the specified electrical limit."
            for index in range(1, 14)
        )
        extraction = ExtractionResult(
            pdf_path=Path("front-matter-columns.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text="Copyright 2026 Example Organization. Contact and publication details.",
                    layout_risk=True,
                ),
                PageText(page_number=2, text=f"1 Scope\n{dense_body}"),
            ],
            total_pages=2,
            selected_start_page=1,
            selected_end_page=2,
        )

        result = compare_extractions(extraction, extraction, DiffOptions())

        self.assertEqual((1,), result.assessment.old_document.layout_risk_pages)
        self.assertEqual((), result.assessment.old_document.technical_layout_risk_pages)
        self.assertEqual("reliable", result.assessment.state)


class ReportRoleSerializationTests(unittest.TestCase):
    """Report artifacts must preserve technical versus publication metadata roles."""

    @staticmethod
    def _table_result(old_table: TableVisual | None, new_table: TableVisual | None):
        body = (
            "The receiver shall meet every normative electrical timing and calibration "
            "requirement for each declared operating mode. "
        ) * 8
        old = ExtractionResult(
            pdf_path=Path("old.pdf"),
            pages=[PageText(page_number=1, text=f"1 Scope\n{body}")],
            table_visuals=[old_table] if old_table else [],
        )
        new = ExtractionResult(
            pdf_path=Path("new.pdf"),
            pages=[PageText(page_number=1, text=f"1 Scope\n{body}")],
            table_visuals=[new_table] if new_table else [],
        )
        return compare_extractions(old, new, DiffOptions())

    def test_generic_caption_revision_table_is_document_metadata(self) -> None:
        revision_table = TableVisual(
            page_number=3,
            table_number=1,
            title="in the table below:",
            bbox=(10.0, 10.0, 500.0, 300.0),
            image_data_uri="",
            row_texts=[
                "表格行: T1 | Description=Initial publication | Revision=1.0 | Date=2025-01-01",
                "表格行: T1 | Description=Editorial update | Revision=1.1 | Date=2026-01-01",
            ],
            grid_summary="structured rows",
        )
        result = self._table_result(None, revision_table)

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, Path(temp_dir), DiffOptions())
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
            table_csv = outputs["table_csv"].read_text(encoding="utf-8-sig")
            reader_reports = [
                outputs[key].read_text(encoding="utf-8")
                for key in ("html", "markdown", "text")
            ]

        # 机器审计继续保留修订历史表的角色和每一行事实。
        self.assertEqual("document_metadata", payload["table_changes"][0]["role"])
        self.assertEqual(2, payload["table_changes"][0]["row_change_count"])
        self.assertIn("document_metadata", table_csv)
        self.assertIn("Initial publication", table_csv)
        # 读者报告不再显示修订历史表卡或其中的出版记录。
        for reader_report in reader_reports:
            self.assertNotIn("Initial publication", reader_report)
            self.assertNotIn("Editorial update", reader_report)

    def test_electrical_parameter_table_remains_technical(self) -> None:
        electrical_table = TableVisual(
            page_number=8,
            table_number=1,
            title="Table 8-1 Receiver electrical limits",
            bbox=(10.0, 10.0, 500.0, 300.0),
            image_data_uri="",
            row_texts=[
                "表格行: T1 | Parameter=Input voltage | Symbol=Vin | Value=800 | Units=mV",
                "表格行: T1 | Parameter=Timing margin | Symbol=Tmargin | Value=0.10 | Units=UI",
            ],
            grid_summary="structured rows",
        )
        result = self._table_result(None, electrical_table)

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, Path(temp_dir), DiffOptions())
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        self.assertEqual("technical", payload["table_changes"][0]["role"])

    def test_table_report_keeps_named_spelling_changes_visible(self) -> None:
        old_table = TableVisual(
            page_number=8,
            table_number=1,
            title="Table 8-2 Connector inventory",
            bbox=(10.0, 10.0, 500.0, 300.0),
            image_data_uri="",
            row_texts=["表格行: T1 | Parameter=Connector name | Value=Adaptor"],
            grid_summary="structured rows",
        )
        new_table = TableVisual(
            page_number=8,
            table_number=1,
            title="Table 8-2 Connector inventory",
            bbox=(10.0, 10.0, 500.0, 300.0),
            image_data_uri="",
            row_texts=["表格行: T1 | Parameter=Connector name | Value=Adapter"],
            grid_summary="structured rows",
        )
        result = self._table_result(old_table, new_table)

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, Path(temp_dir), DiffOptions())
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        self.assertEqual(1, len(payload["table_changes"]))
        self.assertEqual(1, payload["table_changes"][0]["row_change_count"])
        row_change = payload["table_changes"][0]["row_changes"][0]
        self.assertIn("Adaptor", row_change["old_value"])
        self.assertIn("Adapter", row_change["new_value"])

    def test_json_and_csv_outputs_include_roles(self) -> None:
        old_text = (
            "Copyright 2025 Example Organization.\n"
            "1 Scope\n"
            + ("The receiver shall support calibration mode A. " * 14)
        )
        new_text = (
            "Copyright 2026 Example Organization.\n"
            "1 Scope\n"
            + ("The receiver shall support calibration mode B. " * 14)
        )
        old = ExtractionResult(Path("old.pdf"), [PageText(1, old_text)])
        new = ExtractionResult(Path("new.pdf"), [PageText(1, new_text)])
        result = compare_extractions(old, new, DiffOptions())

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, Path(temp_dir), DiffOptions())
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
            with outputs["csv"].open(encoding="utf-8-sig") as handle:
                prose_rows = list(csv.DictReader(handle))
            table_header = outputs["table_csv"].read_text(encoding="utf-8-sig").splitlines()[0]

        self.assertTrue(payload["changes"])
        self.assertTrue(payload["old_sections"])
        self.assertTrue(all("role" in change for change in payload["changes"]))
        self.assertTrue(all("role" in section for section in payload["old_sections"]))
        self.assertTrue(prose_rows)
        self.assertTrue(all(row["role"] in {"technical", "document_metadata"} for row in prose_rows))
        self.assertIn("role", table_header)

    def test_reader_reports_hide_metadata_but_audit_outputs_retain_it(self) -> None:
        body_old = ("The receiver shall support calibration mode A. " * 14)
        body_new = ("The receiver shall support calibration mode B. " * 14)
        table = TableVisual(
            page_number=2,
            table_number=1,
            title="Table 2 Receiver electrical limits",
            bbox=(10.0, 10.0, 500.0, 300.0),
            image_data_uri="",
            row_texts=["表格行: T1 | Parameter=Input voltage | Value=800 | Units=mV"],
            grid_summary="structured rows",
        )
        old = ExtractionResult(
            Path("old.pdf"),
            [PageText(1, f"Copyright 2025 Example Organization.\n1 Scope\n{body_old}")],
        )
        new = ExtractionResult(
            Path("new.pdf"),
            [PageText(1, f"Copyright 2026 Example Organization.\n1 Scope\n{body_new}")],
            table_visuals=[table],
        )
        result = compare_extractions(old, new, DiffOptions())

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, Path(temp_dir), DiffOptions())
            html = outputs["html"].read_text(encoding="utf-8")
            markdown = outputs["markdown"].read_text(encoding="utf-8")
            text = outputs["text"].read_text(encoding="utf-8")
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
            with outputs["csv"].open(encoding="utf-8-sig") as handle:
                prose_rows = list(csv.DictReader(handle))

        # 原始比较结果仍需保留技术正文和元信息两类事实，供机器审计追溯。
        self.assertEqual(1, sum(change.role == "technical" for change in result.changes))
        self.assertEqual(1, sum(change.role == "document_metadata" for change in result.changes))
        # 三种读者报告不得再为作者、版权或修订记录占用正文空间。
        for reader_report in (html, markdown, text):
            self.assertNotIn("文档元信息变化", reader_report)
            self.assertNotIn("Copyright 2025 Example Organization", reader_report)
            self.assertNotIn("Copyright 2026 Example Organization", reader_report)
        # JSON/CSV 是无损审计面，必须继续输出 document_metadata 事实。
        self.assertTrue(any(change["role"] == "document_metadata" for change in payload["changes"]))
        self.assertTrue(any(row["role"] == "document_metadata" for row in prose_rows))
        self.assertIn("<span>核心技术变化</span>", html)


if __name__ == "__main__":
    unittest.main()
