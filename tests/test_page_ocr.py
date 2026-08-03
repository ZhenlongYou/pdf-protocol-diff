"""Public-path regressions for conservative full-page OCR fallback."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest import mock

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from protocol_pdf_diff.pdf_extract import extract_pdf_text
from protocol_pdf_diff.compare import compare_extractions, run_diff
from protocol_pdf_diff.models import DiffOptions, ExtractionResult, PageParserRoute, PageText


class _ScannedPage:
    """Small pdfplumber-compatible page containing one full-page raster image."""

    width = 600
    height = 800
    bbox = (0, 0, 600, 800)
    chars: list[object] = []
    lines: list[object] = []
    rects: list[object] = []
    images = [{"x0": 0, "x1": 600, "top": 0, "bottom": 800}]

    def __init__(self, native_text: str = "") -> None:
        self._native_text = native_text

    def filter(self, _predicate: object) -> "_ScannedPage":
        return self

    def extract_words(self, **_kwargs: object) -> list[dict[str, object]]:
        return []

    def extract_text(self, **_kwargs: object) -> str:
        return self._native_text

    def to_image(self, **_kwargs: object) -> SimpleNamespace:
        self.render_options = dict(_kwargs)
        return SimpleNamespace(original=Image.new("RGB", (1200, 1600), "white"))


class _OnePagePdf:
    def __init__(self, page: _ScannedPage) -> None:
        self.pages = [page]

    def close(self) -> None:
        pass


class PageOcrTests(unittest.TestCase):
    def test_scanned_page_uses_ocr_without_becoming_reliable_native_text(self) -> None:
        """A raster-only page should yield reviewable OCR text and explicit risk."""

        ocr_text = (
            "1 Scope\n"
            "The receiver shall preserve every timing requirement and voltage limit."
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "scan.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            with (
                mock.patch("pdfplumber.open", return_value=_OnePagePdf(_ScannedPage())),
                mock.patch(
                    "shutil.which",
                    return_value="/usr/local/bin/tesseract",
                ),
                mock.patch("pytesseract.image_to_string", return_value=ocr_text),
            ):
                extraction = extract_pdf_text(pdf_path)

        self.assertIn("1 Scope", extraction.pages[0].text)
        self.assertTrue(extraction.pages[0].image_dominant)
        self.assertTrue(extraction.pages[0].ocr_used)
        self.assertFalse(extraction.pages[0].layout_risk)
        self.assertEqual(PageParserRoute.OCR_FALLBACK, extraction.pages[0].parser_route)
        self.assertTrue(any("整页 OCR" in warning for warning in extraction.warnings))

    def test_ocr_page_is_an_explicit_quality_metric_and_never_auto_equal(self) -> None:
        """Even dense structured OCR text must remain visible as degraded evidence."""

        body = (
            "The receiver shall preserve every timing requirement, voltage limit, "
            "calibration condition, and compliance record. "
        ) * 12
        ocr_text = f"1 Scope\n{body}\n2 Requirements\n{body}"
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "scan.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            with (
                mock.patch("pdfplumber.open", return_value=_OnePagePdf(_ScannedPage())),
                mock.patch(
                    "shutil.which",
                    return_value="/usr/local/bin/tesseract",
                ),
                mock.patch("pytesseract.image_to_string", return_value=ocr_text),
            ):
                extraction = extract_pdf_text(pdf_path)

        result = compare_extractions(extraction, extraction, DiffOptions())

        self.assertEqual((1,), result.assessment.old_document.ocr_pages)
        self.assertEqual("degraded", result.assessment.state.value)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)
        self.assertTrue(any("OCR" in reason for reason in result.assessment.reasons))

    def test_scan_without_tesseract_stays_empty_and_actionable(self) -> None:
        """A missing OCR engine must not fabricate text or hide the scan diagnosis."""

        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "scan.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            with (
                mock.patch("pdfplumber.open", return_value=_OnePagePdf(_ScannedPage())),
                mock.patch("shutil.which", return_value=None),
                mock.patch("pytesseract.image_to_string") as image_to_string,
            ):
                extraction = extract_pdf_text(pdf_path)

        image_to_string.assert_not_called()
        self.assertEqual("", extraction.pages[0].text)
        self.assertTrue(extraction.pages[0].image_dominant)
        self.assertFalse(extraction.pages[0].ocr_used)
        self.assertFalse(extraction.pages[0].layout_risk)
        self.assertEqual(PageParserRoute.UNREADABLE_IMAGE, extraction.pages[0].parser_route)
        self.assertTrue(any("未发现 tesseract" in warning for warning in extraction.warnings))
        result = compare_extractions(extraction, extraction, DiffOptions())
        self.assertEqual("indeterminate", result.assessment.state.value)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_sparse_decorative_image_does_not_trigger_full_page_ocr(self) -> None:
        """One small logo must not turn a text-light native page into a scan."""

        page = _ScannedPage("1 Scope")
        page.images = [{"x0": 0, "x1": 100, "top": 0, "bottom": 100}]
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "logo-page.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            with (
                mock.patch("pdfplumber.open", return_value=_OnePagePdf(page)),
                mock.patch("shutil.which", return_value="/usr/local/bin/tesseract"),
                mock.patch("pytesseract.image_to_string") as image_to_string,
            ):
                extraction = extract_pdf_text(pdf_path)

        image_to_string.assert_not_called()
        self.assertFalse(extraction.pages[0].ocr_used)
        self.assertEqual("1 Scope", extraction.pages[0].text)

    def test_overlapping_images_contribute_only_their_union_coverage(self) -> None:
        """Repeated XObjects over one region must not fabricate scan-like coverage."""

        page = _ScannedPage("1 Scope")
        repeated_strip = {"x0": 0, "x1": 600, "top": 0, "bottom": 240}
        page.images = [repeated_strip, repeated_strip.copy()]
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "overlap.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            with (
                mock.patch("pdfplumber.open", return_value=_OnePagePdf(page)),
                mock.patch("shutil.which", return_value="/usr/local/bin/tesseract"),
                mock.patch("pytesseract.image_to_string") as image_to_string,
            ):
                extraction = extract_pdf_text(pdf_path)

        image_to_string.assert_not_called()
        self.assertFalse(extraction.pages[0].ocr_used)

    def test_oversized_page_is_not_rendered_at_unbounded_ocr_resolution(self) -> None:
        """A pathological page size must fail safely before allocating its bitmap."""

        page = _ScannedPage()
        page.width = 20_000
        page.height = 20_000
        page.images = [{"x0": 0, "x1": 20_000, "top": 0, "bottom": 20_000}]
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "oversized.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            with (
                mock.patch("pdfplumber.open", return_value=_OnePagePdf(page)),
                mock.patch("shutil.which", return_value="/usr/local/bin/tesseract"),
                mock.patch("pytesseract.image_to_string") as image_to_string,
            ):
                extraction = extract_pdf_text(pdf_path)

        image_to_string.assert_not_called()
        self.assertFalse(extraction.pages[0].ocr_used)
        self.assertTrue(any("渲染尺寸过大" in warning for warning in extraction.warnings))

    def test_empty_and_failed_ocr_results_remain_explicitly_unusable(self) -> None:
        """OCR timeouts and near-empty output must not be reported as recovered text."""

        outcomes = ["x", RuntimeError("OCR timed out")]
        expected_messages = ["未识别到足够文字", "OCR 失败"]
        for outcome, expected_message in zip(outcomes, expected_messages, strict=True):
            with self.subTest(outcome=outcome):
                with tempfile.TemporaryDirectory() as temp_dir:
                    pdf_path = Path(temp_dir) / "scan.pdf"
                    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
                    ocr_patch = (
                        mock.patch("pytesseract.image_to_string", side_effect=outcome)
                        if isinstance(outcome, Exception)
                        else mock.patch("pytesseract.image_to_string", return_value=outcome)
                    )
                    with (
                        mock.patch(
                            "pdfplumber.open",
                            return_value=_OnePagePdf(_ScannedPage()),
                        ),
                        mock.patch(
                            "shutil.which",
                            return_value="/usr/local/bin/tesseract",
                        ),
                        ocr_patch,
                    ):
                        extraction = extract_pdf_text(pdf_path)

                self.assertFalse(extraction.pages[0].ocr_used)
                self.assertEqual("", extraction.pages[0].text)
                self.assertTrue(
                    any(expected_message in warning for warning in extraction.warnings)
                )

    def test_generated_raster_pdf_reaches_full_page_ocr_path(self) -> None:
        """Image-only PDFs from a real parser page should trigger the OCR fallback."""

        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "generated-scan.pdf"
            Image.new("RGB", (1200, 1600), "white").save(
                pdf_path,
                format="PDF",
                resolution=150,
            )
            with (
                mock.patch(
                    "shutil.which",
                    return_value="/usr/local/bin/tesseract",
                ),
                mock.patch(
                    "pytesseract.image_to_string",
                    return_value="1 Scope\nA scanned requirement shall remain reviewable.",
                ),
            ):
                extraction = extract_pdf_text(pdf_path)

        self.assertTrue(extraction.pages[0].ocr_used)
        self.assertIn("scanned requirement", extraction.pages[0].text)

    def test_selectable_text_layer_skips_ocr_even_over_a_full_page_image(self) -> None:
        """A healthy text layer must remain authoritative and avoid the slow fallback."""

        native_line = (
            "The receiver shall preserve every native selectable requirement, "
            "numeric limit, unit, and normative condition. "
        ).strip()
        native_lines = ["1 Scope", *([native_line] * 18)]
        native_text = "\n".join(native_lines)
        searchable_page = _ScannedPage(native_text)
        searchable_page.chars = [
            {
                "text": character,
                "x0": float(40 + character_index * 4),
                "x1": float(43 + character_index * 4),
                "top": float(40 + line_index * 35),
                "bottom": float(50 + line_index * 35),
            }
            for line_index, line in enumerate(native_lines)
            for character_index, character in enumerate(line)
        ]  # 每个抽取字符都有同源坐标，且正文行跨越页面大部分高度。
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "searchable-scan.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            with (
                mock.patch(
                    "pdfplumber.open",
                    return_value=_OnePagePdf(searchable_page),
                ),
                mock.patch(
                    "shutil.which",
                    return_value="/usr/local/bin/tesseract",
                ),
                mock.patch("pytesseract.image_to_string") as image_to_string,
            ):
                extraction = extract_pdf_text(pdf_path)

        image_to_string.assert_not_called()
        self.assertEqual(native_text, extraction.pages[0].text)
        self.assertTrue(extraction.pages[0].image_dominant)
        self.assertFalse(extraction.pages[0].ocr_used)
        self.assertFalse(extraction.pages[0].layout_risk)
        self.assertEqual(PageParserRoute.IMAGE_TEXT_LAYER, extraction.pages[0].parser_route)
        result = compare_extractions(extraction, extraction, DiffOptions())
        self.assertEqual("degraded", result.assessment.state.value)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)
        self.assertTrue(any("栅格" in warning for warning in extraction.warnings))

    def test_sparse_header_and_footer_text_layer_does_not_suppress_full_page_ocr(self) -> None:
        """Long margin text over a scanned page cannot hide a real numeric revision."""

        native_header = (
            "Protocol comparison copy generated for controlled document review. " * 3
        ).strip()
        native_footer = "Page 1"
        native_text_layer = f"{native_header}\n{native_footer}"

        def scanned_page() -> _ScannedPage:
            page = _ScannedPage(native_text_layer)
            header_chars = [
                {
                    "text": character,
                    "x0": float(index * 3),
                    "x1": float(index * 3 + 2),
                    "top": 95.0,
                    "bottom": 105.0,
                }
                for index, character in enumerate(native_header)
            ]
            footer_chars = [
                {
                    "text": character,
                    "x0": float(250 + index * 5),
                    "x1": float(254 + index * 5),
                    "top": 695.0,
                    "bottom": 705.0,
                }
                for index, character in enumerate(native_footer)
            ]
            page.chars = [*header_chars, *footer_chars]
            return page

        ocr_outputs = (
            "1 Scope\nThe uncorrelated jitter maximum shall be 0.121 UI.",
            "1 Scope\nThe uncorrelated jitter maximum shall be 0.118 UI.",
        )
        extractions: list[ExtractionResult] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            for index, ocr_text in enumerate(ocr_outputs):
                pdf_path = Path(temp_dir) / f"scan-{index}.pdf"
                pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
                with (
                    mock.patch(
                        "pdfplumber.open",
                        return_value=_OnePagePdf(scanned_page()),
                    ),
                    mock.patch(
                        "shutil.which",
                        return_value="/usr/local/bin/tesseract",
                    ),
                    mock.patch(
                        "pytesseract.image_to_string",
                        return_value=ocr_text,
                    ),
                ):
                    extractions.append(extract_pdf_text(pdf_path))

        result = compare_extractions(extractions[0], extractions[1], DiffOptions())
        changed_text = "\n".join(
            f"{pair.old}\n{pair.new}"
            for change in result.changes
            for pair in change.replaced_snippets
        )

        self.assertTrue(all(page.ocr_used for extraction in extractions for page in extraction.pages))
        self.assertIn("0.121 UI", changed_text)
        self.assertIn("0.118 UI", changed_text)
        self.assertEqual("degraded", result.assessment.state.value)

    def test_image_dominant_page_without_native_coordinates_uses_ocr(self) -> None:
        """Missing coordinate evidence cannot certify a long hidden text layer as complete."""

        native_text = "Unverified hidden text layer without glyph coordinates. " * 4
        page = _ScannedPage(native_text)
        page.chars = []
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "coordinate-missing-scan.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            with (
                mock.patch("pdfplumber.open", return_value=_OnePagePdf(page)),
                mock.patch("shutil.which", return_value="/usr/local/bin/tesseract"),
                mock.patch(
                    "pytesseract.image_to_string",
                    return_value=(
                        "1 Scope\nThe receiver jitter maximum shall remain 0.118 UI."
                    ),
                ) as image_to_string,
            ):
                extraction = extract_pdf_text(pdf_path)

        image_to_string.assert_called_once()
        self.assertTrue(extraction.pages[0].ocr_used)
        self.assertIn("0.118 UI", extraction.pages[0].text)
        self.assertEqual(PageParserRoute.OCR_FALLBACK, extraction.pages[0].parser_route)

    def test_requested_ocr_language_uses_document_quality_rendering_and_timeout(self) -> None:
        """The public extractor should pass reproducible OCR quality controls."""

        page = _ScannedPage()
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "multilingual-scan.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            with (
                mock.patch("pdfplumber.open", return_value=_OnePagePdf(page)),
                mock.patch("shutil.which", return_value="/usr/local/bin/tesseract"),
                mock.patch(
                    "pytesseract.image_to_string",
                    return_value="1 范围\n接收机 shall preserve every voltage limit.",
                ) as image_to_string,
            ):
                extraction = extract_pdf_text(
                    pdf_path,
                    ocr_language="chi_sim+eng",
                )

        self.assertTrue(extraction.pages[0].ocr_used)
        self.assertEqual(300, page.render_options["resolution"])
        self.assertEqual("chi_sim+eng", image_to_string.call_args.kwargs["lang"])
        self.assertEqual(60, image_to_string.call_args.kwargs["timeout"])

    def test_invalid_ocr_language_expression_is_rejected_before_opening_pdf(self) -> None:
        """Language configuration must not be forwarded as arbitrary Tesseract syntax."""

        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "scan.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            with mock.patch("pdfplumber.open") as open_pdf:
                with self.assertRaisesRegex(ValueError, "Tesseract 语言代码"):
                    extract_pdf_text(pdf_path, ocr_language="eng;--psm 6")

        open_pdf.assert_not_called()

    def test_comparison_options_forward_ocr_language_to_both_documents(self) -> None:
        """One explicit language choice must apply symmetrically to old and new PDFs."""

        body = "The receiver shall preserve every timing and voltage requirement. " * 10
        extractions = [
            ExtractionResult(
                pdf_path=Path(name),
                pages=[PageText(page_number=1, text=f"1 Scope\n{body}")],
                total_pages=1,
                selected_start_page=1,
                selected_end_page=1,
            )
            for name in ("old.pdf", "new.pdf")
        ]
        with mock.patch(
            "protocol_pdf_diff.compare.extract_pdf_text",
            side_effect=extractions,
        ) as extract:
            result = run_diff(
                "old.pdf",
                "new.pdf",
                DiffOptions(ocr_language="  chi_sim+eng  "),
            )

        self.assertEqual("chi_sim+eng", extract.call_args_list[0].kwargs["ocr_language"])
        self.assertEqual("chi_sim+eng", extract.call_args_list[1].kwargs["ocr_language"])
        self.assertEqual(
            "chi_sim+eng",
            result.provenance.effective_thresholds.ocr_language,
        )
        self.assertEqual(300, result.provenance.effective_thresholds.ocr_render_resolution)
        self.assertEqual(60, result.provenance.effective_thresholds.ocr_page_timeout_seconds)
        self.assertEqual(
            50_000_000,
            result.provenance.effective_thresholds.ocr_maximum_render_pixels,
        )
        self.assertEqual(
            8,
            result.provenance.effective_thresholds.ocr_native_text_vertical_band_count,
        )
        self.assertEqual(
            0.5,
            result.provenance.effective_thresholds.ocr_minimum_native_text_vertical_band_coverage,
        )


if __name__ == "__main__":
    unittest.main()
