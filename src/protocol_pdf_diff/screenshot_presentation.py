"""Reader image helpers; display viewports never redefine extraction ownership."""

import base64
import difflib
import io
import re
from collections import Counter

from PIL import Image

from .models import TableChange, TableVisual
from .prose_source_visuals import _annotated_source_crop, _jpeg_data_uri


def table_context_image(table: TableVisual, change: TableChange | None, side: str) -> tuple[str, bool]:
    """Only color unique observed changed words in a reliably aligned table.

    Repeated values and uncertain row ownership remain neutral. A report must
    not turn a detector's bounding rectangle into an assertion of a change.
    """
    uri = table.context_image_data_uri
    if (not uri or not table.context_bbox or not table.row_alignment_reliable
            or not table.context_words or change is None or change.change_type == "review"):
        return uri, False
    # Count across the entire logical table, including continuation pages.
    tables = change.old_tables if side == "old" else change.new_tables
    counts = Counter(w[0] for t in tables for w in t.context_words)
    changed = set()
    for row in change.row_changes:
        if row.change_type == "需人工复核":
            continue
        old = re.findall(r"\w+(?:[.−+-]\w+)*|[^\w\s]", row.old_value)
        new = re.findall(r"\w+(?:[.−+-]\w+)*|[^\w\s]", row.new_value)
        for tag, i, j, a, b in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
            if tag != "equal":
                changed.update(old[i:j] if side == "old" else new[a:b])
    boxes = tuple(
        (x, y, right, bottom) for word, x, y, right, bottom in table.context_words
        if word in changed and counts[word] == 1
        and table.bbox[0] <= x < right <= table.bbox[2]
        and table.bbox[1] <= y < bottom <= table.bbox[3]
    )
    if not boxes:
        return uri, False
    try:
        with Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))) as source:
            image = _annotated_source_crop(source.convert("RGB"), page_bbox=table.context_bbox,
                                          crop_bbox=table.context_bbox, highlight_boxes=boxes, side=side)
            return _jpeg_data_uri(image), True
    except (ValueError, OSError):
        return uri, False


IMAGE_VIEWER = """
<style>
.prose-source-page img,.table-shot-page img {cursor:zoom-in}
.similarity-review-appendix {margin-top:32px;border:1px solid #d6dce5;border-radius:12px;padding:20px;background:#f8fafc}
.similarity-review-appendix>summary {font-size:19px;font-weight:650;cursor:pointer}
.table-text-details {margin-top:16px}
.source-image-viewer {max-width:96vw;max-height:96vh;border:1px solid #cbd5e1;border-radius:12px;padding:12px}
.source-image-viewer::backdrop {background:rgba(15,23,42,.65)}
.source-image-viewer header {position:sticky;top:0;background:white;color:#1f2937;display:flex;justify-content:space-between;gap:20px;padding:8px}
.source-image-viewer img {display:block;max-width:none;width:auto;height:auto}
@media print {.similarity-review-appendix:not([open]) {display:none}}
</style>
<dialog class="source-image-viewer" aria-label="完整原页截图"><header><span></span><button type="button">关闭</button></header><img alt=""></dialog>
<script>
(() => {
  const dialog = document.querySelector('.source-image-viewer');
  document.addEventListener('click', e => {
    const image = e.target.closest('.prose-source-page img,.table-shot-page img');
    if (!image) return;
    const full = dialog.querySelector('img'); full.src = image.src; full.alt = image.alt;
    dialog.querySelector('span').textContent = image.alt + ' · 原始分辨率';
    dialog.showModal();
  });
  dialog.querySelector('button').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', e => {if(e.target === dialog) dialog.close();});
})();
</script>
"""
