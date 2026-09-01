"""Frozen pre-fix preview renderer used only for the RED evidence run."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw


def render_material_diff_preview(old_image: Image.Image, mask: np.ndarray) -> Image.Image:
    """Reproduce the escaped defect by drawing one union rectangle."""

    base = np.asarray(old_image, dtype=np.uint8)
    dimmed = (base.astype(np.float32) * 0.45 + 255.0 * 0.55).astype(np.uint8)
    dimmed[mask.astype(bool)] = np.array([220, 38, 38], dtype=np.uint8)
    preview = Image.fromarray(dimmed, mode="RGB")
    ys, xs = np.nonzero(mask)
    ImageDraw.Draw(preview).rectangle(
        (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1),
        outline=(185, 28, 28),
        width=3,
    )
    return preview
