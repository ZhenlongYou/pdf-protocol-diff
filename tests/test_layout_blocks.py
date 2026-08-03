"""公开 PDF 抽取路径的不可变版面证据回归测试。"""

from __future__ import annotations

from pathlib import Path  # 临时 PDF 路径使用平台无关的 Path。
from types import SimpleNamespace  # 模拟 pdfplumber 的最小渲染对象，不依赖私有实现。
import tempfile  # 每个测试生成后自动清理独立的 PDF 输入。
import unittest  # 沿用项目现有的标准库测试框架。
from unittest import mock  # 只替换外部 PDF/OCR 边界，公开 extract 路径仍然真实执行。

from PIL import Image  # 伪页面渲染使用真实 Pillow 图像，覆盖 OCR/表格截图入口。

from protocol_pdf_diff.layout_blocks import (
    extract_pdfplumber_text_blocks,
    reassign_block_reading_order,
)
from protocol_pdf_diff.page_ocr import extract_scan_page_text
from protocol_pdf_diff.pdf_extract import extract_pdf_text
from protocol_pdf_diff.models import DocumentBlock, DocumentBlockKind, PageText
from protocol_pdf_diff.sample_data import write_multipage_text_pdf


class _OnePagePdf:
    """为公开 ``extract_pdf_text`` 提供可关闭的单页 pdfplumber 容器。"""

    def __init__(self, page: object) -> None:
        # 生产代码以列表读取 pages，因此最小伪对象也保留同样外观。
        self.pages = [page]

    def close(self) -> None:
        # 内存伪对象没有底层句柄，但公开提取器会始终调用 close。
        return None


class _RasterPage:
    """覆盖整页图像的最小 pdfplumber 页面，用来走真实 OCR fallback。"""

    width = 600
    height = 800
    bbox = (0.0, 0.0, 600.0, 800.0)
    chars: list[object] = []
    lines: list[object] = []
    rects: list[object] = []
    images = [{"x0": 0, "x1": 600, "top": 0, "bottom": 800}]

    def __init__(self, native_text: str = "") -> None:
        # 短原生文字层用于证明 OCR 块不能错误归因为 tesseract。
        self._native_text = native_text

    def filter(self, _predicate: object) -> "_RasterPage":
        # 没有水印字符时过滤视图与原页面相同。
        return self

    def extract_words(self, **_kwargs: object) -> list[dict[str, object]]:
        # 扫描页没有原生坐标词，OCR 块应是唯一的文字证据。
        return []

    def extract_text(self, **_kwargs: object) -> str:
        # 默认扫描页没有原生文字层，但测试可注入短可选择层覆盖混合页路径。
        return self._native_text

    def to_image(self, **_kwargs: object) -> SimpleNamespace:
        # 产生有限的 RGB 图像，让 OCR 渲染入口在测试中真实执行。
        return SimpleNamespace(original=Image.new("RGB", (1200, 1600), "white"))


class _TableObject:
    """提供 table.extract 和 bbox 的最小表格对象。"""

    bbox = (60.0, 180.0, 540.0, 300.0)

    def extract(self) -> list[list[str]]:
        # 两列表格足以产生可审计的结构化表格行。
        return [["Parameter", "Value"], ["Voltage", "12 V"]]


class _TablePage:
    """包含原生坐标文字和一个可接受表格的最小 pdfplumber 页面。"""

    width = 600
    height = 800
    bbox = (0.0, 0.0, 600.0, 800.0)
    chars: list[object] = []
    lines: list[object] = []
    rects: list[object] = []
    images: list[object] = []

    def __init__(self) -> None:
        # 调用计数锁定同一过滤页坐标词只能被提取和校验一次。
        self.extract_word_call_count = 0

    def filter(self, _predicate: object) -> "_TablePage":
        # 没有水印字符时直接返回同一页视图即可。
        return self

    def extract_words(self, **_kwargs: object) -> list[dict[str, object]]:
        # 页面标题和正文各占一行，验证表格块会在原生块之后统一编号。
        self.extract_word_call_count += 1
        return [
            {"text": "Table", "x0": 60, "x1": 90, "top": 100, "bottom": 112},
            {"text": "1", "x0": 94, "x1": 101, "top": 100, "bottom": 112},
            {"text": "Voltage", "x0": 60, "x1": 105, "top": 140, "bottom": 152},
            {"text": "limits", "x0": 109, "x1": 145, "top": 140, "bottom": 152},
        ]

    def extract_text(self, **_kwargs: object) -> str:
        # 标题触发表格提取，同时为表题 helper 提供一致的可选择文字。
        return "Table 1 Voltage limits\nVoltage limit is 12 V"

    def find_tables(self) -> list[_TableObject]:
        # 生产表格路径只依赖公开 find_tables 返回的对象序列。
        return [_TableObject()]

    def crop(self, _bbox: object) -> "_TablePage":
        # 表题和截图测试不需要真的裁剪像素，只需维持公开页面方法。
        return self

    def to_image(self, **_kwargs: object) -> SimpleNamespace:
        # 用真实图像使表格截图和网格摘要路径可执行。
        return SimpleNamespace(original=Image.new("RGB", (1200, 1600), "white"))


class LayoutBlockTests(unittest.TestCase):
    """验证抽取结果既保留比较文本，也提供可审计的坐标块。"""

    def test_public_extraction_returns_contiguous_native_text_blocks(self) -> None:
        """定位的原生文字必须成为按阅读顺序连续编号的文本块。"""

        # 使用真实 PDF 写入和公开抽取入口，避免测试与内部坐标 helper 绑定。
        with tempfile.TemporaryDirectory() as temporary_directory:
            # 临时目录中的文件只用于本测试，不能成为仓库的二进制 fixture。
            pdf_path = Path(temporary_directory) / "positioned-native.pdf"
            # 每一行在 PDF 中具有不同纵向位置，足以验证实际坐标阅读顺序。
            write_multipage_text_pdf(
                pdf_path,
                [["1 Scope", "Voltage limit is 12 V", "End of requirement."]],
            )
            # 通过生产接口获得页级文字和新增的块证据。
            extraction = extract_pdf_text(pdf_path)

        # 块应保留每个可见物理行，且没有改变页面原有可比较文字。
        blocks = extraction.pages[0].blocks
        self.assertEqual(3, len(blocks))
        self.assertEqual("1 Scope", blocks[0].text)
        self.assertEqual("Voltage limit is 12 V", blocks[1].text)
        self.assertEqual((0, 1, 2), tuple(block.reading_order for block in blocks))
        self.assertEqual((1, 1, 1), tuple(block.page_number for block in blocks))
        self.assertTrue(all(block.kind.value == "text" for block in blocks))
        self.assertTrue(all(block.source_engine == "pdfplumber" for block in blocks))
        self.assertTrue(all(block.confidence is None for block in blocks))
        self.assertTrue(all(len(block.bbox) == 4 for block in blocks))

    def test_coordinate_word_geometry_skips_bad_evidence_and_stably_orders_lines(self) -> None:
        """空白、无效和重复词不得污染块，剩余词按 top/x0 形成物理行。"""

        class CoordinatePage:
            """只暴露本纯几何规则所需的公开 ``extract_words`` 方法。"""

            def extract_words(self, **_kwargs: object) -> list[dict[str, object]]:
                # 故意打乱输入、加入重复/空白/NaN/零面积项，验证输出不依赖输入顺序。
                return [
                    {"text": "Right", "x0": 160, "x1": 200, "top": 100, "bottom": 112},
                    {"text": " ", "x0": 10, "x1": 20, "top": 90, "bottom": 102},
                    {"text": "Below", "x0": 80, "x1": 120, "top": 145, "bottom": 157},
                    {"text": "Left", "x0": 60, "x1": 100, "top": 100, "bottom": 112},
                    {"text": "Right", "x0": 160, "x1": 200, "top": 100, "bottom": 112},
                    {"text": "NaN", "x0": float("nan"), "x1": 30, "top": 110, "bottom": 122},
                    {"text": "Flat", "x0": 10, "x1": 30, "top": 120, "bottom": 120},
                ]

        # 直接调用纯几何 public helper，隔离坐标清洗而不伪造 PDF 文字层。
        blocks, warnings = extract_pdfplumber_text_blocks(CoordinatePage(), page_number=7)

        # 视觉左右顺序必须覆盖打乱输入，第二个 top 则构成下一条连续块。
        self.assertEqual(("Left Right", "Below"), tuple(block.text for block in blocks))
        self.assertEqual((0, 1), tuple(block.reading_order for block in blocks))
        self.assertEqual((7, 7), tuple(block.page_number for block in blocks))
        self.assertTrue(any("空白" in warning for warning in warnings))
        self.assertTrue(any("重复" in warning for warning in warnings))
        self.assertGreaterEqual(sum("坐标无效" in warning for warning in warnings), 2)

    def test_final_block_order_uses_geometry_across_native_and_table_kinds(self) -> None:
        """标题、表格与正文应按 bbox 位置排序，重叠时按显式种类规则稳定打破平局。"""

        # 故意以错误的追加顺序构造正文、标题和表格，检验最终排序并非调用顺序。
        body = DocumentBlock(
            page_number=1,
            bbox=(60.0, 340.0, 540.0, 360.0),
            kind=DocumentBlockKind.TEXT,
            text="Body at top 340",
            reading_order=99,
            source_engine="pdfplumber",
        )
        title = DocumentBlock(
            page_number=1,
            bbox=(60.0, 100.0, 540.0, 120.0),
            kind=DocumentBlockKind.TEXT,
            text="Title at top 100",
            reading_order=99,
            source_engine="pdfplumber",
        )
        table = DocumentBlock(
            page_number=1,
            bbox=(60.0, 180.0, 540.0, 300.0),
            kind=DocumentBlockKind.TABLE,
            text="Table at top 180",
            reading_order=99,
            source_engine="pdfplumber",
        )
        # 同 bbox 的两种来源要求按 kind 的固定次序排序，避免底层收集顺序泄漏进审计结果。
        overlapping_ocr = DocumentBlock(
            page_number=1,
            bbox=(60.0, 400.0, 540.0, 430.0),
            kind=DocumentBlockKind.OCR,
            text="OCR tie",
            reading_order=99,
            source_engine="tesseract",
        )
        overlapping_text = DocumentBlock(
            page_number=1,
            bbox=(60.0, 400.0, 540.0, 430.0),
            kind=DocumentBlockKind.TEXT,
            text="Text tie",
            reading_order=99,
            source_engine="pdfplumber",
        )

        # 重编号函数是全页证据的最终入口，因此直接覆盖跨种类几何排序合同。
        ordered = reassign_block_reading_order(
            [body, overlapping_ocr, table, title, overlapping_text]
        )

        # 100pt 标题、180pt 表格、340pt 正文必须先后出现，重叠处 TEXT 再 OCR。
        self.assertEqual(
            ("Title at top 100", "Table at top 180", "Body at top 340", "Text tie", "OCR tie"),
            tuple(block.text for block in ordered),
        )
        self.assertEqual(tuple(range(5)), tuple(block.reading_order for block in ordered))

    def test_successful_public_ocr_adds_tesseract_page_bounds_block(self) -> None:
        """成功整页 OCR 必须保留 tesseract 来源、页 bbox 与空置信度。"""

        # OCR 文字特意与原生页不同，确保断言检验真正 OCR 输出而非默认页面文本。
        ocr_text = "1 Scope\nOCR evidence must remain reviewable."
        with tempfile.TemporaryDirectory() as temporary_directory:
            # 提取器先检查文件存在，伪 pdfplumber 打开前须有一个最小 PDF 文件。
            pdf_path = Path(temporary_directory) / "scan.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            with (
                mock.patch("pdfplumber.open", return_value=_OnePagePdf(_RasterPage())),
                mock.patch("shutil.which", return_value="/usr/local/bin/tesseract"),
                mock.patch("pytesseract.image_to_string", return_value=ocr_text),
            ):
                # 公共入口同时执行扫描页判定、OCR 和块汇总。
                extraction = extract_pdf_text(pdf_path)

        # OCR 是一个单独类别，不能被混同成 pdfplumber 原生文字块。
        ocr_blocks = [
            block
            for block in extraction.pages[0].blocks
            if block.kind is DocumentBlockKind.OCR
        ]
        self.assertEqual(1, len(ocr_blocks))
        self.assertEqual("tesseract", ocr_blocks[0].source_engine)
        self.assertEqual((0.0, 0.0, 600.0, 800.0), ocr_blocks[0].bbox)
        self.assertIn("OCR evidence must remain reviewable.", ocr_blocks[0].text)
        self.assertIsNone(ocr_blocks[0].confidence)
        self.assertEqual((0,), tuple(block.reading_order for block in extraction.pages[0].blocks))

    def test_ocr_block_excludes_short_native_text_layer(self) -> None:
        """短原生文字层可触发 OCR，但不能被归因到 tesseract 证据块。"""

        # 两个唯一标记分别代表搜索文字层和真实 OCR 返回，避免只检查块数量的假绿。
        native_marker = "NATIVE_ONLY_MARKER"
        ocr_marker = "OCR_ONLY_MARKER"
        # OCR 文本必须超过最低字符阈值，才能进入成功 OCR 和块创建路径。
        ocr_text = f"{ocr_marker} remains a reviewable OCR observation."
        with tempfile.TemporaryDirectory() as temporary_directory:
            # 公开入口仍要求输入路径存在，即使 pdfplumber 边界被最小页面模拟替换。
            pdf_path = Path(temporary_directory) / "mixed-text-layer.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            with (
                mock.patch(
                    "pdfplumber.open",
                    return_value=_OnePagePdf(_RasterPage(native_marker)),
                ),
                mock.patch("shutil.which", return_value="/usr/local/bin/tesseract"),
                mock.patch("pytesseract.image_to_string", return_value=ocr_text),
            ):
                # 合并页面文字继续供比较器使用，块则必须单独保留原始 OCR 流。
                extraction = extract_pdf_text(pdf_path)

        # PageText 保持既有合并语义，两个来源的可比较文字都必须存在。
        self.assertIn(native_marker, extraction.pages[0].text)
        self.assertIn(ocr_marker, extraction.pages[0].text)
        # tesseract 块只能携带 OCR 实际观测，绝不能误带短原生文字层。
        ocr_blocks = [
            block
            for block in extraction.pages[0].blocks
            if block.kind is DocumentBlockKind.OCR
        ]
        self.assertEqual(1, len(ocr_blocks))
        self.assertIn(ocr_marker, ocr_blocks[0].text)
        self.assertNotIn(native_marker, ocr_blocks[0].text)

    def test_public_ocr_helper_keeps_legacy_four_value_unpacking(self) -> None:
        """外部调用者仍能解包公开 OCR helper 的原有四个返回值。"""

        # 真实 OCR 路径需要超过最小字符阈值的返回值，避免测试落入未成功分支。
        raw_ocr_text = "Legacy OCR result remains long enough for the public helper."
        with (
            mock.patch("shutil.which", return_value="/usr/local/bin/tesseract"),
            mock.patch("pytesseract.image_to_string", return_value=raw_ocr_text),
        ):
            # 这行若被改成五元组就会立即报 ValueError，保护公开 API 兼容性。
            text, warnings, ocr_used, image_dominant = extract_scan_page_text(
                _RasterPage(),
                "",
                "legacy.pdf",
                1,
            )

        # 四项继续表达原先的公开语义，原始 OCR 流只能由内部证据路径访问。
        self.assertIn("Legacy OCR result", text)
        self.assertTrue(warnings)
        self.assertTrue(ocr_used)
        self.assertTrue(image_dominant)

    def test_public_table_visual_becomes_pdfplumber_table_block(self) -> None:
        """已接受表格视觉证据必须共享其 bbox 和行摘要，而非改写比较文本。"""

        with tempfile.TemporaryDirectory() as temporary_directory:
            # 伪 PDF 文件只负责通过公开输入校验，页面行为由 pdfplumber 边界模拟。
            pdf_path = Path(temporary_directory) / "table.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            with (
                mock.patch("pdfplumber.open", return_value=_OnePagePdf(_TablePage())),
                mock.patch("shutil.which", return_value=None),
            ):
                # 公共入口应保留原有表格行，同时附加不参与比较的表格块。
                extraction = extract_pdf_text(pdf_path)

        # 块来自已接受的 TableVisual，二者 bbox 必须严格相同以便截图回查。
        table_blocks = [
            block
            for block in extraction.pages[0].blocks
            if block.kind is DocumentBlockKind.TABLE
        ]
        self.assertEqual(1, len(extraction.table_visuals))
        self.assertEqual(1, len(table_blocks))
        self.assertEqual(extraction.table_visuals[0].bbox, table_blocks[0].bbox)
        self.assertEqual(_TablePage.bbox, extraction.table_visuals[0].page_bbox)  # 报告层只能用真实原页边界证明靠页边的跨页几何。
        self.assertEqual("pdfplumber", table_blocks[0].source_engine)
        self.assertEqual("\n".join(extraction.table_visuals[0].row_texts), table_blocks[0].text)
        self.assertIn(table_blocks[0].text, extraction.pages[0].text)
        self.assertEqual(
            tuple(range(len(extraction.pages[0].blocks))),
            tuple(block.reading_order for block in extraction.pages[0].blocks),
        )

    def test_public_extraction_reads_filtered_coordinate_words_once(self) -> None:
        """同一过滤页的坐标词提取结果应复用于风险检查和文本块生成。"""

        # 该页面同时有原生文字、阅读顺序分析和表格证据，能覆盖两个消费者。
        page = _TablePage()
        with tempfile.TemporaryDirectory() as temporary_directory:
            # 公开入口先验证输入路径存在，再由 mock 提供最小 pdfplumber 容器。
            pdf_path = Path(temporary_directory) / "single-coordinate-read.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            with (
                mock.patch("pdfplumber.open", return_value=_OnePagePdf(page)),
                mock.patch("shutil.which", return_value=None),
            ):
                # 抽取结果仍走完整生产路径，不能用私有 helper 掩盖重复调用。
                extract_pdf_text(pdf_path)

        # Blocks 和 layout-risk 必须共享一次过滤页 extract_words 观测。
        self.assertEqual(1, page.extract_word_call_count)

    def test_legacy_page_text_has_empty_blocks_and_document_blocks_are_immutable(self) -> None:
        """旧调用者不传 blocks 时保持空元组，新增块模型不允许原地篡改。"""

        # 兼容性断言保护大量既有 ``PageText(page_number, text)`` 调用。
        self.assertEqual((), PageText(page_number=1, text="legacy").blocks)
        # 直接构造最小块以验证 frozen dataclass 的公开不可变合同。
        block = DocumentBlock(
            page_number=1,
            bbox=(0.0, 0.0, 10.0, 10.0),
            kind=DocumentBlockKind.TEXT,
            text="immutable",
            reading_order=0,
            source_engine="pdfplumber",
        )
        # 原地赋值应明确失败，防止审计证据在报告阶段被悄悄改写。
        with self.assertRaises(AttributeError):
            block.text = "changed"  # type: ignore[misc]


if __name__ == "__main__":
    # 允许 PyCharm 直接运行本文件时复用同一组 unittest。
    unittest.main()
