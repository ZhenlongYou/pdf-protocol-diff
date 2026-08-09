"""Conservative page-image watchdog for semantic PDF comparison.

The watchdog never creates text, table, or formula facts.  It compares source
pixels only when both extracted page texts are identical, then emits a review
item if a material visual delta remains.  This catches changed diagrams,
vector marks, stamps, or formula artwork without letting pixel noise rewrite
the semantic diff.
"""

from __future__ import annotations

import base64
from difflib import SequenceMatcher
import io
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from .models import ExtractionResult, VisualReviewItem


VISUAL_RENDER_DPI = 96
VISUAL_PIXEL_DELTA_THRESHOLD = 28
VISUAL_MIN_CHANGED_PIXEL_RATIO = 0.0005
VISUAL_MIN_COMPONENT_AREA = 18


def detect_visual_review_items(
    old_extraction: ExtractionResult,
    new_extraction: ExtractionResult,
) -> tuple[list[VisualReviewItem], list[str]]:
    """Return unexplained visual page deltas and non-fatal diagnostic warnings.

    Pages with exactly equal whitespace-normalized extracted text are paired in
    monotonic order, so an inserted page does not shift every later comparison.
    Empty text layers use same-number pairing only when both selected windows
    have equal length. A different text page stays with the semantic pipeline;
    an empty or identical text layer can no longer hide a source-pixel change.
    """

    old_path = Path(old_extraction.pdf_path)
    new_path = Path(new_extraction.pdf_path)
    if not old_path.is_file() or not new_path.is_file():
        return [], []
    try:
        import pypdfium2
    except ModuleNotFoundError:
        return [], ["视觉漏检哨兵未运行：pypdfium2 未安装。"]

    eligible_page_pairs = _monotonic_exact_text_pairs(
        old_extraction,
        new_extraction,
    )
    if not eligible_page_pairs:
        return [], []

    old_document = None
    new_document = None
    items: list[VisualReviewItem] = []
    warnings: list[str] = []
    try:
        old_document = pypdfium2.PdfDocument(str(old_path))
        new_document = pypdfium2.PdfDocument(str(new_path))
        for old_page_number, new_page_number, alignment_method in eligible_page_pairs:
            try:
                old_image = _render_page(old_document, old_page_number)
                new_image = _render_page(new_document, new_page_number)
                item = _compare_page_images(
                    old_image,
                    new_image,
                    old_page_number=old_page_number,
                    new_page_number=new_page_number,
                    alignment_method=alignment_method,
                )
            except Exception as exc:
                warnings.append(
                    "视觉漏检哨兵未能核对"
                    f"旧第 {old_page_number} / 新第 {new_page_number} 页："
                    f"{type(exc).__name__}。"
                )
                continue
            if item is not None:
                items.append(item)
    except Exception as exc:
        return [], [f"视觉漏检哨兵未运行：{type(exc).__name__}。"]
    finally:
        if old_document is not None:
            old_document.close()
        if new_document is not None:
            new_document.close()
    return items, warnings


def _render_page(document: object, page_number: int) -> Image.Image:
    """Render one source page at a bounded review resolution and release PDFium objects."""

    page = document[page_number - 1]
    bitmap = None
    try:
        bitmap = page.render(
            scale=VISUAL_RENDER_DPI / 72.0,
            limit_image_cache=True,
        )
        return bitmap.to_pil().convert("RGB").copy()
    finally:
        if bitmap is not None:
            bitmap.close()
        page.close()


def _compare_page_images(
    old_image: Image.Image,
    new_image: Image.Image,
    *,
    old_page_number: int,
    new_page_number: int,
    alignment_method: str,
) -> VisualReviewItem | None:
    """Create a review item only for a connected, material pixel delta."""

    old_canvas, new_canvas = _same_size_canvases(old_image, new_image)
    old_array = np.asarray(old_canvas, dtype=np.int16)
    new_array = np.asarray(new_canvas, dtype=np.int16)
    maximum_channel_delta = np.max(np.abs(old_array - new_array), axis=2).astype(np.uint8)
    raw_mask = (maximum_channel_delta >= VISUAL_PIXEL_DELTA_THRESHOLD).astype(np.uint8)
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(
        raw_mask,
        connectivity=8,
    )
    material_mask = np.zeros_like(raw_mask)
    for label in range(1, component_count):
        if int(stats[label, cv2.CC_STAT_AREA]) >= VISUAL_MIN_COMPONENT_AREA:
            material_mask[labels == label] = 1
    changed_pixels = int(np.count_nonzero(material_mask))
    changed_ratio = changed_pixels / float(material_mask.size)
    if changed_ratio < VISUAL_MIN_CHANGED_PIXEL_RATIO:
        return None

    ys, xs = np.nonzero(material_mask)
    diff_bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    diff_preview = _diff_preview(old_canvas, material_mask, diff_bbox)
    return VisualReviewItem(
        old_page_number=old_page_number,
        new_page_number=new_page_number,
        change_type="modified",
        pixel_similarity=max(0.0, 1.0 - changed_ratio),
        changed_pixel_ratio=changed_ratio,
        reason="图形变化未被文字、表格或公式差异覆盖，请回到源 PDF 核对。",
        alignment_method=alignment_method,
        diff_bbox=diff_bbox,
        old_image_data_uri=_image_data_uri(old_canvas, image_format="JPEG"),
        new_image_data_uri=_image_data_uri(new_canvas, image_format="JPEG"),
        diff_image_data_uri=_image_data_uri(diff_preview, image_format="PNG"),
    )


def _same_size_canvases(
    old_image: Image.Image,
    new_image: Image.Image,
) -> tuple[Image.Image, Image.Image]:
    """Place both renderings on equal white canvases without geometric rescaling."""

    width = max(old_image.width, new_image.width)
    height = max(old_image.height, new_image.height)

    def place(image: Image.Image) -> Image.Image:
        canvas = Image.new("RGB", (width, height), "white")
        canvas.paste(image, (0, 0))
        return canvas

    return place(old_image), place(new_image)


def _diff_preview(
    old_image: Image.Image,
    mask: np.ndarray,
    diff_bbox: tuple[int, int, int, int],
) -> Image.Image:
    """Overlay material delta pixels and one bounding box on a dimmed old page."""

    base = np.asarray(old_image, dtype=np.uint8)
    dimmed = (base.astype(np.float32) * 0.45 + 255.0 * 0.55).astype(np.uint8)
    dimmed[mask.astype(bool)] = np.array([220, 38, 38], dtype=np.uint8)
    preview = Image.fromarray(dimmed, mode="RGB")
    ImageDraw.Draw(preview).rectangle(diff_bbox, outline=(185, 28, 28), width=3)
    return preview


def _image_data_uri(image: Image.Image, *, image_format: str) -> str:
    """Encode one bounded screenshot for a standalone offline HTML report."""

    buffer = io.BytesIO()
    save_options = {"optimize": True}
    if image_format == "JPEG":
        save_options.update({"quality": 72})
    image.save(buffer, format=image_format, **save_options)
    mime = "image/jpeg" if image_format == "JPEG" else "image/png"
    return f"data:{mime};base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _normalized_page_text(value: str) -> str:
    """Collapse only whitespace; all technical characters remain case-sensitive."""

    return " ".join(value.split())


def _monotonic_exact_text_pairs(
    old_extraction: ExtractionResult,
    new_extraction: ExtractionResult,
) -> list[tuple[int, int, str]]:
    """Pair equal-text pages monotonically so inserted pages do not shift evidence.

    ``SequenceMatcher`` keeps memory near-linear in page count. Empty pages use
    side-specific sentinels, so they cannot create accidental cross-page matches.
    """

    old_pages = list(old_extraction.pages)
    new_pages = list(new_extraction.pages)
    old_keys = [_normalized_page_text(page.text) for page in old_pages]
    new_keys = [_normalized_page_text(page.text) for page in new_pages]
    old_count = len(old_pages)
    new_count = len(new_pages)
    old_sequence = [
        ("text", key) if key else ("old-empty", index)
        for index, key in enumerate(old_keys)
    ]
    new_sequence = [
        ("text", key) if key else ("new-empty", index)
        for index, key in enumerate(new_keys)
    ]
    pairs: list[tuple[int, int, str]] = []
    matcher = SequenceMatcher(None, old_sequence, new_sequence, autojunk=False)
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            old_index = block.a + offset
            new_index = block.b + offset
            if not old_keys[old_index]:
                continue
            old_page_number = old_pages[old_index].page_number
            new_page_number = new_pages[new_index].page_number
            method = (
                "same-page-text"
                if old_page_number == new_page_number
                else "monotonic-exact-text"
            )
            pairs.append((old_page_number, new_page_number, method))
    # Empty text layers cannot participate in the LCS identity proof.  When both
    # selected windows have the same page count, preserve same-number pairing so
    # scanned/image-only revisions still receive a pixel-level warning.
    if old_count == new_count:
        matched_old = {old_page for old_page, _new_page, _method in pairs}
        matched_new = {new_page for _old_page, new_page, _method in pairs}
        new_by_page = {page.page_number: page for page in new_pages}
        for old_page, old_key in zip(old_pages, old_keys, strict=True):
            new_page = new_by_page.get(old_page.page_number)
            if (
                old_key
                or new_page is None
                or _normalized_page_text(new_page.text)
                or old_page.page_number in matched_old
                or new_page.page_number in matched_new
            ):
                continue
            pairs.append(
                (old_page.page_number, new_page.page_number, "same-page-empty-text")
            )
        pairs.sort(key=lambda pair: (pair[0], pair[1]))
    return pairs
