"""Conservative full-page OCR fallback for scan-like PDF pages."""

from __future__ import annotations

import re
import shutil

from .models import classify_page_parser_route  # 重新导出模型层纯路由器，避免 PageText 不变量产生循环依赖。
from .text_utils import normalize_line


OCR_NATIVE_CHARACTER_LIMIT = 80
OCR_MINIMUM_IMAGE_COVERAGE = 0.50
OCR_NATIVE_TEXT_VERTICAL_BAND_COUNT = 8
OCR_MINIMUM_NATIVE_TEXT_VERTICAL_BAND_COVERAGE = 0.50
OCR_MINIMUM_RESULT_CHARACTERS = 12
# Tesseract recommends roughly 300 DPI input and appropriate page segmentation;
# pytesseract exposes a timeout for the recognition stage of one page.
# Sources: https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html
#          https://github.com/madmaze/pytesseract
OCR_RENDER_RESOLUTION = 300
OCR_PAGE_TIMEOUT_SECONDS = 60
OCR_MAXIMUM_RENDER_PIXELS = 50_000_000
_LANGUAGE_PATTERN = re.compile(r"[A-Za-z0-9_-]+(?:\+[A-Za-z0-9_-]+)*")


def normalize_ocr_language(value: str | None) -> str | None:
    """Validate an optional Tesseract language expression such as ``chi_sim+eng``."""

    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if not _LANGUAGE_PATTERN.fullmatch(normalized):
        raise ValueError(
            "OCR 语言必须使用 Tesseract 语言代码，多个语言用 + 连接，"
            "例如 chi_sim+eng。"
        )
    return normalized


def extract_scan_page_text(
    page: object,
    native_text: str,
    pdf_name: str,
    page_number: int,
    *,
    ocr_language: str | None = None,
) -> tuple[str, list[str], bool, bool]:
    """保持旧四元组公开合同，返回合并文字、告警、OCR 和图像事实。"""

    # 原始 OCR 流只供提取层建立 provenance block，外部旧调用者无需感知新增证据。
    text, warnings, ocr_used, image_dominant, _raw_ocr_text = _extract_scan_page_text_with_evidence(
        page,
        native_text,
        pdf_name,
        page_number,
        ocr_language=ocr_language,
    )
    # 保持历史 API 可四值解包，避免下游现有脚本因证据增强而中断。
    return text, warnings, ocr_used, image_dominant


def _extract_scan_page_text_with_evidence(
    page: object,
    native_text: str,
    pdf_name: str,
    page_number: int,
    *,
    ocr_language: str | None = None,
) -> tuple[str, list[str], bool, bool, str | None]:
    """返回内部 OCR 证据五元组，其中原始 OCR 文字绝不混入原生来源。"""

    native_characters = len(re.sub(r"\s+", "", native_text))
    if not _page_has_substantial_raster_image(page):
        return native_text, [], False, False, None
    native_vertical_coverage = _native_text_vertical_band_coverage(page)
    if (
        native_characters >= OCR_NATIVE_CHARACTER_LIMIT
        and native_vertical_coverage is not None
        and native_vertical_coverage >= OCR_MINIMUM_NATIVE_TEXT_VERTICAL_BAND_COVERAGE
    ):
        return native_text, [
            f"{pdf_name}: 第 {page_number} 页含大面积栅格图和可选文字层；"
            "已保留原生文字并跳过重复 OCR，但该文字层可能不完整，需人工复核。"
        ], False, True, None
    estimated_pixels = _estimated_render_pixels(page)
    if estimated_pixels > OCR_MAXIMUM_RENDER_PIXELS:
        return native_text, [
            f"{pdf_name}: 第 {page_number} 页疑似扫描页，但按 {OCR_RENDER_RESOLUTION} DPI "
            f"渲染尺寸过大（约 {estimated_pixels:,} 像素），未执行整页 OCR。"
        ], False, True, None
    if shutil.which("tesseract") is None:
        return native_text, [
            f"{pdf_name}: 第 {page_number} 页疑似扫描页，但未发现 tesseract，"
            "未执行整页 OCR。"
        ], False, True, None
    try:
        import pytesseract
    except ModuleNotFoundError:
        return native_text, [
            f"{pdf_name}: 第 {page_number} 页疑似扫描页，但 pytesseract 未安装，"
            "未执行整页 OCR。"
        ], False, True, None
    try:
        image = page.to_image(resolution=OCR_RENDER_RESOLUTION).original.convert("RGB")
        ocr_arguments: dict[str, object] = {
            "config": "--psm 3",
            "timeout": OCR_PAGE_TIMEOUT_SECONDS,
        }
        if ocr_language is not None:
            ocr_arguments["lang"] = ocr_language
        raw_ocr_text = pytesseract.image_to_string(image, **ocr_arguments)
    except Exception as exc:
        return native_text, [
            f"{pdf_name}: 第 {page_number} 页整页 OCR 失败: {exc}"
        ], False, True, None
    ocr_text = _clean_ocr_text(raw_ocr_text)
    ocr_characters = len(re.sub(r"\s+", "", ocr_text))
    if ocr_characters < OCR_MINIMUM_RESULT_CHARACTERS:
        return native_text, [
            f"{pdf_name}: 第 {page_number} 页整页 OCR 未识别到足够文字。"
        ], False, True, None
    merged_text = _merge_native_and_ocr_lines(native_text, ocr_text)
    return merged_text, [
        f"{pdf_name}: 第 {page_number} 页已执行整页 OCR；OCR 文字参与差异定位，"
        "但该页保持需人工复核，不能据此自动判定一致。"
    ], True, True, ocr_text


def _page_has_substantial_raster_image(page: object) -> bool:
    """Return True only when raster images cover a scan-like share of the page."""

    width = float(getattr(page, "width", 0) or 0)
    height = float(getattr(page, "height", 0) or 0)
    page_area = width * height
    if page_area <= 0:
        return False
    rectangles: list[tuple[float, float, float, float]] = []
    for image in getattr(page, "images", []) or []:
        if not isinstance(image, dict):
            continue
        try:
            image_left = max(0.0, float(image["x0"]))
            image_right = min(width, float(image["x1"]))
            image_top = max(0.0, float(image["top"]))
            image_bottom = min(height, float(image["bottom"]))
        except (KeyError, TypeError, ValueError):
            continue
        if image_right > image_left and image_bottom > image_top:
            rectangles.append((image_left, image_right, image_top, image_bottom))
    covered_area = _rectangle_union_area(rectangles)
    return covered_area / page_area >= OCR_MINIMUM_IMAGE_COVERAGE


def _native_text_vertical_band_coverage(page: object) -> float | None:
    """Return the share of page-height bands containing native glyphs."""

    height = float(getattr(page, "height", 0) or 0)
    if height <= 0:
        return None
    occupied_bands: set[int] = set()
    for character in getattr(page, "chars", []) or []:
        if not isinstance(character, dict) or not str(character.get("text", "")).strip():
            continue
        try:
            top = max(0.0, float(character["top"]))
            bottom = min(height, float(character["bottom"]))
        except (KeyError, TypeError, ValueError):
            continue
        if bottom > top:
            center = (top + bottom) / 2
            occupied_bands.add(
                min(
                    OCR_NATIVE_TEXT_VERTICAL_BAND_COUNT - 1,
                    int(center * OCR_NATIVE_TEXT_VERTICAL_BAND_COUNT / height),
                )
            )
    if not occupied_bands:
        return None  # 无坐标不能证明隐藏文字层完整；大面积栅格页继续走可复核 OCR。
    return len(occupied_bands) / OCR_NATIVE_TEXT_VERTICAL_BAND_COUNT  # 页眉加页脚只占两段，不能伪造整页文字覆盖。


def _estimated_render_pixels(page: object) -> int:
    """Estimate the bitmap allocation before rendering one PDF page."""

    width_points = max(0.0, float(getattr(page, "width", 0) or 0))
    height_points = max(0.0, float(getattr(page, "height", 0) or 0))
    pixels_per_point = OCR_RENDER_RESOLUTION / 72
    return int(width_points * pixels_per_point * height_points * pixels_per_point)


def _rectangle_union_area(
    rectangles: list[tuple[float, float, float, float]],
) -> float:
    """Return exact union area so overlapping PDF image objects count once."""

    x_edges = sorted({edge for left, right, _, _ in rectangles for edge in (left, right)})
    area = 0.0
    for slab_left, slab_right in zip(x_edges, x_edges[1:]):
        intervals = sorted(
            (top, bottom)
            for left, right, top, bottom in rectangles
            if left <= slab_left and right >= slab_right
        )
        if not intervals:
            continue
        covered_height = 0.0
        merged_top, merged_bottom = intervals[0]
        for top, bottom in intervals[1:]:
            if top > merged_bottom:
                covered_height += merged_bottom - merged_top
                merged_top, merged_bottom = top, bottom
            else:
                merged_bottom = max(merged_bottom, bottom)
        covered_height += merged_bottom - merged_top
        area += (slab_right - slab_left) * covered_height
    return area


def _clean_ocr_text(text: str) -> str:
    """Normalize OCR lines without guessing at their semantic content."""

    return "\n".join(
        line
        for raw_line in text.splitlines()
        if (line := normalize_line(raw_line))
    )


def _merge_native_and_ocr_lines(native_text: str, ocr_text: str) -> str:
    """Preserve native lines and append only distinct OCR observations."""

    merged: list[str] = []
    seen: set[str] = set()
    for source in (native_text, ocr_text):
        for raw_line in source.splitlines():
            line = normalize_line(raw_line)
            key = line.casefold()
            if not line or key in seen:
                continue
            merged.append(line)
            seen.add(key)
    return "\n".join(merged)
