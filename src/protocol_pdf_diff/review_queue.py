"""Build a reader-facing queue without changing comparison conclusions.

The comparison pipeline has several useful evidence channels: prose, table rows,
unexplained pixels, and pages whose pixels could not be compared.  Their native
units are deliberately different.  This module turns each safely attributable
unit into an explicit *review task* so the report never adds those counts as if
they were one kind of engineering change.

It is intentionally a presentation adapter.  It must not merge a prose card
and a table card merely because their page numbers are close, and it must not
turn a visual or coverage task into a confirmed content change.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Iterable

from .models import SectionChange, TableChange, VisualCoverageIssue, VisualReviewItem


@dataclass(frozen=True)
class ReviewTask:
    """One user action with its status and a stable link to report evidence."""

    task_id: str
    status: str  # detected / review / coverage
    evidence_kind: str  # prose / table / visual / coverage
    title: str
    detail: str
    href: str
    fact_count: int = 0
    old_pages: tuple[int, ...] = ()
    new_pages: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Keep JSON machine-readable without exposing private Python fields."""

        return asdict(self)


def build_review_tasks(
    prose_changes: Iterable[SectionChange],
    table_changes: Iterable[TableChange],
    visual_items: Iterable[VisualReviewItem],
    coverage_issues: Iterable[VisualCoverageIssue],
) -> tuple[ReviewTask, ...]:
    """Return a conservative, source-linked queue in report-card order.

    A prose section and a logical table are already source-owned objects.  A
    visual page is a separate diagnostic object.  Coverage records are grouped
    only when they carry the same *reason and category*, while every physical
    page remains in the detailed coverage table.
    """

    tasks: list[ReviewTask] = []
    for index, change in enumerate(prose_changes, 1):
        material_facts = _prose_material_fact_count(change)
        review_facts = _prose_review_fact_count(change)
        common = {
            "evidence_kind": "prose",
            "href": f"#change-{index}",
            "old_pages": _section_pages(change.old_section),
            "new_pages": _section_pages(change.new_section),
        }
        # A mixed card may contain one confirmed literal change plus separate
        # ambiguous glyph/ownership evidence.  Keeping both in a single review
        # status would hide the confirmed fact; two actions may therefore link
        # to the same immutable source card.
        if change.change_type != "review" and (
            material_facts or change.change_type in {"added", "deleted"}
        ):
            facts = material_facts or 1
            tasks.append(ReviewTask(
                task_id=f"C{index}", status="detected", fact_count=facts,
                title=_prose_title(change, facts, review=False),
                detail="已检测到文字内容变化；请核对原文与适用条件。", **common,
            ))
        if change.change_type == "review" or review_facts:
            facts = review_facts or _prose_fact_count(change) or 1
            tasks.append(ReviewTask(
                task_id=f"C{index}" if change.change_type == "review" else f"C{index}R",
                status="review", fact_count=facts,
                title=_prose_title(change, facts, review=True),
                detail=change.review_reason or "文字对应关系尚未完全验证。", **common,
            ))
    for index, change in enumerate(table_changes, 1):
        material_facts = _table_material_fact_count(change)
        review_facts = _table_review_fact_count(change)
        common = {
            "evidence_kind": "table",
            "href": f"#table-change-{index}",
            "old_pages": tuple(table.page_number for table in change.old_tables),
            "new_pages": tuple(table.page_number for table in change.new_tables),
        }
        if change.change_type != "review" and (
            material_facts or change.change_type in {"added", "deleted"}
        ):
            facts = material_facts or 1
            tasks.append(ReviewTask(
                task_id=f"T{index}", status="detected", fact_count=facts,
                title=_table_title(change, facts, review=False),
                detail="已检测到表格内容、表题或表号变化。", **common,
            ))
        if change.change_type == "review" or review_facts:
            facts = review_facts or _table_fact_count(change) or 1
            tasks.append(ReviewTask(
                task_id=f"T{index}" if change.change_type == "review" else f"T{index}R",
                status="review", fact_count=facts,
                title=_table_title(change, facts, review=True),
                detail="表格行列或对应关系尚未完全验证。", **common,
            ))
    for index, item in enumerate(visual_items, 1):
        old_pages = ((item.old_page_number,) if item.old_page_number is not None else ())
        new_pages = ((item.new_page_number,) if item.new_page_number is not None else ())
        tasks.append(
            ReviewTask(
                task_id=f"V{index}",
                status="review",
                evidence_kind="visual",
                title=f"未解释的视觉变化：旧页 {_page_label(old_pages)} / 新页 {_page_label(new_pages)}",
                detail="像素变化需要人工解释；它不等于已确认的技术内容变化。",
                href=f"#visual-review-{index}",
                fact_count=len(item.focus_regions),
                old_pages=old_pages,
                new_pages=new_pages,
            )
        )
    grouped_coverage = Counter((issue.category, issue.reason) for issue in coverage_issues)
    for index, ((category, reason), count) in enumerate(sorted(grouped_coverage.items()), 1):
        tasks.append(
            ReviewTask(
                task_id=f"U{index}",
                status="coverage",
                evidence_kind="coverage",
                title=f"{count} 条尚未完成的视觉核对：{_coverage_category_label(category)}",
                detail=reason,
                href="#visual-coverage",
                fact_count=count,
            )
        )
    return tuple(tasks)


def task_counts(tasks: Iterable[ReviewTask]) -> dict[str, int]:
    """Count review actions by status; never combine their fact counters."""

    counts = Counter(task.status for task in tasks)
    return {key: counts.get(key, 0) for key in ("detected", "review", "coverage")}


def _prose_fact_count(change: SectionChange) -> int:
    return (
        len(change.added_snippets)
        + len(change.removed_snippets)
        + len(change.replaced_snippets)
        + len(change.review_replaced_snippets)
    )


def _prose_material_fact_count(change: SectionChange) -> int:
    return (
        len(change.added_snippets)
        + len(change.removed_snippets)
        + len(change.replaced_snippets)
    )


def _prose_review_fact_count(change: SectionChange) -> int:
    return len(change.review_replaced_snippets)


def _prose_title(change: SectionChange, facts: int, *, review: bool) -> str:
    location = (change.new_section or change.old_section)
    label = location.location if location is not None else "未确定章节"
    prefix = "待核实的文字证据" if review else "文字变化"
    return f"{prefix}：{label}（{facts} 条明细）"


def _table_fact_count(change: TableChange) -> int:
    return len(change.row_changes) + int(change.caption_changed)


def _table_material_fact_count(change: TableChange) -> int:
    return int(change.caption_changed) + sum(
        row.change_type != "需人工复核" for row in change.row_changes
    )


def _table_review_fact_count(change: TableChange) -> int:
    return sum(row.change_type == "需人工复核" for row in change.row_changes)


def _table_title(change: TableChange, facts: int, *, review: bool) -> str:
    table = next(iter(change.new_tables or change.old_tables), None)
    title = table.title if table and table.title else "未命名表格"
    prefix = "待核实的表格证据" if review else "表格变化"
    return f"{prefix}：{title}（{facts} 条明细）"


def _section_pages(section: object | None) -> tuple[int, ...]:
    if section is None:
        return ()
    return tuple(range(section.start_page, section.end_page + 1))


def _page_label(pages: tuple[int, ...]) -> str:
    return ", ".join(str(page) for page in pages) if pages else "无对应页"


def _coverage_category_label(category: str) -> str:
    return {
        "unpaired": "没有安全页面对应关系",
        "layout": "版面或文字块无法安全对齐",
        "locator": "来源定位不足",
        "unavailable": "视觉后端不可用",
        "error": "执行异常",
    }.get(category, category or "原因未分类")
