"""Coordinate-backed source crops for long prose changes.

The semantic diff remains text based.  This module only turns already detected
long changes into reader evidence while the extraction page blocks and their
source snapshot hashes are still available.
"""

from __future__ import annotations

import base64
import difflib
import io
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO

from PIL import Image

from .figure_filters import filter_figure_visual_snippets
from .models import (
    DiffResult,
    DocumentBlock,
    DocumentBlockKind,
    ExtractionResult,
    FormulaVisual,
    ProseSourceVisual,
    ProseSourceVisualGroup,
    Section,
    SectionChange,
    TableVisual,
)
from .pdf_extract import _looks_like_figure_caption
from .visual_watchdog import _render_page, _snapshot_pdf

PROSE_SOURCE_VISUAL_MIN_CHANGED_CHARACTERS = 500
PROSE_SOURCE_VISUAL_MAX_PAGES_PER_SIDE = 3
PROSE_SOURCE_VISUAL_RENDER_DPI = 120
_MIN_BLOCK_TOKEN_OVERLAP = 0.45
_MIN_MATCHED_BLOCK_TOKENS = 3
_MIN_SECTION_BLOCK_COVERAGE = 0.72
_MIN_VISIBLE_REGION_WIDTH = 6.0
_MIN_VISIBLE_REGION_HEIGHT = 3.0
_MIN_VISIBLE_REGION_AREA_RATIO = 0.08
_SOURCE_CROP_MIN_PAGE_WIDTH_RATIO = 0.64
_SOURCE_CROP_VERTICAL_PADDING = 2.0
_SOURCE_CROP_HORIZONTAL_PADDING = 14.0
_SOURCE_PARAGRAPH_LINE_MAX_GAP = 4.0
_NUMBERED_HEADING_BLOCK_RE = re.compile(
    r"^\s*(?:\d+\s+)?\d+(?:\.\d+)*\s+"
    r"(?=[A-Za-z0-9 /()_-]*[A-Za-z]{3})[A-Z][A-Za-z0-9 /()_-]{2,}"
)
_PROSE_BOUNDARY_RE = re.compile(
    r"(?i)\b(?:shall|should|must|may|can|is|are|was|were|defines?|"
    r"requires?|specifies?|describes?|meets?|uses?|shown)\b"
)
_DISPLAYED_FORMULA_NUMBER_RE = re.compile(
    r"\([A-Z]?\d+(?:[-.]\d+)+\)\s*$",
    flags=re.IGNORECASE,
)
_DISPLAYED_FORMULA_OPERATOR_RE = re.compile(r"[=≤≥<>±×÷*/^∑∫√]|[\ue000-\uf8ff]")
_BOTTOM_MARGIN_FURNITURE_HINT_RE = re.compile(
    r"(?i)\b(?:clause|consortium|copyright|draft|edition|forum|page|revision|"
    r"specification|standard|version|working\s+group|www\.|https?://)\b|©"
)


def build_prose_source_visuals(
    result: DiffResult,
    old_extraction: ExtractionResult,
    new_extraction: ExtractionResult,
) -> tuple[list[ProseSourceVisualGroup], list[str]]:
    """Build screenshot evidence for long changes without changing diff facts."""

    candidates = [
        change
        for change in result.changes
        if change.role == "technical" and change.change_type != "unchanged"
    ]
    if not candidates:
        return [], []
    try:
        import pypdfium2
    except ModuleNotFoundError:
        return [], ["长正文原文截图未生成：pypdfium2 未安装，已回退到文字明细。"]

    old_snapshot: BinaryIO | None = None
    new_snapshot: BinaryIO | None = None
    old_document = None
    new_document = None
    try:
        old_snapshot, old_sha = _snapshot_pdf(Path(old_extraction.pdf_path))
        new_snapshot, new_sha = _snapshot_pdf(Path(new_extraction.pdf_path))
        if not (
            old_extraction.source_sha256
            and new_extraction.source_sha256
            and old_sha == old_extraction.source_sha256
            and new_sha == new_extraction.source_sha256
        ):
            return [], [
                "长正文原文截图未生成：渲染快照与文字抽取快照不一致，已回退到文字明细。"
            ]
        old_snapshot.seek(0)
        new_snapshot.seek(0)
        old_document = pypdfium2.PdfDocument(old_snapshot, autoclose=False)
        new_document = pypdfium2.PdfDocument(new_snapshot, autoclose=False)
        return _build_visual_groups(
            result,
            candidates,
            old_document=old_document,
            new_document=new_document,
            old_extraction=old_extraction,
            new_extraction=new_extraction,
        ), []
    except Exception as exc:  # noqa: BLE001 - 展示增强失败必须回退文字，不能中断主报告。
        return [], [f"长正文原文截图未生成：{type(exc).__name__}，已回退到文字明细。"]
    finally:
        if old_document is not None:
            old_document.close()
        if new_document is not None:
            new_document.close()
        if old_snapshot is not None:
            old_snapshot.close()
        if new_snapshot is not None:
            new_snapshot.close()


def _build_visual_groups(
    result: DiffResult,
    candidates: list[SectionChange],
    *,
    old_document: object,
    new_document: object,
    old_extraction: ExtractionResult,
    new_extraction: ExtractionResult,
) -> list[ProseSourceVisualGroup]:
    """Materialize mutually owned prose, Figure, Table, and Formula regions."""

    old_pages = {page.page_number: page for page in old_extraction.pages}
    new_pages = {page.page_number: page for page in new_extraction.pages}
    old_table_bboxes = _visual_bboxes_by_page(result.old_table_visuals)
    new_table_bboxes = _visual_bboxes_by_page(result.new_table_visuals)
    old_figure_blockers = _merge_bboxes_by_page(
        old_table_bboxes,
        _visual_bboxes_by_page(result.old_formula_visuals),
    )
    new_figure_blockers = _merge_bboxes_by_page(
        new_table_bboxes,
        _visual_bboxes_by_page(result.new_formula_visuals),
    )
    groups: list[ProseSourceVisualGroup] = []
    for change in candidates:
        old_snippets = _change_snippets(change, side="old")
        new_snippets = _change_snippets(change, side="new")
        if _eligible_change(change):
            old_visuals, old_omitted_page_count = _build_side_visuals(
                old_document,
                change.old_section,
                old_pages,
                old_snippets,
                excluded_bboxes_by_page=old_table_bboxes,
            )
            new_visuals, new_omitted_page_count = _build_side_visuals(
                new_document,
                change.new_section,
                new_pages,
                new_snippets,
                excluded_bboxes_by_page=new_table_bboxes,
            )
        else:
            old_visuals, old_omitted_page_count = [], 0
            new_visuals, new_omitted_page_count = [], 0
        old_figure_visuals = _build_figure_visuals(
            old_document,
            change.old_section,
            old_pages,
            blocking_bboxes_by_page=old_figure_blockers,
        )
        new_figure_visuals = _build_figure_visuals(
            new_document,
            change.new_section,
            new_pages,
            blocking_bboxes_by_page=new_figure_blockers,
        )
        if not (old_visuals or new_visuals or old_figure_visuals or new_figure_visuals):
            continue
        groups.append(
            ProseSourceVisualGroup(
                change_type=change.change_type,
                old_section_id=(
                    change.old_section.section_id if change.old_section else None
                ),
                new_section_id=(
                    change.new_section.section_id if change.new_section else None
                ),
                old_visuals=tuple(old_visuals),
                new_visuals=tuple(new_visuals),
                old_figure_visuals=tuple(old_figure_visuals),
                new_figure_visuals=tuple(new_figure_visuals),
                old_omitted_page_count=old_omitted_page_count,
                new_omitted_page_count=new_omitted_page_count,
            )
        )
    return groups


def _eligible_change(change: SectionChange) -> bool:
    """Use screenshots only when text walls would materially hurt scanning."""

    if change.role != "technical" or change.change_type == "unchanged":
        return False
    if any(
        section is not None and section.section_id == "running-header-evidence"
        for section in (change.old_section, change.new_section)
    ):
        return False
    changed_characters = sum(
        len(value) for value in _change_snippets(change, side="old")
    )
    changed_characters += sum(
        len(value) for value in _change_snippets(change, side="new")
    )
    return changed_characters >= PROSE_SOURCE_VISUAL_MIN_CHANGED_CHARACTERS


def _change_snippets(change: SectionChange, *, side: str) -> tuple[str, ...]:
    if side == "old":
        values = [
            *change.removed_snippets,
            *(pair.old for pair in change.replaced_snippets),
        ]
    else:
        values = [
            *change.added_snippets,
            *(pair.new for pair in change.replaced_snippets),
        ]
    return tuple(filter_figure_visual_snippets(values))


def _visual_bboxes_by_page(
    visuals: Iterable[TableVisual | FormulaVisual],
) -> dict[int, tuple[tuple[float, float, float, float], ...]]:
    """Group coordinate-owned Table or Formula regions by source page."""

    grouped: dict[int, list[tuple[float, float, float, float]]] = defaultdict(list)
    for visual in visuals:
        bbox = tuple(float(value) for value in visual.bbox)
        if len(bbox) != 4 or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            continue
        grouped[int(visual.page_number)].append(bbox)
    return {
        page_number: tuple(sorted(boxes, key=lambda box: (box[1], box[0])))
        for page_number, boxes in grouped.items()
    }


def _merge_bboxes_by_page(
    *sources: dict[int, tuple[tuple[float, float, float, float], ...]],
) -> dict[int, tuple[tuple[float, float, float, float], ...]]:
    """Merge independently owned layout regions without losing page order."""

    grouped: dict[int, list[tuple[float, float, float, float]]] = defaultdict(list)
    for source in sources:
        for page_number, boxes in source.items():
            grouped[page_number].extend(boxes)
    return {
        page_number: tuple(sorted(boxes, key=lambda box: (box[1], box[0])))
        for page_number, boxes in grouped.items()
    }


def _build_side_visuals(
    document: object,
    section: Section | None,
    pages: dict[int, object],
    snippets: tuple[str, ...],
    *,
    excluded_bboxes_by_page: dict[
        int,
        tuple[tuple[float, float, float, float], ...],
    ] | None = None,
) -> tuple[list[ProseSourceVisual], int]:
    if section is None or not snippets:
        return [], 0
    snippets_by_page = _assign_snippets_to_pages(section, snippets)
    page_body_by_number = dict(
        section.page_bodies or ((section.start_page, section.body),)
    )
    candidates: list[
        tuple[
            int,
            int,
            tuple[tuple[float, float, float, float], ...],
            tuple[tuple[float, float, float, float], ...],
            int,
        ]
    ] = []
    for page_number, page_snippets in snippets_by_page.items():
        page = pages.get(page_number)
        if page is None or page.page_bbox is None:
            continue
        page_body = page_body_by_number.get(page_number, "")
        if (excluded_bboxes_by_page or {}).get(page_number):
            continue  # Table 页面已有专属结构化卡，不再重复附加正文截图。
        if _page_contains_figure_caption(page.blocks):
            continue  # Figure 页面由无标色原图卡独占视觉证据。
        boxes, matched_snippet_count = _highlight_boxes(
            page.blocks,
            page_snippets,
            allowed_text=page_body,
            excluded_bboxes=(
                *page.visual_noise_bboxes,
                *(excluded_bboxes_by_page or {}).get(page_number, ()),
            ),
        )
        boxes = _expand_boxes_to_complete_paragraph_lines(
            page.blocks,
            boxes,
            allowed_text=page_body,
            excluded_bboxes=(
                *page.visual_noise_bboxes,
                *(excluded_bboxes_by_page or {}).get(page_number, ()),
            ),
        )
        if not boxes:
            continue
        crop_regions = _crop_regions(
            page_bbox=page.page_bbox,
            boxes=boxes,
            blocking_bboxes=(excluded_bboxes_by_page or {}).get(page_number, ()),
            noise_bboxes=page.visual_noise_bboxes,
        )
        if not crop_regions:
            continue
        matched_extent = sum(max(0.0, box[3] - box[1]) for box in boxes)
        candidates.append(
            (
                page_number,
                int(matched_extent * 100),
                boxes,
                crop_regions,
                matched_snippet_count,
            )
        )
    ranked = sorted(candidates, key=lambda item: (-item[1], item[0]))
    selected = ranked[:PROSE_SOURCE_VISUAL_MAX_PAGES_PER_SIDE]
    selected.sort(key=lambda item: item[0])
    visuals: list[ProseSourceVisual] = []
    for page_number, _score, _boxes, crop_regions, matched_snippet_count in selected:
        page = pages[page_number]
        image = _render_page(
            document,
            page_number,
            dpi=PROSE_SOURCE_VISUAL_RENDER_DPI,
        )
        for crop_index, crop_bbox in enumerate(crop_regions):
            crop = _source_crop(
                image,
                page_bbox=page.page_bbox,
                crop_bbox=crop_bbox,
            )
            visuals.append(
                ProseSourceVisual(
                    page_number=page_number,
                    crop_bbox=crop_bbox,
                    image_data_uri=_jpeg_data_uri(crop),
                    highlight_region_count=0,
                    matched_snippet_count=(
                        matched_snippet_count if crop_index == 0 else 0
                    ),
                    precision="source-coordinate-raw-crop",
                )
            )
    return visuals, max(0, len(ranked) - len(selected))


def _build_figure_visuals(
    document: object,
    section: Section | None,
    pages: dict[int, object],
    *,
    blocking_bboxes_by_page: dict[
        int,
        tuple[tuple[float, float, float, float], ...],
    ] | None = None,
) -> list[ProseSourceVisual]:
    """Render raw Figure regions owned by this section, without comparison paint."""

    if section is None:
        return []
    visuals: list[ProseSourceVisual] = []
    for page_number, page_body in (
        section.page_bodies or ((section.start_page, section.body),)
    ):
        if not re.search(r"(?i)\bFigure\s+[A-Z]?\d", page_body):
            continue
        page = pages.get(page_number)
        if page is None or page.page_bbox is None:
            continue
        ordered_blocks = tuple(sorted(page.blocks, key=lambda block: block.reading_order))
        caption_indexes = [
            index
            for index, block in enumerate(ordered_blocks)
            if (
                block.kind is not DocumentBlockKind.TABLE
                and _looks_like_figure_caption(block.text.strip())
                and _block_belongs_to_section(block.text, page_body)
            )
        ]
        if not caption_indexes:
            continue
        image = _render_page(
            document,
            page_number,
            dpi=PROSE_SOURCE_VISUAL_RENDER_DPI,
        )
        for caption_index in caption_indexes:
            crop_bbox = _figure_crop_bbox(
                page_bbox=page.page_bbox,
                blocks=ordered_blocks,
                caption_index=caption_index,
                blocking_bboxes=(blocking_bboxes_by_page or {}).get(page_number, ()),
                noise_bboxes=page.visual_noise_bboxes,
                vector_graphic_bboxes=page.vector_graphic_bboxes,
            )
            if crop_bbox is None:
                continue
            visuals.append(
                ProseSourceVisual(
                    page_number=page_number,
                    crop_bbox=crop_bbox,
                    image_data_uri=_jpeg_data_uri(
                        _source_crop(
                            image,
                            page_bbox=page.page_bbox,
                            crop_bbox=crop_bbox,
                        )
                    ),
                    highlight_region_count=0,
                    matched_snippet_count=0,
                    precision="source-figure-uncompared",
                )
            )
            if len(visuals) >= PROSE_SOURCE_VISUAL_MAX_PAGES_PER_SIDE:
                return visuals
    return visuals


def _figure_crop_bbox(
    *,
    page_bbox: tuple[float, float, float, float],
    blocks: tuple[DocumentBlock, ...],
    caption_index: int,
    blocking_bboxes: tuple[tuple[float, float, float, float], ...],
    noise_bboxes: tuple[tuple[float, float, float, float], ...],
    vector_graphic_bboxes: tuple[tuple[float, float, float, float], ...] = (),
) -> tuple[float, float, float, float] | None:
    """Bound one caption-led Figure at the next prose, heading, Figure, or Table."""

    caption = blocks[caption_index]
    x0, page_top, x1, page_bottom = page_bbox
    left, right = _content_horizontal_bounds(page_bbox, noise_bboxes)
    top = max(page_top, caption.bbox[1] - 10.0)
    bottom = page_bottom
    boundary_blocks = _figure_boundary_blocks(
        blocks,
        caption=caption,
        page_bbox=page_bbox,
        content_left=left,
        content_right=right,
        vector_graphic_bboxes=vector_graphic_bboxes,
    )
    if boundary_blocks:
        bottom = min(
            bottom,
            min(block.bbox[1] for block in boundary_blocks) - 8.0,
        )
    footer_boundary = _coordinate_footer_boundary(
        noise_bboxes,
        caption_bottom=caption.bbox[3],
        content_left=left,
        content_right=right,
    )
    if footer_boundary is not None:
        bottom = min(bottom, footer_boundary - 8.0)
    for blocker in blocking_bboxes:
        if blocker[1] >= caption.bbox[3] and blocker[1] < bottom:
            bottom = blocker[1]
    if right - left < (x1 - x0) * _SOURCE_CROP_MIN_PAGE_WIDTH_RATIO:
        left, right = x0, x1
    if bottom - top < 36.0:
        return None
    return (left, top, right, bottom)


def _figure_boundary_blocks(
    blocks: tuple[DocumentBlock, ...],
    *,
    caption: DocumentBlock,
    page_bbox: tuple[float, float, float, float],
    content_left: float,
    content_right: float,
    vector_graphic_bboxes: tuple[tuple[float, float, float, float], ...],
) -> list[DocumentBlock]:
    """Collect content objects that take ownership below a Figure."""

    boundaries = [
        block
        for block in blocks
        if block.bbox[1] > caption.bbox[3] + 4.0
        and _is_figure_boundary_block(
            block,
            page_bbox=page_bbox,
            content_left=content_left,
            content_right=content_right,
        )
    ]
    wrapped = _wrapped_prose_boundary(
        blocks,
        caption=caption,
        content_left=content_left,
        content_right=content_right,
    )
    if wrapped is not None:
        boundaries.append(wrapped)
    formula = _displayed_formula_boundary(blocks, caption=caption)
    if formula is not None:
        boundaries.append(formula)
    unnumbered_formula = _unnumbered_formula_boundary(
        blocks,
        caption=caption,
        page_bbox=page_bbox,
        vector_graphic_bboxes=vector_graphic_bboxes,
    )
    if unnumbered_formula is not None:
        boundaries.append(unnumbered_formula)
    return boundaries


def _is_figure_boundary_block(
    block: DocumentBlock,
    *,
    page_bbox: tuple[float, float, float, float],
    content_left: float,
    content_right: float,
) -> bool:
    text = " ".join(block.text.split())
    return bool(
        _looks_like_figure_caption(text)
        or _NUMBERED_HEADING_BLOCK_RE.match(text)
        or _looks_like_prose_after_figure(text)
        or _looks_like_bottom_margin_furniture(
            block,
            page_bbox=page_bbox,
            content_left=content_left,
            content_right=content_right,
        )
    )


def _displayed_formula_boundary(
    blocks: tuple[DocumentBlock, ...],
    *,
    caption: DocumentBlock,
) -> DocumentBlock | None:
    """Find an equation row only when a numbered formula anchors its geometry."""

    anchors = [
        block
        for block in blocks
        if block.bbox[1] > caption.bbox[3] + 4.0
        and _DISPLAYED_FORMULA_NUMBER_RE.search(" ".join(block.text.split()))
    ]
    if not anchors:
        return None
    anchor = min(anchors, key=lambda block: (block.bbox[1], block.bbox[0]))
    aligned_expressions = [
        block
        for block in blocks
        if caption.bbox[3] + 4.0 < block.bbox[1] <= anchor.bbox[3]
        and anchor.bbox[1] - block.bbox[1] <= 36.0
        and _looks_like_formula_expression(block.text)
    ]
    return min(
        (anchor, *aligned_expressions),
        key=lambda block: (block.bbox[1], block.bbox[0]),
    )


def _looks_like_formula_expression(value: str) -> bool:
    """Recognize math only inside a nearby numbered-formula evidence group."""

    compact = " ".join(value.split())
    operator_count = len(_DISPLAYED_FORMULA_OPERATOR_RE.findall(compact))
    number_count = len(re.findall(r"\d+(?:\.\d+)?", compact))
    return 8 <= len(compact) <= 240 and operator_count >= 2 and number_count >= 1


def _unnumbered_formula_boundary(
    blocks: tuple[DocumentBlock, ...],
    *,
    caption: DocumentBlock,
    page_bbox: tuple[float, float, float, float],
    vector_graphic_bboxes: tuple[tuple[float, float, float, float], ...],
) -> DocumentBlock | None:
    """Stop at unnumbered math only when it is geometrically below the graphic."""

    graphic_bottom = _substantial_figure_graphic_bottom(
        caption,
        page_bbox=page_bbox,
        vector_graphic_bboxes=vector_graphic_bboxes,
    )
    if graphic_bottom is None:
        return None
    candidates = [
        block
        for block in blocks
        if block.bbox[1] >= graphic_bottom + 6.0
        and _looks_like_formula_expression(block.text)
    ]
    return min(candidates, key=lambda block: (block.bbox[1], block.bbox[0])) if candidates else None


def _substantial_figure_graphic_bottom(
    caption: DocumentBlock,
    *,
    page_bbox: tuple[float, float, float, float],
    vector_graphic_bboxes: tuple[tuple[float, float, float, float], ...],
) -> float | None:
    """Return the last edge of a wide or tall graphic below the Figure caption."""

    page_width = max(1.0, page_bbox[2] - page_bbox[0])
    substantial = [
        bbox
        for bbox in vector_graphic_bboxes
        if bbox[3] > caption.bbox[3]
        and bbox[2] - bbox[0] >= page_width * 0.18
    ]
    return max((bbox[3] for bbox in substantial), default=None)


def _looks_like_prose_after_figure(value: str) -> bool:
    """Recognize the first ordinary sentence after a caption-led diagram."""

    if not value:
        return False
    ends_sentence = bool(re.search(r"[.!?。！？]\s*$", value))
    return bool(
        (len(value) >= 80 and (_PROSE_BOUNDARY_RE.search(value) or ends_sentence))
        or (len(value) >= 45 and ends_sentence)
    )


def _looks_like_bottom_margin_furniture(
    block: DocumentBlock,
    *,
    page_bbox: tuple[float, float, float, float],
    content_left: float,
    content_right: float,
) -> bool:
    """Recognize one wide, shallow line isolated in the extreme bottom margin."""

    _page_left, page_top, _page_right, page_bottom = page_bbox
    page_height = max(1.0, page_bottom - page_top)
    content_width = max(1.0, content_right - content_left)
    return bool(
        "\n" not in block.text
        and block.bbox[1] >= page_top + page_height * 0.90
        and block.bbox[3] - block.bbox[1] <= page_height * 0.035
        and block.bbox[2] - block.bbox[0] >= content_width * 0.55
        and _BOTTOM_MARGIN_FURNITURE_HINT_RE.search(block.text)
    )


def _wrapped_prose_boundary(
    blocks: tuple[DocumentBlock, ...],
    *,
    caption: DocumentBlock,
    content_left: float,
    content_right: float,
) -> DocumentBlock | None:
    """Find a body paragraph whose first physical line is not a full sentence."""

    content_width = max(1.0, content_right - content_left)
    following = sorted(
        (
            block
            for block in blocks
            if (
                block.kind is not DocumentBlockKind.TABLE
                and block.bbox[1] > caption.bbox[3] + 4.0
            )
        ),
        key=lambda block: (block.bbox[1], block.bbox[0]),
    )
    for index, first in enumerate(following):
        first_text = " ".join(first.text.split())
        first_words = re.findall(r"[A-Za-z]{2,}", first_text)
        if (
            _looks_like_figure_caption(first_text)
            or _NUMBERED_HEADING_BLOCK_RE.match(first_text)
            or first.bbox[0] > content_left + content_width * 0.14
            or (first.bbox[2] - first.bbox[0]) < content_width * 0.62
            or len(first_words) < 7
        ):
            continue
        for second in following[index + 1 :]:
            gap = second.bbox[1] - first.bbox[3]
            if gap > 8.0:
                break
            second_text = " ".join(second.text.split())
            second_words = re.findall(r"[A-Za-z]{2,}", second_text)
            if (
                gap < -2.0
                or second.bbox[0] > content_left + content_width * 0.16
                or abs(second.bbox[0] - first.bbox[0]) > 40.0
                or len(second_words) < 4
            ):
                continue
            combined = f"{first_text} {second_text}"
            if _PROSE_BOUNDARY_RE.search(combined) or len(
                re.findall(r"[A-Za-z]{2,}", combined)
            ) >= 16:
                return first
    return None


def _page_contains_figure_caption(blocks: Iterable[DocumentBlock]) -> bool:
    """Use full-page coordinates to assign a Figure page to the Figure channel."""

    return any(
        block.kind is not DocumentBlockKind.TABLE
        and _looks_like_figure_caption(block.text.strip())
        for block in blocks
    )


def _coordinate_footer_boundary(
    noise_bboxes: tuple[tuple[float, float, float, float], ...],
    *,
    caption_bottom: float,
    content_left: float,
    content_right: float,
) -> float | None:
    """Combine same-baseline footer words into a boundary without using gutters."""

    bands: list[list[tuple[float, float, float, float]]] = []
    for bbox in sorted(noise_bboxes, key=lambda item: (item[1], item[0])):
        if bbox[1] <= caption_bottom + 12.0:
            continue
        if bands and abs(bbox[1] - bands[-1][0][1]) <= 2.5:
            bands[-1].append(bbox)
        else:
            bands.append([bbox])
    content_width = max(1.0, content_right - content_left)
    for band in bands:
        intervals = sorted(
            (
                max(content_left, bbox[0]),
                min(content_right, bbox[2]),
            )
            for bbox in band
            if min(content_right, bbox[2]) > max(content_left, bbox[0])
        )
        covered = 0.0
        merged_right: float | None = None
        for interval_left, interval_right in intervals:
            if merged_right is None or interval_left > merged_right:
                covered += interval_right - interval_left
                merged_right = interval_right
            elif interval_right > merged_right:
                covered += interval_right - merged_right
                merged_right = interval_right
        if covered / content_width >= 0.45:
            return min(bbox[1] for bbox in band)
    return None


def _assign_snippets_to_pages(
    section: Section,
    snippets: tuple[str, ...],
) -> dict[int, tuple[str, ...]]:
    page_bodies = section.page_bodies or ((section.start_page, section.body),)
    page_tokens = {
        page_number: set(_tokens(body))
        for page_number, body in page_bodies
        if body.strip()
    }
    assigned: dict[int, list[str]] = defaultdict(list)
    for snippet in snippets:
        snippet_tokens = set(_tokens(snippet))
        if not snippet_tokens:
            continue
        scored = [
            (len(snippet_tokens & body_tokens) / len(snippet_tokens), page_number)
            for page_number, body_tokens in page_tokens.items()
        ]
        if not scored:
            continue
        score, page_number = max(scored)
        if score >= 0.20:
            assigned[page_number].append(snippet)
    return {page: tuple(values) for page, values in assigned.items()}


def _highlight_boxes(
    blocks: Iterable[DocumentBlock],
    snippets: tuple[str, ...],
    *,
    allowed_text: str = "",
    excluded_bboxes: tuple[tuple[float, float, float, float], ...] = (),
) -> tuple[tuple[tuple[float, float, float, float], ...], int]:
    materialized = tuple(
        block
        for block in blocks
        if (
            block.text.strip()
            and block.kind is not DocumentBlockKind.TABLE
            and (
                not allowed_text
                or _block_belongs_to_section(block.text, allowed_text)
            )
        )
    )
    selected: dict[tuple[float, float, float, float], None] = {}
    matched_snippets = 0
    for snippet in snippets:
        snippet_tokens = set(_tokens(snippet))
        if not snippet_tokens:
            continue
        snippet_matched = False
        for block in materialized:
            block_tokens = set(_tokens(block.text))
            if not block_tokens:
                continue
            ordered_overlap = difflib.SequenceMatcher(
                None,
                tuple(_tokens(block.text)),
                tuple(_tokens(snippet)),
                autojunk=False,
            ).find_longest_match().size
            required = min(_MIN_MATCHED_BLOCK_TOKENS, len(block_tokens))
            if (
                ordered_overlap >= required
                and ordered_overlap / len(block_tokens) >= _MIN_BLOCK_TOKEN_OVERLAP
            ):
                visible_bboxes = _subtract_excluded_regions(
                    block.bbox,
                    excluded_bboxes,
                )
                if visible_bboxes:
                    for visible_bbox in visible_bboxes:
                        selected[visible_bbox] = None
                    snippet_matched = True
        if snippet_matched:
            matched_snippets += 1
    return tuple(sorted(selected, key=lambda box: (box[1], box[0]))), matched_snippets


def _expand_boxes_to_complete_paragraph_lines(
    blocks: Iterable[DocumentBlock],
    boxes: tuple[tuple[float, float, float, float], ...],
    *,
    allowed_text: str = "",
    excluded_bboxes: tuple[tuple[float, float, float, float], ...] = (),
) -> tuple[tuple[float, float, float, float], ...]:
    """Grow matched lines to complete tightly connected source paragraphs.

    Exact snippet matching can stop on a subscript or the penultimate line of a
    paragraph.  A raw source crop should remain readable context, so include
    coordinate-adjacent lines that still belong to the same section.  The
    strict vertical-gap limit prevents the next heading or paragraph from
    leaking into the crop.
    """

    if not boxes:
        return ()
    candidates: list[tuple[float, float, float, float]] = []
    for block in blocks:
        if (
            not block.text.strip()
            or block.kind is DocumentBlockKind.TABLE
            or (
                allowed_text
                and not _block_belongs_to_section(block.text, allowed_text)
            )
        ):
            continue
        candidates.extend(_subtract_excluded_regions(block.bbox, excluded_bboxes))

    expanded = set(boxes)
    changed = True
    while changed:
        changed = False
        for candidate in candidates:
            if candidate in expanded:
                continue
            if any(
                _paragraph_lines_are_connected(candidate, selected)
                for selected in expanded
            ):
                expanded.add(candidate)
                changed = True
    return tuple(sorted(expanded, key=lambda box: (box[1], box[0])))


def _paragraph_lines_are_connected(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    vertical_gap = max(first[1], second[1]) - min(first[3], second[3])
    if vertical_gap > _SOURCE_PARAGRAPH_LINE_MAX_GAP:
        return False
    first_width = max(1.0, first[2] - first[0])
    second_width = max(1.0, second[2] - second[0])
    horizontal_overlap = max(
        0.0,
        min(first[2], second[2]) - max(first[0], second[0]),
    )
    aligned_left = abs(first[0] - second[0]) <= 28.0
    return aligned_left or horizontal_overlap / min(first_width, second_width) >= 0.35


def _block_belongs_to_section(block_text: str, section_page_body: str) -> bool:
    """Require ordered evidence that a page block belongs to this section body."""

    block_tokens = _tokens(block_text)
    body_tokens = _tokens(section_page_body)
    if not block_tokens or not body_tokens:
        return False
    longest = difflib.SequenceMatcher(
        None,
        block_tokens,
        body_tokens,
        autojunk=False,
    ).find_longest_match().size
    return longest / len(block_tokens) >= _MIN_SECTION_BLOCK_COVERAGE


def _subtract_excluded_regions(
    bbox: tuple[float, float, float, float],
    excluded_bboxes: tuple[tuple[float, float, float, float], ...],
) -> tuple[tuple[float, float, float, float], ...]:
    """Subtract every proven table/noise rectangle from one highlight block."""

    source_width = max(0.0, bbox[2] - bbox[0])
    source_height = max(0.0, bbox[3] - bbox[1])
    source_area = source_width * source_height
    pieces = [bbox]
    for excluded in excluded_bboxes:
        next_pieces: list[tuple[float, float, float, float]] = []
        for left, top, right, bottom in pieces:
            overlap_left = max(left, excluded[0])
            overlap_top = max(top, excluded[1])
            overlap_right = min(right, excluded[2])
            overlap_bottom = min(bottom, excluded[3])
            if overlap_right <= overlap_left or overlap_bottom <= overlap_top:
                next_pieces.append((left, top, right, bottom))
                continue
            candidates = (
                (left, top, right, overlap_top),
                (left, overlap_bottom, right, bottom),
                (left, overlap_top, overlap_left, overlap_bottom),
                (overlap_right, overlap_top, right, overlap_bottom),
            )
            next_pieces.extend(
                candidate
                for candidate in candidates
                if _region_is_meaningful(candidate, source_area=source_area)
            )
        pieces = next_pieces
        if not pieces:
            break
    return tuple(pieces)


def _region_is_meaningful(
    bbox: tuple[float, float, float, float],
    *,
    source_area: float,
) -> bool:
    """Reject crop slivers that cannot contain a readable glyph or phrase."""

    width = max(0.0, bbox[2] - bbox[0])
    height = max(0.0, bbox[3] - bbox[1])
    area_ratio = width * height / source_area if source_area else 0.0
    return (
        width >= _MIN_VISIBLE_REGION_WIDTH
        and height >= _MIN_VISIBLE_REGION_HEIGHT
        and area_ratio >= _MIN_VISIBLE_REGION_AREA_RATIO
    )


def _tokens(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return tuple(re.findall(r"[\w.±%+-]+", normalized, flags=re.UNICODE))


def _crop_regions(
    *,
    page_bbox: tuple[float, float, float, float],
    boxes: tuple[tuple[float, float, float, float], ...],
    blocking_bboxes: tuple[tuple[float, float, float, float], ...] = (),
    noise_bboxes: tuple[tuple[float, float, float, float], ...] = (),
) -> tuple[tuple[float, float, float, float], ...]:
    """Build readable raw-source crops without crossing Table-owned regions."""

    if not boxes:
        return ()
    x0, top, x1, bottom = page_bbox
    content_left, content_right = _content_horizontal_bounds(
        page_bbox,
        noise_bboxes,
    )
    clusters = _cluster_source_boxes(
        boxes,
        blocking_bboxes=blocking_bboxes,
        content_left=content_left,
        content_right=content_right,
    )
    minimum_width = (x1 - x0) * _SOURCE_CROP_MIN_PAGE_WIDTH_RATIO
    regions: list[tuple[float, float, float, float]] = []
    for cluster in clusters:
        candidate = _crop_region_for_cluster(
            cluster,
            page_top=top,
            page_bottom=bottom,
            content_left=content_left,
            content_right=content_right,
            minimum_width=minimum_width,
            blocking_bboxes=blocking_bboxes,
        )
        if candidate is not None:
            regions.append(candidate)
    return tuple(regions)


def _cluster_source_boxes(
    boxes: tuple[tuple[float, float, float, float], ...],
    *,
    blocking_bboxes: tuple[tuple[float, float, float, float], ...],
    content_left: float,
    content_right: float,
) -> list[list[tuple[float, float, float, float]]]:
    """Split changed text boxes only where another owned region intervenes."""

    clusters: list[list[tuple[float, float, float, float]]] = []
    for box in sorted(boxes, key=lambda item: (item[1], item[0])):
        if not clusters:
            clusters.append([box])
            continue
        previous_bottom = max(item[3] for item in clusters[-1])
        blocked = any(
            _is_vertical_blocker(
                blocker,
                previous_bottom,
                box[1],
                content_left,
                content_right,
            )
            for blocker in blocking_bboxes
        )
        if blocked:
            clusters.append([box])
        else:
            clusters[-1].append(box)
    return clusters


def _crop_region_for_cluster(
    cluster: list[tuple[float, float, float, float]],
    *,
    page_top: float,
    page_bottom: float,
    content_left: float,
    content_right: float,
    minimum_width: float,
    blocking_bboxes: tuple[tuple[float, float, float, float], ...],
) -> tuple[float, float, float, float] | None:
    """Fit one readable crop and clip it away from independently owned regions."""

    left = max(
        content_left,
        min(box[0] for box in cluster) - _SOURCE_CROP_HORIZONTAL_PADDING,
    )
    right = min(
        content_right,
        max(box[2] for box in cluster) + _SOURCE_CROP_HORIZONTAL_PADDING,
    )
    left, right = _expand_crop_to_minimum_width(
        left,
        right,
        content_left=content_left,
        content_right=content_right,
        minimum_width=minimum_width,
    )
    crop_top = max(
        page_top,
        min(box[1] for box in cluster) - _SOURCE_CROP_VERTICAL_PADDING,
    )
    crop_bottom = min(
        page_bottom,
        max(box[3] for box in cluster) + _SOURCE_CROP_VERTICAL_PADDING,
    )
    crop_top, crop_bottom = _clip_crop_to_blockers(
        (left, crop_top, right, crop_bottom),
        cluster=cluster,
        blocking_bboxes=blocking_bboxes,
    )
    if right - left < minimum_width or crop_bottom - crop_top < 8.0:
        return None
    return (left, crop_top, right, crop_bottom)


def _expand_crop_to_minimum_width(
    left: float,
    right: float,
    *,
    content_left: float,
    content_right: float,
    minimum_width: float,
) -> tuple[float, float]:
    """Widen a narrow text crop symmetrically, then anchor it to a content edge."""

    if right - left >= minimum_width:
        return left, right
    extra = minimum_width - (right - left)
    left = max(content_left, left - extra / 2.0)
    right = min(content_right, right + extra / 2.0)
    if right - left >= minimum_width:
        return left, right
    if left <= content_left:
        return left, min(content_right, content_left + minimum_width)
    return max(content_left, content_right - minimum_width), right


def _clip_crop_to_blockers(
    crop: tuple[float, float, float, float],
    *,
    cluster: list[tuple[float, float, float, float]],
    blocking_bboxes: tuple[tuple[float, float, float, float], ...],
) -> tuple[float, float]:
    """Keep the crop on its side of every horizontally overlapping owner."""

    left, crop_top, right, crop_bottom = crop
    cluster_top = min(box[1] for box in cluster)
    cluster_bottom = max(box[3] for box in cluster)
    for blocker in blocking_bboxes:
        if _horizontal_overlap_ratio((left, crop_top, right, crop_bottom), blocker) < 0.45:
            continue
        if cluster_bottom <= blocker[1]:
            crop_bottom = min(crop_bottom, blocker[1])
        elif cluster_top >= blocker[3]:
            crop_top = max(crop_top, blocker[3])
    return crop_top, crop_bottom


def _content_horizontal_bounds(
    page_bbox: tuple[float, float, float, float],
    noise_bboxes: tuple[tuple[float, float, float, float], ...],
) -> tuple[float, float]:
    """Use coordinate-proven side furniture to keep source crops inside body lanes."""

    x0, _top, x1, _bottom = page_bbox
    width = max(1.0, x1 - x0)
    left = x0 + width * 0.035
    right = x1 - width * 0.035
    left_gutter = _proven_side_gutter_bound(
        page_bbox,
        noise_bboxes,
        side="left",
    )
    right_gutter = _proven_side_gutter_bound(
        page_bbox,
        noise_bboxes,
        side="right",
    )
    if left_gutter is not None:
        left = max(left, left_gutter)
    if right_gutter is not None:
        right = min(right, right_gutter)
    if right - left < width * _SOURCE_CROP_MIN_PAGE_WIDTH_RATIO:
        return x0 + width * 0.035, x1 - width * 0.035
    return left, right


def _proven_side_gutter_bound(
    page_bbox: tuple[float, float, float, float],
    noise_bboxes: tuple[tuple[float, float, float, float], ...],
    *,
    side: str,
) -> float | None:
    """Return an inner gutter edge only for a dense, tall column of small boxes."""

    x0, page_top, x1, page_bottom = page_bbox
    width = max(1.0, x1 - x0)
    height = max(1.0, page_bottom - page_top)
    candidates = [
        box
        for box in noise_bboxes
        if (
            box[2] > box[0]
            and box[3] > box[1]
            and box[2] - box[0] <= width * 0.08
            and (
                (
                    side == "left"
                    and box[0] <= x0 + width * 0.16
                    and box[2] <= x0 + width * 0.22
                )
                or (
                    side == "right"
                    and box[2] >= x1 - width * 0.16
                    and box[0] >= x1 - width * 0.22
                )
            )
        )
    ]
    distinct_rows = {
        round((box[1] + box[3]) / 2.0, 1)
        for box in candidates
    }
    if len(distinct_rows) < 8:
        return None
    vertical_span = max(box[3] for box in candidates) - min(
        box[1] for box in candidates
    )
    if vertical_span < height * 0.35:
        return None
    inner_edges = sorted(box[2] if side == "left" else box[0] for box in candidates)
    return inner_edges[len(inner_edges) // 2]


def _is_vertical_blocker(
    blocker: tuple[float, float, float, float],
    upper: float,
    lower: float,
    content_left: float,
    content_right: float,
) -> bool:
    if blocker[1] >= lower or blocker[3] <= upper:
        return False
    content_width = max(1.0, content_right - content_left)
    overlap = max(0.0, min(blocker[2], content_right) - max(blocker[0], content_left))
    return overlap / content_width >= 0.45


def _horizontal_overlap_ratio(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    first_width = max(1.0, first[2] - first[0])
    overlap = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    return overlap / first_width


def _source_crop(
    image: Image.Image,
    *,
    page_bbox: tuple[float, float, float, float],
    crop_bbox: tuple[float, float, float, float],
) -> Image.Image:
    """Return an unmodified source crop; comparison colors belong to text diff only."""

    x0, top, x1, bottom = page_bbox
    source_width = max(1.0, x1 - x0)
    source_height = max(1.0, bottom - top)
    scale_x = image.width / source_width
    scale_y = image.height / source_height
    left, crop_top, right, crop_bottom = crop_bbox
    pixel_crop = (
        max(0, round((left - x0) * scale_x)),
        max(0, round((crop_top - top) * scale_y)),
        min(image.width, round((right - x0) * scale_x)),
        min(image.height, round((crop_bottom - top) * scale_y)),
    )
    return image.convert("RGB").crop(pixel_crop)


def _jpeg_data_uri(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=84, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode(
        "ascii"
    )
