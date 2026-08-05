"""Public validation tests for accuracy-sensitive comparison options."""

from __future__ import annotations

import math  # 构造 NaN/Inf 边界，确认无效阈值不会静默进入匹配算法。
import os  # 为 Windows 重定向子进程显式指定 UTF-8，避免中文 stderr 被写成反斜杠转义。
from pathlib import Path  # 定位当前项目的 src 目录，保证单文件测试也能独立运行。
import subprocess  # 走真实命令行入口，验证配置错误不会泄露 traceback。
import sys  # 在导入项目包前显式配置本地 src 路径。
import unittest  # 使用仓库现有 unittest 测试框架，不增加依赖。


PROJECT_ROOT = Path(__file__).resolve().parents[1]  # 从测试文件反向定位规范项目根目录。
SRC_DIR = PROJECT_ROOT / "src"  # src-layout 包需要在直接 unittest 运行时显式加入搜索路径。
if str(SRC_DIR) not in sys.path:  # 避免重复插入路径影响其它测试的导入优先级。
    sys.path.insert(0, str(SRC_DIR))  # 让单文件和 discovery 两种运行方式使用同一份本地源码。

from protocol_pdf_diff.desktop_gui import parse_positive_float  # GUI 数值入口也必须执行有限区间验证。
from protocol_pdf_diff.models import DiffOptions  # 公共配置模型是 CLI、GUI 与 API 的统一安全边界。


class AccuracyOptionValidationTests(unittest.TestCase):
    """Reject values that would disable or corrupt section matching."""

    def test_similarity_threshold_requires_a_finite_unit_interval_value(self) -> None:
        """NaN、无穷及区间外数值都不能静默关闭候选匹配。"""

        invalid_values = (math.nan, math.inf, -math.inf, 0.0, -0.1, 1.0001)  # 覆盖非有限值及上下边界外输入。
        for value in invalid_values:  # 每个危险输入都应由共享模型独立拒绝。
            with self.subTest(value=value):  # 失败时显示具体阈值，便于用户定位配置问题。
                with self.assertRaisesRegex(ValueError, "章节匹配阈值"):  # 错误必须明确指出哪个配置无效。
                    DiffOptions(min_section_match_similarity=value)  # 通过公共模型验证 CLI/API 均无法绕过门禁。

        valid_values = (0.01, 0.72, 1.0)  # 合法区间包含严格正下界附近值与闭合上界。
        for value in valid_values:  # 对每个有效边界确认不会误拒绝用户配置。
            with self.subTest(value=value):  # 分开报告有效值回归，避免单一断言掩盖边界错误。
                self.assertEqual(value, DiffOptions(min_section_match_similarity=value).min_section_match_similarity)  # 公共模型应原样保存有效阈值。

    def test_similarity_threshold_rejects_boolean_and_non_numeric_api_values(self) -> None:
        """Python API misuse should produce the same actionable ValueError as CLI input."""

        for value in (True, False, "0.8", None):  # bool 是 int 子类，必须显式拒绝；字符串/None 也不能触发 TypeError。
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "章节匹配阈值"):
                    DiffOptions(min_section_match_similarity=value)  # type: ignore[arg-type]

    def test_gui_similarity_parser_rejects_non_finite_and_above_one_values(self) -> None:
        """GUI 应在启动后台任务前给出可操作的阈值错误。"""

        for raw_value in ("nan", "inf", "1.2"):  # 覆盖 Python float 可解析但不应接受的字符串。
            with self.subTest(raw_value=raw_value):  # 失败时保留用户实际输入文本。
                with self.assertRaisesRegex(ValueError, "0 到 1"):  # GUI 错误需直接说明允许区间。
                    parse_positive_float(raw_value, "章节匹配阈值")  # 走真实 GUI 解析函数而不是复制生产逻辑。

    def test_cli_reports_invalid_similarity_without_traceback(self) -> None:
        """The PyCharm/CLI entry point should catch shared model validation failures."""

        completed = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "main.py"),
                "--min-section-match-similarity",
                "1.2",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            env={
                **os.environ,
                "PYTHONIOENCODING": "utf-8",
            },  # Windows 管道不是交互控制台；输出端和父进程解码均固定 UTF-8，避免 cp1252 误解码中文。
        )

        self.assertEqual(2, completed.returncode)
        self.assertIn("运行失败: 章节匹配阈值", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)


if __name__ == "__main__":  # 支持在 PyCharm 中直接运行当前测试文件。
    unittest.main()  # 使用标准 unittest runner 输出可复现结果。
