"""OCR 累计预算与语言的边界检查；使用可控时钟避免等待真实超时。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from protocol_pdf_diff.models import DiffOptions
from protocol_pdf_diff.ocr_cache import cached_image_to_string
from protocol_pdf_diff.ocr_policy import current_ocr_language, ocr_scope
from protocol_pdf_diff.pdf_extract import _ocr_table_image, extract_pdf_text
from tests.test_page_ocr import _OnePagePdf, _ScannedPage


class OcrPolicyTests(unittest.TestCase):
    def test_shared_budget_limits_live_calls_and_resets_after_failure(self):
        """第二次识别受前一次用时影响，退出后新任务不继承耗尽状态。"""
        image = Image.new("RGB", (4, 4), "white")  # 仅核对调用预算，不检验 OCR 引擎。
        with patch("protocol_pdf_diff.ocr_cache._cache_directory", return_value=None), \
             patch("protocol_pdf_diff.ocr_cache._call_tesseract", return_value="text") as recognize, \
             patch("protocol_pdf_diff.ocr_policy.monotonic", side_effect=[0, 6]):
            with ocr_scope("chi_sim+eng", seconds=5) as budget:
                cached_image_to_string(image, config="--psm 3", timeout=60)
                with self.assertRaisesRegex(RuntimeError, "累计 OCR"):
                    cached_image_to_string(image, config="--psm 6", timeout=60)
                self.assertEqual(1, budget.skipped)  # 耗尽后不启动新子进程。
            self.assertEqual(1, recognize.call_count)
            self.assertEqual(5, recognize.call_args.kwargs["timeout"])
            self.assertEqual("chi_sim+eng", recognize.call_args.kwargs["lang"])
        self.assertIsNone(current_ocr_language())  # 失败和正常退出都不能污染下一任务。

    def test_cache_hits_remain_usable_after_budget_exhaustion(self):
        """已缓存证据不需要识别时间，零预算仍可使用相同语言的结果。"""
        with tempfile.TemporaryDirectory() as directory, \
             patch("protocol_pdf_diff.ocr_cache._cache_directory", return_value=Path(directory)), \
             patch("protocol_pdf_diff.ocr_cache._engine_identity", return_value="test"), \
             patch("protocol_pdf_diff.ocr_cache._call_tesseract", return_value="cached") as recognize:
            image = Image.new("RGB", (4, 4), "white")
            with ocr_scope("eng", None):
                cached_image_to_string(image, config="--psm 3", timeout=60)  # 建立同像素缓存。
            with ocr_scope("eng", 0):
                self.assertEqual("cached", cached_image_to_string(image, config="--psm 3", timeout=60))
            with ocr_scope("chi_sim", 0), self.assertRaisesRegex(RuntimeError, "累计 OCR"):
                cached_image_to_string(image, config="--psm 3", timeout=60)  # 不同语言不能复用旧缓存。
            self.assertEqual(1, recognize.call_count)

    def test_table_recognition_inherits_selected_language(self):
        """表格辅助 OCR 与整页 OCR 使用相同语言，并保留缓存键区分。"""
        with ocr_scope("chi_sim+eng"), patch("shutil.which", return_value="tesseract"), \
             patch("pytesseract.image_to_string", return_value="参数 3.3 V") as recognize:
            self.assertEqual("参数 3.3 V", _ocr_table_image(Image.new("RGB", (4, 4)))[0])
        self.assertEqual("chi_sim+eng", recognize.call_args.kwargs["lang"])

    def test_exhausted_budget_is_visible_on_public_scan_extraction(self):
        """预算耗尽不能生成可比较的空扫描文本或隐瞒未识别页。"""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "scan.pdf"
            source.write_bytes(b"%PDF-1.4\n%%EOF\n")  # 外部 PDF 边界使用现成模拟页。
            with ocr_scope("eng", 0), \
                 patch("pdfplumber.open", return_value=_OnePagePdf(_ScannedPage())), \
                 patch("shutil.which", return_value="tesseract"), \
                 patch("pytesseract.image_to_string") as recognize:
                result = extract_pdf_text(source, ocr_language="eng")
            recognize.assert_not_called()
            self.assertTrue(any("累计 OCR" in value and "第 1 页" in value for value in result.warnings))
            self.assertFalse(result.pages[0].ocr_used)

    def test_invalid_budget_rejected_before_work(self):
        """非法配置必须失败，不能让 NaN 或负超时绕过限制。"""
        for value in (-1, float("nan"), float("inf"), True, "5"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                DiffOptions(ocr_time_budget_seconds=value)
