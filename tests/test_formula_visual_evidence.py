"""Real-corpus regressions for formula and screenshot evidence."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from protocol_pdf_diff.compare import compare_extractions
from protocol_pdf_diff.models import DiffOptions, DiffResult, ExtractionResult
from protocol_pdf_diff.pdf_extract import extract_pdf_text
from protocol_pdf_diff.reporting import write_reports
from protocol_pdf_diff.text_utils import readable_symbol_font_glyphs


OIF_532_05 = Path("/Users/mac/Documents/文件对比工具/oif2024.532.05.pdf")
OIF_532_04 = Path("/Users/mac/Documents/文件对比工具/oif2024.532.04.pdf")


@unittest.skipUnless(OIF_532_05.is_file(), "本地 OIF 2024.532.05 样本不存在")
class FormulaVisualEvidenceTests(unittest.TestCase):
    """公开样本验收公式几何和误截图边界。"""

    def test_formula_fields_do_not_shift_legacy_high_position_arguments(self) -> None:
        """新增公式字段必须追加，不能改变旧 dataclass 位置参数含义。"""

        extraction = ExtractionResult(
            Path("legacy.pdf"), [], [], 1, 1, 1, [], "legacy-sha256"
        )
        result = DiffResult(
            Path("old.pdf"),
            Path("new.pdf"),
            [],
            [],
            [],
            [],
            1,
            1,
            1,
            1,
            1,
            1,
            [],
            [],
            "legacy-assessment",  # type: ignore[arg-type] -- 仅验证旧位置参数绑定。
            "legacy-provenance",  # type: ignore[arg-type] -- 同上。
            (),
            (),
        )

        self.assertEqual("legacy-sha256", extraction.source_sha256)
        self.assertEqual([], extraction.formula_visuals)
        self.assertEqual("legacy-assessment", result.assessment)
        self.assertEqual("legacy-provenance", result.provenance)
        self.assertEqual([], result.formula_changes)

    def test_real_532_figure_mcb_label_is_not_a_table_visual(self) -> None:
        """Figure 31-5 内的 MCB 单格线框不能进入表格截图。"""

        extraction = extract_pdf_text(OIF_532_05, start_page=18, end_page=18)
        rows = "\n".join(
            row
            for table in extraction.table_visuals
            for row in table.row_texts
        )

        self.assertNotIn("Column 1=MCB", rows)

    def test_legacy_symbol_formula_punctuation_is_reader_readable(self) -> None:
        """OIF 公式中的 Adobe Symbol 私用区括号和运算符不能泄漏到报告。"""

        self.assertEqual(
            "(f/2)+1=2−1*1",
            readable_symbol_font_glyphs(
                "\uf028f\uf02f2\uf029\uf02b1\uf03d2\uf02d1\uf02a1"
            ),
        )

    def test_real_532_table_inline_fb_subscript_is_not_moved_after_ghz(self) -> None:
        """Table 31-10 的 ``f_b/2`` 不能被拆成行尾 ``GHz`` 后的孤立 b。"""

        extraction = extract_pdf_text(OIF_532_05, start_page=15, end_page=15)
        rows = "\n".join(
            row
            for table in extraction.table_visuals
            for row in table.row_texts
        )

        self.assertIn("Parameter=Test channel insertion loss at fb /2 GHz", rows)
        self.assertNotIn("GHz\\nb", rows)

    @unittest.skipUnless(OIF_532_04.is_file(), "本地 OIF 2024.532.04 样本不存在")
    def test_real_532_display_formula_preserves_subscripts_and_superscripts(self) -> None:
        """公式截图的可读语义要保留 SDD 下标和 f 指数。"""

        extraction = extract_pdf_text(OIF_532_04, start_page=25, end_page=25)
        formula = next(
            visual
            for visual in extraction.formula_visuals
            if visual.formula_number == "(31-3)"
        )

        self.assertIn("SDD_{MTFmin}", formula.semantic_text)
        self.assertIn("f^{1.5}", formula.semantic_text)
        self.assertIn("f^{2}", formula.semantic_text)
        self.assertGreaterEqual(formula.script_count, 3)
        self.assertTrue(formula.image_data_uri.startswith("data:image/jpeg;base64,"))

    @unittest.skipUnless(OIF_532_04.is_file(), "本地 OIF 2024.532.04 样本不存在")
    def test_real_532_report_renders_formula_scripts_and_source_crops(self) -> None:
        """读者报告要将编号顺延的三条公式作为视觉证据并排展示。"""

        old = extract_pdf_text(OIF_532_04, start_page=25, end_page=25)
        new = extract_pdf_text(OIF_532_05, start_page=24, end_page=24)
        result = compare_extractions(old, new, DiffOptions())

        self.assertEqual(3, len(result.formula_changes))
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            html = outputs["html"].read_text(encoding="utf-8")
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        self.assertIn("公式视觉核对", html)
        self.assertIn('<span class="nav-label">公式</span>', html)
        self.assertIn('<div class="nav-body"><strong>视觉核对</strong>', html)
        self.assertIn("SDD<sub>MTFmin</sub>", html)
        self.assertIn("f<sup>1.5</sup>", html)
        self.assertIn(
            ".summary, .compare-grid, .table-shot-grid { grid-template-columns: minmax(0, 1fr); }",
            html,
        )
        self.assertIn(
            ".table-shot-grid, .table-shot { min-width: 0; max-width: 100%; }",
            html,
        )
        self.assertIn(
            ".table-row-summary { table-layout: fixed; min-width: 0; }",
            html,
        )
        self.assertIn(
            ".table-row-summary th, .table-row-summary td { overflow-wrap: anywhere; word-break: break-word; min-width: 0; }",
            html,
        )
        self.assertEqual(3, len(payload["formula_changes"]))
        self.assertTrue(
            payload["formula_changes"][0]["old_formula"]["has_embedded_image"]
        )

if __name__ == "__main__":
    unittest.main()
