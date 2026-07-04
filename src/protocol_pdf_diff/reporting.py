"""Report writers for protocol PDF comparison results."""

# Codex说明(自动生成)： 从 __future__ 导入 annotations，启用较新的类型标注行为，减少运行期导入或前向引用问题。
from __future__ import annotations

# Codex说明(自动生成)： 导入 csv，读写 CSV 表格数据。
import csv
# Codex说明(自动生成)： 导入 json，读写结构化 JSON 配置或结果文件。
import json
# Codex说明(自动生成)： 从 datetime 导入 datetime，提供本文件后续流程需要的库能力。
from datetime import datetime
# Codex说明(自动生成)： 从 pathlib 导入 Path，用 Path 对象处理跨平台文件路径。
from pathlib import Path

# Codex说明(自动生成)： 从 models 导入 DiffOptions, DiffResult, Section, SectionChange，提供本文件后续流程需要的库能力。
from .models import DiffOptions, DiffResult, Section, SectionChange
# Codex说明(自动生成)： 从 text_utils 导入 compact_inline, truncate，提供本文件后续流程需要的库能力。
from .text_utils import compact_inline, truncate

# Codex说明(自动生成)： 计算并保存 _CHANGE_LABELS，供后续语句继续读取或更新。
_CHANGE_LABELS = {
    "added": "新增",
    "deleted": "删除",
    "modified": "修改",
    "unchanged": "未变化",
}


# Codex说明(自动生成)： 定义函数 write_reports，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
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

    # Codex说明(自动生成)： 计算并保存 base_dir，供后续语句继续读取或更新。
    base_dir = Path(output_dir).expanduser().resolve()
    # Codex说明(自动生成)： 计算并保存 timestamp，供后续语句继续读取或更新。
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Codex说明(自动生成)： 计算并保存 report_dir，供后续语句继续读取或更新。
    report_dir = base_dir / f"protocol_diff_{timestamp}"
    # Codex说明(自动生成)： 调用 report_dir.mkdir，执行当前流程需要的具体操作或副作用。
    report_dir.mkdir(parents=True, exist_ok=True)

    # Codex说明(自动生成)： 计算并保存 markdown，供后续语句继续读取或更新。
    markdown = _render_markdown(result, options)
    # Codex说明(自动生成)： 计算并保存 text，供后续语句继续读取或更新。
    text = _markdown_to_plain_text(markdown)
    # Codex说明(自动生成)： 计算并保存 csv_rows，供后续语句继续读取或更新。
    csv_rows = _rows_for_csv(result.changes)
    # Codex说明(自动生成)： 计算并保存 sections_payload，供后续语句继续读取或更新。
    sections_payload = {
        "old_pdf": str(result.old_pdf),
        "new_pdf": str(result.new_pdf),
        "old_sections": [_section_to_dict(section) for section in result.old_sections],
        "new_sections": [_section_to_dict(section) for section in result.new_sections],
        "warnings": result.warnings,
    }

    # Codex说明(自动生成)： 计算并保存 md_path，供后续语句继续读取或更新。
    md_path = report_dir / "protocol_diff_report.md"
    # Codex说明(自动生成)： 计算并保存 txt_path，供后续语句继续读取或更新。
    txt_path = report_dir / "protocol_diff_report.txt"
    # Codex说明(自动生成)： 计算并保存 csv_path，供后续语句继续读取或更新。
    csv_path = report_dir / "changes.csv"
    # Codex说明(自动生成)： 计算并保存 json_path，供后续语句继续读取或更新。
    json_path = report_dir / "parsed_sections.json"

    # Codex说明(自动生成)： 调用 md_path.write_text 写出文件或数据，保存当前处理结果。
    md_path.write_text(markdown, encoding="utf-8")
    # Codex说明(自动生成)： 调用 txt_path.write_text 写出文件或数据，保存当前处理结果。
    txt_path.write_text(text, encoding="utf-8")
    # Codex说明(自动生成)： 进入上下文 csv_path.open('w', encoding='utf-8-sig', newline='')，确保文件、资源或临时状态按作用域正确释放。
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        # Codex说明(自动生成)： 计算并保存 writer，供后续语句继续读取或更新。
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
        # Codex说明(自动生成)： 调用 writer.writeheader 写出文件或数据，保存当前处理结果。
        writer.writeheader()
        # Codex说明(自动生成)： 调用 writer.writerows 写出文件或数据，保存当前处理结果。
        writer.writerows(csv_rows)
    # Codex说明(自动生成)： 调用 json_path.write_text 写出文件或数据，保存当前处理结果。
    json_path.write_text(json.dumps(sections_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # Codex说明(自动生成)： 返回 {'report_dir': report_dir, 'markdown': md_path, 'text':...，让调用方取得本函数的处理结果。
    return {
        "report_dir": report_dir,
        "markdown": md_path,
        "text": txt_path,
        "csv": csv_path,
        "json": json_path,
    }


# Codex说明(自动生成)： 定义函数 _render_markdown，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _render_markdown(result: DiffResult, options: DiffOptions) -> str:
    """Render the main review report in Markdown."""

    # Codex说明(自动生成)： 计算并保存 counts，供后续语句继续读取或更新。
    counts = _change_counts(result.changes)
    # Codex说明(自动生成)： 计算并保存 generated_at，供后续语句继续读取或更新。
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Codex说明(自动生成)： 声明并保存 lines，同时保留类型信息方便维护和静态检查。
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

    # Codex说明(自动生成)： 检查条件 result.warnings，根据结果选择后续执行路径。
    if result.warnings:
        # Codex说明(自动生成)： 调用 lines.extend 更新列表或集合，把当前步骤产生的数据加入结果。
        lines.extend(["## 抽取警告", ""])
        # Codex说明(自动生成)： 遍历 result.warnings 中的 warning，逐项执行循环体逻辑。
        for warning in result.warnings:
            # Codex说明(自动生成)： 调用 lines.append 更新列表或集合，把当前步骤产生的数据加入结果。
            lines.append(f"- {warning}")
        # Codex说明(自动生成)： 调用 lines.append 更新列表或集合，把当前步骤产生的数据加入结果。
        lines.append("")

    # Codex说明(自动生成)： 调用 lines.extend 更新列表或集合，把当前步骤产生的数据加入结果。
    lines.extend(
        [
            "## 详细差异",
            "",
            "说明: 页码来自 PDF 抽取顺序；如果 PDF 自身页脚页码不同，请以 PDF 阅读器显示为准。",
            "",
        ]
    )

    # Codex说明(自动生成)： 检查条件 not result.changes，根据结果选择后续执行路径。
    if not result.changes:
        # Codex说明(自动生成)： 调用 lines.extend 更新列表或集合，把当前步骤产生的数据加入结果。
        lines.extend(["未发现章节级差异。", ""])
        # Codex说明(自动生成)： 返回 '\n'.join(lines)，让调用方取得本函数的处理结果。
        return "\n".join(lines)

    # Codex说明(自动生成)： 遍历 enumerate(result.changes, start=1) 中的 (index, change)，逐项执行循环体逻辑。
    for index, change in enumerate(result.changes, start=1):
        # Codex说明(自动生成)： 计算并保存 label，供后续语句继续读取或更新。
        label = _CHANGE_LABELS.get(change.change_type, change.change_type)
        # Codex说明(自动生成)： 调用 lines.append 更新列表或集合，把当前步骤产生的数据加入结果。
        lines.append(f"### {index}. {label}: {change.report_location}")
        # Codex说明(自动生成)： 检查条件 change.old_section，根据结果选择后续执行路径。
        if change.old_section:
            # Codex说明(自动生成)： 调用 lines.append 更新列表或集合，把当前步骤产生的数据加入结果。
            lines.append(
                f"- 旧位置: {change.old_section.location}（页 {change.old_section.page_range}）"
            )
        # Codex说明(自动生成)： 检查条件 change.new_section，根据结果选择后续执行路径。
        if change.new_section:
            # Codex说明(自动生成)： 调用 lines.append 更新列表或集合，把当前步骤产生的数据加入结果。
            lines.append(
                f"- 新位置: {change.new_section.location}（页 {change.new_section.page_range}）"
            )
        # Codex说明(自动生成)： 检查条件 change.old_section and change.new_section，根据结果选择后续执行路径。
        if change.old_section and change.new_section:
            # Codex说明(自动生成)： 调用 lines.append 更新列表或集合，把当前步骤产生的数据加入结果。
            lines.append(f"- 相似度: {change.similarity:.3f}")

        # Codex说明(自动生成)： 检查条件 change.replaced_snippets，根据结果选择后续执行路径。
        if change.replaced_snippets:
            # Codex说明(自动生成)： 调用 lines.append 更新列表或集合，把当前步骤产生的数据加入结果。
            lines.append("- 替换片段:")
            # Codex说明(自动生成)： 遍历 change.replaced_snippets 中的 pair，逐项执行循环体逻辑。
            for pair in change.replaced_snippets:
                # Codex说明(自动生成)： 调用 lines.append 更新列表或集合，把当前步骤产生的数据加入结果。
                lines.append(f"  - 旧: {pair.old}")
                # Codex说明(自动生成)： 调用 lines.append 更新列表或集合，把当前步骤产生的数据加入结果。
                lines.append(f"    新: {pair.new}")
        # Codex说明(自动生成)： 检查条件 change.added_snippets，根据结果选择后续执行路径。
        if change.added_snippets:
            # Codex说明(自动生成)： 调用 lines.append 更新列表或集合，把当前步骤产生的数据加入结果。
            lines.append("- 新增片段:")
            # Codex说明(自动生成)： 遍历 change.added_snippets 中的 snippet，逐项执行循环体逻辑。
            for snippet in change.added_snippets:
                # Codex说明(自动生成)： 调用 lines.append 更新列表或集合，把当前步骤产生的数据加入结果。
                lines.append(f"  - {snippet}")
        # Codex说明(自动生成)： 检查条件 change.removed_snippets，根据结果选择后续执行路径。
        if change.removed_snippets:
            # Codex说明(自动生成)： 调用 lines.append 更新列表或集合，把当前步骤产生的数据加入结果。
            lines.append("- 删除片段:")
            # Codex说明(自动生成)： 遍历 change.removed_snippets 中的 snippet，逐项执行循环体逻辑。
            for snippet in change.removed_snippets:
                # Codex说明(自动生成)： 调用 lines.append 更新列表或集合，把当前步骤产生的数据加入结果。
                lines.append(f"  - {snippet}")
        # Codex说明(自动生成)： 调用 lines.append 更新列表或集合，把当前步骤产生的数据加入结果。
        lines.append("")

    # Codex说明(自动生成)： 返回 '\n'.join(lines)，让调用方取得本函数的处理结果。
    return "\n".join(lines)


# Codex说明(自动生成)： 定义函数 _markdown_to_plain_text，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _markdown_to_plain_text(markdown: str) -> str:
    """Convert the Markdown report into a lightweight TXT version."""

    # Codex说明(自动生成)： 计算并保存 replacements，供后续语句继续读取或更新。
    replacements = {
        "# ": "",
        "## ": "",
        "### ": "",
        "`": "",
        "|": " ",
        "---": "",
    }
    # Codex说明(自动生成)： 计算并保存 text，供后续语句继续读取或更新。
    text = markdown
    # Codex说明(自动生成)： 遍历 replacements.items() 中的 (old, new)，逐项执行循环体逻辑。
    for old, new in replacements.items():
        # Codex说明(自动生成)： 计算并保存 text，供后续语句继续读取或更新。
        text = text.replace(old, new)
    # Codex说明(自动生成)： 计算并保存 lines，供后续语句继续读取或更新。
    lines = [line.rstrip() for line in text.splitlines()]
    # Codex说明(自动生成)： 返回 '\n'.join(lines).strip() + '\n'，让调用方取得本函数的处理结果。
    return "\n".join(lines).strip() + "\n"


# Codex说明(自动生成)： 定义函数 _rows_for_csv，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _rows_for_csv(changes: list[SectionChange]) -> list[dict[str, str]]:
    """Flatten section changes for spreadsheet review."""

    # Codex说明(自动生成)： 声明并保存 rows，同时保留类型信息方便维护和静态检查。
    rows: list[dict[str, str]] = []
    # Codex说明(自动生成)： 遍历 changes 中的 change，逐项执行循环体逻辑。
    for change in changes:
        # Codex说明(自动生成)： 计算并保存 replaced，供后续语句继续读取或更新。
        replaced = [
            f"旧: {pair.old} / 新: {pair.new}" for pair in change.replaced_snippets
        ]
        # Codex说明(自动生成)： 计算并保存 summary_parts，供后续语句继续读取或更新。
        summary_parts = []
        # Codex说明(自动生成)： 检查条件 change.replaced_snippets，根据结果选择后续执行路径。
        if change.replaced_snippets:
            # Codex说明(自动生成)： 调用 summary_parts.append 更新列表或集合，把当前步骤产生的数据加入结果。
            summary_parts.append(f"{len(change.replaced_snippets)} 处替换")
        # Codex说明(自动生成)： 检查条件 change.added_snippets，根据结果选择后续执行路径。
        if change.added_snippets:
            # Codex说明(自动生成)： 调用 summary_parts.append 更新列表或集合，把当前步骤产生的数据加入结果。
            summary_parts.append(f"{len(change.added_snippets)} 处新增")
        # Codex说明(自动生成)： 检查条件 change.removed_snippets，根据结果选择后续执行路径。
        if change.removed_snippets:
            # Codex说明(自动生成)： 调用 summary_parts.append 更新列表或集合，把当前步骤产生的数据加入结果。
            summary_parts.append(f"{len(change.removed_snippets)} 处删除")
        # Codex说明(自动生成)： 调用 rows.append 更新列表或集合，把当前步骤产生的数据加入结果。
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
    # Codex说明(自动生成)： 返回 rows，让调用方取得本函数的处理结果。
    return rows


# Codex说明(自动生成)： 定义函数 _change_counts，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _change_counts(changes: list[SectionChange]) -> dict[str, int]:
    """Count changes by type."""

    # Codex说明(自动生成)： 声明并保存 counts，同时保留类型信息方便维护和静态检查。
    counts: dict[str, int] = {}
    # Codex说明(自动生成)： 遍历 changes 中的 change，逐项执行循环体逻辑。
    for change in changes:
        # Codex说明(自动生成)： 更新 counts[change.change_type]，把当前配置或计算结果写入对应对象。
        counts[change.change_type] = counts.get(change.change_type, 0) + 1
    # Codex说明(自动生成)： 返回 counts，让调用方取得本函数的处理结果。
    return counts


# Codex说明(自动生成)： 定义函数 _section_to_dict，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _section_to_dict(section: Section) -> dict[str, object]:
    """Serialize section metadata for debug/audit output."""

    # Codex说明(自动生成)： 返回 {'section_id': section.section_id, 'heading': section.h...，让调用方取得本函数的处理结果。
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
