from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from protocol_pdf_diff.compare import compare_extractions
from protocol_pdf_diff.models import DiffOptions, ExtractionResult, PageText
from protocol_pdf_diff.reporting import _report_pair_label, write_reports
from protocol_pdf_diff.webview_gui import ProtocolDiffWebApi, load_web_ui, validate_web_ui_contract


class UserFacingSettingsTests(unittest.TestCase):
    def test_report_pair_label_uses_safe_imported_stems(self) -> None:
        label = _report_pair_label(
            Path("/tmp/旧版:协议?.pdf"),
            Path("/tmp/新版:协议*.pdf"),
        )
        self.assertEqual("旧版_协议__vs_新版_协议_", label)
        self.assertNotRegex(label, r'[<>:"/\\|?*]')

    def test_human_facing_report_files_use_imported_pair_label(self) -> None:
        old_pdf = Path("/tmp/旧版协议.pdf")
        new_pdf = Path("/tmp/新版协议.pdf")
        options = DiffOptions(visual_watchdog=False)
        result = compare_extractions(
            ExtractionResult(old_pdf, [PageText(1, "1 Scope\nThe receiver shall support mode A.")]),
            ExtractionResult(new_pdf, [PageText(1, "1 Scope\nThe receiver shall support mode B.")]),
            options,
        )
        with tempfile.TemporaryDirectory() as directory:
            outputs = write_reports(result, directory, options)

        label = _report_pair_label(old_pdf, new_pdf)
        self.assertEqual(f"{label}.html", outputs["html"].name)
        self.assertEqual(f"{label}.md", outputs["markdown"].name)
        self.assertEqual(f"{label}.txt", outputs["text"].name)
        self.assertTrue(outputs["report_dir"].name.startswith(f"{label}_"))

    def test_webview_config_keeps_internal_strategy_fixed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_pdf = root / "old.pdf"
            new_pdf = root / "new.pdf"
            old_pdf.write_bytes(b"%PDF-1.4\n")
            new_pdf.write_bytes(b"%PDF-1.4\n")
            api = ProtocolDiffWebApi()
            _old, _new, _output, options = api._collect_config(
                {
                    "old_pdf": str(old_pdf),
                    "new_pdf": str(new_pdf),
                    "output_dir": str(root),
                    "min_similarity": "0.01",
                    "max_snippets": "9999",
                    "include_unchanged": True,
                }
            )
        self.assertEqual(0.72, options.min_section_match_similarity)
        self.assertEqual(20, options.max_snippets_per_section)
        self.assertFalse(options.include_unchanged_sections)

    def test_web_ui_hides_internal_tuning_controls_and_invalidates_inputs(self) -> None:
        html = load_web_ui()
        validate_web_ui_contract(html)
        for token in (
            "章节匹配阈值",
            "每章片段数",
            "列出未变化章节",
            'id="min-similarity"',
            'id="max-snippets"',
            'id="include-unchanged"',
        ):
            self.assertNotIn(token, html)
        self.assertIn("报告设置", html)
        self.assertIn("输入已修改，请重新比较", html)
        self.assertIn('document.querySelectorAll(".range input")', html)


if __name__ == "__main__":
    unittest.main()
