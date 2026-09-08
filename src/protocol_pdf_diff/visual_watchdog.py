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
from PIL import Image

from .models import (
    DiffResult,
    DocumentBlockKind,
    ExtractionResult,
    PageText,
    Section,
    VisualCoverageIssue,
    VisualReviewItem,
    VisualWatchdogAudit,
)
from .text_utils import compact_inline
from .visual_preview import full_width_preview_bbox, render_material_diff_preview

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

    (
        eligible_page_pairs,
        unmatched_old_pages,
        unmatched_new_pages,
    ) = _provable_exact_text_pairs(
        old_extraction,
        new_extraction,
    )
    covered_old_pages, covered_new_pages = (
        _reader_visible_semantic_change_pages(semantic_result)
        if unmatched_old_pages or unmatched_new_pages
        else (set(), set())
    )
    ambiguous_page_count = sum(
        page_number not in covered_old_pages for page_number in unmatched_old_pages
    ) + sum(
        page_number not in covered_new_pages for page_number in unmatched_new_pages
    )
    coverage_issues = [
        *[VisualCoverageIssue(p, None, "未能建立唯一的页面对应关系，像素尚未核对。")
          for p in unmatched_old_pages if p not in covered_old_pages],
        *[VisualCoverageIssue(None, p, "未能建立唯一的页面对应关系，像素尚未核对。")
          for p in unmatched_new_pages if p not in covered_new_pages],
    ]

    def record_unchecked_pairs(reason: str, category: str) -> None:
        for old_number, new_number, _method in eligible_page_pairs:
            if (old_number, new_number) not in processed_pairs:
                coverage_issues.append(VisualCoverageIssue(old_number, new_number, reason, category))
                processed_pairs.add((old_number, new_number))

    processed_pairs: set[tuple[int, int]] = set()
    warnings: list[str] = []
    if ambiguous_page_count:
        warnings.append(
            "视觉漏检哨兵跳过了 "
            f"{ambiguous_page_count} 个无法用完全一致或读者等价文字安全配对的页面。"
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
            coverage_issues=tuple(coverage_issues),
        )

    try:
        import pypdfium2
    except ModuleNotFoundError:
        warnings.append("视觉漏检哨兵未运行：pypdfium2 未安装。")
        record_unchecked_pairs("图像读取组件不可用，像素尚未核对。", "unavailable")
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
            coverage_issues=tuple(coverage_issues),
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
            record_unchecked_pairs("图像与文字来自不同文件快照，已停止像素核对。", "unavailable")
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
                coverage_issues=tuple(coverage_issues),
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
            layout_reason = _text_layout_incompatibility(old_page, new_page)
            if layout_reason is not None:
                failed_page_pair_count += 1
                coverage_issues.append(VisualCoverageIssue(old_page_number, new_page_number,
                    layout_reason, "layout"))
                processed_pairs.add((old_page_number, new_page_number))
                warnings.append(
                    "视觉漏检哨兵未生成像素差异卡："
                    f"旧第 {old_page_number} / 新第 {new_page_number} 页：{layout_reason}"
                )
                continue
            if alignment_method == "reader-equivalent-text":
                failed_page_pair_count += 1
                coverage_issues.append(VisualCoverageIssue(old_page_number, new_page_number,
                    "引用编号发生变化，但缺少逐字符坐标，不能安全排除编号区域。", "locator"))
                processed_pairs.add((old_page_number, new_page_number))
                warnings.append(
                    "视觉漏检哨兵未生成像素差异卡："
                    f"旧第 {old_page_number} / 新第 {new_page_number} 页仅含纯引用编号变化，"
                    "但当前抽取证据只有整行边界框、没有逐字符编号坐标；为避免吞掉"
                    "同一行内的真实小图形，本页视觉覆盖按未完成处理。"
                )
                continue
            old_excluded = (
                *old_page.visual_noise_bboxes,
                *old_evidence.get(old_page_number, ()),
            )
            new_excluded = (
                *new_page.visual_noise_bboxes,
                *new_evidence.get(new_page_number, ()),
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
                processed_pairs.add((old_page_number, new_page_number))
            except Exception as exc:
                failed_page_pair_count += 1
                coverage_issues.append(VisualCoverageIssue(old_page_number, new_page_number,
                    f"页面图像核对失败：{type(exc).__name__}。", "error"))
                processed_pairs.add((old_page_number, new_page_number))
                warnings.append(
                    "视觉漏检哨兵未能核对"
                    f"旧第 {old_page_number} / 新第 {new_page_number} 页："
                    f"{type(exc).__name__}。"
                )
                continue
            if item is not None:
                items.append(item)
    except Exception as exc:
        record_unchecked_pairs(f"图像核对中断：{type(exc).__name__}。", "error")
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
        coverage_issues=tuple(coverage_issues),
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


def _render_page(
    document: object,
    page_number: int,
    *,
    dpi: int = VISUAL_RENDER_DPI,
) -> Image.Image:
    """Render one source page at a bounded review resolution and release PDFium objects."""

    page = document[page_number - 1]
    bitmap = None
    try:
        bitmap = page.render(
            scale=dpi / 72.0,
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
    preview_bbox = full_width_preview_bbox(
        diff_bbox,
        old_canvas.size,
        padding=VISUAL_PREVIEW_PADDING,
    )
    diff_preview = render_material_diff_preview(old_canvas, material_mask).crop(preview_bbox)
    # Nearby material pixels share a navigation target; no pixel is removed and
    # these boxes never participate in semantic decisions or the original mask.
    grouped = cv2.dilate(material_mask, np.ones((9, 9), dtype=np.uint8))
    count, grouped_labels, _, _ = cv2.connectedComponentsWithStats(grouped, connectivity=8)
    group_ids = grouped_labels[ys, xs]
    lefts = np.full(count, material_mask.shape[1], dtype=np.int32)
    tops = np.full(count, material_mask.shape[0], dtype=np.int32)
    rights = np.full(count, -1, dtype=np.int32)
    bottoms = np.full(count, -1, dtype=np.int32)
    np.minimum.at(lefts, group_ids, xs)
    np.minimum.at(tops, group_ids, ys)
    np.maximum.at(rights, group_ids, xs)
    np.maximum.at(bottoms, group_ids, ys)
    regions = [(int(lefts[i])-preview_bbox[0], int(tops[i])-preview_bbox[1],
                int(rights[i])+1-preview_bbox[0], int(bottoms[i])+1-preview_bbox[1])
               for i in range(1, count) if rights[i] >= 0]
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
        focus_regions=tuple(sorted(regions, key=lambda box: (box[1], box[0]))),
        preview_size=diff_preview.size,
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


def _text_layout_comparable(old_page: PageText, new_page: PageText) -> bool:
    return _text_layout_incompatibility(old_page, new_page) is None


def _text_layout_incompatibility(old_page: PageText, new_page: PageText) -> str | None:
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
                _bboxes_intersect(block.bbox, noise_bbox)
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
        return None
    if len(old_blocks) != len(new_blocks):
        return "文字版式证据中的文本块数量不同，不能直接比较像素。"
    if old_page.page_bbox is None or new_page.page_bbox is None:
        return "缺少页面坐标，不能核对文字版式与像素位置。"

    for (old_text, old_bbox), (new_text, new_bbox) in zip(
        old_blocks,
        new_blocks,
        strict=True,
    ):
        if old_text != new_text:
            return "对应文本块内容不同，无法证明文字版式与像素位置一致。"
        if not _bboxes_share_page_geometry(
            old_bbox,
            new_bbox,
            old_page_bbox=old_page.page_bbox,
            new_page_bbox=new_page.page_bbox,
        ):
            return "文字版式坐标发生移动或重排，不能直接比较像素。"
    return None


def _bboxes_intersect(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    """Return true when a text line contains any coordinate-proven noise word."""

    first_x0, first_top, first_x1, first_bottom = first
    second_x0, second_top, second_x1, second_bottom = second
    return (
        min(first_x1, second_x1) > max(first_x0, second_x0)
        and min(first_bottom, second_bottom) > max(first_top, second_top)
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
) -> tuple[list[tuple[int, int, str]], tuple[int, ...], tuple[int, ...]]:
    """Pair every uniquely identifiable exact or reader-equivalent text page.

    Repeated/empty pages may use same-number pairing only when both selected
    windows have equal length and the entire page-key sequence agrees. Every
    unmatched page is counted as incomplete coverage: an insertion, deletion,
    semantic edit, or extraction mismatch must never disappear behind an
    ``eligible=0, complete=true`` audit result.
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

    unmatched_old_pages = tuple(
        page.page_number
        for index, page in enumerate(old_pages)
        if index not in matched_old_indexes
    )
    unmatched_new_pages = tuple(
        page.page_number
        for index, page in enumerate(new_pages)
        if index not in matched_new_indexes
    )
    pairs.sort(key=lambda pair: (pair[0], pair[1]))
    return pairs, unmatched_old_pages, unmatched_new_pages


def _reader_visible_semantic_change_pages(
    result: DiffResult | None,
) -> tuple[set[int], set[int]]:
    """Return pages already represented by material reader-facing change cards.

    The pixel watchdog deliberately compares only pages with unchanged or
    locator-equivalent reader text.  A page that is unmatched because it has a
    visible semantic change is outside that scope and must not make every normal
    redline look degraded.  Conversely, raw citation renumbering, metadata, and
    other reader-suppressed facts do not count as coverage; if such a page loses
    its reader identity, the audit remains incomplete.
    """

    if result is None:
        return set(), set()
    from .reporting import (
        _build_table_changes,
        _ordered_table_changes,
        _paired_table_visuals,
        _reader_changes_without_cross_card_locator_pairs,
        _reader_section_change,
        _reader_table_changes,
    )

    table_groups = _paired_table_visuals(
        result.old_table_visuals,
        result.new_table_visuals,
        old_sections=result.old_sections,
        new_sections=result.new_sections,
    )
    table_changes = _ordered_table_changes(_build_table_changes(result))
    table_evidence = [*table_changes, *table_groups]
    figure_visual_sides_by_identity = {
        (group.change_type, group.old_section_id, group.new_section_id): (
            bool(group.old_figure_visuals),
            bool(group.new_figure_visuals),
        )
        for group in result.prose_source_visuals
        if group.old_figure_visuals or group.new_figure_visuals
    }
    reader_changes = []
    for change in result.changes:
        if change.role == "document_metadata":
            continue
        reader_change = _reader_section_change(
            change,
            table_evidence,
            figure_visual_sides=figure_visual_sides_by_identity.get(
                (
                    change.change_type,
                    change.old_section.section_id if change.old_section else None,
                    change.new_section.section_id if change.new_section else None,
                ),
                (False, False),
            ),
        )
        if reader_change is not None:
            reader_changes.append(reader_change)
    reader_changes = _reader_changes_without_cross_card_locator_pairs(reader_changes)

    old_pages: set[int] = set()
    new_pages: set[int] = set()
    for change in reader_changes:
        for pair in change.replaced_snippets:
            old_pages.update(
                _reader_snippet_unique_page(change.old_section, pair.old)
            )
            new_pages.update(
                _reader_snippet_unique_page(change.new_section, pair.new)
            )
        for snippet in change.removed_snippets:
            old_pages.update(
                _reader_snippet_unique_page(change.old_section, snippet)
            )
        for snippet in change.added_snippets:
            new_pages.update(
                _reader_snippet_unique_page(change.new_section, snippet)
            )
    for change in _reader_table_changes(table_changes):
        old_pages.update(table.page_number for table in change.old_tables)
        new_pages.update(table.page_number for table in change.new_tables)
    for change in result.formula_changes:
        if change.old_formula is not None:
            old_pages.add(change.old_formula.page_number)
        if change.new_formula is not None:
            new_pages.add(change.new_formula.page_number)
    return old_pages, new_pages


def _reader_snippet_unique_page(
    section: Section | None,
    snippet: str,
) -> set[int]:
    """Bind a reader-visible snippet to one physical page or fail closed.

    A section card may span many pages, so its location range is not page-level
    provenance. Only an exact unique occurrence in ``page_bodies`` can exempt
    an unmatched page from the unchanged-page pixel audit. Legacy single-page
    sections remain unambiguous; multi-page sections without provenance do not.
    """

    if section is None:
        return set()
    normalized = compact_inline(snippet)
    if not normalized:
        return set()
    heading_fact = compact_inline(f"章节标题: {section.heading}")
    if normalized == heading_fact:
        return {section.start_page}
    if not section.page_bodies:
        return (
            {section.start_page}
            if section.start_page == section.end_page
            else set()
        )
    occurrence_pages = {
        page_number
        for page_number, body in section.page_bodies
        if normalized in compact_inline(body)
    }
    return occurrence_pages if len(occurrence_pages) == 1 else set()
