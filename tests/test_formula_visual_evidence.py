"""Real-corpus regressions for formula and screenshot evidence."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from protocol_pdf_diff.compare import compare_extractions
from protocol_pdf_diff.models import (
    DiffOptions,
    DiffResult,
    ExtractionResult,
    FormulaChange,
    FormulaVisual,
    PageText,
    Section,
    SectionChange,
    SnippetPair,
)
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

    def test_same_number_different_formula_meaning_stays_separate(self) -> None:
        """编号相同但语义不同的公式不能伪装成同一条修改。"""

        # 构造同号但主体差异很大的公式，验证编号复用不能覆盖语义证据。
        old_formula = FormulaVisual(
            page_number=14,
            formula_number="(31-1)",
            bbox=(10.0, 20.0, 200.0, 40.0),
            image_data_uri="data:image/jpeg;base64,b2xk",
            source_text="SCD11 < -18 + 6 * (f / fb)",
            semantic_text="SCD11 < -18 + 6 * (f / f_{b})",
            script_count=1,
            image_dhash="0000000000000000",
        )
        # 新版保留唯一的 (31-1) 标识，但公式限值和适用频段已经实质改变。
        new_formula = FormulaVisual(
            page_number=12,
            formula_number="(31-1)",
            bbox=(10.0, 20.0, 200.0, 40.0),
            image_data_uri="data:image/jpeg;base64,bmV3",
            source_text="SCD11 <= -12 dB",
            semantic_text="SCD11 ≤ -12 dB",
            script_count=0,
            image_dhash="ffffffffffffffff",
        )
        # 通过公开比较入口生成报告事实，避免测试私有配对函数的实现细节。
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old.pdf"),
                pages=[PageText(14, "31.3.8 Conversion\nOld requirement text")],
                total_pages=14,
                selected_start_page=14,
                selected_end_page=14,
                formula_visuals=[old_formula],
            ),
            ExtractionResult(
                pdf_path=Path("new.pdf"),
                pages=[PageText(12, "31.3.9 Return Loss\nNew requirement text")],
                total_pages=12,
                selected_start_page=12,
                selected_end_page=12,
                formula_visuals=[new_formula],
            ),
            DiffOptions(),
        )

        # 公式号会因版本增删而被复用；语义证据不足时必须分别呈现删除和新增。
        self.assertEqual(2, len(result.formula_changes))
        self.assertEqual(
            {"deleted", "added"},
            {item.change_type for item in result.formula_changes},
        )
        by_type = {item.change_type: item for item in result.formula_changes}
        self.assertEqual(old_formula, by_type["deleted"].old_formula)
        self.assertEqual(new_formula, by_type["added"].new_formula)

    def test_html_embeds_formula_evidence_in_owning_section_card(self) -> None:
        """公式截图应跟随所属正文条款，顶部只留下可跳转的紧凑索引。"""

        # 旧版公式位于被替换条款，正文保留公式号以提供与源页不同的独立归属锚点。
        old_section = Section(
            section_id="31.3.8",
            heading="31.3.8 Conversion",
            title="Conversion",
            level=3,
            heading_path=("31 Electrical", "31.3 Characteristics", "31.3.8 Conversion"),
            number_path=("31", "31.3", "31.3.8"),
            start_page=14,
            end_page=14,
            body="The old limit is defined by Equation (31-1).",
        )
        # 新版保留 31.3.8，但不再承载旧公式；删除证据应继续跟随旧版这一条款。
        new_conversion_section = Section(
            section_id="31.3.8",
            heading="31.3.8 Conversion",
            title="Conversion",
            level=3,
            heading_path=("31 Electrical", "31.3 Characteristics", "31.3.8 Conversion"),
            number_path=("31", "31.3", "31.3.8"),
            start_page=12,
            end_page=12,
            body="The conversion requirement no longer contains Equation (31-1).",
        )
        # 新版新增 31.3.9 并复用公式号；新增证据必须进入新条款，不能与旧公式硬配对。
        new_section = Section(
            section_id="31.3.9",
            heading="31.3.9 Common-Mode Return Loss",
            title="Common-Mode Return Loss",
            level=3,
            heading_path=(
                "31 Electrical",
                "31.3 Characteristics",
                "31.3.9 Common-Mode Return Loss",
            ),
            number_path=("31", "31.3", "31.3.9"),
            start_page=12,
            end_page=13,
            # 真实 PDF 会把下标 b 排到行尾；稳定的公式前缀仍应证明它属于本条款。
            body="The new limit is SCD11 ≤ – 12 dB for f ⁄ 2 < f < f\nb b.",
        )
        # 两侧截图模拟已经由比较层合并的一条保守公式复核事实。
        old_formula = FormulaVisual(
            14,
            "(31-1)",
            (10.0, 20.0, 200.0, 40.0),
            "data:image/jpeg;base64,b2xk",
            "old formula",
            "SCD11 < -18 + 6f",
            0,
            "0000000000000000",
        )
        new_formula = FormulaVisual(
            12,
            "(31-1)",
            (10.0, 20.0, 200.0, 40.0),
            "data:image/jpeg;base64,bmV3",
            "SCD11 ≤ – 12 dB for f b ⁄ 2 < f < f b (31-1)",
            "SCD11 ≤ – 12 dB for f_{b} ⁄ 2 < f < f_{b}",
            0,
            "ffffffffffffffff",
        )
        # 直接构造公开报告模型，隔离验证报告信息架构而不依赖 PDF 抽取器。
        result = DiffResult(
            old_pdf=Path("old.pdf"),
            new_pdf=Path("new.pdf"),
            old_sections=[old_section],
            new_sections=[new_conversion_section, new_section],
            changes=[
                SectionChange(
                    change_type="modified",
                    old_section=old_section,
                    new_section=new_conversion_section,
                    similarity=0.6,
                    replaced_snippets=[
                        SnippetPair(
                            "The old limit is defined by Equation (31-1).",
                            "The conversion requirement no longer contains Equation (31-1).",
                        )
                    ],
                ),
                SectionChange(
                    change_type="added",
                    old_section=None,
                    new_section=new_section,
                    similarity=0.0,
                    added_snippets=["The new limit is defined by Equation (31-1)."],
                ),
            ],
            warnings=[],
            old_total_pages=14,
            new_total_pages=13,
            formula_changes=[
                FormulaChange(
                    change_type="deleted",
                    old_formula=old_formula,
                    new_formula=None,
                    similarity=0.0,
                    visual_similarity=None,
                    reason="旧版公式未在新版找到可靠对应项。",
                ),
                FormulaChange(
                    change_type="added",
                    old_formula=None,
                    new_formula=new_formula,
                    similarity=0.0,
                    visual_similarity=None,
                    reason="新版出现新的公式视觉证据。",
                ),
            ],
        )

        # 通过真实报告写入入口读取最终 HTML，断言用户看到的结构而不是私有 helper 调用。
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            html = outputs["html"].read_text(encoding="utf-8")

        # 顶部索引只负责跳转，旧版集中公式截图板块必须消失。
        self.assertIn('id="formula-index"', html)
        self.assertIn('href="#formula-1"', html)
        self.assertIn('href="#formula-2"', html)
        self.assertNotIn('id="formula-changes"', html)
        self.assertLess(
            html.index('id="formula-index"'),
            html.index('id="text-changes"'),
        )
        # 删除和新增公式必须分别进入旧、新条款，不能因复用编号而挤在同一卡片。
        change_one_start = html.index('id="change-1"')
        change_two_start = html.index('id="change-2"')
        formula_one_start = html.index('id="formula-1"')
        formula_two_start = html.index('id="formula-2"')
        self.assertLess(change_one_start, formula_one_start)
        self.assertLess(formula_one_start, change_two_start)
        self.assertLess(change_two_start, formula_two_start)
        self.assertIn(
            "31.3.9 Common-Mode Return Loss",
            html[change_two_start:formula_two_start],
        )
        # 每张源裁图都应提供可发现的放大按钮，并复用一个离线对话框显示原始像素。
        self.assertEqual(2, html.count('class="formula-shot-open"'))
        self.assertIn('id="formula-zoom-dialog"', html)

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
        """真实 532 公式应跟随正文条款，并保留索引、上下标和可放大源裁图。"""

        old = extract_pdf_text(OIF_532_04, start_page=25, end_page=25)
        new = extract_pdf_text(OIF_532_05, start_page=24, end_page=24)
        result = compare_extractions(old, new, DiffOptions())

        self.assertEqual(3, len(result.formula_changes))
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            html = outputs["html"].read_text(encoding="utf-8")
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        # 公式索引只负责跳转，旧版集中截图区不能再次出现在真实报告。
        self.assertIn("公式复核索引", html)
        self.assertIn('id="formula-index"', html)
        self.assertIn('<span class="nav-label">公式</span>', html)
        self.assertIn('<div class="nav-body"><strong>复核索引</strong>', html)
        self.assertNotIn('id="formula-changes"', html)
        self.assertIn("本条款相关公式证据", html)
        self.assertIn("SDD<sub>MTFmin</sub>", html)
        self.assertIn("f<sup>1.5</sup>", html)
        self.assertEqual(6, html.count('class="formula-shot-open"'))
        self.assertIn('id="formula-zoom-dialog"', html)
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
