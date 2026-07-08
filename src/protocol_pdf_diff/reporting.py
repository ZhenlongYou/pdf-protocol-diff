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

from .models import DiffOptions, DiffResult, LayoutRegion, RegionChange, Section, SectionChange, TableVisual
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
        "region_changes": [_region_change_to_dict(change) for change in result.region_changes],
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

    counts = _change_counts(result.changes)  # 统计新增、删除、修改数量，供首屏指标卡使用。
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # 记录报告生成时间，方便用户区分多次输出。
    comparison_note = _comparison_method_note(result)  # 说明章节匹配策略，避免用户误以为只是按页对齐。
    scope_note = _report_scope_note(options)  # 说明本报告覆盖范围，帮助用户理解哪些视觉元素不会进入正文 diff。
    table_visual_count = (
        len(_reportable_table_visuals(result.old_table_visuals))
        + len(_reportable_table_visuals(result.new_table_visuals))
    )  # 首页指标只统计最终会展示的真实表格截图，不把曲线图误识别计入。
    title = "协议 PDF 差异报告"  # HTML 标题和页面主标题保持一致。
    warning_html = ""  # 默认没有抽取警告区域，只有真正存在警告时才渲染。
    if result.warnings:  # 有警告时提前展示，避免用户忽略 PDF 抽取质量问题。
        warning_items = "\n".join(  # 每条警告独立成行，便于手机端阅读。
            f"<li>{_escape(warning)}</li>" for warning in result.warnings
        )
        warning_html = f"""
        <section class="card warnings">
          <h2>抽取警告</h2>
          <ul>{warning_items}</ul>
        </section>
        """

    review_focus_html = _render_review_focus_html(result)  # 自动排序重点变化，让用户先看最可能影响结论的条目。
    noise_html = _render_noise_notes_html(result)  # 汇总可降权噪声，减少用户被格式差异误导。
    region_changes_html = _render_region_changes_html(result)  # layout-aware 区域 diff 优先展示表格、公式和图片截图。
    table_visual_html = _render_table_visuals_html(result)  # 传统表格截图始终保留，避免 layout 区域不完整时丢失复核证据。
    change_nav_html = _render_change_navigation_html(result.changes)  # 正文差异导航放在明细前，不再占用整页侧栏。
    change_cards = "\n".join(  # 正文明细仍完整保留，供用户从重点项继续下钻。
        _render_change_html(index, change)
        for index, change in enumerate(result.changes, start=1)
    )
    if not change_cards:  # 没有正文变化时给出明确空状态。
        change_cards = f'<section class="card empty-state">{_escape(_empty_report_message(result))}</section>'

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
      --real: #b3261e;
      --real-bg: #fdebea;
      --format: #8a5a00;
      --format-bg: #fff4d8;
      --reflow: #255c99;
      --reflow-bg: #e7f0fb;
      --same: #16794c;
      --same-bg: #e7f6ee;
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
      padding: 26px 34px;
      color: #fff;
      background: #1f3757;
    }}
    h1, h2, h3 {{ margin: 0; }}
    header p {{ margin: 8px 0 0; color: #dbe6f5; }}
    main {{ max-width: 1320px; margin: auto; padding: 22px; }}
    .summary, .grid4 {{
      display: grid;
      grid-template-columns: repeat(4, minmax(120px, 1fr));
      gap: 12px;
    }}
    .card, .metric {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      margin: 16px 0;
      padding: 16px;
      box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04);
    }}
    .metric {{
      padding: 14px;
      margin: 0;
    }}
    .metric strong {{ display: block; font-size: 28px; line-height: 1.1; }}
    .metric span {{ color: var(--muted); font-size: 13px; }}
    .meta dl {{ display: grid; grid-template-columns: 84px minmax(0, 1fr); gap: 6px 12px; margin: 0; }}
    .meta dt {{ color: var(--muted); }}
    .meta dd {{ margin: 0; overflow-wrap: anywhere; }}
    .muted {{ color: var(--muted); }}
    .focus-list {{ margin: 10px 0 0; padding-left: 22px; }}
    .focus-list li {{ margin: 8px 0; }}
    .noise-list {{ margin: 10px 0 0; padding-left: 18px; }}
    .noise-list li {{ margin: 7px 0; }}
    .toc a {{
      display: inline-block;
      margin: 5px 8px 5px 0;
      color: var(--blue);
      text-decoration: none;
      border: 1px solid #c5d8f0;
      background: #eef5ff;
      border-radius: 999px;
      padding: 5px 10px;
      font-size: 13px;
    }}
    .toc a:hover {{ background: #dcecff; }}
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
    .tag {{
      display: inline-block;
      color: var(--blue);
      background: #e7f0fb;
      border: 1px solid #c5d8f0;
      border-radius: 999px;
      padding: 2px 10px;
      font-size: 13px;
      margin-right: 8px;
    }}
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
    .verdict {{
      margin: 12px 0;
      padding: 10px 12px;
      border-radius: 8px;
      background: #f8fafc;
      border: 1px solid #e2e8f0;
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
      margin-bottom: 16px;
    }}
    .severity-high {{ border-left: 6px solid var(--real); }}
    .severity-medium {{ border-left: 6px solid var(--format); }}
    .severity-low {{ border-left: 6px solid var(--blue); }}
    .table-shot-grid, .shots {{
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
    .old {{ background: #fffafa; }}
    .new {{ background: #fbfffb; }}
    .kind {{
      white-space: nowrap;
      font-size: 12px;
      border-radius: 999px;
      padding: 2px 8px;
      border: 1px solid transparent;
      display: inline-block;
    }}
    .kind-real {{ color: var(--real); background: var(--real-bg); border-color: #f5c4c0; }}
    .kind-format {{ color: var(--format); background: var(--format-bg); border-color: #f1d489; }}
    .kind-reflow {{ color: var(--reflow); background: var(--reflow-bg); border-color: #c5d8f0; }}
    .kind-same {{ color: var(--same); background: var(--same-bg); border-color: #b8e5ce; }}
    .detail-heading {{ margin: 24px 0 8px; }}
    .region-toolbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }}
    .filter-btn {{
      border: 1px solid #c5d8f0;
      background: #eef5ff;
      color: var(--blue);
      border-radius: 999px;
      padding: 6px 11px;
      cursor: pointer;
      font: inherit;
      font-size: 13px;
    }}
    .filter-btn:hover {{ background: #dcecff; }}
    .region-card[data-region-type="table"] {{ border-left: 6px solid #255c99; }}
    .region-card[data-region-type="formula"] {{ border-left: 6px solid #8a5a00; }}
    .region-card[data-region-type="figure"] {{ border-left: 6px solid #16794c; }}
    .region-card[data-region-type="paragraph"], .region-card[data-region-type="heading"] {{ border-left: 6px solid #647184; }}
    .region-meta {{
      color: var(--muted);
      font-size: 13px;
      margin: 6px 0 10px;
    }}
    .region-type {{
      display: inline-block;
      min-width: 82px;
      color: #fff;
      background: #334155;
      border-radius: 999px;
      padding: 2px 9px;
      font-size: 12px;
      text-align: center;
      margin-right: 8px;
    }}
    .visual-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      margin-top: 10px;
    }}
    .visual-panel {{
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: auto;
      background: #fff;
    }}
    .visual-panel h4 {{
      margin: 0;
      padding: 8px 10px;
      font-size: 13px;
      color: var(--muted);
      background: #edf1f6;
      border-bottom: 1px solid var(--line);
    }}
    .visual-panel img {{
      display: block;
      width: 100%;
      height: auto;
      background: #fff;
    }}
    @media (max-width: 860px) {{
      main {{ padding: 14px; }}
      .summary, .grid4, .compare-grid, .table-shot-grid, .shots, .visual-grid {{ grid-template-columns: 1fr; }}
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
  <main>
    <section class="summary grid4">
      <div class="metric"><strong>{counts.get("modified", 0)}</strong><span>修改</span></div>
      <div class="metric"><strong>{counts.get("added", 0)}</strong><span>新增</span></div>
      <div class="metric"><strong>{counts.get("deleted", 0)}</strong><span>删除</span></div>
      <div class="metric"><strong>{table_visual_count}</strong><span>表格截图</span></div>
    </section>
    <section class="card meta">
      <dl>
        <dt>旧协议</dt><dd>{_escape(str(result.old_pdf))}</dd>
        <dt>新协议</dt><dd>{_escape(str(result.new_pdf))}</dd>
        <dt>旧/新页数</dt><dd>{_source_page_count(result, "old")} / {_source_page_count(result, "new")}</dd>
        <dt>旧选择页</dt><dd>{_escape(_selected_page_label(result, "old"))}</dd>
        <dt>新选择页</dt><dd>{_escape(_selected_page_label(result, "new"))}</dd>
        <dt>比较方式</dt><dd>{_escape(comparison_note)}</dd>
        <dt>报告范围</dt><dd>{_escape(scope_note)}</dd>
        <dt>提示</dt><dd>先看优先复核和表格截图，再按需展开正文明细；页码来自 PDF 抽取顺序。</dd>
      </dl>
    </section>
    {warning_html}
    {review_focus_html}
    {noise_html}
    {region_changes_html}
    {table_visual_html}
    {change_nav_html}
    <h2 class="detail-heading">正文差异明细</h2>
    {change_cards}
  </main>
  <script>
    document.querySelectorAll('[data-filter]').forEach((button) => {{
      button.addEventListener('click', () => {{
        const filter = button.getAttribute('data-filter');
        document.querySelectorAll('.region-card').forEach((card) => {{
          const type = card.getAttribute('data-region-type');
          card.style.display = filter === 'all' || type === filter ? '' : 'none';
        }});
      }});
    }});
  </script>
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


def _render_region_changes_html(result: DiffResult) -> str:
    """Render layout-aware region differences before legacy text details."""

    if not result.region_changes:
        return ""
    counts = _region_type_counts(result.region_changes)  # 统计每类 region 差异，帮助用户判断报告重点。
    summary = " · ".join(
        f"{_region_type_label(region_type)} {count}"
        for region_type, count in counts.items()
        if count
    )  # 只展示出现过的类型。
    cards = "\n".join(
        _render_region_change_card(index, change)
        for index, change in enumerate(result.region_changes, start=1)
    )  # region card 按页码和阅读顺序排列。
    return f"""
      <section class="card">
        <h2>Layout-aware 区域差异</h2>
        <p class="change-summary">按 paragraph / heading / table / formula / figure 分区比较。表格、公式和图片使用截图视觉对比，正文使用段落级 diff。{_escape(summary)}</p>
        <div class="region-toolbar" aria-label="region filters">
          <button class="filter-btn" type="button" data-filter="all">全部</button>
          <button class="filter-btn" type="button" data-filter="paragraph">only text changes</button>
          <button class="filter-btn" type="button" data-filter="heading">only heading changes</button>
          <button class="filter-btn" type="button" data-filter="table">only table changes</button>
          <button class="filter-btn" type="button" data-filter="formula">only formula changes</button>
          <button class="filter-btn" type="button" data-filter="figure">only figure changes</button>
        </div>
      </section>
      {cards}
    """


def _render_region_change_card(index: int, change: RegionChange) -> str:
    """Render one layout-aware region change card."""

    region = change.new_region or change.old_region
    if region is None:
        return ""
    label = _CHANGE_LABELS.get(change.change_type, change.change_type)
    type_label = _region_type_label(change.region_type)
    title = _region_card_title(index, change)
    meta = _region_change_meta(change)
    body = (
        _render_visual_region_change(change)
        if change.region_type in {"table", "formula", "figure"}
        else _render_text_region_change(change)
    )
    return f"""
      <section class="card region-card" data-region-type="{_escape(change.region_type)}" id="region-{index}">
        <h3><span class="region-type">{_escape(type_label)}</span><span class="badge badge-{change.change_type}">{_escape(label)}</span>{_escape(title)}</h3>
        <div class="region-meta">{_escape(meta)}</div>
        {body}
      </section>
    """


def _render_text_region_change(change: RegionChange) -> str:
    """Render paragraph or heading changes at region granularity."""

    old_text = change.old_region.text if change.old_region else ""
    new_text = change.new_region.text if change.new_region else ""
    if change.old_region and change.new_region:
        old_html, new_html = _inline_diff_html(old_text, new_text)
    else:
        old_html = _render_text_with_highlights(old_text, [(0, len(old_text), "del")]) if old_text else ""
        new_html = _render_text_with_highlights(new_text, [(0, len(new_text), "ins")]) if new_text else ""
    return f"""
      <div class="compare-grid">
        <div class="pane">
          <h4>旧区域</h4>
          <div class="snippet">{old_html or "无对应区域"}</div>
        </div>
        <div class="pane">
          <h4>新区域</h4>
          <div class="snippet">{new_html or "无对应区域"}</div>
        </div>
      </div>
    """


def _render_visual_region_change(change: RegionChange) -> str:
    """Render table/formula/figure screenshots and heatmap diff."""

    old_panel = _visual_panel("旧区域截图", change.old_image_data_uri)
    new_panel = _visual_panel("新区域截图", change.new_image_data_uri)
    diff_panel = _visual_panel("视觉差异热图", change.diff_image_data_uri)
    context = _region_context(change)
    context_html = f'<p class="muted">{_escape(context)}</p>' if context else ""
    return f"""
      {context_html}
      <div class="visual-grid">{old_panel}{new_panel}{diff_panel}</div>
    """


def _visual_panel(title: str, image_data_uri: str) -> str:
    """Render one image panel or an explicit empty state."""

    if not image_data_uri:
        return f'<div class="visual-panel"><h4>{_escape(title)}</h4><div class="snippet">无对应截图</div></div>'
    return f'<div class="visual-panel"><h4>{_escape(title)}</h4><img alt="{_escape(title)}" src="{image_data_uri}"></div>'


def _region_card_title(index: int, change: RegionChange) -> str:
    """Build a human-readable region card title."""

    region = change.new_region or change.old_region
    if region is None:
        return f"{index}. 区域差异"
    context = _region_context(change)
    if context:
        return f"{index}. {truncate(context, 110)}"
    return f"{index}. Page {region.page_number} {change.region_type}"


def _region_change_meta(change: RegionChange) -> str:
    """Return page, bbox, and similarity metadata for a region change."""

    old_page = str(change.old_region.page_number) if change.old_region else "-"
    new_page = str(change.new_region.page_number) if change.new_region else "-"
    old_bbox = _region_bbox_label(change.old_region)
    new_bbox = _region_bbox_label(change.new_region)
    return (
        f"old page {old_page}, bbox {old_bbox} · new page {new_page}, bbox {new_bbox} · "
        f"similarity {change.similarity:.3f}"
    )


def _region_context(change: RegionChange) -> str:
    """Return the best available context for a region."""

    region = change.new_region or change.old_region
    if region is None:
        return ""
    return compact_inline(region.section_context or region.text)


def _region_bbox_label(region: LayoutRegion | None) -> str:
    """Format a region bbox for the report."""

    if region is None:
        return "-"
    return "({:.1f}, {:.1f}, {:.1f}, {:.1f})".format(*region.bbox)


def _region_type_counts(changes: list[RegionChange]) -> dict[str, int]:
    """Count layout changes by region type in display order."""

    counts = {key: 0 for key in ("paragraph", "heading", "table", "formula", "figure")}
    for change in changes:
        counts[change.region_type] = counts.get(change.region_type, 0) + 1
    return counts


def _region_type_label(region_type: str) -> str:
    """Return a bilingual region type label."""

    labels = {
        "paragraph": "paragraph 正文",
        "heading": "heading 标题",
        "table": "table 表格",
        "formula": "formula 公式",
        "figure": "figure 图片",
    }
    return labels.get(region_type, region_type)


def _render_review_focus_html(result: DiffResult) -> str:
    """Render an auto-ranked checklist of changes worth reading first."""

    focused = _top_review_changes(result.changes, limit=8)  # 自动挑出数值、表格和删除类高风险变化。
    if not focused:
        return '<section class="card"><h2>建议优先复核</h2><p class="muted">未发现需要优先展开的正文差异。</p></section>'
    items = "\n".join(  # 每个条目包含位置、摘要和一个代表片段，方便用户先抓重点。
        "<li>"
        f"<strong>{_escape(_display_change_location(change))}：</strong>"
        f"{_escape(_change_summary(change) or _CHANGE_LABELS.get(change.change_type, change.change_type))}"
        f"<div class=\"muted\">{_escape(_review_focus_preview(change))}</div>"
        "</li>"
        for change in focused
    )
    return f"""
      <section class="card focus">
        <h2>建议优先复核</h2>
        <ol class="focus-list">{items}</ol>
      </section>
    """


def _top_review_changes(changes: list[SectionChange], limit: int) -> list[SectionChange]:
    """Return high-signal changes before ordinary wording churn."""

    ranked = sorted(  # 分数高的排前面；同分时保持报告中的页码顺序。
        enumerate(changes),
        key=lambda item: (_change_focus_score(item[1]), -item[0]),
        reverse=True,
    )
    return [change for _index, change in ranked[:limit] if _change_focus_score(change) > 0]


def _change_focus_score(change: SectionChange) -> int:
    """Score a change by the chance that a human reviewer must inspect it."""

    texts: list[str] = []  # 收集可见片段用于判断数值、表格和引用风险。
    for pair in change.replaced_snippets:
        texts.extend([pair.old, pair.new])  # 替换片段两侧都参与风险判断。
    texts.extend(change.added_snippets)  # 新增片段可能包含新要求。
    texts.extend(change.removed_snippets)  # 删除片段可能代表旧要求被移除。
    joined = "\n".join(texts)  # 合并后用正则做轻量分类。
    score = 0  # 默认低风险，下面逐项加权。
    if change.change_type in {"added", "deleted"}:
        score += 4  # 整节新增/删除比普通措辞变化更需要复核。
    if re.search(r"(?i)(?:<=|>=|≤|≥|[+-]?\d+(?:\.\d+)?\s*(?:ppm|ohm|Ω|ui|db|ghz|gsym/s|mv|v|%))", joined):
        score += 3  # 数值和单位变化优先级较高。
    if re.search(r"(?i)\b(?:section|appendix|table|figure|ieee|annex)\s+[A-Z0-9.:-]+", joined):
        score += 2  # 外部章节、表号或标准引用变化也需要复核。
    score += min(3, len(change.replaced_snippets) + len(change.added_snippets) + len(change.removed_snippets))
    return score


def _review_focus_preview(change: SectionChange) -> str:
    """Return one compact example snippet for the focus checklist."""

    if change.replaced_snippets:
        pair = change.replaced_snippets[0]  # 替换片段最适合作为旧新对照预览。
        return truncate(f"旧: {compact_inline(pair.old)} -> 新: {compact_inline(pair.new)}", 220)
    if change.added_snippets:
        return truncate(f"新增: {compact_inline(change.added_snippets[0])}", 220)
    if change.removed_snippets:
        return truncate(f"删除: {compact_inline(change.removed_snippets[0])}", 220)
    return "该条只有位置或元数据变化，请按页码回到源 PDF 复核。"


def _render_noise_notes_html(result: DiffResult) -> str:
    """Render concrete noise rules applied or recommended by the report."""

    notes = _noise_note_items(result)  # 根据实际结果生成降噪说明，避免空泛描述。
    if not notes:
        return ""
    items = "\n".join(f"<li>{_escape(note)}</li>" for note in notes)
    return f"""
      <section class="card">
        <h2>自动过滤/降权的噪声</h2>
        <ul class="noise-list">{items}</ul>
      </section>
    """


def _noise_note_items(result: DiffResult) -> list[str]:
    """Build short notes about noise removed from text and table review."""

    notes: list[str] = []  # 只展示与当前报告有关的噪声规则。
    if any("已隐藏" in warning for warning in result.warnings):
        notes.append("重复页眉页脚、DRAFT 水印、版权行、页边行号已尽量从正文差异中隐藏。")
    if any(_has_math_format_only_table_rows(table) for table in result.old_table_visuals + result.new_table_visuals):
        notes.append("表格文字和公式 OCR 不再作为确定差异列出；请以左右表格截图和源 PDF 为准。")
    if any(_looks_like_report_filtered_visual(table) for table in result.old_table_visuals + result.new_table_visuals):
        notes.append("曲线图网格和坐标轴碎片不作为表格对比对象展示。")
    if not notes:
        notes.append("报告优先展示正文数值、限值、章节引用和新增/删除；表格 OCR 行、公式图形和普通格式变化会降低优先级。")
    return notes


def _has_math_format_only_table_rows(table: TableVisual) -> bool:
    """Return True when rows contain math notation that has known format variants."""

    joined = "\n".join(table.row_texts)  # 表格行合并后检查乘号和科学计数法格式。
    return bool(re.search(r"(?i)(?:x|×|\*)\s*10|[a-z_]\s*(?:×|\*)\s*[a-z0-9_]", joined))


def _render_change_navigation_html(changes: list[SectionChange]) -> str:
    """Render compact chips that jump to detailed text changes."""

    if not changes:
        return ""
    links = "\n".join(_render_nav_item(index, change) for index, change in enumerate(changes, start=1))
    return f"""
      <section class="card">
        <h2>正文差异导航</h2>
        <div class="toc">{links}</div>
      </section>
    """


def _render_table_visuals_html(result: DiffResult) -> str:
    """Render screenshot-backed table evidence without replacing text diffs."""

    old_tables = _reportable_table_visuals(result.old_table_visuals)  # 旧版只保留真实表格候选。
    new_tables = _reportable_table_visuals(result.new_table_visuals)  # 新版同样过滤曲线图和坐标轴碎片。
    if not old_tables and not new_tables:
        return ""
    pairs = _paired_table_visuals(old_tables, new_tables)  # 同名表格按出现顺序配对，跨页表格不会互相抢配。
    toc = _render_table_toc(pairs)  # 表格导航放在卡片前，便于用户直接跳到目标表。
    cards = "\n".join(
        _render_table_visual_pair(index, old_table, new_table)
        for index, (old_table, new_table) in enumerate(pairs, start=1)
    )
    return f"""
      <section class="table-visuals">
        <section class="card">
          <h2>表格图像对比</h2>
          <p class="change-summary">本区按表名和出现顺序展示旧/新截图；表格文字和公式 OCR 不作为确定差异列出，避免把识别错位当成协议修改。</p>
          {toc}
        </section>
        {cards}
      </section>
    """


def _paired_table_visuals(
    old_tables: list[TableVisual],
    new_tables: list[TableVisual],
) -> list[tuple[TableVisual | None, TableVisual | None]]:
    """Pair table visuals by ordered alignment, not by exact title buckets."""

    return _align_table_visuals(old_tables, new_tables)  # 使用动态规划顺序对齐，减少“明明有对应却写新增”的误判。


_TABLE_PAIR_MIN_SCORE = 0.42  # 低于该分数的旧/新截图不强行配对，避免真正新增/删除被误连。
_CONTEXT_TITLE_ROW_MIN_SCORE = 0.14  # 跨页续段行身份可能差异较大，只要求弱相似证据。


def _align_table_visuals(
    old_tables: list[TableVisual],
    new_tables: list[TableVisual],
) -> list[tuple[TableVisual | None, TableVisual | None]]:
    """Align old/new table screenshots while preserving document order."""

    old_count = len(old_tables)  # 旧版候选表格数量。
    new_count = len(new_tables)  # 新版候选表格数量。
    old_title_keys = _contextual_table_title_keys(old_tables)  # 旧侧跨页续段可继承相邻表题参与配对。
    new_title_keys = _contextual_table_title_keys(new_tables)  # 新侧同样补齐无 caption 的续页片段。
    scores = [
        [
            _table_pair_score(old_table, new_table, old_title_keys[old_index], new_title_keys[new_index])
            + _table_order_position_score(old_index, new_index, old_count, new_count)
            for new_index, new_table in enumerate(new_tables)
        ]
        for old_index, old_table in enumerate(old_tables)
    ]  # 预先计算所有旧/新表格相似度，后续 DP 只查表。
    dp = [[0.0] * (new_count + 1) for _ in range(old_count + 1)]  # dp[i][j] 表示前 i 个旧表和前 j 个新表的最佳总分。
    choice = [[""] * (new_count + 1) for _ in range(old_count + 1)]  # 记录回溯路径：match/delete/insert。
    for old_index in range(1, old_count + 1):
        dp[old_index][0] = dp[old_index - 1][0] - _table_delete_gap_penalty(0, new_count)  # 开头连续旧侧空配需要轻微惩罚。
        choice[old_index][0] = "delete"  # 新侧为空时，旧表只能作为未配对项输出。
    for new_index in range(1, new_count + 1):
        dp[0][new_index] = dp[0][new_index - 1] - _table_insert_gap_penalty(0, old_count)  # 开头连续新侧空配需要轻微惩罚。
        choice[0][new_index] = "insert"  # 旧侧为空时，新表只能作为未配对项输出。
    for old_index in range(1, old_count + 1):
        for new_index in range(1, new_count + 1):
            pair_score = scores[old_index - 1][new_index - 1]  # 当前旧/新表的匹配可信度。
            match_score = (
                dp[old_index - 1][new_index - 1] + pair_score
                if pair_score >= _TABLE_PAIR_MIN_SCORE
                else -1.0
            )  # 分数够高才允许配对，否则视为不可配。
            delete_score = dp[old_index - 1][new_index] - _table_delete_gap_penalty(new_index, new_count)  # 中间跳过旧表要轻微扣分。
            insert_score = dp[old_index][new_index - 1] - _table_insert_gap_penalty(old_index, old_count)  # 中间跳过新表要轻微扣分。
            if match_score >= delete_score and match_score >= insert_score:
                dp[old_index][new_index] = match_score  # 同分优先配对，减少误报新增。
                choice[old_index][new_index] = "match"  # 回溯时旧/新各退一步。
            elif delete_score >= insert_score:
                dp[old_index][new_index] = delete_score  # 跳过旧表。
                choice[old_index][new_index] = "delete"  # 回溯时只退旧侧。
            else:
                dp[old_index][new_index] = insert_score  # 跳过新表。
                choice[old_index][new_index] = "insert"  # 回溯时只退新侧。
    pairs: list[tuple[TableVisual | None, TableVisual | None]] = []  # 回溯得到的逆序配对。
    old_index = old_count  # 从 DP 右下角开始回溯。
    new_index = new_count  # 新侧索引同样从末尾开始。
    while old_index > 0 or new_index > 0:
        step = choice[old_index][new_index]  # 当前回溯动作。
        if step == "match":
            pairs.append((old_tables[old_index - 1], new_tables[new_index - 1]))  # 旧新成对输出。
            old_index -= 1  # 旧侧消耗一个表格。
            new_index -= 1  # 新侧消耗一个表格。
        elif step == "delete":
            pairs.append((old_tables[old_index - 1], None))  # 旧侧找不到可信新表。
            old_index -= 1  # 只消耗旧侧。
        else:
            pairs.append((None, new_tables[new_index - 1]))  # 新侧找不到可信旧表。
            new_index -= 1  # 只消耗新侧。
    return list(reversed(pairs))  # 回溯结果反转回文档阅读顺序。


def _table_delete_gap_penalty(aligned_new_count: int, new_count: int) -> float:
    """Penalize unmatched old tables before the new sequence has ended."""

    return 0.0 if aligned_new_count >= new_count else 0.06  # 尾部缺失可自然发生，中间缺失更可能造成错位。


def _table_insert_gap_penalty(aligned_old_count: int, old_count: int) -> float:
    """Penalize unmatched new tables before the old sequence has ended."""

    return 0.0 if aligned_old_count >= old_count else 0.06  # 新版多出的尾页不惩罚，前面插空需要更强证据。


def _contextual_table_title_keys(tables: list[TableVisual]) -> list[str]:
    """Infer conservative title keys for captionless continuation screenshots."""

    own_keys = [_normalized_table_title_key(table.title) for table in tables]  # 先只取截图自身真正抽到的表题。
    effective_keys = own_keys[:]  # 输出会在少数可信续页上补齐表题，但不改原始 TableVisual。
    for index, table in enumerate(tables):
        if effective_keys[index] or index == 0:
            continue  # 已有表题或没有前序表时，前向继承不适用。
        previous_table = tables[index - 1]  # 只允许从紧邻前一个物理表格继承，避免跨很多页错配。
        previous_key = effective_keys[index - 1]  # 前序表题可能也是已经确认的续页表题。
        next_key = own_keys[index + 1] if index + 1 < len(tables) else ""  # 同一跨页表的下一段若有表题，可作为交叉确认。
        if not previous_key or not _adjacent_pdf_pages(previous_table, table):
            continue
        if next_key and next_key == previous_key:
            effective_keys[index] = previous_key  # 无题片段夹在同名表题之间，基本可确定是同一跨页表。
            continue
        if _table_row_identity_similarity(previous_table, table) >= _CONTEXT_TITLE_ROW_MIN_SCORE:
            effective_keys[index] = previous_key  # 行身份仍相近时，认为是上一表的续段。
    for index, table in enumerate(tables):
        if effective_keys[index] or index + 1 >= len(tables):
            continue  # 只处理仍无标题、且后面紧邻有候选表题的片段。
        next_table = tables[index + 1]  # 用于修复表题被抽到下一页的首段跨页表。
        next_key = own_keys[index + 1]  # 反向继承只使用下一张自身表题，避免传播二手推断。
        if not next_key or not _adjacent_pdf_pages(table, next_table):
            continue
        if _table_row_identity_similarity(table, next_table) >= _CONTEXT_TITLE_ROW_MIN_SCORE:
            effective_keys[index] = next_key  # 首段与下一段行身份接近时，补回下一页表题。
    return effective_keys


def _adjacent_pdf_pages(first_table: TableVisual, second_table: TableVisual) -> bool:
    """Return True when two table visuals are on consecutive source pages."""

    return second_table.page_number - first_table.page_number == 1  # 只跨一页继承，避免把隔页新表误当续页。


def _table_pair_score(
    old_table: TableVisual,
    new_table: TableVisual,
    old_title_key: str | None = None,
    new_title_key: str | None = None,
) -> float:
    """Score whether two table screenshots should be shown as one comparison."""

    old_title = old_title_key if old_title_key is not None else _normalized_table_title_key(old_table.title)  # 旧表题可使用续页上下文。
    new_title = new_title_key if new_title_key is not None else _normalized_table_title_key(new_table.title)  # 新表题可使用同一推断口径。
    row_score = _table_row_identity_similarity(old_table, new_table)  # 行身份分数负责处理 caption 缺失。
    title_score = _table_title_similarity(old_title, new_title)  # 表题分数负责处理表号重排。
    page_score = _table_page_shift_score(old_table, new_table)  # 页码只作弱提示，不能单独决定配对。
    if old_title and new_title and old_title == new_title:
        return 1.0 + page_score * 0.01  # 同名跨页表按出现顺序配，不让局部行相似度把片段错位。
    if old_title and new_title and title_score >= 0.86:
        return max(0.82, title_score * 0.55 + row_score * 0.35 + page_score * 0.10)  # 同名表优先配对。
    if (old_title or new_title) and row_score >= 0.50:
        return row_score * 0.78 + title_score * 0.12 + page_score * 0.10  # 一侧 caption 丢失时主要看行身份。
    if row_score >= 0.58:
        return row_score * 0.88 + page_score * 0.12  # 双方 caption 都缺失时必须有较强行结构相似。
    return row_score * 0.70 + title_score * 0.20 + page_score * 0.10  # 弱候选保留低分，通常不会越过阈值。


def _table_order_position_score(
    old_index: int,
    new_index: int,
    old_count: int,
    new_count: int,
) -> float:
    """Add a tiny score that keeps repeated table segments aligned from the front."""

    old_position = old_index / max(1, old_count - 1) if old_count > 1 else 0.0  # 旧表在序列中的相对位置。
    new_position = new_index / max(1, new_count - 1) if new_count > 1 else 0.0  # 新表在序列中的相对位置。
    closeness = max(0.0, 1.0 - abs(old_position - new_position))  # 相对位置越接近，越可能是对应片段。
    return closeness * 0.025  # 只作为同分时的微弱排序信号，不压过真实行/标题证据。


def _table_title_similarity(old_title: str, new_title: str) -> float:
    """Return title similarity with blank titles treated as unknown."""

    if old_title and new_title:
        return difflib.SequenceMatcher(None, old_title, new_title, autojunk=False).ratio()  # 双方有表题时直接比较归一化标题。
    if not old_title and not new_title:
        return 0.0  # 双方都没标题不能提供正向证据。
    return 0.28  # 一侧标题缺失时给少量分数，避免完全惩罚 caption 抽取失败。


def _table_page_shift_score(old_table: TableVisual, new_table: TableVisual) -> float:
    """Return a weak page-order compatibility score."""

    gap = abs(new_table.page_number - old_table.page_number)  # 插页会造成页码偏移，但偏移通常不会特别大。
    if gap <= 3:
        return 1.0  # 常见修订导致 1-3 页偏移，给予弱正向信号。
    if gap <= 6:
        return 0.65  # 较大偏移仍可能是同一表，但不能强配。
    return 0.25  # 很远的页码只保留极低弱信号。


def _table_row_identity_similarity(old_table: TableVisual, new_table: TableVisual) -> float:
    """Compare table rows using identity fields rather than value cells."""

    old_keys = [_table_row_identity_key(row) for row in old_table.row_texts if _table_row_identity_key(row)]  # 旧表每行取参数/特性身份。
    new_keys = [_table_row_identity_key(row) for row in new_table.row_texts if _table_row_identity_key(row)]  # 新表每行同样取身份。
    if not old_keys or not new_keys:
        return 0.0  # 没有可用行身份时不能靠行结构配对。
    sequence_score = difflib.SequenceMatcher(None, old_keys, new_keys, autojunk=False).ratio()  # 顺序相似度适合跨页表片段。
    old_set = set(old_keys)  # 集合分数能容忍新增/删除少量行。
    new_set = set(new_keys)  # 新表行身份集合。
    overlap_score = len(old_set & new_set) / max(1, min(len(old_set), len(new_set)))  # 用较小集合做分母，避免续段长度不同被过度惩罚。
    count_score = min(len(old_keys), len(new_keys)) / max(len(old_keys), len(new_keys))  # 行数相近时提高可信度。
    return max(sequence_score, overlap_score * 0.72 + count_score * 0.18)  # 取更强证据作为行身份相似度。


def _table_row_identity_key(row: str) -> str:
    """Return a row key focused on labels/parameters, not changed values."""

    cells = _table_row_cells_for_identity(row)  # 将结构化表格行拆成单元格。
    identity_parts: list[str] = []  # 只保存能稳定识别一行的片段。
    for cell in cells:
        label, value = _split_table_cell_assignment(cell)  # 拆成 Header=Value 或普通文本。
        normalized_label = _normalize_table_identity_text(label)  # 表头名归一化后判断列类型。
        normalized_value = _normalize_table_identity_text(value)  # 单元格值归一化后加入身份。
        if not normalized_value:
            continue
        if _is_table_identity_label(normalized_label):
            identity_parts.append(f"{normalized_label}:{normalized_value}")  # 参数名、特性名、标签列是行身份。
        elif not label and len(identity_parts) < 2:
            identity_parts.append(normalized_value)  # 无表头行保留前两个描述性单元格。
    if not identity_parts and cells:
        identity_parts.append(_normalize_table_identity_text(cells[0]))  # 兜底使用第一格，避免完全无身份。
    return " | ".join(identity_parts[:4])  # 限制长度，避免长注释拖低相似度。


def _table_row_cells_for_identity(row: str) -> list[str]:
    """Split a structured table row into visible cells for identity matching."""

    payload = row.removeprefix("表格行:").strip()  # 删除报告内部表格行前缀。
    payload = re.sub(r"^T\d+\s*\|\s*", "", payload)  # 删除表格序号，跨页/跨版本序号不同也应可配。
    return [cell.strip() for cell in payload.split("|") if cell.strip()]  # 竖线分隔的结构化单元格。


def _split_table_cell_assignment(cell: str) -> tuple[str, str]:
    """Split Header=Value cells while preserving unlabeled text."""

    if "=" not in cell:
        return "", cell  # 没有 Header=Value 时按普通文本处理。
    label, value = cell.split("=", 1)  # 只按第一个等号拆，避免公式里的等号破坏后半段。
    return label.strip(), value.strip()  # 去掉两侧空白，便于后续归一化。


def _is_table_identity_label(label: str) -> bool:
    """Return True for columns that identify a table row across versions."""

    if not label:
        return False  # 空 label 由调用方单独处理。
    identity_words = {
        "parameter",
        "characteristic",
        "label",
        "description",
        "frequency range",
        "coefficients",
        "coefficient",
        "symbol",
    }  # 这些列描述的是“哪一行”，而不是本行限值。
    return label in identity_words or any(word in label for word in identity_words)


def _normalize_table_identity_text(value: str) -> str:
    """Normalize table identity text for matching, removing numeric value noise."""

    normalized = compact_inline(value).casefold().replace("µ", "u").replace("μ", "u")  # 空白、大小写和微符号归一化。
    normalized = re.sub(r"(?i)\btable\s+\d+(?:[-–]\d+)?\.?", "table", normalized)  # 行内表号变化不影响身份。
    normalized = re.sub(r"(?i)\b\d+(?:\.\d+)?\s*(?:ohm|ω|ui|db|ghz|mhz|gsym/s|mv|v|ff|pf|ph|mm|%)\b", " value ", normalized)  # 单位值不是行身份。
    normalized = re.sub(r"[+-]?\d+(?:\.\d+)?(?:e[+-]?\d+)?", " number ", normalized)  # 普通数字用占位符，保留“这里有数值”的结构。
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff_]+", " ", normalized)  # 删除标点差异。
    normalized = re.sub(r"\s+", " ", normalized).strip()  # 收紧多余空白。
    return normalized


def _reportable_table_visuals(tables: list[TableVisual]) -> list[TableVisual]:
    """Remove chart-grid false positives from the final table image section."""

    return [table for table in tables if not _looks_like_report_filtered_visual(table)]


def _looks_like_report_filtered_visual(table: TableVisual) -> bool:
    """Return True for visuals that are charts or axis fragments, not tables."""

    title = compact_inline(table.title).casefold()  # 标题是识别 Figure 和 X/Y 轴碎片的最强信号。
    if re.match(r"^figure\s+\d", title):
        return True  # 曲线图不属于表格图像对比。
    if title in {"x", "y"} and not table.row_texts:
        return True  # 坐标轴碎片没有表格行，直接隐藏。
    if not title and _looks_like_chart_legend_rows(table.row_texts):
        return True  # 无标题且只有 F/A/R/IL min 这类图例短行时，按曲线图误识别隐藏。
    if len(table.row_texts) <= 1 and not re.search(r"(?i)\btable\s+\d|表\s*\d|\btable\b", title):
        return True  # 没有表题且行数极少，通常是 pdfplumber 的弱误识别。
    return False


def _looks_like_chart_legend_rows(row_texts: list[str]) -> bool:
    """Return True for short chart legend fragments misdetected as tables."""

    if not row_texts or len(row_texts) > 4:
        return False  # 真实表格通常有更多行，少量短标签才按图例处理。
    payload = " ".join(row.removeprefix("表格行:").strip() for row in row_texts)  # 去掉内部前缀后看用户可见内容。
    if "=" in payload:
        return False  # Header=Value 表示结构化表格行，不是简单图例。
    tokens = re.findall(r"[A-Za-z]+(?:\s+[A-Za-z]+)?", payload)  # 提取 IL min、IL max、F 等图例词。
    if not tokens:
        return True
    return all(len(token.replace(" ", "")) <= 5 for token in tokens) and bool(
        re.search(r"(?i)\b(?:il\s*min|il\s*max|[far])\b", payload)
    )  # 全部都是短标签且包含典型曲线图图例时过滤。


def _table_groups_by_key(tables: list[TableVisual]) -> dict[str, list[TableVisual]]:
    """Group table visuals by a stable title key while preserving order."""

    groups: dict[str, list[TableVisual]] = {}  # Python 字典保留首次出现顺序，适合报告导航。
    for table in tables:
        key = _table_visual_group_key(table)  # 表号变化会被归一到同一语义表名。
        groups.setdefault(key, []).append(table)
    return groups


def _table_visual_group_key(table: TableVisual) -> str:
    """Return the semantic table key used for image pairing."""

    title_key = _normalized_table_title_key(table.title)  # 优先使用表题，去掉版本重排导致的表号差异。
    if title_key:
        return title_key
    row_key = " ".join(_table_row_display_key(row) for row in table.row_texts[:2])  # 无标题时用前两行辅助分组。
    if row_key:
        return f"rows:{row_key[:160]}"
    return f"page:{table.page_number}:table:{table.table_number}"  # 最后退回到物理位置，避免误配。


def _normalized_table_title_key(title: str) -> str:
    """Normalize a table title so changed table numbers still pair."""

    candidate = compact_inline(title).casefold()  # 表题大小写和多余空白不影响配对。
    if not candidate:
        return ""
    candidate = re.sub(r"(?i)^\d+\s+(?=table\s+\d)", "", candidate)  # 删除抽取混入 caption 前的页边行号。
    candidate = re.sub(r"(?i)(?<=\D)\s+\d+$", "", candidate)  # 删除 caption 后的页边行号。
    if re.match(r"^figure\s+\d", candidate):
        return ""  # Figure 不是表格配对键。
    if re.search(r"(?i)^implementation\s+agreement\s+oif-cei", candidate):
        return ""  # 页眉不能作为表名。
    candidate = re.sub(r"(?i)^table\s+\d+(?:[-–]\d+)?\.?\s*", "", candidate)  # 删除 Table 32-9 这类可变表号。
    candidate = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", candidate).strip()  # 标点差异不影响同表匹配。
    return candidate[:160]


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


def _render_table_visual_pair(
    index: int,
    old_table: TableVisual | None,
    new_table: TableVisual | None,
) -> str:
    """Render one old/new table screenshot pair."""

    title = _table_pair_title(index, old_table, new_table)
    tag = _table_pair_tag(old_table, new_table)  # 短标签显示旧/新表格定位。
    severity = _table_pair_severity(old_table, new_table)  # 用左边框提示实质变化、重排或低风险。
    verdict = _table_pair_verdict(old_table, new_table)  # 每个表格卡片给出先读结论，贴近 visual table 报告。
    old_shot = _render_one_table_shot("旧版截图", old_table)
    new_shot = _render_one_table_shot("新版截图", new_table)
    return f"""
        <section class="card table-visual-card {severity}" id="table-{index}">
          <h3><span class="tag">{_escape(tag)}</span>{_escape(title)}</h3>
          <p class="muted">{_escape(_table_pair_pages(old_table, new_table))}</p>
          <div class="verdict"><strong>报告结论：</strong>{_escape(verdict)}</div>
          <div class="table-shot-grid shots">{old_shot}{new_shot}</div>
        </section>
    """


def _table_pair_title(
    index: int,
    old_table: TableVisual | None,
    new_table: TableVisual | None,
) -> str:
    """Build a readable title for one visual table pair."""

    table = new_table or old_table
    if table is None:
        return f"表格 {index}"
    title = _display_table_title(new_table) if new_table else ""  # 优先展示新版表题，贴近用户正在复核的新文档。
    if not title and old_table:
        title = _display_table_title(old_table)  # 新版续页无 caption 时，用旧版表题说明是哪张表。
    if not title:
        title = "跨页表格续段" if table.is_continuation else ""  # 双方都没有表题时才退回通用续段文案。
    if title:
        return f"{index}. {title}"
    return f"{index}. 第 {table.page_number} 页表格 {table.table_number}"


def _display_table_title(table: TableVisual) -> str:
    """Return a table title suitable for users."""

    title = compact_inline(table.title)  # 压缩空白，避免 PDF 抽取产生怪异换行。
    if not title:
        return ""
    title = re.sub(r"(?i)^\d+\s+(?=table\s+\d)", "", title)  # 展示时也去掉 caption 前的页边行号。
    title = re.sub(r"(?i)(?<=\D)\s+\d+$", "", title)  # 展示时去掉 caption 后的页边行号。
    if re.search(r"(?i)^implementation\s+agreement\s+oif-cei", title):
        return ""  # 页眉不能当表题展示。
    return title


def _table_pair_tag(old_table: TableVisual | None, new_table: TableVisual | None) -> str:
    """Build a compact old/new table locator tag."""

    old_label = f"旧 p{old_table.page_number}" if old_table else "旧无"  # 旧侧定位。
    new_label = f"新 p{new_table.page_number}" if new_table else "新无"  # 新侧定位。
    return f"{old_label} / {new_label}"


def _table_pair_pages(old_table: TableVisual | None, new_table: TableVisual | None) -> str:
    """Return source-page text for one table pair."""

    old_pages = str(old_table.page_number) if old_table else "-"
    new_pages = str(new_table.page_number) if new_table else "-"
    return f"旧页: {old_pages} · 新页: {new_pages}"


def _table_pair_severity(old_table: TableVisual | None, new_table: TableVisual | None) -> str:
    """Classify a table pair for visual emphasis."""

    if old_table is None or new_table is None:
        return "severity-high"  # 只有一侧存在，代表新增/删除表格或未配对片段。
    return "severity-low"  # 双侧已配对时不再根据低置信度 OCR 行数判断严重性。


def _table_pair_verdict(old_table: TableVisual | None, new_table: TableVisual | None) -> str:
    """Explain what the reviewer should do with a table pair."""

    if old_table is None:
        return "新版出现旧版没有的表格截图，请确认是否为新增表或跨页续段。"
    if new_table is None:
        return "旧版表格在新版未找到对应截图，请确认是否删除、移动或表号重排。"
    return "已找到旧/新对应截图；请直接查看左右图像，自动表格文字识别不作为差异结论。"


def _table_changed_row_count(old_table: TableVisual, new_table: TableVisual) -> int:
    """Count non-equal row blocks after table-row normalization."""

    old_keys = [_table_row_display_key(row) for row in old_table.row_texts]  # 旧表行按格式等价规则归一。
    new_keys = [_table_row_display_key(row) for row in new_table.row_texts]  # 新表行使用同一规则。
    matcher = difflib.SequenceMatcher(None, old_keys, new_keys, autojunk=False)
    return sum(max(old_end - old_start, new_end - new_start) for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes() if tag != "equal")


def _render_table_toc(pairs: list[tuple[TableVisual | None, TableVisual | None]]) -> str:
    """Render jump links for table image pairs."""

    if not pairs:
        return ""
    links = "\n".join(
        f'<a href="#table-{index}">{_escape(_table_pair_title(index, old_table, new_table))}</a>'
        for index, (old_table, new_table) in enumerate(pairs, start=1)
    )  # 每个表格卡片一个跳转入口，手机端也能快速定位。
    return f'<div class="toc">{links}</div>'


def _render_one_table_shot(label: str, table: TableVisual | None) -> str:
    """Render one side of a table screenshot pair."""

    if table is None:
        return f'<div class="table-shot"><h4>{_escape(label)}</h4><div class="snippet">无对应表格截图</div></div>'
    caption = f"{label} · 页 {table.page_number} · 表格 {table.table_number}"
    grid_summary = _display_table_grid_summary(table.grid_summary)
    return (
        f'<div class="table-shot"><h4>{_escape(caption)}</h4>'
        f'<img alt="{_escape(caption)}" src="{table.image_data_uri}">'
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
    old_table: TableVisual | None,
    new_table: TableVisual | None,
) -> str:
    """Render a compact row-level summary from structured table rows."""

    old_rows = old_table.row_texts if old_table else []
    new_rows = new_table.row_texts if new_table else []
    old_keys = [_table_row_display_key(row) for row in old_rows]
    new_keys = [_table_row_display_key(row) for row in new_rows]
    matcher = difflib.SequenceMatcher(None, old_keys, new_keys, autojunk=False)
    body_rows: list[str] = []  # 保存 HTML 表格行。
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        old_block = old_rows[old_start:old_end]
        new_block = new_rows[new_start:new_end]
        block_count = max(len(old_block), len(new_block))
        for offset in range(block_count):
            old_value = old_block[offset] if offset < len(old_block) else ""
            new_value = new_block[offset] if offset < len(new_block) else ""
            body_rows.append(
                "<tr>"
                f"<td>{_escape(_table_diff_kind(old_value, new_value))}</td>"
                f"<td>{_escape(old_value)}</td>"
                f"<td>{_escape(new_value)}</td>"
                "</tr>"
            )
    if not body_rows:
        body_rows.append('<tr><td>未检测到行级变化</td><td></td><td></td></tr>')
    visible_rows = body_rows[:12]  # 表格摘要保持可读，超出的行数必须显式提示。
    omitted_count = max(0, len(body_rows) - len(visible_rows))  # 统计被折叠的表格行变化。
    omitted_note = (
        f'<div class="omitted-note">另有 {omitted_count} 行表格变化未展示；完整表格行已参与正文差异比较。</div>'
        if omitted_count
        else ""
    )
    return (
        '<table class="table-row-summary"><thead><tr>'
        '<th>类型</th><th>旧版表格行</th><th>新版表格行</th>'
        '</tr></thead><tbody>'
        + "\n".join(visible_rows)
        + "</tbody></table>"
        + omitted_note
    )


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
        return "该章节发生变化，但当前每章展示片段数未展开具体文本；可调大 --max-snippets 后复跑。"
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

    return f"另有 {omitted_count} 条差异片段未展示；完整章节已比较，可调大 --max-snippets 展开更多报告片段。"


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
        "主要比较 PDF 中可抽取的正文文字，表格仅提供截图辅助复核，不再列出低置信度 OCR 行差异；"
        "图片、印章、普通矢量图、公式图和封面作者信息不作为正文差异；重复页眉页脚和动态页码会尽量过滤；"
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


def _region_change_to_dict(change: RegionChange) -> dict[str, object]:
    """Serialize layout-aware region diff metadata without image payloads."""

    return {
        "change_type": change.change_type,
        "region_type": change.region_type,
        "similarity": round(change.similarity, 6),
        "old_region": _layout_region_to_dict(change.old_region),
        "new_region": _layout_region_to_dict(change.new_region),
        "has_old_image": bool(change.old_image_data_uri),
        "has_new_image": bool(change.new_image_data_uri),
        "has_diff_image": bool(change.diff_image_data_uri),
    }


def _layout_region_to_dict(region: LayoutRegion | None) -> dict[str, object] | None:
    """Serialize one layout region for JSON audit output."""

    if region is None:
        return None
    return {
        "region_id": region.region_id,
        "region_type": region.region_type,
        "page_number": region.page_number,
        "bbox": [round(value, 3) for value in region.bbox],
        "text_preview": truncate(compact_inline(region.text), 500),
        "section_context": truncate(compact_inline(region.section_context), 500),
        "order_index": region.order_index,
        "image_hash": region.image_hash,
        "page_width": round(region.page_width, 3),
        "page_height": round(region.page_height, 3),
    }
