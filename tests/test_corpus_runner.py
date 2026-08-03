"""Public-behavior tests for the privacy-safe Corpus v0 runner."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
for import_path in (PROJECT_ROOT, SRC_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from protocol_pdf_diff.sample_data import write_multipage_text_pdf
from tools.validate_corpus import (
    _expectation_failures,
    _material_report_text,
    _review_report_text,
    run_manifest,
    validate_manifest,
)


class CorpusRunnerTests(unittest.TestCase):
    def _run(self, root: Path, cases: list[dict[str, object]]) -> dict[str, object]:
        manifest_path = root / "manifest.json"
        manifest_path.write_text(
            json.dumps({"schema_version": 1, "cases": cases}),
            encoding="utf-8",
        )
        return run_manifest(manifest_path, corpus_root=root)

    def test_manifest_schema_errors_are_reported_as_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "casez": [],
                        "cases": [
                            {
                                "id": "unsafe-case",
                                "kind": "pair",
                                "certification_required": "yes",
                                "old": {
                                    "path": "/private/old.pdf",
                                    "start_page": 0,
                                    "start_pages": 1,
                                },
                                "new": {"path": "../new.pdf"},
                                "expect": {"state": "unknown"},
                                "expects": {},
                            },
                            {
                                "id": "wrong-json-types",
                                "kind": [],
                                "expect": {"state": []},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            summary = run_manifest(manifest_path, corpus_root=root)

        self.assertEqual("fail", summary["status"])
        self.assertEqual({"pass": 0, "fail": 1, "skip": 0}, summary["counts"])
        self.assertEqual("<manifest>", summary["cases"][0]["id"])
        failures = "\n".join(summary["cases"][0]["failures"])
        self.assertIn("schema_version", failures)
        self.assertIn("relative path", failures)
        self.assertIn("start_page", failures)
        self.assertIn("certification_required", failures)
        self.assertIn("state", failures)
        self.assertIn("unsupported top-level keys: casez", failures)
        self.assertIn("unsupported keys: expects", failures)
        self.assertIn("unsupported keys: start_pages", failures)

    def test_example_certification_pairs_have_finite_false_positive_budgets(self) -> None:
        """Every release-gating version pair must bound section and table noise."""

        manifest = json.loads(
            (PROJECT_ROOT / "corpus" / "manifest.example.json").read_text(
                encoding="utf-8"
            )
        )  # 直接审计实际发布命令使用的 manifest，避免测试副本与门禁配置漂移。
        certification_pairs = [
            case
            for case in manifest["cases"]
            if case["kind"] == "pair"
            and case.get("certification_required", True)
        ]  # pair 默认纳入认证；只有显式退出的示例版本对不属于本门禁。

        self.assertTrue(certification_pairs)  # 没有任何真实版本对时，认证清单本身不具备准确度证据。
        self.assertEqual(
            [],
            validate_manifest(manifest, require_certification_oracles=True),
        )  # 直接对实际发布 manifest 执行严格 schema/oracle 门，不依赖私有 PDF 是否存在。
        for case in certification_pairs:
            with self.subTest(case_id=case["id"]):
                expect = case["expect"]
                must_find = expect.get("must_find")
                self.assertIsInstance(must_find, list)  # 正向 oracle 必须是可执行的 literal 锚点列表。
                self.assertTrue(must_find)  # 空列表不能证明工具找到任何已知差异。
                self.assertTrue(all(isinstance(anchor, str) and anchor.strip() for anchor in must_find))
                for field in (
                    "max_technical_section_changes",
                    "max_technical_table_changes",
                ):
                    maximum = expect.get(field)
                    self.assertIsInstance(maximum, int)  # 缺失上限会让任意数量的 false positive 仍然假绿。
                    self.assertNotIsInstance(maximum, bool)  # bool 虽是 int 子类，但不能充当变化预算。
                    self.assertGreaterEqual(maximum, 0)  # 有限非负预算与 manifest schema 的公开契约一致。
                    self.assertLess(maximum, 999_999)  # 复核中的洪泛反例必须被每个认证 case 阻断。

    def test_manifest_read_failure_does_not_leak_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            summary = run_manifest(root / "private-manifest.json", corpus_root=root)

        serialized = json.dumps(summary)
        self.assertEqual("fail", summary["status"])
        self.assertNotIn(str(root), serialized)
        self.assertIn("<manifest>", serialized)

    def test_optional_missing_private_pdf_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            summary = self._run(
                Path(temp_dir),
                [
                    {
                        "id": "optional-private-pair",
                        "kind": "pair",
                        "required": False,
                        "old": {"path": "old-private.pdf"},
                        "new": {"path": "new-private.pdf"},
                        "expect": {},
                    }
                ],
            )

        self.assertEqual("skip", summary["status"])
        self.assertEqual({"pass": 0, "fail": 0, "skip": 1}, summary["counts"])
        self.assertEqual("skip", summary["cases"][0]["status"])

    def test_cli_uses_environment_root_writes_json_and_exits_zero_for_all_skips(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = root / "manifest.json"
            output_path = root / "summary" / "result.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "cases": [
                            {
                                "id": "optional-private-self-diff",
                                "kind": "self_diff",
                                "required": False,
                                "document": {"path": "private.pdf"},
                                "expect": {},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "tools" / "validate_corpus.py"),
                    str(manifest_path),
                    "--output-json",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                check=False,
                env={**os.environ, "PDF_DIFF_CORPUS_ROOT": str(root)},
            )

            stdout_summary = json.loads(completed.stdout)
            file_summary = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("skip", stdout_summary["status"])
        self.assertEqual(stdout_summary, file_summary)

    def test_certification_cli_fails_when_manifest_has_no_real_pair(self) -> None:
        """准确性认证不能把 self-diff 清单误报为版本差异证据。"""

        with tempfile.TemporaryDirectory() as temp_dir:  # 用空目录稳定构造“真实语料一个也没执行”的认证场景。
            root = Path(temp_dir)  # 临时目录同时承载 manifest 和缺失的 corpus 根目录。
            manifest_path = root / "manifest.json"  # 写入一个 optional case，模拟开发机没有私有 PDF。
            manifest_path.write_text(  # 使用真实 CLI 支持的 v1 manifest，而不是直接调用内部函数。
                json.dumps(
                    {
                        "schema_version": 1,
                        "cases": [
                            {
                                "id": "optional-private-self-diff",
                                "kind": "self_diff",
                                "required": False,
                                "document": {"path": "private.pdf"},
                                "expect": {},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(  # 走用户实际调用的 corpus CLI，并启用严格认证门。
                [
                    sys.executable,
                    str(PROJECT_ROOT / "tools" / "validate_corpus.py"),
                    str(manifest_path),
                    "--corpus-root",
                    str(root),
                    "--require-executed",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            summary = json.loads(completed.stdout)  # 解析机器可读结果，验证失败原因而不只看退出码。

        self.assertEqual(1, completed.returncode, completed.stderr)  # 严格认证模式必须阻止全 skip 假绿。
        self.assertEqual("fail", summary["status"])  # JSON 状态与非零退出码保持一致。
        self.assertEqual("<manifest>", summary["cases"][-1]["id"])  # 缺少版本对是认证清单缺陷，不应等到 PDF 执行阶段。
        self.assertIn("certification_required pair", summary["cases"][-1]["failures"][0])  # 提示需要 old/new 差异 oracle。

    def test_certification_fails_when_a_required_pair_skips_beside_a_passing_self_diff(self) -> None:
        """A simple self-diff cannot make a skipped accuracy version pair look certified."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_multipage_text_pdf(
                root / "native.pdf",
                [["1 Scope", "The receiver shall preserve the same controlled limit."]],
            )
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "cases": [
                            {
                                "id": "passing-self-diff",
                                "kind": "self_diff",
                                "required": True,
                                "document": {"path": "native.pdf"},
                                "expect": {"max_technical_section_changes": 0},
                            },
                            {
                                "id": "accuracy-version-pair",
                                "kind": "pair",
                                "required": False,
                                "certification_required": True,
                                "old": {"path": "old-private.pdf"},
                                "new": {"path": "new-private.pdf"},
                                "expect": {
                                    "state": "degraded",
                                    "max_technical_section_changes": 0,
                                    "must_find": ["known version-pair change"],
                                },
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            summary = run_manifest(
                manifest_path,
                corpus_root=root,
                require_executed=True,
            )

        self.assertEqual("fail", summary["status"])
        result_by_id = {case["id"]: case for case in summary["cases"]}
        self.assertEqual("pass", result_by_id["passing-self-diff"]["status"])
        self.assertEqual("fail", result_by_id["accuracy-version-pair"]["status"])
        self.assertIn(
            "认证必跑 case 未执行",
            result_by_id["accuracy-version-pair"]["failures"][0],
        )

    def test_certification_rejects_pair_without_independent_oracle(self) -> None:
        """An executed pair with ``expect={}`` is not accuracy certification."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_multipage_text_pdf(
                root / "old.pdf",
                [["1 Scope", "The receiver shall preserve the controlled limit."]],
            )
            write_multipage_text_pdf(
                root / "new.pdf",
                [["1 Scope", "The receiver shall preserve the revised limit."]],
            )
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "cases": [
                            {
                                "id": "oracle-free-pair",
                                "kind": "pair",
                                "certification_required": True,
                                "old": {"path": "old.pdf"},
                                "new": {"path": "new.pdf"},
                                "expect": {},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            summary = run_manifest(
                manifest_path,
                corpus_root=root,
                require_executed=True,
            )

        self.assertEqual("fail", summary["status"])
        self.assertEqual("<manifest>", summary["cases"][0]["id"])
        failures = "\n".join(summary["cases"][0]["failures"])
        self.assertIn("state or states", failures)
        self.assertIn("independent accuracy gate", failures)

    def test_certification_rejects_pair_with_only_false_positive_caps(self) -> None:
        """Upper bounds cannot prove that a known difference was actually detected."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_multipage_text_pdf(
                root / "old.pdf",
                [["1 Scope", "The receiver limit is 10 UI."]],
            )
            write_multipage_text_pdf(
                root / "new.pdf",
                [["1 Scope", "The receiver limit is 20 UI."]],
            )
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "cases": [
                            {
                                "id": "caps-only-pair",
                                "kind": "pair",
                                "certification_required": True,
                                "old": {"path": "old.pdf"},
                                "new": {"path": "new.pdf"},
                                "expect": {
                                    "state": "degraded",
                                    "max_technical_section_changes": 99,
                                    "max_technical_table_changes": 99,
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            summary = run_manifest(
                manifest_path,
                corpus_root=root,
                require_executed=True,
            )

        self.assertEqual("fail", summary["status"])
        self.assertEqual("<manifest>", summary["cases"][0]["id"])
        self.assertIn(
            "positive must_find oracle",
            "\n".join(summary["cases"][0]["failures"]),
        )

    def test_certification_rejects_self_diff_as_the_only_certification_case(self) -> None:
        """A self-diff oracle cannot replace an old/new accuracy version pair."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_multipage_text_pdf(
                root / "native.pdf",
                [["1 Scope", "The receiver shall preserve the controlled limit."]],
            )
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "cases": [
                            {
                                "id": "self-diff-only",
                                "kind": "self_diff",
                                "certification_required": True,
                                "document": {"path": "native.pdf"},
                                "expect": {
                                    "states": ["degraded", "indeterminate"],
                                    "max_technical_section_changes": 0,
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            summary = run_manifest(
                manifest_path,
                corpus_root=root,
                require_executed=True,
            )

        self.assertEqual("fail", summary["status"])
        self.assertEqual("<manifest>", summary["cases"][0]["id"])
        self.assertIn(
            "at least one certification_required pair",
            "\n".join(summary["cases"][0]["failures"]),
        )

    def test_required_missing_private_pdf_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            summary = self._run(
                Path(temp_dir),
                [
                    {
                        "id": "required-private-pair",
                        "kind": "pair",
                        "required": True,
                        "old": {"path": "old-private.pdf"},
                        "new": {"path": "new-private.pdf"},
                        "expect": {},
                    }
                ],
            )

        self.assertEqual("fail", summary["status"])
        self.assertEqual(1, summary["counts"]["fail"])
        self.assertIn("missing required PDF", summary["cases"][0]["failures"][0])

    def test_manifest_can_allow_scan_quality_states_without_allowing_reliable(self) -> None:
        """Optional OCR availability may change scan state, but never to reliable."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_multipage_text_pdf(
                root / "short.pdf",
                [["1 Scope", "A short document remains below the reliable text gate."]],
            )
            summary = self._run(
                root,
                [
                    {
                        "id": "environment-dependent-scan-state",
                        "kind": "self_diff",
                        "required": True,
                        "document": {"path": "short.pdf"},
                        "expect": {
                            "states": ["degraded", "indeterminate"],
                            "max_technical_section_changes": 0,
                        },
                    }
                ],
            )

        self.assertEqual("pass", summary["status"], summary)
        self.assertNotEqual("reliable", summary["cases"][0]["metrics"]["state"])

    def test_manifest_rejects_invalid_or_conflicting_quality_state_sets(self) -> None:
        """State alternatives must remain explicit, unique, and mutually exclusive."""

        with tempfile.TemporaryDirectory() as temp_dir:
            summary = self._run(
                Path(temp_dir),
                [
                    {
                        "id": "invalid-state-set",
                        "kind": "self_diff",
                        "document": {"path": "scan.pdf"},
                        "expect": {
                            "state": "degraded",
                            "states": ["degraded", "degraded"],
                        },
                    }
                ],
            )

        self.assertEqual("fail", summary["status"])
        failures = "\n".join(summary["cases"][0]["failures"])
        self.assertIn("unique quality states", failures)
        self.assertIn("cannot define both state and states", failures)

    def test_manifest_rejects_invalid_source_and_location_anchor_types(self) -> None:
        """Every source/location accuracy anchor must be a non-empty string array."""

        with tempfile.TemporaryDirectory() as temp_dir:  # 只验证 schema，PDF 不需要真实存在。
            root = Path(temp_dir)  # 临时 manifest 避免污染私有 corpus。
            manifest_path = root / "manifest.json"  # schema runner 读取与 CLI 相同的 JSON 路径。
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "cases": [
                            {
                                "id": "bad-accuracy-anchors",
                                "kind": "pair",
                                "old": {"path": "old.pdf"},
                                "new": {"path": "new.pdf"},
                                "expect": {
                                    "must_extract_old": "not-an-array",
                                    "must_extract_new": [""],
                                    "must_ignore_locations": [1],
                                    "must_review": [""],
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            summary = run_manifest(manifest_path, corpus_root=root)  # schema 失败应早于缺失 PDF 的 skip。

        failures = "\n".join(summary["cases"][0]["failures"])  # 汇总三项独立类型错误。
        self.assertEqual("fail", summary["status"])  # 任一无效锚点都阻止 manifest 执行。
        self.assertIn("must_extract_old", failures)
        self.assertIn("must_extract_new", failures)
        self.assertIn("must_ignore_locations", failures)
        self.assertIn("must_review", failures)

    def test_cli_exits_one_when_a_required_pdf_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "cases": [
                            {
                                "id": "required-private-self-diff",
                                "kind": "self_diff",
                                "required": True,
                                "document": {"path": "private.pdf"},
                                "expect": {},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "tools" / "validate_corpus.py"),
                    str(manifest_path),
                    "--corpus-root",
                    str(root),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(1, completed.returncode, completed.stderr)
        self.assertEqual("fail", json.loads(completed.stdout)["status"])

    def test_unreadable_pdf_is_a_machine_readable_case_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "broken.pdf").write_bytes(b"not a PDF")
            summary = self._run(
                root,
                [
                    {
                        "id": "broken-input",
                        "kind": "self_diff",
                        "required": True,
                        "document": {"path": "broken.pdf"},
                        "expect": {},
                    }
                ],
            )

        self.assertEqual("fail", summary["status"])
        self.assertEqual("fail", summary["cases"][0]["status"])
        self.assertIn("case execution error", summary["cases"][0]["failures"][0])
        self.assertNotIn(str(root), json.dumps(summary))

    def test_pair_checks_must_find_and_must_ignore_across_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_multipage_text_pdf(
                root / "formula-old.pdf",
                [["1 Formula", "The transfer coefficient alpha equals 1.00."]],
            )
            write_multipage_text_pdf(
                root / "formula-new.pdf",
                [["1 Formula", "The transfer coefficient alpha equals 1.25."]],
            )
            summary = self._run(
                root,
                [
                    {
                        "id": "controlled-formula-change",
                        "kind": "pair",
                        "required": True,
                        "old": {"path": "formula-old.pdf"},
                        "new": {"path": "formula-new.pdf"},
                        "expect": {
                            "state": "degraded",
                            "max_technical_section_changes": 1,
                            "max_technical_table_changes": 0,
                            "must_find": ["alpha equals 1.25"],
                            "must_ignore": ["decorative page border"],
                        },
                    }
                ],
            )

        self.assertEqual("pass", summary["status"], summary)
        self.assertEqual([], summary["cases"][0]["failures"])
        serialized = json.dumps(summary)
        self.assertNotIn(str(root), serialized)
        self.assertNotIn("sha256", serialized)
        self.assertNotIn("report_dir", serialized)
        self.assertNotIn("protocol_diff_", serialized)

    def test_must_review_checks_only_explicit_review_findings(self) -> None:
        """A stale false-positive oracle must not reject intentional review evidence."""

        review_payload = {
            "provenance": {"build_commit": "Table 31-10. Metadata only"},
            "changes": [
                {
                    "change_type": "modified",
                    "summary": "Table 31-11. Ordinary section change",
                },
                {
                    "change_type": "review",
                    "summary": "Table 31-12. Explicit section review",
                },
            ],
            "table_changes": [
                {
                    "change_type": "review",
                    "old_titles": ["Table 31-8. Coefficient Initial Conditions"],
                    "new_titles": ["Table 31-8. Coefficient Initial Conditions"],
                    "row_changes": [
                        {
                            "item": "表格结构复核",
                            "old_value": "行列边界未验证",
                            "new_value": "行列边界未验证",
                            "change_type": "需人工复核",
                        }
                    ],
                },
                {
                    "change_type": "modified",
                    "old_titles": ["Table 31-9. Material Change"],
                    "new_titles": ["Table 31-9. Material Change"],
                    "row_changes": [
                        {
                            "item": "Voltage",
                            "old_value": "1 V",
                            "new_value": "2 V",
                            "change_type": "实质变化",
                        }
                    ],
                },
                {
                    "change_type": "modified",
                    "old_titles": ["Table 31-13. Mixed review table"],
                    "new_titles": ["Table 31-13. Mixed review table"],
                    "row_changes": [
                        {
                            "item": "Symbol encoding",
                            "old_value": "U+F067",
                            "new_value": "U+03B3",
                            "change_type": "需人工复核",
                        },
                        {
                            "item": "Table 31-14. Material row only",
                            "old_value": "1 V",
                            "new_value": "2 V",
                            "change_type": "实质变化",
                        },
                    ],
                },
            ],
        }
        reviewable = _review_report_text(review_payload)
        common = {
            "metrics": {
                "state": "degraded",
                "technical_section_changes": 0,
                "technical_table_changes": 2,
            },
            "searchable": json.dumps(review_payload, ensure_ascii=False),
            "reviewable": reviewable,
            "source_texts": {"old": "", "new": ""},
            "location_text": "",
        }

        self.assertEqual(
            [],
            _expectation_failures(
                {"must_review": ["Table 31-8. Coefficient Initial Conditions"]},
                **common,
            ),
        )
        failures = _expectation_failures(
            {"must_review": ["Table 31-9. Material Change"]},
            **common,
        )
        self.assertEqual(
            ["must_review anchor absent: 'Table 31-9. Material Change'"],
            failures,
        )
        for anchor in (
            "Table 31-12. Explicit section review",
            "Table 31-13. Mixed review table",
            "Symbol encoding",
        ):
            self.assertEqual(
                [],
                _expectation_failures(
                    {"must_review": [anchor]},
                    **common,
                ),
            )

    def test_must_find_cannot_be_satisfied_by_review_only_findings(self) -> None:
        """Known material deltas must not certify after degrading to review."""

        anchor = "KNOWN-MATERIAL-DELTA"
        review_only_payload = {
            "changes": [
                {
                    "role": "technical",
                    "change_type": "review",
                    "summary": anchor,
                }
            ],
            "table_changes": [
                {
                    "role": "technical",
                    "change_type": "review",
                    "caption_changed": True,
                    "old_titles": [anchor],
                    "new_titles": [anchor],
                    "row_changes": [
                        {
                            "item": anchor,
                            "old_value": "1 V",
                            "new_value": "2 V",
                            "change_type": "需人工复核",
                        }
                    ],
                }
            ],
        }
        common = {
            "metrics": {
                "state": "degraded",
                "technical_section_changes": 1,
                "technical_table_changes": 1,
            },
            "searchable": json.dumps(review_only_payload, ensure_ascii=False),
            "material_searchable": _material_report_text(review_only_payload),
            "reviewable": _review_report_text(review_only_payload),
            "source_texts": {"old": "", "new": ""},
            "location_text": "",
        }

        self.assertEqual(
            [f"must_find anchor absent: {anchor!r}"],
            _expectation_failures({"must_find": [anchor]}, **common),
        )
        self.assertEqual(
            [],
            _expectation_failures({"must_review": [anchor]}, **common),
        )
        for anchor in (
            "Table 31-10. Metadata only",
            "Table 31-11. Ordinary section change",
            "Table 31-14. Material row only",
        ):
            self.assertEqual(
                [f"must_review anchor absent: {anchor!r}"],
                _expectation_failures(
                    {"must_review": [anchor]},
                    **common,
                ),
            )

    def test_must_find_cannot_be_satisfied_by_unchanged_locations_or_titles(self) -> None:
        """A positive oracle must occur in the changed fact, not its container metadata."""

        section_anchor = "KNOWN-DELTA-LOST"
        table_anchor = "TABLE-TITLE-UNCHANGED"
        payload = {
            "changes": [
                {
                    "role": "technical",
                    "change_type": "modified",
                    "report_location": section_anchor,
                    "old_location": section_anchor,
                    "new_location": section_anchor,
                    "added_snippets": ["Voltage is 2 V"],
                    "removed_snippets": ["Voltage is 1 V"],
                    "replaced_snippets": [
                        {"old": "Voltage is 1 V", "new": "Voltage is 2 V"}
                    ],
                }
            ],
            "table_changes": [
                {
                    "role": "technical",
                    "change_type": "modified",
                    "caption_changed": False,
                    "old_titles": [table_anchor],
                    "new_titles": [table_anchor],
                    "row_changes": [
                        {
                            "item": "Voltage",
                            "old_value": "1 V",
                            "new_value": "2 V",
                            "change_type": "实质变化",
                        }
                    ],
                }
            ],
        }
        material_searchable = _material_report_text(payload)
        common = {
            "metrics": {
                "state": "degraded",
                "technical_section_changes": 1,
                "technical_table_changes": 1,
            },
            "searchable": json.dumps(payload, ensure_ascii=False),
            "material_searchable": material_searchable,
            "reviewable": _review_report_text(payload),
            "source_texts": {"old": "", "new": ""},
            "location_text": "",
        }

        for anchor in (section_anchor, table_anchor):
            self.assertEqual(
                [f"must_find anchor absent: {anchor!r}"],
                _expectation_failures({"must_find": [anchor]}, **common),
            )
        self.assertIn("Voltage is 2 V".casefold(), material_searchable)

    def test_source_and_location_anchors_reject_missing_or_fake_structure(self) -> None:
        """认证清单应同时发现抽取漏词和伪章节位置。"""

        with tempfile.TemporaryDirectory() as temp_dir:  # 用受控 PDF 建立不依赖生产期望逻辑的可复现 oracle。
            root = Path(temp_dir)  # 临时根目录承载 old/new PDF 与 manifest。
            write_multipage_text_pdf(  # 旧版故意缺少真实的前导 100，模拟 gutter 误删后的事实面。
                root / "channel-old.pdf",
                [["1 Scope", "The impedance is +/- 10 ohm."]],
            )
            write_multipage_text_pdf(  # 新版保留完整数值并增加一个应被 location 门禁捕获的伪章节。
                root / "channel-new.pdf",
                [
                    [
                        "1 Scope",
                        "The impedance is 100 +/- 10 ohm.",
                        "1.1 Recommended Channel",
                        "The host uses a controlled trace.",
                    ]
                ],
            )
            summary = self._run(  # 通过公开 manifest runner 验证源事实与报告位置采用不同搜索面。
                root,
                [
                    {
                        "id": "source-and-location-accuracy-gate",
                        "kind": "pair",
                        "required": True,
                        "old": {"path": "channel-old.pdf"},
                        "new": {"path": "channel-new.pdf"},
                        "expect": {
                            "must_extract_old": ["100 +/- 10 ohm"],
                            "must_extract_new": ["100 +/- 10 ohm"],
                            "must_ignore_locations": ["1.1 Recommended Channel"],
                        },
                    }
                ],
            )

        self.assertEqual("fail", summary["status"])  # 两类已知准确度缺陷任一存在都必须阻断认证。
        failures = "\n".join(summary["cases"][0]["failures"])  # 合并失败项便于验证独立错误均被报告。
        self.assertIn("must_extract_old anchor absent", failures)  # 旧侧漏掉 100 必须由源抽取门禁发现。
        self.assertNotIn("must_extract_new anchor absent", failures)  # 新侧完整数值不应产生假失败。
        self.assertIn("must_ignore_locations anchor present", failures)  # 伪章节必须由结构位置门禁发现。

    def test_source_anchor_matching_normalizes_pdf_line_break_whitespace(self) -> None:
        """A visual line wrap must not make an otherwise literal source anchor fail."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_multipage_text_pdf(
                root / "wrapped-notes.pdf",
                [["1 Scope", "See Notes 1,", "2 for the applicable receiver limits."]],
            )
            summary = self._run(
                root,
                [
                    {
                        "id": "wrapped-source-anchor",
                        "kind": "pair",
                        "required": True,
                        "old": {"path": "wrapped-notes.pdf"},
                        "new": {"path": "wrapped-notes.pdf"},
                        "expect": {
                            "must_extract_old": ["See Notes 1, 2"],
                            "must_extract_new": ["See Notes 1, 2"],
                        },
                    }
                ],
            )

        self.assertEqual("pass", summary["status"], summary)

    def test_must_find_does_not_pass_from_unchanged_json_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_multipage_text_pdf(
                root / "unchanged.pdf",
                [["1 Scope", "Static source-only anchor STATIC-ANCHOR-731 remains unchanged."]],
            )
            summary = self._run(
                root,
                [
                    {
                        "id": "unchanged-anchor-is-not-a-finding",
                        "kind": "self_diff",
                        "required": True,
                        "document": {"path": "unchanged.pdf"},
                        "expect": {"must_find": ["STATIC-ANCHOR-731"]},
                    }
                ],
            )

        self.assertEqual("fail", summary["status"])
        self.assertIn("must_find anchor absent", summary["cases"][0]["failures"][0])

    def test_must_find_does_not_pass_from_report_filename_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_multipage_text_pdf(
                root / "FILENAME-ANCHOR-928.pdf",
                [["1 Scope", "The unchanged requirement has no filename token in its body."]],
            )
            summary = self._run(
                root,
                [
                    {
                        "id": "filename-is-not-a-finding",
                        "kind": "self_diff",
                        "required": True,
                        "document": {"path": "FILENAME-ANCHOR-928.pdf"},
                        "expect": {"must_find": ["FILENAME-ANCHOR-928"]},
                    }
                ],
            )

        self.assertEqual("fail", summary["status"])
        self.assertIn("must_find anchor absent", summary["cases"][0]["failures"][0])

    def test_state_change_limits_and_anchor_failures_are_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_multipage_text_pdf(
                root / "formula-old.pdf",
                [["1 Formula", "The transfer coefficient alpha equals 1.00."]],
            )
            write_multipage_text_pdf(
                root / "formula-new.pdf",
                [["1 Formula", "The transfer coefficient alpha equals 1.25."]],
            )
            summary = self._run(
                root,
                [
                    {
                        "id": "failing-gates",
                        "kind": "pair",
                        "required": True,
                        "old": {"path": "formula-old.pdf"},
                        "new": {"path": "formula-new.pdf"},
                        "expect": {
                            "state": "reliable",
                            "max_technical_section_changes": 0,
                            "must_find": ["beta equals 9.99"],
                            "must_ignore": ["alpha equals 1.25"],
                        },
                    }
                ],
            )

        self.assertEqual("fail", summary["status"])
        failures = "\n".join(summary["cases"][0]["failures"])
        self.assertIn("state expected 'reliable', got 'degraded'", failures)
        self.assertIn("technical_section_changes expected <= 0, got 1", failures)
        self.assertIn("must_find anchor absent", failures)
        self.assertIn("must_ignore anchor present", failures)

    def test_pair_page_windows_exclude_out_of_scope_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_multipage_text_pdf(
                root / "window-old.pdf",
                [
                    ["1 Outside", "Outside-window voltage is 3.3 V."],
                    ["2 Selected", "Selected-window limit is 12.0 dB."],
                ],
            )
            write_multipage_text_pdf(
                root / "window-new.pdf",
                [
                    ["1 Outside", "Outside-window voltage is 2.5 V."],
                    ["1.1 Inserted", "A new preface shifts the selected clause."],
                    ["2 Selected", "Selected-window limit is 12.0 dB."],
                ],
            )
            summary = self._run(
                root,
                [
                    {
                        "id": "independent-page-windows",
                        "kind": "pair",
                        "required": True,
                        "old": {"path": "window-old.pdf", "start_page": 2, "end_page": 2},
                        "new": {"path": "window-new.pdf", "start_page": 3, "end_page": 3},
                        "expect": {
                            "max_technical_section_changes": 0,
                            "max_technical_table_changes": 0,
                            "must_ignore": ["Outside-window voltage"],
                        },
                    }
                ],
            )

        self.assertEqual("pass", summary["status"], summary)
        self.assertEqual(0, summary["cases"][0]["metrics"]["technical_section_changes"])

    def test_cross_page_table_style_change_runs_through_reports(self) -> None:
        common_first_page = [
            "7 Electrical Limits",
            "Table 7-1 Receiver Limits",
            "Parameter Symbol Minimum Maximum Unit",
            "Termination resistance RTERM 45 55 ohm",
            "Table 7-1 continues on the next page.",
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_multipage_text_pdf(
                root / "table-old.pdf",
                [
                    common_first_page,
                    [
                        "7 Electrical Limits",
                        "Table 7-1 Receiver Limits continued",
                        "Parameter Symbol Minimum Maximum Unit",
                        "Receiver bandwidth fRX 20 40 GHz",
                    ],
                ],
            )
            write_multipage_text_pdf(
                root / "table-new.pdf",
                [
                    common_first_page,
                    [
                        "7 Electrical Limits",
                        "Table 7-1 Receiver Limits continued",
                        "Parameter Symbol Minimum Maximum Unit",
                        "Receiver bandwidth fRX 22 40 GHz",
                    ],
                ],
            )
            summary = self._run(
                root,
                [
                    {
                        "id": "controlled-cross-page-table-text",
                        "kind": "pair",
                        "required": True,
                        "old": {"path": "table-old.pdf", "start_page": 1, "end_page": 2},
                        "new": {"path": "table-new.pdf", "start_page": 1, "end_page": 2},
                        "expect": {
                            "max_technical_section_changes": 1,
                            "max_technical_table_changes": 0,
                            "must_find": ["Receiver bandwidth fRX 22 40 GHz"],
                            "must_ignore": ["Receiver bandwidth fRX 21 40 GHz"],
                        },
                    }
                ],
            )

        self.assertEqual("pass", summary["status"], summary)

    def test_self_diff_has_no_substantive_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_multipage_text_pdf(
                root / "native.pdf",
                [["1 Scope", "The receiver supports 32.0 GT/s."], ["2 Limits", "UI <= 31.25 ps."]],
            )
            summary = self._run(
                root,
                [
                    {
                        "id": "native-self-diff",
                        "kind": "self_diff",
                        "required": True,
                        "document": {"path": "native.pdf", "start_page": 1, "end_page": 2},
                        "expect": {
                            "max_technical_section_changes": 0,
                            "max_technical_table_changes": 0,
                        },
                    }
                ],
            )

        self.assertEqual("pass", summary["status"], summary)
        self.assertEqual(0, summary["cases"][0]["metrics"]["technical_section_changes"])
        self.assertEqual(0, summary["cases"][0]["metrics"]["technical_table_changes"])


if __name__ == "__main__":
    unittest.main()
