"""混合页的栅格文字与固定图示证据；不把 OCR 自动归入附近条款。"""

import math
import re
from collections import Counter
from .layout_blocks import page_bounds, make_ocr_document_block
from .models import Section
from .ocr_cache import cached_image_to_string
from .page_ocr import OCR_MAXIMUM_RENDER_PIXELS, OCR_PAGE_TIMEOUT_SECONDS, OCR_RENDER_RESOLUTION, _clean_ocr_text


def overlaps(a, b, padding=0):
    return a[0] - padding < b[2] and a[2] + padding > b[0] and a[1] - padding < b[3] and a[3] + padding > b[1]


def object_bbox(value):
    try:
        box = tuple(float(value[key]) for key in ("x0", "top", "x1", "bottom"))
    except (KeyError, TypeError, ValueError):
        return None
    return box if all(math.isfinite(x) for x in box) and box[2] > box[0] and box[3] > box[1] else None


def native_char_boxes(page):
    """必须能读取全部原始字符坐标；不使用过滤后的比较文字证明空白。"""
    chars = getattr(page, "chars", None)
    if not isinstance(chars, (list, tuple)):
        return None
    boxes = [object_bbox(c) for c in chars if str(c.get("text", "")).strip()]
    return None if any(b is None for b in boxes) else boxes


def raster_bboxes(page):
    bounds = page_bounds(page)
    if bounds is None:
        return ()
    boxes = [object_bbox(obj) for obj in getattr(page, "images", ()) or ()]
    return tuple(dict.fromkeys(b for b in boxes if b and b[0] >= bounds[0] and b[1] >= bounds[1]
                              and b[2] <= bounds[2] and b[3] <= bounds[3]))


def extract_image_region_text(page, page_number, excluded_bboxes=()):
    """只识别未被原生字形、表格或公式占用的小图；结果保留为独立复核块。"""
    char_boxes = native_char_boxes(page)
    if char_boxes is None:
        return (), []
    regions, warnings = [], []
    for box in raster_bboxes(page):
        width, height = box[2]-box[0], box[3]-box[1]
        if width < 60 or height < 20 or any(overlaps(box, other) for other in excluded_bboxes):
            continue
        label = f"第 {page_number} 页图像区域 {tuple(round(v, 1) for v in box)}"
        if any(overlaps(box, other) for other in char_boxes):
            warnings.append(label + "与原生文字重叠，局部 OCR 未运行；该图像仍需核对。")
            continue
        if width * height * (OCR_RENDER_RESOLUTION / 72)**2 > OCR_MAXIMUM_RENDER_PIXELS:
            warnings.append(label + "尺寸过大，未执行局部 OCR。")
            continue
        try:
            cropped = page.crop(box)
            pixels = cropped.to_image(resolution=OCR_RENDER_RESOLUTION).original.convert("RGB")
            text = _clean_ocr_text(cached_image_to_string(pixels, config="--psm 3", timeout=OCR_PAGE_TIMEOUT_SECONDS))
            if len(re.sub(r"\s", "", text)) < 12:
                warnings.append(label + "局部 OCR 未识别到足够文字；可能是无文字图示，仍需核对源图。")
                continue
            block, warning = make_ocr_document_block(cropped, page_number, text)
            if block:
                regions.append(block)
            if warning:
                warnings.append(label + warning)
            warnings.append(label + "已执行局部 OCR，文字独立列入复核，不自动归入正文条款。")
        except Exception as exc:
            warnings.append(label + f"局部 OCR 失败：{exc}")
    return tuple(regions), warnings


def image_region_sections(extraction):
    """以单独来源单元保留识别内容；后续变化只允许作为复核候选。"""
    sections = []
    for page in extraction.pages:
        for index, block in enumerate(page.image_text_regions, 1):
            title = f"图像区域文字（{index}，待核对）"
            sections.append(Section(f"image-region-{page.page_number}-{index}", title, title, 1,
                                    (title,), (), page.page_number, page.page_number, block.text,
                                    page_bodies=((page.page_number, block.text),), heading_provenance="region-ocr-review"))
    return sections


def captioned_graphic_regions(page, words, vectors, excluded_bboxes=()):
    """唯一图题和完整物理区域限定图示；图内标签保留，跨边界正文不借用。"""
    from .pdf_extract import _visual_word_lines, _words_to_visual_line
    chars = native_char_boxes(page)
    bounds = page_bounds(page)
    if chars is None or bounds is None or getattr(page, "rotation", 0) != 0:
        return ()
    captions = []
    for line in _visual_word_lines(words):
        text = _words_to_visual_line(line)
        if re.match(r"^(?:Figure|Fig\.|图)\s*\d+[\w.\-]*[.:：\s]", text):
            captions.append((text, min(float(w["top"]) for w in line), min(float(w["x0"]) for w in line), max(float(w["x1"]) for w in line)))
    candidates = []
    for kind, boxes in (("raster", raster_bboxes(page)), ("vector", vectors)):
        for box in boxes:
            if (box[2]-box[0] < 60 or box[3]-box[1] < 20 or box[0] < bounds[0] or box[1] < bounds[1]
                    or box[2] > bounds[2] or box[3] > bounds[3]
                    or any(overlaps(box, other, 2) for other in excluded_bboxes)
                    or any(overlaps(box, other, 2) and not
                           (box[0] <= other[0] <= other[2] <= box[2]
                            and box[1] <= other[1] <= other[3] <= box[3]) for other in chars)):
                continue
            labels = [label for label, top, left, right in captions
                      if 2 <= top-box[3] <= 40 and left < box[2] and right > box[0]]
            if len(labels) == 1:
                candidates.append((kind, labels[0], box))
    counts = Counter(label for _kind, label, _box in candidates)
    return tuple(c for c in candidates if counts[c[1]] == 1)
