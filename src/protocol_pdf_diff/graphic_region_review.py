"""正文有改动时，补查由唯一原文图题和固定几何共同证明的图示区域。"""

from collections import Counter
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from .models import VisualCoverageIssue


def compare_captioned_graphics(old, new, audit):
    """局部结果不会升级整页覆盖，也不会推断图的技术含义。"""
    from .visual_watchdog import _snapshot_pdf, _render_page, _compare_page_images
    changed_old = {a for a, _b in audit.semantic_change_pages if a is not None}
    changed_new = {b for _a, b in audit.semantic_change_pages if b is not None}
    observations = []
    for extraction in (old, new):
        values = [(page, kind, label, box) for page in extraction.pages for kind, label, box in page.graphic_regions]
        counts = Counter((kind, label) for _page, kind, label, _box in values)
        observations.append({(kind, label): (page, box) for page, kind, label, box in values if counts[kind, label] == 1})
    candidates = []
    for key in observations[0].keys() & observations[1].keys():
        a, box = observations[0][key]
        b, other = observations[1][key]
        if (a.page_number in changed_old and b.page_number in changed_new and box == other
                and a.page_bbox is not None and a.page_bbox == b.page_bbox):
            candidates.append((key, a, b, box))
    if not candidates:
        return [], [], audit
    items, warnings, checked, issues = [], [], [], list(audit.coverage_issues)
    try:
        import pypdfium2
        with ExitStack() as stack:
            snapshots = []
            for extraction in (old, new):
                handle, digest = _snapshot_pdf(Path(extraction.pdf_path))
                stack.callback(handle.close)
                if not extraction.source_sha256 or digest != extraction.source_sha256:
                    raise ValueError("图示渲染与抽取快照不一致")
                handle.seek(0)
                document = pypdfium2.PdfDocument(handle, autoclose=False)
                stack.callback(document.close)
                snapshots.append(document)
            rendered = ({}, {})
            for (kind, label), a, b, box in sorted(candidates, key=lambda c: (c[1].page_number, c[3])):
                for index, page in enumerate((a, b)):
                    if page.page_number not in rendered[index]:
                        rendered[index][page.page_number] = _render_page(snapshots[index], page.page_number)
                old_image, new_image = rendered[0][a.page_number], rendered[1][b.page_number]
                if old_image.size != new_image.size:
                    issues.append(VisualCoverageIssue(a.page_number, b.page_number, "固定图示所在页渲染尺寸不同，未核对该区域。", "region"))
                    continue
                item = _compare_page_images(old_image, new_image, old_page_number=a.page_number,
                    new_page_number=b.page_number, alignment_method="unique-caption-fixed-region",
                    old_page_bbox=a.page_bbox, new_page_bbox=b.page_bbox, allowed_bboxes=(box,))
                checked.append(dict(old_page=a.page_number, new_page=b.page_number, bbox=list(box),
                                    caption=label, kind=kind, changed=item is not None,
                                    method="unique-caption-fixed-region"))
                if item:
                    items.append(replace(item, reason=f"{label}：相同图题及固定区域内出现像素变化，请核对源图；未解释图形语义。"))
    except Exception as exc:
        warnings.append(f"局部图示核对未完成：{type(exc).__name__}。")
        completed = {(r["old_page"], r["new_page"], tuple(r["bbox"])) for r in checked}
        for _key, a, b, box in candidates:
            if (a.page_number, b.page_number, box) not in completed:
                issues.append(VisualCoverageIssue(a.page_number, b.page_number, "固定图示区域未完成核对：" + type(exc).__name__, "region"))
    return items, warnings, replace(audit, checked_graphic_regions=tuple(checked), coverage_issues=tuple(issues))
