"""Report writers for protocol PDF comparison results."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from .models import DiffOptions, DiffResult, Section, SectionChange
from .text_utils import compact_inline, truncate

_CHANGE_LABELS = {
    "added": "新增",
    "deleted": "删除",
    "modified": "修改",
    "unchanged": "未变化",
}


def write_reports(
    result: DiffResult,
    output_dir: str | Path,
    options: DiffOptions,
) -> dict[str, Path]:
    """Write Markdown, TXT, CSV, and JSON report artifacts.

    The Markdown/TXT reports are for human review. CSV is meant for filtering in
    Excel, and JSON preserves parsed section metadata for troubleshooting false
    positives or missed headings.
    """

    base_dir = Path(output_dir).expanduser().resolve()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = base_dir / f"protocol_diff_{timestamp}"
    report_dir.mkdir(parents=True, exist_ok=True)

    markdown = _render_markdown(result, options)
    text = _markdown_to_plain_text(markdown)
    csv_rows = _rows_for_csv(result.changes)
    sections_payload = {
        "old_pdf": str(result.old_pdf),
        "new_pdf": str(result.new_pdf),
        "old_sections": [_section_to_dict(section) for section in result.old_sections],
        "new_sections": [_section_to_dict(section) for section in result.new_sections],
        "warnings": result.warnings,
    }

    md_path = report_dir / "protocol_diff_report.md"
    txt_path = report_dir / "protocol_diff_report.txt"
    csv_path = report_dir / "changes.csv"
    json_path = report_dir / "parsed_sections.json"

    md_path.write_text(markdown, encoding="utf-8")
    txt_path.write_text(text, encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "change_type",
                "new_location",
                "old_location",
                "new_pages",
                "old_pages",
                "similarity",
                "summary",
                "added_snippets",
                "removed_snippets",
                "replaced_snippets",
            ],
        )
        writer.writeheader()
        writer.writerows(csv_rows)
    json_path.write_text(json.dumps(sections_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "report_dir": report_dir,
        "markdown": md_path,
        "text": txt_path,
        "csv": csv_path,
        "json": json_path,
    }


def _render_markdown(result: DiffResult, options: DiffOptions) -> str:
    """Render the main review report in Markdown."""

    counts = _change_counts(result.changes)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines: list[str] = [
        "# 协议 PDF 差异报告",
        "",
        f"- 旧协议: `{result.old_pdf}`",
        f"- 新协议: `{result.new_pdf}`",
        f"- 生成时间: {generated_at}",
        f"- 章节匹配阈值: {options.min_section_match_similarity:.2f}",
        f"- 未变化判定阈值: {options.unchanged_similarity:.3f}",
        "",
        "## 汇总",
        "",
        "| 类型 | 数量 |",
        "|---|---:|",
        f"| 修改 | {counts.get('modified', 0)} |",
        f"| 新增 | {counts.get('added', 0)} |",
        f"| 删除 | {counts.get('deleted', 0)} |",
        f"| 未变化(仅在配置开启时列出) | {counts.get('unchanged', 0)} |",
        "",
    ]

    if result.warnings:
        lines.extend(["## 抽取警告", ""])
        for warning in result.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    lines.extend(
        [
            "## 详细差异",
            "",
            "说明: 页码来自 PDF 抽取顺序；如果 PDF 自身页脚页码不同，请以 PDF 阅读器显示为准。",
            "",
        ]
    )

    if not result.changes:
        lines.extend(["未发现章节级差异。", ""])
        return "\n".join(lines)

    for index, change in enumerate(result.changes, start=1):
        label = _CHANGE_LABELS.get(change.change_type, change.change_type)
        lines.append(f"### {index}. {label}: {change.report_location}")
        if change.old_section:
            lines.append(
                f"- 旧位置: {change.old_section.location}（页 {change.old_section.page_range}）"
            )
        if change.new_section:
            lines.append(
                f"- 新位置: {change.new_section.location}（页 {change.new_section.page_range}）"
            )
        if change.old_section and change.new_section:
            lines.append(f"- 相似度: {change.similarity:.3f}")

        if change.replaced_snippets:
            lines.append("- 替换片段:")
            for pair in change.replaced_snippets:
                lines.append(f"  - 旧: {pair.old}")
                lines.append(f"    新: {pair.new}")
        if change.added_snippets:
            lines.append("- 新增片段:")
            for snippet in change.added_snippets:
                lines.append(f"  - {snippet}")
        if change.removed_snippets:
            lines.append("- 删除片段:")
            for snippet in change.removed_snippets:
                lines.append(f"  - {snippet}")
        lines.append("")

    return "\n".join(lines)


def _markdown_to_plain_text(markdown: str) -> str:
    """Convert the Markdown report into a lightweight TXT version."""

    replacements = {
        "# ": "",
        "## ": "",
        "### ": "",
        "`": "",
        "|": " ",
        "---": "",
    }
    text = markdown
    for old, new in replacements.items():
        text = text.replace(old, new)
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip() + "\n"


def _rows_for_csv(changes: list[SectionChange]) -> list[dict[str, str]]:
    """Flatten section changes for spreadsheet review."""

    rows: list[dict[str, str]] = []
    for change in changes:
        replaced = [
            f"旧: {pair.old} / 新: {pair.new}" for pair in change.replaced_snippets
        ]
        summary_parts = []
        if change.replaced_snippets:
            summary_parts.append(f"{len(change.replaced_snippets)} 处替换")
        if change.added_snippets:
            summary_parts.append(f"{len(change.added_snippets)} 处新增")
        if change.removed_snippets:
            summary_parts.append(f"{len(change.removed_snippets)} 处删除")
        rows.append(
            {
                "change_type": _CHANGE_LABELS.get(change.change_type, change.change_type),
                "new_location": change.new_section.location if change.new_section else "",
                "old_location": change.old_section.location if change.old_section else "",
                "new_pages": change.new_section.page_range if change.new_section else "",
                "old_pages": change.old_section.page_range if change.old_section else "",
                "similarity": f"{change.similarity:.3f}" if change.old_section and change.new_section else "",
                "summary": "；".join(summary_parts),
                "added_snippets": "\n".join(change.added_snippets),
                "removed_snippets": "\n".join(change.removed_snippets),
                "replaced_snippets": "\n".join(replaced),
            }
        )
    return rows


def _change_counts(changes: list[SectionChange]) -> dict[str, int]:
    """Count changes by type."""

    counts: dict[str, int] = {}
    for change in changes:
        counts[change.change_type] = counts.get(change.change_type, 0) + 1
    return counts


def _section_to_dict(section: Section) -> dict[str, object]:
    """Serialize section metadata for debug/audit output."""

    return {
        "section_id": section.section_id,
        "heading": section.heading,
        "title": section.title,
        "level": section.level,
        "location": section.location,
        "number_path": list(section.number_path),
        "page_range": section.page_range,
        "body_preview": truncate(compact_inline(section.body), 500),
    }
