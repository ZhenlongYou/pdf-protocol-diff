"""Regression tests for the optional heavy layout-backend routing.

The default extractor must remain the fast pdfplumber path.  These tests keep
the routing and acceptance gate independent from an installed Docling runtime,
so they are stable on ordinary developer and CI machines.
"""

from __future__ import annotations

import sys  # 注入可控的 Docling 公共模块，避免测试依赖本机可选安装。
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace  # 构造只含公开字段的 Docling 测试对象。
from unittest import mock

from protocol_pdf_diff import layout_backend
from protocol_pdf_diff.layout_backend import (
    LayoutBackendMode,
    LayoutBackendUnavailableError,
    choose_docling_page_text,
    enrich_with_optional_layout_backend,
    normalize_layout_backend,
    should_attempt_docling,
)
from protocol_pdf_diff.models import ExtractionResult, PageText


class LayoutBackendRoutingTests(unittest.TestCase):
    """Verify that the optional parser cannot slow down ordinary PDFs."""

    def test_native_mode_never_attempts_docling(self) -> None:
        """Default native mode performs no optional-backend work at all."""

        pages = [PageText(page_number=1, text="1 Scope\nLinear native text.")]

        self.assertFalse(should_attempt_docling(LayoutBackendMode.NATIVE, pages))
        self.assertIs(
            normalize_layout_backend(LayoutBackendMode.NATIVE),
            LayoutBackendMode.NATIVE,
        )
        extraction = ExtractionResult(pdf_path=Path("ordinary.pdf"), pages=pages)
        with mock.patch.object(
            layout_backend,
            "_docling_is_available",
            side_effect=AssertionError("native mode must not inspect Docling"),
        ):
            self.assertIs(
                enrich_with_optional_layout_backend(extraction, "native"),
                extraction,
            )

    def test_auto_mode_attempts_only_when_native_layout_is_risky(self) -> None:
        """Auto mode avoids imports/conversion unless native evidence is weak."""

        ordinary = [PageText(page_number=1, text="1 Scope\nLinear native text.")]
        risky = [
            PageText(
                page_number=1,
                text="1 Scope\nLeft column text followed by right column text.",
                layout_risk=True,
            )
        ]

        self.assertFalse(should_attempt_docling(LayoutBackendMode.AUTO, ordinary))
        self.assertTrue(should_attempt_docling(LayoutBackendMode.AUTO, risky))

    def test_explicit_docling_checks_installation_even_without_a_risky_page(self) -> None:
        """An explicit mode must never silently pretend the optional parser ran."""

        extraction = ExtractionResult(
            pdf_path=Path("ordinary.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\nLinear native text.")],
        )
        with mock.patch.object(layout_backend, "_docling_is_available", return_value=False):
            with self.assertRaises(LayoutBackendUnavailableError):
                enrich_with_optional_layout_backend(extraction, "docling")

    def test_docling_text_requires_native_agreement_before_replacing_page(self) -> None:
        """A divergent optional result remains evidence, not an unsafe rewrite."""

        native = "1 Scope\nThe receiver shall support 53.125 GBd operation."
        accepted = "1 Scope\nThe receiver shall support 53.125 GBd operation."
        divergent = "Unrelated marketing page with different words entirely."
        incomplete = "The receiver shall support 53.125 GBd operation."

        self.assertEqual(
            choose_docling_page_text(native, accepted, layout_risk=True),
            accepted,
        )
        self.assertIsNone(
            choose_docling_page_text(native, divergent, layout_risk=True)
        )
        self.assertIsNone(
            choose_docling_page_text(native, incomplete, layout_risk=True)
        )
        self.assertIsNone(
            choose_docling_page_text(native, accepted, layout_risk=False)
        )

    def test_docling_text_cannot_drop_a_negation_or_requirement_token(self) -> None:
        """Small but semantic omissions must not pass a broad word-overlap gate."""

        native = (
            "The receiver shall not support 53.125 GBd operation when error "
            "counters are disabled during validation."
        )
        missing_negation = (
            "The receiver shall support 53.125 GBd operation when error "
            "counters are disabled during validation."
        )

        self.assertIsNone(
            choose_docling_page_text(native, missing_negation, layout_risk=True)
        )

    def test_docling_text_cannot_rebind_a_negation_or_drop_numeric_sign(self) -> None:
        """Critical tokens must stay with their local requirement, including sign."""

        native = (
            "The receiver shall not support ModeA but shall support ModeB. "
            "The limit is -3.0 mV."
        )
        rebound_negation = (
            "The receiver shall support ModeA but shall not support ModeB. "
            "The limit is -3.0 mV."
        )
        removed_sign = (
            "The receiver shall not support ModeA but shall support ModeB. "
            "The limit is 3.0 mV."
        )
        unicode_minus = (
            "The receiver shall not support ModeA but shall support ModeB. "
            "The limit is −3.0 mV."
        )

        self.assertIsNone(
            choose_docling_page_text(native, rebound_negation, layout_risk=True)
        )
        self.assertIsNone(
            choose_docling_page_text(native, removed_sign, layout_risk=True)
        )
        self.assertIsNone(
            choose_docling_page_text(unicode_minus, removed_sign, layout_risk=True)
        )

    def test_docling_text_cannot_drop_a_unit_wrapped_after_its_number(self) -> None:
        """PDF 换行不能让下一视觉行的小写工程单位脱离关键事实门。"""

        shared_prose = " ".join(["context"] * 200)
        for unit in ("ps", "ns", "ppm", "ui", "mv"):
            with self.subTest(unit=unit):
                native = f"the calibrated limit remains 10\n{unit} {shared_prose}"
                candidate_without_unit = (
                    f"the calibrated limit remains 10\n{shared_prose}"
                )

                self.assertIsNone(
                    choose_docling_page_text(
                        native,
                        candidate_without_unit,
                        layout_risk=True,
                    )
                )

    def test_docling_text_cannot_weaken_a_normative_requirement_in_long_text(self) -> None:
        """The small general-overlap allowance must not hide shall-to-should edits."""

        native = " ".join(["context"] * 160 + ["shall", "apply"])
        weakened = " ".join(["context"] * 160 + ["should", "apply"])

        self.assertIsNone(
            choose_docling_page_text(native, weakened, layout_risk=True)
        )

    def test_docling_text_cannot_weaken_cannot_or_chinese_prohibition(self) -> None:
        """Prohibitions must remain protected even in long otherwise-matching pages."""

        prefix = " ".join(["context"] * 160)
        self.assertIsNone(
            choose_docling_page_text(
                f"{prefix} cannot apply",
                f"{prefix} can apply",
                layout_risk=True,
            )
        )
        self.assertIsNone(
            choose_docling_page_text(
                "本设备不得连接外部电源。" * 20,
                "本设备可连接外部电源。" * 20,
                layout_risk=True,
            )
        )

    def test_docling_text_cannot_drop_a_short_equation_from_long_prose(self) -> None:
        """长段落的高重合率不能掩盖独立公式 ``x = y`` 被删除。"""

        # 大量相同正文专门复现旧版 98% 覆盖门会放过短公式的缺陷。
        shared_prose = " ".join(["context"] * 200)
        # 原生文字保留独立公式，Docling 候选模拟漏掉 formula item 的结果。
        native = f"{shared_prose}\nx = y"
        candidate_without_equation = shared_prose

        # 公式事实缺失时必须回退原生文字，不能采用语义变弱的候选。
        self.assertIsNone(
            choose_docling_page_text(
                native,
                candidate_without_equation,
                layout_risk=True,
            )
        )

    def test_docling_text_cannot_drop_a_greek_variable_from_formula(self) -> None:
        """即使等号仍在，希腊变量缺失也必须否决 Docling 候选。"""

        # 保留相同长正文和等号，确保断言只针对希腊变量而非一般覆盖率。
        shared_prose = " ".join(["context"] * 200)
        # 候选故意漏掉 alpha，但保留等号和右侧变量以模拟局部公式抽取缺陷。
        native = f"{shared_prose}\nα = voltage"
        candidate_without_alpha = f"{shared_prose}\n= voltage"

        # 希腊变量属于工程公式身份，不能按普通未识别字形忽略。
        self.assertIsNone(
            choose_docling_page_text(
                native,
                candidate_without_alpha,
                layout_risk=True,
            )
        )

    def test_docling_text_cannot_drop_formula_operators(self) -> None:
        """加、乘、正负、除和约等运算符都是不可被覆盖率稀释的公式事实。"""

        shared_prose = " ".join(["context"] * 200)  # 让单个运算符缺失仍高于一般 98% 文字重合门槛。
        for operator in ("+", "×", "±", "÷", "≈"):
            with self.subTest(operator=operator):  # 分别锁定每个协议公式常用 Unicode 运算符。
                native = f"x {operator} y {shared_prose}"  # 原生抽取含完整的三 token 公式事实。
                candidate_without_operator = f"x y {shared_prose}"  # 候选只漏一个运算符，其他内容完全相同。

                self.assertIsNone(
                    choose_docling_page_text(
                        native,
                        candidate_without_operator,
                        layout_risk=True,
                    )
                )  # 运算符丢失必须回退原生文本，不能采用语义已变化的候选。

    def test_docling_text_cannot_drop_compact_ascii_minus_operator(self) -> None:
        """紧凑公式 ``x-y`` 中的减号不能被普通标识符 token 吞掉。"""

        shared_prose = " ".join(["context"] * 200)

        self.assertIsNone(
            choose_docling_page_text(
                f"x-y {shared_prose}",
                f"xy {shared_prose}",
                layout_risk=True,
            )
        )

    def test_docling_text_cannot_drop_other_compact_formula_operators(self) -> None:
        """紧凑公式中的 Unicode 减号、乘除、幂和百分号均须保真。"""

        shared_prose = " ".join(["context"] * 200)
        for native_formula, weakened_formula in (
            ("x−y", "xy"),
            ("x*y", "xy"),
            ("x/y", "xy"),
            ("x^y", "xy"),
            ("10%", "10"),
        ):
            with self.subTest(native_formula=native_formula):
                self.assertIsNone(
                    choose_docling_page_text(
                        f"{native_formula} {shared_prose}",
                        f"{weakened_formula} {shared_prose}",
                        layout_risk=True,
                    )
                )

    def test_docling_text_cannot_drop_unary_or_multi_letter_formula_operators(self) -> None:
        """一元符号和多字母变量不能落到单字母公式启发式之外。"""

        shared_prose = " ".join(["context"] * 200)
        for native_formula, weakened_formula in (
            ("-x", "x"),
            ("−x", "x"),
            ("Vout - Vin", "Vout Vin"),
            ("alpha/beta", "alphabeta"),
        ):
            with self.subTest(native_formula=native_formula):
                self.assertIsNone(
                    choose_docling_page_text(
                        f"{native_formula} {shared_prose}",
                        f"{weakened_formula} {shared_prose}",
                        layout_risk=True,
                    )
                )

    def test_compact_minus_or_slash_cannot_move_to_different_operands(self) -> None:
        """运算符总数相同也不能把减法或除法绑定到另一组变量。"""

        shared_prose = " ".join(["context"] * 200)
        for native_formula, rebound_formula in (
            ("x-y a b", "x y a-b"),
            ("x/y a b", "x y a/b"),
        ):
            with self.subTest(native_formula=native_formula):
                self.assertIsNone(
                    choose_docling_page_text(
                        f"{native_formula} {shared_prose}",
                        f"{rebound_formula} {shared_prose}",
                        layout_risk=True,
                    )
                )

    def test_formula_operator_cannot_move_to_a_different_requirement_row(self) -> None:
        """相同局部公式窗口不能掩盖运算符被搬到另一条要求。"""

        shared_prose = " ".join(["context"] * 200)
        for operator in ("-", "−", "*", "/", "%", "^", "+", "×", "±", "÷", "≈"):
            with self.subTest(operator=operator):
                native = (
                    f"channel one has stable filler a b x{operator}y c d\n"
                    f"channel two has stable filler a b x y c d\n{shared_prose}"
                )
                rebound = (
                    "channel one has stable filler a b x y c d\n"
                    f"channel two has stable filler a b x{operator}y c d\n{shared_prose}"
                )

                self.assertIsNone(
                    choose_docling_page_text(native, rebound, layout_risk=True)
                )

    def test_numeric_and_formula_facts_cannot_swap_requirement_rows(self) -> None:
        """关键事实集合相同时，完整要求主体仍必须保持原绑定。"""

        shared_prose = " ".join(["context"] * 200)
        for native_rows, rebound_rows in (
            (
                (
                    "ModeA operating channel uses the declared calibrated limit 10 mV.",
                    "ModeB operating channel uses the declared calibrated limit 20 mV.",
                ),
                (
                    "ModeA operating channel uses the declared calibrated limit 20 mV.",
                    "ModeB operating channel uses the declared calibrated limit 10 mV.",
                ),
            ),
            (
                (
                    "ModeA operating channel uses the declared relation x - y.",
                    "ModeB operating channel uses the declared relation a + b.",
                ),
                (
                    "ModeA operating channel uses the declared relation a + b.",
                    "ModeB operating channel uses the declared relation x - y.",
                ),
            ),
        ):
            with self.subTest(native_rows=native_rows):
                native = "\n".join((*native_rows, shared_prose))
                rebound = "\n".join((*rebound_rows, shared_prose))

                self.assertIsNone(
                    choose_docling_page_text(native, rebound, layout_risk=True)
                )

    def test_formula_grouping_and_interval_delimiters_cannot_change(self) -> None:
        """token 内容相同也不能改变公式结合顺序、区间端点或索引语义。"""

        shared_prose = " ".join(["context"] * 220)
        for native_formula, regrouped_formula in (
            ("x * (y + z)", "(x * y) + z"),
            ("-(x + y)", "(-x) + y"),
            ("[1, 2)", "(1, 2]"),
            ("A[1]", "A(1)"),
        ):
            with self.subTest(native_formula=native_formula):
                self.assertIsNone(
                    choose_docling_page_text(
                        f"{native_formula} {shared_prose}",
                        f"{regrouped_formula} {shared_prose}",
                        layout_risk=True,
                    )
                )

    def test_ordinary_punctuation_cannot_move_between_words(self) -> None:
        """即使词和标点总数相同，普通文本中的标点搬移也必须回退。"""

        shared_prose = " ".join(["context"] * 220)
        native = f"alpha, beta gamma delta {shared_prose}"
        punctuation_moved = f"alpha beta, gamma delta {shared_prose}"

        self.assertIsNone(
            choose_docling_page_text(native, punctuation_moved, layout_risk=True)
        )

    def test_wrapped_facts_cannot_swap_between_requirement_labels(self) -> None:
        """标签与事实分行时，大小写标签都不能靠行 Counter 丢失绑定。"""

        shared_prose = " ".join(["context"] * 220)
        for first_label, second_label in (
            ("ModeA", "ModeB"),
            ("primary", "secondary"),
        ):
            for first_fact, second_fact in (
                ("10 mV", "20 mV"),
                ("x - y", "a + b"),
            ):
                with self.subTest(
                    first_label=first_label,
                    first_fact=first_fact,
                ):
                    native = "\n".join(
                        (
                            f"{first_label} operating channel uses the declared fact",
                            first_fact,
                            f"{second_label} operating channel uses the declared fact",
                            second_fact,
                            shared_prose,
                        )
                    )
                    rebound = "\n".join(
                        (
                            f"{first_label} operating channel uses the declared fact",
                            second_fact,
                            f"{second_label} operating channel uses the declared fact",
                            first_fact,
                            shared_prose,
                        )
                    )

                    self.assertIsNone(
                        choose_docling_page_text(native, rebound, layout_risk=True)
                    )

    def test_docling_text_cannot_add_critical_formula_facts(self) -> None:
        """候选额外生成希腊变量或运算符时，也不能冒充高保真重排。"""

        shared_prose = " ".join(["context"] * 200)  # 候选只增加极少 token，专门越过旧单向覆盖检查。
        native = f"x = y {shared_prose}"  # 基准公式保持不变，新增事实均来自候选尾部。
        for added_fact in ("α", "+"):
            with self.subTest(added_fact=added_fact):  # 同时覆盖额外希腊变量和额外关键运算符。
                candidate_with_extra_fact = f"{native} context context context {added_fact}"

                self.assertIsNone(
                    choose_docling_page_text(
                        native,
                        candidate_with_extra_fact,
                        layout_risk=True,
                    )
                )  # 关键事实集合必须双向一致，不能只检查原文是否被候选覆盖。

    def test_docling_text_cannot_add_compact_formula_operators(self) -> None:
        """Docling 候选不能在紧凑公式中幻觉出任何受保护运算符。"""

        shared_prose = " ".join(["context"] * 200)
        for candidate_formula in (
            "x-y",
            "x−y",
            "x*y",
            "x/y",
            "x^y",
            "10%",
            "x+y",
            "x×y",
            "x±y",
            "x÷y",
            "x≈y",
        ):
            native_formula = "10" if candidate_formula == "10%" else "xy"
            with self.subTest(candidate_formula=candidate_formula):
                self.assertIsNone(
                    choose_docling_page_text(
                        f"{native_formula} {shared_prose}",
                        f"{candidate_formula} {shared_prose}",
                        layout_risk=True,
                    )
                )

    def test_reflow_with_critical_text_fails_closed(self) -> None:
        """换行可能承载结构，缺少共享坐标时不能自动认定 reflow 安全。"""

        signed_limit = (
            "Before calibration the stable documented path docs/spec-v1/chapter "
            "always keeps the signed limit -3.0 mV safely under all operating modes today."
        )
        identifiers = (
            "During validation the declared identifiers PCIe-6.0 and SERDES/TX "
            "remain exactly unchanged across every supported operating mode today."
        )
        native = f"{signed_limit}\n{identifiers}"
        reflowed = f"{signed_limit} {identifiers}"

        self.assertIsNone(
            choose_docling_page_text(native, reflowed, layout_risk=True)
        )

    def test_reflow_cannot_destroy_section_boundaries_or_literal_spacing(self) -> None:
        """章节换行、字符串空格和缩进都必须逐字符保留。"""

        body = "The receiver shall preserve the documented behavior. " * 8
        native_sections = f"1 Scope\n{body}\n2 Requirements\n{body}"
        flattened_sections = f"1 Scope {body} 2 Requirements {body}"
        self.assertIsNone(
            choose_docling_page_text(
                native_sections,
                flattened_sections,
                layout_risk=True,
            )
        )

        shared_prose = " ".join(["context"] * 220)
        self.assertIsNone(
            choose_docling_page_text(
                f"literal = 'A  B'\n{shared_prose}",
                f"literal = 'A B'\n{shared_prose}",
                layout_risk=True,
            )
        )

    def test_formula_pages_fail_closed_on_row_reordering(self) -> None:
        """含关键公式的页面即使整行换序也保守回退原生文本。"""

        multiply_row = (
            "For lane alpha the calibrated output relation x*y remains valid "
            "during every normal operating cycle today."
        )
        percent_row = (
            "For lane beta the calibrated duty ratio 10% remains valid during "
            "every normal operating cycle today."
        )
        native = f"{multiply_row}\n{percent_row}"
        reordered = f"{percent_row}\n{multiply_row}"

        self.assertIsNone(
            choose_docling_page_text(native, reordered, layout_risk=True)
        )

    def test_short_critical_rows_fail_closed_on_reordering(self) -> None:
        """短公式、标识符或数值行换序也不能绕过关键页面不变式。"""

        shared_prose = " ".join(["context"] * 200)
        for first_row, second_row in (
            ("x*y", "a+b"),
            ("docs/spec-v1/chapter", "PCIe-6.0"),
            (
                "this stable documented row has enough ordinary words and finishes at value 7",
                "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu",
            ),
        ):
            with self.subTest(first_row=first_row):
                native = f"{first_row}\n{second_row}\n{shared_prose}"
                reordered = f"{second_row}\n{first_row}\n{shared_prose}"

                self.assertIsNone(
                    choose_docling_page_text(native, reordered, layout_risk=True)
                )

    def test_ordinary_rows_also_fail_closed_on_reordering(self) -> None:
        """普通文字同样可能因换序改变语义，候选只能调整空白。"""

        shared_prose = " ".join(["context"] * 200)
        first_row = "alpha beta gamma delta epsilon zeta eta theta"
        second_row = "lambda mu nu omicron pi rho sigma tau"
        native = f"{first_row}\n{second_row}\n{shared_prose}"
        reordered = f"{second_row}\n{first_row}\n{shared_prose}"

        self.assertIsNone(
            choose_docling_page_text(native, reordered, layout_risk=True)
        )

    def test_unknown_engineering_glyphs_cannot_change_or_disappear(self) -> None:
        """未知数学符号、前缀与温标也受逐字符不变式保护。"""

        shared_prose = " ".join(["context"] * 220)
        for native_fact, weakened_fact in (
            ("x²", "x³"),
            ("√x", "x"),
            ("x ≠ y", "x y"),
            ("5 µV", "5 V"),
            ("25 °C", "25 C"),
        ):
            with self.subTest(native_fact=native_fact):
                self.assertIsNone(
                    choose_docling_page_text(
                        f"The receiver shall preserve {native_fact}. {shared_prose}",
                        f"The receiver shall preserve {weakened_fact}. {shared_prose}",
                        layout_risk=True,
                    )
                )

    def test_docling_formula_item_is_merged_into_candidate_page_text(self) -> None:
        """Docling 的 formula item 必须并入候选文字，不能在遍历阶段丢弃。"""

        # 原生页提供足量正文和独立公式，页面风险使公开增强入口真正尝试 Docling。
        prose = ("The receiver shall preserve every declared operating mode. " * 8).strip()
        native = f"1 Scope\n{prose}\nx = y"
        extraction = ExtractionResult(
            pdf_path=Path("formula.pdf"),
            pages=[PageText(page_number=1, text=native, layout_risk=True)],
        )
        # 伪文档只实现 Docling 公共遍历字段；正文和公式刻意分成两个 item。
        body_item = SimpleNamespace(
            label=SimpleNamespace(value="text"),
            text=f"1 Scope\n{prose}",
            prov=[SimpleNamespace(page_no=1)],
        )
        formula_item = SimpleNamespace(
            label=SimpleNamespace(value="formula"),
            text="x = y",
            prov=[SimpleNamespace(page_no=1)],
        )
        document = SimpleNamespace(iterate_items=lambda: iter((body_item, formula_item)))
        # 假转换器返回上述公开文档对象，不执行模型下载或文件访问。
        converter_type = type(
            "DocumentConverter",
            (),
            {"convert": lambda self, *_args, **_kwargs: SimpleNamespace(document=document)},
        )
        document_converter = ModuleType("docling.document_converter")
        document_converter.DocumentConverter = converter_type
        docling_package = ModuleType("docling")

        # 通过公开 enrich 入口验证最终采用 Docling，而非直接测试过滤常量。
        with mock.patch.dict(
            sys.modules,
            {
                "docling": docling_package,
                "docling.document_converter": document_converter,
            },
        ):
            with mock.patch.object(layout_backend, "_docling_is_available", return_value=True):
                result = enrich_with_optional_layout_backend(extraction, "auto")

        # 只有公式 item 被合并并通过事实保真门时，来源才会标记为 docling。
        self.assertEqual("docling", result.pages[0].comparison_text_source)
        self.assertIn("x = y", result.pages[0].text)

    def test_auto_mode_applies_only_an_agreeing_candidate_and_keeps_risk(self) -> None:
        """The optional parser repairs text order without upgrading confidence."""

        native = "1 Scope\nThe receiver shall support 53.125 GBd operation."
        extraction = ExtractionResult(
            pdf_path=Path("complex.pdf"),
            pages=[PageText(page_number=1, text=native, layout_risk=True)],
        )
        with mock.patch.object(layout_backend, "_docling_is_available", return_value=True):
            with mock.patch.object(
                layout_backend,
                "_extract_docling_page_texts",
                return_value={1: native},
            ) as extractor:
                result = enrich_with_optional_layout_backend(extraction, "auto")

        self.assertEqual(result.pages[0].text, native)
        self.assertTrue(result.pages[0].layout_risk)
        self.assertEqual(result.pages[0].parser_route.value, "native_layout_risk")
        self.assertEqual(result.pages[0].comparison_text_source, "docling")
        self.assertTrue(any("Docling" in warning for warning in result.warnings))
        extractor.assert_called_once_with(Path("complex.pdf"), page_range=(1, 1))


if __name__ == "__main__":
    unittest.main()
