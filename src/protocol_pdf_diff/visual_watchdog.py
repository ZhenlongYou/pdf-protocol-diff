"""Conservative page-image watchdog for semantic PDF comparison.

The watchdog never creates text, table, or formula facts. It compares pixels
only for page pairs whose extracted text gives a unique exact or reader-equivalent identity.
Every rendering is made from a new immutable snapshot whose digest must equal
the digest of the bytes parsed by the extraction layer.
"""

from __future__ import annotations

import base64
import hashlib
import io
import tempfile
from collections import Counter
from pathlib import Path
from typing import BinaryIO

import cv2
import numpy as np
from PIL import Image, ImageDraw

from .models import (
    DiffResult,
    DocumentBlockKind,
    ExtractionResult,
    PageText,
    VisualReviewItem,
    VisualWatchdogAudit,
)

VISUAL_RENDER_DPI = 96
VISUAL_PIXEL_DELTA_THRESHOLD = 28
# A material connected component is sufficient evidence. The whole-page ratio
# remains an audit metric, not a second gate that can erase one small symbol.
VISUAL_MIN_CHANGED_PIXEL_RATIO = 0.0
VISUAL_MIN_COMPONENT_AREA = 18
VISUAL_PREVIEW_PADDING = 48
# Pixel subtraction is only meaningful while the extracted text blocks retain
# the same geometry.  A larger shift means that identical glyphs and diagrams
# can paint different pixels across most of the page, which must be reported as
# incomplete coverage instead of fabricated visual-change evidence.
VISUAL_LAYOUT_COORDINATE_TOLERANCE_POINTS = 4.0


def detect_visual_review_items(
    old_extraction: ExtractionResult,
    new_extraction: ExtractionResult,
    *,
    semantic_result: DiffResult | None = None,
) -> tuple[list[VisualReviewItem], list[str], VisualWatchdogAudit]:
    """Return unexplained visual deltas, diagnostics, and coverage audit facts."""

    eligible_page_pairs, ambiguous_page_count = _provable_exact_text_pairs(
        old_extraction,
        new_extraction,
    )
    warnings: list[str] = []
    if ambiguous_page_count:
        warnings.append(
            "视觉漏检哨兵跳过了 "
            f"{ambiguous_page_count} 个无法唯一配对的重复或空文字页。"
        )

    if not eligible_page_pairs:
        return [], warnings, VisualWatchdogAudit(
            enabled=True,
            attempted=bool(ambiguous_page_count),
            backend_available=None,
            eligible_page_pair_count=0,
            checked_page_pair_count=0,
            failed_page_pair_count=0,
            ambiguous_page_count=ambiguous_page_count,
            excluded_region_count=0,
            complete=ambiguous_page_count == 0,
            source_hashes_match=None,
        )

    try:
        import pypdfium2
    except ModuleNotFoundError:
        warnings.append("视觉漏检哨兵未运行：pypdfium2 未安装。")
        return [], warnings, VisualWatchdogAudit(
            enabled=True,
            attempted=True,
            backend_available=False,
            eligible_page_pair_count=len(eligible_page_pairs),
            checked_page_pair_count=0,
            failed_page_pair_count=len(eligible_page_pairs),
            ambiguous_page_count=ambiguous_page_count,
            excluded_region_count=0,
            complete=False,
            source_hashes_match=None,
        )

    old_snapshot: BinaryIO | None = None
    new_snapshot: BinaryIO | None = None
    old_document = None
    new_document = None
    old_visual_sha: str | None = None
    new_visual_sha: str | None = None
    items: list[VisualReviewItem] = []
    checked_page_pair_count = 0
    failed_page_pair_count = 0
    excluded_region_count = 0
    try:
        old_snapshot, old_visual_sha = _snapshot_pdf(Path(old_extraction.pdf_path))
        new_snapshot, new_visual_sha = _snapshot_pdf(Path(new_extraction.pdf_path))
        hashes_match = bool(
            old_extraction.source_sha256
            and new_extraction.source_sha256
            and old_visual_sha == old_extraction.source_sha256
            and new_visual_sha == new_extraction.source_sha256
        )
        if not hashes_match:
            warnings.append(
                "视觉漏检哨兵未使用像素证据：渲染快照与文字抽取快照的 SHA-256 不一致。"
            )
            return [], warnings, VisualWatchdogAudit(
                enabled=True,
                attempted=True,
                backend_available=True,
                eligible_page_pair_count=len(eligible_page_pairs),
                checked_page_pair_count=0,
                failed_page_pair_count=len(eligible_page_pairs),
                ambiguous_page_count=ambiguous_page_count,
                excluded_region_count=0,
                complete=False,
                source_hashes_match=False,
                old_visual_source_sha256=old_visual_sha,
                new_visual_source_sha256=new_visual_sha,
            )

        old_snapshot.seek(0)
        new_snapshot.seek(0)
        old_document = pypdfium2.PdfDocument(old_snapshot, autoclose=False)
        new_document = pypdfium2.PdfDocument(new_snapshot, autoclose=False)
        old_pages = {page.page_number: page for page in old_extraction.pages}
        new_pages = {page.page_number: page for page in new_extraction.pages}
        old_evidence = _semantic_evidence_bboxes(semantic_result, side="old")
        new_evidence = _semantic_evidence_bboxes(semantic_result, side="new")

        for old_page_number, new_page_number, alignment_method in eligible_page_pairs:
            old_page = old_pages[old_page_number]
            new_page = new_pages[new_page_number]
            if not _text_layout_comparable(old_page, new_page):
                failed_page_pair_count += 1
                warnings.append(
                    "视觉漏检哨兵未生成像素差异卡："
                    f"旧第 {old_page_number} / 新第 {new_page_number} 页的文字版式"
                    "发生整体移动、换行或重排，当前像素坐标不可直接比较。"
                )
                continue
            (
                old_locator_boxes,
                new_locator_boxes,
                locator_coverage_complete,
            ) = _reader_locator_change_bboxes(
                old_page,
                new_page,
            )
            if alignment_method == "reader-equivalent-text" and not locator_coverage_complete:
                failed_page_pair_count += 1
                warnings.append(
                    "视觉漏检哨兵未能用逐行坐标完整屏蔽"
                    f"旧第 {old_page_number} / 新第 {new_page_number} 页的纯引用编号变化。"
                )
                continue
            old_excluded = (
                *old_page.visual_noise_bboxes,
                *old_evidence.get(old_page_number, ()),
                *old_locator_boxes,
            )
            new_excluded = (
                *new_page.visual_noise_bboxes,
                *new_evidence.get(new_page_number, ()),
                *new_locator_boxes,
            )
            excluded_region_count += len(old_excluded) + len(new_excluded)
            try:
                old_image = _render_page(old_document, old_page_number)
                new_image = _render_page(new_document, new_page_number)
                item = _compare_page_images(
                    old_image,
                    new_image,
                    old_page_number=old_page_number,
                    new_page_number=new_page_number,
                    alignment_method=alignment_method,
                    old_page_bbox=old_page.page_bbox,
                    new_page_bbox=new_page.page_bbox,
                    old_excluded_bboxes=old_excluded,
                    new_excluded_bboxes=new_excluded,
                )
                checked_page_pair_count += 1
            except Exception as exc:
                failed_page_pair_count += 1
                warnings.append(
                    "视觉漏检哨兵未能核对"
                    f"旧第 {old_page_number} / 新第 {new_page_number} 页："
                    f"{type(exc).__name__}。"
                )
                continue
            if item is not None:
                items.append(item)
    except Exception as exc:
        failed_page_pair_count = max(
            failed_page_pair_count,
            len(eligible_page_pairs) - checked_page_pair_count,
        )
        warnings.append(f"视觉漏检哨兵未运行：{type(exc).__name__}。")
    finally:
        if old_document is not None:
            old_document.close()
        if new_document is not None:
            new_document.close()
        if old_snapshot is not None:
            old_snapshot.close()
        if new_snapshot is not None:
            new_snapshot.close()

    complete = (
        ambiguous_page_count == 0
        and failed_page_pair_count == 0
        and checked_page_pair_count == len(eligible_page_pairs)
    )
    return items, warnings, VisualWatchdogAudit(
        enabled=True,
        attempted=True,
        backend_available=True,
        eligible_page_pair_count=len(eligible_page_pairs),
        checked_page_pair_count=checked_page_pair_count,
        failed_page_pair_count=failed_page_pair_count,
        ambiguous_page_count=ambiguous_page_count,
        excluded_region_count=excluded_region_count,
        complete=complete,
        source_hashes_match=(
            bool(
                old_extraction.source_sha256
                and new_extraction.source_sha256
                and old_visual_sha == old_extraction.source_sha256
                and new_visual_sha == new_extraction.source_sha256
            )
            if old_visual_sha and new_visual_sha
            else None
        ),
        old_visual_source_sha256=old_visual_sha,
        new_visual_source_sha256=new_visual_sha,
    )


def _snapshot_pdf(path: Path) -> tuple[BinaryIO, str]:
    """Copy one path once and bind every rendered byte to its SHA-256 digest."""

    snapshot = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024, mode="w+b")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                snapshot.write(chunk)
                digest.update(chunk)
        snapshot.seek(0)
        return snapshot, digest.hexdigest()
    except Exception:
        snapshot.close()
        raise


def _semantic_evidence_bboxes(
    result: DiffResult | None,
    *,
    side: str,
) -> dict[int, tuple[tuple[float, float, float, float], ...]]:
    """Collect regions already covered by structured table/formula evidence."""

    if result is None:
        return {}
    # Import lazily because the report module itself imports quality constants
    # from this module. Only material table cards and formula findings are
    # covered; unchanged structured regions remain eligible for the watchdog to
    # catch a glyph that their semantic extractor missed.
    from .reporting import _build_table_changes

    table_changes = _build_table_changes(result)
    grouped: dict[int, list[tuple[float, float, float, float]]] = {}
    table_visuals = tuple(
        visual
        for change in table_changes
        for visual in (change.old_tables if side == "old" else change.new_tables)
    )
    formula_visuals = tuple(
        formula
        for change in result.formula_changes
        for formula in (
            (change.old_formula,)
            if side == "old"
            else (change.new_formula,)
        )
        if formula is not None
    )
    for visual in (*table_visuals, *formula_visuals):
        grouped.setdefault(visual.page_number, []).append(visual.bbox)
    return {page: tuple(boxes) for page, boxes in grouped.items()}


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
    old_page_bbox: tuple[float, float, float, float] | None = None,
    new_page_bbox: tuple[float, float, float, float] | None = None,
    old_excluded_bboxes: tuple[tuple[float, float, float, float], ...] = (),
    new_excluded_bboxes: tuple[tuple[float, float, float, float], ...] = (),
) -> VisualReviewItem | None:
    """Create a review item when any connected material pixel delta remains."""

    old_canvas, new_canvas = _same_size_canvases(old_image, new_image)
    old_array = np.asarray(old_canvas, dtype=np.int16)
    new_array = np.asarray(new_canvas, dtype=np.int16)
    maximum_channel_delta = np.max(np.abs(old_array - new_array), axis=2).astype(np.uint8)
    raw_mask = (maximum_channel_delta >= VISUAL_PIXEL_DELTA_THRESHOLD).astype(np.uint8)
    _clear_excluded_regions(
        raw_mask,
        old_excluded_bboxes,
        page_bbox=old_page_bbox,
        source_size=old_image.size,
    )
    _clear_excluded_regions(
        raw_mask,
        new_excluded_bboxes,
        page_bbox=new_page_bbox,
        source_size=new_image.size,
    )
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(
        raw_mask,
        connectivity=8,
    )
    material_mask = np.zeros_like(raw_mask)
    for label in range(1, component_count):
        if int(stats[label, cv2.CC_STAT_AREA]) >= VISUAL_MIN_COMPONENT_AREA:
            material_mask[labels == label] = 1
    changed_pixels = int(np.count_nonzero(material_mask))
    if changed_pixels == 0:
        return None
    changed_ratio = changed_pixels / float(material_mask.size)

    ys, xs = np.nonzero(material_mask)
    diff_bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    preview_bbox = _expanded_bbox(diff_bbox, old_canvas.size)
    diff_preview = _diff_preview(old_canvas, material_mask, diff_bbox).crop(preview_bbox)
    return VisualReviewItem(
        old_page_number=old_page_number,
        new_page_number=new_page_number,
        change_type="modified",
        pixel_similarity=max(0.0, 1.0 - changed_ratio),
        changed_pixel_ratio=changed_ratio,
        reason="图形变化未被文字、表格或公式差异覆盖，请回到源 PDF 核对。",
        alignment_method=alignment_method,
        diff_bbox=diff_bbox,
        old_image_data_uri=_image_data_uri(old_canvas.crop(preview_bbox), image_format="JPEG"),
        new_image_data_uri=_image_data_uri(new_canvas.crop(preview_bbox), image_format="JPEG"),
        diff_image_data_uri=_image_data_uri(diff_preview, image_format="PNG"),
    )


def _clear_excluded_regions(
    mask: np.ndarray,
    bboxes: tuple[tuple[float, float, float, float], ...],
    *,
    page_bbox: tuple[float, float, float, float] | None,
    source_size: tuple[int, int],
) -> None:
    """Clear only source-coordinate regions proven by extraction or structure."""

    if not bboxes or page_bbox is None:
        return
    page_x0, page_top, page_x1, page_bottom = page_bbox
    page_width = page_x1 - page_x0
    page_height = page_bottom - page_top
    if page_width <= 0 or page_height <= 0:
        return
    image_width, image_height = source_size
    for x0, top, x1, bottom in bboxes:
        left = max(0, min(image_width, int(np.floor((x0 - page_x0) / page_width * image_width))))
        right = max(0, min(image_width, int(np.ceil((x1 - page_x0) / page_width * image_width))))
        upper = max(0, min(image_height, int(np.floor((top - page_top) / page_height * image_height))))
        lower = max(0, min(image_height, int(np.ceil((bottom - page_top) / page_height * image_height))))
        if right > left and lower > upper:
            mask[upper:lower, left:right] = 0


def _expanded_bbox(
    bbox: tuple[int, int, int, int],
    image_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    """Add bounded local context so standalone evidence stays compact and readable."""

    left, top, right, bottom = bbox
    width, height = image_size
    return (
        max(0, left - VISUAL_PREVIEW_PADDING),
        max(0, top - VISUAL_PREVIEW_PADDING),
        min(width, right + VISUAL_PREVIEW_PADDING),
        min(height, bottom + VISUAL_PREVIEW_PADDING),
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
    """Encode one local crop for a standalone offline HTML report."""

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


def _reader_page_identity(value: str) -> str:
    """Use the same fail-closed locator neutralization as reader reports."""

    from .reporting import _reader_neutralize_locator_numbers

    return _reader_neutralize_locator_numbers(value)


def _reader_locator_change_bboxes(
    old_page: PageText,
    new_page: PageText,
) -> tuple[
    tuple[tuple[float, float, float, float], ...],
    tuple[tuple[float, float, float, float], ...],
    bool,
]:
    """Mask tight locator-bearing lines on a page proved reader-equivalent as a whole."""

    from .reporting import (
        _reader_neutralize_locator_numbers,
        _reader_values_match_after_locator_renumbering,
    )

    if not _reader_values_match_after_locator_renumbering(
        old_page.text,
        new_page.text,
    ):
        return (), (), False

    def locator_boxes(page: PageText) -> tuple[tuple[float, float, float, float], ...]:
        boxes = []
        for block in page.blocks:
            if block.kind is not DocumentBlockKind.TEXT:
                continue
            compact = _normalized_page_text(block.text)
            if compact and _reader_neutralize_locator_numbers(block.text) != compact:
                boxes.append(block.bbox)
        return tuple(boxes)

    old_boxes = locator_boxes(old_page)
    new_boxes = locator_boxes(new_page)
    return old_boxes, new_boxes, bool(old_boxes and new_boxes)


def _text_layout_comparable(old_page: PageText, new_page: PageText) -> bool:
    """Prove that direct pixel subtraction has a shared text coordinate frame.

    The watchdog deliberately does not guess a geometric transform.  It first
    removes coordinate-proven running furniture, then requires every remaining
    native text block to keep both reader-equivalent content and a tight source
    bbox.  Reflowed, mirrored-line-number, or differently wrapped pages fail
    closed: the audit becomes incomplete, but the report does not invent a
    page-wide visual change from layout movement.
    """

    from .reporting import _reader_neutralize_locator_numbers

    def visible_text_blocks(
        page: PageText,
    ) -> list[tuple[str, tuple[float, float, float, float]]]:
        blocks: list[tuple[str, tuple[float, float, float, float]]] = []
        for block in page.blocks:
            if block.kind is not DocumentBlockKind.TEXT:
                continue
            if any(
                _bbox_is_covered(block.bbox, noise_bbox)
                for noise_bbox in page.visual_noise_bboxes
            ):
                continue
            text = _reader_neutralize_locator_numbers(block.text)
            if text:
                blocks.append((text, block.bbox))
        return blocks

    old_blocks = visible_text_blocks(old_page)
    new_blocks = visible_text_blocks(new_page)
    if not old_blocks and not new_blocks:
        return True
    if len(old_blocks) != len(new_blocks):
        return False
    if old_page.page_bbox is None or new_page.page_bbox is None:
        return False

    for (old_text, old_bbox), (new_text, new_bbox) in zip(
        old_blocks,
        new_blocks,
        strict=True,
    ):
        if old_text != new_text:
            return False
        if not _bboxes_share_page_geometry(
            old_bbox,
            new_bbox,
            old_page_bbox=old_page.page_bbox,
            new_page_bbox=new_page.page_bbox,
        ):
            return False
    return True


def _bbox_is_covered(
    inner: tuple[float, float, float, float],
    outer: tuple[float, float, float, float],
) -> bool:
    """Return true when one extracted block is proven inside a noise region."""

    inner_x0, inner_top, inner_x1, inner_bottom = inner
    outer_x0, outer_top, outer_x1, outer_bottom = outer
    return (
        inner_x0 >= outer_x0
        and inner_top >= outer_top
        and inner_x1 <= outer_x1
        and inner_bottom <= outer_bottom
    )


def _bboxes_share_page_geometry(
    old_bbox: tuple[float, float, float, float],
    new_bbox: tuple[float, float, float, float],
    *,
    old_page_bbox: tuple[float, float, float, float],
    new_page_bbox: tuple[float, float, float, float],
) -> bool:
    """Compare source bboxes after normalizing each coordinate to its page."""

    old_page_x0, old_page_top, old_page_x1, old_page_bottom = old_page_bbox
    new_page_x0, new_page_top, new_page_x1, new_page_bottom = new_page_bbox
    old_width = old_page_x1 - old_page_x0
    old_height = old_page_bottom - old_page_top
    new_width = new_page_x1 - new_page_x0
    new_height = new_page_bottom - new_page_top
    if min(old_width, old_height, new_width, new_height) <= 0:
        return False

    old_normalized = (
        (old_bbox[0] - old_page_x0) / old_width,
        (old_bbox[1] - old_page_top) / old_height,
        (old_bbox[2] - old_page_x0) / old_width,
        (old_bbox[3] - old_page_top) / old_height,
    )
    new_normalized = (
        (new_bbox[0] - new_page_x0) / new_width,
        (new_bbox[1] - new_page_top) / new_height,
        (new_bbox[2] - new_page_x0) / new_width,
        (new_bbox[3] - new_page_top) / new_height,
    )
    tolerances = (
        VISUAL_LAYOUT_COORDINATE_TOLERANCE_POINTS / min(old_width, new_width),
        VISUAL_LAYOUT_COORDINATE_TOLERANCE_POINTS / min(old_height, new_height),
        VISUAL_LAYOUT_COORDINATE_TOLERANCE_POINTS / min(old_width, new_width),
        VISUAL_LAYOUT_COORDINATE_TOLERANCE_POINTS / min(old_height, new_height),
    )
    return all(
        abs(old_value - new_value) <= tolerance
        for old_value, new_value, tolerance in zip(
            old_normalized,
            new_normalized,
            tolerances,
            strict=True,
        )
    )


def _provable_exact_text_pairs(
    old_extraction: ExtractionResult,
    new_extraction: ExtractionResult,
) -> tuple[list[tuple[int, int, str]], int]:
    """Pair every uniquely identifiable exact or reader-equivalent text page.

    Repeated/empty pages may use same-number pairing only when both selected
    windows have equal length and the entire page-key sequence agrees. If an
    insertion makes identity ambiguous, those pages are skipped and the audit
    records an incomplete check rather than fabricating modified-page evidence.
    """

    old_pages = list(old_extraction.pages)
    new_pages = list(new_extraction.pages)
    old_raw_keys = [_normalized_page_text(page.text) for page in old_pages]
    new_raw_keys = [_normalized_page_text(page.text) for page in new_pages]
    old_keys = [_reader_page_identity(page.text) for page in old_pages]
    new_keys = [_reader_page_identity(page.text) for page in new_pages]
    old_counts = Counter(key for key in old_keys if key)
    new_counts = Counter(key for key in new_keys if key)

    pairs: list[tuple[int, int, str]] = []
    matched_old_indexes: set[int] = set()
    matched_new_indexes: set[int] = set()
    new_unique_indexes = {
        key: index
        for index, key in enumerate(new_keys)
        if key and new_counts[key] == 1 and old_counts[key] == 1
    }
    for old_index, key in enumerate(old_keys):
        if not key or old_counts[key] != 1 or key not in new_unique_indexes:
            continue
        new_index = new_unique_indexes[key]
        old_page_number = old_pages[old_index].page_number
        new_page_number = new_pages[new_index].page_number
        if old_raw_keys[old_index] != new_raw_keys[new_index]:
            method = "reader-equivalent-text"
        else:
            method = (
                "same-page-text"
                if old_page_number == new_page_number
                else "unique-exact-text"
            )
        pairs.append((old_page_number, new_page_number, method))
        matched_old_indexes.add(old_index)
        matched_new_indexes.add(new_index)

    if len(old_pages) == len(new_pages) and old_keys == new_keys:
        for index, (old_page, new_page) in enumerate(zip(old_pages, new_pages, strict=True)):
            if index in matched_old_indexes or old_keys[index] != new_keys[index]:
                continue
            method = "same-page-empty-text" if not old_keys[index] else "same-page-repeated-text"
            pairs.append((old_page.page_number, new_page.page_number, method))
            matched_old_indexes.add(index)
            matched_new_indexes.add(index)

    ambiguous_page_count = sum(
        1
        for index, key in enumerate(old_keys)
        if index not in matched_old_indexes and (not key or old_counts[key] > 1 or new_counts[key] > 1)
    ) + sum(
        1
        for index, key in enumerate(new_keys)
        if index not in matched_new_indexes and (not key or old_counts[key] > 1 or new_counts[key] > 1)
    )
    pairs.sort(key=lambda pair: (pair[0], pair[1]))
    return pairs, ambiguous_page_count
