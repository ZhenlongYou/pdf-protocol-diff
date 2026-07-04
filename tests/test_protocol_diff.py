"""Regression tests for the protocol PDF diff workflow.

These tests use the built-in minimal PDFs so validation does not depend on any
company document. They cover the user-facing promise: old/new PDFs are accepted,
reports are produced, and chapter/section changes are classified.
"""

# Codex说明(自动生成)： 从 __future__ 导入 annotations，启用较新的类型标注行为，减少运行期导入或前向引用问题。
from __future__ import annotations

# Codex说明(自动生成)： 导入 tempfile，创建测试或 demo 使用的临时文件目录。
import tempfile
# Codex说明(自动生成)： 导入 unittest，组织单元测试和断言。
import unittest
# Codex说明(自动生成)： 从 pathlib 导入 Path，用 Path 对象处理跨平台文件路径。
from pathlib import Path
# Codex说明(自动生成)： 导入 sys，访问解释器路径、退出码和标准错误输出。
import sys
# Codex说明(自动生成)： 从 argparse 导入 Namespace，解析命令行参数，支持用户从终端覆盖默认配置。
from argparse import Namespace

# Codex说明(自动生成)： 计算并保存 PROJECT_ROOT，供后续语句继续读取或更新。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
# Codex说明(自动生成)： 计算并保存 SRC_DIR，供后续语句继续读取或更新。
SRC_DIR = PROJECT_ROOT / "src"
# Codex说明(自动生成)： 检查条件 str(SRC_DIR) not in sys.path，根据结果选择后续执行路径。
if str(SRC_DIR) not in sys.path:
    # Codex说明(自动生成)： 调用 sys.path.insert 更新列表或集合，把当前步骤产生的数据加入结果。
    sys.path.insert(0, str(SRC_DIR))
# Codex说明(自动生成)： 检查条件 str(PROJECT_ROOT) not in sys.path，根据结果选择后续执行路径。
if str(PROJECT_ROOT) not in sys.path:
    # Codex说明(自动生成)： 调用 sys.path.insert 更新列表或集合，把当前步骤产生的数据加入结果。
    sys.path.insert(0, str(PROJECT_ROOT))

# Codex说明(自动生成)： 从 main 导入 resolve_inputs，提供本文件后续流程需要的库能力。
from main import resolve_inputs
# Codex说明(自动生成)： 从 protocol_pdf_diff.compare 导入 compare_extractions，提供本文件后续流程需要的库能力。
from protocol_pdf_diff.compare import compare_extractions
# Codex说明(自动生成)： 从 protocol_pdf_diff.compare 导入 run_diff，提供本文件后续流程需要的库能力。
from protocol_pdf_diff.compare import run_diff
# Codex说明(自动生成)： 从 protocol_pdf_diff.models 导入 DiffOptions, ExtractionResult, PageText，提供本文件后续流程需要的库能力。
from protocol_pdf_diff.models import DiffOptions, ExtractionResult, PageText
# Codex说明(自动生成)： 从 protocol_pdf_diff.reporting 导入 write_reports，提供本文件后续流程需要的库能力。
from protocol_pdf_diff.reporting import write_reports
# Codex说明(自动生成)： 从 protocol_pdf_diff.sample_data 导入 write_demo_pdfs，提供本文件后续流程需要的库能力。
from protocol_pdf_diff.sample_data import write_demo_pdfs
# Codex说明(自动生成)： 从 protocol_pdf_diff.sectioning 导入 section_document，提供本文件后续流程需要的库能力。
from protocol_pdf_diff.sectioning import section_document


# Codex说明(自动生成)： 定义 ProtocolDiffTests 类，把相关数据结构、校验规则或操作方法组织在一起。
class ProtocolDiffTests(unittest.TestCase):
    """End-to-end tests over generated old/new sample PDFs."""

    # Codex说明(自动生成)： 定义函数 test_demo_pdfs_produce_modified_and_added_sections，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
    def test_demo_pdfs_produce_modified_and_added_sections(self) -> None:
        # Codex说明(自动生成)： 进入上下文 tempfile.TemporaryDirectory()，确保文件、资源或临时状态按作用域正确释放。
        with tempfile.TemporaryDirectory() as temp_dir:
            # Codex说明(自动生成)： 计算并保存 (old_pdf, new_pdf)，供后续语句继续读取或更新。
            old_pdf, new_pdf = write_demo_pdfs(Path(temp_dir) / "inputs")
            # Codex说明(自动生成)： 计算并保存 result，供后续语句继续读取或更新。
            result = run_diff(old_pdf, new_pdf, DiffOptions())

            # Codex说明(自动生成)： 计算并保存 change_types，供后续语句继续读取或更新。
            change_types = [change.change_type for change in result.changes]
            # Codex说明(自动生成)： 计算并保存 locations，供后续语句继续读取或更新。
            locations = [change.report_location for change in result.changes]

            # Codex说明(自动生成)： 调用 self.assertIn 检查测试期望，确认实际结果符合预期。
            self.assertIn("modified", change_types)
            # Codex说明(自动生成)： 调用 self.assertIn 检查测试期望，确认实际结果符合预期。
            self.assertIn("added", change_types)
            # Codex说明(自动生成)： 调用 self.assertTrue 检查测试期望，确认实际结果符合预期。
            self.assertTrue(any("1.1 Delivery" in location for location in locations))
            # Codex说明(自动生成)： 调用 self.assertTrue 检查测试期望，确认实际结果符合预期。
            self.assertTrue(any("2.1 Security" in location for location in locations))

    # Codex说明(自动生成)： 定义函数 test_reports_are_written，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
    def test_reports_are_written(self) -> None:
        # Codex说明(自动生成)： 进入上下文 tempfile.TemporaryDirectory()，确保文件、资源或临时状态按作用域正确释放。
        with tempfile.TemporaryDirectory() as temp_dir:
            # Codex说明(自动生成)： 计算并保存 temp_path，供后续语句继续读取或更新。
            temp_path = Path(temp_dir)
            # Codex说明(自动生成)： 计算并保存 (old_pdf, new_pdf)，供后续语句继续读取或更新。
            old_pdf, new_pdf = write_demo_pdfs(temp_path / "inputs")
            # Codex说明(自动生成)： 计算并保存 result，供后续语句继续读取或更新。
            result = run_diff(old_pdf, new_pdf, DiffOptions())
            # Codex说明(自动生成)： 计算并保存 outputs，供后续语句继续读取或更新。
            outputs = write_reports(result, temp_path / "reports", DiffOptions())

            # Codex说明(自动生成)： 遍历 ('markdown', 'text', 'csv', 'json') 中的 key，逐项执行循环体逻辑。
            for key in ("markdown", "text", "csv", "json"):
                # Codex说明(自动生成)： 调用 self.assertTrue 检查测试期望，确认实际结果符合预期。
                self.assertTrue(outputs[key].exists(), key)
            # Codex说明(自动生成)： 计算并保存 report_text，供后续语句继续读取或更新。
            report_text = outputs["text"].read_text(encoding="utf-8")
            # Codex说明(自动生成)： 调用 self.assertIn 检查测试期望，确认实际结果符合预期。
            self.assertIn("协议 PDF 差异报告", report_text)
            # Codex说明(自动生成)： 调用 self.assertIn 检查测试期望，确认实际结果符合预期。
            self.assertIn("Delivery", report_text)
            # Codex说明(自动生成)： 调用 self.assertIn 检查测试期望，确认实际结果符合预期。
            self.assertIn("3.0 V", report_text)
            # Codex说明(自动生成)： 调用 self.assertIn 检查测试期望，确认实际结果符合预期。
            self.assertIn("2.8 V", report_text)

    # Codex说明(自动生成)： 定义函数 test_invalid_explicit_paths_do_not_fall_back_to_demo，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
    def test_invalid_explicit_paths_do_not_fall_back_to_demo(self) -> None:
        # Codex说明(自动生成)： 计算并保存 args，供后续语句继续读取或更新。
        args = Namespace(
            demo=False,
            old_pdf="/path/that/does/not/exist/old.pdf",
            new_pdf="/path/that/does/not/exist/new.pdf",
        )

        # Codex说明(自动生成)： 进入上下文 self.assertRaises(FileNotFoundError)，确保文件、资源或临时状态按作用域正确释放。
        with self.assertRaises(FileNotFoundError):
            # Codex说明(自动生成)： 调用 resolve_inputs，执行当前流程需要的具体操作或副作用。
            resolve_inputs(args)

    # Codex说明(自动生成)： 定义函数 test_title_only_change_is_reported，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
    def test_title_only_change_is_reported(self) -> None:
        # Codex说明(自动生成)： 计算并保存 old_extraction，供后续语句继续读取或更新。
        old_extraction = ExtractionResult(
            pdf_path=Path("old.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nThe requirement is unchanged.")],
        )
        # Codex说明(自动生成)： 计算并保存 new_extraction，供后续语句继续读取或更新。
        new_extraction = ExtractionResult(
            pdf_path=Path("new.pdf"),
            pages=[PageText(page_number=1, text="1 Applicability\nThe requirement is unchanged.")],
        )

        # Codex说明(自动生成)： 计算并保存 result，供后续语句继续读取或更新。
        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        # Codex说明(自动生成)： 调用 self.assertEqual 检查测试期望，确认实际结果符合预期。
        self.assertEqual(["modified"], [change.change_type for change in result.changes])
        # Codex说明(自动生成)： 调用 self.assertIn 检查测试期望，确认实际结果符合预期。
        self.assertIn("章节标题: 1 Scope", result.changes[0].replaced_snippets[0].old)
        # Codex说明(自动生成)： 调用 self.assertIn 检查测试期望，确认实际结果符合预期。
        self.assertIn("章节标题: 1 Applicability", result.changes[0].replaced_snippets[0].new)

    # Codex说明(自动生成)： 定义函数 test_standalone_numeric_headings_are_merged_with_next_title_line，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
    def test_standalone_numeric_headings_are_merged_with_next_title_line(self) -> None:
        # Codex说明(自动生成)： 计算并保存 extraction，供后续语句继续读取或更新。
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

        # Codex说明(自动生成)： 计算并保存 sections，供后续语句继续读取或更新。
        sections = section_document(extraction)
        # Codex说明(自动生成)： 计算并保存 locations，供后续语句继续读取或更新。
        locations = [section.location for section in sections]

        # Codex说明(自动生成)： 调用 self.assertIn 检查测试期望，确认实际结果符合预期。
        self.assertIn("1 适用范围", locations[0])
        # Codex说明(自动生成)： 调用 self.assertIn 检查测试期望，确认实际结果符合预期。
        self.assertIn("1 适用范围 / 1.1 交付要求", locations[1])

    # Codex说明(自动生成)： 定义函数 test_chinese_chapter_and_section_headings_are_detected，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
    def test_chinese_chapter_and_section_headings_are_detected(self) -> None:
        # Codex说明(自动生成)： 计算并保存 extraction，供后续语句继续读取或更新。
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

        # Codex说明(自动生成)： 计算并保存 sections，供后续语句继续读取或更新。
        sections = section_document(extraction)
        # Codex说明(自动生成)： 计算并保存 locations，供后续语句继续读取或更新。
        locations = [section.location for section in sections]

        # Codex说明(自动生成)： 调用 self.assertEqual 检查测试期望，确认实际结果符合预期。
        self.assertEqual("第一章 总则", locations[0])
        # Codex说明(自动生成)： 调用 self.assertEqual 检查测试期望，确认实际结果符合预期。
        self.assertEqual("第一章 总则 / 第2节 交付要求", locations[1])


# Codex说明(自动生成)： 检查条件 __name__ == '__main__'，根据结果选择后续执行路径。
if __name__ == "__main__":
    # Codex说明(自动生成)： 调用 unittest.main，执行当前流程需要的具体操作或副作用。
    unittest.main()
