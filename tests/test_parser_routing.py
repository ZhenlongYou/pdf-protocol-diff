"""Public regressions for per-page parser route evidence."""

from __future__ import annotations

import json  # 读取报告 JSON，验证审计信息不会泄漏页面或块正文。
from pathlib import Path  # 临时 PDF 和手工构造 extraction 需要平台无关路径。
import tempfile  # 每个公开提取场景使用独立、自动清理的输入目录。
import unittest  # 沿用项目的标准库测试框架。
from unittest import mock  # 只替换 PDF/OCR 外部边界，抽取与评估路径仍真实执行。

from PIL import Image  # 伪 pdfplumber 页面返回真实 Pillow 图像供 OCR 分支使用。

from protocol_pdf_diff.compare import compare_extractions  # 通过公开比较入口生成质量与报告数据。
from protocol_pdf_diff.models import DocumentBlock, DocumentBlockKind, DiffOptions, ExtractionResult, PageExtractionAudit, PageParserRoute, PageText  # 路由模型必须保持旧 PageText 构造兼容。
from protocol_pdf_diff.page_ocr import classify_page_parser_route  # 纯分类器必须可脱离文件名和告警单独验证。
from protocol_pdf_diff.pdf_extract import extract_pdf_text  # 公共 PDF 抽取入口负责把事实写回页面。
from protocol_pdf_diff.reporting import write_reports  # JSON 报告是用户可见的审计出口。
from protocol_pdf_diff.sample_data import write_multipage_text_pdf  # 生成真实原生文本 PDF，避免伪造普通页面路径。


class _OnePagePdf:
    """为公开抽取入口提供可关闭的单页 pdfplumber 容器。"""

    def __init__(self, page: object) -> None:
        # 生产提取器读取 pages 列表，因此测试容器保持同样的公开形状。
        self.pages = [page]

    def close(self) -> None:
        # 内存页面没有文件句柄，但生产代码仍会无条件关闭容器。
        return None


class _ImagePage:
    """可配置文字层和整页图像事实的最小 pdfplumber 页面。"""

    width = 600  # 600x800pt 页面可使整页图像恰好超过扫描判定覆盖率。
    height = 800
    bbox = (0.0, 0.0, 600.0, 800.0)
    chars: list[object] = []
    lines: list[object] = []
    rects: list[object] = []
    images = [{"x0": 0.0, "x1": 600.0, "top": 0.0, "bottom": 800.0}]

    def __init__(self, native_text: str = "") -> None:
        # 文本层长度决定是否执行 OCR，空值还覆盖无法识别图像页。
        self._native_text = native_text

    def filter(self, _predicate: object) -> "_ImagePage":
        # 此页面没有水印字符，过滤视图与原页相同。
        return self

    def extract_words(self, **_kwargs: object) -> list[dict[str, object]]:
        # 扫描或图像文字层场景不提供原生坐标词，聚焦路由事实而非块细节。
        return []

    def extract_text(self, **_kwargs: object) -> str:
        # 返回配置的可选文字层，以驱动可搜索图像页与 OCR fallback 的差异。
        return self._native_text

    def to_image(self, **_kwargs: object) -> object:
        # OCR 入口只需要 original 图像，真实 Pillow 对象可覆盖 RGB 转换链路。
        return type("RenderedPage", (), {"original": Image.new("RGB", (1200, 1600), "white")})()


class _TwoColumnNativePage:
    """带强双栏坐标证据的英文正文页，含普通计数但不含工程单位。"""

    width = 600  # 坐标与现有双栏判定测试使用相同的 600pt 页面尺度。
    height = 800
    bbox = (0.0, 0.0, 600.0, 800.0)
    chars: list[object] = []
    lines: list[object] = []
    rects: list[object] = []
    images: list[object] = []

    def filter(self, _predicate: object) -> "_TwoColumnNativePage":
        # 没有水印字符时，过滤器应保留原页面并让坐标证据继续可见。
        return self

    def extract_words(self, **_kwargs: object) -> list[dict[str, object]]:
        # 此受控双栏含普通英文计数；它们不是工程单位，不能阻止纯正文的列优先重排。
        return [
            {"text": "This requirement paragraph explains why 1 item remains in scope.", "x0": 60, "x1": 250, "top": 100, "bottom": 112},
            {"text": "This condition paragraph records why 2 items need review.", "x0": 360, "x1": 550, "top": 100, "bottom": 112},
            {"text": "The left column documents 3 items before evaluation begins.", "x0": 60, "x1": 255, "top": 145, "bottom": 157},
            {"text": "The right paragraph explains 4 items in test context detail.", "x0": 360, "x1": 555, "top": 145, "bottom": 157},
            {"text": "Later left text explains 5 items used during validation.", "x0": 60, "x1": 255, "top": 190, "bottom": 202},
            {"text": "Later right text records 6 items for the review section.", "x0": 360, "x1": 555, "top": 190, "bottom": 202},
            {"text": "Final left paragraph completes evidence for 7 items.", "x0": 60, "x1": 255, "top": 235, "bottom": 247},
            {"text": "Final right paragraph preserves 8 items in narrative order.", "x0": 360, "x1": 555, "top": 235, "bottom": 247},
        ]

    def extract_text(self, **_kwargs: object) -> str:
        # 可选文字层仍正常存在，因此本例只验证原生版面风险而非扫描 OCR。
        return "1 Scope\n" + "\n".join(
            (
                "This requirement paragraph explains why 1 item remains in scope. This condition paragraph records why 2 items need review.",
                "The left column documents 3 items before evaluation begins. The right paragraph explains 4 items in test context detail.",
                "Later left text explains 5 items used during validation. Later right text records 6 items for the review section.",
                "Final left paragraph completes evidence for 7 items. Final right paragraph preserves 8 items in narrative order.",
            )
        )


class _BorderlessTwoColumnTablePage:
    """无规则线的三行参数/值表，视觉上两列但阅读顺序必须逐行左右保留。"""

    width = 600  # 使用与双栏页相同宽度，确保测试只挑战语义判别而非页面尺度。
    height = 800
    bbox = (0.0, 0.0, 600.0, 800.0)
    chars: list[object] = []
    lines: list[object] = []
    rects: list[object] = []
    images: list[object] = []

    def filter(self, _predicate: object) -> "_BorderlessTwoColumnTablePage":
        # 无水印的最小页面保持原样，生产过滤器仍能正常走完。
        return self

    def extract_words(self, **_kwargs: object) -> list[dict[str, object]]:
        # 三行都达到旧列优先阈值，却含明确表头和数值/单位值，覆盖无框表误重排风险。
        return [
            {"text": "Parameter Description", "x0": 60, "x1": 235, "top": 120, "bottom": 132},
            {"text": "Requirement Value", "x0": 360, "x1": 540, "top": 120, "bottom": 132},
            {"text": "Supply voltage requirement", "x0": 60, "x1": 255, "top": 160, "bottom": 172},
            {"text": "3.3 Volts nominal value", "x0": 360, "x1": 550, "top": 160, "bottom": 172},
            {"text": "Timing window requirement", "x0": 60, "x1": 250, "top": 200, "bottom": 212},
            {"text": "25 Picoseconds maximum value", "x0": 360, "x1": 550, "top": 200, "bottom": 212},
        ]

    def extract_text(self, **_kwargs: object) -> str:
        # 模拟 pdfplumber 的 y-first 输出，作为无框参数表最安全的行优先基准。
        return "\n".join(
            (
                "Parameter Description Requirement Value",
                "Supply voltage requirement 3.3 Volts nominal value",
                "Timing window requirement 25 Picoseconds maximum value",
            )
        )


class _EnglishLabelValueTablePage:
    """无表头英文参数/值表，重复数字单位足以使轻量路径保留逐行顺序。"""

    width = 600  # 与双栏正文使用相同坐标，以隔离单元格形态的结构证据。
    height = 800
    bbox = (0.0, 0.0, 600.0, 800.0)
    chars: list[object] = []
    lines: list[object] = []
    rects: list[object] = []
    images: list[object] = []

    def filter(self, _predicate: object) -> "_EnglishLabelValueTablePage":
        # 页面没有水印，公共提取器可直接沿用同一对象。
        return self

    def extract_words(self, **_kwargs: object) -> list[dict[str, object]]:
        # 左侧字段标签可长可短；右侧连续三行数字+工程单位形成轻量路径不重排的值栅格。
        return [
            {"text": "Supply voltage", "x0": 55, "x1": 255, "top": 120, "bottom": 132},
            {"text": "3.3 Volts nominal value", "x0": 350, "x1": 555, "top": 120, "bottom": 132},
            {"text": "Timing window", "x0": 55, "x1": 260, "top": 165, "bottom": 177},
            {"text": "25 Picoseconds maximum value", "x0": 350, "x1": 555, "top": 165, "bottom": 177},
            {"text": "Output current", "x0": 55, "x1": 260, "top": 210, "bottom": 222},
            {"text": "12 Milliamperes typical value", "x0": 350, "x1": 555, "top": 210, "bottom": 222},
        ]

    def extract_text(self, **_kwargs: object) -> str:
        # 参数表的逐行左右对应关系比 column-major 更可复核。
        return "\n".join(
            (
                "Supply voltage 3.3 Volts nominal value",
                "Timing window 25 Picoseconds maximum value",
                "Output current 12 Milliamperes typical value",
            )
        )


class _AmbiguousValueColumnsPage:
    """含测量值的双栏叙述在轻量路径中不可可靠消歧，必须保留 y-first。"""

    width = 600  # 仍满足双栏坐标门，测试重点是证据语义而非几何不足。
    height = 800
    bbox = (0.0, 0.0, 600.0, 800.0)
    chars: list[object] = []
    lines: list[object] = []
    rects: list[object] = []
    images: list[object] = []

    def filter(self, _predicate: object) -> "_AmbiguousValueColumnsPage":
        # 无水印页面直接返回自身，保持公共路径完整。
        return self

    def extract_words(self, **_kwargs: object) -> list[dict[str, object]]:
        # 两行右栏测量值嵌在完整叙述中，仍可能与长标签表不可区分，轻量路径不猜测列优先。
        return [
            {"text": "The left narrative introduces the receiver context.", "x0": 55, "x1": 255, "top": 120, "bottom": 132},
            {"text": "The right narrative introduces measurement context.", "x0": 350, "x1": 555, "top": 120, "bottom": 132},
            {"text": "The left narrative explains the operating assumption.", "x0": 55, "x1": 260, "top": 165, "bottom": 177},
            {"text": "The right narrative cites a typical value of 3.3 Volts.", "x0": 350, "x1": 555, "top": 165, "bottom": 177},
            {"text": "The left narrative records the review method.", "x0": 55, "x1": 260, "top": 210, "bottom": 222},
            {"text": "The right narrative cites a maximum value of 25 Picoseconds.", "x0": 350, "x1": 555, "top": 210, "bottom": 222},
        ]

    def extract_text(self, **_kwargs: object) -> str:
        # 数值双栏不允许轻量规则猜测列顺序，默认文本正是 pdfplumber 的逐行输出。
        return "\n".join(
            (
                "The left narrative introduces the receiver context. The right narrative introduces measurement context.",
                "The left narrative explains the operating assumption. The right narrative cites a typical value of 3.3 Volts.",
                "The left narrative records the review method. The right narrative cites a maximum value of 25 Picoseconds.",
            )
        )


class _ChineseTwoColumnNativePage:
    """四行 Y 重叠中文正文栏，验证表头词出现在普通正文中仍可列优先。"""

    width = 600  # 复用标准页面宽度，确保中英文测试只在文字形态上不同。
    height = 800
    bbox = (0.0, 0.0, 600.0, 800.0)
    chars: list[object] = []
    lines: list[object] = []
    rects: list[object] = []
    images: list[object] = []

    def filter(self, _predicate: object) -> "_ChineseTwoColumnNativePage":
        # 页面没有水印字符，保持原对象以覆盖真实提取器的过滤调用。
        return self

    def extract_words(self, **_kwargs: object) -> list[dict[str, object]]:
        # 中文受控双栏只使用纯正文；数值/单位双栏会保守保留默认 y-first 顺序。
        return [
            {"text": "左栏连续正文第一段提出技术要求以说明系统行为。", "x0": 55, "x1": 250, "top": 120, "bottom": 132},
            {"text": "右栏连续正文第一段描述测试条件与工作方法。", "x0": 350, "x1": 550, "top": 120, "bottom": 132},
            {"text": "左栏连续正文第二段解释输入信号的边界范围。", "x0": 55, "x1": 260, "top": 165, "bottom": 177},
            {"text": "右栏连续正文第二段说明配置背景与测量意义。", "x0": 350, "x1": 555, "top": 165, "bottom": 177},
            {"text": "左栏连续正文第三段讨论接口工作状态。", "x0": 55, "x1": 260, "top": 210, "bottom": 222},
            {"text": "右栏连续正文第三段记录审阅流程和输出说明。", "x0": 350, "x1": 555, "top": 210, "bottom": 222},
            {"text": "左栏连续正文第四段用于验证完整阅读顺序。", "x0": 55, "x1": 260, "top": 255, "bottom": 267},
            {"text": "右栏连续正文第四段给出后续审阅提示。", "x0": 350, "x1": 555, "top": 255, "bottom": 267},
        ]

    def extract_text(self, **_kwargs: object) -> str:
        # 模拟默认 y-first 顺序，修复前右栏第一段会插在左栏第二段之前。
        return "\n".join(
            (
                "左栏连续正文第一段提出技术要求以说明系统行为。 右栏连续正文第一段描述测试条件与工作方法。",
                "左栏连续正文第二段解释输入信号的边界范围。 右栏连续正文第二段说明配置背景与测量意义。",
                "左栏连续正文第三段讨论接口工作状态。 右栏连续正文第三段记录审阅流程和输出说明。",
                "左栏连续正文第四段用于验证完整阅读顺序。 右栏连续正文第四段给出后续审阅提示。",
            )
        )


class _ChineseBorderlessTablePage:
    """中文无框参数/值表，防止 Unicode 正文检测把表格错误改写为列优先。"""

    width = 600  # 保持与中文正文栏一致的几何条件，隔离文本语义防护。
    height = 800
    bbox = (0.0, 0.0, 600.0, 800.0)
    chars: list[object] = []
    lines: list[object] = []
    rects: list[object] = []
    images: list[object] = []

    def filter(self, _predicate: object) -> "_ChineseBorderlessTablePage":
        # 无水印测试页不改变对象，生产过滤步骤仍完整执行。
        return self

    def extract_words(self, **_kwargs: object) -> list[dict[str, object]]:
        # 表头与数值单位行都足够长，确保负例会挑战 Unicode 连续正文门而非强度阈值。
        return [
            {"text": "参数描述字段说明与测试条件", "x0": 55, "x1": 255, "top": 120, "bottom": 132},
            {"text": "要求数值与单位信息说明文本", "x0": 350, "x1": 555, "top": 120, "bottom": 132},
            {"text": "供电电压参数要求说明文本", "x0": 55, "x1": 260, "top": 165, "bottom": 177},
            {"text": "3.3伏特标称数值与限制条件", "x0": 350, "x1": 555, "top": 165, "bottom": 177},
            {"text": "时序窗口参数要求说明文本", "x0": 55, "x1": 260, "top": 210, "bottom": 222},
            {"text": "25皮秒最大数值与限制条件", "x0": 350, "x1": 555, "top": 210, "bottom": 222},
        ]

    def extract_text(self, **_kwargs: object) -> str:
        # 默认 y-first 是参数表可复核的正确行顺序。
        return "\n".join(
            (
                "参数描述字段说明与测试条件 要求数值与单位信息说明文本",
                "供电电压参数要求说明文本 3.3伏特标称数值与限制条件",
                "时序窗口参数要求说明文本 25皮秒最大数值与限制条件",
            )
        )


class _ChineseLongLabelValueTablePage:
    """中文无框长标签表，数值藏在值句中时仍不得按正文双栏重排。"""

    width = 600  # 复用真双栏几何条件，确保负例仅依赖跨行值结构而非规则线。
    height = 800
    bbox = (0.0, 0.0, 600.0, 800.0)
    chars: list[object] = []
    lines: list[object] = []
    rects: list[object] = []
    images: list[object] = []

    def filter(self, _predicate: object) -> "_ChineseLongLabelValueTablePage":
        # 页面没有水印，保留同一个对象使公共提取路径完成全部过滤调用。
        return self

    def extract_words(self, **_kwargs: object) -> list[dict[str, object]]:
        # 首行不是短表头；右栏后两行的“值为 + 数字 + 单位”跨行形成真正的值列证据。
        return [
            {"text": "测试项目名称及适用场景说明", "x0": 55, "x1": 255, "top": 120, "bottom": 132},
            {"text": "典型值范围及附加注释内容", "x0": 350, "x1": 555, "top": 120, "bottom": 132},
            {"text": "供电轨配置与额定工作模式说明", "x0": 55, "x1": 260, "top": 165, "bottom": 177},
            {"text": "典型值为3.3伏特并包含容差注释", "x0": 350, "x1": 555, "top": 165, "bottom": 177},
            {"text": "时钟恢复路径及锁定过程说明", "x0": 55, "x1": 260, "top": 210, "bottom": 222},
            {"text": "最大值为25皮秒并给出限制依据", "x0": 350, "x1": 555, "top": 210, "bottom": 222},
        ]

    def extract_text(self, **_kwargs: object) -> str:
        # pdfplumber 的 y-first 文字层是此参数/值表应保留的原始行对应关系。
        return "\n".join(
            (
                "测试项目名称及适用场景说明 典型值范围及附加注释内容",
                "供电轨配置与额定工作模式说明 典型值为3.3伏特并包含容差注释",
                "时钟恢复路径及锁定过程说明 最大值为25皮秒并给出限制依据",
            )
        )


class ParserRoutingTests(unittest.TestCase):
    """路由标签必须反映互斥页面事实，而非根据告警文字猜测。"""

    def test_pure_classifier_has_documented_precedence_for_all_five_routes(self) -> None:
        """OCR、图像、版面风险与正常原生文本应按固定优先级分类。"""

        # 每一行覆盖公开枚举的一个唯一值，最后一个场景证明 OCR 优先于其他所有事实。
        cases = (
            ("native", "native text", False, False, False, PageParserRoute.NATIVE_TEXT),
            ("layout", "two columns", True, False, False, PageParserRoute.NATIVE_LAYOUT_RISK),
            ("image", "searchable layer", False, True, False, PageParserRoute.IMAGE_TEXT_LAYER),
            ("unreadable", "  ", False, True, False, PageParserRoute.UNREADABLE_IMAGE),
            ("ocr", "OCR text", True, True, True, PageParserRoute.OCR_FALLBACK),
        )
        # 逐项调用纯函数，禁止用文件名或 warnings 等间接线索替代事实输入。
        observed = {
            name: classify_page_parser_route(
                text=text,
                layout_risk=layout_risk,
                image_dominant=image_dominant,
                ocr_used=ocr_used,
            )
            for name, text, layout_risk, image_dominant, ocr_used, _expected in cases
        }
        # 精确映射可同时锁定五条分支和 OCR 的最高 precedence。
        self.assertEqual(
            {name: expected for name, *_facts, expected in cases},
            observed,
        )

    def test_public_ocr_extraction_keeps_image_and_ocr_facts_separate(self) -> None:
        """成功 OCR 页必须同时记录图像事实、OCR 事实和 OCR fallback 路由。"""

        # OCR 返回足够长的文字，确保真实 OCR fallback 不会被最小字符数门限拒绝。
        ocr_text = "1 Scope\nOCR evidence must remain explicitly reviewable."
        with tempfile.TemporaryDirectory() as temporary_directory:
            # 提取器会先检查路径存在，因此用最小 PDF 外壳承载伪 pdfplumber 页面。
            pdf_path = Path(temporary_directory) / "scan.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            with (
                mock.patch("pdfplumber.open", return_value=_OnePagePdf(_ImagePage())),
                mock.patch("shutil.which", return_value="/usr/local/bin/tesseract"),
                mock.patch("pytesseract.image_to_string", return_value=ocr_text),
            ):
                # 仅通过公开入口得到页面事实，避免把私有 OCR 返回值当成路由证据。
                extraction = extract_pdf_text(pdf_path)

        # OCR 成功不应抹掉扫描页图像事实，两者分别可供后续质量和审计层使用。
        page = extraction.pages[0]
        self.assertTrue(page.image_dominant)
        self.assertTrue(page.ocr_used)
        self.assertEqual(PageParserRoute.OCR_FALLBACK, page.parser_route)

    def test_public_image_pages_distinguish_searchable_layer_from_unreadable_input(self) -> None:
        """大图像页无论 OCR 是否可用，都必须保留可搜索与不可读两种不同路由。"""

        # 长文字层超过 OCR 跳过门限，因此可以验证“图像主导但可搜索”的非 OCR 路径。
        searchable_text = "1 Scope\n" + ("Searchable image text remains reviewable. " * 3)
        with tempfile.TemporaryDirectory() as temporary_directory:
            # 两个存在的占位 PDF 分别承载可搜索文字层和完全不可读图像页。
            searchable_pdf = Path(temporary_directory) / "searchable.pdf"
            unreadable_pdf = Path(temporary_directory) / "unreadable.pdf"
            searchable_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
            unreadable_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
            with (
                mock.patch("pdfplumber.open", side_effect=[
                    _OnePagePdf(_ImagePage(searchable_text)),
                    _OnePagePdf(_ImagePage()),
                ]),
                mock.patch("shutil.which", return_value=None),
            ):
                # 两次公共抽取分别覆盖健康文字层和缺少 OCR 引擎的图像失败态。
                searchable = extract_pdf_text(searchable_pdf)
                unreadable = extract_pdf_text(unreadable_pdf)

        # 可搜索图像页保留文字但不虚构 OCR；不可读页必须有明确的失败路由。
        self.assertTrue(searchable.pages[0].image_dominant)
        self.assertFalse(searchable.pages[0].ocr_used)
        self.assertEqual(PageParserRoute.IMAGE_TEXT_LAYER, searchable.pages[0].parser_route)
        self.assertTrue(unreadable.pages[0].image_dominant)
        self.assertFalse(unreadable.pages[0].ocr_used)
        self.assertEqual(PageParserRoute.UNREADABLE_IMAGE, unreadable.pages[0].parser_route)

    def test_public_native_extraction_distinguishes_linear_and_non_linear_routes(self) -> None:
        """普通原生文本与有明确双栏坐标证据的原生文本不能共用同一标签。"""

        with tempfile.TemporaryDirectory() as temporary_directory:
            # 真实轻量 PDF 覆盖普通线性原生提取，而伪页面仅隔离难以稳定写出的双栏坐标。
            native_pdf = Path(temporary_directory) / "native.pdf"
            two_column_pdf = Path(temporary_directory) / "two-column.pdf"
            write_multipage_text_pdf(native_pdf, [["1 Scope", "Linear native text remains readable."]])
            two_column_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
            # 线性页直接通过真实解析器，确保默认路由没有绕过公开入口。
            linear = extract_pdf_text(native_pdf)
            with mock.patch("pdfplumber.open", return_value=_OnePagePdf(_TwoColumnNativePage())):
                # 双栏场景同样走公开抽取与现有坐标风险分析，不调用私有分类状态。
                non_linear = extract_pdf_text(two_column_pdf)

        # 两类原生文字都没有图像或 OCR，但只有双栏页必须显式暴露阅读顺序风险。
        self.assertEqual(PageParserRoute.NATIVE_TEXT, linear.pages[0].parser_route)
        self.assertFalse(linear.pages[0].image_dominant)
        self.assertFalse(linear.pages[0].ocr_used)
        self.assertEqual(PageParserRoute.NATIVE_LAYOUT_RISK, non_linear.pages[0].parser_route)
        self.assertTrue(non_linear.pages[0].layout_risk)
        # 普通 “1 item” 计数不是工程单位：左栏末段仍必须在右栏首段之前。
        self.assertLess(
            non_linear.pages[0].text.index("Final left paragraph"),
            non_linear.pages[0].text.index("This condition paragraph"),
            non_linear.pages[0].text,
        )

    def test_public_borderless_parameter_table_keeps_row_major_text_despite_layout_risk(self) -> None:
        """无框参数/值表可标记双栏风险，但不能被 column-major 重排破坏行内语义。"""

        with tempfile.TemporaryDirectory() as temporary_directory:
            # 公开入口先检查文件存在，最小 PDF 外壳承载生产 pdfplumber 模拟页面。
            pdf_path = Path(temporary_directory) / "borderless-table.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            with mock.patch(
                "pdfplumber.open",
                return_value=_OnePagePdf(_BorderlessTwoColumnTablePage()),
            ):
                # 通过真实 extract_pdf_text 覆盖列优先重排、表格判别和 route 赋值的完整路径。
                page = extract_pdf_text(pdf_path).pages[0]

        # 每一行必须先出现左侧描述再出现右侧值；layout_risk 仍允许提示人工复核。
        self.assertLess(
            page.text.index("Requirement Value"),
            page.text.index("Supply voltage requirement"),
            page.text,
        )
        self.assertLess(
            page.text.index("3.3 Volts"),
            page.text.index("Timing window requirement"),
            page.text,
        )
        self.assertIn("25 Picoseconds", page.text)
        self.assertTrue(page.layout_risk)
        self.assertEqual(PageParserRoute.NATIVE_LAYOUT_RISK, page.parser_route)

    def test_public_english_label_value_table_keeps_row_major_text(self) -> None:
        """没有首表头时，重复数字单位仍必须保护英文参数表的逐行顺序。"""

        with tempfile.TemporaryDirectory() as temporary_directory:
            # 只模拟 pdfplumber 的页面边界，实际列检测、路由与文本输出继续经过公开 API。
            pdf_path = Path(temporary_directory) / "english-label-value-table.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            with mock.patch(
                "pdfplumber.open",
                return_value=_OnePagePdf(_EnglishLabelValueTablePage()),
            ):
                page = extract_pdf_text(pdf_path).pages[0]

        # 每个右侧数值必须仍在下一条左侧标签之前，证明没有被集中到右栏末尾。
        self.assertLess(
            page.text.index("3.3 Volts"),
            page.text.index("Timing window"),
            page.text,
        )
        self.assertLess(
            page.text.index("25 Picoseconds"),
            page.text.index("Output current"),
            page.text,
        )
        self.assertTrue(page.layout_risk)
        self.assertEqual(PageParserRoute.NATIVE_LAYOUT_RISK, page.parser_route)

    def test_public_numeric_value_grid_keeps_row_major_text_and_degraded_quality(self) -> None:
        """数值双栏即使是完整正文也保持逐行顺序与降级质量，避免轻量规则猜测列优先。"""

        with tempfile.TemporaryDirectory() as temporary_directory:
            # 此页几何上有双栏强证据；跨行数字单位是轻量路径的明确不重排边界。
            pdf_path = Path(temporary_directory) / "ambiguous-value-columns.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            with mock.patch(
                "pdfplumber.open",
                return_value=_OnePagePdf(_AmbiguousValueColumnsPage()),
            ):
                extraction = extract_pdf_text(pdf_path)

        # 即使数值嵌在完整句中，逐行顺序和 layout risk 仍保留，避免轻量规则擅自重排。
        page = extraction.pages[0]
        self.assertLess(
            page.text.index("typical value of 3.3 Volts"),
            page.text.index("The left narrative records"),
            page.text,
        )
        self.assertTrue(page.layout_risk)
        self.assertEqual(PageParserRoute.NATIVE_LAYOUT_RISK, page.parser_route)
        result = compare_extractions(extraction, extraction, DiffOptions())
        self.assertEqual("degraded", result.assessment.state.value)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_public_chinese_parallel_columns_use_column_major_text_and_remain_degraded(self) -> None:
        """中文 Y 重叠双栏应先完整输出左栏再输出右栏，且阅读顺序风险继续降级。"""

        with tempfile.TemporaryDirectory() as temporary_directory:
            # 临时 PDF 仅满足公共入口的路径校验，页面坐标与中文文本由 pdfplumber 边界模拟提供。
            pdf_path = Path(temporary_directory) / "chinese-columns.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            with mock.patch(
                "pdfplumber.open",
                return_value=_OnePagePdf(_ChineseTwoColumnNativePage()),
            ):
                # 公共提取路径必须以坐标证据重建中文列优先文本，而非依赖字体或 PDF 写入器能力。
                extraction = extract_pdf_text(pdf_path)

        page = extraction.pages[0]
        # 左栏第四段必须在右栏第一段之前，证明不是默认按 y 交织的逐行输出。
        self.assertLess(
            page.text.index("左栏连续正文第四段"),
            page.text.index("右栏连续正文第一段"),
            page.text,
        )
        self.assertTrue(page.layout_risk)
        self.assertEqual(PageParserRoute.NATIVE_LAYOUT_RISK, page.parser_route)
        result = compare_extractions(extraction, extraction, DiffOptions())
        self.assertEqual("degraded", result.assessment.state.value)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_public_chinese_borderless_parameter_table_keeps_row_major_text(self) -> None:
        """Unicode 正文证据扩大后，中文参数/值表仍必须保持每行左→右的可读顺序。"""

        with tempfile.TemporaryDirectory() as temporary_directory:
            # 公共入口下的伪页面复用真实坐标分析，避免只测私有正则而漏掉重排调用点。
            pdf_path = Path(temporary_directory) / "chinese-borderless-table.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            with mock.patch(
                "pdfplumber.open",
                return_value=_OnePagePdf(_ChineseBorderlessTablePage()),
            ):
                # 表头和数值单位分别覆盖两种中文无框表负证据。
                page = extract_pdf_text(pdf_path).pages[0]

        # 右侧表头和值必须保留在对应左侧说明之后，而不能被挪到所有左列内容之后。
        self.assertLess(
            page.text.index("要求数值"),
            page.text.index("供电电压参数"),
            page.text,
        )
        self.assertLess(
            page.text.index("3.3伏特"),
            page.text.index("时序窗口参数"),
            page.text,
        )
        self.assertIn("25皮秒", page.text)
        self.assertTrue(page.layout_risk)
        self.assertEqual(PageParserRoute.NATIVE_LAYOUT_RISK, page.parser_route)

    def test_public_chinese_long_label_value_table_keeps_row_major_text(self) -> None:
        """跨行“值为+数字+单位”证据应保护中文长标签无框表的行内对应关系。"""

        with tempfile.TemporaryDirectory() as temporary_directory:
            # 仅替换 pdfplumber 边界；页面分析、路由与公共抽取 API 都由生产代码执行。
            pdf_path = Path(temporary_directory) / "chinese-long-value-table.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            with mock.patch(
                "pdfplumber.open",
                return_value=_OnePagePdf(_ChineseLongLabelValueTablePage()),
            ):
                page = extract_pdf_text(pdf_path).pages[0]

        # 后两条左栏说明必须仍各自先于右栏典型值/最大值，而不是被集中到所有右栏文字之后。
        self.assertLess(
            page.text.index("典型值为3.3伏特"),
            page.text.index("时钟恢复路径"),
            page.text,
        )
        self.assertIn("最大值为25皮秒", page.text)
        self.assertTrue(page.layout_risk)
        self.assertEqual(PageParserRoute.NATIVE_LAYOUT_RISK, page.parser_route)

    def test_quality_metrics_group_each_selected_page_by_one_parser_route(self) -> None:
        """聚合路由必须覆盖每个选择页一次，并保留旧 PageText 的默认兼容路线。"""

        # 第一页刻意采用旧式最简构造，其余页面以显式互斥路由模拟完整抽取事实。
        pages = [
            PageText(page_number=1, text="legacy native page"),
            PageText(page_number=2, text="two columns", layout_risk=True, parser_route=PageParserRoute.NATIVE_LAYOUT_RISK),
            PageText(page_number=3, text="searchable layer", image_dominant=True, parser_route=PageParserRoute.IMAGE_TEXT_LAYER),
            PageText(page_number=4, text="", image_dominant=True, parser_route=PageParserRoute.UNREADABLE_IMAGE),
            PageText(page_number=5, text="OCR text", layout_risk=True, ocr_used=True, image_dominant=True, parser_route=PageParserRoute.OCR_FALLBACK),
        ]
        # 用同一 extraction 比较可走质量聚合的公开路径而无需伪造内部 metrics。
        extraction = ExtractionResult(
            pdf_path=Path("routed.pdf"),
            pages=pages,
            total_pages=5,
            selected_start_page=1,
            selected_end_page=5,
        )
        result = compare_extractions(extraction, extraction, DiffOptions())

        # 旧构造自动归为 native_text，每个选中页在扁平化结果中只能出现一次。
        routes = result.assessment.old_document.parser_route_pages
        self.assertEqual(
            (
                ("native_text", (1,)),
                ("native_layout_risk", (2,)),
                ("ocr_fallback", (5,)),
                ("image_text_layer", (3,)),
                ("unreadable_image", (4,)),
            ),
            routes,
        )
        self.assertEqual((1, 2, 5, 3, 4), tuple(page for _route, pages in routes for page in pages))
        self.assertEqual((5,), result.assessment.old_document.ocr_pages)

    def test_legacy_page_facts_derive_routes_for_metrics_and_audit(self) -> None:
        """旧构造仅给出风险事实时，模型必须派生自洽路由并传递到全部审计出口。"""

        # 刻意不传 parser_route，模拟升级前调用方只知道 layout/OCR/图像事实的构造方式。
        pages = [
            PageText(page_number=1, text="native"),
            PageText(page_number=2, text="two columns", layout_risk=True),
            PageText(page_number=3, text="searchable image", image_dominant=True),
            PageText(page_number=4, text="", image_dominant=True),
            PageText(
                page_number=5,
                text="OCR evidence",
                layout_risk=True,
                image_dominant=True,
                ocr_used=True,
            ),
        ]
        # 逐页断言不变量，使老调用方的已有事实不再与默认 native_text 标签矛盾。
        self.assertEqual(
            (
                PageParserRoute.NATIVE_TEXT,
                PageParserRoute.NATIVE_LAYOUT_RISK,
                PageParserRoute.IMAGE_TEXT_LAYER,
                PageParserRoute.UNREADABLE_IMAGE,
                PageParserRoute.OCR_FALLBACK,
            ),
            tuple(page.parser_route for page in pages),
        )
        extraction = ExtractionResult(
            pdf_path=Path("legacy-routes.pdf"),
            pages=pages,
            total_pages=5,
            selected_start_page=1,
            selected_end_page=5,
        )
        # 比较与写报告是旧 PageText 最常用的两个消费路径，二者都必须看到同一套派生路由。
        result = compare_extractions(extraction, extraction, DiffOptions())
        with tempfile.TemporaryDirectory() as temporary_directory:
            outputs = write_reports(result, temporary_directory, DiffOptions())
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        # metrics 的 grouped pages 与每页 audit 的 route 顺序共同锁定跨层一致性。
        self.assertEqual(
            [
                ["native_text", [1]],
                ["native_layout_risk", [2]],
                ["ocr_fallback", [5]],
                ["image_text_layer", [3]],
                ["unreadable_image", [4]],
            ],
            payload["assessment"]["old_document"]["parser_route_pages"],
        )
        self.assertEqual(
            [
                "native_text",
                "native_layout_risk",
                "image_text_layer",
                "unreadable_image",
                "ocr_fallback",
            ],
            [record["parser_route"] for record in payload["extraction_audit"]["old"]],
        )

    def test_explicit_conflicting_route_is_normalized_to_page_facts(self) -> None:
        """显式陈旧 route 不能覆盖更可靠的 OCR/图像/版面事实。"""

        # 调用方错误指定 native_text，但 OCR 事实优先级最高，模型应统一归一化为 OCR fallback。
        page = PageText(
            page_number=1,
            text="OCR evidence",
            layout_risk=True,
            image_dominant=True,
            ocr_used=True,
            parser_route=PageParserRoute.NATIVE_TEXT,
        )

        # 选择归一化而非抛异常，保证反序列化历史记录时仍可得到明确、可信的审计出口。
        self.assertEqual(PageParserRoute.OCR_FALLBACK, page.parser_route)

    def test_legacy_image_routes_cannot_be_reliable_without_extract_side_effects(self) -> None:
        """替代后端只报告图像事实时，可搜索和不可读图像页也不能被质量层误判可靠。"""

        # 文字量刻意超过可靠阈值，证明 searchable 图像页降级来自图像事实而非字数不足。
        searchable_body = (
            "The receiver shall preserve every timing requirement, voltage limit, "
            "calibration condition, and compliance record. "
        ) * 12
        searchable = ExtractionResult(
            pdf_path=Path("legacy-searchable-image.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=f"1 Scope\n{searchable_body}",
                    image_dominant=True,
                )
            ],
            total_pages=1,
            selected_start_page=1,
            selected_end_page=1,
        )
        unreadable = ExtractionResult(
            pdf_path=Path("legacy-unreadable-image.pdf"),
            pages=[PageText(page_number=1, text="", image_dominant=True)],
            total_pages=1,
            selected_start_page=1,
            selected_end_page=1,
        )
        # 对同一输入自比较可排除文字差异的干扰，只验证质量入口是否尊重图像事实。
        searchable_result = compare_extractions(searchable, searchable, DiffOptions())
        unreadable_result = compare_extractions(unreadable, unreadable, DiffOptions())
        with tempfile.TemporaryDirectory() as temporary_directory:
            # 两份 JSON 审计都应保留 image_dominant 与已派生的页面路由。
            searchable_outputs = write_reports(
                searchable_result,
                Path(temporary_directory) / "searchable",
                DiffOptions(),
            )
            unreadable_outputs = write_reports(
                unreadable_result,
                Path(temporary_directory) / "unreadable",
                DiffOptions(),
            )
            searchable_payload = json.loads(
                searchable_outputs["json"].read_text(encoding="utf-8")
            )
            unreadable_payload = json.loads(
                unreadable_outputs["json"].read_text(encoding="utf-8")
            )

        # 可搜索图像页必须显式 degraded；空不可读页保留 indeterminate，但绝不能给出 reliable 结论。
        self.assertEqual("degraded", searchable_result.assessment.state.value)
        self.assertFalse(searchable_result.assessment.allows_no_difference_conclusion)
        self.assertTrue(any("栅格" in reason for reason in searchable_result.assessment.reasons))
        self.assertNotEqual("reliable", unreadable_result.assessment.state.value)
        self.assertFalse(unreadable_result.assessment.allows_no_difference_conclusion)
        # 新质量指标与逐页 JSON audit 必须对两个 legacy/alternative-backend 场景保持一致。
        self.assertEqual(
            [1],
            searchable_payload["assessment"]["old_document"]["image_dominant_pages"],
        )
        self.assertEqual(
            [1],
            unreadable_payload["assessment"]["old_document"]["image_dominant_pages"],
        )
        self.assertEqual(
            "image_text_layer",
            searchable_payload["extraction_audit"]["old"][0]["parser_route"],
        )
        self.assertEqual(
            "unreadable_image",
            unreadable_payload["extraction_audit"]["old"][0]["parser_route"],
        )
        self.assertTrue(searchable_payload["extraction_audit"]["old"][0]["image_dominant"])
        self.assertTrue(unreadable_payload["extraction_audit"]["old"][0]["image_dominant"])

    def test_diff_result_keeps_only_lightweight_page_audit_snapshot(self) -> None:
        """比较结果应压缩页面审计事实，不能长期引用完整 ExtractionResult 或其块文本。"""

        # 用含敏感块文字的 OCR 页面验证快照只保留报告需要的标量事实与块数量。
        block = DocumentBlock(
            page_number=1,
            bbox=(0.0, 0.0, 80.0, 30.0),
            kind=DocumentBlockKind.OCR,
            text="FULL_BLOCK_TEXT_MUST_NOT_BE_RETAINED",
            reading_order=0,
            source_engine="tesseract",
        )
        page = PageText(
            page_number=1,
            text="1 Scope\nFull page text remains comparison input only.",
            layout_risk=True,
            ocr_used=True,
            image_dominant=True,
            blocks=(block,),
        )
        extraction = ExtractionResult(
            pdf_path=Path("snapshot.pdf"),
            pages=[page],
            total_pages=1,
            selected_start_page=1,
            selected_end_page=1,
        )
        # 公开比较入口必须在返回前把页面事实变为不可变、无正文的 audit snapshot。
        result = compare_extractions(extraction, extraction, DiffOptions())

        # 快照保留 JSON 审计所需的全部字段，但不含 PageText、DocumentBlock 或原始 ExtractionResult。
        expected_snapshot = (
            PageExtractionAudit(
                page_number=1,
                parser_route=PageParserRoute.OCR_FALLBACK,
                image_dominant=True,
                ocr_used=True,
                layout_risk=True,
                block_count=1,
                comparison_text_source="native",
                layout_backend_version=None,
            ),
        )
        self.assertEqual(expected_snapshot, result.old_extraction_audit)
        self.assertEqual(expected_snapshot, result.new_extraction_audit)
        self.assertFalse(hasattr(result, "old_extraction"))
        self.assertFalse(hasattr(result, "new_extraction"))
        self.assertFalse(any(value is extraction for value in result.__dict__.values()))
        with tempfile.TemporaryDirectory() as temporary_directory:
            # JSON 审计仍从压缩快照生成，格式必须与此前逐页事实合同相同。
            outputs = write_reports(result, temporary_directory, DiffOptions())
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        # 报告数据与快照一致，同时不直接序列化被比较页或块的完整文字。
        self.assertEqual(
            [{
                "page_number": 1,
                "parser_route": "ocr_fallback",
                "image_dominant": True,
                "ocr_used": True,
                "layout_risk": True,
                "block_count": 1,
                "comparison_text_source": "native",
                "layout_backend_version": None,
            }],
            payload["extraction_audit"]["old"],
        )
        self.assertNotIn("FULL_BLOCK_TEXT_MUST_NOT_BE_RETAINED", json.dumps(payload["extraction_audit"]))

    def test_json_report_serializes_route_audit_without_page_or_block_text(self) -> None:
        """机器可读报告应给出路由证据，但 extraction_audit 中不能泄漏文字内容。"""

        # 块正文与页面正文使用不同私有令牌，确保测试能识别两类意外泄漏。
        block = DocumentBlock(
            page_number=1,
            bbox=(0.0, 0.0, 100.0, 40.0),
            kind=DocumentBlockKind.OCR,
            text="BLOCK_SECRET_MUST_NOT_APPEAR",
            reading_order=0,
            source_engine="tesseract",
        )
        page = PageText(
            page_number=1,
            text="1 Scope\nPAGE_SECRET_MUST_NOT_APPEAR",
            layout_risk=True,
            ocr_used=True,
            blocks=(block,),
            image_dominant=True,
            parser_route=PageParserRoute.OCR_FALLBACK,
        )
        extraction = ExtractionResult(
            pdf_path=Path("private.pdf"),
            pages=[page],
            total_pages=1,
            selected_start_page=1,
            selected_end_page=1,
        )
        result = compare_extractions(extraction, extraction, DiffOptions())
        with tempfile.TemporaryDirectory() as temporary_directory:
            # 真实 JSON 写入路径验证报告层不会直接序列化 PageText 或 DocumentBlock。
            outputs = write_reports(result, temporary_directory, DiffOptions())
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        # 指标输出保留路由到页号映射，便于外部工具汇总而不重算页面分类。
        self.assertEqual(
            [["ocr_fallback", [1]]],
            payload["assessment"]["old_document"]["parser_route_pages"],
        )
        audit = payload["extraction_audit"]["old"]
        # 每页审计只允许稳定事实字段和块数量，禁止暴露 OCR/原生/表格的原文内容。
        self.assertEqual(
            [{
                "page_number": 1,
                "parser_route": "ocr_fallback",
                "image_dominant": True,
                "ocr_used": True,
                "layout_risk": True,
                "block_count": 1,
                "comparison_text_source": "native",
                "layout_backend_version": None,
            }],
            audit,
        )
        audit_text = json.dumps(audit, ensure_ascii=False)
        self.assertNotIn("BLOCK_SECRET_MUST_NOT_APPEAR", audit_text)
        self.assertNotIn("PAGE_SECRET_MUST_NOT_APPEAR", audit_text)


if __name__ == "__main__":
    # 允许在 PyCharm 中直接运行此模块，同时保持 unittest discover 行为一致。
    unittest.main()
