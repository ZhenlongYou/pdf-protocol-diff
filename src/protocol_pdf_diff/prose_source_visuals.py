"""Coordinate-backed source crops for long prose changes.

The semantic diff remains text based.  This module only turns already detected
long changes into reader evidence while the extraction page blocks and their
source snapshot hashes are still available.
"""

from __future__ import annotations

import base64
import io
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO

from PIL import Image, ImageDraw

from .figure_filters import filter_figure_visual_snippets
from .models import (
    DiffResult,
    DocumentBlock,
    DocumentBlockKind,
    ExtractionResult,
    ProseSourceVisual,
    ProseSourceVisualGroup,
    Section,
    SectionChange,
    TableVisual,
)
from .visual_watchdog import _render_page, _snapshot_pdf

PROSE_SOURCE_VISUAL_MIN_CHANGED_CHARACTERS = 500
PROSE_SOURCE_VISUAL_MAX_PAGES_PER_SIDE = 3
PROSE_SOURCE_VISUAL_RENDER_DPI = 120
_MIN_BLOCK_TOKEN_OVERLAP = 0.45
_MIN_MATCHED_BLOCK_TOKENS = 3


def build_prose_source_visuals(
    result: DiffResult,
    old_extraction: ExtractionResult,
    new_extraction: ExtractionResult,
) -> tuple[list[ProseSourceVisualGroup], list[str]]:
    """Build screenshot evidence for long changes without changing diff facts."""

    eligible = [change for change in result.changes if _eligible_change(change)]
    if not eligible:
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
        old_pages = {page.page_number: page for page in old_extraction.pages}
        new_pages = {page.page_number: page for page in new_extraction.pages}
        old_table_bboxes = _table_bboxes_by_page(result.old_table_visuals)
        new_table_bboxes = _table_bboxes_by_page(result.new_table_visuals)
        groups: list[ProseSourceVisualGroup] = []
        for change in eligible:
            old_snippets = _change_snippets(change, side="old")
            new_snippets = _change_snippets(change, side="new")
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
            if old_visuals or new_visuals:
                groups.append(
                    ProseSourceVisualGroup(
                        change_type=change.change_type,
                        old_section_id=(
                            change.old_section.section_id
                            if change.old_section
                            else None
                        ),
                        new_section_id=(
                            change.new_section.section_id
                            if change.new_section
                            else None
                        ),
                        old_visuals=tuple(old_visuals),
                        new_visuals=tuple(new_visuals),
                        old_omitted_page_count=old_omitted_page_count,
                        new_omitted_page_count=new_omitted_page_count,
                    )
                )
        return groups, []
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


def _table_bboxes_by_page(
    tables: Iterable[TableVisual],
) -> dict[int, tuple[tuple[float, float, float, float], ...]]:
    """Group recognized table source regions for prose-highlight exclusion."""

    grouped: dict[int, list[tuple[float, float, float, float]]] = defaultdict(list)
    for table in tables:
        bbox = tuple(float(value) for value in table.bbox)
        if len(bbox) != 4 or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            continue
        grouped[int(table.page_number)].append(bbox)
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
    candidates: list[
        tuple[int, int, tuple[tuple[float, float, float, float], ...], int]
    ] = []
    for page_number, page_snippets in snippets_by_page.items():
        page = pages.get(page_number)
        if page is None or page.page_bbox is None:
            continue
        boxes, matched_snippet_count = _highlight_boxes(
            page.blocks,
            page_snippets,
            excluded_bboxes=(
                *page.visual_noise_bboxes,
                *(excluded_bboxes_by_page or {}).get(page_number, ()),
            ),
        )
        if not boxes:
            continue
        matched_extent = sum(max(0.0, box[3] - box[1]) for box in boxes)
        candidates.append(
            (page_number, int(matched_extent * 100), boxes, matched_snippet_count)
        )
    ranked = sorted(candidates, key=lambda item: (-item[1], item[0]))
    selected = ranked[:PROSE_SOURCE_VISUAL_MAX_PAGES_PER_SIDE]
    selected.sort(key=lambda item: item[0])
    visuals: list[ProseSourceVisual] = []
    for page_number, _score, boxes, matched_snippet_count in selected:
        page = pages[page_number]
        image = _render_page(
            document,
            page_number,
            dpi=PROSE_SOURCE_VISUAL_RENDER_DPI,
        )
        crop_bbox, crop, region_count = _annotated_crop(
            image,
            page_bbox=page.page_bbox,
            highlight_boxes=boxes,
        )
        visuals.append(
            ProseSourceVisual(
                page_number=page_number,
                crop_bbox=crop_bbox,
                image_data_uri=_jpeg_data_uri(crop),
                highlight_region_count=region_count,
                matched_snippet_count=matched_snippet_count,
            )
        )
    return visuals, max(0, len(ranked) - len(selected))


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
    excluded_bboxes: tuple[tuple[float, float, float, float], ...] = (),
) -> tuple[tuple[tuple[float, float, float, float], ...], int]:
    materialized = tuple(
        block
        for block in blocks
        if (
            block.text.strip()
            and block.kind is not DocumentBlockKind.TABLE
            and not _bbox_center_in_any(block.bbox, excluded_bboxes)
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
            overlap = len(block_tokens & snippet_tokens)
            required = min(_MIN_MATCHED_BLOCK_TOKENS, len(block_tokens))
            if (
                overlap >= required
                and overlap / len(block_tokens) >= _MIN_BLOCK_TOKEN_OVERLAP
            ):
                clipped_bbox = _clip_outer_noise(block.bbox, excluded_bboxes)
                if clipped_bbox is not None:
                    selected[clipped_bbox] = None
                    snippet_matched = True
        if snippet_matched:
            matched_snippets += 1
    return tuple(sorted(selected, key=lambda box: (box[1], box[0]))), matched_snippets


def _bbox_center_in_any(
    bbox: tuple[float, float, float, float],
    excluded_bboxes: tuple[tuple[float, float, float, float], ...],
) -> bool:
    center_x = (bbox[0] + bbox[2]) / 2.0
    center_y = (bbox[1] + bbox[3]) / 2.0
    return any(
        left <= center_x <= right and top <= center_y <= bottom
        for left, top, right, bottom in excluded_bboxes
    )


def _clip_outer_noise(
    bbox: tuple[float, float, float, float],
    excluded_bboxes: tuple[tuple[float, float, float, float], ...],
) -> tuple[float, float, float, float] | None:
    """Keep highlight color out of proven left/right printing-number gutters."""

    left, top, right, bottom = bbox
    for noise_left, noise_top, noise_right, noise_bottom in excluded_bboxes:
        if min(bottom, noise_bottom) <= max(top, noise_top):
            continue
        if noise_left <= left <= noise_right < right:
            left = max(left, noise_right)
        elif left < noise_left <= right <= noise_right:
            right = min(right, noise_left)
    if right - left < 1.0 or bottom - top < 1.0:
        return None
    return (left, top, right, bottom)


def _tokens(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return tuple(re.findall(r"[\w.±%+-]+", normalized, flags=re.UNICODE))


def _annotated_crop(
    image: Image.Image,
    *,
    page_bbox: tuple[float, float, float, float],
    highlight_boxes: tuple[tuple[float, float, float, float], ...],
) -> tuple[tuple[float, float, float, float], Image.Image, int]:
    x0, top, x1, bottom = page_bbox
    source_width = max(1.0, x1 - x0)
    source_height = max(1.0, bottom - top)
    scale_x = image.width / source_width
    scale_y = image.height / source_height
    left = max(x0, min(box[0] for box in highlight_boxes) - 24.0)
    crop_top = max(top, min(box[1] for box in highlight_boxes) - 36.0)
    right = min(x1, max(box[2] for box in highlight_boxes) + 24.0)
    crop_bottom = min(bottom, max(box[3] for box in highlight_boxes) + 36.0)
    crop_bbox = (left, crop_top, right, crop_bottom)
    annotated = image.convert("RGBA")
    overlay = Image.new("RGBA", annotated.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    for box in highlight_boxes:
        pixel_box = (
            max(0, round((box[0] - x0) * scale_x)),
            max(0, round((box[1] - top) * scale_y)),
            min(image.width - 1, round((box[2] - x0) * scale_x)),
            min(
                image.height - 1,
                round((box[3] - top) * scale_y),
            ),
        )
        draw.rectangle(
            pixel_box,
            fill=(255, 196, 61, 38),
            outline=(202, 111, 0, 140),
            width=1,
        )
    annotated = Image.alpha_composite(annotated, overlay).convert("RGB")
    pixel_crop = (
        max(0, round((left - x0) * scale_x)),
        max(0, round((crop_top - top) * scale_y)),
        min(image.width, round((right - x0) * scale_x)),
        min(image.height, round((crop_bottom - top) * scale_y)),
    )
    return crop_bbox, annotated.crop(pixel_crop), len(highlight_boxes)


def _jpeg_data_uri(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=84, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode(
        "ascii"
    )
