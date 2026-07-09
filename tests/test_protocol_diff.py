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
from unittest import mock
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
from protocol_pdf_diff.compare import _is_global_noise_snippet  # 直接覆盖报告层短碎片过滤规则。
from protocol_pdf_diff.compare import _merge_wrapped_lines  # 验证 PCIe 页眉簇过滤不会吞掉正文修订历史。
from protocol_pdf_diff.compare import _split_line_preserving_numbers  # 验证列表破折号粘连会拆成独立审阅句。
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
import protocol_pdf_diff.pdf_extract as pdf_extract_module
from protocol_pdf_diff.models import DiffOptions, ExtractionResult, PageText, TableVisual
from protocol_pdf_diff.pdf_extract import (
    MissingDependencyError,  # 验证缺少 pdfplumber 时直接失败，而不是静默退回 pypdf。
    _clean_extracted_page_text,  # 验证抽取层先过滤页边行号、DRAFT 和版权页脚。
    _combine_text_and_table_lines,  # 验证结构化表格行覆盖原始长表格文本后的降噪行为。
    _keep_non_watermark_object,  # 直接验证水印过滤谓词，防止大标题被误删。
    _should_skip_detected_table,  # 验证 Figure/空伪表格不会进入表格截图和正文 diff。
    _table_lines_from_rows,  # 直接验证 pdfplumber 表格行格式化，覆盖无需真实 PDF 的边界场景。
    extract_pdf_text,
)
from protocol_pdf_diff.reporting import _inline_diff_html, _paired_table_visuals, write_reports
from protocol_pdf_diff.sample_data import write_demo_pdfs, write_multipage_text_pdf
from protocol_pdf_diff.sectioning import detect_heading, section_document
from protocol_pdf_diff.text_utils import remove_draft_watermark_letter_artifacts
from protocol_pdf_diff.venv_bootstrap import (  # 验证 GUI/命令行入口会优先使用项目本地 .venv。
    BOOTSTRAP_ATTEMPT_ENV,
    project_venv_python,
    project_venv_root,
    reexec_into_project_venv,
    should_reexec_into_project_venv,
)

OIF_OLD_SAMPLE = Path("/Users/mac/Downloads/oif2024.058.11.pdf")  # 真实回归样本旧版路径；文件不存在时测试会跳过，避免影响 CI。
OIF_NEW_SAMPLE = Path("/Users/mac/Downloads/oif2024.058.13.pdf")  # 真实回归样本新版路径；用于验证用户反馈的 OIF 表格差异。


class ProtocolDiffTests(unittest.TestCase):
    """End-to-end tests over generated old/new sample PDFs."""

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

    def test_extraction_filters_margin_line_numbers_draft_and_copyright(self) -> None:
        """Extraction cleanup should remove page furniture before sectioning."""

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

        self.assertNotIn("DRAFT", cleaned)  # 水印不应进入后续章节或 diff。
        self.assertNotIn("Copyright", cleaned)  # 版权页脚不应进入用户报告。
        self.assertNotIn("Optical Internetworking Forum - Clause", cleaned)  # 运行页眉不应成为正文。
        self.assertNotIn(line_numbers, cleaned)  # 1~49 行号串不应残留。
        self.assertIn("Receiver calibration shall remain.", cleaned)  # 正文句子仍应保留。

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
        self.assertIsNone(detect_heading("2 Signal 0"))  # 图形/表格值残片不是章节标题。
        self.assertIsNone(detect_heading("802.3dj)"))  # 标准名残片不是章节标题。
        self.assertIsNone(
            detect_heading("32.3.1. The test transmitter is constrained such that for any transmitter equalizer setting")
        )  # 长正文句子不能被当作章节路径。
        self.assertIsNotNone(detect_heading("2 400G Interfaces"))  # 合法协议章节仍应识别。
        self.assertIsNotNone(
            detect_heading("2.13.2 Overview of Calibration Steps at 16.0 GT/s")
        )  # 带 GT/s 单位的深层章节标题仍应识别。
        self.assertIsNotNone(detect_heading("1 The Protocol Architecture."))  # 合法 The 开头标题不能被脚注规则误删。
        cleaned_heading = detect_heading("32.1 RequirRements")  # 标题里的 DRAFT 字母残片应在分节前清理。
        self.assertIsNotNone(cleaned_heading)  # 清理后的标题仍应保留为合法章节。
        self.assertEqual("32.1 Requirements", cleaned_heading.raw)  # 报告位置不应显示 RequirRements。

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

    def test_draft_watermark_letter_fragments_do_not_create_text_diffs(self) -> None:
        """Leaked DRAFT letters inside prose should be treated as extraction noise."""

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

        self.assertEqual([], result.changes)  # 水印字母残片不应成为用户可见差异。
        self.assertEqual(
            "laneTraining shall remain visible.",
            remove_draft_watermark_letter_artifacts("laneTraining shall remain visible."),
        )  # 白名单外的合法驼峰标识符不能被清理成 laneraining。
        self.assertEqual("than", remove_draft_watermark_letter_artifacts("thFan"))  # 短词中的 F 水印残字也应修正。
        self.assertEqual("and", remove_draft_watermark_letter_artifacts("anRd"))  # 短词中的 R 水印残字也应修正。
        self.assertEqual("Signal", remove_draft_watermark_letter_artifacts("SignaDl"))  # 技术词中的 D 残字应修正。
        self.assertEqual("package", remove_draft_watermark_letter_artifacts("Fpackage"))  # 句首贴入的 F 水印残字也应修正。

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

    def test_oif_contact_notice_boilerplate_is_suppressed(self) -> None:
        """OIF front-matter contact notice should not become a user-facing diff."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old.pdf"),  # 旧侧只有真实正文。
            pages=[PageText(page_number=1, text="1 Scope\nCore requirement remains.")],
            total_pages=1,
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new.pdf"),  # 新侧额外混入 OIF 前言联系信息。
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
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

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())  # 执行完整比较和噪声 suppress。

        self.assertEqual([], result.changes)  # 前言联系信息不应成为报告卡片。

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

        self.assertIn("operating windows", html)  # 大段正文句子必须仍在报告里。
        self.assertIn('class="del">10</mark>', html)  # 旧正文数值必须被保留并高亮。
        self.assertIn('class="ins">12</mark>', html)  # 新正文数值必须被保留并高亮。
        self.assertIn("表格截图识别", html)  # 表格截图区也必须存在。
        self.assertIn("<th>项目</th><th>旧版</th><th>新版</th><th>类型</th>", html)  # 表格摘要应像人工审查表。
        self.assertIn("Input jitter", html)  # 项目列应显示参数名，而不是内部“表格行”。
        self.assertIn("0.30 UI", html)  # 旧版列保留旧值。
        self.assertIn("0.28 UI", html)  # 新版列保留新值。
        self.assertIn("实质变化", html)  # 类型列应给出可读变化标签。
        self.assertNotIn("OpenCV", html)  # 用户报告不显示内部图像库名称。
        self.assertNotIn("pdfplumber", html)  # 用户报告不显示内部文本库名称。
        self.assertNotIn("OCR 未启用", html)  # 缺少 OCR 引擎不应作为表格比较正文展示。
        self.assertNotIn("bbox", html)  # 用户报告不显示内部坐标。
        self.assertNotIn("为什么", html)  # 报告不应包含实现自述式文案。
        self.assertNotIn("不接入", html)  # 报告不应解释内部集成取舍。

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

        self.assertIn("operating windows", visible_snippets)  # 正文段落变化仍然保留。
        self.assertNotIn("表格行:", visible_snippets)  # 主正文差异卡片不再重复内部表格行。
        self.assertNotIn("表格行:", html)  # HTML 页面也不应直接暴露内部表格行格式。
        self.assertIn("Input jitter", html)  # 表格视觉摘要仍展示项目名。
        self.assertIn("0.30 UI", html)  # 表格视觉摘要仍展示旧值。
        self.assertIn("0.28 UI", html)  # 表格视觉摘要仍展示新值。

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

        self.assertIn("<th>项目</th><th>旧版</th><th>新版</th><th>类型</th>", html)  # 摘要表头应和视觉参考一致。
        self.assertIn("Uncorrelated Jitter symbol", html)  # 项目列应标出符号变化。
        self.assertIn("T_J4.3u03 | 0.121 UI", html)  # 旧版值应紧凑显示符号、数值和单位。
        self.assertIn("T_JH4.3u | 0.121 UI", html)  # 新版值应紧凑显示符号、数值和单位。
        self.assertIn("Uncorrelated jitter RMS symbol", html)  # 第二个符号变化也应有独立项目。
        self.assertIn("T_JRMS03 | 0.023 UIrms", html)  # 旧版 RMS 值应可直接对照。
        self.assertIn("T_JHRMS | 0.023 UIrms", html)  # 新版 RMS 值应可直接对照。
        self.assertIn("Even-Odd Jitter", html)  # 小表格有变化时，相关未变化行也应显示。
        self.assertIn("无变化", html)  # 未变化行应有绿色状态标签。
        self.assertGreaterEqual(html.count("实质/符号变化"), 2)  # 两个符号变化都应被明确标记。

    def test_table_visual_summary_ignores_display_only_row_noise(self) -> None:
        """Table summaries should ignore row ids, checkboxes, punctuation, and case."""

        old_table = TableVisual(
            page_number=1,  # 旧表页码只用于报告定位。
            table_number=1,  # 旧表内部序号不应进入行级比较 key。
            title="Table 1 Calibration notes",  # 同名表会被报告层配对。
            bbox=(0.0, 0.0, 100.0, 100.0),  # 测试不依赖真实截图尺寸。
            image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",  # 极小图片占位。
            row_texts=[
                "表格行: T1 | Value=❑ Sj – 5 to 10 ps PP @ TP1",  # 旧侧带复选框、大小写和长横线。
                "表格行: T1 | Value=Note: Adapters such as DC blocks, pickoff T’s, etc. that are connected",  # 旧侧带逗号。
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
                "表格行: T2 | Value=SJ - 5 to 10 ps PP @ TP1",  # 新侧缺复选框且大小写不同，但内容相同。
                "表格行: T2 | Value=Note: Adapters such as DC blocks, pickoff T’s etc. that are connected",  # 新侧少一个逗号。
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

        self.assertIn("未检测到行级变化", html)  # 这些展示差异不应被标成实质变化。
        self.assertNotIn("实质变化", html)  # 没有数值/符号变化时不应出现红色实质变化标签。
        self.assertNotIn("替换/修改", html)  # 标点和大小写差异也不应显示成替换。

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

        self.assertIn("New impedance", html)  # 插入行应显示为新增行，而不是和旧行错配。
        self.assertIn("新表新增行", html)  # 插入行类型应明确。
        self.assertIn("Reference resistance", html)  # 原有参数仍应被配到同一行。
        self.assertIn("50 Ω", html)  # 旧值保留。
        self.assertIn("46.25 Ω", html)  # 新值保留。
        self.assertIn("Eye height", html)  # `<` 到 `>` 的限值符号变化不能被吞掉。
        self.assertIn("&lt; 15 mV", html)  # 旧侧单独小于号必须进入显示值。
        self.assertIn("&gt; 15 mV", html)  # 新侧单独大于号必须进入显示值。
        self.assertGreaterEqual(html.count("实质变化"), 2)  # 数值变化和限值方向变化都应判为实质变化。

    def test_wrapped_table_row_continuations_merge_symbol_suffixes(self) -> None:
        """Wrapped table rows should not split one symbol change across two summary rows."""

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

        self.assertEqual(2, len(lines))  # 两个物理行应合成一个逻辑行，后续真实行保留。
        self.assertIn("Characteristic=Uncorrelated Jitter (time interval from 0.0025% to 99.9975%", lines[0])
        self.assertIn("Symbol=T_J4.3u03", lines[0])  # 符号后缀应拼回同一个符号。
        self.assertIn("MAX=0.121", lines[0])  # 上一行数值不能丢失。
        self.assertIn("Symbol=T_EOJ03", lines[1])  # 后续独立行应保持独立。

    def test_raw_jitter_table_fragments_are_hidden_when_structured_rows_exist(self) -> None:
        """Raw table text fragments should not duplicate structured table diffs."""

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

        self.assertNotIn("T_J 0.121 UI 99.9975%", snippets)  # 旧版原始表格块应隐藏。
        self.assertNotIn("T_JH 0.121 UI 99.9975%", snippets)  # 新版短表格碎片也应隐藏。
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

    def test_standalone_figure_blocks_are_removed_but_figure_references_stay(self) -> None:
        """Image/plot blocks should be hidden while normal prose references remain diffable."""

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
        self.assertNotIn("Figure 32-2.Channel", cleaned)  # 独立 Figure 图块不再进入正文。
        self.assertNotIn("Amplitude X", cleaned)  # 曲线坐标轴碎片不应进入正文 diff。

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
        self.assertNotIn("Frequency (GHz)", split_chart_text)  # 裸图轴标题不应进入正文。
        self.assertNotIn("IL min =", split_chart_text)  # 图形公式残片不应进入正文。
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

        self.assertEqual([], figure_only_result.changes)  # 只有图片块变化时不应生成报告卡片。

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

        self.assertEqual([], axis_label_result.changes)  # `Amplitude X` 不应成为用户报告里的新增片段。

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

        self.assertEqual([], formula_label_result.changes)  # `IL min =` 不应成为删除片段。

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
        self.assertNotIn("Figure 32- R cd", corrupt_snippets)
        self.assertNotIn("Figure cd 32-5", corrupt_snippets)
        self.assertNotIn("6) (TBI)", corrupt_snippets)

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
        )  # 纯符号和图轴/表头残片不应成为删除差异。
        self.assertEqual([], orphan_symbol_result.changes)

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
        """Untitled continuation pages should stay in the same visual table group."""

        old_tables = [
            TableVisual(
                page_number=4,
                table_number=1,
                title="Table 32-1. COM Parameter Values",
                bbox=(0.0, 0.0, 100.0, 100.0),
                image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
                row_texts=["表格行: T1 | Parameter=Head | Value=old"],
                grid_summary="OpenCV 网格检测: 横线 4 条，竖线 3 条",
            ),
            TableVisual(
                page_number=5,
                table_number=1,
                title="",
                bbox=(0.0, 0.0, 100.0, 100.0),
                image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
                row_texts=["表格行: T1 | Parameter=Tail | Value=old"],
                grid_summary="OpenCV 网格检测: 横线 4 条，竖线 3 条",
                is_continuation=True,
            ),
        ]  # 旧版真实抽取常只有第一页有表题，续页没有 title。
        new_tables = [
            TableVisual(
                page_number=6,
                table_number=1,
                title="Table 32-1. COM Parameter Values",
                bbox=(0.0, 0.0, 100.0, 100.0),
                image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
                row_texts=["表格行: T1 | Parameter=Head | Value=new"],
                grid_summary="OpenCV 网格检测: 横线 4 条，竖线 3 条",
            ),
            TableVisual(
                page_number=7,
                table_number=1,
                title="",
                bbox=(0.0, 0.0, 100.0, 100.0),
                image_data_uri="data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2w==",
                row_texts=["表格行: T1 | Parameter=Tail | Value=new"],
                grid_summary="OpenCV 网格检测: 横线 4 条，竖线 3 条",
                is_continuation=True,
            ),
        ]  # 新版同样只有首页有表题。

        groups = _paired_table_visuals(old_tables, new_tables)

        self.assertEqual(1, len(groups))  # 有标题首页和无标题续页应合成一个逻辑表格组。
        self.assertEqual([4, 5], [table.page_number for table in groups[0].old_tables])
        self.assertEqual([6, 7], [table.page_number for table in groups[0].new_tables])

    def test_table_visual_summary_reports_omissions_and_avoids_bad_pairing(self) -> None:
        """Visual table summaries should avoid false pairs and visible truncation."""

        old_rows = [f"表格行: T1 | Parameter=P{index} | Value={index}" for index in range(20)]  # 构造 20 行旧表变化。
        new_rows = [f"表格行: T1 | Parameter=P{index} | Value={index + 1}" for index in range(20)]  # 构造 20 行新表变化。
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

        self.assertIn("另有 8 行表格变化未展示", html)  # 20 行变化只展示 12 行时必须说明遗漏数量。
        self.assertGreaterEqual(html.count("无对应表格截图"), 2)  # 不相似的新旧表不能按顺序硬凑成一组。
        self.assertIn("未检测到行级变化", html)  # fb*n 与 fb×n 是同一数学表达，不应报表格变化。

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
                app.unchanged_similarity_var.set("0.99")  # 调整未变化阈值，验证高级参数仍能读取。
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
                    for character in value:
                        entry.event_generate(f"<KeyPress-{character}>")  # 发送真实按键按下事件。
                        entry.event_generate(f"<KeyRelease-{character}>")  # 发送真实按键释放事件。
                    root.update()  # 处理键盘事件，让输入框文本完成更新。

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
        self.assertIn("填入 Demo 文件", widget_texts)  # Demo 按钮只填路径，不再自动开跑导致卡顿。
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

    @unittest.skipUnless(
        sys.platform == "darwin" or os.name == "nt" or os.environ.get("DISPLAY"),
        "Tk demo test needs a desktop session",
    )
    def test_desktop_gui_demo_button_only_fills_inputs(self) -> None:
        """Demo action should prepare sample files without starting a comparison."""

        import tkinter as tk  # Tk 用于创建真实桌面窗口并调用 Demo 按钮逻辑。

        root = tk.Tk()  # 创建窗口，确保 Demo 行为在真实 Tk 环境中运行。
        root.geometry("980x700+0+0")  # 固定窗口尺寸，避免布局差异影响控件状态。
        try:
            app = ProtocolDiffDesktopApp(root)  # 构建完整 GUI。
            root.update()  # 处理初始布局事件。
            app.old_start_var.set("9")  # 先填一个旧值，用于确认 Demo 会清空页码范围。
            app.new_end_var.set("10")  # 先填一个旧值，用于确认 Demo 会清空页码范围。

            app.run_demo()  # 调用 Demo 行为；它应该只填 PDF 路径，不启动比较线程。
            root.update()  # 处理 Demo 更新到界面的状态文本。

            old_pdf_path = Path(app.old_pdf_var.get())  # 读取 Demo 填入的旧 PDF 路径。
            new_pdf_path = Path(app.new_pdf_var.get())  # 读取 Demo 填入的新 PDF 路径。
            summary_text = app.summary_var.get()  # 读取结果摘要，确认没有进入比较完成状态。
            status_text = app.status_var.get()  # 读取状态栏，确认提示用户手动开始比较。
            report_text = app.report_path_var.get()  # 读取报告路径，确认还没有生成报告。
            last_outputs = app._last_outputs  # 读取最近输出，确认没有后台比较结果。
            old_start_value = app.old_start_var.get()  # 销毁窗口前缓存旧协议起始页变量。
            new_end_value = app.new_end_var.get()  # 销毁窗口前缓存新协议终止页变量。
        finally:
            root.destroy()  # 销毁窗口，避免影响后续测试。

        self.assertTrue(old_pdf_path.exists())  # Demo 应该生成并填入旧 PDF。
        self.assertTrue(new_pdf_path.exists())  # Demo 应该生成并填入新 PDF。
        self.assertEqual("", old_start_value)  # Demo 应清空旧协议起始页，方便用户重新输入。
        self.assertEqual("", new_end_value)  # Demo 应清空新协议终止页，方便用户重新输入。
        self.assertEqual("Demo 文件已填入", summary_text)  # 摘要应停留在“已填入”，而不是“比较完成”。
        self.assertIn("点击开始比较", status_text)  # 状态栏应提示用户手动开始。
        self.assertEqual("", report_text)  # 未点击开始前不应该有报告路径。
        self.assertIsNone(last_outputs)  # 未点击开始前不应该有比较输出。

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

        self.assertNotIn("表格行:", snippet_text)  # 正文卡片不应再重复内部结构化表格行。
        self.assertNotIn("表格行:", html)  # HTML 报告不应暴露内部表格行格式。
        self.assertIn("表格截图识别", html)  # 表格变化应转移到下方视觉表格区。
        self.assertIn("Single-ended reference resistance", html)  # 用户关心的参数名仍必须可搜索定位。
        self.assertIn("46.25", html)  # 新版值必须在表格摘要或截图区可见。
        self.assertIn("50", html)  # 旧版值必须在表格摘要或截图区可见。

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
        self.assertIn(
            "表格行: T1 | Parameter=Single-ended reference resistance | Symbol=R0 | Value=50 | Units=Ω",
            continuation_lines,
        )  # 续页第一条参数必须被拆成独立结构化行。
        self.assertIn(
            "表格行: T1 | Parameter=Single-ended termination resistance | Symbol=Rd | Value=46.25 | Units=Ω",
            continuation_lines,
        )  # 符号上下标碎片应合并为 Rd，并和对应数值同行。

    def test_table_expansion_skips_device_package_group_label(self) -> None:
        """Embedded table group labels should not shift parameter/value alignment."""

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

        self.assertIn(
            "表格行: T1 | Parameter=Single-ended PKG capacitance | Symbol=Cp | Value=40 | Units=fF",
            lines,
        )  # 第一条参数不能拿到下一行的 87.5Ω。
        self.assertIn(
            "表格行: T1 | Parameter=Transmission line 2 characteristic impedance | Symbol=Zc2 | Value=87.5 | Units=Ω",
            lines,
        )  # 特性阻抗应与 87.5Ω 对齐。
        self.assertIn(
            "表格行: T1 | Parameter=Single-ended PKG capacitance at pkg-to-board IF | Symbol=Cp | Value=40 | Units=fF",
            lines_with_stray_value,
        )  # 孤立 T 不能让 PKG capacitance 错拿 87.5Ω。
        self.assertIn(
            "表格行: T1 | Parameter=Transmission line 2 characteristic impedance | Symbol=Zc2 | Value=95 | Units=Ω",
            lines_with_stray_value,
        )  # 孤立 T 被过滤后，后续阻抗值应继续对齐。
        self.assertFalse(
            any("Parameter=D / Device package model" in line for line in lines_with_stray_value),
            lines_with_stray_value,
        )  # DRAFT 水印残片不能让整张表退化成一条巨大聚合行。

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

        self.assertIn("Value=A", "\n".join(old_lines))  # 结构化表格行自身仍保留旧值 A。
        self.assertIn("Value=B", "\n".join(new_lines))  # 结构化表格行自身仍保留新值 B。
        self.assertNotIn("Value=A", snippets)  # 正文差异卡片不再展示纯表格行。
        self.assertNotIn("Value=B", snippets)  # 新值 B 也不应刷进正文区。

    def test_structured_table_lines_suppress_duplicate_raw_table_text(self) -> None:
        """Raw long table text should disappear when structured rows cover it."""

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

        combined = _combine_text_and_table_lines(text, table_lines)  # 合并正文和结构化表格行，并执行重复表格长行过滤。

        self.assertIn("This ordinary paragraph should stay visible.", combined)  # 普通正文不应被降噪误删。
        self.assertNotIn(raw_short_table_row, combined)  # 已结构化的单条原始表格行也不应再进入 diff 合并阶段。
        self.assertNotIn(raw_table_line, combined)  # 已被结构化表格覆盖的超长原始表格行应被删除。
        self.assertIn("Parameter=Single-ended reference resistance", combined)  # 结构化表格行必须保留用于后续 diff。

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
        self.assertNotIn("3 Acceptance", locations)
        self.assertEqual("3", technical.old_section.page_range)
        self.assertEqual("4", technical.new_section.page_range)
        self.assertIn("3.0 V", all_snippets)
        self.assertIn("2.8 V", all_snippets)
        self.assertNotIn("Confidential", all_snippets)
        self.assertNotIn("Page 2 of", all_snippets)
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

    def test_raw_table_blocks_are_suppressed_when_structured_rows_exist(self) -> None:
        """Diff snippets should prefer structured table rows over raw table blocks."""

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

        self.assertNotIn("COM Parameter Values Device package model", snippets)  # 原始大块表格文本不应污染报告。
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
        self.assertIn("class of transmitter package", added_text)  # 单独下标 `p` 不能插入完整句中。
        self.assertNotIn("class of p transmitter package", added_text)  # 抽取残片不能污染新增正文。
        self.assertIn("should be used.", added_text)  # 新增片段不能被截成半句。

    def test_fragmentary_table_reference_snippets_are_hidden_from_text_cards(self) -> None:
        """Short table/equation row fragments should not crowd prose diffs."""

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

        self.assertNotIn("Conversion (32-6)", snippets)  # 短公式项不再作为正文句子展示。
        self.assertNotIn("Interference Tolerance Table 32-10", snippets)  # 表格索引项不再刷屏。
        self.assertNotIn("Jitter Tolerance Table 32-11", snippets)  # 表格索引项不再刷屏。
        self.assertNotIn("Block Error Ratio, Note 3 3.2e-13", snippets)  # 表格值行应由表格区承担。
        self.assertTrue(_is_global_noise_snippet("03"))  # 单独数值残片不应出现在正文卡片。
        self.assertTrue(_is_global_noise_snippet("-1 -1/3 1/3 1"))  # 表格数值串不应出现在正文卡片。
        self.assertTrue(_is_global_noise_snippet("UNIT"))  # 表头词不应单独显示。
        self.assertTrue(_is_global_noise_snippet("Note 2D"))  # 脚注编号残片不应单独显示。
        self.assertTrue(_is_global_noise_snippet("FFE_Post"))  # 孤立表格标识符不应单独显示。
        self.assertTrue(_is_global_noise_snippet("fx bx FFE_Post"))  # 短标识符组合也不应单独显示。
        self.assertTrue(_is_global_noise_snippet("| MAX=1000 | UNIT=mVppd"))  # 表格单元串不应当作正文句。
        self.assertTrue(_is_global_noise_snippet("Baud Rate R_Baud 72 116 Gsym/s"))  # Baud Rate 表头行应隐藏。
        self.assertTrue(_is_global_noise_snippet("SeFe Section"))  # 被水印残字污染的 See Section 表头应隐藏。
        self.assertTrue(_is_global_noise_snippet("NOTES: D"))  # 表格脚注表头残片应隐藏。
        self.assertIn("The receiver shall use BERadded=1e-4", snippets)  # 真正文句仍保留。
        self.assertIn("The receiver shall use BER =1e-4", snippets)  # 新正文句也必须完整可见。

    def test_pcie_running_page_furniture_is_suppressed_from_text_cards(self) -> None:
        """PCIe running headers, revision lines, and dates should not be reported."""

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

        self.assertNotIn("PCI Express Architecture PHY Test Specification", snippets)  # 页眉不应抢占差异列表。
        self.assertNotIn("Revision 4.0, Version 1.2", snippets)  # 旧版页脚版本行不应显示为删除。
        self.assertNotIn("Revision 6.0", snippets)  # 新版页脚版本行不应显示为新增。
        self.assertNotIn("August 18, 2021", snippets)  # 旧版发布日期页脚不应显示为删除。
        self.assertNotIn("April 22, 2026", snippets)  # 新版发布日期页脚不应显示为新增。
        self.assertIn("steps 16 and 17", snippets)  # 真正的步骤号变化仍要保留。
        self.assertIn("steps 19 and 20", snippets)  # 新侧步骤号变化同样保留。
        self.assertTrue(_is_global_noise_snippet("PCI Express Architecture PHY Test Specification | 78"))  # 单独 PCIe 页眉是全局噪声。
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
        self.assertEqual([], footer_units)  # 页眉簇不会进入正文 diff。
        self.assertIn("Revision 6.0", cleaned_body)  # 抽取层保留独立修订正文。
        self.assertIn("April 22, 2026", cleaned_body)  # 抽取层保留独立日期正文。
        self.assertEqual("", cleaned_footer)  # 抽取层删除完整 PCIe 页眉/页脚簇。
        self.assertIn("Revision 5.0", snippets)  # 最终报告路径保留旧修订正文。
        self.assertIn("Revision 6.0", snippets)  # 最终报告路径保留新修订正文。
        self.assertIn("March 1, 2025", snippets)  # 最终报告路径保留旧日期正文。
        self.assertIn("April 22, 2026", snippets)  # 最终报告路径保留新日期正文。

    def test_embedded_pcie_running_page_furniture_is_stripped_from_replacements(self) -> None:
        """PCIe header/footer text glued inside a sentence should be removed."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_embedded_pcie_header.pdf"),  # 旧侧是没有页脚污染的干净步骤句。
            pages=[
                PageText(
                    page_number=37,
                    text=(
                        "1 Scope\n"
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

        self.assertNotIn("PCI Express Architecture PHY Test Specification", snippets)  # 句中页眉不应残留。
        self.assertNotIn("Revision 6.0", snippets)  # 句中版本页脚不应残留。
        self.assertNotIn("April 22, 2026", snippets)  # 句中日期页脚不应残留。
        self.assertIn("Appendix C", snippets)  # 旧侧真实附录引用仍保留。
        self.assertIn("Appendix A", snippets)  # 新侧真实附录引用仍保留。

    def test_inline_highlight_ignores_case_and_display_only_variants(self) -> None:
        """Inline highlights should ignore case, arrows, spacing, and spelling variants."""

        old_html, new_html = _inline_diff_html(
            "computed Sj through Generator→SMP Adaptor at 30dB before step 16 and P5 at E-12.",
            "computed SJ through Generator->SMP Adapter at 30 dB before step 19 and Preset 5 at 1e-12.",
        )  # 只有步骤号是实质变化；其他都是抽取或显示格式差异。
        amp_old_html, amp_new_html = _inline_diff_html(
            "Eye Width & Extrapolated Eye Height",
            "Eye Width and Extrapolated Eye Height",
        )  # & 与 and 是同义连接符，不应在报告里制造高亮。

        self.assertNotIn("<mark", old_html.split("before")[0])  # 旧侧步骤号前的大小写/箭头/拼写/空格不应高亮。
        self.assertNotIn("<mark", new_html.split("before")[0])  # 新侧步骤号前的等价显示差异同样不应高亮。
        self.assertNotIn("<mark", old_html.split("and P5")[1])  # P5/E-12 不应作为格式噪声高亮。
        self.assertNotIn("<mark", new_html.split("and Preset 5")[1])  # Preset 5/1e-12 同样不应高亮。
        self.assertNotIn("<mark", amp_old_html)  # 旧侧 & 不应被标成删除。
        self.assertNotIn("<mark", amp_new_html)  # 新侧 and 不应被标成新增。
        self.assertIn('<mark class="del">16</mark>', old_html)  # 旧侧真实步骤号变化仍需标红。
        self.assertIn('<mark class="ins">19</mark>', new_html)  # 新侧真实步骤号变化仍需标绿。

    def test_dash_joined_list_clauses_split_into_review_units(self) -> None:
        """A dash-glued list sentence should split before the next If clause."""

        units = _split_line_preserving_numbers(
            "a) All waveform outliers are removed from the average ― If the Eye Width is less than 1.0 ps."
        )  # PCIe 列表有时把 a) 说明和后续 If 子句粘在一行。

        self.assertEqual("a) All waveform outliers are removed from the average", units[0])  # 第一条列表说明独立成句。
        self.assertEqual("If the Eye Width is less than 1.0 ps.", units[1])  # 后续 If 子句可与新版 b. 正确配对。

    def test_leading_table_header_prefix_does_not_create_paragraph_diff(self) -> None:
        """Table header text glued before prose should compare as extraction noise."""

        old_extraction = ExtractionResult(
            pdf_path=Path("old_header_prefix.pdf"),  # 旧侧模拟表头残片粘到 ERL 句前。
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\n"
                        "UNIT Baud Rate R_Baud 72 116 Gsym/s See Section "
                        "Effective return loss (ERL) 11.3 dB"
                    ),
                )
            ],
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new_header_prefix.pdf"),  # 新侧只有真正的 ERL 句。
            pages=[PageText(page_number=1, text="1 Scope\nEffective return loss (ERL) 11.3 dB")],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())  # 表头前缀应被 key 归一化吃掉。

        self.assertEqual([], result.changes)  # 不能为粘连表头生成用户可见替换卡。

    def test_embedded_table_identifier_residue_does_not_create_paragraph_diff(self) -> None:
        """Short table identifiers glued into a sentence should be normalized away."""

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

        self.assertEqual([], result.changes)  # 不应为 `fx bx FFE_post` 生成正文替换卡。

    def test_noise_filter_runs_before_snippet_limit(self) -> None:
        """Table fragments should not consume the only visible snippet slot."""

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

        self.assertIn("receiver shall enable", snippets)  # 真正正文变化不能被前面的表格碎片挤掉。
        self.assertIn("receiver shall disable", snippets)  # 新侧完整句也必须可见。
        self.assertNotIn("Conversion (32", snippets)  # 表格/公式短碎片不占唯一名额。

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
