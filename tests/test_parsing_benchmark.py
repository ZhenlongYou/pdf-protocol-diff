"""通过公开接口验证 extraction-only PDF 解析基准的行为契约。"""

from __future__ import annotations

import json  # 把 summary 序列化后检查隐私字段是否意外泄漏。
from pathlib import Path  # 使用跨平台路径对象创建临时 manifest 和 PDF。
import shutil  # 根据真实 tesseract 可执行文件决定 OCR case 必须 pass 还是 skip。
import subprocess  # 从真实命令行边界验证 stdout、输出文件和退出码。
import sys  # CLI 测试复用当前 canonical venv 解释器，避免环境漂移。
import tempfile  # 所有合成输入都放在自动清理的临时目录中。
import unittest  # 沿用项目现有的标准库测试框架。

from protocol_pdf_diff.parsing_benchmark import (
    _controlled_manifest,
    _write_positioned_text_pdf,
    run_controlled_parsing_benchmark,
    run_parsing_benchmark,
    validate_parsing_benchmark_manifest,
)
from protocol_pdf_diff.pdf_extract import extract_pdf_text
from protocol_pdf_diff.sample_data import write_multipage_text_pdf


class ParsingBenchmarkTests(unittest.TestCase):
    """覆盖 manifest runner、受控 fixtures 与 CLI 的公开行为。"""

    def _run_cases(
        self,
        root: Path,
        cases: list[dict[str, object]],
    ) -> dict[str, object]:
        """写入最小 manifest 并通过公开 runner 返回 summary。"""

        # 每个测试自行拥有 manifest，避免 case 间共享磁盘状态。
        manifest_path = root / "manifest.json"
        manifest_path.write_text(
            json.dumps({"schema_version": 1, "cases": cases}),
            encoding="utf-8",
        )
        # 显式 corpus root 让测试覆盖生产调用者传参路径。
        return run_parsing_benchmark(manifest_path, corpus_root=root)

    def test_runner_reports_anchor_order_failures_without_leaking_private_context(self) -> None:
        """输入 PDF 只影响有限指标和失败原因，不能把全文或根目录写入 summary。"""

        # 用正常 PDF helper 生成可提取文本，确保测试走真实公开抽取路径。
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_multipage_text_pdf(
                root / "private-sensitive.pdf",
                [[
                    "1 Scope",
                    "left-column anchor",
                    "right-column anchor",
                    "forbidden draft marker",
                    "PRIVATE FULL TEXT SENTINEL",
                ]],
            )
            # 同时制造缺失、禁用和逆序三类锚点错误，锁定机器可读失败语义。
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "cases": [
                            {
                                "id": "PRIVATE CASE IDENTIFIER",
                                "required": True,
                                "description": "PRIVATE CASE DESCRIPTION",
                                "document": {"path": "private-sensitive.pdf"},
                                "expect": {
                                    "must_extract": [
                                        "missing required anchor",
                                        "PRIVATE MANIFEST ASSERTION TOKEN",
                                    ],
                                    "must_not_extract": ["forbidden draft marker"],
                                    "ordered_extract": [
                                        "right-column anchor",
                                        "left-column anchor",
                                    ],
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            # 只通过公开 runner 观察最终 summary，不耦合任何私有 helper。
            summary = run_parsing_benchmark(manifest_path, corpus_root=root)

        # 三类失败必须归入同一 case，方便 CI 一次展示完整解析退化证据。
        self.assertEqual("fail", summary["status"])
        failures = "\n".join(summary["cases"][0]["failures"])
        self.assertIn("must_extract", failures)
        self.assertIn("must_not_extract", failures)
        self.assertIn("ordered_extract", failures)
        # JSON summary 不得持久化全文、解析后的 corpus root 或任何 manifest
        # 语义字段；本机只可按输入数组的 case_index 关联回 manifest。
        serialized = json.dumps(summary, ensure_ascii=False)
        self.assertNotIn(str(root), serialized)
        self.assertNotIn("PRIVATE FULL TEXT SENTINEL", serialized)
        self.assertNotIn("PRIVATE MANIFEST ASSERTION TOKEN", serialized)
        self.assertNotIn("PRIVATE CASE IDENTIFIER", serialized)
        self.assertNotIn("PRIVATE CASE DESCRIPTION", serialized)
        self.assertNotIn("private-sensitive.pdf", serialized)
        self.assertNotIn("report_path", serialized)
        self.assertNotIn("sha256", serialized)
        self.assertEqual(1, summary["cases"][0]["case_index"])
        self.assertNotIn("id", summary["cases"][0])

    def test_ordered_anchor_positions_may_overlap_but_must_strictly_increase(self) -> None:
        """顺序门禁比较 literal 起始位置，不得错误要求两个锚点彼此不重叠。"""

        # ``AB`` 与其尾部 ``B`` 的开始位置是 0、1，满足严格单调但文本区间重叠。
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_multipage_text_pdf(root / "overlap.pdf", [["AB"]])
            summary = self._run_cases(
                root,
                [
                    {
                        "id": "overlapping-literals",
                        "required": True,
                        "document": {"path": "overlap.pdf"},
                        "expect": {"ordered_extract": ["AB", "B"]},
                    }
                ],
            )

        self.assertEqual("pass", summary["status"], summary)

    def test_validator_returns_all_strict_schema_errors(self) -> None:
        """单次校验应报告未知字段、路径、页窗、状态、OCR 与 anchor 的全部错误。"""

        # 两个 case 复用 ID，并在每一层放入未知字段，验证错误不会首项即停。
        manifest = {
            "schema_version": True,
            "casez": [],
            "cases": [
                {
                    "id": "duplicate-id",
                    "required": "yes",
                    "description": 7,
                    "ocr_language": [],
                    "document": {
                        "path": "/private/source.txt",
                        "start_page": 3,
                        "end_page": 2,
                        "start_pages": 1,
                    },
                    "expect": {
                        "state": "degraded",
                        "states": ["reliable", "reliable", "unknown"],
                        "must_extract": [""],
                        "must_not_extract": "draft",
                        "ordered_extract": [7],
                        "ocr_used": "true",
                        "must_find": [],
                    },
                    "expects": {},
                },
                {
                    "id": "duplicate-id",
                    "required": False,
                    "document": {"path": "../escape.pdf", "start_page": 0},
                    "expect": {},
                },
            ],
        }

        # 公开 validator 必须返回列表，让编辑器或 CI 一次修完 manifest。
        failures = "\n".join(validate_parsing_benchmark_manifest(manifest))

        # 每组断言对应一条独立 schema 边界，且绝对路径内容本身不得被回显。
        self.assertIn("schema_version", failures)
        self.assertIn("unsupported top-level keys", failures)
        self.assertIn("unsupported keys: expects", failures)
        self.assertIn("unsupported keys: start_pages", failures)
        self.assertIn("safe relative path", failures)
        self.assertIn("PDF file", failures)
        self.assertIn("end_page must be >= start_page", failures)
        self.assertIn("start_page must be a positive integer", failures)
        self.assertIn("required must be a boolean", failures)
        self.assertIn("description must be a string", failures)
        self.assertIn("ocr_language must be a non-empty string", failures)
        self.assertIn("duplicates", failures)
        self.assertIn("cannot define both state and states", failures)
        self.assertIn("unique quality states", failures)
        self.assertIn("must_extract must be an array of non-empty strings", failures)
        self.assertIn("must_not_extract must be an array of non-empty strings", failures)
        self.assertIn("ordered_extract must be an array of non-empty strings", failures)
        self.assertIn("ocr_used must be a boolean", failures)
        self.assertIn("unsupported keys: must_find", failures)
        self.assertNotIn("/private/source.txt", failures)

    def test_missing_documents_and_ocr_expectation_have_explicit_statuses(self) -> None:
        """optional 缺失应 skip，required 缺失和 OCR 事实不符都应 fail。"""

        # 原生文本 fixture 不应使用 OCR，用反向 expectation 锁定真实布尔判断。
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_multipage_text_pdf(
                root / "native.pdf",
                [["1 Scope", "Native selectable text remains available."]],
            )
            summary = self._run_cases(
                root,
                [
                    {
                        "id": "optional-missing",
                        "required": False,
                        "document": {"path": "optional.pdf"},
                        "expect": {},
                    },
                    {
                        "id": "required-missing",
                        "required": True,
                        "document": {"path": "required.pdf"},
                        "expect": {},
                    },
                    {
                        "id": "native-ocr-mismatch",
                        "required": True,
                        "document": {"path": "native.pdf"},
                        "expect": {"ocr_used": True},
                    },
                ],
            )

        # 三个状态必须独立计数，不能把环境缺文件误报为解析通过。
        self.assertEqual({"pass": 0, "fail": 2, "skip": 1}, summary["counts"])
        results = {case["case_index"]: case for case in summary["cases"]}
        self.assertEqual("skip", results[1]["status"])
        self.assertEqual([], results[1]["failures"])
        self.assertEqual("fail", results[2]["status"])
        self.assertIn("missing required PDF", results[2]["failures"][0])
        self.assertNotIn("required.pdf", json.dumps(results[2]))
        self.assertEqual("fail", results[3]["status"])
        self.assertIn("ocr_used expected True", results[3]["failures"][0])
        # 每个 case 都严格限制为规格允许的 summary 字段。
        allowed_keys = {
            "case_index",
            "status",
            "failures",
            "state",
            "page_count",
            "ocr_pages",
            "warning_count",
            "character_count",
            "elapsed_seconds",
        }
        self.assertTrue(all(set(case) == allowed_keys for case in summary["cases"]))

    def test_corrupt_pdf_error_is_bounded_and_redacts_corpus_root(self) -> None:
        """损坏输入应成为单 case 失败，且异常不应泄露临时 corpus 绝对路径。"""

        # 写入非 PDF 字节但保留 .pdf 扩展，保证 schema 已通过后才触发解析异常。
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "corrupt.pdf").write_text("not a PDF document", encoding="utf-8")
            summary = self._run_cases(
                root,
                [
                    {
                        "id": "corrupt-input",
                        "required": True,
                        "document": {"path": "corrupt.pdf"},
                        "expect": {},
                    }
                ],
            )

        result = summary["cases"][0]
        self.assertEqual("fail", result["status"])
        self.assertTrue(result["failures"])
        self.assertIn("case execution error", result["failures"][0])
        self.assertNotIn(str(root), json.dumps(summary, ensure_ascii=False))
        self.assertNotIn("corrupt.pdf", json.dumps(summary, ensure_ascii=False))

    def test_manifest_read_error_keeps_private_filename_out_of_summary(self) -> None:
        """无法读取 manifest 时也只允许稳定异常类别，不得回显其文件名。"""

        # 不创建目标文件，令公开 runner 走 JSON/OSError 读取错误路径。
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "private-parser-benchmark.json"
            summary = run_parsing_benchmark(manifest_path)

        serialized = json.dumps(summary, ensure_ascii=False)
        self.assertEqual("fail", summary["status"])
        self.assertEqual(0, summary["cases"][0]["case_index"])
        self.assertIn("cannot read manifest", summary["cases"][0]["failures"][0])
        self.assertNotIn("private-parser-benchmark.json", serialized)

    def test_controlled_manifest_locks_table_and_ocr_literal_gates(self) -> None:
        """受控 manifest 必须防回退：无框表和 OCR 的关键量都进入顺序门。"""

        # 直接检查临时 fixture 使用的公开 manifest 形状，避免弱化锚点后仍绿灯。
        cases = _controlled_manifest(tesseract_available=True)["cases"]
        two_columns = cases[1]["expect"]
        borderless_table = cases[3]["expect"]
        raster_ocr = cases[4]["expect"]

        self.assertEqual(
            [
                "LEFT COLUMN BEGIN",
                "Left prose anchor",
                "LEFT COLUMN END",
                "RIGHT COLUMN BEGIN",
                "Right prose anchor",
                "RIGHT COLUMN END",
            ],
            two_columns["ordered_extract"],
        )
        self.assertEqual(
            [
                "Parameter",
                "Minimum",
                "Maximum",
                "Units",
                "Input Voltage",
                "0.8",
                "1.2",
                "V",
                "Timing Window",
                "20",
                "35",
                "ps",
            ],
            borderless_table["ordered_extract"],
        )
        self.assertEqual(
            [
                "SCANNED RECEIVER",
                "VOLTAGE LIMIT",
                "3.3 V",
                "TIMING WINDOW",
                "25 PS",
            ],
            raster_ocr["ordered_extract"],
        )

    def test_controlled_benchmark_exercises_layout_formula_table_and_real_ocr(self) -> None:
        """受控基准应走生产 runner，且有引擎时 raster case 必须实际 OCR。"""

        # 公共入口负责在 TemporaryDirectory 内生成 fixtures、运行并清理。
        summary = run_controlled_parsing_benchmark()

        # 四个原生/布局 fixture 在任何环境都必须通过，不允许因缺 OCR 一起 skip。
        results = {case["case_index"]: case for case in summary["cases"]}
        mandatory_indexes = {1, 2, 3, 4}
        self.assertTrue(mandatory_indexes.issubset(results))
        self.assertTrue(
            all(results[case_index]["status"] == "pass" for case_index in mandatory_indexes),
            summary,
        )
        # 本机发现 Tesseract 时必须证明 OCR 页事实，而不是 mock 或静默跳过。
        ocr_result = results[5]
        if shutil.which("tesseract"):
            self.assertEqual("pass", ocr_result["status"], summary)
            self.assertEqual([1], ocr_result["ocr_pages"])
            self.assertGreater(ocr_result["character_count"], 30)
            self.assertEqual(5, summary["counts"]["pass"])
        else:
            # 无引擎时只允许 OCR case skip，其余四个仍构成有效基准。
            self.assertEqual("skip", ocr_result["status"])
            self.assertEqual(1, summary["counts"]["skip"])
        # summary 仍必须保持 JSON 可序列化，且不出现临时 corpus 的绝对路径。
        serialized = json.dumps(summary, ensure_ascii=False)
        self.assertNotIn("pdf_parsing_benchmark_", serialized)
        self.assertNotIn("controlled-native-linear", serialized)
        self.assertNotIn("english-raster.pdf", serialized)

    def test_true_parallel_columns_use_column_major_text_and_remain_layout_risk(self) -> None:
        """真正纵向重叠的双栏必须先完整左栏再完整右栏，且仍会降级。"""

        # 两栏每一行共享 y 坐标且不含数值单位，证明这是轻量路径允许重排的纯正文双栏。
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "parallel-columns.pdf"
            _write_positioned_text_pdf(
                pdf_path,
                [
                    (54, 740, "LEFT COLUMN BEGIN"),
                    (330, 740, "RIGHT COLUMN BEGIN"),
                    (54, 700, "Left prose anchor begins"),
                    (330, 700, "Right prose anchor begins"),
                    (54, 660, "LEFT COLUMN END"),
                    (330, 660, "RIGHT COLUMN END"),
                ],
            )
            page = extract_pdf_text(pdf_path).pages[0]

        left_end = page.text.casefold().index("left column end")
        right_begin = page.text.casefold().index("right column begin")
        self.assertLess(left_end, right_begin, page.text)
        self.assertTrue(page.layout_risk)

    def test_cli_supports_manifest_and_controlled_modes_with_machine_exit_codes(self) -> None:
        """CLI 应只输出 summary JSON，写可选文件，并按 fail 状态返回 1。"""

        # required missing manifest 提供快速、确定的 exit 1 路径。
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "cases": [
                            {
                                "id": "cli-required-missing",
                                "required": True,
                                "document": {"path": "missing.pdf"},
                                "expect": {},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            cli_path = Path(__file__).resolve().parents[1] / "tools" / "benchmark_pdf_parsing.py"
            failed = subprocess.run(
                [
                    sys.executable,
                    str(cli_path),
                    str(manifest_path),
                    "--corpus-root",
                    str(root),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            # controlled 模式同时覆盖 exit 0 与 --output-json 的父目录创建。
            output_path = root / "summary" / "controlled.json"
            controlled = subprocess.run(
                [
                    sys.executable,
                    str(cli_path),
                    "--controlled",
                    "--output-json",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            # 同时给 positional manifest 和 --controlled 必须由 argparse 拒绝。
            conflicting = subprocess.run(
                [sys.executable, str(cli_path), str(manifest_path), "--controlled"],
                capture_output=True,
                text=True,
                check=False,
            )
            # --controlled 没有用户 corpus 输入，传入 root 必须在参数阶段拒绝。
            invalid_controlled_root = subprocess.run(
                [
                    sys.executable,
                    str(cli_path),
                    "--controlled",
                    "--corpus-root",
                    str(root),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            # stdout 能被直接当作单个 JSON document 解析，不含状态日志或路径提示。
            failed_summary = json.loads(failed.stdout)
            controlled_summary = json.loads(controlled.stdout)
            file_summary = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(1, failed.returncode, failed.stderr)
        self.assertEqual("fail", failed_summary["status"])
        self.assertEqual(0, controlled.returncode, controlled.stderr)
        self.assertEqual("pass", controlled_summary["status"])
        self.assertEqual(controlled_summary, file_summary)
        self.assertEqual(2, conflicting.returncode)
        self.assertEqual("", conflicting.stdout)
        self.assertEqual(2, invalid_controlled_root.returncode)
        self.assertEqual("", invalid_controlled_root.stdout)

    def test_optional_real_document_example_is_strict_and_safe_when_corpus_is_absent(self) -> None:
        """示例清单应复用既有真实文档锚点，缺私有 corpus 时只产生 skip。"""

        # 示例属于源码的一部分，先验证它没有因手工编辑偏离严格 schema。
        project_root = Path(__file__).resolve().parents[1]
        manifest_path = project_root / "corpus" / "parsing_benchmark.example.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual([], validate_parsing_benchmark_manifest(manifest))

        # 显式传空临时 root，保证示例不会意外读取仓库外的用户私有路径。
        with tempfile.TemporaryDirectory() as temp_dir:
            summary = run_parsing_benchmark(manifest_path, corpus_root=Path(temp_dir))

        self.assertEqual("skip", summary["status"])
        self.assertEqual(0, summary["counts"]["fail"])
        self.assertGreaterEqual(summary["counts"]["skip"], 5)


if __name__ == "__main__":
    # 允许从 PyCharm 直接运行本测试文件，同时保持 discovery 行为一致。
    unittest.main()
