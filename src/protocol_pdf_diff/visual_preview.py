"""Render conservative visual-difference evidence for offline reports."""

from __future__ import annotations

import numpy as np
from PIL import Image

VISUAL_REVIEW_IMAGE_CSS = """    .visual-review-shot img {
      display: block;
      width: auto;
      max-width: 100%;
      height: auto;
      margin: 0 auto;
    }"""
VISUAL_MASK_TECHNICAL_EXPLANATION = (
    "红色仅表示像素发生变化，不等同于协议参数或文字内容发生变化。"
)


def render_visual_mask_disclosure(diff_image_html: str) -> str:
    """Keep pixel-level diagnostics available without leading the reader flow."""

    return f"""
          <details class="visual-mask-detail">
            <summary>像素变化定位（技术复核）</summary>
            <p class="change-summary">{VISUAL_MASK_TECHNICAL_EXPLANATION}</p>
            <div class="table-shot visual-review-shot"><h4>差异掩膜</h4>{diff_image_html}</div>
          </details>"""


def full_width_preview_bbox(
    bbox: tuple[int, int, int, int],
    image_size: tuple[int, int],
    *,
    padding: int,
) -> tuple[int, int, int, int]:
    """Keep row labels by cropping visual evidence only in the vertical axis."""

    _, top, _, bottom = bbox
    width, height = image_size
    return (0, max(0, top - padding), width, min(height, bottom + padding))


def render_material_diff_preview(old_image: Image.Image, mask: np.ndarray) -> Image.Image:
    """Dim context and color only pixels proven to differ materially.

    The caller may crop around the union of all changed pixels, but this renderer
    deliberately adds no union outline. When changes occur on distant rows, such
    an outline would visually claim that unchanged content between them changed.
    """

    base = np.asarray(old_image, dtype=np.uint8)
    dimmed = (base.astype(np.float32) * 0.45 + 255.0 * 0.55).astype(np.uint8)
    dimmed[mask.astype(bool)] = np.array([220, 38, 38], dtype=np.uint8)
    return Image.fromarray(dimmed, mode="RGB")
