"""Frozen pre-fix visual-review layout policy for RED evidence runs."""

from __future__ import annotations

import numpy as np
from PIL import Image

VISUAL_REVIEW_IMAGE_CSS = """    .visual-review-shot img {
      display: block;
      width: 100%;
      height: auto;
      background: #fff;
    }"""


def full_width_preview_bbox(
    bbox: tuple[int, int, int, int],
    image_size: tuple[int, int],
    *,
    padding: int,
) -> tuple[int, int, int, int]:
    """Reproduce the escaped defect by cropping both axes around the change."""

    left, top, right, bottom = bbox
    width, height = image_size
    return (
        max(0, left - padding),
        max(0, top - padding),
        min(width, right + padding),
        min(height, bottom + padding),
    )


def render_material_diff_preview(old_image: Image.Image, mask: np.ndarray) -> Image.Image:
    """Keep mask rendering correct so layout defects are isolated."""

    base = np.asarray(old_image, dtype=np.uint8)
    dimmed = (base.astype(np.float32) * 0.45 + 255.0 * 0.55).astype(np.uint8)
    dimmed[mask.astype(bool)] = np.array([220, 38, 38], dtype=np.uint8)
    return Image.fromarray(dimmed, mode="RGB")
