"""Render conservative visual-difference evidence for offline reports."""

from __future__ import annotations

import numpy as np
from PIL import Image


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
