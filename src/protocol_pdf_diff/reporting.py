"""Report writers for protocol PDF comparison results."""

from __future__ import annotations

import csv
import difflib
import html as html_lib
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from datetime import datetime
from pathlib import Path

from .models import DiffOptions, DiffResult, Section, SectionChange, TableVisual
from .text_utils import (
    CHINESE_COUNT_UNIT_PATTERN,
    CHINESE_NUMBER_CHARS,
    canonicalize_chinese_number_token,
    compact_inline,
    parse_number_word_phrase,
    truncate,
)

_CHANGE_LABELS = {
    "added": "新增",
    "deleted": "删除",
    "modified": "修改",
    "unchanged": "未变化",
}

_INLINE_TOKEN_RE = re.compile(
    r"<=|>=|≤|≥|(?<!-)[<>](?!-)|="
    r"|[+-]?(?:\d+(?:\.\d+)?|\.\d+)\s*(?:x|×|\*)\s*10\s*[+-]?\d+"
    r"|[+-]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)(?:e[+-]?\d+)?"
    r"|[A-Za-zµμ]+[A-Za-z0-9µμ]*(?:[-_/][A-Za-z0-9µμ]+)*|[\u4e00-\u9fff]+",
    flags=re.I,
)
_INLINE_NUMBER_RE = re.compile(
    r"[+-]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)(?:e[+-]?\d+)?",
    flags=re.I,
)
_CHINESE_INLINE_NUMBER_RE = re.compile(
    rf"[{CHINESE_NUMBER_CHARS}]+(?=\s*(?:{CHINESE_COUNT_UNIT_PATTERN}))",
    flags=re.I,
)
_PROTECTED_NUMBER_WORD_PREFIXES = frozenset(
    {
        "appendix",
        "clause",
        "figure",
        "gen",
        "generation",
        "model",
        "part",
        "profile",
        "rev",
        "revision",
        "section",
        "table",
        "type",
    }
)
_PROTECTED_NUMBER_WORD_SUFFIXES = frozenset(
    {
        "csv",
        "dat",
        "doc",
        "docx",
        "html",
        "json",
        "pdf",
        "txt",
        "xls",
        "xlsx",
        "xml",
        "zip",
    }
)


@dataclass(frozen=True)
class _InlineToken:
    """A visible token used for noise-aware HTML highlighting."""

    text: str
    start: int
    end: int
    key: str


@dataclass(frozen=True)
class _TableVisualGroup:
    """One old/new logical table group, possibly spanning multiple PDF pages."""

    old_tables: tuple[TableVisual, ...]  # 旧版同一逻辑表格的一个或多个截图区域。
    new_tables: tuple[TableVisual, ...]  # 新版同一逻辑表格的一个或多个截图区域。


def write_reports(
    result: DiffResult,
    output_dir: str | Path,
    options: DiffOptions,
) -> dict[str, Path]:
    """Write Markdown, HTML, TXT, CSV, and JSON report artifacts.

    The HTML report is the most visual review surface: it groups changes by
    section, shows old/new snippets side by side, and highlights inline
    replacements. Markdown/TXT remain useful for copy-paste workflows, CSV is
    meant for filtering in Excel, and JSON preserves parsed section metadata for
    troubleshooting false positives or missed headings.
    """

    base_dir = Path(output_dir).expanduser().resolve()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = base_dir / f"protocol_diff_{timestamp}"
    report_dir.mkdir(parents=True, exist_ok=True)

    markdown = _render_markdown(result, options)
    html = _render_html(result, options)
    text = _markdown_to_plain_text(markdown)
    csv_rows = _rows_for_csv(result.changes)
    sections_payload = {
        "old_pdf": str(result.old_pdf),
        "new_pdf": str(result.new_pdf),
        "old_total_pages": _source_page_count(result, "old"),
        "new_total_pages": _source_page_count(result, "new"),
        "old_selected_pages": _selected_page_payload(result, "old"),
        "new_selected_pages": _selected_page_payload(result, "new"),
        "changes": [_change_to_dict(change) for change in result.changes],
        "old_sections": [_section_to_dict(section) for section in result.old_sections],
        "new_sections": [_section_to_dict(section) for section in result.new_sections],
        "old_table_visuals": [_table_visual_to_dict(table) for table in result.old_table_visuals],
        "new_table_visuals": [_table_visual_to_dict(table) for table in result.new_table_visuals],
        "warnings": result.warnings,
    }

    md_path = report_dir / "protocol_diff_report.md"
    html_path = report_dir / "protocol_diff_report.html"
    txt_path = report_dir / "protocol_diff_report.txt"
    csv_path = report_dir / "changes.csv"
    json_path = report_dir / "protocol_diff_data.json"

    md_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(html, encoding="utf-8")
    txt_path.write_text(text, encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "change_type",
                "report_location",
                "new_location",
                "old_location",
                "new_pages",
                "old_pages",
                "similarity",
                "summary",
                "added_snippets",
                "removed_snippets",
                "replaced_snippets",
                "omitted_snippet_count",
            ],
        )
        writer.writeheader()
        writer.writerows(csv_rows)
    json_path.write_text(json.dumps(sections_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "report_dir": report_dir,
        "markdown": md_path,
        "html": html_path,
        "text": txt_path,
        "csv": csv_path,
        "json": json_path,
    }


def _render_markdown(result: DiffResult, options: DiffOptions) -> str:
    """Render the main review report in Markdown."""

    counts = _change_counts(result.changes)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    comparison_note = _comparison_method_note(result)
    scope_note = _report_scope_note(options)
    lines: list[str] = [
        "# 协议 PDF 差异报告",
        "",
        f"- 旧协议: `{result.old_pdf}`",
        f"- 新协议: `{result.new_pdf}`",
        f"- 生成时间: {generated_at}",
        f"- 旧/新页数: {_source_page_count(result, 'old')} / {_source_page_count(result, 'new')}",
        f"- 旧选择页: {_selected_page_label(result, 'old')}",
        f"- 新选择页: {_selected_page_label(result, 'new')}",
        f"- 章节匹配阈值: {options.min_section_match_similarity:.2f}",
        f"- 未变化判定阈值: {options.unchanged_similarity:.3f}",
        f"- 比较方式: {comparison_note}",
        f"- 报告范围: {scope_note}",
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
        lines.extend([_empty_report_message(result), ""])
        return "\n".join(lines)

    for index, change in enumerate(result.changes, start=1):
        label = _CHANGE_LABELS.get(change.change_type, change.change_type)
        display_location = _display_change_location(change)
        lines.append(f"### {index}. {label}: {display_location}")
        if change.old_section:
            lines.append(
                f"- 旧位置: {_display_section_location(change.old_section, '旧')}（页 {change.old_section.page_range}）"
            )
        if change.new_section:
            lines.append(
                f"- 新位置: {_display_section_location(change.new_section, '新')}（页 {change.new_section.page_range}）"
            )
        if change.old_section and change.new_section:
            lines.append(f"- 相似度: {change.similarity:.3f}")
        summary = _change_summary(change)
        if summary:
            lines.append(f"- 差异摘要: {summary}")

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
        if change.omitted_snippet_count:
            lines.append(f"- {_omitted_snippet_message(change.omitted_snippet_count)}")
        lines.append("")

    return "\n".join(lines)


def _markdown_to_plain_text(markdown: str) -> str:
    """Convert the Markdown report into a readable plain-text review note.

    The Markdown report contains heading markers and table separators that are
    useful in a renderer but distracting in `.txt`. This converter keeps the
    semantic content, flattens small tables into aligned-ish rows, and avoids
    copying formatting artifacts such as `##` or `|---|---:|` into the text
    report that users may paste into email or chat.
    """

    lines: list[str] = []
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        if stripped.startswith("### "):
            lines.append(stripped[4:].replace("`", ""))
            continue
        if stripped.startswith("## "):
            lines.append(stripped[3:].replace("`", ""))
            continue
        if stripped.startswith("# "):
            lines.append(stripped[2:].replace("`", ""))
            continue
        if stripped.startswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if cells and all(set(cell) <= {"-", ":"} for cell in cells):
                continue
            lines.append("  ".join(cells))
            continue
        lines.append(line.replace("`", ""))

    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n"


def _render_html(result: DiffResult, options: DiffOptions) -> str:
    """Render an easy-to-scan standalone HTML review report."""

    counts = _change_counts(result.changes)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    comparison_note = _comparison_method_note(result)
    scope_note = _report_scope_note(options)
    title = "协议 PDF 差异报告"
    warning_html = ""
    if result.warnings:
        warning_items = "\n".join(
            f"<li>{_escape(warning)}</li>" for warning in result.warnings
        )
        warning_html = f"""
        <section class="warnings">
          <h2>抽取警告</h2>
          <ul>{warning_items}</ul>
        </section>
        """

    nav_items = "\n".join(
        _render_nav_item(index, change)
        for index, change in enumerate(result.changes, start=1)
    )
    if not nav_items:
        nav_items = f'<div class="empty-nav">{_escape(_empty_report_message(result))}</div>'

    change_cards = "\n".join(
        _render_change_html(index, change)
        for index, change in enumerate(result.changes, start=1)
    )
    if not change_cards:
        change_cards = f'<section class="empty-state">{_escape(_empty_report_message(result))}</section>'
    table_visual_html = _render_table_visuals_html(result)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #18202a;
      --muted: #647184;
      --line: #d8dee8;
      --add: #16794c;
      --add-bg: #e7f6ee;
      --del: #b3261e;
      --del-bg: #fdebea;
      --mod: #8a5a00;
      --mod-bg: #fff4d8;
      --blue: #255c99;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background: var(--bg);
      line-height: 1.55;
    }}
    header {{
      padding: 24px 32px;
      color: #fff;
      background: #1f3757;
    }}
    h1, h2, h3 {{ margin: 0; }}
    header p {{ margin: 8px 0 0; color: #dbe6f5; }}
    .layout {{
      display: grid;
      grid-template-columns: minmax(220px, 300px) minmax(0, 1fr);
      min-height: calc(100vh - 112px);
    }}
    aside {{
      border-right: 1px solid var(--line);
      background: #fff;
      padding: 18px;
      position: sticky;
      top: 0;
      height: 100vh;
      overflow: auto;
    }}
    main {{ padding: 22px; max-width: 1180px; width: 100%; }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(4, minmax(120px, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }}
    .metric {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }}
    .metric strong {{ display: block; font-size: 28px; line-height: 1.1; }}
    .metric span {{ color: var(--muted); font-size: 13px; }}
    .meta, .warnings, .change-card, .empty-state {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      margin-bottom: 16px;
      padding: 16px;
    }}
    .meta dl {{ display: grid; grid-template-columns: 84px minmax(0, 1fr); gap: 6px 12px; margin: 0; }}
    .meta dt {{ color: var(--muted); }}
    .meta dd {{ margin: 0; overflow-wrap: anywhere; }}
    .nav-title {{ color: var(--muted); font-size: 13px; margin-bottom: 10px; }}
    .nav-item {{
      display: grid;
      grid-template-columns: 28px minmax(0, 1fr);
      gap: 8px;
      align-items: start;
      color: var(--text);
      text-decoration: none;
      padding: 9px 8px;
      border-radius: 6px;
      margin-bottom: 4px;
      border-left: 4px solid transparent;
    }}
    .nav-item:hover {{ background: #eef3f8; }}
    .nav-item span {{
      color: var(--muted);
      font-variant-numeric: tabular-nums;
    }}
    .nav-body {{ min-width: 0; }}
    .nav-label {{
      display: inline-block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 2px;
    }}
    .nav-location {{ overflow-wrap: anywhere; }}
    .nav-added {{ border-left-color: var(--add); }}
    .nav-deleted {{ border-left-color: var(--del); }}
    .nav-modified {{ border-left-color: var(--mod); }}
    .nav-unchanged {{ border-left-color: var(--blue); }}
    .badge {{
      display: inline-block;
      border-radius: 999px;
      padding: 2px 10px;
      font-size: 13px;
      margin-right: 8px;
      border: 1px solid transparent;
    }}
    .badge-added {{ color: var(--add); background: var(--add-bg); border-color: #b8e5ce; }}
    .badge-deleted {{ color: var(--del); background: var(--del-bg); border-color: #f5c4c0; }}
    .badge-modified {{ color: var(--mod); background: var(--mod-bg); border-color: #f1d489; }}
    .badge-unchanged {{ color: var(--blue); background: #e7f0fb; border-color: #c5d8f0; }}
    .change-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: start;
      margin-bottom: 12px;
    }}
    .change-title {{ font-size: 18px; }}
    .pages {{ color: var(--muted); font-size: 13px; white-space: nowrap; }}
    .compare-grid {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 12px;
      margin-top: 12px;
    }}
    .pane {{
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: #fbfcfe;
    }}
    .pane h4 {{
      margin: 0;
      padding: 8px 10px;
      font-size: 13px;
      color: var(--muted);
      background: #edf1f6;
      border-bottom: 1px solid var(--line);
    }}
    .snippet {{ padding: 10px; white-space: pre-wrap; overflow-wrap: anywhere; }}
    mark {{ border-radius: 3px; padding: 0 2px; }}
    .ins {{ color: var(--add); background: var(--add-bg); }}
    .del {{ color: var(--del); background: var(--del-bg); text-decoration: line-through; }}
    .change-summary {{
      color: var(--muted);
      background: #f7f9fc;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      margin: 10px 0 12px;
    }}
    .single-list {{ margin: 10px 0 0 0; padding-left: 18px; }}
    .single-list li {{ margin: 6px 0; }}
    .omitted-note {{
      color: var(--muted);
      background: #f2f5f9;
      border: 1px dashed var(--line);
      border-radius: 8px;
      padding: 10px;
      margin-top: 12px;
    }}
    .table-visuals {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      margin-bottom: 16px;
      padding: 16px;
    }}
    .table-visual-card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      margin-top: 12px;
      background: #fbfcfe;
    }}
    .table-shot-grid {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 12px;
      margin-top: 10px;
    }}
    .table-shot {{
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: auto;
      background: #fff;
    }}
    .table-shot h4 {{
      margin: 0;
      padding: 8px 10px;
      color: var(--muted);
      background: #edf1f6;
      border-bottom: 1px solid var(--line);
      font-size: 13px;
    }}
    .table-shot img {{
      display: block;
      width: 100%;
      height: auto;
      background: #fff;
    }}
    .table-shot-page {{
      border-bottom: 1px solid var(--line);
    }}
    .table-shot-page:last-child {{
      border-bottom: 0;
    }}
    .table-shot-page-label {{
      padding: 7px 10px;
      color: var(--muted);
      font-size: 12px;
      background: #f7f9fc;
      border-bottom: 1px solid var(--line);
    }}
    .table-status {{
      color: var(--muted);
      font-size: 12px;
      margin: 6px 0 12px;
    }}
    .table-row-summary {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 10px;
      font-size: 13px;
    }}
    .table-row-summary th, .table-row-summary td {{
      border: 1px solid var(--line);
      padding: 7px 8px;
      vertical-align: top;
      text-align: left;
    }}
    .table-row-summary th {{ background: #edf1f6; color: var(--muted); }}
    .table-row-summary td:nth-child(2) {{ background: #fffafa; }}
    .table-row-summary td:nth-child(3) {{ background: #fbfffb; }}
    .table-kind {{
      display: inline-block;
      border-radius: 999px;
      padding: 2px 9px;
      font-size: 12px;
      white-space: nowrap;
      border: 1px solid var(--line);
    }}
    .table-kind-change {{ color: #b42318; background: #fff1f0; border-color: #ffccc7; }}
    .table-kind-same {{ color: #146c43; background: #eaf7ef; border-color: #b7e4c7; }}
    .table-kind-add {{ color: #1d4ed8; background: #eff6ff; border-color: #bfdbfe; }}
    .table-kind-del {{ color: #7a4b00; background: #fff7db; border-color: #f5d889; }}
    @media (max-width: 860px) {{
      .layout {{ grid-template-columns: 1fr; }}
      aside {{ position: static; height: auto; border-right: 0; border-bottom: 1px solid var(--line); }}
      main {{ padding: 14px; }}
      .summary, .compare-grid, .table-shot-grid {{ grid-template-columns: 1fr; }}
      .change-head {{ display: block; }}
      .pages {{ margin-top: 4px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>{title}</h1>
    <p>生成时间: {_escape(generated_at)} · 章节匹配阈值: {options.min_section_match_similarity:.2f}</p>
  </header>
  <div class="layout">
    <aside>
      <div class="nav-title">差异导航</div>
      {nav_items}
    </aside>
    <main>
      <section class="summary">
        <div class="metric"><strong>{counts.get("modified", 0)}</strong><span>修改</span></div>
        <div class="metric"><strong>{counts.get("added", 0)}</strong><span>新增</span></div>
        <div class="metric"><strong>{counts.get("deleted", 0)}</strong><span>删除</span></div>
        <div class="metric"><strong>{len(result.changes)}</strong><span>总差异</span></div>
      </section>
      <section class="meta">
        <dl>
          <dt>旧协议</dt><dd>{_escape(str(result.old_pdf))}</dd>
          <dt>新协议</dt><dd>{_escape(str(result.new_pdf))}</dd>
          <dt>旧/新页数</dt><dd>{_source_page_count(result, "old")} / {_source_page_count(result, "new")}</dd>
          <dt>旧选择页</dt><dd>{_escape(_selected_page_label(result, "old"))}</dd>
          <dt>新选择页</dt><dd>{_escape(_selected_page_label(result, "new"))}</dd>
          <dt>比较方式</dt><dd>{_escape(comparison_note)}</dd>
          <dt>报告范围</dt><dd>{_escape(scope_note)}</dd>
          <dt>提示</dt><dd>页码来自 PDF 抽取顺序；最终结论请回到源 PDF 复核。</dd>
        </dl>
      </section>
      {warning_html}
      {change_cards}
      {table_visual_html}
    </main>
  </div>
</body>
</html>
"""


def _render_change_html(index: int, change: SectionChange) -> str:
    """Render one change as a side-by-side HTML block."""

    label = _CHANGE_LABELS.get(change.change_type, change.change_type)
    old_pages = change.old_section.page_range if change.old_section else "-"
    new_pages = change.new_section.page_range if change.new_section else "-"
    similarity = (
        f" · 相似度 {change.similarity:.3f}"
        if change.old_section and change.new_section
        else ""
    )
    summary = _change_summary(change)
    summary_html = (
        f'<div class="change-summary">{_escape(summary)}</div>'
        if summary
        else ""
    )
    pairs = "\n".join(_render_pair_html(pair.old, pair.new) for pair in change.replaced_snippets)
    added = _render_single_list("新增片段", change.added_snippets, "ins")
    removed = _render_single_list("删除片段", change.removed_snippets, "del")
    omitted = _render_omitted_html(change.omitted_snippet_count)
    body = pairs or ""
    body += added
    body += removed
    body += omitted
    if not body:
        body = f'<p class="snippet">{_escape(_empty_change_message(change))}</p>'
    return f"""
      <section class="change-card" id="change-{index}">
        <div class="change-head">
          <h3 class="change-title"><span class="badge badge-{change.change_type}">{_escape(label)}</span>{_escape(_display_change_location(change))}</h3>
          <div class="pages">旧定位页 {_escape(old_pages)} · 新定位页 {_escape(new_pages)}{_escape(similarity)}</div>
        </div>
        {summary_html}
        {body}
      </section>
    """


def _render_table_visuals_html(result: DiffResult) -> str:
    """Render screenshot-backed table evidence without replacing text diffs."""

    if not result.old_table_visuals and not result.new_table_visuals:
        return ""
    groups = _paired_table_visuals(result.old_table_visuals, result.new_table_visuals)
    cards = "\n".join(
        _render_table_visual_group(index, group)
        for index, group in enumerate(groups, start=1)
    )
    return f"""
      <section class="table-visuals">
        <h2>表格截图识别</h2>
        <p class="change-summary">上方保留正文和章节差异；本区集中展示表格截图、网格证据和行级变化，方便直接回看表格内容。</p>
        {cards}
      </section>
    """


def _paired_table_visuals(
    old_tables: list[TableVisual],
    new_tables: list[TableVisual],
) -> list[_TableVisualGroup]:
    """Pair table visuals by logical table group without forcing weak matches."""

    old_unused = set(range(len(old_tables)))  # 未匹配旧表索引。
    new_unused = set(range(len(new_tables)))  # 未匹配新表索引。
    groups: list[_TableVisualGroup] = []  # 输出旧/新逻辑表格组。
    _pair_same_caption_table_groups(old_tables, new_tables, old_unused, new_unused, groups)
    scored: list[tuple[float, int, int]] = []  # 保存所有足够可信的候选配对。
    for old_index, old_table in enumerate(old_tables):
        if old_index not in old_unused:
            continue
        for new_index, new_table in enumerate(new_tables):
            if new_index not in new_unused:
                continue
            score = _table_visual_similarity(old_table, new_table)
            if score >= 0.65:
                scored.append((score, old_index, new_index))
    for _score, old_index, new_index in sorted(scored, reverse=True):
        if old_index not in old_unused or new_index not in new_unused:
            continue
        old_unused.remove(old_index)
        new_unused.remove(new_index)
        groups.append(_TableVisualGroup((old_tables[old_index],), (new_tables[new_index],)))
    for old_index in sorted(old_unused):
        groups.append(_TableVisualGroup((old_tables[old_index],), ()))
    for new_index in sorted(new_unused):
        groups.append(_TableVisualGroup((), (new_tables[new_index],)))
    return groups


def _pair_same_caption_table_groups(
    old_tables: list[TableVisual],
    new_tables: list[TableVisual],
    old_unused: set[int],
    new_unused: set[int],
    groups: list[_TableVisualGroup],
) -> None:
    """Pair exact same captions as whole cross-page table groups."""

    old_by_key = _table_visual_indexes_by_caption_key(old_tables)  # 旧版按表题分组，特别处理跨页同名表。
    new_by_key = _table_visual_indexes_by_caption_key(new_tables)  # 新版同样按表题分组，方便顺序配对。
    for key in sorted(old_by_key.keys() & new_by_key.keys()):
        old_indexes = [index for index in old_by_key[key] if index in old_unused]  # 过滤掉前面已匹配的旧表。
        new_indexes = [index for index in new_by_key[key] if index in new_unused]  # 过滤掉前面已匹配的新表。
        if not old_indexes and not new_indexes:
            continue
        for old_index in old_indexes:
            old_unused.remove(old_index)  # 同名旧表页全部归入一个逻辑表格组。
        for new_index in new_indexes:
            new_unused.remove(new_index)  # 同名新表页全部归入一个逻辑表格组。
        groups.append(
            _TableVisualGroup(
                tuple(old_tables[index] for index in old_indexes),
                tuple(new_tables[index] for index in new_indexes),
            )
        )  # Table 32-1 这种跨页表按整组展示，不再按页误报移动行。


def _table_visual_indexes_by_caption_key(tables: list[TableVisual]) -> dict[str, list[int]]:
    """Group table visuals by a strong exact caption key."""

    grouped: dict[str, list[int]] = {}  # key -> 按文档顺序出现的表格索引。
    current_key = ""  # 真实跨页表常只有首页带表题，续页需要继承最近的表题。
    current_page = -1  # 记录最近归组页码，避免跨很远页面误挂续页。
    for index, table in enumerate(tables):
        key = _table_visual_caption_key(table)  # 只有明确同名 Table 才走强配对。
        if key:
            current_key = key
            current_page = table.page_number
        elif table.is_continuation and current_key and table.page_number <= current_page + 1:
            key = current_key  # 无标题续页跟随前一张有标题表，避免跨页表被拆成多张卡片。
            current_page = table.page_number
        else:
            current_key = ""  # 遇到非续页无标题表时中断继承，避免误合并无关表格。
        if key:
            grouped.setdefault(key, []).append(index)
    return grouped


def _table_visual_caption_key(table: TableVisual) -> str:
    """Return an exact pairing key for repeated pages of the same named table."""

    title = compact_inline(table.title).casefold()  # 表题是跨页表格最稳定的人工可读身份。
    if not re.search(r"\btable\s+\d+(?:[-–]\d+)?\b|表\s*\d+", title):
        return ""  # 无编号表或续页缺表题时继续交给后面的保守 fuzzy 配对。
    return re.sub(r"\s+", " ", title).strip()


def _table_visual_similarity(old_table: TableVisual, new_table: TableVisual) -> float:
    """Score whether two screenshot table regions likely represent the same table."""

    old_key = _table_visual_identity(old_table)  # 标题和前几行共同构成旧表身份。
    new_key = _table_visual_identity(new_table)  # 新表同样使用标题和行文本。
    if not old_key or not new_key:
        return 0.0
    return difflib.SequenceMatcher(None, old_key, new_key, autojunk=False).ratio()


def _table_visual_identity(table: TableVisual) -> str:
    """Return a compact identity string for pairing table screenshots."""

    title = compact_inline(table.title).casefold()  # 表题通常是最强身份信号。
    rows = " ".join(compact_inline(row) for row in table.row_texts[:6])  # 前几行表头/参数名可辅助跨页配对。
    return compact_inline(f"{title} {rows}").casefold()


def _render_table_visual_group(
    index: int,
    group: _TableVisualGroup,
) -> str:
    """Render one old/new logical table group."""

    title = _table_group_title(index, group)
    old_shot = _render_table_shot_group("旧版截图", group.old_tables)
    new_shot = _render_table_shot_group("新版截图", group.new_tables)
    rows_html = _render_table_row_summary(group.old_tables, group.new_tables)
    return f"""
        <div class="table-visual-card">
          <h3>{_escape(title)}</h3>
          <div class="table-shot-grid">{old_shot}{new_shot}</div>
          {rows_html}
        </div>
    """


def _table_group_title(
    index: int,
    group: _TableVisualGroup,
) -> str:
    """Build a readable title for one visual table group."""

    table = _first_table_in_group(group)
    if table is None:
        return f"表格 {index}"
    title = table.title or ("跨页表格续段" if table.is_continuation else "")
    suffix = _table_group_page_suffix(group)
    if title:
        return f"{index}. {title}{suffix}"
    return f"{index}. 第 {table.page_number} 页表格 {table.table_number}{suffix}"


def _first_table_in_group(group: _TableVisualGroup) -> TableVisual | None:
    """Return the first available table from a visual group."""

    return next(iter(group.new_tables or group.old_tables), None)


def _table_group_page_suffix(group: _TableVisualGroup) -> str:
    """Return a compact page-count suffix for multipage visual groups."""

    old_count = len(group.old_tables)  # 旧版截图页数用于说明跨页表格。
    new_count = len(group.new_tables)  # 新版截图页数用于说明跨页表格。
    if old_count <= 1 and new_count <= 1:
        return ""
    return f"（旧 {old_count} 页 / 新 {new_count} 页）"


def _render_table_shot_group(label: str, tables: tuple[TableVisual, ...]) -> str:
    """Render one side of a table screenshot group."""

    if not tables:
        return f'<div class="table-shot"><h4>{_escape(label)}</h4><div class="snippet">无对应表格截图</div></div>'
    heading = f"{label} · {len(tables)} 页" if len(tables) > 1 else f"{label} · 页 {tables[0].page_number} · 表格 {tables[0].table_number}"
    pages = "".join(_render_one_table_shot_page(table) for table in tables)
    return f'<div class="table-shot"><h4>{_escape(heading)}</h4>{pages}</div>'


def _render_one_table_shot_page(table: TableVisual) -> str:
    """Render one table screenshot page inside a screenshot group."""

    caption = f"页 {table.page_number} · 表格 {table.table_number}"
    grid_summary = _display_table_grid_summary(table.grid_summary)
    image_html = (
        f'<img alt="{_escape(caption)}" src="{table.image_data_uri}">'
        if table.image_data_uri
        else '<div class="snippet">无截图：仅使用结构化表格行摘要</div>'
    )  # 文字表格兜底没有截图，避免渲染空图片。
    return (
        f'<div class="table-shot-page"><div class="table-shot-page-label">{_escape(caption)}</div>'
        f"{image_html}"
        f'<div class="snippet">{_escape(grid_summary)}</div></div>'
    )


def _display_table_grid_summary(summary: str) -> str:
    """Convert internal grid diagnostics into user-facing table evidence text."""

    match = re.search(r"横线\s*(\d+)\s*条，竖线\s*(\d+)\s*条", summary)
    if match:
        return f"表格网格：横线 {match.group(1)} 条，竖线 {match.group(2)} 条"
    if summary:
        return "表格网格：检测未完成"
    return "表格网格：未检测到稳定网格"


def _render_table_row_summary(
    old_tables: tuple[TableVisual, ...],
    new_tables: tuple[TableVisual, ...],
) -> str:
    """Render a compact structured summary from table rows."""

    old_rows = _table_group_rows(old_tables)  # 旧版同一逻辑表格的所有页按顺序合并。
    new_rows = _table_group_rows(new_tables)  # 新版同一逻辑表格的所有页按顺序合并。
    old_keys = [_table_row_display_key(row) for row in old_rows]
    new_keys = [_table_row_display_key(row) for row in new_rows]
    matcher = difflib.SequenceMatcher(None, old_keys, new_keys, autojunk=False)
    body_rows: list[str] = []  # 保存 HTML 表格行。
    include_equal_rows = max(len(old_rows), len(new_rows)) <= 8  # 小表格可显示未变化行，像人工审查表一样直观。
    diff_count = 0  # 用于判断是否需要补充小表格里的无变化行。
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal" and not include_equal_rows:
            continue
        if tag == "equal":
            for old_value, new_value in zip(old_rows[old_start:old_end], new_rows[new_start:new_end], strict=False):
                body_rows.append(_render_structured_table_summary_row(old_value, new_value, "无变化"))
            continue
        old_block = old_rows[old_start:old_end]
        new_block = new_rows[new_start:new_end]
        block_count = max(len(old_block), len(new_block))
        for offset in range(block_count):
            old_value = old_block[offset] if offset < len(old_block) else ""
            new_value = new_block[offset] if offset < len(new_block) else ""
            kind = _table_structured_diff_kind(old_value, new_value)
            body_rows.append(_render_structured_table_summary_row(old_value, new_value, kind))
            diff_count += 1
    if not body_rows or diff_count == 0:
        body_rows = ['<tr><td>未检测到行级变化</td><td></td><td></td><td></td></tr>']
    visible_rows = body_rows[:12]  # 表格摘要保持可读，超出的行数必须显式提示。
    omitted_count = max(0, len(body_rows) - len(visible_rows))  # 统计被折叠的表格行变化。
    omitted_note = (
        f'<div class="omitted-note">另有 {omitted_count} 行表格变化未展示；可结合上方截图复核完整表格。</div>'
        if omitted_count
        else ""
    )
    return (
        '<table class="table-row-summary"><thead><tr>'
        '<th>项目</th><th>旧版</th><th>新版</th><th>类型</th>'
        '</tr></thead><tbody>'
        + "\n".join(visible_rows)
        + "</tbody></table>"
        + omitted_note
    )


def _table_group_rows(tables: tuple[TableVisual, ...]) -> list[str]:
    """Return all structured rows from a table visual group."""

    rows: list[str] = []  # 保持 PDF 页顺序，跨页表格才能按整组比较。
    for table in tables:
        rows.extend(table.row_texts)
    return rows


def _render_structured_table_summary_row(old_row: str, new_row: str, kind: str) -> str:
    """Render one user-readable table-summary row."""

    item = _table_summary_item(old_row, new_row)
    old_display = _table_row_value_display(old_row)
    new_display = _table_row_value_display(new_row)
    class_name = _table_kind_class(kind)
    return (
        "<tr>"
        f"<td>{_escape(item)}</td>"
        f"<td>{_escape(old_display)}</td>"
        f"<td>{_escape(new_display)}</td>"
        f'<td><span class="table-kind {class_name}">{_escape(kind)}</span></td>'
        "</tr>"
    )


def _table_summary_item(old_row: str, new_row: str) -> str:
    """Return the left-column label for one structured table summary row."""

    fields = _table_row_fields(new_row) or _table_row_fields(old_row)  # 优先用新版字段名，旧版删除行则回退旧字段。
    label = _first_table_field(fields, ("parameter", "characteristic", "description", "label", "name"))  # 参数/特性名最适合给用户定位。
    if not label:
        label = _first_nonempty_table_cell(new_row or old_row)  # 无 Header=Value 时回退第一格文本。
    if _table_symbol_changed(old_row, new_row) and label and not re.search(r"(?i)\bsymbol\b", label):
        return f"{label} symbol"
    return label or "表格行"


def _table_row_value_display(row: str) -> str:
    """Return the old/new value cell shown in the structured summary."""

    if not row:
        return ""
    fields = _table_row_fields(row)
    if not fields:
        return " | ".join(_table_row_cells_for_display(row))
    parts: list[str] = []  # 用户更关心符号和值/单位，描述字段已放到项目列。
    symbol = fields.get("symbol")
    if symbol:
        parts.append(symbol)
    value_parts = [
        value
        for key in ("value", "values", "min", "minimum", "typ", "typical", "max", "maximum")
        if (value := fields.get(key))
    ]  # 数值列保留多个上下限，避免只显示 Symbol。
    unit = _first_table_field(fields, ("unit", "units"))
    if value_parts:
        value_text = " / ".join(value_parts)
        parts.append(f"{value_text} {unit}".strip() if unit else value_text)
    elif unit and not parts:
        parts.append(unit)
    if not parts:
        parts.extend(_non_identity_table_field_values(fields))
    return " | ".join(part for part in parts if part)


def _table_structured_diff_kind(old_row: str, new_row: str) -> str:
    """Classify one table summary row with a short review label."""

    if old_row and new_row and _table_row_display_key(old_row) == _table_row_display_key(new_row):
        return "无变化"
    if old_row and not new_row:
        return "旧表删除行"
    if new_row and not old_row:
        return "新表新增行"
    if _table_symbol_changed(old_row, new_row):
        return "实质/符号变化"
    if _table_numeric_value_changed(old_row, new_row):
        return "实质变化"
    return "替换/修改"


def _table_kind_class(kind: str) -> str:
    """Return a CSS class for a structured table summary kind."""

    if kind == "无变化":
        return "table-kind-same"
    if "删除" in kind:
        return "table-kind-del"
    if "新增" in kind:
        return "table-kind-add"
    return "table-kind-change"


def _table_row_fields(row: str) -> dict[str, str]:
    """Parse ``Header=Value`` cells from one structured table row."""

    fields: dict[str, str] = {}  # 小写字段名映射到原始显示值。
    for cell in _table_row_cells_for_display(row):
        if "=" not in cell:
            continue
        key, value = cell.split("=", 1)
        normalized_key = compact_inline(key).casefold()
        normalized_value = compact_inline(value)
        if normalized_key and normalized_value:
            fields[normalized_key] = normalized_value
    return fields


def _table_row_cells_for_display(row: str) -> list[str]:
    """Split a structured table row into display cells without the T-number prefix."""

    text = compact_inline(row)  # 表格行由抽取层统一使用管道分隔。
    if text.startswith("表格行:"):
        text = text[len("表格行:") :].strip()
    cells = [cell.strip() for cell in text.split("|") if cell.strip()]
    if cells and re.fullmatch(r"T\d+", cells[0], flags=re.I):
        return cells[1:]
    return cells


def _first_table_field(fields: dict[str, str], names: tuple[str, ...]) -> str:
    """Return the first populated field from a list of normalized names."""

    return next((fields[name] for name in names if fields.get(name)), "")


def _first_nonempty_table_cell(row: str) -> str:
    """Return the first useful cell from a structured table row."""

    for cell in _table_row_cells_for_display(row):
        if "=" in cell:
            _key, value = cell.split("=", 1)
            if value.strip():
                return compact_inline(value)
        elif cell:
            return compact_inline(cell)
    return ""


def _table_symbol_changed(old_row: str, new_row: str) -> bool:
    """Return True when the symbol column changed for the same table row."""

    if not old_row or not new_row:
        return False
    old_symbol = _table_row_fields(old_row).get("symbol", "")
    new_symbol = _table_row_fields(new_row).get("symbol", "")
    return bool(old_symbol and new_symbol and _table_row_display_key(old_symbol) != _table_row_display_key(new_symbol))


def _table_numeric_value_changed(old_row: str, new_row: str) -> bool:
    """Return True when value/min/max-style fields changed."""

    old_fields = _table_row_fields(old_row)  # 旧版字段用于提取数值列。
    new_fields = _table_row_fields(new_row)  # 新版字段用于提取数值列。
    value_keys = ("value", "values", "min", "minimum", "typ", "typical", "max", "maximum")  # 常见数值列名。
    for key in value_keys:
        old_value = old_fields.get(key, "")
        new_value = new_fields.get(key, "")
        if old_value and new_value and _table_row_display_key(old_value) != _table_row_display_key(new_value):
            return True
    return False


def _non_identity_table_field_values(fields: dict[str, str]) -> list[str]:
    """Return fallback values after removing identity/description fields."""

    skipped = {"parameter", "characteristic", "description", "label", "name"}  # 这些字段已经用于项目列。
    return [value for key, value in fields.items() if key not in skipped and value]


def _table_row_display_key(row: str) -> str:
    """Normalize one visual table row for row-level summary matching."""

    normalized = compact_inline(row).casefold()
    normalized = normalized.replace("µ", "u").replace("μ", "u")
    normalized = _normalize_table_row_math_text(normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _normalize_table_row_math_text(value: str) -> str:
    """Normalize table-row math notation for visual summary matching."""

    normalized = value.replace("−", "-").replace("–", "-").replace("—", " - ")  # 数学负号和破折号统一。
    normalized = re.sub(
        r"(?i)(?<![a-z])([+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*(?:x|×|\*)\s*10\s*([+-]?\d+)",
        r"\1e\2",
        normalized,
    )  # 5x10-6、5×10-6、5*10-6 在表格摘要中等价。
    normalized = re.sub(
        r"(?i)(?<=\d)\s*(?:x|×|\*)\s*(?=[a-z_])",
        " ",
        normalized,
    )  # 2xT_Vf 与 2×T_Vf 等价。
    normalized = re.sub(
        r"(?i)(?<=[a-z_])\s*(?:×|\*)\s*(?=[a-z0-9_])",
        " ",
        normalized,
    )  # fb*n 与 fb×n 等价。
    normalized = re.sub(r"(?<=[a-z])[-‐‑](?=[a-z])", "", normalized)  # 词内换行连字符不造成表格伪差异。
    return normalized


def _table_diff_kind(old_value: str, new_value: str) -> str:
    """Classify one visual table row diff for the summary column."""

    if old_value and new_value:
        return "替换/修改"
    if old_value:
        return "旧表删除行"
    return "新表新增行"


def _render_nav_item(index: int, change: SectionChange) -> str:
    """Render one left-navigation entry with an explicit change type label."""

    label = _CHANGE_LABELS.get(change.change_type, change.change_type)
    return (
        f'<a class="nav-item nav-{change.change_type}" href="#change-{index}">'
        f"<span>{index}</span>"
        '<div class="nav-body">'
        f'<div class="nav-label">{_escape(label)}</div>'
        f'<div class="nav-location">{_escape(_display_change_location(change))}</div>'
        "</div></a>"
    )


def _display_change_location(change: SectionChange) -> str:
    """Return a friendlier location label for human-facing reports."""

    if change.report_location == "范围起始页前序内容":
        section = change.new_section or change.old_section
        if section:
            side = "新" if change.new_section else "旧"
            return _display_section_location(section, side)
    return change.report_location


def _display_section_location(section: Section, side: str) -> str:
    """Return a friendly section location for reports."""

    if section.location == "范围起始页前序内容":
        return f"{side}选择范围第 {section.start_page} 页的章节前内容"
    return section.location


def _empty_change_message(change: SectionChange) -> str:
    """Explain why a change card has no snippet body."""

    if change.change_type == "unchanged":
        return "该章节未发现正文或标题变化。"
    if change.old_section and change.new_section:
        return "该章节发生变化，但当前报告只展示了有限数量的片段；可在工具设置中提高每章展示数量后重新生成。"
    return "该章节没有可展示的正文片段，请回到源 PDF 对应页复核。"


def _change_summary(change: SectionChange) -> str:
    """Summarize visible snippet counts and likely review focus."""

    parts: list[str] = []
    if change.replaced_snippets:
        parts.append(f"{len(change.replaced_snippets)} 处替换")
    if change.added_snippets:
        parts.append(f"{len(change.added_snippets)} 处新增")
    if change.removed_snippets:
        parts.append(f"{len(change.removed_snippets)} 处删除")
    if change.omitted_snippet_count:
        parts.append(f"{change.omitted_snippet_count} 处未展示")

    natures = _change_natures(change)
    if natures:
        parts.append("重点关注: " + "、".join(natures))
    return "；".join(parts)


def _change_natures(change: SectionChange) -> list[str]:
    """Infer high-level review categories from displayed snippets."""

    texts: list[str] = []
    for pair in change.replaced_snippets:
        texts.extend([pair.old, pair.new])
    texts.extend(change.added_snippets)
    texts.extend(change.removed_snippets)
    joined = "\n".join(texts)
    natures: list[str] = []
    if re.search(r"(?i)\b(?:step|steps)\s+\d+|\b\d+\s+through\s+\d+\b", joined):
        natures.append("步骤编号/步骤范围")
    if re.search(r"(?i)\b(?:appendix|section)\s+[A-Z0-9]+(?:\.\d+)*\b", joined):
        natures.append("附录/章节引用")
    if re.search(r"(?i)\b(?:<=|>=|≤|≥|<|>|=|[+-]?\d+(?:\.\d+)?\s*(?:ps|mv|db|gt/s|mhz|ghz|ui|%))\b", joined):
        natures.append("数值或限值")
    if re.search(r"(?i)\b(?:revision|cem|clb|cbb|preset|template|sigtest)\b", joined):
        natures.append("术语/设备/模板")
    return natures[:4]


def _render_pair_html(old_text: str, new_text: str) -> str:
    """Render old/new replacement snippets with inline highlighting."""

    old_html, new_html = _inline_diff_html(old_text, new_text)
    return f"""
        <div class="compare-grid">
          <div class="pane">
            <h4>旧协议</h4>
            <div class="snippet">{old_html}</div>
          </div>
          <div class="pane">
            <h4>新协议</h4>
            <div class="snippet">{new_html}</div>
          </div>
        </div>
    """


def _render_single_list(title: str, snippets: list[str], css_class: str) -> str:
    """Render added-only or removed-only snippets."""

    if not snippets:
        return ""
    items = "\n".join(
        f'<li><mark class="{css_class}">{_escape(snippet)}</mark></li>'
        for snippet in snippets
    )
    return f"<h4>{title}</h4><ul class=\"single-list\">{items}</ul>"


def _render_omitted_html(omitted_count: int) -> str:
    """Render an explicit note when snippet limiting hides additional deltas."""

    if omitted_count <= 0:
        return ""
    return f'<div class="omitted-note">{_escape(_omitted_snippet_message(omitted_count))}</div>'


def _omitted_snippet_message(omitted_count: int) -> str:
    """Explain that more substantive differences exist than are displayed."""

    return f"另有 {omitted_count} 条差异片段未展示；完整章节已比较，可在工具设置中提高每章展示数量后重新生成。"


def _inline_diff_html(old_text: str, new_text: str) -> tuple[str, str]:
    """Highlight substantive token changes without emphasizing PDF noise."""

    old_tokens = _inline_tokens(old_text)
    new_tokens = _inline_tokens(new_text)
    matcher = difflib.SequenceMatcher(
        None,
        [token.key for token in old_tokens],
        [token.key for token in new_tokens],
        autojunk=False,
    )
    old_highlights: list[tuple[int, int, str]] = []
    new_highlights: list[tuple[int, int, str]] = []

    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        old_changed = old_tokens[old_start:old_end]
        new_changed = new_tokens[new_start:new_end]
        old_highlights.extend((token.start, token.end, "del") for token in old_changed)
        new_highlights.extend((token.start, token.end, "ins") for token in new_changed)

    return (
        _render_text_with_highlights(old_text, old_highlights),
        _render_text_with_highlights(new_text, new_highlights),
    )


def _inline_tokens(text: str) -> list[_InlineToken]:
    """Tokenize visible text and compute noise-tolerant keys for highlighting."""

    raw_tokens: list[tuple[str, int, int]] = []
    for match in _INLINE_TOKEN_RE.finditer(text):
        if _is_arrow_operator_noise(text, match.start(), match.end()):
            continue
        raw_tokens.extend(
            _expand_chinese_inline_token(
                text[match.start() : match.end()],
                match.start(),
            )
        )

    tokens: list[_InlineToken] = []
    raw_words = [_inline_context_word(raw) for raw, _start, _end in raw_tokens]
    index = 0
    while index < len(raw_tokens):
        raw, start, end = raw_tokens[index]
        parsed = parse_number_word_phrase(raw_words, index)
        if parsed:
            number_key, consumed = parsed
            previous_word = raw_words[index - 1] if index > 0 else ""
            next_index = index + consumed
            next_word = raw_words[next_index] if next_index < len(raw_words) else ""
            if (
                previous_word not in _PROTECTED_NUMBER_WORD_PREFIXES
                and next_word not in _PROTECTED_NUMBER_WORD_SUFFIXES
            ):
                phrase_end = raw_tokens[index + consumed - 1][2]
                tokens.append(
                    _InlineToken(
                        text=text[start:phrase_end],
                        start=start,
                        end=phrase_end,
                        key=number_key,
                    )
                )
                index += consumed
                continue
        key = _inline_token_key(raw, text, start, end)
        if key:
            tokens.append(_InlineToken(text=raw, start=start, end=end, key=key))
        index += 1
    return tokens


def _is_arrow_operator_noise(text: str, start: int, end: int) -> bool:
    """Return True when ``<``/``>`` is part of an extracted arrow, not a limit."""

    token = text[start:end]
    if token not in {"<", ">"}:
        return False
    left = text[max(0, start - 3) : start]
    right = text[end : min(len(text), end + 3)]
    return bool(re.search(r"-\s*$", left) or re.match(r"^\s*-", right))


def _expand_chinese_inline_token(raw: str, start: int) -> list[tuple[str, int, int]]:
    """Split Chinese count numerals out of a larger Chinese token.

    PDF text extraction often returns ``供应商应捕获七个波形`` as one token while
    ``供应商应捕获 7 个波形`` becomes three tokens. Splitting only clear numeric
    spans lets the highlighter align ``七`` and ``7`` without over-tokenizing all
    Chinese prose.
    """

    if not raw or not re.fullmatch(r"[\u4e00-\u9fff]+", raw):
        return [(raw, start, start + len(raw))]

    expanded: list[tuple[str, int, int]] = []
    cursor = 0
    for match in _CHINESE_INLINE_NUMBER_RE.finditer(raw):
        if canonicalize_chinese_number_token(match.group(0)) is None:
            continue
        if match.start() > cursor:
            expanded.append((raw[cursor : match.start()], start + cursor, start + match.start()))
        expanded.append((match.group(0), start + match.start(), start + match.end()))
        cursor = match.end()
    if not expanded:
        return [(raw, start, start + len(raw))]
    if cursor < len(raw):
        expanded.append((raw[cursor:], start + cursor, start + len(raw)))
    return expanded


def _inline_token_key(token: str, source_text: str, start: int, end: int) -> str:
    """Normalize one token for display highlighting, not comparison semantics."""

    normalized = _inline_context_word(token).replace("µ", "u").replace("μ", "u")
    normalized = normalized.replace("≤", "<=").replace("≥", ">=")
    normalized = _normalize_inline_math_token(normalized, source_text, start)
    normalized = re.sub(r"(?<=[a-z])[-‐‑](?=[a-z])", "", normalized)
    normalized = re.sub(r"\bpreset\s*([0-9]+)\b", r"p\1", normalized)
    chinese_number = _contextual_chinese_number_key(normalized, source_text, start, end)
    if chinese_number is not None:
        return chinese_number
    if _INLINE_NUMBER_RE.fullmatch(normalized):
        return _canonical_inline_number(normalized)
    return normalized


def _normalize_inline_math_token(token: str, source_text: str, start: int) -> str:
    """Normalize visual-only multiplication/exponent token variants."""

    normalized = token.replace("−", "-").replace("–", "-")  # 数学负号和短横线都归一为 ASCII hyphen。
    normalized = re.sub(
        r"(?i)^([+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*(?:x|×|\*)\s*10\s*([+-]?\d+)$",
        r"\1e\2",
        normalized,
    )  # 5x10-6、5×10-6、5*10-6 在高亮层等价。
    if normalized.startswith("x") and start > 0 and source_text[start - 1 : start].isdigit():
        normalized = normalized[1:]  # 2xT_Vf 抽成 token xT_Vf 时，去掉作为乘号的 x。
    return normalized


def _contextual_chinese_number_key(
    normalized_token: str,
    source_text: str,
    start: int,
    end: int,
) -> str | None:
    """Canonicalize Chinese numerals only in explicit count/ordinal contexts."""

    value = canonicalize_chinese_number_token(normalized_token)
    if value is None:
        return None

    before = source_text[max(0, start - 8) : start]
    after = source_text[end : min(len(source_text), end + 32)]
    if re.match(rf"^\s*(?:{CHINESE_COUNT_UNIT_PATTERN})", after, flags=re.I):
        return value
    if re.search(r"第\s*$", before) and re.match(r"^\s*[章节条项部分]", after):
        return value
    return None


def _inline_context_word(token: str) -> str:
    """Normalize one inline token for context checks."""

    return token.casefold().replace("‐", "-").replace("‑", "-")


def _canonical_inline_number(token: str) -> str:
    """Normalize visually different but numerically equal values for highlighting."""

    sign = "-" if token.startswith("-") else ""
    body = token.lstrip("+-")
    if body.startswith("."):
        body = f"0{body}"
    try:
        value = Decimal(body.replace(",", ""))
    except InvalidOperation:
        return token
    if value == 0:
        sign = ""
    numeric = format(value.normalize(), "f")
    if "." in numeric:
        numeric = numeric.rstrip("0").rstrip(".")
    return f"{sign}{numeric}"


def _render_text_with_highlights(text: str, highlights: list[tuple[int, int, str]]) -> str:
    """Render text with non-overlapping token highlights."""

    parts: list[str] = []
    cursor = 0
    for start, end, css_class in sorted(highlights):
        if start < cursor:
            continue
        parts.append(_escape(text[cursor:start]))
        parts.append(f'<mark class="{css_class}">{_escape(text[start:end])}</mark>')
        cursor = end
    parts.append(_escape(text[cursor:]))
    return "".join(parts)


def _escape(value: object) -> str:
    """HTML-escape values while preserving readable line breaks."""

    return html_lib.escape(str(value), quote=True)


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
        if change.omitted_snippet_count:
            summary_parts.append(f"{change.omitted_snippet_count} 处未展示")
        natures = _change_natures(change)
        if natures:
            summary_parts.append("重点关注: " + "、".join(natures))
        rows.append(
            {
                "change_type": _CHANGE_LABELS.get(change.change_type, change.change_type),
                "report_location": _display_change_location(change),
                "new_location": (
                    _display_section_location(change.new_section, "新")
                    if change.new_section
                    else ""
                ),
                "old_location": (
                    _display_section_location(change.old_section, "旧")
                    if change.old_section
                    else ""
                ),
                "new_pages": change.new_section.page_range if change.new_section else "",
                "old_pages": change.old_section.page_range if change.old_section else "",
                "similarity": f"{change.similarity:.3f}" if change.old_section and change.new_section else "",
                "summary": "；".join(summary_parts),
                "added_snippets": "\n".join(change.added_snippets),
                "removed_snippets": "\n".join(change.removed_snippets),
                "replaced_snippets": "\n".join(replaced),
                "omitted_snippet_count": str(change.omitted_snippet_count),
            }
        )
    return rows


def _change_counts(changes: list[SectionChange]) -> dict[str, int]:
    """Count changes by type."""

    counts: dict[str, int] = {}
    for change in changes:
        counts[change.change_type] = counts.get(change.change_type, 0) + 1
    return counts


def _comparison_method_note(result: DiffResult) -> str:
    """Describe the matching strategy used for this report."""

    fallback_count = _page_fallback_section_count(result)
    total_sections = len(result.old_sections) + len(result.new_sections)
    if fallback_count and fallback_count == total_sections:
        return "未识别到稳定章节，已退回按页块和正文相似度比较；页码用于定位，不作为唯一匹配依据。"
    if fallback_count:
        return "至少一份 PDF 未识别到稳定章节，已混合使用章节、页块和正文相似度比较；页码用于定位，不作为唯一匹配依据。"
    return "按章节编号、标题和正文相似度匹配；页码只用于定位，不用于直接对齐。"


def _report_scope_note(options: DiffOptions) -> str:
    """Explain output boundaries that matter during protocol review."""

    return (
        "主要比较 PDF 中可抽取文字，表格会额外提供截图辅助复核；图片、印章、普通矢量图等其它视觉元素不比较；"
        "重复页眉页脚和动态页码会尽量过滤；"
        f"每个章节最多展示 {options.max_snippets_per_section} 条差异片段，完整章节仍会参与匹配和比较。"
    )


def _empty_report_message(result: DiffResult) -> str:
    """Return an empty-state message that matches the active comparison mode."""

    if _page_fallback_section_count(result):
        return "未发现章节/文本块级差异。"
    return "未发现章节级差异。"


def _page_fallback_section_count(result: DiffResult) -> int:
    """Count synthetic no-heading page chunks across both documents."""

    return sum(
        1
        for section in result.old_sections + result.new_sections
        if section.section_id.startswith("P")
    )


def _page_count(sections: list[Section]) -> int:
    """Estimate total document pages from extracted section metadata."""

    if not sections:
        return 0
    return max(section.end_page for section in sections)


def _source_page_count(result: DiffResult, side: str) -> int:
    """Return the source PDF page count for one side of the comparison."""

    if side == "old":
        return result.old_total_pages or _page_count(result.old_sections)
    return result.new_total_pages or _page_count(result.new_sections)


def _selected_pages(result: DiffResult, side: str) -> tuple[int | None, int | None]:
    """Return the one-based inclusive selected source page range."""

    if side == "old":
        start = result.old_selected_start_page
        end = result.old_selected_end_page
        sections = result.old_sections
    else:
        start = result.new_selected_start_page
        end = result.new_selected_end_page
        sections = result.new_sections
    if start is not None and end is not None:
        return start, end
    if not sections:
        return None, None
    return min(section.start_page for section in sections), max(section.end_page for section in sections)


def _selected_page_label(result: DiffResult, side: str) -> str:
    """Format the selected page range for human-facing reports."""

    start, end = _selected_pages(result, side)
    total_pages = _source_page_count(result, side)
    if start is None or end is None:
        return "无可比较页"
    if total_pages and start == 1 and end == total_pages:
        return f"全部 (1-{total_pages})" if total_pages > 1 else "全部 (1)"
    if start == end:
        return str(start)
    return f"{start}-{end}"


def _selected_page_payload(result: DiffResult, side: str) -> dict[str, object]:
    """Serialize selected-page metadata for the JSON audit file."""

    start, end = _selected_pages(result, side)
    total_pages = _source_page_count(result, side)
    return {
        "start_page": start,
        "end_page": end,
        "label": _selected_page_label(result, side),
        "is_full_document": bool(total_pages and start == 1 and end == total_pages),
    }


def _change_to_dict(change: SectionChange) -> dict[str, object]:
    """Serialize one user-facing section change for machine-readable reports."""

    return {
        "change_type": change.change_type,
        "change_label": _CHANGE_LABELS.get(change.change_type, change.change_type),
        "report_location": change.report_location,
        "display_report_location": _display_change_location(change),
        "old_location": change.old_section.location if change.old_section else None,
        "new_location": change.new_section.location if change.new_section else None,
        "display_old_location": (
            _display_section_location(change.old_section, "旧")
            if change.old_section
            else None
        ),
        "display_new_location": (
            _display_section_location(change.new_section, "新")
            if change.new_section
            else None
        ),
        "old_pages": change.old_section.page_range if change.old_section else None,
        "new_pages": change.new_section.page_range if change.new_section else None,
        "similarity": round(change.similarity, 6),
        "summary": _change_summary(change),
        "change_nature": _change_natures(change),
        "added_snippets": list(change.added_snippets),
        "removed_snippets": list(change.removed_snippets),
        "replaced_snippets": [
            {"old": pair.old, "new": pair.new} for pair in change.replaced_snippets
        ],
        "omitted_snippet_count": change.omitted_snippet_count,
    }


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


def _table_visual_to_dict(table: TableVisual) -> dict[str, object]:
    """Serialize table visual metadata without duplicating huge image payloads."""

    return {
        "page_number": table.page_number,
        "table_number": table.table_number,
        "title": table.title,
        "bbox": list(table.bbox),
        "row_texts": list(table.row_texts),
        "grid_summary": table.grid_summary,
        "ocr_status": table.ocr_status,
        "ocr_text_preview": truncate(compact_inline(table.ocr_text), 500),
        "has_embedded_image": bool(table.image_data_uri),
        "is_continuation": table.is_continuation,
    }
