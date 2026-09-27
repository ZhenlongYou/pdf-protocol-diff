"""OCR 缓存必须无损：命中文本等于实时识别，任何识别输入差异都必须是未命中。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from protocol_pdf_diff import ocr_cache


class _RecordingRunner:
    """Deterministic stand-in for the live Tesseract call."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, str, int, str | None]] = []

    def __call__(self, image, *, config, timeout, lang=None):  # noqa: ANN001, ANN002, ANN003
        self.calls.append((getattr(image, "size", None), config, timeout, lang))
        return f"text:{config}:{lang or 'default'}"


class OcrCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        # 每个测试使用独立缓存目录，并在模块层固定引擎身份，结果不依赖本机 tesseract。
        environment = mock.patch.dict(
            os.environ,
            {"PROTOCOL_PDF_DIFF_OCR_CACHE": self.directory.name},
        )
        environment.start()
        self.addCleanup(environment.stop)
        engine = mock.patch.object(
            ocr_cache, "_engine_identity", return_value="tesseract-test"
        )
        engine.start()
        self.addCleanup(engine.stop)
        ocr_cache.reset_statistics()

    def test_identical_request_hits_cache_and_skips_recognizer(self) -> None:
        runner = _RecordingRunner()
        image = Image.new("RGB", (8, 6), "white")
        with mock.patch.object(ocr_cache, "_call_tesseract", runner):
            first = ocr_cache.cached_image_to_string(image, config="--psm 6", timeout=60)
            second = ocr_cache.cached_image_to_string(image, config="--psm 6", timeout=60)
        self.assertEqual(first, second)
        self.assertEqual(1, len(runner.calls))
        self.assertEqual(
            {"calls": 2, "hits": 1, "misses": 1, "writes": 1},
            ocr_cache.statistics,
        )

    def test_config_language_and_pixels_are_part_of_the_key(self) -> None:
        runner = _RecordingRunner()
        image = Image.new("RGB", (8, 6), "white")
        other = Image.new("RGB", (8, 6), "black")
        with mock.patch.object(ocr_cache, "_call_tesseract", runner):
            ocr_cache.cached_image_to_string(image, config="--psm 6", timeout=60)
            ocr_cache.cached_image_to_string(image, config="--psm 3", timeout=60)
            ocr_cache.cached_image_to_string(image, config="--psm 6", timeout=60, lang="eng")
            ocr_cache.cached_image_to_string(other, config="--psm 6", timeout=60)
        self.assertEqual(4, len(runner.calls))

    def test_timeout_change_does_not_invalidate_successful_text(self) -> None:
        runner = _RecordingRunner()
        image = Image.new("RGB", (8, 6), "white")
        with mock.patch.object(ocr_cache, "_call_tesseract", runner):
            ocr_cache.cached_image_to_string(image, config="--psm 6", timeout=60)
            ocr_cache.cached_image_to_string(image, config="--psm 6", timeout=10)
        self.assertEqual(1, len(runner.calls))

    def test_disabled_cache_never_reads_or_writes(self) -> None:
        runner = _RecordingRunner()
        image = Image.new("RGB", (8, 6), "white")
        with mock.patch.dict(os.environ, {"PROTOCOL_PDF_DIFF_OCR_CACHE": "off"}), \
                mock.patch.object(ocr_cache, "_call_tesseract", runner):
            ocr_cache.cached_image_to_string(image, config="--psm 6", timeout=60)
            ocr_cache.cached_image_to_string(image, config="--psm 6", timeout=60)
        self.assertEqual(2, len(runner.calls))
        self.assertEqual(
            [],
            list(Path(self.directory.name).rglob("*.txt")),
        )

    def test_unhashable_image_falls_back_without_caching(self) -> None:
        runner = _RecordingRunner()
        with mock.patch.object(ocr_cache, "_call_tesseract", runner):
            first = ocr_cache.cached_image_to_string(object(), config="--psm 6", timeout=60)
            second = ocr_cache.cached_image_to_string(object(), config="--psm 6", timeout=60)
        self.assertEqual(first, second)
        self.assertEqual(2, len(runner.calls))


if __name__ == "__main__":
    unittest.main()
